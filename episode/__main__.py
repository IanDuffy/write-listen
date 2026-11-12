"""python -m episode <timing|references|build> <run> [options]

    timing <run>       -> <run>/11_episode/timing.json (ElevenLabs' timings from narration, else
                          the local recognizer; no cost either way)
    references <run>   -> <run>/11_episode/references.json (one Sonnet 5.5 call per segment)
    build <run>        both, then <run>/11_episode/episode.json, the one file the page reads
    check <run>        the end-to-end checks on a built run -> <run>/11_episode/checks.json, screenshots/

<run> is a run name under runs/ or a path to a run folder. Nothing outside 11_episode/ is written.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from stages.common import ROOT, StageError, load_config


def run_dir(name: str) -> Path:
    p = Path(name)
    for cand in (p, ROOT / "runs" / name):
        if (cand / "09_script_final.md").exists():
            return cand.resolve()
    raise SystemExit(f"No finished run at {name!r} (looked for 09_script_final.md).")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m episode", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["timing", "references", "build", "check"])
    ap.add_argument("run")
    ap.add_argument("--model", default="small.en", help="faster-whisper model (timing)")
    ap.add_argument("--device", default="auto", help="auto | mps | cpu (timing)")
    ap.add_argument("--timings", default="auto", choices=["auto", "elevenlabs", "whisper"],
                    help="timing source: auto takes ElevenLabs' timings when every chunk has them (timing)")
    ap.add_argument("--recorded", help="replay saved recognizer output instead of running Whisper (tests)")
    ap.add_argument("--max-usd", type=float, default=3.0, help="spending cap for the references calls")
    args = ap.parse_args(argv)
    run = run_dir(args.run)
    cfg = load_config()
    try:
        if args.command in ("timing", "build"):
            from . import recognize, timing
            rec = (recognize.RecordedRecognizer(Path(args.recorded)) if args.recorded
                   else timing.pick_timings(run, args.timings,
                                            lambda: recognize.WhisperRecognizer(args.model, args.device)))
            print(f"  timing from {rec.name}")
            t = timing.build(run, rec, cfg["narration"].get("pronunciations") or {})
            s = t["stats"]
            print(f"  timing: {s['words']} words, {s['exact']} exact, {s['fuzzy']} fuzzy, {s['estimated']} estimated "
                  f"-> {timing.write(run, t)}")
        if args.command in ("references", "build"):
            from . import references
            refs, cost = references.build(run, cfg, max_usd=args.max_usd)
            print(f"  references: {len(refs)} cards, ${cost:.3f} -> {references.write(run, refs)}")
        if args.command == "build":
            from . import bundle
            print(f"  -> {bundle.write(run)}")
        if args.command == "check":
            from . import checks, recognize
            out = checks.run_all(run, recognize.WhisperRecognizer(args.model, args.device))
            m, d, sp, sc, rf = (out[k] for k in ("match", "duration", "spot", "screens", "references"))
            print(f"  8. matched {m['matched_pct']}% ({m['exact']} exact, {m['fuzzy']} fuzzy, {m['estimated']} estimated); "
                  f"{len(m['estimated_runs_over_5'])} run(s) of more than five estimated words")
            print(f"  9. aligned end {d['aligned_end']} s, audio {d['audio_duration']} s (difference {d['difference']} s)")
            print(f"  10. spot check: {sp['passed']} of {sp['of']} words heard in their one-second clips")
            print(f"  11. {len(sc['shots'])} screenshots in {run}/11_episode/screenshots")
            print(f"  12. {rf['total']} references {rf['by_kind']}; brief-assigned entries never attached: "
                  f"{rf['assigned_never_attached'] or 'none'}")
    except StageError as e:
        print(f"Stopped: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
