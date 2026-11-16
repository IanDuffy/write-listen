"""The library timeline and listening notes (docs/library-timeline.md): moments, playback progress, and the
reader the two screens use (the chapter strip with its scrubber, and the chapter page).

    chapters/<id>/moments.jsonl   one line per moment in that chapter, in time order:
                                  {"id": "m_0001", "kind": question|mention|note, "position_s", "paragraph_id",
                                   "sentence_id", "text", "reply", "outcome": answered|saved|became_chapter|in_library,
                                   "outcome_ref", "session"}, plus "seed" (the seeds.jsonl id a saved moment became)
                                  and, for a mention, "title", "creator", "work_kind" (its library.jsonl item)
    chapters/<id>/playback.json   {"furthest_s", "duration_s", "finished", "last_played", "plays"}: the player
                                  reports every 10 seconds and on pause (POST /api/chapters/<run>/playback)

Both files are rewritten whole (atomic), moments.jsonl under a lock file: the planner (the scheduler's process)
and the server (an ask's capture, Change) both write it.

Outcomes. `became_chapter`'s outcome_ref is the plan id of the card that took the moment's seed (`plan-...`): the
chapter folder doesn't exist until the build locks the card. The reader resolves it: the card still up next is
"tonight's chapter"; a card a chapter was built from (its 00_source/up_next.json id) is that chapter, linked.
A card replaced before it was built puts its moments back to `saved` (and their seeds back into the planner's
pool: library.mark_unused).

The reader (state) reads only these files, each chapter's 00_source/up_next.json and 11_episode/episode.json, the
listener's up_next.json and library.jsonl (the first conversation), as the spec asks; guide.md for the guide's name.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from . import profiles

MOMENTS, PLAYBACK = "moments.jsonl", "playback.json"
KINDS = ("question", "mention", "note")
OUTCOMES = ("answered", "saved", "became_chapter", "in_library")
FINISHED_AT = 0.95  # playback past this share of the chapter counts as finished
TOPIC_COLORS = ("teal", "coral", "amber")  # in order of each main topic's first chapter
_LOCK = threading.Lock()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _norm(s: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))


@contextmanager
def _locked(root: Path):
    """One writer of a chapter's moments at a time, across threads and processes."""
    root.mkdir(parents=True, exist_ok=True)
    with _LOCK, open(root / ".moments.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


# --- moments ------------------------------------------------------------------------------

def moments(root: Path) -> list[dict]:
    """A chapter's moments in time order (then by id)."""
    rows = [r for r in profiles.read_jsonl(Path(root) / MOMENTS) if r.get("id") and r.get("kind") in KINDS]
    return sorted(rows, key=lambda r: (float(r.get("position_s") or 0), r["id"]))


def _write(root: Path, rows: list[dict]) -> None:
    p = Path(root) / MOMENTS
    tmp = p.with_name(f".{MOMENTS}.{os.getpid()}.tmp")
    rows = sorted(rows, key=lambda r: (float(r.get("position_s") or 0), r["id"]))
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(p)


def _next_id(rows: list[dict]) -> str:
    n = max((int(m.group(1)) for r in rows if (m := re.fullmatch(r"m_(\d+)", str(r.get("id"))))), default=0)
    return f"m_{n + 1:04d}"


def _same(a: dict, b: dict) -> bool:
    """The same moment written twice (a pass run again, a seed saved twice in one aside)."""
    return (a.get("session") == b.get("session") and a.get("kind") == b.get("kind")
            and _norm(a.get("text")) == _norm(b.get("text")))


def add_moments(root: Path, items: list[dict]) -> list[dict]:
    """Append moments (each {"kind", "position_s", "text", ...}); returns the ones that were new, with ids.
    A moment already there (same session, kind and words) is not added again."""
    root = Path(root)
    added = []
    with _locked(root):
        rows = moments(root)
        for it in items:
            if it.get("kind") not in KINDS or not (it.get("text") or "").strip():
                continue
            rec = {"id": None, "kind": it["kind"], "position_s": round(float(it.get("position_s") or 0), 1),
                   "paragraph_id": it.get("paragraph_id"), "sentence_id": it.get("sentence_id"),
                   "text": " ".join(it["text"].split()), "reply": (it.get("reply") or "").strip() or None,
                   "outcome": it.get("outcome") if it.get("outcome") in OUTCOMES else None,
                   "outcome_ref": it.get("outcome_ref"), "session": it.get("session")}
            for k in ("seed", "title", "creator", "work_kind", "aside", "t"):
                if it.get(k) is not None:
                    rec[k] = it[k]
            if any(_same(rec, r) for r in rows):
                continue
            rec["id"] = _next_id(rows)
            rows.append(rec)
            added.append(rec)
        if added:
            _write(root, rows)
    return added


def update_moments(root: Path, change) -> list[dict]:
    """Apply change(row) -> dict | None to each moment under the lock; rewrites the file if any changed.
    Returns the changed rows."""
    root = Path(root)
    if not (root / MOMENTS).exists():
        return []
    with _locked(root):
        rows, changed = moments(root), []
        for i, r in enumerate(rows):
            new = change(dict(r))
            if new is not None and new != r:
                rows[i] = new
                changed.append(new)
        if changed:
            _write(root, rows)
    return changed


# --- outcomes the planner sets ----------------------------------------------------------------

def _chapter_roots(listener: Path) -> list[Path]:
    return profiles.chapters(listener=listener)


def moments_for_seed(listener: Path, seed: dict) -> list[tuple[Path, dict]]:
    """The moment(s) a seed came from: those that name it (`seed`), else, for a seed an aside wrote by code
    (overnight/live.aside_seeds: same session), the question or note of that session whose words it shares most."""
    sid = seed.get("id")
    found = [(root, m) for root in _chapter_roots(listener) for m in moments(root) if sid and m.get("seed") == sid]
    if found:
        return found
    src = seed.get("source") or {}
    session, aside = src.get("session"), src.get("aside")  # after_chapter's open questions name the aside only
    if not session and not aside:
        return []
    cands = [(root, m) for root in _chapter_roots(listener) if not src.get("chapter") or root.name == src["chapter"]
             for m in moments(root) if m["kind"] in ("question", "note")
             and ((session and m.get("session") == session) or (aside and m.get("aside") == aside))]
    if len(cands) <= 1:
        return cands
    words = set(_norm(f"{seed.get('text')} {seed.get('words')}").split())
    best = max(cands, key=lambda c: len(words & set(_norm(c[1]["text"]).split())))
    return [best]


def became(listener: Path, seed: dict, plan_id: str) -> list[dict]:
    """The planner took this seed for its card: its moment(s) became that chapter."""
    out = []
    for root, m in moments_for_seed(listener, seed):
        out += update_moments(root, lambda r, m=m: {**r, "outcome": "became_chapter", "outcome_ref": plan_id}
                              if r["id"] == m["id"] else None)
    return out


def unbecame(listener: Path, plan_id: str) -> list[dict]:
    """The card `plan_id` was replaced before it was built: its moments go back to saved."""
    out = []
    for root in _chapter_roots(listener):
        out += update_moments(root, lambda r: {**r, "outcome": "saved", "outcome_ref": None}
                              if r.get("outcome") == "became_chapter" and r.get("outcome_ref") == plan_id else None)
    return out


# --- playback -----------------------------------------------------------------------------

def playback(root: Path) -> dict:
    p = profiles.read_json(Path(root) / PLAYBACK, {}) or {}
    return {"furthest_s": float(p.get("furthest_s") or 0), "duration_s": p.get("duration_s"),
            "finished": bool(p.get("finished")), "last_played": p.get("last_played"), "plays": int(p.get("plays") or 0)}


def record_playback(root: Path, t: float, duration: float | None = None, start: bool = False,
                    now: str | None = None) -> dict:
    """What the player reports: where it is (t) in a chapter of `duration` seconds; start=True once per play."""
    root = Path(root)
    with _LOCK:
        p = playback(root)
        dur = float(duration) if duration and duration > 0 else (float(p["duration_s"]) if p["duration_s"] else None)
        t = max(0.0, float(t or 0))
        if dur:
            t = min(t, dur)
        furthest = max(p["furthest_s"], t)
        out = {"furthest_s": round(furthest, 1), "duration_s": round(dur, 2) if dur else None,
               "finished": p["finished"] or bool(dur and furthest >= FINISHED_AT * dur),
               "last_played": now or _now(), "plays": p["plays"] + (1 if start else 0)}
        profiles.write_json(root / PLAYBACK, out)
    return out


# --- the reader the screens use -------------------------------------------------------------

def in_library(root: Path) -> bool:
    """A chapter the library lists: a finished one (its audio) or one marked finished without audio (the demo)."""
    return profiles.finished(root) or (root / "finished.json").exists()


def _day(root: Path, card: dict) -> str | None:
    if re.match(r"\d{4}-\d{2}-\d{2}-", root.name):
        return root.name[:10]
    t = profiles.finished_at(root)
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d") if t else None


def _duration(card: dict, ep: dict, timing: dict, pb: dict | None = None) -> float | None:
    """The chapter's length: its episode's (the audio), else what the player measured, else its card's minutes."""
    secs = ep.get("duration") or timing.get("duration") or (pb or {}).get("duration_s")
    if not secs and card.get("minutes"):  # a chapter without audio (the demo's filler) says its length on its card
        secs = float(card["minutes"]) * 60
    return float(secs) if secs else None


def counts_line(ms: list[dict]) -> str:
    n = {k: sum(1 for m in ms if m["kind"] == k) for k in KINDS}
    words = {"question": ("question", "questions"), "mention": ("mention", "mentions"), "note": ("note", "notes")}
    return " · ".join(f"{n[k]} {words[k][n[k] != 1]}" for k in KINDS if n[k])


def status_of(pb: dict, duration: float | None) -> tuple[str, str]:
    """(finished|partial|not_started, the card's status line: Finished, "12 of 21 min", Not started)."""
    if pb["finished"]:
        return "finished", "Finished"
    if pb["furthest_s"] >= 30 or (pb["furthest_s"] > 0 and pb["plays"]):
        heard = max(1, int(pb["furthest_s"] // 60))
        total = round(duration / 60) if duration else None
        return "partial", (f"{heard} of {total} min" if total else f"{heard} min heard")
    return "not_started", "Not started"


def _first_conversation(listener: Path) -> dict | None:
    convs = [w for w in profiles.read_jsonl(listener / "library.jsonl")
             if w.get("kind") == "conversation" and (w.get("date") or w.get("t"))]
    if not convs:
        return None
    first = min(convs, key=lambda w: str(w.get("date") or w.get("t"))[:10])
    return {"date": str(first.get("date") or first.get("t"))[:10], "title": first.get("title")}


def _short(title: str) -> str:
    """A card title before its colon: "Borrowed Scenery: Why Japanese Gardens Hide the View" -> "Borrowed Scenery"."""
    head = (title or "").split(":")[0].strip()
    return head or title


def state(listener: Path, card: dict | None, card_state: str, latest: str | None, guide_name: str | None,
          today: date | None = None) -> dict:
    """The library screen's and chapter page's data: every chapter oldest first (moments, playback, status),
    the first conversation, and where Tonight sits. `card` is the listener's up_next.json when it is still to come
    (planned, building, failed), else None; `latest` the playable chapter's folder name."""
    today = today or date.today()
    roots = sorted((p for p in profiles.chapters(listener=listener) if in_library(p)),  # by date, then finish time:
                   key=lambda p: (_day(p, {}) or "", profiles.finished_at(p), p.name))  # colours by first appearance
    built_from: dict[str, Path] = {}
    for p in profiles.chapters(listener=listener):
        cid = (profiles.read_json(p / "00_source" / "up_next.json", {}) or {}).get("id")
        if cid:
            built_from[cid] = p
    titles: dict[str, str] = {}
    rows, topics = [], []
    for root in roots:
        c = profiles.read_json(root / "00_source" / "up_next.json", {}) or {}
        ep = profiles.read_json(root / "11_episode" / "episode.json", {}) or {}
        timing = profiles.read_json(root / "11_episode" / "timing.json", {}) or {}
        title = c.get("title") or ep.get("title") or root.name
        titles[root.name] = title
        pb = playback(root)
        dur = _duration(c, ep, timing, pb)
        topic = ((c.get("topics") or [None])[0] or title).strip()
        if _norm(topic) not in topics:
            topics.append(_norm(topic))
        st, line = status_of(pb, dur)
        rows.append({"run": root.name, "title": title, "overview": c.get("overview") or ep.get("angle") or "",
                     "summary": c.get("summary") or c.get("overview") or "", "date": _day(root, c),
                     "duration_s": round(dur, 2) if dur else None, "minutes": round(dur / 60) if dur else None,
                     "topic": topic, "color": TOPIC_COLORS[topics.index(_norm(topic)) % len(TOPIC_COLORS)],
                     "playable": root.name == latest and profiles.finished(root),
                     "audio": profiles.finished(root), "episode": (root / "11_episode" / "episode.json").exists(),
                     "demo": bool(c.get("demo") or (profiles.read_json(root / "finished.json", {}) or {}).get("demo")),
                     "playback": pb, "status": st, "status_line": line, "moments": moments(root)})
    # roots are already in time order (a day's chapters by when they finished): the strip keeps it
    tonight_id = (card or {}).get("id")
    for r in rows:
        for m in r["moments"]:
            m.update(_outcome(m, tonight_id, built_from, titles))
        r["counts"] = counts_line(r["moments"])
    tonight = today
    if card and card.get("for_morning"):
        try:
            tonight = date.fromisoformat(card["for_morning"]) - timedelta(days=1)
        except ValueError:
            pass
    last = max((r["date"] for r in rows if r["date"]), default=None)
    if last and tonight.isoformat() <= last:  # Tonight is always after the last chapter
        tonight = date.fromisoformat(last) + timedelta(days=1)
    return {"chapters": rows, "latest": latest, "first": _first_conversation(listener),
            "tonight": tonight.isoformat(), "guide": guide_name, "up_next_state": card_state,
            "up_next_short": _short(card.get("title", "")) if card else None}


def _outcome(m: dict, tonight_id: str | None, built_from: dict[str, Path], titles: dict[str, str]) -> dict:
    """How a moment's outcome reads: {"outcome_label", "outcome_run" (a chapter it links to), "tonight"}."""
    o, ref = m.get("outcome"), m.get("outcome_ref")
    if o == "became_chapter":
        if ref and ref == tonight_id:
            return {"outcome_label": "Became tonight's chapter", "outcome_run": None, "tonight": True}
        root = built_from.get(ref or "")
        run = root.name if root is not None else (ref if ref in titles else None)  # a ref that names the folder
        if run and run in titles:
            return {"outcome_label": titles[run], "outcome_run": run, "tonight": False}
        return {"outcome_label": "Became a chapter", "outcome_run": None, "tonight": False}
    label = {"answered": "Answered", "saved": "Saved for later", "in_library": "In your library"}.get(o or "", "")
    return {"outcome_label": label, "outcome_run": None, "tonight": False}
