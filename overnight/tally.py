"""`python -m overnight.tally [--listener ID]`: how the morning intros ended (check 12), and the asides on
recorded chapters (check 13), from the listener's session transcripts and the chapters' 12_asks logs."""

from __future__ import annotations

import argparse
import json
from collections import Counter

from stages import common

from . import profiles


def intros(listener) -> list[dict]:
    out = []
    for p in sorted((listener / "sessions").glob("*.jsonl")):
        recs = profiles.read_jsonl(p)
        head = next((x for x in recs if x.get("who") == "session" and x.get("mode")), None)
        if not head or head.get("mode") != "intro":
            continue
        end = next((x for x in reversed(recs) if x.get("who") == "session" and "ended" in x), {})
        out.append({"session": p.stem, "run": head.get("run"), "ended": end.get("ended", "(open)"),
                    "seconds": end.get("seconds"), "tool": any(x.get("who") == "tool" for x in recs)})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m overnight.tally")
    ap.add_argument("--listener")
    a = ap.parse_args(argv)
    listener = profiles.folder(a.listener) if a.listener else common.listener_dir()
    rows = intros(listener)
    print(f"Intros ({len(rows)}): " + ", ".join(f"{k} {v}" for k, v in Counter(r["ended"] for r in rows).items()))
    for r in rows:
        print(f"  {r['session']}  {r['run']}  ended: {r['ended']}  {r['seconds']} s")
    for ch in profiles.chapters(listener=listener):
        if profiles.rehearsal(ch):  # a voice test's replayed chapter, not a recorded one
            continue
        asides = [x for x in profiles.read_jsonl(ch / "12_asks" / "asides.jsonl") if x.get("voice") == "gpt-live"]
        for x in asides:
            print(f"  aside {ch.name} at {x['t']:.0f}s -> restart {x['restart']['t']:.1f}s ({x['restart']['rule']}), "
                  f"ended {x['ended']}, {x['seconds']} s")
    print(json.dumps({"intros": Counter(r["ended"] for r in rows)}, default=dict))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
