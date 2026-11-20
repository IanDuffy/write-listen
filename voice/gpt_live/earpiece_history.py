"""The earpiece's history, for the admin page (Ian, 2026-11-20: "retain a history with timestamps that the notes are
passed to the narrator as well to understand how it affects the conversation").

Read from a listener's conversation transcripts (<listener>/sessions/<id>.jsonl), nothing else, no model:

- `conversations(folder)`: one summary per conversation, newest first.
- `timeline(folder, session)`: one conversation as items in file order: the guide's and the listener's lines, the
  page's notes (a whisper as delivered is marked, with the look whose note it was), tools, new GPT-Live sessions,
  and each look by the earpiece (voice/gpt_live/director.py "director" rows) with its plan's changes, its note's
  fate on the page ("director_fate" rows, POST /api/live/direct/fate), when the note reached the guide and the
  guide's next line after it.
- `plan_changes(prev, plan)`: the plan change rule the page's panel (page/earpiece.js) follows too: items matched
  by text (lowercased, whitespace collapsed); "new" for an item not there before, the new state for one whose state
  changed, null otherwise; items gone are `removed`.

Rows are written when a line ends and "t" has one-second resolution, so a reply's time is the end of the guide's
line. A look's row is written after its answer: it was asked about `ms` earlier (`asked_secs`).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from . import director

SESSION_ID = re.compile(r"\d{8}-\d{6}(-\d+)?")


def _key(item: str) -> str:
    return " ".join(str(item or "").lower().split())


def plan_changes(prev: list[dict] | None, plan: list[dict] | None) -> tuple[list[dict], list[str]]:
    """(plan with "change" on each item, removed items). `prev` None: the first look, nothing marked."""
    plan = [p for p in plan or [] if isinstance(p, dict)]
    if prev is None:
        return [{"item": p.get("item"), "state": p.get("state"), "change": None} for p in plan], []
    before = {_key(p.get("item")): p.get("state") for p in prev if isinstance(p, dict)}
    out = []
    for p in plan:
        k = _key(p.get("item"))
        change = "new" if k not in before else (p.get("state") if before[k] != p.get("state") else None)
        out.append({"item": p.get("item"), "state": p.get("state"), "change": change})
    now = {_key(p.get("item")) for p in plan}
    removed = [p.get("item") for p in prev if isinstance(p, dict) and _key(p.get("item")) not in now]
    return out, removed


def _rows(path: Path) -> list[dict]:
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _time(rec: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(rec.get("t")))
    except ValueError:
        return None


def _files(folder: Path) -> list[Path]:
    d = folder / "sessions"
    if not d.is_dir():
        return []
    return [p for p in d.glob("*.jsonl") if SESSION_ID.fullmatch(p.stem)]


def _speech(rec: dict) -> bool:
    return rec.get("who") in ("guide", "listener") and bool(str(rec.get("text") or "").strip())


def conversations(folder: Path) -> list[dict]:
    """Every conversation in this listener's sessions/, newest first, with its earpiece's counts (as the timeline
    counts them, so a note sent in an older conversation with no fate rows counts as sent)."""
    out = []
    for p in _files(folder):
        got = timeline(folder, p.stem)
        times = [t for t in (_time(r) for r in _rows(p)) if t]
        D = got["director"]
        out.append({"session": p.stem, "started": got["started"], "ended": got["ended"],
                    "minutes": round((times[-1] - times[0]).total_seconds() / 60, 1) if times else 0.0,
                    "lines": sum(1 for x in got["items"] if x["kind"] == "line"),
                    **{k: D[k] for k in ("looks", "notes", "sent", "dropped", "errors", "cost_usd")}})
    return sorted(out, key=lambda c: (c["started"] or "", c["session"]), reverse=True)


def timeline(folder: Path, session: str) -> dict:
    """One conversation's timeline (see the module's notes). Raises FileNotFoundError for an id that isn't a
    conversation's or a missing file."""
    if not SESSION_ID.fullmatch(session or ""):
        raise FileNotFoundError(session)
    path = folder / "sessions" / f"{session}.jsonl"
    if not path.is_file():
        raise FileNotFoundError(session)
    rows = _rows(path)
    t0 = next((t for t in (_time(r) for r in rows) if t), None)

    def stamp(rec: dict) -> dict:
        t = _time(rec)
        return {"t": t.isoformat() if t else None, "clock": t.strftime("%H:%M:%S") if t else None,
                "secs": int((t - t0).total_seconds()) if t and t0 else 0}

    items: list[dict] = []
    looks: dict = {}          # look id -> its item (the latest look with that id)
    prev_plan = None
    fate_rows: dict = {}      # look id -> its fate rows in order
    for rec in rows:
        who = rec.get("who")
        text = str(rec.get("text") or "").strip()
        if who in ("guide", "listener") and text:
            items.append({**stamp(rec), "kind": "line", "who": who, "text": text})
        elif who == "page" and text:
            got = director.whisper_in(text)
            look_id = None
            if got:
                look_id = next((x["id"] for x in reversed(items) if x["kind"] == "look" and x.get("note")
                                and x["note"].strip() == got[1]), None)
            items.append({**stamp(rec), "kind": "note", "text": text, "whisper": bool(got), "look": look_id})
        elif who == "tool" and rec.get("name"):
            items.append({**stamp(rec), "kind": "tool", "name": str(rec["name"])})
        elif who == "session" and rec.get("live_id"):  # a GPT-Live session (the conversation's own start row has none)
            items.append({**stamp(rec), "kind": "session", "live_id": rec.get("live_id")})
        elif who == "director":
            st = stamp(rec)
            plan = rec.get("plan") if isinstance(rec.get("plan"), list) else None
            if plan:
                marked, removed = plan_changes(prev_plan, plan)
                prev_plan = plan
            else:
                marked, removed = [], []
            ms = rec.get("ms") if isinstance(rec.get("ms"), (int, float)) else 0
            item = {**st, "kind": "look", "id": rec.get("id"), "trigger": rec.get("trigger"),
                    "speaking": rec.get("speaking"), "ms": rec.get("ms"),
                    "asked_secs": st["secs"] - round(ms / 1000), "cost_usd": rec.get("cost_usd"),
                    "error": rec.get("error") or None, "read": rec.get("read"), "plan": marked, "removed": removed,
                    "next": rec.get("next"), "changed": rec.get("changed"), "note": rec.get("note"),
                    "note_kind": rec.get("kind"), "about": rec.get("about"),
                    "fate": None, "delivered": None, "reply": None}
            items.append(item)
            looks[rec.get("id")] = item
        elif who == "director_fate" and rec.get("id") is not None:
            fate_rows.setdefault(rec["id"], []).append({**rec, **stamp(rec)})

    # When each look's note reached the guide (its whisper as the page delivered it), and the guide's next line.
    for i, x in enumerate(items):
        if x["kind"] != "note" or not x["whisper"] or x["look"] is None:
            continue
        look = looks.get(x["look"])
        if look is None or look["delivered"] is not None:
            continue
        look["delivered"] = {k: x[k] for k in ("t", "clock", "secs")}
        reply = next((y for y in items[i + 1:] if y["kind"] == "line" and y["who"] == "guide"), None)
        if reply:
            look["reply"] = {**{k: reply[k] for k in ("t", "clock", "secs")},
                             "after_secs": reply["secs"] - x["secs"], "text": reply["text"]}

    held_total = 0
    for look in looks.values():
        got = fate_rows.get(look["id"])
        if got:
            final = got[-1]
            held = next((r for r in got if r.get("fate") == "held"), None)
            held_secs = None
            if final.get("fate") != "held":
                if isinstance(final.get("held_ms"), (int, float)):
                    held_secs = round(final["held_ms"] / 1000, 1)
                elif held is not None:
                    held_secs = float(final["secs"] - held["secs"])
            if held is not None:
                held_total += 1
            look["fate"] = {"fate": final.get("fate"), "reason": final.get("reason"), "detail": final.get("detail"),
                            "held_secs": held_secs, "t": final["t"], "clock": final["clock"],
                            "secs": final["secs"], "inferred": False}
        elif look["delivered"] is not None:  # an older conversation: no fate rows, but the whisper went
            look["fate"] = {"fate": "sent", "reason": None, "detail": None, "held_secs": None,
                            **look["delivered"], "inferred": True}

    look_items = [x for x in items if x["kind"] == "look"]
    fates = [x["fate"] for x in look_items if x["fate"]]
    times = [t for t in (_time(r) for r in rows) if t]
    return {"lid": folder.name, "session": session,
            "started": times[0].isoformat() if times else None,
            "ended": times[-1].isoformat() if times else None,
            "director": {"looks": len(look_items), "notes": sum(1 for x in look_items if x.get("note")),
                         "sent": sum(1 for f in fates if f["fate"] == "sent"),
                         "dropped": sum(1 for f in fates if f["fate"] == "dropped"),
                         "held": held_total,
                         "errors": sum(1 for x in look_items if x["error"]),
                         "cost_usd": round(sum(float(x.get("cost_usd") or 0) for x in look_items), 4)},
            "items": items}
