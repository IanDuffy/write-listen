"""The signals/ folder of a listener, beside sessions/ and study/ and never inside them: memory.meeting() counts
any sessions/*.jsonl as a conversation with the guide, and demo/state.py reads study/*.jsonl as activity.

    signals/<stem>.jsonl          the turn records of one conversation: page records appended as they come
                                  (the latest line for a turn wins), recording records written by the replay
    signals/<stem>.marks.jsonl    Ian's marks on that conversation
    signals/audio/<live_id>.wav   GPT-Live recordings fetched for the replay, one per GPT-Live session (a guide
                                  conversation spans several: a rest closes one and a resume opens the next)

<stem> is the transcript's: 20261121-101500 for sessions/20261121-101500.jsonl, intro-<sid> and debrief-<sid>
for study/intro-<sid>.jsonl and study/debrief-<sid>.jsonl. A tester's reset or delete moves or removes the whole
listener folder, so signals go with it.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from . import record

FOLDER = "signals"
STEM = re.compile(r"(?:(?:intro|debrief)-)?\d{8}-\d{6}(?:-\d+)?")
LIVE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
MARK_MAX = 500          # characters in one mark
MARKS_MAX = 2000        # marks on one conversation
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_LOCK = threading.Lock()


def valid_stem(stem: str) -> bool:
    return bool(STEM.fullmatch(stem or ""))


def part_of(stem: str) -> str:
    return "intro" if stem.startswith("intro-") else "debrief" if stem.startswith("debrief-") else "guide"


def folder(listener: Path) -> Path:
    return Path(listener) / FOLDER


def path(listener: Path, stem: str) -> Path:
    if not valid_stem(stem):
        raise ValueError(f"not a conversation: {stem!r}")
    return folder(listener) / f"{stem}.jsonl"


def marks_path(listener: Path, stem: str) -> Path:
    return path(listener, stem).with_name(f"{stem}.marks.jsonl")


def audio_dir(listener: Path) -> Path:
    return folder(listener) / "audio"


def audio_path(listener: Path, live_id: str) -> Path:
    if not LIVE_ID.fullmatch(live_id or ""):
        raise ValueError(f"not a session id: {live_id!r}")
    return audio_dir(listener) / f"{live_id}.wav"


def _lock(p: Path) -> threading.Lock:
    with _LOCKS_LOCK:
        return _LOCKS.setdefault(str(p), threading.Lock())


def _read(p: Path) -> list[dict]:
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _ensure(p: Path) -> None:
    """signals/ inside the listener folder, never the listener folder itself: a tester reset or deleted while a
    line or a replay is in flight must stay gone (FileNotFoundError, which the callers catch)."""
    p.parent.mkdir(exist_ok=True)


def _write(p: Path, recs: list[dict]) -> None:
    _ensure(p)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
    os.replace(tmp, p)


def append(listener: Path, stem: str, rec: dict) -> None:
    p = path(listener, stem)
    with _lock(p):
        _ensure(p)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def rows(listener: Path, stem: str) -> list[dict]:
    """Every line of the conversation's file, as written."""
    p = path(listener, stem)
    with _lock(p):
        return _read(p)


def _order(r: dict):
    return (r.get("t") or "", 0 if r.get("source") == "recording" else 1, r.get("turn") or 0)


def merged(lines: list[dict]) -> list[dict]:
    """The records of one conversation: the latest line per (source, turn); a page record that a recording record
    replaces (its `replaces` lists page turns) dropped; time order."""
    latest: dict[tuple, dict] = {}
    for r in lines:
        if r.get("source") in record.SOURCES and isinstance(r.get("turn"), int):
            latest[(r["source"], r["turn"])] = r
    gone = {t for r in latest.values() if r["source"] == "recording" for t in r.get("replaces") or []}
    keep = [r for (src, turn), r in latest.items() if not (src == "page" and turn in gone)]
    return sorted(keep, key=_order)


def records(listener: Path, stem: str) -> list[dict]:
    return merged(rows(listener, stem))


def started(stem: str) -> str:
    """The stem's start time, for ordering: intro-, debrief- and guide stems sort together by when they began."""
    return stem.split("-", 1)[1] if stem.startswith(("intro-", "debrief-")) else stem


def stems(listener: Path) -> list[str]:
    """The conversations with a records file, oldest first."""
    d = folder(listener)
    if not d.is_dir():
        return []
    return sorted((p.stem for p in d.glob("*.jsonl") if valid_stem(p.stem)), key=lambda s: (started(s), s))


def visit(listener: Path, source: str | None = None) -> list[dict]:
    """Every record of the listener, all conversations, in time order (the norm carries across the visit)."""
    out = [r for s in stems(listener) for r in records(listener, s)]
    if source:
        out = [r for r in out if r.get("source") == source]
    return sorted(out, key=_order)


def recent(listener: Path, source: str, enough: int, min_words: int) -> list[dict]:
    """The newest records of one source, in time order: conversations read newest first until `enough` turns of
    min_words or more are in, then one more conversation (a conversation can run past the next one's start).
    What record.place reads (the norm's latest norm_turns, the trend's last two), without every file each time."""
    out, n, extra = [], 0, False
    for s in reversed(stems(listener)):
        got = [r for r in records(listener, s) if r.get("source") == source]
        out += got
        n += sum(1 for r in got if (r.get("words") or 0) >= min_words)
        if extra:
            break
        extra = n >= enough
    return sorted(out, key=_order)


def latest(listener: Path, stem: str, source: str = "page") -> dict | None:
    """The newest record of one source in a conversation (the highest turn)."""
    got = [r for r in records(listener, stem) if r.get("source") == source]
    return max(got, key=lambda r: r["turn"]) if got else None


def wait_turn(listener: Path, stem: str, turn: int, timeout_s: float, *, sleep=time.sleep,
              clock=time.monotonic) -> dict | None:
    """The page record for `turn` or a later one, waiting up to timeout_s for it to land: the page posts the line
    and asks for a look at about the same moment, and either request can reach the server first."""
    end = clock() + max(0.0, timeout_s)
    while True:
        rec = latest(listener, stem)
        if rec is not None and rec["turn"] >= turn:
            return rec
        if clock() >= end:
            return rec
        sleep(0.05)


def match_page(recs: list[dict], page: list[dict], slack_s: float = 1.0) -> list[dict]:
    """Recording records with `replaces`: the page turns they cover. Same GPT-Live session and overlapping times
    (within slack_s), or, when the page had no session clock, the same words. Each page turn goes to one."""
    taken: set[int] = set()
    out = []
    for r in recs:
        hit = []
        for p in page:
            if p["turn"] in taken or p.get("live_id") != r.get("live_id"):
                continue
            a, b = p.get("start_s"), p.get("end_s")
            if a is not None and b is not None and r.get("start_s") is not None and r.get("end_s") is not None:
                if a <= r["end_s"] + slack_s and b >= r["start_s"] - slack_s:
                    hit.append(p["turn"])
            elif record.plain(p.get("text") or "") and record.plain(p.get("text") or "") in record.plain(r.get("text") or ""):
                hit.append(p["turn"])
        taken.update(hit)
        out.append({**r, "replaces": hit})
    return out


def write_recordings(listener: Path, stem: str, recs: list[dict]) -> Path:
    """The replay's records for one conversation, in place of the replay's earlier ones; page records stay as
    they were (a recording record that covers a page turn says so in `replaces`)."""
    p = path(listener, stem)
    with _lock(p):
        page = [r for r in _read(p) if r.get("source") != "recording"]
        _write(p, page + list(recs))
    return p


def marks(listener: Path, stem: str) -> list[dict]:
    p = marks_path(listener, stem)
    with _lock(p):
        return _read(p)


def add_mark(listener: Path, stem: str, secs: float, text: str, t: str | None = None) -> dict:
    """Ian's mark on a moment ("lost them here"): seconds on the conversation's axis (from its first row)."""
    text = " ".join(str(text or "").split())[:MARK_MAX]
    if not text:
        raise ValueError("empty mark")
    secs = float(secs)
    if not (secs == secs) or secs < -86400 or secs > 86400 * 2:
        raise ValueError("mark outside the conversation")
    mark = {"t": t or time.strftime("%Y-%m-%dT%H:%M:%S"), "secs": round(secs, 2), "text": text}
    p = marks_path(listener, stem)
    with _lock(p):
        if len(_read(p)) >= MARKS_MAX:
            raise ValueError("too many marks")
        _ensure(p)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(mark, ensure_ascii=False) + "\n")
    return mark
