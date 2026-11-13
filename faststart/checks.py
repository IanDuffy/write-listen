"""Code checks on what the fast writer and editor return. Pure code: no model, no files.

In a fast run the editor writes final text and nothing reads it afterwards: a locked section is
narrated at once. So what the overnight pipeline leaves to stage 8b and to narration's own guards
is checked here, on each section, before it is locked:

    section_problems   acceptance check 8 for one section, plus everything stages/tts.py would
                       stop on (a bracket the voice would read aloud, an unclosed marker)
    move_problems      acceptance check 4: the paired edit may move material between two sections
                       without losing or duplicating it
    loss_problems      an edit that came back shorter than its cuts explain
    repair             what is safe to fix by code when a fix call has not: afterwards
                       section_problems finds nothing, so narration never stops on a section
    chapter_problems   check 8 over the whole script
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from stages import tts
from stages.common import (LEFTOVER_MARKER, MARKER, dossier_entries, entry_id, entry_numbers, entry_tags,
                           spoken_text, word_count)
from stages.coverage import unbacked_quotes
from stages.report import claim_sentences
from stages.research import ELLIPSIS, QUOTE, _norm_text

LONG = 8          # a sentence this long said twice is a duplicate, never a refrain
MOVE_WORDS = 6    # sentences shorter than this are too common to trace a move by
KEEP = 0.8        # the share of the drafts' words an edit must keep, cuts aside

COMMENT = re.compile(r"<!--.*?-->", re.S)
SEG_MARKER = re.compile(r"<!--\s*seg", re.I)
ANY_BRACKET = re.compile(r"\[[^\[\]\n]*\]")
ONE_MARKER = re.compile(r"\[D\d+[^\[\]\n]{0,40}\]")
# '[D12' that never closes. Possessive, so the match is the whole run of numbers or nothing: with
# backtracking a closed marker longer than 40 characters matched in part ('[D0' of '[D01, D02, ...]')
# and repair left the rest of it as prose for the narrator.
UNCLOSED = re.compile(r"\[D\d++(?:[ \t]*[,;][ \t]*D?\d++)*+(?![^\[\]\n]{0,40}\])")
ELLIPSIS_BRACKET = re.compile(r"\[\s*(?:\.\s*){3}\]|\[…\]")
LETTERS = re.compile(r"\[[A-Za-z]{1,12}\]")
TTS_BRACKET = re.compile(r"\[[^\]\n]{0,40}\]")  # the check in tts.narrate_segments
SOURCE = re.compile(r"\bSource:")


def cited(text: str) -> set[int]:
    """The dossier entry numbers a text's [Dn] markers name."""
    return {n for m in MARKER.findall(text) for n in entry_numbers(m)}


def _ids(nums) -> str:
    return ", ".join(entry_id(n) for n in sorted(nums))


def short(text: str, words: int = 16) -> str:
    w = " ".join(text.split()).split(" ")
    return " ".join(w[:words]) + (" ..." if len(w) > words else "")


# --- one section ----------------------------------------------------------------------------

def _speakable(text: str, m: re.Match) -> bool:
    """Whether narration copes with this bracket: a dossier marker (stripped), [pause] (a held
    beat), or a quote's editorial bracket ([sic], [...], [T]he, want[ed]), exactly as
    tts.narrate_segments treats them."""
    tag = m.group()
    if ONE_MARKER.fullmatch(tag) or re.fullmatch(r"\[pause\]", tag, re.I) or re.fullmatch(r"\[sic\]", tag, re.I):
        return True
    if ELLIPSIS_BRACKET.fullmatch(tag):
        return True
    if LETTERS.fullmatch(tag):
        before, after = text[m.start() - 1:m.start()], text[m.end():m.end() + 1]
        return bool(re.match(r"\w", before) or re.match(r"\w", after))
    return False


def narrator_problems(text: str) -> list[str]:
    """What stages/tts.py would stop on before paying, or read aloud: the same rules as
    narrate_segments (only [pause] and editorial brackets survive), plus any stray bracket."""
    out = []
    clean = spoken_text(text)
    if m := LEFTOVER_MARKER.search(clean):
        out.append("a dossier marker is left unclosed and would be read aloud, near "
                   f"{clean[max(0, m.start() - 40):m.start() + 20]!r}")
    for piece in tts.split_beats(clean):
        piece = tts.speak_editorial_brackets(piece)
        for tag in TTS_BRACKET.findall(piece):
            out.append(f"a bracketed tag the narrator would read aloud: {tag!r} (only [pause] is understood)")
        rest = re.sub(r"\[D\d+", "", TTS_BRACKET.sub("", piece))
        at = min((i for i in (rest.find("["), rest.find("]")) if i >= 0), default=-1)
        if at >= 0:
            out.append(f"a stray square bracket the narrator would read, near {rest[max(0, at - 30):at + 30]!r}")
    return out


def _quote_home(q: str, entries: dict[int, str]) -> int | None:
    """The entry whose text holds a quotation's words, verbatim-tagged or not."""
    pieces = [p for p in (_norm_text(x) for x in ELLIPSIS.split(q)) if p]
    for n, t in entries.items():
        body = " " + _norm_text(SOURCE.split(t, maxsplit=1)[0]) + " "
        if pieces and all(f" {p} " in body for p in pieces):
            return n
    return None


def _own_words(q: str, said: str) -> bool:
    """Whether a quoted passage is the listener's own words: each piece of it stands word for word
    in what they said. Their words are not a quotation from a source, so no entry has to back them."""
    if not said:
        return False
    pieces = [p for p in (_norm_text(x) for x in ELLIPSIS.split(q)) if p]
    body = f" {_norm_text(said)} "
    return bool(pieces) and all(f" {p} " in body for p in pieces)


def _unbacked(text: str, dossier_text: str, said: str = "") -> list[str]:
    """The quotations no `verbatim: yes` entry backs, the listener's own words aside."""
    return [q for _, q in unbacked_quotes(text, dossier_text) if not _own_words(q, said)]


def section_problems(text: str, dossier_text: str, allowed: set[int] | None = None, said: str = "") -> list[str]:
    """Acceptance check 8 for one section: every quotation is backed by a `verbatim: yes` entry;
    every [Dn] the text cites exists in the dossier; with `allowed` (the opening: scout numbers
    only) every cited number is in it; nothing the narrator would choke on; no '<!-- seg' marker
    inside the text. [] when the section is fit to lock. `said` is what the listener said (the
    seed, the outline's listener_words, the listener notes): a passage found word for word in it
    is their own words echoed back, not a quotation, and is not counted."""
    if not spoken_text(text).strip():
        return ["the section has no text"]
    out = []
    if SEG_MARKER.search(text):
        out.append("the text holds a '<!-- seg' marker; a section's text has none")
    entries = dossier_entries(dossier_text)
    nums = cited(text)
    for n in sorted(nums - set(entries)):
        out.append(f"cites {entry_id(n)}, which is not in the dossier")
    if allowed is not None:
        for n in sorted((nums & set(entries)) - set(allowed)):
            out.append(f"cites {entry_id(n)}, which is not a scout entry (the opening may cite only the scout's)")
    for q in _unbacked(COMMENT.sub("", text), dossier_text, said):
        home = _quote_home(q, entries)
        if home is not None and entry_tags(entries[home])["verbatim"] != "yes":
            out.append(f"a quotation whose only source, {entry_id(home)}, is not marked verbatim: yes, so it may "
                       f"not be quoted: \"{q}\"")
        else:
            out.append(f"a quotation that matches the quoted words of no verbatim: yes entry: \"{q}\"")
    return out + narrator_problems(text)


def chapter_problems(sections: list[tuple[int, str]], dossier_text: str, said: str = "") -> list[str]:
    """Check 8 over a whole script: section_problems for every section, each named."""
    return [f"section {n}: {p}" for n, text in sections for p in section_problems(text, dossier_text, said=said)]


# --- repairs by code ------------------------------------------------------------------------

def repair(text: str, dossier_text: str, allowed: set[int] | None = None,
           said: str = "") -> tuple[str, list[str]]:
    """Fix by code what a fix call left behind, where that is safe, and say what was done:
    a marker for an entry that does not exist (or, for the opening, is not the scout's) is
    dropped; an unbacked quotation loses its quotation marks, so it is no longer presented as
    someone's exact words; a bracket the narrator would read, an unclosed marker and a segment
    marker are removed. Sentences are never cut or reworded here: that is an editor's call.
    A text with none of these problems comes back untouched. `said` as in section_problems: the
    listener's own words keep their quotation marks."""
    notes: list[str] = []
    if COMMENT.search(text) or SEG_MARKER.search(text):
        text = re.sub(r"<!--\s*seg[^\n]*", "", COMMENT.sub("", text), flags=re.I)
        notes.append("removed a comment or segment marker from the text")
    text = _repair_brackets(text, dossier_entries(dossier_text), allowed, notes)
    text = _repair_quotes(text, dossier_text, notes, said)
    if notes:  # close the gaps the removals left
        text = re.sub(r"[ \t]+([,.;:!?])", r"\1", re.sub(r"[ \t]{2,}", " ", text))
        text = re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", text)).strip()
    return text, notes


def _repair_brackets(text: str, entries: dict[int, str], allowed: set[int] | None, notes: list[str]) -> str:
    def bracket(m: re.Match) -> str:
        tag = m.group()
        if ONE_MARKER.fullmatch(tag):
            nums = entry_numbers(tag)
            keep = [n for n in nums if n in entries and (allowed is None or n in allowed)]
            if keep == nums:
                return tag
            gone = [n for n in nums if n not in keep]
            why = "not in the dossier" if any(n not in entries for n in gone) else "not a scout entry"
            notes.append(f"dropped the marker for {_ids(gone)} ({why})")
            return "[" + ", ".join(entry_id(n) for n in keep) + "]" if keep else ""
        if _speakable(text, m):
            return tag
        notes.append(f"removed a bracketed tag the narrator would read: {tag!r}")
        return ""

    for _ in range(20):  # to a fixed point: taking one bracket out can leave the pair around it, or bare what was beside it
        before = text
        if UNCLOSED.search(text):
            text = UNCLOSED.sub("", text)
            notes.append("removed an unclosed dossier marker")
        text = ANY_BRACKET.sub(bracket, text)
        kept = [m.span() for m in ANY_BRACKET.finditer(text)]
        stray = {i for i, c in enumerate(text) if c in "[]" and not any(a <= i < b for a, b in kept)}
        if stray:
            text = "".join(c for i, c in enumerate(text) if i not in stray)
            notes.append("removed a stray square bracket")
        if text == before:
            break
    if narrator_problems(text):  # whatever is left: only markers and held beats keep their brackets
        kept = [m.span() for m in re.finditer(r"\[D\d+[^\[\]\n]{0,40}\]|\[pause\]", text, re.I)]
        text = "".join(c for i, c in enumerate(text) if c not in "[]" or any(a <= i < b for a, b in kept))
        notes.append("removed the square brackets the narrator could not read")
    return text


def _repair_quotes(text: str, dossier_text: str, notes: list[str], said: str = "") -> str:
    """Take the marks off each unbacked quotation. Which marks pair with which depends on every
    mark before them, so each pair is tried in turn and kept off if the unbacked count falls."""
    for _ in range(50):
        bad = len(_unbacked(text, dossier_text, said))
        if not bad:
            break
        for m in QUOTE.finditer(text):
            trial = text[:m.start()] + m.group(1) + text[m.end():]
            if len(_unbacked(trial, dossier_text, said)) < bad:
                notes.append("took the quotation marks off a quotation no verbatim: yes entry backs: "
                             f"\"{short(spoken_text(m.group(1)))}\"")
                text = trial
                break
        else:  # no single pair accounts for it (marks that do not pair up): none may stand
            text = re.sub(r"[\"“”]", "", text)
            notes.append("removed every quotation mark: the marks did not pair up around a backed quotation")
            break
    return text


# --- an edit of one or two sections ---------------------------------------------------------

def sentences(text: str) -> list[str]:
    """A text's spoken sentences, normalised for matching (lower case, no punctuation, no markers,
    no held beats)."""
    out = []
    for para in re.split(r"\n\s*\n", spoken_text(text)):
        for s in tts.split_sentences(" ".join(tts.PAUSE.sub(" ", para).split())):
            if n := _norm_text(s):
                out.append(n)
    return out


def _near(a: str, b: str, at: float) -> bool:
    return a == b or SequenceMatcher(None, a.split(), b.split()).ratio() >= at


def _cut_text(cut: list) -> str:
    """The declared cuts as one normalised text. A cut is the passage removed (a string, which may
    carry its reason after it) or {"text": ..., "reason": ...}."""
    parts = [c.get("text", "") if isinstance(c, dict) else str(c) for c in cut or []]
    return " " + " ".join(_norm_text(spoken_text(p)) for p in parts) + " "


def _cut_words(cut: list) -> int:
    return sum(word_count(c.get("text", "") if isinstance(c, dict) else str(c)) for c in cut or [])


def is_loss(problem: str) -> bool:
    """Whether a problem says text went missing. It is the one kind code cannot repair, so it
    outranks every other when an edit and its fix are compared (faststart/sections.py _weight)."""
    return problem.startswith(("text was lost", "the section has no text")) or "came back empty" in problem


def loss_problems(before: list[str], after: list[str], cut: list, keep: float = KEEP) -> list[str]:
    """An edit keeps the text it was given: its outputs together hold at least 80% (`keep`) of the
    inputs' words unless declared cuts account for the difference. Catches a final text that came
    back as a fragment or a summary."""
    was, now = sum(word_count(t) for t in before), sum(word_count(t) for t in after)
    gone = _cut_words(cut)
    if was and now + gone < keep * was:
        return [f"text was lost: {was} words went in and {now} came back, with {gone} declared cut. Restore what "
                "is missing, or declare each removed passage in cut with its reason"]
    return []


def move_problems(before: str, before_next: str, after: str, after_next: str, moved: list[dict],
                  cut: list[str]) -> list[str]:
    """Acceptance check 4: the lock may move material between two sections without losing or
    duplicating it. No sentence of 8+ words (normalised) appears in both outputs or twice in one;
    each declared move's text is found in its destination and is not left in its source, in the
    wording it arrived with or the wording it had; every [Dn] cited in the inputs is still cited
    in an output, or the passage that carried it is declared in `cut` or is still said (as it was,
    or reworded with another of its markers kept: the editor may take a marker off a sentence the
    entry does not support); the two outputs together keep at least 80% of the inputs' words
    unless cuts account for the difference, and so does each section on its own, declared moves
    aside (pooled, an unchanged neighbour would hide a section that came back 40% short).
    `before`/`after` are the section being locked, `before_next`/`after_next` the one after it;
    a move's "from" and "to" are section numbers, so the lower of the two is the section being locked."""
    out = []
    here, there = sentences(after), sentences(after_next)
    if not there and spoken_text(before_next).strip():
        out.append("the revised draft of the next section came back empty")

    seen_here, seen_there, flagged = set(), set(), set()
    missing = False  # a passage already reported as gone: the per-section word rule would say it twice
    for s, seen, where in [(s, seen_here, "the section") for s in here] + \
                          [(s, seen_there, "the next section's draft") for s in there]:
        if len(s.split()) < LONG:
            continue
        if s in seen and s not in flagged:
            flagged.add(s)
            out.append(f"a sentence appears twice in {where}: \"{short(s)}\"")
        seen.add(s)
    for s in sorted((seen_here & seen_there) - flagged, key=here.index):
        out.append(f"a sentence appears in both sections; keep it in one place only: \"{short(s)}\"")

    for mv in moved or []:
        text, a, b = str(mv.get("text", "")), mv.get("from"), mv.get("to")
        if not isinstance(a, int) or not isinstance(b, int) or a == b:
            out.append(f"a move must name two different sections (from {a!r} to {b!r}): \"{short(text)}\"")
            continue
        back = b < a  # from the next section into the one being locked
        dest, source, source_was = (here, there, sentences(before_next)) if back else (there, here, sentences(before))
        parts = [s for s in sentences(text) if len(s.split()) >= MOVE_WORDS]
        if not parts:  # a fragment: look for it whole
            whole = _norm_text(spoken_text(text))
            if not whole:
                out.append("a move was declared with no text")
            elif f" {whole} " not in f" {' '.join(dest)} ":
                out.append(f"the moved passage is not in section {b}: \"{short(text)}\"")
            elif f" {whole} " in f" {' '.join(source)} ":
                out.append(f"the moved passage is still in section {a} as well: \"{short(text)}\"")
            continue
        if absent := [p for p in parts if not any(_near(p, s, 0.7) for s in dest)]:
            missing = True
            out.append(f"the passage declared moved to section {b} is not there: \"{short(absent[0])}\"")
        # Left behind: in the wording it arrived with, or in the wording it had in the source's draft.
        as_it_was = {s for s in source_was if any(_near(p, s, 0.7) for p in parts)}
        left = [s for s in source if s in as_it_was or any(_near(p, s, 0.85) for p in parts)]
        if left and not any(f"\"{short(s)}\"" in p for s in left for p in out):
            out.append(f"the passage moved to section {b} is still in section {a} as well: \"{short(left[0])}\"")

    still = cited(after) | cited(after_next)
    gone = (cited(before) | cited(before_next)) - still
    if gone:
        declared = _cut_text(cut)
        now = [(s[-1], set(refs)) for t, refs in claim_sentences(after + "\n\n" + after_next) if (s := sentences(t))]
        for n in sorted(gone):
            carried = [(t, set(refs)) for t, refs in claim_sentences(before + "\n\n" + before_next) if n in refs]
            # The sentence that carried it: gone unless it is declared cut or still said, as it was
            # or reworded (near enough, or a sentence that overlaps it and keeps another of its markers).
            lost = [t for t, refs in carried if (s := sentences(t)) and f" {s[-1]} " not in declared
                    and not any(_near(s[-1], x, 0.6) for x in here + there)
                    and not any(kept & (refs - {n}) and _near(s[-1], x, 0.3) for x, kept in now)]
            if lost:
                missing = True
                out.append(f"{entry_id(n)} was cited in the drafts and is cited in neither section now, and the "
                           f"passage that carried it is not declared cut: \"{short(lost[0])}\"")
    if pooled := loss_problems([before, before_next], [after, after_next], cut):
        return out + pooled
    if missing:
        return out
    # Each section on its own, net of declared moves. Cuts do not say which draft they came from,
    # so all of them are credited to both.
    valid = [m for m in moved or [] if isinstance(m.get("from"), int) and isinstance(m.get("to"), int)
             and m["from"] != m["to"]]
    low = min((min(m["from"], m["to"]) for m in valid), default=None)  # the section being locked
    went = sum(word_count(str(m.get("text", ""))) for m in valid if m["from"] == low)  # out of it, into the next
    came = sum(word_count(str(m.get("text", ""))) for m in valid if m["to"] == low)    # into it, out of the next
    gone = _cut_words(cut)
    for was, is_now, gave, took, name in ((before, after, went, came, "the section"),
                                          (before_next, after_next, came, went, "the next section's draft")):
        w, a = word_count(was), word_count(is_now)
        if w and a - took + gave + gone < KEEP * w:
            out.append(f"text was lost from {name}: {w} words went in and {a} came back, with {gave} moved out, "
                       f"{took} moved in and {gone} declared cut. Restore what is missing, or declare each removed "
                       "passage in cut with its reason")
    return out
