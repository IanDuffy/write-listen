"""A record from the page's measurements: the live line routes (voice/gpt_live/routes.py for the guide,
demo/interviewer.py for the intro and the debrief) pass each listener line's `sig` here.

The page measures the audio (page/sig.js: the pause before answering, the turn's length, its pauses, loudness;
no pitch yet) and posts it with the line, cumulative for the turn so far and with the page's turn number; the
server adds the text measures with the same rules the replay uses (record.py), places the turn against the
listener's earlier page turns, and appends it. A turn's later lines append a new line for the same turn, which
wins when read (store.merged).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from . import record, store

# The page's raw fields and the range a real value can have; anything else is dropped (null, never a guess).
RANGES = {"gap_ms": (-120_000, 3_600_000), "dur_ms": (0, 3_600_000), "speak_ms": (0, 3_600_000),
          "pauses": (0, 10_000), "longest_pause_ms": (0, 3_600_000), "rms_db": (-160, 10),
          "start_s": (0, 86_400), "end_s": (0, 86_400), "ago_ms": (0, 3_600_000)}


def clean(sig) -> dict | None:
    """The page's sig with only plausible numbers kept; None if it has no turn number."""
    if not isinstance(sig, dict):
        return None
    turn = sig.get("turn")
    if isinstance(turn, bool) or not isinstance(turn, int) or not 0 <= turn <= 1_000_000:
        return None
    out = {"turn": turn}
    for k, (lo, hi) in RANGES.items():
        v = sig.get(k)
        out[k] = v if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and lo <= v <= hi else None
    cut = sig.get("cut_in")
    out["cut_in"] = cut if isinstance(cut, bool) else None
    return out


def record_line(listener: Path, stem: str, *, part: str, live_id: str | None, text: str, sig, cfg: dict,
                now: datetime | None = None) -> dict | None:
    """Append this line's turn record and return it (None when there is nothing to record)."""
    sig = clean(sig)
    text = (text or "").strip()
    if sig is None or not text or not store.valid_stem(stem):
        return None
    S = record.settings(cfg)
    now = now or datetime.now()
    turn = sig["turn"]
    prev = next((r for r in reversed(store.rows(listener, stem))
                 if r.get("source") == "page" and r.get("turn") == turn), None)
    if prev is not None:
        joined, t = record.join([prev.get("text") or "", text]), prev.get("t")
    else:
        joined = text
        t = (now - timedelta(milliseconds=sig["ago_ms"] or 0)).isoformat(timespec="milliseconds")
    rec = record.build(sig, joined, stem=stem, part=part, source="page", turn=turn,
                       live_id=live_id or (prev or {}).get("live_id"), t=t, S=S)
    key = (rec["t"] or "", rec["turn"])
    earlier = [r for r in store.recent(listener, "page", int(S["norm_turns"]) + 2, S["min_words"])
               if not (r.get("stem") == stem and r.get("turn") == turn) and (r.get("t") or "", r.get("turn") or 0) < key]
    rec = record.place(rec, earlier, S)
    store.append(listener, stem, rec)
    return rec
