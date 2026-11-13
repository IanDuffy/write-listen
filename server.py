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
    POST /api/chapters/{run}/stop   stop the run being made: its process and anything it started
    POST /api/reset                 forget the listener: end the conversation without remembering it,
                                    stop any chapter being made, move listener memory aside
    POST /api/chat/start            a new conversation with the guide; it speaks first
    POST /api/chat                  {"message": "..."} -> the guide's reply (it may start a chapter)
    POST /api/chat/end              the page is closing: run the memory pass for the conversation
    POST /api/voice/start           a voice conversation: a signed URL for the guide agent + dynamic variables
    POST /api/voice/resume          {"session", "reason"}: the guide stepped back and is coming back (the
                                    listener spoke or tapped, or the chapter is ready): a new connection
    POST /api/voice/tool            {"session", "name", "args"}: a client tool the agent called, run here
    POST /api/voice/line            {"session", "who", "text"}: a line for the transcript (or the call's id)
    GET  /api/chapters/{run}/audio  the finished chapter's MP3
    GET  /wake.js                   the page's local listener (page/wake.js), which brings the guide back

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
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from stages import guide, memory, voice
from stages import seed as seed_stage
from stages import new as new_stage
from stages.common import ROOT, RUNS_DIR, Run, StageError, load_config, load_prompt

# What reset moves out of listener/ (the guide's memory). voice.json, the agent's ids, stays: it holds
# nothing about the listener.
MEMORY_FILES = ("profile.md", "guide.md", "history.md", "seeds", "sessions")

PAGE = ROOT / "page" / "index.html"
WAKE_JS = ROOT / "page" / "wake.js"
PAGE_VOICE_KEYS = ("rest_after_silence_secs", "rest_after_ready_secs", "rest_after_no_words_secs",
                   "rest_max_minutes", "wake")
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

    def stop(self, run: str, cfg: dict) -> dict:
        """Stop the run being made. Ctrl-C first, so the pipeline records where it stopped
        (decisions.md) and its streaming Claude call is dropped; then, if it has not ended within a
        few seconds (research agents run in threads), terminate and kill its process group, which
        takes ffmpeg and anything else it started. Queued batch requests are cancelled. The status
        becomes "stopped"; the run can be continued later (python cli.py new <run> --unattended)."""
        with self.lock:
            r = Run.open(run, runs_dir=self.runs_dir)
            r.unattended = True  # so decide() writes to decisions.md, as the run itself does
            status = self._read(run) or {}
            own = self.proc is not None and self.proc.poll() is None and self.run == run
            pid = self.proc.pid if own else status.get("pid")
            was_running = own or (status.get("state") == "making" and alive(pid))
            if was_running:
                group = own or _leads_group(pid)  # a run started from a terminal shares the shell's group
                for sig, wait in ((signal.SIGINT, 5.0), (signal.SIGTERM, 3.0), (signal.SIGKILL, 2.0)):
                    _signal(pid, sig, group)
                    if _wait_gone(pid, self.proc if own else None, wait):
                        break
                if group:  # anything left in its group (an ffmpeg it started) goes too
                    _signal(pid, signal.SIGKILL, True)
            cancelled = _cancel_batches(r.root)
            if was_running or status.get("state") == "making":
                new_stage.write_status(r, cfg, "stopped", stage=status.get("stage"), label=status.get("label"),
                                       message="You stopped this chapter.", pid=None,
                                       history=status.get("history", []))
                r.decide("stopped", "Stopped from the page." + (f" Cancelled batches: {', '.join(cancelled)}."
                                                                 if cancelled else ""))
            if own:
                self.proc = None
        return {"run": run, "stopped": bool(was_running), "batches_cancelled": cancelled}

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


def _leads_group(pid) -> bool:
    try:
        return os.getpgid(pid) == pid
    except (ProcessLookupError, PermissionError, TypeError):
        return False


def _signal(pid, sig, group: bool) -> None:
    try:
        os.killpg(pid, sig) if group else os.kill(pid, sig)
    except (ProcessLookupError, PermissionError, TypeError):
        pass


def _wait_gone(pid, proc, seconds: float) -> bool:
    """Whether the process has ended within `seconds` (reaped, if it is the server's own child)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if proc is not None:
            if proc.poll() is not None:
                return True
        elif not alive(pid):
            return True
        time.sleep(0.1)
    return False


def _cancel_batches(root: Path) -> list[str]:
    """Cancel the Message Batches a stopped run left queued (stages/llm.py saves each id in
    _batch_<label>.json before it waits). A later run then finds the batch cancelled and sends the
    request again, streamed. Seed runs stream every stage, so there are usually none."""
    markers = sorted(root.glob("_batch_*.json"))
    if not markers:
        return []
    from stages import llm
    done = []
    for m in markers:
        try:
            batch_id = json.loads(m.read_text(encoding="utf-8"))["batch_id"]
            llm.get_client().messages.batches.cancel(batch_id)
            done.append(batch_id)
        except Exception as e:  # already ended, or no network: the run's next start sorts it out
            print(f"  could not cancel the batch in {m.name}: {e}")
    return done


class ChapterRequest(BaseModel):
    seed: str
    why: str = ""
    notes: str = ""


class ChatRequest(BaseModel):
    message: str
    session: str | None = None  # the id /api/chat/start returned


class VoiceTool(BaseModel):
    session: str
    name: str
    args: dict = {}


class VoiceResume(BaseModel):
    session: str
    reason: str = "tap"  # "spoke", "tap", "typed" or "ready"


class VoiceLine(BaseModel):
    session: str
    who: str            # "listener", "guide", or "page" (the guide stepped back, and why)
    text: str = ""
    conversation_id: str | None = None  # sent once, when the voice connection opens


def create_app(pipeline: Pipeline | None = None, cfg: dict | None = None, remember_on_start: bool = True) -> FastAPI:
    pipe = pipeline or Pipeline()
    cfg = cfg or load_config()
    app = FastAPI(title="write-listen")
    app.state.pipeline = pipe

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        # The ElevenLabs browser client, pinned in config.yaml (voice.client_js), and the page's share
        # of the voice settings (when the guide steps back, and what brings it back).
        settings = json.dumps({k: cfg["voice"][k] for k in PAGE_VOICE_KEYS})
        return (PAGE.read_text(encoding="utf-8").replace("__VOICE_CLIENT_JS__", cfg["voice"]["client_js"])
                .replace("__VOICE_SETTINGS__", settings))

    @app.get("/wake.js")
    def wake_js() -> FileResponse:
        return FileResponse(WAKE_JS, media_type="text/javascript", headers={"Cache-Control": "no-cache"})

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

    # --- the guide's voice (milestone 4): the ElevenLabs agent talks; the tools run here ------------

    def current(session_id: str) -> guide.Session:
        if chat["session"] is None or session_id != chat["session"].id:
            raise HTTPException(409, "That conversation has ended.")
        return chat["session"]

    @app.post("/api/voice/start")
    def voice_start() -> dict:
        """A new voice conversation: a signed URL for the private agent (the key stays here), and the
        guide prompt's values as dynamic variables. The page then tells the guide the listener arrived."""
        with chat["lock"]:
            try:
                url = voice.signed_url(cfg)  # creates or updates the agent first if the prompt or tools changed
            except Exception as e:
                raise HTTPException(503, f"Voice isn't available: {e}") from None
            session = new_session()
            opened = load_prompt("guide_opened").strip()
            session.record("page", text=opened, mode="voice")
        return {"session": session.id, "signed_url": url, "dynamic_variables": voice.dynamic_variables(session),
                "opened": opened, "ready_note": load_prompt("guide_ready").strip()}

    @app.post("/api/voice/resume")
    def voice_resume(req: VoiceResume) -> dict:
        """The same conversation on a new connection: the guide stepped back (its step_back tool, or the
        page after a silence) and the listener spoke or tapped, or the chapter became ready. The agent
        remembers nothing between connections, so it gets the conversation so far with its prompt, and
        the page's note about why it is back as its first turn (a tap, the chapter: it speaks first)."""
        with chat["lock"]:
            session = current(req.session)
            if req.reason == "ready":
                title = (pipe.status() or {}).get("title") or "your chapter"
                note = load_prompt("guide_ready").strip().replace("{title}", title)
            elif req.reason == "typed":  # what they typed is the first turn
                note = ""
            elif req.reason == "spoke":
                # A sound woke it, which may not have been the listener (a door, a television): it
                # listens, and the page plays two soft notes. Speaking on every sound was the
                # lingering voice again. What they say next is its first turn.
                note = ""
            else:
                note = load_prompt("guide_back").strip()
            try:
                url = voice.signed_url(cfg)
            except Exception as e:
                raise HTTPException(503, f"Voice isn't available: {e}") from None
            variables = voice.dynamic_variables(session, resumed=True)  # before the note: it is not "earlier"
            if note:
                session.record("page", text=note, mode="voice")
        return {"session": session.id, "signed_url": url, "dynamic_variables": variables, "opened": note}

    @app.post("/api/voice/tool")
    def voice_tool(req: VoiceTool) -> dict:
        """A client tool the agent called: run it in the conversation's session (as the typed chat does)."""
        with chat["lock"]:
            session = current(req.session)
            try:
                out = session.tool(req.name, dict(req.args or {}))
            except Exception as e:
                out = {"error": f"{type(e).__name__}: {e}"}
            session.record("tool", name=req.name, input=req.args, result=out)
        return out

    @app.post("/api/voice/line")
    def voice_line(req: VoiceLine) -> dict:
        """What was said, for the transcript the memory pass reads; or the connection's id."""
        with chat["lock"]:
            session = current(req.session)
            if req.conversation_id and req.conversation_id not in session.voice_conversations:
                session.voice_conversations.append(req.conversation_id)
                session.record("session", conversation_id=req.conversation_id)
            if req.text.strip() and req.who in ("listener", "guide", "page"):
                session.record(req.who, text=req.text.strip())
        return {"ok": True}

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
            # ElevenLabs' record of each connection (latency per turn, cost), once it has processed it.
            for n, cid in enumerate(session.voice_conversations, 1):
                threading.Thread(target=_save_voice, args=(cfg, session.id, cid, n), daemon=True).start()
        return {"ended": session is not None}

    @app.post("/api/chat")
    def chat_turn(req: ChatRequest) -> dict:
        if not req.message.strip():
            raise HTTPException(400, "Say something first.")
        return talk(req.message, fresh=False, session_id=req.session)

    @app.post("/api/chapters/{run}/stop")
    def stop(run: str) -> dict:
        try:
            return pipe.stop(run, cfg)
        except StageError:
            raise HTTPException(404, "No such chapter.") from None

    @app.post("/api/reset")
    def reset() -> dict:
        """Forget the listener and start fresh. The conversation in progress ends without a memory
        pass (that would write some of it back), a chapter being made is stopped (it would add
        itself to history.md when done), and listener memory is moved to listener/_cleared_<time>/,
        which nothing reads. The finished chapters in runs/ stay, and so do ElevenLabs' own records
        of past voice calls."""
        with chat["lock"]:
            chat["session"] = None
            for t in chat["passes"]:  # a pass in flight would write profile.md after the move
                t.join()
            chat["passes"] = []
            stopped = None
            if pipe.busy():
                status = pipe.status()
                if status.get("run"):
                    stopped = pipe.stop(status["run"], cfg)["run"]
            with memory.LOCK:
                d = guide.listener_dir(cfg)
                found = [n for n in MEMORY_FILES if (d / n).exists()]
                moved_to = None
                if found:
                    moved_to = d / f"_cleared_{time.strftime('%Y%m%d-%H%M%S')}"
                    n = 1
                    while moved_to.exists():
                        n += 1
                        moved_to = moved_to.with_name(f"_cleared_{time.strftime('%Y%m%d-%H%M%S')}-{n}")
                    moved_to.mkdir(parents=True)
                    for name in found:
                        (d / name).rename(moved_to / name)
        return {"forgotten": found, "moved_to": moved_to.name if moved_to else None, "stopped": stopped}

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


def _save_voice(cfg: dict, session_id: str, conversation_id: str, n: int = 1) -> None:
    try:
        voice.save_conversation(cfg, session_id, conversation_id, n=n)
    except Exception as e:  # never take the server down over a call record
        print(f"  saving the voice record failed: {e}")


app = create_app(remember_on_start=False)  # importing the module (tests, cli chat) touches no memory

if __name__ == "__main__":
    import uvicorn

    print(f"  open http://{HOST}:{PORT}")
    # An open page holds an event stream; don't wait for it to close on Ctrl-C.
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="warning", timeout_graceful_shutdown=2)
