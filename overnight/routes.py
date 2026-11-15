"""The library page and its API (docs/overnight-chapters.md, "Library page"; the morning flow and
Change by voice add their live-session routes here).

    GET  /library        page/library.html: Up next on top, the latest chapter below
    GET  /api/library    what the page shows (the card, its state line, the latest chapter, settings)
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
    """The latest chapter card: title, overview, date, length, and where its page is."""
    card = profiles.read_json(root / "00_source" / "up_next.json", {}) or {}
    ep = profiles.read_json(root / "11_episode" / "episode.json", {}) or {}
    timing = profiles.read_json(root / "11_episode" / "timing.json", {}) or {}
    title = card.get("title") or ep.get("title") or root.name
    overview = card.get("overview") or ep.get("angle") or ""
    day = root.name[:10] if len(root.name) > 10 and root.name[4] == "-" else None
    secs = ep.get("duration") or timing.get("duration")
    return {"run": root.name, "title": title, "overview": overview, "date": day,
            "minutes": round(secs / 60) if secs else None,
            "audio": f"/api/chapters/{root.name}/audio",
            "episode": f"/episode/index.html?run=/runs/{root.name}" if (root / "11_episode" / "episode.json").exists() else None,
            "intro": (root / "intro_brief.md").exists()}


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


def library_state(cfg: dict, now: datetime | None = None) -> dict:
    from . import schedule
    now = now or datetime.now()
    listener = common.listener_dir(cfg)
    settings = profiles.settings(cfg, listener=listener)
    card = profiles.read_json(listener / "up_next.json")
    build = None
    if card and card.get("chapter"):
        build = profiles.read_json(listener / "chapters" / card["chapter"] / "status.json")
    state, line = state_line(card, settings, now, build)
    try:
        can, why = schedule.can_change(card, settings, now)
    except Exception:
        can, why = False, ""
    shown = card if state in ("planned", "building", "failed") else None
    latest = profiles.latest_chapter(listener=listener)
    return {"listener": listener.name, "now": now.isoformat(timespec="seconds"),
            "up_next": {"state": state, "line": line, "card": shown,
                        "change": state in ("planned", "failed"), "can_change": can,
                        "change_note": why},
            "latest": _chapter_info(latest) if latest else None,
            "settings": settings}


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
