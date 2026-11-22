"""signals/store.py and signals/live.py: the signals/ folder, the merged records, the page's records."""
from datetime import datetime

import pytest

from signals import live, record, store

STEM = "20261121-101500"


def page_sig(turn, **kw):
    return {"turn": turn, "gap_ms": 700, "dur_ms": 3000, "speak_ms": 2600, "pauses": 0, "longest_pause_ms": 0,
            "rms_db": -30.0, "ago_ms": 3500, "start_s": 10.0 + turn * 10, "end_s": 13.0 + turn * 10, **kw}


def at(sec):
    return datetime(2026, 11, 21, 10, 15, 0) .replace(second=sec)


def test_paths_and_parts(tmp_path):
    assert store.path(tmp_path, STEM) == tmp_path / "signals" / f"{STEM}.jsonl"
    assert store.marks_path(tmp_path, "debrief-20261121-103000").name == "debrief-20261121-103000.marks.jsonl"
    assert store.part_of("intro-20261121-101000") == "intro"
    assert store.part_of(STEM) == "guide"
    for bad in ("log", "../x", "20261121", "intro-..", ""):
        with pytest.raises(ValueError):
            store.path(tmp_path, bad)
    with pytest.raises(ValueError):
        store.audio_path(tmp_path, "../../etc/passwd")
    assert store.audio_path(tmp_path, "sess_abc-1").name == "sess_abc-1.wav"


def test_records_stay_out_of_sessions_and_study(tmp_path):
    live.record_line(tmp_path, STEM, part="guide", live_id="s1", text="Hello there.", sig=page_sig(0),
                     cfg={}, now=at(5))
    assert not (tmp_path / "sessions").exists() and not (tmp_path / "study").exists()
    assert store.stems(tmp_path) == [STEM]


def test_a_turns_later_lines_join_and_win(tmp_path):
    live.record_line(tmp_path, STEM, part="guide", live_id="s1", text="[inhale", sig=page_sig(0, dur_ms=800),
                     cfg={}, now=at(5))
    live.record_line(tmp_path, STEM, part="guide", live_id="s1", text="]Yeah, it was good.",
                     sig=page_sig(0, dur_ms=2500), cfg={}, now=at(7))
    lines = store.rows(tmp_path, STEM)
    assert len(lines) == 2
    got = store.records(tmp_path, STEM)
    assert len(got) == 1
    r = got[0]
    assert r["text"] == "[inhale]Yeah, it was good." and r["words"] == 4 and r["tags"] == ["inhale"]
    assert r["dur_ms"] == 2500
    assert r["t"] == "2026-11-21T10:15:01.500"                  # the turn's start, from its first line
    assert r["source"] == "page" and r["part"] == "guide" and r["live_id"] == "s1"


def test_the_norm_carries_across_the_visit(tmp_path):
    ten = " ".join(["word"] * 10) + "."
    live.record_line(tmp_path, "intro-20261121-101000", part="intro", live_id="i1", text=ten, sig=page_sig(0),
                     cfg={}, now=at(1))
    for k in range(2):
        live.record_line(tmp_path, STEM, part="guide", live_id="s1", text=ten, sig=page_sig(k), cfg={},
                         now=at(10 + k * 10))
    r = live.record_line(tmp_path, "debrief-20261121-103000", part="debrief", live_id="d1", text="Fine.",
                         sig=page_sig(0, speak_ms=260), cfg={}, now=at(50))
    assert r["norm"] is not None and r["norm"]["words"] == 10 and r["norm_turns"] == 3
    assert r["mention"] == ["words"]
    assert [x["part"] for x in store.visit(tmp_path)] == ["intro", "guide", "guide", "debrief"]


def test_bad_sig_records_nothing(tmp_path):
    for sig in (None, {}, {"turn": "1"}, {"turn": True}, {"turn": -1}):
        assert live.record_line(tmp_path, STEM, part="guide", live_id=None, text="Hi.", sig=sig, cfg={}) is None
    assert live.record_line(tmp_path, STEM, part="guide", live_id=None, text="  ", sig=page_sig(0), cfg={}) is None
    assert live.record_line(tmp_path, "log", part="guide", live_id=None, text="Hi.", sig=page_sig(0), cfg={}) is None
    r = live.record_line(tmp_path, STEM, part="guide", live_id=None, text="Hi there you.",
                         sig=page_sig(0, gap_ms="x", rms_db=99, cut_in="yes"), cfg={}, now=at(3))
    assert r["gap_ms"] is None and r["rms_db"] is None and r["cut_in"] is None


def test_recording_records_replace_the_page_turns_they_cover(tmp_path):
    for k in range(3):
        live.record_line(tmp_path, STEM, part="guide", live_id="s1", text=f"Turn {k} words here.",
                         sig=page_sig(k), cfg={}, now=at(10 + k * 10))
    S = record.settings({})
    rec = record.build({"start_s": 20.4, "end_s": 23.1, "gap_ms": 650}, "Turn 1 words here.", stem=STEM,
                       part="guide", source="recording", turn=0, live_id="s1", t="2026-11-21T10:15:20.400", S=S)
    matched = store.match_page([rec], [r for r in store.records(tmp_path, STEM)])
    assert matched[0]["replaces"] == [1]
    store.write_recordings(tmp_path, STEM, matched)
    got = store.records(tmp_path, STEM)
    assert [(r["source"], r["turn"]) for r in got] == [("page", 0), ("recording", 0), ("page", 2)]
    store.write_recordings(tmp_path, STEM, matched)            # a second replay replaces, never doubles
    assert len(store.rows(tmp_path, STEM)) == 4


def test_match_by_words_when_the_page_had_no_session_clock(tmp_path):
    page = [{"source": "page", "turn": 0, "live_id": "s1", "start_s": None, "end_s": None,
             "text": "Yeah, it was good"}]
    r = {"source": "recording", "turn": 0, "live_id": "s1", "start_s": 5.0, "end_s": 7.0,
         "text": "[inhale]Yeah, it was good."}
    assert store.match_page([r], page)[0]["replaces"] == [0]
    other = {**r, "live_id": "s2"}
    assert store.match_page([other], page)[0]["replaces"] == []


def test_wait_turn_waits_for_the_line_to_land(tmp_path):
    clock = {"t": 0.0}

    def sleep(s):
        clock["t"] += s
        if clock["t"] >= 0.2 and not store.rows(tmp_path, STEM):
            live.record_line(tmp_path, STEM, part="guide", live_id="s1", text="Here now.", sig=page_sig(4),
                             cfg={}, now=at(9))

    got = store.wait_turn(tmp_path, STEM, 4, 1.0, sleep=sleep, clock=lambda: clock["t"])
    assert got["turn"] == 4 and clock["t"] < 0.5
    assert store.wait_turn(tmp_path, STEM, 5, 0.3, sleep=sleep, clock=lambda: clock["t"])["turn"] == 4


def test_marks(tmp_path):
    m = store.add_mark(tmp_path, STEM, 64.25, "  lost them   here ", t="2026-11-21T12:00:00")
    assert m == {"t": "2026-11-21T12:00:00", "secs": 64.25, "text": "lost them here"}
    assert store.marks(tmp_path, STEM) == [m]
    assert store.stems(tmp_path) == []                           # a marks file is not a conversation
    for bad in ("", "   "):
        with pytest.raises(ValueError):
            store.add_mark(tmp_path, STEM, 1, bad)
    with pytest.raises(ValueError):
        store.add_mark(tmp_path, STEM, float("nan"), "x")


def test_a_reset_listener_folder_is_never_recreated(tmp_path):
    gone = tmp_path / "t-reset"
    with pytest.raises(FileNotFoundError):                       # the line routes catch it and carry on
        live.record_line(gone, STEM, part="guide", live_id="s1", text="Hello there you.", sig=page_sig(0),
                         cfg={}, now=at(5))
    with pytest.raises(FileNotFoundError):
        store.append(gone, STEM, {"source": "page", "turn": 0})
    with pytest.raises(FileNotFoundError):
        store.write_recordings(gone, STEM, [])
    with pytest.raises(FileNotFoundError):
        store.add_mark(gone, STEM, 1.0, "x")
    assert not gone.exists()


def test_stems_sort_by_when_they_began_whatever_the_prefix(tmp_path):
    for stem in ("debrief-20261121-103000", "20261121-101500", "intro-20261121-101000"):
        store.append(tmp_path, stem, {"source": "page", "turn": 0})
    assert store.stems(tmp_path) == ["intro-20261121-101000", "20261121-101500", "debrief-20261121-103000"]


def test_the_norm_reads_only_the_newest_conversations_it_needs(tmp_path, monkeypatch):
    ten = " ".join(["word"] * 10) + "."
    for k, stem in enumerate(["20261101-100000", "20261102-100000", "20261103-100000", "20261104-100000"]):
        for j in range(3):
            live.record_line(tmp_path, stem, part="guide", live_id=f"s{k}", text=ten, sig=page_sig(j), cfg={},
                             now=datetime(2026, 11, 1 + k, 10, 0, 10 + j * 10))
    read = []
    real = store.records
    monkeypatch.setattr(store, "records", lambda listener, stem: read.append(stem) or real(listener, stem))
    got = store.recent(tmp_path, "page", 4, 3)
    assert read == ["20261104-100000", "20261103-100000", "20261102-100000"]     # 3 + 3 >= 4, then one more
    assert [r["live_id"] for r in got] == ["s1"] * 3 + ["s2"] * 3 + ["s3"] * 3
    cfg = {"signals": {"norm_turns": 4}}
    r = live.record_line(tmp_path, "20261105-100000", part="guide", live_id="s4", text="Fine thanks then.",
                         sig=page_sig(0), cfg=cfg, now=datetime(2026, 11, 5, 10, 0, 10))
    assert r["norm_turns"] == 4 and r["norm"]["words"] == 10
