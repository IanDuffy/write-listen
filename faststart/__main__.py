"""python -m faststart <e2e|overnight|report|notes-use|compare|spend|record> [...]

    e2e --seed "..." [--why "..."] [--name RUN] [--max-usd N] [--narrate real|first|sim]
        [--skip-at SECONDS] [--remarks FILE] [--remark-gap SECONDS] [--listener-dir DIR] [--no-episode]
                           one live fast run with a simulated listener who plays in real time
                           (docs/fast-start.md, checks 9, 10 and 12); prints the ElevenLabs balance
                           and what the run will use, then the run's report. --name of a run that
                           exists continues it, narrated as it began (run it detached: it plays
                           in real time, longer than a foreground command may take)
    overnight --seed "..." [--why "..."] [--name RUN] [--max-usd N]
                           the other script of check 11: the overnight pipeline as far as its final
                           script (no narration), in a folder of its own, under the build's cap
    report <run> [--json]  a run's timeline as tables: seed to scout, outline and first audio; each
                           section ready against needed; holds; solo edits; ledger coverage; cost
    notes-use <run>        each listener note, and the lines of later sections that share its words
    compare <fast_run> <other_run_folder>
                           a blind model comparison of the two final scripts (check 11)
                           -> fast_compare.md, fast_compare.json in the fast run folder
    spend                  what the build's live runs have cost so far (faststart/build_costs.jsonl)
    record <run> [--what overnight]
                           add a run made another way to that log (the overnight run of check 11), once

<run> is a run name under runs/ or a path to a run folder. e2e, overnight and compare make paid
calls and refuse to pass the build's cap (config.yaml fast.build_cap_usd).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from stages import common
from stages.common import Run, StageError, load_config


def open_run(name: str) -> Run:
    """A run by name (under runs/) or by the path of its folder."""
    p = Path(name).expanduser()
    if p.is_dir():
        return Run(name=p.resolve().name, root=p.resolve())
    return Run.open(name, runs_dir=common.RUNS_DIR)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m faststart", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    e = sub.add_parser("e2e")
    e.add_argument("--seed", help="the topic, in the listener's words")
    e.add_argument("--why", default="", help="what the listener said about why it matters to them")
    e.add_argument("--name", help="the run's name (made from the seed if not given); an existing run is continued")
    e.add_argument("--max-usd", type=float, help="this run's spending cap (default fast.max_run_usd)")
    e.add_argument("--narrate", choices=["real", "first", "sim"],
                   help="real: every piece from ElevenLabs; first: the chapter's first piece only (up to "
                        "fast.narration.first_piece_max_chars real characters); sim: silence of the right length "
                        "(the default for a new run; a continued run keeps the mode it began with)")
    e.add_argument("--skip-at", type=float, help="the listener taps skip this many seconds into listening")
    e.add_argument("--remarks", help="a JSON list of {kind, text}: what the listener says at the first check-in")
    e.add_argument("--remark-gap", type=float, default=4.0, help="seconds between those remarks")
    e.add_argument("--listener-dir", help="where the listener's memory goes (default: inside the run folder)")
    e.add_argument("--no-episode", action="store_true", help="skip the episode build (its references calls)")
    o = sub.add_parser("overnight")
    o.add_argument("--seed", help="the topic, in the listener's words (the fast run's, word for word)")
    o.add_argument("--why", default="", help="what the listener said about why it matters to them")
    o.add_argument("--name", help="the run's name (made from the seed if not given); an existing run is continued")
    o.add_argument("--max-usd", type=float, help="this run's spending cap (default unattended.max_run_usd)")
    r_ = sub.add_parser("report")
    r_.add_argument("run")
    r_.add_argument("--json", action="store_true")
    n = sub.add_parser("notes-use")
    n.add_argument("run")
    c = sub.add_parser("compare")
    c.add_argument("fast_run")
    c.add_argument("other_run")
    sub.add_parser("spend")
    k = sub.add_parser("record")
    k.add_argument("run")
    k.add_argument("--what", default="overnight", help="what the run was, for the log")
    args = ap.parse_args(argv)

    from . import e2e

    cfg = load_config()
    try:
        if args.command == "e2e":
            out = e2e.e2e(cfg, seed_text=args.seed, why=args.why, name=args.name, max_usd=args.max_usd,
                          narrate=args.narrate, skip_at=args.skip_at,
                          remarks=e2e.load_remarks(args.remarks) if args.remarks else None,
                          remark_gap=args.remark_gap, listener_dir=args.listener_dir, episode=not args.no_episode)
            print("\n" + out["report"])
        elif args.command == "overnight":
            out = e2e.overnight(cfg, seed_text=args.seed, why=args.why, name=args.name, max_usd=args.max_usd)
            print(f"\n  {out['run']}: the script is {out['script']}\n"
                  f"  Compare: python -m faststart compare <fast_run> {out['run']}")
        elif args.command == "report":
            r = open_run(args.run)
            rep = e2e.report(r, common.run_config(r, cfg))
            print(json.dumps(rep, ensure_ascii=False, indent=1) if args.json else e2e.render(rep))
        elif args.command == "notes-use":
            r = open_run(args.run)
            print(e2e.render_notes_use(r, e2e.notes_use(r)))
        elif args.command == "compare":
            from . import compare

            r, other = open_run(args.fast_run), open_run(args.other_run)
            cfg = common.run_config(r, cfg)
            e2e.check_build_cap(cfg, compare.estimate(r, other.root, cfg), "The comparison")
            t0 = time.monotonic()
            try:
                out = compare.run(r, other.root, cfg)
            finally:
                e2e.record_costs("compare", r, time.monotonic() - t0, other=other.name)
            print("\n" + compare.render(out))
        elif args.command == "record":
            r = open_run(args.run)
            if any(x.get("run") == r.name and x.get("what") == args.what and x.get("state") != e2e.STARTED
                   for x in e2e.cost_lines()):
                raise StageError(f"{r.name} is already in {e2e.COSTS.name} as \"{args.what}\".")
            line = e2e.record_costs(args.what, r, sum(x.get("seconds", 0) or 0 for x in r.read_log()))
            print(f"  {r.name}: ${line['cost_usd']:.2f} added; the build so far: ${e2e.build_spend()['cost_usd']:.2f}")
        elif args.command == "spend":
            s, cap = e2e.build_spend(), cfg["fast"]["build_cap_usd"]
            print(f"  The build so far ({s['lines']} paid step(s), {e2e.COSTS.name}): ${s['cost_usd']:.2f} of ${cap:.2f} "
                  f"(Claude ${s['claude_usd']:.2f}; ElevenLabs {int(s['elevenlabs_chars']):,} characters, "
                  f"${s['elevenlabs_usd']:.2f}); ${max(0.0, cap - s['cost_usd']):.2f} left.")
    except StageError as err:
        print(f"Stopped: {err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
