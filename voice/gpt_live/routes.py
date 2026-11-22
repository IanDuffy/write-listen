"""The live page's routes (/api/live/*, /live.js), mounted by server.py when live.provider is gpt_live.

    POST /api/live/start      {"sdp", "handoff"?}: a new conversation in one GPT-Live session; the page's WebRTC
                              offer goes to OpenAI with the guide's instructions and tools, the answer comes back.
                              `handoff` {...}: a tester handed over by the interviewer (Tester Demo): no
                              opening is sent or recorded here (the page opens this session a moment ahead of
                              the handover and sends the `handoff` note itself, with the name, at the handover);
                              the answer's `debrief_done`: their debrief is done, so no handback again
    GET  /api/live/demo       {tester, show, done, setting}: a tester's End demo button (page/live.js) on a page
                              they came back to: Ian's setting for them, and whether their debrief is done
    POST /api/live/resume    {"session", "sdp"}: the same conversation in a new GPT-Live session (the old
                              one dropped, rested too long, or neared its time limit), told the conversation so far
    POST /api/live/tool       {"session", "name", "args"}: a tool the backend called, run in the conversation
    POST /api/live/line       {"session", "who", "text", "live_id"?, "sig"?}: a line for the transcript. `sig`: how a
                              spoken listener line sounded (page/sig.js), sent only while the page measures
                              (signals.record.live_on); kept as a turn record in signals/<session>.jsonl
                              (signals/live.py), never in the transcript
    POST /api/live/usage      {"session", "live_id", "seconds", "backend"}: a session's cost, when it ends
    POST /api/live/direct     {"session", "id", "trigger", "speaking", "silence_ms", "last_who", "recent", "page",
                              "sig_turn"?}:
                              one look by the earpiece (voice/gpt_live/director.py; Ian, 2026-11-19): {"id", "read",
                              "plan", "next", "changed", "kind", "about", "note", "ms", "cost_usd"}; on any failure
                              no whisper (changed false, note and kind null) and an "error"
    POST /api/live/direct/fate {"session", "id", "fate", "reason"?, "detail"?, "secs"?, "held_ms"?}: what became of
                              a look's whisper on the page (sent, held, dropped and why; director.FATES,
                              FATE_REASONS), recorded as a "director_fate" row for the admin's earpiece history
                              (voice/gpt_live/earpiece_history.py); 409 for a conversation that has ended
    GET  /api/live/chapter/{run}            the chapter as paragraphs (voice/gpt_live/chapter.py)
    GET  /api/live/card/{run}               the chapter card (faststart/card.py), with the outline's title once
                                            there is one; 404 until it is written
    POST /api/live/context    {"run", "id", "sentence"}: what the guide is told when the listener taps
    POST /api/live/restart    {"run", "id", "sentence", "seconds", "rewind"}: where the reading picks up
    POST /api/live/read       {"run", "id", "heard", ...}: a paragraph as the guide said it (the reading check)
    POST /api/live/aside      {"session", "run", "kind", "id", ...}: a question or check-in, for the aside log
                              (runs/<run>/12_asks/asides.jsonl, as text in the episode)
    POST /api/live/archive    {"run", "references"}: cut the reading from the session recordings into the
                              chapter, in the background (voice/gpt_live/archive.py; progress in
                              runs/<run>/live/archive.json): {"started": true} or {"started": false, "reason"}
    GET  /live.js                            the page's script, with config live.page and the notes
    GET  /earpiece.js                        the earpiece panel (page/earpiece.js), only for a listener with the view
                                             on (director.view_on: their tester.json, or live.director.view off demo);
                                             404 otherwise. /api/live/start's answer carries `earpiece_view`
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from faststart import base as fast_base
from signals import live as signals_live
from signals import record as signals_record
from stages import common, guide
from stages.common import ROOT, Run, StageError

from . import chapter as live_chapter
from . import director as live_director
from . import session as live_session

LIVE_JS = ROOT / "page" / "live.js"
ALIGN_JS = ROOT / "page" / "align.js"
LIVE_PAGE = ROOT / "page" / "live.html"
EARPIECE_JS = ROOT / "page" / "earpiece.js"


class Start(BaseModel):
    sdp: str
    handoff: dict | None = None   # {"name"}: the interviewer welcomed them and handed over (Tester Demo)


class Resume(BaseModel):
    session: str
    sdp: str
    reason: str = "reopen"


class Tool(BaseModel):
    session: str
    name: str
    args: dict | None = None
    run: str | None = None        # the page's own chapter (L.own): start_reading checks it is the one written


class Line(BaseModel):
    session: str
    who: str
    text: str = ""
    live_id: str | None = None
    sig: dict | None = None       # Listener Signals: the page's measures of a spoken listener line (page/sig.js)


class Direct(BaseModel):
    session: str
    id: int = 0                   # the page's look number (it echoes back; one look in flight at a time)
    trigger: str = "cadence"      # cadence, turn_end, silence, started or offered (live.director; director.TRIGGERS)
    speaking: str = "none"        # who is speaking right now, from the page's audio: listener, guide or none
    silence_ms: float = 0         # since the last line ended
    last_who: str | None = None   # whose line ended last: guide or listener
    recent: list[dict] = []       # the page's last few lines ({who, text}): any the transcript lacks yet are added
    page: dict = {}               # {seconds, explained, started, turns}: what the page knows of the conversation
    sig_turn: int | None = None   # the listener's latest turn the page measured (Listener Signals), while it measures


class Fate(BaseModel):
    session: str
    id: int                                          # the look's id
    fate: str                                        # sent, held or dropped (director.FATES)
    reason: str | None = None                        # why it was dropped (director.FATE_REASONS)
    detail: str | None = Field(None, max_length=200)
    secs: float | None = None                        # the page's clock since the conversation started
    held_ms: int | None = None                       # how long it was held before this


class Usage(BaseModel):
    session: str
    live_id: str
    seconds: float = 0
    backend: dict | None = None


class At(BaseModel):
    run: str
    id: str
    sentence: int = 0


class Restart(At):
    seconds: float = 0
    rewind: str | None = None


class Aside(BaseModel):
    session: str
    run: str
    kind: str = "ask"             # "ask" (a tap mid-chapter) or "check_in" (the next part wasn't ready)
    id: str | None = None         # the paragraph it happened in (the one about to start, for a check-in)
    sentence: int = 0
    seconds: float = 0
    how: str = "guide"            # guide (resume_chapter), button, unasked (the page went on), ready, dropped
    restart: dict | None = None
    lines: list[dict] = []
    live_id: str | None = None


class ArchiveReq(BaseModel):
    run: str
    references: bool = False      # also the episode's reference cards (Claude calls, capped)


class Read(At):
    heard: str = ""
    cut: bool = False
    times: dict | None = None
    live_id: str | None = None


LINE_WHO = ("listener", "guide", "page", "event")
SIGNALS_LOCK = threading.Lock()


def page_settings(cfg: dict) -> dict:
    out = {**cfg["live"]["page"], "notes": live_session.notes(), "restart_bands_secs": cfg["live"]["restart_bands_secs"],
           "opening": str(cfg["live"].get("opening") or "").strip(),
           # the earpiece's page side (live.director): off, today's cues. Never the panel's flag ("view"): the page
           # learns that per listener from /api/live/start, so a friend's page never sees it
           "director": {k: v for k, v in live_director.settings(cfg).items() if k != "view"},
           # the local listener's settings (page/wake.js): voice.wake, with live.page.wake over them
           "wake": {**((cfg.get("voice") or {}).get("wake") or {}), **(cfg["live"]["page"].get("wake") or {})}}
    sig = common.sig_settings(cfg)
    if sig is not None:  # Listener Signals (page/sig.js): only while the page measures; absent, nothing of it runs
        out["signals"] = sig
    return out


def continuing(cfg: dict, pipe) -> dict | None:
    """The chapter a new conversation takes over (the page was reloaded or reopened, or the listener came
    back): the newest run, read live, if it is still being made (`begun` false until its reading has
    started); or, once written ("ready"), if its reading was under way less than `live.continue_minutes`
    ago, or it was never begun and was written less than `live.unread_ready_minutes` ago (`ready`: they
    went away while it was made). Never one heard to the end or archived (its archive starts only then),
    failed or stopped: an earlier conversation's chapter is never read by itself."""
    s = pipe.status() or {}
    if not (s.get("run") and s.get("mode") == "fast" and s.get("voice") == "live"):
        return None
    if s.get("state") not in ("making", "ready") or s.get("audio"):
        return None
    try:
        r = Run.open(s["run"], runs_dir=pipe.runs_dir)
    except StageError:
        return None
    player = fast_base.read_json(fast_base.path(r, "player")) or {}
    if player.get("state") in ("done", "idle") or (fast_base.read_json(r.root / "live" / "archive.json") or {}).get("state"):
        return None  # heard to the end (or its archive begun), or let go (Reset)
    begun = player.get("state") in ("playing", "paused", "waiting")
    unheard = False
    if s["state"] == "ready":
        L = cfg["live"]
        if begun:
            try:
                age = time.time() - float(player.get("at") or 0)
            except (TypeError, ValueError):
                return None
            if age > float(L.get("continue_minutes", 15)) * 60:
                return None
        else:
            try:
                written = datetime.fromisoformat(str(s.get("updated"))).timestamp()
            except ValueError:
                return None
            if time.time() - written > float(L.get("unread_ready_minutes", 120)) * 60:
                return None
            unheard = True
    section, piece = int(player.get("section") or 1), int(player.get("piece") or 0)
    return {"run": r.name, "title": s.get("title") or "", "begun": begun, "ready": unheard,
            "id": f"s{section}-p{piece + 1:02d}", "sentence": 0, "waiting": player.get("state") == "waiting"}


def debrief_done(cfg: dict) -> bool:
    """Tester Demo: this tester's debrief is done (study/debrief.json, demo/interviewer.py), so the page never
    hands back to the interviewer again: a later chapter just ends, and End demo isn't shown."""
    got = fast_base.read_json(common.listener_dir(cfg) / "study" / "debrief.json", {})
    return bool(isinstance(got, dict) and got.get("done"))


def end_demo(cfg: dict) -> dict:
    """Tester Demo: the End demo button (page/live.js; it replaced the library's debrief card, Ian 2026-11-18)
    for a tester on a page they came back to. Handed over by the interviewer the page shows it without asking;
    back later, it follows Ian's setting for them (tester.json `debrief_card`, the admin page's "End demo"
    control; the stored field kept its name): "on" always, "off" never, "auto" (the default) once they have
    a chapter (a folder under chapters/; the page adds: or a conversation begun on it). `show` is false, and
    `done` true, once the debrief is done (study/debrief.json). Not a tester (no tester.json): never."""
    listener = common.listener_dir(cfg)
    tester = fast_base.read_json(listener / "tester.json", None)
    if not isinstance(tester, dict):
        return {"tester": False, "show": False, "done": False, "setting": "off"}
    done = debrief_done(cfg)
    setting = tester.get("debrief_card") if tester.get("debrief_card") in ("auto", "on", "off") else "auto"
    chapters = listener / "chapters"
    started = chapters.is_dir() and any(p.is_dir() and not p.name.startswith((".", "_")) for p in chapters.iterdir())
    return {"tester": True, "show": bool(not done and (setting == "on" or (setting == "auto" and started))),
            "done": done, "setting": setting}


def let_go(pipe) -> None:
    """Reset memory: the newest live chapter is not this listener's to pick back up (continuing() skips a
    player.json in state "idle")."""
    s = pipe.status() or {}
    if not (s.get("run") and s.get("mode") == "fast" and s.get("voice") == "live"):
        return
    try:
        r = Run.open(s["run"], runs_dir=pipe.runs_dir)
    except StageError:
        return
    p = fast_base.read_json(fast_base.path(r, "player")) or {}
    fast_base.write_json(fast_base.path(r, "player"), {**p, "state": "idle", "reset_at": time.time()})


def mount(app, cfg: dict, pipe, chat) -> None:
    """`chat`: the server's conversation handle (lock, new_session(), current(id))."""

    def run_of(name: str) -> Run:
        try:
            r = Run.open(name, runs_dir=pipe.runs_dir)
        except StageError:
            raise HTTPException(404, "No such chapter.") from None
        if not fast_base.is_fast(r):
            raise HTTPException(400, "That chapter isn't read live.")
        return r

    def chapter_of(name: str) -> tuple[Run, dict]:
        r = run_of(name)
        try:
            return r, live_chapter.paragraphs(r)
        except StageError as e:
            raise HTTPException(409, str(e)) from None

    def open_session(session, sdp: str, values: dict, opening: str | None = None) -> dict:
        sc = live_session.session_config(cfg, values, fast=session.fast is not None, opening=opening, greet=False)
        try:
            got = live_session.create_webrtc(cfg, sc, sdp)
        except Exception as e:
            raise HTTPException(503, f"Voice isn't available: {e}") from None
        ids = session.__dict__.setdefault("live_ids", [])
        ids.append(got["id"])
        # What was sent, so "were the instructions applied?" can be answered from the record (OpenAI
        # can't return a session's configuration afterwards).
        sent = hashlib.sha256(sc["instructions"].encode("utf-8")).hexdigest()[:16]
        tools = [t.get("name") for t in sc["delegation"]["responses"]["tools"]]
        first = [c.get("text") for m in sc.get("input") or [] if m.get("role") == "developer" for c in m.get("content") or []]
        session.record("session", live_id=got["id"], voice=cfg["live"]["voice"], model=cfg["live"]["model"],
                       backend=cfg["live"]["backend_model"], instructions_sha256=sent,
                       instructions_chars=len(sc["instructions"]), tools=tools, startup=first[-1:] or None)
        return got

    @app.get("/live.js")
    def live_js() -> Response:
        text = LIVE_JS.read_text(encoding="utf-8").replace("__LIVE_SETTINGS__", json.dumps(page_settings(cfg)))
        text = common.with_sig(text, cfg)  # page/sig.js, only while the page measures (Listener Signals)
        text = common.with_speaker(text, cfg)  # page/speaker.js: the voice on a phone's loudspeaker
        return Response(text, media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    @app.get("/earpiece.js")
    def earpiece_js() -> Response:
        """The earpiece panel, for a listener with the view on; nobody else's page can load it."""
        if not live_director.view_on(cfg) or not EARPIECE_JS.is_file():
            return Response(status_code=404)
        return Response(EARPIECE_JS.read_text(encoding="utf-8"), media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})

    @app.get("/align.js")
    def align_js() -> Response:
        return Response(ALIGN_JS.read_text(encoding="utf-8"), media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})

    @app.get("/api/live/demo")
    def demo() -> dict:
        return end_demo(cfg)

    @app.post("/api/live/start")
    def start(req: Start) -> dict:
        """A new conversation. The page greets (speakable context once the session starts), unless a
        chapter's reading was under way a moment ago: then the session opens on that chapter and the page
        reads on from where it was (`continue`). No other chapter is this conversation's until its
        start_chapter returns one."""
        with chat.lock:
            cont = continuing(cfg, pipe)
            session = chat.new_session(live=True)
            opening = None
            # Handed over by the interviewer: the page says who they are once the interviewer has finished (the
            # session may be opened a moment ahead, and left unused if they go), so no opening is sent or
            # recorded here. With no line of the listener's it is no meeting (memory.conversations).
            handoff = not cont and isinstance(req.handoff, dict)
            if cont:
                session.run = cont["run"]
                note = live_session.notes()["reloaded" if cont["begun"] else "ready_unheard" if cont["ready"] else "reopened"]
                opening = note.replace("{title}", cont["title"] or "their chapter")
            got = open_session(session, req.sdp, session.values, opening)
            if cont:
                session.record("page", text=f"[Picked up the chapter {cont['run']} at {cont['id']}.]", mode="live")
            elif not handoff:  # what the page sends as the session starts (live.js connect)
                words = str(cfg["live"].get("opening") or "").strip()
                notes = live_session.notes()
                session.record("page", text=notes["opening_words"].replace("{words}", words) if words else notes["opened"],
                               mode="live")
        return {"session": session.id, "live_id": got["id"], "sdp": got["sdp"], "continue": cont,
                "handoff": bool(handoff), "debrief_done": debrief_done(cfg),
                "earpiece_view": live_director.view_on(cfg)}

    @app.post("/api/live/resume")
    def resume(req: Resume) -> dict:
        with chat.lock:
            session = chat.current(req.session)
            notes = live_session.notes()  # no greeting: the conversation is already going
            # A new session at a section boundary, or after a pause long enough to close the old one: it says
            # nothing and reads on; after a drop, it says it's back.
            # The guide steps back with GPT-Live closed (Ian's fifth note), so a rest's end is a new session too:
            # a tap ("I'm here"), a voice that may not be the listener (listen only), the chapter ready (say
            # nothing until given words), play (the reading follows).
            opening = {"paused-ask": notes["asking"], "section boundary": notes["continue"], "paused": notes["continue"],
                       "play": notes["continue"], "tap": notes["back_from_rest"], "wake": notes["listening_again"],
                       "ready": notes["ready_back"]}.get(req.reason, notes["returned"])
            values = session.resume_values()
            values["explained"] = live_session.explained(session.transcript)  # told what this is: not offered again
            said = session.name_said() or live_session.name_said(session.transcript)
            if said and str(values.get("identity", "")).startswith("(none"):  # it survives the cut of "earlier"
                values["identity"] = (f"(none yet: this is your first meeting, and you have already told them your "
                                      f"name is {said}: keep it)")
            got = open_session(session, req.sdp, values, opening)
            session.record("page", text=f"[A new voice session: {req.reason}.]", mode="live")
        return {"session": session.id, "live_id": got["id"], "sdp": got["sdp"]}

    @app.post("/api/live/tool")
    def tool(req: Tool) -> dict:
        with chat.lock:
            session = chat.current(req.session)
            args = dict(req.args or {})
            try:
                out = extra_tool(session, req.name, args, req.run)
                if out is None:
                    out = session.tool(req.name, args)
            except Exception as e:
                out = {"error": f"{type(e).__name__}: {e}"}
            if isinstance(out, dict) and "while_waiting" in out:
                # The typed and ElevenLabs guides' wait rules (prompts/guide_wait_fast.md: "keep them
                # talking", everything they say into add_note): the live guide's own parts cover the
                # wait, as company rather than an interview (Ian's second test, 2026-11-15).
                out = {k: v for k, v in out.items() if k != "while_waiting"}
            session.record("tool", name=req.name, input=args, result=out)
            if req.name == "start_chapter" and isinstance(out, dict) and out.get("started") and session.run:
                out = {**out, "run": session.run}  # for the page (the chapter it may read); not passed to the backend
        return out

    def extra_tool(session, name: str, args: dict, page_run: str | None = None) -> dict | None:
        """The aside's tools, now in the same session: the page acts on resume_chapter itself."""
        if name == "resume_chapter":
            return {"resuming": True, **({"rewind": args["rewind"]} if args.get("rewind") else {})}
        if name == "start_reading":  # the page begins the reading once the guide's lead-in is said
            st = pipe.status() or {}
            run = session.run or page_run  # the page's own chapter (a reloaded page picks one up without start_chapter)
            ready = bool(run and st.get("run") == run
                         and any(x.get("n") == 1 and x.get("text") for x in st.get("sections") or []))
            return {"starting": True} if ready else {"starting": False, "reason": "the opening isn't ready yet"}
        if name == "save_seed":
            now = pipe.status() or {}  # this conversation's chapter, else one being made now: never an old one
            run = session.run or (now.get("run") if now.get("state") == "making" else None)
            if not run:
                return {"saved": False, "reason": "no chapter to keep it with"}
            from ask import aside as ask_aside
            r = Run.open(run, runs_dir=pipe.runs_dir)
            return {"saved": True, **ask_aside.save_seed(r.root, args.get("topic", ""), args.get("why", ""), 0.0, "live")}
        return None

    @app.post("/api/live/line")
    def line(req: Line) -> dict:
        with chat.lock:
            session = chat.current(req.session)
            if req.text.strip() and req.who in LINE_WHO:
                session.record(req.who, text=req.text.strip(), mode="live")
        if req.who == "listener" and req.sig and req.text.strip() and signals_record.live_on(cfg):
            # How the line sounded, as a turn record beside the transcript (never in it); a failure here never
            # costs the line. The session pins its listener's folder (the thread may serve anyone next).
            live_id = req.live_id or (session.__dict__.get("live_ids") or [None])[-1]
            try:
                with SIGNALS_LOCK:  # a turn's lines join their text: one at a time
                    signals_live.record_line(common.listener_dir(session.cfg), session.id, part="guide",
                                             live_id=live_id, text=req.text, sig=req.sig, cfg=cfg)
            except Exception as e:  # noqa: BLE001
                print(f"  signals: the line's record failed: {type(e).__name__}: {e}")
        return {"ok": True}

    @app.post("/api/live/direct")
    def direct(req: Direct) -> dict:
        """One look by the earpiece (voice/gpt_live/director.py). The conversation lock is held only to read and
        write the transcript, never for the model call (about 2 s), so the page's line posts go on meanwhile."""
        with chat.lock:
            session = chat.current(req.session)
        return live_director.ask(cfg, session, req.model_dump(), lock=chat.lock)

    @app.post("/api/live/direct/fate")
    def direct_fate(req: Fate) -> dict:
        """What became of a look's whisper on the page, for the admin's earpiece history. Never a "text" or
        "note" field: nothing that reads the conversation (earlier_text, memory, explained, whisper_in) reads it."""
        if req.fate not in live_director.FATES:
            raise HTTPException(400, "Unknown fate.")
        if req.reason is not None and req.reason not in live_director.FATE_REASONS:
            raise HTTPException(400, "Unknown reason.")
        if req.fate == "dropped" and not req.reason:
            raise HTTPException(400, "A dropped note needs a reason.")
        with chat.lock:
            session = chat.current(req.session)
            session.record("director_fate", id=req.id, fate=req.fate, reason=req.reason, detail=req.detail,
                           page_secs=req.secs, held_ms=req.held_ms)
        return {"ok": True}

    @app.post("/api/live/usage")
    def usage(req: Usage) -> dict:
        """A session's cost when it ends, in the conversations' cost log (listener/sessions/log.jsonl)."""
        usd = live_session.cost(cfg, req.seconds, req.backend)
        with chat.lock:  # also for a conversation already ended (Reset, or the page closing first): it cost all the same
            log = Run(name="sessions", root=guide.listener_dir(cfg) / "sessions")
            if any(x.get("live_id") == req.live_id and x.get("provider") == "openai" for x in log.read_log()):
                return {"cost_usd": usd, "logged": False}  # reported twice (a beacon and the end)
            log.log({"stage": "voice", "provider": "openai", "session": req.session, "live_id": req.live_id,
                     "seconds": round(req.seconds, 1), "backend": req.backend, "cost_usd": usd})
        return {"cost_usd": usd}

    @app.get("/api/live/chapter/{run}")
    def chapter(run: str) -> dict:
        r, ch = chapter_of(run)
        ch["outline"] = fast_base.read_json(fast_base.path(r, "outline_json")) or {}
        return ch

    @app.get("/api/live/card/{run}")
    def card(run: str) -> dict:
        """The chapter card, written beside the run from its seed at start_chapter (it may come before
        fast.json does)."""
        from faststart import card as fast_card
        try:
            r = Run.open(run, runs_dir=pipe.runs_dir)
        except StageError:
            raise HTTPException(404, "No such chapter.") from None
        got = fast_card.read(r.root)
        if not got:
            raise HTTPException(404, "No card yet.")
        return got

    @app.post("/api/live/context")
    def context(req: At) -> dict:
        r, ch = chapter_of(req.run)
        outline = fast_base.read_json(fast_base.path(r, "outline_json"))
        try:
            text = live_chapter.context(ch, outline, req.id, req.sentence)
        except StageError as e:
            raise HTTPException(404, str(e)) from None
        return {"chunks": live_chapter.append_chunks(text)}

    @app.post("/api/live/restart")
    def restart(req: Restart) -> dict:
        r, ch = chapter_of(req.run)
        try:
            at = live_chapter.restart(ch, req.id, req.sentence, req.seconds, cfg["live"], req.rewind)
        except StageError as e:
            raise HTTPException(404, str(e)) from None
        return at

    @app.post("/api/live/aside")
    def aside(req: Aside) -> dict:
        """A conversation in the middle of the chapter, logged as ask-by-voice logs its asides (the
        episode shows them as text). Its place is a paragraph and sentence; the archive turns that into
        a time once the recording is timed."""
        r = run_of(req.run)
        from ask import aside as ask_aside
        rec = {"id": f"{time.strftime('%Y%m%d-%H%M%S')}-live", "kind": req.kind, "voice": "live",
               "started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - req.seconds)),
               "paragraph": req.id, "sentence": req.sentence, "ended": req.how, "seconds": round(req.seconds, 1),
               "restart": req.restart, "transcript": [{"who": x.get("who"), "text": x.get("text")} for x in req.lines],
               "live_id": req.live_id, "session": req.session}
        ask_aside.append_aside(r.root, rec)
        return {"logged": rec["id"]}

    @app.post("/api/live/archive")
    def archive_route(req: ArchiveReq) -> dict:
        """The live chapter's archive, in a background thread (one at a time per run)."""
        from . import archive as live_archive
        return live_archive.start(run_of(req.run), cfg, references=req.references)

    @app.post("/api/live/read")
    def read(req: Read) -> dict:
        r, ch = chapter_of(req.run)
        try:
            rec = live_chapter.check(r, ch, req.id, req.heard, req.sentence, req.cut, req.times, live_id=req.live_id)
        except StageError as e:
            raise HTTPException(404, str(e)) from None
        return {k: rec[k] for k in ("id", "same", "deviates", "missing", "added", "changed")}
