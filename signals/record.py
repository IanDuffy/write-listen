"""One record per listener turn, compared with that listener's own norm. Pure: no files, no clock, no models.

A record is flat JSON (one line in <listener>/signals/<stem>.jsonl):

    v, stem, part ("intro" | "guide" | "debrief"), source ("recording" | "page"), turn (int, unique per stem and
    source), live_id, t (wall clock of the turn's first voiced frame, naive local ISO with milliseconds),
    start_s, end_s (the GPT-Live session's clock, which its recording runs on; null when unknown), text,
    the measures (MEASURES; null when not taken, never a guess), and what place() adds:
    norm ({measure: value} or null before there is one), norm_turns, change, trend, mention, note.

The measurers (measure.py from a recording, page/sig.js on the page) give the audio measures; this module adds
the text measures and everything compared with the norm, so both sources are judged by one set of rules.
"""
from __future__ import annotations

import re
from statistics import median

VERSION = 1
PARTS = ("intro", "guide", "debrief")
SOURCES = ("recording", "page")

# Audio measures, from a measurer. speak_ms is the turn's length less its pauses (the time words were spoken in).
AUDIO = ("gap_ms", "dur_ms", "speak_ms", "pauses", "longest_pause_ms", "cut_in", "rms_db", "pitch_sd_st")
# Text measures, from the turn's joined text (text_measures below).
TEXT = ("words", "hedges", "fillers", "tags", "trailed")
MEASURES = AUDIO[:3] + ("words", "wps") + AUDIO[3:5] + TEXT[1:] + AUDIO[5:]

# Compared with the norm: a ratio (0.5 is half their norm), except loudness, a difference in dB.
NORMED = ("gap_ms", "words", "wps", "pitch_sd_st", "rms_db")
DB = ("rms_db",)

DEFAULTS = {
    "live": False,
    "min_words": 3,
    "min_turns": 3,
    "norm_turns": 40,
    "pause_ms": 700,
    "thresholds": {
        "words": [0.5, 2.0],
        "gap_ms": {"ratio": 2.0, "plus_ms": 800},
        "wps": [0.7, 1.3],
        "pitch_sd_st": 0.6,
        "rms_db": 4.0,
    },
    "trend": {"words": 0.8, "gap_ms": 300, "wps": 0.9, "rms_db": 1.0, "pitch_sd_st": 0.9},
    "hedges": ["i guess", "kind of", "sort of", "i suppose"],
    "fillers": ["um", "uh", "er", "erm", "hmm"],
    "trail_words": ["so", "but", "and", "or", "i don't know", "i dunno"],
    "trail_unpunctuated": True,
    "note_words": 30,
}


def settings(cfg: dict | None) -> dict:
    """config.yaml `signals:` over DEFAULTS (one level deep for the nested blocks)."""
    over = dict(((cfg or {}).get("signals") or {}))
    out = {**DEFAULTS, **over}
    for k in ("thresholds", "trend"):
        out[k] = {**DEFAULTS[k], **(over.get(k) or {})}
    return out


def live_on(cfg: dict | None) -> bool:
    """Does the page measure? signals.live, or the earpiece's note (live.director.signals), which needs it."""
    live = ((cfg or {}).get("live") or {}).get("director") or {}
    return bool(settings(cfg).get("live") or live.get("signals"))


# --- text --------------------------------------------------------------------------

TAG = re.compile(r"\[([^\[\]]*)\]")
OPEN_TAG = re.compile(r"\[([^\[\]\s]*)\s*$")          # a turn ending inside a tag: "[inhale"
WORD_STRIP = re.compile(r"\[[^\]]*\]?|\]")             # page/live.js words(): tags are not words
ALNUM = re.compile(r"[a-z0-9]", re.I)
SENTENCE_END = (".", "?", "!")
TRAIL_MARKS = ("...", "…", "-", "—", "–")


def join(fragments: list[str]) -> str:
    """One turn's lines as one text. GPT-Live sends tags in pieces ("[inhale" then "]Yeah"): a piece starting
    with a tag's close joins with no space; anything else joins with one."""
    out = ""
    for f in fragments:
        f = (f or "").strip()
        if not f:
            continue
        if not out:
            out = f
        elif f.startswith("]"):
            out += f
        else:
            out += " " + f
    return out


def words(text: str) -> int:
    """page/live.js words(), word for word: tags removed, then the pieces with a letter or digit."""
    return sum(1 for w in WORD_STRIP.sub(" ", text or "").split() if ALNUM.search(w))


def tags(text: str) -> list[str]:
    """The sound tags GPT-Live wrote into the turn ("[inhale]", "[tongue click ]"), lower case, in order."""
    text = text or ""
    out = [" ".join(m.group(1).lower().split()) for m in TAG.finditer(text)]
    m = OPEN_TAG.search(text)
    if m and m.start() >= text.rfind("]"):
        out.append(m.group(1).lower())
    return [t for t in out if t]


def plain(text: str) -> str:
    """Lower case words only, for counting phrases: tags out, curly apostrophes straight, punctuation to spaces."""
    t = WORD_STRIP.sub(" ", text or "").lower().replace("’", "'")
    return " ".join(re.sub(r"[^a-z0-9']+", " ", t).split())


def count_phrases(text: str, phrases: list[str]) -> int:
    p = f" {plain(text)} "
    n = 0
    for ph in phrases:
        ph = plain(ph)
        if ph:
            n += p.count(f" {ph} ")
    return n


def trailed(text: str, S: dict) -> bool | None:
    """Ends without a sentence end (when trail_unpunctuated), on a trailing mark, or on a trailing word."""
    body = WORD_STRIP.sub(" ", text or "").strip()
    if not ALNUM.search(body):
        return None
    if body.endswith(TRAIL_MARKS):
        return True
    last = f" {plain(body)}"
    if any(last.endswith(f" {plain(w)}") for w in S.get("trail_words") or [] if plain(w)):
        return True
    return bool(S.get("trail_unpunctuated")) and not body.rstrip("\"')”").endswith(SENTENCE_END)


def text_measures(text: str, S: dict) -> dict:
    return {"words": words(text), "hedges": count_phrases(text, S.get("hedges") or []),
            "fillers": count_phrases(text, S.get("fillers") or []), "tags": tags(text),
            "trailed": trailed(text, S)}


# --- one record ----------------------------------------------------------------------

def _num(v, digits=None):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        return None
    return round(float(v), digits) if digits is not None else v


def build(raw: dict, text: str, *, stem: str, part: str, source: str, turn: int, live_id: str | None,
          t: str | None, S: dict) -> dict:
    """A record from a measurer's raw audio measures and the turn's text. Nothing compared with the norm yet."""
    raw = raw or {}
    rec = {"v": VERSION, "stem": stem, "part": part, "source": source, "turn": int(turn), "live_id": live_id,
           "t": t, "start_s": _num(raw.get("start_s"), 2), "end_s": _num(raw.get("end_s"), 2), "text": text}
    for k in ("gap_ms", "dur_ms", "speak_ms", "pauses", "longest_pause_ms"):
        v = _num(raw.get(k))
        rec[k] = int(round(v)) if v is not None else None
    rec.update(text_measures(text, S))
    speak = rec["speak_ms"] if rec["speak_ms"] is not None else rec["dur_ms"]
    rec["wps"] = round(rec["words"] / (speak / 1000), 2) if speak and speak >= 300 and rec["words"] else None
    cut = raw.get("cut_in")
    rec["cut_in"] = bool(cut) if isinstance(cut, bool) else (rec["gap_ms"] < 0 if rec["gap_ms"] is not None else None)
    rec["rms_db"] = _num(raw.get("rms_db"), 1)
    rec["pitch_sd_st"] = _num(raw.get("pitch_sd_st"), 2)
    return rec


# --- the norm and the change from it ---------------------------------------------------

def qualifies(rec: dict, S: dict) -> bool:
    return (rec.get("words") or 0) >= S["min_words"]


def norm(earlier: list[dict], S: dict) -> tuple[dict | None, int]:
    """(the norm, how many turns it read). The median of each measure over the latest norm_turns earlier turns of
    min_words or more; null until there are min_turns of them, and a measure with fewer values than that is null."""
    pool = [r for r in earlier if qualifies(r, S)][-int(S["norm_turns"]):]
    if len(pool) < S["min_turns"]:
        return None, len(pool)
    out = {}
    for k in NORMED:
        vals = [r[k] for r in pool if _num(r.get(k)) is not None]
        out[k] = round(median(vals), 2) if len(vals) >= S["min_turns"] else None
    return out, len(pool)


def change(rec: dict, nm: dict | None) -> dict | None:
    if not nm:
        return None
    out = {}
    for k in NORMED:
        v, n = _num(rec.get(k)), _num(nm.get(k))
        if v is None or n is None:
            out[k] = None
        elif k in DB:
            out[k] = round(v - n, 1)
        else:
            out[k] = round(v / n, 2) if n > 0 and v >= 0 else None
    return out


TREND_WORDS = {"words": ("shorter", "longer"), "gap_ms": ("quicker to answer", "slower to answer"),
               "wps": ("slower", "faster"), "rms_db": ("quieter", "louder"), "pitch_sd_st": ("flatter", "livelier")}


def trend(last3: list[dict], S: dict) -> list[str]:
    """Over the last three turns (this one last): each step the same way by at least the trend step (a ratio for
    words, rate and pitch spread; ms for the pause before answering; dB for loudness)."""
    if len(last3) < 3:
        return []
    out = []
    for k, (down, up) in TREND_WORDS.items():
        vals = [_num(r.get(k)) for r in last3]
        if any(v is None for v in vals):
            continue
        step = S["trend"][k]
        pairs = list(zip(vals, vals[1:]))
        if k in ("gap_ms", "rms_db"):
            if all(b - a >= step for a, b in pairs):
                out.append(up)
            elif all(a - b >= step for a, b in pairs):
                out.append(down)
        elif all(v > 0 for v in vals):
            if all(b <= a * step for a, b in pairs):
                out.append(down)
            elif all(b * step >= a for a, b in pairs):
                out.append(up)
    return out


def mention(rec: dict, ch: dict | None, nm: dict | None, S: dict) -> list[str]:
    """The measures worth mentioning, in the note's order. Tags, a cut-in and trailing off: always."""
    th, out = S["thresholds"], []
    if ch:
        c = ch.get("words")
        if c is not None and (c <= th["words"][0] or c >= th["words"][1]):
            out.append("words")
        c = ch.get("gap_ms")
        if c is not None and c >= th["gap_ms"]["ratio"] and rec["gap_ms"] - nm["gap_ms"] >= th["gap_ms"]["plus_ms"]:
            out.append("gap_ms")
        c = ch.get("wps")
        if c is not None and (c <= th["wps"][0] or c >= th["wps"][1]):
            out.append("wps")
        c = ch.get("pitch_sd_st")
        if c is not None and c <= th["pitch_sd_st"]:
            out.append("pitch_sd_st")
        c = ch.get("rms_db")
        if c is not None and abs(c) >= th["rms_db"]:
            out.append("rms_db")
    if rec.get("tags"):
        out.append("tags")
    if rec.get("cut_in"):
        out.append("cut_in")
    if rec.get("trailed"):
        out.append("trailed")
    return out


def _s(ms) -> str:
    return f"{ms / 1000:.1f} s"


def _phrase(k: str, rec: dict, nm: dict | None) -> str:
    n = nm or {}
    if k == "words":
        return f"{rec['words']} words (norm {n['words']:g})"
    if k == "gap_ms":
        return f"{_s(rec['gap_ms'])} before answering (norm {_s(n['gap_ms'])})"
    if k == "wps":
        return f"{rec['wps']:.1f} words a second (norm {n['wps']:.1f})"
    if k == "pitch_sd_st":
        return f"pitch range {rec['pitch_sd_st']:.1f} semitones (norm {n['pitch_sd_st']:.1f})"
    if k == "rms_db":
        d = rec["rms_db"] - n["rms_db"]
        return f"{abs(d):.0f} dB {'louder' if d > 0 else 'quieter'} than their norm"
    if k == "tags":
        return " ".join(f"[{t}]" for t in rec["tags"])
    if k == "cut_in":
        return "cut in"
    if k == "trailed":
        return "trailed off"
    return k


def note(rec: dict, S: dict | None = None) -> str:
    """The earpiece's one line about this turn: at most note_words words, only what passed a threshold, each
    number with the norm beside it. It reports measurements; it never names a feeling."""
    S = S or DEFAULTS
    limit = int(S.get("note_words") or 30)
    nm, said = rec.get("norm"), rec.get("mention") or []
    parts = [_phrase(k, rec, nm) for k in said]
    if rec.get("trend"):
        parts.append("three turns running: " + ", ".join(rec["trend"]))
    if nm is None:
        head = f"Their last turn: no norm yet ({rec.get('norm_turns', 0)} of {S.get('min_turns', 3)} turns)"
    elif not parts:
        return "Their last turn: nothing outside their norm."
    else:
        head = "Their last turn:"
    if not parts:
        return head + "."
    sep = " " if head.endswith(":") else "; "
    for k in range(len(parts), 0, -1):          # drop whole items from the end until it fits
        line = head + sep + "; ".join(parts[:k]) + "."
        if len(line.split()) <= limit:
            return line
    return " ".join(line.split()[:limit])


def place(rec: dict, earlier: list[dict], S: dict) -> dict:
    """The record compared with the earlier turns of the same source (time order, this one excluded): norm,
    norm_turns, change, trend, mention, note. A recording and the page are different instruments, so each is
    judged against its own norm."""
    same = [r for r in earlier if r.get("source") == rec.get("source")]
    nm, n = norm(same, S)
    ch = change(rec, nm)
    out = {**rec, "norm": nm, "norm_turns": n, "change": ch, "trend": trend(same[-2:] + [rec], S),
           "mention": mention(rec, ch, nm, S)}
    out["note"] = note(out, S)
    return out


def annotate(records: list[dict], S: dict) -> list[dict]:
    """Every record placed against the ones before it (the list in time order, e.g. a whole visit)."""
    out = []
    for i, r in enumerate(records):
        out.append(place(r, records[:i], S))
    return out
