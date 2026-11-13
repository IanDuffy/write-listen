// Ask mid-chapter, by voice (docs/ask-voice.md). Loaded by page/index.html after its own script,
// whose globals it uses ($, status, note, post, render). While a chapter plays, the circle reads
// "Ask a question": a tap fades the chapter out, the circle stirs (Connecting), the guide opens in
// its own words, and when the conversation is done the chapter picks up again. During an aside the
// circle reads "Back to the chapter" and a tap always goes back, whatever the guide is doing.
//
// The server keeps the aside (ask/routes.py): what was said, the tools, where to restart, the log.
// The page keeps the timers: the slow-connection line and giving up, and the silences the guide is
// told about (prompts/guide_aside_notes.md). The mic is off during playback: the voice session,
// which opens it, starts only on the tap, and ends before the chapter resumes.
(function () {
  "use strict";
  const S = __ASK_SETTINGS__;  // config.yaml ask (ask/routes.py page_settings)
  const audio = $("audio");
  const orb = $("orb");
  const blob = document.querySelector("#orb .blob");
  const calm = matchMedia("(prefers-reduced-motion: reduce)");
  const VOICE_ON = 0.06;           // input volume above this counts as the listener talking

  let a = null;                    // the aside in progress (see begin)
  let token = 0;                   // bumped on every ending, so a late connection is closed, never used
  let flash = "", flashTimer = 0;  // one plain line under the circle, for a few seconds
  const debug = { asides: [] };    // for tests: timings of each aside

  // ?chapter=<run>&t=<seconds>: start the chapter there (Ian's test script, ask/REPORT.md).
  const startAt = Number(new URLSearchParams(location.search).get("t")) || 0;
  if (startAt) audio.addEventListener("playing", () => { if (audio.currentTime < startAt) audio.currentTime = startAt; }, { once: true });

  // --- the circle: Connecting, and its hand-off to Speaking ---------------------------------------
  // A closed path through 64 points, r(θ) = 78 · scale · (1 + amp · noise(θ, t)). Connecting: amp
  // eases up to a few percent and the whole shape breathes once every 2 s. Live: amp eases to 0 and
  // the scale follows the voice, so the membrane settles into the speaking circle without a snap.
  let amp = 0, vol = 0, frame = 0, drawing = false;
  const R0 = 78, N = 64;

  function noise(th, t) {
    return 0.55 * Math.sin(2 * th + 0.9 * t) + 0.3 * Math.sin(3 * th - 1.3 * t + 1.7) + 0.15 * Math.sin(5 * th + 0.6 * t + 0.4);
  }

  function path(scale, amplitude, t) {
    const pts = [];
    for (let k = 0; k < N; k++) {
      const th = (k / N) * 2 * Math.PI;
      const r = R0 * scale * (1 + amplitude * noise(th, t));
      pts.push([90 + r * Math.cos(th), 90 + r * Math.sin(th)]);
    }
    let d = `M${pts[0][0].toFixed(2)},${pts[0][1].toFixed(2)}`;
    for (let k = 0; k < N; k++) {  // Catmull-Rom through the points, as cubic Béziers
      const p0 = pts[(k - 1 + N) % N], p1 = pts[k], p2 = pts[(k + 1) % N], p3 = pts[(k + 2) % N];
      const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
      const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
      d += `C${c1[0].toFixed(2)},${c1[1].toFixed(2)} ${c2[0].toFixed(2)},${c2[1].toFixed(2)} ${p2[0].toFixed(2)},${p2[1].toFixed(2)}`;
    }
    return d + "Z";
  }

  function draw() {
    if (!a) { drawing = false; return; }
    const t = performance.now() / 1000;
    const connecting = a.phase === "connecting";
    const still = calm.matches;
    const target = connecting && !still ? 0.045 : 0;
    amp += (target - amp) * 0.06;  // about 0.4 s to settle at 60 fps
    let v = 0;
    if (a.conv && !still) {
      try { v = a.mode === "speaking" ? a.conv.getOutputVolume() : a.conv.getInputVolume(); } catch (e) { v = 0; }
    }
    vol += (Math.min(1, v || 0) - vol) * 0.25;
    const breath = connecting && !still ? 1 + 0.025 * Math.sin((2 * Math.PI * t) / 2) : 1;
    blob.setAttribute("d", path(breath * (1 + vol * 0.2), amp, t));
    frame = requestAnimationFrame(draw);
  }

  function startDrawing() {
    if (!drawing) { drawing = true; draw(); }  // the first frame now, not on the next animation frame
  }

  // --- what the page shows --------------------------------------------------------------------
  function classes() {
    const s = status;
    let c = s.state === "ready" && s.asks ? " asks" : "";
    if (a) c += " aside " + a.phase + (a.mode ? " " + a.mode : "") + (calm.matches ? " calm" : "");
    return c;
  }

  function line() {  // what the line under the circle says, or null to leave it to the page
    if (flash) return flash;
    if (a) return a.slow && a.phase === "connecting" ? "Still waking up" : "Back to the chapter";
    if (status.state === "ready" && status.asks && !audio.paused) return "Ask a question";
    return null;
  }

  function showFlash(text) {
    flash = text;
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => { flash = ""; render(); }, 4000);
    render();
  }

  // The read-along link, when the chapter has an episode page (episode/page, served at /episode).
  const read = document.createElement("a");
  read.id = "read";
  read.textContent = "Read along";
  read.target = "_blank";
  $("line").after(read);
  function decorate() {
    const s = status;
    const on = s.state === "ready" && s.episode && !a;
    read.style.display = on ? "" : "none";
    if (on) read.href = `/episode/index.html?run=/runs/${encodeURIComponent(s.run)}`;
  }

  // --- the aside --------------------------------------------------------------------------------
  function fadeOut(ms) {  // the chapter fades out over ms, then pauses; done when paused
    return new Promise((resolve) => {
      const v0 = audio.volume, t0 = performance.now();
      function step() {
        const k = Math.min(1, (performance.now() - t0) / ms);
        audio.volume = Math.max(0, v0 * (1 - k));
        if (k < 1 && !audio.paused) return requestAnimationFrame(step);
        audio.pause();
        audio.volume = v0 || 1;
        resolve();
      }
      step();
    });
  }

  function onTap() {  // true: the tap was ours
    if (a) { back("button"); return true; }
    if (status.state === "ready" && status.asks && !audio.paused) { begin(); return true; }
    return false;
  }

  async function begin() {
    const tap = performance.now();
    const t = audio.currentTime;
    const my = ++token;
    a = { phase: "connecting", tap, t, id: null, conv: null, mode: null, slow: false, lines: [],
          guideTurns: 0, listenerSpoke: false, lastGuide: "", heard: tap, sentFor: -1, resuming: false,
          ready: null, firstSound: null, blobAt: null, pausedAt: null, longSent: false, chain: Promise.resolve() };
    startDrawing();
    a.blobAt = performance.now();
    render();
    fadeOut(S.fade_ms).then(() => { if (a && a.tap === tap) a.pausedAt = performance.now(); });
    a.slowTimer = setTimeout(() => { if (a && token === my) { a.slow = true; render(); } }, S.slow_line_secs * 1000);
    a.giveUp = setTimeout(() => { if (a && token === my && a.phase === "connecting") back("timeout"); }, S.give_up_secs * 1000);
    let start;
    try {
      start = await post("/api/ask/start", { run: status.run, t });
    } catch (err) {
      if (token === my) back("error", "Couldn't reach the guide, so back to the chapter.");
      return;
    }
    if (token !== my) {  // they tapped back (or it gave up) while the server was answering
      post("/api/ask/end", { aside: start.aside, how: "button" }).catch(() => {});
      return;
    }
    a.id = start.aside;
    a.notes = start.notes;
    const EL = window.ElevenLabsClient;
    if (!EL) return back("error", "Voice isn't available, so back to the chapter.");
    let conv;
    try {
      conv = await EL.Conversation.startSession({
        signedUrl: start.signed_url, connectionType: "websocket", dynamicVariables: start.dynamic_variables,
        clientTools: {
          resume_chapter: (p) => tool("resume_chapter", p, my),
          save_seed: (p) => tool("save_seed", p, my),
        },
        onConnect: ({ conversationId }) => line_("", "", { conversation_id: conversationId }, my),
        onMessage: ({ role, message }) => heard(role === "agent" ? "guide" : "listener", message, my),
        onModeChange: ({ mode }) => modeChange(mode, my),
        onDisconnect: () => { if (a && token === my && a.conv === conv && a.phase !== "ending") back("dropped", "The connection dropped, so back to the chapter."); },
        onError: () => {},
      });
    } catch (err) {
      if (token === my) back("error", "Couldn't connect, so back to the chapter.");
      return;
    }
    if (token !== my) { try { conv.endSession(); } catch (e) { /* closed */ } return; }  // too late: close it
    a.conv = conv;
    a.phase = "live";
    a.ready = performance.now();
    a.heard = a.ready;
    clearTimeout(a.giveUp);
    conv.sendUserMessage(a.notes.opened);  // the guide speaks first, in its own words
    a.ticker = setInterval(() => tick(my), 200);
    a.longTimer = setTimeout(() => { if (a && token === my && a.conv) { a.conv.sendUserMessage(a.notes.long); } }, S.long_aside_minutes * 60000);
    render();
  }

  // An aside's lines and tool calls reach the server one after another, in the order they happened:
  // posted all at once they can arrive out of order, and the log would read wrong.
  function inOrder(cur, url, body) {
    const p = cur.chain.then(() => post(url, body));
    cur.chain = p.catch(() => {});
    return p;
  }

  function line_(who, text, extra, my) {
    if (!a || token !== my || !a.id) return;
    inOrder(a, "/api/ask/line", { aside: a.id, who, text, ...extra }).catch(() => {});
  }

  function heard(who, text, my) {
    if (!a || token !== my) return;
    a.lines.push({ who, text });
    line_(who, text, {}, my);
    a.heard = performance.now();
    if (who === "guide") { a.guideTurns++; a.lastGuide = text; }
    else if (text.replace(/[.\s…]/g, "") && !text.startsWith("[")) a.listenerSpoke = true;  // not a page note
  }

  function modeChange(mode, my) {
    if (!a || token !== my) return;
    a.mode = mode;
    a.heard = performance.now();
    if (mode === "speaking" && a.firstSound === null) a.firstSound = performance.now();
    render();
    if (a.resuming && mode === "speaking") { a.leadIn = true; clearTimeout(a.settle); }
    if (a.resuming && a.leadIn && mode === "listening") settleThenBack(my);
  }

  // Silences, timed here: after the opening, and after an answer (longer if it asked something).
  function tick(my) {
    if (!a || token !== my || !a.conv || a.resuming || a.mode !== "listening" || !a.guideTurns) return;
    let v = 0;
    try { v = a.conv.getInputVolume(); } catch (e) { v = 0; }
    const now = performance.now();
    if (v > VOICE_ON) { a.heard = now; return; }
    if (a.sentFor === a.guideTurns) return;  // one note per guide turn
    const opening = !a.listenerSpoke;
    const wait = opening ? S.silence_after_greeting_secs
      : /\?\s*$/.test(a.lastGuide) ? S.silence_after_question_secs : S.silence_after_answer_secs;
    if (now - a.heard >= wait * 1000) {
      a.sentFor = a.guideTurns;
      const note_ = opening ? a.notes.silence_greeting : a.notes.silence_answer;
      a.conv.sendUserMessage(note_);
      line_("page", note_, {}, my);
    }
  }

  async function tool(name, params, my) {
    if (!a || token !== my) return JSON.stringify({ error: "This aside has ended." });
    let out;
    try { out = await inOrder(a, "/api/ask/tool", { aside: a.id, name, args: params || {} }); }
    catch (err) { out = { error: err.message }; }
    if (name === "resume_chapter" && a && token === my && !a.resuming) {
      // The tool's reply asks for the lead-in (ask/routes.py RESUMED): go back once it has been said,
      // or after a moment if none comes; never later than 8 s.
      a.resuming = true;
      a.leadIn = false;
      a.noLeadIn = setTimeout(() => { if (a && token === my && !a.leadIn) back("guide"); }, 3000);
      a.resumeBy = setTimeout(() => back("guide"), 8000);
    }
    return JSON.stringify(out);
  }

  function settleThenBack(my) {  // a breath after its last word, in case more is coming
    clearTimeout(a.settle);
    a.settle = setTimeout(() => { if (a && token === my && a.mode === "listening") back("guide"); }, 350);
  }

  async function back(how, message = "") {
    if (!a || a.phase === "ending") return;
    const cur = a;
    const my = ++token;
    cur.phase = "ending";
    [cur.slowTimer, cur.giveUp, cur.longTimer, cur.resumeBy, cur.settle, cur.noLeadIn].forEach(clearTimeout);
    clearInterval(cur.ticker);
    const conv = cur.conv;
    cur.conv = null;
    const ended = conv ? conv.endSession().catch(() => {}) : Promise.resolve();
    let restart = { t: cur.t };
    if (cur.id) {
      try {
        await cur.chain;  // every line and tool call first: the log is written when the aside ends
        const res = await post("/api/ask/end", {
          aside: cur.id, how,
          tap_to_ready_ms: cur.ready && Math.round(cur.ready - cur.tap),
          tap_to_first_sound_ms: cur.firstSound && Math.round(cur.firstSound - cur.tap),
        });
        restart = res.restart;
      } catch (e) { /* resume where they paused */ }
    }
    await ended;
    await new Promise((r) => setTimeout(r, S.resume_delay_ms));
    debug.asides.push({ how, tap: cur.tap, blobAt: cur.blobAt, pausedAt: cur.pausedAt, ready: cur.ready,
                        firstSound: cur.firstSound, restart, t: cur.t });
    transcriptAside(cur);
    if (a === cur) a = null;
    if (token !== my) return;
    if (message) showFlash(message);
    audio.currentTime = restart.t;
    audio.play().catch(() => {});
    render();
  }

  function transcriptAside(cur) {  // the aside in the page's transcript, collapsed to its first line
    const said = cur.lines.filter((x) => x.text && x.text.trim());
    if (!said.length) return;
    const d = document.createElement("details");
    d.className = "aside";
    const s = document.createElement("summary");
    s.textContent = (said[0].who === "guide" ? "" : "You: ") + said[0].text;
    d.appendChild(s);
    said.slice(1).forEach((x) => {
      const p = document.createElement("p");
      p.className = x.who === "guide" ? "guide" : "you";
      p.textContent = (x.who === "guide" ? "" : "You: ") + x.text;
      d.appendChild(p);
    });
    $("transcript").appendChild(d);
  }

  addEventListener("pagehide", () => {
    if (a && a.conv) a.conv.endSession();
    if (a && a.id) navigator.sendBeacon("/api/ask/end", new Blob([JSON.stringify({ aside: a.id, how: "dropped" })],
                                                                { type: "application/json" }));
  });

  window.Ask = { onTap, classes, line, decorate, debug, state: () => a && { phase: a.phase, mode: a.mode, id: a.id } };
})();
