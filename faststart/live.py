"""Live narration: a fast run read aloud in the live session (fast.json "voice": "live"; Spec One
Live Voice). There is no recording step: a locked section's text goes straight to the paragraph
queue the page feeds to GPT-Live, so "ready" means locked.

    10_audio/secNN/live.json    {"section", "paragraphs": [{"id", "text", "seconds", "pause_ms", "beat"}],
                                 "seconds"}: the section as paragraphs (voice.gpt_live.chapter's ids and
                                 cleaning), each with an estimated length; written when the section is
                                 "narrated", which for a live run is the moment it is locked

The driver treats each paragraph as a piece (narrate.Piece with no audio files), so the scheduler's
rules (pieces by index, the last one makes a section whole, narrate_done) hold unchanged. A
paragraph's seconds are its pause and its characters at fast.narration.speech_chars_per_second: an
estimate for the scheduler and the page's progress, not a measurement. The reading's audio is the
live session's recording, archived by its own step.
"""

from __future__ import annotations

import sys

from stages.common import Run, StageError

from . import base
from .narrate import Piece

FILE = "live.json"


def _path(r: Run, n: int):
    return base.section_dir(r, n) / FILE


def plan(cfg: dict, n: int, text: str) -> list[dict]:
    """Section n's locked text as paragraphs with their pause and estimated seconds. Raises
    StageError (from clean_paragraphs) on a bracket the voice would read aloud or a leftover marker."""
    from voice.gpt_live.chapter import clean_paragraphs

    ncfg, speech = cfg["narration"], base.settings(cfg)["narration"]["speech_chars_per_second"]
    out = []
    for i, p in enumerate(clean_paragraphs(text, n)):
        if i == 0:  # nothing before the chapter, a section break before the rest
            pause = 0 if n == 1 else ncfg["segment_pause_ms"]
        else:
            pause = ncfg.get("beat_pause_ms", 1100) if p["beat"] else ncfg["chunk_pause_ms"]
        out.append({"id": p["id"], "text": p["text"], "seconds": round(pause / 1000 + len(p["text"]) / speech, 3),
                    "pause_ms": pause, "beat": bool(p["beat"])})
    if not out:
        raise StageError(f"Section {n} has no text to read.")
    return out


def _pieces(n: int, paragraphs: list[dict]) -> list[Piece]:
    last = len(paragraphs) - 1
    return [Piece(section=n, index=i, wav=None, mp3=None, seconds=p["seconds"], pause_ms=p["pause_ms"],
                  chars=len(p["text"]), text=p["text"], simulated=False, last=i == last)
            for i, p in enumerate(paragraphs)]


def paragraphs(r: Run, n: int) -> list[dict]:
    """live.json's paragraphs for section n, or [] before it is there."""
    got = base.read_json(_path(r, n))
    return list(got.get("paragraphs") or []) if isinstance(got, dict) else []


def pieces(r: Run, n: int) -> list[Piece]:
    """narrate.pieces for a live run: section n's paragraphs as pieces, from live.json."""
    return _pieces(n, paragraphs(r, n))


def narrated(r: Run, n: int) -> bool:
    """narrate.narrated for a live run: section n's paragraphs are written."""
    return bool(paragraphs(r, n))


class LiveNarrator:
    """narrate.Narrator's interface with nothing to synthesize."""

    def __init__(self, r: Run, cfg: dict):
        self.r, self.cfg = r, cfg

    def section(self, n: int, text: str, on_piece=None) -> list[Piece]:
        """Write section n's live.json, then call on_piece for each paragraph in order (the last
        with piece.last set). Returns the pieces. on_piece may raise (the driver's stop check):
        the section is on disk whole either way, so a continued run finds it narrated."""
        paras = plan(self.cfg, n, text)
        base.write_json(_path(self.r, n), {"section": n, "paragraphs": paras,
                                           "seconds": round(sum(p["seconds"] for p in paras), 3)})
        out = _pieces(n, paras)
        for piece in out:
            if on_piece:
                on_piece(piece)
        return out


def module(r: Run):
    """The module whose pieces() and narrated() read run r's sections: this one for a live run,
    faststart.narrate otherwise."""
    from . import narrate

    return sys.modules[__name__] if base.is_live(r) else narrate
