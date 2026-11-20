"""Where each tester has got to (demo/state.py): stage, left early, minutes heard, questions, debrief, cost."""

import pytest

from demo import state, testers

from tester_fakes import demo_cfg, jsonl, seed


@pytest.fixture
def dcfg(cfg):
    return demo_cfg(cfg)


@pytest.mark.parametrize("stage", state.STAGES)
def test_each_stage_is_read_from_the_folder(dcfg, stage):
    lid = seed(dcfg, "Pilot 1", stage)
    s = state.tester_state(dcfg, lid)
    assert s["stage"] == stage and s["left_early"] is False
    assert (s["last_active"] is None) == (stage == "not opened")


def test_left_early_once_quiet_for_half_an_hour_and_not_done(dcfg):
    gone = seed(dcfg, "Gone", "chapter", quiet_for=2 * 3600)
    here = seed(dcfg, "Here", "chapter", quiet_for=60)
    done = seed(dcfg, "Done", "done", quiet_for=2 * 3600)
    assert state.tester_state(dcfg, gone)["left_early"] is True
    assert state.tester_state(dcfg, here)["left_early"] is False
    assert state.tester_state(dcfg, done)["left_early"] is False


def test_a_full_run_minutes_questions_title_debrief_and_cost(dcfg):
    lid = seed(dcfg, "Pilot 1", "done")
    s = state.tester_state(dcfg, lid)
    assert s["title"] == "How Bridges Stand" and s["chapter"] == "bridges-20261116"
    # the best reading of each paragraph: 150 words (not 50 + 150) and 225, at 2.5 words a second
    assert s["minutes_heard"] == 2.5 and s["chapter_minutes"] == 10
    assert s["questions"] == 1  # an ask with the listener's words; not the empty tap, not the check-in
    assert s["debrief"] == {"done": True, "summary": "debrief_summary.md", "transcript": "debrief-s2.jsonl",
                            "transcripts": ["debrief-s2.jsonl"]}
    # Claude: guide 0.02 + outline 1.00 + the early scout once (0.50, not its copy in the run's log) + summary 0.10.
    # GPT-Live: intro 0.05 + guide 0.30 + live_2, seen only in the reading log, estimated from its 120 s: 0.10.
    assert s["cost"] == {"claude": 1.62, "gpt_live": 0.45, "total": 2.07, "estimated": 0.1}


def test_a_debrief_that_ended_without_the_closing_question_is_done(dcfg):
    """The debrief ends itself on its farewell (Ian's note, 2026-11-18); the closing question is optional, so
    `closing: false` is not an unfinished debrief anywhere the stage is read (the admin page, the report)."""
    import json
    lid = seed(dcfg, "Pilot 1", "done")
    p = testers.folder(lid) / "study" / "debrief.json"
    p.write_text(json.dumps({**json.loads(p.read_text()), "closing": False, "farewell": True}))
    s = state.tester_state(dcfg, lid)
    assert s["stage"] == "done" and s["debrief"]["done"] is True and s["left_early"] is False


def test_title_falls_back_to_the_outline_then_the_card(dcfg):
    lid = seed(dcfg, "Pilot 1", "chapter")
    ch = testers.folder(lid) / "chapters" / "bridges-20261116"
    (ch / "status.json").write_text('{"state": "making", "title": null}')
    assert state.tester_state(dcfg, lid)["title"] == "How Bridges Stand"
    (ch / "05_outline.json").unlink()
    assert state.tester_state(dcfg, lid)["title"] is None
    (ch / "fast").mkdir()
    (ch / "fast" / "card.json").write_text('{"title": "Bridges", "overview": "", "summary": ""}')
    assert state.tester_state(dcfg, lid)["title"] == "Bridges"


def test_an_estimate_spans_a_sessions_transcript_lines(dcfg):
    lid = seed(dcfg, "Pilot 1", "onboarding")
    jsonl(testers.folder(lid) / "study" / "debrief-s9.jsonl", [
        {"t": "2026-11-16T11:00:00", "who": "interviewer", "text": "Hello", "live_id": "live_d"},
        {"t": "2026-11-16T11:06:00", "who": "listener", "text": "Bye", "live_id": "live_d"}])
    c = state.tester_state(dcfg, lid)["cost"]
    assert c["estimated"] == pytest.approx(0.30)  # 6 minutes at $0.05


def test_the_chapter_minutes_follow_the_testers_settings(dcfg):
    from overnight import profiles
    lid = seed(dcfg, "Pilot 1", "intro")
    profiles.save_settings(dcfg, {"chapter": {"minutes": 12}}, listener=testers.folder(lid))
    assert state.tester_state(dcfg, lid)["chapter_minutes"] == 12


def test_all_states_and_totals(dcfg):
    seed(dcfg, "A", "done")
    seed(dcfg, "B", "not opened")
    testers.revoke("t-a")
    rows = state.all_states(dcfg)
    assert [r["lid"] for r in rows] == ["t-a", "t-b"]
    assert rows[0]["revoked"] is True and rows[1]["revoked"] is False and rows[1]["token"]
    assert state.totals(rows) == {"claude": 1.62, "gpt_live": 0.45, "total": 2.07, "estimated": 0.1}


def test_state_marks_a_rehearsal_chapter(dcfg):
    """A rehearsal (faststart/rehearsal.py) is shown as one, with the chapter it replays; it costs nothing."""
    import json
    lid = testers.create(dcfg, "Voice Test")["lid"]
    testers.update(lid, opened_at="2026-11-20T10:00:00", consent_at="2026-11-20T10:00:00", intro_done=True,
                   chapter="rehearsal")
    ch = testers.folder(lid) / "chapters" / "herons-20261120"
    ch.mkdir(parents=True)
    (ch / "fast.json").write_text(json.dumps({"mode": "fast", "voice": "live", "rehearsal": {
        "source": "/x/2026-11-14-herons", "source_run": "2026-11-14-herons", "sections": 2}}))
    (ch / "status.json").write_text(json.dumps({"run": ch.name, "state": "making", "started": "2026-11-20T10:01:00"}))
    (ch / "log.jsonl").write_text(json.dumps({"stage": "fast_archive", "label": "archive (read live: no audio)"}) + "\n")
    s = state.tester_state(dcfg, lid)
    assert s["rehearsal"] is True and s["rehearsal_source"] == "2026-11-14-herons" and s["chapter"] == ch.name
    assert s["cost"]["claude"] == 0 and s["cost"]["total"] == 0
    row = next(r for r in state.all_states(dcfg) if r["lid"] == lid)
    assert row["chapter_kind"] == "rehearsal" and row["rehearsal"] is True
    other = seed(dcfg, "Pilot 1", "done")
    assert state.tester_state(dcfg, other)["rehearsal"] is False
