"""The page's measurer (page/sig.js) under Node: synthetic frames, one a tick, with known gaps, lengths, pauses,
bleed, a cut-in, a tab in the background, and the voice session's clock. The rules are the replay's
(signals/measure.py), so the numbers it gives are the ones a recording of the same turn would."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SIG_JS = Path(__file__).resolve().parents[2] / "page" / "sig.js"

HARNESS = r"""
const Sig = require(process.argv[1]);
const out = {};
const TICK = 20, ROOM = 0.002, VOICE = 0.3, OTHER = 0.2;
const span = (t, a, b) => t > a && t <= b;
// Frames from 0 to `to` ms: mic(t) and out(t) give the levels (mic null: off).
function feed(tr, to, mic, oth, step = () => TICK) {
  for (let t = 0; t <= to; t += step(t)) tr.push(t, mic(t), oth(t));
  return tr;
}
const pick = (r) => { const o = Sig.raw(r, null, {}); delete o.first; return o; };

// 1. A gap of 600 ms after the other voice, 800 ms of speech, with the other voice bleeding into the mic.
{
  const tr = feed(new Sig.Tracker({}), 6000,
    (t) => span(t, 1600, 2400) ? VOICE : span(t, 0, 1000) ? 0.05 : ROOM,
    (t) => span(t, 0, 1000) ? OTHER : 0);
  out.basic = pick(tr.take(4500, 4600));
  out.basic_bleed = tr.bleed;
  out.basic_again = pick(tr.take(5000, 5100));   // nothing left to take
}
// 2. Pauses: a 100 ms dip (speech), a 300 ms quiet (not a pause), a 1000 ms quiet (a pause).
{
  const tr = feed(new Sig.Tracker({}), 8000,
    (t) => (span(t, 1600, 2000) || span(t, 2100, 2400) || span(t, 2700, 3000) || span(t, 4000, 4400)) ? VOICE : ROOM,
    (t) => span(t, 0, 1000) ? OTHER : 0);
  out.pauses = pick(tr.take(5000, 5200));
}
// 3. A cut-in: they start 500 ms before the other voice stops.
{
  const tr = feed(new Sig.Tracker({}), 6000,
    (t) => span(t, 2500, 3500) ? VOICE : ROOM,
    (t) => span(t, 0, 3000) ? OTHER : 0);
  out.cut = pick(tr.take(5000, 5100));
}
// 4. A tab in the background: ticks a second apart before the speech; the timing is not taken, the level is.
{
  const tr = feed(new Sig.Tracker({}), 6000,
    (t) => span(t, 2200, 2800) ? VOICE : ROOM,
    (t) => span(t, 0, 600) ? OTHER : 0,
    (t) => t >= 1000 && t < 2000 ? 1000 : TICK);
  out.throttled = pick(tr.take(4000, 4100));
  out.breaks = tr.breaks;
}
// 5. No voiced frame: everything null.
{
  const tr = feed(new Sig.Tracker({}), 3000, () => ROOM, (t) => span(t, 0, 500) ? OTHER : 0);
  out.silent = pick(tr.take(2500, 2600));
}
// 6. Muted (null) frames are never voiced, nor counted toward the room.
{
  const tr = feed(new Sig.Tracker({}), 3000, (t) => span(t, 1000, 2000) ? null : ROOM, () => 0);
  out.muted = pick(tr.take(2500, 2600));
}
// 7. The room's level: at 0.004 a reading of 0.012 is above min_rms but not 4 times the room: quiet.
{
  const tr = feed(new Sig.Tracker({}), 4000,
    (t) => span(t, 2000, 2600) ? 0.012 : span(t, 3000, 3400) ? VOICE : 0.004, () => 0);
  out.floor = pick(tr.take(3600, 3700));
  out.floor_level = tr.floor;
}
// 8. An "mm" under the other voice, which then goes on: the line is the answer after it, not the "mm".
{
  const tr = feed(new Sig.Tracker({}), 7000,
    (t) => span(t, 1000, 1300) ? VOICE : span(t, 3000, 3600) ? VOICE : ROOM,
    (t) => span(t, 0, 2500) ? OTHER : 0);
  out.mm = pick(tr.take(5000, 5100));
}
// 9. Speech more than lead_ms before the line's first words is not the line's.
{
  const tr = feed(new Sig.Tracker({ lead_ms: 2000 }), 8000,
    (t) => span(t, 1000, 1500) ? VOICE : span(t, 5000, 5500) ? VOICE : ROOM,
    (t) => span(t, 0, 500) ? OTHER : 0);
  out.lead = pick(tr.take(6500, 6600));
}
// 10. The clock: page ms less end_ms, the median per session.
{
  const c = new Sig.Clock();
  c.see("live_1", 10000, 500); c.see("live_1", 10210, 700); c.see("live_1", 10390, 900);
  c.see("live_2", 50000, 0); c.see(null, 1, 1); c.see("live_1", 1, undefined);
  out.clock = { at: c.at("live_1", 11000), two: c.at("live_2", 52500), none: c.at("live_3", 11000),
                offset: c.offset("live_1") };
}
// 11. One turn over two lines, a typed line, then a new turn (Meter.line, as the page calls it).
{
  const m = new Sig.Meter({});
  const mic = (t) => (span(t, 1600, 2400) || span(t, 3400, 3800) || span(t, 9000, 9500)) ? VOICE : ROOM;
  const oth = (t) => span(t, 0, 1000) || span(t, 6000, 8000) ? OTHER : 0;
  m.clock.see("live_1", 500, 0);   // the session's clock: page ms - 500
  const lines = [];
  for (let t = 0; t <= 12000; t += TICK) {
    m.tracker.push(t, mic(t), oth(t));
    if (t === 2900) lines.push(m.line(2800, 2900, false, true, "live_1", t));    // turn 1, first line
    if (t === 4500) lines.push(m.line(4300, 4400, true, true, "live_1", t));     // the same turn
    if (t === 5000) lines.push(m.line(null, null, false, false, "live_1", t));   // typed: no number
    if (t === 10500) lines.push(m.line(10300, 10400, true, true, "live_1", t));  // spoken after the typed line
  }
  out.meter = lines;
}
// 12. The turn rule: within merge_ms, and no line of the other voice between.
{
  const tu = new Sig.Turns(1500);
  out.rule = [tu.rule(1000, 2000), tu.rule(3000, 3500), tu.rule(5100, 5600)];
  tu.other(6000);
  out.rule.push(tu.rule(6500, 7000));
  out.rule.push(tu.rule(7600, 8000));
}
// 13. A sound under the other voice's reply after the answer (a "yeah", an echo burst), taken in because the
// answer's words came late: the line is the answer, not the sound, and no cut-in.
{
  const tr = feed(new Sig.Tracker({}), 5400,
    (t) => span(t, 1500, 3000) ? VOICE : span(t, 3400, 3520) ? 0.05 : span(t, 0, 1000) || t > 3200 ? 0.01 : ROOM,
    (t) => span(t, 0, 1000) || t > 3200 ? OTHER : 0);
  out.burst = pick(tr.take(3500, 4500));
}
// 14. An "mm" in a pause of the other voice, which then goes on, then the answer: the answer.
{
  const tr = feed(new Sig.Tracker({}), 7000,
    (t) => span(t, 2100, 2300) ? VOICE : span(t, 4500, 5500) ? VOICE : ROOM,
    (t) => span(t, 0, 2000) || span(t, 2400, 4000) ? OTHER : 0);
  out.mm_pause = pick(tr.take(6000, 6200));
}
// 15. A real "wait, what?" under the other voice, which talks on, after an "mm" under it: the cut-in.
{
  const tr = feed(new Sig.Tracker({}), 6000,
    (t) => span(t, 1000, 1200) ? VOICE : span(t, 3000, 3800) ? VOICE : ROOM,
    (t) => span(t, 0, 4400) ? OTHER : 0);
  out.cut_after_mm = pick(tr.take(5000, 5200));
}
// 16. One utterance the transcript brought as two lines 2.5 s apart (two turns by countLine): the second goes on
// from their own speech, so it had no pause before answering. Without a break, with a 1 s pause inside (the replay's
// one turn: quiet under merge_ms), and with a 1.6 s pause (two turns in the replay too: the gap is the other voice's).
{
  const go = (mic) => {
    const m = new Sig.Meter({}), got = [];
    for (let t = 0; t <= 9000; t += TICK) {
      m.tracker.push(t, mic(t), span(t, 0, 1000) ? OTHER : 0);
      if (t === 4900) got.push(m.line(3500, 4000, false, true, "live_1", t));
      if (t === 7900) got.push(m.line(6500, 7000, false, true, "live_1", t));
    }
    return got;
  };
  out.split = go((t) => span(t, 1500, 8000) ? VOICE : ROOM);
  out.split_pause = go((t) => span(t, 1500, 3500) || span(t, 4500, 8000) ? VOICE : ROOM);
  out.split_long = go((t) => span(t, 1500, 3000) || span(t, 4600, 8000) ? VOICE : ROOM);
}
// 17. A rest: the other voice's last words, the session closed and the mic off for a minute, then a new session
// (Meter.session) where they speak first: no gap, as the replay gives a recording's first turn. Then the other
// voice speaks in it, and the next turn has its gap; the turn numbers go on.
{
  const run = (boundary) => {
    const m = new Sig.Meter({}), got = [];
    const mic = (t) => span(t, 5000, 60000) ? null
      : span(t, 1600, 2400) || span(t, 61500, 63000) || span(t, 67500, 68000) ? VOICE : ROOM;
    const oth = (t) => span(t, 0, 1000) || span(t, 4000, 5000) || span(t, 66000, 67000) ? OTHER : 0;
    for (let t = 0; t <= 70000; t += TICK) {
      m.tracker.push(t, mic(t), oth(t));
      if (t === 60000 && boundary) m.session(t);
      if (t === 3000) got.push(m.line(2800, 2900, false, true, "live_1", t));
      if (t === 65400) got.push(m.line(64000, 64500, false, true, "live_2", t));
      if (t === 69000) got.push(m.line(68500, 68600, false, true, "live_2", t));
    }
    return got;
  };
  out.rest = run(true);
  out.rest_unbounded = run(false);
}
// 18. A sound the other voice talked straight through, longer than the short answer after it: a "yeah, yeah" under
// its question, a loudspeaker's echo burst, a laugh at its joke. The answer is the line, with its own gap.
{
  const go = (to, mic, oth, start) => pick(feed(new Sig.Tracker({}), to, mic, oth).take(start, start + 100));
  out.under_before = {
    yeah: go(6000, (t) => span(t, 1000, 1600) || span(t, 3500, 3800) ? VOICE : ROOM,
             (t) => span(t, 0, 3000) ? OTHER : 0, 5000),
    echo: go(6000, (t) => span(t, 2000, 2400) ? 0.05 : span(t, 3500, 3750) ? VOICE : span(t, 0, 3000) ? 0.01 : ROOM,
             (t) => span(t, 0, 3000) ? OTHER : 0, 5000),
    laugh: go(7000, (t) => span(t, 1500, 2300) || span(t, 3700, 4300) ? VOICE : ROOM,
              (t) => span(t, 0, 3200) ? OTHER : 0, 5500),
  };
}
// 19. The answer, the other voice's short reply, then their next words, begun before the answer's transcript was
// done: the answer's line leaves them to the next line, which has its own gap and length. Their next words still
// going when the answer's line is taken, longer by then than the answer, count only up to the answer's last words.
// A captioned "yeah" under the reply gets its own line, a cut-in; an echo burst under it, left the same way, is
// dropped again by the next line.
{
  const go = (to, mic, oth, lines) => {
    const m = new Sig.Meter({}), got = [];
    for (let t = 0; t <= to; t += TICK) {
      m.tracker.push(t, mic(t), oth(t));
      for (const l of lines) if (t === l[2]) got.push(m.line(l[0], l[1], false, true, "live_1", t));
    }
    return got;
  };
  out.next_words = go(10000, (t) => span(t, 1500, 3500) || span(t, 4500, 8000) ? VOICE : ROOM,
                      (t) => span(t, 0, 1000) || span(t, 3700, 4200) ? OTHER : 0,
                      [[4000, 5000, 5900], [7000, 8500, 9400]]);
  out.next_words_long = go(10000, (t) => span(t, 1500, 3000) || span(t, 4300, 6500) ? VOICE : ROOM,
                           (t) => span(t, 0, 1000) || span(t, 3400, 4000) ? OTHER : 0,
                           [[4000, 5000, 5900], [6500, 8800, 9700]]);
  out.yeah_under_reply = go(7000, (t) => span(t, 1500, 3000) || span(t, 4000, 4300) ? VOICE : ROOM,
                            (t) => span(t, 0, 1000) || span(t, 3200, 6000) ? OTHER : 0,
                            [[3000, 4500, 5400], [5000, 5200, 6100]]);
  out.echo_under_reply = go(9000,
    (t) => span(t, 1500, 3000) ? VOICE : span(t, 3400, 3800) ? 0.05 : span(t, 5500, 5750) ? VOICE
      : span(t, 0, 1000) || span(t, 3200, 5000) ? 0.01 : ROOM,
    (t) => span(t, 0, 1000) || span(t, 3200, 5000) ? OTHER : 0,
    [[3500, 4500, 5400], [6000, 6500, 7400]]);
}
// The lines below come as the pages time them: posted line_quiet_ms (900) after their last words, which stand about
// lag_ms (2500) after the speech ended, as signals.recording.lag_s has it for the replay.
const lines = (to, mic, oth, ls, session) => {
  const m = new Sig.Meter({}), got = [];
  for (let t = 0; t <= to; t += TICK) {
    m.tracker.push(t, mic(t), oth(t));
    if (session && t === session) m.session(t);
    for (const l of ls) if (t === l[2]) got.push(m.line(l[0], l[1], false, true, "live_1", t));
  }
  return got;
};
// 20. A short answer after a longer sound no line took, inside lead_ms of its first words: a laugh at the other
// voice's joke (overlapping its last word, then a short question; and one after the joke), a 1.5 s noise in the
// room. The answer is the line, as the replay gives it; the sound neither nulls its gap nor reads as a cut-in.
{
  const joke = (t) => span(t, 0, 1000) || span(t, 3500, 5400) || span(t, 6600, 7600) ? OTHER : 0;
  out.untaken = {
    laugh_over: lines(13000, (t) => span(t, 5300, 6300) ? 0.05 : span(t, 1500, 3000) || span(t, 8000, 8500) ? VOICE : ROOM,
                      joke, [[2500, 4600, 5500], [9000, 10100, 11000]]),
  };
  const oth = (t) => span(t, 0, 1000) || span(t, 3500, 5500) || span(t, 7300, 8500) || span(t, 12000, 13000) ? OTHER : 0;
  const ls = [[2500, 4000, 4900], [9900, 10800, 11700], [14500, 16000, 16900]];
  out.untaken.laugh = lines(19000,
    (t) => span(t, 1500, 3000) || span(t, 5800, 7000) || span(t, 8900, 9300) || span(t, 13500, 15000) ? VOICE : ROOM, oth, ls);
  out.untaken.noise = lines(19000,
    (t) => span(t, 5300, 6800) ? 0.05 : span(t, 1500, 3000) || span(t, 8900, 9500) || span(t, 13500, 15000) ? VOICE : ROOM,
    oth, ls);
}
// 21. The other voice's "mm-hm" in the middle of one line, its reply, then a short "yes": the line gets the part the
// replay gives it (the second stretch, nearest its words), the rest of the line is measured, and the "yes" is its
// own line with its own gap, not the stretch left over.
out.backchannel = lines(12000, (t) => span(t, 1500, 4000) || span(t, 4600, 7000) || span(t, 8400, 8800) ? VOICE : ROOM,
                        (t) => span(t, 0, 1000) || span(t, 4100, 4400) || span(t, 7200, 8000) ? OTHER : 0,
                        [[2500, 8000, 8900], [9000, 9800, 10700]]);
// 22. A short answer, the other voice's quick "ok" (or a reply they barge in on), then their longer next words, the
// transcript bringing the two as lines: the answer is the first line even though the next words outlast it by its
// end, and the next words are the second, with their own gap (a cut-in when they barged in).
out.short_then_long = {
  ok: lines(10000, (t) => span(t, 1500, 2000) || span(t, 2600, 6000) ? VOICE : ROOM,
            (t) => span(t, 0, 1000) || span(t, 2100, 2400) ? OTHER : 0, [[2500, 3500, 4400], [5000, 6800, 7700]]),
  barge: lines(11000, (t) => span(t, 1500, 1900) || span(t, 2700, 6500) ? VOICE : ROOM,
               (t) => span(t, 0, 1000) || span(t, 2500, 2900) ? OTHER : 0, [[3400, 3400, 4400], [5200, 8000, 9000]]),
};
// The same short answer and next words, the lines timed by a page whose transcript lags far less: the speech end
// its first line implies falls in the next words, so the line is those (the options, not fixed numbers, place it).
{
  const m = new Sig.Meter({ line_quiet_ms: 900, lag_ms: 500 }), got = [];
  for (let t = 0; t <= 5000; t += TICK) {
    m.tracker.push(t, span(t, 1500, 2000) || span(t, 2600, 6000) ? VOICE : ROOM, span(t, 0, 1000) || span(t, 2100, 2400) ? OTHER : 0);
    if (t === 4400) got.push(m.line(2500, 3500, false, true, "live_1", t));
  }
  out.short_then_long.ok_short_lag = got;
}
// 23. A drop while they spoke (no line ever took it), a quick reconnect (Meter.session), their first line in the new
// session within lead_ms of that speech: only the new session's speech is the turn, and no gap until the other voice
// speaks in it.
out.drop_quick = lines(8000, (t) => span(t, 3000, 3300) ? null : span(t, 1500, 3000) || span(t, 4500, 5500) ? VOICE : ROOM,
                       (t) => span(t, 0, 1000) ? OTHER : 0, [[5000, 6000, 6500]], 3300);
// 24. The answer, then a sound under the other voice's reply (an echo burst; a captioned "yeah"), the transcript half a
// second slower than lag_ms (within the replay's lag search): the speech end the answer's line implies falls nearer
// the sound than the answer, but a sound the other voice talked straight through is never the line.
{
  const oth = (t) => span(t, 0, 1000) || span(t, 3200, 7000) || span(t, 12500, 13500) ? OTHER : 0;
  const ls = [[3500, 5100, 6000], [8600, 10700, 11600], [15000, 17600, 18500]];
  const later = (t) => span(t, 7600, 8600) || span(t, 14000, 15500);
  out.slow_under = {
    burst: lines(21000, (t) => span(t, 1500, 3000) ? VOICE : span(t, 3400, 3520) ? 0.05 : later(t) ? VOICE
      : span(t, 0, 1000) || span(t, 3200, 7000) ? 0.01 : ROOM, oth, ls),
    yeah: lines(21000, (t) => span(t, 1500, 3000) || span(t, 3600, 3900) || later(t) ? VOICE : ROOM, oth, ls),
  };
}
// 25. The speech end a line implies counts the quiet the page waits before posting it: their words never captioned,
// the other voice's answer, then a short "yes" (lag_ms alone would put it in the uncaptioned words); and the short
// answer and next words of 22 on a page whose lines close on 2.9 s of quiet, posted then.
out.quiet_end = { yes: lines(10000, (t) => span(t, 1500, 3500) || span(t, 4800, 5100) ? VOICE : ROOM,
                             (t) => span(t, 0, 1000) || span(t, 3700, 4500) ? OTHER : 0, [[5600, 6200, 7100]]) };
{
  const m = new Sig.Meter({ line_quiet_ms: 2900 }), got = [];
  for (let t = 0; t <= 7000; t += TICK) {
    m.tracker.push(t, span(t, 1500, 2000) || span(t, 2600, 6000) ? VOICE : ROOM, span(t, 0, 1000) || span(t, 2100, 2400) ? OTHER : 0);
    if (t === 6400) got.push(m.line(2500, 3500, false, true, "live_1", t));
  }
  out.quiet_end.long_quiet = got;
}
// 26. The short answer, a quick "ok", the next words still going when the answer's line is posted, its speech end in
// the quiet between: the answer, by the tenth of the distance to each part's end (end 3900), and with the next
// words' end as heard so far, not cut at the line's last words (end 4000). Then the next words and a later turn.
out.between = {};
for (const end of [3900, 4000]) {
  out.between[end] = lines(19000, (t) => span(t, 1500, 2000) || span(t, 2600, 6000) || span(t, 14000, 15500) ? VOICE : ROOM,
                           (t) => span(t, 0, 1000) || span(t, 2100, 2400) || span(t, 12500, 13500) ? OTHER : 0,
                           [[2500, end, end + 900], [5000, 7600, 8500], [15000, 17100, 18000]]);
}
// 27. Two parts at the same distance from the speech end (550 each; 330 each): the earlier, as the replay gives it.
// The next words still going at the post, then their own line; a later part over by the post, which no line takes.
out.tie = {
  going: lines(19000, (t) => span(t, 1500, 2000) || span(t, 2800, 6000) || span(t, 14000, 15500) ? VOICE : ROOM,
               (t) => span(t, 0, 1000) || span(t, 2100, 2700) || span(t, 12500, 13500) ? OTHER : 0,
               [[2500, 4100, 5000], [5000, 7600, 8500], [15000, 17100, 18000]]),
  ended: lines(19000, (t) => span(t, 1500, 2000) || span(t, 2500, 3600) || span(t, 14000, 15500) ? VOICE : ROOM,
               (t) => span(t, 0, 1000) || span(t, 2100, 2400) || span(t, 12500, 13500) ? OTHER : 0,
               [[2500, 3900, 4800], [15000, 17100, 18000]]),
};
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out():
    node = shutil.which("node") or ("/opt/homebrew/bin/node" if Path("/opt/homebrew/bin/node").exists() else None)
    if not node:
        pytest.skip("node is not installed")
    res = subprocess.run([node, "-e", HARNESS, str(SIG_JS)], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_gap_and_length_with_the_other_voice_bleeding_in(out):
    b = out["basic"]
    assert b["gap_ms"] == 600 and b["dur_ms"] == 800 and b["speak_ms"] == 800
    assert b["pauses"] == 0 and b["longest_pause_ms"] == 0 and b["cut_in"] is False
    assert b["rms_db"] == pytest.approx(-10.5, abs=0.1)          # 20 log10(0.3)
    assert out["basic_bleed"] == pytest.approx(0.05)              # the mic under the other voice: not speech
    assert out["basic_again"]["dur_ms"] is None                   # a run is measured once


def test_pauses_are_quiet_runs_of_pause_ms_inside_the_turn(out):
    p = out["pauses"]
    assert p["dur_ms"] == 2800 and p["gap_ms"] == 600
    assert p["pauses"] == 1 and p["longest_pause_ms"] == 1000
    assert p["speak_ms"] == 1800                                  # the 300 ms quiet stays in


def test_a_cut_in_is_a_negative_gap(out):
    assert out["cut"]["gap_ms"] == -500 and out["cut"]["cut_in"] is True and out["cut"]["dur_ms"] == 1000


def test_ticks_far_apart_leave_the_timing_unknown_but_not_the_level(out):
    t = out["throttled"]
    assert out["breaks"] and all(k is None for k in (t["gap_ms"], t["dur_ms"], t["speak_ms"], t["pauses"], t["cut_in"]))
    assert t["rms_db"] == pytest.approx(-10.5, abs=0.1)


def test_nothing_voiced_is_null_never_a_guess(out):
    assert all(v is None for v in out["silent"].values())
    assert all(v is None for v in out["muted"].values())


def test_the_room_level_sets_the_bar(out):
    assert out["floor_level"] == pytest.approx(0.004, rel=0.05)
    assert out["floor"]["dur_ms"] == 400                          # only the real voice


def test_a_sound_under_the_other_voice_is_not_the_answer(out):
    assert out["mm"]["gap_ms"] == 500 and out["mm"]["dur_ms"] == 600 and out["mm"]["cut_in"] is False


def test_only_speech_within_lead_ms_of_the_line(out):
    assert out["lead"]["dur_ms"] == 500 and out["lead"]["gap_ms"] == 4500


def test_the_session_clock(out):
    c = out["clock"]
    assert c["offset"] == 9500 and c["at"] == 1.5 and c["two"] == 2.5 and c["none"] is None


def test_a_turn_over_two_lines_then_a_typed_line_then_a_new_turn(out):
    first, second, typed, after = out["meter"]
    assert first["turn"] == 1 and first["dur_ms"] == 800 and first["gap_ms"] == 600
    assert first["start_s"] == 1.1 and first["end_s"] == 1.9 and first["ago_ms"] == 1300
    assert second["turn"] == 1 and second["dur_ms"] == 2200 and second["gap_ms"] == 600   # cumulative, first line's gap
    assert second["pauses"] == 1 and second["longest_pause_ms"] == 1000 and second["speak_ms"] == 1200
    assert second["end_s"] == 3.3 and second["ago_ms"] == 2900
    assert typed is None
    assert after["turn"] == 2 and after["dur_ms"] == 500 and after["gap_ms"] == 1000


def test_the_turn_rule(out):
    assert out["rule"] == [False, True, False, False, True]


def test_a_sound_under_the_reply_after_the_answer_is_not_the_answer(out):
    b = out["burst"]
    assert b["gap_ms"] == 500 and b["dur_ms"] == 1500 and b["cut_in"] is False


def test_an_mm_in_a_pause_of_the_other_voice_then_the_answer(out):
    m = out["mm_pause"]
    assert m["gap_ms"] == 500 and m["dur_ms"] == 1000 and m["cut_in"] is False


def test_a_cut_in_after_an_mm_is_still_the_cut_in(out):
    c = out["cut_after_mm"]
    assert c["cut_in"] is True and c["dur_ms"] == 800 and c["gap_ms"] == -1400


def test_a_turn_that_goes_on_from_their_own_speech_has_no_gap(out):
    for key, first_dur in (("split", 3400), ("split_pause", 2000)):
        one, two = out[key]
        assert one["turn"] == 1 and one["gap_ms"] == 500 and one["cut_in"] is False and one["dur_ms"] == first_dur
        assert two["turn"] == 2 and two["gap_ms"] is None and two["cut_in"] is None, (key, two)
        assert two["dur_ms"] is not None
    one, two = out["split_long"]                   # quiet of merge_ms or more: a new turn, its gap the other voice's
    assert one["gap_ms"] == 500 and two["turn"] == 2 and two["gap_ms"] == 3600 and two["cut_in"] is False


def test_a_new_session_measures_its_first_turn_on_its_own(out):
    before, first, later = out["rest"]
    assert before["turn"] == 1 and before["gap_ms"] == 600
    assert first["turn"] == 2 and first["gap_ms"] is None and first["cut_in"] is None and first["dur_ms"] == 1500
    assert later["turn"] == 3 and later["gap_ms"] == 500 and later["cut_in"] is False
    assert out["rest_unbounded"][1]["gap_ms"] == 56500     # without the boundary: the whole rest, as before the fix


def test_a_sound_talked_through_before_a_short_answer_is_not_the_answer(out):
    for key, dur in (("yeah", 300), ("echo", 240), ("laugh", 600)):
        u = out["under_before"][key]
        assert u["gap_ms"] == 500 and u["dur_ms"] == dur and u["cut_in"] is False, (key, u)


def test_their_next_words_begun_before_the_answers_transcript_was_done_are_the_next_lines(out):
    for key, (a, b) in (("next_words", ((500, 2000), (300, 3500))), ("next_words_long", ((500, 1500), (300, 2200)))):
        one, two = out[key]
        assert (one["gap_ms"], one["dur_ms"], one["cut_in"]) == (*a, False), (key, one)
        assert two["turn"] == 2 and (two["gap_ms"], two["dur_ms"], two["cut_in"]) == (*b, False), (key, two)


def test_a_sound_under_the_reply_is_its_own_line_or_dropped_by_the_next(out):
    answer, yeah = out["yeah_under_reply"]
    assert answer["gap_ms"] == 500 and answer["dur_ms"] == 1500 and answer["cut_in"] is False
    assert yeah["gap_ms"] == -2000 and yeah["dur_ms"] == 300 and yeah["cut_in"] is True
    answer, no = out["echo_under_reply"]
    assert answer["gap_ms"] == 500 and answer["dur_ms"] == 1500 and answer["cut_in"] is False
    assert no["gap_ms"] == 500 and no["dur_ms"] == 240 and no["cut_in"] is False


def test_a_short_answer_beats_a_longer_sound_no_line_took(out):
    """Regression: take() kept the part with the most voiced time, so a laugh, a cough or words never captioned inside
    lead_ms of a short answer's first words won, and the answer was measured as that sound (its gap null, or a cut-in
    of the laugh over the joke's last word). The part the replay gives the line is the one nearest its speech end."""
    one, two = out["untaken"]["laugh_over"]
    assert (one["gap_ms"], one["dur_ms"]) == (500, 1500)
    assert two["turn"] == 2 and (two["gap_ms"], two["dur_ms"], two["cut_in"]) == (400, 500, False), two
    for key, dur in (("laugh", 400), ("noise", 600)):
        one, two, three = out["untaken"][key]
        assert (one["gap_ms"], one["dur_ms"]) == (500, 1500), (key, one)
        assert two["turn"] == 2 and (two["gap_ms"], two["dur_ms"], two["cut_in"]) == (400, dur, False), (key, two)
        assert (three["gap_ms"], three["dur_ms"]) == (500, 1500), (key, three)


def test_a_backchannel_inside_a_line_leaves_no_stretch_for_the_next(out):
    """Regression: the line kept its longer first stretch and left the second for the next line, which then measured
    that stretch instead of the short "yes" after the other voice's reply."""
    one, two = out["backchannel"]
    assert (one["gap_ms"], one["dur_ms"], one["cut_in"]) == (200, 2400, False), one
    assert two["turn"] == 2 and (two["gap_ms"], two["dur_ms"], two["cut_in"]) == (400, 400, False), two


def test_a_short_answer_then_longer_next_words_are_two_turns(out):
    """Regression: by the short answer's line end their next words had more voiced time, so the answer's line took
    them and the answer was lost; the replay gives each line its own."""
    one, two = out["short_then_long"]["ok"]
    assert (one["gap_ms"], one["dur_ms"], one["cut_in"]) == (500, 500, False), one
    assert two["turn"] == 2 and (two["gap_ms"], two["dur_ms"], two["cut_in"]) == (200, 3400, False), two
    one, two = out["short_then_long"]["barge"]
    assert (one["gap_ms"], one["dur_ms"], one["cut_in"]) == (500, 400, False), one
    assert two["turn"] == 2 and (two["gap_ms"], two["dur_ms"], two["cut_in"]) == (-200, 3800, True), two
    (one,) = out["short_then_long"]["ok_short_lag"]
    assert (one["gap_ms"], one["dur_ms"]) == (200, 1800), one   # their next words, as far as they had gone


def test_a_quick_reconnect_after_a_drop_leaves_the_closed_sessions_speech_out(out):
    (s,) = out["drop_quick"]
    assert s["turn"] == 1 and s["dur_ms"] == 1000 and s["pauses"] == 0
    assert s["gap_ms"] is None and s["cut_in"] is None


def test_a_sound_under_the_reply_is_not_the_answer_when_the_transcript_runs_slow(out):
    """The `under` filter decides here: without it the burst (or "yeah") nearest the late speech end is the line, a
    cut-in of 120 (300) ms, where the replay gives the answer."""
    for key in ("burst", "yeah"):
        got = [(r["gap_ms"], r["dur_ms"], r["cut_in"]) for r in out["slow_under"][key]]
        assert got == [(500, 1500, False), (600, 1000, False), (500, 1500, False)], (key, got)


def test_the_speech_end_counts_the_quiet_before_the_line_is_posted(out):
    """Without line_quiet_ms in the speech end, the "yes" is measured as the uncaptioned words (no gap, 1.9 s); the
    page's own line_quiet_ms, not a fixed 900, places it (the 2.9 s page takes the next words, as the replay would)."""
    (yes,) = out["quiet_end"]["yes"]
    assert (yes["gap_ms"], yes["dur_ms"], yes["cut_in"]) == (300, 300, False), yes
    (one,) = out["quiet_end"]["long_quiet"]
    assert (one["gap_ms"], one["dur_ms"], one["cut_in"]) == (200, 3400, False), one


def test_a_speech_end_between_the_answer_and_the_next_words_goes_to_the_answer(out):
    """Regression (end 4000): the next words' end was cut at the line's last words, which made them look nearer the
    speech end than the replay's turn (it ends where the speech does); the line took them and the answer was lost.
    End 3900 is the tenth's case: without it the two parts tie and the later one wins."""
    for end in ("3900", "4000"):
        got = [(r["turn"], r["gap_ms"], r["dur_ms"], r["cut_in"]) for r in out["between"][end]]
        assert got == [(1, 500, 500, False), (2, 200, 3400, False), (3, 500, 1500, False)], (end, got)


def test_a_tie_goes_to_the_earlier_part(out):
    """Regression: a tie went to the later part, the one whose turn the replay can only see as further off (it ends
    where the speech does), and the replay takes the earlier turn on a tie too."""
    got = [(r["turn"], r["gap_ms"], r["dur_ms"], r["cut_in"]) for r in out["tie"]["going"]]
    assert got == [(1, 500, 500, False), (2, 100, 3200, False), (3, 500, 1500, False)], got
    got = [(r["turn"], r["gap_ms"], r["dur_ms"], r["cut_in"]) for r in out["tie"]["ended"]]
    assert got == [(1, 500, 500, False), (2, 500, 1500, False)], got
