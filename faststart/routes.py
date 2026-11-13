"""The server's side of a fast run on the page (docs/fast-start.md, "On the page"; milestone 5).

    GET  /fast.js             page/fast.js, with config.yaml fast's page settings
    POST /api/fast/position   {"run", "state", "section", "piece", "t", "chapter_t", "waiting_for",
                               "since", "reason"} -> fast/player.json, which the run reads every tick

The run is another process, so the page's position reaches it through that one file (the report
dict of faststart/run.py's docstring, plus `at`: when the server received it). The run answers in
status.json (`waiting_for`, `check_in`), which the page already follows. The check-in itself goes
through the ask routes (ask/routes.py).

`mode_args` is what Pipeline.start_seed adds to the command for a chapter started fast.
"""

from __future__ import annotations

import json
import time
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from stages.common import ROOT, Run, StageError

from . import base

PAGE_JS = ROOT / "page" / "fast.js"


class Position(BaseModel):
    run: str
    state: Literal["idle", "playing", "waiting", "paused", "done"]
    section: int | None = None           # the section being played, or the one to play next
    piece: int | None = None             # 0-based, within the section
    t: float | None = None               # seconds into the section
    chapter_t: float | None = None       # seconds of audio heard so far
    waiting_for: int | None = None       # the section the listener is waiting for
    since: float | None = None           # when that wait began: epoch seconds
    reason: Literal["end_of_audio", "skip"] | None = None


def mode_args(r: Run, mode: str) -> list[str]:
    """What `new <run> --unattended` needs added for this mode. A chapter started fast is marked
    fast here, when its seed is set: that moment is the zero of its timeline, and from it on the
    folder is a fast run's whatever happens to the process (a seed lookup, Try again, add_note)."""
    if mode != "fast":
        return []
    base.mark_fast(r)
    return ["--mode", "fast"]


def page_settings(cfg: dict) -> dict:
    f = base.settings(cfg)
    return {**{k: f[k] for k in ("position_seconds", "short_wait_seconds", "silence_seconds", "gap_mic")},
            **(f.get("page") or {})}


def mount(app: FastAPI, cfg: dict, pipe) -> None:
    @app.get("/fast.js")
    def fast_js() -> Response:  # page/fast.js, with config.yaml fast's page settings
        if not PAGE_JS.exists():
            raise HTTPException(404, "page/fast.js is not there.")
        text = PAGE_JS.read_text(encoding="utf-8")
        return Response(text.replace("__FAST_SETTINGS__", json.dumps(page_settings(cfg))),
                        media_type="text/javascript")

    @app.post("/api/fast/position")
    def position(req: Position) -> dict:
        """Where the listener is. Written whole each time, atomically: the run may be reading it."""
        try:
            r = Run.open(req.run, runs_dir=pipe.runs_dir)  # a plain name inside the runs folder, or nothing
        except StageError:
            raise HTTPException(404, "No such chapter.") from None
        if not base.is_fast(r):
            raise HTTPException(404, "That chapter is not being made the fast way.")
        waiting = req.state == "waiting" and req.waiting_for is not None
        base.write_json(base.path(r, "player"), {
            "at": time.time(), "state": req.state, "section": req.section, "piece": req.piece, "t": req.t,
            "chapter_t": req.chapter_t, "waiting_for": req.waiting_for if waiting else None,
            "since": req.since if waiting else None, "reason": req.reason if waiting else None})
        return {"ok": True}
