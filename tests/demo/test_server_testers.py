"""Tester demo, part A: the per-tester server (docs/tester-demo-design.md, Identity and Per-listener
server state). Testers come from a stand-in for demo/testers.py (part B), keyed by the wl_tester cookie."""

import importlib.machinery
import json
import os
import sys
import threading
import time
import types
from types import SimpleNamespace as NS

import pytest
from fakes import FakeAnthropic, Responder
from fastapi import Request
from fastapi.testclient import TestClient

import server
from faststart import base as fast_base
from faststart import guide_hooks
from overnight import profiles
from stages import common, guide, llm, memory

TOKENS = {"tokA": "t-alice", "tokB": "t-bob", "tokC": "t-carol"}


class FakeProc:
    pid = 0

    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode


def install_testers(monkeypatch, tokens=TOKENS):
    """demo.testers as part B builds it, reduced to what the middleware calls."""
    mod = types.ModuleType("demo.testers")

    def listener_for_request(scope):
        for k, v in scope.get("headers") or []:
            if k == b"cookie":
                for part in v.decode().split(";"):
                    name, _, val = part.strip().partition("=")
                    if name == "wl_tester":
                        return tokens.get(val)
        return None
    mod.listener_for_request = listener_for_request
    pkg = types.ModuleType("demo")
    pkg.testers = mod
    monkeypatch.setitem(sys.modules, "demo", pkg)
    monkeypatch.setitem(sys.modules, "demo.testers", mod)


@pytest.fixture
def demo(cfg, monkeypatch):
    cfg["demo"] = {"enabled": True, "max_builds": 3}
    cfg.pop("listener_dir", None)
    for lid in set(TOKENS.values()):
        d = profiles.create(lid)
        profiles.write_json(d / "tester.json", {"lid": lid, "label": lid[2:].title()})
    profiles.create("ian")
    profiles.set_active("ian")  # Ian's own listener: no tester request may touch it
    install_testers(monkeypatch)
    monkeypatch.setattr(server, "QUEUE_WATCH_SECONDS", 3600)  # the tests tick the queue themselves
    resp = Responder()
    monkeypatch.setattr(llm, "_client", FakeAnthropic(resp))
    launched = []

    def popen(cmd, **kw):
        launched.append({"cmd": cmd, "env": kw["env"], "proc": FakeProc()})
        return launched[-1]["proc"]
    monkeypatch.setattr(server.subprocess, "Popen", popen)
    app = server.create_app(cfg=cfg, remember_on_start=False)

    def client(token=None):
        return TestClient(app, headers={"cookie": f"wl_tester={token}"} if token else {})
    return NS(app=app, client=client, launched=launched, cfg=cfg, resp=resp, L=common.LISTENERS_DIR)


# --- the middleware ---------------------------------------------------------------------------

def test_without_a_tester_only_the_gate_paths_answer(demo):
    c = demo.client()
    for path in ("/api/status", "/api/library", "/live.js", "/runs/x/status.json", "/api/events"):
        res = c.get(path)
        assert res.status_code == 404 and res.content == b"", path
    for path in ("/api/chat/start", "/api/reset", "/api/chapters"):
        res = c.post(path, json={})
        assert res.status_code == 404 and res.content == b"", path
    assert c.get("/healthz").json() == {"ok": True}
    assert c.get("/").status_code == 200  # the blank page (part B's route serves it in tester mode)
    for path in ("/t/sometoken", "/admin", "/api/admin/testers", "/interview.js"):  # reach the app: its own 404
        assert c.get(path).content != b"", path
    assert demo.client("unknown").get("/api/status").status_code == 404  # a cookie that names no tester


def test_a_missing_testers_module_means_no_tester(demo, monkeypatch):
    monkeypatch.setitem(sys.modules, "demo.testers", None)  # import fails
    monkeypatch.setitem(sys.modules, "demo", types.ModuleType("demo"))
    assert server.tester_for({"headers": [(b"cookie", b"wl_tester=tokA")]}) == (None, None)
    assert demo.client("tokA").get("/api/status").status_code == 404


def test_demo_off_changes_nothing(cfg, monkeypatch):
    install_testers(monkeypatch)
    cfg["demo"] = {"enabled": False}
    c = TestClient(server.create_app(server.Pipeline(), cfg, remember_on_start=False))
    assert c.get("/api/status").json() == {"state": "idle"}
    assert common.REQUEST_LISTENER.get() is None


def test_the_listener_reaches_the_threadpool_async_routes_and_the_event_stream(demo):
    app = demo.app

    @app.get("/api/_who")
    def who_sync(request: Request) -> dict:  # a sync route runs in the threadpool
        return {"lid": common.REQUEST_LISTENER.get(), "dir": str(common.listener_dir()),
                "tester": request.state.tester}

    @app.get("/api/_who_async")
    async def who_async() -> dict:
        return {"lid": common.REQUEST_LISTENER.get()}

    a = demo.client("tokA").get("/api/_who").json()
    assert a["lid"] == "t-alice" and a["dir"] == str(demo.L / "t-alice")
    assert a["tester"] == {"lid": "t-alice", "label": "Alice"}
    assert demo.client("tokB").get("/api/_who_async").json() == {"lid": "t-bob"}
    assert common.REQUEST_LISTENER.get() is None  # nothing leaks out of the request

    for lid, title in (("t-alice", "Alice's chapter"), ("t-bob", "Bob's chapter")):
        run = demo.L / lid / "chapters" / "lesson-20261116"
        run.mkdir(parents=True)
        (run / "status.json").write_text(json.dumps({"run": run.name, "state": "ready", "title": title}))
    with demo.client("tokB").stream("GET", "/api/events") as res:
        for line in res.iter_lines():
            if line.startswith("data: "):
                assert json.loads(line[6:])["title"] == "Bob's chapter"
                break


# --- per-listener state -------------------------------------------------------------------------

def test_two_testers_conversations_and_chapters_are_their_own(demo):
    a, b = demo.client("tokA"), demo.client("tokB")
    sa = a.post("/api/chat/start").json()["session"]
    sb = b.post("/api/chat/start").json()["session"]  # never ends Alice's conversation
    assert (demo.L / "t-alice" / "sessions" / f"{sa}.jsonl").exists()
    assert (demo.L / "t-bob" / "sessions" / f"{sb}.jsonl").exists()
    assert a.post("/api/chat", json={"message": "I pour concrete", "session": sa}).status_code == 200
    assert getattr(demo.resp, "memory_calls", 0) == 0  # Bob's start ran no pass on Alice's conversation
    assert "I pour concrete" not in (demo.L / "t-bob" / "sessions" / f"{sb}.jsonl").read_text()
    # Ids are unique within a listener's folder only (both may be this second's): the states keep them apart.
    assert b.post("/api/chat/end", json={"session": sb}).json() == {"ended": True}
    assert a.post("/api/chat", json={"message": "still there?", "session": sa}).status_code == 200
    assert b.post("/api/chat", json={"message": "hello", "session": sa}).status_code == 409  # Bob's has ended
    for t in server.state_for("t-bob").chat["passes"]:
        t.join(10)
    sb = b.post("/api/chat/start").json()["session"]

    b.post("/api/chat", json={"message": "I pour concrete", "session": sb})
    assert "under way" in a.post("/api/chat", json={"message": "yes please", "session": sa}).json()["reply"]
    assert "under way" in b.post("/api/chat", json={"message": "yes please", "session": sb}).json()["reply"]
    ra, rb = a.get("/api/status").json(), b.get("/api/status").json()
    assert ra["run"] == rb["run"]  # the same seed: the same name, each in its own folder
    assert (demo.L / "t-alice" / "chapters" / ra["run"] / "00_source" / "seed.md").exists()
    assert (demo.L / "t-bob" / "chapters" / rb["run"] / "00_source" / "seed.md").exists()
    assert (demo.L / "t-alice" / "seeds" / f"{ra['run']}.md").exists()  # memory.save_seed, pinned
    assert not (demo.L / "ian" / "chapters" / ra["run"]).exists() and not (demo.L / "ian" / "seeds").exists()
    assert [x["env"][common.LISTENER_ENV] for x in demo.launched] == ["t-alice", "t-bob"]
    st = server.state_for("t-alice")
    assert st is not server.state_for("t-bob") and st.pipe.listener == "t-alice"
    assert st.chat["session"].cfg["listener_dir"] == str(demo.L / "t-alice")


def test_a_tester_cannot_reset_the_server(demo):
    res = demo.client("tokA").post("/api/reset")
    assert res.status_code == 403
    assert common.active_listener_id() == "ian"


# --- the build limit ----------------------------------------------------------------------------

def start(client, topic):
    res = client.post("/api/chapters", json={"seed": topic, "mode": "fast"})
    assert res.status_code == 200, res.text
    return res.json()["run"]


def test_builds_past_the_limit_wait_their_turn(demo, monkeypatch):
    demo.cfg["demo"]["max_builds"] = 1
    app = server.create_app(cfg=demo.cfg, remember_on_start=False)
    a = TestClient(app, headers={"cookie": "wl_tester=tokA"})
    b = TestClient(app, headers={"cookie": "wl_tester=tokB"})
    q = app.state.build_queue
    ra = start(a, "Roman concrete")
    rb = start(b, "Why bridges sway")
    assert len(demo.launched) == 1 and demo.launched[0]["env"][common.LISTENER_ENV] == "t-alice"
    broot = demo.L / "t-bob" / "chapters" / rb
    s = b.get("/api/status").json()
    assert s["state"] == "making" and s["queued"] is True and s["label"] == "Waiting its turn" and s["mode"] == "fast"
    assert s["run"] == rb
    assert guide.plain_status(s)["stage"] == "waiting its turn" and "its turn" in guide.plain_status(s)["queued"]
    assert b.post("/api/chapters", json={"seed": "Another", "mode": "fast"}).status_code == 409  # one per listener
    assert b.post(f"/api/chapters/{rb}/retry").status_code == 409
    queued = json.loads((common.SHARED_DIR / "build_queue.json").read_text())["queue"]
    assert [(e["lid"], e["run"]) for e in queued] == [("t-bob", rb)]
    queued_zero = json.loads((broot / "fast.json").read_text())["seed_set"]

    assert q.tick() == []  # Alice's build is still going
    demo.launched[0]["proc"].returncode = 0  # it finished
    (demo.L / "t-alice" / "chapters" / ra / "status.json").write_text(json.dumps({"run": ra, "state": "ready"}))
    time.sleep(0.01)
    assert q.tick() == [rb]
    assert len(demo.launched) == 2 and demo.launched[1]["env"][common.LISTENER_ENV] == "t-bob"
    assert demo.launched[1]["cmd"][2:] == ["new", rb, "--unattended", "--mode", "fast"]
    assert json.loads((broot / "fast.json").read_text())["seed_set"] > queued_zero  # the timeline starts now
    assert json.loads((common.SHARED_DIR / "build_queue.json").read_text())["queue"] == []
    assert b.get("/api/status").json()["state"] == "making"  # its own process now


def test_a_queued_run_can_be_stopped_and_the_queue_survives_a_restart(demo):
    demo.cfg["demo"]["max_builds"] = 1
    app = server.create_app(cfg=demo.cfg, remember_on_start=False)
    a, b, c = (TestClient(app, headers={"cookie": f"wl_tester={t}"}) for t in ("tokA", "tokB", "tokC"))
    start(a, "Roman concrete")
    rb = start(b, "Why bridges sway")
    rc = start(c, "Herons")
    assert b.post(f"/api/chapters/{rb}/stop").json()["stopped"] is False
    assert b.get("/api/status").json()["state"] == "stopped"
    queued = json.loads((common.SHARED_DIR / "build_queue.json").read_text())["queue"]
    assert [e["run"] for e in queued] == [rc]

    demo.launched[0]["proc"].returncode = 0
    restarted = server.create_app(cfg=demo.cfg, remember_on_start=False)  # a new server reads the same queue
    cc = TestClient(restarted, headers={"cookie": "wl_tester=tokC"})
    assert cc.get("/api/status").json()["queued"] is True  # waiting, not a problem
    assert restarted.state.build_queue.tick() == [rc]
    assert demo.launched[-1]["env"][common.LISTENER_ENV] == "t-carol" and len(demo.launched) == 2


def test_the_watcher_starts_a_queued_run_when_a_slot_frees(demo, monkeypatch):
    monkeypatch.setattr(server, "QUEUE_WATCH_SECONDS", 0.05)
    demo.cfg["demo"]["max_builds"] = 1
    app = server.create_app(cfg=demo.cfg, remember_on_start=False)
    a = TestClient(app, headers={"cookie": "wl_tester=tokA"})
    b = TestClient(app, headers={"cookie": "wl_tester=tokB"})
    try:
        ra = start(a, "Roman concrete")
        start(b, "Why bridges sway")
        time.sleep(0.2)
        assert len(demo.launched) == 1
        demo.launched[0]["proc"].returncode = 0
        (demo.L / "t-alice" / "chapters" / ra / "status.json").write_text(json.dumps({"run": ra, "state": "ready"}))
        for _ in range(100):
            if len(demo.launched) == 2:
                break
            time.sleep(0.02)
        assert demo.launched[-1]["env"][common.LISTENER_ENV] == "t-bob"
    finally:
        server._WATCHER["queue"] = None  # the watcher stops


def test_a_queued_status_with_no_queue_entry_is_a_problem_to_retry(demo):
    root = demo.L / "t-alice" / "chapters" / "lost-20261116"
    root.mkdir(parents=True)
    (root / "status.json").write_text(json.dumps({"run": root.name, **server.QUEUED_STATUS}))
    s = demo.client("tokA").get("/api/status").json()
    assert s["state"] == "problem" and s["retryable"] is True


# --- threads pin their listener -----------------------------------------------------------------

def test_a_memory_pass_writes_into_the_testers_folder(demo):
    a = demo.client("tokA")
    sa = a.post("/api/chat/start").json()["session"]
    a.post("/api/chat", json={"message": "I pour concrete", "session": sa})
    assert a.post("/api/chat/end", json={"session": sa}).json() == {"ended": True}
    for t in server.state_for("t-alice").chat["passes"]:
        t.join(10)
    assert "Pours concrete" in (demo.L / "t-alice" / "profile.md").read_text()
    assert (demo.L / "t-alice" / "sessions" / f"{sa}.done").exists()
    assert not (demo.L / "ian" / "profile.md").exists() and not (demo.L / "t-bob" / "profile.md").exists()


def test_session_and_hooks_pin_the_listener_at_construction(demo, tmp_path):
    with common.listener_scope("t-bob"):
        hooks = guide_hooks.Hooks(demo.cfg, tmp_path)
    assert hooks.cfg["listener_dir"] == str(demo.L / "t-bob")
    with common.listener_scope("t-alice"):
        assert common.pinned(demo.cfg)["listener_dir"] == str(demo.L / "t-alice")
    assert "listener_dir" not in demo.cfg  # pinned() copies


# --- the run-sizing hook and the chapter card ---------------------------------------------------

def test_a_ten_minute_listener_gets_a_sized_run_and_a_card(demo, monkeypatch):
    import faststart
    monkeypatch.setattr(fast_base, "sized_overrides", lambda minutes: {"sections": 2, "minutes": minutes},
                        raising=False)
    cards = []
    card = types.ModuleType("faststart.card")
    card.__spec__ = importlib.machinery.ModuleSpec("faststart.card", None)
    card.start = lambda root, cfg: cards.append((root, cfg["listener_dir"]))
    monkeypatch.setitem(sys.modules, "faststart.card", card)
    monkeypatch.setattr(faststart, "card", card, raising=False)
    profiles.save_settings(demo.cfg, {"chapter": {"minutes": 10}}, listener=demo.L / "t-alice")

    pipe = server.state_for("t-alice").pipe
    run = pipe.start_seed("Roman concrete", mode="fast", live=True)
    root = demo.L / "t-alice" / "chapters" / run
    import yaml
    assert yaml.safe_load((root / "config.yaml").read_text()) == {"fast": {"sections": 2, "minutes": 10}}
    for _ in range(100):
        if cards:
            break
        time.sleep(0.01)
    assert cards == [(root, str(demo.L / "t-alice"))]

    bob = server.state_for("t-bob").pipe  # the default length: no run config at all
    rb = bob.start_seed("Herons", mode="fast")
    assert not (demo.L / "t-bob" / "chapters" / rb / "config.yaml").exists()


# --- the admin's reset, revoke and delete (drop) ------------------------------------------------

def hooked_app(demo):
    """A new app whose drop() is on the stand-in testers module's ON_CHANGE, as it is on demo/testers.py."""
    mod = sys.modules["demo.testers"]
    mod.ON_CHANGE = []
    app = server.create_app(cfg=demo.cfg, remember_on_start=False)
    return app, mod.ON_CHANGE[0]


def test_dropping_a_tester_waits_for_their_memory_passes(demo):
    """A pass still in its call when the folder moves would write into the fresh folder (review)."""
    app, drop = hooked_app(demo)
    a = TestClient(app, headers={"cookie": "wl_tester=tokA"})
    sa = a.post("/api/chat/start").json()["session"]
    a.post("/api/chat", json={"message": "I pour concrete", "session": sa})
    demo.resp.memory_delay = 0.5
    st = app.state.state_for("t-alice")
    assert a.post("/api/chat/end", json={"session": sa}).json() == {"ended": True}
    passes = list(st.chat["passes"])
    assert passes and passes[0].is_alive()
    drop("t-alice", "reset")
    assert not any(t.is_alive() for t in passes)
    assert (demo.L / "t-alice" / "sessions" / f"{sa}.done").exists()  # finished before the folder could move
    assert app.state.state_for("t-alice") is not st

    # A pass started by another path (a check-in's) holds the listener's memory lock: drop waits for it too.
    lock = memory.lock_for({**demo.cfg, "listener_dir": str(demo.L / "t-bob")})
    lock.acquire()
    try:
        th = threading.Thread(target=drop, args=("t-bob", "delete"))
        th.start()
        th.join(0.3)
        assert th.is_alive()
    finally:
        lock.release()
    th.join(5)
    assert not th.is_alive()


def test_dropping_a_tester_with_no_state_here_still_stops_their_build(demo, monkeypatch):
    """After a restart this server has no state for the tester, but their build went on (KillMode=process)."""
    app, drop = hooked_app(demo)
    root = demo.L / "t-alice" / "chapters" / "concrete-20261116"
    root.mkdir(parents=True)
    (root / "status.json").write_text(json.dumps({"run": root.name, "state": "making", "pid": os.getpid()}))
    stopped = []
    monkeypatch.setattr(server.Pipeline, "stop", lambda self, run, cfg: stopped.append((self.listener, run)))
    drop("t-alice", "delete")
    assert stopped == [("t-alice", root.name)]


def test_ending_a_chat_never_blocks_the_event_loop(demo):
    """POST /api/chat/end waits for the chat lock (a turn in progress) off the event loop (review)."""
    st = demo.app.state.state_for("t-alice")
    with TestClient(demo.app, headers={"cookie": "wl_tester=tokA"}) as a:  # one event loop for every request
        ended = []
        with st.chat["lock"]:  # a turn in progress
            th = threading.Thread(target=lambda: ended.append(a.post("/api/chat/end", json={"session": "x"}).json()))
            th.start()
            time.sleep(0.2)
            got = []
            other = threading.Thread(target=lambda: got.append(a.get("/healthz").json()))
            other.start()
            other.join(3)
            assert got == [{"ok": True}]
            assert not ended  # still waiting for the turn
        th.join(5)
        assert ended == [{"ended": False}]


def test_a_page_open_during_a_reset_runs_no_pass_on_the_conversation_let_go(demo):
    """Review: drop() runs outside testers._LOCK, so the tester's open page (its /api/events tick) recreates
    their state before the folder moves, and that state's catch-up pass paid to remember the conversation
    drop() had let go, its cost line landing in the fresh folder."""
    mod = sys.modules["demo.testers"]  # the stand-in testers module, as in hooked_app
    mod.ON_CHANGE = []
    app = server.create_app(cfg=demo.cfg, remember_on_start=True)
    drop = mod.ON_CHANGE[0]
    a = TestClient(app, headers={"cookie": "wl_tester=tokA"})
    sa = a.post("/api/chat/start").json()["session"]
    a.post("/api/chat", json={"message": "I pour concrete", "session": sa})
    for t in app.state.state_for("t-alice").chat["passes"]:
        t.join(5)
    before = getattr(demo.resp, "memory_calls", 0)
    drop("t-alice", "reset")  # the conversation is let go without a memory pass
    st = app.state.state_for("t-alice")  # the page's next tick, before the folder moves
    for t in st.chat["passes"]:
        t.join(5)
    assert getattr(demo.resp, "memory_calls", 0) == before
    assert "skipped" in (demo.L / "t-alice" / "sessions" / f"{sa}.done").read_text()

