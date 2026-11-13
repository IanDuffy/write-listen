"""The page's local listener (page/wake.js), which brings the guide back after it has stepped back.
Its decision is plain JavaScript with no browser in it, run here under Node on synthetic sound."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WAKE_JS = Path(__file__).resolve().parent.parent / "page" / "wake.js"

HARNESS = r"""
const { WakeDetector, Activity } = require(process.argv[2]);
const RATE = 48000;
let seed = 7;
const rand = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 1073741824 - 1; };
// Sound builders: seconds of samples at RATE.
const room = (secs, level = 0.001) => Float32Array.from({ length: secs * RATE }, () => rand() * level);
const tone = (secs, hz, amp) => Float32Array.from({ length: secs * RATE }, (_, i) => amp * Math.sin(2 * Math.PI * hz * i / RATE));
// Voice-like: harmonics of a 140 Hz pitch with four syllables a second.
const voice = (secs, amp = 0.08) => Float32Array.from({ length: secs * RATE }, (_, i) => {
  const t = i / RATE, syll = 0.55 + 0.45 * Math.sin(2 * Math.PI * 4 * t);
  return amp * syll * (Math.sin(2 * Math.PI * 140 * t) + 0.8 * Math.sin(2 * Math.PI * 420 * t)
                       + 0.6 * Math.sin(2 * Math.PI * 1120 * t) + 0.3 * Math.sin(2 * Math.PI * 2400 * t)) / 2;
});
const mix = (a, b) => a.map((x, i) => x + (b[i] || 0));
const join = (...parts) => { const out = new Float32Array(parts.reduce((n, p) => n + p.length, 0)); let o = 0;
                             for (const p of parts) { out.set(p, o); o += p.length; } return out; };
// Feed in 128-sample blocks, as an AudioWorklet does; the time (s) of the wake, or null.
function wokeAt(samples, options) {
  const d = new WakeDetector(RATE, options);
  for (let i = 0; i < samples.length; i += 128) {
    if (d.push(samples.subarray(i, i + 128))) return +(i / RATE).toFixed(2);
  }
  return null;
}
const out = {};
out.quiet_room = wokeAt(room(6));
out.voice_after_quiet = wokeAt(join(room(3), voice(2)));
out.quiet_voice = wokeAt(join(room(3), voice(2, 0.02)));
const click = room(4); for (let i = 2 * RATE; i < 2 * RATE + 0.03 * RATE; i++) click[i] = 0.7 * (i % 2 ? 1 : -1);
out.click = wokeAt(click);
const clap = room(4); for (let i = 2 * RATE; i < 2 * RATE + 0.12 * RATE; i++) clap[i] = 0.5 * rand();
out.clap = wokeAt(clap);
out.mains_hum = wokeAt(join(room(2), tone(3, 60, 0.1)));
out.rumble = wokeAt(join(room(2), tone(3, 40, 0.3)));
out.mic_opening = wokeAt(join(voice(0.4, 0.3), room(4)));
out.noisy_room = wokeAt(room(8, 0.03));
out.voice_in_noisy_room = wokeAt(mix(room(8, 0.03), join(room(5, 0), voice(3, 0.4))));
out.fan_starts = wokeAt(join(room(2), room(20, 0.05)));
// A capture that begins with exact zeros (20 ms, or the whole warm-up) in a steadily noisy room.
out.zeros_then_noise = wokeAt(join(new Float32Array(0.02 * RATE), room(8, 0.03)));
out.long_zeros_then_noise = wokeAt(join(new Float32Array(0.8 * RATE), room(8, 0.03)));
out.zeros_then_voice = wokeAt(join(new Float32Array(1 * RATE), room(2), voice(2)));
// A second speaker's words after the fan has become the room.
const d = new WakeDetector(RATE);
const fan = join(room(2), room(20, 0.05), mix(room(2, 0.05), voice(2, 0.5)));
let wakes = [];
for (let i = 0; i < fan.length; i += 128) if (d.push(fan.subarray(i, i + 128))) wakes.push(+(i / RATE).toFixed(2));
out.fan_wakes = wakes; out.fan_floor = d.floor;
// The decision on its own: loud frames must add up within the window.
const frames = (pattern) => { const f = new WakeDetector(RATE); let woke = null;
  pattern.forEach((rms, i) => { if (f.frame(rms) && woke === null) woke = i; }); return woke; };
const quiet = Array(50).fill(0.001);
out.scattered = frames([...quiet, ...Array.from({ length: 200 }, (_, i) => (i % 10 === 0 ? 0.1 : 0.001))]);
out.sustained = frames([...quiet, ...Array(20).fill(0.1)]);
out.once = (() => { const f = new WakeDetector(RATE); let n = 0;
  [...quiet, ...Array(200).fill(0.1)].forEach((r) => { if (f.frame(r)) n++; }); return n; })();
const a = new Activity();
out.activity_room = Array.from({ length: 200 }, () => a.heard(0.31 + 0.01 * rand())).filter(Boolean).length;
out.activity_speech = a.heard(0.5);
out.activity_bad = a.heard(undefined);
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def heard(tmp_path_factory):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed")
    script = tmp_path_factory.mktemp("wake") / "harness.js"
    script.write_text(HARNESS)
    res = subprocess.run([node, str(script), str(WAKE_JS)], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_a_voice_brings_the_guide_back_quickly(heard):
    assert heard["quiet_room"] is None
    assert 3.2 <= heard["voice_after_quiet"] <= 3.7  # within about half a second of the first word
    assert heard["quiet_voice"] is not None  # someone speaking softly across the room


def test_short_and_low_sounds_do_not(heard):
    assert heard["click"] is None and heard["clap"] is None  # too short
    assert heard["mains_hum"] is None and heard["rumble"] is None  # below the voice
    assert heard["mic_opening"] is None  # the first half second is never a wake


def test_a_noisy_room_is_the_room(heard):
    assert heard["noisy_room"] is None  # steady from the start: that is the floor
    assert heard["voice_in_noisy_room"] is not None and heard["voice_in_noisy_room"] >= 5
    # A fan starting is a sustained loud sound: it wakes the guide once (accepted), then becomes the room.
    assert heard["fan_wakes"] and heard["fan_wakes"][0] < 3 and len(heard["fan_wakes"]) == 1
    assert heard["fan_floor"] > 0.005


def test_digital_silence_is_not_the_room(heard):
    # A floor learned from zeros would make any steady noise a wake, every time the guide rests.
    assert heard["zeros_then_noise"] is None and heard["long_zeros_then_noise"] is None
    assert heard["zeros_then_voice"] is not None


def test_loud_frames_must_add_up_within_the_window(heard):
    assert heard["scattered"] is None  # one loud frame in ten never adds up
    assert heard["sustained"] == 50 + 12 - 1  # the twelfth loud 20 ms frame: 240 ms
    assert heard["once"] == 1  # one wake per listening spell: the page starts a new detector each rest


def test_activity_during_a_conversation(heard):
    assert heard["activity_room"] == 0  # the room's own level is not speech
    assert heard["activity_speech"] is True and heard["activity_bad"] is False
