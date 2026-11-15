// The library page's live sessions with the guide, on GPT-Live (overnight/live.py; docs/overnight-chapters.md):
//   Play    the guide opens live (the intro, from the chapter's intro brief), then start_chapter and the
//           recording plays. If the guide doesn't call the tool, the recording starts after its lead-in line
//           and about 1.5 s of silence, or at 90 s at the latest.
//   Ask     a tap while the recording plays pauses it; the guide answers in a new session; resume_chapter
//           (or its lead-back line, or "Back to the chapter") and the recording resumes from the restart
//           point the server picks (ask/aside.py's rewind rules).
//   Change  a conversation about tonight's chapter, ending in set_next_chapter; the card refreshes.
// One WebRTC session at a time. The session is opened through our server (GPT-Live has no client keys).
(() => {
  const $ = (id) => document.getElementById(id);
  const now = () => Date.now();
  const L = { pc: null, dc: null, stream: null, mic: null, session: null, mode: null, started: false, startedAt: 0,
              out: "", outAt: 0, inp: "", inAt: 0, spokeAt: 0, heardAt: 0, lastGuideAt: 0, lastListenerAt: 0,
              seconds: 0, backend: { model: null, input: 0, output: 0, cached: 0 }, calls: new Set(),
              pending: null, notes: {}, S: {}, how: null, ending: false, toolAt: 0, wrapped: false,
              run: null, playing: false, aside: null, read: null, epoch: 0 };
  window.__library = L;  // for tests
  const audio = $("audio");
  const remote = new Audio();
  remote.autoplay = true;

  function line(text) { const el = $("live"); el.textContent = text || ""; document.body.classList.toggle("live", !!text); }
  async function post(url, body) {
    const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    if (!res.ok) { let d = ""; try { d = (await res.json()).detail; } catch (e) {} throw new Error(d || `${res.status}`); }
    return res.json();
  }
  const quiet = (url, body) => post(url, body).catch(() => {});

  // --- the session ------------------------------------------------------------------------------
  let seq = 0;
  function send(type, fields = {}) {
    if (!L.dc || L.dc.readyState !== "open") return false;
    L.dc.send(JSON.stringify({ type, event_id: `l${++seq}_${now() % 1e7}`, ...fields }));
    return true;
  }
  function mute(on) { if (L.mic) L.mic.enabled = !on; send(on ? "session.input_audio.mute" : "session.input_audio.unmute"); }
  function instruct(text) {  // GPT-Live acts on instructions only with the mic open
    mute(false);
    send("session.instructions.append", { delegation_id: null, content: text });
    if (L.session) quiet("/api/overnight/live/line", { session: L.session, who: "page", text });
  }
  function speakable(text) { send("session.commentary.append", { delegation_id: null, content: text }); }

  async function iceDone(pc, ms) {
    if (pc.iceGatheringState === "complete") return;
    await new Promise((done) => {
      const t = setTimeout(done, ms);
      pc.addEventListener("icegatheringstatechange", () => { if (pc.iceGatheringState === "complete") { clearTimeout(t); done(); } });
    });
  }

  async function connect(mode, body) {
    const mine = ++L.epoch;
    Object.assign(L, { mode, started: false, out: "", inp: "", spokeAt: 0, heardAt: 0, lastGuideAt: 0, lastListenerAt: 0,
                       seconds: 0, backend: { model: null, input: 0, output: 0, cached: 0 }, calls: new Set(),
                       pending: null, how: null, ending: false, toolAt: 0, wrapped: false });
    if (!L.stream) {
      L.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      L.mic = L.stream.getAudioTracks()[0];
    }
    const pc = new RTCPeerConnection();
    L.pc = pc;
    pc.ontrack = (e) => { remote.srcObject = e.streams[0]; };
    pc.addTrack(L.mic, L.stream);
    const dc = pc.createDataChannel("oai-events");
    L.dc = dc;
    dc.onmessage = (e) => { try { onEvent(JSON.parse(e.data)); } catch (err) { /* a malformed event: skip it */ } };
    await pc.setLocalDescription(await pc.createOffer());
    await iceDone(pc, 2500);
    const got = await post("/api/overnight/live/start", { mode, sdp: pc.localDescription.sdp, ...body });
    if (mine !== L.epoch) { pc.close(); throw new Error("superseded"); }
    L.session = got.session; L.notes = got.notes || {}; L.S = got.settings || {};
    await pc.setRemoteDescription({ type: "answer", sdp: got.sdp });
    const t0 = now();
    while (!L.started && now() - t0 < 10000) await new Promise((r) => setTimeout(r, 50));
    if (!L.started) { hangUp(); throw new Error("Voice couldn't start."); }
    L.startedAt = now();
    return got;
  }

  function hangUp() {
    if (L.pc) { try { L.pc.close(); } catch (e) {} }
    L.pc = null; L.dc = null;
    if (L.stream) L.stream.getTracks().forEach((x) => x.stop());
    L.stream = null; L.mic = null;
  }

  async function finish(how) {  // close the session, report its cost; the server keeps the transcript
    if (L.ending || !L.session) return null;
    L.ending = true;
    send("session.close");
    const sid = L.session;
    hangUp();
    L.session = null;
    const b = L.backend.model ? { ...L.backend } : null;
    try { return await post("/api/overnight/live/end", { session: sid, seconds: L.seconds, backend: b, how }); }
    catch (e) { return null; }
  }

  // --- events -------------------------------------------------------------------------------------
  function onEvent(m) {
    switch (m.type) {
      case "session.started": L.started = true; break;
      case "session.output_transcript.delta": {
        const d = (m.delta || "").replace(/\[[^\]]*\]\s*/g, "");
        L.spokeAt = now();
        if (L.read && L.read.cur) L.read.heard += d;
        else { L.out += d; L.outAt = now(); }
        break;
      }
      case "session.input_transcript.delta": L.inp += m.delta || ""; L.inAt = L.heardAt = now(); break;
      case "response.event": backend(m.event || {}); break;
      case "session.usage.updated": if (m.usage && m.usage.seconds != null) L.seconds = m.usage.seconds; break;
      case "session.closed": if (m.usage && m.usage.seconds != null) L.seconds = m.usage.seconds; break;
    }
  }

  async function backend(e) {
    if (e.type === "response.completed" && e.response && e.response.usage) {
      const u = e.response.usage;
      L.backend.model = e.response.model || L.backend.model;
      L.backend.input += u.input_tokens || 0; L.backend.output += u.output_tokens || 0;
      L.backend.cached += (u.input_tokens_details && u.input_tokens_details.cached_tokens) || 0;
      return;
    }
    if (e.type !== "response.output_item.done" || !e.item || e.item.type !== "function_call") return;
    const item = e.item;
    if (L.calls.has(item.call_id)) return;
    L.calls.add(item.call_id);
    let args = {};
    try { args = JSON.parse(item.arguments || "{}"); } catch (err) {}
    const sid = L.session;
    let out;
    try { out = await post("/api/overnight/live/tool", { session: sid, name: item.name, args }); }
    catch (err) { out = { error: err.message }; }
    send("response.item.create", { item: { type: "function_call_output", call_id: item.call_id, output: JSON.stringify(out) } });
    send("response.create");
    if (sid !== L.session) return;
    if (item.name === "start_chapter" && L.mode === "intro") { L.toolAt = now(); afterGuide(() => (L.readLive ? readLive("tool") : pivot("tool")), 9000, L.S.intro_quiet_ms || 1500); }
    if (item.name === "resume_chapter" && L.mode === "ask") afterGuide(() => back("guide"), L.S.resume_max_ms || 8000, L.S.resume_quiet_ms || 900);
    if (item.name === "set_next_chapter" && L.mode === "change") { L.changed = out; afterGuide(() => closeChange(out), L.S.change_close_ms || 6000, 1500, 2500); }
  }

  // Run fn once both sides have been quiet for `quietMs` (counted from now or from the guide's last word,
  // whichever is later) and at least `min` ms have passed, or after `max` ms. The lead-in usually comes just
  // before the tool, so speech after the call is not required; `min` leaves room for a line the tool's
  // result asks for (Change: "it'll be the next one").
  function afterGuide(fn, max, quietMs, min = 0) { if (!L.pending) L.pending = { fn, since: now(), max, quietMs, min }; }

  const rx = (k, fallback) => { try { return new RegExp(L.S[k] || fallback, "i"); } catch (e) { return new RegExp(fallback, "i"); } };

  setInterval(() => {
    const t = now();
    if (!L.session) return;
    const lineQuiet = L.S.line_quiet_ms || 900;
    if (L.out && t - L.outAt > lineQuiet) {
      const text = L.out.trim(); L.out = "";
      if (text) {
        L.lastGuideAt = t;
        quiet("/api/overnight/live/line", { session: L.session, who: "guide", text });
        if (L.aside) L.aside.lines.push(text);
        guideSaid(text, t);
      }
    }
    if (L.inp && t - L.inAt > lineQuiet) {
      const text = L.inp.trim(); L.inp = "";
      if (text) { L.lastListenerAt = t; quiet("/api/overnight/live/line", { session: L.session, who: "listener", text }); }
    }
    if (L.pending) {
      const p = L.pending;
      const calm = t - Math.max(L.spokeAt, p.since) > p.quietMs && t - L.heardAt > p.quietMs && !L.inp;
      if ((calm && t - p.since >= p.min) || t - p.since > p.max) { L.pending = null; p.fn(); }
    }
    if (L.mode === "intro") introClock(t);
    if (L.mode === "ask") askClock(t);
    if (L.read) readClock(t);
  }, 100);

  function guideSaid(text, t) {
    if (L.mode === "intro" && !L.pending && t - L.startedAt > (L.S.intro_min_secs ?? 20) * 1000 && rx("lead_in", "\\bhere it is\\b").test(text)) {
      afterGuide(() => (L.readLive ? readLive("lead-in") : pivot("lead-in")), 6000, L.S.intro_quiet_ms || 1500);
    }
    if (L.mode === "ask" && L.aside && !L.pending && t - L.aside.t0 > 2000 && rx("lead_back", "\\banyway\\b").test(text)) {
      afterGuide(() => back("lead-back"), L.S.resume_max_ms || 8000, L.S.resume_quiet_ms || 900);
    }
  }

  // --- Play: the intro, then the recording ----------------------------------------------------------
  function introClock(t) {
    const secs = (t - L.startedAt) / 1000, max = L.S.intro_max_secs || 90;
    if (!L.wrapped && secs > max - 30 && !L.pending) { L.wrapped = true; if (L.notes.intro_wrap) instruct(L.notes.intro_wrap); }
    if (secs > max && !L.how) {  // at 90 s at the latest (a listener mid-sentence gets a few seconds more)
      if (L.inp && secs < max + 6) return;
      L.readLive ? readLive("timeout") : pivot("timeout");
    }
  }

  async function pivot(how) {
    if (L.mode !== "intro" || L.how) return;
    L.how = how; L.pending = null;
    await finish(how);
    L.mode = null;
    startRecording(0);
  }

  function startRecording(at) {
    if (!audio.src || !audio.src.endsWith(L.audioUrl)) audio.src = L.audioUrl;
    audio.controls = true;
    const seek = () => { try { audio.currentTime = at || 0; } catch (e) {} };
    if (audio.readyState >= 1) seek(); else audio.addEventListener("loadedmetadata", seek, { once: true });
    audio.play().catch(() => {});  // with preload="none", metadata only arrives once playback is asked for
    L.playing = true;
    line("");
    setButton("Ask a question");
  }

  function setButton(text) { $("play").textContent = text; }

  async function play(latest) {
    if (!latest) return;
    if (L.mode === "ask") return back("button");
    if (L.playing && !audio.paused) return ask(latest);
    if (L.playing) { audio.play().catch(() => {}); setButton("Ask a question"); return; }
    if (L.mode === "read") return stopReading();
    if (L.mode === "intro") return L.readLive ? readLive("button") : pivot("button");  // pressed again: straight to the chapter
    L.run = latest.run; L.audioUrl = latest.audio;
    if (!latest.intro) return startRecording(0);  // an older chapter has no intro brief: just play it
    line("Connecting…");
    setButton("Skip to the chapter");
    try {
      const got = await connect("intro", { run: latest.run });
      line("");
      speakable((L.notes.intro_opened || "").replace("{title}", got.title || latest.title));
      setTimeout(() => { if (L.mode === "intro" && !L.spokeAt && L.notes.intro_opened) instruct("[" + L.notes.intro_opened.replace("{title}", got.title || latest.title) + "]"); }, L.S.greet_fallback_ms || 5000);
      L.readLive = !!got.read_live;
    } catch (e) {
      if (e.message === "superseded") return;
      L.mode = null;
      startRecording(0);  // no voice: the chapter itself still plays
    }
  }

  audio.addEventListener("ended", () => { L.playing = false; setButton("Play"); audio.controls = false; });
  audio.addEventListener("pause", () => { if (!L.aside && L.playing && !audio.ended) setButton("Play"); });
  audio.addEventListener("play", () => { if (L.playing) setButton("Ask a question"); });

  // --- Ask: a tap while the recording plays -------------------------------------------------------
  async function ask(latest) {
    const t = audio.currentTime;
    audio.pause();
    L.aside = { t0: now(), t, lines: [], silenced: false };
    setButton("Back to the chapter");
    line("Connecting…");
    try {
      await connect("ask", { run: latest.run, t });
      line("");
      instruct(L.notes.ask_opened || "[The listener tapped to ask something.]");
    } catch (e) {
      L.aside = null; L.mode = null; line("");
      audio.play().catch(() => {});  // no voice: back to the chapter where it paused
    }
  }

  function askClock(t) {
    const a = L.aside;
    if (!a || a.silenced || L.pending) return;
    const last = Math.max(L.lastGuideAt, L.lastListenerAt, L.spokeAt, L.heardAt);
    if (L.lastGuideAt && last && t - last > (L.S.aside_silence_secs || 8) * 1000) { a.silenced = true; instruct(L.notes.silence || "[Silence.]"); }
  }

  async function back(how) {
    if (L.mode !== "ask" || !L.aside) return;
    const a = L.aside;
    L.pending = null;
    const out = await finish(how);
    L.mode = null; L.aside = null;
    const at = out && out.restart ? out.restart.t : a.t;
    startRecording(at);
  }

  // --- Change: what tonight's chapter should be ---------------------------------------------------
  async function openChange(up) {
    if (L.session) return;
    const btn = $("change");
    btn.disabled = true;
    const before = up.card ? up.card.id : null;
    $("up-line").textContent = "Connecting…";
    try {
      const got = await connect("change", {});
      $("up-line").textContent = "Talking it over… (tap Change again to finish)";
      btn.disabled = false; btn.textContent = "Done";
      speakable((L.notes.change_opened || "").replace("{title}", got.title || "tonight's plan"));
      L.changeBefore = before;
    } catch (e) {
      btn.disabled = false;
      $("up-line").textContent = e.message || "Voice couldn't start.";
      L.mode = null;
    }
  }

  async function closeChange(out) {
    if (L.mode !== "change") return;
    await finish(out ? "tool" : "button");
    L.mode = null;
    const btn = $("change");
    btn.textContent = "Change";
    if (out && out.changing) {
      $("up-line").textContent = "Rewriting the card…";
      const t0 = now();
      while (now() - t0 < 120000) {
        await new Promise((r) => setTimeout(r, 3000));
        const s = await (await fetch("/api/library")).json().catch(() => null);
        if (s && s.up_next.card && s.up_next.card.id !== L.changeBefore) { window.Library.reload(); return; }
      }
    }
    window.Library.reload();
  }

  // --- read live (settings playback.read_live): the guide reads the chapter after the intro -----------
  // The same session switches into reading mode: the mic is muted and the chapter goes one paragraph at a
  // time as speakable context; the next is sent once the transcript has heard the current one through
  // (about 90% of its words and a short quiet), or after a longer quiet. A tap stops the reading and ends
  // the session (asking mid-reading is the live-voice page's, once merged).
  async function readLive(how) {
    if (L.mode !== "intro" || L.how) return;
    L.how = how; L.pending = null; L.mode = "read";
    let paras = [];
    try { paras = (await (await fetch(`/api/overnight/paragraphs/${L.run}`)).json()).paragraphs || []; } catch (e) {}
    if (!paras.length) { L.mode = "intro"; L.how = null; return pivot(how); }
    mute(true);
    L.read = { items: paras, next: 0, cur: null, heard: "", sentAt: 0 };
    setButton("Stop");
    readNext();
  }
  const words = (s) => (s.toLowerCase().match(/[a-z0-9']+/g) || []);
  function readNext() {
    const r = L.read;
    if (!r) return;
    if (r.next >= r.items.length) { finishReading("read-live-done"); return; }
    r.cur = r.items[r.next++];
    r.heard = ""; r.sentAt = now(); r.n = words(r.cur.text).length;
    speakable(`${L.notes.read || "Read this passage aloud now, word for word:"} ${r.cur.text}`);
  }
  function readClock(t) {
    const r = L.read;
    if (!r || !r.cur) return;
    const got = words(r.heard).length, quietFor = t - Math.max(L.spokeAt, r.sentAt);
    if ((got >= 0.9 * r.n && quietFor > 700) || (got >= 0.6 * r.n && quietFor > 2500) || quietFor > 5000) {
      r.cur = null;
      const gap = { section: 1200, beat: 900 }[(r.items[r.next] || {}).pause] || 400;
      setTimeout(readNext, gap);
    }
  }
  function stopReading() { instruct(L.notes.read_stop || "[Stop reading now.]"); finishReading("read-live-stopped"); }
  async function finishReading(how) {
    L.read = null;
    await finish(how);
    L.mode = null;
    setButton("Play");
  }

  window.Morning = { play };
  window.Change = { open: (up) => (L.mode === "change" ? closeChange(L.changed || null) : openChange(up)) };
})();
