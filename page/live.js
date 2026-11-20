// One live voice (voice/gpt_live/, Spec "One Live Voice", Ian 2026-11-13): the page's side of the one
// GPT-Live session in which the guide talks with the listener and reads them the chapter.
//
// The session: WebRTC to OpenAI, set up through our server (/api/live/start: the key stays there). The
// mic and the guide's voice are media tracks; everything else is JSON on the "oai-events" data channel:
// transcripts both ways, and the guide's tool calls (GPT-Live delegates them to a Terra backend; the
// page runs them on the server through /api/live/tool and hands back the result).
//
// Phases: idle -> connecting -> talk (the conversation) [-> resting: muted, tap to talk]
//   -> (the chapter's opening is ready: in talk the page has the guide say so at a pause and the reading
//   starts on start_reading, the listener's yes, the guide's lead-in or asking for it; resting (mic off), one
//   line that it's ready and the play button) -> reading (paragraphs fed one at a time, the mic muted)
//   <-> aside (a tap: the reading pauses, the mic opens, the guide answers, resume_chapter goes back)
//   <-> paused (the pause button, or a tap nobody spoke after: the session closed; play opens one and reads
//   on) [seeking: the guide being stopped] -> waiting (the next part isn't ready: a pause, or the guide's check-in)
//   -> ended (the last paragraph was heard: the session stays end_hold_secs for the slider) -> done.
// A tester (Tester Demo, body.tester): -> handback (the chapter's end, or End demo: the guide closes, or with no
//   session open nothing is said) -> the interviewer's debrief (page/interview.js), the screen cooled -> idle.
//   Their button presses go to /api/demo/event (report), for the debrief to ask about what they did.
//
// Live-tested facts this rests on (voice/gpt_live/REPORT.md): there is no event for "a response has
// finished" or "its audio has played", so the page follows the output transcript, which arrives in time
// with the audio, word by word against the paragraph being read. Paragraphs go as speakable context
// (session.commentary.append), one queued ahead, which plays straight on. Notes from the page go as
// instructions (session.instructions.append), which GPT-Live ignores while the mic is muted, so the page
// opens the mic first. The listener's question gets the chapter around it as silent context.
//
// Whose chapter: the page reads only the chapter that belongs to this conversation (L.own): the run
// start_chapter returned in it, or the one /api/live/start says was being read a moment ago (a page
// reloaded mid-chapter). The newest run in runs/ is often an older conversation's finished chapter:
// reading that the moment the listener first spoke was Ian's first live test (2026-11-14).
(function () {
  const S = __LIVE_SETTINGS__;  // config.yaml live.page, the notes (voice/gpt_live/notes.md), restart bands
  const N = S.notes;
  const LEAD_BACK = new RegExp(S.lead_back, "i");  // the guide's words for going back to the chapter
  const START_PROMISE = new RegExp(S.start_promise, "i");  // the guide's words for starting the chapter
  const QUIET = new RegExp(S.quiet_ask, "i");               // the listener asking for quiet
  const STEP_BACK = new RegExp(S.step_back_said, "i");      // the guide's parting words
  const OFFER_Q = new RegExp(S.offer_question, "i");        // the guide offering the ready chapter: it names it,
  const OFFER_ACT = new RegExp(S.offer_action, "i");        // and asks to hear it
  const YES = new RegExp(S.ready_yes, "i"), NOT_YET = new RegExp(S.ready_not_yet, "i");
  const LEAD_IN = new RegExp(S.lead_in, "i");
  const READY_ASK = new RegExp(S.ready_ask, "i");           // the listener asking for the ready chapter outright
  const EXPLAINED = new RegExp(S.explained || "$^", "i");   // the guide telling them what it does (holds the pitch nudge)
  const MIC_OFF = new RegExp(S.mic_off_ask, "i");           // "turn off your mic": then only a tap wakes it
  const STALL = new RegExp(S.stall, "i");
  const DECLINE = /^\W*(no|nope|nah|okay|ok|sure|thanks|thank you|sounds good|go ahead|i'?m good|all good|perfect|great|not really|that'?s fine)\b/i;                   // "Let me give that a thought": not an answer (the whole line)
  const FILLER = /^\W*((um+|uh+|er+|hmm+|mm+|ah+|oh)\W*)+$/i;  // a sound, not yet an answer
  // "No" to "anything else you'd like to chat about first?" (a "sure" or a "yes" is a yes to chatting). NO_CHAT is a
  // whole line of decline words ("Okay, thanks."; "Okay, sure, let's chat!" is a yes); NO_LEAD the sounds and
  // acknowledgements a no may open with ("Um, no, I'm good", "Sure, go ahead"); NO_ANY a no anywhere in a short reply
  // ("I think I'm good"), unless it also asks for talk (YES_CHAT). Ian's third tester run, 2026-11-19.
  const NO_CHAT = /^\W*(?:(?:no|nope|nah|not really|not right now|nothing(?: else)?|maybe later|i'?m (?:good|fine|all set)|all good|that'?s (?:all|fine|it)|go ahead|go for it|thanks|thank you|ok(?:ay)?|sounds good|perfect|great)\b\W*)+$/i;
  const NO_LEAD = /^\W*((um+|uh+|er+|hmm+|mm+|ah+|oh|well|so|yeah|yep|sure)\b\W*)+/i;
  const NO_ANY = /\b(go ahead|go for it|i'?m (good|fine|all set)|all good|not (right )?now|maybe later)\b/i;
  const YES_CHAT = /\b(tell me|let'?s|chat|talk)\b/i;
  const ACK = /^\W*(ok(ay)?|yeah|yes|sure|right|alright|go ahead|go on|mm+[- ]?hmm|uh[- ]huh|got it|of course)\W*$/i;  // nor a bare "Okay."               // the guide leading into the chapter
  const WPS = S.words_per_second || 2.5;
  const $ = (id) => document.getElementById(id);
  const R = 88, CIRC = 2 * Math.PI * R;
  const ring = document.querySelector("#orb .ring");
  ring.style.strokeDasharray = CIRC;
  ring.style.strokeDashoffset = CIRC;
  const core = document.querySelector("#orb .core");
  const remote = $("remote");

  const L = {
    phase: "idle",          // idle | connecting | talk | resting | leadin | reading | aside | waiting | checkin | done
    status: { state: "idle" }, lastState: "idle", lastLabel: null,
    session: null, liveId: null, pc: null, dc: null, stream: null, mic: null,
    started: false, muted: false, typing: false, epoch: 0,
    out: "", outAt: 0, outFirst: 0,   // the guide's words since its line began; when the last one came
    inp: "", inAt: 0,                 // the listener's
    spokeAt: 0, heardAt: 0,
    micVoiceAt: 0, outVoiceAt: 0, micFloor: null,  // voice heard on the mic, sound from the guide (the meters: dirAudio)           // the last guide word, the last listener word (any phase)
    seconds: 0, ratio: 0, backend: { model: null, input: 0, output: 0, cached: 0 },
    run: null, chapter: null, chapterAt: "",
    own: null,              // the run this conversation may read (start_chapter's, or the one picked back up)
    cont: null,             // /api/live/start's `continue`: {run, title, id, sentence, waiting}
    startCalled: false, promiseAt: 0, nudges: 0,  // start_chapter in this conversation; a promise without it
    lastGuideLine: "", lastGuideAt: 0, lastListenerAt: 0, lineEndAt: 0,  // the last whole lines (a question waiting for its answer)
    feed: { items: [], next: 0, cur: null, ahead: null },
    readStarted: false, finished: false,
    offer: null,            // the ready chapter offered in conversation: {at, askedAt, reminded}
    pausedAt: null,         // paused: {id, sentence} the reading goes on from
    convAt: 0, pitchNudges: 0, helloChecked: false, quietAt: 0,  // the conversation's start; nudges sent
    explainedAt: 0, askedAt: 0, pitchNudgeAt: 0,  // the guide said what it does; its latest question; the last pitch nudge
    aside: null,            // {t0, at: {id, sentence}, resumeAsked, lastGuide, silenced}
    wait: null,             // {n, since, checkedIn, silenced, arrived}
    pending: null,          // a step waiting for the guide to finish speaking: {fn, since, max}
    log: [],                // for tests and the report: [t, kind, detail]
    trace: [],              // every session event but audio and transcripts: [t, type, detail]
    asides: [], checkins: [], gaps: [],
    // Tester Demo (docs/tester-demo.md): a tester's page (body.tester, set by the server; or handed over by the
    // interviewer on the gate page), the classes for the reveal and the cool-down, which render() keeps.
    tester: document.body.classList.contains("tester"), skin: new Set(),
    handoff: null,          // {name}: sent with the next /api/live/start (the interviewer handed them over)
    handoffOpen: null,      // this conversation's session opens with the handoff note: {name}, as known at the handover
    hold: null,             // the session is opened ahead of the handover, silent and deaf: {who, go, released, done}
    handingBack: null,      // "end" | "done" | "talk" | "now": the guide is closing (or nothing to say), then the debrief
    debriefing: false,      // the interviewer's debrief is on (the screen cooled)
    debriefDone: false,     // their debrief is done (/api/live/start, /api/live/demo): no handback, no End demo
    handedOver: false,      // the interviewer handed them over on this page: End demo shows from the reveal
    demo: null,             // /api/live/demo {show, done, setting}: End demo for a tester back on a page of their own
    demoBegun: false,       // ... who has started a conversation here
    micRefused: false,      // a tester's mic was refused: the line asks for it, a tap or Try again asks again
    // The director, an earpiece (voice/gpt_live/director.py, config live.director; Ian, 2026-11-19): a model that
    // looks at the conversation on a cadence, keeps a plan, and whispers the guide's next move when its advice changes.
    // {fails, down, ctl, id, next, lines, askedAt, event, queued, turnOpen, silenceIx, sentAt, offered}
    dir: null,
    recent: [],             // the last few lines ({who, text}) for the director (any the transcript lacks yet)
    turnWords: [], turnEnd: 0, inStart: 0,  // the listener's turns for engaged(): fragments merged, words per turn
    replyWait: null,        // the guide offered to chat before stepping back: {at} its line ended; the page waits for a reply
  };
  if (L.tester) L.skin.add("tester");
  window.Live = { L, S, state: () => L.phase };  // S: tests turn the clock-driven cues on

  const now = () => Date.now();
  function mark(kind, detail) { L.log.push([now(), kind, detail || null]); }

  function note(text, kind = "stage") {
    if (!text) return;
    const p = document.createElement("p");
    p.className = kind;
    p.textContent = text;
    $("transcript").appendChild(p);
    $("transcript").scrollTop = $("transcript").scrollHeight;
  }

  async function post(url, body) {
    let res;
    try {
      res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                               body: body ? JSON.stringify(body) : undefined });
    } catch (e) { throw new Error("The server isn't running."); }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      const e = new Error(typeof err.detail === "string" ? err.detail : "Something went wrong.");
      e.conflict = res.status === 409;
      throw e;
    }
    return res.json();
  }
  const quietPost = (url, body) => post(url, body).catch(() => {});

  // --- the session -----------------------------------------------------------------------------------
  let seq = 0;
  function send(type, fields = {}) {
    if (!L.dc || L.dc.readyState !== "open") return false;
    L.dc.send(JSON.stringify({ type, event_id: `p${++seq}_${now() % 1e7}`, ...fields }));
    return true;
  }
  function mute(on) {
    if (L.muted === on) return;
    L.muted = on;
    if (L.mic) L.mic.enabled = !on;  // nothing leaves the laptop while muted
    send(on ? "session.input_audio.mute" : "session.input_audio.unmute");
  }
  function instruct(text) {  // a note from the page; GPT-Live only acts on it with the mic open
    mute(false);
    send("session.instructions.append", { delegation_id: null, content: text });
    mark("instruct", text.slice(0, 80));
    remember("page", text);
    if (L.session) quietPost("/api/live/line", { session: L.session, who: "page", text });  // in the transcript, so a line has its cause
  }
  function remember(who, text) { L.recent = L.recent.concat([{ who, text }]).slice(-12); }
  function think(text) { send("session.thinking.append", { delegation_id: null, content: text }); }
  function speakable(text) { send("session.commentary.append", { delegation_id: null, content: text }); }

  async function iceDone(pc, ms) {
    if (pc.iceGatheringState === "complete") return;
    await new Promise((done) => {
      const t = setTimeout(done, ms);
      pc.addEventListener("icegatheringstatechange", () => {
        if (pc.iceGatheringState === "complete") { clearTimeout(t); done(); }
      });
    });
  }

  // Opens the session: a new conversation, or (`resume`) the same one in a new session.
  // A connect that ends without a live session: the mic released (it was asked for again), and a paused
  // chapter stays paused where it was.
  function notLive(resume) {
    if (L.stream && !L.pc) { L.stream.getTracks().forEach((x) => x.stop()); L.stream = L.mic = null; }
    if (resume && resume.startsWith("paused")) { L.phase = "paused"; L.pauseAt = now(); }
  }

  async function connect(resume = null) {
    const lib = document.getElementById("chapter-audio");  // one voice at a time with the library below
    if (lib && !lib.paused) lib.pause();
    if (window.Library && Library.busy && Library.busy()) await Library.end();
    const mine = ++L.epoch;
    // Where an earlier conversation on this page stopped mid-chapter: if the server picks that chapter
    // back up, the reading goes on from that sentence rather than its paragraph's start.
    const stoppedAt = !resume && L.dropAt && L.run ? { run: L.run, ...L.dropAt } : null;
    if (!resume) newConversation();
    else dirForget();
    L.phase = "connecting";
    render();
    const sayOnly = resume === "ready";  // only to say the chapter is ready: no mic, never on (Ian asked for it off)
    try {
      if (!L.stream && !sayOnly) {
        L.stream = await navigator.mediaDevices.getUserMedia(
          { audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
        L.mic = L.stream.getAudioTracks()[0];
        meters.input(L.stream);
      }
    } catch (e) {
      if (L.tester) {  // testers can't type (it's hidden): ask for the mic, as the gate does, and a tap asks again
        L.micRefused = true;
        L.phase = resume && resume.startsWith("paused") ? "paused" : resume ? "resting" : "idle";
        if (L.phase === "paused") notLive(resume);
        render();
        return false;
      }
      note("No microphone, so let's type instead.");
      if (resume && resume.startsWith("paused")) { notLive(resume); render(); }
      else if (resume) { L.phase = "resting"; render(); }
      return false;
    }
    L.micRefused = false;
    if (mine !== L.epoch) { if (!L.pc && L.phase !== "connecting") notLive(null); return true; }
    const pc = new RTCPeerConnection();
    L.pc = pc;
    pc.ontrack = (e) => {
      remote.srcObject = e.streams[0];
      const p = remote.play();  // playing from the moment the track arrives, not left to autoplay at the first word
      if (p && p.catch) p.catch(() => {});
      meters.output(e.streams[0]);
    };
    const hold = resume ? null : L.hold;
    let sender = null;
    if (L.mic) {
      if (sayOnly) L.mic.enabled = false;
      sender = pc.addTrack(L.mic, L.stream);
      // Opened ahead of the handover: the mic is the interviewer's until then, so nothing of it is sent here.
      if (hold && sender && sender.replaceTrack) sender.replaceTrack(null).catch(() => {});
    } else pc.addTransceiver("audio", { direction: "sendrecv" });  // no mic at all: it only speaks
    const dc = pc.createDataChannel("oai-events");
    L.dc = dc;
    // Only the current session's events count: a closed one's last words (errors, its session.closed) once
    // ended the new session play had just opened (live check after Ian's third test).
    dc.onmessage = (e) => {
      if (dc !== L.dc) {  // a session being let go of: only its close and its usage count matter now
        try {
          const m = JSON.parse(e.data);
          if (m.usage && m.usage.seconds != null) dc.seconds = m.usage.seconds;
          if (m.type === "session.closed") dc.closedAt = now();
        } catch (err) { /* not ours to read */ }
        return;
      }
      try { onEvent(JSON.parse(e.data)); } catch (err) { mark("bad-event", String(err)); }
    };
    pc.onconnectionstatechange = () => {
      if (pc === L.pc && ["failed", "closed", "disconnected"].includes(pc.connectionState)) dropped(pc.connectionState);
    };
    let got;
    try {
      await pc.setLocalDescription(await pc.createOffer());
      await iceDone(pc, 2500);
      got = resume ? await post("/api/live/resume", { session: L.session, sdp: pc.localDescription.sdp, reason: resume })
                   : await post("/api/live/start", { sdp: pc.localDescription.sdp, ...(L.handoff ? { handoff: L.handoff } : {}) });
      if (mine !== L.epoch) { pc.close(); return true; }
      L.session = got.session; L.liveId = got.live_id;
      if (!resume && got.handoff && L.handoff) { L.handoffOpen = L.handoff; L.handoff = null; }  // kept for a retry until it opens
      if (!resume) { L.debriefDone = !!got.debrief_done; L.demoBegun = true; }
      if (L.tester && !L.handedOver && !L.demo) loadDemo();  // the load-time ask failed: asked again at a start
      if (!resume && got.continue) {
        L.cont = { ...got.continue };
        L.own = got.continue.run;
        if (L.cont.ready && !L.cont.begun) {  // ready_unheard asks whether they'd like it: their yes must count
          L.cont.offered = true;
          L.offer = { at: now(), askedAt: now(), reminded: false };
        }
        if (L.cont.begun && stoppedAt && stoppedAt.run === L.cont.run) { L.cont.id = stoppedAt.id; L.cont.sentence = stoppedAt.sentence || 0; }
      }
      await pc.setRemoteDescription({ type: "answer", sdp: got.sdp });
    } catch (err) {
      pc.close();
      if (L.pc === pc) { L.pc = null; L.dc = null; }
      if (mine !== L.epoch) return true;
      note(err.message || "Voice couldn't start.");
      L.phase = resume && resume.startsWith("paused") ? "paused" : resume ? "resting" : "idle";
      if (L.phase === "paused" || L.phase === "idle") notLive(resume);
      if (L.phase === "resting") { L.restAt = now(); setTimeout(() => { if (L.phase === "resting") listenForWake(); }, 1000); }
      render();
      return false;
    }
    L.started = false; L.muted = sayOnly;
    if (L.mic) L.mic.enabled = !sayOnly;  // kept from before a drop, it may still be disabled
    const ok = await until(() => L.started || mine !== L.epoch, 10000);
    if (mine !== L.epoch) return true;
    if (!ok) {
      note(resume ? "Voice couldn't come back. Tap to try again." : "Voice couldn't start.");
      reportUsage();
      hangUp();
      L.phase = resume ? "resting" : "idle";
      notLive(resume);
      if (L.phase === "resting") { L.restAt = now(); setTimeout(() => { if (L.phase === "resting") listenForWake(); }, 1000); }
      render();
      return false;
    }
    if (hold) {
      // Opened ahead of the handover (Live.handoff, hold): connected and playing, but nothing is sent to it and
      // it hears nothing until the interviewer has finished, so its first words come about as fast as it can
      // answer. Never let go of: closed, the conversation ended (no line of the listener's: not a meeting).
      send("session.input_audio.mute");
      mark("held");
      const went = await Promise.race([hold.released, new Promise((r) => setTimeout(() => r(false), S.hold_max_ms || 20000))]);
      if (L.hold === hold) L.hold = null;
      if (mine !== L.epoch) return true;
      if (L.pc !== pc || !went) {  // dropped meanwhile (once let go: again, from the start), or never let go of
        if (L.pc === pc) { send("session.close"); reportUsage(); hangUp(); }
        if (went) { L.handoff = hold.who; return connect(); }
        if (L.session) quietPost("/api/chat/end", { session: L.session });
        L.session = null;
        L.handoff = hold.who;  // a later handover starts again with it
        L.phase = "idle";
        render();
        return false;
      }
      if (sender && sender.replaceTrack) await sender.replaceTrack(L.mic).catch(() => {});
      send("session.input_audio.unmute");
      L.startedAt = now();  // its clocks (the greeting's fallback, the reveal's) count from the handover
      mark("released");
    }
    if (!resume) L.convAt = now();
    L.wokeAt = 0; L.stepBackAt = 0; L.restFrom = null;
    L.phase = resume && resume.startsWith("paused") ? "paused" : resume === "ready" ? "resting" : L.chapter && L.readStarted && (!L.finished || L.replaying) ? "aside" : "talk";
    if (L.phase === "paused") L.pauseAt = now();
    if (L.phase === "resting") { L.muted = false; mute(true); }  // back only to say the chapter is ready: muted, and the session told so
    if (L.phase === "aside" && resume !== "section boundary") {  // back after a drop: an aside, so resume_chapter can go on
      L.aside = { t0: now(), at: L.dropAt ? { id: L.dropAt.id, sentence: L.dropAt.sentence } : null,
                  lastGuide: 0, lastListener: 0, silenced: false, lines: [] };
      L.feed.cur = L.feed.ahead = null;
    }
    // The guide speaks first. Context appended the moment the session starts makes it speak (live,
    // about 1.5 s); a developer message in the startup input alone did not, reliably. With an opening
    // set (config.yaml live.opening) those words, else a greeting in its own words. A picked-up chapter's
    // session opened with its own first message (`reloaded`), and a resumed one with `continue`/`returned`.
    const silent = () => mine === L.epoch && !L.sessionWord && L.phase === "talk" && meters.level(meters.out) < 0.01
      && !(L.heardAt > (L.startedAt || 0)) && !L.inp;  // nothing said, and the listener hasn't begun either
    if (!resume && L.handoffOpen) {
      // Handed over by the interviewer: who they are, as speakable context (acted on at once, as the greeting
      // is). As the session's startup message alone it waited out greet_fallback_ms in silence (Ian's run).
      const who = L.handoffOpen.name;
      const first = who ? N.handoff.replace(/\{name\}/g, who) : N.opened;
      speakable(first);
      mark("handoff-note", who);
      quietPost("/api/live/line", { session: L.session, who: "page", text: first });
      setTimeout(() => { if (silent()) instruct(`[${first}]`); }, S.greet_fallback_ms);  // nothing yet: once more
    } else if (!resume && !L.cont) {
      speakable(S.opening ? N.opening_words.replace("{words}", S.opening) : N.opened);
      setTimeout(() => { if (silent()) instruct(N.opened); }, S.greet_fallback_ms);  // nothing yet: once more, the mic open
    } else if (!resume && L.cont) {  // its first message is the startup input, which alone may not make it speak
      const first = (L.cont.begun ? N.reloaded : (L.cont.ready ? N.ready_unheard : N.reopened)).replace("{title}", L.cont.title || "their chapter");
      setTimeout(() => { if (silent()) instruct(`[${first}]`); }, S.greet_fallback_ms);
    } else if (resume === "tap" || resume === "dropped") {
      // Back on a tap (or typed text) after a rest or a drop: the note that says so is the new session's startup
      // message, which alone may not make it speak. Nothing said by either side yet: once more, the mic open.
      const again = resume === "tap" ? N.wake : `[${N.returned}]`;
      setTimeout(() => { if (silent()) { mark("back-again", resume); instruct(again); } }, S.greet_fallback_ms);
    }
    if (!resume && L.cont && L.cont.begun) {  // the page was reopened mid-chapter: once the guide has said it's back, read on
      onStatus(L.status);
      const c = L.cont;
      afterGuide(() => pickUp(c), S.lead_in_max_ms, S.greet_fallback_ms + 4000);
    } else if (!resume && L.cont) {  // reopened while it is being made (or written, unheard): it begins when its opening is written
      onStatus(L.status);
    } else if (resume && resume !== "section boundary" && !L.readStarted && L.cont && L.cont.begun && L.own === L.cont.run) {
      const c = L.cont;  // dropped before the picked-up chapter went on: go on from its place, not its opening
      afterGuide(() => pickUp(c), S.lead_in_max_ms, 5000);
    } else if (!["section boundary", "ready", "wake"].includes(resume) && !L.readStarted && !(L.cont && L.cont.begun) && mine_(L.status) && (L.status.sections || []).some((x) => x.n === 1 && x.text)) {
      // This conversation's opening was written (or offered) while the connection was down: offer it again
      // after the guide's line (the new session didn't hear the offer).
      if (!L.wantPlay) { L.offer = null; afterGuide(() => beginChapter(), S.lead_in_max_ms); }
    }
    render();
    return true;
  }

  // A new conversation: nothing of an earlier one's chapter is carried over (the page may have stayed open).
  function newConversation() {
    L.own = null; L.cont = null; L.startCalled = false; L.promiseAt = 0; L.nudges = 0; L.outFirst = 0;
    L.run = null; L.chapter = null; L.chapterAt = ""; L.feed = { items: [], next: 0, cur: null, ahead: null };
    L.readStarted = false; L.finished = false; L.archived = false; L.aside = null; L.wait = null; L.dropAt = null;
    L.runEnded = false; L.leading = false; L.lastAt = null; L.stalled = null; L.lastGuideLine = ""; L.lastGuideAt = 0; L.lastListenerAt = 0; L.lineEndAt = 0;
    L.offer = null; L.pausedAt = null; L.convAt = 0; L.pitchNudges = 0; L.pitchSent = 0; L.explainedAt = 0; L.askedAt = 0; L.pitchNudgeAt = 0; L.helloChecked = false; L.quietAt = 0; L.ringMax = 0;
    L.seekTo = null; L.replaying = false; L.ending = false; L.wokeAt = 0;
    L.turnWords = []; L.turnEnd = 0; L.stepBackAt = 0; L.replyWait = null;
    L.handoffOpen = null; L.handingBack = null;
    if (L.dir && L.dir.ctl) L.dir.ctl.abort();
    L.dir = { fails: 0, down: false, ctl: null, id: 0, next: null, lines: 0, askedAt: 0, event: null, queued: null,
              turnOpen: false, silenceIx: 0, sentAt: 0, offered: false };
    L.recent = [];
    W.sounds = 0; W.total = 0; W.off = false;
    renderMarkers();
  }

  // The chapter picked back up (a page reopened mid-reading): from the paragraph it was in.
  async function pickUp(c) {
    if (L.readStarted || L.own !== c.run || !L.dc || L.dc.readyState !== "open" || !["talk", "resting"].includes(L.phase)) return;
    if (L.run !== c.run) onStatus(L.status);
    if (!(await loadChapter())) return;
    if (!L.dc || L.dc.readyState !== "open" || !["talk", "resting"].includes(L.phase)) return;
    L.readStarted = true;
    const k = L.feed.items.findIndex((x) => x.id === c.id);
    if (k === -1) { L.feed.next = L.feed.items.length; mute(true); return queueEmpty(); }  // its part isn't written yet
    startReading({ id: c.id, sentence: c.sentence || 0 }, "picked up");
  }

  function until(cond, ms) {
    return new Promise((done) => {
      const t0 = now();
      const iv = setInterval(() => {
        if (cond()) { clearInterval(iv); done(true); } else if (now() - t0 > ms) { clearInterval(iv); done(false); }
      }, 50);
    });
  }

  function hangUp() {
    const pc = L.pc;
    L.pc = null; L.dc = null;
    L.thinking = false;  // a closed session's delegation never completes
    if (pc) try { pc.close(); } catch (e) { /* closed */ }
  }

  function dropped(why) {
    if (L.phase === "idle" || L.phase === "done") return;
    mark("dropped", why);
    const was = L.phase;
    L.dropAt = where() || (L.aside && L.aside.at) || L.seekTo || L.pausedAt || L.dropAt || null;  // where the reading was
    L.pending = null;
    hangUp();
    reportUsage();
    stopWake(); L.wokeAt = 0;
    if (was === "ended") return endConversation();  // heard to the end: nothing to come back to
    if (L.handingBack) return finishHandback(L.handingBack);  // closing anyway: on to the debrief
    if (["paused", "pausing", "seeking"].includes(was) && L.dropAt) {  // still paused there: play reconnects
      L.pausedAt = { id: L.dropAt.id, sentence: L.dropAt.sentence || 0 };
      L.seekTo = null;
      mute(true);
      L.phase = "paused"; L.pauseAt = now();
      return render();
    }
    note("The connection dropped. Tap to talk again.");
    mute(true);  // the device stays open for the reconnect, but nothing is heard
    L.phase = "resting"; L.restAt = now(); L.restFrom = "drop";
    render();
  }

  // `keepStream`: the mic is handed on (the interviewer's debrief), so it is let go of here, not stopped.
  async function endConversation(keepStream = false) {
    if (keepStream !== true) keepStream = false;  // a click handler passes its event
    stopWake();
    L.wokeAt = 0;
    if (L.ending) return;  // the end hold and the rest timeout tick every 200 ms: one end
    L.ending = true;
    L.epoch++;
    L.pending = null;
    if (L.readStarted && !L.finished) L.dropAt = where() || (L.aside && L.aside.at) || L.seekTo || L.pausedAt || L.dropAt || null;
    if (L.dc && L.dc.readyState === "open") {
      send("session.close");
      await until(() => !L.dc || L.closed, 3000);
    }
    await reportUsage();
    hangUp();
    if (L.stream && !keepStream) L.stream.getTracks().forEach((t) => t.stop());
    if (keepStream && L.mic) L.mic.enabled = true;  // muted here, perhaps: the interviewer must be heard
    L.stream = L.mic = null;
    if (L.session) quietPost("/api/chat/end", { session: L.session });
    // A chapter heard to the end: its reading, cut from the session's recording, becomes the episode.
    if (L.finished && L.run && !L.archived) { L.archived = true; quietPost("/api/live/archive", { run: L.run }); }
    L.session = null; L.chatSession = null;
    L.phase = L.finished ? "done" : "idle";
    L.out = L.inp = "";
    L.ending = false;
    render();
  }

  let reported = null;
  function reportUsage() {  // a promise: the conversation's end must wait for it (the server forgets the session)
    if (!L.session || !L.liveId || reported === L.liveId) return Promise.resolve();
    reported = L.liveId;
    const b = L.backend.model ? { ...L.backend } : null;
    const sent = quietPost("/api/live/usage", { session: L.session, live_id: L.liveId, seconds: L.seconds, backend: b });
    L.seconds = 0; L.backend = { model: null, input: 0, output: 0, cached: 0 };  // the next session counts its own
    return sent;
  }

  // --- events --------------------------------------------------------------------------------------
  function onEvent(m) {
    if (m.type !== "session.output_transcript.delta" && m.type !== "session.input_transcript.delta" && m.type !== "session.usage.updated") {
      const e = m.event || {};
      if (m.type !== "response.event" || ["response.output_item.done", "response.completed", "response.output_text.done"].includes(e.type)) {
        L.trace.push([now(), m.type, m.type === "response.event" ? e.type + " " + (e.text || (e.item && (e.item.name || e.item.type)) || "") : (m.client_event_id || m.reason || "")]);
      }
    }
    switch (m.type) {
      case "session.started":
        L.started = true; L.closed = false; L.startedAt = now(); L.sessionWord = 0;
        if (m.session && m.session.expires_at) L.expires = m.session.expires_at * 1000;
        mark("started", m.session && m.session.id);
        break;
      case "session.output_transcript.delta": return heardGuide((m.delta || "").replace(/\[[^\]]*\]\s*/g, ""), m.start_ms, m.end_ms);
      case "session.input_transcript.delta": return heardListener(m.delta || "");
      case "session.delegation.created": L.thinking = true; render(); break;
      case "response.event": return backend(m.event || {});
      case "session.usage.updated":
        if (m.usage && m.usage.seconds != null) L.seconds = m.usage.seconds;
        if (m.context_window) L.ratio = m.context_window.usage_ratio;
        break;
      case "session.closed":
        L.closed = true;
        if (m.usage && m.usage.seconds != null) L.seconds = m.usage.seconds;
        mark("closed", m.reason);
        if (m.reason !== "close_requested" && !L.closing) dropped(m.reason);
        break;
      case "error":
        mark("error", m.error && m.error.message);
        if (m.error && m.error.message && !L.closing) note("Voice: " + m.error.message);  // a session being closed may complain
        break;
    }
  }

  function heardGuide(delta, startMs, endMs) {
    const t = now();
    L.spokeAt = t;
    if (L.phase === "reading" && L.feed.cur) return reading(delta, startMs, endMs);
    if (!L.out) L.outFirst = L.outFirst || t;
    L.sessionWord = L.sessionWord || t;  // this session's first word
    if (!L.out) L.lineAt = t;
    L.out += delta;
    L.outAt = t;
  }

  function heardListener(delta) {
    if (!L.inp) L.inStart = now();
    L.heardAt = L.inAt = now();
    L.inp += delta;
  }

  // Every 200 ms: lines that have ended go to the transcript; steps waiting on the guide to finish go on.
  setInterval(() => {
    const t = now();
    if (L.out && t - L.outAt > S.line_quiet_ms) {
      const text = L.out.replace(/\[[^\]]*\]\s*/g, "").trim(); L.out = "";  // a sound tag split across deltas
      // Right after a tap, the last words of the passage it was reading are not an answer (Ian's second
      // test: "poor part" counted as one, and the silence after it sent the reading back unasked).
      const tail = text && L.phase === "aside" && L.aside && tailOf(L.aside, text, t);
      if (tail) { mark("tail", text.slice(0, 60)); if (L.session) quietPost("/api/live/line", { session: L.session, who: "guide", text }); }
      if (text && !tail) {
        note(text, "guide");
        L.lastGuideLine = text; L.lastGuideAt = t; L.lineEndAt = Math.max(L.lineEndAt || 0, L.outAt || t);  // its last word
        remember("guide", text);
        directorLine(false);  // the earpiece counts lines for its cadence
        guideSaid(text, t, L.lineAt || t);
        if (!L.explainedAt && EXPLAINED.test(text)) L.explainedAt = t;  // the pitch nudge waits for these (conversationCues)
        if (text.includes("?")) L.askedAt = t;
        // A promise to start never counts in a line that asks (a pitch or an offer: the listener hasn't agreed
        // yet), and is dropped once the guide asks something else.
        if (["talk", "resting"].includes(L.phase) && !L.own && !L.startCalled && !text.includes("?") && START_PROMISE.test(text)) L.promiseAt = L.promiseAt || t;
        else if (text.includes("?")) L.promiseAt = 0;  // it has moved on to asking something (a parting line keeps it)
        if (L.session) quietPost("/api/live/line", { session: L.session, who: "guide", text });
        if (L.aside || L.wait) (L.aside || L.wait).lines.push({ who: "guide", text });
        // A stall ("Hmm, let me give that a thought": GPT-Live had handed the question to the backend, Ian's
        // third test) is not an answer: the silence after it must not send the reading back.
        const stall = STALL.test(text) || ACK.test(text);
        if (stall) { mark("stall", text.slice(0, 60)); if (L.aside) L.aside.stalledAt = t; }
        if (L.aside && L.aside.silenced && t > L.aside.silencedAt) L.aside.silenced = false;  // it spoke after the note: the clock starts again
        if (L.aside && !stall) L.aside.lastGuide = t;
        if (L.wait) L.wait.lastGuide = t;
        // It led back in ("Anyway, where was I... right.") but GPT-Live often doesn't go on to call
        // resume_chapter (live, about half the asides): the lead-back itself is the cue.
        if (L.phase === "aside" && L.aside && !L.pending && LEAD_BACK.test(text) && now() - L.aside.t0 > 2000) {
          mark("lead-back", text.slice(0, 60));
          resumeChapter(null, "lead-back");
        }
      }
    }
    if (L.inp && t - L.inAt > S.line_quiet_ms) {
      const text = L.inp.trim(); L.inp = "";
      if (text) {
        note("You: " + text, "you");
        L.lastListenerAt = t; L.lineEndAt = Math.max(L.lineEndAt || 0, L.inAt || t);
        countLine(text, L.inStart || L.inAt, L.inAt);
        remember("listener", text);
        directorLine(true);
        listenerSaid(text, t);
        if (L.session) quietPost("/api/live/line", { session: L.session, who: "listener", text });
        if (L.aside || L.wait) (L.aside || L.wait).lines.push({ who: "listener", text });
        if (L.aside) L.aside.lastListener = t;
        if (L.wait) L.wait.lastListener = t;
      }
    }
    if (L.pending) {
      const p = L.pending;
      const quiet = t - Math.max(L.spokeAt, p.since) > S.line_quiet_ms && t - L.heardAt > S.line_quiet_ms;
      const spoke = L.spokeAt > p.since;
      if ((spoke && quiet) || (t - p.since > p.max && !L.inp) || (!spoke && t - p.since > (p.none || p.max) && t - L.heardAt > S.line_quiet_ms && !L.inp)) {
        L.pending = null;
        p.fn();
      }
    }
    if (tokens.partial && t - (L.deltaAt || 0) > 500) {
      const ws = tokens.flush();
      if (L.phase === "reading") { if (L.feed.cur) readWords(ws); }  // the end of a paragraph's last word
      else if (ws.length) { L.out += " " + ws.join(" "); L.outAt = t; }
    }
    // It said it would start the chapter, and start_chapter hasn't come (delegation can miss it): remind it, at most twice.
    if (L.promiseAt && !L.startCalled && !L.own && t - L.promiseAt > S.start_nudge_ms && !L.inp && t - L.spokeAt > 1000) {
      L.promiseAt = 0;
      if (L.nudges < 2 && ["talk", "resting"].includes(L.phase) && L.dc && L.dc.readyState === "open") {
        L.nudges++;
        mark("start-nudge");
        const wasResting = L.phase === "resting";
        if (wasResting) { L.phase = "talk"; L.restAt = 0; }  // the note opens the mic: the page says so
        instruct(N.start_nudge);
        if (wasResting && !L.pending) afterGuide(rest, S.lead_in_max_ms, 4000);  // and rests again once it has acted
        render();
      }
    }
    if (W.ctx && L.phase !== "resting") stopWake();
    if (L.wokeAt && L.phase === "talk") {  // woken by a sound: words, or back to rest
      if (L.heardAt > L.wokeAt) { L.wokeAt = 0; W.sounds = 0; }
      else if (t - L.wokeAt > S.wake_listen_secs * 1000 && t - L.spokeAt > 2000 && !L.pending) {
        L.wokeAt = 0;
        mark("wake-unanswered");
        if (W.sounds >= 2 || W.total >= ((S.wake && S.wake.max_sound_wakes) || 8)) W.off = true;
        rest();
      }
    }
    if (L.stepBackAt && L.phase === "talk" && t - L.stepBackAt > (S.step_back_wait_secs || 12) * 1000 && !L.inp && !L.out && !L.pending
        && t - L.spokeAt > 2000 && t - L.heardAt > 3000) {
      L.stepBackAt = 0;
      mark("rest-said", "after start, unsaid");
      rest();
    }
    // The guide said it would step back unless they'd like to chat, and asked: a real offer, so the page waits for
    // their answer (Ian's third tester run: it rested 6 s after the offer, before he could take it up). Silence for
    // step_back_reply_secs after anyone last spoke, and it rests; their answer is read in listenerSaid.
    if (L.replyWait && L.phase === "talk" && !L.inp && !L.out && !L.pending
        && t - Math.max(L.replyWait.at, L.spokeAt, L.heardAt) > (S.step_back_reply_secs || 8) * 1000) {
      L.replyWait = null;
      mark("rest-said", "after start, no reply");
      rest();
    }
    // When this stretch of talk began (a new session after a rest, a tap, a sound): the long-silence rest counts
    // from here. Without it a tap after 90 s of rest rested again within a second, the last words being that old
    // (Ian's tester run, 2026-11-16: "Tap to talk doesn't work once turned off").
    if (L.phase !== L.wasPhase) { if (L.phase === "talk") L.talkAt = t; L.wasPhase = L.phase; }
    if (dirOn()) dirAudio(t);
    if (L.dir && L.dir.next) directorDeliver(t);
    if (L.dir && dirHere()) directorTick(t);
    if (L.dc && L.dc.readyState === "open") conversationCues(t);
    if (L.phase === "paused" && L.pc && !L.closing) closeLive();  // never open while paused (a session that came up meanwhile)
    if (L.phase === "ended" && t - L.endedAt > S.end_hold_secs * 1000) endConversation();
    if (L.phase === "reading") watchReading(t);
    if (L.aside && L.phase === "aside") watchAside(t);
    if (L.wait && (L.phase === "waiting" || L.phase === "checkin")) watchWait(t);
    if (L.phase === "resting" && L.restAt && t - L.restAt > S.rest_close_minutes * 60000) endConversation();
    // The handoff's reveal: the screen fades in at the guide's first word (or, if it never comes, a while after
    // the session started, so a tester is never left with a dot alone).
    if (L.skin.has("handoff") && !L.skin.has("revealed") && !L.hold
        && (L.sessionWord || (L.started && t - (L.startedAt || t) > (S.reveal_fallback_ms || 8000)))) reveal();
  }, 200);

  // When the guide has finished speaking (its words have stopped and nobody is talking), do `fn`.
  // `none`: give up waiting for it to start speaking after this long.
  function afterGuide(fn, max = S.lead_in_max_ms, none = null) {
    L.pending = { fn, since: now(), max, none };
  }

  // Notes the page sends into the conversation on a clock: a hello nobody answered, a conversation running
  // long with no chapter, a ready chapter not taken up. A note goes while the listener is speaking (the
  // guide's reply to them takes it in) or after a few seconds' quiet, never on top of the guide.
  // With the director up (dirOn), its earpiece reads the conversation instead of most of these clocks: no offer cue
  // (Ian's third tester run: it went 50 s in, mid-sentence), and the pitch nudges are late fallbacks
  // (live.director.fallback) that go only in a lull, never while they talk, and not within min_gap_secs of the
  // earpiece's last whisper; the first of them is the offer cue if the guide hasn't said what it does (the earpiece
  // was never seen to whisper an offer in the replay). The hello check-in stays the page's own clock. With it off,
  // exactly the clocks as they were; gone down mid-conversation (two failures in a row), the same clocks but in a
  // lull only (fb): by then they are long overdue, and would go into the listener's sentence on the next tick.
  function conversationCues(t) {
    if (L.phase !== "talk" || L.pending) return;
    const dir = dirOn(), DC = S.director || {};
    const fb = !dir && !!(DC.enabled && L.dir && L.dir.down);
    const theyTalk = L.inp && t - L.inAt < 600 && !L.out && t - L.spokeAt > 1500;
    const lull = !L.inp && !L.out && t - L.spokeAt > 4000 && t - L.heardAt > 4000;
    if (!L.cont && !L.helloChecked && L.sessionWord && L.lastGuideAt && !(L.heardAt > L.convAt) && !L.inp
        && t - L.spokeAt > S.hello_silence_secs * 1000) {
      L.helloChecked = true;
      mark("hello-silence");
      return instruct(N.hello_silence);
    }
    // What it does, early. Ian's second tester run (2026-11-18): "Want to hear what I do?" came two minutes in,
    // after four questions about his day; he liked that it found a silence for it and wanted it within a couple
    // of turns. Once the listener has spoken and the conversation is after_secs old, the first lull is the
    // guide's cue (offer_nudge); by max_secs it goes even while they talk. Once a conversation (kept by its
    // start time), never once the guide has said what it does, nor while its own offer is the line being answered.
    const O = dir ? null : S.offer_nudge;
    if (O && L.offerNudgeFor !== L.convAt && !L.explainedAt && !L.own && !L.startCalled && !L.cont && L.convAt
        && L.heardAt > L.convAt && t - L.convAt > O.after_secs * 1000
        && !(O.offered && new RegExp(O.offered, "i").test(L.lastGuideLine || "")) && !(L.dir && L.dir.offered)
        && (lull || (!fb && theyTalk && t - L.convAt > O.max_secs * 1000))) {
      L.offerNudgeFor = L.convAt;
      mark("offer-nudge");
      return instruct(N.offer_nudge);
    }
    // The pitch nudge. Ian's tester run (2026-11-16): it went 12 s after the guide's first open question, 31 s
    // after it had said what it does, mid-answer, and the guide pitched the first thing he mentioned. So it
    // waits a while after the explanation, and for the answer to the guide's latest question (that wait is
    // bounded: a guide that asks every turn is still nudged); the second comes no sooner after the first
    // than the settings' gap. And it waits for a lull rather than going while they talk (an answer longer
    // than the question hold was still cut into), until max_secs past due.
    const nudges = (dir ? (DC.fallback || {}).pitch_nudge_secs : S.pitch_nudge_secs) || [], H = S.pitch_hold || {}, n = L.pitchNudges;
    const due = n < nudges.length && L.convAt
      ? Math.max(L.convAt + nudges[n] * 1000, n ? L.pitchNudgeAt + (nudges[n] - nudges[n - 1]) * 1000 : 0) : 0;
    const held = (L.explainedAt && t - L.explainedAt < (H.explained_secs || 0) * 1000)
      || (L.askedAt && t - L.askedAt < (H.question_secs || 0) * 1000 && t - due < (H.max_secs || 0) * 1000);
    const opening = dir ? lull && !(L.dir.sentAt && t - L.dir.sentAt < (DC.min_gap_secs || 0) * 1000)
      : fb ? lull : lull || (theyTalk && t - due >= (H.max_secs || 0) * 1000);
    if (!L.own && !L.startCalled && !L.cont && due && t > due && !held && opening) {
      // The director has no offer clock, so with it up nothing has made sure the guide said what it does: the first
      // fallback is the offer cue then (it says to ask what they've been listening to if they already know).
      const O2 = S.offer_nudge || {};
      if (dir && !L.explainedAt && !L.dir.offered && L.offerNudgeFor !== L.convAt
          && !(O2.offered && new RegExp(O2.offered, "i").test(L.lastGuideLine || ""))) {
        L.offerNudgeFor = L.convAt; L.pitchNudges++; L.pitchNudgeAt = t;  // takes the first fallback's place
        mark("offer-nudge", "fallback");
        return instruct(N.offer_nudge);
      }
      L.pitchNudges++; L.pitchNudgeAt = t;
      mark("pitch-nudge", L.pitchNudges);
      return instruct(L.pitchSent++ ? N.pitch_nudge_again : N.pitch_nudge);
    }
    // Nobody has said anything for a long while: the mic goes off (tap to talk), as if they'd asked for quiet.
    if (S.talk_rest_secs && !L.inp && !L.out && t - L.spokeAt > S.talk_rest_secs * 1000 && t - L.heardAt > S.talk_rest_secs * 1000
        && t - Math.max(L.convAt || 0, L.talkAt || 0) > S.talk_rest_secs * 1000) {
      mark("silence-rest");
      return rest();
    }
    if (L.offer && !L.readStarted && !L.offer.reminded && t - L.offer.at > S.ready_remind_secs * 1000 && lull) {
      L.offer.reminded = true;
      mark("ready-again");
      instruct(N.ready_again);
    }
  }

  // --- the director: an earpiece with a plan (voice/gpt_live/director.py; Ian, 2026-11-19) -------------------
  // "A listening agent on top of the gptlive model, in order to direct it when to do things", then (the same day)
  // "constantly evaluate the conversation and provide updated notes ... with a plan". A Claude call looks at the
  // conversation in the background, keeps a plan for it on the server (checked off, reordered, changed as the guide
  // improvises), and answers with a whisper only when its advice changes (`changed` and a `note`). It never blocks a
  // turn: it may look while someone is talking (the request says who, `speaking`), and a whisper goes at once as an
  // instruction (notes.md `direct`, `direct_now`, `direct_start`), worded for its moment by the director, never as
  // speakable context. It looks:
  //   cadence   every every_secs (company_every_secs once a chapter is started) with a new line since the last look;
  //   turn_end  when a listener turn ends (no line of theirs for engaged_merge_ms) that had every_words words, or
  //             with every_lines new lines since the last look;
  //   started   once after the started note;
  //   silence   once a stretch, after silence_secs with nobody speaking.
  // One look in flight; a newer trigger waits for it (never an abort: it would throw the analysis away). Asked in
  // conversation only (before a chapter starts, and while it is made: company), never while reading, in an aside or
  // check-in, resting, muted, after a sound wake, while the guide offers to chat before stepping back, or around the
  // handover and handback. Two failures in a row (an error, a timeout, a cap) and the clocks take over for the rest
  // of the conversation, in a lull only.
  function dirOn() { return !!((S.director || {}).enabled && L.dir && !L.dir.down); }
  // Not while a tool call is in flight either (review, 2026-11-20): start_chapter's delegation takes seconds before
  // the page knows the chapter started, and a "start" whisper in that window invited a second start_chapter.
  function dirHere() {
    return dirOn() && L.phase === "talk" && !!L.session && !!L.dc && L.dc.readyState === "open" && !L.muted && !L.pending
      && !L.handingBack && !L.debriefing && !L.hold && !L.readStarted && !L.offer && !offerShown() && !L.stepBackAt
      && !L.replyWait && !L.wokeAt && !L.thinking && !!L.lastGuideAt;
  }
  const dirSecs = (v) => (Array.isArray(v) ? v : v == null ? [] : [v]);  // silence_secs: 12 or [12]
  // Who is speaking, from the audio as well as the words (review, 2026-11-20). GPT-Live's input transcript runs
  // seconds behind the voice (run 3's fragments came 2-3 s apart while Ian was mid-sentence), so the words alone
  // read a pause in the transcript as nobody speaking. Each tick (dirAudio) notes when the guide's audio was last
  // above live.director.audio.out_rms, and when the mic last carried a sound floor_x times the room's level and
  // above min_rms while the guide was quiet (its voice from the speakers is not the listener); either counts for
  // window_ms. Muted, the mic counts for nothing.
  function dirAudio(t) {
    const A = (S.director || {}).audio || {};
    if (meters.level(meters.out) >= (A.out_rms || 0.01)) L.outVoiceAt = t;
    if (L.muted || !meters.inp) return;
    const v = meters.level(meters.inp);
    L.micFloor = L.micFloor == null || v < L.micFloor ? v : L.micFloor * 0.998 + v * 0.002;  // the room: falls at once, rises slowly
    if (v >= Math.max(A.min_rms || 0.02, (A.floor_x || 4) * L.micFloor) && t - L.outVoiceAt > (A.window_ms || 600)) L.micVoiceAt = t;
  }
  function dirSpeaking(t = now()) {
    const w = ((S.director || {}).audio || {}).window_ms || 600;
    if (L.inp || t - L.micVoiceAt < w) return "listener";
    return L.out || t - L.outVoiceAt < w ? "guide" : "none";
  }
  // The page stopped being here without having looked (a rest, a session coming back): a started event, a queued
  // trigger, an open turn, a look in flight or a whisper not yet sent would fire on the first tick back, minutes
  // stale (review, 2026-11-20: a "started" look after a step back and a tap said the guide had "just" started).
  function dirForget() {
    const D = L.dir;
    if (!D) return;
    if (D.ctl) { D.ctl.abort(); D.ctl = null; }
    D.id++;  // a look still on its way is ignored when it comes
    D.event = null; D.queued = null; D.turnOpen = false; D.next = null;
  }

  // A line ended (the guide's, theirs spoken or typed): it counts toward the cadence, and starts a new silence stretch.
  function directorLine(listener) {
    const D = L.dir;
    if (!D) return;
    D.lines++; D.silenceIx = 0;
    if (listener) D.turnOpen = true;  // turn_end is judged once their turn is over (engaged_merge_ms)
  }

  // Which trigger is due, if any. One-shot triggers (started, a turn's end, a silence) are used up here.
  function directorDue(t) {
    const D = L.dir, C = S.director || {};
    if (D.event) { const e = D.event; D.event = null; return e; }
    const merge = S.engaged_merge_ms || 1500;
    if (D.turnOpen && !L.inp && t - L.turnEnd >= merge && t - L.micVoiceAt >= merge) {
      D.turnOpen = false;
      const w = L.turnWords.length ? L.turnWords[L.turnWords.length - 1] : 0;
      if (w >= (C.every_words || 12) || D.lines >= (C.every_lines || 4)) return "turn_end";
    }
    const started = !!(L.own || L.startCalled);
    const every = (started ? C.company_every_secs || 30 : C.every_secs || 15) * 1000;
    if (D.lines && t - (D.askedAt || L.convAt || t) >= every) return "cadence";
    const secs = dirSecs(C.silence_secs == null ? 12 : C.silence_secs);
    if (D.silenceIx < secs.length && dirSpeaking(t) === "none"
        && t - Math.max(L.spokeAt, L.heardAt, L.micVoiceAt, L.outVoiceAt) >= secs[D.silenceIx] * 1000) {
      D.silenceIx++;
      return "silence";
    }
    return null;
  }

  function directorTick(t) {
    const D = L.dir;
    const due = directorDue(t);
    if (due && (!D.queued || D.queued !== "started")) D.queued = due;  // the newest waits; "started" is never lost
    if (D.ctl || !D.queued) return;  // one look in flight
    const trigger = D.queued;
    D.queued = null;
    directorAsk(t, trigger);
  }

  function directorAsk(t, trigger) {
    const D = L.dir, C = S.director || {};
    const ctl = new AbortController(), id = ++D.id;
    D.ctl = ctl; D.lines = 0; D.askedAt = t;
    const body = {
      // silence_ms: since the last line ended, at its last word (whoever is speaking now), as last_who is read
      // (review, 2026-11-20: the time since any word read "the listener's line, 0 seconds ago" while the guide talked on).
      session: L.session, id, trigger, speaking: dirSpeaking(t), silence_ms: Math.max(0, t - (L.lineEndAt || L.convAt || t)),
      last_who: !L.lastListenerAt && !L.lastGuideAt ? null : L.lastListenerAt > L.lastGuideAt ? "listener" : "guide",
      recent: L.recent.slice(-8),
      page: { seconds: Math.round((t - (L.convAt || t)) / 1000), explained: !!L.explainedAt,
              started: !!(L.own || L.startCalled), turns: turns().length },
    };
    mark("direct-ask", trigger);
    const timer = setTimeout(() => ctl.abort(), (C.timeout_ms || 6000) + 1500);
    fetch("/api/live/direct", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
                                signal: ctl.signal })
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((got) => { if (L.dir === D && D.id === id) { D.ctl = null; directorGot(got, t); } })
      .catch((e) => { if (L.dir === D && D.id === id) { D.ctl = null; directorFailed(String(e && e.message || e)); } })
      .finally(() => clearTimeout(timer));
  }

  function directorGot(got, askedAt) {
    const D = L.dir;
    if (!got || got.error) return directorFailed((got && got.error) || "no answer");
    D.fails = 0;
    mark("direct", got.changed && got.note ? `${got.kind || "when_it_fits"} ${got.about || ""}`.trim() : "no change");
    // A whisper only when its advice changed; the newest replaces one not yet delivered.
    if (got.changed && got.note) D.next = { kind: got.kind || "when_it_fits", about: got.about || null, note: got.note, askedAt, at: now() };
  }

  function directorFailed(why) {
    const D = L.dir;
    D.fails++;
    mark("direct-fail", why);
    if (D.fails >= 2 && !D.down) { D.down = true; D.next = null; D.queued = null; mark("director-down"); }  // the clocks take over
  }

  // Whether a whisper goes now: "send", "hold" or "drop". Pure: tested with any state. A when_it_fits whisper goes at
  // once, even while they talk (it is worded for its moment, and GPT-Live never reads an instruction aloud), unless it
  // is within min_gap_secs of the last whisper (other page notes don't count). A "now" whisper (a clear yes not acted
  // on, quiet asked for and not given) is exempt from the gap, but is dropped if the listener has spoken since the
  // look was asked for (the moment has moved), and held while they are mid-utterance. After the start the offer,
  // the pitch, an interest question or a start are stale; an offer is stale once the guide has said what it does.
  function directorVerdict(n, t, st, C) {
    if (!st.here) return "drop";
    if (st.started && ["offer", "pitch", "interest", "start"].includes(n.about)) return "drop";
    if (n.about === "offer" && (st.explained || st.offered)) return "drop";
    if (n.kind === "now") {
      if (st.heardAt > n.askedAt) return "drop";
      return st.listenerSpeaking ? "hold" : "send";
    }
    if (st.sentAt && t - st.sentAt < (C.min_gap_secs || 0) * 1000) return "drop";
    return "send";
  }

  function directorDeliver(t) {
    const D = L.dir, n = D.next, C = S.director || {};
    const O = S.offer_nudge || {};
    const verdict = directorVerdict(n, t, {
      here: dirHere(), heardAt: L.heardAt, listenerSpeaking: dirSpeaking(t) === "listener", sentAt: D.sentAt,
      explained: !!L.explainedAt, started: !!(L.own || L.startCalled),
      offered: D.offered || !!(O.offered && new RegExp(O.offered, "i").test(L.lastGuideLine || "")),
    }, C);
    if (verdict === "hold") return;
    D.next = null;
    const what = n.about || n.kind;
    if (verdict === "drop") return mark("direct-drop", what);
    D.sentAt = t;
    if (n.about === "offer") D.offered = true;
    mark("direct-sent", what);
    const tpl = n.kind === "now" ? (n.about === "start" ? N.direct_start : N.direct_now || N.direct) : N.direct;
    instruct((tpl || "[{note}]").replace("{note}", n.note));
    // A quiet whisper never rests the page itself (DESIGN: the earpiece does not drive restSoon; review,
    // 2026-11-20): it would rest after whatever line the guide was saying, a question left hanging. The guide's
    // parting words, read by guideSaid (STEP_BACK), or its step_back call rest it as before.
  }

  // --- cues in what was said (Ian's second test, 2026-11-15) -------------------------------------------
  // Delegation misses tools (step_back never came when he asked for quiet), so the page also reads the lines.
  function guideSaid(text, t, began = t) {
    const asks = text.includes("?");
    // Its line about stepping back (one begun after the note, not an "I'm on it" already under way), whatever its
    // words: it now ends by asking whether they'd like to chat first (notes.md started_step_back), so the page waits
    // for their answer (the loop rests after step_back_reply_secs of silence; listenerSaid reads the answer). Run 3's
    // guide paraphrased it with none of the step-back words, and the 12 s fallback rested before he could answer.
    if (L.stepBackAt && (began > L.stepBackAt || L.outAt - L.stepBackAt > 1500) && L.phase === "talk" && !L.pending) {
      L.stepBackAt = 0;
      L.replyWait = { at: t };
      mark("step-back-offer", text.slice(0, 60));
      return;
    }
    // Quiet: the guide's first line after the listener asked for it (or its own plain parting words) says
    // whether it is stepping back; a line that asks something means it isn't. The mic goes off once it has
    // finished, step_back or not, unless the listener speaks first.
    const afterAsk = L.quietAt && t - L.quietAt < 20000;
    if (afterAsk || STEP_BACK.test(text)) {
      L.quietAt = 0;  // the ask is answered by this line, either way
      if (!asks && L.phase === "talk" && !L.pending) { mark("rest-said", text.slice(0, 60)); restSoon(); }
      else if (!asks && L.phase === "checkin" && L.wait && !L.wait.arrived) quietWait();
    }
    // The offer: only the guide's latest question counts, and only one that names the chapter and asks to
    // hear it; its lead-in counts only after the listener answered that question with something not a no.
    if (L.offer && !L.readStarted) {
      const o = L.offer;
      // "Your chapter's ready, want to hear it?", or just "Your chapter's ready whenever you are."
      if (OFFER_Q.test(text) && (!asks || OFFER_ACT.test(text))) { o.askedAt = t; o.replied = 0; o.agreed = false; }
      else if (asks) { o.askedAt = 0; o.agreed = false; }  // something else asked: a yes now answers that
      else if (o.agreed && LEAD_IN.test(text)) { mark("lead-in", text.slice(0, 60)); afterGuide(() => startFromOffer("lead-in"), S.lead_in_max_ms, 1500); }
    }
  }

  // For engaged(): the listener's turns, spoken or typed. GPT-Live transcribes a halting speaker in fragments
  // ("[inhale", "]Yeah", "Um", Ian's third tester run), so a line heard within engaged_merge_ms of the last, with no
  // guide line between, is the same turn; sound tags are not words, and a turn of no words doesn't count.
  function words(text) {
    return text.replace(/\[[^\]]*\]?|\]/g, " ").split(/\s+/).filter((w) => /[a-z0-9]/i.test(w)).length;
  }
  function countLine(text, start = now(), end = start) {
    const n = words(text), last = L.turnWords.length - 1;
    const same = last >= 0 && start - L.turnEnd < (S.engaged_merge_ms || 1500) && !(L.lastGuideAt > L.turnEnd);
    if (same) L.turnWords[last] += n;
    else L.turnWords = L.turnWords.concat([n]).slice(-50);
    L.turnEnd = end;
  }
  function turns() { return L.turnWords.filter((n) => n > 0); }

  function listenerSaid(text, t) {
    // An ask for quiet waits for the guide's next line; anything else said clears it.
    L.quietAt = ["talk", "checkin"].includes(L.phase) && QUIET.test(text) ? t : 0;
    // About to step back after the start, and they say something more than "okay" or "no, go ahead": they'd like to talk.
    const talk = !L.quietAt && text.split(/\s+/).length > 3 && !DECLINE.test(text);
    if (talk) L.talkedAt = t;
    if (L.stepBackAt && talk) { L.stepBackAt = 0; mark("step-back-cancelled"); }
    if (MIC_OFF.test(text)) L.micOffAt = t;
    // Their answer to "...unless you'd like to chat first?": a plain no ("no, go ahead", "okay, thanks") rests once
    // the guide has said its few words; anything else they say keeps the company going (the offer was real). A
    // sound tag or an "um" alone is not an answer yet: the wait goes on. A quiet ask is the quiet rule's (guideSaid).
    if (L.replyWait) {
      const said = text.replace(/\[[^\]]*\]?|\]/g, " ").trim();
      if (said && !FILLER.test(said)) {
        L.replyWait = null;
        if (L.quietAt || MIC_OFF.test(said)) mark("step-back-answer", "quiet");
        else if (declinesChat(said)) { mark("step-back-answer", "no"); restSoon(true); }
        else mark("step-back-cancelled", "chat");
      }
    }
    // Their answer to the guide's offer (the first thing they say after it): a short yes, with no "not yet"
    // in it, starts the reading once the guide's few words of lead-in are said (start_reading usually comes
    // too; whichever is first starts it, once). The yes may open any sentence ("Ha, okay. Yeah, let's hear it.").
    const o = L.offer;
    // Asking for it outright while it's offered ("Yes, let's hear it", "play it"), whatever was asked last.
    if (o && !L.readStarted && READY_ASK.test(text) && !NOT_YET.test(text)) {
      o.replied = t; o.agreed = true;
      mark("ready-yes", text.slice(0, 60));
      return afterGuide(() => startFromOffer("yes"), S.lead_in_max_ms, 4000);
    }
    if (o && !L.readStarted && o.askedAt && !o.replied) {
      o.replied = t;
      const no = NOT_YET.test(text);
      o.agreed = !no;
      const yes = text.split(/\s+/).length <= 12 && text.split(/(?<=[.!?])\s+/).some((s) => YES.test(s));
      if (yes && !no) {
        mark("ready-yes", text.slice(0, 60));
        afterGuide(() => startFromOffer("yes"), S.lead_in_max_ms, 4000);
      }
    }
  }

  // A plain no to "anything else you'd like to chat about first?": short, no question, and decline words only once
  // the sounds and acknowledgements it opens with are set aside, or a no anywhere that doesn't also ask for talk.
  function declinesChat(said) {
    if (said.includes("?") || words(said) > 6) return false;
    const core = said.replace(NO_LEAD, "").trim();
    return NO_CHAT.test(said) || (!!core && NO_CHAT.test(core)) || (NO_ANY.test(said) && !YES_CHAT.test(said));
  }

  // Rest once the guide has finished, unless the listener starts speaking first (a parting line they answer).
  // `lenient` (the step back after a start): only real talk cancels it, not "okay, thanks" or "no, go ahead".
  function restSoon(lenient = false) {
    const at = now();
    afterGuide(() => {
      if (L.inp || (lenient ? L.talkedAt > at : L.heardAt > at)) { mark("rest-cancelled"); return; }
      rest();
    }, 6000, 1500);
  }

  // A check-in they'd rather spend in quiet: the mic off until the next part is ready (arrived() reads on).
  function quietWait() {
    const w = L.wait, at = now();
    afterGuide(() => {
      if (L.wait !== w || L.phase !== "checkin" || w.arrived || L.heardAt > at || L.inp) return;
      mute(true); L.phase = "waiting"; mark("quiet-wait"); render();
    }, 6000, 1500);
  }

  // A line right after a tap made of the passage's next words: the tail of the reading, not an answer.
  function tailOf(a, text, t) {
    if (!a.cut || t - a.t0 > 6000 || a.lastListener) return false;
    const ws = norm(text);
    if (!ws.length) return false;
    const have = new Set(a.cut);
    return ws.filter((w) => have.has(w)).length >= Math.max(1, Math.ceil(ws.length * 0.6));
  }

  // The ready chapter, offered in conversation: the guide says so at a pause, the reading waits for a yes.
  function offer(noted) {
    L.offer = { at: now(), askedAt: noted ? now() : 0, reminded: false };
    mark("offer", noted ? "noted" : "");
    // Said, not left to the guide (Ian's third test: the note came while he was talking to someone else and the
    // guide never mentioned it in three minutes; the play button was the only sign). Speakable context is said
    // word for word (the opening line, the paragraphs); a note is context the guide may let pass.
    if (!noted) say(N.offer_line);
    render();
  }

  // A line the guide says word for word, now (speakable context, as live.opening's first words). Recorded in
  // the transcript as the page's note.
  function say(line) {
    const text = N.opening_words.replace("{words}", line.replace("{title}", (L.chapter && L.chapter.title) || "your chapter"));
    if (L.phase === "resting") { stopWake(); setTimeout(() => { if (L.phase === "resting") listenForWake(); }, 1200); }
    speakable(text);
    mark("say", line.slice(0, 60));
    if (L.session) quietPost("/api/live/line", { session: L.session, who: "page", text });
  }

  // The ready chapter's play button (the bar's; the circle has no play mark: Ian, 2026-11-16): whenever this
  // conversation's chapter is ready and not yet started, in talk as well as resting. The bar is the one way to
  // start it by hand, so it no longer goes away when they tap the circle to talk (it was only shown with the
  // mic off, after Ian's third test, when the circle still played). "Ready" is the opening written, not the
  // offer made: a tap back to talk drops the offer until the guide has made it again in the new session.
  function offerShown() {
    const ready = L.offer || (L.chapter && L.own && L.run === L.own && sectionLocked(1) && !(L.cont && L.cont.begun));
    return !!(ready && !L.readStarted && (L.phase === "resting" || L.phase === "talk"));
  }

  // Play the offered chapter; with no session (it dropped), reconnect first and play, with no second offer.
  async function playOffer(how) {
    if (L.pc) return startFromOffer(how);
    L.wantPlay = true;
    const ok = await connect("play");  // told to say nothing: the reading follows
    L.wantPlay = false;
    if (ok && L.pc) afterGuide(() => startFromOffer(how), S.lead_in_max_ms, 2500);
  }

  async function startFromOffer(how, from = null) {
    if (L.readStarted || !L.dc || L.dc.readyState !== "open" || !["talk", "resting", "leadin"].includes(L.phase)) return;
    if (!L.chapter && L.run) await loadChapter();
    if (L.readStarted || !L.chapter || !sectionLocked(1) || !L.dc || L.dc.readyState !== "open") return;
    L.readStarted = true;
    L.offer = null; L.restAt = 0;
    mark("start-reading", how);
    startReading(from, how);
  }

  // --- the guide's tools ------------------------------------------------------------------------------
  const calls = new Set();
  async function backend(e) {
    if (e.type === "response.completed" && e.response) {
      const u = e.response.usage;
      if (u) {
        L.backend.model = e.response.model || L.backend.model;
        L.backend.input += u.input_tokens || 0;
        L.backend.output += u.output_tokens || 0;
        L.backend.cached += (u.input_tokens_details && u.input_tokens_details.cached_tokens) || 0;
      }
      L.thinking = false; render();
      return;
    }
    if (["response.failed", "response.incomplete", "response.cancelled"].includes(e.type)) { L.thinking = false; render(); return; }
    if (e.type !== "response.output_item.done" || !e.item || e.item.type !== "function_call") return;
    const item = e.item;
    if (calls.has(item.call_id)) return;
    calls.add(item.call_id);
    let args = {};
    try { args = JSON.parse(item.arguments || "{}"); } catch (err) { /* empty */ }
    mark("tool", item.name);
    if (item.name === "start_chapter") { L.startCalled = true; L.promiseAt = 0; }
    let out;
    try {
      out = await post("/api/live/tool", { session: L.session, name: item.name, args, run: L.own });
    } catch (err) {
      out = { error: err.conflict ? "This conversation has ended." : err.message };
    }
    let own = null;
    if (item.name === "start_chapter" && out && out.started && out.run) {  // the run is the page's, not the backend's
      own = out.run;
      out = { ...out };
      delete out.run;
    }
    send("response.item.create", { item: { type: "function_call_output", call_id: item.call_id, output: JSON.stringify(out) } });
    send("response.create");
    if (item.name === "step_back") {  // by phase: talk rests; a check-in waits muted; elsewhere nothing to step back from
      if (L.phase === "talk") restSoon();
      else if (L.phase === "checkin" && L.wait && !L.wait.arrived) quietWait();
    }
    if (item.name === "resume_chapter") resumeChapter(args.rewind || null);
    // (Not once End demo was tapped: the handback's wait is the page's one pending step, and the guide is saying goodbye.)
    if (item.name === "start_reading" && out && out.starting && !L.readStarted && !L.handingBack) afterGuide(() => startFromOffer("tool"), S.lead_in_max_ms, 2500);
    if (own) {
      L.own = own;
      note("Making your chapter…");
      if (L.status.run === own) onStatus(L.status);  // its status may have come first
      if (!L.handingBack) afterStart(out, args);
    }
  }

  // Started (Ian's fifth note): by default the guide steps back while it researches (the mic off, GPT-Live
  // closed), saying they can speak up or tap the circle any time, and asking whether they'd like to chat first (the
  // page waits for that answer: L.replyWait); if they've been chatting easily, it offers to keep them company while
  // it works instead. Engaged: engaged_turns turns so far, their last few averaging engaged_words words or more.
  // Both notes name the topic ({topic}: start_chapter's, as pitched), so the guide can say in a sentence what it
  // will be about (Ian's third tester run: "What would it be about?" went unanswered).
  function engaged() {
    const all = turns(), r = all.slice(-4);
    const avg = r.length ? r.reduce((a, b) => a + b, 0) / r.length : 0;
    return all.length >= (S.engaged_turns || 0) && (!S.engaged_words || avg >= S.engaged_words);
  }

  function afterStart(out, args) {
    const m = out && out.first_audio_minutes;
    const when = m ? `about ${m} minute${m === 1 ? "" : "s"}` : "a few minutes";
    const topic = String((args && args.topic) || (out && (out.topic || out.title)) || "").trim() || "the one you pitched";
    const fill = (note) => note.replace("{when}", when).replace(/\{topic\}/g, topic);
    if (L.dir) L.dir.event = "started";  // the earpiece looks once after the started note (when it is here: company)
    if (engaged()) {
      mark("after-start", "company");
      return instruct(fill(N.started_company));
    }
    mark("after-start", "step back");
    L.stepBackAt = now();
    instruct(fill(N.started_step_back));
  }

  // The guide stepped back: the mic off and GPT-Live closed (it bills per connected minute; Ian's fifth note:
  // "no point wasting GPT-Live credits"). A voice (wake.js, on this machine), a tap or the chapter being ready
  // opens a new session in the same conversation.
  function rest() {
    if (L.phase !== "talk") return;
    // A tool call in flight (start_chapter, say) would be lost with the session: wait for it, up to 8 s.
    if (L.thinking && (!L.restWaitAt || now() - L.restWaitAt < 8000)) {
      L.restWaitAt = L.restWaitAt || now();
      return setTimeout(rest, 400);
    }
    L.restWaitAt = 0; L.stepBackAt = 0; L.replyWait = null;
    dirForget();  // nothing of the earpiece's carries over a rest
    // It said it would start the chapter and hasn't: remind it while the session is still open, then rest.
    if (L.promiseAt && !L.startCalled && !L.own && L.nudges < 2 && L.dc && L.dc.readyState === "open") {
      L.promiseAt = 0; L.nudges++;
      mark("start-nudge", "before rest");
      instruct(N.start_nudge);
      return afterGuide(rest, S.lead_in_max_ms, 4000);
    }
    mute(true);
    L.phase = "resting"; L.restAt = now(); L.wokeAt = 0;
    quietPost("/api/live/line", { session: L.session, who: "page", text: "[The guide stepped back.]" });
    // They asked outright for the mic off: no waking on a sound this time, only a tap (Ian's fourth test: it
    // kept coming back while he talked to his son), and the mic is released. Otherwise the local listener
    // starts after a grace period on the mic kept here (nothing of it is sent anywhere).
    W.tapOnly = !!(L.micOffAt && now() - L.micOffAt < 30000);
    if (W.tapOnly) mark("tap-only");
    closeLive(!W.tapOnly);
    const rest_ = L.restAt;
    setTimeout(() => { if (L.phase === "resting" && L.restAt === rest_) listenForWake(); }, Math.max(800, (S.wake_grace_secs || 0) * 1000));
    render();
  }

  // --- the local listener while resting (page/wake.js, as the ElevenLabs page had: Ian's third test) --------
  // Nothing goes to OpenAI while resting: the mic track sent to the session is off. A copy of it feeds
  // wake.js on this machine, which says "wake" for a sustained sound in the voice band well above the room.
  // A sound then opens the mic with no note (it may not be the listener: the guide only listens) and two soft
  // notes play; no words within wake_listen_secs and it rests again. Two such wakes in a row with nothing said,
  // or voice.wake.max_sound_wakes between taps, and only a tap wakes it.
  const W = { ctx: null, track: null, token: 0, sounds: 0, total: 0, off: false };
  async function listenForWake() {
    stopWake();
    if (W.off || W.tapOnly || !L.mic || L.phase !== "resting") return;
    const token = W.token;
    if (now() - L.spokeAt < 1500 || L.out) {  // the guide still speaking (its voice would wake it, or set the room's level)
      setTimeout(() => { if (token === W.token && L.phase === "resting") listenForWake(); }, 500);
      return;
    }
    let ctx = null, track = null;
    try {
      track = L.mic.clone();
      track.enabled = true;
      ctx = new AudioContext();
      await ctx.audioWorklet.addModule("/wake.js");
      if (ctx.state === "suspended") await ctx.resume();
      if (token !== W.token || L.phase !== "resting") throw new Error("no longer resting");
      const node = new AudioWorkletNode(ctx, "wake", { numberOfOutputs: 0, processorOptions: S.wake || {} });
      node.port.onmessage = () => { if (token === W.token) soundWake(); };
      ctx.createMediaStreamSource(new MediaStream([track])).connect(node);
      W.ctx = ctx; W.track = track;
      mark("wake-listening");
      render();
    } catch (e) {
      mark("wake-failed", String(e && e.message || e));
      if (track) track.stop();
      if (ctx) ctx.close().catch(() => {});
    }
  }

  function stopWake() {
    W.token++;
    if (W.track) W.track.stop();
    if (W.ctx) W.ctx.close().catch(() => {});
    W.ctx = W.track = null;
  }

  async function soundWake() {
    if (L.phase !== "resting" || L.closing) return stopWake();
    if (now() - L.spokeAt < 1500) return listenForWake();  // its own voice: the detector fires once, so start it afresh
    stopWake();
    if (chapterPlayer() || libraryLive()) {  // the library's chapter or its guide: not the listener. Listen again later
      return setTimeout(() => { if (L.phase === "resting") listenForWake(); }, 5000);  // (the detector fires only once)
    }
    W.sounds++; W.total++;
    mark("sound-wake", W.sounds);
    chime();
    if (!L.pc) {  // a new session, told only to listen (it may not have been the listener)
      if (!(await connect("wake")) || L.phase !== "talk") return;
    } else {
      L.phase = "talk"; L.restAt = 0;
      mute(false);
    }
    L.wokeAt = now();
    render();
  }

  function chime() {  // two soft notes: listening again
    try {
      const ctx = new AudioContext(), gain = ctx.createGain(), t0 = ctx.currentTime;
      gain.connect(ctx.destination);
      gain.gain.setValueAtTime(0.0001, t0);
      [[660, 0], [880, 0.13]].forEach(([hz, at]) => {
        const osc = ctx.createOscillator();
        osc.type = "sine"; osc.frequency.value = hz; osc.connect(gain);
        gain.gain.exponentialRampToValueAtTime(0.06, t0 + at + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, t0 + at + 0.12);
        osc.start(t0 + at); osc.stop(t0 + at + 0.13);
      });
      setTimeout(() => ctx.close().catch(() => {}), 600);
    } catch (e) { /* the circle shows it */ }
  }

  function wake() {
    W.sounds = 0; W.total = 0; W.off = false; L.wokeAt = 0;  // a tap: sounds may wake it again
    L.phase = "talk"; L.restAt = 0;
    dirForget();
    instruct(N.wake);
    render();
  }

  // --- the chapter -----------------------------------------------------------------------------------
  const isLive = (s) => s && s.mode === "fast" && s.voice === "live" && s.run;
  const mine_ = (s) => !!(s && s.run && s.run === L.own);  // this conversation's chapter (see the top)
  // The status as this page shows it: this conversation's chapter, or another being made now (one at a
  // time, so the guide can't start one); an older finished, failed or stopped chapter is not shown.
  const shownStatus = () => (mine_(L.status) || L.status.state === "making") ? L.status : { state: "idle" };

  async function loadChapter() {
    if (!L.run) return null;
    try {
      const res = await fetch(`/api/live/chapter/${encodeURIComponent(L.run)}`);
      if (!res.ok) return null;
      L.chapter = await res.json();
    } catch (e) { return null; }
    const known = new Set(L.feed.items.map((x) => x.id));
    L.chapter.sections.forEach((s) => s.paragraphs.forEach((p, i) => {
      if (known.has(p.id)) return;
      L.feed.items.push(item(s.n, i, p, 0));
    }));
    L.feed.items.sort((a, b) => a.n - b.n || a.i - b.i);
    renderMarkers();
    return L.chapter;
  }

  const norm = Align.norm;
  const tokens = new Align.Tokens();  // the reading's transcript, as whole words

  function item(n, i, p, sentence) {  // a paragraph to read, from `sentence` on (a restart mid-paragraph)
    const sentences = p.sentences && p.sentences.length ? p.sentences : [p.text];
    const from = Math.max(0, Math.min(sentence, sentences.length - 1));
    const text = sentence ? sentences.slice(from).join(" ") : p.text;
    const starts = [];  // the word index where each sentence starts, in this item's words
    let w = 0;
    sentences.slice(from).forEach((s) => { starts.push(w); w += norm(s).length; });
    return { id: p.id, n, i, p, sentence: from, text, words: norm(text), starts, matched: 0, heard: "",
             sentAt: 0, startedAt: 0, lastAt: 0, done: false, t0: null, t1: null };
  }

  function sectionLocked(n) {
    const s = L.chapter && L.chapter.sections.find((x) => x.n === n);
    return !!(s && s.locked);
  }
  const lastSection = () => (L.chapter ? Math.max(...L.chapter.sections.map((s) => s.n)) : 0);

  function nextItem() {
    const f = L.feed;
    if (f.next >= f.items.length) return null;
    const it = f.items[f.next];
    f.next += 1;
    return it;
  }

  function sendRead(it) {
    it.sentAt = now();
    L.lastRead = it.n;
    if (it === L.feed.cur || !L.feed.cur) it.curSince = it.sentAt;
    speakable(`${N.read} ${it.text}`);
    mark("read", it.id + (it.sentence ? `#${it.sentence}` : ""));
  }

  function pump() {
    const f = L.feed;
    if (L.phase !== "reading") return;
    if (!f.cur) {
      const nx = f.items[f.next];
      if (nx && L.lastRead && nx.n !== L.lastRead && needsReopen()) return reopen();
      f.cur = nextItem();
      if (!f.cur) return queueEmpty();
      sendRead(f.cur);
    }
    if (f.cur.startedAt && !f.ahead && f.cur.words.length - f.cur.matched <= S.queue_words_left) {
      const nx = f.items[f.next];
      // A new section only once it is written, and not in a session about to be replaced (the boundary
      // above opens the new one first).
      if (nx && (nx.n === f.cur.n || (sectionLocked(nx.n) && !needsReopen()))) {
        f.ahead = nextItem();
        sendRead(f.ahead);
      }
    }
    render();
  }

  // The guide's words while reading, matched to the paragraph being read: a word found a little ahead
  // of where the reading is moves it on; a word not found is kept as what was heard (the check).
  function reading(delta, startMs, endMs) {
    L.deltaAt = now();
    if (startMs != null) L.readMs = L.readMs == null ? startMs : L.readMs;
    if (endMs != null) L.readEnd = endMs;
    readWords(tokens.push(delta), startMs, endMs);
  }

  function readWords(ws, startMs, endMs) {
    const f = L.feed, t = now();
    for (const w of ws) {
      let it = f.cur;
      if (!it) break;
      if (it.done && f.ahead) { it = f.ahead; }
      if (!it.startedAt && !Align.opens(it.words, w)) {  // not its opening: a lead-in still being said
        L.out += (L.out ? " " : "") + w; L.outAt = t;
        continue;
      }
      if (!it.startedAt) {
        it.startedAt = t;
        if (L.resuming) { L.resuming.first_word_ms = t - L.resuming.resume_at; L.resuming = null; }
        if (L.prevEnd) { L.gaps.push(t - L.prevEnd); L.prevEnd = 0; }  // silence between two paragraphs
        if (it === f.cur) pump();
      }
      const j = Align.match(it.words, it.matched, w);
      if (j !== -1) {
        it.matched = j;
        if (it === f.cur && !f.ahead && it.words.length - it.matched <= S.queue_words_left) pump();
      } else if (it === f.cur && f.ahead && it.matched >= it.words.length - 3 && Align.opens(f.ahead.words, w, 3)) {
        it.done = true;  // it skipped its last words: the next paragraph has begun
        finishParagraph(it);
        readWords([w], startMs, endMs);
        continue;
      }
      it.heard += (it.heard ? " " : "") + w;
      it.lastAt = t;
      if (it.t0 == null && startMs != null) it.t0 = startMs;
      if (endMs != null) it.t1 = endMs;
      if (it.matched >= it.words.length && !it.done) { it.done = true; finishParagraph(it); }
    }
  }

  function watchReading(t) {
    const f = L.feed, it = f.cur;
    if (!it) return;
    // The words stopped short of the end: after a while, take it as read (the check logs what was missed).
    // Or it said something else (paraphrased, or talked instead): after a longer quiet, move on all the same.
    if (it.startedAt && !it.done && ((t - it.lastAt > 2500 && it.matched >= it.words.length * 0.6) || t - it.lastAt > 5000)) {
      it.done = true; finishParagraph(it);
    }
    // Sent twice and never started: skip it (logged as heard empty) rather than stop the chapter.
    if (!it.startedAt && it.resent && t - it.curSince > 3 * S.resend_ms && t - L.spokeAt > 2500) {
      mark("skipped", it.id); it.done = true; finishParagraph(it);
    }
    // Sent, but the guide never started it: send it again, once.
    if (!it.startedAt && it.curSince && t - it.curSince > S.resend_ms && !it.resent && t - L.spokeAt > 2500) {
      it.resent = true; mark("resend", it.id); sendRead(it);
    }
    if (t - (L.posAt || 0) > S.position_seconds * 1000) position("playing");
  }

  function finishParagraph(it) {
    const f = L.feed;
    quietPost("/api/live/read", { run: L.run, id: it.id, heard: it.heard, sentence: it.sentence, cut: false,
                                 live_id: L.liveId, times: it.t0 != null ? { start_ms: it.t0, end_ms: it.t1 } : null });
    if (f.cur === it) {
      L.prevEnd = f.ahead && f.ahead.startedAt ? 0 : (it.lastAt || now());
      f.cur = f.ahead; f.ahead = null;
      if (f.cur && !f.cur.curSince) f.cur.curSince = now();
      if (f.cur) position("playing");  // else pump() reports waiting or done straight after (two reports raced)
      pump();  // the next one goes now if it wasn't queued, or the queue is empty
    }
    renderMarkers();
  }

  function where() {  // {id, sentence} of the paragraph being heard
    const it = L.feed.cur;
    if (!it) return null;
    let s = 0;
    it.starts.forEach((w, k) => { if (it.matched > w) s = k; });
    return { id: it.id, sentence: it.sentence + s, n: it.n, i: it.i, item: it };
  }

  function position(state, extra = {}) {
    if (!L.run) return;
    L.posAt = now();
    // Where the reading is, or (no paragraph current: a question, between paragraphs, a stop) where it last
    // was. Never a made-up section 1: a page reopened soon after picks the chapter up from this report.
    let now_ = where();
    if (!now_ && ["paused", "pausing"].includes(L.phase) && L.pausedAt) {  // moved while paused: report where it is
      const x = L.feed.items.find((q) => q.id === L.pausedAt.id);
      if (x) L.lastAt = { id: x.id, sentence: L.pausedAt.sentence || 0, n: x.n, i: x.i, item: x };
    }
    if (now_) L.lastAt = now_;
    const at = now_ || (L.wait ? null : L.lastAt || null);
    const items = L.feed.items;
    const n = at ? at.n : (L.wait ? L.wait.n : 1);
    let t = 0, total = 0;
    for (const x of items) {
      const secs = (x.p.text.split(/\s+/).length) / 2.5;
      if (at && x === at.item) { t += at.item.matched / 2.5; total += at.item.matched / 2.5; break; }
      if (x.n === n) t += secs;
      total += secs;
    }
    quietPost("/api/fast/position", { run: L.run, state, section: n, piece: at ? at.i : 0, t, chapter_t: total, ...extra });
  }
  // The whole chapter's seconds at position()'s pace: what a reading heard to its end reports as heard.
  const chapterSecs = () => L.feed.items.reduce((sum, x) => sum + x.p.text.split(/\s+/).length / 2.5, 0);

  // Ready while the guide was away (resting, GPT-Live closed): a session opens just to say so (the mic stays
  // off), then closes again; play, the circle or a voice brings it back.
  async function readyWhileAway() {
    if (L.readyConnecting || L.readStarted || L.offer) return;  // a rest's old session still closing doesn't matter: it's let go of
    L.readyConnecting = true;
    try {
      if (!(await loadChapter()) || !sectionLocked(1) || L.phase !== "resting" || L.pc) return;
      if (!(await connect("ready")) || L.phase !== "resting") return;
      L.offer = { at: now(), askedAt: 0, reminded: true };
      mark("offer", "away");
      say(N.ready_line_resting);
      afterGuide(() => {
        if (L.phase !== "resting" || L.readStarted || !L.pc) return;
        closeLive(!W.tapOnly);
        if (!W.tapOnly) setTimeout(() => { if (L.phase === "resting") listenForWake(); }, 1500);
      }, 9000, 4000);
    } finally { L.readyConnecting = false; }
  }

  async function beginChapter() {
    if (L.phase === "resting" && !L.pc && L.session && !L.readStarted && !L.offer && (mine_(L.status) || L.run === L.own)) return readyWhileAway();
    if (L.readStarted || L.leading || L.offer || !L.session || !L.dc || L.dc.readyState !== "open" || !["talk", "resting"].includes(L.phase)) return;
    if (!mine_(L.status) && L.run !== L.own) return;  // only this conversation's chapter
    if (!(await loadChapter()) || !sectionLocked(1)) return;
    L.leading = true;
    const t0 = now(), epoch = L.epoch;
    const go = () => {
      L.leading = false;
      if (epoch !== L.epoch || L.readStarted || L.offer || !L.dc || L.dc.readyState !== "open" || !["talk", "resting"].includes(L.phase)) return;
      if (L.phase === "talk") {  // mid-conversation: offered at a pause, read on their yes (Ian's second test)
        const noted = !!(L.cont && L.cont.ready && !L.cont.offered);  // the session's first message already asked
        if (L.cont) L.cont.offered = true;
        return offer(noted);
      }
      // They stepped back (the mic is off): one line that it's ready, the mic stays off, and the play button
      // and the circle start it (Ian's third test: the play button only when the mic is off).
      L.offer = { at: now(), askedAt: 0, reminded: true };
      mark("offer", "resting");
      say(N.ready_line_resting);
      render();
    };
    // Never cut into the conversation: wait for an answer to the guide's question (for a while), then for a
    // pause from both sides, as a check-in hands back.
    const wait = () => {
      if (epoch !== L.epoch) { L.leading = false; return; }
      const t = now();
      const asked = /\?\s*$/.test(L.lastGuideLine || "") && L.lastGuideAt > L.lastListenerAt;
      const quiet = t - L.heardAt > S.handback_quiet_ms && t - L.spokeAt > S.handback_quiet_ms && !L.inp && !L.out;
      if (L.phase === "resting") return go();  // stepped back to wait for it: no conversation to cut into
      const hello = L.sessionWord || t - (L.startedAt || 0) > 6000;  // the session's first line said (or never coming)
      const answering = asked && t - L.lastGuideAt < S.answer_wait_ms && t - t0 < S.answer_wait_ms * 3;  // each question its own wait, capped
      if (!hello || answering || !quiet) return setTimeout(wait, 300);
      go();
    };
    wait();
  }

  // The reading goes on: from the next paragraph, or (`from`) a restart point.
  function logAside(how, restart) {
    const a = L.aside, w = L.wait;
    if (!L.run || !L.session) return;
    if (a) {
      quietPost("/api/live/aside", { session: L.session, run: L.run, kind: "ask", id: a.at ? a.at.id : null,
        sentence: a.at ? a.at.sentence : 0, seconds: (now() - a.t0) / 1000, how, restart, lines: a.lines, live_id: L.liveId });
    } else if (w && w.checkedIn) {
      const nx = L.feed.items.find((x) => x.n === w.n);
      quietPost("/api/live/aside", { session: L.session, run: L.run, kind: "check_in", id: nx ? nx.id : null, sentence: 0,
        seconds: (now() - w.since) / 1000, how, restart: null, lines: w.lines, live_id: L.liveId });
    }
  }

  function startReading(from, how = "guide") {
    L.pending = null;  // whatever else was waiting on the guide (a lead-in, a resume) is done with
    if (!L.dc || L.dc.readyState !== "open") return;  // dropped or ended meanwhile: a tap reconnects
    L.dropAt = null;
    const f = L.feed;
    if (L.aside || (L.wait && L.wait.checkedIn)) logAside(how, from);
    f.cur = f.ahead = null;
    if (from) {
      const k = f.items.findIndex((x) => x.id === from.id);
      if (k !== -1) {
        const base = f.items[k];
        f.items[k] = item(base.n, base.i, base.p, from.sentence || 0);
        // Everything after it is read afresh (the slider or a part's marker can go back over paragraphs
        // already heard: kept as heard, they were queued at once and the reading raced ahead).
        for (let j = k + 1; j < f.items.length; j++) {
          const x = f.items[j];
          if (x.startedAt || x.done || x.sentAt) f.items[j] = item(x.n, x.i, x.p, 0);
        }
        f.next = k;
      }
    }
    L.aside = null; L.wait = null; L.prevEnd = 0; L.pausedAt = null; L.seekTo = null;
    if (L.finished) L.replaying = true;
    tokens.flush();
    L.phase = "reading";
    mute(true);
    pump();
    render();
  }

  // GPT-Live keeps the whole session in its context (it can't drop read paragraphs) and ends a session
  // after its time limit: near either, a new session takes over at a section boundary, told the
  // conversation so far (the server's resume), and reads on.
  function needsReopen() {
    return L.ratio > S.reopen_ratio || (L.expires && L.expires - now() < S.reopen_before_expiry_minutes * 60000);
  }

  async function reopen() {
    if (L.reopening) return;
    L.reopening = true;
    mark("reopen", `ratio ${L.ratio}`);
    const old = L.pc;
    send("session.close");
    reportUsage();
    L.pc = null; L.dc = null;
    setTimeout(() => { try { old && old.close(); } catch (e) { /* closed */ } }, 3000);
    L.ratio = 0; L.expires = 0;
    const mine = L.epoch + 1;  // the epoch connect() takes: anything else by its end means Stop (or a new start) came first
    const ok = await connect("section boundary");
    L.reopening = false;
    if (L.epoch !== mine) return;
    if (ok && L.pc && L.dc) { L.phase = "reading"; mute(true); pump(); }
    else if (L.phase !== "idle" && L.phase !== "done") dropped("reopen failed");
  }

  function queueEmpty() {
    const f = L.feed;
    const last = f.items[f.items.length - 1];
    const nextN = last ? last.n + 1 : 1;
    if (last && last.n >= lastSection() && sectionLocked(last.n) && f.next >= f.items.length) return chapterDone();
    if (L.runEnded) return stoppedTalk();  // nothing more is coming: say so rather than wait for it
    L.phase = "waiting";
    L.prevEnd = 0;
    L.wait = { n: nextN, since: now(), checkedIn: false, lines: [] };
    position("waiting", { waiting_for: nextN, since: Date.now() / 1000, reason: "end_of_audio" });
    mark("waiting", nextN);
    render();
  }

  function watchWait(t) {
    const w = L.wait;
    if (L.phase === "checkin" && w.arrived && !w.leading && !L.pending) {
      // A pause in the conversation (nobody has spoken for a moment, and the listener isn't mid-line):
      // the guide leads in with one line, and the reading goes on once it has said it.
      if (t - L.spokeAt > S.handback_quiet_ms && t - L.heardAt > S.handback_quiet_ms && !L.inp) {
        w.leading = true;
        instruct(N.next_ready);
        afterGuide(() => startReading(null, "ready"), S.lead_in_max_ms, 5000);
      }
      return;
    }
    if (L.phase === "waiting" && !w.checkedIn) {
      const decided = L.status.waiting_for === w.n ? L.status.check_in : null;
      if (decided === "check_in" || t - w.since > 20000) checkIn();
    }
    if (L.phase === "checkin" && !w.silenced && w.lastGuide && !w.lastListener && t - w.lastGuide > S.checkin_silence_secs * 1000
        && t - L.spokeAt > S.checkin_silence_secs * 1000) {
      w.silenced = true;
      instruct(w.arrived ? N.silence : N.checkin_silence);  // nothing to go back to yet: a word, then wait
    }
  }

  async function checkIn() {
    const w = L.wait;
    w.checkedIn = true;
    L.phase = "checkin";
    const earlier = L.checkins.length ? ` Earlier check-ins opened with: ${L.checkins.map((c) => `"${c}"`).join("; ")}.` : "";
    const eta = L.status.eta_seconds ? `${Math.max(1, Math.round(L.status.eta_seconds / 60))} minutes away` : "a few minutes away";
    instruct(N.check_in.replace("{when}", eta).replace("{earlier}", earlier));
    L.checkins.push("");
    const opening = L.checkins.length - 1;
    const t0 = now();
    const grab = setInterval(() => {  // remember how it opened, so the next one opens differently
      if (L.log.length && now() - t0 > 6000) clearInterval(grab);
      const lines = [...document.querySelectorAll("#transcript p.guide")];
      const last = lines[lines.length - 1];
      if (last && L.lineAt > t0) { L.checkins[opening] = last.textContent.slice(0, 80); clearInterval(grab); }
    }, 500);
    position("paused");
    render();
  }

  async function arrived() {  // the part they were waiting for is written
    const w = L.wait;
    if (!w || w.arrived) return;
    await loadChapter();
    if (!sectionLocked(w.n)) return;
    w.arrived = true;
    mark("arrived", w.n);
    if (L.phase === "checkin") {
      w.readyAt = now();  // watchWait asks for the lead-in at the next pause: a conversation is never cut off
    } else {
      setTimeout(() => { if (L.phase === "waiting") startReading(null); }, S.ready_beat_ms);
    }
  }

  // This conversation's chapter stopped (failed, or Stop): what is written is read; then the guide says so.
  function chapterStopped() {
    if (L.runEnded) return;
    L.runEnded = true;
    mark("run-ended", L.status.state);
    if (L.phase === "waiting" || L.phase === "checkin") return stoppedTalk();
    // Before any of it was read (and not being led in, which reads what is written, then says so): tell them now.
    if (L.offer && !L.readStarted) {  // the opening is written and offered: it can still be heard, then this is said
      if (L.phase === "talk" && L.dc && L.dc.readyState === "open") instruct(L.status.state === "stopped" ? N.chapter_halted : N.chapter_stopped);
      return render();
    }
    if (!L.readStarted && !L.leading && L.dc && L.dc.readyState === "open" && ["talk", "resting"].includes(L.phase)) {
      const quiet = L.phase === "resting";
      if (quiet) { L.phase = "talk"; L.restAt = 0; }
      instruct(L.status.state === "stopped" ? N.chapter_halted_early : N.chapter_failed);
      if (quiet) restSoon();  // they'd asked for quiet: the mic off again once it has said so
      render();
    }
  }

  function stoppedTalk() {
    if (L.wait) position("paused");  // before the wait is cleared: the report keeps its section
    const quiet = L.phase === "waiting" && L.muted && L.wait && L.wait.checkedIn;  // a check-in spent in quiet
    L.stalled = L.wait ? L.wait.n : null;
    L.wait = null; L.pending = null;
    L.phase = "talk";
    instruct(L.status.state === "stopped" ? N.chapter_halted : N.chapter_stopped);  // they stopped it, or it failed
    if (quiet) restSoon();
    render();
  }

  // Heard to the end: the session stays a minute (the slider can take them back, or they can talk), then closes.
  function chapterDone() {
    L.finished = true; L.replaying = false;
    mark("done");
    position("done", { chapter_t: chapterSecs() });  // heard to the end (End demo reports where it stopped)
    if (L.tester && !L.debriefDone && !L.handingBack && (L.phase === "reading" || L.phase === "waiting")) return handback("end");  // no end hold
    if (L.phase === "reading" || L.phase === "waiting") { mute(true); L.phase = "ended"; L.endedAt = now(); }
    renderMarkers();
    render();
  }

  // --- the handback to the interviewer (Tester Demo, steps 6-8) -------------------------------------------
  // At the chapter's end, or when the tester taps End demo with the guide there to say goodbye, the guide closes
  // in a sentence or two and says it is handing them back; once it has said so the conversation ends as any other
  // (its cost, the memory pass, the archive of a chapter heard to the end) and the interviewer's debrief starts
  // on the same mic, the screen cooled. `kind`: "end" (the chapter's end), "done" (End demo while the chapter is
  // read or asked about: the reading stops there), "talk" (End demo in conversation; a chapter still being made is stopped
  // by the server as the debrief starts: server.py stop_for_debrief, the handback_making note).
  const HANDED_BACK = /\b(hand(ing)?|pass(ing)?)\b.*\bback\b|\bback to\b|\bwelcomed\b/i;  // its words for handing back
  async function handback(kind) {
    if (L.handingBack || L.debriefing || L.debriefDone) return;  // one debrief per tester: later chapters just end
    L.handingBack = kind;
    L.handbackTries = 0;
    const nth = L.handbacks = (L.handbacks || 0) + 1;
    mark("handback", kind);
    const was = L.phase, t0 = now();
    if (L.aside || (L.wait && L.wait.checkedIn)) logAside("end demo", null);  // the debrief is told what they asked: from this log
    L.aside = null; L.wait = null; L.pending = null; L.offer = null; L.stepBackAt = 0; L.wokeAt = 0; L.promiseAt = 0;
    stopWake();
    let ducked = false;
    if (kind === "done") {
      if (L.run && !L.finished) position("done");  // heard as far as they wanted: never offered again by itself
      if (was === "reading" && L.feed.cur) { cutRead(L.feed.cur); remote.volume = 0; ducked = true; }  // stopped as pause stops it
      L.feed.cur = L.feed.ahead = null;
      tokens.flush();
    }
    L.phase = "handback";
    render();
    // No session to say it in (it dropped a moment ago): straight on to the debrief. A session was once opened
    // here just for the goodbye; End demo now goes on at once when the guide isn't there (Ian, 2026-11-18).
    if (!L.pc || !L.dc || L.dc.readyState !== "open") return finishHandback(kind);
    L.handbackAt = t0;
    if (kind === "done") instruct(N.handback_done);  // mid-reading an instruction stops it, as a tap to ask does (heard live, Ian's first run)
    else {
      // With nothing being read (in conversation, at the chapter's end) the note goes as speakable context, which
      // GPT-Live acts on at once (as the greeting and the handoff note do). As an instruction it was let pass,
      // live (2026-11-18): in conversation the guide had just asked a question and went on waiting for the
      // answer; at the chapter's end it had nothing to answer. Both times it said nothing, and the debrief came
      // after 8 to 12 s of silence. Still silent after a moment: once more as an instruction, then on without it.
      const making = kind === "talk" && L.own && !L.readStarted && mine_(L.status) && L.status.state === "making";
      const text = kind === "end" ? N.handback_end : making ? N.handback_making : N.handback_talk;
      // Tapped while the guide was mid-reply (GPT-Live can't be cut off, and finishes it first: live, 10 s before
      // its goodbye began): silent at once, as the reading is when it is stopped.
      if (kind === "talk" && (now() - L.spokeAt < 600 || L.out)) { L.remoteVolume = remote.volume || 1; remote.volume = 0; ducked = true; }
      if (kind === "talk") mute(false);  // (the chapter's end keeps the mic off, as the reading had it)
      speakable(text);
      mark("handback-note", kind === "end" ? "end" : making ? "making" : "talk");
      if (L.session) quietPost("/api/live/line", { session: L.session, who: "page", text });
      setTimeout(() => {
        if (L.handbacks === nth && L.handingBack === kind && L.pc && !(L.spokeAt > L.handbackAt) && !L.out) instruct(`[${text}]`);
      }, S.handback_again_ms || 3000);
    }
    // (handbackAt: from here on a word of the guide's is its goodbye, not the tail of the reply or passage the tap cut)
    const wait = () => { L.handbackAt = now(); afterGuide(() => handedBack(kind), S.lead_in_max_ms * 2, kind === "done" ? 8000 : (S.handback_silent_ms || 7000)); };
    // Whatever else takes the page's one waiting step meanwhile (a tool call that lands now), the debrief comes.
    setTimeout(() => { if (L.handbacks === nth && L.handingBack === kind && !L.debriefing) finishHandback(kind); }, S.lead_in_max_ms * 3 + 8000);
    if (!ducked) return wait();
    const unduck = () => {  // the listener hears the reading (or the reply) stop at once; its close is heard
      if (L.handbacks !== nth || L.handingBack !== kind) return;
      const quiet = now() - L.spokeAt > S.stop_quiet_ms;
      // In conversation, a reply still going after this long isn't waited out in silence: on to the debrief.
      if (kind === "talk" && !quiet && now() - t0 > 2500) return finishHandback(kind);
      if (quiet || now() - t0 > 2500) { remote.volume = L.remoteVolume || 1; wait(); }
      else setTimeout(unduck, 100);
    };
    setTimeout(unduck, S.stop_quiet_ms);
  }

  // Its line has ended: if it hasn't yet said it is handing them back (a pause between its sentences), once more.
  // If it never spoke at all, the debrief comes without its goodbye (no second wait in silence).
  function handedBack(kind) {
    if (L.handingBack !== kind) return;
    if (!HANDED_BACK.test(L.lastGuideLine || "") && L.handbackTries < 1 && L.pc && L.spokeAt > L.handbackAt) {
      L.handbackTries++;
      return afterGuide(() => handedBack(kind), S.lead_in_max_ms, 4000);
    }
    finishHandback(kind);
  }

  const micLive = (s) => !!(s && s.getAudioTracks().some((t) => t.readyState === "live"));
  function askMic() {  // a promise of the mic, or null if it is refused
    try {
      return navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } })
        .catch(() => null);
    } catch (e) { return Promise.resolve(null); }
  }

  // `mic`: a promise of the mic asked for in the tap (End demo with none held: resting tap-only, paused, a page
  // they came back to).
  async function finishHandback(kind, mic = null) {
    if (L.handingBack !== kind || L.debriefing || L.finishingBack) return;
    L.finishingBack = true;
    if (L.ending) await until(() => !L.ending, 5000);  // the conversation was already ending (Stop listening): after it
    if (L.handingBack !== kind || L.debriefing) { L.finishingBack = false; return; }
    mark("handed-back", kind);
    L.pending = null;
    let stream = micLive(L.stream) ? L.stream : null;
    // The interviewer's session opens while the guide's closes (its close, up to 3 s, and its cost report were
    // waited for before the debrief even began to connect). Whatever the guide is still saying isn't heard over it.
    remote.volume = 0;
    const ending = endConversation(!!stream).then(() => { remote.volume = 1; });
    if (!stream) stream = await (mic || askMic());  // released while it rested or paused: asked for again
    L.finishingBack = false;
    if (!stream) {  // refused: the line asks for it, and End demo (or Try again) asks once more
      await ending;
      L.handingBack = null;
      L.micRefused = "debrief";
      mark("debrief", "no mic");
      return render();
    }
    L.micRefused = false;
    stream.getAudioTracks().forEach((t) => { t.enabled = true; });  // disabled while the chapter was read, perhaps: the interviewer must hear them from its first word
    debrief({ stream });
    await ending;
    L.handingBack = null;
  }

  // The interviewer's debrief (page/interview.js, window.Interview): the screen cools while it runs (the product
  // fades to the plain ground, the circle back to the neutral dot), and the library comes up after it.
  function debrief(opts = {}) {
    if (L.debriefing) return;
    const stream = opts.stream || null;
    L.debriefing = true;
    skin("debrief", true);
    render();
    scrollTo(0, 0);  // the circle above is the interviewer's dot while it talks (the library's card did this)
    const finish = () => {
      if (!L.debriefing) return;
      L.debriefing = false;
      if (stream) stream.getTracks().forEach((t) => t.stop());  // the mic is released once the debrief is over
      skin("debrief", false);
      render();
      loadDemo();  // done: End demo is gone for good; dropped part-way: it is there to go back to it
      if (window.Library && Library.reload) Library.reload();
      const lib = $("library");
      if (lib && lib.scrollIntoView) lib.scrollIntoView({ behavior: "smooth", block: "start" });
      if (opts.onDone) try { opts.onDone(); } catch (e) { /* the caller's own */ }
    };
    if (!window.Interview || !Interview.start) { mark("debrief", "no interviewer"); return finish(); }
    mark("debrief", "start");
    try {
      const p = Interview.start("debrief", { stream, onDone: finish });
      if (p && p.catch) p.catch((e) => { mark("debrief-failed", String(e && e.message || e)); finish(); });
    } catch (e) { mark("debrief-failed", String(e && e.message || e)); finish(); }
  }

  function skin(cls, on) { if (on) L.skin.add(cls); else L.skin.delete(cls); }

  // --- End demo (Tester Demo; Ian's second run, 2026-11-18) ---------------------------------------------
  // "The Got five minutes to tell Ian how it went? isn't the right method, there should be a more prominent
  // button, maybe in the upper right that says end demo and visible while the onboarding and chapter is being
  // read, not only after. The transition should happen by default, they can close the window if they don't want
  // to do it." So one button (#end-demo) replaced I'm done (shown only during the chapter) and the library's
  // debrief card. A tester sees it from the moment the guide's screen is revealed: through the conversation,
  // while the chapter is made (talking or resting) and while it is read, paused or asked about. Not during the
  // interviewer's intro (the gate covers this page) or the debrief, and never again once the debrief is done.
  // A tap goes on to the debrief, with nothing to confirm: with the guide's session open the guide closes in a
  // sentence and hands back (handback); with none (resting, paused, idle) the debrief starts at once. A chapter
  // still being made is stopped as the debrief starts (server.py stop_for_debrief, Ian's third run, 2026-11-19); the
  // library shows it as stopped. One that has finished is untouched.
  // A tester back on a page of their own (not handed over here) sees it as Ian's setting for them says
  // (/api/live/demo, the admin page's "End demo" control): always, never, or (auto) once they have a chapter or
  // have started a conversation here.
  function endDemoShown() {
    if (!L.tester || L.debriefDone || L.debriefing || L.handingBack) return false;  // (handingBack: tapped, or the chapter's end)
    if (L.skin.has("handoff") && !L.skin.has("revealed")) return false;  // the interviewer's last words: the reveal hasn't begun
    if (L.handedOver) return true;
    const d = L.demo;
    return !!(d && !d.done && d.setting !== "off" && (d.show || L.demoBegun));
  }

  async function loadDemo() {
    if (!L.tester) return;
    try {
      const res = await fetch("/api/live/demo");
      if (!res.ok) return;
      L.demo = await res.json();
      L.debriefDone = !!L.demo.done;
      render();
    } catch (e) { /* asked again after the next debrief, or when a conversation starts (connect) */ }
  }

  // Where the tester is, in the words the server's account of their session uses (study/events.jsonl).
  function demoPhase() {
    switch (L.phase) {
      case "reading": case "seeking": case "leadin": case "waiting": case "checkin": return "reading";  // (a check-in: the wait for the next part, the guide talking; they asked nothing)
      case "aside": return "aside";
      case "paused": case "pausing": return "paused";
      case "ended": case "done": return "ended";
      case "resting": return "resting";
      case "talk": case "connecting": return L.own && !L.readStarted ? "making" : "talk";
    }
    return null;
  }

  // What the tester did, for the debrief (Ian, 2026-11-18: "pass a bit of context about the things that it did or
  // buttons it pressed, like if they asked a question"): each press is appended to their study/events.jsonl by
  // the server. Tester mode only; called before the press is acted on (the phase is the one they pressed it in);
  // never waited for, and a failure is nobody's problem.
  function report(event, detail = null) {
    if (!L.tester) return;
    try {
      let at = null;
      if (L.readStarted && L.chapter) { const tl = timeline(); at = tl ? playedSecs(tl) : null; }
      fetch("/api/demo/event", { method: "POST", headers: { "Content-Type": "application/json" }, keepalive: true,
                                 body: JSON.stringify({ event, phase: demoPhase(), chapter_t: at == null ? null : Math.round(at),
                                                        detail: detail == null ? null : String(detail) }) }).catch(() => {});
    } catch (e) { /* never the page's problem */ }
  }

  const SAYS_GOODBYE = ["talk", "reading", "aside", "checkin", "waiting", "seeking", "leadin", "ended"];
  function endDemo() {
    if (!endDemoShown()) return;  // also a second tap, and a tap during the handback or the debrief
    report("end_demo");
    mark("end-demo", L.phase);
    try {
      if (window.Interview && Interview.unlock) Interview.unlock();  // inside the tap: its voice may play (phones)
      const lib = $("chapter-audio");  // the library below: its player stops, and any session of its own
      if (lib && !lib.paused) lib.pause();
      if (window.Library && Library.busy && Library.busy()) Library.end();
      const open = L.pc && L.dc && L.dc.readyState === "open" && L.started && !L.closing;
      if (open && SAYS_GOODBYE.includes(L.phase)) {
        return handback(["talk", "ended"].includes(L.phase) ? "talk" : "done");
      }
      // The guide isn't there (idle, still connecting, resting, paused): the debrief starts at once.
      const mic = micLive(L.stream) ? null : askMic();  // asked for inside the tap (a phone wants it there)
      if (L.run && L.readStarted && !L.finished) position("done");  // heard as far as they wanted: never offered again by itself
      L.handingBack = "now";
      mark("handback", "now");
      if (L.aside || (L.wait && L.wait.checkedIn)) logAside("end demo", null);  // a question the connection dropped under
      L.aside = null; L.wait = null; L.pending = null; L.offer = null; L.stepBackAt = 0; L.wokeAt = 0; L.promiseAt = 0;
      stopWake();
      L.phase = "handback";  // nothing else opens a session meanwhile (a sound, the chapter coming ready)
      return finishHandback("now", mic);
    } finally { render(); }  // handingBack (then debriefing) hides the button from here
  }

  // Handed over by the interviewer (page/interview.js on the gate, contract "Demo chapter"): the gate's mic stream
  // is reused (the tap's unlock), the guide's session opens with the handoff note, and the screen is revealed:
  // the neutral dot warms to the coral circle while it connects; the rest fades in at the guide's first word.
  // `hold`: called when the handover is near (the interviewer is saying its last words): the session opens now,
  // silent and deaf, and a second call without `hold`, with the name as known then, lets the guide greet them.
  function handoff({ name, stream, hold } = {}) {
    // The name goes into a note to the guide: a name's length, no brackets or braces.
    const who = String(name || "").replace(/[\[\]{}<>"]/g, "").replace(/\s+/g, " ").trim().slice(0, 60);
    if (L.hold) {
      const held = L.hold;
      held.who.name = who;
      if (!hold) { held.let = true; held.go(true); }  // let go of: from here a failure must show (below)
      return held.done;
    }
    L.tester = true;
    L.handedOver = true;  // End demo shows from the reveal
    skin("tester", true);
    L.handoff = { name: who };
    if (stream) { L.stream = stream; L.mic = stream.getAudioTracks()[0] || null; meters.input(stream); }
    skin("handoff", true);
    render();
    requestAnimationFrame(() => requestAnimationFrame(() => { skin("warm", true); render(); }));  // after a frame: the fill's transition runs
    let held = null;
    if (hold) { held = L.hold = { who: L.handoff }; held.released = new Promise((r) => { held.go = r; }); }
    const done = connect().then((ok) => {
      if (L.hold === held) L.hold = null;  // it couldn't open: the handover's own call starts again
      // It couldn't open: the screen (with its "Voice couldn't start") comes up, unless this was only the
      // session opened ahead and the handover hasn't come (its own call starts again). A held connect that
      // failed after it was let go of once left a tester with a bare circle.
      if (!ok && (!held || held.let)) { reveal(); $("transcript").classList.add("open"); }  // the reason, in sight
      return ok;
    });
    if (held) held.done = done;
    return done;
  }

  // Where the circle is and its colour, for the screen before this one: its dot grows into exactly this.
  function spot() {
    const r = core.getBoundingClientRect();
    return { x: r.left, y: r.top, w: r.width, h: r.height, color: getComputedStyle(core).getPropertyValue("--accent").trim() };
  }

  function reveal() {
    if (!L.skin.has("handoff") || L.skin.has("revealed")) return;
    skin("revealed", true);
    mark("revealed");
    render();
    setTimeout(() => { ["handoff", "warm", "revealed"].forEach((c) => skin(c, false)); render(); }, 2000);
  }

  // --- a tap mid-chapter: a question, in the same session -----------------------------------------------
  // `paused`: a tap while paused asks from the paused place (nothing is being read to stop).
  async function ask(paused = null) {
    const at = paused || where();
    L.remoteVolume = remote.volume || 1;
    if (!paused) remote.volume = 0;  // the listener hears it stop at once; the session stops a moment later
    const it = paused ? null : L.feed.cur;
    // The passage's next words: if the guide says a few more of them after the tap, that is the reading's
    // tail, not an answer (tailOf).
    const cut = it ? it.words.slice(it.matched, it.matched + 60).concat(L.feed.ahead ? L.feed.ahead.words.slice(0, 30) : []) : null;
    if (!L.finished && !paused) position("paused");  // while where() still sees the paragraph: a reopened page resumes here
    L.feed.cur = L.feed.ahead = null;
    L.pausedAt = null;
    tokens.flush();
    L.phase = "aside";
    L.aside = { t0: now(), at, cut, lastGuide: 0, lastListener: 0, silenced: false, lines: [] };
    instruct(N.tapped);
    mark("ask", at && at.id);
    if (it) quietPost("/api/live/read", { run: L.run, id: it.id, heard: it.heard, sentence: it.sentence, cut: true,
                                         live_id: L.liveId, times: it.t0 != null ? { start_ms: it.t0, end_ms: it.t1 } : null });
    render();
    const a = L.aside;
    const unduck = () => {
      if (now() - L.spokeAt > S.stop_quiet_ms || now() - a.t0 > 2500) {
        remote.volume = L.remoteVolume || 1;
        a.stopMs = Math.max(0, Math.min(L.spokeAt, now()) - a.t0);  // the last word of the reading after the tap
      } else setTimeout(unduck, 100);
    };
    setTimeout(unduck, S.stop_quiet_ms);
    if (at && L.run) {
      let ctx = null;
      try { ctx = await post("/api/live/context", { run: L.run, id: at.id, sentence: at.sentence }); }
      catch (e) { mark("context-failed", e.message); }
      // Only once they have started asking: sent before, GPT-Live talked about it unasked.
      await until(() => L.aside !== a || a.lastListener || (L.inp && L.inAt > a.t0), 120000);
      if (ctx && L.aside === a) ctx.chunks.forEach((c, k) => think((k === 0 ? N.context + "\n" : "") + c));
    }
  }

  function watchAside(t) {
    const a = L.aside;
    // A tap, then nobody speaks: the chapter stays paused and the mic goes off (play reads on). Never
    // back to the reading by itself when they haven't said a word (Ian's second test).
    if (!L.inp && !L.pending && t - Math.max(a.t0, L.spokeAt, L.heardAt) > S.aside_quiet_secs * 1000) {
      // A long quiet ends any aside: back to the chapter if a question was answered, else paused (session closed).
      mark("aside-quiet", a.at && a.at.id);
      if (a.lastListener && a.lastGuide > a.lastListener) return resumeChapter(null, "unasked");
      return toPaused(a.at, "quiet");
    }
    // They asked, the guide spoke (or only stalled), then silence: the page says so (answer if you haven't;
    // else go back), at most twice. Not while the backend is still working on something.
    const spoke = a.lastGuide || a.stalledAt;
    if (!a.silenced && !L.thinking && (a.silences || 0) < 2 && a.lastListener && spoke && Math.max(a.lastGuide, a.stalledAt || 0) > a.lastListener
        && t - L.spokeAt > S.silence_after_answer_secs * 1000 && t - L.heardAt > S.silence_after_answer_secs * 1000 && !L.inp) {
      a.silenced = true; a.silencedAt = t; a.silences = (a.silences || 0) + 1;
      instruct(a.lastGuide > a.lastListener ? N.silence : N.unanswered);
      return;
    }
    // Told about the silence and still nothing (GPT-Live led back in words but never called
    // resume_chapter, in about one aside in three): the reading goes on by itself.
    if (a.silenced && !L.pending && !L.thinking && a.lastGuide > a.lastListener && t - a.silencedAt > S.aside_give_up_secs * 1000
        && t - L.spokeAt > 3000 && t - L.heardAt > 3000 && !L.inp) {
      mark("resume-unasked", a.at && a.at.id);
      resumeChapter(null, "unasked");
    }
  }

  async function resumeChapter(rewind, how = "guide") {
    // resume_chapter: the guide has led back in (or is saying it); the reading goes on once it has finished.
    if (L.phase === "checkin" && L.wait) {
      if (!L.wait.arrived || L.wait.leading) return;  // nothing to go back to yet, or already on its way
      L.wait.leading = true;
      const w = L.wait;
      afterGuide(() => { if (L.wait === w && L.phase === "checkin") startReading(null, "guide"); }, S.lead_in_max_ms, 1500);
      return;
    }
    if (L.phase !== "aside" || !L.aside || L.aside.resuming) return;
    const a = L.aside;
    a.resuming = true;  // the tool, a lead-back and the silence fallback can all ask: one restart
    let from = a.at ? { id: a.at.id, sentence: a.at.sentence } : null;
    if (a.at && L.run) {
      try {
        from = await post("/api/live/restart", { run: L.run, id: a.at.id, sentence: a.at.sentence,
                                                seconds: (now() - a.t0) / 1000, rewind });
      } catch (e) { /* the interrupted sentence, then */ }
      if (L.aside !== a || L.phase !== "aside") return;  // over while we asked (the button, a drop)
    }
    const rec = { seconds: (now() - a.t0) / 1000, at: a.at && a.at.id, from, rewind, stop_ms: a.stopMs, resume_at: now() };
    L.asides.push(rec);
    afterGuide(() => {
      if (L.aside !== a || L.phase !== "aside") return;
      // A lead-back the listener then answered with a question of their own: stay and talk.
      if (how !== "button" && L.heardAt > rec.resume_at) {
        L.asides.pop(); a.resuming = false; mark("lead-back-cancelled"); return;
      }
      L.resuming = rec; startReading(from, how);
    }, S.lead_in_max_ms, 1500);
  }

  function backToChapter() {  // the listener tapped "Back to the chapter"
    // The note goes with the mic open (GPT-Live ignores notes while muted) a moment before the reading
    // mutes it again.
    const go = (from, check) => {
      instruct(N.back);
      const epoch = L.epoch;
      L.backing = true;
      setTimeout(() => { L.backing = false; if (epoch === L.epoch && check()) startReading(from, "button"); }, 300);
    };
    if (L.backing) return;
    if (L.phase === "checkin") {
      if (!L.wait.arrived) return;
      const w = L.wait;
      return go(null, () => L.wait === w && L.phase === "checkin");
    }
    const a = L.aside;
    if (a) go(a.at ? { id: a.at.id, sentence: a.at.sentence } : null, () => L.aside === a && L.phase === "aside");
  }

  // --- play, pause and the slider (Ian's second test: there is no audio file, so the slider is the script
  // at an estimated pace; moving it stops the guide and feeds the reading from that sentence) -----------
  function cutRead(it) {  // a paragraph stopped part-way, for the reading check (and the archive's choice)
    if (it && L.run) quietPost("/api/live/read", { run: L.run, id: it.id, heard: it.heard, sentence: it.sentence, cut: true,
                                                  live_id: L.liveId, times: it.t0 != null ? { start_ms: it.t0, end_ms: it.t1 } : null });
  }

  // Stop the guide mid-sentence: the audio is ducked at once and the note asks it to stop (GPT-Live acts on
  // notes only with the mic open, so it opens for a moment); `then` once it has gone quiet.
  function hush(text, then) {
    const t0 = now(), epoch = L.epoch;
    remote.volume = 0;
    cutRead(L.feed.cur);
    const k = L.feed.cur ? L.feed.items.indexOf(L.feed.cur) : -1;
    if (k !== -1) L.feed.next = k;  // never past the cut paragraph (nextFrom, a later startReading(null))
    L.feed.cur = L.feed.ahead = null;
    tokens.flush();
    instruct(text);
    const wait = () => {
      if (epoch !== L.epoch) { remote.volume = 1; L.seekTo = null; return; }
      if ((now() - L.spokeAt > S.stop_quiet_ms && now() - t0 > S.stop_quiet_ms) || now() - t0 > 2500) { remote.volume = 1; then(); }
      else setTimeout(wait, 100);
    };
    setTimeout(wait, S.stop_quiet_ms);
  }

  function nextFrom() {  // where the reading would go on from, with nothing current
    const nx = L.feed.items[L.feed.next];
    return nx ? { id: nx.id, sentence: nx.sentence || 0 } : L.lastAt ? { id: L.lastAt.id, sentence: L.lastAt.sentence } : null;
  }

  // Paused: GPT-Live is closed, not just muted (Ian's third test: "be sure GPT-Live is not running when the
  // narration is paused"). Closing also stops any word it was still saying. Play opens a new session, told to
  // say nothing, and reads on from the paused sentence; a tap asks from there the same way.
  function toPaused(at, how) {
    if (L.aside) { logAside(how, null); L.aside = null; }
    L.pending = null;
    L.pausedAt = at ? { id: at.id, sentence: at.sentence || 0 } : nextFrom();
    L.phase = "paused"; L.pauseAt = now();
    position("paused");
    closeLive();
    render();
  }

  // Paused: wait for the pause's own close to finish (a tap within it asked the dying session, live check);
  // true if still paused with no session.
  async function closed() {
    await until(() => !L.closing, 3000);
    if (L.phase === "paused" && L.pc && !L.closing) await closeLive();
    return L.phase === "paused" && !L.pc;
  }

  // The session closed and the mic released; the conversation (server side) goes on. The audio is cut at once
  // (the mic track off, the voice muted); the session's own close waits a moment for its last usage count.
  // The page lets go of the session at once (so a tap, play or the chapter in the next moment opens a new one,
  // never acting on this one); its close, and its last usage count, are waited for on the side.
  async function closeLive(keepMic = false) {
    const pc = L.pc, dc = L.dc;
    if (L.mic) L.mic.enabled = false;
    L.muted = true;
    if (!keepMic && L.stream) { L.stream.getTracks().forEach((x) => x.stop()); L.stream = L.mic = null; }
    if (!pc && !dc) return;
    const snap = { session: L.session, live_id: L.liveId, seconds: L.seconds, backend: L.backend.model ? { ...L.backend } : null };
    reported = L.liveId;  // reported below, once
    L.seconds = 0; L.backend = { model: null, input: 0, output: 0, cached: 0 };
    L.pc = null; L.dc = null; L.thinking = false;
    L.closing = true;
    if (dc && dc.readyState === "open") {
      try { dc.send(JSON.stringify({ type: "session.close", event_id: `p${++seq}_${now() % 1e7}` })); } catch (e) { /* closed */ }
      await until(() => dc.closedAt || dc.readyState !== "open", 1500);
    }
    if (dc && dc.seconds != null) snap.seconds = dc.seconds;
    if (snap.session && snap.live_id) quietPost("/api/live/usage", snap);
    try { if (pc) pc.close(); } catch (e) { /* closed */ }
    L.closing = false;
    setTimeout(() => { remote.volume = 1; }, 300);  // ducked for the stop; the next session's voice is heard
  }

  function pauseReading() {
    if (L.phase !== "reading") return;
    const at = where();
    position("paused");  // while where() still sees the paragraph
    const from = at ? { id: at.id, sentence: at.sentence } : nextFrom();
    remote.volume = 0;  // silent at once
    cutRead(L.feed.cur);
    const k = L.feed.cur ? L.feed.items.indexOf(L.feed.cur) : -1;
    if (k !== -1) L.feed.next = k;
    L.feed.cur = L.feed.ahead = null;
    tokens.flush();
    mark("pause", from && from.id);
    toPaused(from, "pause");
  }

  async function play() {
    switch (L.phase) {
      case "paused": {
        const from = L.pausedAt || nextFrom();
        if (!(await closed())) return;
        if (!L.pc) {  // the pause closed the session: a new one carries on, told to say nothing
          if (!(await connect("paused"))) return;
        }
        if (L.phase !== "paused") return;
        L.pausedAt = null;
        return startReading(from, "play");
      }
      case "aside": case "checkin": return backToChapter();
      case "ended": {  // heard to the end: from the start again
        const first = L.feed.items[0];
        return first ? startReading({ id: first.id, sentence: 0 }, "replay") : null;
      }
      case "talk": case "resting": case "leadin":
        if (!L.readStarted && L.chapter && sectionLocked(1)) return playOffer("button");
        if (L.readStarted && L.pc && L.phase !== "leadin") {  // after the end, or the chapter stopped: from where it was
          const first = L.feed.items[0];
          const from = L.finished ? (first && { id: first.id, sentence: 0 }) : (L.dropAt || (L.lastAt && { id: L.lastAt.id, sentence: L.lastAt.sentence }) || nextFrom());
          if (from) return moveTo(from, "play");
        }
    }
  }

  // Stop whatever the guide is saying and read from `to`.
  function moveTo(to, how) {
    if (L.aside) { logAside(how, to); L.aside = null; }
    L.seekTo = to;
    L.phase = "seeking";
    render();
    hush(N.moved, () => { L.seekTo = null; if (L.phase === "seeking") startReading(to, how); });
  }

  // The chapter as a timeline at the reading's estimated pace: every written paragraph (its sentences'
  // words), then the sections still being written at their outline's length. `written`: where the
  // written part (the first sections, in order) ends.
  function timeline() {
    const ch = L.chapter;
    if (!ch) return null;
    const outline = (ch.outline && ch.outline.sections) || [];
    const paras = [], lens = [];
    let t = 0, written = 0, open = true;
    for (const s of ch.sections) {
      if (s.locked && s.paragraphs.length) {
        const t0 = t;
        s.paragraphs.forEach((p, i) => {
          const sents = (p.sentences && p.sentences.length ? p.sentences : [p.text]).map((x) => norm(x).length);
          const words = Math.max(1, sents.reduce((a, b) => a + b, 0));
          paras.push({ id: p.id, n: s.n, i, start: t, dur: words / WPS, words, sents });
          t += words / WPS;
        });
        lens.push(t - t0);
        if (open) written = t;
      } else {
        open = false;
        const o = outline.find((x) => x.n === s.n);
        t += o && o.target_words ? o.target_words / WPS : (lens.length ? lens.reduce((a, b) => a + b, 0) / lens.length : 150);
      }
    }
    return { paras, total: t, written };
  }

  function timeOf(tl, at) {  // seconds into the chapter of {id, sentence[, words more]}
    const p = at && tl.paras.find((x) => x.id === at.id);
    if (!p) return null;
    const before = p.sents.slice(0, at.sentence || 0).reduce((a, b) => a + b, 0) + (at.more || 0);
    return p.start + Math.min(p.words, before) / WPS;
  }

  function placeAt(tl, secs) {  // {id, sentence} of the sentence playing at `secs`, inside the written part
    const end = Math.max(0, tl.written - 0.01);
    const s = Math.max(0, Math.min(secs, end));
    const p = tl.paras.find((x) => s < x.start + x.dur) || tl.paras[tl.paras.length - 1];
    if (!p) return null;
    let w = (s - p.start) * WPS, k = 0;
    while (k < p.sents.length - 1 && w >= p.sents[k]) { w -= p.sents[k]; k++; }
    return { id: p.id, sentence: k };
  }

  function playedSecs(tl) {  // where the listener is, on the timeline
    if (L.seekTo) return timeOf(tl, L.seekTo);
    const w = where();
    if (w && w.item && w.item.startedAt) return timeOf(tl, { id: w.id, sentence: w.item.sentence, more: w.item.matched });
    const at = (["paused", "pausing"].includes(L.phase) && L.pausedAt) || (L.aside && L.aside.at) || (w && { id: w.id, sentence: w.sentence });
    if (at) return timeOf(tl, at);
    if (L.finished && ["ended", "done"].includes(L.phase)) return tl.written;
    if (L.wait) return tl.written;
    return L.lastAt ? timeOf(tl, L.lastAt) : 0;
  }

  function seek(frac) {
    const tl = timeline();
    if (!tl || !tl.paras.length) return;
    const to = placeAt(tl, frac * tl.total);
    if (!to) return;
    mark("seek", `${to.id}#${to.sentence}`);
    switch (L.phase) {
      case "paused": L.pausedAt = to; position("paused"); return render();
      case "reading": case "aside": case "checkin": return moveTo(to, "seek");
      case "waiting": case "ended": return startReading(to, "seek");
      case "talk": case "resting": case "leadin":
        if (!L.readStarted) return startFromOffer("seek", to);
        if (L.pc && L.phase !== "leadin") return moveTo(to, "seek");  // the chapter stopped, or talk after the end
    }
  }

  // --- the page --------------------------------------------------------------------------------------
  function onStatus(s) {
    L.status = s;
    const mine = mine_(s);
    if (mine && s.state === "making" && s.label && s.label !== L.lastLabel) note(s.label + "…", "stage");
    if (mine && s.state === "problem" && L.lastState !== "problem") note(s.message || "Something went wrong.");
    if (mine && isLive(s)) {
      if (L.run !== s.run) { L.run = s.run; L.ringMax = 0; L.chapter = null; L.chapterAt = ""; L.feed = { items: [], next: 0, cur: null, ahead: null }; L.readStarted = false; L.finished = false; L.runEnded = false; }
      const locked = (s.sections || []).filter((x) => x.text).map((x) => x.n).join(",");
      if (locked !== L.chapterAt) {
        L.chapterAt = locked;
        if (L.readStarted) loadChapter().then(() => { if (L.wait) arrived(); });
      }
      if (s.state === "making" && L.runEnded) {  // tried again: what it writes now is read, after a word from the guide
        L.runEnded = false;
        if (L.readStarted && !L.finished && !L.wait && ["talk", "resting"].includes(L.phase) && L.pc) {
          const nx = L.feed.items[L.feed.next], last = L.feed.items[L.feed.items.length - 1];
          L.wait = { n: L.stalled || (nx ? nx.n : (last ? last.n + 1 : 1)), since: now(), checkedIn: true, lines: [] };
          L.phase = "checkin"; L.restAt = 0;
          if (L.muted) mute(false);
          mark("retried", L.wait.n);
        }
      }
      if (!L.readStarted && !(L.cont && L.cont.begun) && (s.sections || []).some((x) => x.n === 1 && x.text)) beginChapter();
    }
    if (mine && ["problem", "stopped"].includes(s.state) && !L.finished) chapterStopped();
    if (mine || s.state === "making") { L.lastState = s.state; L.lastLabel = s.label; }
    render();
  }
  new EventSource("/api/events").onmessage = (e) => onStatus(JSON.parse(e.data));

  const meters = {
    ctx: null, inp: null, out: null,
    analyser(stream) {
      try {
        this.ctx = this.ctx || new AudioContext();
        const a = this.ctx.createAnalyser();
        a.fftSize = 512;
        this.ctx.createMediaStreamSource(stream).connect(a);
        return a;
      } catch (e) { return null; }
    },
    input(stream) { this.inp = this.analyser(stream); },
    output(stream) { this.out = this.analyser(stream); },
    level(a) {
      if (!a) return 0;
      const d = new Uint8Array(a.fftSize);
      a.getByteTimeDomainData(d);
      let sum = 0;
      for (const v of d) sum += ((v - 128) / 128) ** 2;
      return Math.sqrt(sum / d.length);
    },
  };

  function animate() {
    const speaking = now() - L.spokeAt < 400;
    const v = speaking ? meters.level(meters.out) : (L.muted ? 0 : meters.level(meters.inp));
    const base = L.ringOn ? 0.82 : 1;
    core.style.transform = L.pc ? `scale(${base * (1 + Math.min(1, (v || 0) * 4) * 0.2)})` : "";
    requestAnimationFrame(animate);
  }
  requestAnimationFrame(animate);

  function lineText() {
    if (L.handingBack || L.debriefing) return "";  // the guide's goodbye, or the debrief: no line (endConversation sets idle under them)
    const s = shownStatus();
    const title = (L.chapter && L.chapter.title) || "Your chapter";
    switch (L.phase) {
      case "connecting": return "Connecting…";
      case "resting":
        if (L.offer) return `${title} is ready`;
        if (mine_(s) && ["problem", "stopped"].includes(L.status.state)) return L.status.message || "Your chapter stopped.";
        return "Tap to talk";
      case "leadin": return title;
      case "reading": return "Tap to ask a question";
      case "aside": return "Listening · tap to go back to the chapter";
      case "pausing": case "paused": return "Paused · tap to ask a question";
      case "seeking": return title;
      case "checkin": return L.wait && L.wait.arrived ? "Back to the chapter" : "The next part is on its way";
      case "waiting": return "The next part is on its way · Talk while you wait";
      case "ended": case "done": return `${title} · finished`;
      case "handback": return "";
    }
    if (L.offer) return `${title} is ready`;
    if (L.micRefused && L.phase === "idle") return "I need your microphone to talk with you";  // the gate's words
    if (s.state === "making") return s.label || "Working";
    if (s.state === "problem") return s.message || "Something went wrong.";
    if (L.phase === "talk") return "";
    return "Tap to begin";
  }

  function render() {
    const s = shownStatus();
    const shown = s.state === "making" ? "making" : s.state === "problem" ? "problem" : "idle";
    const speaking = now() - L.spokeAt < 600;
    document.body.className = [shown, L.phase, L.pc ? "voice" : "", L.pc ? (speaking ? "speaking" : "listening") : "",
      L.thinking ? "thinking" : "", L.phase === "resting" ? "resting" : "", L.typing ? "typing" : "",
      L.phase !== "idle" ? "talking" : "", offerShown() ? "offer" : "", endDemoShown() ? "end-demo" : "",
      L.micRefused ? "mic-refused" : "",
      ...L.skin].filter(Boolean).join(" ");
    $("line").textContent = lineText();
    renderSteps();
    const voiceWake = L.phase === "resting" && L.mic && !W.off && !W.tapOnly;
    $("how").textContent = voiceWake ? "Say something, or tap, to talk" : "Tap to talk";  // the circle talks; play is in the bar
    renderRing();
    renderPlayer();
    renderMarkers();
  }
  setInterval(render, 500);

  // While this conversation's chapter is made, before its opening is written: the steps to it (status.steps:
  // done ticked, the current one pulsing), as the fast-start page showed them (page/fast.js). No card: what the
  // chapter will be is the library's to show, once it is written (Ian, after his tester run, 2026-11-16).
  const STEP_SR = { done: ", done", doing: ", now", todo: "" };
  function renderSteps() {
    const list = $("steps"), s = L.status;
    const steps = mine_(s) && s.state === "making" && !L.readStarted && !L.offer && !s.first_audio
      && !["idle", "done", "handback"].includes(L.phase) && Array.isArray(s.steps) ? s.steps : [];
    list.hidden = !steps.length;
    while (list.children.length > steps.length) list.lastChild.remove();
    steps.forEach((x, k) => {
      let li = list.children[k];
      if (!li) { li = document.createElement("li"); li.innerHTML = '<span class="mark" aria-hidden="true"></span><span></span>'; list.appendChild(li); }
      if (li.dataset.state !== x.state) li.dataset.state = x.state;
      if (li.lastChild.textContent !== x.label) li.lastChild.textContent = x.label;
      li.setAttribute("aria-label", x.label + (STEP_SR[x.state] || ""));
      if (x.state === "doing") li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
    });
  }

  // The ring: before the opening is ready, how close it is (from the run's start and its countdown, or a
  // plain estimate, never going backwards: Ian's second test, it sat empty until the outline was done);
  // ready, full; while the chapter is read, where the listener is in it. Another conversation's chapter
  // being made: its stages.
  function openingFrac(s) {
    if (s.first_audio || (s.sections || []).some((x) => x.n === 1 && x.text)) return 1;
    const t = now(), started = Date.parse(s.started || "") || t, updated = Date.parse(s.updated || "") || t;
    const gone = Math.max(0, (t - started) / 1000);
    let left = s.first_audio_in != null ? Math.max(0, s.first_audio_in - (t - updated) / 1000) : null;
    if (left == null || left === 0) left = Math.max((S.opening_estimate_secs || 240) - gone, gone * 0.15);
    const f = Math.min(0.97, gone / Math.max(1, gone + left));
    L.ringMax = Math.max(L.ringMax || 0, f);
    return L.ringMax;
  }

  function ringFrac() {
    const s = shownStatus();
    if (L.readStarted && L.chapter && !["idle", "done"].includes(L.phase)) {
      const tl = timeline();
      return tl && tl.total ? Math.min(1, playedSecs(tl) / tl.total) : 0;
    }
    if (mine_(s) && ["making", "ready"].includes(s.state) && ["talk", "resting", "leadin", "connecting"].includes(L.phase)) return openingFrac(s);
    if (s.state === "making" && !mine_(s)) return s.stages_total ? s.stages_done / s.stages_total : 0;
    return null;
  }

  function renderRing() {
    const f = ringFrac();
    L.ringOn = f != null;
    document.body.classList.toggle("ring", L.ringOn);
    if (L.ringOn) ring.style.strokeDashoffset = CIRC * (1 - Math.max(0, Math.min(1, f)));
  }

  const fmt = (secs) => { const s = Math.max(0, Math.round(secs)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
  function renderPlayer() {
    const box = $("player");
    const tl = L.chapter && L.own && L.run === L.own ? timeline() : null;
    const on = !!(tl && tl.paras.length && !["idle", "done"].includes(L.phase) && (L.phase !== "connecting" || L.readStarted)
                  && (L.readStarted || offerShown()));
    box.hidden = !on;
    document.body.classList.toggle("live-bar", on);  // the library's bar gives way, and the page ends above this one
    document.documentElement.style.setProperty("--live-bar-h", on ? `${box.offsetHeight}px` : "0px");
    if (!on) { L.dragging = false; return; }
    $("play-title").textContent = L.chapter.title || "Your chapter";
    const open = L.phase !== "connecting";
    const between = ["talk", "resting"].includes(L.phase) && L.readStarted && !!L.pc;  // stopped, or after the end: play reads on
    const playing = ["reading", "seeking", "pausing"].includes(L.phase);
    const canPlay = open && (["paused", "aside", "ended"].includes(L.phase) || (L.phase === "checkin" && L.wait && L.wait.arrived)
      || (!L.readStarted && ["talk", "resting"].includes(L.phase)) || between);
    const b = $("play");
    b.dataset.state = playing ? "pause" : "play";
    b.setAttribute("aria-label", playing ? "Pause" : "Play");
    b.disabled = playing ? L.phase !== "reading" : !canPlay;
    const at = playedSecs(tl), seek = $("seek");
    const pct = (x) => `${(100 * Math.max(0, Math.min(1, x / tl.total))).toFixed(2)}%`;
    seek.style.setProperty("--played", pct(at));
    seek.style.setProperty("--written", pct(tl.written));
    seek.disabled = !(["reading", "paused", "aside", "checkin", "waiting", "ended"].includes(L.phase) || between
                      || (!L.readStarted && L.offer && ["talk", "resting"].includes(L.phase)));
    if (!L.dragging) seek.value = String(Math.round(1000 * Math.min(at, tl.total) / tl.total));
    const shown = L.dragging ? Number(seek.value) / 1000 * tl.total : at;
    $("time").textContent = `${fmt(shown)} / ${tl.written < tl.total ? "~" : ""}${fmt(tl.total)}`;
  }

  function markerState(sec) {
    const at = where();
    if (at && at.n === sec.n) return "playing";
    if (!sec.locked) return "being_written";
    const items = L.feed.items.filter((x) => x.n === sec.n);
    const lastI = items.length ? L.feed.items.indexOf(items[items.length - 1]) : -1;
    if (lastI !== -1 && lastI < L.feed.next - (L.feed.cur ? 1 : 0) - (L.feed.ahead ? 1 : 0)) return "played";
    return "ready";
  }
  const STATE_WORD = { played: "played", playing: "playing", ready: "ready", being_written: "being written" };
  function renderMarkers() {
    const list = $("sections");
    // Once the opening is written and while it is read: every part and where it stands. From the run's first
    // audio with the chapter loaded, not only from the offer (in talk the offer waits for a pause, and neither
    // the steps nor the parts showed meanwhile).
    const written = L.readStarted || L.offer || (mine_(L.status) && L.status.first_audio);
    if (!L.chapter || L.run !== L.own || !written || ["idle", "done"].includes(L.phase)) { list.style.display = "none"; return; }
    list.style.display = "";
    while (list.children.length > L.chapter.sections.length) list.lastChild.remove();
    L.chapter.sections.forEach((sec, k) => {
      let li = list.children[k];
      if (!li) { li = document.createElement("li"); li.innerHTML = '<button><span class="title"></span><span class="state"></span></button>'; list.appendChild(li); }
      const b = li.firstChild, state = markerState(sec);
      b.dataset.state = state; b.dataset.n = sec.n;
      b.querySelector(".title").textContent = sec.title || `Part ${sec.n}`;
      b.querySelector(".state").textContent = STATE_WORD[state];
    });
  }
  $("sections").addEventListener("click", (e) => {  // a written part: read from its start
    const b = e.target.closest("button");
    if (!b || !L.pc || !["reading", "waiting"].includes(L.phase)) return;
    const n = Number(b.dataset.n);
    if (!sectionLocked(n)) {  // the next part, not written yet: skip to it, and wait for it (the check-in)
      const at = L.feed.cur ? L.feed.cur.n : L.lastRead;
      if (L.phase !== "reading" || n !== at + 1) return;
      L.phase = "seeking";
      hush(N.moved, () => { if (L.phase !== "seeking") return; L.feed.next = L.feed.items.length; mute(true); queueEmpty(); });
      return;
    }
    const first = L.feed.items.find((x) => x.n === n);
    if (!first) return;
    report("seek", `part ${n}`);
    if (L.phase === "waiting") return startReading({ id: first.id, sentence: 0 }, "section");
    L.phase = "seeking";  // stop what it is reading (the mic opens for it; startReading mutes it again)
    hush(N.moved, () => { if (L.phase === "seeking") startReading({ id: first.id, sentence: 0 }, "section"); });
  });
  $("play").addEventListener("click", () => {
    report(L.phase === "reading" ? "pause" : "play");
    return L.phase === "reading" ? pauseReading() : play();
  });
  $("seek").addEventListener("input", () => { L.dragging = true; renderPlayer(); });
  $("seek").addEventListener("change", () => {
    L.dragging = false;
    const frac = Number($("seek").value) / 1000, tl = timeline();
    report("seek", tl ? `to ${fmt(frac * tl.total)}` : null);
    seek(frac);
  });
  ["pointerup", "pointercancel", "blur"].forEach((ev) => $("seek").addEventListener(ev, () => { L.dragging = false; }));

  // --- typed -----------------------------------------------------------------------------------------
  async function typedChat(message) {  // no voice here: the typed guide (Claude, /api/chat)
    $("transcript").classList.add("open");
    L.thinking = true; render();
    try {
      let res;
      if (!L.chatSession && L.session) L.chatSession = L.session;  // voice dropped: the same conversation, typed
      if (!L.chatSession) { res = await post("/api/chat/start"); L.chatSession = res.session; note(res.reply, "guide"); }
      if (message) { res = await post("/api/chat", { message, session: L.chatSession }); note(res.reply, "guide"); }
    } catch (err) { note(err.message); }
    L.thinking = false; render();
  }

  $("form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("say").value.trim();
    if (!text) return;
    $("say").value = "";
    note("You: " + text, "you");
    if (L.phase === "paused") {  // typed while paused: a question from there, in a new live session
      const at = L.pausedAt;
      if (!(await closed()) || !(await connect("paused-ask")) || L.phase !== "paused") return;
      ask(at);
    }
    if (L.phase === "resting" && !L.pc && L.session) {  // stepped back (GPT-Live closed): typing brings it back
      W.sounds = 0; W.total = 0; W.off = false;
      if (!(await connect(L.restFrom === "drop" ? "dropped" : "tap"))) return typedChat(text);
    }
    if (!L.pc) return typedChat(text);
    if (L.session) quietPost("/api/live/line", { session: L.session, who: "listener", text });
    L.heardAt = L.inAt = L.lastListenerAt = L.lineEndAt = now();  // typed counts as said for every cue
    L.turnEnd = 0;  // a typed line is its own turn: never merged on timing
    countLine(text);
    remember("listener", text);
    directorLine(true);
    listenerSaid(text, now());
    if (L.phase === "waiting" && L.wait) { L.wait.checkedIn = true; L.phase = "checkin"; }
    if (L.phase === "reading") ask();
    const conv = L.aside || L.wait;  // typed counts as said: the silent context and the aside's lines
    if (conv) { conv.lastListener = now(); conv.lines.push({ who: "listener", text }); }
    if (L.phase === "resting" || L.phase === "ended") { L.phase = "talk"; L.restAt = 0; }
    instruct(N.typed.replace("{text}", text));
  });

  $("orb").addEventListener("click", async () => {
    if (L.debriefing || L.handingBack || L.skin.has("handoff")) return;  // the interviewer's turn, or the guide's goodbye
    switch (L.phase) {
      case "idle":
      case "done":  // a new conversation (connect forgets the finished chapter: it is never read again by itself)
        if (!(await connect()) && !L.tester) { L.typing = true; render(); typedChat(null); }  // testers can't type
        return;
      // The circle is for talking, never for play (Ian, 2026-11-16): a ready chapter starts from the bar's play button.
      case "talk": return rest();  // the mic off (tap to talk again)
      case "resting":
        report("tap_talk");
        if (!L.pc) { W.sounds = 0; W.total = 0; W.off = false; return connect(L.restFrom === "drop" ? "dropped" : "tap"); }
        return wake();
      case "reading": report("ask"); return ask();
      case "paused": {  // ask from where it paused, in a new session (the pause closed the last one)
        report("ask");
        const at = L.pausedAt;
        if (!(await closed())) return;
        if ((await connect("paused-ask")) && L.phase === "paused") ask(at);
        return;
      }
      case "ended": return wake();  // talk about it
      case "aside": return backToChapter();
      case "checkin": return backToChapter();
      case "waiting": L.wait.checkedIn = true; return checkIn();
    }
  });
  $("stop").addEventListener("click", endConversation);
  // End demo (tester mode): on to the interviewer's debrief (endDemo, above).
  $("end-demo").addEventListener("click", endDemo);
  if (L.tester) loadDemo();  // back on a page of their own: whether the button shows (Ian's setting, the debrief done)
  // A tester's mic refused: Try again asks for it once more, from this tap (phones need a gesture).
  $("mic-again").addEventListener("click", (e) => {
    e.stopPropagation();
    if (L.micRefused === "debrief") return endDemo();  // refused for the debrief: End demo again
    if (L.micRefused && L.phase === "idle") connect();
  });
  // A tester's page talks with the interviewer at the end: its script, unless the gate page brought it already.
  if (L.tester && !window.Interview) {
    const s = document.createElement("script");
    s.src = "/interview.js";
    document.body.appendChild(s);
  }
  $("type").addEventListener("click", () => {
    L.typing = !L.typing;
    $("type").setAttribute("aria-pressed", L.typing);
    render();
    if (L.typing) $("say").focus();
  });
  // The transcript button shows only once there is something to show (Ian, 2026-11-15).
  new MutationObserver(() => { $("toggle").hidden = !$("transcript").textContent.trim(); })
    .observe($("transcript"), { childList: true, subtree: true, characterData: true });
  $("toggle").hidden = !$("transcript").textContent.trim();

  // The library below (page/library.js) has its own player (#chapter-audio) and live sessions: one voice at a
  // time. It ends this conversation (window.Orb) before it plays or opens the mic; this page pauses its player
  // and ends its session before connecting, and a sound never wakes the guide while a chapter plays there.
  window.Orb = { busy: () => !!(L.pc || L.phase === "connecting"), end: () => endConversation() };
  const chapterPlayer = () => { const c = $("chapter-audio"); return !!(c && !c.paused); };
  const libraryLive = () => !!(window.Library && Library.busy && Library.busy());

  $("toggle").addEventListener("click", () => {
    const open = $("transcript").classList.toggle("open");
    $("toggle").textContent = open ? "Hide transcript" : "Show transcript";
    $("toggle").setAttribute("aria-expanded", open);
  });
  $("halt").addEventListener("click", async () => {
    if (!L.status.run || !confirm("Stop making this chapter?")) return;
    $("halt").disabled = true;
    try { await post(`/api/chapters/${encodeURIComponent(L.status.run)}/stop`); } catch (err) { note(err.message); $("halt").disabled = false; }
  });
  $("retry").addEventListener("click", async () => {
    try { await post(`/api/chapters/${encodeURIComponent(L.status.run)}/retry`); } catch (err) { note(err.message); }
  });
  $("reset").addEventListener("click", async () => {
    if (!confirm("Start fresh as a new listener? The guide will meet you for the first time."
                 + " Nothing is deleted: the current listener's memory and chapters stay as they are.")) return;
    let out;
    await reportUsage();  // before the server forgets the conversation
    try { out = await post("/api/reset"); } catch (err) { note(err.message); return; }
    L.session = null;
    await endConversation();
    $("transcript").textContent = "";
    note("Started fresh. The next conversation is a first meeting.");
    if (window.Library && Library.reload) Library.reload();  // the new listener's library
  });
  addEventListener("pagehide", () => {
    if (L.dc && L.dc.readyState === "open") send("session.close");
    if (L.session && L.liveId && reported !== L.liveId) {
      navigator.sendBeacon("/api/live/usage", new Blob([JSON.stringify({ session: L.session, live_id: L.liveId, seconds: L.seconds, backend: L.backend.model ? L.backend : null })], { type: "application/json" }));
    }
    if (L.session) navigator.sendBeacon("/api/chat/end", new Blob([JSON.stringify({ session: L.session })], { type: "application/json" }));
  });

  Object.assign(window.Live, { handoff, spot, handback, debrief, endDemo, endDemoShown, loadDemo, directorVerdict, declinesChat, engaged, turns,
                               onEvent, connect, endConversation, ask, startReading, where, beginChapter, onStatus,
                               pauseReading, play, seek, timeline, placeAt, playedSecs: () => { const tl = timeline(); return tl ? playedSecs(tl) : null; } });
})();
