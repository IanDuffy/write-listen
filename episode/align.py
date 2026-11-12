"""Match the script's words to a recognizer's timed words. The script supplies the text; the
recognizer only the times, so its spelling never reaches the output.

1. Word pass: normalized script tokens against normalized recognizer tokens (difflib, no junk
   heuristic). Equal runs are exact matches.
2. Character pass, inside each gap: the gap's script words (as displayed and as spoken) against
   the gap's recognizer words, character by character. This catches split or joined words
   ("houseful" / "house full"), numbers ("two thousand eight" / "2008") and respelled names
   ("Dee-oh-TEE-mah" heard as "Diotima").
3. Words still unmatched get times interpolated between their matched neighbours, by length,
   and are marked estimated.
Then times are made monotonic and no word is longer than MAX_WORD seconds.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

MAX_WORD = 2.0
FUZZY_MIN_RATIO = 0.5
FUZZY_MAX_WORDS = 80   # a longer gap means the recognizer lost its place there: estimate instead

_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen " \
        "sixteen seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def number_words(n: int) -> str:
    """A cardinal number in words, as the script writes them (no 'and')."""
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        return _ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + number_words(n % 100))
    if n < 1_000_000:
        return number_words(n // 1000) + " thousand" + ("" if n % 1000 == 0 else " " + number_words(n % 1000))
    return str(n)


def norm_tokens(text: str) -> list[str]:
    """Lowercase word tokens: apostrophes dropped, hyphens, dashes and slashes split, digits in words."""
    t = text.lower().replace("’", "'").replace("‘", "'").replace("'", "")
    t = re.sub(r"[-‐‑‒–—―/]", " ", t)
    t = re.sub(r"(\d),(\d{3})", r"\1\2", t)
    t = re.sub(r"\d+", lambda m: " " + number_words(int(m.group())) + " ", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return t.split()


def _rec_tokens(rec: list[dict]) -> list[dict]:
    """Recognizer words as tokens; a word that normalizes to several tokens shares its time by length."""
    out = []
    for w in rec:
        toks = norm_tokens(w["w"])
        if not toks:
            continue
        total = sum(len(x) for x in toks)
        t = w["start"]
        for x in toks:
            d = (w["end"] - w["start"]) * len(x) / total
            out.append({"t": x, "start": t, "end": t + d})
            t += d
    return out


def align(words: list[dict], rec: list[dict], limit: float | None = None) -> list[dict]:
    """words: [{'w': displayed, 'spoken': spoken}] in order; rec: [{'w','start','end'}] in order (seconds).
    Returns [{'start', 'end', 'match': 'exact'|'fuzzy'|'estimated'}], one per word."""
    rtoks = _rec_tokens(rec)
    stoks, owner = [], []  # script tokens (displayed form) and the word each belongs to
    for i, w in enumerate(words):
        for x in norm_tokens(w["w"]):
            stoks.append(x)
            owner.append(i)
    hits: dict[int, list[int]] = {i: [] for i in range(len(words))}  # word -> recognizer token indexes
    how: dict[int, str] = {}

    sm = SequenceMatcher(None, stoks, [r["t"] for r in rtoks], autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                hits[owner[i1 + k]].append(j1 + k)
                how.setdefault(owner[i1 + k], "exact")
        elif op == "replace":
            ws = sorted({owner[k] for k in range(i1, i2)})
            if len(ws) <= FUZZY_MAX_WORDS and j2 - j1 <= FUZZY_MAX_WORDS * 2:
                _fuzzy(words, ws, rtoks, j1, j2, hits, how)
        # 'delete' (script words never heard) is estimated below; 'insert' (extra recognizer words) is ignored

    times = []
    for i in range(len(words)):
        js = hits[i]
        if js:
            times.append({"start": min(rtoks[j]["start"] for j in js), "end": max(rtoks[j]["end"] for j in js),
                          "match": how.get(i, "fuzzy")})
        else:
            times.append(None)
    _share_tokens(words, hits, times)
    _interpolate(words, times)
    tidy(times, limit)
    return times


def _fuzzy(words, ws, rtoks, j1, j2, hits, how) -> None:
    """Character-level matching inside one gap; assigns recognizer tokens to the gap's words."""
    r_str, r_map = "", []
    for j in range(j1, j2):
        if r_str:
            r_str += " "
            r_map.append(None)
        r_str += rtoks[j]["t"]
        r_map += [j] * len(rtoks[j]["t"])
    best = None
    for form in ("w", "spoken"):
        s_str, s_map = "", []
        for i in ws:
            toks = " ".join(norm_tokens(words[i][form]))
            if not toks:
                continue
            if s_str:
                s_str += " "
                s_map.append(None)
            s_str += toks
            s_map += [i] * len(toks)
        sm = SequenceMatcher(None, s_str, r_str, autojunk=False)
        if best is None or sm.ratio() > best[0]:
            best = (sm.ratio(), sm, s_map)
    ratio, sm, s_map = best
    if ratio < FUZZY_MIN_RATIO:
        return
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            i, j = s_map[a + k], r_map[b + k]
            if i is not None and j is not None and j not in hits[i]:
                hits[i].append(j)
                how.setdefault(i, "fuzzy")


def _share_tokens(words, hits, times) -> None:
    """Script words that got exactly the same recognizer tokens (one heard word, several written
    ones) share that time by length rather than overlapping."""
    i = 0
    while i < len(words):
        if times[i] is None:
            i += 1
            continue
        j = i
        while j + 1 < len(words) and times[j + 1] is not None and set(hits[j + 1]) == set(hits[i]):
            j += 1
        if j > i:
            start, end = times[i]["start"], times[i]["end"]
            total = sum(max(1, len(words[k]["w"])) for k in range(i, j + 1))
            t = start
            for k in range(i, j + 1):
                d = (end - start) * max(1, len(words[k]["w"])) / total
                times[k] = {**times[k], "start": t, "end": t + d}
                t += d
        i = j + 1


def _interpolate(words, times) -> None:
    """Unmatched words: spread between the matched neighbours, by length."""
    i = 0
    n = len(words)
    while i < n:
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < n and times[j] is None:
            j += 1
        left = times[i - 1]["end"] if i > 0 else (times[j]["start"] - 0.3 * (j - i) if j < n else 0.0)
        right = times[j]["start"] if j < n else left + 0.3 * (j - i)
        left = max(0.0, left)
        right = max(right, left)
        total = sum(max(1, len(words[k]["w"])) for k in range(i, j))
        t = left
        for k in range(i, j):
            d = (right - left) * max(1, len(words[k]["w"])) / total
            times[k] = {"start": t, "end": t + d, "match": "estimated"}
            t += d
        i = j


def tidy(times, limit: float | None = None) -> None:
    """Never backwards, never longer than MAX_WORD, never past `limit` (the audio's end). A long word
    is trimmed on the side that borders silence (Whisper stretches a word into the pause before or
    after it)."""
    prev_end = 0.0
    if limit is not None:
        for t in times:
            t["start"], t["end"] = min(t["start"], limit), min(t["end"], limit)
    for k, t in enumerate(times):
        start, end = max(t["start"], prev_end), t["end"]
        end = max(end, start + 0.01)
        if end - start > MAX_WORD:
            nxt = times[k + 1]["start"] if k + 1 < len(times) else end
            if start - prev_end < 0.1 and nxt - end >= 0:  # tight before: the slack is the pause after
                end = start + MAX_WORD
            else:
                start = end - MAX_WORD
        if limit is not None and end > limit:  # squeezed against the end of the audio
            end, start = limit, min(start, limit - 0.01)
        t["start"], t["end"] = round(start, 3), round(end, 3)
        prev_end = t["end"]
