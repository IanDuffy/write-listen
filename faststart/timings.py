"""The fast run's clock: the names of the timeline's events, and the medians of earlier runs.

fast_timeline.jsonl (base.Timeline) holds one line per event; `t` is seconds since the seed was
set. The names below are the contract between the driver (faststart/run.py), which writes them,
and everything that reads them: this module's medians, the report (python -m faststart report)
and milestone 5's page. A step's own length is in its `seconds` field, measured where the step
ran, so a run that was stopped and continued still gives true step lengths.

The medians (config.yaml fast.timings_file, by default faststart/timings.json) are what the
guide's time estimate and the scheduler's eta() are made from. The file is local state: it is
gitignored, and each machine's first runs have no estimate (the guide then gives no time, and
every wait becomes a check-in).

    {"updated": "...", "medians": {"scout": 71.2, "outline": 80.4, "opening": 55.0, ...},
     "runs": [{"run": "roman-concrete-20261113", "at": "...", "steps": {"scout": [71.2], ...}}]}
"""

from __future__ import annotations

from pathlib import Path
from statistics import median

from stages.common import ROOT, Run

from . import base
from .scheduler import MEDIANS

# --- the timeline's events ----------------------------------------------------------------
SEED_SET = "seed_set"                # t = 0: the run was first started
CONTINUED = "continued"              # the same run started again (after a stop or a kill)
SCOUT_DONE = "scout_done"            # seconds, entries, adopted (an early scout was reused)
OUTLINE_DONE = "outline_done"        # seconds, sections, rows, title
OPENING_START = "opening_start"
OPENING_DONE = "opening_done"        # seconds (written and edited: section 1 is locked), words
RESEARCH_START = "research_start"    # row, section
RESEARCH_DONE = "research_done"      # row, section, seconds, entries, thin, error, searches, paid
RESEARCH_TIMEOUT = "research_timeout"  # row, section: its section is drafted without it
DRAFT_START = "draft_start"          # section
DRAFT_DONE = "draft_done"            # section, seconds, words
LOCK_START = "lock_start"            # section, mode (paired | solo | final)
LOCK_DONE = "lock_done"              # section, mode, seconds, words, moved, cut, problems, title
SOLO_FALLBACK = "solo_fallback"      # section, waited: the listener waited max_hold_seconds
NARRATE_START = "narrate_start"      # section
PIECE_READY = "piece_ready"          # section, index, seconds (its audio), after (since narrate_start), last
NARRATE_DONE = "narrate_done"        # section, seconds, audio_seconds, pieces, resumed
FIRST_AUDIO = "first_audio"          # the chapter's first piece is playable
PLAYBACK_START = "playback_start"    # the listener began to listen
SECTION_NEEDED = "section_needed"    # section, state (playing | waiting), projected: playback reached it
SKIP = "skip"                        # from, to: a tap on skip (a simulated player's)
PLAYBACK_DONE = "playback_done"
HOLD_START = "hold_start"            # section, reason (end_of_audio | skip)
CHECK_IN = "check_in"                # section, decision (pause | check_in), eta, late
HOLD_END = "hold_end"                # section, seconds, reason, decision, solo
LISTENER_NOTE = "listener_note"      # kind, section, text, said
NOTE_ROW = "note_row"                # row, section, question, researched: a note's extra research agent
RETRY = "retry"                      # action, error: the driver's one retry
STEP_TIMEOUT = "step_timeout"        # action, seconds
ACTION_FAILED = "action_failed"      # action, error: handed to the scheduler, which issues it once more
ARCHIVE_START = "archive_start"
ARCHIVE_DONE = "archive_done"        # seconds, audio_seconds, words, sections, timing, episode
EPISODE_FAILED = "episode_failed"    # error: the chapter plays; only its episode page is missing
READY = "ready"                      # words, audio_seconds, holds, solo, cost_usd
PROBLEM = "problem"                  # stage, error

# --- the medians --------------------------------------------------------------------------
# What a run adds to the file. KEYS are always in `medians` (None until some run has measured
# one): the scheduler's own (scheduler.MEDIANS: opening, research, draft, lock, first_piece,
# narrate) and three the estimate before the first audio needs. "opening" is the scheduler's:
# writing and editing section 1, to its lock; the first piece after that is "first_piece".
# "lock" is the paired lock. The solo and the final lock are kept apart, and appear in
# `medians` only once measured (the scheduler falls back to "lock" without them).
KEYS = (*MEDIANS, "scout", "outline", "first_audio")
OPTIONAL = ("lock_solo", "lock_final")
KEEP_RUNS = 20


def path(cfg: dict) -> Path:
    return ROOT / base.settings(cfg)["timings_file"]


def steps(events: list[dict]) -> dict[str, list[float]]:
    """One run's step lengths, from its timeline. A step that only picked up an earlier try's
    work says so in its event (resumed, adopted, paid: false) and is left out, as is the time to
    the first audio of a run that was stopped and continued on the way there."""
    out: dict[str, list[float]] = {k: [] for k in (*KEYS, *OPTIONAL)}
    whole = True  # no stop between the seed and the first audio
    heard = False
    for e in events:
        name, secs = e.get("event"), e.get("seconds")
        if name == CONTINUED and not heard:
            whole = False
        elif name == FIRST_AUDIO:
            if whole and not heard:
                out["first_audio"].append(float(e["t"]))
            heard = True
        elif name == PIECE_READY:
            if e.get("index") == 0 and e.get("after") is not None:
                out["first_piece"].append(float(e["after"]))
        elif secs is None:
            continue
        elif name == SCOUT_DONE and not e.get("adopted"):
            out["scout"].append(float(secs))
        elif name == OUTLINE_DONE:
            out["outline"].append(float(secs))
        elif name == OPENING_DONE:
            out["opening"].append(float(secs))
        elif name == RESEARCH_DONE and e.get("paid"):
            out["research"].append(float(secs))
        elif name == DRAFT_DONE:
            out["draft"].append(float(secs))
        elif name == LOCK_DONE:
            out[{"solo": "lock_solo", "final": "lock_final"}.get(e.get("mode"), "lock")].append(float(secs))
        elif name == NARRATE_DONE and not e.get("resumed"):
            out["narrate"].append(float(secs))
    return out


def medians_of(runs: list[dict]) -> dict:
    """Each step's median over the runs given, every measurement counting once."""
    pooled: dict[str, list[float]] = {}
    for run in runs:
        for key, values in (run.get("steps") or {}).items():
            pooled.setdefault(key, []).extend(float(v) for v in values)
    out = {k: (round(median(pooled[k]), 2) if pooled.get(k) else None) for k in KEYS}
    out.update({k: round(median(pooled[k]), 2) for k in OPTIONAL if pooled.get(k)})
    return out


def load(cfg: dict) -> dict | None:
    """The medians of earlier runs, in seconds, or None before any run has been measured. A step
    no run has measured yet is None in the dict (the scheduler then gives no estimate that needs it)."""
    data = base.read_json(path(cfg))
    medians = (data or {}).get("medians") if isinstance(data, dict) else None
    if not isinstance(medians, dict) or not any(v is not None for v in medians.values()):
        return None
    return {**{k: None for k in KEYS}, **medians}


def update(cfg: dict, r: Run) -> dict:
    """Add this run's step lengths (from its timeline) and recompute the medians over the last
    KEEP_RUNS runs. A run already in the file (it was continued) is replaced. Returns the medians."""
    data = base.read_json(path(cfg))
    runs = [x for x in (data.get("runs") if isinstance(data, dict) else None) or []
            if isinstance(x, dict) and x.get("run") != r.name]
    mine = {k: [round(v, 2) for v in vs] for k, vs in steps(base.read_timeline(r)).items() if vs}
    if mine:
        runs.append({"run": r.name, "at": base.now_iso(), "steps": mine})
    runs = runs[-KEEP_RUNS:]
    medians = medians_of(runs)
    base.write_json(path(cfg), {"updated": base.now_iso(), "medians": medians, "runs": runs})
    return medians


def estimate(cfg: dict) -> float | None:
    """Seconds from the topic being set to the first audio, as earlier runs went; None until one
    has been measured. The guide gives a time only when this is a number."""
    medians = load(cfg)
    return medians.get("first_audio") if medians else None
