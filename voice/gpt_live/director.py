"""The director: a model that reads the live conversation and decides the guide's next move.

Ian, 2026-11-19 (his third tester run): "add some kind of listening agent on top of the gptlive model, in order
to direct it when to do things ... a model to decide when to initiate the want to hear what I do, or what to do
in each moment". GPT-Live decides for itself when to speak and takes notes appended to its context as
instructions, so the director cannot shape the reply the guide is already giving (GPT-Live answers about 0.9 s
after the listener stops; a decision takes about 2 s). It steers the next opening: when to offer what the guide
does, what to pitch, a question to answer, a playful hook to stay on, stepping back, a light check-in.

- `decide(cfg, log_run, ...)`: one Claude call (config stages.director: Sonnet 5.5, effort low) with
  prompts/live_director.md as the system prompt and prompts/live_director_moment.md as the moment (the state the
  page reports, the profile, the conversation so far); structured output (SCHEMA). Any error is a "wait" with the
  error, never an exception: the page counts failures and goes back to its timers after two in a row.
- `lines_from_transcript`: the conversation as the director reads it, from the session's transcript
  (listener/sessions/<id>.jsonl): the guide's and the listener's lines, the page's notes ("app note", so it
  doesn't repeat them) and the tools called. Rows of its own ("director") are left out; they carry `note`, not
  `text`, so guide.earlier_text, memory and session.explained() never read them either.
- `merge_recent`: the page sends its last few lines with each request; any not yet in the transcript (its line
  posts are fire-and-forget and may arrive later) are added at the end. Sturdier than a count of posts.
- `ask(cfg, session, req)`: the route's work (voice/gpt_live/routes.py POST /api/live/direct): the per-
  conversation caps (live.director.max_calls, max_usd, and guide.max_session_usd over everything the
  conversation cost), the call with a timeout (live.director.timeout_ms), the decision recorded in the
  transcript as a "director" row and its cost in listener/sessions/log.jsonl as stage "director" with the
  session id (so Session.spent() counts it).

The page (page/live.js) asks only in conversation before a chapter starts and while it is made (never while
reading, in an aside, a check-in, resting or the handback), and delivers a move only in a real lull, dropping it
if anyone has spoken since it was asked for or the move is already done.
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime
from pathlib import Path

from stages import llm
from stages.common import load_prompt

MOVES = ["wait", "draw_out", "offer_what_i_do", "ask_interest", "pitch", "answer", "start_chapter", "step_back",
         "check_in"]
SCHEMA = {
    "type": "object",
    "properties": {
        "why": {"type": "string"},
        "move": {"type": "string", "enum": MOVES},
        "note": {"type": "string"},
    },
    "required": ["why", "move", "note"],
    "additionalProperties": False,
}
STAGE = "director"
MAX_LINES = 80          # the conversation's last lines the director reads
MAX_CHARS = 12000
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="director")


def settings(cfg: dict) -> dict:
    return {"enabled": False, "timeout_ms": 4000, "max_calls": 150, "max_usd": 0.5, **(cfg["live"].get("director") or {})}


def _secs(t0: datetime | None, stamp: str | None) -> int | None:
    try:
        t = datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    return int((t - t0).total_seconds()) if t0 else 0


def lines_from_transcript(path: Path) -> list[dict]:
    """[{"secs", "who", "text"}]: who is guide, listener, app (a page note) or tool (a tool the guide called)."""
    out: list[dict] = []
    if not path.exists():
        return out
    t0 = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        who, text = rec.get("who"), str(rec.get("text") or "").strip()
        if t0 is None:
            try:
                t0 = datetime.fromisoformat(str(rec.get("t")))
            except ValueError:
                pass
        secs = _secs(t0, rec.get("t"))
        if who in ("guide", "listener") and text:
            out.append({"secs": secs, "who": who, "text": text})
        elif who == "page" and text:
            out.append({"secs": secs, "who": "app", "text": text})
        elif who == "tool" and rec.get("name"):
            out.append({"secs": secs, "who": "tool", "text": str(rec["name"])})
    return out


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower()).split())


def _is_subseq(seq: list, tail: list) -> bool:
    it = iter(tail)
    return all(any(t == s for t in it) for s in seq)


def merge_recent(lines: list[dict], recent: list[dict], secs_now: int | None = None) -> list[dict]:
    """The page's recent lines that the transcript doesn't have yet, added at the end in the page's order. By
    order, not by text: the longest run of the page's lines (from its first) found in order in the transcript's
    tail is already there, and the rest is added. A set of texts dropped a fresh "Yeah." to a pitch when the
    listener had said "Yeah." a minute before and its /api/live/line post hadn't landed yet."""
    valid = []
    for x in recent or []:
        who = {"page": "app"}.get(str(x.get("who")), str(x.get("who")))
        text = str(x.get("text") or "").strip()
        if who in ("guide", "listener", "app") and text:
            valid.append((who, text))
    keys = [(who, _norm(text)) for who, text in valid]
    tail = [(x["who"], _norm(x["text"])) for x in lines[-40:]]
    k = next(k for k in range(len(keys), -1, -1) if _is_subseq(keys[:k], tail))
    return list(lines) + [{"secs": secs_now, "who": who, "text": text} for who, text in valid[k:]]


def transcript_text(lines: list[dict]) -> str:
    rows = []
    for x in lines[-MAX_LINES:]:
        s = x.get("secs")
        stamp = f"[{s // 60}:{s % 60:02d}] " if isinstance(s, int) and s >= 0 else ""
        if x["who"] == "tool":
            rows.append(f"{stamp}app: (the guide called {x['text']})")
        elif x["who"] == "app":
            rows.append(f"{stamp}app note to the guide: {x['text']}")
        else:
            rows.append(f"{stamp}{x['who']}: {x['text']}")
    text = "\n".join(rows)
    if len(text) > MAX_CHARS:
        text = "(the start is left out)\n" + text[-MAX_CHARS:].split("\n", 1)[-1]
    return text or "(nothing said yet)"


def moment(*, phase: str, seconds: int, explained: bool, started: bool, turns: int, silence_secs: float,
           last_who: str, profile: str, transcript: str) -> str:
    return load_prompt("live_director_moment", phase=phase, seconds=str(int(seconds)),
                       explained="yes" if explained else "no", started="yes" if started else "no",
                       turns=str(int(turns)), silence_secs=str(round(float(silence_secs), 1)),
                       last_who=last_who or "nobody", profile=profile.strip() or "(nothing known yet)",
                       transcript=transcript)


def parse(text: str) -> dict:
    """The model's JSON as a decision; a move outside MOVES is an error."""
    got = json.loads((text or "").strip())
    move = got.get("move")
    if move not in MOVES:
        raise ValueError(f"unknown move {move!r}")
    # The note goes into the guide's ear inside brackets: nothing of the JSON or markup may come with it (replays
    # saw notes ending in '}' and '```</br>{').
    note = re.split(r"[{}<>`\[\]]", str(got.get("note") or ""), maxsplit=1)[0].strip().strip('"“”').strip()
    if move != "wait" and not note:
        raise ValueError(f"{move} with no note")
    return {"move": move, "note": note if move != "wait" else "", "why": str(got.get("why") or "")[:200]}


def decide(cfg: dict, log_run, user: str, log_extra: dict | None = None) -> dict:
    """One decision: {"move", "note", "why", "cost_usd", "ms"}, or {"move": "wait", "error"} on any failure."""
    t = time.monotonic()
    try:
        res = llm.call(log_run, cfg, STAGE, role="guide", system=load_prompt("live_director"),
                       messages=[{"role": "user", "content": user}], json_schema=SCHEMA, label=STAGE,
                       preapproved=True, log_extra=log_extra)
    except Exception as e:  # never break the conversation: the page falls back to its timers
        return {"move": "wait", "note": "", "error": f"{type(e).__name__}: {e}"[:300],
                "ms": int((time.monotonic() - t) * 1000), "cost_usd": 0.0}
    ms = int((time.monotonic() - t) * 1000)
    try:
        out = parse(res.text)
    except (ValueError, json.JSONDecodeError, AttributeError) as e:
        return {"move": "wait", "note": "", "error": f"unreadable decision: {e}"[:300], "ms": ms,
                "cost_usd": res.usage.cost_usd}
    return {**out, "ms": ms, "cost_usd": res.usage.cost_usd}


def spent(session) -> tuple[int, float]:
    """This conversation's director calls so far and what they cost."""
    recs = [r for r in session.log_run.read_log() if r.get("session") == session.id and r.get("stage") == STAGE
            and not r.get("error")]
    return len(recs), sum(r.get("cost_usd", 0) or 0 for r in recs)


PHASES = {"talk": "first conversation, no chapter yet", "company": "their chapter is being made; you keep them company"}


def ask(cfg: dict, session, req: dict, lock=None) -> dict:
    """The route's work. `req`: {"id", "trigger", "silence_ms", "last_who", "recent", "page": {...}} from the
    page. `lock`: the conversation lock, held only to read and write the transcript, never during the call."""
    D = settings(cfg)
    rid = req.get("id")
    page = req.get("page") or {}
    if not D.get("enabled"):
        return {"id": rid, "move": "wait", "error": "off"}
    calls, usd = spent(session)
    cap = float(cfg["guide"].get("max_session_usd") or 0)
    if calls >= int(D["max_calls"]) or usd >= float(D["max_usd"]) or (cap and session.spent() >= cap):
        return {"id": rid, "move": "wait", "error": "cap"}
    from . import session as live_session

    with lock if lock is not None else _Null():
        lines = merge_recent(lines_from_transcript(session.transcript), req.get("recent") or [],
                             int(page.get("seconds") or 0) or None)
        explained = bool(page.get("explained")) or live_session.explained(session.transcript)
    # A returning listener knows what it does (session.values_for gives that guide offer_known.md: "Never offer to
    # explain it again"), and that guide never says "audio chapters": the offer is done, the pitch is open.
    known = not str(session.values.get("identity", "")).startswith("(none")
    explained = explained or known
    started = bool(page.get("started"))
    profile = "\n".join(x for x in (str(session.values.get("identity") or ""), str(session.values.get("profile") or ""))
                        if x.strip())
    user = moment(phase=PHASES["company" if started else "talk"], seconds=int(page.get("seconds") or 0),
                  explained=explained, started=started, turns=int(page.get("turns") or 0),
                  silence_secs=float(req.get("silence_ms") or 0) / 1000, last_who=str(req.get("last_who") or ""),
                  profile=profile, transcript=transcript_text(lines))
    fut = _POOL.submit(decide, cfg, session.log_run, user, {"session": session.id, "trigger": req.get("trigger")})
    try:
        got = fut.result(timeout=float(D["timeout_ms"]) / 1000)
    except FutureTimeout:  # it goes on in its thread and logs its cost; the page has moved on
        got = {"move": "wait", "note": "", "error": "timeout", "ms": int(D["timeout_ms"]), "cost_usd": 0.0}
    if known and got.get("move") == "offer_what_i_do":  # never for a listener who knows (the page only sends what comes back)
        got = {**got, "move": "wait", "note": ""}
    rec = {k: got.get(k) for k in ("move", "note", "why", "ms", "cost_usd", "error") if got.get(k) not in (None, "")}
    with lock if lock is not None else _Null():
        session.record("director", id=rid, trigger=req.get("trigger"), **rec)
    return {"id": rid, **{k: got.get(k, "") for k in ("move", "note", "why")}, "ms": got.get("ms"),
            "cost_usd": got.get("cost_usd", 0.0), **({"error": got["error"]} if got.get("error") else {})}


class _Null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
