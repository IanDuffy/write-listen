"""`wl study ...`: the Tester Demo from the terminal (docs/tester-demo.md).

    wl study testers          every tester: stage, chapter, questions, debrief, cost
    wl study create <label>   a new tester; prints the link (origin from WL_BASE_URL or demo.base_url)
    wl study report [--yes]   study/report.md from every tester's debrief (one Claude call; --yes passes
                              the cost guard)
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

from stages.common import ROOT, StageError, load_config

from . import report, state, testers


def _testers(cfg: dict) -> None:
    rows = state.all_states(cfg)
    if not rows:
        print("  No testers yet. Make one with: wl study create <label>")
        return
    for s in rows:
        flags = [x for x in ("revoked" if s["revoked"] else "", "left early" if s["left_early"] else "") if x]
        chapter = f"\"{s['title']}\", {s['minutes_heard']} of {s['chapter_minutes']} min" if s.get("title") else "-"
        debrief = "done" if s["debrief"]["done"] else "-"
        print(f"  {s['label']:<18} {s['lid']:<22} {s['stage']:<11}{' (' + ', '.join(flags) + ')' if flags else ''}")
        print(f"    chapter {chapter}; questions {s['questions']}; debrief {debrief}; "
              f"last active {s['last_active'] or '-'}; cost ${s['cost']['total']:.2f}")
    t = state.totals(rows)
    print(f"  Total ${t['total']:.2f} (Claude ${t['claude']:.2f}, GPT-Live ${t['gpt_live']:.2f}"
          + (f", of which ${t['estimated']:.2f} estimated" if t["estimated"] else "") + ")")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="wl study", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("testers", help="list the testers")
    c = sub.add_parser("create", help="a new tester's link")
    c.add_argument("label", nargs="+")
    r = sub.add_parser("report", help="write study/report.md")
    r.add_argument("--yes", action="store_true", help="pass the cost guard")
    args = ap.parse_args(argv)
    # The link's origin (WL_BASE_URL) is read from the environment, which `wl` on the droplet doesn't have:
    # it runs as the app's user through sudo, with a clean one. .env (setup.sh links it to the service's
    # /etc/write-listen/env) is where the keys come from too (stages/llm.py); a variable already set wins.
    load_dotenv(ROOT / ".env")
    cfg = load_config()
    try:
        if args.cmd == "testers":
            _testers(cfg)
        elif args.cmd == "create":
            got = testers.create(cfg, " ".join(args.label))
            print(f"  {got['label']} ({got['lid']}): {got['link']}")
        elif args.cmd == "report":
            out = report.build(cfg, yes=args.yes)
            print(f"  Wrote {out}")
    except StageError as e:
        print(f"  Stopped: {e}")
        return 1
    return 0
