"""Overnight recording with GPT-Live (overnight/record.py; spec checks 6 and 8)."""

import json
import threading

import numpy as np
import pytest

from overnight import record
from overnight_record_fakes import (FakeLive, FakeServer, Killed, make_run, no_threads_left,  # noqa: F401
                                    offline, silence, tone)
from stages.common import StageError


def texts_by_id(r):
    return {p["id"]: p["text"] for p in record.paragraphs(r)}


def logged(r):
    return [x for x in r.read_log() if x.get("stage") == "record"]


# --- paragraphs ------------------------------------------------------------------------------------

def test_paragraphs_are_the_spoken_script_with_their_pauses(tmp_path):
    r = make_run(tmp_path)
    ps = record.paragraphs(r)
    assert [p["id"] for p in ps] == ["s1-p01", "s1-p02", "s2-p01", "s2-p02", "s2-p03", "s3-p01", "s3-p02",
                                     "s4-p01", "s4-p02"]
    assert [p["pause"] for p in ps] == ["start", "paragraph", "section", "beat", "paragraph", "section",
                                        "paragraph", "section", "paragraph"]
    for p in ps:
        assert "[" not in p["text"] and "*" not in p["text"], p
    assert ps[2]["text"] == ("Herons have been studied for centuries. The Natural History of Pliny describes them, "
                             "Though not kindly.")


def test_a_long_paragraph_is_cut_at_sentences_to_fit_one_append(tmp_path):
    long = " ".join(f"Sentence number {i} is here to make the paragraph long enough." for i in range(40))
    r = make_run(tmp_path, f"<!-- seg01 -->\n\n{long}\n\nShort one.\n")
    ps = record.paragraphs(r)
    assert len(ps) > 2 and all(len(p["text"]) <= record.PARA_MAX_CHARS for p in ps)
    assert " ".join(p["text"] for p in ps[:-1]) == long
    assert [p["pause"] for p in ps][:2] == ["start", "paragraph"]


def test_a_bracket_the_voice_would_say_stops_before_recording(tmp_path):
    r = make_run(tmp_path, "<!-- seg01 -->\n\nA line with [laughs] in it.\n")
    with pytest.raises(StageError, match="bracketed"):
        record.paragraphs(r)


def test_the_instructions_carry_the_pronunciation_table(cfg):
    text = record.instructions(cfg)
    assert '- Diotima: say it like "Dee-oh-TEE-mah"' in text
    assert "{{" not in text and "Read contractions as written" in text


# --- the check -------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,heard", [
    ("The colour of honour, in the neighbour's garden.", "The color of honor, in the neighbor's garden."),
    ("They realised the organisation was in defence.", "They realized the organization was in defense."),
    ("He travelled to the centre of the theatre.", "He traveled to the center of the theater."),
    ("A dialogue in grey.", "A dialog in gray."),
    ("Vlastos's reading", "vlastos s reading"),
    ("In nineteen ninety-eight, re-listening.", "In 1998, relistening."),
    ("It cost 50% more, from 1914–1918.", "It cost 50 percent more, from 1914 to 1918."),
    ("“Wait,” she said — and he didn’t.", "Wait, she said and he didn't."),
    ("Their check in was late.", "Their checkin was late."),
])
def test_transcript_spellings_are_not_differences(text, heard):
    d = record.deviation(text, heard)
    assert d["diffs"] == [] and d["critical"] == 0, d


def test_a_dropped_word_counts_and_a_dropped_not_fails():
    text = "The heron does not move until the very last moment."
    one = record.deviation(text, "The heron does not move until the last moment.")
    assert (one["missing"], one["critical"]) == (1, 0) and record.passes(one, 2)
    neg = record.deviation(text, "The heron does move until the very last moment.")
    assert neg["missing"] == 1 and neg["critical"] == 1 and not record.passes(neg, 2)
    three = record.deviation(text, "The heron does not move until moment.")
    assert three["missing"] == 3 and not record.passes(three, 2)
    added = record.deviation(text, "Sure. The heron does not move until the very last moment. Shall I go on?")
    assert added["added"] == 5 and not record.passes(added, 2)
    num = record.deviation("It waited for three hours.", "It waited for two hours.")
    assert num["critical"] == 1 and not record.passes(num, 2)


def test_a_contraction_is_a_change_but_not_a_critical_one():
    d = record.deviation("They do not know.", "They don't know.")
    assert d["changed"] == 2 and d["critical"] == 0 and record.passes(d, 2)


# --- following and cutting -----------------------------------------------------------------------

def test_follow_joins_split_words_and_ignores_what_came_before():
    f = record.Follow("Listening is a strategy with a cost.")
    for delta, ms in [(" Right.", 0), (" List", 600), ("ening", 800), (" is a", 1000), (" strat", 1200),
                      ("egy with", 1400), (" a cost.", 1600)]:
        f.push(delta, ms)
    assert f.done  # "cost." is still a partial word (no space follows the last word), and counts
    f.flush()
    assert f.started and f.start_ms == 600 and f.done
    assert " ".join(f.heard) == "Listening is a strategy with a cost."


def test_follow_is_not_done_when_a_late_word_is_found_early():
    f = record.Follow("one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
                      "patience")
    for w in "one two three patience".split():
        f.push(f" {w}", 0)
    f.flush()
    assert f.pos == 15 and not f.done  # matched far ahead, but most of the words were never heard


def test_trim_keeps_the_speech_with_fixed_pads_and_leaves_out_what_came_before():
    rate = record.RATE
    buf = np.concatenate([silence(300), tone(200), silence(500), tone(1000), silence(800)])
    a, b = record.trim(buf, hint=int(1.0 * rate))  # the passage starts at 1.0 s; "Right." ended at 0.5 s
    assert abs(a / rate - (1.0 - record.PAD_LEAD)) < 0.011
    assert abs(b / rate - (2.0 + record.PAD_TAIL)) < 0.011
    assert record.trim(silence(500), 0) is None


# --- the stage, with a fake session ---------------------------------------------------------------

def test_every_paragraph_is_recorded_misreads_retaken_and_a_bad_one_flagged(tmp_path, cfg):
    r = make_run(tmp_path)
    t = texts_by_id(r)
    live = FakeLive({t["s2-p01"]: ["misread", "ok"], t["s3-p01"]: ["misread"] * 9, t["s1-p02"]: ["slip"]})
    out = record.run(r, cfg, session_factory=live)

    audio = r.root / "10_audio"
    for pid in t:
        assert (audio / "paragraphs" / f"{pid}.wav").exists() and (audio / "paragraphs" / f"{pid}.json").exists()
    reads = live.texts()
    assert reads.count(t["s2-p01"]) == 2 and reads.count(t["s3-p01"]) == 4 and reads.count(t["s1-p02"]) == 1
    assert out["paragraphs"] == 9 and out["passed"] == 8 and out["retakes_total"] == 1 + 3
    assert [f["id"] for f in out["flagged"]] == ["s3-p01"] and out["flagged"][0]["diffs"]
    chosen = json.loads((audio / "paragraphs" / "s2-p01.json").read_text())
    assert chosen["passed"] and chosen["take"] == 2 and chosen["takes"] == 2 and chosen["pause"] == "section"
    slip = json.loads((audio / "paragraphs" / "s1-p02.json").read_text())
    assert slip["passed"] and slip["deviation"]["missing"] == 1
    lines = [json.loads(x) for x in (audio / "recording.jsonl").read_text().splitlines()]
    assert len(lines) == 9 + 4 and {x["id"] for x in lines} == set(t)
    assert sorted(p.name for p in (audio / "takes").glob("s3-p01.t*.wav")) == [f"s3-p01.t{k}.wav" for k in range(1, 5)]
    assert json.loads((audio / "recorded.json").read_text()) == out

    costs = logged(r)
    assert costs and all(x["provider"] == "openai" and x["model"] == "gpt-live-1" for x in costs)
    total_s = sum(s.seconds for s in live.sessions)
    assert out["seconds"] == pytest.approx(total_s, abs=0.2)
    assert out["cost_usd"] == pytest.approx(total_s / 60 * 0.05, abs=0.001)
    assert r.spent() == pytest.approx(out["cost_usd"], abs=0.001)
    assert out["sessions"] == len(live.sessions) and live.closed == len(live.sessions)
    assert sum(1 for x in costs if x.get("closed")) == len(live.sessions)

    n = len(live.reads)
    assert record.run(r, cfg, session_factory=live) == out and len(live.reads) == n  # done: nothing again


def test_up_to_four_sessions_record_contiguous_sections_at_once(tmp_path, cfg):
    r = make_run(tmp_path)
    barrier = threading.Barrier(4, timeout=5)
    first = set()

    def together(session, text):
        if session.id not in first:  # every session's first read waits for the other three
            first.add(session.id)
            barrier.wait()
    live = FakeLive(before_read=together)
    out = record.run(r, cfg, session_factory=live)
    assert len(live.sessions) == 4 and out["sessions"] == 4
    by_session = {}
    for sid, _, text, _ in live.reads:
        by_session.setdefault(sid, []).append(text)
    segs = {p["text"]: p["seg"] for p in record.paragraphs(r)}
    owned = [sorted({segs[x] for x in v}) for v in by_session.values()]
    assert sorted(s for o in owned for s in o) == [1, 2, 3, 4]  # each section in exactly one session
    for o in owned:
        assert o == list(range(o[0], o[-1] + 1))


def test_groups_are_contiguous_whole_sections():
    ps = [{"id": f"s{s}-p{k}", "seg": s, "text": "x" * (100 if s != 2 else 400)} for s in range(1, 7) for k in (1, 2)]
    groups = record._groups(ps, 4)
    assert len(groups) == 4 and [p for g in groups for p in g] == ps
    for g in groups:
        segs = [p["seg"] for p in g]
        assert segs == sorted(segs)
    assert len(record._groups(ps[:2], 4)) == 1


def test_the_runaway_stop_ends_recording_then_resume_finishes_it(tmp_path, cfg):
    r = make_run(tmp_path)
    r.max_usd = 0.012  # about two paragraphs of connected time
    cfg["overnight"]["record"]["sessions"] = 1
    live = FakeLive()
    with pytest.raises(StageError, match="runaway stop"):
        record.run(r, cfg, session_factory=live)
    done = sorted(p.name for p in (r.root / "10_audio" / "paragraphs").glob("*.wav"))
    assert 0 < len(done) < 9 and not (r.root / "10_audio" / "recorded.json").exists()
    assert r.spent() >= 0.012 and live.closed == len(live.sessions)  # the session was closed and paid for

    r.max_usd = 50
    again = FakeLive()
    out = record.run(r, cfg, session_factory=again)
    assert out["paragraphs"] == 9 and len(again.reads) == 9 - len(done)


def test_a_kill_mid_recording_resumes_without_redoing_finished_paragraphs(tmp_path, cfg):
    """Acceptance check 8, the recording half: the process dies during a paragraph; the next run records
    only the paragraphs whose WAV is missing, and leaves the others byte for byte."""
    r = make_run(tmp_path)
    cfg["overnight"]["record"]["sessions"] = 1  # one lane, so where the kill lands is certain
    t = texts_by_id(r)

    def kill(session, text):
        if text == t["s3-p02"]:
            raise Killed()
    first = FakeLive({t["s1-p02"]: ["misread", "ok"]}, before_read=kill)
    with pytest.raises(Killed):
        record.run(r, cfg, session_factory=first)
    folder = r.root / "10_audio" / "paragraphs"
    kept = {p.name: p.read_bytes() for p in folder.glob("*.wav")}
    assert sorted(kept) == [f"{pid}.wav" for pid in ["s1-p01", "s1-p02", "s2-p01", "s2-p02", "s2-p03", "s3-p01"]]

    cfg["overnight"]["record"]["sessions"] = 4
    second = FakeLive()
    out = record.run(r, cfg, session_factory=second)
    redone = {pid for pid, text in t.items() if text in second.texts()}
    assert redone == {pid for pid in t if f"{pid}.wav" not in kept}
    assert all((folder / name).read_bytes() == data for name, data in kept.items())
    assert out["paragraphs"] == 9 and out["passed"] == 9
    takes = json.loads((folder / "s1-p02.json").read_text())["take"]
    assert takes == 2  # the take numbers continue; nothing overwritten


def test_a_lost_connection_opens_a_new_session(tmp_path, cfg):
    r = make_run(tmp_path)
    cfg["overnight"]["record"]["sessions"] = 1
    t = texts_by_id(r)
    live = FakeLive({t["s2-p02"]: ["lost"], t["s3-p01"]: ["never", "ok"]})
    out = record.run(r, cfg, session_factory=live)
    assert out["passed"] == 9 and len(live.sessions) == 3  # one for the drop, one after the stuck take
    assert [how for *_, how in live.reads].count("lost") == 1
    never = [json.loads(x) for x in (r.root / "10_audio" / "recording.jsonl").read_text().splitlines()
             if json.loads(x)["status"] == "never_started"]
    assert len(never) == 1 and never[0]["id"] == "s3-p01" and not never[0]["passed"]


def test_repeated_drops_stop_the_stage(tmp_path, cfg):
    r = make_run(tmp_path)
    cfg["overnight"]["record"]["sessions"] = 1
    t = texts_by_id(r)
    live = FakeLive({t["s1-p01"]: ["lost"] * 10})
    with pytest.raises(StageError, match="failed 4 times"):
        record.run(r, cfg, session_factory=live)
    assert live.closed == len(live.sessions) == 4


def test_a_paragraph_never_read_stops_the_stage_unfinished(tmp_path, cfg):
    r = make_run(tmp_path)
    cfg["overnight"]["record"]["sessions"] = 1
    t = texts_by_id(r)
    with pytest.raises(StageError, match="never read paragraph s1-p01"):
        record.run(r, cfg, session_factory=FakeLive({t["s1-p01"]: ["never"] * 4}))
    assert not (r.root / "10_audio" / "recorded.json").exists()


# --- the real client against a local fake server ---------------------------------------------------

@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.stop()


def test_the_live_client_follows_reads_and_cuts_takes(server, cfg):
    first = "The heron stands in the shallows, motionless, considering everything."
    second = "Nothing about it moves, except, now and then, the eye."
    server.plan = ["ok", "preamble"]
    s = record.LiveSession(cfg, "INSTRUCTIONS", "t", key="test-key", url=server.url)
    try:
        assert s.id == "live_test_1"
        start = server.start
        assert start["type"] == "session.start" and server.headers["authorization"] == "Bearer test-key"
        assert start["session"] == {"model": "gpt-live-1", "instructions": "INSTRUCTIONS",
                                    "audio": {"output": {"voice": "gleam"},
                                              "format": {"type": "audio/pcm", "rate": 24000}},
                                    "input": [], "store": False}
        a = s.read(first)
        words = [w for w in server.words if w["w"] in first.split()]
        assert a.status == "done" and record.deviation(first, a.heard)["diffs"] == []
        assert a.heard == first  # "motionless" and "considering" came split across two deltas
        onset, end = words[0]["start"], words[-1]["end"]
        assert abs(a.start_ms - (onset - record.PAD_LEAD * 1000)) <= 15
        assert abs(a.seconds - ((end - onset) / 1000 + record.PAD_LEAD + record.PAD_TAIL)) <= 0.03

        b = s.read(second)  # "Right." first: not the paragraph, and not in its audio
        assert b.status == "done" and b.heard == second
        onset2 = next(w["start"] for w in server.words if w["w"] == "Nothing")
        assert abs(b.start_ms - (onset2 - record.PAD_LEAD * 1000)) <= 15
        pcm = np.frombuffer(b.pcm, np.int16)
        lead = int(record.PAD_LEAD * record.RATE)
        assert not pcm[: lead - 240].any() and pcm[lead + 240: lead + 480].any()
    finally:
        seconds = s.close()
    types = [e["type"] for e in server.received]
    assert types.count("session.commentary.append") == 2 and "session.input_audio.mute" in types
    assert types.count("session.input_audio.append") > 5 and types[-1] == "session.close"
    contents = [e["content"] for e in server.received if e["type"] == "session.commentary.append"]
    assert contents == [f"{record.READ} {first}", f"{record.READ} {second}"]
    assert all(e["delegation_id"] is None for e in server.received if e["type"] == "session.commentary.append")
    assert seconds == server.closed_with and seconds > 5


def test_the_live_client_resends_once_then_gives_up(server, cfg):
    text = "Patience, it turns out, is a strategy with a cost."
    server.plan = ["ignore", "ok", "ignore", "ignore"]
    s = record.LiveSession(cfg, "I", "t", key="k", url=server.url)
    try:
        a = s.read(text)
        assert a.resent and a.status == "done" and a.heard == text
        b = s.read(text)
        assert b.status == "never_started" and b.pcm == b"" and b.resent
    finally:
        s.close()
    assert [e["type"] for e in server.received].count("session.commentary.append") == 4


def test_the_live_client_ends_a_cut_take_after_a_quiet(server, cfg):
    text = "So it waits, and it does not move, not for anything at all, not even for the wind."
    s = record.LiveSession(cfg, "I", "t", key="k", url=server.url)
    real = server.schedule
    server.schedule = lambda t, at: real(" ".join(t.split()[:7]), at)  # stops after seven words
    try:
        a = s.read(text)
    finally:
        s.close()
    assert a.status == "quiet" and a.heard == " ".join(text.split()[:7]) and a.pcm
    assert not record.passes(record.deviation(text, a.heard), 2)


def test_a_session_that_cannot_open_is_lost_not_fatal(cfg):
    with pytest.raises(record.SessionLost, match="could not open"):
        record.LiveSession(cfg, "I", "t", key="k", url="ws://127.0.0.1:9")
