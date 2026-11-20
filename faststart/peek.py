"""A peek at what a fast chapter being made is working on, for the guide's get_status (Ian, 2026-11-20:
"it says writing the intro now... it might be good to see at least something to tease what's being
written").

peek(run_root, status) -> {"about", "working_on", "looking_into", "found", "question"}, each key only when
there is something to say, all of it by code from files the run has already written: no model call, no
clock, no network. Only while a fast run is being made (not queued); {} otherwise.

    about         fast/card.json's overview, first sentence; only until 05_outline.json exists
    working_on    'part N, "<title>"' (N: 1 before the first audio, else the first part not ready), plus
                  ': <what the part settles>' from the outline's "lands" when it is short enough; never
                  for the last part, whose "lands" is the chapter's answer
    looking_into  up to two research questions under way: rows with a research_start and no later
                  research_done in fast_timeline.jsonl (fast/state.json is rewritten only when a status
                  field changes, so a row that has landed can read "running" there for minutes); with
                  none running, the working part's rows still to research. Only what reads as a question
                  (after "to settle:", "Settle", "Check", "The section settles", or starting how, what,
                  whether...): a row's idea that is a claim is the part's finding, not a question
    found         one short finding already in 04_dossier.md, preferring the working part's newest rows;
                  the last part's rows too (in a two-part tester chapter that is the only fresh one; Ian's
                  check 2026-11-20 heard the same part-1 finding for three minutes), but never one that echoes
                  where the chapter ends (most of its longer words in the last part's "lands" or the outline's
                  closing_idea), never a research note (ids, tiers, "the scout", a citation), a sentence cut
                  short, or one leaning on an unnamed source ("the paper describes...")
    question      05_outline.json's through_line

It never says closing_idea or the last part's "lands" (it reads them only to keep a finding that echoes them
out), nor reads hand_off, sets_up, notes, drafts, segments, locks or listener notes, so it cannot give the
ending away. Pipeline words
("the section settles", "the narrator") are stripped or the string is dropped. Each string is short (WORDS_EACH; question and about
WORDS_LONG) and the whole at most WORDS_ALL words, filled in priority order. A missing or broken file
just leaves its key out; peek() never raises. A rehearsal (faststart/rehearsal.py) writes the same files
in the same shapes, so it needs no case of its own.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from stages.common import dossier_entries, entry_tags

from . import base

WORDS_EACH = 20   # lands, each looking_into item, found
WORDS_LONG = 25   # question, about
WORDS_ALL = 60    # the whole peek

CARD = "fast/card.json"
DOSSIER = "04_dossier.md"
LEDGER = "ledger.json"

# A finding that leans on what came before it ("the same source", "this heron") means nothing alone.
BACK_WORDS = {"same", "also", "this", "these", "those", "it", "its", "they", "their", "he", "she", "his",
              "her", "opening", "chapter", "section", "listener", "episode"}
# A research note, not a finding: what the researchers said about their sources.
NOTE_WORDS = {"tier", "scout", "snippet", "cites", "cited", "abstract", "citation", "citations", "journal",
              "fetch", "fetched", "verbatim", "quoted", "pages", "i"}  # "i": the researcher ("the pages I could read")
NOTE_IDS = re.compile(r"\b[DL]\d+\b")
# An unnamed source the sentence leans on ("The paper describes two distinct phases"): means nothing alone.
UNNAMED = re.compile(r"\b(?:the|this|that) (?:paper|study|article|authors?|report|book|piece|source|text|review|"
                     r"researchers?|team|survey)\b", re.I)
ECHO_SHARE = 0.5  # a finding with at least this share of its longer words in where the chapter ends is held back
# Pipeline framing the guide would repeat: a string with these words is dropped.
PIPELINE_WORDS = {"narrator", "narrator's", "section", "sections", "chapter", "listener", "episode"}
FINDING_KINDS = ("claim", "material")
# How the outline prompts open a row's idea or a part's "lands": stripped, and what follows is a question.
LEAD = re.compile(r"^(?:(?:the|this) section (?:settles|asks|shows)(?:\s+is)?|what the section settles is"
                  r"|to settle|settle|check)\b\s*:?\s*", re.I)
QUESTION_WORDS = {"how", "what", "whether", "why", "when", "where", "which", "who", "whose", "if"}
SETTLE = re.compile(r"to settle:", re.I)
# A sentence ends at . ! ? (with any closing quote or bracket) before a capital, unless the word before
# the stop is an initial, a common abbreviation, or has a dot inside it (N.Y.S., U.S.A.).
SENTENCE_END = re.compile(r"([.!?][\"'\u201d\u2019)\]]?)\s+(?=[\"'\u201c\u2018(]?[A-Z0-9])")
ABBREVIATIONS = {"al", "e.g", "i.e", "vs", "dr", "st", "mr", "mrs", "ms", "no", "fig", "approx", "ca", "cf",
                 "jr", "sr", "prof", "vol", "pp", "ed", "eds", "etc", "inc", "co", "u.s", "u.k"}


def words(text: str) -> int:
    return len(str(text or "").split())


def first_sentence(text: str) -> str:
    text = " ".join(str(text or "").split())
    for m in SENTENCE_END.finditer(text):
        before = text[:m.start(1)].rsplit(" ", 1)[-1].strip("(\"'\u201c\u2018").lower()
        if (len(before) == 1 and before.isalpha()) or before in ABBREVIATIONS or "." in before:
            continue
        return text[:m.end(1)]
    return text


def clean(text: str) -> bool:
    return bool(text) and "[" not in text and "Source:" not in text


def _words_in(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z]+(?:'s)?", text)}


def balanced(text: str) -> bool:
    """No bracket or quote left open (a sentence cut short)."""
    return (text.count("(") == text.count(")") and text.count('"') % 2 == 0
            and text.count("\u201c") == text.count("\u201d"))


def plain(text: str) -> bool:
    """Fit to say: clean, nothing left open, no pipeline framing."""
    return clean(text) and balanced(text) and not (_words_in(text) & PIPELINE_WORDS)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _timeline(root: Path) -> list[dict]:
    try:
        lines = (root / base.FILES["timeline"]).read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _lowered(text: str) -> str:
    """'Light bends...' -> 'light bends...' after a colon; a name ("Higuchi's"), "I" or an acronym keeps
    its capital."""
    first = text.split(" ", 1)[0]
    if first == "I" or first.endswith("'s") or (len(first) > 1 and first[1].isupper()):
        return text
    return text[:1].lower() + text[1:]


def lands(section: dict) -> str | None:
    """What a part settles, short: its "lands", first sentence, without "This section settles"; cut at its
    first ';' when long; None when still too long."""
    text = first_sentence(section.get("lands") or "")
    text = LEAD.sub("", text).strip()
    if words(text) > WORDS_EACH and ";" in text:
        text = text.split(";", 1)[0]
    text = text.strip().rstrip(" ;,.")
    if not text or words(text) > WORDS_EACH or not plain(text):
        return None
    return _lowered(text)


def question_of(row: dict) -> str | None:
    """A ledger row's research question in a few spoken words: its idea after "to settle:" when that says
    enough (8 words or more), else what comes before it (6 or more). The outline prompts' openings ("The
    section settles", "Settle", "Check"...) are stripped, and what is left must read as a question: it
    followed one of them, or starts with how, what, whether... (an idea that is a claim is the part's
    finding, not what it is looking into). None when nothing reads alone."""
    idea = " ".join(str(row.get("idea") or "").split())
    if not idea:
        return None
    parts = SETTLE.split(idea, 1)
    after = parts[1].strip() if len(parts) > 1 else ""
    before = parts[0].strip(" ;,.") if len(parts) > 1 else idea.strip(" ;,.")
    if words(after) >= 8:
        text, asked = after, True
    elif words(before) >= 6:
        text, asked = before, False
    else:
        return None
    led = LEAD.sub("", text)
    asked = asked or led != text
    text = led.rstrip().rstrip(".").strip()
    first = text.split(" ", 1)[0].strip(",").lower() if text else ""
    if not (asked or first in QUESTION_WORDS):
        return None
    if words(text) < 4 or words(text) > WORDS_EACH or not plain(text):
        return None
    return text


def finding(entry: str) -> str | None:
    """The first sentence after a dossier entry's tag, if it stands alone and is short."""
    body = re.sub(r"^[ \t]*(?:[-*]|\d+\.)?[ \t]*\**\[D0*\d+\]\**[ \t]*(?:\([^)]*\))?", "", entry).strip()
    text = first_sentence(body)
    n = words(text)
    if n < 9 or n > WORDS_EACH or not clean(text) or not balanced(text) or NOTE_IDS.search(text):
        return None
    said = {w.lower() for w in re.findall(r"[A-Za-z]+", text)}
    if said & BACK_WORDS or said & NOTE_WORDS or UNNAMED.search(text):
        return None
    last = text.rstrip(".!?\"'\u201d\u2019)]").rsplit(" ", 1)[-1]
    if "." in last:  # cut inside an abbreviation the sentence splitter did not know
        return None
    return text


def block_order(dossier: str) -> list[str]:
    """The rows whose '### Lnn' blocks are in the dossier, in file order (newest last: it is append-only)."""
    return [m.group(1) for m in re.finditer(r"^###\s+(L\d+)\b", dossier, re.M)]


def long_words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'-]{4,}", text or "")}


def echoes(text: str, ending: set[str]) -> bool:
    """True when most of the finding's longer words are in where the chapter ends (the last part's lands, the
    closing idea): saying it would give the ending away."""
    mine = long_words(text)
    return bool(mine) and len(mine & ending) >= ECHO_SHARE * len(mine)


def _found(root: Path, landed: set[str], part_rows: list[str], timeline: list[dict],
           hidden: set[str] = frozenset(), ending: set[str] = frozenset()) -> str | None:
    """`hidden`: rows never drawn on. `ending`: the longer words of where the chapter ends; a finding that
    echoes them is skipped."""
    try:
        dossier = (root / DOSSIER).read_text()
    except OSError:
        return None
    order = block_order(dossier)
    if base.SCOUT_ROW in order or any(e.get("event") == "scout_done" for e in timeline):
        landed = landed | {base.SCOUT_ROW}
    newest = sorted((r for r in landed if r != base.SCOUT_ROW and r in order and r not in hidden),
                    key=order.index, reverse=True)
    rows = [r for r in newest if r in part_rows] + [r for r in newest if r not in part_rows]
    if base.SCOUT_ROW in landed:
        rows.append(base.SCOUT_ROW)
    entries = sorted(dossier_entries(dossier).items())
    for row in rows:
        best = 3 if row == base.SCOUT_ROW else 2
        for _, entry in entries:
            tags = entry_tags(entry)
            if row not in tags["rows"] or tags["kind"] not in FINDING_KINDS:
                continue
            if not isinstance(tags["tier"], int) or tags["tier"] > best:
                continue
            text = finding(entry)
            if text and not echoes(text, ending):
                return text
    return None


def _peek(root: Path, status: dict) -> dict:
    outline = _read_json(root / base.FILES["outline_json"])
    outline = outline if isinstance(outline, dict) else None
    timeline = _timeline(root)
    picked: dict = {}

    # about: the card's overview, only until the outline says more
    if outline is None:
        card = _read_json(root / CARD)
        text = first_sentence(card.get("overview") or "") if isinstance(card, dict) else ""
        if text and words(text) <= WORDS_LONG and clean(text):
            picked["about"] = text
    # question: what the chapter is chasing
    if outline is not None:
        text = " ".join(str(outline.get("through_line") or "").split())
        if text and words(text) <= WORDS_LONG and clean(text):
            picked["question"] = text

    # working_on: the part being written
    n = None
    listed = [s for s in (status.get("sections") or []) if isinstance(s, dict)]
    if not status.get("first_audio"):
        n = 1 if (listed or outline) else None
    else:
        n = next((s.get("n") for s in listed if not s.get("ready")), None)
    parts = [s for s in (outline or {}).get("sections") or [] if isinstance(s, dict)]
    numbers = [s.get("n") for s in parts if isinstance(s.get("n"), int)] or \
        [s.get("n") for s in listed if isinstance(s.get("n"), int)]
    last = max(numbers) if numbers else None
    if n is not None:
        title = next((s.get("title") for s in listed if s.get("n") == n), None)
        part = next((s for s in parts if s.get("n") == n), {})
        title = title or part.get("title")
        if title and clean(str(title)):
            text = f'part {n}, "{title}"'
            # the last part's "lands" is where the chapter ends up: its title alone
            said = lands(part) if part and n != last else None
            picked["working_on"] = f"{text}: {said}" if said else text

    # looking_into: research under way now, from the timeline
    ledger = _read_json(root / LEDGER)
    rows = [r for r in (ledger.get("rows") or []) if isinstance(r, dict)] if isinstance(ledger, dict) else []
    open_rows: dict[str, bool] = {}
    landed: set[str] = set()
    for e in timeline:
        row = e.get("row")
        if e.get("event") == "research_start" and row:
            open_rows[row] = True
        elif e.get("event") == "research_done" and row:
            open_rows.pop(row, None)
            landed.add(row)
    running = [r for r in rows if r.get("id") in open_rows]
    part_rows = [r.get("id") for r in rows if n is not None and r.get("section") == n]
    if not running and n is not None and outline is not None:
        state = _read_json(root / base.FILES["state"])
        states = (state.get("rows") or {}) if isinstance(state, dict) else {}
        running = [r for r in rows if r.get("id") in part_rows and r.get("id") not in landed
                   and isinstance(states.get(r.get("id")), dict) and states[r["id"]].get("state") == "pending"]
    asked = [q for q in (question_of(r) for r in running) if q][:2]

    ending: set[str] = set()
    if outline is not None:
        ending |= long_words(str(outline.get("closing_idea") or ""))
        for part in outline.get("sections") or []:
            if isinstance(part, dict) and part.get("n") == last:
                ending |= long_words(str(part.get("lands") or ""))
    found = _found(root, landed, part_rows, timeline, ending=ending)

    # the fill: greedy, in priority order, within WORDS_ALL
    out: dict = {}
    total = 0

    def add(key: str, text: str | None, many: bool = False) -> None:
        nonlocal total
        if not text or total + words(text) > WORDS_ALL:
            return
        total += words(text)
        if many:
            out.setdefault(key, []).append(text)
        else:
            out[key] = text

    add("working_on", picked.get("working_on"))
    add("looking_into", asked[0] if asked else None, many=True)
    add("found", found)
    add("question", picked.get("question"))
    add("about", picked.get("about"))
    add("looking_into", asked[1] if len(asked) > 1 else None, many=True)
    return out


def peek(run_root: Path, status: dict) -> dict:
    """The peek for get_status, or {} (not a fast run being made, queued, or nothing to say). Never raises."""
    try:
        if not isinstance(status, dict) or status.get("state") != "making" or status.get("mode") != "fast":
            return {}
        if status.get("queued"):
            return {}
        root = Path(run_root)
        if not root.is_dir():
            return {}
        return _peek(root, status)
    except Exception:  # a peek is a nicety: get_status must never break for it
        return {}
