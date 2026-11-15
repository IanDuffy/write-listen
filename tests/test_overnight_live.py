"""overnight/live.py's session bookkeeping, with GPT-Live's session opening faked and its clock replaced:
the billed seconds, stale sessions, an aside's seeds and questions into seeds.jsonl, a replan that fails,
and the conversation recordings."""

import json
import time as real_time
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient
from overnight_plan_fakes import offline  # noqa: F401

import server
from overnight import library, live, planner, profiles, schedule
from stages import memory


@pytest.fixture
def app(cfg, monkeypatch, own_listeners):
    cfg.pop("listener_dir", None)
    listener = profiles.create("ian")
    profiles.set_active("ian")
    monkeypatch.setattr(live, "create_webrtc", lambda cfg, session, sdp, client=None: {"id": "live_1", "sdp": "answer"})
    passes = []
    monkeypatch.setattr(memory, "after_session", lambda cfg, path: passes.append(path) or False)
    clock = [1000.0]
    monkeypatch.setattr(live, "time", NS(monotonic=lambda: clock[0], strftime=real_time.strftime))
    c = TestClient(server.create_app(server.Pipeline(), cfg, remember_on_start=False))
    return NS(c=c, listener=listener, passes=passes, clock=clock, cfg=cfg)


def log(app):
    return [json.loads(x) for x in (app.listener / "sessions" / "log.jsonl").read_text().splitlines()]


def chapter(app):
    import ask_fixture
    root = app.listener / "chapters" / "2026-11-17-why-herons-wait"
    ask_fixture.build(root, audio=True)
    return root


def test_the_billed_seconds_are_the_wall_clock_when_the_page_reports_less(app):
    """S2: the page's last usage figure lags (its session.closed never arrives), so the server's clock counts."""
    s = app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"}).json()
    app.clock[0] += 70
    end = app.c.post("/api/overnight/live/end", json={"session": s["session"], "seconds": 40}).json()
    assert end["cost_usd"] == pytest.approx(70 / 60 * 0.05, abs=1e-5)
    assert log(app)[-1]["seconds"] == 70 and log(app)[-1]["page_seconds"] == 40
    s = app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"}).json()
    app.clock[0] += 5
    app.c.post("/api/overnight/live/end", json={"session": s["session"], "seconds": 9})
    assert log(app)[-1]["seconds"] == 9  # the page's figure when it is the larger


def test_a_session_never_ended_is_ended_when_the_next_starts(app):
    """C13: a closed tab whose beacon was lost: logged up to its last line once quiet for stale_minutes."""
    a = app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"}).json()
    app.clock[0] += 10
    app.c.post("/api/overnight/live/line", json={"session": a["session"], "who": "listener", "text": "Something on tides"})
    app.clock[0] += 29 * 60
    app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"})
    assert not (app.listener / "sessions" / "log.jsonl").exists()  # quiet, not yet stale
    app.clock[0] += 2 * 60
    app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"})
    ended = [x for x in log(app) if x["session"] == a["session"]]
    assert len(ended) == 1 and ended[0]["ended"] == "stale" and ended[0]["seconds"] == 10
    real_time.sleep(0.05)
    assert any(p.name == f"{a['session']}.jsonl" for p in app.passes)  # its memory pass still runs
    assert app.c.post("/api/overnight/live/end", json={"session": a["session"]}).status_code == 409


def test_an_aside_adds_its_saved_topics_and_the_listeners_questions_to_seeds(app):
    """C15: after_chapter ran at build time, so a morning aside's seeds go to seeds.jsonl when it ends."""
    root = chapter(app)
    s = app.c.post("/api/overnight/live/start", json={"mode": "ask", "sdp": "offer", "run": root.name, "t": 20}).json()
    for text in ("Wait, why do herons stand so still?", "Huh, OK"):
        app.c.post("/api/overnight/live/line", json={"session": s["session"], "who": "listener", "text": text})
    app.c.post("/api/overnight/live/tool", json={"session": s["session"], "name": "save_seed",
                                                 "args": {"topic": "Heron eyesight", "why": "they asked"}})
    app.c.post("/api/overnight/live/end", json={"session": s["session"], "seconds": 12})
    seeds = {x["kind"]: x for x in library.seeds(app.listener)}
    assert seeds["question"]["text"] == "Wait, why do herons stand so still?"
    assert seeds["question"]["words"] == "Wait, why do herons stand so still?"
    assert seeds["idea"]["text"] == "Heron eyesight (they asked)" and seeds["idea"]["words"] == "Huh, OK"
    assert seeds["idea"]["source"]["chapter"] == root.name and seeds["idea"]["source"]["session"] == s["session"]
    assert len(library.seeds(app.listener)) == 2  # "Huh, OK" is no question


def test_an_aside_abandoned_before_anything_was_said_keeps_no_aside(app):
    """C9: a tap back while it connected ends the server's session; nothing was said, so nothing is kept."""
    from ask import aside as A
    root = chapter(app)
    s = app.c.post("/api/overnight/live/start", json={"mode": "ask", "sdp": "offer", "run": root.name, "t": 20}).json()
    end = app.c.post("/api/overnight/live/end", json={"session": s["session"], "how": "abandoned"}).json()
    assert end["restart"] and A.load_asides(root) == [] and log(app)[-1]["ended"] == "abandoned"


def test_a_replan_that_fails_queues_the_request(app, monkeypatch):
    monkeypatch.setattr(schedule, "can_change", lambda card, settings, now: (True, ""))

    def fail(*a, **k):
        raise RuntimeError("the planner fell over")
    monkeypatch.setattr(planner, "plan", fail)
    s = app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"}).json()
    out = app.c.post("/api/overnight/live/tool", json={"session": s["session"], "name": "set_next_chapter",
                                                         "args": {"mode": "topic", "topic": "Tides", "notes": "short"}}).json()
    assert out["changing"] is True
    for _ in range(100):
        if (app.listener / "queue.jsonl").exists():
            break
        real_time.sleep(0.02)
    q = profiles.read_jsonl(app.listener / "queue.jsonl")
    assert q[-1]["topic"] == "Tides" and q[-1]["notes"] == "short" and q[-1]["session"] == s["session"]


def test_a_conversations_audio_is_kept_beside_its_transcript(app):
    """C29: the page uploads its recording after /end."""
    s = app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"}).json()
    app.c.post("/api/overnight/live/end", json={"session": s["session"], "seconds": 3})
    sid = s["session"]
    r = app.c.post(f"/api/overnight/live/audio/{sid}", content=b"webm-bytes", headers={"Content-Type": "audio/webm;codecs=opus"})
    assert r.json() == {"saved": f"{sid}.webm", "bytes": 10}
    assert (app.listener / "sessions" / f"{sid}.webm").read_bytes() == b"webm-bytes"
    assert app.c.post(f"/api/overnight/live/audio/{sid}", content=b"").status_code == 400
    assert app.c.post("/api/overnight/live/audio/20991231-000000", content=b"x").status_code == 404
    assert app.c.post("/api/overnight/live/audio/..%2Fprofile", content=b"x").status_code == 404
