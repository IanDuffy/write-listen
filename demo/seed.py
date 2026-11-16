"""`wl demo seed`: the demo listener for the library timeline (docs/library-timeline.md, "Demo content").

    wl demo seed [--source DIR] [--use]

Builds listeners/demo-library/ from demo/library_demo.json, from scratch every time: the listener's profile,
guide, settings, library.jsonl, seeds.jsonl, history.md and up_next.json, and one folder per chapter with its
card (00_source/up_next.json), finished.json, playback.json and moments.jsonl. These are the files a real
listener has, so the screens need no demo code. Two chapters are real: their audio, timed transcript, episode
page and the files ask-by-voice reads are copied from a listener that has them (--source, default
listeners/ian, or the main checkout's when this is a worktree). Nothing is written outside
listeners/demo-library/; the source is only read. --use makes the demo the active listener
(`wl listener use ian` goes back).

The build is the same every time: every date and id comes from the seed file, and the folder is written to a
temporary name and swapped in whole. The scheduler leaves the demo alone (settings.json's "demo": the tick
neither plans nor builds for it).
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from overnight import profiles
from stages import common
from stages.common import ROOT, StageError

SEED = ROOT / "demo" / "library_demo.json"
LISTENER = "demo-library"
# What a real chapter brings: audio, the timed transcript and episode page, and what ask-by-voice reads
# (ask/aside.Chapter: the script, brief, angle, dossier, ledger), the outline and intro brief when it has them.
COPY = ("10_audio/chapter.mp3", "11_episode/episode.json", "11_episode/timing.json", "11_episode/references.json",
        "09_script_final.md", "05_brief.md", "05_outline.json", "03_angle.md", "04_dossier.md", "ledger.json",
        "intro_brief.md")
NEEDED = ("10_audio/chapter.mp3", "11_episode/episode.json", "11_episode/timing.json")


def _main_checkout() -> Path:
    return ROOT.parents[2] if ROOT.parent.name == "worktrees" else ROOT


def default_source() -> Path:
    """listeners/ian here, else in the main checkout (a worktree has no listeners/ of its own)."""
    here = common.LISTENERS_DIR / "ian"
    return here if (here / "chapters").is_dir() else _main_checkout() / "listeners" / "ian"


def _jsonl(p: Path, rows: list[dict]) -> None:
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def build(seed: dict, dest: Path, source: Path) -> dict:
    """Write the demo listener described by `seed` into `dest` (which must not exist)."""
    dest.mkdir(parents=True)
    for name in profiles.LISTENER_FOLDERS:
        (dest / name).mkdir()
    profiles.write_json(dest / "settings.json", seed["settings"])
    (dest / "profile.md").write_text(seed["profile"].rstrip() + "\n", encoding="utf-8")
    (dest / "guide.md").write_text(seed["guide"].rstrip() + "\n", encoding="utf-8")
    if seed.get("history"):
        (dest / "history.md").write_text(seed["history"].rstrip() + "\n", encoding="utf-8")
    _jsonl(dest / "library.jsonl", seed["library"])
    _jsonl(dest / "seeds.jsonl", seed["seeds"])
    copied = []
    for ch in seed["chapters"]:
        root = dest / "chapters" / ch["folder"]
        (root / "00_source").mkdir(parents=True)
        if ch.get("copy_from"):
            src = source / "chapters" / ch["copy_from"]
            missing = [f for f in NEEDED if not (src / f).exists()]
            if missing:
                raise StageError(f"demo: {src} has no {', '.join(missing)}; give the listener that has it with --source.")
            for f in COPY:
                if (src / f).exists():
                    (root / f).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src / f, root / f)
                    copied.append(f"{ch['folder']}/{f}")
        profiles.write_json(root / "00_source" / "up_next.json", ch["card"])
        profiles.write_json(root / "finished.json", {"t": ch["finished"], "demo": True})
        if ch.get("playback"):
            profiles.write_json(root / "playback.json", ch["playback"])
        if ch.get("moments"):
            _jsonl(root / "moments.jsonl", ch["moments"])
    profiles.write_json(dest / "up_next.json", seed["up_next"])
    return {"chapters": len(seed["chapters"]), "copied": copied}


def seed_listener(source: Path | None = None, seed_file: Path = SEED) -> Path:
    """Rebuild listeners/demo-library/ from the seed file. Returns its folder."""
    seed = json.loads(Path(seed_file).read_text(encoding="utf-8"))
    lid = seed.get("listener") or LISTENER
    if lid != LISTENER:  # the one folder this command may replace
        raise StageError(f"demo: the seed file names listener {lid!r}; only {LISTENER} is the demo's.")
    base = common.LISTENERS_DIR
    dest, tmp = base / lid, base / f".{lid}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    try:
        out = build(seed, tmp, Path(source) if source else default_source())
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    if dest.exists():
        old = base / f".{lid}.old"
        shutil.rmtree(old, ignore_errors=True)
        dest.rename(old)
        tmp.rename(dest)
        shutil.rmtree(old)
    else:
        tmp.rename(dest)
    print(f"  {dest}: {out['chapters']} chapters, {len(out['copied'])} files copied from the real chapters")
    return dest


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="wl demo")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed", help="rebuild listeners/demo-library/ from demo/library_demo.json")
    s.add_argument("--source", help="the listener folder the real chapters are copied from (default: listeners/ian)")
    s.add_argument("--use", action="store_true", help="make the demo the active listener")
    args = ap.parse_args(argv)
    try:
        seed_listener(Path(args.source) if args.source else None)
        if args.use:
            profiles.set_active(LISTENER)
            print(f"  active listener: {LISTENER} (`wl listener use ian` goes back)")
    except StageError as e:
        print(f"  {e}")
        return 1
    return 0
