"""signals/cli.py and signals/report.py: wl signals fetch | run | report, through cli.main."""
import json
import os
import shutil
import time

import pytest

import cli
import signals_synth as syn
from demo import testers
from signals import cli as signals_cli
from signals import measure, record, report, store
from stages import common
from stages.common import StageError
from voice.gpt_live import session as live_session


@pytest.fixture(autouse=True)
def no_real_dot_env(monkeypatch, tmp_path_factory):
    """wl signals loads ROOT/.env, which on Ian's machine holds the real keys: never in a test."""
    monkeypatch.setattr(signals_cli, "ROOT", tmp_path_factory.mktemp("no-dot-env"))
    monkeypatch.delenv("WL_DEMO", raising=False)


@pytest.fixture
def no_key(monkeypatch):
    def refuse():
        raise StageError("No OpenAI key: set OPENAI_API_KEY in .env (see README, 'API keys').")
    monkeypatch.setattr(live_session, "api_key", refuse)


def make_tester(cfg, tmp_path, label="Pooja", **kw):
    lid = testers.create(cfg, label)["lid"]
    syn.visit_files(testers.folder(lid), tmp_path, **kw)
    return lid


def phone_on_loudspeaker(lst):
    syn.jsonl(lst / "study" / "events.jsonl", [
        {"t": "2026-11-21T10:09:50", "event": "audio_route",
         "data": {"reason": "speakerphone", "result": "switched", "ua": "Mozilla/5.0 (Linux; Android 16)",
                  "ua_hints": {"model": "Pixel 8", "platform": "Android"},
                  "inputs": ["Speakerphone", "Headset earpiece"], "track_label": "Speakerphone"}},
        {"t": "2026-11-21T10:20:00", "event": "play", "phase": "talk"}])


def test_run_then_report_through_the_cli(cfg, tmp_path, capsys):
    lid = make_tester(cfg, tmp_path, bleed_g2=0.15)
    lst = testers.folder(lid)
    assert cli.main(["signals", "run", lid]) == 0
    out = capsys.readouterr().out
    assert f"{lid} ({lst}): 8 turn records in 2 conversations" in out
    assert "intro-20261121-101000      intro    1 recording, 2 turns; 4 sounds with no transcript" in out
    assert "20261121-101500            guide    2 recordings, 6 turns; 6 sounds with no transcript" in out
    assert len(store.records(lst, syn.GUIDE)) == 6

    phone_on_loudspeaker(lst)
    store.add_mark(lst, syn.GUIDE, 12.0, "lost them here")
    store.add_mark(lst, syn.GUIDE, 300.0, "nothing near")
    assert cli.main(["signals", "report", lid]) == 0
    text = capsys.readouterr().out
    rep = report.build(lst, common.load_config(), lid)
    assert text == rep["text"]
    assert rep["tags"] == {"counts": {"inhale": 1}, "turns": 9, "tagged": 1}
    assert "Sound tags: [inhale] 1 (1 of 9 listener turns had one)" in text
    assert rep["phone"] == {"records": 1, "phone": True, "speakerphone": True, "devices": ["Pixel 8 Android"]}
    rec = {r["live_id"]: r for r in rep["recordings"]}
    assert set(rec) == {"live_i", "live_g1", "live_g2"} and rec["live_i"]["file"] == "live_i.mp3"
    assert rec["live_g1"]["bleed_db"] < -30 < -15 < rec["live_g2"]["bleed_db"]   # the second on the loudspeaker
    s = rep["sources"]["recording"]
    assert s["turns"] == 8 and s["with_norm"] == 5
    assert s["parts"] == {"intro": {"turns": 2, "with_norm": 0}, "guide": {"turns": 6, "with_norm": 5}}
    assert s["first"]["stem"] == syn.GUIDE and s["first"]["turn"] == 1
    assert s["first"]["read"] == {"intro": 2, "guide": 1}                     # the intro counts toward the norm
    assert "first at turn 1 of 20261121-101500 (guide), from intro 2, guide 1" in text
    assert s["mentions"]["tags"] == 1 and s["mentions"]["cut_in"] == 1 and s["mentions"]["trailed"] == 1
    near, far = rep["marks"]
    assert near["text"] == "lost them here" and [t["turn"] for t in near["turns"]] == [0, 1, 2, 3]
    assert "tags" in near["turns"][1]["mention"]
    assert far["turns"] == [] and "no turn within 15 s" in text
    assert "Phone: a phone (Pixel 8 Android), on the loudspeaker; 1 audio_route records" in text


def test_report_before_any_records(cfg, tmp_path):
    lst = tmp_path / "solo"
    syn.jsonl(lst / "sessions" / "20261121-090000.jsonl", [
        {"t": "2026-11-21T09:00:00", "who": "session", "live_id": "x1"},
        {"t": "2026-11-21T09:00:05", "who": "listener", "text": "[tongue click ]Yeah"},
        {"t": "2026-11-21T09:00:06", "who": "guide", "text": "Hi."},
        {"t": "2026-11-21T09:00:09", "who": "listener", "text": "[inhale"},
        {"t": "2026-11-21T09:00:12", "who": "listener", "text": "]Sure."}])
    rep = report.build(lst, cfg)
    assert rep["tags"] == {"counts": {"tongue click": 1, "inhale": 1}, "turns": 2, "tagged": 2}
    assert rep["sources"] == {} and "No records yet (wl signals run)" in rep["text"]
    assert rep["recordings"] == [{"live_id": "x1", "stem": "20261121-090000", "part": "guide", "file": None}]
    assert "no audio (wl signals fetch)" in rep["text"] and "Phone: not known" in rep["text"]


def test_fetch_through_the_cli(cfg, tmp_path, capsys, no_key):
    lid = make_tester(cfg, tmp_path)
    lst = testers.folder(lid)
    store.audio_path(lst, "live_g2").unlink()
    syn.wav(lst / "chapters" / "20261121-heron" / "live" / "live_g2.wav", 1.0, [(0.2, 0.6)], [(0.0, 0.1)])
    assert cli.main(["signals", "fetch", lid]) == 0
    out = capsys.readouterr().out
    assert f"{lid} ({lst}): 3 have" in out
    assert "have     20261121-101500            live_g2: reused chapters/20261121-heron/live/live_g2.wav" in out
    assert "live_i: only study/live_i.mp3 (lossy): No OpenAI key" in out


def test_which_listeners(cfg, tmp_path, capsys, monkeypatch):
    a = testers.create(cfg, "Ann")["lid"]
    b = testers.create(cfg, "Ben")["lid"]
    gone = testers.create(cfg, "Cy")["lid"]
    shutil.rmtree(testers.folder(gone))
    own = common.listener_dir(cfg)
    assert signals_cli.listeners(cfg, None, False) == [("own", own)]
    assert signals_cli.listeners(cfg, "own", False) == [("own", own)]
    assert signals_cli.listeners(cfg, a, False) == [(a, testers.folder(a))]
    assert signals_cli.listeners(cfg, None, True) == [(a, testers.folder(a)), (b, testers.folder(b)), ("own", own)]
    assert [x for x, _ in signals_cli.listeners({**cfg, "demo": {"enabled": True}}, None, True)] == [a, b]
    monkeypatch.setenv("WL_DEMO", "1")
    assert [x for x, _ in signals_cli.listeners(cfg, None, True)] == [a, b]
    for bad in ("t-nobody", "../t-ann", gone):
        with pytest.raises(StageError, match="No tester"):
            signals_cli.listeners(cfg, bad, False)
    monkeypatch.delenv("WL_DEMO")
    assert cli.main(["signals", "run", "t-nobody"]) == 1
    assert "Stopped: No tester t-nobody" in capsys.readouterr().out
    assert cli.main(["signals", "fetch"]) == 0                       # the active listener has no folder yet
    assert f"own: no folder at {own}" in capsys.readouterr().out
    assert cli.main(["signals", "run", "--all"]) == 0
    out = capsys.readouterr().out
    assert f"{a} ({testers.folder(a)}): 0 turn records in 0 conversations" in out and gone not in out


def test_measure_run_is_what_the_cli_runs(cfg, tmp_path, monkeypatch, capsys):
    lid = make_tester(cfg, tmp_path, intro_mp3=False)
    seen = []
    real = measure.run
    monkeypatch.setattr(measure, "run",
                        lambda folder, c, force=False: seen.append(force) or real(folder, c, force=force))
    assert cli.main(["signals", "run", lid, "--force"]) == 0
    assert seen == [True]
    assert "no audio for live_i (wl signals fetch)" in capsys.readouterr().out
    assert json.loads(store.path(testers.folder(lid), syn.GUIDE).read_text().splitlines()[0])["source"] == "recording"


@pytest.mark.parametrize("how", ["reset", "deleted"])
def test_run_says_when_the_folder_went_mid_run(cfg, tmp_path, monkeypatch, capsys, how):
    lid = make_tester(cfg, tmp_path)
    lst = testers.folder(lid)
    real, seen = measure.measure_session, []

    def measure_session(*a):                 # Ian resets or deletes the tester from /admin while it measures
        out = real(*a)
        if not seen:
            testers.reset(lid, cfg) if how == "reset" else testers.delete(lid)
        seen.append(1)
        return out
    monkeypatch.setattr(measure, "measure_session", measure_session)
    assert signals_cli._run(lid, lst, cfg, False)["gone"] == how
    out = capsys.readouterr().out
    assert out == f"  {lid} ({lst}): the folder was {how} while measuring; stopped" + \
        (", nothing written into the new one\n" if how == "reset" else "\n")
    assert not (lst / "signals").exists()


def test_a_mark_on_a_conversation_with_no_records_is_reported(cfg, tmp_path):
    lst = tmp_path / "solo"
    syn.jsonl(lst / "sessions" / "20261121-090000.jsonl", [
        {"t": "2026-11-21T09:00:00", "who": "session", "live_id": "x1"},
        {"t": "2026-11-21T09:00:05", "who": "listener", "text": "Yeah, sure."}])
    store.add_mark(lst, "20261121-090000", 4.0, "lost them here")
    rep = report.build(lst, cfg)
    assert [(m["stem"], m["text"], m["records"], m["turns"]) for m in rep["marks"]] == [
        ("20261121-090000", "lost them here", False, [])]
    assert 'Mark 20261121-090000 at 0:04: "lost them here"\n' \
        "    no records for this conversation: wl signals fetch, then run\n" in rep["text"]
    assert "no turn within" not in rep["text"]


def test_the_report_puts_page_turns_beside_the_recording(cfg, tmp_path):
    lst = syn.visit_files(tmp_path / "t-pooja", tmp_path)
    S = record.settings(cfg)
    page = [  # live_id, start_s, end_s, gap_ms, dur_ms, text; the replay's turns 0-4 (live_g1) are in syn.TURNS
        ("live_g1", 3.9, 5.1, 900, 1200, "I went to the park today."),         # turn 0: 3.8-5.0, 800, 1200
        ("live_g1", 10.3, 13.3, 1300, 2900, "Yeah, it was good, kind of quiet."),   # turn 1: 10.2-13.5, 1200, 3300
        ("live_g1", 20.0, 20.5, 2000, 500, "Hm, right."),                      # turn 2 ended at 18.0
        ("live_g1", None, None, None, 900, "Something else entirely."),        # no session clock, no such words
        ("live_zz", 3.9, 5.1, 900, 1200, "I went to the park today."),         # a session with no recording
    ]
    for k, (live_id, a, b, gap, dur, words) in enumerate(page):
        store.append(lst, syn.GUIDE, record.build({"start_s": a, "end_s": b, "gap_ms": gap, "dur_ms": dur}, words,
                                                  stem=syn.GUIDE, part="guide", source="page", turn=k,
                                                  live_id=live_id, t=syn.iso(syn.BASE, a or 30), S=S))
    store.append(lst, "debrief-20261121-110000", {"source": "page", "turn": 0, "stem": "debrief-20261121-110000",
                                                  "part": "debrief", "t": "2026-11-21T11:00:05", "words": 2})
    measure.run(lst, cfg)
    store.append(lst, syn.GUIDE, record.build({"start_s": 24.1, "end_s": 25.4}, "Um, I don't know.", stem=syn.GUIDE,
                                              part="guide", source="page", turn=5, live_id="live_g1",
                                              t=syn.iso(syn.BASE, 24), S=S))     # written after the replay
    c = report.build(lst, cfg)["compare"]
    assert [(p["stem"], p["turn"], p["page"]) for p in c["pairs"]] == [(syn.GUIDE, 0, [0]), (syn.GUIDE, 1, [1])]
    want = [{"start_ms": -100, "end_ms": -100, "gap_ms": -100, "dur_ms": 0, "words": 0},
            {"start_ms": -100, "end_ms": 200, "gap_ms": -100, "dur_ms": 400, "words": 0}]
    for p, w in zip(c["pairs"], want):
        assert p["diff"] == {k: pytest.approx(v, abs=20) for k, v in w.items()}
    sm = c["summary"]
    assert sm["end_ms"] == {"n": 2, "median": pytest.approx(150, abs=20), "largest": pytest.approx(200, abs=20),
                            "within": 2}
    assert sm["dur_ms"]["within"] == 1 and sm["words"] == {"n": 2, "median": 0, "largest": 0}
    assert [(u["turn"], u["why"]) for u in c["unmatched"]] == [
        (2, "outside the 1 s slack (nearest: recording turn 2, 2.0 s away)"),
        (3, "no session clock and no text match"),
        (4, "no recording of its GPT-Live session"),
        (5, "near recording turn 3, but newer than the replay (wl signals run again)")]
    assert c["unreplayed"] == ["debrief-20261121-110000"]
    text = report.text(report.build(lst, cfg))
    assert "  Page against recording (recording minus page, the same turns):\n" \
        f"    {syn.GUIDE} turn 0 (page 0): start " in text
    assert "    end: median 150 ms, largest 200 ms, 2 of 2 within 300 ms\n" in text
    assert "    words: median 0, largest 0\n" in text
    assert f"    page turn 3 of {syn.GUIDE} matched no recording: no session clock and no text match\n" in text
    assert "    page records but no replay: debrief-20261121-110000 (wl signals fetch, then run)\n" in text
    # the same visit with no page records has no such section
    other = syn.visit_files(tmp_path / "t-other", tmp_path / "o")
    assert "Page against recording" not in report.build(other, cfg)["text"]


def test_a_turn_the_page_split_at_a_pause_compares_by_its_span(cfg, tmp_path):
    """Both sources measure a turn's length as first sound to last, so a turn the page split at a pause (syn's turn
    B has a 0.8 s one) is compared by the span of its pieces, not their lengths added (which leaves the pause out).
    With no session clock, several pieces have no span: no length is compared."""
    S = record.settings(cfg)
    lst = tmp_path / "t-split"
    lst.mkdir()
    pieces = [(10.2, 11.2, 1200, "Yeah, it was good,"), (12.0, 13.5, 800, "kind of quiet.")]
    for stem, clocked in (("20261121-101500", True), ("20261121-102000", False)):
        for k, (a, b, gap, words) in enumerate(pieces):
            raw = {"start_s": a if clocked else None, "end_s": b if clocked else None, "gap_ms": gap,
                   "dur_ms": round((b - a) * 1000)}
            store.append(lst, stem, record.build(raw, words, stem=stem, part="guide", source="page", turn=k,
                                                 live_id="s1", t=f"2026-11-21T10:15:{10 + k}", S=S))
        rec = record.build({"start_s": 10.2, "end_s": 13.5, "gap_ms": 1200, "dur_ms": 3300, "speak_ms": 2500},
                           "Yeah, it was good, kind of quiet.", stem=stem, part="guide", source="recording", turn=0,
                           live_id="s1", t="2026-11-21T10:15:10", S=S)
        store.write_recordings(lst, stem, store.match_page([rec], store.records(lst, stem, "page")))
    c = report.compare(lst)
    assert [(p["stem"], p["page"]) for p in c["pairs"]] == [("20261121-101500", [0, 1]), ("20261121-102000", [0, 1])]
    assert c["pairs"][0]["diff"] == {"start_ms": 0, "end_ms": 0, "gap_ms": 0, "dur_ms": 0, "words": 0}
    assert c["pairs"][1]["diff"] == {"gap_ms": 0, "words": 0}
    assert c["summary"]["dur_ms"] == {"n": 1, "median": 0, "largest": 0, "within": 1}
    assert c["unmatched"] == []


def test_a_cut_in_is_compared_with_its_own_recording_turn(tmp_path):
    """The listener cuts in 0.6 s after their last word: two turns on both sides, the second within the slack of the
    first's end. Each page turn pairs with the recording turn it overlaps, so the cut-in's gap is compared."""
    S = record.settings({})
    stem = "20261121-101500"
    spans = [(10.0, 12.0, 800, False, "I think it was the second one, the"),
             (12.6, 14.0, -200, True, "no wait, the first one.")]

    def built(source, k, a, b, gap, cut, words, off):
        return record.build({"start_s": a + off, "end_s": b + off, "gap_ms": gap, "cut_in": cut,
                             "dur_ms": round((b - a) * 1000)}, words, stem=stem, part="guide", source=source,
                            turn=k, live_id="s1", t=f"2026-11-21T10:15:{10 + 3 * k}", S=S)
    for k, span in enumerate(spans):
        store.append(tmp_path, stem, built("page", k + 1, *span, off=0.1))          # the page's clock 0.1 s off
    recs = [built("recording", k, *span, off=0.0) for k, span in enumerate(spans)]
    store.write_recordings(tmp_path, stem, store.match_page(recs, store.records(tmp_path, stem, "page")))
    c = report.compare(tmp_path)
    assert [(p["turn"], p["page"]) for p in c["pairs"]] == [(0, [1]), (1, [2])]
    for p in c["pairs"]:
        assert p["diff"] == {"start_ms": -100, "end_ms": -100, "gap_ms": 0, "dur_ms": 0, "words": 0}
    assert c["summary"]["end_ms"]["largest"] == 100 and c["unmatched"] == []


def test_a_split_off_reply_is_compared_with_its_own_recording_turn(tmp_path):
    """The page split "Okay." (no session clock yet) and, on the guide's quiet "Mm.", the rest of the replay's first
    turn off it; the listener says "Okay." alone later. The first "Okay." pairs with the turn it was said in, not the
    later one, so no pair disagrees."""
    S = record.settings({})
    stem = "20261121-101500"
    recs = [(2.0, 5.5, "Okay. So what were we talking about?"), (9.0, 11.0, "Right, the herons."),
            (30.0, 30.4, "Okay.")]
    page = [(None, None, "Okay."), (3.6, 5.5, "So what were we talking about?"), (9.0, 11.0, "Right, the herons."),
            (30.0, 30.4, "Okay.")]
    for k, (a, b, words) in enumerate(page):
        store.append(tmp_path, stem, record.build({"start_s": a, "end_s": b, "gap_ms": 500 if k else None,
                                                   "dur_ms": round((b - a) * 1000) if a is not None else 500},
                                                  words, stem=stem, part="guide", source="page", turn=k + 1,
                                                  live_id="s1", t=f"2026-11-21T10:15:{10 + k}", S=S))
    built = [record.build({"start_s": a, "end_s": b, "gap_ms": 500, "dur_ms": round((b - a) * 1000)}, words,
                          stem=stem, part="guide", source="recording", turn=k, live_id="s1",
                          t=f"2026-11-21T10:15:{10 + k}", S=S) for k, (a, b, words) in enumerate(recs)]
    store.write_recordings(tmp_path, stem, store.match_page(built, store.records(tmp_path, stem, "page")))
    c = report.compare(tmp_path)
    assert [(p["turn"], p["page"]) for p in c["pairs"]] == [(0, [1, 2]), (1, [3]), (2, [4])]
    assert c["unmatched"] == []
    full = {"start_ms": 0, "end_ms": 0, "gap_ms": 0, "dur_ms": 0, "words": 0}
    assert [p["diff"] for p in c["pairs"]] == [{"end_ms": 0, "words": 0}, full, full]   # the first starts unclocked


def test_a_session_the_replay_heard_nothing_in_is_not_a_missing_recording(cfg, tmp_path):
    """A quiet mic: live_g2's recording is on disk and measured, but the listener's speech is under min_rms, so the
    replay has no turn there. The page heard one: that is the disagreement, not a missing recording or replay."""
    lst = syn.visit_files(tmp_path / "t-quiet", tmp_path)
    quiet = [(s[0], s[1], {**(s[2] if len(s) > 2 else {}), "amp": 180}) for s in syn.LISTENER]   # about 150 RMS
    g2 = store.audio_path(lst, "live_g2")
    g2.unlink()
    syn.wav(g2, syn.TOTAL, quiet, syn.OTHER)
    S = record.settings(cfg)
    a, b, gap, _, dur, *_ = syn.TURNS[0]
    for k, (live_id, words) in enumerate((("live_g1", "I went to the park today."),
                                          ("live_g2", "Back again, it's me."))):
        store.append(lst, syn.GUIDE, record.build({"start_s": a, "end_s": b, "gap_ms": gap, "dur_ms": dur}, words,
                                                  stem=syn.GUIDE, part="guide", source="page", turn=k,
                                                  live_id=live_id, t=syn.iso(syn.BASE, 120 * k + a), S=S))
    got = measure.run(lst, cfg)
    info = next(x for x in got["conversations"] if x["stem"] == syn.GUIDE)
    assert info["recordings"] == 2 and info["unheard"] >= 1
    assert {r["live_id"] for r in store.records(lst, syn.GUIDE, "recording")} == {"live_g1"}
    measured = ("its recording was measured, but the replay heard no turn of theirs in that session "
                "(signals.recording min_rms/floor_x may be too high for this mic)")
    rep = report.build(lst, cfg)
    assert [(u["turn"], u["why"]) for u in rep["compare"]["unmatched"]] == [(1, measured)]
    assert f"    page turn 1 of {syn.GUIDE} matched no recording: {measured}\n" in rep["text"]
    # fetched again after that replay, by a hard link to a chapter's copy made before it (fetch._place): its mtime
    # is the copy's, an hour old; the link is newer than the replay
    old = time.time() - 3600
    copy = syn.wav(tmp_path / "chapter" / "live_g2.wav", syn.TOTAL, quiet, syn.OTHER)
    os.utime(copy, (old, old))
    g2.unlink()
    os.link(copy, g2)
    replayed = store.path(lst, syn.GUIDE)
    t = replayed.stat().st_mtime - 10                            # never the same tick on a coarse clock
    os.utime(replayed, (t, t))
    assert [u["why"] for u in report.compare(lst)["unmatched"]] == [
        "its recording was fetched after the last replay (wl signals run)"]


def test_a_conversation_the_replay_heard_nothing_in_is_not_unreplayed(cfg, tmp_path):
    """Both sessions quiet: the replay ran and wrote no turn for the conversation. Its page turns are listed as
    unmatched, with why; "no replay (wl signals fetch, then run)" would send Ian to rerun what already ran."""
    lst = syn.visit_files(tmp_path / "t-quiet", tmp_path)
    quiet = [(s[0], s[1], {**(s[2] if len(s) > 2 else {}), "amp": 180}) for s in syn.LISTENER]
    for live_id in ("live_g1", "live_g2"):
        p = store.audio_path(lst, live_id)
        p.unlink()
        syn.wav(p, syn.TOTAL, quiet, syn.OTHER)
    a, b, gap, _, dur, *_ = syn.TURNS[0]
    store.append(lst, syn.GUIDE, record.build({"start_s": a, "end_s": b, "gap_ms": gap, "dur_ms": dur},
                                              "I went to the park today.", stem=syn.GUIDE, part="guide",
                                              source="page", turn=0, live_id="live_g1", t=syn.iso(syn.BASE, a),
                                              S=record.settings(cfg)))
    got = measure.run(lst, cfg)
    assert syn.GUIDE in got["written"] and store.records(lst, syn.GUIDE, "recording") == []
    rep = report.build(lst, cfg)
    assert rep["compare"]["unreplayed"] == [] and "page records but no replay" not in rep["text"]
    assert [(u["turn"], u["why"]) for u in rep["compare"]["unmatched"]] == [
        (0, "its recording was measured, but the replay heard no turn of theirs in that session "
            "(signals.recording min_rms/floor_x may be too high for this mic)")]
