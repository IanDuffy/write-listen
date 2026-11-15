"""`wl listener ...` and `wl overnight ...` (docs/overnight-chapters.md).

    wl listener list                 the listeners, and which is active
    wl listener use <id>             make one active
    wl listener reset                a new demo listener, made active (nothing else is touched)
    wl listener migrate [--from DIR ...] [--chapter RUN ...] [--dry-run]
    wl listener settings [group.key=value ...]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from stages import common
from stages.common import ROOT, StageError, load_config

from . import profiles

# The two runs that become the first chapters (docs/overnight-chapters.md, Migration).
FIRST_CHAPTERS = ("beauty-scarry", "herons-hunt-standing-still-20261114")


def _main_checkout() -> Path:
    """The main checkout when this is a worktree (.claude/worktrees/<name>), else this folder."""
    return ROOT.parents[2] if ROOT.parent.name == "worktrees" else ROOT


def _default_sources(chapters: tuple[str, ...]) -> list[Path]:
    """The main checkout, and any worktree of it holding one of the chapter runs (the herons
    chapter was made in the live-voice worktree)."""
    main = _main_checkout()
    found = [main]
    wt = main / ".claude" / "worktrees"
    for d in sorted(wt.iterdir()) if wt.is_dir() else []:
        if d.resolve() != ROOT.resolve() and any((d / "runs" / c).is_dir() for c in chapters) \
                and not any((main / "runs" / c).is_dir() for c in chapters if (d / "runs" / c).is_dir()):
            found.append(d)
    return found


def listener_main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="wl listener")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    u = sub.add_parser("use")
    u.add_argument("id")
    sub.add_parser("reset")
    m = sub.add_parser("migrate")
    m.add_argument("--from", dest="sources", action="append",
                   help="a checkout whose runs/ and listener/ to copy in (repeatable; default: the main checkout)")
    m.add_argument("--chapter", action="append", help="a run that becomes a chapter (repeatable)")
    m.add_argument("--id", default=common.DEFAULT_LISTENER)
    m.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("settings")
    s.add_argument("changes", nargs="*", help="group.key=value, e.g. overnight.enabled=false")
    args = ap.parse_args(argv)
    cfg = load_config()

    if args.cmd == "list":
        active = common.active_listener_id()
        for lid in profiles.ids():
            print(f"{'*' if lid == active else ' '} {lid}")
        if not profiles.ids():
            print(f"  (none yet; the active id is {active})")
    elif args.cmd == "use":
        profiles.set_active(args.id)
        print(f"  active listener: {args.id}")
    elif args.cmd == "reset":
        print(f"  new listener {profiles.reset()} is active; nothing else was touched")
    elif args.cmd == "migrate":
        chapters = tuple(args.chapter or FIRST_CHAPTERS)
        sources = [Path(x) for x in args.sources] if args.sources else _default_sources(chapters)
        out = profiles.migrate(sources, args.id, chapters, dry_run=args.dry_run)
        if args.dry_run:
            for kind, src, dest in out["plan"]:
                print(f"  {kind:8} {src} -> {dest}")
        else:
            print(f"  copied {len(out['copied'])}, already there {len(out['kept'])}")
            for p in out["copied"]:
                print(f"    {p}")
        if out["missing_chapters"]:
            print(f"  not found (give its checkout with --from): {', '.join(out['missing_chapters'])}")
    elif args.cmd == "settings":
        changes: dict = {}
        for c in args.changes:
            key, _, val = c.partition("=")
            group, _, k = key.partition(".")
            try:
                v = json.loads(val)
            except ValueError:
                v = val
            if k == "days" and isinstance(v, str):
                v = [x for x in v.split(",") if x]
            changes.setdefault(group, {})[k] = v
        now = profiles.save_settings(cfg, changes) if changes else profiles.settings(cfg)
        print(json.dumps(now, indent=1))
    return 0


def main(argv: list[str]) -> int:
    try:
        if argv[0] == "listener":
            return listener_main(argv[1:])
        if argv[0] == "overnight":
            from . import commands
            return commands.main(argv[1:])
    except StageError as e:
        import sys
        print(f"Stopped: {e}", file=sys.stderr)
        return 1
    raise SystemExit(f"unknown command {argv[0]!r}")
