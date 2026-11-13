// A fast run on the page (docs/fast-start.md, "On the page" and "When a section isn't ready").
// Loaded by page/index.html after ask.js; it uses the page's globals ($, status, note, post, render,
// endConversation, voiceConv, resting, wake, readyNote, pendingReady, flushReady, fresh, starting) and
// window.Ask. (Before the voice-first page and the quiet guide, endConversation was endVoice and there
// was no resting: guide() and endGuide() take either page.)
//
// A fast run is listened to while it is made. Its status (faststart/status.py) lists the sections
// and, for each, the pieces narrated so far; a piece is its own file that never changes, and the
// pieces played back to back are the chapter. This script:
//
//   - before the first audio, shows the four steps to it under the circle (status.steps: done ticked,
//     the current one pulsing) and a countdown on the line (status.first_audio_in, counted down here
//     from status.updated; none when the run gives none);
//   - shows the section markers under the line: played, playing, ready, or, for one being written, the
//     step it is at (researching, writing, editing, recording), the next one with "ready in about N min";
//   - plays the pieces through the page's one <audio> element, in order, with a pause between
//     sections, from the tap that follows "first audio";
//   - tells the server where the listener is (POST /api/fast/position), which the run reads;
//   - waits when the listener reaches a part that is not narrated yet. The run answers in the
//     status: a short wait is just a pause, a longer one brings the guide in (the check-in, a voice
//     session run by ask.js's code with the parts below). The awaited part then plays after a beat,
//     or, when a conversation is open, once the guide has said its lead-in: never over a conversation.
//
// Fast is active while the followed run is a fast one being made, and stays so if the run finishes
// while the listener is hearing it (the pieces play to the end; the page never swaps to the whole
// chapter's file mid-listen). A finished fast chapter opened later is an ordinary chapter.
(function () {
  "use strict";
  const S = __FAST_SETTINGS__;  // config.yaml fast (faststart/routes.py page_settings)
  const audio = $("audio");
  const WORDS = { played: "played", playing: "playing", ready: "ready", being_written: "being written" };
  const STEPS = { researching: "researching", writing: "writing", editing: "editing", recording: "recording" };

  const F = {
    run: null,           // the fast run being followed
    secs: [],            // status.sections as last received
    first: null,         // status.first_audio as last received (null: not seen yet)
    mode: "idle",        // idle (nothing played yet) | playing | paused | waiting | done
    n: 1, i: 0,          // the piece being played, or the one to play next
    ln: null, li: null,  // the piece loaded in the audio element
    url: null,           // its address
    live: false,         // the loaded piece's offset is not yet counted in `base`
    base: 0,             // seconds of audio heard, the loaded piece's offset aside
    started: false,      // the listener has started it in this visit (played some, or is waiting for some)
    kept: false,         // the run finished while it was being heard: the pieces play on
    since: null, reason: null,  // the wait: when it began (epoch seconds), end_of_audio | skip
    asked: false,        // this wait has had its check-in
    talk: false,         // the line reads "Talk while you wait"
    ci: null,            // the check-in in progress
    gap: 0, beat: 0, decision: 0,  // timers: between sections, before an awaited part, the run's answer
    busy: false,         // a tap is being handled
    chain: Promise.resolve(),      // position reports, in order
  };
  const debug = { reports: 0, F };

  // --- the sections and their pieces --------------------------------------------------------------
  const sec = (n) => F.secs.find((s) => s.n === n) || null;
  const pieces = (n) => { const s = sec(n); return (s && s.pieces) || []; };
  const playable = (n, i) => pieces(n).length > i;
  const last = () => F.secs.reduce((m, s) => Math.max(m, s.n), 0);
  const length = (ps) => ps.reduce((t, p) => t + (Number(p.seconds) || 0), 0);
  const owned = () => F.url !== null && audio.src.endsWith(F.url);            // the element holds one of our pieces
  const here = () => owned() && F.ln === F.n && F.li === F.i && !audio.ended;  // and it is the one to play
  const offset = () => (here() ? audio.currentTime || 0 : 0);
  const talking = () => !!(window.Ask && Ask.state());                        // an aside or a check-in is open

  function active() {
    const s = status;
    return !!F.run && s.run === F.run && s.mode === "fast" && (s.state === "making" || (s.state === "ready" && F.kept));
  }
  // A chapter heard while it was made, still being heard after the page moved on to follow another one (a new
  // chapter started meanwhile): its pieces play on to the end, and the circle pauses and resumes it.
  const heardOn = () => F.kept && F.started && owned() && (F.mode === "playing" || F.mode === "paused") && !active();

  function time() {  // where the listener is, in the chapter's own seconds (what ask-by-voice counts in)
    let t = 0;
    for (const s of F.secs) {
      if (s.n < F.n) t += length(s.pieces || []);
      else if (s.n === F.n) t += length((s.pieces || []).slice(0, F.i));
    }
    return t + offset();
  }

  const inSection = () => length(pieces(F.n).slice(0, F.i)) + offset();
  const heard = () => F.base + (F.live && owned() ? audio.currentTime || 0 : 0);
  function fold() {  // count what was heard of the loaded piece, once
    if (F.live && owned()) F.base += audio.currentTime || 0;
    F.live = false;
  }

  // --- telling the server where the listener is ---------------------------------------------------
  function reported() {
    const state = F.mode === "playing" && talking() ? "paused" : F.mode;  // an aside has the floor
    const waiting = state === "waiting";
    const r3 = (x) => Math.round(x * 1000) / 1000;
    return { run: F.run, state, section: F.n, piece: F.i, t: r3(inSection()), chapter_t: r3(Math.max(0, heard())),
             waiting_for: waiting ? F.n : null, since: waiting ? F.since : null, reason: waiting ? F.reason : null };
  }

  function report() {  // at once on every change, and every few seconds besides; kept in order
    if (!F.run || F.mode === "idle") return Promise.resolve();
    const body = reported();
    debug.reports++;
    F.chain = F.chain.then(() => post("/api/fast/position", body)).catch(() => {});
    return F.chain;
  }

  setInterval(() => {
    if (active() && (F.mode === "playing" || F.mode === "waiting" || F.mode === "paused")) report();
  }, S.position_seconds * 1000);

  addEventListener("pagehide", () => {  // nobody is listening now: the run should not hold anything for them
    if (!F.run || !F.started || F.mode === "done" || F.mode === "idle") return;
    const body = { ...reported(), state: "paused", waiting_for: null, since: null, reason: null };
    navigator.sendBeacon("/api/fast/position", new Blob([JSON.stringify(body)], { type: "application/json" }));
  });

  // --- playing ------------------------------------------------------------------------------------
  function clearTimers() {
    [F.gap, F.beat, F.decision].forEach(clearTimeout);
    F.gap = F.beat = F.decision = 0;
  }

  function load(n, i, off) {  // put a piece in the player; one already there is never loaded again
    const p = pieces(n)[i];
    const url = `/runs/${encodeURIComponent(F.run)}/` + String(p.src).split("/").map(encodeURIComponent).join("/");
    fold();
    if (owned() && F.url === url) {
      audio.currentTime = off;
    } else {
      audio.src = url;
      F.url = url;
      if (off > 0) audio.addEventListener("loadedmetadata", () => { if (F.url === url) audio.currentTime = off; }, { once: true });
    }
    F.ln = n; F.li = i;
    F.live = true;
    F.base -= off;  // heard() adds the element's clock, which starts at the offset
  }

  // auto: nobody tapped (the next piece, an awaited part arriving). Never over a conversation: the
  // place is kept, paused, and the tap that ends the conversation plays it.
  // The onboarding guide, in any of its states the chapter must not play over: live, or resting (it
  // stepped back and the page listens locally for the listener to speak: voice/quiet-guide).
  function guide() { return !!voiceConv || (typeof resting !== "undefined" && !!resting); }
  function endGuide() { return (typeof endConversation === "function" ? endConversation : endVoice)(); }

  function play(n, i, off = 0, auto = false) {
    clearTimers();
    if (!playable(n, i)) { F.n = n; F.i = i; return settle(); }
    F.n = n; F.i = i;
    F.started = true;
    F.talk = false;
    F.since = F.reason = null;
    if (auto && (guide() || starting || talking())) {
      fold();
      F.mode = "paused";
      report();
      return render();
    }
    load(n, i, off);
    F.mode = "playing";
    const url = F.url;
    audio.play().catch(() => {  // the browser wants a tap first: show that it can be played
      if (F.mode === "playing" && F.url === url && audio.paused && !audio.ended) { F.mode = "paused"; report(); render(); }
    });
    report();
    render();
  }

  function settle() {  // play from the place kept, past any section that is finished; wait if nothing is there yet
    while (!playable(F.n, F.i)) {
      const s = sec(F.n);
      if (!s || !s.complete) return wait(F.n, F.i, "end_of_audio");
      if (F.n >= last()) return finish();
      F.n += 1; F.i = 0;
    }
    play(F.n, F.i);
  }

  function next() {  // a piece ended: the next one at once, a new section after a pause, or a wait
    let n = F.n, i = F.i + 1, crossed = false;
    while (!playable(n, i)) {
      const s = sec(n);
      if (!s || !s.complete) return wait(n, i, "end_of_audio");  // also running dry inside a section
      if (n >= last()) return finish();
      n += 1; i = 0; crossed = true;
    }
    if (!crossed) return play(n, i, 0, true);
    clearTimers();
    F.n = n; F.i = i;
    F.gap = setTimeout(() => { F.gap = 0; if (F.mode === "playing") play(n, i, 0, true); }, S.section_gap_ms);
    report();
    render();
  }

  function pause() {
    clearTimers();
    F.mode = "paused";
    if (!audio.paused) audio.pause();
    report();
    render();
  }

  function resume() {
    if (!here()) return settle();
    clearTimers();
    F.mode = "playing";
    F.started = true;
    audio.play().catch(() => { if (F.mode === "playing" && audio.paused && !audio.ended) { F.mode = "paused"; report(); render(); } });
    report();
    render();
  }

  function finish() {  // the last piece of the last section has played
    clearTimers();
    fold();
    F.mode = "done";
    report();
    if (F.kept || status.state === "ready") return release();
    render();
  }

  function release() {  // the chapter is finished and heard: from here it is an ordinary ready chapter
    clearTimers();
    F.run = null;
    F.kept = false;
    F.url = F.ln = F.li = null;
    F.live = false;
    fresh = true;
    render();
  }

  function seek(t) {  // play from a place in the chapter's own seconds (the way back from a question)
    let at = 0;
    for (const s of F.secs) {
      const ps = s.pieces || [];
      for (let i = 0; i < ps.length; i++) {
        const d = Number(ps[i].seconds) || 0;
        if (t < at + d) return play(s.n, i, Math.max(0, t - at));
        at += d;
      }
    }
    resume();  // past everything narrated so far: carry on from where they were
  }

  audio.addEventListener("ended", () => {
    if (!owned()) return;
    fold();
    if (F.mode !== "playing") return;
    if (!active() && !F.kept) { F.i += 1; F.mode = "paused"; return render(); }  // the run stopped: keep the place
    next();
  });
  // Paused or played from outside the page (a headset button, the system's controls).
  audio.addEventListener("pause", () => {
    if (F.mode === "playing" && owned() && audio.paused && !audio.ended && !F.gap && !talking()) { F.mode = "paused"; report(); render(); }
  });
  // Only a resume of the piece Fast paused on goes ahead. Anything else (in a wait, a check-in or an aside, the
  // pause between sections, or a place already moved on past the loaded piece) would replay an old piece over
  // the guide or skip one, so it is paused again.
  audio.addEventListener("play", () => {
    if (!owned() || audio.paused || (F.mode === "playing" && !F.gap && !talking())) return;
    if (F.mode === "paused" && here() && (active() || heardOn()) && !guide() && !talking()) { F.mode = "playing"; report(); render(); }
    else audio.pause();
  });

  // --- waiting, and the check-in ------------------------------------------------------------------
  function wait(n, i, reason) {
    clearTimers();
    fold();
    F.n = n; F.i = i;
    F.mode = "waiting";
    F.started = true;  // they asked for it, even if nothing has played yet (a page opened mid-wait)
    F.since = Date.now() / 1000;
    F.reason = reason;
    F.asked = F.talk = false;
    if (!audio.paused) audio.pause();
    // The run says what the wait is (status.check_in). If it says nothing, the guide comes in.
    F.decision = setTimeout(() => { F.decision = 0; checkIn(false); }, S.decision_wait_seconds * 1000);
    report();
    render();
    if (active()) waitingStatus(status);
  }

  function waitingStatus(s) {
    if (playable(F.n, F.i)) return arrived();
    if (s.state !== "making" || s.waiting_for !== F.n || !s.check_in) return;
    clearTimeout(F.decision);  // the run has answered: "pause" (nothing happens) or "check_in"
    F.decision = 0;
    if (s.check_in === "check_in") checkIn(false);
  }

  function arrived() {  // what was waited for can be played
    clearTimeout(F.decision);
    F.decision = 0;
    const ci = F.ci;
    if (ci) {
      const st = talking() ? Ask.state() : null;
      if (ci.a) return tell(ci);                               // the guide is told; the part waits for the conversation
      if (st && st.phase === "connecting") return Ask.back("other");  // nobody has spoken yet: no need for it now
      if (!st) F.ci = null;                                    // it had not begun
      else return;
    }
    if (F.beat) return;
    F.beat = setTimeout(() => {
      F.beat = 0;
      if (F.mode === "waiting" && !F.ci && playable(F.n, F.i)) play(F.n, F.i, 0, true);
    }, S.ready_beat_ms);
  }

  function tell(ci) {  // the ready signal: at once as an update the guide reads without speaking
    ci.ready = true;
    if (ci.told) return;
    ci.told = true;
    try { ci.a.conv.sendContextualUpdate(ci.a.notes.ready); } catch (e) { /* the note itself follows */ }
  }

  function checkIn(reopened) {
    if (F.ci || F.mode !== "waiting" || !active() || status.state !== "making" || playable(F.n, F.i)) return;
    if (guide() || starting || talking() || !window.Ask || !Ask.begin) return;  // one conversation at a time
    if (!reopened && F.asked) return;  // once per wait, unless the listener asks for it
    clearTimers();
    F.asked = true;
    F.talk = false;
    const ci = F.ci = { section: F.n, reopened, a: null, ready: false, told: false, muted: false,
                        said: 0, voiceAt: 0, readyFor: -1, readySent: 0, closing: null };
    render();
    // The server reads the last position report to know what the listener has heard: it goes first.
    report().then(() => {
      if (F.ci !== ci) return;
      Ask.begin({ t: heard(), body: { kind: "check_in", section: ci.section, reopened },
                  tools: ["add_note", "get_status", "resume_chapter"], opened, tick, tap: tapped, line: ciLine, done });
    });
  }

  function opened(a) {  // connected: the guide speaks first
    const ci = F.ci;
    if (!ci) return;
    ci.a = a;
    ci.voiceAt = performance.now();
    ci.muted = S.gap_mic === "tap";  // the mic waits for a tap
    if (ci.muted) { try { a.conv.setMicMuted(true); } catch (e) { ci.muted = false; } }
    Ask.send((ci.reopened ? a.notes.reopened : a.notes.opened) + (ci.muted ? " " + a.notes.tap : ""));
    if (playable(F.n, F.i)) tell(ci);
    render();
  }

  // What the listener themselves said: not a page note coming back, not a turn of only "...".
  const theirs = (x) => x.who === "listener" && !!x.text && !x.text.startsWith("[") && !!x.text.replace(/[.\s…]/g, "");
  const spoken = (a) => a.lines.filter(theirs).length;
  function owed(a) {  // the listener spoke last: the guide's reply is on its way
    const turns = a.lines.filter((x) => x.who === "guide" || theirs(x));
    return turns.length > 0 && turns[turns.length - 1].who === "listener";
  }

  function tick(a) {  // every 200 ms while the check-in is live: the silences, and the ready note
    const ci = F.ci;
    if (!ci || ci.a !== a || !a.conv || a.resuming) return;
    const now = performance.now();
    let v = 0;
    try { v = a.conv.getInputVolume(); } catch (e) { v = 0; }
    const said = spoken(a);
    // Nothing said and no turn taken for this long: the mic's level no longer counts as the listener talking
    // (steady noise would hold the ready part, or the session, for as long as it lasts).
    const stalled = now - a.heard >= S.stall_seconds * 1000;
    if ((!ci.muted && v > Ask.VOICE_ON && !stalled) || said > ci.said) {  // the listener is talking: nothing closes over them
      ci.voiceAt = now;
      ci.said = said;
      ci.closing = null;
    }
    if (!a.guideTurns && stalled) return Ask.back("timeout");  // the guide never answered its opening
    if (a.mode !== "listening" || !a.guideTurns) return;  // the guide has not finished speaking
    const quiet = now - Math.max(a.heard, ci.voiceAt);
    if (ci.closing) {  // the silence note is sent: the guide's word or two, then the session closes
      if ((a.guideTurns > ci.closing.turns && quiet >= 350) || now - ci.closing.at >= 6000) Ask.back("silence");
      return;
    }
    // The next part is ready: asked for as a turn once the listener has gone quiet, and again if the
    // guide's turn ended without going back (it may have answered something first). Three times at most,
    // and not while the guide still owes the listener a reply: it knows already, and may go back itself.
    if (ci.ready && ci.readySent < 3 && ci.readyFor !== a.guideTurns && !owed(a) && quiet >= S.quiet_seconds * 1000) {
      ci.readyFor = a.guideTurns;
      ci.readySent++;
      return Ask.send(a.notes.ready);
    }
    if (ci.muted) {  // the question waits for a tap; with none, the session closes quietly
      if (quiet >= S.tap_wait_seconds * 1000) Ask.back("silence");
      return;
    }
    if (quiet >= S.silence_seconds * 1000) {
      ci.closing = { turns: a.guideTurns, at: now };
      Ask.send(a.notes.silence);
    }
  }

  function tapped(a) {  // a tap during the check-in: open the mic if it was waiting; else ask.js ends it
    const ci = F.ci;
    if (!ci || !ci.muted || !a.conv) return false;
    try { a.conv.setMicMuted(false); } catch (e) { /* it stays as it is */ }
    ci.muted = false;
    ci.voiceAt = a.heard = performance.now();
    render();
    return true;
  }

  const ciLine = () => (F.ci && F.ci.muted ? "Tap to answer" : "");

  function done(how, a) {  // the check-in is over (ask.js has closed the session and logged it)
    F.ci = null;
    if (F.mode === "waiting") {
      const resumed = !!a.resuming && how === "guide";                 // the lead-in has been said
      if (!playable(F.n, F.i)) F.talk = !resumed;                      // back to the making look (resumed: the
      else if (resumed) return play(F.n, F.i, 0, true);                // status with the new piece is a moment behind)
      else arrived();
    }
    render();
  }

  // --- the status ---------------------------------------------------------------------------------
  function announce() {  // first audio: the same signals as a chapter becoming ready
    const title = status.title || "your chapter";
    note("Ready to play: " + title);
    if (typeof resting !== "undefined" && resting && !starting && typeof wake === "function") wake("ready");  // it said it would tell them
    else if (voiceConv && readyNote) { pendingReady = readyNote.replace("{title}", title); flushReady(); }
    if ("Notification" in window && Notification.permission === "granted") {
      new Notification("Your chapter is starting", { body: status.title || "" });
    }
  }

  function onStatus(s) {  // called by the page for every status, before it acts on it
    if (s.mode !== "fast" || !s.run) return;
    if (s.run !== F.run) {
      if (heardOn()) return;             // the chapter being heard plays on; the new one is taken up after it
      if (s.state !== "making") return;  // a fast chapter finished earlier: an ordinary chapter
      clearTimers();  // another chapter: start over (a check-in still open keeps its own timers and closes as usual)
      Object.assign(F, { run: s.run, secs: [], first: null, mode: "idle", n: 1, i: 0, ln: null, li: null, url: null,
                         live: false, base: 0, started: false, kept: false, since: null, reason: null,
                         asked: false, talk: false });
    }
    const was = F.first;
    F.secs = Array.isArray(s.sections) ? s.sections : [];
    F.first = !!s.first_audio;
    if (s.state === "ready") {
      if (F.kept) return;
      if (!F.started) { F.run = null; return; }  // never played here: the page's own ready takes over
      F.kept = true;                             // heard while it was made: the pieces play on to the end
      fresh = true;
      if (F.mode === "done") setTimeout(release, 0);
      else if (F.mode === "waiting") arrived();
      return;
    }
    if (s.state !== "making") return;  // a problem: the page shows it; the place is kept
    if (was === false && F.first) announce();
    if (F.mode === "waiting") waitingStatus(s);
  }

  // --- taps ---------------------------------------------------------------------------------------
  function startAt() {  // where a first tap begins: where the status says the listener was, else the start
    const s = F.secs.find((x) => x.state === "playing") || F.secs.find((x) => x.state !== "played") || F.secs[0];
    return s ? s.n : 1;
  }

  async function tap() {
    if (F.busy || starting) return;
    F.busy = true;
    try {
      if (F.mode === "playing") return pause();
      const ended = guide();
      if (ended) await endGuide();  // the mic would hear the chapter
      if (!active() && !heardOn()) return;
      if (F.mode === "waiting") { if (!ended && !F.beat) checkIn(true); return; }  // "Talk while you wait"
      if (F.mode === "idle") play(startAt(), 0);
      else if (F.mode === "done") play(F.secs.length ? F.secs[0].n : 1, 0);
      else resume();
    } finally {
      F.busy = false;
    }
  }

  function onTap() {  // true: the tap was ours
    if (heardOn()) { tap(); return true; }
    if (!active() || (F.mode === "idle" && !F.first)) return false;  // nothing to play yet: the page's own tap
    tap();
    return true;
  }

  function markerState(s) {
    const has = (s.pieces || []).length > 0;
    if (!F.started) return WORDS[s.state] ? s.state : has ? "ready" : "being_written";
    if (F.mode === "done" || s.n < F.n) return "played";
    if (s.n === F.n && has && (F.mode !== "waiting" || F.i > 0)) return "playing";
    return has ? "ready" : "being_written";
  }

  async function markerTap(n) {
    const s = sec(n);
    if (!s || !active() || F.busy || starting || F.ci || talking()) return;  // never over a conversation
    if (markerState(s) === "being_written") {
      // What skipping does: to the next marker only. It plays if it is ready; if not, the wait is for it.
      const atMarker = F.mode === "waiting" && F.i === 0;
      if (!F.started || F.mode === "done" || atMarker || F.n >= last()) return;
      n = F.n + 1;
      if (!playable(n, 0)) return wait(n, 0, "skip");
    }
    F.busy = true;  // it plays: a conversation still open ends first (the mic would hear the chapter)
    try {
      if (guide()) await endGuide();
      if (active()) play(n, 0);
    } finally {
      F.busy = false;
    }
  }

  // --- what the page shows ------------------------------------------------------------------------
  // Seconds left of a countdown the run gave as `secs` at status.updated; null when none, or it has run out.
  function left(secs) {
    if (secs == null) return null;
    const t = Date.parse(status.updated || "");
    const rest = Number(secs) - (Number.isNaN(t) ? 0 : Math.max(0, (Date.now() - t) / 1000));
    return rest > 0 ? rest : null;
  }
  // Whole minutes rounded up, as the guide is told them (faststart/guide_hooks.plain).
  const about = (rest) => (rest < 60 ? "under a minute" : `about ${Math.ceil(rest / 60)} min`);

  const style = document.createElement("style");
  style.textContent = `
  #steps { list-style: none; margin: 0; padding: 0; font-size: 14px; color: var(--muted); }
  #steps li { display: flex; align-items: center; gap: 8px; padding: 2px 0; }
  #steps .mark { width: 14px; text-align: center; flex: none; }
  #steps li[data-state="done"] .mark::before { content: "\\2713"; }
  #steps li[data-state="doing"] { color: var(--fg); }
  #steps li[data-state="doing"] .mark::before { content: ""; display: inline-block; width: 8px; height: 8px;
                                                border-radius: 50%; background: var(--accent); animation: stir 1.6s ease-in-out infinite; }
  #steps li[data-state="todo"] { opacity: .6; }
  #steps li[data-state="todo"] .mark::before { content: "\\00b7"; }
  #steps .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  @keyframes stir { 50% { opacity: .3; } }
  @media (prefers-reduced-motion: reduce) { #steps li[data-state="doing"] .mark::before { animation: none; } }
  #sections { list-style: none; margin: 0; padding: 0; width: 100%; max-width: 26em; }
  #sections button { width: 100%; display: flex; justify-content: space-between; align-items: baseline; gap: 16px;
                     font: inherit; font-size: 14px; color: var(--muted); background: none; border: 0; border-radius: 6px;
                     padding: 4px 8px; text-align: left; cursor: pointer; }
  #sections button:focus-visible { outline: 2px solid var(--accent); }
  #sections .state { font-size: 12px; white-space: nowrap; }
  #sections button[data-state="ready"] .title { color: var(--fg); }
  #sections button[data-state="playing"] .title { color: var(--fg); font-weight: 600; }
  #sections button[data-state="playing"] .state { color: var(--accent); }
  #sections button[data-state="being_written"] { font-style: italic; }
  body.fast-wait #orb { cursor: pointer; }`;
  document.head.appendChild(style);

  const stepList = document.createElement("ol");
  stepList.id = "steps";
  stepList.setAttribute("aria-label", "Making the opening");
  stepList.style.display = "none";
  $("line").after(stepList);
  const HIDDEN = { done: ", done", doing: ", now", todo: "" };

  function openingSteps(listed) {  // one item per step; only what changed is touched
    while (stepList.children.length > listed.length) stepList.lastChild.remove();
    listed.forEach((x, k) => {
      let li = stepList.children[k];
      if (!li) {
        li = document.createElement("li");
        li.innerHTML = '<span class="mark" aria-hidden="true"></span><span class="label"></span><span class="sr"></span>';
        stepList.appendChild(li);
      }
      if (li.dataset.state !== x.state) {
        li.dataset.state = x.state;
        li.lastChild.textContent = HIDDEN[x.state] || "";
        if (x.state === "doing") li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
      }
      if (li.children[1].textContent !== x.label) li.children[1].textContent = x.label;
    });
  }
  // Before the first audio: the steps to it, from the status (a status from before the steps has none).
  const opening = () => (!F.first && Array.isArray(status.steps) && status.steps.length ? status.steps : null);

  const list = document.createElement("ol");
  list.id = "sections";
  list.setAttribute("aria-label", "Parts of the chapter");
  list.style.display = "none";
  ($("read") || $("line")).after(list);
  list.addEventListener("click", (e) => {
    const b = e.target.closest ? e.target.closest("button[data-n]") : null;
    if (b) markerTap(Number(b.dataset.n));
  });

  function markers() {  // one button per section; only what changed is touched
    while (list.children.length > F.secs.length) list.lastChild.remove();
    F.secs.forEach((s, k) => {
      let li = list.children[k];
      if (!li) {
        li = document.createElement("li");
        li.innerHTML = '<button type="button"><span class="title"></span><span class="state"></span></button>';
        list.appendChild(li);
      }
      const b = li.firstChild, state = markerState(s), title = s.title || `Part ${s.n}`;
      let word = WORDS[state];
      if (state === "being_written" && STEPS[s.step]) {  // what is happening to it, and when it is expected
        const rest = left(s.ready_in);
        word = STEPS[s.step] + (rest === null ? "" : ` · ready in ${about(rest)}`);
      }
      if (b.dataset.n !== String(s.n)) b.dataset.n = s.n;
      if (b.dataset.state !== state) b.dataset.state = state;
      if (b.lastChild.textContent !== word) b.lastChild.textContent = word;
      if (b.firstChild.textContent !== title) b.firstChild.textContent = title;
      const name = `${title}: ${word}`;
      if (b.getAttribute("aria-label") !== name) b.setAttribute("aria-label", name);
    });
  }

  // A tap would play: the chapter has begun, or its first audio is ready.
  const canPlay = () => F.mode === "playing" || F.mode === "paused" || F.mode === "done" || (F.mode === "idle" && !!F.first);

  function line() {  // what the line under the circle says for a fast run, or null to leave it to the page
    if (!active()) return null;
    if (canPlay()) return status.title || "Your chapter is starting";
    if (opening() && status.state === "making") {  // the step under way, and the countdown to the first audio
      // none once the step has run past its median: the scout and the outline write no status meanwhile
      const rest = left(status.step_ends_in) === null ? null : left(status.first_audio_in);
      return (status.label || "Working") + (rest === null ? "" : ` · ${about(rest)}`);
    }
    return F.mode === "waiting" && F.talk ? "Talk while you wait" : null;
  }

  function draw() {  // called at the end of the page's render()
    const on = active(), steps = on ? opening() : null;
    stepList.style.display = steps ? "" : "none";
    list.style.display = on && F.secs.length && !steps ? "" : "none";  // the steps first, then the markers
    if (!on) return;
    if (steps) openingSteps(steps);
    markers();
    const b = document.body.classList;
    b.add("fast");
    if (canPlay() && b.contains("making")) { b.remove("making"); b.add("ready"); }  // the ready look: it can be played
    if (F.gap) b.add("playing");                                                    // the pause between two sections
    if (F.mode === "waiting") b.add("fast-wait");                                   // the making look, and a tap talks
    const l = window.Ask && Ask.line() !== null ? null : line();                    // an aside, or "Ask a question", comes first
    if (l !== null) $("line").textContent = l;
  }

  setInterval(() => { if (active() && status.state === "making") render(); }, 5000);  // the countdowns run down

  debug.state = () => ({ run: F.run, mode: F.mode, n: F.n, i: F.i, started: F.started, kept: F.kept, talk: F.talk,
                         checkIn: !!F.ci, since: F.since, reason: F.reason });
  window.Fast = { active, line, render: draw, onTap, onStatus, time, seek, debug };
  if (status && status.mode === "fast") { onStatus(status); render(); }  // a status that came before this script
})();
