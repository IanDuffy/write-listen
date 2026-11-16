"""Phase 1, milestone 5: the listener's memory (profile, guide identity, seeds, history)."""

import json
import shutil
import threading

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
    resp.memory_delay = 0.5  # a slow pass: the next conversation must wait for it
    sid = c.post("/api/chat/start").json()["session"]
    c.post("/api/chat", json={"message": "I pour concrete for a living", "session": sid})
    assert c.post("/api/chat/end", json={"session": "a-stale-tab"}).json() == {"ended": False}
    assert c.post("/api/chat/end", json={"session": sid}).json() == {"ended": True}
    # Reopened at once: the new conversation opens as a returning one, after the pass.
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


def test_a_pass_without_a_forget_request_keeps_what_was_said(mem, cfg):
    listener, resp = mem
    t = session(listener, "a", TALK)
    memory.after_session(cfg, t)
    assert "Maya" in (listener / "profile.md").read_text()


def test_an_older_conversation_is_not_folded_in_after_a_newer_one(mem, cfg):
    listener, resp = mem
    older = session(listener, "20261112-080000", TALK)
    newer = session(listener, "20261112-090000", TALK + [{"who": "listener", "text": "Forget my daughter."}])
    memory.after_session(cfg, newer)
    assert memory.after_session(cfg, older) is False and resp.memory_calls == 1
    assert "skipped" in older.with_suffix(".done").read_text() and "Maya" not in (listener / "profile.md").read_text()


def test_forgetting_a_chapter_removes_it_from_history_and_seeds(mem, cfg, tmp_path):
    listener, resp = mem
    r = Run.open("concrete-20261112", create=True, runs_dir=tmp_path / "runs")
    r.path("source_dir").mkdir()
    r.path("seed").write_text("# Seed\n")
    r.path("brief").write_text("# Brief: Concrete That Heals Itself\n")
    memory.save_seed(cfg, r.root)
    memory.record_chapter(cfg, r)
    t = session(listener, "20261113-090000", TALK + [{"who": "listener", "text": "Please forget the concrete chapter."}])
    memory.after_session(cfg, t)
    assert "concrete-20261112" not in (listener / "history.md").read_text()
    assert not (listener / "seeds" / "concrete-20261112.md").exists()
    call = [c for c in resp.calls if "You keep the memory of a guide" in all_text(c)][-1]
    assert "The conversation took place on 2026-11-13." in all_text(call)


# --- meeting(): has the guide met this listener? (Overnight Chapters round 2) ---------------------------

def test_meeting_is_first_only_with_no_chapter_no_conversation_and_no_guide_md(mem, cfg):
    listener, _ = mem
    (listener / "profile.md").write_text("- Likes birds.\n")  # a profile alone is not a meeting
    session(listener, "20261112-080000", [{"who": "session"}, {"who": "guide", "text": "Hi."}])  # they never spoke
    (listener / "archive" / "sessions").mkdir(parents=True)
    (listener / "archive" / "sessions" / "20261101-080000.jsonl").write_text(json.dumps({"who": "listener", "text": "old"}) + "\n")
    (listener / "chapters" / "2026-11-13-unfinished" / "00_source").mkdir(parents=True)  # no chapter.mp3
    assert memory.meeting(cfg) == {"first": True, "chapters": 0, "conversations": 0, "guide_name": None,
                                   "last_chapter": None}


def test_meeting_counts_what_the_listener_and_guide_share(mem, cfg):
    listener, _ = mem
    session(listener, "20261112-080000", TALK)
    m = memory.meeting(cfg)
    assert m["first"] is False and m["conversations"] == 1 and m["guide_name"] is None
    (listener / "guide.md").write_text("# The guide\n\n**Name:** Wren\n- Chose it for the bird.\n")
    done = listener / "chapters" / "2026-11-14-herons" / "10_audio"
    done.mkdir(parents=True)
    (done / "chapter.mp3").write_bytes(b"x")
    (listener / "history.md").write_text("# Chapters made so far\n\n## Concrete (concrete-20261112, 2026-11-12)\n\n"
                                         "## Why Herons Wait (2026-11-14-herons, 2026-11-14)\n\n- Angle: patience.\n")
    assert memory.meeting(cfg) == {"first": False, "chapters": 1, "conversations": 1, "guide_name": "Wren",
                                   "last_chapter": "Why Herons Wait"}
    only_guide = mem[0].parent / "other"
    (only_guide / "sessions").mkdir(parents=True)
    (only_guide / "guide.md").write_text("- Name: Juniper.\n")
    cfg["listener_dir"] = str(only_guide)
    assert memory.meeting(cfg)["first"] is False and memory.meeting(cfg)["guide_name"] == "Juniper"


def test_recent_is_the_end_of_the_last_conversation_and_the_latest_chapter(mem, cfg):
    listener, _ = mem
    assert memory.recent(cfg) == {"conversation": "", "when": "", "mode": None, "chapter": ""}
    session(listener, "20261112-080000", TALK)
    lines = [{"who": "session", "mode": "change"}] + [{"who": "listener" if i % 2 else "guide", "text": f"line {i}"}
                                                      for i in range(12)]
    session(listener, "20261114-190000", lines)
    session(listener, "20261115-090000", [{"who": "guide", "text": "unheard"}])  # no listener line: not counted
    (listener / "history.md").write_text("# Chapters made so far\n\n## Why Herons Wait (h, 2026-11-14)\n\n- Angle: x.\n")
    got = memory.recent(cfg, lines=4)
    assert got["conversation"] == "you: line 8\nlistener: line 9\nyou: line 10\nlistener: line 11"
    assert got["when"] == "2026-11-14" and got["mode"] == "change"
    assert got["chapter"].startswith("Why Herons Wait (h, 2026-11-14)")
    assert memory.recent(cfg, exclude="20261114-190000")["when"] == "2026-11-12"


# --- review round 2: recent() never quotes back what was forgotten; a session's own chapter is left out ----

class SaysForgot(Responder):
    """The fake memory pass, plus the "forgot" flag the real one sets when the listener asked to forget."""

    def __call__(self, kwargs):
        out = super().__call__(kwargs)
        t = all_text(kwargs)
        if "You keep the memory of a guide" in t and "forget what" in t.split("<transcript>")[1].lower():
            data = json.loads(out.content[0].text)
            out.content[0].text = json.dumps({**data, "forgot": True})
        return out


def test_recent_never_quotes_a_conversation_in_which_they_asked_to_forget(mem, cfg, monkeypatch):
    """R9: the pass removes it from profile.md, but the raw transcript still holds it; recent() fed it to the
    next session. Not quoted before the pass (an aside never gets one) nor after it, and no earlier
    conversation, which may hold what was forgotten, is quoted in its place."""
    listener, _ = mem
    from fakes import FakeAnthropic
    monkeypatch.setattr(llm, "_client", FakeAnthropic(SaysForgot()))
    session(listener, "20261112-090000", TALK)  # said Maya here
    t = session(listener, "20261113-090000", [{"who": "guide", "text": "Morning."},
                                              {"who": "listener", "text": "Actually, forget what I said about Maya."}])
    assert memory.recent(cfg)["conversation"] == "" and memory.recent(cfg)["when"] == ""  # before its pass
    assert memory.after_session(cfg, t) is True and t.with_suffix(".forgot").exists()
    got = memory.recent(cfg)
    assert got["conversation"] == "" and "Maya" not in json.dumps(got)
    later = session(listener, "20261114-090000", [{"who": "listener", "text": "I forget where I left my keys"}])
    memory.after_session(cfg, later)  # the pass found nothing to forget: quoted again
    assert not later.with_suffix(".forgot").exists()
    assert memory.recent(cfg)["conversation"] == "listener: I forget where I left my keys"


def test_a_forgotten_only_chapter_is_never_named_again(mem, cfg, tmp_path):
    """R9: with history.md emptied by forget_chapters, meeting() fell back to the chapter folder's title."""
    listener, _ = mem
    done = listener / "chapters" / "concrete-20261112"
    (done / "10_audio").mkdir(parents=True)
    (done / "10_audio" / "chapter.mp3").write_bytes(b"x")
    (done / "03_angle.md").write_text("# Angle: Concrete That Heals Itself\n")
    (listener / "history.md").write_text("# Chapters made so far\n\n## Concrete That Heals Itself "
                                         "(concrete-20261112, 2026-11-12)\n\n- Angle: lime.\n")
    t = session(listener, "20261113-090000", TALK + [{"who": "listener", "text": "Please forget the concrete chapter."}])
    memory.after_session(cfg, t)
    m = memory.meeting(cfg)
    assert m["chapters"] == 1 and m["last_chapter"] is None and t.with_suffix(".forgot").exists()
    assert memory.recent(cfg)["chapter"] == ""


def test_meeting_and_recent_leave_out_the_chapter_a_session_is_about(mem, cfg):
    """R8: at Play the latest history entry is the chapter being introduced."""
    listener, _ = mem
    (listener / "history.md").write_text("# Chapters made so far\n\n## Concrete (concrete-20261112, 2026-11-12)\n\n"
                                         "## Why Herons Wait (2026-11-14-herons, 2026-11-14)\n\n- Angle: patience.\n")
    assert memory.meeting(cfg, exclude_run="2026-11-14-herons")["last_chapter"] == "Concrete"
    assert memory.recent(cfg, exclude_run="2026-11-14-herons")["chapter"].startswith("Concrete (concrete-20261112")
    assert memory.recent(cfg)["chapter"].startswith("Why Herons Wait")


def test_recent_keeps_the_newest_lines_within_max_chars(mem, cfg):
    listener, _ = mem
    session(listener, "20261114-190000", [{"who": "listener" if i % 2 else "guide", "text": f"line {i} " + "x" * 90}
                                          for i in range(8)])
    got = memory.recent(cfg, max_chars=250)["conversation"]
    assert len(got) <= 250 and got.endswith("line 7 " + "x" * 90) and "line 5" not in got
    one = memory.recent(cfg, lines=1, max_chars=50)["conversation"]
    assert len(one) == 50 and one.startswith("…") and one.endswith("x" * 10)


def test_a_pass_writes_nothing_once_its_folder_was_moved_or_made_afresh(mem, cfg, tmp_path, monkeypatch):
    """An admin reset moves the folder aside (and makes a fresh one) while the pass is inside its call: the
    pass must not write the old listener's memory into the new folder (review, tester demo)."""
    listener, resp = mem
    real = llm.call
    for n, keep_transcript in enumerate((False, True)):
        t = session(listener, f"20261112-09000{n}", TALK)

        def call(*a, **kw):
            out = real(*a, **kw)
            listener.rename(tmp_path / f"cleared{n}")
            (listener / "sessions").mkdir(parents=True)
            if keep_transcript:  # even a copy of the transcript at the same path: still a different folder
                shutil.copy(tmp_path / f"cleared{n}" / "sessions" / t.name, t)
            return out
        monkeypatch.setattr(llm, "call", call)
        assert memory.after_session(cfg, t) is False
        assert not (listener / "profile.md").exists() and not (listener / "guide.md").exists()
        assert not t.with_suffix(".done").exists()
        assert not (tmp_path / f"cleared{n}" / "sessions" / f"{t.stem}.done").exists()


def test_each_listener_folder_has_its_own_memory_lock(mem, cfg, tmp_path):
    """One tester's pass never waits behind another's (review, tester demo)."""
    listener, resp = mem
    other = tmp_path / "other"
    (other / "sessions").mkdir(parents=True)
    ocfg = {**cfg, "listener_dir": str(other)}
    assert memory.lock_for(cfg) is memory.lock_for({**cfg, "listener_dir": str(listener / ".." / "listener")})
    assert memory.lock_for(cfg) is not memory.lock_for(ocfg)
    t = session(other, "20261112-090000", TALK)
    done = []
    with memory.lock_for(cfg):  # a pass in flight for the first listener
        th = threading.Thread(target=lambda: done.append(memory.after_session(ocfg, t)))
        th.start()
        th.join(10)
    assert done == [True] and "Pours concrete" in (other / "profile.md").read_text()
