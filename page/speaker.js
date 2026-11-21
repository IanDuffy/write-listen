// window.Speaker: the voices on a phone's loudspeaker, not its ear speaker (Ian, 2026-11-21: on his phone the
// voice came out of the ear speaker, too quiet unless held to his ear). Served at the top of every page script
// that opens the mic (stages/common.with_speaker); the first one to run defines it, so a document has one,
// whichever of those scripts it loads. (No product words here: it is served with the welcome's own script.
// Not even the word for adding an event handler: the handlers here are on* properties, chained.)
//
//   Speaker.mic()        the mic, as a promise of its stream: call it in the tap, where getUserMedia was called.
//                        On an iPhone it first sets navigator.audioSession.type to "play-and-record". On Android
//                        it may resolve with a second capture of the "Speakerphone" input (below): always use the
//                        stream it resolves with, never one opened before it resolved.
//   Speaker.hold(track)  a mic track opened some other way (a clone, a stream handed over): counted as live.
//   Speaker.free()       call after stopping mic tracks: once none is live, the type goes back to "auto".
//   Speaker.playing(s)   the remote voice's element began playing stream `s`: recorded once per stream.
//   Speaker.follow(fn)   fn(from, to, stream) when a mic track is replaced inside `stream` (Android, a headset
//                        connected after the Speakerphone switch, below): put `to` where `from` was sent or kept.
//   Speaker.reports      the last few route records (for tests and a console).
//
// iPhone (looked into on 2026-11-21, in the WebKit and Chromium sources): with the mic open, WebKit on iOS picks
// the loudspeaker (PlayAndRecord with DefaultToSpeaker, VideoChat mode), but the route has been seen to drop to
// the ear speaker when the category flips as capture comes and goes (between two voice sessions, a mic restart).
// Setting "play-and-record" before the mic opens and keeping it while any mic track lives holds it there;
// headphones and Bluetooth still take the route when connected. Safari 16.4+ only; only on a touch screen.
// Never another type while the mic is open (the spec ends the mic track), and the voices stay on media
// elements fed by the remote stream, never through Web Audio: that is the path the echo canceller covers.
//
// Android (Ian's Android phone, 2026-11-21: the voice from the ear speaker, very quiet). Chrome, with echo
// cancellation on, puts Android in its communication mode while a mic is open and asks for one communication
// device (wired > USB > Bluetooth > loudspeaker; never the ear speaker), without checking that it took; when it
// doesn't, calls default to the ear speaker. Chrome names Android's inputs in fixed English: "Speakerphone",
// "Headset earpiece" (the built-in ear speaker), "Wired headset", "Bluetooth headset", "USB audio"; opening the
// input by its deviceId asks for that device again. So once the first capture is open, if no headset is listed,
// a second capture of "Speakerphone" is opened while the first is still live (the mode is not left between
// them), then the first is stopped. Any failure keeps the first. That request outlives the first moment: Chrome
// keeps an asked-for device while it is there, and the loudspeaker always is, so a headset plugged in or paired
// later would never be chosen over it. So on a device change with a headset listed, a capture with no device asked
// for is opened while the Speakerphone one is still live (the choice is Chrome's again: wired > USB > Bluetooth),
// its track takes the old one's place in the same stream, the pages that sent or kept the old one are told
// (follow), and the old one is stopped. Chrome on Android has no setSinkId, no
// selectAudioOutput and no audioSession, and no web API says where the sound actually goes, so each step is
// recorded ("audio_route", tester pages only): the admin's transcript shows the phone, its inputs and the mic.
window.Speaker = window.Speaker || (() => {
  "use strict";
  const CONSTRAINTS = { audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } };
  // Filled in by stages/common.with_speaker from config.yaml `speaker` (report: tester mode is on).
  const SETTINGS = Object.assign({ android_speakerphone: true, report: false, report_gap_ms: 5000,
                                   switch_timeout_ms: 4000, report_url: "/api/demo/event" },
                                 /*SPEAKER_SETTINGS*/{}/*END*/);
  const SPEAKERPHONE = "Speakerphone";
  const HEADSETS = ["Wired headset", "Bluetooth headset", "USB audio"];
  const held = new Set();  // every mic track opened in this document, clones too
  const lastSent = {};     // reason -> when it was last recorded
  const reports = [];
  const heard = new WeakSet();  // remote streams already recorded as playing
  const pinned = new Map();     // Android: a track opened on the "Speakerphone" input -> the stream it is in
  const followers = [];
  let hintsAsked = null, watching = false, moving = false, again = false;

  function touch() {
    try { return !!(window.matchMedia && matchMedia("(any-pointer: coarse)").matches); } catch (e) { return false; }
  }
  function type(want) {
    try {
      const a = navigator.audioSession;
      if (a && touch() && a.type !== want) a.type = want;
    } catch (e) { /* not this browser */ }
  }
  function android() {
    try {
      const d = navigator.userAgentData;
      return /Android/i.test(navigator.userAgent || "") || !!(d && d.platform === "Android");
    } catch (e) { return false; }
  }

  function live() {
    for (const t of held) if (t.readyState === "ended") held.delete(t);
    return held.size > 0;
  }

  function hold(track) {
    if (track && track.readyState !== "ended") { held.add(track); type("play-and-record"); }
    return track;
  }

  function free() {
    if (!live()) type("auto");
  }

  // --- the record ---------------------------------------------------------------------------------------------

  async function devices() {
    try { return await navigator.mediaDevices.enumerateDevices(); } catch (e) { return []; }
  }
  const labels = (all, kind) => all.filter((d) => d.kind === kind).map((d) => d.label || "(no label)");
  const headset = (label) => HEADSETS.some((h) => String(label || "").startsWith(h));

  function hints() {  // the phone's model and versions: Chrome's user agent string hides the model
    if (!hintsAsked) {
      hintsAsked = (async () => {
        const d = navigator.userAgentData;
        if (!d || !d.getHighEntropyValues) return null;
        const v = await d.getHighEntropyValues(["model", "platformVersion", "fullVersionList"]);
        const brands = v.fullVersionList || d.brands || [];
        const chrome = brands.find((b) => b.brand === "Google Chrome") || brands.find((b) => /Chrom/.test(b.brand));
        const platform = v.platform || d.platform || null;
        return { model: v.model || null, platform, android: platform === "Android" ? v.platformVersion || null : null,
                 chrome: chrome ? chrome.version : null };
      })().catch(() => null);
    }
    return hintsAsked;
  }

  function newest() {
    let last = null;
    for (const t of held) if (t.readyState === "live") last = t;
    return last;
  }

  // One record per reason every report_gap_ms at most; never waited for, never thrown.
  function report(reason, extra = {}) {
    try {
      if (!SETTINGS.report) return;
      const now = Date.now();
      if (lastSent[reason] && now - lastSent[reason] < SETTINGS.report_gap_ms) return;
      lastSent[reason] = now;
      live();
      // The track as it is now (it may be stopped by the time the devices are listed).
      const track = extra.track || newest();
      const count = held.size;
      let settings = null, ec_modes = null;
      try {
        const s = track ? track.getSettings() : null;
        if (s) settings = { deviceId: s.deviceId, echoCancellation: s.echoCancellation, noiseSuppression: s.noiseSuppression,
                            autoGainControl: s.autoGainControl, sampleRate: s.sampleRate };
      } catch (e) { /* no settings */ }
      try {
        const c = track && track.getCapabilities ? track.getCapabilities() : null;
        if (c && c.echoCancellation !== undefined) ec_modes = [].concat(c.echoCancellation);
      } catch (e) { /* no capabilities */ }
      const track_label = track ? track.label : null;
      (async () => {
        const [all, ua_hints] = await Promise.all([devices(), hints()]);
        const data = { reason, result: extra.result || null, ua: navigator.userAgent || null, ua_hints,
                       inputs: labels(all, "audioinput"), outputs: labels(all, "audiooutput"),
                       track_label, settings, ec_modes, live_mic_tracks: count };
        reports.push(data);
        if (reports.length > 20) reports.shift();
        fetch(SETTINGS.report_url, { method: "POST", headers: { "Content-Type": "application/json" }, keepalive: true,
                                     body: JSON.stringify({ event: "audio_route", data }) }).catch(() => {});
      })().catch(() => {});
    } catch (e) { /* never the page's problem */ }
  }

  function watch() {  // a headset plugged in or paired later: recorded, and on Android taken back from Speakerphone
    if (watching) return;
    try {
      const md = navigator.mediaDevices;
      if (!md || !("ondevicechange" in md)) return;
      watching = true;
      const before = md.ondevicechange;
      md.ondevicechange = function (e) {
        try { if (typeof before === "function") before.call(this, e); } catch (x) { /* theirs */ }
        report("devicechange");
        unpin();
      };
    } catch (e) { /* not this browser */ }
  }

  function playing(stream) {
    try {
      if (!stream || heard.has(stream)) return;
      heard.add(stream);
      report("playing");
    } catch (e) { /* never the page's problem */ }
  }

  // --- the mic ------------------------------------------------------------------------------------------------

  function follow(fn) {
    if (typeof fn === "function") followers.push(fn);
  }

  // A capture of `want`, or null on a failure or when nothing opened within switch_timeout_ms (one that opens
  // later is stopped at once).
  function capture(want) {
    let late = false;
    let asked;
    try { asked = navigator.mediaDevices.getUserMedia(want); } catch (e) { return Promise.resolve(null); }
    return new Promise((resolve) => {
      const t = setTimeout(() => { late = true; resolve(null); }, SETTINGS.switch_timeout_ms);
      asked.then((s) => { clearTimeout(t); if (late) s.getTracks().forEach((x) => x.stop()); else resolve(s); },
                 () => { clearTimeout(t); resolve(null); });
    });
  }

  // Android: the "Speakerphone" input opened by its id while the first capture is live; resolves with the stream
  // to use (the first one on any failure). `outcome` for the record: switched, headset present, no label, failed.
  async function speakerphone(first) {
    let outcome = "failed", second = null;
    try {
      const all = await devices();
      const ins = all.filter((d) => d.kind === "audioinput");
      const sp = ins.find((d) => d.label === SPEAKERPHONE);
      if (ins.some((d) => headset(d.label))) outcome = "headset present";
      else if (!sp || !sp.deviceId) outcome = "no label";
      else {
        const want = { audio: Object.assign({}, CONSTRAINTS.audio, { deviceId: { exact: sp.deviceId } }) };
        second = await capture(want);
        if (second && second.getAudioTracks().some((x) => x.readyState === "live")) {
          second.getAudioTracks().forEach(hold);
          second.getAudioTracks().forEach((x) => pinned.set(x, second));
          first.getTracks().forEach((x) => { held.delete(x); x.stop(); });
          outcome = "switched";
        } else if (second) {
          second.getTracks().forEach((x) => x.stop());
          second = null;
        }
      }
    } catch (e) {
      if (second) { try { second.getTracks().forEach((x) => x.stop()); } catch (x) { /* gone */ } }
      second = null;
      outcome = "failed";
    }
    report("speakerphone", { result: outcome, track: (second || first).getAudioTracks()[0] });
    return second || first;
  }

  // Android: a headset now listed while a Speakerphone track is live. A capture with no device asked for, opened
  // beside it, hands the choice back to Chrome; its track replaces the old one in the old one's stream (enabled as
  // it was), the followers move what they send, and the old one is stopped. A failure keeps the old one (pinned: a
  // later device change tries again). One move at a time; a change during one is looked at once it is done.
  async function unpin() {
    if (moving) { again = true; return; }
    for (const t of [...pinned.keys()]) if (t.readyState !== "live") pinned.delete(t);
    if (!pinned.size) return;
    moving = true;
    try {
      const all = await devices();
      if (!all.some((d) => d.kind === "audioinput" && headset(d.label))) return;
      for (const [from, stream] of [...pinned]) {
        if (from.readyState !== "live") { pinned.delete(from); continue; }
        const got = await capture(CONSTRAINTS);
        const to = got ? got.getAudioTracks().find((x) => x.readyState === "live") : null;
        if (from.readyState !== "live") {  // let go of meanwhile: nothing to move
          pinned.delete(from);
          if (got) got.getTracks().forEach((x) => x.stop());
          continue;
        }
        if (!to) {
          if (got) got.getTracks().forEach((x) => x.stop());
          report("headset", { result: "failed", track: from });
          continue;
        }
        to.enabled = from.enabled;
        hold(to);
        try { stream.removeTrack(from); stream.addTrack(to); } catch (e) { /* the followers still move it */ }
        pinned.delete(from);
        for (const fn of followers) { try { fn(from, to, stream); } catch (e) { /* theirs */ } }
        held.delete(from);
        from.stop();
        report("headset", { result: "moved", track: to });
      }
    } catch (e) { /* never the page's problem */ } finally {
      moving = false;
      if (again) { again = false; unpin(); }
    }
  }

  function mic() {
    type("play-and-record");  // before getUserMedia, in the same tap
    let asked;
    try { asked = navigator.mediaDevices.getUserMedia(CONSTRAINTS); } catch (e) { asked = Promise.reject(e); }
    return asked.then(async (stream) => {
      stream.getAudioTracks().forEach(hold);
      watch();
      report("mic", { track: stream.getAudioTracks()[0] });
      if (!SETTINGS.android_speakerphone || !android()) return stream;
      try { return await speakerphone(stream); } catch (e) { return stream; }
    }, (e) => { free(); throw e; });
  }

  return { mic, hold, free, live, playing, follow, reports, settings: SETTINGS };
})();
