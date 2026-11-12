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
    POST /api/chat/start            a new conversation with the guide; it speaks first
    POST /api/chat                  {"message": "..."} -> the guide's reply (it may start a chapter)
    POST /api/chat/end              the page is closing: run the memory pass for the conversation
    GET  /api/chapters/{run}/audio  the finished chapter's MP3

A run is `python cli.py new <run> --unattended` in its own process and session, so
the server stays responsive, and stopping or restarting the server does not stop
the run. Its console
output goes to runs/<run>/run.log. If the process ends without the pipeline
writing a final status (a crash), the server reports a problem itself.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from stages import guide, memory
from stages import seed as seed_stage
from stages.common import ROOT, RUNS_DIR, Run, StageError, load_config

PAGE = ROOT / "page" / "index.html"
HOST, PORT = "127.0.0.1", 8765


def alive(pid) -> bool:
    """Whether a process with this id is running (status.json records the pipeline's pid)."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # it exists, under another user
        return True
    return True


class Pipeline:
    """The run the page follows, and the process making it if this server started it.

    A run is its own process in its own session, so stopping the server (Ctrl-C) does not
    stop the run. Whether a run is still going is read from the pid in its status.json, so
    it holds for runs started from the terminal and across server restarts."""

    def __init__(self, runs_dir: Path | None = None):
        self.runs_dir = runs_dir or RUNS_DIR
        self.run: str | None = None
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()

    def start(self, args: list[str], run: str) -> None:
        with open(self.runs_dir / run / "run.log", "a", encoding="utf-8") as log:  # the child keeps its own copy
            self.proc = subprocess.Popen([sys.executable, str(ROOT / "cli.py"), *args], cwd=ROOT,
                                         stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         start_new_session=True)
        self.run = run

    def start_seed(self, topic: str, why: str = "", notes: str = "", said: str = "") -> str:
        """Start (or continue) the unattended run for this seed; returns the run's name. Used by the
        text box (POST /api/chapters) and by the guide's start_chapter tool."""
        topic = topic.strip()
        if not topic:
            raise HTTPException(400, "Say what the chapter should be about.")
        with self.lock:
            if self.busy():
                raise HTTPException(409, "A chapter is already being made.")
            text = seed_stage.seed_text(topic, why, notes, said)
            name = seed_stage.find_run(text, self.runs_dir) or seed_stage.run_name(topic, self.runs_dir)
            r = Run.open(name, create=True, runs_dir=self.runs_dir)
            if not r.path("seed").exists():
                try:
                    seed_stage.write(r, topic, why, notes, said=said)
                except StageError as e:
                    raise HTTPException(400, str(e)) from None
            self.start(["new", name, "--unattended"], name)
        return name

    def _own_child_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _read(self, run: str | None) -> dict | None:
        if not run:
            return None
        p = self.runs_dir / run / "status.json"
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except json.JSONDecodeError:  # written atomically, so only a hand edit gets here
            return None

    def busy(self) -> bool:
        """A run is going: this server's child, or any run whose status says making with a live pid."""
        if self._own_child_running():
            return True
        latest = self._read(self._latest())
        return bool(latest and latest.get("state") == "making" and alive(latest.get("pid")))

    def status(self) -> dict:
        if not self._own_child_running():
            self.run = self._latest() or self.run  # follow the newest run, wherever it was started
        if not self.run:
            return {"state": "idle"}
        status = self._read(self.run)
        if status is None:
            status = {"run": self.run, "state": "making", "label": "Starting", "stages_done": 0,
                      "stages_total": 1, "minutes_left": None}
            if not self._own_child_running():  # no status and nothing running: it never got going
                status = {**status, "state": "problem", "message": "The chapter stopped before it started.",
                          "retryable": True, "detail": f"see runs/{self.run}/run.log"}
        elif status.get("state") == "making" and not self._own_child_running() and not alive(status.get("pid")):
            # The process ended without writing ready or problem (killed, or a crash).
            status = {**status, "state": "problem", "message": "The chapter stopped unexpectedly.",
                      "retryable": True, "detail": f"see runs/{self.run}/run.log"}
        return status

    def _latest(self) -> str | None:
        found = sorted(self.runs_dir.glob("*/status.json"), key=lambda p: p.stat().st_mtime)
        found += sorted(self.runs_dir.glob("*/run.log"), key=lambda p: p.stat().st_mtime)  # started, no status yet
        found.sort(key=lambda p: p.stat().st_mtime)
        return found[-1].parent.name if found else None


class ChapterRequest(BaseModel):
    seed: str
    why: str = ""
    notes: str = ""


class ChatRequest(BaseModel):
    message: str
    session: str | None = None  # the id /api/chat/start returned


def create_app(pipeline: Pipeline | None = None, cfg: dict | None = None, remember_on_start: bool = True) -> FastAPI:
    pipe = pipeline or Pipeline()
    cfg = cfg or load_config()
    app = FastAPI(title="write-listen")
    app.state.pipeline = pipe

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return PAGE.read_text(encoding="utf-8")

    @app.post("/api/chapters")
    def start(req: ChapterRequest) -> dict:
        return {"run": pipe.start_seed(req.seed, req.why, req.notes)}

    # --- the guide's conversation (milestone 3) ---------------------------------------
    chat = {"session": None, "lock": threading.Lock(), "passes": []}

    def remember(transcript=None) -> None:
        """The memory pass (profile.md, guide.md) in the background, for one ended conversation or
        for every one still waiting (after a restart). Passes run one at a time (memory.LOCK)."""
        def work():
            try:
                memory.after_session(cfg, transcript) if transcript else memory.run_pending(cfg)
            except Exception as e:  # never take the server down over memory
                print(f"  memory pass failed: {e}")
        t = threading.Thread(target=work, daemon=True)
        chat["passes"].append(t)
        t.start()

    if remember_on_start:
        remember()

    def new_session() -> guide.Session:
        if chat["session"] is not None:  # the previous conversation has ended: remember it first
            memory.after_session(cfg, chat["session"].transcript)
        for t in chat["passes"]:  # a returning guide must know who it is: wait for passes in flight
            t.join()
        chat["passes"] = []
        chat["session"] = guide.Session(cfg, pipe.start_seed, pipe.status, pipe.runs_dir)
        return chat["session"]

    def talk(text: str | None, fresh: bool, session_id: str | None = None) -> dict:
        with chat["lock"]:  # one turn at a time
            if not fresh and (chat["session"] is None or session_id != chat["session"].id):
                # The server restarted, or another conversation took over: don't answer out of context.
                raise HTTPException(409, "That conversation has ended.")
            session = new_session() if fresh else chat["session"]
            try:
                reply = session.say(text)
            except StageError as e:
                raise HTTPException(503, f"The guide can't talk right now: {e}") from None
        return {"reply": reply, "session": session.id}

    @app.post("/api/chat/start")
    def chat_start() -> dict:
        """A new conversation; the guide speaks first."""
        return talk(None, fresh=True)

    @app.post("/api/chat/end")
    async def chat_end(request: Request) -> dict:
        """The page is closing: the conversation has ended, so update the memory. The beacon carries
        the conversation's id, so a stale tab closing never ends another tab's conversation."""
        try:
            sid = (await request.json()).get("session")
        except Exception:
            sid = None
        with chat["lock"]:
            if chat["session"] is None or sid != chat["session"].id:
                return {"ended": False}
            session, chat["session"] = chat["session"], None
        if session is not None:
            remember(session.transcript)
        return {"ended": session is not None}

    @app.post("/api/chat")
    def chat_turn(req: ChatRequest) -> dict:
        if not req.message.strip():
            raise HTTPException(400, "Say something first.")
        return talk(req.message, fresh=False, session_id=req.session)

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
    async def events(request: Request) -> StreamingResponse:
        async def stream():
            last = None
            while not await request.is_disconnected():
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


app = create_app(remember_on_start=False)  # importing the module (tests, cli chat) touches no memory

if __name__ == "__main__":
    import uvicorn

    print(f"  open http://{HOST}:{PORT}")
    # An open page holds an event stream; don't wait for it to close on Ctrl-C.
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="warning", timeout_graceful_shutdown=2)
