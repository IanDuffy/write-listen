"""The local server for Phase 1 (docs/phase1-onboarding.md, milestone 2).

    .venv/bin/python server.py        # then open http://127.0.0.1:8765

It serves the one page, starts unattended pipeline runs, and streams their
status.json to the page. It listens on 127.0.0.1 only: nothing on the laptop is
exposed to the network. One listener, one run at a time.

    GET  /                          the page (page/index.html)
    POST /api/chapters              {"seed": "...", "why": "..."} -> start a run
    GET  /api/status                the current run's status.json (or idle)
    GET  /api/events                the same, as server-sent events, whenever it changes
    POST /api/chapters/{run}/retry  continue a run that stopped
    GET  /api/chapters/{run}/audio  the finished chapter's MP3

A run is `python cli.py new <run> --seed ... --unattended` in its own process, so
the server stays responsive and a run survives a server restart. Its console
output goes to runs/<run>/run.log. If the process ends without the pipeline
writing a final status (a crash), the server reports a problem itself.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from stages import seed as seed_stage
from stages.common import ROOT, RUNS_DIR, Run, StageError

PAGE = ROOT / "page" / "index.html"
HOST, PORT = "127.0.0.1", 8765


class Pipeline:
    """The one run the page follows, and the process making it."""

    def __init__(self, runs_dir: Path | None = None):
        self.runs_dir = runs_dir or RUNS_DIR
        self.run: str | None = None
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()

    def start(self, args: list[str], run: str) -> None:
        with open(self.runs_dir / run / "run.log", "a", encoding="utf-8") as log:  # the child keeps its own copy
            self.proc = subprocess.Popen([sys.executable, str(ROOT / "cli.py"), *args], cwd=ROOT,
                                         stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        self.run = run

    def busy(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def status(self) -> dict:
        if not self.run:
            latest = self._latest()
            if not latest:
                return {"state": "idle"}
            self.run = latest
        p = self.runs_dir / self.run / "status.json"
        try:
            status = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except json.JSONDecodeError:  # written atomically, so only a hand edit gets here
            status = None
        if status is None:
            status = {"run": self.run, "state": "making", "label": "Starting", "stages_done": 0,
                      "stages_total": 1, "minutes_left": None}
        if status.get("state") == "making" and not self.busy() and self.proc is not None:
            # The process ended but never wrote ready or problem: it crashed.
            status = {**status, "state": "problem", "message": "The chapter stopped unexpectedly.",
                      "detail": f"exit code {self.proc.returncode}; see runs/{self.run}/run.log"}
        return status

    def _latest(self) -> str | None:
        found = sorted(self.runs_dir.glob("*/status.json"), key=lambda p: p.stat().st_mtime)
        return found[-1].parent.name if found else None


class ChapterRequest(BaseModel):
    seed: str
    why: str = ""
    notes: str = ""


def create_app(pipeline: Pipeline | None = None) -> FastAPI:
    pipe = pipeline or Pipeline()
    app = FastAPI(title="write-listen")
    app.state.pipeline = pipe

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return PAGE.read_text(encoding="utf-8")

    @app.post("/api/chapters")
    def start(req: ChapterRequest) -> dict:
        topic = req.seed.strip()
        if not topic:
            raise HTTPException(400, "Say what the chapter should be about.")
        with pipe.lock:
            if pipe.busy():
                raise HTTPException(409, "A chapter is already being made.")
            text = seed_stage.seed_text(topic, req.why, req.notes)
            name = seed_stage.find_run(text, pipe.runs_dir) or seed_stage.run_name(topic, pipe.runs_dir)
            r = Run.open(name, create=True, runs_dir=pipe.runs_dir)
            if not r.path("seed").exists():
                try:
                    seed_stage.write(r, topic, req.why, req.notes)
                except StageError as e:
                    raise HTTPException(400, str(e)) from None
            pipe.start(["new", name, "--unattended"], name)
        return {"run": name}

    @app.post("/api/chapters/{run}/retry")
    def retry(run: str) -> dict:
        with pipe.lock:
            if pipe.busy():
                raise HTTPException(409, "A chapter is already being made.")
            try:
                r = Run.open(run, runs_dir=pipe.runs_dir)
            except StageError:
                raise HTTPException(404, "No such chapter.") from None
            if not r.path("seed").exists():
                raise HTTPException(400, "That run has no seed.")
            pipe.start(["new", run, "--unattended"], run)
        return {"run": run}

    @app.get("/api/status")
    def status() -> dict:
        return pipe.status()

    @app.get("/api/events")
    async def events() -> StreamingResponse:
        async def stream():
            last = None
            while True:
                now = json.dumps(pipe.status(), ensure_ascii=False)
                if now != last:
                    yield f"data: {now}\n\n"
                    last = now
                else:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @app.get("/api/chapters/{run}/audio")
    def audio(run: str) -> FileResponse:
        try:
            r = Run.open(run, runs_dir=pipe.runs_dir)
        except StageError:
            raise HTTPException(404, "No such chapter.") from None
        mp3 = r.path("chapter_mp3")
        if not mp3.exists():
            raise HTTPException(404, "That chapter is not ready yet.")
        return FileResponse(mp3, media_type="audio/mpeg")

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    print(f"  open http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
