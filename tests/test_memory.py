"""Phase 1, milestone 5: the listener's memory (profile, guide identity, seeds, history)."""

import json

import pytest
from fakes import FakeAnthropic, Responder, all_text

from stages import llm, memory
from stages.common import Run


@pytest.fixture
def mem(tmp_path, cfg, monkeypatch):
    listener = tmp_path / "listener"
    (listener / "sessions").mkdir(parents=True)
    cfg["listener_dir"] = str(listener)
    resp = Responder()
    monkeypatch.setattr(llm, "_client", FakeAnthropic(resp))
    return listener, resp


def session(listener, name, lines):
    p = listener / "sessions" / f"{name}.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return p


TALK = [{"who": "session", "names": ["Wren"]}, {"who": "page", "text": "[The listener has opened the page.]"},
        {"who": "guide", "text": "Hello, I'm Wren."}, {"who": "listener", "text": "I pour concrete. My daughter is Maya."},
        {"who": "tool", "name": "start_chapter", "input": {"topic": "concrete"}, "result": {"started": True}}]


def test_after_session_writes_profile_and_guide_once(mem, cfg):
    listener, resp = mem
    t = session(listener, "20261112-090000", TALK)
    assert memory.after_session(cfg, t) is True
    assert "Pours concrete for a living." in (listener / "profile.md").read_text()
    assert "Wren" in (listener / "guide.md").read_text()
    call = [c for c in resp.calls if "You keep the memory of a guide" in all_text(c)][0]
    assert call["model"] == cfg["models"]["writer"] and call["output_config"]["format"]["type"] == "json_schema"
    assert "listener: I pour concrete. My daughter is Maya." in all_text(call)
    assert "tool start_chapter:" in all_text(call)
    assert memory.after_session(cfg, t) is False  # each conversation is passed once
    assert resp.memory_calls == 1
    log = Run(name="sessions", root=listener / "sessions").read_log()
    assert log and log[0]["stage"] == "memory" and log[0]["session"] == t.stem


def test_forget_is_honoured_and_silent_sessions_are_skipped(mem, cfg):
    listener, resp = mem
    (listener / "profile.md").write_text("# What the listener has said\n- Has a daughter called Maya.\n")
    t = session(listener, "a", TALK + [{"who": "listener", "text": "Please forget about my daughter."}])
    memory.after_session(cfg, t)
    assert "Maya" not in (listener / "profile.md").read_text()
    quiet = session(listener, "b", TALK[:3])  # the listener never spoke
    assert memory.after_session(cfg, quiet) is False and memory.pending_sessions(cfg) == []


def test_pending_sessions_catch_up(mem, cfg):
    listener, resp = mem
    session(listener, "a", TALK)
    session(listener, "b", TALK)
    assert [p.stem for p in memory.pending_sessions(cfg)] == ["a", "b"]
    assert memory.run_pending(cfg) == 2 and memory.pending_sessions(cfg) == []


def test_history_and_seeds(mem, cfg, tmp_path):
    listener, _ = mem
    r = Run.open("concrete-20261112", create=True, runs_dir=tmp_path / "runs")
    r.path("source_dir").mkdir()
    r.path("seed").write_text("# Seed\n\nWhy Roman concrete lasted\n")
    r.path("angle").write_text("# Angle: The harbor that heals itself\n\nLime clasts let Roman concrete mend.\n")
    r.path("brief").write_text("# Brief: Concrete That Heals Itself\n")
    r.path("ledger").write_text(json.dumps({"rows": [], "parked": ["Pozzolana trade routes", {"idea": "Pantheon dome"}]}))
    memory.save_seed(cfg, r.root)
    assert (listener / "seeds" / "concrete-20261112.md").read_text().startswith("# Seed")
    memory.record_chapter(cfg, r)
    memory.record_chapter(cfg, r)  # once per run
    h = (listener / "history.md").read_text()
    assert h.count("## Concrete That Heals Itself (concrete-20261112,") == 1
    assert "- Angle: Lime clasts let Roman concrete mend." in h
    assert "Pozzolana trade routes; Pantheon dome" in h


def test_the_server_remembers_when_a_conversation_ends(mem, cfg, tmp_path):
    import time as _t

    from fastapi.testclient import TestClient

    import server
    listener, resp = mem
    pipe = server.Pipeline(runs_dir=tmp_path / "runs")
    (tmp_path / "runs").mkdir()
    c = TestClient(server.create_app(pipe, cfg, remember_on_start=False))
    c.post("/api/chat/start")
    c.post("/api/chat", json={"message": "I pour concrete for a living"})
    assert c.post("/api/chat/end").json() == {"ended": True}
    for _ in range(50):  # the pass runs in the background
        if (listener / "profile.md").exists():
            break
        _t.sleep(0.05)
    assert "Pours concrete" in (listener / "profile.md").read_text()
    # A new conversation then opens as a returning one: guide.md is its identity.
    c.post("/api/chat/start")
    system = all_text([x for x in resp.calls if "You are a guide. You make chapters" in all_text(x)][-1])
    assert "Chose the name for the bird" in system and "(none: this is your first meeting)" not in system
    assert "Pours concrete for a living." in system


def test_a_restarted_server_catches_up_on_memory(mem, cfg, tmp_path):
    import time as _t

    import server
    listener, _ = mem
    session(listener, "20261112-080000", TALK)
    server.create_app(server.Pipeline(runs_dir=tmp_path), cfg)  # remember_on_start
    for _ in range(50):
        if (listener / "profile.md").exists():
            break
        _t.sleep(0.05)
    assert (listener / "sessions" / "20261112-080000.done").exists()
