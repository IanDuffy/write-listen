"""`wl overnight ...` (docs/overnight-chapters.md).

    wl overnight run [--now] [--listener ID]   build tonight's chapter (the scheduler runs it; --now: at once)
    wl overnight resume [--listener ID]        continue a build that failed or was stopped
    wl overnight status [--listener ID]        the card and the build's status
    wl overnight plan [--mode M] [--topic T]   write the Up next card now
    wl overnight tick [--listener ID]          what the scheduler calls every quarter hour: plan or build if due
    wl overnight schedule show|install|remove  the laptop's scheduler entry (launchd), for testing
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from stages import common
from stages.common import ROOT, StageError, load_config

from . import profiles, runner

LABEL = "com.writelisten.overnight"


def plist_text(listener: str | None = None) -> str:
    """A launchd job that runs `wl overnight tick` on every quarter hour (so the build starts at 02:00
    exactly, and follows any change to the times in settings.json without reinstalling)."""
    args = [str(ROOT / "wl"), "overnight", "tick"] + (["--listener", listener] if listener else [])
    log = common.SHARED_DIR / "logs" / "overnight.log"
    items = "".join(f"\n      <string>{a}</string>" for a in args)
    times = "".join(f"\n      <dict><key>Minute</key><integer>{m}</integer></dict>" for m in (0, 15, 30, 45))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key><string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>{items}
    </array>
    <key>WorkingDirectory</key><string>{ROOT}</string>
    <key>StartCalendarInterval</key>
    <array>{times}
    </array>
    <key>StandardOutPath</key><string>{log}</string>
    <key>StandardErrorPath</key><string>{log}</string>
  </dict>
</plist>
"""


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def tick(cfg: dict, lid: str | None, clock: datetime | None = None) -> dict:
    """Plan if a planning pass is due, build if a build is due. Safe to call at any time. A card left
    building by a dead build is marked failed first (runner.reconcile); a planning pass that keeps
    failing is not retried more than schedule.plan_backoff allows."""
    from . import planner, schedule
    listener = runner.listener_folder(lid)
    if not listener.is_dir():
        return {"did": None, "reason": f"no listener folder at {listener}"}
    t = clock or datetime.now()
    settings = profiles.settings(cfg, listener=listener)
    card = runner.reconcile(listener)
    print(f"[{t:%Y-%m-%d %H:%M}] tick for {listener.name}")
    if schedule.build_due(card, settings, t):
        return {"did": "build", **runner.run(cfg, listener.name, clock=t, keep_awake=True)}
    if schedule.plan_due(card, settings, t):
        wait = schedule.plan_backoff([f.get("t") or "" for f in planner.failures(listener)], settings, t)
        if wait:
            print(f"  not planning: {wait}")
            return {"did": None, "reason": wait}
        return {"did": "plan", "card": planner.plan(cfg, listener, now=t)}
    return {"did": None}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="wl overnight")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "resume", "status", "plan", "tick"):
        p = sub.add_parser(name)
        p.add_argument("--listener", help="listener id (default: the active one)")
        if name == "run":
            p.add_argument("--now", action="store_true", help="build at once, whatever the day and time")
        if name == "plan":
            p.add_argument("--mode", choices=["surprise", "continue", "topic"])
            p.add_argument("--topic", default="")
            p.add_argument("--notes", default="")
    sc = sub.add_parser("schedule")
    sc.add_argument("action", choices=["show", "install", "remove"])
    sc.add_argument("--listener")
    args = ap.parse_args(argv)
    cfg = load_config()
    try:
        if args.cmd == "run":
            out = runner.run(cfg, args.listener, now=args.now, keep_awake=True)
            print(json.dumps({k: out.get(k) for k in ("ran", "reason", "run", "state", "cost_usd") if k in out}))
        elif args.cmd == "resume":
            out = runner.resume(cfg, args.listener)
            print(json.dumps({k: out.get(k) for k in ("run", "state", "cost_usd")}))
        elif args.cmd == "status":
            print(json.dumps(runner.state(cfg, args.listener), indent=1, ensure_ascii=False))
        elif args.cmd == "plan":
            from . import planner
            request = ({"mode": args.mode or "topic", "topic": args.topic, "notes": args.notes, "session": None}
                       if args.mode or args.topic else None)
            card = planner.plan(cfg, runner.listener_folder(args.listener), request=request)
            if card and card.get("queued"):
                print(f"  {card.get('note')} Queued for the next build day.")
            print(json.dumps(card, indent=1, ensure_ascii=False))
        elif args.cmd == "tick":
            out = tick(cfg, args.listener)
            print(json.dumps({"did": out.get("did"), "state": out.get("state"), "reason": out.get("reason")}))
        elif args.cmd == "schedule":
            text = plist_text(args.listener)
            if args.action == "show":
                print(text)
            elif args.action == "install":
                (common.SHARED_DIR / "logs").mkdir(parents=True, exist_ok=True)
                plist_path().parent.mkdir(parents=True, exist_ok=True)
                plist_path().write_text(text, encoding="utf-8")
                subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist_path())],
                               capture_output=True)
                subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path())], check=True)
                print(f"  installed {plist_path()}; log: {common.SHARED_DIR / 'logs' / 'overnight.log'}")
            else:
                subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist_path())],
                               capture_output=True)
                if plist_path().exists():
                    plist_path().unlink()
                print("  removed")
    except StageError as e:
        print(f"Stopped: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    return 0
