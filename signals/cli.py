"""`wl signals ...`: Listener Signals from the terminal (signals/; Ian's build plan, 2026-11-21).

    wl signals fetch [lid] [--all]     keep every GPT-Live recording of the listener in <listener>/signals/audio/
                                       (OpenAI deletes them after 30 days): reused from a copy already on disk,
                                       else downloaded; says why for each one it can't get
    wl signals run [lid] [--all] [--force]
                                       measure every recorded conversation (intro, guide, debrief) into
                                       <listener>/signals/<stem>.jsonl; one changed in the last 5 minutes may
                                       still be live and waits, unless --force
    wl signals report [lid] [--all]    what the data says: sound tags, bleed into the listener's channel, phone,
                                       the norm, what was mentioned, the page against the recording for the
                                       same turns, Ian's marks

lid: "own" (the default: the active listener) or a tester id (t-...). --all: every tester, and the active
listener too when demo mode is off. Nothing here runs from the server or calls a model.
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from dotenv import load_dotenv

from stages import common
from stages.common import ROOT, StageError, load_config

from . import fetch as fetch_mod
from . import measure, report

LID = re.compile(r"[a-z0-9][a-z0-9._-]*")


def demo_on(cfg: dict) -> bool:
    return bool((cfg.get("demo") or {}).get("enabled")) or \
        os.environ.get("WL_DEMO", "").strip().lower() in ("1", "true", "yes", "on")


def listeners(cfg: dict, lid: str | None, every: bool) -> list[tuple[str, Path]]:
    """(label, folder) for each listener the command is for."""
    from demo import testers
    if every:
        out = [(t["lid"], testers.folder(t["lid"])) for t in testers.all_testers() if not t.get("missing")]
        if not demo_on(cfg):
            own = common.listener_dir(cfg)
            if all(p.resolve() != own.resolve() for _, p in out):
                out.append(("own", own))
        return out
    if not lid or lid == "own":
        return [("own", common.listener_dir(cfg))]
    if not LID.fullmatch(lid) or testers.record(lid) is None:
        raise StageError(f"No tester {lid}. Give a tester id from wl study testers, or own.")
    return [(lid, testers.folder(lid))]


def _fetch(label: str, folder: Path, cfg: dict) -> list[dict]:
    got = fetch_mod.fetch(folder, cfg)
    counts = {}
    for r in got:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    tally = ", ".join(f"{n} {s}" for s, n in counts.items()) or "no GPT-Live sessions"
    print(f"  {label} ({folder}): {tally}")
    for r in got:
        print(f"    {r['status']:<8} {r['stem']:<26} {r['live_id']}" + (f": {r['detail']}" if r["detail"] else ""))
    return got


def _run(label: str, folder: Path, cfg: dict, force: bool) -> dict:
    got = measure.run(folder, cfg, force=force)
    if got.get("gone"):     # a tester reset or deleted from /admin while it measured: what it found is not theirs
        print(f"  {label} ({folder}): the folder was {got['gone']} while measuring; stopped"
              + (", nothing written into the new one" if got["gone"] == "reset" else ""))
        return got
    print(f"  {label} ({folder}): {got['records']} turn records in {len(got['written'])} conversations")
    for c in got["conversations"]:
        head = f"    {c['stem']:<26} {c['part']:<8}"
        if c.get("skipped"):
            print(f"{head} skipped: {c['skipped']}")
            continue
        bits = [f"{c['recordings']} recording{'s' * (c['recordings'] != 1)}, {c['turns']} turns"]
        if c["untranscribed"]:
            bits.append(f"{c['untranscribed']} sounds with no transcript")
        if c["unheard"]:
            bits.append(f"{c['unheard']} lines matched to no sound")
        if c["no_audio"]:
            bits.append(f"no audio for {', '.join(c['no_audio'])} (wl signals fetch)")
        bits += c["errors"]
        print(f"{head} " + "; ".join(bits))
    return got


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="wl signals", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, helps in (("fetch", "keep the recordings"), ("run", "measure the recorded conversations"),
                        ("report", "what the data says")):
        p = sub.add_parser(name, help=helps)
        p.add_argument("lid", nargs="?", help='a tester id, or "own" (the default)')
        p.add_argument("--all", action="store_true", help="every tester (and the active listener, demo off)")
        if name == "run":
            p.add_argument("--force", action="store_true", help="measure conversations changed in the last 5 minutes")
    args = ap.parse_args(argv)
    load_dotenv(ROOT / ".env")  # the OpenAI key for fetch, as wl study does (a variable already set wins)
    cfg = load_config()
    try:
        todo = listeners(cfg, args.lid, args.all)
        if not todo:
            print("  No listeners to look at.")
        for label, folder in todo:
            if not folder.is_dir():
                print(f"  {label}: no folder at {folder}")
                continue
            if args.cmd == "fetch":
                _fetch(label, folder, cfg)
            elif args.cmd == "run":
                _run(label, folder, cfg, args.force)
            else:
                print(report.build(folder, cfg, label).get("text"), end="")
    except StageError as e:
        print(f"  Stopped: {e}")
        return 1
    return 0
