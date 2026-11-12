"""`new`: a seed -> a finished chapter, with no checkpoints (Phase 1, milestone 1).

    python cli.py new --seed "I can't stop thinking about Roman concrete" --why "I pour it for a living" --unattended

Creates a run, writes 00_source/seed.md, and with --unattended runs every stage
in order: scout, angles, choose, ledger, research, ledger-update, brief, draft,
assemble, coverage, edit, revise, narrate, report. A stage whose output already
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

from . import (angles, assemble, brief, choose, coverage, draft, edit, estimate, ledger, ledger_update,
               narrate, report, research, revise, scout)
from .common import Run, StageError, word_count

# (label, the output that shows the stage is done, the stage)
STEPS = [
    ("scout", "map", scout.run),
    ("angles", "angles", angles.run),
    ("choose", "angle", choose.run),
    ("ledger", "ledger", ledger.run),
    ("research", "dossier", research.run),
    ("ledger-update", "ledger_update", ledger_update.run),
    ("brief", "brief", brief.run),
    ("draft", "script_draft", draft.run),  # keeps segments already drafted
    ("assemble", "script_draft", assemble.run),
    ("coverage", "coverage", coverage.run),
    ("edit", "edit_notes", edit.run),
    ("revise", "script_final", revise.run),
    ("narrate", "chapter_mp3", narrate.run),
    ("report", "report", report.run),
]


def remaining_forecast(r: Run, cfg: dict) -> float:
    return sum(usd for _, usd, done in estimate.forecast(r, cfg) if not done)


def run(r: Run, cfg: dict) -> None:
    if not r.unattended:
        raise StageError("new runs the whole pipeline only with --unattended.")
    t0 = time.monotonic()
    todo = [label for label, key, _ in STEPS if not r.path(key).exists()]
    if not todo:
        print(f"  {r.name} is finished: {r.path('chapter_mp3').relative_to(r.root)}")
        return
    ahead, spent = remaining_forecast(r, cfg), r.spent()
    print(f"  unattended run {r.name}: {', '.join(todo)}\n"
          f"  forecast for the rest, upper end: ${ahead:.2f}; spent so far ${spent:.2f}; cap ${r.max_usd:.2f}")
    if spent + ahead > r.max_usd:
        msg = (f"The rest of this run could cost up to ${ahead:.2f} (upper end; ${spent:.2f} spent), past its "
               f"${r.max_usd:.2f} cap. Not started. Raise --max-usd or unattended.max_run_usd to go ahead.")
        r.decide("stopped", msg)
        raise StageError(msg)
    r.decide("start" if spent == 0 else "continue",
             f"Running {', '.join(todo)} with no checkpoints; upper-end forecast ${ahead:.2f}, "
             f"cap ${r.max_usd:.2f}, ${spent:.2f} spent before this.")
    for label, key, stage in STEPS:
        if r.path(key).exists():
            continue
        print(f"\n== {label} ==")
        try:
            stage(r, cfg)
        except StageError as e:
            r.decide("stopped", f"Stopped at {label}: {e}")
            raise StageError(f"stopped at {label}: {e}\n  To continue from {label}: python cli.py new {r.name} "
                             f"--unattended (raise --max-usd if the cap stopped it). Decisions so far: {r.path('decisions')}")
    words = word_count(r.read("script_final"))
    minutes = (time.monotonic() - t0) / 60
    summary = (f"Finished in {minutes:.0f} min: {words:,} words, {r.path('chapter_mp3').relative_to(r.root)}, "
               f"${r.spent():.2f} spent in all.")
    r.decide("done", summary)
    print(f"\n  {summary}\n  Decisions: {r.path('decisions')}")
