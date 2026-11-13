"""Stage 3: episode.json, the one file the page reads: title, angle, duration, audio path, the timing
tree, and the references with their start times filled in from timing.json."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .timing import EPISODE_DIR


def _heading(path: Path, prefix: str) -> str | None:
    if not path.exists():
        return None
    m = re.search(rf"^#\s+{prefix}:\s*(.+)$", path.read_text(encoding="utf-8"), re.M)
    return m.group(1).strip() if m else None


def make(run: Path) -> dict:
    timing = json.loads((run / EPISODE_DIR / "timing.json").read_text(encoding="utf-8"))
    rp = run / EPISODE_DIR / "references.json"
    # No references yet (an unattended run builds only the timing): the transcript reads along without cards.
    refs = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else []
    sentence = {x["id"]: x for s in timing["segments"] for x in s["sentences"]}
    missing = [r["sentence_id"] for r in refs if r["sentence_id"] not in sentence]
    if missing:
        raise ValueError(f"references point to sentences timing.json does not have: {missing[:5]}")
    angle = _heading(run / "03_angle.md", "Angle")
    return {
        "title": _heading(run / "05_brief.md", "Brief") or angle or run.name,
        "angle": angle,
        "run": run.name,
        "duration": timing["duration"],
        "audio": timing["audio"],  # relative to the run folder
        "segments": timing["segments"],
        "references": sorted(({**r, "start": sentence[r["sentence_id"]]["start"]} for r in refs),
                             key=lambda r: r["start"]),
    }


def write(run: Path) -> Path:
    p = run / EPISODE_DIR / "episode.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(make(run), ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    return p
