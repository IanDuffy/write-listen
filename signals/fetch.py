"""Keeps the GPT-Live recordings the replay measures (package 1): OpenAI deletes them 30 days after the session.

One guide conversation (sessions/<id>.jsonl) spans several GPT-Live sessions, since every rest closes one and a
resume opens another; each starts with a session row carrying its live_id. The interviewer's intro and debrief
(study/intro-<sid>.jsonl, study/debrief-<sid>.jsonl) are one session each. Every session with listener audio
gets signals/audio/<live_id>.wav: reused from a copy already on disk (a chapter's live/<live_id>.wav, the
interviewer's study/<live_id>.wav; hard-linked when it can be), else downloaded. The interviewer's
study/<live_id>.mp3 is lossy, so the WAV is still fetched while OpenAI has it; the mp3 is the fallback.

A command Ian runs (wl signals fetch); nothing here runs from the server.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from stages.common import StageError
from voice.gpt_live import session as live_session
from voice.gpt_live.earpiece_history import SESSION_ID

from . import store

KEEP_DAYS = 30   # OpenAI keeps a stored session's recording this long
MODES = ("intro", "debrief")


class Gone(Exception):
    """The listener's folder vanished or was replaced mid-run (a tester's reset or delete)."""


def jsonl(p: Path) -> list[dict]:
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


def conversations(listener: Path) -> list[dict]:
    """Every conversation with GPT-Live sessions, in the order they began: {stem, part, path, rows, sessions},
    each session {live_id, t (its row's wall time), s (the interviewer's seconds, or None), rows (until the next
    session)}. The library's sessions (a session row with "mode": intro, change, ask; never stored) are left out."""
    listener = Path(listener)
    files = []
    d = listener / "sessions"
    if d.is_dir():
        files += [(p, "guide") for p in sorted(d.glob("*.jsonl")) if SESSION_ID.fullmatch(p.stem)]
    d = listener / "study"
    if d.is_dir():
        files += [(p, m) for m in MODES for p in sorted(d.glob(f"{m}-*.jsonl")) if store.valid_stem(p.stem)]
    out = []
    for p, part in files:
        rows = jsonl(p)
        sessions, cur, skip = [], None, False
        for r in rows:
            if r.get("who") == "session" and r.get("live_id"):
                skip = part == "guide" and bool(r.get("mode")) or not store.LIVE_ID.fullmatch(str(r["live_id"]))
                if skip:
                    continue
                cur = next((x for x in sessions if x["live_id"] == str(r["live_id"])), None)
                if cur is None:
                    cur = {"live_id": str(r["live_id"]), "t": r.get("t"), "s": r.get("s"), "rows": []}
                    sessions.append(cur)
                continue
            if cur is not None and not skip:
                cur["rows"].append(r)
        if sessions:
            out.append({"stem": p.stem, "part": part, "path": p, "rows": rows, "sessions": sessions})
    return sorted(out, key=lambda c: str(c["sessions"][0]["t"] or ""))


def targets(listener: Path) -> list[dict]:
    """Every GPT-Live session with listener audio, oldest first (they expire first):
    {live_id, stem, part, t, path_hint (the transcript it came from, relative to the listener's folder)}."""
    listener = Path(listener)
    out, seen = [], set()
    for c in conversations(listener):
        for s in c["sessions"]:
            if s["live_id"] in seen:
                continue
            seen.add(s["live_id"])
            out.append({"live_id": s["live_id"], "stem": c["stem"], "part": c["part"], "t": s["t"],
                        "path_hint": str(c["path"].relative_to(listener))})
    return sorted(out, key=lambda x: str(x["t"] or ""))


def is_wav(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            head = f.read(12)
    except OSError:
        return False
    return len(head) == 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE" and p.stat().st_size > 44


def lossless_copy(listener: Path, live_id: str) -> Path | None:
    """A WAV of this session already on disk outside signals/: the interviewer's, or a chapter's (the archive
    fetches the whole session a chapter was read in, conversation included)."""
    listener = Path(listener)
    for p in [listener / "study" / f"{live_id}.wav", *sorted(listener.glob(f"chapters/*/live/{live_id}.wav"))]:
        if is_wav(p):
            return p
    return None


def audio_for(listener: Path, live_id: str) -> Path | None:
    """The recording to measure: signals/audio/, else a lossless copy elsewhere, else the interviewer's mp3."""
    listener = Path(listener)
    p = store.audio_path(listener, live_id)
    if is_wav(p):
        return p
    p = lossless_copy(listener, live_id)
    if p:
        return p
    p = listener / "study" / f"{live_id}.mp3"
    return p if p.is_file() and p.stat().st_size > 0 else None


def age_days(t, now: datetime | None = None) -> float | None:
    try:
        then = datetime.fromisoformat(str(t))
    except ValueError:
        return None
    if then.tzinfo:
        then = then.astimezone().replace(tzinfo=None)
    return max(0.0, ((now or datetime.now()) - then).total_seconds() / 86400)


def missing_words(status, age: float | None) -> str:
    head = f"OpenAI has no recording (HTTP {status})" if status else "OpenAI gave no recording"
    if age is not None and age >= KEEP_DAYS:
        return f"{head}: the session began {age:.0f} days ago, past the {KEEP_DAYS} days OpenAI keeps recordings."
    when = f" The session began {age:.0f} days ago." if age is not None else ""
    return (f"{head}.{when} OpenAI keeps recordings {KEEP_DAYS} days; a session not stored (live.store off) "
            "or not finished has none.")


def _ident(listener: Path) -> int:
    try:
        return listener.stat().st_ino
    except OSError:
        raise Gone() from None


def _still(listener: Path, ident: int) -> bool:
    try:
        return listener.is_dir() and listener.stat().st_ino == ident
    except OSError:
        return False


def _place(listener: Path, ident: int, src: Path, dest: Path, *, link: bool) -> None:
    """src into dest through a .part file beside it, only into the folder this run started on: signals/ is made
    inside the listener's folder, never the folder itself, so a reset or deleted tester stays gone."""
    if not _still(listener, ident):
        raise Gone()
    try:
        store.folder(listener).mkdir(exist_ok=True)
        store.audio_dir(listener).mkdir(exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        part.unlink(missing_ok=True)
        if link:
            try:
                os.link(src, part)
            except OSError:
                shutil.copy2(src, part)
        else:
            shutil.move(str(src), str(part))
    except FileNotFoundError:
        raise Gone() from None
    if not _still(listener, ident):
        part.unlink(missing_ok=True)
        raise Gone()
    os.replace(part, dest)


def _rel(listener: Path, p: Path) -> str:
    try:
        return str(p.relative_to(listener))
    except ValueError:
        return str(p)


def fetch(listener: Path, cfg: dict, *, client=None, now: datetime | None = None) -> list[dict]:
    """Every target's recording into signals/audio/<live_id>.wav. One result per target:
    {live_id, stem, part, status, detail, bytes}, status "have" (already kept, or reused from a copy on disk, or
    only the interviewer's mp3 when the WAV is gone), "fetched", "missing" (OpenAI has none, a 404: why, in plain
    words) or "error" (worth trying again: the key, the network, any other answer). A missing or refused key
    stops the requests for the rest of the run."""
    listener = Path(listener)
    if not listener.is_dir():
        return []
    ident = _ident(listener)
    out: list[dict] = []
    no_key = None
    todo = targets(listener)
    for k, tg in enumerate(todo):
        base = {"live_id": tg["live_id"], "stem": tg["stem"], "part": tg["part"]}
        try:
            got = _one(listener, ident, cfg, tg, client, now, no_key)
        except Gone:
            out += [{"live_id": x["live_id"], "stem": x["stem"], "part": x["part"], "status": "error",
                     "detail": "the listener's folder is gone (reset or deleted); nothing written", "bytes": None}
                    for x in todo[k:]]
            break
        except OSError as e:  # the disk: this one is an error, the others may not be
            got = {"status": "error", "detail": f"{type(e).__name__}: {e}", "bytes": None}
        no_key = got.pop("no_key", no_key)
        out.append({**base, **got})
    return out


def _one(listener: Path, ident: int, cfg: dict, tg: dict, client, now, no_key) -> dict:
    live_id = tg["live_id"]
    dest = store.audio_path(listener, live_id)
    if is_wav(dest):
        return {"status": "have", "detail": "already kept", "bytes": dest.stat().st_size}
    src = lossless_copy(listener, live_id)
    if src:
        _place(listener, ident, src, dest, link=True)
        return {"status": "have", "detail": f"reused {_rel(listener, src)}", "bytes": dest.stat().st_size}
    mp3 = listener / "study" / f"{live_id}.mp3"
    mp3 = mp3 if mp3.is_file() and mp3.stat().st_size > 0 else None

    def fallback(why: str, status: str) -> dict:
        if mp3:
            return {"status": "have", "detail": f"only {_rel(listener, mp3)} (lossy): {why}",
                    "bytes": mp3.stat().st_size}
        return {"status": status, "detail": why, "bytes": None}

    if no_key:
        return fallback(no_key, "error")
    why: dict = {}
    with tempfile.TemporaryDirectory(prefix="wl-signals-") as tmp:
        part = Path(tmp) / f"{live_id}.wav.part"
        try:
            got = live_session.recording(cfg, live_id, part, client=client, why=why)
        except StageError as e:  # no OpenAI key: every download would fail the same way
            return {**fallback(str(e), "error"), "no_key": str(e)}
        except Exception as e:  # noqa: BLE001 (network: this one is an error, the others may not be)
            return fallback(f"{type(e).__name__}: {e}", "error")
        if not got:
            st = why.get("status")
            if st in (404, 410):            # OpenAI has none: not stored, not finished, or past its 30 days
                return fallback(missing_words(st, age_days(tg.get("t"), now)), "missing")
            if st in (401, 403):            # the key: every download would fail the same way
                msg = f"OpenAI refused the key (HTTP {st}); check OPENAI_API_KEY and run wl signals fetch again"
                return {**fallback(msg, "error"), "no_key": msg}
            # a rate limit, OpenAI's own trouble, an empty answer: it may well be there next time
            return fallback(f"OpenAI answered HTTP {st} without the recording; run wl signals fetch again", "error")
        if not is_wav(part):
            return fallback(f"OpenAI's answer (HTTP {why.get('status')}) is not a WAV file", "error")
        _place(listener, ident, part, dest, link=False)
    return {"status": "fetched", "detail": None, "bytes": dest.stat().st_size}
