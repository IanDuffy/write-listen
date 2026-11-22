"""The debrief's earpiece (docs/debrief-earpiece-design.md; Ian, 2026-11-22: "build in the earpiece workflow into
the post demo interview to help guide the flow and questions ... not connecting or acting like a real product
interview").

A sibling of voice/gpt_live/director.py (the guide's earpiece), sharing its plan model, its note cleaning
(director.shape, cap_words), its call (director.decide) and merge_recent; the guide's code paths are unchanged.
The debrief is two minutes and two to four interviewer turns, so the page looks while the tester is still answering
(`hearing`), as well as once at the start (`opened`) and at a turn's end nothing covered (`turn_end`): the note for
the next question is in place as they finish.

- `look(...)`: one look's user message (prompts/interviewer_director_moment.md): the debrief so far, the tester's words
  in progress, the interviewer's questions so far, the account of what they did and the visit brief
  (demo/debrief.visit_brief, also in the interviewer's own instructions), its own plan and read from its last row, its
  whispers so far.
- `ask(cfg, s, req, write, lock)`: the route's work (demo/interviewer.py POST /api/interview/direct): caps
  (demo.interviewer.director max_calls, max_usd), the call on a thread with timeout_ms (stage interviewer_director),
  its cost in study/log.jsonl (kind interviewer: the admin page counts it), and one "director" row in the debrief's
  transcript (study/debrief-<sid>.jsonl), `note` but never `text`, so the summary, covered() and the visit
  transcript never read it as speech.
- `whisper_in(text)`: a page note as the page delivered it (prompts/interviewer_notes.md `direct_debrief`) -> (kind,
  note), for the admin's Earpiece history.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

from stages.common import Run, load_prompt
from voice.gpt_live import director

STAGE = "interviewer_director"
KINDS = ["next_turn", "now"]
ABOUT = ["follow_up", "thread", "moment", "idea", "unanswered", "wrap"]
TRIGGERS = ["opened", "hearing", "turn_end"]
SCHEMA = {
    "type": "object",
    "properties": {
        "read": {"type": "string"},
        "plan": {"type": "array", "items": {
            "type": "object",
            "properties": {"item": {"type": "string"}, "state": {"type": "string", "enum": director.STATES}},
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
FIELDS = director.FIELDS
NO_WHISPER = director.NO_WHISPER
MAX_LINES = 40
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="debrief-earpiece")
TRIGGER_TEXT = {
    "opened": "the debrief has just begun: the interviewer is welcoming them back and asking its opening question",
    "hearing": "they are answering right now (their words so far are below): your note is for the interviewer's "
               "turn when they finish",
    "turn_end": "they have just finished a turn",
}


def settings(cfg: dict) -> dict:
    """demo.interviewer.director over the defaults."""
    return {"enabled": False, "hear_words": 8, "hear_every_ms": 5000, "turn_merge_ms": 1500, "timeout_ms": 5000,
            "max_calls": 20, "max_usd": 0.30,
            **(((cfg.get("demo") or {}).get("interviewer") or {}).get("director") or {})}


def page_settings(cfg: dict) -> dict:
    """What the page needs (interview.js): whether it is on, and when to look."""
    D = settings(cfg)
    return {k: D[k] for k in ("enabled", "hear_words", "hear_every_ms", "turn_merge_ms", "timeout_ms")}


def shape(got: dict) -> dict:
    out = director.shape(got, KINDS, ABOUT)
    if out.get("note"):  # a replay's note ended `"pretty interesting".'`: no stray quote marks around it
        out["note"] = _unquoted(out["note"])
    return out


_PAIRS = {"'": "'", "‘": "’", "“": "”", '"': '"'}


def _unquoted(n: str) -> str:
    """The note without a quote mark that wraps it whole or has no partner in it; their quoted words keep theirs."""
    n = n.strip()
    if len(n) > 1 and n[0] in _PAIRS and n[-1] == _PAIRS[n[0]] and n[0] not in n[1:-1]:
        n = n[1:-1].strip()
    while n and n[-1] in "'’”\"" and not any(o in n[:-1] for o, c in _PAIRS.items() if c == n[-1]):
        n = n[:-1].rstrip()
    while n and n[0] in "'‘“\"" and _PAIRS[n[0]] not in n[1:]:
        n = n[1:].lstrip()
    return n


def parse(text: str) -> dict:
    return shape(json.loads((text or "").strip()))


# --- what it reads -------------------------------------------------------------------------------------------

def _rows(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def lines_from(path: Path) -> list[dict]:
    """[{"secs", "who", "text"}] from a debrief transcript: who is interviewer, listener or app (a page note).
    Its "s" is seconds since the session started."""
    out = []
    for rec in _rows(path):
        who, text = rec.get("who"), str(rec.get("text") or "").strip()
        secs = int(rec["s"]) if isinstance(rec.get("s"), (int, float)) else None
        if who in ("interviewer", "listener") and text:
            out.append({"secs": secs, "who": who, "text": text})
        elif who == "page" and text:
            out.append({"secs": secs, "who": "app", "text": text})
    return out


def _wrapper() -> tuple[str, str]:
    from demo import interviewer
    before, _, after = interviewer.notes()["direct_debrief"].partition("{note}")
    return before, after


def whisper_in(text: str) -> tuple[str, str] | None:
    """("next_turn", note) when a page note is this earpiece's whisper as the page delivered it, else None."""
    before, after = _wrapper()
    m = re.match(r"^" + re.escape(before) + r"(.*?)" + re.escape(after) + r"$", (text or "").strip(), re.S)
    return ("next_turn", m.group(1).strip()) if m else None


def _stamp(s) -> str:
    return f"{s // 60}:{s % 60:02d}" if isinstance(s, int) and s >= 0 else "?"


def transcript_text(lines: list[dict]) -> str:
    """The debrief so far, timestamped; the app's notes in brackets, this earpiece's own whispers left out (they are
    listed apart)."""
    rows = []
    for x in lines[-MAX_LINES:]:
        stamp = f"[{_stamp(x.get('secs'))}] " if isinstance(x.get("secs"), int) else ""
        if x["who"] == "app":
            if whisper_in(x["text"]):
                continue
            rows.append(f"{stamp}app note to the interviewer: {x['text']}")
        else:
            rows.append(f"{stamp}{x['who']}: {x['text']}")
    return "\n".join(rows) or "(nothing said yet)"


def notes_text(lines: list[dict]) -> str:
    rows = [f"[{_stamp(x.get('secs'))}] {got[1]}" for x in lines if x["who"] == "app" and (got := whisper_in(x["text"]))]
    return "\n".join(rows) or "(none yet)"


def memory(path: Path) -> tuple[list[dict] | None, str]:
    """Its previous plan and read, from its newest rows that have them."""
    plan, read = None, "(first look)"
    for rec in _rows(path):
        if rec.get("who") == "director":
            plan = rec.get("plan") or plan
            read = rec.get("read") or read
    return plan, read


def questions_asked(lines: list[dict]) -> int:
    return sum(1 for x in lines if x["who"] == "interviewer" and "?" in x["text"])


def look(*, lines: list[dict], trigger: str, hearing: str, seconds: int, questions: int, time_up: bool,
         did: str, visit: str, plan: list[dict] | None, last_read: str) -> str:
    """One look's user message (prompts/interviewer_director_moment.md)."""
    trigger = trigger if trigger in TRIGGERS else "turn_end"
    return load_prompt("interviewer_director_moment", elapsed=_stamp(max(0, int(seconds))),
                       trigger=TRIGGER_TEXT[trigger],
                       hearing=" ".join(str(hearing or "").split()) or "(nothing in progress)",
                       questions=str(questions), time_up="yes" if time_up else "no",
                       did=did.strip() or "(no record)", visit=visit.strip() or "(nothing recorded)",
                       plan=director.plan_text(plan), last_read=last_read or "(first look)",
                       notes=notes_text(lines), transcript=transcript_text(lines))


# --- the route's work ----------------------------------------------------------------------------------------

def spent(study: Path, sid: str) -> tuple[int, float]:
    """This debrief's earpiece calls so far and what they cost (study/log.jsonl)."""
    recs = [r for r in Run(name="study", root=study).read_log()
            if r.get("session") == sid and r.get("stage") == STAGE and not r.get("error")]
    return len(recs), sum(r.get("cost_usd", 0) or 0 for r in recs)


def ask(cfg: dict, s: dict, req: dict, write, lock=None) -> dict:
    """The route's work for the debrief session `s` (demo/interviewer.py's dict: id, path, study, values).
    `req`: {"id", "trigger", "recent", "hearing", "page": {seconds, questions, time_up}}. `write(s, rec)` appends a
    row to the transcript. `lock`: held only to read and write the transcript, never during the call."""
    D = settings(cfg)
    rid = req.get("id")
    page = req.get("page") or {}
    if not D.get("enabled"):
        return {"id": rid, **NO_WHISPER, "error": "off"}
    calls, usd = spent(s["study"], s["id"])
    if calls >= int(D["max_calls"]) or usd >= float(D["max_usd"]):
        return {"id": rid, **NO_WHISPER, "error": "cap"}
    trigger = req.get("trigger") if req.get("trigger") in TRIGGERS else "turn_end"
    with lock if lock is not None else director._Null():
        # merge_recent speaks the guide's names and matches lines by (who, text): both sides as "guide" (a review
        # found the transcript's "interviewer" lines never matched the page's, so every look saw the debrief twice)
        server = [{**x, "who": "guide" if x["who"] == "interviewer" else x["who"]} for x in lines_from(s["path"])]
        lines = director.merge_recent(server,
                                      [{"who": {"interviewer": "guide"}.get(x.get("who"), x.get("who")), "text": x.get("text")}
                                       for x in (req.get("recent") or [])],
                                      int(page.get("seconds") or 0) or None)
        plan, last_read = memory(s["path"])
    lines = [{**x, "who": "interviewer" if x["who"] == "guide" else x["who"]} for x in lines]
    values = s.get("values") or {}
    user = look(lines=lines, trigger=trigger, hearing=str(req.get("hearing") or ""),
                seconds=int(page.get("seconds") or 0),
                questions=max(int(page.get("questions") or 0), questions_asked(lines)),
                time_up=bool(page.get("time_up")), did=str(values.get("did") or ""), visit=str(values.get("visit") or ""),
                plan=plan, last_read=last_read)
    log_run = Run(name="study", root=s["study"])
    fut = _POOL.submit(director.decide, cfg, log_run, user,
                       {"session": s["id"], "kind": "interviewer", "trigger": trigger},
                       stage=STAGE, system="interviewer_director", schema=SCHEMA, parse_fn=parse)
    try:
        got = fut.result(timeout=float(D["timeout_ms"]) / 1000)
    except FutureTimeout:  # it goes on in its thread and logs its cost; the page has moved on
        got = {**NO_WHISPER, "error": "timeout", "ms": int(D["timeout_ms"]), "cost_usd": 0.0}
    rec = {k: got.get(k) for k in FIELDS + ("ms", "cost_usd", "error") if k in got}
    with lock if lock is not None else director._Null():
        write(s, {"who": "director", "id": rid, "trigger": trigger,
                  "speaking": "listener" if str(req.get("hearing") or "").strip() else "none", **rec})
    out = {"id": rid, **{k: got.get(k) for k in FIELDS if k in got}, **(NO_WHISPER if got.get("error") else {}),
           "ms": got.get("ms"), "cost_usd": got.get("cost_usd", 0.0)}
    if got.get("error"):
        out["error"] = got["error"]
    return out


FATE_REASONS = {**director.FATE_REASONS, "moved_on": "the interviewer had already taken its turn"}
