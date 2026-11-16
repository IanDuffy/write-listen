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
    /api/live/*, GET /live.js       one GPT-Live session for the conversation and the reading (voice/gpt_live/
                                    routes.py), the page when config live.provider is gpt_live (page/live.html)
    GET  /api/chapters/{run}/audio  the finished chapter's MP3
    GET  /wake.js                   the page's local listener (page/wake.js), which brings the guide back
    /api/ask/*                      asking mid-chapter, by voice (ask/routes.py, docs/ask-voice.md)
    GET /fast.js, POST /api/fast/position   a fast run on the page (faststart/routes.py, docs/fast-start.md)
    GET  /episode/, /runs/          the episode page (read along) and the run folders it reads

The page and the status may name a finished chapter (?chapter=<run>), to play and ask about it.

Tester mode (config demo.enabled; docs/tester-demo-design.md): each request runs as the tester its cookie
names (TesterScope, common.listener_scope), with a Pipeline and a conversation of their own (state_for);
without a tester only GET /, /t/<token>, /admin*, /healthz and the gate's script answer. Chapter builds
share a limit of demo.max_builds at once; more wait their turn (BuildQueue). GET /healthz says the server
is up. With demo off, one listener as before.

A run is `python cli.py new <run> --unattended` in its own process and session, so
the server stays responsive, and stopping or restarting the server does not stop
the run. Its console
output goes to runs/<run>/run.log. If the process ends without the pipeline
writing a final status (a crash), the server reports a problem itself.
"""

from __future__ import annotations

import asyncio
import functools
import importlib.util
import json
import re
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Literal

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ask import aside as ask_aside
from ask import routes as ask_routes
from demo import routes as demo_routes
from faststart import base as fast_base
from faststart import guide_hooks as fast_hooks
from faststart import routes as fast_routes
from overnight import profiles
from overnight import routes as overnight_routes
from stages import guide, memory, voice
from stages import seed as seed_stage
from stages import new as new_stage
from stages import common
from stages.common import ROOT, Run, StageError, load_config, load_prompt
from voice.gpt_live import routes as live_routes


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
    it holds for runs started from the terminal and across server restarts.

    `listener`: a tester's pipeline (docs/tester-demo-design.md): its chapters are that listener's,
    whatever request or thread asks. `queue`: the server-wide build limit (demo.max_builds), or None."""

    def __init__(self, runs_dir: Path | None = None, listener: str | None = None, queue=None,
                 cfg: dict | None = None):
        self._runs_dir = runs_dir  # fixed (tests), or None: the listener's chapters
        self.listener = listener
        self.queue = queue
        self.cfg = cfg  # for the run-sizing hook; create_app fills it in
        self.run: str | None = None
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()

    @property
    def runs_dir(self) -> Path:
        if self._runs_dir:
            return self._runs_dir
        if self.listener and common.RUNS_DIR is None:  # a thread (the queue's watcher) has no request to go by
            return common.LISTENERS_DIR / self.listener / "chapters"
        return common.default_runs_dir()

    def start(self, args: list[str], run: str) -> None:
        """Start the run's process, or, past the server's build limit, queue it (it waits its turn)."""
        if self.queue is not None and not self.queue.submit(self, args, run):
            self.run = run  # the page follows it while it waits
            return
        try:
            self._launch(args, run)
        except BaseException:
            if self.queue is not None:
                self.queue.release(self.runs_dir / run)
            raise
        if self.queue is not None:
            self.queue.started(self.runs_dir / run, self.proc)

    def _launch(self, args: list[str], run: str) -> None:
        env = dict(os.environ)
        # Always pinned: the child resolves its run folder, profile and memory by this id, never by whichever
        # listener happens to be active (or whichever tester's request came last) when it looks.
        env[common.LISTENER_ENV] = self.listener or common.active_listener_id()
        with open(self.runs_dir / run / "run.log", "a", encoding="utf-8") as log:  # the child keeps its own copy
            self.proc = subprocess.Popen([sys.executable, str(ROOT / "cli.py"), *args], cwd=ROOT, env=env,
                                         stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         start_new_session=True)
        self.run = run

    def start_seed(self, topic: str, why: str = "", notes: str = "", said: str = "", mode: str = "overnight",
                   live: bool = False) -> str:
        """Start (or continue) the unattended run for this seed; returns the run's name. Used by the
        text box (POST /api/chapters) and by the guide's start_chapter tool (which asks for mode "fast":
        docs/fast-start.md). A seed's unfinished run is continued only in the mode that made it."""
        topic = topic.strip()
        if not topic:
            raise HTTPException(400, "Say what the chapter should be about.")
        with self.lock:
            if self.busy():
                raise HTTPException(409, "A chapter is already being made.")
            text = seed_stage.seed_text(topic, why, notes, said)
            def ok(root):  # a fast run is continued only in the voice it was started in (`live`: read live)
                return fast_base.continues(root, mode) and (
                    not (Path(root) / fast_base.FILES["mode"]).exists()
                    or fast_base.voice_matches(root, live and live_voice_on()))
            name = seed_stage.find_run(text, self.runs_dir, ok=ok) or seed_stage.run_name(topic, self.runs_dir)
            r = Run.open(name, create=True, runs_dir=self.runs_dir)
            if not r.path("seed").exists():
                try:
                    seed_stage.write(r, topic, why, notes, said=said)
                except StageError as e:
                    raise HTTPException(400, str(e)) from None
            args = ["new", name, "--unattended", *fast_routes.mode_args(r, mode, live=live)]
            if mode == "fast":
                self._size(r)  # before the start: the run reads its config.yaml as it begins
            self.start(args, name)
        if mode == "fast" and live:
            self._card(r.root)
        return name

    def _size(self, r: Run) -> None:
        """A listener whose chapter.minutes differs from config.yaml's (a tester's 10 minutes) gets the run
        sized to it: <run>/config.yaml's fast: block, from faststart.base.sized_overrides (fast runs
        otherwise ignore chapter.minutes). Skipped while that function is not there."""
        sized = getattr(fast_base, "sized_overrides", None)
        owner = common.owner_of(r.root)
        if sized is None or owner is None:
            return
        cfg = self.cfg or load_config()
        minutes = profiles.settings(cfg, listener=owner)["chapter"]["minutes"]
        if minutes == ((cfg.get("listener_settings") or {}).get("chapter") or {}).get("minutes"):
            return
        p = r.root / "config.yaml"
        over = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}
        fast = common._merge(over.get("fast") or {}, sized(minutes))
        if fast != over.get("fast"):
            fast_base.write_atomic(p, yaml.safe_dump({**over, "fast": fast}, sort_keys=False, allow_unicode=True))

    def _card(self, root: Path) -> None:
        """The chapter card (title, overview, summary) at t = 0, in a thread: faststart/card.py, when there.
        Only for the live page's chapters, the one place a card is shown."""
        if importlib.util.find_spec("faststart.card") is None:
            return
        from faststart import card
        cfg = self.cfg or load_config()
        owner = common.owner_of(root)
        cfg = {**cfg, "listener_dir": str(owner)} if owner is not None else common.pinned(cfg)

        def work():
            try:
                card.start(root, cfg)
            except Exception as e:  # a card is a nicety; never the chapter
                print(f"  chapter card for {root.name} failed: {type(e).__name__}: {e}")
        threading.Thread(target=work, name=f"card-{root.name}", daemon=True).start()

    def stop(self, run: str, cfg: dict) -> dict:
        """Stop the run being made. Ctrl-C first, so the pipeline records where it stopped
        (decisions.md) and its streaming Claude call is dropped; then, if it has not ended within a
        few seconds (research agents run in threads), terminate and kill its process group, which
        takes ffmpeg and anything else it started. Queued batch requests are cancelled. The status
        becomes "stopped"; the run can be continued later (python cli.py new <run> --unattended)."""
        with self.lock:
            r = Run.open(run, runs_dir=self.runs_dir)
            if self.queue is not None:  # waiting its turn: it never starts
                self.queue.remove(r.root)
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
                                       history=status.get("history", []),
                                       # a fast run's fields: the live page still knows the run for what it is
                                       **{k: status[k] for k in ("mode", "voice", "sections", "first_audio") if k in status})
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

    def _waiting(self, run: str | None, status: dict) -> bool:
        """The run is in the build queue, waiting its turn (its status has no pid yet)."""
        return bool(run and status.get("queued") and self.queue is not None and self.queue.holds(self.runs_dir / run))

    def busy(self) -> bool:
        """A run is going: this server's child, any run whose status says making with a live pid, or one
        waiting its turn in the build queue."""
        if self._own_child_running():
            return True
        name = self._latest()
        latest = self._read(name)
        return bool(latest and latest.get("state") == "making"
                    and (alive(latest.get("pid")) or self._waiting(name, latest)))

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
        elif (status.get("state") == "making" and not self._own_child_running() and not alive(status.get("pid"))
              and not self._waiting(self.run, status)):
            # The process ended without writing ready or problem (killed, or a crash).
            status = {**status, "state": "problem", "message": "The chapter stopped unexpectedly.",
                      "retryable": True, "detail": f"see runs/{self.run}/run.log"}
        return {**status, "run": self.run}  # the folder it was read from (a migrated copy may name its old one)

    def detach(self) -> None:
        """Stop following the run this server started (Start fresh: it was the previous listener's).
        The run itself keeps going in its own process and writes into its own listener's folder."""
        with self.lock:
            self.proc, self.run = None, None

    def _latest(self) -> str | None:
        """The newest seed or fast run. Overnight builds share the chapters folder but are the overnight
        runner's (wl overnight ...): the page never follows them, retries them or waits for them."""
        found = [p for pattern in ("*/status.json", "*/run.log")  # run.log: started, no status yet
                 for p in self.runs_dir.glob(pattern) if not profiles.overnight_build(p.parent)]
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


# --- the build limit (docs/tester-demo-design.md, Per-listener server state) ----------------------

QUEUE_WATCH_SECONDS = 2.0
QUEUED_STATUS = {"state": "making", "stage": "queued", "label": "Waiting its turn", "queued": True, "pid": None,
                 "stages_done": 0, "stages_total": 1, "minutes_left": None, "title": None, "audio": None,
                 "message": "Other chapters are being made; this one starts as soon as one of them is done.",
                 "detail": None, "retryable": True}


class BuildQueue:
    """At most `limit` chapter builds at once, server-wide (demo.max_builds), so several testers' runs don't
    hit the APIs' rate limits together. Running builds are counted from every listener's status.json (making,
    with a live pid) plus the processes this server started that haven't written one yet. A start beyond
    the limit waits in shared/build_queue.json (first in, first out; it survives a restart) behind a
    server-written status.json that says so, and a watcher thread starts it when a slot frees.

    Locks: a Pipeline's lock is always taken before this one, never after (start_seed and stop hold theirs
    when they call in; the watcher pops entries under this lock and launches them after releasing it).

    One watcher per process: the queue of the app that last served a request (importing server.py makes an
    app too, which never serves when server.py is run as a script). A watcher whose queue was superseded, or
    whose queue file moved (a test's temporary folder, gone), stops."""

    def __init__(self, limit: int, launch):
        self.limit = max(1, int(limit))
        self.launch = launch      # launch(entry) -> bool: start the entry's run now (False: it was stopped)
        self.lock = threading.Lock()
        self.reserved: set[str] = set()                 # admitted, process not started yet
        self.own: dict[str, subprocess.Popen] = {}      # started here, maybe no status.json yet

    @staticmethod
    def path() -> Path:
        return common.shared_dir() / "build_queue.json"

    def entries(self) -> list[dict]:
        data = fast_base.read_json(self.path(), {}) or {}
        return [e for e in data.get("queue") or [] if isinstance(e, dict) and e.get("root") and e.get("run")]

    def _save(self, entries: list[dict]) -> None:
        fast_base.write_json(self.path(), {"queue": entries})

    def holds(self, root) -> bool:
        return any(e["root"] == str(root) for e in self.entries())

    def running(self) -> set[str]:
        """The run folders being built now (call with the lock held)."""
        out = set(self.reserved)
        for root, proc in list(self.own.items()):
            if proc.poll() is None:
                out.add(root)
            else:
                self.own.pop(root, None)
        for p in common.LISTENERS_DIR.glob("*/chapters/*/status.json"):
            s = fast_base.read_json(p) or {}
            if s.get("state") == "making" and not s.get("queued") and alive(s.get("pid")):
                out.add(str(p.parent))
        return out

    def submit(self, pipe: "Pipeline", args: list[str], run: str) -> bool:
        """True: a slot is reserved, start it now (then started()). False: it is queued."""
        root = pipe.runs_dir / run
        with self.lock:
            entries = [e for e in self.entries() if e["root"] != str(root)]
            if not entries and len(self.running() - {str(root)}) < self.limit:
                self.reserved.add(str(root))
                return True
            old = fast_base.read_json(root / "status.json") or {}
            entries.append({"lid": pipe.listener, "run": run, "root": str(root), "args": list(args),
                            "queued_at": fast_base.now_iso(), "fresh": not old})
            self._save(entries)
            _write_queued_status(root, run, old)
        print(f"  build queue: {run} waits its turn ({len(entries)} waiting)")
        self.ensure_watcher()
        return False

    def started(self, root, proc) -> None:
        with self.lock:
            self.reserved.discard(str(root))
            if proc is not None:
                self.own[str(root)] = proc

    def release(self, root) -> None:
        with self.lock:
            self.reserved.discard(str(root))

    def remove(self, root) -> bool:
        """Take a run out of the queue (stopped while it waited). Whether it was there."""
        with self.lock:
            entries = self.entries()
            keep = [e for e in entries if e["root"] != str(root)]
            if len(keep) != len(entries):
                self._save(keep)
            return len(keep) != len(entries)

    def tick(self) -> list[str]:
        """Start queued runs while slots are free; returns the runs started."""
        with self.lock:
            entries = self.entries()
            # A run stopped or removed by hand while it waited leaves the queue.
            keep = [e for e in entries if (fast_base.read_json(Path(e["root"]) / "status.json") or {}).get("queued")]
            free = max(0, self.limit - len(self.running()))
            go, keep = keep[:free], keep[free:]
            if len(keep) != len(entries):
                self._save(keep)
            self.reserved.update(e["root"] for e in go)
        started = []
        for e in go:
            try:
                if self.launch(e):
                    started.append(e["run"])
            except Exception as ex:  # the run shows as a problem the listener can retry; the queue moves on
                print(f"  build queue: {e['run']} could not start: {type(ex).__name__}: {ex}")
                root = Path(e["root"])
                if root.is_dir():
                    fast_base.write_json(root / "status.json", {
                        **(fast_base.read_json(root / "status.json") or {}), "run": e["run"], "state": "problem",
                        "queued": False, "message": "The chapter couldn't start.", "retryable": True,
                        "detail": f"{type(ex).__name__}: {ex}", "updated": fast_base.now_iso()})
            finally:
                self.release(e["root"])
        return started

    def ensure_watcher(self) -> None:
        with _WATCHER_LOCK:
            t = _WATCHER.get("thread")
            if _WATCHER.get("queue") is self and t is not None and t.is_alive():
                return
            _WATCHER["queue"] = self
            _WATCHER["thread"] = t = threading.Thread(target=self._watch, args=(self.path(),), name="build-queue",
                                                      daemon=True)
            t.start()

    def _watch(self, path: Path) -> None:
        while True:
            time.sleep(QUEUE_WATCH_SECONDS)
            if _WATCHER.get("queue") is not self or self.path() != path:
                return
            try:
                if self.entries():
                    self.tick()
            except Exception as e:  # never let the watcher die over one bad pass
                print(f"  build queue: {type(e).__name__}: {e}")


_WATCHER: dict = {"queue": None, "thread": None}
_WATCHER_LOCK = threading.Lock()


def _write_queued_status(root: Path, run: str, old: dict) -> None:
    """The status of a run waiting its turn. It keeps what the page and the guide know a run by (a fast run's
    mode and voice, a retried run's sections), and no start time: the run's own first status sets that."""
    info = fast_base.read_json(root / fast_base.FILES["mode"]) or {}
    keep = {k: old[k] for k in ("started", "history", "sections", "first_audio") if k in old}
    if info.get("mode") == "fast":
        keep["mode"] = "fast"
        if info.get("voice"):
            keep["voice"] = info["voice"]
    fast_base.write_json(root / "status.json", {"run": run, **QUEUED_STATUS, **keep, "updated": fast_base.now_iso()})


def _reset_zero(root: Path) -> None:
    """A fast run that waited its turn: its timeline starts now, not when it was queued, so the time to the
    first audio (faststart/timings.py's medians, the page's ring) doesn't count the wait."""
    p = root / fast_base.FILES["mode"]
    info = fast_base.read_json(p)
    if isinstance(info, dict) and info.get("seed_set") is not None:
        fast_base.write_json(p, {**info, "seed_set": time.time(), "seed_set_at": fast_base.now_iso()})


# --- testers (docs/tester-demo-design.md, Identity) ---------------------------------------------

LID_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")
# What answers without a tester's cookie when demo.enabled: the blank dot page and the gate's needs, the
# link that sets the cookie, the admin (part B guards it) and the health check. Everything else is a 404.
OPEN_EXACT = {("GET", "/"), ("HEAD", "/"), ("GET", "/healthz"), ("HEAD", "/healthz"), ("GET", "/interview.js")}
OPEN_PREFIX = ("/admin", "/api/admin")


def demo_on(cfg: dict) -> bool:
    return bool((cfg.get("demo") or {}).get("enabled"))


def _open_without_tester(method: str, path: str) -> bool:
    if (method, path) in OPEN_EXACT or path.startswith("/favicon"):
        return True
    if method in ("GET", "HEAD") and path.startswith("/t/"):
        return True
    return path.startswith(OPEN_PREFIX)  # /admin* and /api/admin*: part B guards them itself


def tester_for(scope) -> tuple[str | None, dict | None]:
    """(lid, tester record) for a request, from demo.testers (part B) reading its cookie. A missing module,
    an error or an id that isn't a plain folder name all count as no tester."""
    try:
        from demo import testers
    except ImportError:
        return None, None
    try:
        lid = testers.listener_for_request(scope)
    except Exception as e:
        print(f"  tester lookup failed: {type(e).__name__}: {e}")
        return None, None
    if not lid or not LID_RE.fullmatch(str(lid)):
        return None, None
    record = profiles.read_json(common.LISTENERS_DIR / lid / "tester.json", {}) or {}
    return lid, {"lid": lid, **record}


class TesterScope:
    """Pure ASGI middleware: with demo.enabled, each request runs as the tester its cookie names
    (common.listener_scope), so every listener_dir() under it, in the threadpool and in the event stream
    alike, is that tester's folder; request.state.tester is their record. Without a tester only the paths
    above answer. With demo off it passes everything through untouched. (Pure ASGI rather than
    BaseHTTPMiddleware: the context it sets must reach the streamed body of /api/events.)"""

    def __init__(self, app, cfg: dict, on_request=None):
        self.app, self.cfg, self.on_request = app, cfg, on_request

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or not demo_on(self.cfg):
            await self.app(scope, receive, send)
            return
        lid, record = tester_for(scope)
        scope.setdefault("state", {})["tester"] = record
        if self.on_request is not None:
            self.on_request()
        if lid is None:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            if not _open_without_tester(scope.get("method", "GET"), scope.get("path", "")):
                await send({"type": "http.response.start", "status": 404, "headers": [(b"content-length", b"0")]})
                await send({"type": "http.response.body", "body": b""})
                return
        with common.listener_scope(lid):
            await self.app(scope, receive, send)


class ChapterRequest(BaseModel):
    seed: str
    why: str = ""
    notes: str = ""
    mode: Literal["overnight", "fast"] = "overnight"  # the text box makes a chapter the overnight way unless asked


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


class State:
    """One listener's server state: the pipeline its page follows and its conversation with the guide
    (the current guide.Session, the lock that keeps its turns in order, memory passes in flight)."""

    def __init__(self, pipe: Pipeline):
        self.pipe = pipe
        self.chat = {"session": None, "lock": threading.Lock(), "passes": []}


class _Here:
    """Attributes of the current request's listener's object: what the routes mounted with `pipe` hold,
    so the route code written for one listener serves each tester its own."""

    def __init__(self, get):
        object.__setattr__(self, "_get", get)

    def __getattr__(self, name):
        return getattr(self._get(), name)


class _ChatHere:
    """The current request's listener's chat dict (overnight/live.py appends its memory passes to it)."""

    def __init__(self, get):
        self._get = get

    def __getitem__(self, key):
        return self._get()[key]

    def __setitem__(self, key, value):
        self._get()[key] = value


class _Conversation:
    """voice/gpt_live/routes.py's handle on the conversation: lock, new_session(), current(id)."""

    def __init__(self, chat, new_session, current):
        self._chat, self.new_session, self.current = chat, new_session, current

    @property
    def lock(self) -> threading.Lock:
        return self._chat()["lock"]


_APP = {"state_for": None}  # the newest app's registry, for state_for() below


def state_for(lid: str | None) -> State:
    """A listener's server state in the newest app (created on first use). Part B's admin reset and
    delete use it to drop a tester's conversation and stop their chapter before moving the folder."""
    if _APP["state_for"] is None:
        raise RuntimeError("no app has been created yet")
    return _APP["state_for"](lid)


def create_app(pipeline: Pipeline | None = None, cfg: dict | None = None, remember_on_start: bool = True) -> FastAPI:
    pipe0 = pipeline or Pipeline()
    cfg = cfg or load_config()
    if pipe0.cfg is None:
        pipe0.cfg = cfg
    app = FastAPI(title="write-listen")
    app.state.pipeline = pipe0
    # With demo off there is one listener, as there always was: one State, the pipeline given. With demo on,
    # each tester gets a State of their own on first use, and their runs share the server's build limit.
    default = State(pipe0)
    states: dict[str, State] = {}
    registry = threading.Lock()
    queue = BuildQueue((cfg.get("demo") or {}).get("max_builds", 3), lambda e: launch(e)) if demo_on(cfg) else None
    if queue is not None and remember_on_start:  # the real server: runs queued before a restart go on waiting
        queue.ensure_watcher()

    def state_for(lid: str | None) -> State:
        if not demo_on(cfg) or not lid:
            return default
        with registry:
            st, fresh = states.get(lid), False
            if st is None:
                st = states[lid] = State(Pipeline(listener=lid, queue=queue, cfg=cfg))
                fresh = True
        if fresh and remember_on_start:  # conversations a restart left without their memory pass
            with common.listener_scope(lid):
                remember(st)
        return st

    def here() -> State:
        return state_for(common.REQUEST_LISTENER.get())

    def launch(entry: dict) -> bool:
        """The queue's watcher starts a run that waited its turn, in its listener's pipeline."""
        p = state_for(entry.get("lid")).pipe
        root = Path(entry["root"])
        with p.lock:
            if not (fast_base.read_json(root / "status.json") or {}).get("queued"):
                return False  # stopped while it waited
            if entry.get("fresh"):
                _reset_zero(root)
            p._launch(list(entry["args"]), entry["run"])
            queue.started(root, p.proc)
        print(f"  build queue: {entry['run']} started")
        return True

    app.state.state_for = state_for
    app.state.build_queue = queue
    _APP["state_for"] = state_for
    pipe = _Here(lambda: here().pipe)  # what the routes below and the mounted ones hold
    chat = _ChatHere(lambda: here().chat)
    app.add_middleware(TesterScope, cfg=cfg, on_request=queue.ensure_watcher if queue is not None else None)
    demo_routes.mount(app, cfg)  # before the page route: with demo.enabled its GET / wins over the one below

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def page(chapter: str | None = None) -> str:
        if live(cfg) and not chapter:  # one GPT-Live session (page/live.html); a finished chapter keeps the old page
            return live_routes.LIVE_PAGE.read_text(encoding="utf-8")
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
        return {"run": pipe.start_seed(req.seed, req.why, req.notes, mode=req.mode)}

    # --- the guide's conversation (milestone 3) ---------------------------------------

    def remember(st: State, transcript=None) -> None:
        """The memory pass (profile.md, guide.md) in the background, for one ended conversation or
        for every one still waiting (after a restart). Passes run one at a time (memory.LOCK).
        Pinned to the listener now: the thread has no request to tell it whose memory it is."""
        pcfg = common.pinned(cfg)

        def work():
            try:
                memory.after_session(pcfg, transcript) if transcript else memory.run_pending(pcfg)
            except Exception as e:  # never take the server down over memory
                print(f"  memory pass failed: {e}")
        t = threading.Thread(target=work, daemon=True)
        st.chat["passes"].append(t)
        t.start()

    if remember_on_start:
        remember(default)

    def new_session(live: bool = False) -> guide.Session:
        st = here()
        chat, p = st.chat, st.pipe
        if chat["session"] is not None:  # the previous conversation has ended: remember it first
            memory.after_session(chat["session"].cfg, chat["session"].transcript)
        for t in chat["passes"]:  # a returning guide must know who it is: wait for passes in flight
            t.join()
        chat["passes"] = []
        # The guide's chapters are made the fast way (docs/fast-start.md); the hooks are what that changes for it.
        # `live`: the page's GPT-Live conversation, whose chapters are read live in the same session.
        # The session holds this listener's own pipeline, not the proxy: its tools act for this listener.
        chat["session"] = guide.Session(cfg, functools.partial(p.start_seed, mode="fast", live=live), p.status,
                                        p.runs_dir,
                                        fast=fast_hooks.Hooks(cfg, p.runs_dir, p.status), own_only=live)
        return chat["session"]

    def talk(text: str | None, fresh: bool, session_id: str | None = None) -> dict:
        chat = here().chat
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
            remember(here(), session.transcript)
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
    def reset(request: Request) -> dict:
        """Start fresh as a new demo listener (docs/overnight-chapters.md, Reset): the conversation in
        progress ends without a memory pass (that would write it into the listener being left),
        passes in flight finish, then listeners/demo-<date>-<letter>/ is created and made active.
        Nothing is moved, stopped or deleted: the previous listener's folder (ian/ above all) stays
        byte for byte, and a chapter being made for it keeps writing into its own folder.
        A tester can't: it would make a new active listener for the whole server (the admin page resets
        a tester instead)."""
        if common.REQUEST_LISTENER.get() or getattr(request.state, "tester", None):
            raise HTTPException(403, "Ask Ian to reset this.")
        with chat["lock"]:
            chat["session"] = None
            for t in chat["passes"]:
                t.join()
            chat["passes"] = []
            with memory.LOCK:
                previous = common.listener_dir(cfg).name
                cfg.pop("listener_dir", None)  # from now on, follow listeners/active.json
                lid = profiles.reset()
            pipe.detach()  # the new listener's page is idle, and free to start a chapter
        return {"listener": lid, "previous": previous}

    @app.post("/api/chapters/{run}/retry")
    def retry(run: str) -> dict:
        with pipe.lock:
            if pipe.busy():
                raise HTTPException(409, "A chapter is already being made.")
            try:
                r = Run.open(run, runs_dir=pipe.runs_dir)
            except StageError:
                raise HTTPException(404, "No such chapter.") from None
            if profiles.overnight_build(r.root):  # the seed pipeline would rebuild it its own way, and pay twice
                raise HTTPException(400, "That is an overnight chapter: continue it with `wl overnight resume`.")
            if not r.path("seed").exists():
                raise HTTPException(400, "That run has no seed.")
            pipe.start(["new", run, "--unattended"], run)
        return {"run": run}

    def shown(chapter: str | None) -> dict:  # the current run, or the finished chapter the address names
        if chapter:
            return ask_routes.chapter_status(pipe.runs_dir, chapter)
        return ask_routes.with_extras(pipe.status(), pipe.runs_dir)

    @app.get("/api/status")
    def status(chapter: str | None = None) -> dict:
        return shown(chapter)

    @app.get("/api/events")
    async def events(request: Request, chapter: str | None = None) -> StreamingResponse:
        async def stream():
            last = None
            while not await request.is_disconnected():
                now = json.dumps(shown(chapter), ensure_ascii=False)
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

    ask_routes.mount(app, cfg, lambda: pipe.runs_dir)
    overnight_routes.mount(app, cfg, pipe, chat)
    live_routes.mount(app, cfg, pipe, _Conversation(lambda: here().chat, new_session, current))
    fast_routes.mount(app, cfg, pipe)
    app.mount("/episode", StaticFiles(directory=ROOT / "episode" / "page", html=True), name="episode")
    app.mount("/runs", _RunsFiles(pipe), name="runs")
    return app


class _RunsFiles:
    """/runs/<run>/... from the folder the pipeline follows now (the active listener's chapters,
    which a reset can change), with StaticFiles' range requests."""

    def __init__(self, pipe: Pipeline):
        self.pipe, self.apps = pipe, {}

    async def __call__(self, scope, receive, send):
        d = self.pipe.runs_dir
        app = self.apps.get(d) or self.apps.setdefault(d, StaticFiles(directory=d, check_dir=False))
        await app(scope, receive, send)


def live_voice_on() -> bool:
    """config.yaml as it is on disk says the page's voice is GPT-Live (what mode_args marks a run by)."""
    return fast_base.live_voice(load_config())


def live(cfg: dict) -> bool:
    """The page talks through one GPT-Live session (voice/gpt_live/), not the ElevenLabs agents."""
    return (cfg.get("live") or {}).get("provider") == "gpt_live"


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
