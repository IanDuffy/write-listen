"""Where each tester has got to, for the admin page and the study report (docs/tester-demo.md, "Admin page").

tester_state(cfg, lid) -> {stage, left_early, last_active, title, minutes_heard, chapter_minutes, questions,
                           debrief: {done, summary, transcript, transcripts}, cost: {claude, gpt_live, total,
                           estimated}}

Everything is read from the tester's folder with explicit paths, never through the active listener, so it
is right whoever's request (or none) is in scope.

- stage: not opened (no opened_at) / intro (no intro_done, written by the interviewer's handoff) /
  onboarding (no chapter folder yet) / chapter / debrief (a debrief transcript exists) / done (debrief.json
  says done). left_early: not done, and nothing has happened for LEFT_AFTER_MINUTES.
- minutes_heard: the newest chapter's live/reading.jsonl, the best reading of each paragraph (most words
  heard), at live.page.words_per_second.
- questions: that chapter's asides of kind ask in which the listener said something.
- all_states adds the tester's record, with debrief_card (auto/on/off): whether a tester who comes back with
  the debrief not done sees the End demo button (demo/testers.py), and chapter_kind (real/rehearsal): what
  their chapters are, tester.json's chapter else config demo.chapter ("chapter" is the newest chapter's folder),
  and earpiece_view: their page shows the earpiece panel (the admin's toggle).
- rehearsal: the newest chapter is a rehearsal (fast.json, faststart/rehearsal.py), and rehearsal_source the
  chapter it replays. Its cost is $0: a rehearsal logs none.
- The tester's button presses (study/events.jsonl, POST /api/demo/event) count as activity for last_active,
  like every study/*.jsonl; the debrief's account of what they did is demo/debrief.activity.
- cost: sessions/log.jsonl, study/log.jsonl, chapters/*/log.jsonl (minus the early scout's lines copied in
  when a run adopts it: they are counted in chapters/_scouts/) and chapters/_scouts/*/log.jsonl; provider
  openai is GPT-Live, anthropic (or none) Claude. A GPT-Live session seen in a transcript or the reading log
  with no usage record (the page closed before it reported) is estimated from the span of its lines;
  `estimated` is the part of gpt_live that is such an estimate.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from faststart import rehearsal as rehearsal_mod
from overnight import profiles

from . import testers

STAGES = ("not opened", "intro", "onboarding", "chapter", "debrief", "done")
LEFT_AFTER_MINUTES = 30
ESTIMATE_FLOOR_SECONDS = 30  # a session that left one line still connected, greeted and was heard


def read_jsonl(p: Path) -> list[dict]:
    return profiles.read_jsonl(p)


def chapters(d: Path) -> list[Path]:
    return profiles.chapters(listener=d) if d.exists() else []


def newest_chapter(d: Path) -> Path | None:
    """The chapter begun last (status.json's started, else the folder's time)."""
    def key(p: Path):
        st = profiles.read_json(p / "status.json") or {}
        return (str(st.get("started") or ""), p.stat().st_mtime)
    found = chapters(d)
    return max(found, key=key) if found else None


def chapter_title(root: Path | None) -> str | None:
    if root is None:
        return None
    for p in (root / "status.json", root / "05_outline.json", root / "fast" / "card.json"):
        got = profiles.read_json(p)
        if isinstance(got, dict) and got.get("title"):
            return str(got["title"])
    return None


def words(text: str) -> int:
    return len(str(text or "").split())


def minutes_heard(cfg: dict, root: Path | None) -> float:
    if root is None:
        return 0.0
    best: dict[str, int] = {}
    for rec in read_jsonl(root / "live" / "reading.jsonl"):
        pid = rec.get("id")
        if pid:
            best[pid] = max(best.get(pid, 0), words(rec.get("heard")))
    wps = float(((cfg.get("live") or {}).get("page") or {}).get("words_per_second") or 2.5)
    return round(sum(best.values()) / wps / 60, 1)


def asks(root: Path | None) -> list[dict]:
    """The chapter's questions: asides of kind ask with something the listener said."""
    if root is None:
        return []
    out = []
    for a in read_jsonl(root / "12_asks" / "asides.jsonl"):
        said = [x.get("text", "").strip() for x in a.get("transcript") or []
                if x.get("who") == "listener" and str(x.get("text") or "").strip()]
        if a.get("kind") == "ask" and said:
            out.append({**a, "said": said})
    return out


def _activity_files(d: Path) -> list[Path]:
    """What the tester's own actions write (not tester.json: the admin's actions write that too, and not
    the chapter's build files, which go on changing after they leave)."""
    files = list(d.glob("sessions/*.jsonl")) + list(d.glob("study/*.jsonl"))
    for ch in chapters(d):
        files += [ch / "live" / "reading.jsonl", ch / "12_asks" / "asides.jsonl", ch / "fast" / "player.json"]
    return [p for p in files if p.name != "log.jsonl" and p.exists()]


def _ts(s) -> float | None:
    try:
        return datetime.fromisoformat(str(s)).timestamp()
    except (TypeError, ValueError):
        return None


def last_active(d: Path, rec: dict) -> float | None:
    times = [p.stat().st_mtime for p in _activity_files(d)]
    times += [t for t in (_ts(rec.get("opened_at")), _ts(rec.get("consent_at"))) if t]
    return max(times) if times else None


def stage_of(d: Path, rec: dict, chapter: Path | None, debrief: dict) -> str:
    if not d.exists() or not rec.get("opened_at"):
        return "not opened"
    if debrief.get("done"):
        return "done"
    if list(d.glob("study/debrief-*.jsonl")):
        return "debrief"
    if chapter is not None:
        return "chapter"
    return "onboarding" if rec.get("intro_done") else "intro"


def _iso(t: float | None) -> str | None:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)) if t else None


# --- cost ------------------------------------------------------------------------------

def _logs(d: Path) -> list[dict]:
    recs = read_jsonl(d / "sessions" / "log.jsonl") + read_jsonl(d / "study" / "log.jsonl")
    for ch in chapters(d):
        recs += [r for r in read_jsonl(ch / "log.jsonl") if not r.get("early_scout")]
    for p in sorted((d / "chapters" / "_scouts").glob("*/log.jsonl")):
        recs += read_jsonl(p)
    return recs


def _sessions_seen(d: Path) -> dict[str, float]:
    """GPT-Live session ids found outside the cost logs, with an estimate of how long each was connected:
    the wider of its lines' wall-clock span and its readings' session-relative end."""
    wall: dict[str, list[float]] = {}
    read_to: dict[str, float] = {}
    lines = []
    for p in list(d.glob("sessions/*.jsonl")) + list(d.glob("study/*.jsonl")):
        if p.name != "log.jsonl":
            lines += read_jsonl(p)
    for ch in chapters(d):
        lines += read_jsonl(ch / "12_asks" / "asides.jsonl")
        for rec in read_jsonl(ch / "live" / "reading.jsonl"):
            if rec.get("live_id"):
                end = ((rec.get("times") or {}).get("end_ms") or 0) / 1000
                read_to[rec["live_id"]] = max(read_to.get(rec["live_id"], 0.0), end)
    for rec in lines:
        if rec.get("live_id"):
            t = _ts(rec.get("t") or rec.get("started"))
            wall.setdefault(rec["live_id"], []).extend([t] if t else [])
    out = {}
    for lid in set(wall) | set(read_to):
        ts = wall.get(lid) or []
        span = max(ts) - min(ts) if ts else 0.0
        out[lid] = max(span, read_to.get(lid, 0.0), ESTIMATE_FLOOR_SECONDS)
    return out


def cost(cfg: dict, d: Path) -> dict:
    claude = gpt = 0.0
    reported = set()
    for r in _logs(d):
        usd = float(r.get("cost_usd") or 0)
        prov = r.get("provider") or "anthropic"
        if prov == "openai":
            gpt += usd
            if r.get("live_id"):
                reported.add(r["live_id"])
        elif prov == "anthropic":
            claude += usd
    per_min = float((cfg.get("live") or {}).get("price_per_minute") or 0.05)
    est = sum(s / 60 * per_min for lid, s in _sessions_seen(d).items() if lid not in reported)
    gpt += est
    return {"claude": round(claude, 4), "gpt_live": round(gpt, 4), "total": round(claude + gpt, 4),
            "estimated": round(est, 4)}


# --- the whole picture ---------------------------------------------------------------------

def tester_state(cfg: dict, lid: str) -> dict:
    d = testers.folder(lid)
    rec = testers.record(lid) or {}
    ch = newest_chapter(d)
    debrief = profiles.read_json(d / "study" / "debrief.json") or {}
    stage = stage_of(d, rec, ch, debrief)
    last = last_active(d, rec)
    left = stage not in ("not opened", "done") and bool(last) and time.time() - last > LEFT_AFTER_MINUTES * 60
    transcripts = sorted(p.name for p in d.glob("study/debrief-*.jsonl"))
    settings = profiles.settings(cfg, listener=d) if d.exists() else {}
    minutes = (settings.get("chapter") or {}).get("minutes") or (cfg.get("demo") or {}).get("chapter_minutes", 10)
    return {
        "stage": stage,
        "left_early": left,
        "last_active": _iso(last),
        "chapter": ch.name if ch else None,
        "title": chapter_title(ch),
        "rehearsal": bool(marker := (rehearsal_mod.marked(ch) if ch else None)),
        "rehearsal_source": (marker or {}).get("source_run"),
        "minutes_heard": minutes_heard(cfg, ch),
        "chapter_minutes": minutes,
        "questions": len(asks(ch)),
        "debrief": {"done": bool(debrief.get("done")),
                    "summary": "debrief_summary.md" if (d / "study" / "debrief_summary.md").exists() else None,
                    "transcript": transcripts[-1] if transcripts else None,
                    "transcripts": transcripts},
        "cost": cost(cfg, d) if d.exists() else {"claude": 0.0, "gpt_live": 0.0, "total": 0.0, "estimated": 0.0},
    }


def all_states(cfg: dict) -> list[dict]:
    """Every tester's record and state, oldest first, as the admin page and the report read them."""
    out = []
    for rec in testers.all_testers():
        out.append({"lid": rec["lid"], "label": rec.get("label"), "token": rec.get("token"),
                    "name": rec.get("name"), "created": rec.get("created"), "opened_at": rec.get("opened_at"),
                    "consent_at": rec.get("consent_at"), "debrief_card": rec.get("debrief_card") or "auto",
                    "chapter_kind": testers.chapter_of(cfg, rec), "earpiece_view": testers.earpiece_view(rec),
                    "revoked": not rec.get("token"), **tester_state(cfg, rec["lid"])})
    return out


def totals(states: list[dict]) -> dict:
    keys = ("claude", "gpt_live", "total", "estimated")
    return {k: round(sum(s["cost"][k] for s in states), 4) for k in keys}
