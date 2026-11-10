"""Blind comparison: copy both MP3s to compare/ as A.mp3 and B.mp3 in random order.

The key goes in compare/key.txt. Listen to both before opening it.
"""

import secrets
import shutil

from .common import Run, StageError


def run(r: Run, cfg: dict):
    chapter = r.need("chapter_mp3")
    baseline = r.need("baseline_mp3")
    d = r.path("compare_dir")
    if d.exists() and any(d.iterdir()) and not r.force:
        raise StageError("compare/ already has files. Use --force to reshuffle (you will need to listen again).")
    d.mkdir(exist_ok=True)
    pair = [("pipeline", chapter), ("baseline", baseline)]
    if secrets.randbelow(2):
        pair.reverse()
    for letter, (_, src) in zip("AB", pair):
        shutil.copyfile(src, d / f"{letter}.mp3")
    (d / "key.txt").write_text(
        "".join(f"{letter}.mp3 = {name}\n" for letter, (name, _) in zip("AB", pair)), encoding="utf-8")
    print(f"  Wrote compare/A.mp3 and compare/B.mp3. The key is in compare/key.txt: don't open it until you've listened.")
    return d
