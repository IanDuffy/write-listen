"""The chapter as the live session reads it: paragraphs, restart points, the context for a question,
and the reading check.

A fast run's locked sections (06_segments/segNN.md: "ready" means locked, with no narration step)
become paragraphs with ids `s<section>-p<nn>` (1-based), cleaned exactly as narration cleaned them
(claim markers stripped, [pause] beats split, editorial brackets made speakable) and cut so each
fits in one GPT-Live append (500 tokens). Pronunciations are not spelled into the text any more:
they are in the guide's instructions (voice/gpt_live/instructions/70_pronunciation.md).

The page feeds paragraphs one at a time, tracks the one being heard from the output transcript, and
on a tap asks for `context` (the interrupted sentence, about 90 seconds just read, about 3 minutes
coming, the outline) and later for a `restart` point. `check` compares what the guide said with the
text and logs the deviations to runs/<run>/live/reading.jsonl (for tuning the prompt; nothing is
re-read live).
"""

from __future__ import annotations

import difflib
import json
import re
import threading
import time
from pathlib import Path

from faststart import base, sections
from faststart.narrate import BRACKET, _fit
from stages import tts
from stages.common import LEFTOVER_MARKER, Run, StageError, spoken_text

from . import session as live_session

PARA_MAX_CHARS = 1400  # about 350 words: well inside one 500-token append
WORDS_PER_SECOND = 2.5  # 150 words a minute, the chapter's pace (chapter.words_per_minute)
LOG_LOCK = threading.Lock()


def clean_paragraphs(text: str, n: int) -> list[dict]:
    """Section n's locked text as paragraphs: {"id", "text", "sentences", "beat"} (beat: it opens
    after a [pause])."""
    clean = spoken_text(text)
    if m := LEFTOVER_MARKER.search(clean):
        raise StageError(f"Section {n} still has a dossier marker after stripping, near "
                         f"{clean[max(0, m.start() - 40):m.start() + 20]!r}.")
    out = []
    for b, beat in enumerate(tts.split_beats(clean)):
        beat = tts.speak_editorial_brackets(beat)
        if m := BRACKET.search(beat):
            raise StageError(f"Section {n} has a bracketed tag the guide would read aloud: {m.group()!r}.")
        first = True
        for para in re.split(r"\n\s*\n", beat):
            para = re.sub(r"\s+", " ", para).strip()
            if not para:
                continue
            for piece in _fit(para, PARA_MAX_CHARS):
                out.append({"id": f"s{n}-p{len(out) + 1:02d}", "text": piece,
                            "sentences": tts.split_sentences(piece), "beat": b > 0 and first})
                first = False
    return out


def paragraphs(r: Run) -> dict:
    """The run as the page reads it: {"run", "title", "sections": [{"n", "title", "locked",
    "paragraphs": [...] (empty until locked)}]}."""
    outline = base.read_json(base.path(r, "outline_json")) or {"sections": []}
    out = []
    for s in outline["sections"]:
        text = sections.locked_text(r, s["n"])
        out.append({"n": s["n"], "title": s.get("title", ""), "locked": text is not None,
                    "paragraphs": clean_paragraphs(text, s["n"]) if text else []})
    return {"run": r.name, "title": outline.get("title", ""), "sections": out}


def _flat(chapter: dict) -> list[tuple[int, dict]]:
    return [(s["n"], p) for s in chapter["sections"] for p in s["paragraphs"]]


def find(chapter: dict, pid: str) -> tuple[int, int, dict] | None:
    """(section, index in the flat list, paragraph) for an id."""
    for i, (n, p) in enumerate(_flat(chapter)):
        if p["id"] == pid:
            return n, i, p
    return None


def _words(text: str) -> list[str]:
    return text.split()


def restart(chapter: dict, pid: str, sentence: int, seconds: float, rules: dict,
            rewind: str | None = None) -> dict:
    """Where the reading picks up after a conversation: {"id", "sentence", "rule"}. The ask-by-voice
    rewind table, by sentence instead of time (live reading has no word timing yet): under
    `bands[0]` seconds the interrupted sentence, up to `bands[1]` one sentence earlier, over that
    the paragraph's start; `rewind` ("sentence", "previous", "paragraph") overrides; never back
    across a section's start."""
    hit = find(chapter, pid)
    if hit is None:
        raise StageError(f"No paragraph {pid} in this chapter.")
    n, i, p = hit
    sentence = max(0, min(sentence, len(p["sentences"]) - 1))
    lo, hi = rules["restart_bands_secs"]
    rule = rewind if rewind in ("sentence", "previous", "paragraph") else (
        "sentence" if seconds < lo else "previous" if seconds <= hi else "paragraph")
    if rule == "paragraph":
        return {"id": pid, "sentence": 0, "rule": rule}
    if rule == "previous":
        if sentence > 0:
            return {"id": pid, "sentence": sentence - 1, "rule": rule}
        flat = _flat(chapter)
        if i > 0 and flat[i - 1][0] == n:  # the last sentence of the paragraph before, same section
            prev = flat[i - 1][1]
            return {"id": prev["id"], "sentence": len(prev["sentences"]) - 1, "rule": rule}
        return {"id": pid, "sentence": 0, "rule": rule}
    return {"id": pid, "sentence": sentence, "rule": rule}


def text_from(p: dict, sentence: int) -> str:
    """A paragraph from one of its sentences on (a restart mid-paragraph)."""
    return " ".join(p["sentences"][max(0, sentence):]) or p["text"]


def context(chapter: dict, outline: dict | None, pid: str, sentence: int) -> str:
    """What the guide is told, silently, once the listener has started asking mid-chapter: where it
    stopped, the sentence it was reading, and the parts by title (written or not).

    The spec asked for the last 90 seconds and the next 3 minutes word for word and the outline. In
    the one session the guide has already heard everything it read, and with the rest GPT-Live read on
    after the tap, or volunteered what the outline said was coming, in nearly every graded aside
    (persona tuning, 2026-11-14). So what is coming is left out: a question about it is answered with
    "the chapter gets there", which is what the rules ask for anyway."""
    hit = find(chapter, pid)
    if hit is None:
        raise StageError(f"No paragraph {pid} in this chapter.")
    n, i, p = hit
    sentence = max(0, min(sentence, len(p["sentences"]) - 1))
    parts = "; ".join(f"part {s['n']}, \"{s['title']}\" ({'written' if s['locked'] else 'still being written'})"
                      for s in chapter["sections"])
    return "\n".join([
        "For answering their question only. Never read, retell or add to the chapter aloud, and never say what"
        " is coming in it: if they ask, the chapter gets there.",
        f"Chapter: {chapter.get('title', '')}. They tapped in part {n} ({dict((s['n'], s['title']) for s in chapter['sections']).get(n, '')}),"
        f" while you were reading: \"{p['sentences'][sentence]}\"",
        f"The parts: {parts}.",
    ])


# --- the reading check ---------------------------------------------------------------------

def _norm(text: str) -> list[str]:
    """Words as page/align.js compares them: lower case, hyphenated words joined (the transcript
    writes "relistening" for "re-listening"), other punctuation dropped."""
    t = re.sub(r"([a-z0-9])-(?=[a-z0-9])", r"\1", text.lower().replace("’", "'").replace("‘", "'"))
    return [w.strip("'") for w in re.sub(r"[^a-z0-9' ]+", " ", t).split() if w.strip("'")]


_NUM = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
        "seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand "
        "million and oh").split()
_NUM_WORD = re.compile("^(?:" + "|".join(sorted(_NUM, key=len, reverse=True)) + ")+$")


def _same_number(words: list[str], heard: list[str]) -> bool:
    """A number the script spells out and the transcript writes in digits ("nineteen ninety-eight" /
    "1998"): the voice said it; only the transcription differs."""
    return bool(words) and bool(heard) and all(w.isdigit() for w in heard) and all(_NUM_WORD.match(w) for w in words)


def deviation(text: str, heard: str) -> dict:
    """How what was said differs from the text, word by word: {"words", "missing", "added",
    "changed", "same" (word for word), "diffs": [[op, text words, heard words], ...]}."""
    a, b = _norm(text), _norm(heard)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    diffs, missing, added, changed = [], 0, 0, 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal" or (op == "replace" and ("".join(a[i1:i2]) == "".join(b[j1:j2])
                                                  or _same_number(a[i1:i2], b[j1:j2]))):
            continue  # the same words, written together or apart ("check in" / "checkin"), or in digits
        diffs.append([op, " ".join(a[i1:i2]), " ".join(b[j1:j2])])
        if op == "delete":
            missing += i2 - i1
        elif op == "insert":
            added += j2 - j1
        else:
            changed += max(i2 - i1, j2 - j1)
    return {"words": len(a), "missing": missing, "added": added, "changed": changed,
            "same": not diffs, "diffs": diffs}


def log_path(r: Run) -> Path:
    return r.root / "live" / "reading.jsonl"


def check(r: Run, chapter: dict, pid: str, heard: str, sentence: int = 0, cut: bool = False,
          times: dict | None = None, few: int = 3, live_id: str | None = None) -> dict:
    """Compare a paragraph's output transcript with its text (from `sentence` on, for a restart) and
    log it. A paragraph cut short by a tap (`cut`) is compared only as far as it got. `times`: the
    session-relative ms the transcript spanned, and `live_id` the session, kept for the archive (the
    session's recording runs on the same clock)."""
    hit = find(chapter, pid)
    if hit is None:
        raise StageError(f"No paragraph {pid} in this chapter.")
    text = text_from(hit[2], sentence)
    if cut:  # only as far as it got
        n = len(_norm(heard))
        words = text.split()
        text = " ".join(words[:max(n, 1)])
    d = deviation(text, heard)
    d["deviates"] = (d["missing"] + d["added"] + d["changed"]) > few
    rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "id": pid, "sentence": sentence, "cut": cut,
           "heard": heard, **d, **({"times": times} if times else {}), **({"live_id": live_id} if live_id else {})}
    with LOG_LOCK:
        p = log_path(r)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def append_chunks(text: str) -> list[str]:
    return live_session.split_for_append(text)
