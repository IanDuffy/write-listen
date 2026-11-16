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
  const MIC_OFF = new RegExp(S.mic_off_ask, "i");           // "turn off your mic": then only a tap wakes it
  const STALL = new RegExp(S.stall, "i");
  const STEPPING = /\b(step (back|away)|tap (my|the) (circle|dot)|speak up)\b/i;  // the guide's step-back line, question or not
  const DECLINE = /^\W*(no|nope|nah|okay|ok|sure|thanks|thank you|sounds good|go ahead|i'?m good|all good|perfect|great|not really|that'?s fine)\b/i;                   // "Let me give that a thought": not an answer (the whole line)
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
    spokeAt: 0, heardAt: 0,           // the last guide word, the last listener word (any phase)
    seconds: 0, ratio: 0, backend: { model: null, input: 0, output: 0, cached: 0 },
    run: null, chapter: null, chapterAt: "",
    own: null,              // the run this conversation may read (start_chapter's, or the one picked back up)
    cont: null,             // /api/live/start's `continue`: {run, title, id, sentence, waiting}
    startCalled: false, promiseAt: 0, nudges: 0,  // start_chapter in this conversation; a promise without it
    lastGuideLine: "", lastGuideAt: 0, lastListenerAt: 0,  // the last whole lines (a question waiting for its answer)
    feed: { items: [], next: 0, cur: null, ahead: null },
    readStarted: false, finished: false,
    offer: null,            // the ready chapter offered in conversation: {at, askedAt, reminded}
    pausedAt: null,         // paused: {id, sentence} the reading goes on from
    convAt: 0, pitchNudges: 0, helloChecked: false, quietAt: 0,  // the conversation's start; nudges sent
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
    handoffOpen: null,      // this conversation's session opened with the handoff note: the name in it
    handingBack: null,      // "end" | "done": the guide is closing, then the interviewer's debrief
    debriefing: false,      // the interviewer's debrief is on (the screen cooled)
    card: null,             // this conversation's chapter card (/api/live/card)
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
    if (L.session) quietPost("/api/live/line", { session: L.session, who: "page", text });  // in the transcript, so a line has its cause
  }
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
      note("No microphone, so let's type instead.");
      if (resume && resume.startsWith("paused")) { notLive(resume); render(); }
      else if (resume) { L.phase = "resting"; render(); }
      return false;
    }
    if (mine !== L.epoch) { if (!L.pc && L.phase !== "connecting") notLive(null); return true; }
    const pc = new RTCPeerConnection();
    L.pc = pc;
    pc.ontrack = (e) => { remote.srcObject = e.streams[0]; meters.output(e.streams[0]); };
    if (L.mic) {
      if (sayOnly) L.mic.enabled = false;
      pc.addTrack(L.mic, L.stream);
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
      if (!resume && got.handoff && L.handoff) { L.handoffOpen = L.handoff.name; L.handoff = null; }  // kept for a retry until it opens
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
    if (!resume && L.handoffOpen) {  // handed over by the interviewer: the session's first message greets them by name
      const first = N.handoff.replace(/\{name\}/g, L.handoffOpen);
      setTimeout(() => { if (silent()) instruct(`[${first}]`); }, S.greet_fallback_ms);  // alone it may not make it speak
    } else if (!resume && !L.cont) {
      speakable(S.opening ? N.opening_words.replace("{words}", S.opening) : N.opened);
      setTimeout(() => { if (silent()) instruct(N.opened); }, S.greet_fallback_ms);  // nothing yet: once more, the mic open
    } else if (!resume && L.cont) {  // its first message is the startup input, which alone may not make it speak
      const first = (L.cont.begun ? N.reloaded : (L.cont.ready ? N.ready_unheard : N.reopened)).replace("{title}", L.cont.title || "their chapter");
      setTimeout(() => { if (silent()) instruct(`[${first}]`); }, S.greet_fallback_ms);
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
    L.runEnded = false; L.leading = false; L.lastAt = null; L.stalled = null; L.lastGuideLine = ""; L.lastGuideAt = 0; L.lastListenerAt = 0;
    L.offer = null; L.pausedAt = null; L.convAt = 0; L.pitchNudges = 0; L.helloChecked = false; L.quietAt = 0; L.ringMax = 0;
    L.seekTo = null; L.replaying = false; L.ending = false; L.wokeAt = 0;
    L.heardLines = 0; L.recentWords = []; L.stepBackAt = 0;
    L.handoffOpen = null; L.card = null; L.handingBack = null;
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
        L.lastGuideLine = text; L.lastGuideAt = t;
        guideSaid(text, t, L.lineAt || t);
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
        L.lastListenerAt = t;
        countLine(text);
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
    if (L.dc && L.dc.readyState === "open") conversationCues(t);
    if (L.phase === "paused" && L.pc && !L.closing) closeLive();  // never open while paused (a session that came up meanwhile)
    if (L.phase === "ended" && t - L.endedAt > S.end_hold_secs * 1000) endConversation();
    if (L.phase === "reading") watchReading(t);
    if (L.aside && L.phase === "aside") watchAside(t);
    if (L.wait && (L.phase === "waiting" || L.phase === "checkin")) watchWait(t);
    if (L.phase === "resting" && L.restAt && t - L.restAt > S.rest_close_minutes * 60000) endConversation();
    // The handoff's reveal: the screen fades in at the guide's first word (or, if it never comes, a while after
    // the session started, so a tester is never left with a dot alone).
    if (L.skin.has("handoff") && !L.skin.has("revealed")
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
  function conversationCues(t) {
    if (L.phase !== "talk" || L.pending) return;
    const theyTalk = L.inp && t - L.inAt < 600 && !L.out && t - L.spokeAt > 1500;
    const lull = !L.inp && !L.out && t - L.spokeAt > 4000 && t - L.heardAt > 4000;
    if (!L.cont && !L.helloChecked && L.sessionWord && L.lastGuideAt && !(L.heardAt > L.convAt) && !L.inp
        && t - L.spokeAt > S.hello_silence_secs * 1000) {
      L.helloChecked = true;
      mark("hello-silence");
      return instruct(N.hello_silence);
    }
    const nudges = S.pitch_nudge_secs || [];
    if (!L.own && !L.startCalled && !L.cont && L.convAt && L.pitchNudges < nudges.length
        && t - L.convAt > nudges[L.pitchNudges] * 1000 && (theyTalk || lull)) {
      L.pitchNudges++;
      mark("pitch-nudge", L.pitchNudges);
      return instruct(L.pitchNudges === 1 ? N.pitch_nudge : N.pitch_nudge_again);
    }
    // Nobody has said anything for a long while: the mic goes off (tap to talk), as if they'd asked for quiet.
    if (S.talk_rest_secs && !L.inp && !L.out && t - L.spokeAt > S.talk_rest_secs * 1000 && t - L.heardAt > S.talk_rest_secs * 1000
        && t - (L.convAt || 0) > S.talk_rest_secs * 1000) {
      mark("silence-rest");
      return rest();
    }
    if (L.offer && !L.readStarted && !L.offer.reminded && t - L.offer.at > S.ready_remind_secs * 1000 && lull) {
      L.offer.reminded = true;
      mark("ready-again");
      instruct(N.ready_again);
    }
  }

  // --- cues in what was said (Ian's second test, 2026-11-15) -------------------------------------------
  // Delegation misses tools (step_back never came when he asked for quiet), so the page also reads the lines.
  function guideSaid(text, t, began = t) {
    const asks = text.includes("?");
    // Its line about stepping back (one begun after the note, not an "I'm on it" already under way): then the mic off.
    if (L.stepBackAt && (began > L.stepBackAt || L.outAt - L.stepBackAt > 1500) && L.phase === "talk" && !L.pending
        && (!asks || STEPPING.test(text))) {
      L.stepBackAt = 0;
      mark("rest-said", "after start");
      return restSoon(true);
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

  function countLine(text) {  // for engaged(): spoken or typed
    L.heardLines = (L.heardLines || 0) + 1;
    L.recentWords = (L.recentWords || []).concat([text.split(/\s+/).length]).slice(-4);
  }

  function listenerSaid(text, t) {
    // An ask for quiet waits for the guide's next line; anything else said clears it.
    L.quietAt = ["talk", "checkin"].includes(L.phase) && QUIET.test(text) ? t : 0;
    // About to step back after the start, and they say something more than "okay" or "no, go ahead": they'd like to talk.
    const talk = !L.quietAt && text.split(/\s+/).length > 3 && !DECLINE.test(text);
    if (talk) L.talkedAt = t;
    if (L.stepBackAt && talk) { L.stepBackAt = 0; mark("step-back-cancelled"); }
    if (MIC_OFF.test(text)) L.micOffAt = t;
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

  // The ready chapter's play button and the circle's play mark: only with the mic off (Ian's third test), or
  // just woken by a sound with nothing said yet (they may be reaching for play).
  function offerShown() {
    return !!(L.offer && !L.readStarted && (L.phase === "resting" || (L.phase === "talk" && L.wokeAt && !(L.heardAt > L.wokeAt))));
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
    if (item.name === "start_reading" && out && out.starting && !L.readStarted) afterGuide(() => startFromOffer("tool"), S.lead_in_max_ms, 2500);
    if (own) {
      L.own = own;
      note("Making your chapter…");
      if (L.status.run === own) onStatus(L.status);  // its status may have come first
      afterStart(out);
    }
  }

  // Started (Ian's fifth note): by default the guide steps back while it researches (the mic off, GPT-Live
  // closed), saying they can speak up or tap the circle any time; if they've been chatting easily, it offers
  // to keep them company while it works instead. Engaged: engaged_lines lines so far, their last few
  // averaging engaged_words words or more.
  function engaged() {
    const r = L.recentWords || [];
    const words = r.length ? r.reduce((a, b) => a + b, 0) / r.length : 0;
    return (L.heardLines || 0) >= S.engaged_lines && (!S.engaged_words || words >= S.engaged_words);
  }

  function afterStart(out) {
    const m = out && out.first_audio_minutes;
    const when = m ? `about ${m} minute${m === 1 ? "" : "s"}` : "a few minutes";
    if (engaged()) {
      mark("after-start", "company");
      return instruct(N.started_company.replace("{when}", when));
    }
    mark("after-start", "step back");
    L.stepBackAt = now();
    instruct(N.started_step_back.replace("{when}", when));
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
    L.restWaitAt = 0; L.stepBackAt = 0;
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
    position("done");
    if (L.tester && !L.handingBack && (L.phase === "reading" || L.phase === "waiting")) return handback("end");  // no end hold
    if (L.phase === "reading" || L.phase === "waiting") { mute(true); L.phase = "ended"; L.endedAt = now(); }
    renderMarkers();
    render();
  }

  // --- the handback to the interviewer (Tester Demo, steps 6-8) -------------------------------------------
  // At the chapter's end, or when the tester taps I'm done, the guide closes in a sentence or two and says it is
  // handing them back; once it has said so the conversation ends as any other (its cost, the memory pass, the
  // archive of a chapter heard to the end) and the interviewer's debrief starts on the same mic, the screen cooled.
  const HANDED_BACK = /\b(hand(ing)?|pass(ing)?)\b.*\bback\b|\bback to\b|\bwelcomed\b/i;  // its words for handing back
  async function handback(kind) {
    if (L.handingBack || L.debriefing) return;
    L.handingBack = kind;
    L.handbackTries = 0;
    mark("handback", kind);
    const was = L.phase, t0 = now();
    L.aside = null; L.wait = null; L.pending = null; L.offer = null; L.stepBackAt = 0; L.wokeAt = 0;
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
    if (!L.pc || !L.dc || L.dc.readyState !== "open") {  // paused or resting: GPT-Live was closed; a session to say it in
      await until(() => !L.closing, 3000);
      const ok = await connect("paused");  // told to say nothing until given words (`continue`)
      if (!ok || !L.pc || L.handingBack !== kind) return finishHandback(kind);
      L.phase = "handback";
    }
    instruct(kind === "done" ? N.handback_done : N.handback_end);
    const wait = () => afterGuide(() => handedBack(kind), S.lead_in_max_ms * 2, 8000);
    if (!ducked) return wait();
    const unduck = () => {  // the listener hears the reading stop at once; its close is heard
      if (now() - L.spokeAt > S.stop_quiet_ms || now() - t0 > 2500) { remote.volume = L.remoteVolume || 1; wait(); }
      else setTimeout(unduck, 100);
    };
    setTimeout(unduck, S.stop_quiet_ms);
  }

  // Its line has ended: if it hasn't yet said it is handing them back (a pause between its sentences), once more.
  function handedBack(kind) {
    if (L.handingBack !== kind) return;
    if (!HANDED_BACK.test(L.lastGuideLine || "") && L.handbackTries < 1 && L.pc) {
      L.handbackTries++;
      return afterGuide(() => handedBack(kind), S.lead_in_max_ms, 4000);
    }
    finishHandback(kind);
  }

  async function finishHandback(kind) {
    if (L.handingBack !== kind || L.ending) return;
    mark("handed-back", kind);
    const stream = L.stream;
    L.pending = null;
    await endConversation(true);
    L.handingBack = null;
    debrief({ stream });
  }

  // The interviewer's debrief (page/interview.js, window.Interview): the screen cools while it runs (the product
  // fades to the plain ground, the circle back to the neutral dot), and the library comes up after it.
  function debrief(opts = {}) {
    if (L.debriefing) return;
    const stream = opts.stream || null;
    L.debriefing = true;
    skin("debrief", true);
    render();
    const finish = () => {
      if (!L.debriefing) return;
      L.debriefing = false;
      if (stream) stream.getTracks().forEach((t) => t.stop());  // the mic is released once the debrief is over
      skin("debrief", false);
      render();
      if (window.Library && Library.reload) Library.reload();
      const lib = $("library");
      if (lib && lib.scrollIntoView) lib.scrollIntoView({ behavior: "smooth", block: "start" });
      if (opts.onDone) try { opts.onDone(); } catch (e) { /* the library's own */ }
    };
    if (!window.Interview || !Interview.start) { mark("debrief", "no interviewer"); return finish(); }
    mark("debrief", "start");
    try {
      const p = Interview.start("debrief", { stream, onDone: finish });
      if (p && p.catch) p.catch((e) => { mark("debrief-failed", String(e && e.message || e)); finish(); });
    } catch (e) { mark("debrief-failed", String(e && e.message || e)); finish(); }
  }

  function skin(cls, on) { if (on) L.skin.add(cls); else L.skin.delete(cls); }

  // During the chapter, for I'm done: reading, a question, paused, waiting, a check-in, or talking after the
  // chapter stopped part-way (else a tester whose chapter failed would have no way on to the debrief).
  const DURING = ["reading", "aside", "paused", "pausing", "seeking", "waiting", "checkin"];
  function inChapter() {
    return !L.handingBack && !L.debriefing && (DURING.includes(L.phase) || (L.readStarted && ["talk", "resting"].includes(L.phase)));
  }

  // Handed over by the interviewer (page/interview.js on the gate, contract "Demo chapter"): the gate's mic stream
  // is reused (the tap's unlock), the guide's session opens with the handoff note, and the screen is revealed:
  // the neutral dot warms to the coral circle while it connects; the rest fades in at the guide's first word.
  async function handoff({ name, stream } = {}) {
    L.tester = true;
    skin("tester", true);
    L.handoff = { name: String(name || "").trim() };
    if (stream) { L.stream = stream; L.mic = stream.getAudioTracks()[0] || null; meters.input(stream); }
    skin("handoff", true);
    render();
    requestAnimationFrame(() => requestAnimationFrame(() => { skin("warm", true); render(); }));  // after a frame: the fill's transition runs
    const ok = await connect();
    if (!ok) reveal();
    return ok;
  }

  function reveal() {
    if (!L.skin.has("handoff") || L.skin.has("revealed")) return;
    skin("revealed", true);
    mark("revealed");
    render();
    setTimeout(() => { ["handoff", "warm", "revealed"].forEach((c) => skin(c, false)); render(); }, 2000);
  }

  // The chapter card (faststart/card.py): asked for while this conversation has a chapter, until it carries the
  // outline's title.
  async function pollCard() {
    try {
      const run = L.own;
      if (run && !(L.card && L.card.run === run && L.card.outlined) && !["idle", "done"].includes(L.phase)) {
        const res = await fetch(`/api/live/card/${encodeURIComponent(run)}`);
        if (res.ok && run === L.own) { L.card = await res.json(); renderCard(); }
      }
    } catch (e) { /* the next poll tries again */ }
    setTimeout(pollCard, S.card_poll_ms || 3000);
  }
  setTimeout(pollCard, 500);

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
    const s = shownStatus();
    const title = (L.chapter && L.chapter.title) || "Your chapter";
    switch (L.phase) {
      case "connecting": return "Connecting…";
      case "resting":
        if (L.offer) return `${title} is ready · tap to play`;
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
      L.phase !== "idle" ? "talking" : "", offerShown() ? "offer" : "", inChapter() ? "in-chapter" : "",
      ...L.skin].filter(Boolean).join(" ");
    $("line").textContent = lineText();
    renderCard();
    const voiceWake = L.phase === "resting" && L.mic && !W.off && !W.tapOnly, ready = L.offer && !L.readStarted;
    $("how").textContent = voiceWake ? (ready ? "Say something, or tap to play" : "Say something, or tap, to talk") : (ready ? "Tap to play" : "Tap to talk");
    renderRing();
    renderPlayer();
    renderMarkers();
  }
  setInterval(render, 500);

  // The card under the orb once this conversation's chapter has one; compact (title and overview) once the
  // reading has begun, when the player and the parts sit under it too.
  function renderCard() {
    const box = $("card");
    if (!box) return;
    const c = L.card;
    const on = !!(c && c.run === L.own && !["idle", "done"].includes(L.phase));
    box.hidden = !on;
    if (!on) return;
    $("card-title").textContent = c.title || "";
    $("card-overview").textContent = c.overview || "";
    $("card-summary").textContent = c.summary || "";
    box.classList.toggle("compact", !!L.readStarted);
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
    if (!on) { L.dragging = false; return; }
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
    if (!L.chapter || !L.readStarted) { list.style.display = "none"; return; }
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
    if (L.phase === "waiting") return startReading({ id: first.id, sentence: 0 }, "section");
    L.phase = "seeking";  // stop what it is reading (the mic opens for it; startReading mutes it again)
    hush(N.moved, () => { if (L.phase === "seeking") startReading({ id: first.id, sentence: 0 }, "section"); });
  });
  $("play").addEventListener("click", () => (L.phase === "reading" ? pauseReading() : play()));
  $("seek").addEventListener("input", () => { L.dragging = true; renderPlayer(); });
  $("seek").addEventListener("change", () => { L.dragging = false; seek(Number($("seek").value) / 1000); });
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
    L.heardAt = L.inAt = L.lastListenerAt = now();  // typed counts as said for every cue
    countLine(text);
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
        if (!(await connect())) { L.typing = true; render(); typedChat(null); }
        return;
      case "talk": return offerShown() ? startFromOffer("tap") : rest();  // the mic off (tap to talk again)
      case "resting":
        if (L.offer && !L.readStarted) return playOffer("tap");  // "… is ready · tap to play"
        if (!L.pc) { W.sounds = 0; W.total = 0; W.off = false; return connect(L.restFrom === "drop" ? "dropped" : "tap"); }
        return wake();
      case "reading": return ask();
      case "paused": {  // ask from where it paused, in a new session (the pause closed the last one)
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
  // I'm done (tester mode, shown during the chapter): the guide closes and hands them back to the interviewer.
  $("done").addEventListener("click", () => { if (L.tester && inChapter()) handback("done"); });
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

  Object.assign(window.Live, { handoff, handback, debrief, renderCard,
                               onEvent, connect, endConversation, ask, startReading, where, beginChapter, onStatus,
                               pauseReading, play, seek, timeline, placeAt, playedSecs: () => { const tl = timeline(); return tl ? playedSecs(tl) : null; } });
})();
