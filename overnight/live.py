"""The guide's live sessions on the library page (docs/overnight-chapters.md: "Morning playback" and
"Change by voice"), on OpenAI's GPT-Live: one voice (Gleam), the guide's tools run through GPT-Live's
delegation to a Responses backend.

Three modes, each a session opened from the page's WebRTC offer (GPT-Live has no client keys, so the
offer goes through this server):

    intro   Play on the latest chapter: the guide opens live, talks for under a minute from the
            chapter's intro_brief.md (never the script), then calls start_chapter and the page plays
            the recording (or, with playback.read_live, the guide reads it).
    change  Change on the Up next card: a conversation about what the listener would rather hear,
            ending in set_next_chapter; before the cutoff the planner rewrites the card, after it the
            request goes to queue.jsonl for the next build day.
    ask     a tap during the recorded chapter: the recording pauses, the guide answers from the
            chapter's context (ask/aside.py), resume_chapter, and the recording resumes with the usual
            rewind (ask/aside.Chapter.restart_point).

Protocol facts (checked live on the live-voice branch, voice/gpt_live/REPORT.md there, and in this
build's probe): POST {url} {"session": ..., "transport": {"type": "webrtc", "sdp": offer}} returns the
answer SDP; no opening parameter exists, so the page appends the first note as speakable context the
moment the session starts; notes go as instructions with the mic open; the voice hands tool work to the
backend when it says "Generate <|SpawnThinking|>" (OpenAI's delegation policy wording).

Every conversation is saved to the listener's sessions/<id>.jsonl in the format the memory pass reads,
its audio (the page's recording of both sides, when the browser can make one) to sessions/<id>.webm, and
its cost (GPT-Live's connected seconds, the backend's tokens) to sessions/log.jsonl. A session the page
never ended (a closed tab whose beacon was lost) is ended when the next one starts, once it has been
quiet for live.stale_minutes.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import Request  # module level: the audio route's annotation is resolved from here
from pydantic import BaseModel

from stages import common, guide
from stages.common import PROMPTS_DIR, ROOT, Run, StageError, listener_profile, load_prompt

from . import profiles

MODES = ("intro", "change", "ask")
TOOLS_FILE = PROMPTS_DIR / "overnight_voice_tools.json"
MODE_TOOLS = {"intro": ("start_chapter",), "change": ("set_next_chapter",), "ask": ("resume_chapter", "save_seed")}
APPEND_LIMIT_CHARS = 1600  # GPT-Live takes at most 500 tokens per append


def lcfg(cfg: dict) -> dict:
    return cfg["overnight"]["live"]


def notes() -> dict[str, str]:
    """prompts/overnight_voice_notes.md: what the page tells the guide, one `key: text` per line."""
    out = {}
    for line in load_prompt("overnight_voice_notes").splitlines():
        k, sep, v = line.partition(":")
        if sep and re.fullmatch(r"[a-z_]+", k.strip()):
            out[k.strip()] = v.strip()
    return out


def tools(mode: str) -> list[dict]:
    """The mode's tools as Responses function tools for the delegation backend."""
    allowed = MODE_TOOLS[mode]
    raw = json.loads(TOOLS_FILE.read_text(encoding="utf-8"))
    return [{"type": "function", "name": t["name"], "description": t["description"],
             "parameters": t["input_schema"], "strict": False} for t in raw if t["name"] in allowed]


def session_config(cfg: dict, mode: str, values: dict) -> dict:
    L = lcfg(cfg)
    text = load_prompt(f"overnight_voice_{mode}", guide=load_prompt("overnight_voice_guide",
                                                                    identity=values["identity"]), **values)
    if len(text) / 3.5 > 16000:
        raise StageError(f"The {mode} session's instructions are too long for GPT-Live ({len(text)} characters).")
    return {
        "model": L["model"],
        "instructions": text,
        "audio": {"output": {"voice": L["voice"]}},
        "input": [],
        "delegation": {"type": "responses", "responses": {
            "model": L["backend_model"], "instructions": load_prompt("overnight_voice_backend"),
            "tools": tools(mode), "tool_choice": "auto", "reasoning": {"effort": L.get("backend_effort", "low")}}},
        "store": False,
    }


def api_key() -> str:
    import os

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise StageError("No OpenAI key: set OPENAI_API_KEY in .env.")
    return key


def create_webrtc(cfg: dict, session: dict, offer_sdp: str, client=None) -> dict:
    """Open the session from the page's offer; returns {"id", "sdp"} (the answer)."""
    import httpx
    c = client or httpx
    res = c.post(lcfg(cfg)["url"], headers={"Authorization": f"Bearer {api_key()}"},
                 json={"session": session, "transport": {"type": "webrtc", "sdp": offer_sdp}}, timeout=20)
    if res.status_code >= 400:
        raise StageError(f"GPT-Live refused the session ({res.status_code}): {res.text[:300]}")
    body = res.json()
    return {"id": body["session"]["id"], "sdp": body["transport"]["sdp"]}


def cost(cfg: dict, seconds: float, backend: dict | None) -> float:
    L = lcfg(cfg)
    usd = (seconds or 0) / 60 * L["price_per_minute"]
    if backend and backend.get("model") in (L.get("backend_prices") or {}):
        i, o, c = L["backend_prices"][backend["model"]]
        cached = backend.get("cached", 0) or 0
        usd += ((backend.get("input", 0) - cached) * i + cached * c + backend.get("output", 0) * o) / 1e6
    return round(usd, 5)


# --- what each mode's guide is told ------------------------------------------------------

def _identity(cfg: dict) -> str:
    return guide.identity(cfg) or "(You haven't met this listener before, or don't know your name with them. Don't make a point of names.)"


def _now_text(now: datetime) -> str:
    part = "morning" if now.hour < 12 else "afternoon" if now.hour < 18 else "evening"
    return f"{now:%A} {part}"


def intro_values(cfg: dict, root: Path, now: datetime) -> dict:
    brief = root / "intro_brief.md"
    if not brief.exists():
        raise StageError("This chapter has no intro brief.")
    s = profiles.settings(cfg, listener=common.listener_dir(cfg))
    return {"identity": _identity(cfg), "profile": listener_profile(cfg), "now": _now_text(now),
            "brief": brief.read_text(encoding="utf-8").strip(),
            "after": ("You read the chapter aloud yourself after the intro." if s["playback"]["read_live"]
                      else "The recording of the chapter, in your own voice, plays after the intro.")}


def _chapter_line(root: Path) -> str:
    card = profiles.read_json(root / "00_source" / "up_next.json", {}) or {}
    if card.get("title"):
        return f"\"{card['title']}\": {card.get('summary') or card.get('overview') or ''}".strip()
    ep = profiles.read_json(root / "11_episode" / "episode.json", {}) or {}
    return f"\"{ep.get('title') or root.name}\": {ep.get('angle') or ''}".strip()


def change_values(cfg: dict, now: datetime) -> dict:
    from . import library, schedule
    listener = common.listener_dir(cfg)
    s = profiles.settings(cfg, listener=listener)
    from . import runner
    card = runner.reconcile(listener)  # a build that died counts as failed, not still building
    can, why = schedule.can_change(card, s, now)
    latest = profiles.latest_chapter(listener=listener)
    seeds = [x for x in library.seeds(listener)][-6:]
    works = profiles.read_jsonl(listener / "library.jsonl")[-8:]
    plan = ("(nothing planned yet)" if not card or card.get("status") == "ready" else
            f"Title: {card['title']}\nOverview: {card.get('overview', '')}\nSummary: {card.get('summary', '')}\n"
            f"Mode: {card.get('mode', '')}")
    return {"identity": _identity(cfg), "profile": listener_profile(cfg), "now": _now_text(now),
            "plan": plan, "title": (card or {}).get("title") or "nothing yet",
            "last": _chapter_line(latest) if latest else "(no chapter yet)",
            "seeds": "\n".join(f"- {x.get('text')}" + (f" (they said: \"{x['words']}\")" if x.get("words") else "")
                               for x in seeds) or "(none)",
            "library": "\n".join(f"- {x.get('title')}" + (f", {x['creator']}" if x.get("creator") else "")
                                 for x in works) or "(none)",
            "when": (f"Tonight's chapter can still change: the build starts at {s['overnight']['start']}."
                     if can else f"{why} Anything new goes to the next build day.")}


def ask_values(cfg: dict, chapter, t: float, earlier: list[dict]) -> dict:
    """ask/aside.py's context for the moment they tapped, without the coming text word for word: told what
    comes next verbatim, GPT-Live read on from it (voice/gpt_live REPORT, check 9)."""
    v = chapter.context(t, earlier)
    return {k: v[k] for k in ("identity", "profile", "chapter", "where", "just_said", "later", "dossier",
                              "ledger", "earlier_asides")}


# --- the routes ---------------------------------------------------------------------------

class Start(BaseModel):
    mode: str
    sdp: str
    run: str | None = None
    t: float = 0.0


class Line(BaseModel):
    session: str
    who: str
    text: str


class Tool(BaseModel):
    session: str
    name: str
    args: dict | None = None


class End(BaseModel):
    session: str
    seconds: float = 0.0
    backend: dict | None = None
    how: str = "other"


def mount(app, cfg: dict, pipe=None, chat=None) -> None:
    from fastapi import HTTPException

    from ask import aside as A

    from . import library, schedule

    open_: dict[str, dict] = {}
    lock = threading.Lock()
    planning = {"thread": None}

    def chapters() -> Path:
        return common.listener_dir(cfg) / "chapters"

    def new_id() -> str:
        sessions = common.listener_dir(cfg) / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        base = time.strftime("%Y%m%d-%H%M%S")
        sid, n = base, 2
        while (sessions / f"{sid}.jsonl").exists() or sid in open_:
            sid, n = f"{base}-{n}", n + 1
        return sid

    def write_line(s: dict, rec: dict) -> None:
        with open(s["path"], "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), **rec}, ensure_ascii=False) + "\n")

    def get(sid: str) -> dict:
        s = open_.get(sid)
        if s is None:
            raise HTTPException(409, "That conversation has ended.")
        return s

    def close(s: dict, page_seconds: float, backend: dict | None, how: str, seconds: float | None = None) -> dict:
        """End a session: log its cost, keep an aside, start the memory pass. GPT-Live's seconds are the
        page's last usage figure or the wall clock since the start, whichever is more (the page's lags)."""
        if seconds is None:
            seconds = time.monotonic() - s["t0"]
        billed = round(max(page_seconds or 0.0, seconds), 1)
        usd = cost(cfg, billed, backend)
        log = Run(name="sessions", root=common.listener_dir(cfg) / "sessions")
        log.log({"stage": "voice", "session": s["id"], "mode": s["mode"], "provider": "openai",
                 "live_id": s["live_id"], "seconds": billed, "page_seconds": page_seconds, "backend": backend,
                 "cost_usd": usd, "ended": how})
        out: dict = {"ended": True, "cost_usd": usd}
        if s["mode"] == "ask":
            ch = s["chapter"]
            rewind = (s.get("resume") or {}).get("rewind")
            reason = (s.get("resume") or {}).get("reason") or how
            restart = ch.restart_point(s["t"], seconds, rewind)
            if s["lines"]:  # nothing said (a tap back while it connected): no aside to keep
                A.append_aside(s["root"], {"id": s["aside"], "kind": "ask", "voice": "gpt-live", "t": round(s["t"], 3),
                                           "sentence_id": s["sentence_id"], "segment": ch.sents[ch.locate(s["t"])].seg + 1,
                                           "transcript": s["lines"], "ended": reason, "seconds": round(seconds, 1),
                                           "restart": restart, "rewind_asked": rewind, "seeds": s["seeds"],
                                           "live_id": s["live_id"], "cost_usd": usd})
                aside_seeds(s)
            out["restart"] = restart
        write_line(s, {"who": "session", "ended": how, "seconds": billed, "cost_usd": usd})
        if s["mode"] != "ask" and chat is not None:  # the usual after-conversation pass (profile, library, seeds)
            from stages import memory
            t = threading.Thread(target=memory.after_session, args=(cfg, s["path"]), name="overnight-memory",
                                 daemon=True)
            chat["passes"].append(t)
            t.start()
        return out

    def aside_seeds(s: dict) -> None:
        """An aside's saved topics (kind idea) and the listener's questions (in their words) into seeds.jsonl:
        after_chapter ran when the chapter was built, before any morning aside existed."""
        items, last = [], None
        for ln in s["lines"]:
            text = (ln.get("text") or "").strip()
            if ln.get("who") == "listener" and text:
                last = text
                if library.is_question(text):
                    items.append({"kind": "question", "text": text, "words": text})
            elif ln.get("who") == "tool" and ln.get("name") == "save_seed":
                args = ln.get("input") or {}
                topic, why = (args.get("topic") or "").strip(), (args.get("why") or "").strip()
                if topic:  # the text after_chapter gives a saved seed, so the two dedupe
                    items.append({"kind": "idea", "text": topic + (f" ({why})" if why else ""), "words": last})
        if items:
            library.add_seeds(common.listener_dir(cfg), items,
                              source={"chapter": s["run"], "aside": s["aside"], "session": s["id"]})

    def sweep() -> None:
        """End sessions the page never ended once they have been quiet for stale_minutes, counting
        GPT-Live up to their last line or tool call."""
        limit = float(lcfg(cfg).get("stale_minutes", 30)) * 60
        now = time.monotonic()
        with lock:
            stale = [open_.pop(sid) for sid, s in list(open_.items()) if now - s["last"] > limit]
        for s in stale:
            print(f"  live: ending stale session {s['id']} ({s['mode']})")
            close(s, 0.0, None, "stale", seconds=s["last"] - s["t0"])

    @app.post("/api/overnight/live/start")
    def start(req: Start) -> dict:
        if req.mode not in MODES:
            raise HTTPException(400, "Unknown mode.")
        sweep()
        now = datetime.now()
        extra: dict = {}
        try:
            if req.mode in ("intro", "ask"):
                if not req.run:
                    raise HTTPException(400, "Which chapter?")
                try:
                    root = Run.open(req.run, runs_dir=chapters()).root
                except StageError:
                    raise HTTPException(404, "No such chapter.") from None
            if req.mode == "intro":
                values = intro_values(cfg, root, now)
            elif req.mode == "change":
                values = change_values(cfg, now)
            else:
                if not A.can_ask(root):
                    raise HTTPException(400, "This chapter has no timed transcript, so it can't be asked about yet.")
                ch = A.Chapter(root, cfg)
                t = min(max(req.t, 0.0), ch.duration)
                values = ask_values(cfg, ch, t, A.load_asides(root))
                extra = {"chapter": ch, "t": t, "sentence_id": ch.sents[ch.locate(t)].id, "root": root,
                         "aside": f"{time.strftime('%Y%m%d-%H%M%S')}-live", "seeds": [], "lines": []}
            session = session_config(cfg, req.mode, values)
            got = create_webrtc(cfg, session, req.sdp)
        except StageError as e:
            raise HTTPException(503, str(e)) from None
        sid = new_id()
        s = {"id": sid, "mode": req.mode, "run": req.run, "live_id": got["id"], "t0": time.monotonic(),
             "last": time.monotonic(),
             "path": common.listener_dir(cfg) / "sessions" / f"{sid}.jsonl", "tools": [], **extra}
        write_line(s, {"who": "session", "mode": req.mode, "run": req.run, "live_id": got["id"],
                       "voice": session["audio"]["output"]["voice"], "model": session["model"]})
        with lock:
            open_[sid] = s
        out = {"session": sid, "live_id": got["id"], "sdp": got["sdp"], "notes": notes(),
               "settings": page_settings(cfg)}
        if req.mode == "intro":
            out["read_live"] = bool(profiles.settings(cfg, listener=common.listener_dir(cfg))["playback"]["read_live"])
            out["title"] = (profiles.read_json(root / "00_source" / "up_next.json", {}) or {}).get("title") or \
                A.chapter_title(root)
        if req.mode == "change":
            out["title"] = values["title"]
        if req.mode == "ask":
            out.update(t=extra["t"], sentence_id=extra["sentence_id"])
        return out

    @app.get("/api/overnight/paragraphs/{run}")
    def paragraphs(run: str) -> dict:
        """The chapter as the guide reads it live (playback.read_live): record.py's paragraphs."""
        from . import record
        try:
            r = Run.open(run, runs_dir=chapters())
            return {"paragraphs": [{"id": p["id"], "text": p["text"], "pause": p["pause"]} for p in record.paragraphs(r)]}
        except StageError:
            raise HTTPException(404, "No such chapter.") from None

    @app.post("/api/overnight/live/line")
    def line(req: Line) -> dict:
        s = get(req.session)
        s["last"] = time.monotonic()
        if req.text.strip() and req.who in ("listener", "guide", "page"):
            write_line(s, {"who": req.who, "text": req.text.strip()})
            if s["mode"] == "ask":
                s["lines"].append({"s": round(time.monotonic() - s["t0"], 2), "who": req.who, "text": req.text.strip()})
        return {"ok": True}

    def set_next(s: dict, args: dict) -> dict:
        from . import planner
        listener = common.listener_dir(cfg)
        mode = args.get("mode") or "topic"
        if mode == "keep":
            return {"kept": True}
        request = {"mode": mode if mode in ("surprise", "continue", "topic") else "topic",
                   "topic": (args.get("topic") or "").strip(), "notes": (args.get("notes") or "").strip(),
                   "session": s["id"]}
        settings = profiles.settings(cfg, listener=listener)
        from . import runner
        card = runner.reconcile(listener)
        can, why = schedule.can_change(card, settings, datetime.now())
        if not can:
            library.queue_add(listener, request)
            return {"queued": True, "when": schedule.next_label(settings, datetime.now()),
                    "note": f"{why} Saved for the next build day."}

        def replan():
            try:
                planner.plan(cfg, listener, request=request)  # past the cutoff mid-pass, it queues it itself
            except Exception as e:  # the card keeps the old plan; the request waits for the next pass
                print(f"  replanning failed: {type(e).__name__}: {e}; the request goes to the queue")
                library.queue_add(listener, request)

        t = threading.Thread(target=replan, name="overnight-replan", daemon=True)
        planning["thread"] = t
        t.start()
        return {"changing": True, "note": "The card is being rewritten now; it shows in about a minute."}

    @app.post("/api/overnight/live/tool")
    def tool(req: Tool) -> dict:
        s = get(req.session)
        s["last"] = time.monotonic()
        args = dict(req.args or {})
        if req.name not in MODE_TOOLS[s["mode"]]:
            out = {"error": f"No tool named {req.name}."}
        elif req.name == "start_chapter":
            out = {"starting": True}
        elif req.name == "set_next_chapter":
            out = set_next(s, args)
        elif req.name == "resume_chapter":
            s["resume"] = {"reason": args.get("reason") or "other", "rewind": args.get("rewind")}
            out = {"resuming": True}
        else:  # save_seed
            saved = A.save_seed(s["root"], args.get("topic", ""), args.get("why", ""), s["t"], s["sentence_id"])
            s["seeds"].append(saved)
            out = {"saved": True}
        s["tools"].append(req.name)
        write_line(s, {"who": "tool", "name": req.name, "input": args, "result": out})
        if s["mode"] == "ask":
            s["lines"].append({"s": round(time.monotonic() - s["t0"], 2), "who": "tool", "name": req.name,
                               "input": args, "result": out})
        return out

    @app.post("/api/overnight/live/end")
    def end(req: End) -> dict:
        with lock:
            s = open_.pop(req.session, None)
        if s is None:
            raise HTTPException(409, "That conversation has ended.")
        return close(s, req.seconds, req.backend, req.how)

    @app.post("/api/overnight/live/audio/{session}")
    async def audio(session: str, request: Request) -> dict:
        """The page's recording of one conversation (both sides), kept beside its transcript. It arrives
        after /end, so the session is known by its transcript, not by open_."""
        sessions = common.listener_dir(cfg) / "sessions"
        if not re.fullmatch(r"\d{8}-\d{6}(-\d+)?", session) or not (sessions / f"{session}.jsonl").exists():
            raise HTTPException(404, "No such conversation.")
        body = await request.body()
        if not body:
            raise HTTPException(400, "No audio.")
        if len(body) > float(lcfg(cfg).get("audio_max_mb", 200)) * 1e6:
            raise HTTPException(413, "That recording is too long to keep.")
        ext = "ogg" if "ogg" in (request.headers.get("content-type") or "") else "webm"
        (sessions / f"{session}.{ext}").write_bytes(body)
        return {"saved": f"{session}.{ext}", "bytes": len(body)}


def page_settings(cfg: dict) -> dict:
    return dict(lcfg(cfg).get("page") or {})
