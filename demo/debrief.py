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
    events(listener_dir)          the presses (never audio_route: nothing that reads presses sees it)
    audio_routes(listener_dir)    the page's audio_route records (page/speaker.js: the phone, its audio inputs and
                                  outputs, the mic in use; Ian's Android phone, 2026-11-21), for the admin's transcript

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
                              tap_talk; play is recorded and not read (whether any was heard is above).
                              audio_route records (not presses) are left out of all of it
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from datetime import datetime, timedelta
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

PRESSES = ("end_demo", "play", "pause", "seek", "ask", "tap_talk")
AUDIO_ROUTE = "audio_route"   # not a press: page/speaker.js's record of the phone's audio route (data, below)
EVENTS = PRESSES + (AUDIO_ROUTE,)
AUDIO_MAX_BYTES = 128_000     # audio_route records stop here, so presses always keep room in the file
AUDIO_REASONS = ("mic", "speakerphone", "devicechange", "playing", "headset")
AUDIO_RESULTS = ("switched", "no label", "headset present", "failed", "moved")
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


def _plain(v, n: int = 80):
    """A JSON scalar kept as it is (a string clipped to n characters); anything else is None."""
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return v if math.isfinite(v) else None
    if isinstance(v, str):
        return " ".join(v.split())[:n]
    return None


def audio_fields(data) -> dict:
    """An audio_route record's data as page/speaker.js sends it, bounded: only its own fields, short strings,
    at most 12 labels of each kind."""
    if not isinstance(data, dict):
        return {}
    labels = lambda v: [_plain(x, 60) for x in v[:12] if isinstance(x, str)] if isinstance(v, list) else []  # noqa: E731
    out = {"reason": data.get("reason") if data.get("reason") in AUDIO_REASONS else None,
           "result": data.get("result") if data.get("result") in AUDIO_RESULTS else None,
           "ua": _plain(data.get("ua"), 300) if isinstance(data.get("ua"), str) else None,
           "inputs": labels(data.get("inputs")), "outputs": labels(data.get("outputs")),
           "track_label": _plain(data.get("track_label")) if isinstance(data.get("track_label"), str) else None,
           "live_mic_tracks": data.get("live_mic_tracks") if isinstance(data.get("live_mic_tracks"), int)
           and not isinstance(data.get("live_mic_tracks"), bool) and 0 <= data["live_mic_tracks"] < 100 else None}
    hints = data.get("ua_hints")
    out["ua_hints"] = {k: _plain(hints.get(k), 40) for k in ("model", "platform", "android", "chrome")} \
        if isinstance(hints, dict) else None
    s = data.get("settings")
    out["settings"] = {k: _plain(s.get(k)) for k in ("deviceId", "echoCancellation", "noiseSuppression",
                                                     "autoGainControl", "sampleRate")} if isinstance(s, dict) else None
    ec = data.get("ec_modes")
    out["ec_modes"] = [_plain(x, 20) for x in ec[:6]] if isinstance(ec, list) else None
    return out


def add_event(listener_dir: Path, event: str, phase: str | None = None, chapter_t=None,
              detail: str | None = None, data=None) -> dict | None:
    """Append one button press to study/events.jsonl: {"t", "event", "phase", "chapter_t", "detail"}. Only a
    known event; an unknown phase is kept as None, the position as a finite number of seconds, the detail
    short. None (nothing written) for an unknown event or a file already at EVENTS_MAX_BYTES.

    audio_route (page/speaker.js) is no press: {"t", "event", "data"}, its data through audio_fields, and nothing
    written once the file is at AUDIO_MAX_BYTES."""
    if event not in EVENTS:
        return None
    if event == AUDIO_ROUTE:
        rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, "data": audio_fields(data)}
        p = Path(listener_dir) / "study" / EVENTS_FILE
        with _EVENTS_LOCK:
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists() and p.stat().st_size > AUDIO_MAX_BYTES:
                return None
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec
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
    """The presses (PRESSES), in order; audio_route records are left out."""
    return [e for e in _read_jsonl(Path(listener_dir) / "study" / EVENTS_FILE) if e.get("event") in PRESSES]


def audio_routes(listener_dir: Path) -> list[dict]:
    """The audio_route records, in order."""
    return [e for e in _read_jsonl(Path(listener_dir) / "study" / EVENTS_FILE)
            if e.get("event") == AUDIO_ROUTE and isinstance(e.get("data"), dict)]


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


# --- the visit brief (docs/debrief-earpiece-design.md; Ian, 2026-11-22) -------------------------------------------
#
# What the debrief's interviewer and its earpiece know of the visit beyond the account of what they did: what they
# asked for and why, their own lines to the voice, the piece's outline (titles and what each part covers, never the
# script) with what they heard of it, and their questions mid-piece with the voice's answers. Ian's choice: "their
# words + outline". Code only: no call, no wait as the debrief starts. In the debrief's own words ("the piece", "the
# voice"), and at most BRIEF_CHARS.

BRIEF_CHARS = 3500
LINE_CHARS = 200          # one of their lines, or one answer
THEIR_LINES_CHARS = 1200  # all their lines to the voice (the oldest go first)
VISIT_BEFORE_SECONDS = 1800   # conversations up to this long before the piece was begun count as this visit
_PLACEHOLDERS = {"(not said)", "(nothing)", "(the topic above is in their words)"}


def _cut(text: str, n: int = LINE_CHARS) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


def seed_parts(root: Path) -> dict:
    """00_source/seed.md as {topic, said, why} (prompts/seed_file.md's headings); placeholders left out."""
    p = root / "00_source" / "seed.md"
    if not p.exists():
        return {}
    parts, key = {}, None
    names = {"topic": "topic", "what the listener said": "said", "why it matters": "why"}
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            head = line[3:].strip().lower()
            key = next((v for k, v in names.items() if head.startswith(k)), None)
            continue
        if key and line.strip():
            parts[key] = (parts.get(key, "") + " " + line.strip()).strip()
    return {k: v for k, v in parts.items() if v and v not in _PLACEHOLDERS}


def _when(rec: dict) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(rec.get("t") or rec.get("started") or ""))
    except ValueError:
        return None
    return t.replace(tzinfo=None) if t.tzinfo else t


def their_lines(listener_dir: Path, since: datetime | None) -> list[str]:
    """Their own lines to the voice in this visit's conversations (sessions/*.jsonl), oldest first: the
    conversations begun from `since` on, or only the newest that has a line of theirs when there is no `since`."""
    convs = []
    for p in sorted((Path(listener_dir) / "sessions").glob("*.jsonl")):
        if p.name == "log.jsonl":
            continue
        rows = _read_jsonl(p)
        first = next((t for r in rows if (t := _when(r))), None)
        said = [str(r.get("text") or "").strip() for r in rows if r.get("who") == "listener"]
        said = [x for x in said if x]
        if said:
            convs.append((first, said))
    if since is not None:
        picked = [said for first, said in convs if first is None or first >= since]
    else:
        picked = convs[-1:] and [convs[-1][1]]
    lines = [_cut(x) for said in picked for x in said]
    while lines and sum(len(x) + 3 for x in lines) > THEIR_LINES_CHARS:
        lines.pop(0)
    return lines


def _heard_sections(root: Path) -> dict[int, str]:
    """{section n: "heard" | "partly heard"} from live/reading.jsonl (paragraph ids s<n>-p<kk>): a paragraph with any
    words heard counts toward its section, which is "heard" only when every paragraph had a whole (uncut) reading.
    The page logs a cut row with nothing heard whenever a reading stops (End demo, pause, a tap): that is not heard."""
    read, whole = set(), set()
    for r in _read_jsonl(root / "live" / "reading.jsonl"):
        pid = str(r.get("id") or "")
        if pid and str(r.get("heard") or "").split():
            read.add(pid)
            if not r.get("cut"):
                whole.add(pid)
    if not read:
        return {}
    by_section: dict[int, set] = {}
    for p in _paragraphs(root):
        m = re.match(r"s(\d+)-p", str(p.get("id") or ""))
        if m:
            by_section.setdefault(int(m.group(1)), set()).add(p["id"])
    out = {}
    for pid in read:
        m = re.match(r"s(\d+)-p", pid)
        if not m:
            continue
        n = int(m.group(1))
        every = by_section.get(n)
        out[n] = "heard" if every and every <= whole else "partly heard"
    return out


def _asides(root: Path) -> list[tuple[str, str]]:
    """Each question asked mid-piece: (their first line, the voice's first answer after it)."""
    out = []
    for rec in _read_jsonl(root / "12_asks" / "asides.jsonl"):
        if rec.get("kind") != "ask":
            continue
        lines = [(x.get("who"), str(x.get("text") or "").strip()) for x in rec.get("transcript") or []]
        i = next((k for k, (w, t) in enumerate(lines) if w == "listener" and t), None)
        if i is None:
            continue
        answer = next((t for w, t in lines[i + 1:] if w in ("guide", "assistant", "voice") and t), "")
        out.append((lines[i][1], answer))
    return out


def visit_brief(listener_dir: Path, cfg: dict | None = None, stopped: bool = False) -> str:
    """The debrief's {{visit}}: what they asked for, what they said, the piece's outline and what they heard of it,
    and their questions mid-piece with the voice's answers. "" when there is nothing to tell."""
    root = latest_chapter(listener_dir)
    parts: list[str] = []
    status = _read_json(root / "status.json", {}) if root else {}
    begun = _when({"t": (status or {}).get("started")}) if root else None
    since = begun - timedelta(seconds=VISIT_BEFORE_SECONDS) if begun else None
    rehearsal = bool(root and (_read_json(root / "fast.json", {}) or {}).get("rehearsal"))

    if root:
        seed = seed_parts(root)
        asked = [f'They asked for a piece about: {_cut(seed["topic"])}'] if seed.get("topic") else []
        if seed.get("said"):
            asked.append(f'In their words: "{_cut(seed["said"])}"')
        if seed.get("why"):
            asked.append(f"Why it mattered to them: {_cut(seed['why'])}")
        if asked:
            parts.append("WHAT THEY ASKED FOR\n" + "\n".join(f"- {x}" for x in asked))

    lines = their_lines(listener_dir, since)
    if lines:
        parts.append("WHAT THEY SAID TO THE VOICE (their own words, oldest first)\n"
                     + "\n".join(f'- "{x}"' for x in lines))

    if root and rehearsal:
        parts.append("THE PIECE\n- The piece they heard was a stand-in replayed for this test, not made from what they"
                     " asked for. Don't ask about its subject as if it were theirs, and don't point out the difference.")
    elif root:
        outline = _read_json(root / "05_outline.json", {}) or {}
        ctx = chapter_context(listener_dir, cfg)
        heard = _heard_sections(root)
        piece = [f'Title: "{_cut(ctx.get("title") or outline.get("title") or root.name, 120)}"']
        sections = [s for s in outline.get("sections") or [] if isinstance(s, dict)]
        if sections:
            for s in sections:
                n = int(s.get("n") or 0)
                mark = heard.get(n, "not heard")
                piece.append(f'Part {n}, "{_cut(s.get("title"), 80)}" ({mark}): {_cut(s.get("lands"), 220)}')
        elif ctx.get("summary"):
            piece.append(f"What it was to cover (no outline yet): {_cut(ctx['summary'], 300)}")
        if stopped or (status or {}).get("state") == "stopped":
            piece.append("It was stopped before it was finished.")
        parts.append("THE PIECE\n" + "\n".join(f"- {x}" for x in piece))

    if root and not rehearsal:
        asks = _asides(root)
        if asks:
            parts.append("THEIR QUESTIONS WHILE IT WAS READ\n" + "\n".join(
                f'- They asked: "{_cut(q)}"' + (f' The voice answered: "{_cut(a)}"' if a else "") for q, a in asks))

    text = "\n\n".join(parts)
    return text if len(text) <= BRIEF_CHARS else text[: BRIEF_CHARS - 1].rsplit("\n", 1)[0] + "\n…"
