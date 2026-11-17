// window.Interview: the welcome before, and the talk after, as one small voice session of its own.
//
//   Interview.unlock(ctx)   call inside the tap: lets the voice play on phones (and keeps ctx for the level meter)
//   Interview.start(mode, {stream, onNear, onHandoff, onDone, onState}) -> Promise<bool>: true once the session started
//                           mode "intro" or "debrief"; stream: the caller's mic (never stopped here, so the
//                           next part of the page can reuse it); onState("connecting"|"speaking"|"quiet"|"closed")
//                           onNear({scripts, entry}) when the handover is decided (its last words are being
//                           said): the caller gets the next part ready; I.next names the same from the start;
//                           onHandoff({name, stream, scripts, entry}) after the handover's last words;
//                           onDone({ok, how}) when a session ends any other way (also when it failed to start)
//   Interview.end()         close now; Interview.busy()
//
// The browser's offer goes through /api/interview/start (the server holds the key; no key ever reaches the
// page). The session's tools run at /api/interview/tool; the page acts on a result's `act`, never on a tool's
// name. Lines go to /api/interview/line, the cost to /api/interview/usage, and /api/interview/end closes.
// The voice speaks first: the opening note is sent as speakable context the moment the session starts.
(() => {
  "use strict";
  const GONE = "gone";
  const now = () => Date.now();
  const voice = document.createElement("audio");
  voice.autoplay = true;
  voice.setAttribute("playsinline", "");

  const I = { epoch: 0, pc: null, dc: null, sid: null, connecting: false, ctx: null, opts: {}, S: {}, notes: {},
              stateNow: null };

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
      act: null, handover: null, wordsAt: 0, next: null, closing: false, sid: null, liveId: null, cues: {}, closingAsked: 0, said: 0,
      graceAt: 0, graceAct: null, stateNow: null });
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
      if (p && p.catch) p.catch(() => {});
      meter(e.streams[0]);
    };
    const track = I.stream && I.stream.getAudioTracks()[0];
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
      Object.assign(I, { sid, liveId: got.live_id, notes: got.notes || {}, S: got.settings || {}, next: got.next || null });
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
      case "session.input_transcript.delta": I.inp += m.delta || ""; I.inAt = I.heardAt = now(); break;
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
    if (got && sid === I.sid) act(got);
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
  // and the clock at intro_max_minutes are not held by this.
  function mayHandOver() {
    const min = I.S.handover_min_ms == null ? 15000 : I.S.handover_min_ms;
    const el = now() - I.startedAt;  // a tester who never speaks (a nod, a mic not heard) is handed over too, later
    return !!I.startedAt && el >= min && (!!I.heardAt || el >= 3 * min);
  }

  const rx = (k) => { try { return I.S[k] ? new RegExp(I.S[k], "i") : null; } catch (e) { return null; } };

  function lineSaid(raw, t) {  // a line of the voice's, finished
    I.said++;
    if (String(raw || "").split(/\s+/).filter(Boolean).length >= (I.S.reply_words || 3)) I.repliedAt = I.outAt;  // more than "Mm-hmm."
    const text = String(raw || "").replace(/[‘’]/g, "'");  // transcripts may write didn’t: the patterns use '
    const hand = rx("handover"), ask = rx("closing_question"), thanks = rx("thanks");
    if (I.mode === "intro" && hand && hand.test(text) && !I.act && mayHandOver()) { I.graceAt = t; I.graceAct = "handover"; }
    if (I.mode === "debrief" && ask && ask.test(text)) I.closingAsked = t;
    if (I.mode === "debrief" && thanks && thanks.test(text) && I.closingAsked && I.lastThemAt > I.closingAsked && !I.act) {
      I.graceAt = t; I.graceAct = "close";
    }
  }

  setInterval(() => {
    if (!I.sid || I.closing || I.connecting || !I.startedAt) return;  // its clocks count from the start
    const t = now(), S = I.S, lineQuiet = S.line_quiet_ms || 900;
    if (I.inp && t - I.inAt > lineQuiet) {
      const text = I.inp.trim(); I.inp = "";
      if (text) { I.lastThemAt = t; quiet("/api/interview/line", { sid: I.sid, who: "in", text }); }
    }
    if (I.out && t - I.outAt > lineQuiet) {
      const text = I.out.trim(); I.out = "";
      if (text) { quiet("/api/interview/line", { sid: I.sid, who: "out", text }); lineSaid(text, t); }
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
    waiting(t, speaking);
    clock(t);
  }, 100);

  function cue(key, text) { if (!I.cues[key] && text) { I.cues[key] = true; instruct(text); } }

  // They finished and the voice has said nothing since but a word or two ("Mm-hmm.", "Hmm."), or nothing at
  // all: it is told they are waiting, after reply_wait_ms and once more after three times that. The voice
  // decides for itself when to speak, and once sat through 77 seconds of someone waiting for an answer.
  function waiting(t, speaking) {
    if (I.waitSid !== I.sid) { I.waitSid = I.sid; I.repliedAt = 0; I.waitFor = 0; I.waitSent = 0; }  // a new session
    if (!I.heardAt || I.repliedAt > I.heardAt || I.act || I.pending || speaking || I.inp || I.out) return;
    const wait = I.S.reply_wait_ms || 6000, again = I.waitFor === I.heardAt;
    if (again && I.waitSent > 1) return;
    if (t - Math.max(I.heardAt, I.spokeAt) > (again ? wait * 3 : wait)) {
      I.waitSent = again ? 2 : 1; I.waitFor = I.heardAt;
      instruct(I.notes.waiting);
    }
  }

  function clock(t) {
    const mins = (t - I.startedAt) / 60000, S = I.S;
    if (I.mode === "intro") {
      const max = S.intro_max_minutes || 3;
      if (mins > max - 0.5) cue("handover", I.notes.handover_now);
      if (mins > max && !I.act) selfCall("handover");
    } else {
      if (mins > (S.debrief_target_minutes || 8)) cue("time", I.notes.time_up);
      if (mins > (S.debrief_max_minutes || 9)) cue("wrap", I.notes.wrap_now);
      if (mins > (S.debrief_max_minutes || 9) + 1 && !I.act) { I.act = "timeout"; finish("timeout"); }
    }
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
