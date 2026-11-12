"""Stage 0 for a seed run: the listener's topic -> 00_source/seed.md

Phase 1 (docs/phase1-onboarding.md) starts a chapter from a seed, not a
transcript: the topic in the listener's own words, what they said about why,
and anything to include or avoid. Milestone 1 types it on the command line
(`new --seed "..." --why "..."`); later the guide's conversation writes the
same file. Its words also feed the chapter's opening callback.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from .common import RUNS_DIR, Run, StageError, load_prompt


def seed_text(topic: str, why: str = "", notes: str = "") -> str:
    return load_prompt("seed_file", topic=topic.strip(), why=why.strip() or "(not said)",
                       notes=notes.strip() or "(nothing)").strip() + "\n"


def run_name(topic: str, runs_dir: Path | None = None) -> str:
    """'I can't stop thinking about Roman concrete' -> 'roman-concrete-20261112' (unique)."""
    stop = {"i", "im", "a", "an", "the", "of", "about", "and", "to", "in", "on", "can", "cant", "t", "stop",
            "thinking", "how", "why", "what", "is", "are", "was", "it", "me", "my", "we", "you", "that",
            "this", "do", "does", "with", "for", "keep", "wonder", "wondering", "know", "want", "really"}
    words = [w for w in re.findall(r"[a-z0-9]+", topic.lower().replace("'", "")) if w not in stop]
    base = "-".join(words[:4]) or "seed"
    name = f"{base}-{time.strftime('%Y%m%d')}"
    root, n = runs_dir or RUNS_DIR, 2
    candidate = name
    while (root / candidate).exists():
        candidate, n = f"{name}-{n}", n + 1
    return candidate


def write(r: Run, topic: str = "", why: str = "", notes: str = "", seed_file: str | None = None) -> Path:
    if r.path("transcript").exists():
        raise StageError(f"{r.name} already starts from a transcript; a seed run needs its own run folder.")
    if seed_file:
        p = Path(seed_file).expanduser()
        if not p.exists():
            raise StageError(f"Seed file not found: {p}")
        text = p.read_text(encoding="utf-8").strip() + "\n"
    elif topic.strip():
        text = seed_text(topic, why, notes)
    else:
        raise StageError("A seed run needs a topic: --seed \"...\" (and optionally --why \"...\"), or --seed-file.")
    out = r.write("seed", text)
    print(f"  seed: {out.relative_to(r.root)}")
    return out
