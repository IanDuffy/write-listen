"""What the interviewer is told for a tester's debrief (docs/tester-demo.md, "The interviewer"): the chapter
they heard, their moments, and what they did. Never the script or the guide's instructions.

    moments(listener_dir)         [{at_s, clock, words}]: each question they asked mid-chapter (a tap), in
                                  their words, at about where the reading was
    chapter_context(listener_dir) {run, title, summary, minutes_heard, minutes}
    activity(listener_dir)        what the tester did, from their own records (Ian's notes, 2026-11-18: his
                                  debrief said "Earlier, you chose not to cut in" though he had heard none of
                                  the piece). Facts only; demo/interviewer.account turns them into the lines
                                  the interviewer reads (wording in prompts/interviewer_notes.md `did_*`).
    add_event(listener_dir, ...)  one button press, appended to study/events.jsonl (POST /api/demo/event)

moments and chapter_context read the tester's newest live chapter (listeners/<lid>/chapters/<run>/ with
fast.json "voice": "live"). A live chapter has no word timing until its archive is built, so a moment's time
is estimated from where its paragraph and sentence sit in the text, at live.page.words_per_second (the
page's own estimate for its slider).

activity reads, and says nothing about what has no record:
    sessions/*.jsonl          the guide's conversation: how long they talked before the start_chapter tool
                              call, summed over stretches of unbroken talk (a rest, and the gap between two
                              visits, are not talk); "[The guide stepped back.]" (when one of their last two
                              lines since the piece was started or the last rest matches live.page.quiet_ask:
                              they asked for quiet), "[A new voice session: tap.]" (they tapped to talk),
                              get_status after a line of theirs (they asked how it was coming along)
    chapters/<run>/           fast_timeline.jsonl's first_audio (when the opening was ready), else
                              status.json; live/reading.jsonl and fast/player.json (whether any was heard)
    study/events.jsonl        the page's button presses: end_demo (with the phase at the tap), pause, seek,
                              ask (a tap to ask it something; their words, if any were heard, are a moment),
                              tap_talk; play is recorded and not read (whether any was heard is above)
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from datetime import datetime
from pathlib import Path

WORDS_PER_SECOND = 2.5  # live.page.words_per_second (voice/gpt_live/chapter.py WORDS_PER_SECOND)


def _read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def latest_chapter(listener_dir: Path) -> Path | None:
    """The tester's newest live chapter folder, by when it was started (fast.json is written first)."""
    found = []
    for d in (Path(listener_dir) / "chapters").glob("*"):
        mode = d / "fast.json"
        if d.is_dir() and mode.exists() and (_read_json(mode, {}) or {}).get("voice") == "live":
            found.append((mode.stat().st_mtime, d.name, d))
    return max(found)[2] if found else None


def _paragraphs(root: Path) -> list[dict]:
    """The chapter's paragraphs in reading order (voice/gpt_live/chapter.py), or [] if it can't be read."""
    from stages.common import Run
    from voice.gpt_live import chapter as live_chapter
    try:
        ch = live_chapter.paragraphs(Run(name=root.name, root=root))
    except Exception:  # noqa: BLE001 (a chapter that can't be split still has a debrief, without times)
        return []
    return [p for s in ch["sections"] for p in s["paragraphs"]]


def _words_before(paras: list[dict], pid: str | None, sentence: int) -> int | None:
    n = 0
    for p in paras:
        if p["id"] == pid:
            return n + sum(len(s.split()) for s in p["sentences"][:max(0, sentence)])
        n += len(p["text"].split())
    return None


def clock(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _wps(cfg: dict | None) -> float:
    try:
        return float(((cfg or {}).get("live") or {}).get("page", {}).get("words_per_second") or WORDS_PER_SECOND)
    except (TypeError, ValueError):
        return WORDS_PER_SECOND


def moments(listener_dir: Path, cfg: dict | None = None) -> list[dict]:
    """Each question asked mid-chapter (12_asks/asides.jsonl, kind "ask"): the first thing they said in it,
    at about where the reading was. A tap where they said nothing is not a moment."""
    root = latest_chapter(listener_dir)
    if root is None:
        return []
    asides = root / "12_asks" / "asides.jsonl"
    if not asides.exists():
        return []
    paras, wps = _paragraphs(root), _wps(cfg)
    out = []
    for line in asides.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("kind") != "ask":
            continue
        said = [str(x.get("text") or "").strip() for x in rec.get("transcript") or [] if x.get("who") == "listener"]
        said = [x for x in said if x]
        if not said:
            continue
        words = _words_before(paras, rec.get("paragraph"), int(rec.get("sentence") or 0))
        at = round(words / wps, 1) if words is not None else None
        out.append({"at_s": at, "clock": clock(at) if at is not None else None, "words": said[0]})
    return sorted(out, key=lambda m: (m["at_s"] is None, m["at_s"] or 0))


def chapter_context(listener_dir: Path, cfg: dict | None = None) -> dict:
    """{run, title, summary, minutes_heard, minutes} for the newest live chapter; Nones when there is none.
    The title is the outline's (it replaces the card's when it lands), the summary the card's (part D writes
    fast/card.json at start_chapter), else the outline's angle."""
    root = latest_chapter(listener_dir)
    if root is None:
        return {"run": None, "title": None, "summary": None, "minutes_heard": 0, "minutes": None}
    outline = _read_json(root / "05_outline.json", {}) or {}
    card = _read_json(root / "fast" / "card.json", {}) or {}
    status = _read_json(root / "status.json", {}) or {}
    player = _read_json(root / "fast" / "player.json", {}) or {}
    title = outline.get("title") or card.get("title") or status.get("title") or root.name
    summary = card.get("summary") or card.get("overview") or outline.get("angle") or ""
    wps = _wps(cfg)
    paras = _paragraphs(root)
    sections = outline.get("sections") or []
    all_locked = bool(sections) and all((root / "06_segments" / f"seg{int(s.get('n', 0)):02d}.md").exists()
                                        for s in sections)
    if all_locked and paras:
        minutes = sum(len(p["text"].split()) for p in paras) / wps / 60
    elif sections and all(s.get("target_words") for s in sections):
        minutes = sum(int(s["target_words"]) for s in sections) / wps / 60
    else:
        minutes = float(((cfg or {}).get("demo") or {}).get("chapter_minutes") or 10)
    # chapter_t is how far they heard, also when they tapped I'm done (state "done" too, part-way: the page's
    # chapter end reports the whole chapter); only a finished reading without one counts as heard to the end.
    try:
        heard = float(player["chapter_t"]) / 60 if player.get("chapter_t") is not None else None
    except (TypeError, ValueError):
        heard = None
    if heard is None:
        heard = minutes if player.get("state") == "done" else _read_minutes(root, wps)
    return {"run": root.name, "title": title, "summary": summary,
            "minutes_heard": round(min(heard, minutes), 1), "minutes": round(minutes, 1)}


def _read_minutes(root: Path, wps: float) -> float:
    """Minutes heard by the page's reading log alone (live/reading.jsonl: the most words heard of each
    paragraph), for a chapter whose player position never arrived."""
    best: dict[str, int] = {}
    for rec in _read_jsonl(root / "live" / "reading.jsonl"):
        if rec.get("id"):
            best[rec["id"]] = max(best.get(rec["id"], 0), len(str(rec.get("heard") or "").split()))
    return sum(best.values()) / wps / 60


# --- what they did (Ian's notes, 2026-11-18) ----------------------------------------------------------

EVENTS = ("end_demo", "play", "pause", "seek", "ask", "tap_talk")
PHASES = ("talk", "making", "resting", "reading", "aside", "paused", "ended")
EVENTS_FILE = "events.jsonl"
EVENTS_MAX_BYTES = 256_000   # a page can't fill the disk: past this, presses are no longer recorded
DETAIL_MAX = 200
STEPPED_BACK = "[The guide stepped back.]"            # page/live.js, when the guide rests
NEW_SESSION = re.compile(r"^\[A new voice session: ([a-z-]+)\.\]$")  # ... and when a rest ends (the reason)
# The listener asking for quiet, when config live.page.quiet_ask isn't there (tests with a bare cfg).
QUIET_ASK = r"\b(some quiet|quiet time|a (little|bit of) quiet|be quiet|stop talking|i'?ll just (wait|listen|sit))\b"
_EVENTS_LOCK = threading.Lock()


def _read_jsonl(p: Path) -> list[dict]:
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _ts(s) -> float | None:
    """Epoch seconds of an ISO time; one without an offset is local time (as the transcripts write it)."""
    try:
        return datetime.fromisoformat(str(s)).timestamp()
    except (TypeError, ValueError):
        return None


def add_event(listener_dir: Path, event: str, phase: str | None = None, chapter_t=None,
              detail: str | None = None) -> dict | None:
    """Append one button press to study/events.jsonl: {"t", "event", "phase", "chapter_t", "detail"}. Only a
    known event; an unknown phase is kept as None, the position as a finite number of seconds, the detail
    short. None (nothing written) for an unknown event or a file already at EVENTS_MAX_BYTES."""
    if event not in EVENTS:
        return None
    try:
        at = float(chapter_t) if chapter_t is not None else None
    except (TypeError, ValueError):
        at = None
    if at is not None and (not math.isfinite(at) or at < 0 or at > 86400):
        at = None
    text = " ".join(str(detail).split())[:DETAIL_MAX] if detail else None
    rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, "phase": phase if phase in PHASES else None,
           "chapter_t": round(at, 1) if at is not None else None, "detail": text or None}
    p = Path(listener_dir) / "study" / EVENTS_FILE
    with _EVENTS_LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > EVENTS_MAX_BYTES:
            return None
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def events(listener_dir: Path) -> list[dict]:
    return [e for e in _read_jsonl(Path(listener_dir) / "study" / EVENTS_FILE) if e.get("event") in EVENTS]


def _conversation(listener_dir: Path, cfg: dict | None) -> dict:
    """The guide's conversation, from sessions/*.jsonl in order."""
    lines: list[dict] = []
    for p in sorted((Path(listener_dir) / "sessions").glob("*.jsonl")):
        if p.name != "log.jsonl":
            lines += [{"who": "session"}] + _read_jsonl(p)  # each file is a visit of its own
    spoken = [x for x in lines if x.get("who") in ("guide", "listener") and str(x.get("text") or "").strip()]
    if not spoken:
        return {}
    try:
        quiet = re.compile(str(((cfg or {}).get("live") or {}).get("page", {}).get("quiet_ask") or QUIET_ASK), re.I)
    except re.error:
        quiet = re.compile(QUIET_ASK, re.I)
    started_at = None
    # Stretches of unbroken talk, [first line, last line]: a rest and the gap between two visits break one (a
    # tester back the next day had "talked for about 1441 minutes"; review, 2026-11-18).
    stretches: list[list[float]] = []
    broken = True
    since_back: list[str] = []   # what they said since the piece was started, or the last rest began or ended
    said_since_status = False
    out = {"quiet_asks": 0, "step_backs": 0, "taps": 0, "status_asks": 0}
    for x in lines:
        who, text = x.get("who"), str(x.get("text") or "").strip()
        t = _ts(x.get("t"))
        if who == "session" or (who == "page" and (text == STEPPED_BACK or NEW_SESSION.match(text))):
            broken = True
        elif who in ("guide", "listener") and text and t is not None:
            if broken:
                stretches.append([t, t])
            stretches[-1][1] = max(stretches[-1][1], t)
            broken = False
        if who == "listener" and text:
            since_back.append(text)
            said_since_status = True
        elif who == "tool" and x.get("name") == "start_chapter":
            if started_at is None and (x.get("result") or {}).get("started"):
                started_at = _ts(x.get("t"))
                out["quiet_asks"] = out["step_backs"] = 0  # only rests while the piece is made are told
                since_back = []  # ... and only what they said after it was started is an ask for quiet then
        elif who == "tool" and x.get("name") == "get_status":
            out["status_asks"] += said_since_status
            said_since_status = False
        elif who == "page" and text == STEPPED_BACK:
            out["step_backs"] += 1
            # Just before the rest, as the page keys it on their latest line (two: a turn can be split in the
            # transcript). Any line since the conversation began counted once: "I finally have some quiet", said
            # of their evening, became "They asked for quiet" at the guide's own step back (review, 2026-11-18).
            out["quiet_asks"] += any(quiet.search(said) for said in since_back[-2:])
            since_back = []
        elif who == "page" and (m := NEW_SESSION.match(text)):
            since_back = []
            out["taps"] += m.group(1) == "tap"
    out["started"] = started_at is not None
    if started_at is None:
        out["quiet_asks"] = out["step_backs"] = 0
    return {**out, "_stretches": stretches, "_started_at": started_at}


def _ready_at(root: Path) -> float | None:
    """When the piece's opening was ready to hear (fast_timeline.jsonl's first_audio)."""
    for rec in _read_jsonl(root / "fast_timeline.jsonl"):
        if rec.get("event") == "first_audio":
            return _ts(rec.get("ts"))
    return None


def activity(listener_dir: Path, cfg: dict | None = None, now: float | None = None) -> dict:
    """What the tester did, as their records show it; a key is None (or 0) when no record says.

    {"talk_s": seconds they talked with the guide before the piece was started (or in all, with none started),
     summed within visits, rests and the gaps between visits left out,
     "started": a piece was started, "quiet_asks": rests they asked for while it was made, "step_backs": rests
     in all, "taps": taps to talk, "status_asks": times they asked how it was coming along,
     "ready": the opening was ready when they ended (None: no record), "listened": any of it was heard,
     "pauses", "seeks": presses, "asks": taps to ask it something while it was read or paused (whether or not
     a question of theirs was then heard), "end": the End demo press {"phase", "chapter_t", "t"} or None,
     "stopped": the piece was stopped unfinished (status.json "stopped": End demo while it was made stops it,
     Ian's third run, 2026-11-19)}

    `now` (epoch seconds) is when they ended if no End demo press was recorded: the debrief's start."""
    listener_dir = Path(listener_dir)
    out = {"talk_s": None, "started": False, "quiet_asks": 0, "step_backs": 0, "taps": 0, "status_asks": 0,
           "ready": None, "listened": False, "pauses": 0, "seeks": 0, "asks": 0, "end": None, "stopped": False}
    talk = _conversation(listener_dir, cfg)
    out.update({k: v for k, v in talk.items() if not k.startswith("_")})
    started_at = talk.get("_started_at")
    evs = events(listener_dir)
    count = lambda name: sum(e["event"] == name for e in evs)  # noqa: E731
    out["taps"] = max(out["taps"], count("tap_talk"))
    out["pauses"], out["seeks"], out["asks"] = count("pause"), count("seek"), count("ask")
    ends = [e for e in evs if e["event"] == "end_demo"]
    if ends:
        out["end"] = {k: ends[-1].get(k) for k in ("phase", "chapter_t", "t")}
    ended_at = (_ts(ends[-1].get("t")) if ends else None) or now or time.time()
    root = latest_chapter(listener_dir)
    if root is not None:
        out["started"] = True
        status = _read_json(root / "status.json", {}) or {}
        out["stopped"] = status.get("state") == "stopped"
        if started_at is None:  # no start_chapter in the transcript: when the run itself says it began
            started_at = _ts(status.get("started")) or _ts((_read_json(root / "fast.json", {}) or {}).get("seed_set_at"))
        player = _read_json(root / "fast" / "player.json", {}) or {}
        ready_at = _ready_at(root)
        if ready_at is not None:
            out["ready"] = ready_at <= ended_at
        elif status:
            out["ready"] = bool(status.get("first_audio")) if "first_audio" in status or status.get("state") else None
        try:
            position = float(player.get("chapter_t") or 0)
        except (TypeError, ValueError):
            position = 0.0
        out["listened"] = bool(_read_jsonl(root / "live" / "reading.jsonl")) or position > 0 \
            or player.get("state") in ("playing", "paused", "waiting", "done")
        if out["listened"]:
            out["ready"] = True
    # How long they talked first: the stretches of talk up to the piece's start (the one it fell in ends there),
    # or (none started) all of them.
    parts = talk.get("_stretches") or []
    if out["started"]:
        parts = [x for x in parts if started_at is not None and x[0] <= started_at]
        parts = parts[:-1] + [[parts[-1][0], started_at]] if parts else []
    out["talk_s"] = round(sum(b - a for a, b in parts), 1) if parts else None
    return out
