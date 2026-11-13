"""status.json for a fast run: the overnight fields the page and the server already read, plus
the sections and what the listener is waiting for.

The run's own process writes the file (faststart/run.py, through stages.new.write_status, from
one thread). `state` stays "making" until the archive is done, because the server's invariants
key on it (one run at a time, crash detection, where add_note goes); "the first audio is
playable" is `first_audio`. The fields a fast run adds:

    mode          "fast"
    sections      [{n, title, state, step, ready_in, ready, complete, pieces: [{src, seconds}], seconds, text,
                  timing}]
                  state: played | playing | ready | being_written (the marker's four states).
                  step: what is happening to it now (section_step): researching | writing | editing |
                  recording | ready. ready_in: on the next section the listener will reach that is not
                  ready, seconds (as of `updated`) until its first piece is playable, from the medians
                  of earlier runs (Scheduler.eta); null on every other section, and when there is no
                  estimate or it has passed.
                  title: the outline's, or the one its lock gave it. pieces[].src, text and timing
                  are paths inside the run folder; a piece's MP3 holds its pause and then the
                  speech, so playing the pieces back to back is the chapter as it will be archived.
                  text is null until the section is locked, timing until its word timing exists.
    first_audio   true once the chapter's first piece is playable
    waiting_for   the section the listener is waiting for, or null
    eta_seconds   when that section is expected, from the medians of earlier runs, or null
    check_in      "pause" | "check_in" | null: what the wait is (faststart/checkin.py)
    steps         [{key, label, state}]: the four steps to the first audio (OPENING), each "done",
                  "doing" or "todo"; all done once the first audio is playable
    first_audio_in  seconds (as of `updated`) to the first audio, from the medians of the current
                  step and the ones after it; null with no medians, once the current step has run
                  past its median (never a countdown that sits at zero), and after the first audio
    step_ends_in  seconds (as of `updated`) until the step under way reaches its median; null when
                  first_audio_in is. The scout and the outline run without a status write, so the
                  page and the guide drop the countdown themselves once this has run out

The page counts `first_audio_in`, `step_ends_in` and `ready_in` down itself from `updated`, so the run does not
rewrite status.json for a countdown that is merely running down (settled).

Milestone 5's get_status reuses `sections` (from fast/state.json, the scheduler's snapshot) and
`plain` (the words the guide may say).
"""

from __future__ import annotations

from stages.common import Run

from . import base

MODE = "fast"
WORDS = {"played": "played", "playing": "playing", "ready": "ready", "being_written": "being written"}
# The steps to the first audio: (key, what the page says). The keys are the medians' (timings.KEYS).
OPENING = (("scout", "Looking into it"), ("outline", "Planning the chapter"),
           ("opening", "Writing the opening"), ("first_piece", "Recording the opening"))
STEPS = ("researching", "writing", "editing", "recording", "ready")


def steps(current: str | None) -> list[dict]:
    """The opening steps, those before `current` done; current None: all done (first audio)."""
    keys = [k for k, _ in OPENING]
    at = len(keys) if current is None else keys.index(current)
    return [{"key": k, "label": label, "state": "done" if i < at else "doing" if i == at else "todo"}
            for i, (k, label) in enumerate(OPENING)]


def first_audio_by(current: str | None, started: float | None, now: float, medians: dict | None) -> float | None:
    """When the first audio is expected, in the caller's clock: the current step's start, its
    median and the medians of the steps after it. None when a median is missing, the step's start
    is unknown, or the step has already run past its median (the time left is then unknown)."""
    if current is None or started is None or not medians:
        return None
    keys = [k for k, _ in OPENING]
    lengths = [medians.get(k) for k in keys[keys.index(current):]]
    if any(x is None for x in lengths) or started + float(lengths[0]) <= now:
        return None
    return started + sum(float(x) for x in lengths)


def section_step(n: int, info: dict, rows: dict, running: list) -> str:
    """What is happening to section n, from a scheduler snapshot's section, rows and running
    actions. A step waiting its turn counts as that step (a locked section is "recording" while
    the one before it is narrated). The opening is written and edited in one step: "writing"."""
    if info.get("state") == "ready":
        return "ready"
    if info.get("locked"):
        return "recording"
    if info.get("drafted"):
        return "editing"
    if any(list(a.get("action") or []) == ["draft", n] for a in running):
        return "writing"
    if any(int(r.get("section") or 0) == n and r.get("state") in ("pending", "running") for r in rows.values()):
        return "researching"
    return "writing"


def next_up(snapshot: dict | None) -> int | None:
    """The first section whose first piece is not playable: the next one the listener reaches
    that is not ready (narration is in order). None when all are ready."""
    secs = (snapshot or {}).get("sections") or {}
    return next((int(n) for n in sorted(secs, key=int) if secs[n].get("state") != "ready"), None)


def settled(fields: dict) -> dict:
    """The status without its countdowns' seconds: what decides whether status.json is rewritten
    (the caller compares the countdowns' end times instead)."""
    out = {k: v for k, v in fields.items() if k not in ("first_audio_in", "step_ends_in")}
    if "sections" in out:
        out["sections"] = [{k: v for k, v in s.items() if k != "ready_in"} for s in out["sections"]]
    return out


def titles(r: Run, outline: dict) -> dict[int, str]:
    """The marker titles a lock changed: {section: title}, from the lock records on disk."""
    out = {}
    for s in outline["sections"]:
        saved = base.read_json(base.path(r, "locks") / f"seg{s['n']:02d}.json")
        if isinstance(saved, dict) and saved.get("title"):
            out[s["n"]] = saved["title"]
    return out


def sections(r: Run, outline: dict, snapshot: dict, changed: dict[int, str] | None = None,
             ready_in: tuple[int, float] | None = None) -> list[dict]:
    """The status's section list, from the outline and a scheduler snapshot (Scheduler.snapshot(),
    or the same read back from fast/state.json, where the section numbers are strings).
    `changed` holds the titles locks have given; the outline itself is never rewritten.
    `ready_in`: (section, seconds) for the next section the listener reaches, if estimated."""
    snap = {int(n): s for n, s in (snapshot.get("sections") or {}).items()}
    rows, running = snapshot.get("rows") or {}, snapshot.get("running") or []
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
            "step": section_step(n, info, rows, running),
            "ready_in": round(ready_in[1]) if ready_in and ready_in[0] == n else None,
            "ready": info.get("state") == "ready", "complete": bool(info.get("complete")),
            "pieces": [{"src": rel(folder / f"p{i:02d}.mp3"), "seconds": secs} for i, secs in enumerate(lengths)],
            "seconds": round(sum(lengths), 3),
            "text": rel(text) if info.get("locked") and text.exists() else None,
            "timing": rel(timing) if timing.exists() else None})
    return out


def fields(r: Run, outline: dict | None, snapshot: dict | None, changed: dict[int, str] | None = None, *,
           first_audio: bool = False, waiting_for: int | None = None, eta_seconds: float | None = None,
           check_in: str | None = None, step: str | None = None, first_audio_in: float | None = None,
           ready_in: tuple[int, float] | None = None, step_ends_in: float | None = None) -> dict:
    """What a fast run adds to status.json. `step`: the opening step under way (OPENING's keys;
    scout when not given); `first_audio_in`, `step_ends_in` and `ready_in` in seconds from now."""
    no_countdown = first_audio or first_audio_in is None
    return {"mode": MODE,
            "sections": sections(r, outline, snapshot, changed, ready_in) if outline and snapshot else [],
            "first_audio": bool(first_audio), "waiting_for": waiting_for,
            "eta_seconds": None if eta_seconds is None else round(eta_seconds),
            "check_in": check_in if waiting_for is not None else None,
            "steps": steps(None if first_audio else step or OPENING[0][0]),
            "first_audio_in": None if no_countdown else max(0, round(first_audio_in)),
            "step_ends_in": None if no_countdown or step_ends_in is None else max(0, round(step_ends_in))}


def progress(snapshot: dict | None, total_sections: int) -> tuple[int, int]:
    """(steps done, steps in all) for the page's ring: the scout, the outline, then every section
    locked and narrated, then the archive."""
    total = 2 + 2 * total_sections + 1
    if not snapshot:
        return 0, total
    secs = snapshot["sections"].values()
    done = 2 + sum(bool(s.get("locked")) for s in secs) + sum(bool(s.get("narrated")) for s in secs)
    return done + (1 if snapshot.get("finished") else 0), total


def word(section: dict) -> str:
    """A marker's state in words; one being written says which step it is at."""
    if section.get("state") == "being_written" and section.get("step") in STEPS[:-1]:
        return section["step"]
    return WORDS.get(section["state"], section["state"])


def plain(status: dict) -> dict:
    """A fast run's status in the words the guide may use (to add to guide.plain_status): each
    section's title and where it stands, whether listening can start, and what the listener is
    waiting for. Empty for a run that is not fast."""
    if status.get("mode") != MODE:
        return {}
    listed = status.get("sections") or []
    out: dict = {"can_start_listening": bool(status.get("first_audio"))}
    if listed:
        out["sections"] = [{"title": s["title"], "state": word(s)} for s in listed]
    waiting = next((s for s in listed if s["n"] == status.get("waiting_for")), None)
    if waiting:
        out["waiting_for"] = waiting["title"]
        if status.get("eta_seconds") is not None:
            out["expected_in_seconds"] = status["eta_seconds"]
    return out
