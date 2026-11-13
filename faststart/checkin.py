"""The server's side of the check-in (docs/fast-start.md, "When a section isn't ready").

The run decides what a wait is (`decide`) and the simulated player where a skip goes
(`next_marker`): pure code. The rest is what the server needs when the guide comes in (milestone
5, through ask/routes.py): what the guide is told (`context`), what its three tools do (`tool`),
the notes and settings the page gets, and where what was said goes afterwards (the aside log's
anchor, listener_notes.md, the profile). The page does the voice session, the mic and the timers.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from stages.common import Run, listener_profile, load_prompt, spoken_text

from . import base, notes, status


def decide(eta: float | None, short_wait: float = 15) -> str:
    """What happens when the listener reaches a section that is not ready: "pause" or "check_in".

    A section expected within about 15 seconds (fast.short_wait_seconds) is just a pause: the
    circle shows the making state and nothing else happens. Otherwise the guide comes in, and
    also when there is no estimate: a guess that turned out wrong would leave the listener in
    silence, which is the one thing the check-in exists to prevent."""
    if eta is None:
        return "check_in"
    return "pause" if eta <= short_wait else "check_in"


def next_marker(current: int, last: int) -> int | None:
    """Skipping goes to the next section marker only (acceptance check 6): the section after
    the one the listener is in, or None on the last section, where there is nowhere to go."""
    return current + 1 if current < last else None


# --- what the guide is told, and what its tools do (milestone 5) ------------------------------
#
# Everything below runs in the server (ask/routes.py), from the run's files only: the run is
# another process. The page's last position report (fast/player.json) says where the listener is.

# What resume_chapter returns once the awaited part has audio. As in an aside (ask/routes.py RESUMED),
# the platform hands a tool's result back to the model, which then speaks: so it asks for the lead-in.
RESUMED = {"resuming": True, "now": "Say your lead-in: one sentence in your own voice that ties what the listener "
                                    "said to what comes next, and stop. The next part starts the moment you finish. "
                                    "Don't mention this tool."}
NOT_READY = {"resuming": False, "now": "The next part is not ready yet. Stay with the listener; the page will tell "
                                       "you when it is."}
ENDINGS = ("resumed", "silence", "button", "dropped", "timeout", "error", "other")
NO_NAME = "(No record of your name with this listener. Don't make a point of names.)"  # as ask/aside.py says it
MIC = {"auto": "open", "tap": "closed until the listener taps the circle"}
# ElevenLabs documents no size limit for a dynamic variable (docs read 2026-11-13); the aside already
# sends about as much as a whole chapter. This only guards against a runaway section.
HEARD_MAX_CHARS = 60_000
_PAUSE = re.compile(r"[ \t]*\[pause\][ \t]*", re.I)


def _run(run_root: Path) -> Run:
    return Run(name=Path(run_root).name, root=Path(run_root))


def live(run_root: Path) -> bool:
    """A fast run that is not archived yet: what the listener says can still shape it."""
    root = Path(run_root)
    return (root / base.FILES["mode"]).exists() and not (root / base.FILES["work"] / "archive.json").exists()


def run_status(run_root: Path) -> dict:
    got = base.read_json(Path(run_root) / "status.json")
    return got if isinstance(got, dict) else {}


def awaited(run_root: Path) -> int | None:
    """The section the run says the listener is waiting for (status.json), if any."""
    n = run_status(run_root).get("waiting_for")
    return n if isinstance(n, int) and not isinstance(n, bool) else None


def listener_at(run_root: Path, section: int) -> dict:
    """Where the listener is as they wait for `section`, from the page's last report:
    {"section": the section they were listening to (0 before the chapter began), "pieces": how many
    pieces of the awaited section they have already heard (they ran dry inside it), "skipped": bool}."""
    raw = base.read_json(Path(run_root) / base.FILES["player"])
    raw = raw if isinstance(raw, dict) else {}
    inside = raw.get("section") == section and raw.get("state") in ("waiting", "playing", "paused")
    pieces = raw.get("piece") if inside else 0  # the piece to play next: as many as they have heard
    pieces = pieces if isinstance(pieces, int) and not isinstance(pieces, bool) and pieces > 0 else 0
    return {"section": section if pieces else max(section - 1, 0), "pieces": pieces,
            "skipped": raw.get("reason") == "skip" and raw.get("waiting_for") == section}


def _pieces(run_root: Path, n: int) -> list:
    from . import narrate
    return narrate.pieces(_run(run_root), n)


def ready(run_root: Path, section: int, have: int = 0) -> bool:
    """Whether the awaited section has audio the listener has not heard: more pieces than the `have`
    they had heard when the check-in began. From status.json, which is what the page plays from."""
    listed = next((s for s in run_status(run_root).get("sections") or [] if s.get("n") == section), None)
    count = len(listed.get("pieces") or []) if listed else len(_pieces(run_root, section))
    return count > have


def _spoken(text: str) -> str:
    """A locked section as it is heard: markers and [Dn] stripped as narration strips them, and a held
    beat as a paragraph break."""
    return re.sub(r"\n{3,}", "\n\n", _PAUSE.sub("\n\n", spoken_text(text))).strip()


def _outline(run_root: Path) -> dict:
    got = base.read_json(Path(run_root) / base.FILES["outline_json"])
    return got if isinstance(got, dict) and isinstance(got.get("sections"), list) else {"sections": []}


def _titles(run_root: Path, outline: dict) -> dict[int, str]:
    """Each section's title: the outline's, or the one its lock gave it."""
    changed = status.titles(_run(run_root), outline)
    return {s["n"]: changed.get(s["n"]) or str(s.get("title") or f"Part {s['n']}") for s in outline["sections"]}


def sections_known(run_root: Path) -> list[int]:
    return [s["n"] for s in _outline(run_root)["sections"]]


def chapter_title(run_root: Path) -> str:
    """The chapter's title: the outline's, else as ask-by-voice finds it (03_angle.md, the brief)."""
    from ask import aside as A
    return str(_outline(run_root).get("title") or "").strip() or A.chapter_title(Path(run_root))


def _first_line(aside: dict, who: str) -> str:
    return next((x.get("text", "") for x in aside.get("transcript") or []
                 if x.get("who") == who and x.get("text", "").strip(". ")), "")


def context(run_root: Path, cfg: dict, section: int, chapter_t: float = 0.0) -> dict[str, str]:
    """The check-in agent's dynamic variables (stages.voice CHECKIN_PLACEHOLDERS), as the listener
    waits for `section` after `chapter_t` seconds of listening. From files only, and it needs no
    timing and no final script: a skip two minutes into section 1 gets the same.

    `heard` is the locked text of every section before the awaited one, plus the pieces of the
    awaited one the listener has already heard when they ran dry inside it (as sent to the voice:
    the pronunciation table's respellings are in it). `coming` is the awaited section and those
    after it, a title and what each lands: to lead in, never to tell."""
    from ask import aside as A
    from stages import guide

    root, r = Path(run_root), _run(run_root)
    outline = _outline(root)
    titles = _titles(root, outline)
    numbers = [s["n"] for s in outline["sections"]] or list(range(1, section + 1))
    at = listener_at(root, section)

    parts = []
    for n in (k for k in numbers if k < section):
        p = r.segment_path(n)
        if p.exists():
            parts.append(f"(Part {n}: {titles.get(n, f'Part {n}')})\n\n{_spoken(p.read_text(encoding='utf-8'))}")
    if at["pieces"]:
        said = [p.text.strip() for p in _pieces(root, section)[:at["pieces"]]]
        if said:
            parts.append(f"(Part {section}: {titles.get(section, f'Part {section}')}, as far as it has played)\n\n"
                         + "\n\n".join(said))
    heard = "\n\n".join(parts)
    if len(heard) > HEARD_MAX_CHARS:
        tail = heard[-HEARD_MAX_CHARS:]
        heard = ("(The start of the chapter is left out here for length. This is the most recent part.)\n\n"
                 + tail[tail.find("\n\n") + 2 if "\n\n" in tail else 0:])
    if at["skipped"] and heard:
        heard = "(The listener skipped ahead to get here: they have not heard all of the last part below.)\n\n" + heard

    coming = []
    for s in outline["sections"]:
        n = s["n"]
        if n < section:
            continue
        label = (f"Part {n}, the rest of it, being finished now" if n == section and at["pieces"] else
                 f"Part {n}, being finished now" if n == section else f"Part {n}")
        lands = " ".join(str(s.get("lands") or "").split())
        coming.append(f"{label}: {titles[n]}" + (f". {lands}" if lands else ""))

    total = len(numbers)
    minutes = f", after about {round(chapter_t / 60)} minutes of listening" if chapter_t >= 90 else ""
    chapter = [f"Title: {chapter_title(root)}",
               f"Through-line: {' '.join(str(outline.get('through_line') or '').split()) or '(not written down)'}",
               f"Where they are: waiting for part {section} of {total}{minutes}."]

    asides = A.load_asides(root)
    earlier = [("Check-in. " if a.get("kind") == "check_in" else "Question. ") + A.earlier_text([a]) for a in asides]
    openings = [f'- "{line[:240]}"' for a in asides if a.get("kind") == "check_in" and (line := _first_line(a, "guide"))]
    values = {
        "identity": guide.identity(cfg) or NO_NAME,
        "profile": listener_profile(cfg),
        "chapter": "\n".join(chapter),
        "heard": heard or "(nothing yet: they are at the very start)",
        "coming": "\n".join(coming) or "(nothing listed: this is the last part)",
        "notes": notes.as_prompt(notes.load(root)),
        "earlier_asides": "\n".join(earlier) or "(none)",
        "earlier_openings": "\n".join(openings) or "(none: this is the first check-in of the chapter)",
        "mic": MIC.get(base.settings(cfg).get("gap_mic"), MIC["auto"]),
    }
    # The script marks titles in markdown (*Gilead*); a voice would read the asterisks.
    return {k: v.replace("*", "") for k, v in values.items()}


def page_notes() -> dict[str, str]:
    """prompts/guide_checkin_notes.md as {key: note}: what the page sends into the session."""
    return dict(ln.split(": ", 1) for ln in load_prompt("guide_checkin_notes").splitlines() if ": " in ln)


def page_settings(cfg: dict) -> dict:
    """What the page needs to run a check-in (config.yaml fast)."""
    f = base.settings(cfg)
    return {"silence_seconds": f["silence_seconds"], "gap_mic": f["gap_mic"],
            "tap_wait_seconds": f["page"]["tap_wait_seconds"], "quiet_seconds": f["page"]["quiet_seconds"]}


def anchor(run_root: Path, section: int, have: int = 0) -> str:
    """The sentence a conversation is shown before on the episode page: the first of the awaited
    section, or, when the listener had heard `have` pieces of it, the sentence after those (counted
    in words against the locked text, so near enough; the ids are episode.script's)."""
    first = f"seg{section:02d}-s001"
    if have <= 0:
        return first
    try:
        from episode import script as ep_script
        words = sum(len(p.text.split()) for p in _pieces(run_root, section)[:have])
        text = re.sub(r"<!--.*?-->", "", _run(run_root).segment_path(section).read_text(encoding="utf-8"), flags=re.S)
        seen = 0
        sentences = ep_script.parse(f"<!-- seg{section:02d} -->\n\n{text}")[0].sentences
        for s in sentences:
            if seen >= words:
                return s.id
            seen += len(s.words)
        return sentences[-1].id if sentences else first
    except Exception:  # a log line's anchor is never worth a failed request
        return first


def tool(a: dict, name: str, args: dict) -> dict:
    """A client tool the check-in agent called. `a` is the open check-in (ask/routes.py): its run
    folder, the awaited section, where the listener was, and how many pieces they had heard."""
    root = a["run"]
    if name == "add_note":
        text = " ".join(str(args.get("note") or "").split())
        if not text:
            return {"added": False, "reason": "The note was empty."}
        note = notes.add(root, text, args.get("kind"), section=a["was_in"])
        return {"added": True, "kind": note["kind"], "effect": "The parts not yet written can take it into account."}
    if name == "get_status":
        return {**status.plain(run_status(root)), "next_part_ready": ready(root, a["section"], a["have"])}
    if name == "resume_chapter":
        if not ready(root, a["section"], a["have"]):
            return NOT_READY
        a["resume"] = {"reason": args.get("reason") or "other"}
        return RESUMED
    return {"error": f"No tool named {name}."}


def ending(a: dict, how: str) -> str:
    """How a check-in ended, for the log: "resumed" when the guide went back to the chapter."""
    if how == "guide":
        return "resumed" if a.get("resume") else "other"
    return how if how in ENDINGS else "other"


# --- an ask on a fast run still being made ----------------------------------------------------

QUESTION_MIN_WORDS = 6  # a first line without a question mark counts as a question from this many words


def question_notes(run_root: Path, lines: list[dict], section: int, how: str | None = None) -> list[dict]:
    """A mid-chapter question, saved to listener_notes.md as kind "question" when its aside ends
    (the spec's "appended ... by the ask-by-voice aside log"): what the listener first said when it
    reads as a question (a question mark, or a request of QUESTION_MIN_WORDS words or more: "Tell me
    more about how the Romans mixed it"), and anything later that has a question mark. Nothing when
    the aside ended on "never mind" (`how`), or for a run that is not fast or is archived."""
    if not live(run_root) or how == "never_mind":
        return []
    said = [x["text"].strip() for x in lines if x.get("who") == "listener" and x.get("text", "").strip(". ")]
    first = [s for s in said[:1] if "?" in s or len(s.split()) >= QUESTION_MIN_WORDS]
    asked = first + [s for s in said[1:] if "?" in s]
    return [notes.add(run_root, s, "question", section=section) for s in asked]


def unfinished(ch, t: float, values: dict[str, str]) -> dict[str, str]:
    """An ask inside the rolling timing of a fast run still being made: ask.aside.Chapter `ch` knows
    only the sections narrated and timed so far, so its context says the chapter ends with them. The
    brief (archive.write_brief) lists every section: from it, the values that say otherwise (where,
    later, coming, and the outline in chapter). {} when the run is finished or not fast, or the brief
    lists nothing beyond the timing. An ask past the timing is `beyond`'s, which overrides these."""
    from ask.aside import _clock
    total, nseg = len(ch.brief_segments), len(ch.segments)
    if total <= nseg or not live(Path(ch.run)):
        return {}
    t = min(max(t, 0.0), ch.duration)
    s = ch.sents[ch.locate(t)]
    heard = (f"They have heard segments 1 to {s.seg} and this segment up to here; " if s.seg > 1 else
             "They have heard segment 1 and this segment up to here; " if s.seg == 1 else
             "They are in the first segment; ")
    rest = [ch.outline_line(k) for k in range(nseg, total)]
    later = [] if values.get("later", "").startswith("(nothing after that") else [values["later"]]
    coming = values.get("coming", "")
    out = {
        "where": (f"Segment {s.seg + 1} of {total}, \"{ch.seg_title(s.seg)}\": sentence {s.n} of {ch.counts[s.seg]} in "
                  f"that segment. {t / 60:.1f} minutes into the chapter ({_clock(t)}), which is still being made, "
                  "so its length is not known yet. " + heard
                  + (f"segments {s.seg + 2} to {total} are still to come." if s.seg + 2 < total else
                     f"segment {total} is still to come.")),
        "later": "\n".join(later + rest),
        "coming": ("(not recorded yet: the next lines are still being made)"
                   if coming.startswith("(nothing: the chapter has ended)") else coming),
        "chapter": "\n".join([values.get("chapter", "")] + rest),
    }
    return {k: v.replace("*", "") for k, v in out.items()}


def beyond(ch, t: float) -> dict | None:
    """An ask at a place the rolling timing does not reach yet: the listener is in a section whose
    narration is still running (ask.aside.Chapter `ch` holds only the sections narrated in full).
    From that section's pieces: {"section", "sentence_id", "values"}, where values replace the
    aside's where, just_said, coming, later and dossier. None when `t` is inside the timing, or the
    run is not a fast one still being made (the caller then clamps, as before)."""
    root = Path(ch.run)
    if t <= ch.duration + 0.5 or not live(root):
        return None
    a = ch.cfg["ask"]
    n = int(re.sub(r"\D", "", ch.segments[-1]["id"]) or len(ch.segments)) + 1
    at, heard, ahead, current, inside = ch.duration, [], [], n, 0
    while pieces := _pieces(root, n):
        for p in pieces:
            if at < t:
                heard.append(p)
                current, inside = n, (inside + 1 if current == n else 1)
            else:
                ahead.append(p)
            at += p.seconds
        n += 1
    if not heard:
        return None

    def window(ps, seconds: float, newest_first: bool) -> str:
        out, total = [], 0.0
        for p in (reversed(ps) if newest_first else ps):
            if total >= seconds:
                break
            out.append(p.text.strip())
            total += p.seconds
        return "\n\n".join(reversed(out) if newest_first else out)

    title = lambda k: ch.brief_segments[k - 1]["title"] if 0 < k <= len(ch.brief_segments) else f"part {k}"  # noqa: E731
    later = [ch.outline_line(k) for k in range(current, len(ch.brief_segments))]
    values = {
        "where": (f"Segment {current}, \"{title(current)}\", about {t / 60:.1f} minutes into the chapter. This "
                  "segment is still being recorded, so their exact place in it is not known: they paused "
                  "somewhere in the last paragraph of what they just heard."),
        "just_said": window(heard, a["just_said_secs"], True),
        "coming": window(ahead, a["coming_secs"], False) or "(not recorded yet: the next lines are still being made)",
        "later": "\n".join(later) or "(nothing after that: the chapter ends)",
        "dossier": ch.dossier_text(current - 1),
    }
    return {"section": current, "sentence_id": anchor(root, current, inside),
            "values": {k: v.replace("*", "") for k, v in values.items()}}


# --- the profile ------------------------------------------------------------------------------

# The page line a hand-off's transcript opens with, so the memory pass knows what kind of talk it was.
HANDOFF = {"check_in": ("checkin", "[A check-in while the listener heard the chapter \"{title}\": its next part "
                                   "was still being made, and the guide asked how the chapter was landing.]"),
           "ask": ("ask", "[A question the listener asked mid-chapter, in \"{title}\", while the chapter was "
                          "still being made: they tapped Ask and the guide answered.]")}


def remember(cfg: dict, a: dict, title: str, kind: str = "check_in") -> Path | None:
    """What was said at a check-in (or, kind "ask", in a mid-chapter question on a fast run) goes to
    the listener's profile as an onboarding conversation does: its lines are written as a session
    transcript (listener/sessions/<id>.jsonl, the format of guide.Session.record) and the memory
    pass runs on it in a daemon thread. Returns the transcript, or None when the listener said nothing.

    The id is the conversation's own (its start time, then "-checkin" or "-ask"), so sessions sort in
    the order they began, which is the order stages.memory keeps."""
    from stages import memory
    lines = a.get("lines") or []
    if not any(x.get("who") == "listener" and x.get("text", "").strip(". ") for x in lines):
        return None
    sessions = memory.listener_dir(cfg) / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    suffix, opening = HANDOFF[kind]
    path = sessions / f"{a['id']}-{suffix}.jsonl"
    stamp = a.get("started") or time.strftime("%Y-%m-%dT%H:%M:%S")
    recs = [{"t": stamp, "who": "session", "kind": kind, "run": Path(a["run"]).name, "section": a["section"]},
            {"t": stamp, "who": "page", "text": opening.format(title=title)}]
    recs += [{"t": stamp, **{k: v for k, v in x.items() if k != "s"}} for x in lines]
    base.write_atomic(path, "\n".join(json.dumps(x, ensure_ascii=False) for x in recs))
    threading.Thread(target=_pass, args=(cfg, path), name="checkin-memory", daemon=True).start()
    return path


WAIT_POLL_SECONDS = 2.0          # how often a deferred hand-off looks again
WAIT_MAX_SECONDS = 12 * 3600     # then it gives up: the server's catch-up at its next start takes it


def _pass(cfg: dict, path: Path) -> None:
    """The memory pass for a check-in's transcript. stages.memory skips a conversation older than one
    already remembered, so this never goes ahead of an earlier conversation that still waits for its
    pass: one in flight, or one still open (a typed chat left running). It waits in its daemon thread
    and runs as soon as nothing earlier is pending, so the earlier one's pass at its end comes first
    and this one is not left behind for a restart (where a newer conversation remembered meanwhile
    would make stages.memory skip it). After WAIT_MAX_SECONDS it gives up, as before, to the catch-up."""
    from stages import memory
    try:
        deadline = time.monotonic() + WAIT_MAX_SECONDS
        while True:
            with memory.LOCK:  # a pass in flight finishes first
                pass
            if path.with_suffix(".done").exists():
                return
            if not any(p.stem < path.stem for p in memory.pending_sessions(cfg)):
                break
            if time.monotonic() >= deadline:
                return
            time.sleep(WAIT_POLL_SECONDS)
        memory.after_session(cfg, path)
    except Exception as e:  # never take the server down over the profile
        print(f"  memory: {path.name}: {e}")
