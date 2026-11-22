"""The debrief's earpiece, server side (docs/debrief-earpiece-design.md; Ian, 2026-11-22): the visit brief the
interviewer is given (demo/debrief.visit_brief), the earpiece's looks (demo/debrief_earpiece.py, POST
/api/interview/direct) and fates, and the admin's Earpiece history listing debriefs. No model is reached: llm's
client is tests/fakes.py's FakeAnthropic answering with the JSON a test gives it."""

import json

from pathlib import Path

import pytest
from fakes import FakeAnthropic, Responder
from test_interviewer import testers, world  # noqa: F401  (fixtures)

from demo import debrief, debrief_earpiece
from demo import interviewer as IV
from stages import llm
from voice.gpt_live import earpiece_history as EH
from voice.gpt_live import session as live_session


@pytest.fixture(autouse=True)
def earpiece_on(cfg):
    """tests/conftest.py turns the debrief's earpiece off for every test; these are about it."""
    cfg["demo"]["interviewer"]["director"]["enabled"] = True


def answering(monkeypatch, *answers):
    calls, it = [], iter(answers)

    def respond(kwargs):
        calls.append(kwargs)
        a = next(it)
        return Responder.msg(kwargs, a if isinstance(a, str) else json.dumps(a))
    monkeypatch.setattr(llm, "_client", FakeAnthropic(respond))
    return calls


def look_json(note=None, *, plan=None, read="r", about=None, kind=None, nxt=None):
    return {"read": read, "plan": plan if plan is not None else [{"item": "Opening: what stood out", "state": "done"}],
            "next": nxt, "changed": bool(note), "kind": kind or ("next_turn" if note else None),
            "about": about, "note": note}


def jsonl(p: Path, rows: list[dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def a_visit(folder: Path, *, rehearsal=False, stopped=False) -> Path:
    """A tester's folder with one guide conversation and a live chapter: seed, outline, part 1 read, a question
    asked mid-piece with the voice's answer."""
    jsonl(folder / "sessions" / "20261122-100000.jsonl", [
        {"t": "2026-11-22T10:00:00", "who": "session", "names": ["Nilo"]},
        {"t": "2026-11-22T10:00:05", "who": "guide", "text": "Hi there!"},
        {"t": "2026-11-22T10:00:09", "who": "listener", "text": "Hi. I just got back from sailing on the bay."},
        {"t": "2026-11-22T10:00:30", "who": "listener", "text": "I always wondered why sails can go against the wind."},
        {"t": "2026-11-22T10:01:00", "who": "tool", "name": "start_chapter"},
    ])
    jsonl(folder / "sessions" / "20261101-090000.jsonl", [   # a visit weeks ago: not this one
        {"t": "2026-11-01T09:00:00", "who": "listener", "text": "Tell me about bees."}])
    root = folder / "chapters" / "2026-11-22-sails"
    (root / "00_source").mkdir(parents=True)
    (root / "fast.json").write_text(json.dumps({"mode": "fast", "voice": "live",
                                                **({"rehearsal": {"source": "x"}} if rehearsal else {})}))
    (root / "status.json").write_text(json.dumps({"run": root.name, "state": "stopped" if stopped else "ready",
                                                  "started": "2026-11-22T10:01:00"}))
    (root / "00_source" / "seed.md").write_text(
        "# Seed\n\n## Topic\n\nhow sails work upwind\n\n## What the listener said, in their own words\n\n"
        "I always wondered why sails can go against the wind.\n\n## Why it matters to them\n\n(not said)\n\n"
        "## Include or avoid\n\n(nothing)\n")
    (root / "05_outline.json").write_text(json.dumps({"title": "Sailing Into the Wind", "angle": "lift", "sections": [
        {"n": 1, "title": "The Wing on the Water", "lands": "A sail is a wing: lift, not push."},
        {"n": 2, "title": "The Keel's Half", "lands": "Without the keel the boat slides sideways."}]}))
    jsonl(root / "live" / "reading.jsonl", [{"t": "2026-11-22T10:05:00", "id": "s1-p01", "heard": "A sail is"}])
    jsonl(root / "12_asks" / "asides.jsonl", [{"kind": "ask", "paragraph": "s1-p01", "sentence": 0, "transcript": [
        {"who": "listener", "text": "Wait, so is it like an airplane wing?"},
        {"who": "guide", "text": "Very like one: the curve makes the air on one side move faster."}]}])
    return root


# --- the visit brief -------------------------------------------------------------------------------------

def test_the_brief_has_what_they_asked_for_their_words_the_outline_and_their_questions(cfg, tmp_path):
    folder = tmp_path / "t-sam"
    a_visit(folder)
    text = debrief.visit_brief(folder, cfg)
    assert "They asked for a piece about: how sails work upwind" in text
    assert 'In their words: "I always wondered why sails can go against the wind."' in text
    assert "(not said)" not in text and "Why it mattered" not in text   # placeholders are left out
    assert '"Hi. I just got back from sailing on the bay."' in text and "bees" not in text   # this visit only
    assert "Hi there" not in text                                          # their words, not the voice's
    assert 'Part 1, "The Wing on the Water" (partly heard): A sail is a wing' in text
    assert 'Part 2, "The Keel\'s Half" (not heard)' in text
    assert 'They asked: "Wait, so is it like an airplane wing?" The voice answered: "Very like one' in text
    assert len(text) <= debrief.BRIEF_CHARS


def test_a_stopped_piece_and_a_rehearsal(cfg, tmp_path):
    folder = tmp_path / "t-sam"
    a_visit(folder, stopped=True)
    assert "It was stopped before it was finished." in debrief.visit_brief(folder, cfg)
    other = tmp_path / "t-ann"
    a_visit(other, rehearsal=True)
    text = debrief.visit_brief(other, cfg)
    assert "stand-in" in text and "The Wing on the Water" not in text and "airplane wing" not in text
    assert "how sails work upwind" in text   # what they asked for is still theirs


def test_the_brief_is_capped_and_empty_without_a_record(cfg, tmp_path):
    assert debrief.visit_brief(tmp_path / "nobody", cfg) == ""
    folder = tmp_path / "t-sam"
    a_visit(folder)
    jsonl(folder / "sessions" / "20261122-100500.jsonl",
          [{"t": "2026-11-22T10:05:00", "who": "listener", "text": f"line {i} " + "word " * 60} for i in range(40)])
    text = debrief.visit_brief(folder, cfg)
    assert len(text) <= debrief.BRIEF_CHARS and "line 39" in text and "line 0 " not in text   # the oldest go first
    assert sum(len(x) for x in debrief.their_lines(folder, None)) <= debrief.THEIR_LINES_CHARS


def test_the_interviewer_is_given_the_brief_and_told_how_to_use_it(cfg, tmp_path):
    folder = tmp_path / "t-sam"
    a_visit(folder)
    values = IV.debrief_values(cfg, folder)
    text = IV.instructions("debrief", values)
    assert "THEIR VISIT" in text and "how sails work upwind" in text and "EARPIECE NOTES" in text
    assert "ask about one specific thing from their visit" in text
    assert live_session.approx_tokens(text) < live_session.INSTRUCTION_TOKENS
    empty = IV.values_from("Sam", {"run": None}, [], None)
    assert empty["visit"] == IV.notes()["visit_none"]
    assert "{note}" in IV.page_notes("debrief", empty)["direct"]
    assert "direct" not in IV.page_notes("intro", {"_name": "Sam"})


# --- the earpiece ----------------------------------------------------------------------------------------

def debrief_started(world):
    got = world.client.post("/api/interview/start", json={"mode": "debrief", "sdp": "OFFER"}).json()
    return got


def rows(world, sid):
    p = world.dir / "study" / f"debrief-{sid}.jsonl"
    return p, [json.loads(x) for x in p.read_text().splitlines()]


def test_start_gives_the_page_its_knobs_its_wrapper_and_the_testers_view(world, cfg):
    got = debrief_started(world)
    assert got["settings"]["direct"] == {"enabled": True, "hear_words": 8, "hear_every_ms": 5000, "turn_merge_ms": 1500,
                                         "timeout_ms": 5000}
    assert got["notes"]["direct"].startswith("[Earpiece note from Ian") and "{note}" in got["notes"]["direct"]
    assert got["earpiece_view"] is False
    _, recs = rows(world, got["sid"])
    assert "visit" in recs[0]
    intro = world.client.post("/api/interview/start", json={"mode": "intro", "sdp": "OFFER"}).json()
    assert "direct" not in intro["settings"] and "earpiece_view" not in intro


def test_a_look_reads_the_debrief_the_brief_and_its_plan_and_records_a_row_without_text(world, cfg, monkeypatch):
    a_visit(world.dir)
    got = debrief_started(world)
    sid, c = got["sid"], world.client
    c.post("/api/interview/line", json={"sid": sid, "who": "out", "text": "Welcome back, Sam. What's one thing that stood out to you?"})
    plan = [{"item": "Opening: what stood out", "state": "done"}, {"item": "The airplane-wing answer: how it landed", "state": "open"}]
    calls = answering(monkeypatch, look_json("Ask how the voice's airplane-wing answer landed for them.", plan=plan,
                                             about="moment", nxt="The airplane-wing answer: how it landed"),
                      look_json(None, plan=plan))
    ans = c.post("/api/interview/direct", json={"sid": sid, "id": 1, "trigger": "hearing", "hearing": "the wing part was",
                                                "recent": [{"who": "in", "text": "Honestly the bit about lift."}],
                                                "page": {"seconds": 20, "questions": 1}}).json()
    assert ans["changed"] is True and ans["kind"] == "next_turn" and ans["about"] == "moment"
    assert ans["note"].startswith("Ask how the voice's airplane-wing answer")
    user = calls[0]["messages"][0]["content"]
    assert "Their words in progress (not yet a finished line): the wing part was" in user
    assert "listener: Honestly the bit about lift." in user          # the page's recent line, not yet posted
    assert "interviewer: Welcome back, Sam." in user
    assert "how sails work upwind" in user and "Questions the interviewer has asked so far, its opening included: 1." in user
    assert "(first look: start from the default plan)" in user
    p, recs = rows(world, sid)
    look = [r for r in recs if r["who"] == "director"][0]
    assert "text" not in look and look["note"].startswith("Ask how") and look["trigger"] == "hearing"
    c.post("/api/interview/direct", json={"sid": sid, "id": 2, "trigger": "turn_end"})
    assert "The airplane-wing answer: how it landed" in calls[1]["messages"][0]["content"]   # its plan came back
    log = [json.loads(x) for x in (world.dir / "study" / "log.jsonl").read_text().splitlines()]
    mine = [r for r in log if r.get("stage") == "interviewer_director"]
    assert len(mine) == 2 and all(r["session"] == sid and r["kind"] == "interviewer" for r in mine)
    # nothing that reads the debrief takes the earpiece's rows for speech
    q = IV.covered(IV.read_lines(p))
    assert "airplane" not in IV.format_transcript(IV.read_lines(p))
    assert q["said"] is False


def test_off_capped_failed_and_ended(world, cfg, monkeypatch):
    got = debrief_started(world)
    sid, c = got["sid"], world.client
    cfg["demo"]["interviewer"]["director"]["enabled"] = False
    assert c.post("/api/interview/direct", json={"sid": sid, "id": 1}).json()["error"] == "off"
    cfg["demo"]["interviewer"]["director"]["enabled"] = True
    answering(monkeypatch, "not json")
    bad = c.post("/api/interview/direct", json={"sid": sid, "id": 2}).json()
    assert bad["changed"] is False and bad["note"] is None and bad["error"].startswith("unreadable look")
    cfg["demo"]["interviewer"]["director"]["max_calls"] = 0
    assert c.post("/api/interview/direct", json={"sid": sid, "id": 3}).json()["error"] == "cap"
    assert c.post("/api/interview/direct", json={"sid": "nope", "id": 4}).status_code == 409
    intro = c.post("/api/interview/start", json={"mode": "intro", "sdp": "OFFER"}).json()
    assert c.post("/api/interview/direct", json={"sid": intro["sid"], "id": 5}).status_code == 400


def test_fates_are_recorded_without_text_and_checked(world):
    got = debrief_started(world)
    sid, c = got["sid"], world.client
    post = lambda **kw: c.post("/api/interview/direct/fate", json={"sid": sid, "id": 1, **kw}).status_code  # noqa: E731
    assert post(fate="sent", secs=12.5) == 200
    assert post(fate="dropped", reason="moved_on", detail="it asked first") == 200
    assert post(fate="lost") == 400 and post(fate="dropped") == 400 and post(fate="dropped", reason="bored") == 400
    assert c.post("/api/interview/direct/fate", json={"sid": "nope", "id": 1, "fate": "sent"}).status_code == 409
    _, recs = rows(world, sid)
    fates = [r for r in recs if r["who"] == "director_fate"]
    assert [r["fate"] for r in fates] == ["sent", "dropped"] and all("text" not in r for r in fates)


def test_a_whisper_is_recognised_and_shown_in_the_earpiece_history(world, monkeypatch):
    got = debrief_started(world)
    sid, c = got["sid"], world.client
    c.post("/api/interview/line", json={"sid": sid, "who": "out", "text": "What's one thing that stood out to you?"})
    c.post("/api/interview/line", json={"sid": sid, "who": "in", "text": "The voice was soothing."})
    answering(monkeypatch, look_json("Ask when the voice felt most soothing to them.", about="follow_up"))
    c.post("/api/interview/direct", json={"sid": sid, "id": 1, "trigger": "turn_end"})
    wrapped = got["notes"]["direct"].replace("{note}", "Ask when the voice felt most soothing to them.")
    assert debrief_earpiece.whisper_in(wrapped) == ("next_turn", "Ask when the voice felt most soothing to them.")
    c.post("/api/interview/line", json={"sid": sid, "who": "page", "text": wrapped})
    c.post("/api/interview/direct/fate", json={"sid": sid, "id": 1, "fate": "sent", "secs": 9})
    c.post("/api/interview/line", json={"sid": sid, "who": "out", "text": "Soothing. When did you notice it most?"})
    listed = EH.conversations(world.dir)
    mine = next(x for x in listed if x["session"] == f"debrief-{sid}")
    assert mine["kind"] == "debrief" and mine["looks"] == 1 and mine["sent"] == 1
    t = EH.timeline(world.dir, f"debrief-{sid}")
    look = next(x for x in t["items"] if x["kind"] == "look")
    assert look["fate"]["fate"] == "sent" and look["delivered"] is not None
    assert look["reply"]["text"] == "Soothing. When did you notice it most?"
    note = next(x for x in t["items"] if x["kind"] == "note" and x["whisper"])
    assert note["look"] == 1
    assert any(x["kind"] == "line" and x["who"] == "interviewer" for x in t["items"])
    try:
        EH.timeline(world.dir, "debrief-../x")
        raise AssertionError("a bad id must not be read")
    except FileNotFoundError:
        pass


def test_the_visit_transcript_still_leaves_the_earpiece_out(world, monkeypatch):
    from demo import visit
    got = debrief_started(world)
    sid, c = got["sid"], world.client
    c.post("/api/interview/line", json={"sid": sid, "who": "in", "text": "It was calm."})
    answering(monkeypatch, look_json("Ask what made it feel calm.", about="follow_up"))
    c.post("/api/interview/direct", json={"sid": sid, "id": 1})
    c.post("/api/interview/line", json={"sid": sid, "who": "page",
                                        "text": got["notes"]["direct"].replace("{note}", "Ask what made it feel calm.")})
    text = visit.text(visit.read(world.dir, "Sam"))
    assert "Earpiece note from Ian" not in text and "Ask what made it feel calm" not in text
    assert text.count("It was calm.") == 1 and "Conversation with the guide" not in text   # a debrief is not a guide conversation


def test_the_prompts_hold_the_ceiling_and_the_debriefs_own_words():
    from stages.common import load_prompt
    system = load_prompt("interviewer_director")
    for want in ("two more questions", "farewell", "never a note", "stand-in", "Never the words \"now\""):
        assert want.lower() in system.lower(), want
    assert set(debrief_earpiece.ABOUT) == {"follow_up", "thread", "moment", "idea", "unanswered", "wrap"}
    assert debrief_earpiece.KINDS[0] == "next_turn"
    shaped = debrief_earpiece.shape(look_json("Ask " + "word " * 40 + ".", about="thread"))
    assert shaped["note"] is None or len(shaped["note"].split()) <= 25
    assert debrief_earpiece.shape(look_json("Ask which moment felt interesting, building on \"pretty interesting\".'",
                                            about="follow_up"))["note"].endswith('interesting".')
    try:
        debrief_earpiece.shape({**look_json("x", about="pitch")})   # a guide move is not an interview move
        raise AssertionError("pitch must be refused")
    except ValueError:
        pass


# --- after the review (2026-11-22) -------------------------------------------------------------------------

def test_lines_the_transcript_has_are_not_merged_in_twice(world, cfg, monkeypatch):
    """The page's recent lines were merged under "guide" against the transcript's "interviewer": nothing matched, so
    every look saw the debrief twice and counted each question twice (wrapping a question early)."""
    got = debrief_started(world)
    sid, c = got["sid"], world.client
    q = "Welcome back, Sam. What's one thing that stood out to you?"
    c.post("/api/interview/line", json={"sid": sid, "who": "out", "text": q})
    c.post("/api/interview/line", json={"sid": sid, "who": "in", "text": "The voice was calm."})
    calls = answering(monkeypatch, look_json(None))
    c.post("/api/interview/direct", json={"sid": sid, "id": 1, "trigger": "turn_end",
                                          "recent": [{"who": "out", "text": q}, {"who": "in", "text": "The voice was calm."}],
                                          "page": {"questions": 1}})
    user = calls[0]["messages"][0]["content"]
    assert user.count("What's one thing that stood out") == 1 and user.count("The voice was calm.") == 1
    assert "Questions the interviewer has asked so far, its opening included: 1." in user
    _, recs = rows(world, sid)
    assert [r for r in recs if r["who"] == "director"][0]["speaking"] == "none"
    calls = answering(monkeypatch, look_json(None))
    c.post("/api/interview/direct", json={"sid": sid, "id": 2, "trigger": "hearing", "hearing": "and the part about"})
    _, recs = rows(world, sid)
    assert [r for r in recs if r["who"] == "director"][1]["speaking"] == "listener"


def test_a_saved_run_without_a_visit_still_has_its_instructions():
    values = IV.values_from("Sam", {"run": None}, [], None)
    values.pop("visit"), values.pop("farewell")
    assert "THEIR VISIT" in IV.instructions("debrief", values)


def test_heard_means_words_heard_and_whole_means_read_to_the_end(cfg, tmp_path, monkeypatch):
    """A cut row with nothing heard (the page logs one whenever a reading stops) is not heard; a part is "heard" only
    when each of its paragraphs was read whole."""
    folder = tmp_path / "t-sam"
    root = a_visit(folder)
    monkeypatch.setattr(debrief, "_paragraphs", lambda r: [{"id": "s1-p01"}, {"id": "s1-p02"}, {"id": "s2-p01"}])
    jsonl(root / "live" / "reading.jsonl", [
        {"id": "s1-p01", "heard": "A sail is a wing"}, {"id": "s1-p02", "heard": "pulled by lift"},
        {"id": "s2-p01", "heard": "", "cut": True}])
    text = debrief.visit_brief(folder, cfg)
    assert '"The Wing on the Water" (heard)' in text and '"The Keel\'s Half" (not heard)' in text
    jsonl(root / "live" / "reading.jsonl", [
        {"id": "s1-p01", "heard": "A sail is a wing"}, {"id": "s1-p02", "heard": "pulled", "cut": True}])
    assert '"The Wing on the Water" (partly heard)' in debrief.visit_brief(folder, cfg)


def test_a_note_keeps_their_quoted_words_and_loses_stray_marks():
    shape = lambda n: debrief_earpiece.shape(look_json(n, about="thread"))["note"]  # noqa: E731
    assert shape('Ask about "the keel part".') == 'Ask about "the keel part".'
    assert shape("Ask what 'kind of slow' meant to them") == "Ask what 'kind of slow' meant to them"
    assert shape('"Ask when it felt slow."') == "Ask when it felt slow."
    assert shape("Ask when it felt slow.'") == "Ask when it felt slow."
