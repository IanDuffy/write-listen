"""Phase 1, milestone 2: status.json, the local server and the page."""

import json
import sys
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

import server
from stages import new
from stages.common import Run


class FakeProc:
    def __init__(self, code=None):
        self.returncode = code

    def poll(self):
        return self.returncode


@pytest.fixture
def client(tmp_path, monkeypatch):
    pipe = server.Pipeline(runs_dir=tmp_path)
    started = []

    def start(args, run):  # record the command instead of running the pipeline
        started.append(args)
        pipe.proc, pipe.run = FakeProc(), run

    monkeypatch.setattr(pipe, "start", start)
    return TestClient(server.create_app(pipe, remember_on_start=False)), pipe, started, tmp_path


def write_status(runs, name, **fields):
    (runs / name).mkdir(exist_ok=True)
    (runs / name / "status.json").write_text(json.dumps({"run": name, **fields}))


def test_page_and_idle(client):
    c, _, _, _ = client
    assert "Show transcript" in c.get("/").text
    assert c.get("/api/status").json() == {"state": "idle"}


def test_starting_a_chapter_from_the_text_box(client):
    c, pipe, started, runs = client
    res = c.post("/api/chapters", json={"seed": "Why Roman concrete lasted"})
    assert res.status_code == 200
    name = res.json()["run"]
    assert started == [["new", name, "--unattended"]]
    assert "Why Roman concrete lasted" in (runs / name / "00_source" / "seed.md").read_text()
    assert c.get("/api/status").json()["label"] == "Starting"  # before the pipeline writes status.json
    assert c.post("/api/chapters", json={"seed": "Another"}).status_code == 409  # one at a time
    assert c.post("/api/chapters", json={"seed": "  "}).status_code == 400


def test_the_same_seed_continues_its_run(client):
    c, pipe, started, runs = client
    name = c.post("/api/chapters", json={"seed": "Why Roman concrete lasted"}).json()["run"]
    pipe.proc = FakeProc(1)
    write_status(runs, name, state="problem", message="Something went wrong while researching.")
    assert c.post("/api/chapters", json={"seed": "Why Roman concrete lasted"}).json()["run"] == name
    assert c.post(f"/api/chapters/{name}/retry").status_code == 409  # that run is going again
    pipe.proc = FakeProc(1)
    assert c.post(f"/api/chapters/{name}/retry").json() == {"run": name}
    assert started[-1] == ["new", name, "--unattended"]


def test_a_dead_pid_after_a_restart_shows_as_a_problem_and_a_live_one_as_busy(client, tmp_path):
    import os
    c, pipe, started, runs = client
    write_status(runs, "lesson", state="making", label="Researching", pid=2**22 + 12345)  # no such process
    s = c.get("/api/status").json()
    assert s["state"] == "problem" and s["retryable"] is True  # a fresh server: proc is None
    assert c.post("/api/chapters/lesson/retry").status_code == 400  # no seed in this fake run
    write_status(runs, "cli-run", state="making", label="Writing", pid=os.getpid())  # alive: started elsewhere
    assert c.get("/api/status").json()["run"] == "cli-run"  # the server follows the newest run
    assert c.get("/api/status").json()["state"] == "making"
    assert c.post("/api/chapters", json={"seed": "Something new"}).status_code == 409


def test_runs_start_in_their_own_session(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(server.subprocess, "Popen", lambda cmd, **kw: seen.update(kw) or FakeProc())
    pipe = server.Pipeline(runs_dir=tmp_path)
    (tmp_path / "r").mkdir()
    pipe.start(["new", "r", "--unattended"], "r")
    assert seen["start_new_session"] is True and (tmp_path / "r" / "run.log").exists()


def test_a_crash_shows_as_a_problem(client):
    c, pipe, _, runs = client
    name = c.post("/api/chapters", json={"seed": "Concrete"}).json()["run"]
    write_status(runs, name, state="making", label="Researching")
    assert c.get("/api/status").json()["state"] == "making"
    pipe.proc = FakeProc(1)  # the process died without writing ready or problem
    s = c.get("/api/status").json()
    assert s["state"] == "problem" and s["message"] == "The chapter stopped unexpectedly."


def test_status_after_a_restart_and_audio(client):
    c, pipe, _, runs = client
    write_status(runs, "old", state="ready", title="Old")
    write_status(runs, "lesson", state="ready", title="Concrete that heals")
    assert c.get("/api/status").json()["title"] == "Concrete that heals"  # the newest run
    assert c.get("/api/chapters/lesson/audio").status_code == 404
    (runs / "lesson" / "10_audio").mkdir()
    (runs / "lesson" / "10_audio" / "chapter.mp3").write_bytes(b"ID3fake")
    res = c.get("/api/chapters/lesson/audio")
    assert res.status_code == 200 and res.headers["content-type"] == "audio/mpeg"
    assert c.get("/api/chapters/nope/audio").status_code == 404
    assert c.post("/api/chapters/../retry").status_code in (404, 405)


def test_events_stream_sends_the_status(client):
    c, _, _, runs = client
    write_status(runs, "lesson", state="making", label="Writing")
    with c.stream("GET", "/api/events") as res:
        line = next(x for x in res.iter_lines() if x.startswith("data:"))
    assert json.loads(line[5:])["label"] == "Writing"


def test_server_listens_on_localhost_only():
    assert server.HOST == "127.0.0.1"


def test_status_helpers(run, cfg):
    run.path("source_dir").mkdir()
    run.path("seed").write_text("# Seed\n")
    s = new.write_status(run, cfg, "making", stage="scout", label="Looking into it")
    assert s["stages_done"] == 0 and s["stages_total"] == len(new.STEPS)
    assert s["minutes_left"] == sum(cfg["status_minutes"].values())
    run.path("angle").write_text("# Angle: Concrete that heals\n")
    run.path("map").write_text("m")
    s = new.write_status(run, cfg, "making", stage="angles", label="Choosing an angle")
    assert s["title"] == "Concrete that heals" and s["stages_done"] == 2 and s["started"]
    assert not list(run.root.glob("*.tmp"))  # written atomically
    assert new.plain_problem("Researching", "would pass this run's $20.00 cap (unattended.max_run_usd). Stopped.") == \
        ("The chapter stopped while researching: continuing would pass its spending limit.", False)
    assert new.plain_problem("Researching", "Stopped by the search cap: ...")[1] is False
    assert new.plain_problem("Writing", "API error 500") == ("Something went wrong while writing.", True)
    assert "+00:00" in s["updated"] or "-" in s["updated"][19:]  # an offset with a colon: Safari parses it


# --- stop and reset (Ian, 2026-11-13) ----------------------------------------------------------

STUBBORN = """
import signal, subprocess, sys, time
signal.signal(signal.SIGINT, signal.SIG_IGN)   # a run whose worker threads keep it alive after Ctrl-C
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])  # an ffmpeg it started
open(sys.argv[1], "w").write(str(child.pid))
time.sleep(120)
"""

POLITE = """
import sys, time
try:
    time.sleep(120)
except KeyboardInterrupt:  # what stages/new.py does: record the stop, then end
    open(sys.argv[1], "w").write("interrupted")
"""


def _real_run(tmp_path, runs, name, code):
    import subprocess
    import time
    (runs / name).mkdir()
    flag = tmp_path / f"{name}.flag"
    proc = subprocess.Popen([sys.executable, "-c", code, str(flag)], start_new_session=True)
    deadline = time.monotonic() + 10
    while proc.poll() is None and time.monotonic() < deadline:
        if code is POLITE and proc.poll() is None:
            time.sleep(0.5)  # time to install the handler
            break
        if flag.exists() and flag.read_text().strip():
            break
        time.sleep(0.05)
    write_status(runs, name, state="making", stage="research", label="Researching", pid=proc.pid,
                 stages_done=3, stages_total=15, history=[{"stage": "scout", "seconds": 60}])
    return proc, flag


def test_stop_interrupts_a_run_first(client, tmp_path):
    c, pipe, _, runs = client
    proc, flag = _real_run(tmp_path, runs, "lesson", POLITE)
    pipe.proc, pipe.run = proc, "lesson"
    out = c.post("/api/chapters/lesson/stop").json()
    assert out["stopped"] is True and proc.poll() is not None
    assert flag.read_text() == "interrupted"  # Ctrl-C was enough: the pipeline got to record where it stopped
    s = c.get("/api/status").json()
    assert s["state"] == "stopped" and s["message"] == "You stopped this chapter." and s["pid"] is None
    assert s["history"] == [{"stage": "scout", "seconds": 60}]
    assert "Stopped from the page." in (runs / "lesson" / "decisions.md").read_text()
    assert not pipe.busy()
    assert c.post("/api/chapters", json={"seed": "Something new"}).status_code == 200  # free for the next one


def test_stop_escalates_and_takes_what_the_run_started(client, tmp_path):
    c, pipe, _, runs = client
    proc, flag = _real_run(tmp_path, runs, "stubborn", STUBBORN)
    grandchild = int(flag.read_text())
    assert server.alive(grandchild)
    out = c.post("/api/chapters/stubborn/stop").json()  # started elsewhere: found by the pid in status.json
    assert out["stopped"] is True
    proc.wait(5)
    import time
    for _ in range(50):  # the grandchild's parent is gone, so init reaps it
        if not server.alive(grandchild):
            break
        time.sleep(0.1)
    assert not server.alive(grandchild)  # its process group went with it
    assert c.get("/api/status").json()["state"] == "stopped"


def test_stop_with_nothing_running_and_unknown_runs(client):
    c, pipe, _, runs = client
    write_status(runs, "done", state="ready", title="T")
    out = c.post("/api/chapters/done/stop").json()
    assert out["stopped"] is False and c.get("/api/status").json()["state"] == "ready"  # left as it was
    assert c.post("/api/chapters/nope/stop").status_code == 404


def test_stop_cancels_queued_batches(client, monkeypatch):
    from stages import llm
    c, pipe, _, runs = client
    write_status(runs, "batched", state="making", label="Planning", pid=2**22 + 999)
    (runs / "batched" / "_batch_edit.json").write_text(json.dumps({"batch_id": "msgbatch_1", "label": "edit"}))
    cancelled = []
    fake = NS(messages=NS(batches=NS(cancel=lambda bid: cancelled.append(bid))))
    monkeypatch.setattr(llm, "get_client", lambda: fake)
    out = c.post("/api/chapters/batched/stop").json()
    assert cancelled == ["msgbatch_1"] and out["batches_cancelled"] == ["msgbatch_1"]
    assert c.get("/api/status").json()["state"] == "stopped"  # a dead run that still said making


def test_a_stopped_chapter_reads_plainly_to_the_guide():
    from stages import guide
    assert guide.plain_status({"state": "stopped", "title": "T"}) == {"state": "stopped by the listener", "title": "T"}
    assert "stopped by the listener" in guide.chapter_now({"run": "r", "state": "stopped"})


def test_reset_forgets_the_listener(tmp_path, monkeypatch, cfg):
    from fakes import FakeAnthropic, Responder

    from stages import guide, llm, memory
    listener = tmp_path / "listener"
    cfg["listener_dir"] = str(listener)
    monkeypatch.setattr(llm, "_client", FakeAnthropic(Responder()))
    passes = []
    monkeypatch.setattr(memory, "after_session", lambda cfg, t: passes.append(t) or False)
    runs = tmp_path / "runs"
    runs.mkdir()
    pipe = server.Pipeline(runs_dir=runs)
    c = TestClient(server.create_app(pipe, cfg, remember_on_start=False))
    first = c.post("/api/chat/start").json()["session"]
    (listener / "profile.md").write_text("- Builds his own house.\n")
    (listener / "guide.md").write_text("# The guide\n- Name: Wren.\n")
    (listener / "history.md").write_text("# Chapters made so far\n")
    (listener / "seeds").mkdir()
    (listener / "seeds" / "r.md").write_text("seed")
    (listener / "voice.json").write_text("{}")

    out = c.post("/api/reset").json()
    assert sorted(out["forgotten"]) == ["guide.md", "history.md", "profile.md", "seeds", "sessions"]
    moved = [p for p in listener.iterdir() if p.name.startswith("_cleared_")]
    assert len(moved) == 1 and (moved[0] / "profile.md").exists() and (moved[0] / "sessions" / f"{first}.jsonl").exists()
    assert sorted(p.name for p in listener.iterdir()) == [moved[0].name, "voice.json"]  # the agent's ids stay
    assert passes == []  # the conversation was not remembered on its way out
    assert c.post("/api/chat/end", json={"session": first}).json()["ended"] is False  # it is already over
    assert c.post("/api/chat", json={"message": "hi", "session": first}).status_code == 409

    second = c.post("/api/chat/start").json()["session"]  # a first meeting again
    head = json.loads((listener / "sessions" / f"{second}.jsonl").read_text().splitlines()[0])
    assert head["returning"] is False and len(head["names"]) == cfg["guide"]["name_draw"]
    assert passes == []  # starting afresh did not fold the dropped conversation in either
    assert c.post("/api/reset").json()["forgotten"] == ["sessions"]  # only the new conversation's file


def test_reset_forgets_what_was_said_in_asides(tmp_path, monkeypatch, cfg):
    """The aside agent reads earlier asides back (earlier_asides): Reset memory moves them aside too."""
    import json as _json

    from ask import aside as A
    from stages import voice
    listener = tmp_path / "listener"
    cfg["listener_dir"] = str(listener)
    monkeypatch.setattr(voice, "signed_url", lambda cfg, client=None, kind="guide": "wss://example")
    runs = tmp_path / "runs"
    import ask_fixture
    root = ask_fixture.build(runs / "synthetic")
    A.append_aside(root, {"id": "a1", "t": 10.0, "sentence_id": "seg01-s002", "ended": "closing",
                          "transcript": [{"who": "guide", "text": "Yes?"},
                                         {"who": "listener", "text": "My mother just died."}]})
    (root / "12_asks" / "seeds.md").write_text("# Seeds\n")
    (root / "12_asks" / "a1.voice.json").write_text("{}")
    c = TestClient(server.create_app(server.Pipeline(runs_dir=runs), cfg, remember_on_start=False))
    out = c.post("/api/reset").json()  # no listener memory at all: the asides alone are forgotten
    assert out["forgotten"] == ["asides"]
    moved = listener / out["moved_to"] / "asides" / "synthetic"
    assert sorted(p.name for p in moved.iterdir()) == ["a1.voice.json", "asides.jsonl", "seeds.md"]
    assert A.load_asides(root) == []
    start = c.post("/api/ask/start", json={"run": "synthetic", "t": 20}).json()
    assert start["dynamic_variables"]["earlier_asides"] == "(none)"
    assert c.post("/api/reset").json()["forgotten"] == []  # nothing left to forget
