"""What a fast run changes for the onboarding guide (docs/fast-start.md, milestone 5).

stages/guide.py stays the guide's conversation; the server hands each Session one Hooks object
(`fast=`), and the Session asks it at four points. Without one, a Session behaves as it did.

    scout_topic(topic)       step 0: scout.early in a background thread, at most fast.early_scouts
                             topics a conversation; returns at once and never raises
    started(run_root)        start_chapter's result for a fast run: no time unless earlier runs
                             have measured one, and how to behave while the listener waits
                             (prompts/guide_wait_fast.md)
    add_note(run_root, ...)  a fast run's notes go to listener_notes.md, with a kind and the
                             section the listener is in
    plain(out, status)       get_status for a fast run: its sections, whether listening can start

Where a fast run's notes go, and why not to both files: the scout and the outline read the seed
through common.seed_inputs, which appends 00_source/notes.md; the outline, every draft and every
lock also read listener_notes.md. A note written to both would reach the outline twice. The
scout's prompt is built within a second or two of start_chapter, before a note can be said, so
00_source/notes.md could only ever feed the outline's second copy. A fast run's notes therefore
go to listener_notes.md alone: the outline (if it has not run), and every section not yet
locked, see each note once.
"""

from __future__ import annotations

import math
import threading
from pathlib import Path

from stages.common import load_prompt

from . import base, notes, scout, timings
from . import status as fast_status

LISTENING = ("playing", "waiting", "paused")  # player states in which the listener is in a section

SCOUTING = {"scouting": True, "now": "Nothing to say about this. Carry on with the conversation: answer what "
                                     "the listener just said."}


def is_fast(run_root: Path) -> bool:
    return (Path(run_root) / base.FILES["mode"]).exists()


def section_now(run_root: Path) -> int:
    """The section the listener is in, from the page's last position report (fast/player.json);
    0 before the chapter has begun (the wait before first audio) and after it has finished."""
    report = base.read_json(Path(run_root) / base.FILES["player"])
    if not isinstance(report, dict) or report.get("state") not in LISTENING:
        return 0
    try:
        return max(0, int(report.get("section") or 0))
    except (TypeError, ValueError):
        return 0


def plain(out: dict, status: dict) -> dict:
    """guide.plain_status for a fast run: the overnight keys, each section's title and state, and
    whether listening can start. The run's own countdown (from the medians of earlier runs, absent
    until one has been measured) is to the first audio, so it is named for that."""
    out = {**out, **fast_status.plain(status)}
    if "minutes_left" in out:
        out["starts_playing_in_minutes"] = out.pop("minutes_left")
    return out


class Hooks:
    """One conversation's fast hooks. `status()` is the current run's status (the server's)."""

    def __init__(self, cfg: dict, runs_dir: Path, status=None, early=None):
        self.cfg, self.runs_dir, self.status_fn = cfg, Path(runs_dir), status
        self.early = early or scout.early
        self.topics: list[str] = []          # scouted in this conversation, normalised
        self.threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    # --- scout_topic ----------------------------------------------------------------------

    def scout_topic(self, topic: str) -> dict:
        """Start the early scout for a likely topic and return at once (the tool runs under the
        chat lock, and the voice agent does not wait for it). The scout is a thread of the
        server, never the pipeline's process: the server must stay free to start the chapter.
        Its calls are logged in its own folder under runs/_scouts/, so they never count against
        the conversation's own spending limit."""
        topic = " ".join(str(topic or "").split())
        key = " ".join(topic.lower().split())
        if not key:
            return {"scouting": False, "reason": "The topic was empty."}
        now = (self.status_fn() if self.status_fn else None) or {}
        if now.get("state") == "making":
            return {"scouting": False, "reason": "A chapter is already being made."}
        limit = base.settings(self.cfg).get("early_scouts", 2)
        with self._lock:
            if key in self.topics:  # asked again: it is already being looked into
                return dict(SCOUTING)
            if len(self.topics) >= limit:
                return {"scouting": False, "reason": f"{limit} topics have been looked into already in this "
                                                     "conversation. Carry on: start_chapter does its own."}
            self.topics.append(key)
            t = threading.Thread(target=self._scout, args=(topic,), daemon=True, name="early-scout")
            self.threads.append(t)
        t.start()
        return dict(SCOUTING)

    def _scout(self, topic: str) -> None:
        try:
            self.early(self.cfg, self.runs_dir, topic)
        except BaseException as e:  # a scout that fails costs the chapter a minute, never the conversation
            print(f"  early scout of \"{topic}\" failed: {type(e).__name__}: {e}")

    # --- start_chapter --------------------------------------------------------------------

    def started(self, run_root: Path) -> dict | None:
        """start_chapter's result for a fast run; None for any other run (the caller's stands)."""
        if not is_fast(run_root):
            return None
        out: dict = {"started": True}
        seconds = timings.estimate(self.cfg)
        already = (base.read_json(Path(run_root) / "status.json") or {}).get("first_audio")
        if seconds is not None and not already:  # a time only when earlier runs have measured one
            minutes = max(1, math.ceil(seconds / 60))
            out["first_audio_minutes"] = minutes
            out["when"] = (f"Going by earlier chapters, it can start playing in about {minutes} "
                           f"minute{'' if minutes == 1 else 's'}.")
        out["while_waiting"] = load_prompt("guide_wait_fast").strip()
        return out

    # --- add_note -------------------------------------------------------------------------

    def add_note(self, run_root: Path, note: str, kind: str | None = None) -> dict | None:
        """A note for a fast run -> listener_notes.md, with its kind and the section the listener
        is in; None for any other run (the caller then writes 00_source/notes.md as before)."""
        if not is_fast(run_root):
            return None
        saved = notes.add(run_root, note, kind, section=section_now(run_root))
        listed = (base.read_json(Path(run_root) / "status.json") or {}).get("sections") or []
        if listed and all(s.get("text") for s in listed):  # every section has its final text
            effect = "The chapter is already written, so this one won't change it; it is kept with the chapter."
        else:
            effect = "It can shape the parts of the chapter not yet written."
        return {"added": True, "kind": saved["kind"], "effect": effect}
