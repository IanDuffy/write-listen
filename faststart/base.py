"""What every part of a fast run shares: file names, settings, atomic writes, the timeline.

A fast run is a seed run (00_source/seed.md) with fast.json beside it. It keeps the
overnight layout wherever a file has an equivalent (docs/fast-start.md, step 6), so the
episode page and ask-by-voice read it unchanged:

    fast.json                   marks the run as fast; when the seed was set
    01_scout.md                 the scout: numbered entries and two or three scenes
    03_angle.md                 "# Angle: <title>", from the outline
    04_research/L00.md          the scout's entries in row format (they become D01...)
    04_research/Lnn.md          one research agent per ledger row
    04_dossier.md               append-only: an entry never changes its D number
    05_outline.json, .md        the outline (the JSON is the source of truth)
    05_brief.md                 the outline in the brief's shape (titles, Dossier: lines)
    06_segments/segNN.md        a section's locked text; drafts/ holds every draft
    listener_notes.md           what the listener said after the topic was set
    fast_timeline.jsonl         one line per event
    fast/                       working files: player.json (position reports), locks, state
    10_audio/secNN/             a section's pieces as they are narrated
    09_script_final.md, 10_audio/chapter.mp3, 11_episode/   written by the archive step
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from stages.common import PATHS, Run

FILES = {
    "mode": "fast.json",
    "scout": "01_scout.md",
    "outline": "05_outline.md",
    "outline_json": "05_outline.json",
    "notes": "listener_notes.md",
    "timeline": "fast_timeline.jsonl",
    "work": "fast",
    "player": "fast/player.json",
    "state": "fast/state.json",
    "locks": "fast/locks",
    "drafts": "06_segments/drafts",
    "section_timings": "11_episode/sections",
}

SCOUT_ROW = "L00"  # the scout's entries are filed as this row, so they get D numbers and the quote check

LEDGER_LOCK = threading.Lock()  # ledger.json is written by research threads and by locks
_TIMELINE_LOCK = threading.Lock()


def path(r: Run, key: str) -> Path:
    return r.root / FILES[key]


def section_dir(r: Run, n: int) -> Path:
    return r.path("audio_dir") / f"sec{n:02d}"


def settings(cfg: dict) -> dict:
    """config.yaml's `fast:` section."""
    return cfg["fast"]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_atomic(p: Path, text: str) -> Path:
    """Write a file that another thread or the server may be reading. Rolling files (drafts,
    the dossier, status) go through this, not Run.write, which refuses to overwrite."""
    p.parent.mkdir(parents=True, exist_ok=True)
    if text and not text.endswith("\n"):
        text += "\n"
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)
    return p


def write_json(p: Path, obj) -> Path:
    return write_atomic(p, json.dumps(obj, ensure_ascii=False, indent=1))


def read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


# --- the mode marker --------------------------------------------------------------------

def is_fast(r: Run) -> bool:
    return path(r, "mode").exists()


def mark_fast(r: Run, clock=time.time) -> dict:
    """Mark the run as fast and record when the seed was set (the timeline's zero). Kept on a
    re-run, so a continued run's times still count from the first start."""
    info = read_json(path(r, "mode"))
    if not info:
        info = {"mode": "fast", "seed_set": clock(), "seed_set_at": now_iso()}
        write_json(path(r, "mode"), info)
    return info


def continues(root: Path, mode: str) -> bool:
    """Whether an unfinished run folder a seed lookup found is this mode's to continue (cli.py's
    `new --seed`, and the server's start_seed). A run started fast (fast.json) is continued only
    in fast mode, and one the overnight pipeline has worked in (a status, a log or a map, and no
    fast.json) only in overnight mode; a folder that holds just its seed belongs to neither yet.
    (A run named outright is not asked: a run marked fast continues fast, which is how the
    server's Try again continues one.)"""
    root = Path(root)
    if (root / FILES["mode"]).exists():
        return mode == "fast"
    return mode != "fast" or not any((root / PATHS[k]).exists() for k in ("status", "log", "map"))


# --- the timeline -----------------------------------------------------------------------

class Timeline:
    """fast_timeline.jsonl: a timestamped line for every event. `t` is seconds since the
    seed was set. The clock is injectable so the scheduler simulation can use made-up time."""

    def __init__(self, r: Run, clock=time.time):
        self.r, self.clock = r, clock
        self.zero = mark_fast(r, clock)["seed_set"]

    def now(self) -> float:
        return round(self.clock() - self.zero, 2)

    def event(self, name: str, **fields) -> dict:
        rec = {"t": self.now(), "ts": now_iso(), "event": name, **fields}
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with _TIMELINE_LOCK:
            p = path(self.r, "timeline")
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
        return rec


def read_timeline(r: Run) -> list[dict]:
    p = path(r, "timeline")
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# --- sizes ------------------------------------------------------------------------------

def minutes(words: int, cfg: dict) -> float:
    return words / settings(cfg)["words_per_minute"]


def agent_cfg(cfg: dict) -> dict:
    """A copy of the config in which every research agent gets the fast budgets (5 searches and
    2 fetches a row) and the scout gets its own (6 and 3). stages/research.py and stages/scout.py
    read their budgets from the cfg they are handed, so they need no change."""
    f = settings(cfg)
    c = copy.deepcopy(cfg)
    per = dict(f["research"]["per_row"])
    c["research"]["budgets"] = {k: dict(per) for k in ("top", "rest", "covered", "followup")}
    c["research"]["max_searches"] = f["research"]["max_searches"]
    c["research"]["parallel_agents"] = f["research"]["parallel"]
    c["scout"] = dict(f["scout"])
    return c
