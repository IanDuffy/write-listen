"""The server's routes for asking mid-chapter (docs/ask-voice.md), mounted by server.py.

    GET  /api/ask/chapter/{run}  a chapter's title and whether it can be asked about
    POST /api/ask/start          {"run", "t"}: a new aside. A signed URL for the aside agent, the context as
                                 dynamic variables, the page's notes and timings
    POST /api/ask/tool           {"aside", "name", "args"}: a client tool the agent called, run here
    POST /api/ask/line           {"aside", "who", "text"} (or the call's id): the aside's transcript
    POST /api/ask/end            {"aside", "how", timings}: where the chapter restarts; logs the aside

The page runs the voice session with the onboarding code's client (ElevenLabs' browser SDK) and
keeps the timers; the server keeps the aside's state, so a page that dies mid-aside loses nothing
that was said. One aside at a time: a new one ends any still open (logged as dropped).

A fast run (docs/fast-start.md) adds a second kind of conversation on the same routes: the check-in,
when the listener reaches a section that is not ready. /api/ask/start takes "kind": "check_in" (with
"section", the one awaited, and "reopened"); the guide is the check-in agent, its context and tools
come from faststart/checkin.py, and nothing restarts when it ends ("restart": null): the page plays
the awaited section. Every aside's log line says which kind it was.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from faststart import checkin
from stages import voice
from stages.common import ROOT, Run, StageError, load_prompt

from . import aside as A

# What resume_chapter returns to the agent. The platform always hands a tool's result back to the model,
# which then speaks: so the result asks for the lead-in, and the page ends the session once it is said.
RESUMED = {"resuming": True, "now": "Say your short lead-in, a few words in your own voice, and stop. "
                                   "The chapter starts the moment you finish. Don't mention this tool."}
ENDINGS = ("listener", "closing", "silence", "never_mind", "other", "button", "dropped", "timeout", "error")


class AskStart(BaseModel):
    run: str
    t: float
    kind: str = "ask"              # "check_in": the guide comes in while a fast run's next section is made
    section: int | None = None     # a check-in: the section awaited (left out: the one status.json names)
    reopened: bool = False         # a check-in the listener asked for ("Talk while you wait")


class AskTool(BaseModel):
    aside: str
    name: str
    args: dict = {}


class AskLine(BaseModel):
    aside: str
    who: str = ""
    text: str = ""
    conversation_id: str | None = None


class AskEnd(BaseModel):
    aside: str
    how: str                       # "guide" (after resume_chapter), "button", "dropped", "timeout", "error"
    tap_to_ready_ms: float | None = None
    tap_to_first_sound_ms: float | None = None


def notes() -> dict[str, str]:
    """prompts/guide_aside_notes.md as {key: note}."""
    return dict(ln.split(": ", 1) for ln in load_prompt("guide_aside_notes").splitlines() if ": " in ln)


def page_settings(cfg: dict) -> dict:
    keys = ("fade_ms", "resume_delay_ms", "slow_line_secs", "give_up_secs", "silence_after_greeting_secs",
            "silence_after_answer_secs", "silence_after_question_secs", "long_aside_minutes")
    return {k: cfg["ask"][k] for k in keys}


def chapter_status(runs_dir: Path, run: str) -> dict:
    """A finished chapter as the page shows it when the address names it (?chapter=<run>)."""
    try:
        r = Run.open(run, runs_dir=runs_dir)
    except StageError:
        return {"state": "problem", "message": "There's no such chapter.", "retryable": False}
    if not r.path("chapter_mp3").exists():
        return {"state": "problem", "message": "That chapter has no audio yet.", "retryable": False}
    return with_extras({"run": run, "state": "ready", "title": A.chapter_title(r.root)}, runs_dir)


def with_extras(status: dict, runs_dir: Path) -> dict:
    """A ready chapter's status, plus whether it can be asked about and read along."""
    run = status.get("run")
    if status.get("state") == "ready" and run:
        root = runs_dir / run
        status = {**status, "asks": A.can_ask(root), "episode": (root / "11_episode" / "episode.json").exists()}
    elif status.get("state") == "making" and status.get("mode") == "fast" and run:
        # A fast run is listened to while it is made: askable once its first section is timed.
        status = {**status, "asks": A.can_ask(runs_dir / run)}
    return status


def mount(app: FastAPI, cfg: dict, runs_dir: Path) -> None:
    state = {"open": {}, "chapters": {}}
    lock = threading.Lock()

    def folder(run: str) -> Path:
        try:
            return Run.open(run, runs_dir=runs_dir).root
        except StageError:
            raise HTTPException(404, "No such chapter.") from None

    def chapter(run: str) -> A.Chapter:
        root = folder(run)
        if not A.can_ask(root):
            raise HTTPException(400, "This chapter has no timed transcript, so it can't be asked about yet.")
        key = (run, (root / A.TIMING).stat().st_mtime)
        if key not in state["chapters"]:
            state["chapters"] = {key: A.Chapter(root, cfg)}  # one chapter at a time is plenty
        return state["chapters"][key]

    def current(aside_id: str) -> dict:
        a = state["open"].get(aside_id)
        if a is None:
            raise HTTPException(409, "That aside has ended.")
        return a

    def finish(a: dict, how: str, timings: dict) -> dict:
        """End an aside: the restart point from how long it lasted, then the log line."""
        seconds = time.monotonic() - a["t0"]
        if a["kind"] == "check_in":
            return finish_check_in(a, how, seconds, timings)
        ch = a["chapter"]
        rewind = (a.get("resume") or {}).get("rewind")
        if how == "guide":
            how = (a.get("resume") or {}).get("reason") or "other"
        if how not in ENDINGS:
            how = "other"
        restart, segment = ch.restart_point(a["t"], seconds, rewind), ch.sents[ch.locate(a["t"])].seg + 1
        if a["past"]:  # in a section still being narrated (a fast run): no sentence times yet, so where they paused
            restart, segment = {"t": round(a["t"], 3), "sentence_id": a["sentence_id"], "rule": "position"}, a["past"]
        record = {"id": a["id"], "kind": "ask", "started": a["started"], "t": round(a["t"], 3),
                  "sentence_id": a["sentence_id"], "segment": segment, "transcript": a["lines"], "ended": how,
                  "seconds": round(seconds, 1), "restart": restart, "rewind_asked": rewind, "seeds": a["seeds"],
                  "tap_to_ready_ms": timings.get("tap_to_ready_ms"),
                  "tap_to_first_sound_ms": timings.get("tap_to_first_sound_ms"),
                  "conversation_id": a.get("conversation_id"), "cost_usd": None, "credits": None}
        A.append_aside(ch.run, record)
        if a.get("conversation_id"):
            threading.Thread(target=_save_cost, args=(cfg, ch.run, a["id"], a["conversation_id"]), daemon=True).start()
        after(lambda: checkin.question_notes(ch.run, a["lines"], segment))  # a fast run still being made: a note
        return record

    def finish_check_in(a: dict, how: str, seconds: float, timings: dict) -> dict:
        """End a check-in: the same log, no restart point (the page plays the awaited section), and
        what the listener said goes to the profile's memory pass."""
        record = {"id": a["id"], "kind": "check_in", "started": a["started"], "t": round(a["t"], 3),
                  "sentence_id": a["sentence_id"], "segment": a["section"], "section": a["section"],
                  "reopened": a["reopened"], "transcript": a["lines"], "ended": checkin.ending(a, how),
                  "seconds": round(seconds, 1), "restart": None, "rewind_asked": None, "seeds": [],
                  "tap_to_ready_ms": timings.get("tap_to_ready_ms"),
                  "tap_to_first_sound_ms": timings.get("tap_to_first_sound_ms"),
                  "conversation_id": a.get("conversation_id"), "cost_usd": None, "credits": None}
        A.append_aside(a["run"], record)
        if a.get("conversation_id"):
            threading.Thread(target=_save_cost, args=(cfg, a["run"], a["id"], a["conversation_id"]), daemon=True).start()
        after(lambda: checkin.remember(cfg, a, checkin.chapter_title(a["run"])))
        return record

    def after(work) -> None:
        """Something done with an aside once it is logged: its failure never fails the request."""
        try:
            work()
        except Exception as e:
            print(f"  after an aside: {type(e).__name__}: {e}")

    def drop_open() -> None:
        with lock:
            for old in list(state["open"].values()):  # one aside at a time
                state["open"].pop(old["id"], None)
                finish(old, "dropped", {})

    def new_aside(kind: str, t: float, sentence_id: str, **more) -> dict:
        return {"id": time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4], "kind": kind, "t": t,
                "sentence_id": sentence_id, "t0": time.monotonic(), "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "lines": [], "seeds": [], "resume": None, **more}

    def check_in_start(req: AskStart) -> dict:
        root = folder(req.run)
        section = req.section if req.section is not None else checkin.awaited(root)
        if not (root / "fast.json").exists() or section not in checkin.sections_known(root):
            raise HTTPException(400, "There is no part of this chapter to wait for.")
        drop_open()
        try:
            url = voice.signed_url(cfg, kind="checkin")  # creates or updates the check-in agent if its prompt changed
        except Exception as e:
            raise HTTPException(503, f"Voice isn't available: {e}") from None
        at = checkin.listener_at(root, section)
        a = new_aside("check_in", max(req.t, 0.0), checkin.anchor(root, section, at["pieces"]), run=root,
                      section=section, was_in=at["section"], have=at["pieces"], reopened=req.reopened)
        with lock:
            state["open"][a["id"]] = a
        return {"aside": a["id"], "kind": "check_in", "section": section, "t": a["t"], "sentence_id": a["sentence_id"],
                "signed_url": url, "dynamic_variables": checkin.context(root, cfg, section, a["t"]),
                "notes": checkin.page_notes(), "settings": checkin.page_settings(cfg)}

    @app.get("/ask.js")
    def ask_js() -> Response:  # page/ask.js, with config.yaml ask's page settings
        text = (ROOT / "page" / "ask.js").read_text(encoding="utf-8")
        return Response(text.replace("__ASK_SETTINGS__", json.dumps(page_settings(cfg))), media_type="text/javascript")

    @app.get("/api/ask/chapter/{run}")
    def ask_chapter(run: str) -> dict:
        return chapter_status(runs_dir, run)

    @app.post("/api/ask/start")
    def ask_start(req: AskStart) -> dict:
        if req.kind == "check_in":
            return check_in_start(req)
        ch = chapter(req.run)
        drop_open()
        try:
            url = voice.signed_url(cfg, kind="aside")  # creates or updates the aside agent if its prompt changed
        except Exception as e:
            raise HTTPException(503, f"Voice isn't available: {e}") from None
        t = min(max(req.t, 0.0), ch.duration)
        i = ch.locate(t)
        values = ch.context(t, A.load_asides(ch.run))
        # A fast run still being made: the listener may be past the timed transcript, in a section whose
        # narration is still running. The guide is then told that section from its pieces.
        past = checkin.beyond(ch, req.t)
        if past:
            t, values = req.t, {**values, **past["values"]}
        a = new_aside("ask", t, past["sentence_id"] if past else ch.sents[i].id, chapter=ch,
                      past=past["section"] if past else None)
        with lock:
            state["open"][a["id"]] = a
        n = notes()
        return {"aside": a["id"], "t": t, "sentence_id": a["sentence_id"], "signed_url": url,
                "dynamic_variables": values, "notes": n, "settings": page_settings(cfg)}

    @app.post("/api/ask/tool")
    def ask_tool(req: AskTool) -> dict:
        with lock:
            a = current(req.aside)
            args = dict(req.args or {})
            if a["kind"] == "check_in":  # add_note, get_status, and resume_chapter once the awaited part has audio
                try:
                    out = checkin.tool(a, req.name, args)
                except Exception as e:  # a plain result the guide can act on, as the onboarding tools give
                    out = {"error": f"{type(e).__name__}: {e}"}
            elif req.name == "resume_chapter":
                a["resume"] = {"reason": args.get("reason") or "other", "rewind": args.get("rewind")}
                out = RESUMED
            elif req.name == "save_seed":
                saved = A.save_seed(a["chapter"].run, args.get("topic", ""), args.get("why", ""), a["t"], a["sentence_id"])
                a["seeds"].append(saved)
                out = {"saved": True, "note": "Saved for a future chapter."}
            else:
                out = {"error": f"No tool named {req.name}."}
            a["lines"].append({"s": round(time.monotonic() - a["t0"], 2), "who": "tool", "name": req.name,
                               "input": args, "result": out})
        return out

    @app.post("/api/ask/line")
    def ask_line(req: AskLine) -> dict:
        with lock:
            a = current(req.aside)
            if req.conversation_id:
                a["conversation_id"] = req.conversation_id
            if req.text.strip() and req.who in ("listener", "guide", "page"):
                a["lines"].append({"s": round(time.monotonic() - a["t0"], 2), "who": req.who, "text": req.text.strip()})
        return {"ok": True}

    @app.post("/api/ask/end")
    def ask_end(req: AskEnd) -> dict:
        with lock:
            a = state["open"].pop(req.aside, None)
            if a is None:
                raise HTTPException(409, "That aside has ended.")
            record = finish(a, req.how, {"tap_to_ready_ms": req.tap_to_ready_ms,
                                         "tap_to_first_sound_ms": req.tap_to_first_sound_ms})
        return {"restart": record["restart"], "ended": record["ended"], "seconds": record["seconds"]}


def _save_cost(cfg: dict, run: Path, aside_id: str, conversation_id: str, wait_s: int = 180) -> None:
    """ElevenLabs' record of the call, once processed: saved next to the log, its cost into the aside's line."""
    try:
        from stages.tts import el_call
        client = voice._client()
        deadline, rec = time.monotonic() + wait_s, None
        while time.monotonic() < deadline:
            try:
                rec = el_call(client.conversational_ai.conversations.get, conversation_id)
            except StageError:
                rec = None
            if getattr(rec, "status", None) in ("done", "failed"):
                break
            time.sleep(5)
        if rec is None:
            return
        data = rec.model_dump(mode="json") if hasattr(rec, "model_dump") else dict(rec)
        (A.asks_dir(run) / f"{aside_id}.voice.json").write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        meta = data.get("metadata") or {}
        if data.get("status") in ("done", "failed"):
            A.update_aside(run, aside_id, cost_usd=meta.get("cost_fiat"), credits=meta.get("cost"),
                           billed_seconds=meta.get("call_duration_secs"))
    except Exception as e:  # never take the server down over a call record
        print(f"  saving the aside's call record failed: {e}")
