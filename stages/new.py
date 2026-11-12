"""`new`: a seed -> a finished chapter, with no checkpoints (Phase 1, milestone 1).

    python cli.py new --seed "I can't stop thinking about Roman concrete" --why "I pour it for a living" --unattended

Creates a run, writes 00_source/seed.md, and with --unattended runs every stage
in order: scout, angles, choose, ledger, research, ledger-update, brief, draft,
assemble, coverage, edit, factcheck, revise, narrate, report. A stage whose output already
exists is skipped, so running the same command again continues from where a
run stopped.

Unattended means nobody is asked anything (docs/phase1-onboarding.md):
- Before spending, the upper-end forecast for the rest of the run is checked
  against the run's cap (config.yaml unattended.max_run_usd, or --max-usd); a
  run that could pass it does not start.
- Each stage's cost guard approves against that cap instead of asking, and
  every request checks it, so the run stops and reports rather than pass it.
- The follow-up round runs what fits the 40-search cap and the money cap.
- Every decision a person used to make (the angle, the ledger, the brief, the
  follow-ups, going past the $5 guard, a stop) goes to decisions.md for Ian.
"""

from __future__ import annotations

import time

import json
import os
import re

from . import (angles, assemble, brief, choose, coverage, draft, edit, estimate, factcheck, ledger, ledger_update,
               narrate, report, research, revise, scout)
from .common import Run, StageError, word_count

# (step, the output that shows it is done, the stage, what the page says while it runs)
STEPS = [
    ("scout", "map", scout.run, "Looking into it"),
    ("angles", "angles", angles.run, "Choosing an angle"),
    ("choose", "angle", choose.run, "Choosing an angle"),
    ("ledger", "ledger", ledger.run, "Planning"),
    ("research", "dossier", research.run, "Researching"),
    ("ledger-update", "ledger_update", ledger_update.run, "Researching"),
    ("brief", "brief", brief.run, "Planning the chapter"),
    ("draft", "script_draft", draft.run, "Writing"),  # keeps segments already drafted
    ("assemble", "script_draft", assemble.run, "Writing"),
    ("coverage", "coverage", coverage.run, "Editing"),
    ("edit", "edit_notes", edit.run, "Editing"),
    ("factcheck", "factcheck", factcheck.run, "Checking the facts"),
    ("revise", "script_final", revise.run, "Editing"),
    ("narrate", "chapter_mp3", narrate.run, "Recording"),
    ("report", "report", report.run, "Finishing"),
]


# --- status.json: what the page shows ---------------------------------------------------

def _now() -> str:
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")  # '+00:00', which every browser parses


def chapter_title(r: Run) -> str | None:
    """The brief's title once there is one, else the chosen angle's."""
    for key, pattern in (("brief", r"^#\s+Brief:\s*(.+)$"), ("angle", r"^#\s+Angle:\s*(.+)$")):
        if r.path(key).exists():
            m = re.search(pattern, r.path(key).read_text(encoding="utf-8"), re.M)
            if m:
                return m.group(1).strip()
    return None


def minutes_left(r: Run, cfg: dict) -> int:
    """Estimated minutes for the steps not yet done (config.yaml status_minutes, rough until calibrated)."""
    table = cfg.get("status_minutes") or {}
    return round(sum(table.get(step, 1) for step, key, _, _ in STEPS if not r.path(key).exists()))


MONEY_CAP = ("max_run_usd", "research.max_usd", " cap. Stopped")  # the guards' and llm.call's cap messages
SEARCH_CAP = ("search cap",)


def plain_problem(step_label: str, error: str) -> tuple[str, bool]:
    """(one plain sentence for the page, whether Try again can help). The full error stays in
    'detail' and decisions.md. A cap stop needs the cap raised first, so retrying cannot help."""
    where = step_label.lower()
    if any(m in error for m in MONEY_CAP):
        return f"The chapter stopped while {where}: continuing would pass its spending limit.", False
    if any(m in error for m in SEARCH_CAP):
        return f"The chapter stopped while {where}: continuing would pass its research limit.", False
    if isinstance(error, str) and error == "interrupted":
        return f"The chapter was interrupted while {where}.", True
    return f"Something went wrong while {where}.", True


def write_status(r: Run, cfg: dict, state: str, **fields) -> dict:
    """Write runs/<run>/status.json atomically (the server streams it to the page)."""
    old = read_status(r) or {}
    done = sum(1 for _, key, _, _ in STEPS if r.path(key).exists())
    status = {
        "run": r.name, "state": state, "stage": None, "label": None,
        "stages_done": done, "stages_total": len(STEPS), "minutes_left": minutes_left(r, cfg),
        "title": chapter_title(r), "audio": None, "message": None, "detail": None,
        "started": old.get("started") or _now(), "updated": _now(), "history": old.get("history", []),
        "pid": os.getpid(), "retryable": True,  # the server checks the pid to tell a dead run from a slow one
        **fields,
    }
    tmp = r.path("status").with_suffix(".json.tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, r.path("status"))
    return status


def read_status(r: Run) -> dict | None:
    p = r.path("status")
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except json.JSONDecodeError:
        return None


def remaining_forecast(r: Run, cfg: dict) -> float:
    return sum(usd for _, usd, done in estimate.forecast(r, cfg) if not done)


def run(r: Run, cfg: dict) -> None:
    if not r.unattended:
        raise StageError("new runs the whole pipeline only with --unattended.")
    t0 = time.monotonic()
    todo = [step for step, key, _, _ in STEPS if not r.path(key).exists()]
    if not todo:
        from . import memory
        memory.record_chapter(cfg, r)
        write_status(r, cfg, "ready", audio=str(r.path("chapter_mp3").relative_to(r.root)), minutes_left=0)
        print(f"  {r.name} is finished: {r.path('chapter_mp3').relative_to(r.root)}")
        return
    ahead, spent = remaining_forecast(r, cfg), r.spent()
    print(f"  unattended run {r.name}: {', '.join(todo)}\n"
          f"  forecast for the rest, upper end: ${ahead:.2f}; spent so far ${spent:.2f}; cap ${r.max_usd:.2f}")
    if spent + ahead > r.max_usd:
        msg = (f"The rest of this run could cost up to ${ahead:.2f} (upper end; ${spent:.2f} spent), past its "
               f"${r.max_usd:.2f} cap. Not started. Raise --max-usd or unattended.max_run_usd to go ahead.")
        r.decide("stopped", msg)
        write_status(r, cfg, "problem", retryable=False, detail=msg,
                     message=("This chapter could cost more than its spending limit, so it did not start."
                              if spent == 0 else "Continuing this chapter could pass its spending limit."))
        raise StageError(msg)
    r.decide("start" if spent == 0 else "continue",
             f"Running {', '.join(todo)} with no checkpoints; upper-end forecast ${ahead:.2f}, "
             f"cap ${r.max_usd:.2f}, ${spent:.2f} spent before this.")
    for label, key, stage, plain in STEPS:
        if r.path(key).exists():
            continue
        print(f"\n== {label} ==")
        write_status(r, cfg, "making", stage=label, label=plain)
        t_stage = time.monotonic()
        try:
            stage(r, cfg)
        except BaseException as e:  # anything at all, Ctrl-C included: the page must not keep saying "making"
            err = "interrupted" if isinstance(e, KeyboardInterrupt) else str(e)
            message, retryable = plain_problem(plain, err)
            r.decide("stopped", f"Stopped at {label}: {type(e).__name__}: {e}")
            write_status(r, cfg, "problem", stage=label, label=plain, message=message, retryable=retryable,
                         detail=f"{type(e).__name__}: {e}")
            if not isinstance(e, StageError):
                raise
            raise StageError(f"stopped at {label}: {e}\n  To continue from {label}: python cli.py new {r.name} "
                             f"--unattended (raise --max-usd if the cap stopped it). Decisions so far: {r.path('decisions')}") from None
        hist = (read_status(r) or {}).get("history", []) + [
            {"stage": label, "seconds": round(time.monotonic() - t_stage, 1)}]
        write_status(r, cfg, "making", stage=label, label=plain, history=hist)
    words = word_count(r.read("script_final"))
    minutes = (time.monotonic() - t0) / 60
    summary = (f"Finished in {minutes:.0f} min: {words:,} words, {r.path('chapter_mp3').relative_to(r.root)}, "
               f"${r.spent():.2f} spent in all.")
    r.decide("done", summary)
    from . import memory
    memory.record_chapter(cfg, r)  # listener/history.md: what the guide has made for this listener
    write_status(r, cfg, "ready", audio=str(r.path("chapter_mp3").relative_to(r.root)), minutes_left=0)
    print(f"\n  {summary}\n  Decisions: {r.path('decisions')}")
