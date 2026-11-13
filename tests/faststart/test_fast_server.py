"""Fast Start milestone 5, the server's half (server.py, faststart/routes.py): the guide's chapters
start fast and the text box's overnight, a seed's run is never handed to the other mode, the page's
position reports reach the run, and the early scout never makes the server busy. Offline: the
pipeline is never started (the command is recorded), the model and the voice are fakes."""

import json
import threading
import time
from types import SimpleNamespace as NS

import pytest
from fakes import FakeAnthropic, Responder
from fast_run_fakes import no_leaks, offline, settings, world  # noqa: F401
from fastapi.testclient import TestClient

import server
from faststart import base, notes, routes, scout
from faststart import run as fast_run
from stages import guide, llm, voice
from stages.common import Run

TOPIC = {"topic": "Why Roman concrete lasted", "listener_said": "I pour concrete for a living", "why": "", "notes": ""}


class FakeProc:
    def __init__(self, code=None):
        self.returncode = code

    def poll(self):
        return self.returncode


@pytest.fixture
def app(tmp_path, cfg, monkeypatch):
    cfg = settings(cfg, tmp_path)  # the listener's memory and the timings file: in the temp folder
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(llm, "_client", FakeAnthropic(Responder()))
    monkeypatch.setattr(voice, "signed_url", lambda cfg, client=None, kind="guide": "wss://example/signed")
    pipe = server.Pipeline(runs_dir=runs)
    started = []

    def start(args, run):  # record the command instead of running the pipeline
        started.append(args)
        pipe.proc, pipe.run = FakeProc(), run

    monkeypatch.setattr(pipe, "start", start)
    return NS(c=TestClient(server.create_app(pipe, cfg, remember_on_start=False)), pipe=pipe, started=started,
              runs=runs, cfg=cfg)


def stop(app, name, **status):
    """The run's process has ended, leaving this status."""
    app.pipe.proc = FakeProc(1)
    (app.runs / name / "status.json").write_text(json.dumps({"run": name, **status}), encoding="utf-8")


def voice_tool(app, sid, name, args):
    return app.c.post("/api/voice/tool", json={"session": sid, "name": name, "args": args}).json()


# --- which mode a chapter starts in -----------------------------------------------------------

def test_the_guide_starts_a_chapter_fast_by_voice(app):
    sid = app.c.post("/api/voice/start").json()["session"]
    out = voice_tool(app, sid, "start_chapter", TOPIC)
    (name,) = [p.name for p in app.runs.iterdir()]
    assert app.started == [["new", name, "--unattended", "--mode", "fast"]]
    assert base.is_fast(Run.open(name, runs_dir=app.runs))  # marked when the seed is set, before the process is up
    # no time: this machine has measured no fast run yet (the timings file is the temp folder's)
    assert out["started"] is True and "minutes" not in out and "first_audio_minutes" not in out
    assert "a sentence at most" in out["while_waiting"]
    assert (guide.listener_dir(app.cfg) / "seeds" / f"{name}.md").exists()  # the memory's copy, as before


def test_the_guide_starts_a_chapter_fast_in_the_typed_chat(app):
    sid = app.c.post("/api/chat/start").json()["session"]
    app.c.post("/api/chat", json={"message": "I pour concrete for a living", "session": sid})
    assert app.c.post("/api/chat", json={"message": "yes, make it", "session": sid}).status_code == 200
    (name,) = [p.name for p in app.runs.iterdir()]
    assert app.started == [["new", name, "--unattended", "--mode", "fast"]]


def test_the_text_box_starts_overnight_unless_the_request_says_fast(app):
    name = app.c.post("/api/chapters", json={"seed": "Why Roman concrete lasted"}).json()["run"]
    assert app.started == [["new", name, "--unattended"]] and not (app.runs / name / "fast.json").exists()
    stop(app, name, state="problem", message="It stopped.")
    other = app.c.post("/api/chapters", json={"seed": "Octopus minds", "mode": "fast"}).json()["run"]
    assert app.started[-1] == ["new", other, "--unattended", "--mode", "fast"]
    assert (app.runs / other / "fast.json").exists()
    assert app.c.post("/api/chapters", json={"seed": "Tides", "mode": "sideways"}).status_code == 422


def test_a_seed_lookup_on_the_server_never_hands_a_run_to_the_other_mode(app):
    post = lambda **more: app.c.post("/api/chapters", json={"seed": "Why Roman concrete lasted", **more}).json()["run"]
    night = post()
    stop(app, night, state="problem", message="It stopped while researching.")  # the overnight pipeline worked in it
    fast = post(mode="fast")
    assert fast != night and app.started[-1] == ["new", fast, "--unattended", "--mode", "fast"]
    assert not (app.runs / night / "fast.json").exists()
    stop(app, fast, state="problem", message="It stopped.", mode="fast")

    # each mode continues its own run, though the other's is the newer folder
    assert post() == night and app.started[-1] == ["new", night, "--unattended"]
    app.pipe.proc = FakeProc(1)
    assert post(mode="fast") == fast and app.started[-1] == ["new", fast, "--unattended", "--mode", "fast"]
    app.pipe.proc = FakeProc(1)
    assert sorted(p.name for p in app.runs.iterdir()) == sorted([night, fast])

    # Try again needs no mode: cli.py continues a run marked fast as one
    stop(app, fast, state="problem", message="It stopped.", mode="fast")
    assert app.c.post(f"/api/chapters/{fast}/retry").json() == {"run": fast}
    assert app.started[-1] == ["new", fast, "--unattended"] and base.is_fast(Run.open(fast, runs_dir=app.runs))


def test_a_folder_that_holds_only_its_seed_goes_to_whichever_mode_asks_first(app):
    name = app.c.post("/api/chapters", json={"seed": "Why Roman concrete lasted"}).json()["run"]
    app.pipe.proc = FakeProc(1)  # the process died before it wrote anything: status, log and map are all missing
    again = app.c.post("/api/chapters", json={"seed": "Why Roman concrete lasted", "mode": "fast"}).json()["run"]
    assert again == name and base.is_fast(Run.open(name, runs_dir=app.runs))
    app.pipe.proc = FakeProc(1)
    third = app.c.post("/api/chapters", json={"seed": "Why Roman concrete lasted"}).json()["run"]
    assert third != name and app.started[-1] == ["new", third, "--unattended"]  # now it is the fast run's


# --- the page's position reports ----------------------------------------------------------------

def fast_folder(app, name="lesson"):
    r = Run.open(name, create=True, runs_dir=app.runs)
    base.mark_fast(r)
    return r


def test_a_position_report_is_written_as_the_run_reads_it(app):
    r = fast_folder(app)
    waiting = {"run": r.name, "state": "waiting", "section": 3, "piece": 0, "t": 0, "chapter_t": 300.5,
               "waiting_for": 3, "since": time.time() - 14, "reason": "skip"}
    before = time.time()
    assert app.c.post("/api/fast/position", json=waiting).json() == {"ok": True}
    raw = json.loads(base.path(r, "player").read_text(encoding="utf-8"))
    assert before <= raw["at"] <= time.time() and "run" not in raw
    report, age = fast_run.page_report(raw, now=50.0, wall=raw["at"] + 4)
    assert age == 4.0 and report["since"] == pytest.approx(50.0 - 18, abs=0.5)  # the wait began 18 s before `wall`
    assert {k: report[k] for k in ("state", "section", "piece", "t", "chapter_t", "waiting_for", "reason")} == \
        {"state": "waiting", "section": 3, "piece": 0, "t": 0, "chapter_t": 300.5, "waiting_for": 3, "reason": "skip"}
    assert set(report) <= set(fast_run.REPORT_KEYS)

    # a report that is not a wait carries none of a wait's fields, whatever the page sent
    playing = {**waiting, "state": "playing", "t": 12.5}
    assert app.c.post("/api/fast/position", json=playing).status_code == 200
    raw = json.loads(base.path(r, "player").read_text(encoding="utf-8"))
    assert (raw["state"], raw["t"], raw["waiting_for"], raw["since"], raw["reason"]) == ("playing", 12.5, None, None, None)
    assert sorted(p.name for p in base.path(r, "work").iterdir()) == ["player.json"]  # written whole, no temp file left


def test_a_position_report_is_refused_for_a_run_that_is_not_a_fast_one(app):
    report = {"state": "playing", "section": 1, "piece": 0, "t": 1.0, "chapter_t": 1.0}
    assert app.c.post("/api/fast/position", json={"run": "nowhere", **report}).status_code == 404
    Run.open("overnight", create=True, runs_dir=app.runs)
    assert app.c.post("/api/fast/position", json={"run": "overnight", **report}).status_code == 404
    assert not (app.runs / "overnight" / "fast").exists()
    outside = app.runs.parent / "outside"
    outside.mkdir()
    (outside / "fast.json").write_text("{}")  # a fast folder, but not inside the runs folder
    for name in ("../outside", "..", "/etc", "a/b", ""):
        assert app.c.post("/api/fast/position", json={"run": name, **report}).status_code == 404, name
    assert not (outside / "fast").exists()
    r = fast_folder(app)
    for bad in ({"state": "dancing"}, {"section": "two"}, {"t": "soon"}, {"reason": "bored"}, {"since": [1]}):
        assert app.c.post("/api/fast/position", json={"run": r.name, **report, **bad}).status_code == 422, bad
    assert not base.path(r, "player").exists()
    assert app.c.post("/api/fast/position", json={"run": r.name, "state": "idle"}).json() == {"ok": True}


def test_the_run_takes_the_listeners_position_from_what_the_route_wrote(tmp_path, cfg, monkeypatch):
    """The whole path: the page's report -> the server -> fast/player.json -> the run's loop."""
    w = world(tmp_path, cfg, monkeypatch, name="listening")
    base.mark_fast(w.r)  # as start_seed does for a chapter the guide starts
    pipe = server.Pipeline(runs_dir=w.r.root.parent)
    c = TestClient(server.create_app(pipe, w.cfg, remember_on_start=False))
    report = {"run": w.r.name, "state": "playing", "section": 1, "piece": 0, "t": 0.4, "chapter_t": 0.4}
    assert c.post("/api/fast/position", json=report).json() == {"ok": True}
    fast_run.run(w.r, w.cfg, episode=False)
    status = json.loads(w.r.path("status").read_text(encoding="utf-8"))
    assert [s["state"] for s in status["sections"]] == ["playing", "ready", "ready", "ready"]
    assert any(e["event"] == "playback_start" for e in base.read_timeline(w.r))


def test_fast_js_is_served_with_the_page_settings(app, tmp_path, monkeypatch):
    js = tmp_path / "fast.js"
    monkeypatch.setattr(routes, "PAGE_JS", js)
    assert app.c.get("/fast.js").status_code == 404  # until the page's script is there
    js.write_text("window.FastSettings = __FAST_SETTINGS__;\n")
    res = app.c.get("/fast.js")
    assert res.headers["content-type"].startswith("text/javascript")
    got = json.loads(res.text.split(" = ", 1)[1].rstrip(";\n"))
    f = app.cfg["fast"]
    assert got == {"position_seconds": f["position_seconds"], "short_wait_seconds": f["short_wait_seconds"],
                   "silence_seconds": f["silence_seconds"], "gap_mic": f["gap_mic"], **f["page"]}
    assert got["gap_mic"] == "auto" and got["section_gap_ms"] == 1000 and got["decision_wait_seconds"] == 20


# --- scout_topic through the server -------------------------------------------------------------

def test_scout_topic_returns_at_once_and_never_makes_the_server_busy(app, monkeypatch):
    go, seen = threading.Event(), []

    def early(cfg, runs_dir, topic):  # the paid search, held until the test lets it go
        seen.append((runs_dir, topic))
        go.wait(5)
        return runs_dir / "_scouts" / "x"

    monkeypatch.setattr(scout, "early", early)
    sid = app.c.post("/api/voice/start").json()["session"]
    t0 = time.monotonic()
    out = voice_tool(app, sid, "scout_topic", {"topic": "Roman  sea concrete"})
    assert out["scouting"] is True and time.monotonic() - t0 < 2  # the search is still held
    assert app.pipe.busy() is False and app.pipe.proc is None and app.started == []
    assert app.c.get("/api/status").json() == {"state": "idle"}  # nothing for the page to follow

    # the chapter starts while the scout is still searching
    assert voice_tool(app, sid, "start_chapter", TOPIC)["started"] is True
    assert app.started[-1][-2:] == ["--mode", "fast"]
    assert voice_tool(app, sid, "scout_topic", {"topic": "Octopus minds"}) == \
        {"scouting": False, "reason": "A chapter is already being made."}
    go.set()
    for t in threading.enumerate():
        if t.name == "early-scout":
            t.join(5)
    assert seen == [(app.runs, "Roman sea concrete")]
    # the tool call is in the conversation's transcript, like any other
    lines = [json.loads(x) for x in (guide.listener_dir(app.cfg) / "sessions" / f"{sid}.jsonl").read_text().splitlines()]
    assert [x["name"] for x in lines if x["who"] == "tool"] == ["scout_topic", "start_chapter", "scout_topic"]


def test_a_note_by_voice_in_a_fast_run_goes_to_listener_notes_with_the_section(app):
    sid = app.c.post("/api/voice/start").json()["session"]
    voice_tool(app, sid, "start_chapter", TOPIC)
    (name,) = [p.name for p in app.runs.iterdir()]
    assert voice_tool(app, sid, "add_note", {"note": "My grandfather was a mason in Naples.", "kind": "connection"}) == \
        {"added": True, "kind": "connection", "effect": "It can shape the parts of the chapter not yet written."}
    app.c.post("/api/fast/position", json={"run": name, "state": "paused", "section": 2, "piece": 1, "t": 3.0})
    voice_tool(app, sid, "add_note", {"note": "I lost the thread at the lime clasts.", "kind": "unclear"})
    saved = notes.load(app.runs / name)
    assert [(n["section"], n["kind"], n["text"]) for n in saved] == [
        (0, "connection", "My grandfather was a mason in Naples."),
        (2, "unclear", "I lost the thread at the lime clasts.")]
    assert not (app.runs / name / "00_source" / "notes.md").exists()  # one file only: no stage reads a note twice
