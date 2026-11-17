"""The library page's API and its live sessions (overnight/routes.py, overnight/live.py), with GPT-Live's
session opening faked: the card's states, settings, Change before and after the cutoff (check 11 in
code), the intro's context (check 12's "nothing from the script beyond the brief"), and an aside on a
recorded chapter with the rewind (check 13 in code)."""

import json
import os
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from overnight_plan_fakes import offline  # noqa: F401

import server
from overnight import live, planner, profiles, routes, schedule
from stages import common, memory


@pytest.fixture
def app(cfg, tmp_path, monkeypatch, own_listeners):
    cfg.pop("listener_dir", None)
    listener = profiles.create("ian")
    profiles.set_active("ian")
    (listener / "profile.md").write_text("- Watches herons.\n")
    opened = []
    monkeypatch.setattr(live, "create_webrtc", lambda cfg, session, sdp, client=None:
                        opened.append(session) or {"id": f"live_{len(opened)}", "sdp": "answer-sdp"})
    passes = []
    monkeypatch.setattr(memory, "after_session", lambda cfg, path: passes.append(path) or False)
    c = TestClient(server.create_app(server.Pipeline(), cfg, remember_on_start=False))
    return type("A", (), {"c": c, "listener": listener, "opened": opened, "passes": passes, "cfg": cfg})


def card(listener, **over):
    data = {"id": "plan-1", "for_morning": "2026-11-17", "title": "Why Herons Wait", "overview": "Patience as a strategy.",
            "summary": "Why do herons stand still so long? It follows your question at the lake.",
            "mode": "surprise", "decided": "branch", "source": {"kind": "seed", "ref": "s-1", "words": "why so still"},
            "research_questions": [], "angle": "One morning.", "status": "planned", "chapter": None, **over}
    profiles.write_json(listener / "up_next.json", data)
    return data


def test_the_library_states(app):
    s = app.c.get("/api/library").json()
    assert s["up_next"]["state"] == "none" and s["up_next"]["change"] is False and s["latest"] is None
    card(app.listener)
    now = datetime(2026, 11, 16, 19, 0)  # Monday evening
    s = routes.library_state(app.cfg, now)
    assert s["up_next"]["state"] == "planned" and s["up_next"]["card"]["title"] == "Why Herons Wait"
    assert s["up_next"]["line"] == schedule.next_label(s["settings"], now) and s["up_next"]["change"] is True
    root = app.listener / "chapters" / "2026-11-17-why-herons-wait"
    root.mkdir(parents=True)
    profiles.write_json(root / "status.json", {"state": "making", "label": "Recording", "pid": os.getpid()})
    card(app.listener, status="building", chapter=root.name)
    s = routes.library_state(app.cfg, datetime(2026, 11, 17, 3, 0))
    assert s["up_next"]["state"] == "building" and s["up_next"]["change"] is False
    assert s["up_next"]["line"] == "Building now: recording"
    card(app.listener, status="failed", chapter=root.name)
    assert routes.library_state(app.cfg, datetime(2026, 11, 17, 8, 0))["up_next"]["line"] == "Last night's chapter didn't finish"
    import ask_fixture
    ask_fixture.build(root, audio=True)
    profiles.write_json(root / "00_source" / "up_next.json", {"title": "Why Herons Wait", "overview": "Patience."})
    card(app.listener, status="ready", chapter=root.name)
    s = routes.library_state(app.cfg, datetime(2026, 11, 17, 8, 0))
    assert s["up_next"]["state"] == "waiting" and s["up_next"]["card"] is None and s["up_next"]["change"] is False
    assert s["latest"]["title"] == "Why Herons Wait" and s["latest"]["date"] == "2026-11-17"
    assert s["latest"]["audio"] == f"/api/chapters/{root.name}/audio" and s["latest"]["minutes"] is not None
    profiles.save_settings(app.cfg, {"overnight": {"enabled": False}}, "ian")
    assert routes.library_state(app.cfg)["up_next"]["state"] == "off"
    page = app.c.get("/library")  # a shell around the module, which builds the cards and the player
    assert page.status_code == 200 and 'id="library"' in page.text and "/library.js" in page.text
    js = app.c.get("/library.js").text
    assert "window.Morning" in js and 'id="change"' in js and 'id="chapter-audio"' in js and '"lib-player"' in js
    home = app.c.get("/")  # the main page mounts the same module below the orb
    assert 'id="orb"' in home.text and '<section id="library"></section>' in home.text and "/library.js" in home.text
    assert home.text.index('id="orb"') < home.text.index('<section id="library">')


def test_history_lists_every_finished_chapter_newest_first(app):
    import ask_fixture
    chapters = app.listener / "chapters"
    older = chapters / "2026-11-17-why-herons-wait"
    ask_fixture.build(older, audio=True)
    profiles.write_json(older / "00_source" / "up_next.json", {"title": "Why Herons Wait", "overview": "Patience."})
    profiles.write_json(older / "11_episode" / "episode.json", {"title": "Why Herons Wait", "segments": []})
    profiles.write_json(older / "finished.json", {"t": "2026-11-17T04:00:00"})
    seed = chapters / "octopus-eyes-20261118"  # a seed run's name: its date comes from when it finished
    ask_fixture.build(seed, audio=True)
    profiles.write_json(seed / "status.json", {"state": "ready", "updated": "2026-11-18T09:30:00"})
    unfinished = chapters / "2026-11-19-tides"
    (unfinished / "00_source").mkdir(parents=True)
    s = routes.library_state(app.cfg, datetime(2026, 11, 19, 8, 0))
    runs = [x["run"] for x in s["history"]]
    assert runs == [seed.name, older.name]  # newest first by when it finished; the unfinished one is not listed
    first, second = s["history"]
    assert set(first) == {"run", "title", "overview", "date", "minutes", "audio", "episode", "intro", "asks"}
    assert first["date"] == "2026-11-18" and first["episode"] is None and first["intro"] is False
    assert second["title"] == "Why Herons Wait" and second["date"] == "2026-11-17" and second["minutes"]
    assert second["episode"] == f"#/chapter/{older.name}"  # the chapter view in the page, not the old episode page
    assert second["audio"] == f"/api/chapters/{older.name}/audio"
    assert s["latest"]["run"] == seed.name


def test_a_dead_build_shows_as_not_finished_and_can_change(app):
    """C4/C24: kill -9 mid-build; the page must not say 'Building now' all day."""
    import subprocess
    import sys
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    root = app.listener / "chapters" / "2026-11-17-why-herons-wait"
    root.mkdir(parents=True)
    profiles.write_json(root / "status.json", {"state": "making", "label": "Recording", "pid": p.pid})
    card(app.listener, status="building", chapter=root.name, locked_at="2026-11-17T02:00:00")
    s = routes.library_state(app.cfg, datetime(2026, 11, 17, 10, 0))
    assert s["up_next"]["state"] == "failed" and s["up_next"]["line"] == "Last night's chapter didn't finish"
    assert s["up_next"]["change"] is True and s["up_next"]["can_change"] is True
    assert profiles.read_json(root / "status.json")["state"] == "problem"


def test_a_failed_planning_pass_shows_on_the_card(app):
    """C23: 'Couldn't plan the next chapter' with the reason, until a pass succeeds or the window ends."""
    from overnight import planner
    card(app.listener, status="ready", for_morning="2026-11-17", planned_at="2026-11-16T18:00:00")
    profiles.append_jsonl(app.listener / planner.FAILURES, {
        "t": "2026-11-17T18:00:00", "reason": "the title 'Why Herons Wait' repeats the past chapter 'Why Herons Wait', after a retry."})
    s = routes.library_state(app.cfg, datetime(2026, 11, 17, 18, 30))
    assert s["up_next"]["line"].startswith("Couldn't plan the next chapter: the title 'Why Herons Wait' repeats")
    assert s["up_next"]["plan_failed"]
    s = routes.library_state(app.cfg, datetime(2026, 11, 18, 9, 0))  # Wednesday's build start: a new window
    assert s["up_next"]["line"] == "The next plan is written this evening" and s["up_next"]["plan_failed"] is None
    card(app.listener, status="planned", for_morning="2026-11-18", planned_at="2026-11-17T19:00:00")
    assert routes.library_state(app.cfg, datetime(2026, 11, 17, 19, 30))["up_next"]["plan_failed"] is None


def test_settings_are_saved_and_checked(app):
    out = app.c.post("/api/settings", json={"overnight": {"start": "03:15", "days": ["mon", "tue"]}})
    assert out.status_code == 200 and out.json()["settings"]["overnight"]["start"] == "03:15"
    assert profiles.settings(app.cfg, "ian")["overnight"]["days"] == ["mon", "tue"]
    assert app.c.post("/api/settings", json={"overnight": {"start": "late"}}).status_code == 400


def test_change_before_the_cutoff_rewrites_the_card(app, monkeypatch):
    card(app.listener)
    planned = []
    monkeypatch.setattr(planner, "plan", lambda cfg, listener, request=None, now=None: planned.append(request))
    monkeypatch.setattr(schedule, "can_change", lambda card, settings, now: (True, ""))
    s = app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"}).json()
    assert s["sdp"] == "answer-sdp" and s["title"] == "Why Herons Wait" and "change_opened" in s["notes"]
    session = app.opened[-1]
    assert "Why Herons Wait" in session["instructions"] and session["audio"]["output"]["voice"] == "gleam"
    assert [t["name"] for t in session["delegation"]["responses"]["tools"]] == ["set_next_chapter"]
    app.c.post("/api/overnight/live/line", json={"session": s["session"], "who": "listener", "text": "Something on octopus eyes"})
    out = app.c.post("/api/overnight/live/tool", json={"session": s["session"], "name": "set_next_chapter",
                                                         "args": {"mode": "topic", "topic": "How octopus eyes work"}}).json()
    assert out["changing"] is True
    import time
    for _ in range(50):
        if planned:
            break
        time.sleep(0.02)
    assert planned == [{"mode": "topic", "topic": "How octopus eyes work", "notes": "", "session": s["session"]}]
    end = app.c.post("/api/overnight/live/end", json={"session": s["session"], "seconds": 60,
                                                        "backend": {"model": "gpt-6.1-terra", "input": 1000, "output": 100}}).json()
    assert end["cost_usd"] == pytest.approx(0.05 + (1000 * 1 + 100 * 5) / 1e6)
    log = [json.loads(x) for x in (app.listener / "sessions" / "log.jsonl").read_text().splitlines()]
    assert log[-1]["provider"] == "openai" and log[-1]["mode"] == "change"
    lines = [json.loads(x) for x in (app.listener / "sessions" / f"{s['session']}.jsonl").read_text().splitlines()]
    assert [x["who"] for x in lines] == ["session", "listener", "tool", "session"]
    import time as _t
    _t.sleep(0.05)
    assert app.passes and app.passes[-1].name == f"{s['session']}.jsonl"  # the usual after-conversation pass


def test_change_after_the_cutoff_goes_to_the_queue(app, monkeypatch):
    import os
    card(app.listener, status="building", chapter="2026-11-17-why-herons-wait")
    profiles.write_json(app.listener / "chapters" / "2026-11-17-why-herons-wait" / "status.json",
                        {"state": "making", "pid": os.getpid()})  # the build is alive
    monkeypatch.setattr(planner, "plan", lambda *a, **k: pytest.fail("tonight's chapter can't change"))
    s = app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"}).json()
    assert "Anything new goes to the next build day" in app.opened[-1]["instructions"]
    out = app.c.post("/api/overnight/live/tool", json={"session": s["session"], "name": "set_next_chapter",
                                                         "args": {"mode": "topic", "topic": "Tides", "notes": "short"}}).json()
    assert out["queued"] is True and out["when"]
    q = profiles.read_jsonl(app.listener / "queue.jsonl")
    assert q[-1]["topic"] == "Tides" and q[-1]["notes"] == "short" and q[-1]["session"] == s["session"]
    kept = app.c.post("/api/overnight/live/tool", json={"session": s["session"], "name": "set_next_chapter",
                                                          "args": {"mode": "keep"}}).json()
    assert kept == {"kept": True}
    bad = app.c.post("/api/overnight/live/tool", json={"session": s["session"], "name": "start_chapter", "args": {}}).json()
    assert "error" in bad  # not a Change tool


def test_the_intro_is_told_the_brief_and_never_the_script(app):
    import ask_fixture
    root = app.listener / "chapters" / "2026-11-17-why-herons-wait"
    ask_fixture.build(root, audio=True)
    (root / "intro_brief.md").write_text("## Title\n\nWhy Herons Wait\n\n## Summary\n\nPatience.\n\n"
                                         "## Findings you may mention\n\n- A heron's neck strikes like a spring.\n\n"
                                         "## Why this one, for them\n\nYou asked at the lake.\n")
    assert app.c.post("/api/overnight/live/start", json={"mode": "intro", "sdp": "x", "run": "nope"}).status_code == 404
    s = app.c.post("/api/overnight/live/start", json={"mode": "intro", "sdp": "offer", "run": root.name}).json()
    text = app.opened[-1]["instructions"]
    assert "A heron's neck strikes like a spring." in text and "You asked at the lake." in text
    script = (root / "09_script_final.md").read_text()
    words = common.spoken_text(script).split()
    runs = {" ".join(words[i:i + 8]) for i in range(len(words) - 8)}
    assert not any(r in text for r in runs)  # no eight-word run of the script reached the guide
    assert [t["name"] for t in app.opened[-1]["delegation"]["responses"]["tools"]] == ["start_chapter"]
    assert app.c.post("/api/overnight/live/tool", json={"session": s["session"], "name": "start_chapter", "args": {}}).json() == {"starting": True}


def test_an_aside_on_a_recorded_chapter_resumes_with_the_rewind(app):
    import ask_fixture
    from ask import aside as A
    root = app.listener / "chapters" / "2026-11-17-why-herons-wait"
    ask_fixture.build(root, audio=True)
    s = app.c.post("/api/overnight/live/start", json={"mode": "ask", "sdp": "offer", "run": root.name, "t": 20}).json()
    assert s["t"] == 20 and s["sentence_id"]
    text = app.opened[-1]["instructions"]
    assert "<just_said>" in text and "<coming>" not in text  # what comes next only in outline
    app.c.post("/api/overnight/live/line", json={"session": s["session"], "who": "listener", "text": "Wait, why?"})
    app.c.post("/api/overnight/live/tool", json={"session": s["session"], "name": "save_seed",
                                                 "args": {"topic": "Heron eyesight", "why": "they asked"}})
    app.c.post("/api/overnight/live/tool", json={"session": s["session"], "name": "resume_chapter", "args": {"reason": "closing"}})
    end = app.c.post("/api/overnight/live/end", json={"session": s["session"], "seconds": 12}).json()
    ch = A.Chapter(root, app.cfg)
    assert end["restart"] == ch.restart_point(20, 1.0) and end["restart"]["rule"] == "sentence"  # under 30 s
    asides = A.load_asides(root)
    assert asides[-1]["ended"] == "closing" and asides[-1]["voice"] == "gpt-live" and asides[-1]["seeds"]
    assert app.passes == []  # an aside belongs to the chapter, not a memory pass of its own
    assert app.c.post("/api/overnight/live/line", json={"session": s["session"], "who": "guide", "text": "x"}).status_code == 409


def test_the_library_never_reuses_an_id_of_the_pages_it_mounts_on():
    """After the merge (Ian, 2026-11-15): the library made its own #player and #play, the live page already
    had both, so its CSS pinned the live player to the bottom left and its script wrote "Play" over the live
    play button. Only the mount point, #library, may be shared."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "page"
    lib = set(re.findall(r'id=\\?"([\w-]+)\\?"', (root / "library.js").read_text())) | \
        set(re.findall(r'\.id = "([\w-]+)"', (root / "library.js").read_text()))
    for page in ("live.html", "index.html"):
        theirs = set(re.findall(r'id="([\w-]+)"', (root / page).read_text()))
        assert lib & theirs <= {"library"}, (page, sorted(lib & theirs))
