"""The final script as segments, sentences and words, with ids shared by timing and references.

The script is the source of truth for text. A word keeps its displayed form (the script, markers
removed) and its spoken form (after the narration's pronunciation table and editorial-bracket
repair, which is what the recognizer heard). Dossier markers ([D12], [D03, D07]) are kept per
sentence: they attach to the sentence they follow, as the draft stage writes them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from stages.common import MARKER, entry_numbers, split_script_segments
from stages.tts import apply_pronunciations, speak_editorial_brackets

PAUSE = re.compile(r"\[pause\]", re.I)
SENTINEL = "\x00"
# A sentence ends at . ! ? (and any closing quotes or brackets) when the next word starts a sentence.
END = re.compile(r"[.!?]['\"”’)\]]*$")
STARTS = re.compile(r"^['\"“‘(\[]*[A-Z0-9]")
# Never a sentence end: an initial ("C. D. C. Reeve") or a common abbreviation.
ABBREV = re.compile(r"^['\"“‘(\[]*(?:[A-Z]|Mr|Mrs|Ms|Dr|St|Prof|Jr|Sr|vs|cf|e\.g|i\.e)\.$")


@dataclass
class Word:
    w: str            # displayed: exactly as in the script
    spoken: str       # what the narration said (differs where a pronunciation was substituted)


@dataclass
class Sentence:
    id: str
    words: list[Word]
    dossier: list[str] = field(default_factory=list)  # D-numbers from its markers, in order
    para: int = 0     # its paragraph within the segment (0-based): where a long aside rewinds to

    @property
    def text(self) -> str:
        return " ".join(w.w for w in self.words)


@dataclass
class Segment:
    id: str
    n: int
    sentences: list[Sentence]


def _clean(body: str) -> str:
    """What the narration strips before speaking, except the dossier markers (kept for references)."""
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    body = re.sub(r"^#+\s.*$", "", body, flags=re.M)
    return body.replace("**", "").replace("__", "")


def spoken_form(word: str, pronunciations: dict[str, str]) -> str:
    return speak_editorial_brackets(apply_pronunciations(word, pronunciations))


def parse(script: str, pronunciations: dict[str, str] | None = None) -> list[Segment]:
    table = pronunciations or {}
    segments = []
    for n, body in split_script_segments(script):
        sid, sentences = f"seg{n:02d}", []
        for pi, para in enumerate(re.split(r"\n\s*\n", _clean(body))):
            para = PAUSE.sub(" ", para)
            markers: list[list[str]] = []

            def keep(m: re.Match) -> str:  # glue the marker to the word before it
                markers.append([f"D{k:02d}" for k in entry_numbers(m.group(0))])
                return f"{SENTINEL}{len(markers) - 1}{SENTINEL}"
            para = MARKER.sub(keep, para)
            strip = lambda t: re.sub(SENTINEL + r"\d+" + SENTINEL, "", t)  # noqa: E731
            tokens = para.split()
            displays = [strip(t) for t in tokens]
            words: list[Word] = []
            refs: list[str] = []
            for i, tok in enumerate(tokens):
                found = [d for k in re.findall(SENTINEL + r"(\d+)" + SENTINEL, tok) for d in markers[int(k)]]
                display = displays[i]
                if not display:  # a marker on its own: it belongs to the sentence it follows
                    target = refs if words or not sentences else sentences[-1].dossier
                    target += [d for d in found if d not in target]
                    continue
                words.append(Word(display, spoken_form(display, table)))
                refs += [d for d in found if d not in refs]
                nxt = next((d for d in displays[i + 1:] if d), None)
                if nxt is None or (END.search(display) and not ABBREV.match(display) and STARTS.match(nxt)):
                    sentences.append(Sentence(f"{sid}-s{len(sentences) + 1:03d}", words, refs, pi))
                    words, refs = [], []
        segments.append(Segment(sid, n, sentences))
    return segments
