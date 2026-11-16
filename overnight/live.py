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
INSTRUCTIONS_LIMIT_CHARS = int(16000 * 3.5)  # GPT-Live's instructions: 16,000 tokens, at 3.5 characters a token
# {{recent}}'s parts, in characters: the end of the last conversation (an ask's is shortest: its chapter fills
# the rest), and the latest history entry (an ask has none: its own chapter is already in {{chapter}})
RECENT_CHARS = {"intro": 1200, "change": 1200, "ask": 600}
RECENT_ENTRY_CHARS = 700


def lcfg(cfg: dict) -> dict:
    return cfg["overnight"]["live"]


def notes(name: str = "overnight_voice_notes") -> dict[str, str]:
    """prompts/overnight_voice_notes.md: what the page tells the guide, one `key: text` per line (also
    prompts/overnight_voice_meeting.md, the lines {{meeting}}, {{situation}} and {{recent}} are made of)."""
    out = {}
    for line in load_prompt(name).splitlines():
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


def _render(mode: str, values: dict) -> str:
    shared = load_prompt("overnight_voice_guide", identity=values["identity"], meeting=values["meeting"],
                         recent=values["recent"])
    return load_prompt(f"overnight_voice_{mode}", **{**values, "guide": shared})


def trim_dossier(text: str, target: int) -> str:
    """An ask's {{dossier}} cut to about `target` characters: the one-line entries (other segments) go first,
    from the end, then the full ones (this segment and the next), and a line says how many were left out."""
    lines = text.splitlines()
    full = [bool(re.match(r"\[D\d+\]\s*\(", ln)) for ln in lines]
    m = notes("overnight_voice_meeting")
    dropped = 0
    for want_full in (False, True):
        for i in range(len(lines) - 1, -1, -1):
            if sum(len(x) + 1 for x in lines if x is not None) + 60 <= target:
                break
            if lines[i] is not None and full[i] == want_full:
                lines[i], dropped = None, dropped + 1
    kept = [x for x in lines if x is not None]
    return "\n".join(kept + ([_fill(m["dossier_trimmed"], n=dropped)] if dropped else []))


def session_config(cfg: dict, mode: str, values: dict) -> dict:
    """The session GPT-Live opens. Instructions over its limit are cut to fit, never refused: {{recent}} goes
    first (a short note in its place), then an ask's dossier (trim_dossier)."""
    L = lcfg(cfg)
    text = _render(mode, values)
    if len(text) > INSTRUCTIONS_LIMIT_CHARS and values.get("recent"):
        values = {**values, "recent": notes("overnight_voice_meeting")["recent_trimmed"]}
        text = _render(mode, values)
    over = len(text) - INSTRUCTIONS_LIMIT_CHARS
    if over > 0 and values.get("dossier"):
        values = {**values, "dossier": trim_dossier(values["dossier"], len(values["dossier"]) - over)}
        text = _render(mode, values)
    if len(text) > INSTRUCTIONS_LIMIT_CHARS:
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

def _fill(text: str, **fields) -> str:
    for k, v in fields.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def _identity(cfg: dict, meet: dict) -> str:
    m = notes("overnight_voice_meeting")
    return guide.identity(cfg) or (m["identity_first"] if meet["first"] else m["identity_returning"])


def meeting_text(meet: dict, mode: str = "change") -> str:
    """{{meeting}}: whether this is a first meeting, and what the guide and listener share if not. The last
    chapter is named for a change; for an intro it is the one before the chapter being introduced (memory.meeting
    left that out); an ask names none (its chapter, which may be any, is in {{chapter}})."""
    m = notes("overnight_voice_meeting")
    if meet["first"]:
        return m["first"]
    known = []
    for key, n in (("count_chapters", meet["chapters"]), ("count_talks", meet["conversations"])):
        if n:
            known.append(_fill(m[key], n=n, s="" if n == 1 else "s"))
    known.append(_fill(m["named"], name=meet["guide_name"]) if meet.get("guide_name") else m["unnamed"])
    if meet.get("last_chapter") and mode != "ask":
        known.append(_fill(m["last_chapter_before" if mode == "intro" else "last_chapter"], title=meet["last_chapter"]))
    return _fill(m["returning"], known="; ".join(known))


def recent_text(cfg: dict, mode: str = "change", exclude_run: str | None = None) -> str:
    """{{recent}}: the end of their latest conversation and the latest chapter (history.md) other than
    `exclude_run`, the chapter this session is about; an ask gets no chapter entry (RECENT_CHARS)."""
    from stages import memory
    m = notes("overnight_voice_meeting")
    got = memory.recent(cfg, exclude_run=exclude_run, max_chars=RECENT_CHARS[mode])
    if mode == "ask":
        got["chapter"] = ""
    elif len(got["chapter"]) > RECENT_ENTRY_CHARS:
        got["chapter"] = got["chapter"][:RECENT_ENTRY_CHARS - 1].rstrip() + "…"
    parts = []
    if got["conversation"]:
        what = m.get(f"mode_{got['mode']}", "") if got["mode"] else ""
        when = got["when"]
        try:
            when = f"{datetime.fromisoformat(when):%A %-d %B}"
        except ValueError:
            pass
        parts.append(_fill(m["recent_conversation"], when=when, what=what) + "\n" + got["conversation"])
    if got["chapter"]:
        parts.append(m["recent_chapter_before" if exclude_run else "recent_chapter"] + "\n" + got["chapter"])
    return "\n\n".join(parts) or m["recent_none"]


def shared_values(cfg: dict, mode: str = "change", exclude_run: str | None = None) -> dict:
    """What every session gets: identity, profile, {{meeting}}, {{recent}}. A returning listener with no
    profile.md is not told "this is a first meeting" by the profile's placeholder. `exclude_run`: the chapter
    an intro or an ask is about, never presented as "the latest chapter you made them"."""
    from stages import memory
    meet = memory.meeting(cfg, exclude_run=exclude_run)
    has_profile = (common.listener_dir(cfg) / "profile.md").exists()
    return {"identity": _identity(cfg, meet), "meeting": meeting_text(meet, mode),
            "recent": recent_text(cfg, mode, exclude_run),
            "profile": listener_profile(cfg) if has_profile or meet["first"]
            else notes("overnight_voice_meeting")["profile_none"]}


def _finished_when(root: Path, now: datetime) -> str:
    t = profiles.finished_at(root)
    if not t:
        return "recently"
    at = datetime.fromtimestamp(t)
    days = (now.date() - at.date()).days
    if days <= 0:
        return "overnight" if at.hour < 6 else "this morning" if at.hour < 12 else "today"
    if days == 1:
        return "last night" if at.hour >= 18 else "yesterday"
    return f"on {at:%A}" if days < 7 else f"on {at:%-d %B}"


def _day_word(for_morning: str | None, now: datetime) -> str:
    """'tomorrow's', 'this morning's', 'Thursday's': whose chapter the card plans."""
    try:
        d = datetime.fromisoformat(str(for_morning)).date()
    except ValueError:
        return "the next"
    days = (d - now.date()).days
    if days == 0:
        return "this morning's" if now.hour < 12 else "today's"
    if days == 1:
        return "tomorrow's"
    return f"{d:%A}'s"


def change_situation(card: dict | None, can: bool, why: str, now: datetime) -> str:
    m = notes("overnight_voice_meeting")
    if not card or card.get("status") == "ready" or not card.get("title"):
        return m["situation_change_none"]
    if not can:
        return _fill(m["situation_change_late"], title=card["title"], why=why.rstrip(". ").lower() if why else "")
    return _fill(m["situation_change"], title=card["title"], day=_day_word(card.get("for_morning"), now))


def _now_text(now: datetime) -> str:
    part = "morning" if now.hour < 12 else "afternoon" if now.hour < 18 else "evening"
    return f"{now:%A} {part}"


def intro_values(cfg: dict, root: Path, now: datetime) -> dict:
    brief = root / "intro_brief.md"
    if not brief.exists():
        raise StageError("This chapter has no intro brief.")
    s = profiles.settings(cfg, listener=common.listener_dir(cfg))
    title = (profiles.read_json(root / "00_source" / "up_next.json", {}) or {}).get("title")
    if not title:
        from ask import aside as A
        title = A.chapter_title(root)
    situation = _fill(notes("overnight_voice_meeting")["situation_intro"], title=title, when=_finished_when(root, now))
    return {**shared_values(cfg, "intro", exclude_run=root.name), "now": _now_text(now), "situation": situation,
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
    if card and card.get("status") != "ready" and card.get("notes_added"):
        plan += "\nNotes they added to it:\n" + "\n".join(f"- {n.get('text')}" for n in card["notes_added"])
    return {**shared_values(cfg, "change"), "now": _now_text(now),
            "situation": change_situation(card, can, why, now),
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
    out = {k: v[k] for k in ("chapter", "where", "just_said", "later", "dossier", "ledger",
                             "earlier_asides")}
    at = f"{int(t // 60)}:{int(t % 60):02d} in"
    situation = _fill(notes("overnight_voice_meeting")["situation_ask"], title=chapter.title, at=at)
    return {**out, **shared_values(cfg, "ask", exclude_run=Path(chapter.run).name), "situation": situation}


# --- notes for the next chapter ----------------------------------------------------------

NOTE_MAX_CHARS = 2000


def add_note(cfg: dict, text: str, via: str = "typed", now: datetime | None = None) -> dict:
    """A note for the next chapter ("Add a note" under the Up next card, or set_next_chapter keep with notes).
    Returns {"where": "card"|"tonight"|"next", "note", "message"} (overnight/DESIGN.md, Round 2):
      card     a planned card before the cutoff, or a failed one that never started its chapter: the card's
               notes_added, which planner.seed_text puts in seed.md when the build locks it
      tonight  the card's chapter has started (building, or failed and waiting for its retry) and its research
               hasn't finished: the chapter's 00_source/notes.md (which common.seed_inputs gives the research
               judge and the outline, and overnight/write.py the writer and the editor) and the card's
               notes_added (a Change that replaces the card carries them over)
      next     otherwise: next_notes.jsonl, which the next planning pass takes into its card
    All under the card lock, after runner.reconcile (a dead build is failed, not building): the planner's
    final write reads next_notes under the same lock, so no note falls between it and the card."""
    from . import library, locks, runner, schedule
    text = " ".join((text or "").split())[:NOTE_MAX_CHARS]
    if not text:
        raise StageError("The note was empty.")
    listener = common.listener_dir(cfg)
    listener.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now()
    settings = profiles.settings(cfg, listener=listener)
    runner.reconcile(listener)
    note = {"t": now.isoformat(timespec="seconds"), "text": text, "via": via}
    m = notes("overnight_voice_meeting")
    with locks.card_lock(listener):
        card = runner.read_card(listener)
        status = (card or {}).get("status")
        if card and status == "planned" and schedule.can_change(card, settings, now)[0]:
            profiles.write_json(runner.card_path(listener), {**card, "notes_added": [*(card.get("notes_added") or []), note]})
            return {"where": "card", "note": note,
                    "message": _fill(m["note_card"], day=_day_word(card.get("for_morning"), now))}
        if card and status == "failed" and not card.get("chapter"):  # stopped before it started: lock reads the card
            profiles.write_json(runner.card_path(listener), {**card, "notes_added": [*(card.get("notes_added") or []), note]})
            return {"where": "card", "note": note, "message": m["note_retry"]}
        if card and status in ("building", "failed") and card.get("chapter"):
            root = listener / "chapters" / card["chapter"]
            if not (root / "04_research" / "ledger_update.md").exists():
                p = root / "00_source" / "notes.md"
                p.parent.mkdir(parents=True, exist_ok=True)
                if not p.exists():
                    p.write_text(load_prompt("seed_notes").strip() + "\n\n", encoding="utf-8")
                with open(p, "a", encoding="utf-8") as f:
                    f.write(f"- {text}\n")
                profiles.write_json(runner.card_path(listener),
                                    {**card, "notes_added": [*(card.get("notes_added") or []), note]})
                return {"where": "tonight", "note": note,
                        "message": m["note_tonight" if status == "building" else "note_retry"]}
        rec = library.add_next_note(listener, text, via)
        key = ("note_next" if card is None or status not in ("planned", "building", "failed") else
               "note_next_failed" if status == "failed" else "note_next_late")
    return {"where": "next", "note": {**note, "id": rec["id"]}, "message": m[key]}


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


class Note(BaseModel):
    text: str = ""


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
        remember = s["mode"] != "ask" or lcfg(cfg).get("memory_on_asks", True)  # asks: their moments too
        if remember and chat is not None:  # the usual after-conversation pass (profile, library, seeds, moments)
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
                here = ch.sents[ch.locate(t)]
                extra = {"chapter": ch, "t": t, "sentence_id": here.id, "root": root,
                         "paragraph_id": f"seg{here.seg + 1:02d}-p{here.para + 1:02d}",
                         "aside": f"{time.strftime('%Y%m%d-%H%M%S')}-live", "seeds": [], "lines": []}
            session = session_config(cfg, req.mode, values)
            got = create_webrtc(cfg, session, req.sdp)
        except StageError as e:
            raise HTTPException(503, str(e)) from None
        sid = new_id()
        s = {"id": sid, "mode": req.mode, "run": req.run, "live_id": got["id"], "t0": time.monotonic(),
             "last": time.monotonic(),
             "path": common.listener_dir(cfg) / "sessions" / f"{sid}.jsonl", "tools": [], **extra}
        first = {"who": "session", "mode": req.mode, "run": req.run, "live_id": got["id"],
                 "voice": session["audio"]["output"]["voice"], "model": session["model"]}
        if req.mode == "ask":  # where they tapped: the memory pass puts the aside's moments there
            first.update(t=round(extra["t"], 2), sentence_id=extra["sentence_id"], paragraph_id=extra["paragraph_id"])
        write_line(s, first)
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
            said = (args.get("notes") or "").strip()
            if not said:
                return {"kept": True}
            got = add_note(cfg, said, via="voice")
            return {"kept": True, "added": got["where"], "note": got["message"]}
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
            saved = A.save_seed(s["root"], args.get("topic", ""), args.get("why", ""), s["t"], s["sentence_id"],
                                session=s["id"], paragraph_id=s.get("paragraph_id"))
            s["seeds"].append(saved)
            out = {"saved": True}
        s["tools"].append(req.name)
        write_line(s, {"who": "tool", "name": req.name, "input": args, "result": out})
        if s["mode"] == "ask":
            s["lines"].append({"s": round(time.monotonic() - s["t0"], 2), "who": "tool", "name": req.name,
                               "input": args, "result": out})
        return out

    @app.post("/api/overnight/note")
    def note(req: Note) -> dict:
        """"Add a note" under the Up next card: typed, for the next chapter (add_note says where it went)."""
        try:
            return add_note(cfg, req.text, via="typed")
        except StageError as e:
            raise HTTPException(400, str(e)) from None

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
