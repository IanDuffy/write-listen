"""Listener profiles and storage (docs/overnight-chapters.md, step 1; acceptance checks 1 and 2)."""

import hashlib
import json
import os
from datetime import date

import pytest
from fastapi.testclient import TestClient

import server
from overnight import profiles
from stages import common, memory, seed
from stages.common import Run, StageError


def tree(d):
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir"
            for p in sorted(d.rglob("*"))}


def test_the_resolver_follows_active_json_and_a_pin_wins(cfg, own_listeners, monkeypatch):
    cfg.pop("listener_dir", None)
    assert common.listener_dir(cfg) == own_listeners / "ian"  # nothing set up yet: ian, created by nothing
    assert not own_listeners.exists()  # resolving never creates folders
    profiles.create("ian"), profiles.create("sam")
    profiles.set_active("sam")
    assert common.listener_dir(cfg) == own_listeners / "sam" and common.chapters_dir(cfg) == own_listeners / "sam" / "chapters"
    assert common.default_runs_dir() == own_listeners / "sam" / "chapters"
    monkeypatch.setenv(common.LISTENER_ENV, "ian")  # a run the server started for ian stays ian's
    assert common.listener_dir(cfg) == own_listeners / "ian"
    monkeypatch.delenv(common.LISTENER_ENV)
    assert common.listener_dir({"listener_dir": "/somewhere/else"}) == common.ROOT / "/somewhere/else"
    with pytest.raises(StageError):
        profiles.set_active("nobody")


def test_a_chapter_reads_and_writes_its_own_listener_whoever_is_active(cfg, own_listeners):
    cfg.pop("listener_dir", None)
    ian = profiles.create("ian")
    (ian / "profile.md").write_text("- Builds cabinets.\n")
    profiles.create("demo-x")
    profiles.set_active("demo-x")
    r = Run.open("2026-11-17-joinery", create=True, runs_dir=ian / "chapters")
    seed.write(r, topic="Japanese joinery", why="he builds cabinets")
    rc = common.run_config(r, cfg)
    assert common.listener_dir(rc) == ian
    assert common.seed_inputs(r, rc)["profile"] == "- Builds cabinets."
    memory.record_chapter(rc, r)
    assert (ian / "history.md").exists() and not (own_listeners / "demo-x" / "history.md").exists()


def test_settings_start_from_config_and_are_checked(cfg, own_listeners):
    profiles.create("ian")
    s = profiles.settings(cfg, "ian")
    assert s["overnight"] == {"enabled": True, "days": ["mon", "tue", "wed", "thu", "fri"], "start": "02:00",
                              "plan_at": "18:00", "runaway_stop_usd": 50}
    assert s["chapter"] == {"minutes": 20, "mode": "surprise"} and s["playback"] == {"read_live": False}
    s = profiles.save_settings(cfg, {"overnight": {"enabled": False, "days": ["sun", "Mon"], "start": "3:30"}}, "ian")
    assert s["overnight"]["enabled"] is False and s["overnight"]["days"] == ["mon", "sun"]
    assert s["overnight"]["start"] == "03:30" and s["overnight"]["plan_at"] == "18:00"
    on_disk = json.loads((own_listeners / "ian" / "settings.json").read_text())
    assert on_disk["overnight"] == {"enabled": False, "days": ["mon", "sun"], "start": "03:30"}  # only what changed
    for bad in ({"overnight": {"start": "25:00"}}, {"overnight": {"days": ["someday"]}}, {"chapter": {"mode": "x"}},
                {"overnight": {"budget": 3}}, {"nope": {}}, {"overnight": {"runaway_stop_usd": -1}}):
        with pytest.raises(StageError):
            profiles.save_settings(cfg, bad, "ian")


def test_reset_makes_a_demo_listener_and_ian_is_byte_for_byte_unchanged(cfg, own_listeners):
    """Acceptance check 2."""
    ian = profiles.create("ian")
    profiles.set_active("ian")
    (ian / "profile.md").write_text("- Builds cabinets.\n")
    (ian / "chapters" / "2026-11-14-herons" / "10_audio").mkdir(parents=True)
    (ian / "chapters" / "2026-11-14-herons" / "10_audio" / "chapter.mp3").write_bytes(b"mp3")
    os.utime(ian / "profile.md", (1, 1))
    before, stat_before = tree(ian), (ian / "profile.md").stat().st_mtime
    lid = profiles.reset(date(2026, 11, 20))
    assert lid == "demo-2026-11-20-a" and common.active_listener_id() == lid
    assert profiles.reset(date(2026, 11, 20)) == "demo-2026-11-20-b"
    assert tree(ian) == before and (ian / "profile.md").stat().st_mtime == stat_before
    demo = own_listeners / "demo-2026-11-20-b"
    assert sorted(p.name for p in demo.iterdir()) == ["chapters", "plans", "sessions", "settings.json"]


def _checkout(root, runs, listener=()):
    for name, files in runs.items():
        for rel, body in files.items():
            p = root / "runs" / name / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
    for rel, body in listener:
        p = root / "listener" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return root


def test_migration_copies_everything_and_the_chapters_open(tmp_path, cfg, own_listeners):
    """Acceptance check 1, on a small copy of the real layout: nothing lost, chapters open from listeners/ian/."""
    import ask_fixture
    main = tmp_path / "main"
    ask_fixture.build(main / "runs" / "beauty-scarry", audio=True)
    (main / "runs" / "beauty-scarry" / "10_audio" / "concat.txt").write_text(
        f"file '{main / 'runs' / 'beauty-scarry' / '10_audio' / 'p1.wav'}'\n")
    _checkout(main, {"beauty-scarry-short": {"09_script_final.md": "x"}, "ping": {"log.jsonl": ""},
                     "_stopped": {"medieval-monks-20261112/status.json": "{}"}},
              [("voice.json", '{"agent_id": "a"}'), ("_cleared_20261113/profile.md", "- old\n"),
               ("_cleared_20261113/sessions/20261113-1.jsonl", '{"who": "listener"}\n')])
    live = _checkout(tmp_path / "live", {"herons-hunt-standing-still-20261114": {"status.json": "{}"}})
    ask_fixture.build(live / "runs" / "herons-hunt-standing-still-20261114", audio=True)
    from datetime import datetime
    for root, day in ((main / "runs" / "beauty-scarry", 11), (live / "runs" / "herons-hunt-standing-still-20261114", 14)):
        t = datetime(2026, 11, day, 12).timestamp()
        os.utime(root / "10_audio" / "chapter.mp3", (t, t))  # a chapter is dated by when it was finished

    out = profiles.migrate([main, live], "ian", ("beauty-scarry", "herons-hunt-standing-still-20261114"))
    ian = own_listeners / "ian"
    names = [p.name for p in profiles.chapters("ian")]
    assert names == ["2026-11-11-beauty-scarry", "2026-11-14-herons-hunt-standing-still"]
    assert out["missing_chapters"] == []
    for src, dest in ((main / "runs" / "beauty-scarry", ian / "chapters" / names[0]),
                      (live / "runs" / "herons-hunt-standing-still-20261114", ian / "chapters" / names[1])):
        a, b = tree(src), tree(dest)
        assert a.keys() == b.keys()  # every file came along
        assert all(a[k] == b[k] for k in a if not k.endswith("concat.txt"))
    assert (ian / "chapters" / names[0] / "10_audio" / "concat.txt").read_text().count(str(ian)) == 1  # repointed
    assert (ian / "archive" / "main" / "runs" / "beauty-scarry-short" / "09_script_final.md").exists()
    assert (ian / "archive" / "main" / "runs" / "_stopped" / "medieval-monks-20261112" / "status.json").exists()
    assert (ian / "archive" / "main" / "listener" / "_cleared_20261113" / "sessions" / "20261113-1.jsonl").exists()
    assert not (ian / "archive" / "main" / "runs" / "ping").exists()
    assert (common.SHARED_DIR / "voice" / "voice.json").read_text() == '{"agent_id": "a"}'
    assert tree(main / "runs" / "beauty-scarry")  # the sources are copied, never moved
    assert common.active_listener_id() == "ian"
    assert profiles.migrate([main, live], "ian", ("beauty-scarry", "herons-hunt-standing-still-20261114"))["copied"] == []  # idempotent

    cfg.pop("listener_dir", None)
    c = TestClient(server.create_app(server.Pipeline(), cfg, remember_on_start=False))
    for name in names:
        st = c.get(f"/api/ask/chapter/{name}").json()
        assert st["state"] == "ready" and st["asks"] is True
        assert c.get(f"/api/chapters/{name}/audio").status_code == 200
        assert c.get(f"/runs/{name}/11_episode/timing.json").status_code == 200


def test_latest_chapter_is_the_newest_with_audio(own_listeners):
    ian = profiles.create("ian")
    for name, audio in (("2026-11-11-a", True), ("2026-11-14-b", True), ("2026-11-17-c", False), ("_scouts", True)):
        (ian / "chapters" / name / "10_audio").mkdir(parents=True)
        if audio:
            (ian / "chapters" / name / "10_audio" / "chapter.mp3").write_bytes(b"x")
    assert profiles.latest_chapter("ian").name == "2026-11-14-b"
    assert [p.name for p in profiles.chapters("ian")] == ["2026-11-11-a", "2026-11-14-b", "2026-11-17-c"]


def test_the_listener_cli(cfg, own_listeners, monkeypatch, capsys):
    import cli
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    from overnight import cli as ocli
    monkeypatch.setattr(ocli, "load_config", lambda: cfg)
    profiles.create("ian")
    assert cli.main(["listener", "use", "ian"]) == 0
    assert cli.main(["listener", "settings", "overnight.enabled=false", "overnight.days=mon,wed"]) == 0
    assert profiles.settings(cfg, "ian")["overnight"]["days"] == ["mon", "wed"]
    assert cli.main(["listener", "reset"]) == 0
    assert common.active_listener_id().startswith("demo-")
    assert cli.main(["listener", "use", "nobody"]) == 1
