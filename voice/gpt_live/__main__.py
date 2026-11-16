"""Live checks for the GPT-Live guide (voice/gpt_live/persona.py, sim.py).

    python -m voice.gpt_live persona [--n K] [--personas builder,reserved] [--max-turns 12] [--no-grade]
    python -m voice.gpt_live asides [--only closing,lost] [--run runs/<fast run>] [--no-grade]
    python -m voice.gpt_live grade <folder or transcript.json>
    python -m voice.gpt_live spend
    python -m voice.gpt_live archive <run> [--runs-dir DIR] [--no-fetch] [--references]

Each paid call is logged to voice/gpt_live/build_costs.jsonl, and nothing starts that would take the
build past config live.build_cap_usd. Transcripts, grades and audio go to voice/gpt_live/_runs/<stamp>/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from stages.common import ROOT, StageError, load_config

from . import persona as P
from . import sim as S


def _names(spec: str | None, known: list[str]) -> list[str]:
    if not spec:
        return known
    names = [x.strip() for x in spec.split(",") if x.strip()]
    unknown = [x for x in names if x not in known]
    if unknown:
        raise StageError(f"Unknown: {', '.join(unknown)}. Known: {', '.join(known)}.")
    return names


def cmd_persona(cfg: dict, a) -> None:
    people = P.personas()
    out = P.new_out_dir()
    log_run = P.claude_run()
    for name in _names(a.personas, list(people)):
        for k in range(a.n):
            p = dict(people[name])
            if a.n > 1:
                p["name"] = f"{name}-{k + 1}"
            t = asyncio.run(P.converse(cfg, p, out, max_turns=a.max_turns, max_minutes=a.max_minutes, log_run=log_run))
            g = None if a.no_grade else P.grade(cfg, out / f"{p['name']}.json", log_run)
            print(P.summary(p["name"], t, g), flush=True)
    print(f"Saved in {out}. Build spend: ${S.spent():.2f}")


def cmd_asides(cfg: dict, a) -> None:
    run_dir = Path(a.run) if Path(a.run).is_absolute() else ROOT / a.run
    scenarios = {s[0]: s for s in P.ASIDES}
    out = P.new_out_dir()
    log_run = P.claude_run()
    for name in _names(a.only, list(scenarios)):
        t = asyncio.run(P.aside(cfg, run_dir, scenarios[name], out))
        g = None if a.no_grade else P.grade(cfg, out / f"aside-{name}.json", log_run)
        print(P.summary(f"aside-{name}", t, g), flush=True)
        print(f"  checks: {json.dumps(t['checks'])}", flush=True)
    print(f"Saved in {out}. Build spend: ${S.spent():.2f}")


def cmd_grade(cfg: dict, a) -> None:
    target = Path(a.path)
    files = [target] if target.is_file() else sorted(
        p for p in target.glob("*.json") if not p.name.endswith(".grade.json"))
    if not files:
        raise StageError(f"No transcripts in {target}.")
    log_run = P.claude_run()
    for f in files:
        t = json.loads(f.read_text(encoding="utf-8"))
        print(P.summary(f.stem, t, P.grade(cfg, f, log_run)), flush=True)
    print(f"Build spend: ${S.spent():.2f}")


def cmd_spend(cfg: dict, a) -> None:
    by: dict[str, float] = {}
    if S.COSTS.exists():
        for line in S.COSTS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                key = r["what"].split()[0] + (" " + r["what"].split()[1] if r["what"].startswith("claude") else "")
                by[key] = by.get(key, 0) + (r.get("usd") or 0)
    for k, v in sorted(by.items()):
        print(f"{k:28s} ${v:.4f}")
    print(f"{'total':28s} ${S.spent():.4f} of ${cfg['live']['build_cap_usd']:.2f}")


def cmd_archive(cfg: dict, a) -> None:
    """The live chapter's archive: fetch the session recordings, cut the reading, stitch and time it."""
    from stages.common import Run

    from . import archive as A

    runs_dir = Path(a.runs_dir).resolve() if a.runs_dir else None
    r = Run.open(a.run, runs_dir=runs_dir)
    if a.no_fetch:
        result = A.build(r, cfg, references=a.references, on_step=lambda s: print(f"  {s}...", flush=True))
    else:
        print("  fetching the recordings...", flush=True)
        result = A.archive(r, cfg, references=a.references)
    print(A.summary(result))
    print(f"  -> {r.path('chapter_mp3')}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m voice.gpt_live", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("persona", help="simulated onboarding conversations by voice, then graded")
    p.add_argument("--n", type=int, default=1, help="conversations per persona")
    p.add_argument("--personas", help="comma-separated names from prompts/live_personas.md (default: all)")
    p.add_argument("--max-turns", type=int, default=12, help="the listener's spoken lines at most")
    p.add_argument("--max-minutes", type=float, default=6)
    p.add_argument("--no-grade", action="store_true")
    s = sub.add_parser("asides", help="scripted questions mid-chapter, then graded")
    s.add_argument("--only", help="comma-separated scenario names (default: all)")
    s.add_argument("--run", default="runs/ideas-behind-systems-stocks-20261113",
                   help="a fast run folder with 05_outline.json and 06_segments/ (read only)")
    s.add_argument("--no-grade", action="store_true")
    g = sub.add_parser("grade", help="grade saved transcripts")
    g.add_argument("path")
    sub.add_parser("spend", help="the build's spending so far")
    ar = sub.add_parser("archive", help="a live chapter's archive: the reading cut from the session recordings")
    ar.add_argument("run")
    ar.add_argument("--runs-dir", help="the folder the run is in (default: runs/)")
    ar.add_argument("--no-fetch", action="store_true", help="use the recordings already in runs/<run>/live/")
    ar.add_argument("--references", action="store_true", help="also the episode's reference cards (Claude calls)")
    a = ap.parse_args(argv)
    cfg = load_config()
    try:
        {"persona": cmd_persona, "asides": cmd_asides, "grade": cmd_grade, "spend": cmd_spend,
         "archive": cmd_archive}[a.cmd](cfg, a)
    except StageError as e:
        print(f"Stopped: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
