"""status.json for a fast run: the overnight fields the page and the server already read, plus
the sections and what the listener is waiting for.

The run's own process writes the file (faststart/run.py, through stages.new.write_status, from
one thread). `state` stays "making" until the archive is done, because the server's invariants
key on it (one run at a time, crash detection, where add_note goes); "the first audio is
playable" is `first_audio`. The fields a fast run adds:

    mode          "fast"
    sections      [{n, title, state, ready, complete, pieces: [{src, seconds}], seconds, text, timing}]
                  state: played | playing | ready | being_written (the marker's four states).
                  title: the outline's, or the one its lock gave it. pieces[].src, text and timing
                  are paths inside the run folder; a piece's MP3 holds its pause and then the
                  speech, so playing the pieces back to back is the chapter as it will be archived.
                  text is null until the section is locked, timing until its word timing exists.
    first_audio   true once the chapter's first piece is playable
    waiting_for   the section the listener is waiting for, or null
    eta_seconds   when that section is expected, from the medians of earlier runs, or null
    check_in      "pause" | "check_in" | null: what the wait is (faststart/checkin.py)

Milestone 5's get_status reuses `sections` (from fast/state.json, the scheduler's snapshot) and
`plain` (the words the guide may say).
"""

from __future__ import annotations

from stages.common import Run

from . import base

MODE = "fast"
WORDS = {"played": "played", "playing": "playing", "ready": "ready", "being_written": "being written"}


def titles(r: Run, outline: dict) -> dict[int, str]:
    """The marker titles a lock changed: {section: title}, from the lock records on disk."""
    out = {}
    for s in outline["sections"]:
        saved = base.read_json(base.path(r, "locks") / f"seg{s['n']:02d}.json")
        if isinstance(saved, dict) and saved.get("title"):
            out[s["n"]] = saved["title"]
    return out


def sections(r: Run, outline: dict, snapshot: dict, changed: dict[int, str] | None = None) -> list[dict]:
    """The status's section list, from the outline and a scheduler snapshot (Scheduler.snapshot(),
    or the same read back from fast/state.json, where the section numbers are strings).
    `changed` holds the titles locks have given; the outline itself is never rewritten."""
    snap = {int(n): s for n, s in (snapshot.get("sections") or {}).items()}
    rel = lambda p: str(p.relative_to(r.root))
    out = []
    for s in outline["sections"]:
        n = s["n"]
        info = snap.get(n, {})
        lengths = [float(x) for x in info.get("pieces") or []]
        folder = base.section_dir(r, n)
        text, timing = r.segment_path(n), base.path(r, "section_timings") / f"seg{n:02d}.json"
        out.append({
            "n": n, "title": (changed or {}).get(n) or s["title"],
            "state": info.get("marker") or "being_written",
            "ready": info.get("state") == "ready", "complete": bool(info.get("complete")),
            "pieces": [{"src": rel(folder / f"p{i:02d}.mp3"), "seconds": secs} for i, secs in enumerate(lengths)],
            "seconds": round(sum(lengths), 3),
            "text": rel(text) if info.get("locked") and text.exists() else None,
            "timing": rel(timing) if timing.exists() else None})
    return out


def fields(r: Run, outline: dict | None, snapshot: dict | None, changed: dict[int, str] | None = None, *,
           first_audio: bool = False, waiting_for: int | None = None, eta_seconds: float | None = None,
           check_in: str | None = None) -> dict:
    """What a fast run adds to status.json."""
    return {"mode": MODE,
            "sections": sections(r, outline, snapshot, changed) if outline and snapshot else [],
            "first_audio": bool(first_audio), "waiting_for": waiting_for,
            "eta_seconds": None if eta_seconds is None else round(eta_seconds),
            "check_in": check_in if waiting_for is not None else None}


def progress(snapshot: dict | None, total_sections: int) -> tuple[int, int]:
    """(steps done, steps in all) for the page's ring: the scout, the outline, then every section
    locked and narrated, then the archive."""
    total = 2 + 2 * total_sections + 1
    if not snapshot:
        return 0, total
    secs = snapshot["sections"].values()
    done = 2 + sum(bool(s.get("locked")) for s in secs) + sum(bool(s.get("narrated")) for s in secs)
    return done + (1 if snapshot.get("finished") else 0), total


def plain(status: dict) -> dict:
    """A fast run's status in the words the guide may use (to add to guide.plain_status): each
    section's title and where it stands, whether listening can start, and what the listener is
    waiting for. Empty for a run that is not fast."""
    if status.get("mode") != MODE:
        return {}
    listed = status.get("sections") or []
    out: dict = {"can_start_listening": bool(status.get("first_audio"))}
    if listed:
        out["sections"] = [{"title": s["title"], "state": WORDS.get(s["state"], s["state"])} for s in listed]
    waiting = next((s for s in listed if s["n"] == status.get("waiting_for")), None)
    if waiting:
        out["waiting_for"] = waiting["title"]
        if status.get("eta_seconds") is not None:
            out["expected_in_seconds"] = status["eta_seconds"]
    return out
