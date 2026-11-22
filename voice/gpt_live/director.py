"""The earpiece: a model in the background that keeps a plan for the live conversation and whispers the guide a
note when it would help.

Ian, 2026-11-19 (his third tester run): "add some kind of listening agent on top of the gptlive model, in order
to direct it when to do things". Built first per line and delivered only in lulls, it decided "wait" 12 times of
12 live and sent nothing. Then, the same day: "it can constantly evaluate the conversation and provide updated
notes, on what to do next nudging the voice, perhaps even with a plan that the earpiece agent can use to check
off items or even modify the plan ... without causing a delay in the conversation"; and "the guide should
probably have a general idea of what the task is ... but it should certainly retain all the other stylistic
instructions". So the guide keeps all its instructions and its sense of the arc; the earpiece keeps the plan
(ordered items, each open, done or dropped), checks items off from the transcript whoever did them, changes the
arc when the conversation calls for it, and whispers the next move only when its advice changes. GPT-Live takes
any appended instruction as a task for its very next turn (scratch earpiece/EVIDENCE.md), so the earpiece times
the whisper itself (prompts/live_director.md).

- `decide(cfg, log_run, user)`: one Claude call (config stages.director: Sonnet 5.5, effort low) with
  prompts/live_director.md as the system prompt (prompts/live_director_signals.md after it with
  live.director.signals on) and prompts/live_director_moment.md as the look; structured
  output (SCHEMA: read, plan, next, changed, kind, about, note). `shape` cleans it: a whisper is a note with
  changed true; its note loses any markup and is cut to MAX_WORDS at a sentence end (dropped if no sentence end
  fits). Any error comes back as {"error"} with no whisper, never an exception: the page counts failures and
  goes back to its clocks after two in a row.
- `look(...)`: the look's user message from the conversation's lines, the page's state and the earpiece's own
  state (its previous plan and read, from `memory`; its whispers so far, from the app notes in the transcript,
  since only the page knows what it delivered). The persona check (persona.py) builds its looks with it too.
- `lines_from_transcript`: the conversation as the earpiece reads it, from the session's transcript
  (listener/sessions/<id>.jsonl): the guide's and the listener's lines, the page's notes ("app note") and the
  tools called. Its own rows ("director") are left out of the lines and read by `memory`; they carry `note`,
  never `text`, so guide.earlier_text, memory and session.explained() never read them.
- `merge_recent`: the page sends its last few lines with each request; any not yet in the transcript (its line
  posts are fire-and-forget and may arrive later) are added at the end.
- `ask(cfg, session, req)`: the route's work (voice/gpt_live/routes.py POST /api/live/direct): the per-
  conversation caps (live.director.max_calls, max_usd, and guide.max_session_usd over everything the
  conversation cost), the call with a timeout (live.director.timeout_ms), the look recorded in the transcript
  as a "director" row (the plan with it, read back on the next look) and its cost in
  listener/sessions/log.jsonl as stage "director" with the session id (so Session.spent() counts it).

The page (page/live.js) asks on a cadence (every_secs with something new, a listener turn of every_words words
or every_lines new lines, once after the started note, once when the guide first offers to say what it does or
says it after the listener has spoken, once per silence of silence_secs; company_every_secs
once a chapter has started), one look in flight, never while reading, in an aside, a check-in, resting, muted,
around the handover or handback, or in the step-back reply wait. It delivers a whisper at once by
session.instructions.append (notes.md `direct`, `direct_now`, `direct_start`), dropping only stale kinds after
the start, an offer once explained, an "interest" question once the guide has asked something since the look, a
"now" note the listener has spoken since, and a whisper within
min_gap_secs of the last whisper.

The earpiece view (Ian, 2026-11-20): `view_on` says whether this listener's page loads the panel
(page/earpiece.js); the page posts each whisper's fate (FATES, FATE_REASONS) as a "director_fate" row, and
voice/gpt_live/earpiece_history.py builds the admin's timeline from the transcript.

Listener Signals (Ian's build plan, 2026-11-21: the earpiece reads text, so a flat "yeah, it was good" reads the
same as a warm one): behind live.director.signals (off), `heard` reads the note on the listener's latest turn from
the conversation's signals file (signals/live.py writes it with each measured line), the look carries it in a
<listener_signals> block, the system prompt gains prompts/live_director_signals.md (how to weigh it), and the
director row records it as "heard" (null when there was none). Off, the system prompt, looks and rows are as they
were.
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

STATES = ["open", "done", "dropped"]
KINDS = ["when_it_fits", "now"]
ABOUT = ["offer", "pitch", "interest", "habit", "unanswered", "start", "quiet", "wrap"]
TRIGGERS = ["cadence", "turn_end", "silence", "started", "offered"]
SCHEMA = {
    "type": "object",
    "properties": {
        "read": {"type": "string"},
        "plan": {"type": "array", "items": {
            "type": "object",
            "properties": {"item": {"type": "string"}, "state": {"type": "string", "enum": STATES}},
            "required": ["item", "state"], "additionalProperties": False}},
        "next": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "changed": {"type": "boolean"},
        "kind": {"anyOf": [{"type": "string", "enum": KINDS}, {"type": "null"}]},
        "about": {"anyOf": [{"type": "string", "enum": ABOUT}, {"type": "null"}]},
        "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["read", "plan", "next", "changed", "kind", "about", "note"],
    "additionalProperties": False,
}
STAGE = "director"
MAX_WORDS = 25          # a whisper's hard limit (the prompt asks for 20): cut at a sentence end, else dropped
MAX_LINES = 80          # the conversation's last lines the earpiece reads
MAX_CHARS = 12000
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="director")
NO_WHISPER = {"changed": False, "kind": None, "about": None, "note": None}
SPEAKING = {"listener": "the listener is speaking (their line isn't in the transcript yet)",
            "guide": "the guide is speaking (its line isn't in the transcript yet)",
            "none": "nobody is speaking"}
CLEARED = "Any earlier earpiece note is done."   # in notes.md started_step_back and started_company


def settings(cfg: dict) -> dict:
    return {"enabled": False, "every_secs": 15, "every_words": 12, "every_lines": 4, "company_every_secs": 30,
            "silence_secs": [12], "min_gap_secs": 20, "timeout_ms": 6000, "max_calls": 150, "max_usd": 0.5,
            "audio": {"window_ms": 600, "out_rms": 0.01, "min_rms": 0.02, "floor_x": 4}, "view": False,
            "signals": False, "signals_wait_ms": 400, **(cfg["live"].get("director") or {})}


def view_on(cfg: dict) -> bool:
    """The earpiece panel for this request's listener (Ian, 2026-11-20: "a floating window ... just for me").
    Demo mode: that tester's tester.json `earpiece_view` (the admin page's toggle; the config key is ignored).
    Otherwise config live.director.view. Read on every call, never cached."""
    from stages import common

    if (cfg.get("demo") or {}).get("enabled"):
        try:
            rec = json.loads((common.listener_dir(cfg) / "tester.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(rec, dict) and rec.get("earpiece_view") is True
    return bool(settings(cfg).get("view"))


# A whisper's fate on the page (POST /api/live/direct/fate), for the admin's earpiece history. Recorded as a
# "director_fate" row: never a "text" or "note" field, so nothing that reads the conversation reads it.
FATES = ("sent", "held", "dropped")
FATE_REASONS = {
    "not_here": "the page wasn't in a state to send",
    "stale_after_start": "the chapter had started",
    "explained": "the guide had already said what it does",
    "listener_spoke": "the listener spoke after the look was asked",
    "gap": "too soon after the last whisper",
    "replaced": "a newer note replaced it",
    "forgotten": "a rest, a wake or a new session cleared it",
    "down": "the earpiece went down",
    "ended": "the conversation ended",
    "moved_on": "the guide had asked something since the look was asked",
}


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


def _whisper_patterns() -> list[tuple[str, re.Pattern]]:
    """notes.md's three wrappers as patterns that give back the note: (kind, pattern)."""
    from . import session as live_session

    N = live_session.notes()
    out = []
    for key, kind in (("direct_start", "now"), ("direct_now", "now"), ("direct", "when_it_fits")):
        before, _, after = N[key].partition("{note}")
        out.append((kind, re.compile(r"^" + re.escape(before) + r"(.*?)" + re.escape(after) + r"$", re.S)))
    return out


def whisper_in(text: str) -> tuple[str, str] | None:
    """(kind, note) when an app note is one of the earpiece's whispers as the page delivered it, else None."""
    for kind, pat in _whisper_patterns():
        m = pat.match((text or "").strip())
        if m:
            return kind, m.group(1).strip()
    return None


def _stamp(s) -> str:
    return f"{s // 60}:{s % 60:02d}" if isinstance(s, int) and s >= 0 else "?"


def notes_text(lines: list[dict]) -> str:
    """The earpiece's whispers so far with their times, from the app notes (only the page knows what it
    delivered), and the app's clear when the chapter started (notes.md started_*: CLEARED)."""
    rows, pats = [], _whisper_patterns()
    for x in lines:
        if x["who"] != "app":
            continue
        text = x["text"].strip()
        got = next(((k, m.group(1).strip()) for k, pat in pats if (m := pat.match(text))), None)
        if got:
            rows.append(f"[{_stamp(x.get('secs'))}] ({got[0]}) {got[1]}")
        elif CLEARED in text and rows:
            rows.append(f"[{_stamp(x.get('secs'))}] (cleared) (the app cleared your last whisper: the chapter started)")
    return "\n".join(rows) or "(none yet)"


def transcript_text(lines: list[dict]) -> str:
    """The conversation, timestamped; the earpiece's own whispers are left out (they are listed with their
    times in notes_text)."""
    rows, pats = [], _whisper_patterns()
    for x in lines[-MAX_LINES:]:
        stamp = f"[{_stamp(x.get('secs'))}] " if isinstance(x.get("secs"), int) and x["secs"] >= 0 else ""
        if x["who"] == "tool":
            rows.append(f"{stamp}app: (the guide called {x['text']})")
        elif x["who"] == "app":
            if any(pat.match(x["text"].strip()) for _, pat in pats):
                continue
            rows.append(f"{stamp}app note to the guide: {x['text']}")
        else:
            rows.append(f"{stamp}{x['who']}: {x['text']}")
    text = "\n".join(rows)
    if len(text) > MAX_CHARS:
        text = "(the start is left out)\n" + text[-MAX_CHARS:].split("\n", 1)[-1]
    return text or "(nothing said yet)"


def plan_text(plan: list[dict] | None) -> str:
    if not plan:
        return "(first look: start from the default plan)"
    return "\n".join(f"{i + 1}. [{p['state']}] {p['item']}" for i, p in enumerate(plan))


def trigger_text(trigger: str, D: dict, started: bool, silence_secs: float) -> str:
    if trigger == "started":
        # Asked once, right after the started note, or (on a step back) once the listener answered its offer to
        # chat; the page forgets it over a rest, so it is never minutes old (review, 2026-11-20).
        return "the guide just called start_chapter (the app's started note has gone)"
    if trigger == "offered":
        # Asked once, at the end of the guide's first line that offers to say what it does or says it, once the
        # listener has spoken: the pitch is coming, and a one-word "sure" is under every_words (Pooja, 2026-11-20).
        return "the guide just offered to say what it does, or said it (its first time this conversation)"
    if trigger == "turn_end":
        return "a listener turn just ended"
    if trigger == "silence":
        return f"a silence of {int(silence_secs)} seconds"
    return f"the regular look (every {D['company_every_secs'] if started else D['every_secs']} seconds)"


def memory(path: Path) -> tuple[list[dict] | None, str]:
    """The earpiece's previous plan and read, from its newest rows in the transcript that have them."""
    plan, read = None, "(first look)"
    if not path.exists():
        return plan, read
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if rec.get("who") != "director":
            continue
        plan = rec.get("plan") or plan
        read = rec.get("read") or read
    return plan, read


def look(D: dict, *, lines: list[dict], trigger: str, speaking: str, last_who: str | None, silence_secs: float,
         seconds: int, explained: bool, started: bool, profile: str, plan: list[dict] | None,
         last_read: str, signals: str = "") -> str:
    """One look's user message (prompts/live_director_moment.md). `lines` as lines_from_transcript gives them.
    `signals`: the <listener_signals> block (signals_block), or "" for a look exactly as it was without it."""
    if last_who not in ("guide", "listener"):
        last_who = next((x["who"] for x in reversed(lines) if x["who"] in ("guide", "listener")), "guide")
    trigger = trigger if trigger in TRIGGERS else "cadence"
    return load_prompt("live_director_moment", elapsed=_stamp(max(0, int(seconds))),
                       trigger=trigger_text(trigger, D, started, silence_secs),
                       speaking=SPEAKING.get(speaking, SPEAKING["none"]), last_who=last_who,
                       silence_secs=str(int(max(0.0, float(silence_secs)))),
                       explained="yes" if explained else "no", started="yes" if started else "no",
                       profile=profile.strip() or "(nothing known yet)", plan=plan_text(plan),
                       last_read=last_read or "(first look)", notes=notes_text(lines),
                       transcript=transcript_text(lines), signals=signals,
                       longest=str(max((len((x.get("text") or "").split()) for x in lines if x.get("who") == "listener"),
                                       default=0)))


def cap_words(note: str, n: int = MAX_WORDS) -> str | None:
    """The note within n words: whole, or cut at its last sentence end within them, or None."""
    words = note.split()
    if len(words) <= n:
        return note
    m = re.match(r"^(.*[.!?])(?=\s|$)", " ".join(words[:n]))
    return m.group(1).strip() if m else None


def shape(got: dict) -> dict:
    """A look as the page gets it: {read, plan, next, changed, kind, about, note}. A whisper is a note with
    changed true (kind when_it_fits unless it says now); anything else has no note. Raises ValueError on a value
    outside the schema."""
    plan = []
    for p in got.get("plan") or []:
        if not isinstance(p, dict) or p.get("state") not in STATES or not str(p.get("item") or "").strip():
            raise ValueError(f"bad plan item {p!r}")
        plan.append({"item": str(p["item"]).strip(), "state": p["state"]})
    kind, about = got.get("kind"), got.get("about")
    if kind not in KINDS + [None] or about not in ABOUT + [None]:
        raise ValueError(f"unknown kind or about {kind!r}, {about!r}")
    # The note goes into the guide's ear inside brackets: nothing of the JSON or markup may come with it (replays
    # saw notes ending in '}' and '```</br>{').
    note = re.split(r"[{}<>`\[\]]", str(got.get("note") or ""), maxsplit=1)[0].strip().strip('"“”,').strip()
    note = cap_words(note) if note else None
    nxt = got.get("next")
    out = {"read": str(got.get("read") or "")[:600], "plan": plan,
           "next": str(nxt).strip() if isinstance(nxt, str) and nxt.strip() else None}
    if not (got.get("changed") and note):
        return {**out, **NO_WHISPER}
    return {**out, "changed": True, "kind": kind or "when_it_fits", "about": about, "note": note}


def parse(text: str) -> dict:
    return shape(json.loads((text or "").strip()))


def decide(cfg: dict, log_run, user: str, log_extra: dict | None = None) -> dict:
    """One look: shape()'s fields with "ms" and "cost_usd", or {"error", ...} with no whisper on any failure."""
    t = time.monotonic()
    try:
        # How to weigh <listener_signals> goes after the Rules only with the flag on: off, the tuned prompt testers'
        # looks use is sent exactly as it was, and on, its cached prefix still is.
        system = load_prompt("live_director")
        if settings(cfg).get("signals"):
            system += "\n" + load_prompt("live_director_signals")
        res = llm.call(log_run, cfg, STAGE, role="guide", system=system,
                       messages=[{"role": "user", "content": user}], json_schema=SCHEMA, label=STAGE,
                       preapproved=True, log_extra=log_extra)
    except Exception as e:  # never break the conversation: the page falls back to its clocks
        return {**NO_WHISPER, "error": f"{type(e).__name__}: {e}"[:300], "ms": int((time.monotonic() - t) * 1000),
                "cost_usd": 0.0}
    ms = int((time.monotonic() - t) * 1000)
    try:
        out = parse(res.text)
    except (ValueError, TypeError, json.JSONDecodeError, AttributeError) as e:
        return {**NO_WHISPER, "error": f"unreadable look: {e}"[:300], "ms": ms, "cost_usd": res.usage.cost_usd}
    return {**out, "ms": ms, "cost_usd": res.usage.cost_usd}


def spent(session) -> tuple[int, float]:
    """This conversation's earpiece calls so far and what they cost."""
    recs = [r for r in session.log_run.read_log() if r.get("session") == session.id and r.get("stage") == STAGE
            and not r.get("error")]
    return len(recs), sum(r.get("cost_usd", 0) or 0 for r in recs)


FIELDS = ("read", "plan", "next", "changed", "kind", "about", "note")


def heard(session, req: dict, D: dict) -> str | None:
    """The note on the listener's latest turn (signals.record.note, kept on the turn's record in
    <listener>/signals/<stem>.jsonl by the line route), or None. With the page's `sig_turn` (the turn its look is
    about) it waits up to signals_wait_ms for that turn's record, since the line post and the look leave the page
    together and either can land first; a record from an earlier turn gives no note. A `sig_turn` below 1 (turns
    count from 1) means the page has no measured latest turn, a typed line last or nothing said yet: no note, and no
    wait. Without it, the newest page record. The folder is the transcript's: the route's cfg is global, and a
    tester's folder comes from the request."""
    from signals import store

    listener, turn = Path(session.transcript).parent.parent, req.get("sig_turn")
    try:
        if isinstance(turn, int) and not isinstance(turn, bool):
            if turn < 1:  # no measured latest turn: any record is an earlier spoken turn's, not what they last said
                return None
            rec = store.wait_turn(listener, session.id, turn, max(0.0, float(D.get("signals_wait_ms") or 0)) / 1000)
            rec = rec if rec is not None and rec["turn"] >= turn else None
        else:
            rec = store.latest(listener, session.id)
    except (OSError, ValueError):  # no records yet, or not a conversation's file: no note, never a broken look
        return None
    note = (rec or {}).get("note")
    return note.strip() if isinstance(note, str) and note.strip() else None


def signals_block(note: str | None) -> str:
    """The look's {{signals}}: the note in its own block between blank lines, or "" (the look as it was)."""
    return f"\n<listener_signals>\n{note}\n</listener_signals>\n" if note else ""


def ask(cfg: dict, session, req: dict, lock=None) -> dict:
    """The route's work. `req`: {"id", "trigger", "speaking", "silence_ms", "last_who", "recent", "page":
    {seconds, explained, started, turns}, "sig_turn"} from the page. `lock`: the conversation lock, held only to
    read and write the transcript, never during the call or the wait for the turn's signals."""
    D = settings(cfg)
    rid = req.get("id")
    page = req.get("page") or {}
    if not D.get("enabled"):
        return {"id": rid, **NO_WHISPER, "error": "off"}
    calls, usd = spent(session)
    cap = float(cfg["guide"].get("max_session_usd") or 0)
    if calls >= int(D["max_calls"]) or usd >= float(D["max_usd"]) or (cap and session.spent() >= cap):
        return {"id": rid, **NO_WHISPER, "error": "cap"}
    from . import session as live_session

    # Behind live.director.signals: read before the transcript and outside the lock, which the line post carrying
    # the turn's measurements needs in order to land while this waits for it.
    signals_on = bool(D.get("signals"))
    note = heard(session, req, D) if signals_on else None
    with lock if lock is not None else _Null():
        lines = merge_recent(lines_from_transcript(session.transcript), req.get("recent") or [],
                             int(page.get("seconds") or 0) or None)
        explained = bool(page.get("explained")) or live_session.explained(session.transcript)
        plan, last_read = memory(session.transcript)
    # A returning listener knows what it does (session.values_for gives that guide offer_known.md: "Never offer to
    # explain it again"), and that guide never says "audio chapters": the offer is done, the pitch is open.
    known = not str(session.values.get("identity", "")).startswith("(none")
    explained = explained or known
    started = bool(page.get("started"))
    profile = "\n".join(x for x in (str(session.values.get("identity") or ""), str(session.values.get("profile") or ""))
                        if x.strip())
    trigger = req.get("trigger") if req.get("trigger") in TRIGGERS else "cadence"
    speaking = req.get("speaking") if req.get("speaking") in SPEAKING else "none"
    user = look(D, lines=lines, trigger=trigger, speaking=speaking, last_who=req.get("last_who"),
                silence_secs=float(req.get("silence_ms") or 0) / 1000, seconds=int(page.get("seconds") or 0),
                explained=explained, started=started, profile=profile, plan=plan, last_read=last_read,
                signals=signals_block(note))
    fut = _POOL.submit(decide, cfg, session.log_run, user, {"session": session.id, "trigger": trigger})
    try:
        got = fut.result(timeout=float(D["timeout_ms"]) / 1000)
    except FutureTimeout:  # it goes on in its thread and logs its cost; the page has moved on
        got = {**NO_WHISPER, "error": "timeout", "ms": int(D["timeout_ms"]), "cost_usd": 0.0}
    if known and got.get("about") == "offer":  # never for a listener who knows (the page only sends what comes back)
        got = {**got, **NO_WHISPER}
    rec = {k: got.get(k) for k in FIELDS + ("ms", "cost_usd", "error") if k in got}
    # "heard": the note the look was given (null: none), only with the flag on, so rows are as they were with it
    # off. Never under "text" or "note", which the transcript's readers and the earpiece history key on.
    heard_row = {"heard": note} if signals_on else {}
    with lock if lock is not None else _Null():
        session.record("director", id=rid, trigger=trigger, speaking=speaking, **heard_row, **rec)
    out = {"id": rid, **{k: got.get(k) for k in FIELDS if k in got}, **(NO_WHISPER if got.get("error") else {}),
           "ms": got.get("ms"), "cost_usd": got.get("cost_usd", 0.0)}
    if got.get("error"):
        out["error"] = got["error"]
    return out


class _Null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
