"""Phase 1, milestone 2: status.json, the local server and the page."""

import json
import os
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


REHEARSAL_CHILD = """
import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from stages import common
common.RUNS_DIR, common.LISTENERS_DIR, common.SHARED_DIR = (Path(x) for x in sys.argv[2:5])
import cli
sys.exit(cli.main(["new", sys.argv[5], "--unattended"]))
"""


def test_stop_a_rehearsal_subprocess(client, tmp_path, cfg):
    """A rehearsal (faststart/rehearsal.py) is a child process like a real run: Pipeline.stop's SIGINT ends it,
    it writes its problem status on the way out, the server's stopped status follows, and nothing is left."""
    import subprocess
    import time
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent / "faststart"))
    from rehearsal_fakes import make_run, make_source
    from faststart import base
    c, pipe, _, runs = client
    src = make_source(tmp_path / "source-chapter")
    cfg["rehearsal"] = {"source": str(src), "sections": None, "opening_seconds": 60, "after_speed": 1.0}
    r = make_run(runs, "replayed", src, cfg, t0=time.time())
    root = Path(server.__file__).resolve().parent
    proc = subprocess.Popen([sys.executable, "-c", REHEARSAL_CHILD, str(root), str(runs), str(tmp_path / "_l"),
                             str(tmp_path / "_s"), r.name], cwd=root, start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            s = json.loads(r.path("status").read_text()) if r.path("status").exists() else {}
            if s.get("state") == "making" and s.get("pid") == proc.pid:
                break
            time.sleep(0.1)
        assert s.get("state") == "making", s
        pipe.proc, pipe.run = proc, r.name
        t0 = time.monotonic()
        assert c.post(f"/api/chapters/{r.name}/stop").json()["stopped"] is True
        assert proc.poll() is not None and time.monotonic() - t0 < 5.5
        assert any(e["event"] == "problem" for e in base.read_timeline(r))  # its own problem status came first
        assert "KeyboardInterrupt" in r.path("decisions").read_text()
        assert c.get("/api/status").json()["state"] == "stopped"
        assert not server.alive(proc.pid)
    finally:
        if proc.poll() is None:
            proc.kill()


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


def test_stop_never_signals_the_server_itself(client):
    """A status.json naming this process (the live page tests' worlds write os.getpid()) must never have stop()
    signal the server: End demo now stops a chapter being made (review, 2026-11-19)."""
    c, pipe, _, runs = client
    write_status(runs, "mine", state="making", label="Researching", pid=os.getpid())
    out = c.post("/api/chapters/mine/stop").json()
    assert out["stopped"] is False and c.get("/api/status").json()["state"] == "stopped"


def test_a_stopped_chapter_reads_plainly_to_the_guide():
    from stages import guide
    assert guide.plain_status({"state": "stopped", "title": "T"}) == {"state": "stopped by the listener", "title": "T"}
    assert "stopped by the listener" in guide.chapter_now({"run": "r", "state": "stopped"})


def _tree(d):
    import hashlib
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir"
            for p in sorted(d.rglob("*"))}


def test_start_fresh_makes_a_new_listener_and_leaves_the_old_one_byte_for_byte(tmp_path, monkeypatch, cfg):
    from fakes import FakeAnthropic, Responder

    from overnight import profiles
    from stages import common, llm, memory
    monkeypatch.setattr(llm, "_client", FakeAnthropic(Responder()))
    passes = []
    monkeypatch.setattr(memory, "after_session", lambda cfg, t: passes.append(t) or False)
    ian = profiles.create("ian")
    profiles.set_active("ian")
    cfg.pop("listener_dir", None)
    pipe = server.Pipeline()  # follows the active listener's chapters
    c = TestClient(server.create_app(pipe, cfg, remember_on_start=False))
    first = c.post("/api/chat/start").json()["session"]
    (ian / "profile.md").write_text("- Builds his own house.\n")
    (ian / "guide.md").write_text("# The guide\n- Name: Wren.\n")
    import ask_fixture
    from ask import aside as A
    root = ask_fixture.build(ian / "chapters" / "2026-11-14-synthetic")
    A.append_aside(root, {"id": "a1", "t": 10.0, "sentence_id": "seg01-s002", "ended": "closing", "transcript": []})
    before = _tree(ian)

    out = c.post("/api/reset").json()
    assert out["previous"] == "ian" and out["listener"].startswith("demo-")
    assert _tree(ian) == before  # nothing in ian/ moved, changed or went
    assert common.active_listener_id() == out["listener"] and pipe.runs_dir == common.LISTENERS_DIR / out["listener"] / "chapters"
    assert passes == []  # the conversation was not remembered on its way out
    assert c.post("/api/chat", json={"message": "hi", "session": first}).status_code == 409

    second = c.post("/api/chat/start").json()["session"]  # a first meeting, in the new listener's folder
    demo = common.LISTENERS_DIR / out["listener"]
    head = json.loads((demo / "sessions" / f"{second}.jsonl").read_text().splitlines()[0])
    assert head["returning"] is False
    assert c.get("/api/ask/chapter/2026-11-14-synthetic").json()["message"] == "There's no such chapter."  # ian's chapters are not the demo's
    again = c.post("/api/reset").json()
    assert again["listener"] != out["listener"] and again["listener"].endswith("-b") and _tree(ian) == before


# --- review fixes (overnight chapters) ---------------------------------------------------------

def test_status_names_the_folder_it_was_read_from(client):
    """Review C17: a migrated copy's status.json named its old run, and the page's audio address 404'd."""
    c, _, _, runs = client
    write_status(runs, "2026-11-14-herons", run="herons-20261114", state="ready", title="Herons")
    assert c.get("/api/status").json()["run"] == "2026-11-14-herons"


def test_overnight_builds_are_not_the_pages_and_cannot_be_retried_as_seed_runs(client):
    """Review C19: the page followed a nightly build, and its Retry ran the seed pipeline over it."""
    import os

    from stages import seed
    c, pipe, started, runs = client
    write_status(runs, "lesson", state="ready", title="Concrete")
    build = runs / "2026-11-18-why-bridges-sway"
    (build / "00_source").mkdir(parents=True)
    (build / "00_source" / "up_next.json").write_text('{"summary": "Bridges."}')
    (build / "00_source" / "seed.md").write_text("Why bridges sway\n")
    write_status(runs, build.name, mode="overnight", state="making", label="Recording", pid=os.getpid())
    assert c.get("/api/status").json()["run"] == "lesson"  # not the build, though it is newer
    assert not pipe.busy()
    res = c.post(f"/api/chapters/{build.name}/retry")
    assert res.status_code == 400 and "wl overnight resume" in res.json()["detail"] and started == []
    write_status(runs, build.name, mode="overnight", state="problem", retryable=True)
    (build / "00_source" / "up_next.json").unlink()  # the status alone says so too
    assert c.get("/api/status").json()["run"] == "lesson"
    assert c.post(f"/api/chapters/{build.name}/retry").status_code == 400 and started == []
    (build / "00_source" / "up_next.json").write_text('{"summary": "Bridges."}')
    assert seed.find_run("Why bridges sway\n", runs) is None  # the seed pipeline never continues one
    assert c.post("/api/chapters", json={"seed": "Roman concrete"}).status_code == 200


def test_start_fresh_detaches_the_previous_listeners_run(tmp_path, monkeypatch, cfg):
    """Review C20: after a reset the new listener's page showed the old listener's run, and 409'd."""
    from overnight import profiles
    from stages import common
    profiles.create("ian")
    profiles.set_active("ian")
    cfg.pop("listener_dir", None)
    pipe = server.Pipeline()
    started = []

    def start(args, run):
        started.append(run)
        pipe.proc, pipe.run = FakeProc(), run  # still running

    monkeypatch.setattr(pipe, "start", start)
    c = TestClient(server.create_app(pipe, cfg, remember_on_start=False))
    ian_run = c.post("/api/chapters", json={"seed": "Japanese joinery"}).json()["run"]
    assert (common.LISTENERS_DIR / "ian" / "chapters" / ian_run).is_dir() and pipe.busy()
    lid = c.post("/api/reset").json()["listener"]
    assert c.get("/api/status").json() == {"state": "idle"} and not pipe.busy()
    demo_run = c.post("/api/chapters", json={"seed": "Roman concrete"}).json()["run"]
    assert (common.LISTENERS_DIR / lid / "chapters" / demo_run).is_dir() and started == [ian_run, demo_run]


# --- End demo stops the chapter (Ian's third run, 2026-11-19) -----------------------------------------

@pytest.fixture
def debrief_app(tmp_path, monkeypatch, own_listeners):
    """A server whose listener is t-sam, its chapters in t-sam's folder; no GPT-Live, no after-end work."""
    from demo import interviewer as IV
    from stages import common
    from voice.gpt_live import session as live_session
    monkeypatch.setenv(common.LISTENER_ENV, "t-sam")
    lis = own_listeners / "t-sam"
    chapters = lis / "chapters"
    chapters.mkdir(parents=True)
    monkeypatch.setattr(live_session, "create_webrtc", lambda cfg, session, sdp, client=None: {"id": "live_1", "sdp": "A"})
    monkeypatch.setattr(IV, "_after_end", lambda *a: None)
    pipe = server.Pipeline(runs_dir=chapters)
    return NS(client=TestClient(server.create_app(pipe, remember_on_start=False)), pipe=pipe, lis=lis, chapters=chapters)


def _debrief(app):
    sid = app.client.post("/api/interview/start", json={"mode": "debrief", "sdp": "OFFER"}).json()["sid"]
    for t in list(server.STOPS):
        t.join(20)
    return json.loads((app.lis / "study" / f"debrief-{sid}.jsonl").read_text().splitlines()[0])


def test_end_demo_stops_the_chapter_being_made_as_the_debrief_starts(debrief_app, tmp_path):
    """Ian's third run: "We definately need to stop the run of the chapter once the post inverview starts, if the
    user clicks stop demo." The stop runs on the side; the debrief is told the piece was stopped."""
    from demo import debrief, interviewer as IV
    app = debrief_app
    proc, flag = _real_run(tmp_path, app.chapters, "lesson", POLITE)
    app.pipe.proc, app.pipe.run = proc, "lesson"
    debrief.add_event(app.lis, "end_demo", "reading")
    first = _debrief(app)
    assert proc.poll() is not None and flag.read_text() == "interrupted"
    s = json.loads((app.chapters / "lesson" / "status.json").read_text())
    assert s["state"] == "stopped" and s["message"] == "Stopped when the demo ended." and s["pid"] is None
    assert "End demo" in (app.chapters / "lesson" / "decisions.md").read_text()
    assert first["did"].splitlines()[-1] == "- " + IV.notes()["did_stopped"]


def test_the_chapters_own_end_stops_nothing(debrief_app, tmp_path):
    """The chapter's end hands back with no End demo press: nothing is stopped, and neither after a press made
    once the piece had finished."""
    from demo import debrief, interviewer as IV
    app = debrief_app
    proc, flag = _real_run(tmp_path, app.chapters, "lesson", POLITE)
    app.pipe.proc, app.pipe.run = proc, "lesson"
    try:
        first = _debrief(app)
        debrief.add_event(app.lis, "end_demo", "ended")
        second = _debrief(app)
        assert proc.poll() is None and not (flag.exists() and flag.read_text().strip())
        assert json.loads((app.chapters / "lesson" / "status.json").read_text())["state"] == "making"
        assert IV.notes()["did_stopped"] not in first["did"] + second["did"]
    finally:
        proc.kill()
        proc.wait()


def test_the_real_server_fetches_missing_interviewer_recordings_at_start(tmp_path, monkeypatch):
    """Ian's third run: the debrief's recording was lost when the server stopped inside the two minutes it waits.
    The real server (remember_on_start) catches up at start; an imported module or a test app does not."""
    calls = []
    monkeypatch.setattr(server.demo_interviewer, "fetch_pending", lambda cfg: calls.append(cfg))
    server.create_app(server.Pipeline(runs_dir=tmp_path), remember_on_start=False)
    assert calls == []
    server.create_app(server.Pipeline(runs_dir=tmp_path))
    assert len(calls) == 1
