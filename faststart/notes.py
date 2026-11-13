"""listener_notes.md: what the listener said after the topic was set.

One file per run. The guide's add_note and the ask-by-voice aside log append to it (from the
server process); the writer and the editor of every section not yet locked read it (from the
run's process). A line holds the time, the section it was said in, and a kind:

    - [2026-11-12T22:01:03+00:00] (section 2, unclear) I lost the thread at the lime clasts.

Section 0 is the wait before the first audio.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from .base import FILES, now_iso

KINDS = ("landed", "unclear", "connection", "wants more", "question")
LINE = re.compile(r"^- \[([^\]]+)\] \(section (\d+), ([a-z ]+)\) (.+)$")
_LOCK = threading.Lock()

HEADER = ("# Listener notes\n\nWhat the listener said after the topic was set, in order. Sections not yet "
          "locked are written and edited with these in hand.\n\n")


def notes_path(run_root: Path) -> Path:
    return Path(run_root) / FILES["notes"]


def kind_of(kind: str | None) -> str:
    """A kind from the list; anything else (or nothing) counts as a connection, the plain case
    of the listener telling the guide something about themselves."""
    k = (kind or "").strip().lower().replace("_", " ").replace("-", " ")
    return k if k in KINDS else "connection"


def add(run_root: Path, text: str, kind: str | None = None, section: int = 0, when: str | None = None) -> dict:
    """Append one note. Safe to call from the server while the run reads the file."""
    text = " ".join(str(text).split())
    if not text:
        raise ValueError("an empty note")
    note = {"time": when or now_iso(), "section": int(section), "kind": kind_of(kind), "text": text}
    p = notes_path(run_root)
    with _LOCK:
        new = not p.exists()
        with open(p, "a", encoding="utf-8") as f:
            if new:
                f.write(HEADER)
            f.write(f"- [{note['time']}] (section {note['section']}, {note['kind']}) {note['text']}\n")
    return note


def load(run_root: Path) -> list[dict]:
    p = notes_path(run_root)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if m := LINE.match(line.strip()):
            out.append({"time": m.group(1), "section": int(m.group(2)), "kind": m.group(3), "text": m.group(4)})
    return out


def as_prompt(notes: list[dict]) -> str:
    """The notes as the writer and the editor read them."""
    if not notes:
        return "(none yet)"
    where = lambda n: "before the chapter began" if n["section"] == 0 else f"during section {n['section']}"
    return "\n".join(f"- ({n['kind']}, {where(n)}) {n['text']}" for n in notes)
