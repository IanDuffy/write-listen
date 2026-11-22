"""signals/measure.py: the replay measurer on synthetic two-channel recordings (package 3)."""
import os
import time

import numpy as np
import pytest

import signals_synth as syn
from signals import measure as M
from signals import store
from stages import tts
from stages.common import StageError

R = M.settings({"live": {"page": {"engaged_merge_ms": 1500}}})
BASE = "2026-11-21T10:15:00"


def turns_of(path, R=R):
    with M.Audio(path) as a:
        L = M.analyse(a, R)
        return M.measure_turns(a, L, R), L


def check_turns(got, want, ms=20):
    assert len(got) == len(want), [(round(t["start_s"], 2), round(t["end_s"], 2)) for t in got]
    for t, (a, b, gap, cut, dur, speak, pauses, longest) in zip(got, want):
        assert t["start_s"] == pytest.approx(a, abs=ms / 1000) and t["end_s"] == pytest.approx(b, abs=ms / 1000)
        assert t["gap_ms"] == pytest.approx(gap, abs=ms) and t["cut_in"] is cut
        assert t["dur_ms"] == pytest.approx(dur, abs=ms) and t["speak_ms"] == pytest.approx(speak, abs=2 * ms)
        assert t["pauses"] == pauses and t["longest_pause_ms"] == pytest.approx(longest, abs=2 * ms)


@pytest.fixture(scope="module")
def session_wav(tmp_path_factory):
    return syn.wav(tmp_path_factory.mktemp("rec") / "s.wav", syn.TOTAL, syn.LISTENER, syn.OTHER)


def test_every_measure_comes_back(session_wav):
    got, L = turns_of(session_wav)
    check_turns(got, syn.TURNS)
    assert L.thr == R["min_rms"] and 15 < L.floor < 25          # the room's level, under the minimum
    for t in got[:5]:
        assert t["rms_db"] == pytest.approx(syn.rms_db(), abs=0.3)
    assert got[5]["rms_db"] == pytest.approx(syn.rms_db() + 6.02, abs=0.3)     # twice the amplitude
    # A flat voice and a lively one: the spread of B's 4-semitone vibrato, A's none
    assert got[0]["pitch_sd_st"] < 0.2
    assert got[1]["pitch_sd_st"] == pytest.approx(syn.B_SD, abs=0.3) and syn.B_SD > 2
    assert got[3]["pitch_sd_st"] is None                        # 0.3 s: too few pitch frames to say


def test_pitch_in_a_higher_voice_and_an_octave_error_dropped():
    P = R["pitch"]
    x = syn.tone(2.0, f0=240.0, st_amp=2.0, st_rate=1.0)
    voiced = np.ones(200, bool)
    sd = M.pitch_sd(x, voiced, 240, syn.RATE, P)
    assert sd == pytest.approx(syn.expected_sd([(0, 2.0)], 2.0, st_rate=1.0), abs=0.25)
    flat = syn.tone(2.0, f0=110.0)
    jump = flat.copy()
    jump[9600:11520] = syn.tone(0.08, f0=220.0)                 # two pitch frames an octave up: dropped
    assert M.pitch_sd(jump, voiced, 240, syn.RATE, P) < 0.2
    assert M.pitch_sd(np.random.default_rng(3).normal(0, 2000, 48000), voiced, 240, syn.RATE, P) is None   # noise


def test_the_other_voice_bleeding_into_the_mic_is_not_the_listener(tmp_path):
    p = syn.wav(tmp_path / "bleed.wav", syn.TOTAL, syn.LISTENER, syn.OTHER, bleed=0.15)
    got, L = turns_of(p)
    check_turns(got, syn.TURNS)
    assert L.bleed > 300                                        # the guide is heard in their channel
    loose = {**R, "bleed_x": 0, "bleed_tail_ms": 0}
    first = turns_of(p, loose)[0][0]                            # without the rule, the guide's echo is a turn
    assert first["start_s"] == pytest.approx(0.52) and first["cut_in"] is True


def test_a_muted_mic_is_not_the_room(tmp_path):
    p = syn.wav(tmp_path / "muted.wav", syn.TOTAL, syn.LISTENER, syn.OTHER, noise=80.0, muted=[(0, 3.7), (31, 32)])
    got, L = turns_of(p)
    assert 70 < L.floor < 90 and L.thr == pytest.approx(4 * L.floor)
    check_turns(got, syn.TURNS)


def test_the_other_voice_between_two_runs_splits_them(tmp_path):
    lis = [(1.0, 2.0), (3.0, 4.0)]
    one, _ = turns_of(syn.wav(tmp_path / "a.wav", 6, lis, [(0.2, 0.8)]))
    two, _ = turns_of(syn.wav(tmp_path / "b.wav", 6, lis, [(0.2, 0.8), (2.3, 2.6)]))
    assert [(t["start_s"], t["end_s"]) for t in one] == [pytest.approx((1.0, 4.0))]
    assert one[0]["pauses"] == 1 and one[0]["longest_pause_ms"] == pytest.approx(1000)
    assert [round(t["start_s"], 2) for t in two] == [1.0, 3.0]
    assert two[1]["gap_ms"] == pytest.approx(400)


def test_a_two_hour_file_is_read_in_windows(tmp_path, monkeypatch):
    p = syn.wav(tmp_path / "s.wav", syn.TOTAL, syn.LISTENER, syn.OTHER)
    monkeypatch.setattr(M, "BLOCK_S", 7)                       # several windows, one ending mid-turn
    got, _ = turns_of(p)
    check_turns(got, syn.TURNS)


def test_an_mp3_is_decoded_first(tmp_path, session_wav):
    mp3 = tmp_path / "s.mp3"
    tts.ffmpeg(["-i", str(session_wav), "-c:a", "libmp3lame", "-q:a", "4", str(mp3)])
    got, _ = turns_of(mp3)
    check_turns(got, syn.TURNS, ms=30)
    assert got[1]["pitch_sd_st"] == pytest.approx(syn.B_SD, abs=0.4)
    with pytest.raises(StageError):
        M.Audio(tmp_path / "missing.mp3")


def test_a_one_channel_file_is_refused(tmp_path):
    import wave
    p = tmp_path / "mono.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * 2400)
    with pytest.raises(StageError, match="one channel"):
        M.Audio(p)


# --- the transcript's rows to the turns --------------------------------------------------------------------

SPANS = [{"start_s": a, "end_s": b} for a, b, *_ in syn.TURNS]


def rows(pairs):
    return [{"at": float(at), "text": t} for at, t, *_ in pairs]


def test_rows_go_to_turns_by_order_and_time():
    got = M.assign(SPANS, rows(syn.rows_for()), R)
    assert got == [0, 1, 1, 2, 4, 5]                            # B's two pieces to B; D (3) has no row


def test_a_late_transcript_and_a_shifted_clock_are_found():
    # every row 3.5 s later than lag_s says (the session row and the recording's start apart, the lag longer)
    got = M.assign(SPANS, rows(syn.rows_for(t0_secs=3.5)), R)
    assert got == [0, 1, 1, 2, 4, 5]
    assert M.assign(SPANS, rows(syn.rows_for(t0_secs=-2.0)), R) == [0, 1, 1, 2, 4, 5]   # or earlier


def test_a_row_with_no_sound_is_left_out_and_order_holds():
    pairs = syn.rows_for()
    pairs.insert(3, (60, "A line long after everything.", True))    # out of order and far from any turn
    got = M.assign(SPANS, rows(pairs), R)
    assert got[3] is None and [k for k in got if k is not None] == [0, 1, 1, 2, 4, 5]
    assert M.assign([], rows(pairs), R) == [None] * len(pairs)


def test_typed_lines_and_other_sessions_rows_are_not_spoken_rows():
    session = {"live_id": "a", "t": BASE, "s": None, "rows": [
        {"t": syn.iso(BASE, 5), "who": "listener", "text": "Hi there."},
        {"t": syn.iso(BASE, 9), "who": "listener", "text": "typed words"},
        {"t": syn.iso(BASE, 9), "who": "director", "id": 1},
        {"t": syn.iso(BASE, 9), "who": "page", "text": '[The listener typed this instead of speaking: "typed words"]'},
        {"t": syn.iso(BASE, 12), "who": "guide", "text": "Mm."}]}
    assert M.spoken_rows(session) == [{"text": "Hi there.", "at": 5.0}]
    study = {"live_id": "b", "t": BASE, "s": 0.0, "rows": [{"t": BASE, "s": 7.25, "who": "listener", "text": "Yes"}]}
    assert M.spoken_rows(study) == [{"text": "Yes", "at": 7.25}]


# --- a listener's conversations -------------------------------------------------------------------------------

def listener_with_visit(root, **kw):
    return syn.visit_files(root / "t-pooja", root, **kw)


def test_run_writes_a_listeners_records(tmp_path):
    lst = listener_with_visit(tmp_path)
    # the page measured one of the guide's turns live: the recording's record replaces it
    store.append(lst, "20261121-101500", {"v": 1, "stem": "20261121-101500", "part": "guide", "source": "page",
                                          "turn": 0, "live_id": "live_g1", "t": "2026-11-21T10:15:03.900",
                                          "start_s": 3.9, "end_s": 5.1, "text": "I went to the park today."})
    got = M.run(lst, {})
    by = {c["stem"]: c for c in got["conversations"]}
    assert set(by) == {"intro-20261121-101000", "20261121-101500"}            # not the library's
    g = by["20261121-101500"]
    assert g["recordings"] == 2 and g["turns"] == 6 and g["untranscribed"] == 1 + 5 and g["unheard"] == 0
    assert by["intro-20261121-101000"]["turns"] == 2
    recs = store.records(lst, "20261121-101500")
    assert [r["source"] for r in recs] == ["recording"] * 6                    # the page's turn 0 replaced
    assert [r["turn"] for r in recs] == list(range(6))
    assert [r["live_id"] for r in recs] == ["live_g1"] * 5 + ["live_g2"]
    assert recs[0]["replaces"] == [0] and recs[0]["t"] == "2026-11-21T10:15:03.800"
    assert recs[5]["t"] == "2026-11-21T10:17:03.800" and recs[5]["start_s"] == pytest.approx(3.8)
    assert recs[1]["text"] == "[inhale]Yeah, it was good, kind of quiet." and recs[1]["tags"] == ["inhale"]
    assert recs[1]["hedges"] == 1 and recs[1]["pauses"] == 1
    assert recs[1]["pitch_sd_st"] == pytest.approx(syn.B_SD, abs=0.3)
    assert recs[2]["cut_in"] is True and recs[2]["gap_ms"] == pytest.approx(-600, abs=20)
    assert recs[3]["text"] == "Um, I don't know." and recs[3]["fillers"] == 1 and recs[3]["trailed"] is True
    # the norm carries across the visit: two intro turns of 3+ words, then the guide's first, so the guide's
    # second turn is the first with a norm, and the intro counted toward it
    intro = store.records(lst, "intro-20261121-101000")
    assert [r["part"] for r in intro] == ["intro", "intro"] and all(r["norm"] is None for r in intro)
    assert recs[0]["norm"] is None and recs[0]["norm_turns"] == 2
    assert recs[1]["norm"] is not None and recs[1]["norm_turns"] == 3
    assert not (lst / "signals" / "20261121-120000.jsonl").exists()
    # a second run gives the same records (it replaces its own, keeps the page's line)
    M.run(lst, {})
    assert store.records(lst, "20261121-101500") == recs
    assert sum(1 for r in store.rows(lst, "20261121-101500") if r["source"] == "page") == 1


def test_a_conversation_that_may_still_be_live_waits(tmp_path):
    lst = listener_with_visit(tmp_path, intro_mp3=False)
    p = lst / "sessions" / "20261121-101500.jsonl"
    os.utime(p, (time.time() - 60, time.time() - 60))
    got = M.run(lst, {})
    by = {c["stem"]: c for c in got["conversations"]}
    assert "5 minutes" in by["20261121-101500"]["skipped"]
    assert by["intro-20261121-101000"]["no_audio"] == ["live_i"] and by["intro-20261121-101000"]["turns"] == 0
    assert not store.path(lst, "20261121-101500").exists()
    got = M.run(lst, {}, force=True)
    assert len(store.records(lst, "20261121-101500")) == 6


def test_records_kept_when_the_audio_is_gone(tmp_path):
    lst = listener_with_visit(tmp_path)
    M.run(lst, {})
    before = store.records(lst, "20261121-101500")
    store.audio_path(lst, "live_g2").unlink()
    got = M.run(lst, {})
    g = next(c for c in got["conversations"] if c["stem"] == "20261121-101500")
    assert g["no_audio"] == ["live_g2"] and g["turns"] == 5
    assert store.records(lst, "20261121-101500") == before          # live_g2's turn kept from the first run
    store.audio_path(lst, "live_g1").unlink()
    M.run(lst, {})
    assert store.records(lst, "20261121-101500") == before
