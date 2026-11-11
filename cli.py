"""Commissioned audio chapter MVP.

    python cli.py <stage> <run> [options]

Stages, in order ([Ian] marks where the pipeline stops for you):

    init <run> [--source FILE]   create runs/<run>/ (optionally copy in the transcript)
    ingest <run> [--source FILE] 00_source/transcript.pdf -> 00_source/transcript.txt
    map <run>                    -> 01_map.md
    angles <run>                 -> 02_angles.md
                                 [Ian] write 03_angle.md
    ledger <run>                 -> ledger.json, ledger.md (5-7 ranked ideas the chapter must land)
                                 [Ian] review and edit ledger.json; `ledger <run>` again re-checks it
    research <run> --plan-only   each row's search budget and the cost estimate, nothing spent
    research <run> --rows L01    research just those rows (redoes them if already researched)
    research <run>               one agent per remaining row -> 04_research/Lnn.md -> 04_dossier.md
    ledger-update <run>          judge each row, one follow-up round -> ledger.json, 04_dossier.md
    brief <run>                  -> 05_brief.md
                                 [Ian] edit 05_brief.md
    draft <run> [--segments 1-2] -> 06_segments/segNN.md
    assemble <run>               -> 07_script_draft.md
    coverage <run>               draft vs ledger -> 07b_coverage.md (the editor reads it)
    edit <run>                   -> 08_edit_notes.md
    revise <run>                 -> 09_script_final.md
                                 [Ian] read 09_script_final.md
    narrate <run>                -> 10_audio/chapter.mp3
    narrate <run> --pilot        -> 10_audio/pilot.mp3 (segments 1-2 by default)
    baseline <run>               -> baseline/script.md
    narrate <run> --baseline     -> baseline/chapter.mp3
    compare <run>                -> compare/A.mp3, B.mp3, key.txt
    report <run>                 -> report.md

Utilities:

    status <run>                 which outputs exist, the next step, and the cost so far
    estimate [run]               forecast the cost of the rest of the run (or a whole run)
    ping                         one tiny call to each API (milestone 1)
    smoketest                    live check of every API assumption (~$0.10): run before real work
    voices [--search TEXT]       list ElevenLabs models and voices on the account
    voicetest [--voices A,B,C]   narrate voice_test/sample.md in each candidate voice

Options: --force overwrites an existing output; --yes pre-approves the cost guard.
"""

from __future__ import annotations

import argparse
import sys

from stages.common import ROOT, PATHS, Run, StageError, load_config

STAGES_IN_ORDER = [
    ("transcript", "ingest"), ("map", "map"), ("angles", "angles"), ("angle", "[Ian] write 03_angle.md"),
    ("ledger", "ledger, then [Ian] review ledger.json"), ("dossier", "research"),
    ("ledger_update", "ledger-update"), ("brief", "brief"), (None, "[Ian] edit 05_brief.md, then draft"),
    ("script_draft", "assemble (after draft)"), ("coverage", "coverage"), ("edit_notes", "edit"),
    ("script_final", "revise"),
    (None, "[Ian] read 09_script_final.md, then narrate"), ("chapter_mp3", "narrate"),
    ("baseline_script", "baseline"), ("baseline_mp3", "narrate --baseline"),
    ("compare_dir", "compare"), ("report", "report"),
]


def status(r: Run, cfg: dict) -> None:
    from stages.report import cost_table

    print(f"Run: {r.root}")
    next_step = None
    for key, step in STAGES_IN_ORDER:
        if key is None:
            continue
        p = r.path(key)
        exists = p.exists() and (p.is_file() or any(p.iterdir()))
        print(f"  [{'x' if exists else ' '}] {PATHS[key]}")
        if not exists and next_step is None:
            next_step = step
    if r.path("ledger").exists():
        from stages import ledger
        from stages.research import row_paths, searches_used
        try:
            rows = ledger.active(ledger.load(r, cfg, render=False))
            have = [x["id"] for x in rows if row_paths(r, x["id"])[0].exists()]
            missing = [x["id"] for x in rows if x["id"] not in have]
            print(f"  ledger rows researched: {len(have)} of {len(rows)}"
                  + (f" (missing: {', '.join(missing)})" if missing else "")
                  + f"; {searches_used(r)} of {cfg['research']['max_searches']} searches used")
            if any(x["status"] for x in rows):
                print("  row status: " + ", ".join(f"{x['id']} {x['status']}" for x in rows))
            if any(x["coverage"] for x in rows):
                print("  coverage: " + ", ".join(f"{x['id']} {x['coverage']}" for x in rows))
        except StageError as e:
            print(f"  ledger: {e}")
    segs = sorted(r.path("segments_dir").glob("seg*.md")) if r.path("segments_dir").exists() else []
    if segs:
        print(f"  segments drafted: {', '.join(p.stem for p in segs)}")
    rows, total = cost_table(r.read_log())
    print(f"  cost so far: ${total:.2f} " + (f"({', '.join(f'{s} ${c:.2f}' for s, _, c in rows if c)})" if total else ""))
    if next_step:
        print(f"  next: {next_step}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage")
    ap.add_argument("run", nargs="?")
    ap.add_argument("--force", action="store_true", help="overwrite an existing output")
    ap.add_argument("--yes", action="store_true", help="pre-approve the cost guard")
    ap.add_argument("--source", help="transcript file for init/ingest (.pdf, .txt, .md)")
    ap.add_argument("--segments", help="segments to draft or narrate, e.g. 1-2 or 1,3")
    ap.add_argument("--pilot", action="store_true", help="narrate: drafted segments (default 1-2)")
    ap.add_argument("--baseline", action="store_true", help="narrate: the baseline script")
    ap.add_argument("--voices", help="voicetest: comma-separated voice IDs")
    ap.add_argument("--search", help="voices: filter by name")
    ap.add_argument("--seed", type=int, help="report: seed for the claim sample")
    ap.add_argument("--plan-only", action="store_true",
                    help="research: show each row's budget and the estimate, spend nothing")
    ap.add_argument("--rows", help="research: ledger rows to research (or redo), e.g. L01 or L01,L03")
    args = ap.parse_args(argv)

    cfg = load_config()
    try:
        st = args.stage
        if st == "ping":
            from stages import ping
            ping.run(Run.open(args.run or "ping", create=True, yes=args.yes), cfg)
            return 0
        if st == "smoketest":
            from stages import smoketest
            smoketest.run(Run.open(args.run or "smoketest", create=True, yes=args.yes), cfg)
            return 0
        if st == "estimate":
            from stages import estimate
            estimate.run(Run.open(args.run) if args.run else None, cfg)
            return 0
        if st == "voices":
            from stages.voicetest import list_voices
            list_voices(cfg, args.search)
            return 0
        if st == "voicetest":
            from stages import voicetest
            r = Run(name="voice_test", root=ROOT / "voice_test", force=args.force, yes=args.yes)
            voicetest.run(r, cfg, voices=args.voices)
            return 0

        if not args.run:
            ap.error(f"{st} needs a run name, e.g. python cli.py {st} beauty-scarry")
        r = Run.open(args.run, force=args.force, yes=args.yes, create=(st == "init"))

        if st == "init":
            r.path("source_dir").mkdir(exist_ok=True)
            print(f"  created {r.root}")
            if args.source:
                from stages import ingest
                ingest.run(r, cfg, source=args.source)
            else:
                print("  Put the transcript at 00_source/transcript.pdf, then: python cli.py ingest " + r.name)
        elif st == "status":
            status(r, cfg)
        elif st == "ingest":
            from stages import ingest
            ingest.run(r, cfg, source=args.source)
        elif st == "research":
            from stages import research
            research.run(r, cfg, rows=args.rows, plan_only=args.plan_only)
        elif st == "ledger-update":
            from stages import ledger_update
            ledger_update.run(r, cfg)
        elif st in ("map", "angles", "ledger", "brief", "assemble", "coverage", "edit", "revise",
                    "baseline", "compare"):
            import importlib
            importlib.import_module(f"stages.{st}").run(r, cfg)
        elif st == "draft":
            from stages import draft
            draft.run(r, cfg, segments=args.segments)
        elif st == "narrate":
            from stages import narrate
            narrate.run(r, cfg, pilot=args.pilot, baseline=args.baseline, segments=args.segments)
        elif st == "report":
            from stages import report
            report.run(r, cfg, seed=args.seed)
        else:
            ap.error(f"unknown stage {st!r}")
    except StageError as e:
        print(f"Stopped: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
