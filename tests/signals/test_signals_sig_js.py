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
