"""The admin's Listener Signals timeline (Ian's build plan, 2026-11-21, "Admin view"), mounted by server.py.

    GET  /admin/signals                            page/admin_signals.html (?lid=<tester id or own>&session=<stem>,
                                                   or session=visit for the whole visit)
    GET  /api/admin/signals/{lid}                  the listener's conversations, newest first: the guide's (those
                                                   with GPT-Live sessions; not the library's), the intro and the
                                                   debrief, and any conversation with a signals file
    GET  /api/admin/signals/{lid}/visit            the whole visit on one axis (seconds since it began): its parts,
                                                   the chapter read aloud, every record, mark and earpiece look
    GET  /api/admin/signals/{lid}/{stem}           one conversation on its own axis (seconds since its first row)
    POST /api/admin/signals/{lid}/{stem}/mark      {secs, text}: Ian's mark, on that conversation's axis
    GET  /api/admin/signals/{lid}/audio/{live_id}  a GPT-Live session's recording (range requests: the page seeks)

Guarded like every admin route: demo/routes.py's guard, called inline first (as a Depends it would run in a copied
context, and "own" could resolve to the folder of a tester whose cookie Ian's browser carries). Every record is
placed against the whole visit (annotated(): record.annotate over each source's records), so the norm carries from the
intro into the guide and the debrief, and a page record shows the norm it was given live even after a replay hid
the page turns it was built from. A record sits on an axis at its GPT-Live session's start (the transcript's session row for
its live_id: 1 s resolution, written as OpenAI answers, a moment before the media connects) plus its start_s, or
at its own wall time when it has no session clock.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from demo import state, testers, visit
from demo.routes import NO_STORE, _tester_or_404, guard
from stages import common
from stages.common import ROOT
from voice.gpt_live import earpiece_history as EH

from . import record, store

PAGE = ROOT / "page" / "admin_signals.html"
AUDIO_TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg"}
READING_GAP_S = 30      # paragraphs read closer together than this are one stretch of the chapter
LINK_SKIP = 8           # a turn's first line is at most this many listener lines past the earliest it could be
LINK_EARLY_S = 5        # a line is written when it ends: never this long before its turn began
# ... and at most this long after its turn ended: the replay gives a turn rows up to lag_s + lag_search's top +
# max_off_s after it (signals.recording: 2.5 + 4 + 6 s), and a record's session zero is a whole second, written a
# moment before the recording starts. A page turn's rows come a few seconds after it.
LINK_LATE_S = 15


class Mark(BaseModel):
    secs: float
    text: str = Field(max_length=2000)   # store.add_mark keeps MARK_MAX characters


# --- reading the folder ---------------------------------------------------------------------------

def _when(s) -> datetime | None:
    """A naive local time from an ISO string (one with an offset converted to local time), as demo/visit.py reads."""
    try:
        t = datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    return t.astimezone().replace(tzinfo=None) if t.tzinfo else t


def _iso(t: datetime | None) -> str | None:
    return t.isoformat(timespec="seconds") if t else None


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def transcript_path(folder: Path, stem: str) -> Path:
    return folder / ("sessions" if store.part_of(stem) == "guide" else "study") / f"{stem}.jsonl"


def _rows(p: Path) -> list[dict]:
    return EH._rows(p) if p.is_file() else []


def _library(rows: list[dict]) -> bool:
    """The library's voice sessions (overnight/live.py) share sessions/ with the guide; their session rows carry
    a mode and they keep no recording."""
    return any(r.get("who") == "session" and r.get("mode") for r in rows)


def _anchors(rows: list[dict]) -> dict[str, datetime]:
    """live_id -> the wall time of its session row: the zero of that session's recording."""
    out: dict[str, datetime] = {}
    for r in rows:
        if r.get("who") == "session" and r.get("live_id") and (t := _when(r.get("t"))):
            out.setdefault(str(r["live_id"]), t)
    return out


def _zero(rows: list[dict], recs: list[dict]) -> datetime | None:
    """A conversation's axis starts at its transcript's first timed row (as earpiece_history counts), else at its
    first record (a signals file whose transcript is gone)."""
    t = next((t for t in (_when(r.get("t")) for r in rows) if t), None)
    if t is None:
        t = min((t for t in (_when(r.get("t")) for r in recs) if t), default=None)
    return t


def audio_file(folder: Path, live_id: str) -> Path | None:
    """The recording of one GPT-Live session, wherever it was kept: signals/audio/ (wl signals fetch), study/ (the
    interviewer's, demo/interviewer.py), or a chapter's live/ (voice/gpt_live/archive.py)."""
    try:
        p = store.audio_path(folder, live_id)
    except ValueError:
        return None
    if p.is_file():
        return p
    for ext in (".mp3", ".wav"):
        q = folder / "study" / f"{live_id}{ext}"
        if q.is_file():
            return q
    return next(iter(sorted((folder / "chapters").glob(f"*/live/{live_id}.wav"))), None)


def _seconds(folder: Path) -> dict[str, float]:
    """live_id -> connected seconds from the usage logs (about the recording's length)."""
    out: dict[str, float] = {}
    for p in (folder / "sessions" / "log.jsonl", folder / "study" / "log.jsonl"):
        for r in state.read_jsonl(p):
            if r.get("live_id") and _num(r.get("seconds")):
                out[str(r["live_id"])] = float(r["seconds"])
    return out


def _conversation_rows(folder: Path) -> dict[str, list[dict]]:
    """stem -> transcript rows for every conversation the timeline shows."""
    out: dict[str, list[dict]] = {}
    for p in EH._files(folder):
        rows = EH._rows(p)
        if any(r.get("who") == "session" and r.get("live_id") for r in rows) and not _library(rows):
            out[p.stem] = rows
    study = folder / "study"
    if study.is_dir():
        for p in study.glob("*.jsonl"):
            if store.valid_stem(p.stem) and store.part_of(p.stem) != "guide":
                out[p.stem] = EH._rows(p)
    for s in store.stems(folder):
        if s not in out:
            out[s] = _rows(transcript_path(folder, s))
    return out


# --- placing things on an axis ----------------------------------------------------------------------

def _start_end(rec: dict, anchors: dict) -> tuple[datetime | None, datetime | None]:
    """A record's wall-clock start and end: its session's zero plus start_s/end_s, else its own t plus its length."""
    a = anchors.get(rec.get("live_id") or "")
    if a is not None and _num(rec.get("start_s")):
        start = a + timedelta(seconds=rec["start_s"])
        if _num(rec.get("end_s")):
            return start, a + timedelta(seconds=rec["end_s"])
    else:
        start = _when(rec.get("t"))
        if start is None:
            return None, None
    dur = rec.get("dur_ms")
    return start, start + timedelta(milliseconds=dur) if _num(dur) and dur > 0 else start


def _secs(t: datetime | None, t0: datetime | None) -> float | None:
    return round((t - t0).total_seconds(), 2) if t and t0 else None


def _placed(recs: list[dict], anchors: dict, t0: datetime | None) -> list[dict]:
    out = []
    for r in recs:
        a, b = _start_end(r, anchors)
        out.append({**r, "secs": _secs(a, t0), "end_secs": _secs(b, t0), "lines": []})
    return out


def _piece(line: str, text: str) -> bool:
    """Is `line` one of the pieces record.join made `text` from? Found where a piece starts (the text's start, after
    a space, or a piece opening with a tag's close) and ends (the text's end, a space, or the next piece's "]")."""
    i = text.find(line)
    while i >= 0:
        e = i + len(line)
        if (i == 0 or text[i - 1].isspace() or line.startswith("]")) \
                and (e == len(text) or text[e].isspace() or text[e] == "]"):
            return True
        i = text.find(line, i + 1)
    return False


def _belongs(line: str, text: str) -> bool:
    """Is this transcript line part of the turn whose joined text is `text`? Its words in order, as whole words ("no"
    is not in "know"), or the line itself as one of the text's pieces: a tag split across lines ("[inhale" then
    "]Yeah", "[tongue" "click" "]so") leaves pieces with no words outside the tag."""
    line = (line or "").strip()
    if not line:
        return False
    text = text or ""
    if _piece(line, text):
        return True
    p = record.plain(line)
    return bool(p) and f" {p} " in f" {record.plain(text)} "


def link(recs: list[dict], items: list[dict]) -> None:
    """Each record's listener lines (rec["lines"]: item indexes) and each linked line's record (item["rec"]), in
    time order: a turn's lines are the rows the page posted for it, so their words are in the record's text."""
    lines = [x for x in items if x.get("kind") == "line" and x.get("who") == "listener"]

    def early(r: dict, x: dict) -> bool:
        return r.get("secs") is not None and x.get("secs") is not None and x["secs"] < r["secs"] - LINK_EARLY_S

    def fits(r: dict, x: dict) -> bool:
        if early(r, x):
            return False
        # A later line with the same words is another turn's, one with no record of its own (typed, or a quiet
        # "Yeah" the replay matched to no sound). Only a record that knows its length has an end to count from.
        if r.get("end_secs") is not None and _num(r.get("dur_ms")) and x.get("secs") is not None \
                and x["secs"] > r["end_secs"] + LINK_LATE_S:
            return False
        return _belongs(x.get("text"), r.get("text") or "")

    k = 0
    for ri, r in enumerate(recs):
        nxt = recs[ri + 1] if ri + 1 < len(recs) else None
        # Lines too early for this turn are too early for every later one: step past them, or a run of lines with
        # no record (a GPT-Live session with no recording) would fill the window and stop every later turn linking.
        while k < len(lines) and early(r, lines[k]):
            k += 1
        j = next((j for j in range(k, min(len(lines), k + LINK_SKIP)) if fits(r, lines[j])), None)
        if j is None:
            continue
        while j < len(lines) and fits(r, lines[j]) and "rec" not in lines[j]:
            # A line the next turn's words also hold, written after that turn began, is that turn's ("Yeah, I
            # think so" then "Yeah").
            if r["lines"] and nxt is not None and fits(nxt, lines[j]) and nxt.get("secs") is not None \
                    and lines[j].get("secs") is not None and lines[j]["secs"] >= nxt["secs"]:
                break
            lines[j]["rec"] = ri
            r["lines"].append(lines[j]["i"])
            j += 1
        k = j


def _look(x: dict, to_secs) -> dict:
    """An earpiece look (earpiece_history's item) with its times on this axis."""
    f, at = x.get("fate"), to_secs(x.get("t"))
    return {"id": x.get("id"), "secs": at, "asked_secs": None if at is None else at - round((x.get("ms") or 0) / 1000),
            "trigger": x.get("trigger"),
            "speaking": x.get("speaking"), "error": x.get("error"), "read": x.get("read"), "next": x.get("next"),
            "note": x.get("note"), "note_kind": x.get("note_kind"), "about": x.get("about"), "heard": x.get("heard"),
            "fate": None if not f else {"fate": f.get("fate"), "reason": f.get("reason"), "detail": f.get("detail"),
                                        "held_secs": f.get("held_secs"), "inferred": f.get("inferred"),
                                        "secs": to_secs(f.get("t"))},
            "delivered_secs": to_secs((x.get("delivered") or {}).get("t")),
            "reply": None if not x.get("reply") else {"secs": to_secs(x["reply"].get("t")),
                                                      "text": x["reply"].get("text")}}


def _axis(t0: datetime | None):
    def to_secs(iso) -> float | None:
        return _secs(_when(iso), t0) if iso else None
    return to_secs


def _audio(folder: Path, ids) -> dict[str, bool]:
    return {i: audio_file(folder, i) is not None for i in ids}


def _ids(rows: list[dict], recs: list[dict]) -> list[str]:
    out = list(_anchors(rows))
    out += [str(r["live_id"]) for r in recs if r.get("live_id") and str(r["live_id"]) not in out]
    return list(dict.fromkeys(out))


# --- the three views ---------------------------------------------------------------------------------

def conversations(folder: Path) -> list[dict]:
    out = []
    for stem, rows in _conversation_rows(folder).items():
        recs = store.records(folder, stem)
        times = [t for t in (_when(r.get("t")) for r in rows) if t]
        start = _zero(rows, recs)
        ids = _ids(rows, recs)
        out.append({"stem": stem, "part": store.part_of(stem), "started": _iso(start),
                    "ended": _iso(times[-1]) if times else None,
                    "minutes": round((times[-1] - times[0]).total_seconds() / 60, 1) if times else 0.0,
                    "lines": sum(1 for r in rows if r.get("who") in EH.SPEAKERS and str(r.get("text") or "").strip()),
                    "transcript": bool(rows),
                    "turns": {src: sum(1 for r in recs if r.get("source") == src) for src in record.SOURCES},
                    "marks": len(store.marks(folder, stem)), "audio": _audio(folder, ids)})
    return sorted(out, key=lambda c: (c["started"] or "", c["stem"]), reverse=True)


def annotated(folder: Path, S: dict) -> list[dict]:
    """Every record as shown (a replay's recording record in place of the page turns it covers, store.merged), each
    placed against the earlier records of its own source with none hidden: the page norm counts the page turns a
    replay replaced, as signals/live.py did when it told the earpiece, so the note shown is the note it had."""
    by = {(r.get("stem"), src, r.get("turn")): r
          for src in record.SOURCES for r in record.annotate(store.visit(folder, src), S)}
    return [by.get((r.get("stem"), r.get("source"), r.get("turn")), r) for r in store.visit(folder)]


def conversation(folder: Path, lid: str, stem: str, S: dict) -> dict:
    """One conversation on its own axis. Raises FileNotFoundError when it has neither a transcript nor records."""
    rows = _rows(transcript_path(folder, stem))
    if not rows and not store.path(folder, stem).is_file():
        raise FileNotFoundError(stem)
    every = annotated(folder, S)
    mine = [r for r in every if r.get("stem") == stem]
    t0 = _zero(rows, mine)
    to_secs = _axis(t0)
    anchors = _anchors(rows)
    tl = EH.timeline_of(rows, lid, stem)
    items = []
    for x in tl["items"]:
        if x["kind"] == "look":
            continue
        items.append({**x, "i": len(items), "secs": to_secs(x.get("t"))})
    recs = _placed(mine, anchors, t0)
    link(recs, items)
    secs = _seconds(folder)
    ids = _ids(rows, mine)
    sessions = [{"live_id": i, "secs": _secs(anchors.get(i), t0), "seconds": secs.get(i)} for i in ids]
    marks = store.marks(folder, stem)
    ends = [x["secs"] for x in items if x.get("secs") is not None] + \
           [r["end_secs"] for r in recs if r.get("end_secs") is not None] + [m["secs"] for m in marks]
    return {"lid": lid, "stem": stem, "part": store.part_of(stem), "started": _iso(t0),
            "ended": tl["ended"], "duration": max(ends, default=0.0),
            "parts": [{"part": store.part_of(stem), "stem": stem, "label": None, "secs": 0.0,
                       "end_secs": max(ends, default=0.0), "offset": 0.0}],
            "records": recs, "marks": marks, "items": items,
            "looks": [_look(x, to_secs) for x in tl["items"] if x["kind"] == "look"],
            "director": tl["director"], "sessions": sessions, "audio": _audio(folder, ids)}


def _reading(folder: Path, anchors: dict, t0: datetime) -> list[dict]:
    """Each chapter's stretches read aloud, from live/reading.jsonl: a paragraph's session clock (times, live_id)
    on its session's zero, else the row's own time (written as the paragraph was heard to its end)."""
    out = []
    for root in state.chapters(folder):
        spans: list[list[datetime]] = []
        for r in state.read_jsonl(root / "live" / "reading.jsonl"):
            tm, a = r.get("times") or {}, anchors.get(r.get("live_id") or "")
            if a is not None and _num(tm.get("start_ms")) and _num(tm.get("end_ms")):
                s, e = a + timedelta(milliseconds=tm["start_ms"]), a + timedelta(milliseconds=tm["end_ms"])
            elif (t := _when(r.get("t"))) is not None:
                s = e = t
            else:
                continue
            spans.append([s, max(s, e)])
        spans.sort()
        merged: list[list[datetime]] = []
        for s, e in spans:
            if merged and (s - merged[-1][1]).total_seconds() <= READING_GAP_S:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        if merged:
            out.append({"run": root.name, "title": state.chapter_title(root),
                        "spans": [{"secs": _secs(s, t0), "end_secs": _secs(e, t0)} for s, e in merged]})
    return out


def whole_visit(folder: Path, lid: str, name: str | None, S: dict) -> dict:
    """The visit (demo/visit.py's parts, lines and events) with every record, mark and look on one axis."""
    v = visit.read(folder, name)
    every = annotated(folder, S)
    convs = _conversation_rows(folder)
    for p in EH._files(folder):          # the library's sessions are parts of the visit too (no records)
        convs.setdefault(p.stem, EH._rows(p))
    anchors: dict[str, datetime] = {}
    for rows in convs.values():
        for k, t in _anchors(rows).items():
            anchors.setdefault(k, t)
    by_stem: dict[str, list[dict]] = {}
    for r in every:
        by_stem.setdefault(r.get("stem"), []).append(r)
    zeros = {s: _zero(rows, by_stem.get(s, [])) for s, rows in convs.items()}
    starts = [t for t in [_when(v.get("started")), *zeros.values()] if t]
    starts += [a for a, _ in (_start_end(r, anchors) for r in every) if a]
    t0 = min(starts, default=None)
    to_secs = _axis(t0)

    items, stem = [], None
    for x in v["items"]:
        if x["kind"] == "part":
            stem = x.get("id")
        items.append({**x, "i": len(items), "secs": to_secs(x["t"]), "stem": stem})
    parts, cur = [], None
    for x in items:                         # a part runs to its last item (and below, its last record)
        if x["kind"] == "part":
            kind = {"conversation": "guide"}.get(x["part"], x["part"])
            if kind == "guide" and _library(convs.get(x.get("stem")) or []):
                kind = "library"
            cur = {"part": kind, "stem": x.get("stem"), "label": x["text"], "secs": x["secs"],
                   "end_secs": x["secs"], "offset": _secs(zeros.get(x.get("stem")), t0)}
            parts.append(cur)
        elif cur is not None and x.get("secs") is not None:
            cur["end_secs"] = max(cur["end_secs"] or 0, x["secs"])
    for s in by_stem:                      # records whose transcript is gone: a part of their own
        if s and s not in {p["stem"] for p in parts} and zeros.get(s) is not None:
            parts.append({"part": store.part_of(s), "stem": s, "label": f"No transcript: {s}",
                          "secs": _secs(zeros[s], t0), "end_secs": _secs(zeros[s], t0), "offset": _secs(zeros[s], t0)})
    parts.sort(key=lambda p: p["secs"] or 0)

    recs = []
    for s, group in by_stem.items():
        placed = _placed(group, anchors, t0)
        if s:
            link(placed, [x for x in items if x.get("stem") == s])
        recs += placed
    recs.sort(key=lambda r: (r["secs"] is None, r["secs"] or 0))
    for ri, r in enumerate(recs):          # lines point at records by index: renumber after the sort
        for i in r["lines"]:
            items[i]["rec"] = ri

    for p in parts:
        if p["stem"]:
            ends = [r["end_secs"] for r in recs if r.get("stem") == p["stem"] and r.get("end_secs") is not None]
            p["end_secs"] = max([p["end_secs"] or 0, *ends])

    marks, looks = [], []
    for s in convs:
        off = _secs(zeros.get(s), t0)
        for m in store.marks(folder, s):
            marks.append({**m, "stem": s, "conv_secs": m["secs"],
                          "secs": round(off + m["secs"], 2) if off is not None else None})
        if store.part_of(s) == "guide" and any(r.get("who") == "director" for r in convs[s]):
            looks += [{**_look(x, to_secs), "stem": s}
                      for x in EH.timeline_of(convs[s], lid, s)["items"] if x["kind"] == "look"]
    secs = _seconds(folder)
    sessions = [{"live_id": i, "secs": _secs(a, t0), "seconds": secs.get(i)} for i, a in anchors.items()]
    ids = list(dict.fromkeys([*anchors, *(str(r["live_id"]) for r in every if r.get("live_id"))]))
    ends = [p["end_secs"] for p in parts] + [m["secs"] for m in marks if m["secs"] is not None]
    return {"lid": lid, "name": name, "started": _iso(t0), "ended": v.get("ended"),
            "duration": max(ends, default=0.0), "parts": parts, "chapters": _reading(folder, anchors, t0) if t0 else [],
            "records": recs, "marks": sorted(marks, key=lambda m: m["secs"] or 0), "items": items,
            "looks": sorted(looks, key=lambda x: x["secs"] or 0), "sessions": sessions, "audio": _audio(folder, ids)}


# --- mounting ----------------------------------------------------------------------------------------

def mount(app: FastAPI, cfg: dict) -> None:
    S = record.settings(cfg)

    def folder_of(lid: str) -> tuple[Path, str, dict | None]:
        """(folder, label, tester record) for a tester's id, or "own": the active listener (after guard(), so never
        a tester's cookie scope), as the Earpiece history resolves it."""
        if lid == "own":
            return common.listener_dir(cfg), "Your own", None
        rec = _tester_or_404(lid)
        return testers.folder(lid), rec.get("label") or lid, rec

    def stem_or_404(stem: str) -> str:
        if not store.valid_stem(stem):
            raise HTTPException(404, "No such conversation.")
        return stem

    @app.get("/admin/signals", response_class=HTMLResponse)
    def signals_page(request: Request) -> Response:
        guard(request)
        return HTMLResponse(PAGE.read_text(encoding="utf-8"), headers=NO_STORE)

    @app.get("/api/admin/signals/{lid}")
    def signals_list(lid: str, request: Request) -> JSONResponse:
        guard(request)
        d, label, _ = folder_of(lid)
        return JSONResponse({"lid": lid, "label": label, "conversations": conversations(d)}, headers=NO_STORE)

    @app.get("/api/admin/signals/{lid}/visit")   # before /{stem}: "visit" would be taken for one
    def signals_visit(lid: str, request: Request) -> JSONResponse:
        guard(request)
        d, label, rec = folder_of(lid)
        name = (rec or {}).get("name") or (rec or {}).get("given_name") or (None if rec else "You")
        return JSONResponse({**whole_visit(d, lid, name, S), "label": label}, headers=NO_STORE)

    @app.get("/api/admin/signals/{lid}/audio/{live_id}")
    def signals_audio(lid: str, live_id: str, request: Request) -> Response:
        guard(request)
        d, _, _ = folder_of(lid)
        p = audio_file(d, live_id)
        if p is None:
            raise HTTPException(404, "No recording on disk.")
        return FileResponse(p, media_type=AUDIO_TYPES.get(p.suffix, "application/octet-stream"), headers=NO_STORE)

    @app.get("/api/admin/signals/{lid}/{stem}")
    def signals_one(lid: str, stem: str, request: Request) -> JSONResponse:
        guard(request)
        d, label, _ = folder_of(lid)
        try:
            got = conversation(d, lid, stem_or_404(stem), S)
        except FileNotFoundError:
            raise HTTPException(404, "No such conversation.") from None
        return JSONResponse({**got, "label": label}, headers=NO_STORE)

    @app.post("/api/admin/signals/{lid}/{stem}/mark")
    def signals_mark(lid: str, stem: str, req: Mark, request: Request) -> JSONResponse:
        guard(request)
        d, _, _ = folder_of(lid)
        stem_or_404(stem)
        if not transcript_path(d, stem).is_file() and not store.path(d, stem).is_file():
            raise HTTPException(404, "No such conversation.")
        try:
            mark = store.add_mark(d, stem, req.secs, req.text)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return JSONResponse({"mark": mark}, headers=NO_STORE)
