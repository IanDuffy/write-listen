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
lock also read listener_notes.md. A note written to both would reach the outline twice, so a
fast run's notes go to listener_notes.md alone: the outline (if it has not run), and every
section not yet locked, see each note once. The scout is the one stage a note can miss: it reads
only the seed, and its prompt is usually built within seconds of start_chapter, but not always
(the run first waits up to fast.scout_wait_seconds for an early scout still searching, and a
retry scouts again later). Notes said in that window reach the outline and the sections, not the
scout's searches.

A conversation is opened with the run's state (`for_guide`, which also feeds get_status): while
a fast run is being made and nothing can play yet, the wait rules (prompts/guide_wait_fast.md)
go with it, so a guide that meets the listener mid-wait (a reload, a return) has them too; and a
countdown the run can no longer back (past the earlier runs' median) is left out.
"""

from __future__ import annotations

import math
import threading
import time
from datetime import datetime
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


# The guide prompt's {{minutes}} ("about {{minutes}} minutes, or what start_chapter says") when no
# fast run has been measured yet: words, not a figure, so the guide never names a guessed time.
NO_FIGURE = "no known number of (give no time unless a tool result gives one)"


def for_guide(out: dict, status: dict, cfg: dict, runs_dir: Path) -> dict:
    """What the conversation adds to plain(), with the config and the run folder to hand: the wait
    rules while a fast run is being made and nothing can play yet, and no countdown to the first
    audio once the run is past the earlier runs' median (the run itself gives none once the step
    under way has run past its median: faststart/status.first_audio_by)."""
    if status.get("mode") != "fast" or status.get("state") != "making":
        return out
    out = dict(out)
    if not status.get("first_audio"):
        out["while_waiting"] = load_prompt("guide_wait_fast").strip()
    if "starts_playing_in_minutes" in out:
        name = str(status.get("run") or "")
        mode = base.read_json(Path(runs_dir) / name / base.FILES["mode"]) if name and Path(name).name == name else None
        seconds = timings.estimate(cfg)
        set_at = mode.get("seed_set") if isinstance(mode, dict) else None
        if seconds is None or not isinstance(set_at, (int, float)) or time.time() - set_at >= seconds:
            out.pop("starts_playing_in_minutes")
    return out


def plain(out: dict, status: dict) -> dict:
    """guide.plain_status for a fast run: the overnight keys, each section's title and state, and
    whether listening can start. The run's own countdown (from the medians of earlier runs, absent
    until one has been measured) is to the first audio, so it is named for that, and counted down
    from `updated` as the page counts it (status.json is not rewritten while it runs down, nor at
    all during the scout and the outline): whole minutes rounded up, and none once the step under
    way has run past its median (step_ends_in) or the countdown has run out."""
    out = {**out, **fast_status.plain(status)}
    out.pop("minutes_left", None)
    if status.get("state") == "making":
        rest, step = since_written(status, "first_audio_in"), since_written(status, "step_ends_in")
        if rest is not None and step is not None:
            out["starts_playing_in_minutes"] = max(1, math.ceil(rest / 60))
    return out


def since_written(status: dict, key: str) -> float | None:
    """Seconds left of the countdown status[key] (given as of status["updated"]), or None when there
    is none or it has run out: the page's left() (page/fast.js)."""
    secs = status.get(key)
    if secs is None:
        return None
    try:
        written = datetime.fromisoformat(str(status.get("updated"))).timestamp()
    except ValueError:
        written = time.time()
    rest = float(secs) - max(0.0, time.time() - written)
    return rest if rest > 0 else None


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

    # --- the guide prompt's {{minutes}} ----------------------------------------------------

    def minutes(self) -> str:
        """The value of the guide prompt's {{minutes}} for a conversation that makes fast chapters:
        the minutes to the first audio when earlier runs have measured it, else no figure at all
        (never the overnight pipeline's status_minutes)."""
        seconds = timings.estimate(self.cfg)
        if seconds is None:
            return NO_FIGURE
        # guide.md says "about {{minutes}} minutes, or what start_chapter says": the plain number
        # reads naturally there, and start_chapter's result says it is the time until it can play.
        return str(max(1, math.ceil(seconds / 60)))

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
