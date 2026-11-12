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
