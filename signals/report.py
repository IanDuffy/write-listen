"""wl signals report: what one listener's data says about the build plan's open questions, as plain text.

- Sound tags: the bracketed tags GPT-Live wrote into the listener's lines (consecutive lines joined first: tags
  arrive in pieces), and how often.
- Per recording: how much the other voice bleeds into the listener's channel (the median listener level while
  the other voice speaks, against while the listener speaks), and whether the visit was on a phone, from the
  page's audio_route records (study/events.jsonl).
- The norm: how many turns had one, and where it first appeared (does the intro count toward it).
- How often each measure was mentioned.
- Page against recording (package 5's check): each recording record beside the page turns it replaced, from the
  raw lines (the merged records hide those page turns), and the page turns it matched none of, with why.
- Ian's marks: the turns within MARK_SECS of each, and what they mentioned.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np

from stages.common import StageError

from . import fetch, measure, record, store

MARK_SECS = 15
SPEAKERS = ("guide", "interviewer")
COMPARED = (("start_ms", "start"), ("end_ms", "end"), ("gap_ms", "gap"), ("dur_ms", "dur"), ("words", "words"))
WITHIN_MS = 300         # a page time this close to the recording's agrees


def _when(t) -> datetime | None:
    try:
        x = datetime.fromisoformat(str(t))
    except ValueError:
        return None
    return x.astimezone().replace(tzinfo=None) if x.tzinfo else x


def _transcripts(listener: Path) -> list[Path]:
    from voice.gpt_live.earpiece_history import SESSION_ID
    d, s = listener / "sessions", listener / "study"
    out = sorted(p for p in d.glob("*.jsonl") if SESSION_ID.fullmatch(p.stem)) if d.is_dir() else []
    return out + (sorted(p for m in fetch.MODES for p in s.glob(f"{m}-*.jsonl")) if s.is_dir() else [])


def sound_tags(listener: Path) -> dict:
    """Tags in the listener's turns (their lines with no guide or interviewer line between, joined)."""
    tags, turns, tagged = Counter(), 0, 0

    def done(frags):
        nonlocal turns, tagged
        if frags:
            got = record.tags(record.join(frags))
            turns += 1
            tagged += bool(got)
            tags.update(got)
    for p in _transcripts(listener):
        frags: list[str] = []
        for r in fetch.jsonl(p):
            if r.get("who") == "listener" and str(r.get("text") or "").strip():
                frags.append(str(r["text"]))
            elif r.get("who") in SPEAKERS:
                done(frags)
                frags = []
        done(frags)
    return {"counts": dict(tags.most_common()), "turns": turns, "tagged": tagged}


def phone(listener: Path) -> dict:
    """From the page's audio_route records: a phone or not, which, and whether its loudspeaker was in use."""
    from demo import debrief
    routes = debrief.audio_routes(listener)
    out = {"records": len(routes), "phone": None, "speakerphone": None, "devices": []}
    for e in routes:
        d = e["data"]
        h = d.get("ua_hints") or {}
        ua, plat = str(d.get("ua") or ""), str(h.get("platform") or "")
        out["phone"] = bool(out["phone"]) or plat in ("Android", "iOS") or bool(re.search(r"Android|iPhone|iPad", ua))
        loud = (d.get("reason") == "speakerphone" and d.get("result") == "switched"
                or str(d.get("track_label") or "") == "Speakerphone"
                or "Speakerphone" in [str(x) for x in d.get("outputs") or []])
        out["speakerphone"] = bool(out["speakerphone"]) or loud
        dev = " ".join(x for x in (str(h.get("model") or ""), plat) if x)
        if not dev and (m := re.search(r"iPhone|iPad|Android|Macintosh|Windows", ua)):
            dev = m.group(0)
        if dev and dev not in out["devices"]:
            out["devices"].append(dev)
    return out


def recordings(listener: Path, cfg: dict) -> list[dict]:
    R = measure.settings(cfg)
    out = []
    for tg in fetch.targets(listener):
        row = {"live_id": tg["live_id"], "stem": tg["stem"], "part": tg["part"], "file": None}
        src = fetch.audio_for(listener, tg["live_id"])
        if src is None:
            out.append(row)
            continue
        row["file"] = src.name
        try:
            with measure.Audio(src) as a:
                L = measure.analyse(a, R)
        except StageError as e:
            out.append({**row, "error": str(e)})
            continue
        speaking = L.lis[L.lis_v & ~L.oth_v]
        speech = float(np.median(speaking)) if len(speaking) else 0.0
        out.append({**row, "minutes": round(len(L.lis) * L.secs / 60, 1), "bleed_rms": round(L.bleed, 1),
                    "speech_rms": round(speech, 1), "floor_rms": round(L.floor, 1), "threshold": round(L.thr, 1),
                    "bleed_db": round(20 * np.log10(L.bleed / speech), 1) if L.bleed > 0 and speech > 0 else None})
    return out


def norm_and_mentions(listener: Path, cfg: dict) -> dict:
    """Per source: turns, how many had a norm, the first that did and the parts of the turns it read; and the
    measures mentioned."""
    S = record.settings(cfg)
    out = {}
    for src in record.SOURCES:
        recs = store.visit(listener, src)
        if not recs:
            continue
        k = next((i for i, r in enumerate(recs) if r.get("norm")), None)
        first = None
        if k is not None:
            r = recs[k]
            read = [x for x in recs[:k] if record.qualifies(x, S)][-int(S["norm_turns"]):]
            first = {"stem": r["stem"], "part": r["part"], "turn": r["turn"], "t": r.get("t"),
                     "read": dict(Counter(x["part"] for x in read))}
        parts = {}
        for r in recs:
            p = parts.setdefault(r["part"], {"turns": 0, "with_norm": 0})
            p["turns"] += 1
            p["with_norm"] += bool(r.get("norm"))
        said = Counter(m for r in recs for m in r.get("mention") or [])
        out[src] = {"turns": len(recs), "with_norm": sum(1 for r in recs if r.get("norm")), "first": first,
                    "parts": parts, "mentions": dict(said.most_common()),
                    "mentioned_turns": sum(1 for r in recs if r.get("mention"))}
    return out


def _zero(listener: Path, stem: str) -> datetime | None:
    """The conversation's axis starts at its transcript's first row (store.add_mark)."""
    p = listener / ("study" if store.part_of(stem) != "guide" else "sessions") / f"{stem}.jsonl"
    return next((t for t in (_when(r.get("t")) for r in fetch.jsonl(p)) if t), None)


def _ms(secs) -> float | None:
    return secs * 1000 if isinstance(secs, (int, float)) else None


def _total(vals) -> float | None:
    vals = list(vals)
    return sum(vals) if vals and None not in vals else None


def _span(page: list[dict]) -> float | None:
    """The page's length for these turns. Both sources measure a turn as first sound to last, pauses included, so
    turns the page split at a pause span the first's start to the last's end; adding their lengths would leave the
    pauses out. With no session clock there is no span for several turns."""
    if len(page) == 1:
        return page[0].get("dur_ms")
    a, b = page[0].get("start_s"), page[-1].get("end_s")
    return (b - a) * 1000 if a is not None and b is not None else None


def _diff(rec: dict, page: list[dict]) -> dict:
    """Recording minus page, for each measure both have. Several page turns count as one: the first's start and
    gap, the last's end, the span between them, their words added."""
    first, last = page[0], page[-1]
    p = {"start_ms": _ms(first.get("start_s")), "end_ms": _ms(last.get("end_s")), "gap_ms": first.get("gap_ms"),
         "dur_ms": _span(page), "words": _total(x.get("words") for x in page)}
    r = {"start_ms": _ms(rec.get("start_s")), "end_ms": _ms(rec.get("end_s")), "gap_ms": rec.get("gap_ms"),
         "dur_ms": rec.get("dur_ms"), "words": rec.get("words")}
    return {k: round(r[k] - p[k]) for k in p if r[k] is not None and p[k] is not None}


def _apart(page: dict, rec: dict) -> float:
    """Seconds between a page turn and a recording turn on the session's clock (0 when they overlap)."""
    a, b, c, d = page.get("start_s"), page.get("end_s"), rec.get("start_s"), rec.get("end_s")
    return float("inf") if None in (a, b, c, d) else max(c - b, a - d, 0.0)


def _audio(listener: Path, live_id) -> Path | None:
    """The session's recording on disk, as the replay would measure it."""
    return fetch.audio_for(listener, live_id) if store.LIVE_ID.fullmatch(str(live_id or "")) else None


def _unheard(listener: Path, stem: str, live_id) -> str:
    """Why a page turn's GPT-Live session has no recording record: no recording, one the replay hasn't seen yet,
    or one the replay measured and heard none of the listener in (a quiet mic)."""
    audio = _audio(listener, live_id)
    if audio is None:
        return "no recording of its GPT-Live session"
    try:
        got, replayed = audio.stat(), store.path(listener, stem).stat().st_mtime
    except OSError:
        return "no recording of its GPT-Live session"
    # a reused recording is hard-linked in (fetch._place): its mtime is the original's, the link sets its ctime
    if max(got.st_mtime, got.st_ctime) > replayed:
        return "its recording was fetched after the last replay (wl signals run)"
    return ("its recording was measured, but the replay heard no turn of theirs in that session "
            "(signals.recording min_rms/floor_x may be too high for this mic)")


def _why(listener: Path, stem: str, page: dict, recs: list[dict]) -> str:
    """Why store.match_page gave this page turn to no recording record."""
    same = [r for r in recs if r.get("live_id") == page.get("live_id")]
    if not same:
        return _unheard(listener, stem, page.get("live_id"))
    if page.get("start_s") is None or page.get("end_s") is None:
        return "no session clock and no text match"
    near = min(same, key=lambda r: _apart(page, r))
    apart = _apart(page, near)
    if apart <= store.SLACK_S:      # it would match now: the page wrote it (or its last line) after the replay
        return f"near recording turn {near['turn']}, but newer than the replay (wl signals run again)"
    return f"outside the {store.SLACK_S:g} s slack (nearest: recording turn {near['turn']}, {apart:.1f} s away)"


def compare(listener: Path) -> dict:
    """Package 5's check: does the page's timing agree with the replay's for the same turns? From the raw lines,
    the latest per turn, since store.merged drops a page turn a recording record replaces."""
    pairs, unmatched, unreplayed = [], [], []
    for stem in store.stems(listener):
        by = store.per_turn(store.rows(listener, stem))
        page = {t: r for (src, t), r in by.items() if src == "page"}
        recs = sorted((r for (src, _), r in by.items() if src == "recording"), key=lambda r: r["turn"])
        if not page:
            continue
        # no replay record at all is "no replay" only with no recording to replay: a replay that heard nothing
        # (a quiet mic) is the very disagreement this check is for
        if not recs and not any(_audio(listener, x) for x in {p.get("live_id") for p in page.values()}):
            unreplayed.append(stem)
            continue
        taken: set[int] = set()
        for r in recs:
            got = sorted({t for t in r.get("replaces") or [] if t in page})
            if got:
                taken.update(got)
                pairs.append({"stem": stem, "turn": r["turn"], "page": got, "diff": _diff(r, [page[t] for t in got])})
        unmatched += [{"stem": stem, "turn": t, "why": _why(listener, stem, page[t], recs)}
                      for t in sorted(set(page) - taken)]
    summary = {}
    for k, _ in COMPARED:
        vals = [abs(p["diff"][k]) for p in pairs if k in p["diff"]]
        if vals:
            summary[k] = {"n": len(vals), "median": round(float(np.median(vals))), "largest": max(vals)}
            if k != "words":
                summary[k]["within"] = sum(1 for v in vals if v <= WITHIN_MS)
    return {"pairs": pairs, "summary": summary, "unmatched": unmatched, "unreplayed": unreplayed}


def marks(listener: Path) -> list[dict]:
    out = []
    for stem in store.marked(listener):     # a conversation can have marks and no records file
        got = store.marks(listener, stem)
        if not got:
            continue
        zero = _zero(listener, stem)
        recs = store.records(listener, stem)
        for m in got:
            near = []
            for r in recs if zero else []:
                t = _when(r.get("t"))
                if t is None:
                    continue
                a = (t - zero).total_seconds()
                b = a + (r.get("dur_ms") or 0) / 1000
                if a <= m["secs"] + MARK_SECS and b >= m["secs"] - MARK_SECS:
                    near.append({"turn": r["turn"], "source": r["source"], "part": r["part"], "secs": round(a, 1),
                                 "mention": r.get("mention") or [], "text": r.get("text") or ""})
            out.append({"stem": stem, "secs": m["secs"], "text": m["text"], "placed": zero is not None,
                        "records": store.path(listener, stem).exists(), "turns": near})
    return out


def build(listener: Path, cfg: dict, label: str | None = None) -> dict:
    listener = Path(listener)
    out = {"listener": label or listener.name, "folder": str(listener), "tags": sound_tags(listener),
           "phone": phone(listener), "recordings": recordings(listener, cfg),
           "sources": norm_and_mentions(listener, cfg), "compare": compare(listener), "marks": marks(listener)}
    out["text"] = text(out)
    return out


def _mss(secs) -> str:
    s = int(max(0, float(secs)))
    return f"{s // 60}:{s % 60:02d}"


def _clip(s: str, n: int = 80) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n - 1] + "…"


def text(rep: dict) -> str:
    L = [f"{rep['listener']} ({rep['folder']})"]
    ph = rep["phone"]
    if not ph["records"]:
        L.append("  Phone: not known (no audio_route records; the page keeps them in demo mode since 2026-11-21)")
    else:
        what = "a phone" if ph["phone"] else "not a phone"
        loud = ", on the loudspeaker" if ph["speakerphone"] else ""
        dev = f" ({', '.join(ph['devices'])})" if ph["devices"] else ""
        L.append(f"  Phone: {what}{dev}{loud}; {ph['records']} audio_route records")
    tg = rep["tags"]
    if tg["counts"]:
        seen = ", ".join(f"[{k}] {v}" for k, v in tg["counts"].items())
        L.append(f"  Sound tags: {seen} ({tg['tagged']} of {tg['turns']} listener turns had one)")
    else:
        L.append(f"  Sound tags: none in {tg['turns']} listener turns")
    if rep["recordings"]:
        L.append("  Recordings (the other voice in the listener's channel, against their own speech):")
    for r in rep["recordings"]:
        head = f"    {r['stem']} {r['part']} {r['live_id']}"
        if r.get("error"):
            L.append(f"{head}: {r['error']}")
        elif not r["file"]:
            L.append(f"{head}: no audio (wl signals fetch)")
        else:
            bleed = (f"bleed {r['bleed_db']:+.0f} dB" if r["bleed_db"] is not None else "bleed not measurable")
            L.append(f"{head} ({r['file']}, {r['minutes']} min): {bleed} (median {r['bleed_rms']:.0f} under the "
                     f"other voice, {r['speech_rms']:.0f} speaking); floor {r['floor_rms']:.0f}, threshold "
                     f"{r['threshold']:.0f}")
    if not rep["sources"]:
        L.append("  No records yet (wl signals run)")
    for src, s in rep["sources"].items():
        parts = ", ".join(f"{p} {v['with_norm']} of {v['turns']}" for p, v in s["parts"].items())
        L.append(f"  Norm ({src} records): {s['with_norm']} of {s['turns']} turns had one ({parts})")
        f = s["first"]
        if f:
            read = ", ".join(f"{p} {n}" for p, n in f["read"].items())
            L.append(f"    first at turn {f['turn']} of {f['stem']} ({f['part']}), from {read}")
        if s["mentions"]:
            said = ", ".join(f"{k} {v}" for k, v in s["mentions"].items())
            L.append(f"  Mentioned ({src}): {said} ({s['mentioned_turns']} of {s['turns']} turns)")
        else:
            L.append(f"  Mentioned ({src}): nothing")
    c = rep["compare"]
    if c["pairs"] or c["unmatched"] or c["unreplayed"]:
        L.append("  Page against recording (recording minus page, the same turns):")
    for p in c["pairs"]:
        bits = ", ".join(f"{name} {p['diff'][k]:+d}" + (" ms" if k != "words" else "")
                         for k, name in COMPARED if k in p["diff"])
        L.append(f"    {p['stem']} turn {p['turn']} (page {', '.join(map(str, p['page']))}): {bits}")
    for k, name in COMPARED:
        if s := c["summary"].get(k):
            unit = " ms" if k != "words" else ""
            within = f", {s['within']} of {s['n']} within {WITHIN_MS} ms" if "within" in s else ""
            L.append(f"    {name}: median {s['median']}{unit}, largest {s['largest']}{unit}{within}")
    for u in c["unmatched"]:
        L.append(f"    page turn {u['turn']} of {u['stem']} matched no recording: {u['why']}")
    if c["unreplayed"]:
        L.append(f"    page records but no replay: {', '.join(c['unreplayed'])} (wl signals fetch, then run)")
    for m in rep["marks"]:
        L.append(f"  Mark {m['stem']} at {_mss(m['secs'])}: \"{_clip(m['text'], 60)}\"")
        if not m["placed"]:
            L.append("    no transcript to place it on")
        elif not m["records"]:
            L.append("    no records for this conversation: wl signals fetch, then run")
        elif not m["turns"]:
            L.append(f"    no turn within {MARK_SECS} s")
        for t in m["turns"]:
            said = ", ".join(t["mention"]) or "nothing mentioned"
            L.append(f"    turn {t['turn']} ({t['source']}, {_mss(t['secs'])}): {said}: \"{_clip(t['text'])}\"")
    return "\n".join(L) + "\n"
