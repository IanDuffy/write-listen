// window.Sig: how each of a person's turns sounded, measured on the page (Ian's signals build plan, 2026-11-21; the server's side is
// signals/live.py). The pause before they answered, how long they spoke, their pauses inside it and their level,
// from two levels sampled every tick_ms: the mic, and the voice coming in. It measures and never judges, and keeps
// nothing it hears: only the numbers go, with the line they belong to.
//
// The rules are the replay's (signals/measure.py), so the page and a recording agree on the same turns:
// - the mic is voiced above min_rms and floor_x times the room's level (the room: its quiet readings while the
//   other voice is silent; it falls at once and rises slowly). On a loudspeaker the other voice reaches the mic, so
//   while it is voiced (and bleed_tail_ms after) the mic counts only at bleed_x times its level under that voice;
// - quiet shorter than fill_ms inside speech is speech, and a voiced run shorter than min_run_ms is a click;
// - gap_ms is the first voiced frame less the other voice's last voiced frame (negative: they cut in); a quiet run
//   of pause_ms or more inside a turn is a pause; rms_db is the level of the voiced frames, in dBFS.
// A line's transcript comes seconds after the voice, so a line takes the runs from lead_ms before its first words
// to its last; ticks further apart than max_tick_gap_ms (a tab in the background) leave its timing unknown (null).
// Times are on the page's clock; Clock maps them to the voice session's own (its recording's), from the times
// the incoming transcript carries.
//
// One file, two uses: a plain script, served ahead of the page scripts that open the mic and only while signals
// are on; and a Node module (tests/signals/test_signals_sig_js.py). (No product words here: it may be served with
// the welcome's own script. Not even the word for adding an event handler.)
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.Sig = root.Sig || factory();
}(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const DEFAULTS = { tick_ms: 20, min_rms: 0.01, floor_x: 4, out_rms: 0.005, bleed_x: 3, bleed_tail_ms: 150,
                     fill_ms: 150, min_run_ms: 80, max_tick_gap_ms: 250, lead_ms: 4000, pause_ms: 700,
                     merge_ms: 1500 };
  const SILENT = 1e-6;          // a reading this low is digital silence (a track just enabled), not the room
  const FLOOR_RISE_MS = 20000;  // the room's level moves up toward a louder reading over about this long
  const KEEP_MS = 300000;       // frames kept: a line longer than this is measured from its last five minutes
  const BLEED_KEEP = 1500;      // mic readings under the other voice kept for its bleed level (30 s of ticks)
  const CLOCK_KEEP = 60;        // the first readings of a session set its clock (its transcript's times can drift)

  function median(xs) {
    if (!xs.length) return null;
    const s = xs.slice().sort((a, b) => a - b), k = s.length >> 1;
    return s.length % 2 ? s[k] : (s[k - 1] + s[k]) / 2;
  }

  // --- the tracker: one frame a tick ----------------------------------------------------------------------------
  function Tracker(opts) { this.o = Object.assign({}, DEFAULTS, opts || {}); this.reset(); }

  Tracker.prototype.reset = function () {
    this.ft = []; this.fm = []; this.fv = [];   // per tick: page ms, mic level (null: off), voiced
    this.other = [];                            // the other voice's runs {s, e}, fill_ms bridged
    this.breaks = [];                           // [from, to]: ticks further apart than max_tick_gap_ms
    this.lastT = null; this.outAt = null; this.floor = null;
    this.under = []; this.underN = 0; this.bleed = 0;
    this.used = -Infinity;                      // runs up to here belong to a line already measured
  };

  // t: page ms; mic: the mic's RMS (0-1), or null when it sends nothing (muted, none); out: the voice coming in.
  Tracker.prototype.push = function (t, mic, out) {
    const o = this.o;
    if (typeof t !== "number" || !isFinite(t)) return;
    if (this.lastT != null) {
      if (t <= this.lastT) return;
      if (t - this.lastT > o.max_tick_gap_ms) this.breaks.push([this.lastT, t]);
    }
    const dt = this.lastT == null ? o.tick_ms : t - this.lastT;
    this.lastT = t;
    if (typeof out === "number" && out >= o.out_rms) {
      const r = this.other[this.other.length - 1];
      if (r && t - o.tick_ms - r.e < o.fill_ms) r.e = t;
      else this.other.push({ s: t - o.tick_ms, e: t });
      this.outAt = t;
    }
    const near = this.outAt != null && t - this.outAt <= o.bleed_tail_ms;
    const m = typeof mic === "number" && mic >= 0 ? mic : null;
    let v = false;
    if (m != null && m > SILENT) {
      if (near) {
        this.under.push(m);
        if (this.under.length > BLEED_KEEP) this.under.shift();
        if (this.underN++ % 25 === 0) this.bleed = median(this.under);
      } else if (this.floor == null || m < this.floor) this.floor = m;
      else this.floor += (m - this.floor) * Math.min(1, dt / FLOOR_RISE_MS);
      v = m >= Math.max(o.min_rms, o.floor_x * (this.floor || 0)) && (!near || m >= o.bleed_x * this.bleed);
    }
    this.ft.push(t); this.fm.push(m); this.fv.push(v);
    if (t - this.ft[0] > KEEP_MS + 60000) {
      const k = this.index(t - KEEP_MS);
      this.ft.splice(0, k); this.fm.splice(0, k); this.fv.splice(0, k);
    }
    if (this.other.length > 500) this.other.splice(0, this.other.length - 500);
    if (this.breaks.length > 100) this.breaks.splice(0, this.breaks.length - 100);
  };

  Tracker.prototype.index = function (after) {  // the first frame later than `after`
    let lo = 0, hi = this.ft.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (this.ft[mid] > after) hi = mid; else lo = mid + 1; }
    return lo;
  };

  // The mic's voiced runs {s, e} from the frames after `from`: fill_ms bridged, then runs under min_run_ms dropped.
  Tracker.prototype.runs = function (from) {
    const o = this.o, out = [];
    let cur = null;
    for (let i = this.index(from); i < this.ft.length; i++) {
      if (!this.fv[i]) continue;
      const t = this.ft[i];
      if (cur && t - o.tick_ms - cur.e < o.fill_ms) cur.e = t;
      else { if (cur) out.push(cur); cur = { s: t - o.tick_ms, e: t }; }
    }
    if (cur) out.push(cur);
    return out.filter((r) => r.e - r.s >= o.min_run_ms);
  };

  Tracker.prototype.others = function () {
    const min = this.o.min_run_ms;
    return this.other.filter((x) => x.e - x.s >= min);
  };

  // One line, from its first words to its last (page ms, when the transcript brought them): the raw part of the
  // turn it adds. Its runs: those not yet measured, from lead_ms before its first words to its last; and of those,
  // only the ones after the other voice last spoke between them (a sound under the other voice, an "mm" or a
  // cough, is not this line: the replay splits a turn wherever the other voice spoke between two runs).
  Tracker.prototype.take = function (start, end) {
    const o = this.o, others = this.others();
    const from = Math.max(start - o.lead_ms, this.used);
    let rs = this.runs(from).filter((r) => r.s <= end);
    for (let k = rs.length - 1; k > 0; k--) {
      const a = rs[k - 1], b = rs[k];
      if (others.some((x) => x.s < b.s && x.e > a.e)) { rs = rs.slice(k); break; }
    }
    this.used = Math.max(this.used, end, rs.length ? rs[rs.length - 1].e : -Infinity);
    const part = { runs: rs.map((r) => ({ s: r.s, e: r.e })), sumsq: 0, n: 0, gap_ms: null, cut_in: null, broken: false };
    if (!rs.length) return part;
    let k = 0;
    for (let i = this.index(rs[0].s); i < this.ft.length && this.ft[i] <= rs[rs.length - 1].e; i++) {
      const t = this.ft[i];
      while (k < rs.length && t > rs[k].e) k++;
      if (k < rs.length && t > rs[k].s && this.fm[i] != null) { part.sumsq += this.fm[i] * this.fm[i]; part.n++; }
    }
    const first = rs[0].s;
    let prev = null;
    for (const x of others) { if (x.s < first) prev = x; else break; }  // the other voice's last run begun before
    if (prev) { part.gap_ms = Math.round(first - prev.e); part.cut_in = prev.e > first; }
    const lo = Math.min(from, prev ? prev.e : from);
    part.broken = this.breaks.some((b) => b[1] > lo && b[0] < end);
    return part;
  };

  // A turn's later line added to what it had: the gap is its first spoken line's.
  function combine(a, b) {
    if (!a) return b || null;
    if (!b) return a;
    const head = a.runs.length ? a : b;
    return { runs: a.runs.concat(b.runs), sumsq: a.sumsq + b.sumsq, n: a.n + b.n, gap_ms: head.gap_ms,
             cut_in: head.cut_in, broken: a.broken || b.broken };
  }

  // The raw measures of a part (signals/live.py RANGES), `first`: its first voiced frame (page ms). `at(ms)`: the
  // voice session's clock in seconds, or null.
  function raw(part, at, opts) {
    const o = Object.assign({}, DEFAULTS, opts || {});
    const out = { gap_ms: null, dur_ms: null, speak_ms: null, pauses: null, longest_pause_ms: null, cut_in: null,
                  rms_db: part && part.n && part.sumsq > 0 ? Math.round(100 * Math.log10(part.sumsq / part.n)) / 10 : null,
                  start_s: null, end_s: null, first: null };
    if (!part || !part.runs.length || part.broken) return out;
    const rs = part.runs, first = rs[0].s, last = rs[rs.length - 1].e;
    let quiet = 0, pauses = 0, longest = 0;
    for (let k = 1; k < rs.length; k++) {
      const q = rs[k].s - rs[k - 1].e;
      if (q >= o.pause_ms) { quiet += q; pauses++; longest = Math.max(longest, q); }
    }
    return Object.assign(out, {
      gap_ms: part.gap_ms, cut_in: part.cut_in, dur_ms: Math.round(last - first), speak_ms: Math.round(last - first - quiet),
      pauses, longest_pause_ms: Math.round(longest), start_s: at ? at(first) : null, end_s: at ? at(last) : null, first });
  }

  // --- the voice session's clock ------------------------------------------------------------------------------
  // The incoming transcript's deltas carry start_ms and end_ms on the session's clock, which its recording runs
  // on, and arrive with the sound: page ms less end_ms is the offset, its median per session.
  function Clock(keep) { this.keep = keep || CLOCK_KEEP; this.seen = {}; }
  Clock.prototype.see = function (id, pageMs, endMs) {
    if (!id || typeof pageMs !== "number" || typeof endMs !== "number" || !isFinite(endMs)) return;
    const a = this.seen[id] || (this.seen[id] = []);
    if (a.length < this.keep) a.push(pageMs - endMs);
  };
  Clock.prototype.offset = function (id) { const a = id ? this.seen[id] : null; return a && a.length ? median(a) : null; };
  Clock.prototype.at = function (id, pageMs) {
    const off = this.offset(id);
    return off == null || typeof pageMs !== "number" ? null : Math.round(pageMs - off) / 1000;
  };

  // --- turns -------------------------------------------------------------------------------------------------
  // A turn's number for its first spoken line; a typed line takes none, but may begin a turn. `same`: the line
  // continues the turn, by the page's own rule (countLine in the main page script) or by rule(): lines within
  // merge_ms of each other with no line of the other voice between.
  function Turns(mergeMs) { this.merge = mergeMs || DEFAULTS.merge_ms; this.reset(); }
  Turns.prototype.reset = function () { this.n = 0; this.open = false; this.end = null; this.otherAt = 0; this.part = null; };
  Turns.prototype.other = function (t) { this.otherAt = t; };  // a line of the other voice ended at t
  Turns.prototype.rule = function (start, end) {
    const same = this.end != null && start - this.end < this.merge && !(this.otherAt > this.end);
    this.end = end;
    return same;
  };
  Turns.prototype.line = function (same, spoken) {
    if (!same) { this.open = false; this.part = null; }
    if (!spoken) return null;
    if (!this.open) { this.open = true; this.n += 1; this.part = null; }
    return this.n;
  };

  // --- on a page: the sampler ---------------------------------------------------------------------------------
  function level(x) {
    x.a.getFloatTimeDomainData(x.buf);
    let s = 0;
    for (let i = 0; i < x.buf.length; i++) s += x.buf[i] * x.buf[i];
    return Math.sqrt(s / x.buf.length);
  }

  function Meter(opts) {
    this.o = Object.assign({}, DEFAULTS, opts || {});
    this.tracker = new Tracker(this.o); this.clock = new Clock(); this.turns = new Turns(this.o.merge_ms);
    this.mic = null; this.out = null; this.ctx = null;
  }
  Meter.prototype.reset = function () { this.tracker.reset(); this.clock = new Clock(); this.turns.reset(); };
  // Analysers of their own (float levels, about a tick long), never connected to the speakers: the voices stay on
  // media elements, the path the echo canceller covers.
  Meter.prototype.analyser = function (stream, ctx) {
    try {
      const c = ctx || this.ctx || (this.ctx = new AudioContext());
      if (c.state === "suspended" && c.resume) c.resume().catch(() => {});
      const a = c.createAnalyser();
      a.fftSize = 1024;
      c.createMediaStreamSource(stream).connect(a);
      return { a, buf: new Float32Array(a.fftSize) };
    } catch (e) { return null; }
  };
  Meter.prototype.input = function (stream, ctx) { this.mic = stream ? this.analyser(stream, ctx) : null; };
  Meter.prototype.output = function (stream, ctx) { this.out = stream ? this.analyser(stream, ctx) : null; };
  Meter.prototype.tick = function (t, micOff) {
    let mic = null, out = 0;
    try { mic = micOff || !this.mic ? null : level(this.mic); out = this.out ? level(this.out) : 0; } catch (e) { mic = null; }
    this.tracker.push(t, mic, out);
  };
  Meter.prototype.heard = function (id, t, endMs) { this.clock.see(id, t, endMs); };  // an incoming transcript delta
  // A line ended: its turn's number and the turn's measures so far, or null for a typed line. `id`: the session.
  Meter.prototype.line = function (start, end, same, spoken, id, t) {
    const turn = this.turns.line(same, spoken);
    if (turn == null) return null;
    this.turns.part = combine(this.turns.part, this.tracker.take(start, end));
    const r = raw(this.turns.part, (ms) => this.clock.at(id, ms), this.o);
    const first = r.first;
    delete r.first;
    r.turn = turn;
    r.ago_ms = first == null ? null : Math.max(0, Math.round(t - first));
    return r;
  };

  return { DEFAULTS, Tracker, Clock, Turns, Meter, combine, raw, median };
}));
