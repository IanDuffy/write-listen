"""The library page and its API (docs/overnight-chapters.md, "Library page"; the morning flow and
Change by voice add their live-session routes here).

    GET  /library        page/library.html: a standalone shell around page/library.js (the same module
                         page/index.html mounts below the orb: Up next, the latest chapter, history)
    GET  /library.js     the module: its markup, the mini player, the chapter view (#/chapter/<run>)
    GET  /api/library    what the page shows (the card, its state line, the latest chapter, every
                         finished chapter newest first as history, settings)
    POST /api/settings   {"overnight": {...}, "chapter": {...}, "playback": {...}} -> settings.json
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response

from stages import common
from stages.common import ROOT, StageError

from . import profiles

PAGE = ROOT / "page" / "library.html"
LIBRARY_JS = ROOT / "page" / "library.js"


def _chapter_info(root: Path) -> dict:
    """A chapter as the library shows it: title, overview, date, length, its audio and its chapter view
    (`#/chapter/<run>`, the page's in-page read-along, when it has an episode.json). A chapter made in
    conversation (a fast run) has a card of its own (faststart/card.py, fast/card.json): its overview and
    summary are shown here, on the finished chapter, never while it is being made (Ian, 2026-11-16); its
    title is the outline's."""
    card = profiles.read_json(root / "00_source" / "up_next.json", {}) or {}
    fast = profiles.read_json(root / "fast" / "card.json", {}) or {}
    outline = (profiles.read_json(root / "05_outline.json", {}) or {}) if fast else {}
    ep = profiles.read_json(root / "11_episode" / "episode.json", {}) or {}
    timing = profiles.read_json(root / "11_episode" / "timing.json", {}) or {}
    title = card.get("title") or ep.get("title") or outline.get("title") or fast.get("title") or root.name
    overview = card.get("overview") or fast.get("overview") or ep.get("angle") or ""
    day = root.name[:10] if len(root.name) > 10 and root.name[4] == "-" else None
    if day is None:  # a seed or fast run (<slug>-YYYYMMDD): the day it finished
        t = profiles.finished_at(root)
        day = datetime.fromtimestamp(t).strftime("%Y-%m-%d") if t else None
    secs = ep.get("duration") or timing.get("duration")
    return {"run": root.name, "title": title, "overview": overview, "summary": fast.get("summary") or "", "date": day,
            "minutes": round(secs / 60) if secs else None,
            "audio": f"/api/chapters/{root.name}/audio",
            "episode": f"#/chapter/{root.name}" if (root / "11_episode" / "episode.json").exists() else None,
            "intro": (root / "intro_brief.md").exists(),
            "asks": (root / "11_episode" / "timing.json").exists()}


def history(listener: Path) -> list[dict]:
    """Every finished chapter, newest first by when it finished (the order latest_chapter uses)."""
    done = [p for p in profiles.chapters(listener=listener) if profiles.finished(p)]
    done.sort(key=lambda p: (profiles.finished_at(p), p.name), reverse=True)
    return [_chapter_info(p) for p in done]


def state_line(card: dict | None, settings: dict, now: datetime, build: dict | None) -> tuple[str, str]:
    """(state, the small line under the card)."""
    from . import schedule
    if not settings["overnight"]["enabled"]:
        return "off", "Overnight chapters are off"
    if card is None:
        return "none", "The next plan is written this evening"
    st = card.get("status")
    if st == "building":
        live = build and build.get("state") == "making"
        return "building", (f"Building now: {build.get('label', '').lower()}" if live and build.get("label")
                            else "Building now")
    if st == "failed":
        return "failed", "Last night's chapter didn't finish"
    if st == "ready":
        return "waiting", "The next plan is written this evening"
    try:
        return "planned", schedule.next_label(settings, now)
    except Exception:  # a label is a nicety; never break the page over it
        return "planned", "Ready tomorrow morning"


def plan_failure(listener: Path, card: dict | None, settings: dict, now: datetime) -> dict | None:
    """The last planning pass, if it failed in this planning window (since the last build start) and no
    card has been planned since."""
    from . import planner, schedule
    fails = planner.failures(listener)
    if not fails:
        return None
    last, opened = fails[-1], schedule.last_start(settings, now)
    t = str(last.get("t") or "")[:19]
    if opened and t < opened.strftime("%Y-%m-%dT%H:%M:%S"):
        return None
    if card and str(card.get("planned_at") or "")[:19] >= t:
        return None
    return last


DEBRIEF_LINE = "Got five minutes to tell Ian how it went?"


def debrief_card(listener: Path) -> dict:
    """The library's debrief card (Tester Demo, "If they leave early"): shown to a tester (tester.json in the
    folder) whose debrief isn't done (study/debrief.json), unless Ian turned it off (tester.json debrief_card
    "off"); "on" shows it whatever, "auto" once a chapter was started (a folder under chapters/)."""
    tester = profiles.read_json(listener / "tester.json")
    if not isinstance(tester, dict):
        return {"show": False, "line": ""}
    done = (profiles.read_json(listener / "study" / "debrief.json") or {}).get("done")
    setting = tester.get("debrief_card") or "auto"
    show = not done and (setting == "on" or (setting == "auto" and bool(profiles.chapters(listener=listener))))
    return {"show": bool(show), "line": DEBRIEF_LINE}


def library_state(cfg: dict, now: datetime | None = None) -> dict:
    from . import runner, schedule
    now = now or datetime.now()
    listener = common.listener_dir(cfg)
    settings = profiles.settings(cfg, listener=listener)
    try:
        runner.reconcile(listener)  # a build that died leaves 'building': it didn't finish
    except OSError as e:
        print(f"  library: could not check the build ({e})")
    card = profiles.read_json(listener / "up_next.json")
    build = None
    if card and card.get("chapter"):
        build = profiles.read_json(listener / "chapters" / card["chapter"] / "status.json")
    state, line = state_line(card, settings, now, build)
    failed = plan_failure(listener, card, settings, now) if state in ("none", "waiting") else None
    if failed:
        line = f"Couldn't plan the next chapter: {failed.get('reason') or 'the planning pass failed'}"
    try:
        can, why = schedule.can_change(card, settings, now)
    except Exception:
        can, why = False, ""
    shown = card if state in ("planned", "building", "failed") else None
    latest = profiles.latest_chapter(listener=listener)
    return {"listener": listener.name, "now": now.isoformat(timespec="seconds"),
            "up_next": {"state": state, "line": line, "card": shown,
                        "change": state in ("planned", "failed"), "can_change": can,
                        "change_note": why, "plan_failed": (failed or {}).get("reason")},
            "latest": _chapter_info(latest) if latest else None,
            "history": history(listener),
            "settings": settings,
            "debrief": debrief_card(listener)}


def mount(app: FastAPI, cfg: dict, pipe=None, chat=None) -> None:
    from . import live
    live.mount(app, cfg, pipe, chat)  # the intro, Change and aside sessions on GPT-Live

    @app.get("/library", response_class=HTMLResponse)
    def library_page() -> HTMLResponse:
        return HTMLResponse(PAGE.read_text(encoding="utf-8"))

    @app.get("/library.js")
    def library_js() -> Response:  # the live sessions (Play's intro, Change); absent, the page plays plainly
        text = LIBRARY_JS.read_text(encoding="utf-8") if LIBRARY_JS.exists() else ""
        return Response(text, media_type="text/javascript")

    @app.get("/api/library")
    def library() -> dict:
        return library_state(cfg)

    @app.post("/api/settings")
    def save(changes: dict) -> dict:
        try:
            return {"settings": profiles.save_settings(cfg, changes, listener=common.listener_dir(cfg))}
        except StageError as e:
            raise HTTPException(400, str(e)) from None
