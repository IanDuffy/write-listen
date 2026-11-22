// window.Interview: the welcome before, and the talk after, as one small voice session of its own.
//
//   Interview.unlock(ctx)   call inside the tap: lets the voice play on phones (and keeps ctx for the level meter)
//   Interview.start(mode, {stream, onNear, onHandoff, onDone, onState, onLook}) -> Promise<bool>: true once the session started
//                           mode "intro" or "debrief"; stream: the caller's mic (never stopped here, so the
//                           next part of the page can reuse it); onState("connecting"|"speaking"|"quiet"|"closed")
//                           onNear({scripts, entry}) when the handover is decided (its last words are being
//                           said): the caller gets the next part ready; I.next names the same from the start;
//                           onHandoff({name, stream, scripts, entry}) after the handover's last words;
//                           onDone({ok, how}) when a session ends any other way (also when it failed to start)
//                           onLook(ev): the debrief's earpiece, as it happens (looks, notes and what became of them,
//                           the lines), for a panel; who/speaking are "in" (them) and "out" (the voice); never awaited
//   Interview.end()         close now; Interview.busy()
//
// The browser's offer goes through /api/interview/start (the server holds the key; no key ever reaches the
// page). The session's tools run at /api/interview/tool; the page acts on a result's `act`, never on a tool's
// name. Lines go to /api/interview/line, the cost to /api/interview/usage, and /api/interview/end closes.
// The voice speaks first: the opening note is sent as speakable context the moment the session starts.
// The debrief's earpiece (settings.direct; docs/debrief-earpiece-design.md): a look at the start, while they are still
// answering and at a turn's end nothing covered, at /api/interview/direct; a note it sends goes at once as an
// instruction (notes.direct), for the voice's next turn, unless that turn has been taken; what became of each note goes
// to /api/interview/direct/fate.
(() => {
  "use strict";
  const GONE = "gone";
  const now = () => Date.now();
  const voice = document.createElement("audio");
  voice.autoplay = true;
  voice.setAttribute("playsinline", "");

  const I = { epoch: 0, pc: null, dc: null, sid: null, connecting: false, ctx: null, opts: {}, S: {}, notes: {},
              stateNow: null };
  // page/speaker.js: on Android a headset connected after the Speakerphone switch puts a new mic track in the
  // stream (the one the tap opened); it is sent in the old one's place.
  if (window.Speaker && Speaker.follow) Speaker.follow((from, to, stream) => {
    if (!I.pc || I.stream !== stream || !I.pc.getSenders) return;
    for (const sd of I.pc.getSenders()) if (sd.track === from && sd.replaceTrack) sd.replaceTrack(to).catch(() => {});
  });

  async function post(url, body) {
    const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(typeof err.detail === "string" ? err.detail : "Something went wrong.");
    }
    return res.json();
  }
  const quiet = (url, body) => post(url, body).catch(() => null);

  function until(cond, ms) {
    return new Promise((done) => {
      const t0 = now();
      const iv = setInterval(() => {
        if (cond()) { clearInterval(iv); done(true); } else if (now() - t0 > ms) { clearInterval(iv); done(false); }
      }, 50);
    });
  }

  async function iceDone(pc, ms) {
    if (pc.iceGatheringState === "complete") return;
    await new Promise((done) => {
      const t = setTimeout(done, ms);
      pc.onicegatheringstatechange = () => { if (pc.iceGatheringState === "complete") { clearTimeout(t); done(); } };
    });
  }

  function state(s) {
    if (I.stateNow === s) return;
    I.stateNow = s;
    if (I.opts.onState) try { I.opts.onState(s); } catch (e) { /* the caller's problem, not the session's */ }
  }

  // --- talking to the session --------------------------------------------------------------------------
  let seq = 0;
  function send(type, fields = {}) {
    if (!I.dc || I.dc.readyState !== "open") return false;
    I.dc.send(JSON.stringify({ type, event_id: `i${++seq}_${now() % 1e7}`, ...fields }));
    return true;
  }
  function unmute() {
    const t = I.stream && I.stream.getAudioTracks()[0];
    if (t) t.enabled = true;
    send("session.input_audio.unmute");
  }
  function note(text) { if (I.sid && text) quiet("/api/interview/line", { sid: I.sid, who: "page", text }); }
  function speakable(text) { send("session.commentary.append", { delegation_id: null, content: text }); note(text); }
  function instruct(text) {  // acted on only with the mic open
    unmute();
    send("session.instructions.append", { delegation_id: null, content: text });
    note(text);
    if (directOn()) emit("note", { text });
  }

  // The voice's level, for the dot's pulse: an analyser on the incoming sound when there is an AudioContext.
  function meter(stream) {
    I.analyser = null;
    if (!I.ctx) return;
    try {
      const src = I.ctx.createMediaStreamSource(stream);
      const a = I.ctx.createAnalyser();
      a.fftSize = 512;
      src.connect(a);
      I.analyser = a;
      I.buf = new Float32Array(a.fftSize);
    } catch (e) { I.analyser = null; }
  }
  function level() {
    if (!I.analyser) return 0;
    I.analyser.getFloatTimeDomainData(I.buf);
    let s = 0;
    for (const x of I.buf) s += x * x;
    return Math.sqrt(s / I.buf.length);
  }

  // --- start ---------------------------------------------------------------------------------------------
  function fresh(mode, opts) {
    Object.assign(I, { mode, opts, stream: opts.stream || null, connecting: true, started: false, startedAt: 0,
      out: "", outAt: 0, inp: "", inAt: 0, spokeAt: 0, heardAt: 0, spoken: false, seconds: 0,
      backend: { model: null, input: 0, output: 0, cached: 0 }, calls: new Set(), pending: null, name: null,
      act: null, handover: null, wordsAt: 0, next: null, closing: false, sid: null, liveId: null, cues: {}, closingAsked: 0, farewellAt: 0, said: 0,
      graceAt: 0, graceAct: null, stateNow: null, hang: null, noteAt: 0, noteKind: null, handHeld: false,
      // the debrief's earpiece: the look in flight, its numbering, failures, the last lines, this turn of theirs
      look: null, lookId: 0, lookQueued: null, lookFails: 0, lookDown: false, heldNote: null, recent: [], turn: "", turnOpen: false,
      hearAt: 0, hearWords: -1, themSince: false, taken: 0, questions: 0, outStart: 0, inStartAt: 0, view: false });
  }

  async function start(mode, opts = {}) {
    if (busy()) await end();
    const mine = ++I.epoch;
    const gone = () => mine !== I.epoch;
    fresh(mode, opts);
    state("connecting");
    const pc = new RTCPeerConnection();
    I.pc = pc;
    pc.ontrack = (e) => {
      if (I.pc !== pc) return;
      voice.srcObject = e.streams[0];
      const p = voice.play();
      if (p && p.then) p.then(() => Speaker.playing(e.streams[0]), () => {});  // page/speaker.js
      meter(e.streams[0]);
    };
    const track = I.stream && Speaker.hold(I.stream.getAudioTracks()[0]);  // page/speaker.js (served above this file)
    if (track) { track.enabled = true; pc.addTrack(track, I.stream); }
    else pc.addTransceiver("audio", { direction: "sendrecv" });
    const dc = pc.createDataChannel("oai-events");
    I.dc = dc;
    dc.onmessage = (e) => { if (I.dc !== dc) return; try { onEvent(JSON.parse(e.data)); } catch (err) { /* skip a bad event */ } };
    let sid = null;
    try {
      await pc.setLocalDescription(await pc.createOffer());
      await iceDone(pc, 2500);
      if (gone()) throw new Error(GONE);
      const got = await post("/api/interview/start", { mode, sdp: pc.localDescription.sdp });
      sid = got.sid;
      if (gone()) throw new Error(GONE);
      Object.assign(I, { sid, liveId: got.live_id, notes: got.notes || {}, S: got.settings || {}, next: got.next || null,
                         view: !!got.earpiece_view });
      await pc.setRemoteDescription({ type: "answer", sdp: got.sdp });
      const ok = await until(() => I.started || gone(), I.S.start_timeout_ms || 10000);
      if (gone()) throw new Error(GONE);
      if (!ok) throw new Error("The voice couldn't start.");
    } catch (err) {
      try { pc.close(); } catch (e) { /* closed */ }
      if (sid) quiet("/api/interview/end", { sid, how: gone() ? "abandoned" : "failed" });
      if (gone()) return false;  // a newer start or end owns the state now
      I.pc = null; I.dc = null; I.sid = null; I.connecting = false;
      state("closed");
      if (opts.onDone) opts.onDone({ ok: false, how: "failed", error: err.message });
      return false;
    }
    I.connecting = false;
    I.startedAt = now();
    state("quiet");
    speakable(I.notes.opened);
    if (directOn()) { emit("conversation", { view: I.view }); ask("opened"); }
    setTimeout(() => {  // nothing said yet: once more, as an instruction with the mic open
      if (!gone() && I.sid && !I.spoken && !I.inp) instruct(`[${I.notes.opened}]`);
    }, I.S.greet_fallback_ms || 4000);
    return true;
  }

  // --- events --------------------------------------------------------------------------------------------
  function onEvent(m) {
    switch (m.type) {
      case "session.started": I.started = true; break;
      case "session.output_transcript.delta": {
        const d = (m.delta || "").replace(/\[[^\]]*\]\s*/g, "");
        I.spokeAt = now(); I.spoken = true;
        if (!I.out) I.outStart = now();
        I.out += d; I.outAt = now();
        // The handover's words, as they are said: the page calls the tool itself at once (the voice rarely
        // does), so the next part is being got ready while the sentence ends. Waiting for the line to end and
        // then for a tool call that never came was 4.4 s of silence (Ian's run).
        if (I.mode === "intro" && !I.act && !I.wordsAt && mayHandOver() && (rx("handover") || { test: () => false }).test(I.out.replace(/[‘’]/g, "'"))) {
          I.wordsAt = now();
          selfCall("handover");
        }
        break;
      }
      case "session.input_transcript.delta":
        if (!I.inp) I.inStartAt = now();
        I.inp += m.delta || ""; I.inAt = I.heardAt = now();
        if (m.delta && m.delta.trim()) { I.themSince = true; I.turnOpen = true; }
        break;
      case "response.event": backend(m.event || {}); break;
      case "session.usage.updated": if (m.usage && m.usage.seconds != null) I.seconds = m.usage.seconds; break;
      case "session.closed":
        if (m.usage && m.usage.seconds != null) I.seconds = m.usage.seconds;
        if (!I.closing && I.sid) finish("dropped");  // the far end let go
        break;
    }
  }

  async function backend(e) {
    if (e.type === "response.completed" && e.response && e.response.usage) {
      const u = e.response.usage;
      I.backend.model = e.response.model || I.backend.model;
      I.backend.input += u.input_tokens || 0;
      I.backend.output += u.output_tokens || 0;
      I.backend.cached += (u.input_tokens_details && u.input_tokens_details.cached_tokens) || 0;
      // Each backend answer in the transcript, by what it held: a run-3 post-mortem had only token sums to go on.
      const kinds = (e.response.output || []).map((x) => x.type === "function_call" ? `function_call ${x.name}` : x.type);
      if (I.sid) quiet("/api/interview/line", { sid: I.sid, who: "backend", text: `completed: ${kinds.join(", ") || "empty"}` });
      return;
    }
    if (e.type !== "response.output_item.done" || !e.item || e.item.type !== "function_call") return;
    const item = e.item;
    if (I.calls.has(item.call_id)) return;
    I.calls.add(item.call_id);
    let args = {};
    try { args = JSON.parse(item.arguments || "{}"); } catch (err) { /* empty */ }
    const sid = I.sid;
    if (!sid) return;
    let got;
    try { got = await post("/api/interview/tool", { sid, name: item.name, args }); }
    catch (err) { got = { error: err.message }; }
    if (sid !== I.sid) return;
    const shown = { ...got };
    delete shown.act; delete shown.scripts; delete shown.entry;  // for the page alone
    send("response.item.create", { item: { type: "function_call_output", call_id: item.call_id, output: JSON.stringify(shown) } });
    send("response.create");
    act(got);
  }

  // The page calls a tool itself: the voice said the words but delegation never came.
  async function selfCall(which) {
    const name = (I.S.acts || {})[which];
    const sid = I.sid;
    if (!name || !sid || I.act) return;
    const got = await quiet("/api/interview/tool", { sid, name, args: {}, by: "page" });
    if (sid !== I.sid) return;
    if (got) act(got);
    else if (which === "close" && I.farewellAt) act({ act: "close" });  // its last words were said: the end, even unanswered
  }

  function act(got) {
    if (!got || !got.act) return;
    if (got.act === "name") { I.name = got.name || I.name; return; }
    if (I.act) return;
    I.act = got.act;
    if (got.act === "handover") {
      I.handover = { name: got.name || I.name || null, scripts: got.scripts || [], entry: got.entry || null };
      if (I.opts.onNear) try { I.opts.onNear({ scripts: I.handover.scripts, entry: I.handover.entry }); } catch (e) { /* the caller's */ }
      afterSpeech(() => finish("handover"), I.S.handover_quiet_ms);
    } else if (got.act === "close") afterSpeech(() => finish("done"));
  }

  // Do fn once the voice has finished (its words stopped, nobody speaking, at least min ms), or after max ms.
  function afterSpeech(fn, quiet) {
    I.pending = { fn, since: now(), quiet: quiet || I.S.close_quiet_ms || 1400, min: I.S.close_min_ms || 800, max: I.S.close_max_ms || 9000 };
  }

  // The page acts on the handover's words only once the tester has spoken and the intro is handover_min_ms
  // old: never in its opening, where "hand you over" may come up while it says how this will go. The tool
  // and the clock at intro_max_minutes are not held by this. A tester who never speaks (a nod, a mic not heard) is
  // handed over too, at twice that; words said past handover_min_ms but before then are held (I.handHeld) and
  // acted on once it is (the shorter intro of 2026-11-19 says them about 20-28 s in: under the old 3x they were
  // dropped, and a silent tester sat until the 2-minute clock). Words in the opening are never held.
  function mayHandOver() {
    const min = I.S.handover_min_ms == null ? 15000 : I.S.handover_min_ms;
    const el = now() - I.startedAt;
    return !!I.startedAt && el >= min && (!!I.heardAt || el >= 2 * min);
  }

  const rx = (k) => { try { return I.S[k] ? new RegExp(I.S[k], "i") : null; } catch (e) { return null; } };

  function lineSaid(raw, t) {  // a line of the voice's, finished
    I.said++;
    const text = String(raw || "").replace(/[‘’]/g, "'");  // transcripts may write didn’t: the patterns use '
    const hand = rx("handover"), ask = rx("closing_question"), thanks = rx("thanks"), bye = rx("farewell");
    // A reply is more than "Mm-hmm.", and never a line that only stalls ("Just a second.", "I'm still here."):
    // those counted as replies in Ian's third run, and the page never told the voice he was waiting.
    const stall = rx("stall"), goOn = rx("go_on");
    const stalled = !!(stall && stall.test(text));
    const reply = text.split(/\s+/).filter(Boolean).length >= (I.S.reply_words || 3) && !stalled;
    if (reply) I.repliedAt = I.outAt;
    // A line that may leave them hanging (debrief): a reply with no question and no farewell, and not an
    // invitation to go on ("Sorry, go on."). A stall leaves the last one standing; any other line replaces it.
    if (!stalled) {
      I.hang = I.mode === "debrief" && reply && !text.includes("?") && !(goOn && goOn.test(text)) && !(bye && bye.test(text))
        ? { at: I.outAt, sent: false } : null;
    }
    if (I.mode === "intro" && hand && hand.test(text) && !I.act) {
      if (mayHandOver()) { I.graceAt = t; I.graceAct = "handover"; }
      else if (!I.heardAt && now() - I.startedAt >= (I.S.handover_min_ms == null ? 15000 : I.S.handover_min_ms)) I.handHeld = true;
    }
    if (I.mode !== "debrief") return;
    // The debrief ends itself. Its fixed last words are the end whenever they come, and the page acts on them
    // if the tool call hasn't arrived act_grace_ms later: it once had no way out but the last question (Ian was
    // asked it after saying "let's end the conversation here"). The older shape still closes too: a thanks
    // after that question was answered.
    if (ask && ask.test(text)) I.closingAsked = t;
    const said = bye && bye.test(text);
    if (said && !I.farewellAt) I.farewellAt = t;
    if (!I.act && !I.graceAct && (said || (thanks && thanks.test(text) && I.closingAsked && I.lastThemAt > I.closingAsked))) {
      I.graceAt = t; I.graceAct = "close";
    }
  }

  setInterval(() => {
    if (!I.sid || I.closing || I.connecting || !I.startedAt) return;  // its clocks count from the start
    const t = now(), S = I.S, lineQuiet = S.line_quiet_ms || 900;
    if (I.inp && t - I.inAt > lineQuiet) {
      const text = I.inp.trim(); I.inp = "";
      if (text) { I.lastThemAt = t; quiet("/api/interview/line", { sid: I.sid, who: "in", text }); heardLine(text, t); }
    }
    if (I.out && t - I.outAt > lineQuiet) {
      const text = I.out.trim(); I.out = "";
      if (text) { quiet("/api/interview/line", { sid: I.sid, who: "out", text }); lineSaid(text, t); saidLine(text, t); }
    }
    const lv = level();
    const speaking = lv > (S.speaking_level || 0.02) || t - I.spokeAt < 500;
    state(speaking ? "speaking" : "quiet");
    if (I.pending) {
      const p = I.pending;
      const calm = t - Math.max(I.spokeAt, p.since) > p.quiet && t - I.heardAt > p.quiet && !I.inp && !speaking;
      if ((calm && t - p.since >= p.min) || t - p.since > p.max) { I.pending = null; p.fn(); return; }
    }
    if (I.graceAct && !I.act && t - I.graceAt > (S.act_grace_ms || 3500)) { const w = I.graceAct; I.graceAct = null; selfCall(w); }
    if (I.mode === "intro" && I.handHeld && !I.act && !I.graceAct && mayHandOver()) { I.handHeld = false; I.wordsAt = t; selfCall("handover"); }
    waiting(t, speaking);
    hanging(t, speaking);
    clock(t);
    if (directOn()) direct(t);
  }, 100);

  // The page's notes to the voice, one kind at a time: two different notes are never sent closer together than
  // note_gap_ms (waiting, a turn left hanging and the clock's could otherwise stack in a few seconds).
  function gapOk(kind, t) { return !I.noteAt || I.noteKind === kind || t - I.noteAt > (I.S.note_gap_ms || 5000); }
  function pageNote(kind, text) {
    if (!text) return;
    I.noteAt = now(); I.noteKind = kind;
    instruct(text);
  }
  function cue(key, text, t) {
    if (!I.cues[key] && text && gapOk(key, t || now())) { I.cues[key] = true; pageNote(key, text); }
  }

  // They finished and the voice has said nothing since but a word or two ("Mm-hmm.", "Hmm."), or nothing at
  // all: it is told they are waiting, after reply_wait_ms and once more after three times that. The voice
  // decides for itself when to speak, and once sat through 77 seconds of someone waiting for an answer.
  function waiting(t, speaking) {
    if (I.waitSid !== I.sid) { I.waitSid = I.sid; I.repliedAt = 0; I.waitFor = 0; I.waitSent = 0; }  // a new session
    if (I.farewellAt) return;  // its last words are said: whatever they add ("bye!"), it is never told to speak again
    if (!I.heardAt || I.repliedAt > I.heardAt || I.act || I.pending || speaking || I.inp || I.out) return;
    const wait = I.S.reply_wait_ms || 6000, again = I.waitFor === I.heardAt;
    if (again && I.waitSent > 1) return;
    if (t - Math.max(I.heardAt, I.spokeAt) > (again ? wait * 3 : wait) && gapOk("waiting", t)) {
      I.waitSent = again ? 2 : 1; I.waitFor = I.heardAt;
      pageNote("waiting", I.notes.waiting);
    }
  }

  // Left hanging (the debrief; Ian's third run): it answered him ("Yeah, I follow you, and that's useful to hear.")
  // with no question and no farewell, and then neither side spoke for 9.5 s until he asked "But what else?". Once
  // both have been quiet hanging_ms after such a line (and he hasn't spoken since it), one note asks for its next
  // question or its farewell. Never after an invitation to go on, a question or the farewell (lineSaid).
  function hanging(t, speaking) {
    const h = I.hang;
    if (I.mode !== "debrief" || !h || h.sent || I.farewellAt || I.act || I.pending || speaking || I.inp || I.out) return;
    if (!I.heardAt || I.heardAt > h.at) return;  // they spoke since: theirs to finish, or `waiting`'s
    if (t - Math.max(h.at, I.spokeAt, I.heardAt) <= (I.S.hanging_ms || 8000) || !gapOk("hanging", t)) return;
    h.sent = true;
    pageNote("hanging", I.notes.hanging);
  }

  function clock(t) {
    const mins = (t - I.startedAt) / 60000, S = I.S;
    if (I.mode === "intro") {
      const max = S.intro_max_minutes || 3;
      if (mins > max - 0.5) cue("handover", I.notes.handover_now, t);
      if (mins > max && !I.act) selfCall("handover");
    } else {
      if (!I.farewellAt && mins > (S.debrief_target_minutes || 8)) cue("time", I.notes.time_up, t);
      if (!I.farewellAt && mins > (S.debrief_max_minutes || 9)) cue("wrap", I.notes.wrap_now, t);
      if (mins > (S.debrief_max_minutes || 9) + 1 && !I.act) { I.act = "timeout"; finish("timeout"); }
    }
  }

  // --- the debrief's earpiece (docs/debrief-earpiece-design.md; Ian, 2026-11-22) ------------------------------
  // A background look at the talk keeps a plan and, when its advice changes, a short note for the voice's next turn.
  // A debrief is two minutes and a few turns, so it looks while they are still answering: its note is in place as
  // they finish. A note whose turn the voice has already taken is dropped (used a turn late, it would ask the old
  // thing). Off (settings.direct.enabled false, or not the debrief) it never asks, sends or posts anything.
  const D = () => I.S.direct || {};
  function directOn() { return I.mode === "debrief" && !!D().enabled && !!I.sid; }
  const words = (x) => String(x || "").split(/\s+/).filter(Boolean).length;
  const secsNow = () => (I.startedAt ? Math.round((now() - I.startedAt) / 100) / 10 : null);

  // For a panel (onLook): never throws, never awaited, nothing read back.
  function emit(type, fields = {}) {
    if (!I.opts.onLook) return;
    try { I.opts.onLook({ type, at: now(), secs: secsNow(), sid: I.sid, ...fields }); } catch (e) { /* the panel's own */ }
  }

  function remember(who, text) {
    I.recent.push({ who, text });
    if (I.recent.length > 10) I.recent.splice(0, I.recent.length - 10);
  }

  // A line of theirs, finished: part of this turn (a turn ends once no new words come for turn_merge_ms).
  function heardLine(text, t) {
    if (!directOn()) return;
    remember("in", text);
    I.turn = (I.turn ? I.turn + " " : "") + text;
    emit("line", { who: "in", text, began: I.inStartAt || t });
  }

  // A line of the voice's, finished. A reply (a question, or reply_words or more, never a stall) after they spoke is
  // the voice taking its turn: a note for that turn is stale from then on, and their next turn starts afresh.
  function saidLine(text, t) {
    if (!directOn()) return;
    remember("out", text);
    if (text.includes("?")) I.questions++;
    emit("line", { who: "out", text, began: I.outStart || t });
    // A stall ("Just a second.") or an invitation to go on with no question ("Sorry, go on.") leaves the turn theirs.
    const plain = text.replace(/[‘’]/g, "'"), stall = rx("stall"), goOn = rx("go_on");
    const turnLine = (text.includes("?") || words(text) >= (I.S.reply_words || 3))
      && !(stall && stall.test(plain)) && !(!text.includes("?") && goOn && goOn.test(plain));
    const took = turnLine && I.themSince;
    if (turnLine) {
      if (I.themSince) I.taken++;
      I.themSince = false; I.turn = ""; I.turnOpen = false; I.hearAt = 0; I.hearWords = -1;
    }
    // A note that came back while the voice was already answering them waited for this line: if it took the turn, the
    // note was for that turn and is stale; otherwise (a stall, a backchannel) it goes now.
    if (I.heldNote) {
      const h = I.heldNote;
      I.heldNote = null;
      if (took) fate(h, "dropped", "moved_on");
      else deliver(h);
    }
  }

  // Why no look (or no note) now: "" when one may go. The voice's end is near or decided, or the clock has spoken.
  function notHere() {
    if (I.closing) return "closing";
    if (I.farewellAt) return "farewell";
    if (I.act || I.pending) return "ending";
    if (I.cues.time || I.cues.wrap) return "time";
    return "";
  }

  function direct(t) {
    if (I.lookDown || notHere()) return;
    const C = D(), soFar = words(I.turn) + words(I.inp);
    // While they speak: once their turn has hear_words words, then every hear_every_ms with new words.
    if (I.inp && soFar >= (C.hear_words || 8) && soFar > I.hearWords
        && (!I.hearAt || t - I.hearAt >= (C.hear_every_ms || 5000))) {
      I.hearAt = t; I.hearWords = soFar;
      ask("hearing");
    }
    // Their turn is over (no new words for turn_merge_ms): a look, unless one while they spoke covered it.
    if (I.turnOpen && !I.inp && t - I.inAt >= (C.turn_merge_ms || 1500)) {
      I.turnOpen = false;
      if (I.hearWords < 0 || soFar - I.hearWords >= (C.hear_words || 8)) ask("turn_end");
    }
    if (I.lookQueued && !I.look) { const q = I.lookQueued; I.lookQueued = null; ask(q); }
  }

  function ask(trigger) {
    if (!directOn() || I.lookDown || notHere()) return;
    if (I.look) { I.lookQueued = trigger; return; }  // one look in flight; the newest trigger waits
    const C = D(), id = ++I.lookId, sid = I.sid, t = now(), ctl = new AbortController();
    const look = { id, ctl, askedAt: t, taken: I.taken, trigger };
    I.look = look;
    const body = { sid, id, trigger, recent: I.recent.slice(-10), hearing: I.inp.trim(),
                   page: { seconds: Math.round((t - I.startedAt) / 1000), questions: I.questions, time_up: !!I.cues.time } };
    emit("ask", { id, trigger, speaking: I.inp ? "in" : "none" });
    const timer = setTimeout(() => ctl.abort(), (C.timeout_ms || 5000) + 1500);
    fetch("/api/interview/direct", { method: "POST", headers: { "Content-Type": "application/json" },
                                     body: JSON.stringify(body), signal: ctl.signal })
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((got) => { if (I.look === look && I.sid === sid) { I.look = null; answered(got, look); } })
      .catch((e) => { if (I.look === look && I.sid === sid) { I.look = null; failed(String((e && e.message) || e), id); } })
      .finally(() => clearTimeout(timer));
  }

  function answered(got, look) {
    emit("look", { id: look.id, got });
    if (!got || got.error) return failed((got && got.error) || "no answer", look.id);
    I.lookFails = 0;
    if (!(got.changed && got.note)) return;
    const n = { id: look.id, note: got.note, kind: got.kind || "next_turn", about: got.about || null, askedAt: look.askedAt,
                taken: look.taken };
    if (I.taken > n.taken) return fate(n, "dropped", "moved_on");  // the voice took the turn this note was for
    if (I.heldNote) fate(I.heldNote, "dropped", "replaced");
    I.heldNote = null;
    // The voice is answering them right now: its line may be the turn this note was for. It waits for that line.
    if (I.out.trim() && I.themSince && !notHere()) { I.heldNote = n; return; }
    deliver(n);
  }

  function deliver(n) {
    const why = notHere();
    if (why) return fate(n, "dropped", "not_here", why);
    const text = String(I.notes.direct || "").replace("{note}", n.note);
    if (!text || !I.notes.direct) return fate(n, "dropped", "not_here", "no wrapper");
    fate(n, "sent", null, null, { text });
    I.noteAt = now(); I.noteKind = "direct";  // waiting and hanging keep their gap after it (it bypasses theirs)
    instruct(text);
  }

  function failed(why, id) {
    I.lookFails++;
    emit("fail", { id, why, fails: I.lookFails });
    if (I.lookFails >= 2 && !I.lookDown) { I.lookDown = true; I.lookQueued = null; emit("down", {}); }
  }

  function fate(n, what, reason = null, detail = null, extra = {}) {
    const body = { sid: I.sid, id: n.id, fate: what, reason, detail: detail ? String(detail).slice(0, 200) : null,
                   secs: secsNow(), held_ms: null };
    if (I.sid && n.id != null) quiet("/api/interview/direct/fate", body);
    emit("fate", { ...body, note: n.note, kind: n.kind, about: n.about, ...extra });
  }

  // --- the end -------------------------------------------------------------------------------------------
  function heldLines() {
    const lines = [], inp = I.inp.trim(), out = I.out.trim();
    I.inp = ""; I.out = "";
    if (inp) lines.push({ who: "in", text: inp });
    if (out) lines.push({ who: "out", text: out });
    return lines;
  }

  function usageBody(sid) {
    return { sid, live_id: I.liveId, seconds: I.seconds, backend: I.backend.model ? { ...I.backend } : null };
  }

  async function close(how) {
    if (!I.sid || I.closing) return;
    I.closing = true; I.pending = null;
    send("session.close");
    if (I.look || I.lookQueued || I.heldNote) {  // the earpiece's look in flight, or a note waiting: the panel is told
      const lk = I.look, h = I.heldNote;
      I.look = null; I.lookQueued = null; I.heldNote = null;
      try { if (lk) lk.ctl.abort(); } catch (e) { /* gone */ }
      if (h) fate(h, "dropped", "not_here", "closing");
      if (directOn()) emit("forget", { why: how });
    }
    const sid = I.sid, pc = I.pc, usage = usageBody(sid), held = heldLines();
    I.pc = null; I.dc = null; I.sid = null;
    setTimeout(() => { try { if (pc) pc.close(); } catch (e) { /* closed */ } }, 300);  // let the close reach it
    voice.srcObject = null;
    for (const x of held) await quiet("/api/interview/line", { sid, ...x });
    await quiet("/api/interview/usage", usage);
    await quiet("/api/interview/end", { sid, how });
    state("closed");
  }

  async function finish(how) {
    const opts = I.opts, h = I.handover, stream = I.stream, name = I.name, acted = I.act;  // I.name: the latest saved
    const closed = close(how);  // the session is let go of at once; its last lines, cost and end are posted on the side
    // A session that drops after the handover or the thanks was decided still counts as that.
    if ((how === "handover" || acted === "handover") && opts.onHandoff) {
      opts.onHandoff({ name: name || (h && h.name) || null, stream, scripts: (h && h.scripts) || [], entry: h && h.entry });
      return closed;
    }
    await closed;
    if (opts.onDone) opts.onDone({ ok: how === "done" || acted === "close", how });
  }

  async function end() {
    I.epoch++;  // a start in flight gives up at its next step
    if (I.sid) return close("ended");
    if (I.pc) { try { I.pc.close(); } catch (e) { /* closed */ } }
    I.pc = null; I.dc = null; I.connecting = false;
  }

  function busy() { return !!(I.sid || I.connecting); }

  function unlock(ctx) {
    if (ctx) I.ctx = ctx;
    const p = voice.play();
    if (p && p.catch) p.catch(() => {});
  }

  // Leaving the page mid-session: its cost and its end, as beacons.
  const before = window.onpagehide;
  window.onpagehide = (e) => {
    if (I.sid) {
      const sid = I.sid;
      const beacon = (url, body) => {
        try { navigator.sendBeacon(url, new Blob([JSON.stringify(body)], { type: "application/json" })); } catch (x) { /* gone */ }
      };
      heldLines().forEach((x) => beacon("/api/interview/line", { sid, ...x }));
      beacon("/api/interview/usage", usageBody(sid));
      beacon("/api/interview/end", { sid, how: "pagehide" });
      I.sid = null;
    }
    if (typeof before === "function") before(e);
  };

  window.Interview = { start, end, busy, unlock, I };
})();
