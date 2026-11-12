"""Phase 1, milestone 3: the guide's conversation in text, its three tools, and the chat endpoints."""

import json
import random

import pytest
from fakes import FakeAnthropic, Responder, all_text
from fastapi.testclient import TestClient

import server
from stages import common, guide, llm
from stages.common import Run, StageError


@pytest.fixture
def world(tmp_path, cfg, monkeypatch):
    listener = tmp_path / "listener"
    listener.mkdir()
    (listener / "profile.md").write_text("- Pours concrete for a living.\n")
    cfg["listener_dir"] = str(listener)
    runs = tmp_path / "runs"
    runs.mkdir()
    resp = Responder()
    monkeypatch.setattr(llm, "_client", FakeAnthropic(resp))
    started, status = [], {"value": {"state": "idle"}}

    def start(topic, why, notes):
        started.append((topic, why, notes))
        r = Run.open("lesson", create=True, runs_dir=runs)
        r.path("source_dir").mkdir(exist_ok=True)
        r.path("seed").write_text("# Seed\n\n## Topic, in the listener's words\n\n" + topic + "\n")
        status["value"] = {"run": "lesson", "state": "making", "label": "Researching", "minutes_left": 12}
        return "lesson"

    def session():
        return guide.Session(cfg, start, lambda: status["value"], runs, rng=random.Random(1))
    return session, resp, started, status, runs, listener


def guide_calls(resp):
    return [c for c in resp.calls if "You are a guide. You make chapters" in all_text(c)]


def test_opening_first_meeting(world, cfg):
    session, resp, _, _, _, listener = world
    s = session()
    assert s.say(None).startswith("Hello, I'm Wren")
    (call,) = guide_calls(resp)
    assert call["model"] == cfg["models"]["guide"] == "claude-sonnet-5-5"
    assert [t["name"] for t in call["tools"]] == ["start_chapter", "add_note", "get_status"]
    system = all_text(call)
    assert "(none: this is your first meeting)" in system and "Pours concrete for a living." in system
    names = json.loads((listener / "sessions" / f"{s.id}.jsonl").read_text().splitlines()[0])["names"]
    assert len(names) == cfg["guide"]["name_draw"] and all(n in system for n in names)
    assert call["messages"][0]["content"] == "[The listener has opened the page.]"
    lines = [json.loads(x) for x in s.transcript.read_text().splitlines()]
    assert [x["who"] for x in lines] == ["session", "page", "guide"]
    assert all(rec["session"] == s.id for rec in s.log_run.read_log())


def test_yes_starts_the_chapter_and_the_guide_says_so(world):
    session, resp, started, _, _, _ = world
    s = session()
    s.say(None)
    s.say("I pour concrete for a living")
    reply = s.say("yes, make it")
    assert started == [("Why Roman concrete lasted", "I pour concrete for a living", "")]
    assert reply == "It's under way. About 25 minutes; I'll let you know."
    last = guide_calls(resp)[-1]["messages"]
    assert last[-1]["content"][0]["type"] == "tool_result"
    assert json.loads(last[-1]["content"][0]["content"])["started"] is True
    assert last[-2]["role"] == "assistant"  # the tool call went back with the result
    tools = [json.loads(x) for x in s.transcript.read_text().splitlines() if '"tool"' in x]
    assert tools[0]["name"] == "start_chapter" and tools[0]["result"]["started"] is True


def test_a_refused_start_is_reported_to_the_guide(world, cfg):
    session, resp, _, status, runs, _ = world

    def busy(topic, why, notes):
        raise server.HTTPException(409, "A chapter is already being made.")

    s = guide.Session(cfg, busy, lambda: status["value"], runs)
    assert s.say("yes") == "I couldn't start it: A chapter is already being made."


def test_status_and_notes_reach_the_run(world, cfg):
    session, _, _, status, runs, _ = world
    s = session()
    assert s.say("also mention the harbor at Caesarea")  # nothing started yet
    assert "start_chapter's notes" in json.loads(s.messages[-2]["content"][0]["content"])["reason"]
    s.say("yes")
    assert s.say("how's it going?") == "It's Researching now."
    s.say("also mention the harbor at Caesarea")
    r = Run.open("lesson", runs_dir=runs)
    notes = r.path("notes").read_text()
    assert notes.startswith("## Added during the conversation") and "- mention the harbor at Caesarea" in notes
    seed = common.seed_inputs(r, cfg)["seed"]
    assert "Why Roman concrete lasted" in seed and "mention the harbor at Caesarea" in seed
    r.path("brief").write_text("# Brief: x\n")  # once the brief exists, a note can't change the plan
    assert "already planned" in s.add_note("one more")["effect"]


def test_returning_listener_keeps_the_guides_identity(world):
    session, resp, _, _, _, listener = world
    (listener / "guide.md").write_text("Name: Wren. Chose it for the bird that sings in winter.\n")
    s = session()
    s.say(None)
    system = all_text(guide_calls(resp)[-1])
    assert "Chose it for the bird" in system and "(you already have a name: see above)" in system


def test_limits(world, cfg, monkeypatch):
    session, resp, _, _, _, _ = world
    s = session()
    cfg["guide"]["max_tool_rounds"] = 0
    with pytest.raises(StageError, match="too many tool calls"):
        s.say("how's it going?")
    cfg["guide"]["max_session_usd"] = 0.0
    with pytest.raises(StageError, match="limit"):
        s.say("hello")


@pytest.fixture
def client(world, cfg, monkeypatch):
    _, resp, _, _, runs, _ = world
    pipe = server.Pipeline(runs_dir=runs)
    started = []

    def start(args, run):
        started.append(args)
        pipe.proc, pipe.run = type("P", (), {"returncode": None, "poll": lambda self: None})(), run

    monkeypatch.setattr(pipe, "start", start)
    return TestClient(server.create_app(pipe, cfg)), started, runs


def test_chat_endpoints_start_a_real_seed_run(client):
    c, started, runs = client
    assert c.post("/api/chat/start").json()["reply"].startswith("Hello, I'm Wren")
    assert "Caesarea" in c.post("/api/chat", json={"message": "I pour concrete"}).json()["reply"]
    assert c.post("/api/chat", json={"message": "yes please"}).json()["reply"].startswith("It's under way")
    (args,) = started
    seed = (runs / args[1] / "00_source" / "seed.md").read_text()
    assert "Why Roman concrete lasted" in seed and "I pour concrete for a living" in seed
    assert c.post("/api/chat", json={"message": "  "}).status_code == 400


def test_chat_says_plainly_when_the_guide_cannot_talk(client, monkeypatch):
    c, _, _ = client

    def no_key():
        raise StageError("No Anthropic API key.")

    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(llm, "get_client", no_key)
    res = c.post("/api/chat/start")
    assert res.status_code == 503 and res.json()["detail"] == "The guide can't talk right now: No Anthropic API key."


def test_plain_status():
    assert guide.plain_status({"state": "making", "label": "Writing", "minutes_left": 7, "title": "T"}) == \
        {"state": "being made", "stage": "Writing", "minutes_left": 7, "title": "T"}
    assert guide.plain_status({"state": "problem", "message": "It stopped."})["problem"] == "It stopped."
