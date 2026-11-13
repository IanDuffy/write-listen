"""Writing and editing the sections of a fast run (docs/fast-start.md, steps 3 and 5).

Two of the spec's three quality levers live here: every section is written with the full text
of what came before, and a rolling edit reads two sections at a time.

    write_opening, edit_opening   section 1, from the scout alone, edited once in a fresh context
    draft                         section n, with every earlier section's full text in the request
    lock                          the editor: final text of n, changes to the draft of n+1 (it may
                                  move material between the two), which ledger rows n landed
    apply                         persist a lock

The editor returns final text, not notes, and the text is narrated at once, so code checks it
before it is locked (faststart/checks.py): one fix call if the checks find anything (kept only
if it is the better result: a reply that lost text never replaces one that did not), then
repairs by code, so a section that reaches narration never stops it.

Every call shares one system prompt (prompts/fast_context.md: the rules, the style guide and
the outline, none of which change during a run) and sends the chapter so far as one block per
section, locked sections first. Locked text never changes, so that prefix is read from the
prompt cache: each draft reads what the draft before it wrote, each lock what the lock before
it wrote (a structured-output format is part of the cache key, so drafts and locks are two
families; within one, the model, the effort and the format must stay the same). What grows
while the listener listens (dossier entries, listener notes) travels in the last block.

Files: 06_segments/drafts/segNN.dK.md (every draft, K counting up), 06_segments/segNN.md (the
locked text; its existence is what "locked" means), fast/locks/segNN.json (the whole Lock).
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING

from stages import ledger, llm
from stages.common import SEGMENT_MARKER, Run, StageError, entry_id, load_prompt, parse_json, word_count

from . import base, checks
from . import notes as listener_notes
from . import research as fast_research
from .outline import rows_for, section

if TYPE_CHECKING:
    from .dossier import Dossier

CACHE = {"type": "ephemeral"}
MODES = ("paired", "solo", "final")
COVERAGES = ("covered", "thin", "missing")
TITLE_WORDS = 6        # the outline validator's limit on a marker title
OPENING_KEEP = 0.6     # the opening edit declares no cuts, so only a text that came back a fragment counts as lost
DRAFT_FILE = re.compile(r"^seg(\d+)\.d(\d+)\.md$")
_DRAFT_LOCK = threading.Lock()


@dataclass
class Ctx:
    r: Run
    cfg: dict
    outline: dict
    led: dict
    dossier: "Dossier"


@dataclass
class Lock:
    """What the editor returned for one section, after the code checks. Saved whole by apply()."""
    n: int
    mode: str                     # "paired" | "solo" | "final" ("opening" in section 1's record)
    text: str                     # the final text of section n
    next_text: str | None = None  # the draft of n+1 as the editor left it (paired only)
    rows: list[dict] = field(default_factory=list)   # [{"id": "L02", "coverage": "covered|thin|missing", "note": ""}]
    moved: list[dict] = field(default_factory=list)  # [{"text": "...", "from": n|n+1, "to": n|n+1}]
    cut: list[str] = field(default_factory=list)     # passages removed, each with its reason
    title: str | None = None      # a better marker title, if the section's content now calls for one
    corrections: list[str] = field(default_factory=list)  # research contradicted something played: what now says so
    problems: list[str] = field(default_factory=list)     # what the code checks found after the fix round, and did


_TEXT = {"type": "string"}


def _items(**props) -> dict:
    return {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": list(props),
                                       "properties": props}}


# The lock's structured output. One schema for every mode: a format is compiled the first time
# it is sent and is part of the prompt-cache key, so a schema of its own for the solo lock would
# cost the most exactly when the listener has waited longest. Solo and final locks leave
# next_text empty and moved an empty list.
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["text", "next_text", "moved", "rows", "cut", "title", "corrections"],
    "properties": {
        "text": _TEXT,
        "next_text": _TEXT,
        "moved": _items(text=_TEXT, **{"from": {"type": "integer"}, "to": {"type": "integer"}}),
        "rows": _items(id=_TEXT, coverage={"type": "string", "enum": list(COVERAGES)}, note=_TEXT),
        "cut": _items(text=_TEXT, reason=_TEXT),
        "title": _TEXT,
        "corrections": {"type": "array", "items": _TEXT},
    },
}


# --- files ----------------------------------------------------------------------------------

def _versions(r: Run, n: int) -> list[tuple[int, Path]]:
    d = base.path(r, "drafts")
    found = [(int(m.group(2)), p) for p in (d.glob(f"seg{n:02d}.d*.md") if d.exists() else [])
             if (m := DRAFT_FILE.match(p.name)) and int(m.group(1)) == n]
    return sorted(found)


def save_draft(r: Run, n: int, text: str) -> Path:
    """Keep a draft as the section's next version: 06_segments/drafts/segNN.dK.md. Nothing is
    overwritten, so what the writer wrote and what each edit made of it can be compared afterwards."""
    with _DRAFT_LOCK:
        have = _versions(r, n)
        k = have[-1][0] + 1 if have else 1
        return base.write_atomic(base.path(r, "drafts") / f"seg{n:02d}.d{k}.md", text.strip())


def latest_draft(r: Run, n: int) -> str | None:
    have = _versions(r, n)
    return have[-1][1].read_text(encoding="utf-8").strip() if have else None


def locked_text(r: Run, n: int) -> str | None:
    p = r.segment_path(n)
    return p.read_text(encoding="utf-8").strip() if p.exists() else None


def lock_path(r: Run, n: int) -> Path:
    return base.path(r, "locks") / f"seg{n:02d}.json"


def saved_lock(r: Run, n: int) -> Lock | None:
    """The lock record of a section, if it has one (a restarted run, the archive, the report)."""
    data = base.read_json(lock_path(r, n))
    if not isinstance(data, dict) or not data.get("text"):
        return None
    return Lock(**{f.name: data[f.name] for f in fields(Lock) if f.name in data})


# --- what the prompts are filled with -------------------------------------------------------

def _one(v) -> str:
    return " ".join(str(v or "").split())


def _plan_lines(sec: dict) -> list[str]:
    parts = (("Lands", "lands"), ("Sets up", "sets_up"), ("Hands off", "hand_off"))
    return [f"{label}: {_one(sec.get(key))}" for label, key in parts if _one(sec.get(key))]


def outline_text(outline: dict) -> str:
    """The outline as every call's system prompt carries it. Built from the outline alone, in a
    fixed order, so it is the same text from the opening to the last lock and stays cached."""
    lines = [f"Title: {_one(outline.get('title'))}", f"Angle: {_one(outline.get('angle'))}",
             f"Why this angle: {_one(outline.get('why'))}", f"Through-line: {_one(outline.get('through_line'))}",
             f"Closing idea: {_one(outline.get('closing_idea'))}"]
    for s in outline["sections"]:
        lines += ["", f"Section {s['n']}: {_one(s.get('title'))} (about {s.get('target_words')} words)",
                  *_plan_lines(s)]
    return "\n".join(lines)


def plan_text(sec: dict) -> str:
    """One section's part of the outline, with its notes on how to tell it."""
    lines = [f"Section {sec['n']}: {_one(sec.get('title'))}", *_plan_lines(sec)]
    if _one(sec.get("notes")):
        lines.append(f"How to tell it: {_one(sec.get('notes'))}")
    return "\n".join(lines)


def _words_text(outline: dict) -> str:
    return "\n".join(f"- {_one(w)}" for w in outline.get("listener_words") or [] if _one(w)) or "(none recorded)"


def _system(ctx: Ctx) -> str:
    r = ctx.r
    seed = r.path("seed").read_text(encoding="utf-8").strip() if r.path("seed").exists() else ""
    words = sum(int(s.get("target_words") or 0) for s in ctx.outline["sections"])
    return load_prompt("fast_context", seed=seed or "(not recorded)", outline=outline_text(ctx.outline),
                       style_guide=load_prompt("style_guide"), source_tiers=load_prompt("source_tiers_seed"),
                       total=len(ctx.outline["sections"]), minutes=round(base.minutes(words, ctx.cfg)))


def _ledger(ctx: Ctx) -> dict:
    """The ledger as it stands on disk: research threads flag thin rows and a listener's note can
    add a row while sections are being written, so ctx.led may be behind."""
    with base.LEDGER_LOCK:
        led = base.read_json(ctx.r.path("ledger"))
    return led if isinstance(led, dict) and isinstance(led.get("rows"), list) else ctx.led


def _carried(led: dict, n: int) -> list[dict]:
    """Rows an earlier section was meant to land and left thin or missing: a later section may
    take one up (its research may have come in after its own section was locked)."""
    return [row for row in led.get("rows", []) if isinstance(row.get("section"), int) and 1 < row["section"] < n
            and row.get("coverage") in ("thin", "missing")]


def rows_text(ctx: Ctx, own: list[dict], carried: list[dict] = ()) -> str:
    """The ledger rows a section answers for, each with how its research came back."""
    def about(row: dict, was: str = "") -> str:
        nums = ctx.dossier.numbers_of(row["id"])
        thin = ctx.dossier.thin(row)
        got = f"entries {', '.join(entry_id(d) for d in nums)}" if nums else "no entries"
        return "\n".join([f"- {row['id']}: {_one(row.get('idea'))}{was}",
                          f"  Research question: {_one(row.get('research_question'))}",
                          f"  Research: came back thin ({thin}); {got}" if thin else f"  Research: landed; {got}"])

    def left(row: dict) -> str:
        note = f": {_one(row.get('note'))}" if _one(row.get("note")) else ""
        return f" (section {row['section']}'s row, left {row['coverage']} there{note})"

    return ("\n".join([about(row) for row in own] + [about(row, left(row)) for row in carried])
            or "(no ledger rows: this section is told from the scout's entries)")


def _entries_text(entries: dict[int, str]) -> str:
    return "\n\n".join(entries[d] for d in sorted(entries)) or "(none)"


def _said(ctx: Ctx, notes: list[dict]) -> str:
    """Everything the listener said: the seed, the phrases the outline kept, the notes. A passage
    of it in quotation marks is their own words echoed back, not a quotation that a dossier entry
    has to back (checks.section_problems)."""
    r = ctx.r
    seed = r.path("seed").read_text(encoding="utf-8") if r.path("seed").exists() else ""
    return "\n".join([seed, *(str(w) for w in ctx.outline.get("listener_words") or []),
                      *(str(n.get("text") or "") for n in notes or [])])


def _note_entries(ctx: Ctx) -> str:
    """The entries the note judge named as answering something the listener said, under the note
    each answers (faststart/research.py note_answers). A question the dossier can already answer
    gets no research of its own, and its answer may sit under any row, so without this a section
    is never offered it. Read from the dossier as it stands; an entry not in it is left out."""
    answers = getattr(fast_research, "note_answers", None)
    every = ctx.dossier.entries()
    out = []
    for a in (answers(ctx.r) if answers else None) or []:
        nums = [d for d in a.get("entries") or [] if d in every]
        if nums and _one(a.get("text")):
            out.append(f"The listener ({_one(a.get('kind')) or 'note'}): {_one(a['text'])}\nEntries that answer it:\n\n"
                       + "\n\n".join(every[d] for d in nums))
    return "\n\n".join(out) or "(none)"


def _scout_numbers(ctx: Ctx) -> set[int]:
    return set(ctx.dossier.numbers_of(base.SCOUT_ROW))


def _scout_brief(r: Run) -> str:
    """01_scout.md without its entries: the territory and the scenes. The entries reach the writer
    from the dossier, where each has been checked (a quote not found on its page is no longer
    verbatim: yes there) and carries the number the text must cite."""
    p = base.path(r, "scout")
    if not p.exists():
        return "(no scout notes)"
    text = re.sub(r"^##\s+Entries\b.*?(?=^##\s|\Z)", "", p.read_text(encoding="utf-8"), flags=re.M | re.S | re.I)
    return re.sub(r"\n{3,}", "\n\n", text).strip() or "(no scout notes)"


def _first_words(cfg: dict) -> int:
    """The first paragraph is narrated alone (fast.narration.first_piece_max_chars); in words,
    with room to spare, rounded down to a five."""
    return max(20, base.settings(cfg)["narration"]["first_piece_max_chars"] // 7 // 5 * 5)


def _clean(text: str) -> str:
    """A section's text as stored: no segment marker (the archive adds it), no code fence."""
    text = re.sub(r"^```[a-z]*\s*$", "", SEGMENT_MARKER.sub("", str(text or "")), flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _earlier_blocks(earlier: list[tuple[int, str, bool]]) -> list[dict]:
    """The chapter so far as message blocks: the preamble, then one block per section in order.
    The cache breakpoint sits on the last locked section: a prefix no later call changes. A
    draft's block gets none: it is edited before any later call could read it, so a breakpoint
    there would pay the cache-write rate for nothing."""
    if not earlier:
        return []
    blocks = [{"type": "text", "text": load_prompt("fast_earlier")}]
    last_locked = None
    for n, text, locked in earlier:
        state = "locked" if locked else "draft"
        blocks.append({"type": "text", "text": f'<section n="{n}" state="{state}">\n{text.strip()}\n</section>'})
        if locked:
            last_locked = len(blocks) - 1
    if last_locked is not None:
        blocks[last_locked]["cache_control"] = CACHE
    return blocks


def _need_all(earlier_numbers: list[int], n: int, what: str) -> None:
    if earlier_numbers != list(range(1, n)):
        raise StageError(f"{what} section {n} needs the full text of sections 1 to {n - 1} in order; got "
                         f"{earlier_numbers or 'none'}. Each section builds on the ones before it.")


def _call(ctx: Ctx, stage: str, content: list[dict], label: str, n: int, json_schema: dict | None = None,
          **extra) -> llm.Result:
    return llm.call(ctx.r, ctx.cfg, stage, system=_system(ctx), messages=[{"role": "user", "content": content}],
                    label=label, json_schema=json_schema, preapproved=True, log_extra={"section": n, **extra})


def _fix_block(returned: str, problems: list[str]) -> dict:
    return {"type": "text", "text": load_prompt("fast_fix", returned=returned,
                                                problems="\n".join(f"- {p}" for p in problems))}


# --- the opening ----------------------------------------------------------------------------

def _opening_values(ctx: Ctx, notes: list[dict]) -> dict:
    sec = section(ctx.outline, 1)
    return dict(total=len(ctx.outline["sections"]), plan=plan_text(sec), listener_words=_words_text(ctx.outline),
                entries=_entries_text(ctx.dossier.for_rows([base.SCOUT_ROW])), notes=listener_notes.as_prompt(notes),
                target_words=sec["target_words"], first_words=_first_words(ctx.cfg))


def write_opening(ctx: Ctx, notes: list[dict]) -> str:
    """The intro and section 1 from the outline, the scout and the listener's words (stage
    "fast_opening"). Story-led; few facts; every fact marked [Dn] with a scout entry. Kept as
    the section's first draft; a draft already there is returned without a call."""
    r = ctx.r
    if (have := latest_draft(r, 1)) is not None:
        return have
    prompt = load_prompt("fast_opening", scout=_scout_brief(r), **_opening_values(ctx, notes))
    res = _call(ctx, "fast_opening", [{"type": "text", "text": prompt}], "fast opening", 1)
    text = _clean(res.text)
    if not text:
        raise StageError("The opening came back empty.")
    save_draft(r, 1, text)
    return text


def edit_opening(ctx: Ctx, draft: str, notes: list[dict]) -> str:
    """One edit pass with fresh context (stage "fast_opening_edit"): facts against scout entries,
    the quote rule, tone, and that the last lines stand alone and hand off. Returns the final
    text. Code then runs section_problems(allowed=scout numbers); on problems one fix call
    (fast_fix.md) told the problems; what remains is repaired by code where safe (an unknown
    [Dn] marker is dropped; an unbacked quotation loses its quotation marks) and recorded with
    r.decide. Writes 06_segments/seg01.md and its lock record: section 1 is locked by this."""
    r = ctx.r
    if (have := locked_text(r, 1)) is not None:
        return have
    allowed, said = _scout_numbers(ctx), _said(ctx, notes)
    whole = OPENING_KEEP * section(ctx.outline, 1)["target_words"]

    def lost(text: str) -> list[str]:
        if word_count(text) >= whole:
            return []  # still a whole opening: an over-long draft trimmed to its target is not a loss
        return checks.loss_problems([draft], [text], [], keep=OPENING_KEEP)

    def problems(text: str) -> list[str]:
        return checks.section_problems(text, ctx.dossier.text(), allowed, said) + lost(text)

    content = [{"type": "text", "text": load_prompt("fast_opening_edit", draft=draft.strip(),
                                                    **_opening_values(ctx, notes))}]
    text = _clean(_call(ctx, "fast_opening_edit", content, "fast opening edit", 1).text)
    if found := problems(text):
        text, _ = _fixed(ctx, "fast_opening_edit", content, "fast opening fix", 1, text, found,
                         read=lambda res: _clean(res.text), problems=problems, returned=lambda t: t,
                         weigh=lambda t, all_: _weight(ctx, t, lost, all_, allowed, said))
    text, record = _repair(ctx, text, allowed, said)
    if not text:
        raise StageError("The opening's edit came back empty.")
    left = record + lost(text)
    if left:
        r.decide("fast opening", "Code checks on the edited opening, after one fix call:\n"
                 + "\n".join(f"- {p}" for p in left))
    base.write_json(lock_path(r, 1), asdict(Lock(n=1, mode="opening", text=text, problems=left)))
    base.write_atomic(r.segment_path(1), text)
    return text


def _fixed(ctx: Ctx, stage: str, content: list[dict], label: str, n: int, first, found: list[str], *, read,
           problems, returned, weigh, json_schema: dict | None = None):
    """One fix call: the same request, with what came back and what the checks found as a last
    block. Returns the fix, with its problems, only if weigh(result, problems) makes it the better
    of the two (see _weight); on a tie the first result stands. A fix that fails (a refusal, a
    dropped connection) leaves the first result standing too: the listener is waiting, and code
    repairs what it can."""
    try:
        res = _call(ctx, stage, content + [_fix_block(returned(first), found)], label, n, json_schema, fix=True)
        second = read(res)
    except StageError as e:
        print(f"  {label}: failed ({e}); keeping the first result")
        return first, found
    again = problems(second)
    return (second, again) if weigh(second, again) < weigh(first, found) else (first, found)


def _weight(ctx: Ctx, text: str, edit, found: list[str], allowed: set[int] | None = None, said: str = "",
            beside: str = "") -> tuple:
    """How bad a result is, for choosing between an edit and its fix; the lower the better. Counting
    problems is not enough: a six-word reply with one problem (text was lost) would beat a full
    section with two that code repairs. So lost text comes first, because code cannot put it back,
    and of two results that both lost text the one that kept more words is the better; then what
    code repair would leave; then the count. `edit` gives the problems of the edit as a whole (lost
    text, moves) for a section text; `beside` is the next section's draft, in a paired lock."""
    dossier_text = ctx.dossier.text()
    mended = checks.repair(text, dossier_text, allowed, said)[0] if text else ""
    left = (checks.section_problems(mended, dossier_text, allowed, said) if mended else ["the section has no text"])
    left = left + edit(mended)
    lost = any(checks.is_loss(p) for p in left)
    return lost, -(word_count(mended) + word_count(beside)) if lost else 0, len(left), len(found)


def _repair(ctx: Ctx, text: str, allowed: set[int] | None = None, said: str = "") -> tuple[str, list[str]]:
    """What a fix call left behind in a section's text, repaired by code where that is safe.
    Returns the text and a record: each problem found, then what was done about it."""
    dossier_text = ctx.dossier.text()
    found = checks.section_problems(text, dossier_text, allowed, said)
    if not found or not text:
        return text, found
    text, done = checks.repair(text, dossier_text, allowed, said)
    still = checks.section_problems(text, dossier_text, allowed, said) if text else ["the section has no text"]
    return text, found + [f"repaired by code: {d}" for d in done] + [f"not repaired: {p}" for p in still]


# --- drafting -------------------------------------------------------------------------------

def draft(ctx: Ctx, n: int, earlier: list[tuple[int, str, bool]], notes: list[dict]) -> str:
    """Draft section n (stage "fast_draft"). `earlier` is EVERY earlier section in order as
    (number, full text, locked?): the locked text for locked sections and the latest draft for
    the rest. The request carries the full text of each (acceptance check 2), one message block
    per section so the cache prefix survives from call to call, then this section's plan, its
    ledger rows and how their research came back, the dossier entries for those rows (and the
    scout entries the outline gives it), the listener notes, and the entries that answer a note
    (_note_entries). Kept as the section's first draft; a draft already there is returned
    without a call."""
    r = ctx.r
    if (have := latest_draft(r, n)) is not None:
        return have
    earlier = sorted(earlier)
    _need_all([k for k, _, _ in earlier], n, "Drafting")
    sec, total = section(ctx.outline, n), len(ctx.outline["sections"])
    led = _ledger(ctx)
    own, carried = rows_for(led, n), _carried(led, n)
    every = ctx.dossier.entries()
    mine = dict(ctx.dossier.for_rows([row["id"] for row in own + carried]))
    mine.update({d: every[d] for d in sec.get("scout_entries") or [] if d in every})
    other = {d: every[d] for d in _scout_numbers(ctx) if d in every and d not in mine}
    prompt = load_prompt(
        "fast_draft", n=n, total=total, title=_one(sec.get("title")), plan=plan_text(sec),
        rows=rows_text(ctx, own, carried), entries=_entries_text(mine), other_entries=_entries_text(other),
        notes=listener_notes.as_prompt(notes), note_entries=_note_entries(ctx), target_words=sec["target_words"],
        position=load_prompt("fast_draft_last" if n == total else "fast_draft_middle").strip())
    res = _call(ctx, "fast_draft", _earlier_blocks(earlier) + [{"type": "text", "text": prompt}],
                f"fast draft seg{n:02d}", n)
    text = _clean(res.text)
    if not text:
        raise StageError(f"The draft of section {n} came back empty.")
    save_draft(r, n, text)
    return text


# --- locking --------------------------------------------------------------------------------

def lock(ctx: Ctx, n: int, text: str, next_text: str | None, locked: list[tuple[int, str]], notes: list[dict],
         mode: str) -> Lock:
    """The editor, with fresh context (stage "fast_lock", structured output). paired: reads the
    draft of n with the draft of n+1 beside it, all locked text, the outline, the ledger rows for
    n, the dossier entries for both sections, the notes and the entries that answer one
    (_note_entries); returns the final text of n, changes to the draft of n+1 (it may move
    material between the two), which ledger rows n landed, and removes any repetition of an
    earlier section. solo: the same without n+1 (the max_hold
    fallback; still a full edit). final: the last section, with the editor reading the whole
    chapter so the ending lands the through-line and can call back to the opening. The last
    section is always locked as the ending, whatever mode was asked for.

    Code then runs section_problems and (paired) move_problems; on problems, one fix call, kept
    only if it is the better result (_weight); what remains is repaired by code where safe and
    returned in .problems. Writes nothing: apply() does. A section whose lock record exists is
    returned from it without a call."""
    r = ctx.r
    if mode not in MODES:
        raise StageError(f"Unknown lock mode {mode!r}: one of {', '.join(MODES)}.")
    if (done := saved_lock(r, n)) is not None:
        return done
    if (have := locked_text(r, n)) is not None:  # locked by a run that stopped before its record was written
        return Lock(n=n, mode=mode, text=have)
    total = len(ctx.outline["sections"])
    if n == total:
        mode = "final"
    elif mode == "final":
        raise StageError(f"Section {n} is not the last of {total}: it cannot be locked as the ending.")
    paired = mode == "paired"
    if paired and not (next_text or "").strip():
        raise StageError(f"A paired lock of section {n} needs the draft of section {n + 1}.")
    text, next_text = text.strip(), (next_text.strip() if paired else None)
    locked = sorted(locked)
    _need_all([k for k, _ in locked], n, "Locking")

    sec = section(ctx.outline, n)
    led = _ledger(ctx)
    own, carried = rows_for(led, n), _carried(led, n)
    wanted = [row["id"] for row in own + carried] + ([row["id"] for row in rows_for(led, n + 1)] if paired else [])
    every = ctx.dossier.entries()
    entries = dict(ctx.dossier.for_rows([base.SCOUT_ROW] + wanted))
    entries.update({d: every[d] for d in checks.cited(text) | checks.cited(next_text or "") if d in every})

    if paired:
        nxt = section(ctx.outline, n + 1)
        about = load_prompt("fast_lock_paired", n=n, next_n=n + 1, next_draft=next_text, next_plan=plan_text(nxt),
                            next_target=nxt["target_words"])
    else:
        about = load_prompt(f"fast_lock_{mode}", n=n)
    prompt = load_prompt("fast_lock", n=n, total=total, title=_one(sec.get("title")), draft=text, mode=about.strip(),
                         plan=plan_text(sec), rows=rows_text(ctx, own, carried), entries=_entries_text(entries),
                         notes=listener_notes.as_prompt(notes), note_entries=_note_entries(ctx),
                         target_words=sec["target_words"])
    content = _earlier_blocks([(k, t, True) for k, t in locked]) + [{"type": "text", "text": prompt}]
    label = f"fast lock seg{n:02d}"
    said = _said(ctx, notes)

    def read(res: llm.Result) -> dict:
        data = parse_json(r, res.text, f"fast_lock_seg{n:02d}")
        out = {"text": _clean(data.get("text")), "raw": res.text,
               "next_text": (_clean(data.get("next_text")) or next_text) if paired else None,
               "moved": [m for m in data.get("moved") or [] if isinstance(m, dict)] if paired else [],
               "cut": [c for c in data.get("cut") or [] if isinstance(c, dict) and _one(c.get("text"))],
               "rows": [v for v in data.get("rows") or [] if isinstance(v, dict)],
               "title": _one(data.get("title")),
               "corrections": [_one(c) for c in data.get("corrections") or [] if _one(c)]}
        return out

    def edit_problems(got: dict) -> list[str]:
        if paired:
            return checks.move_problems(text, next_text, got["text"], got["next_text"], got["moved"], got["cut"])
        return checks.loss_problems([text], [got["text"]], got["cut"])

    def problems(got: dict) -> list[str]:
        return checks.section_problems(got["text"], ctx.dossier.text(), said=said) + edit_problems(got)

    def weigh(g: dict, found: list[str]) -> tuple:
        return _weight(ctx, g["text"], lambda t: edit_problems({**g, "text": t}), found, said=said,
                       beside=g["next_text"] or "")

    got = read(_call(ctx, "fast_lock", content, label, n, SCHEMA, mode=mode))
    if found := problems(got):
        got, _ = _fixed(ctx, "fast_lock", content, f"fast lock fix seg{n:02d}", n, got, found, read=read,
                        problems=problems, returned=lambda g: g["raw"], weigh=weigh, json_schema=SCHEMA)
    final, record = _repair(ctx, got["text"], said=said)
    if not final:
        raise StageError(f"The lock of section {n} came back with no text.")
    left = record + edit_problems({**got, "text": final})

    title = got["title"]
    if not title or title == _one(sec.get("title")) or len(title.split()) > TITLE_WORDS:
        title = None
    cut = [f"{_one(c['text'])} ({_one(c.get('reason')) or 'no reason given'})" for c in got["cut"]]
    return Lock(n=n, mode=mode, text=final, next_text=got["next_text"],
                rows=_verdicts(ctx, own + carried, got["rows"], final), moved=got["moved"], cut=cut, title=title,
                corrections=got["corrections"], problems=left)


def _verdicts(ctx: Ctx, rows: list[dict], given: list[dict], final: str) -> list[dict]:
    """The editor's verdict on each listed row, checked by code: a row counts as covered only if
    the final text cites one of its entries; a row the editor skipped is judged by that alone."""
    by_id = {v.get("id"): v for v in given}
    cites = checks.cited(final)
    out = []
    for row in rows:
        v = by_id.get(row["id"])
        used = bool(cites & set(ctx.dossier.numbers_of(row["id"])))
        if v is None or v.get("coverage") not in COVERAGES:
            coverage = "thin" if used else "missing"
            note = "No verdict from the editor; judged by code from the markers."
        else:
            coverage, note = v["coverage"], _one(v.get("note"))
            if coverage == "covered" and not used:
                coverage = "thin"
                note = (note + " " if note else "") + "(By code: the section cites none of this row's entries.)"
        out.append({"id": row["id"], "coverage": coverage, "note": note})
    return out


def apply(ctx: Ctx, lock: Lock) -> None:
    """Persist a lock: fast/locks/segNN.json (the whole Lock) first, then the revised draft of n+1
    as a new draft version, the rows' coverage in ledger.json (under LEDGER_LOCK), decisions.md
    entries for material moved, cuts, thin rows and corrections and, last, 06_segments/segNN.md:
    the locked text is what marks the section locked, so it is written when everything else is.

    The record goes first because lock() returns a saved record without a call. If this stops
    anywhere below (the process dies, the ledger write raises and the driver retries), the next
    try applies this same lock again. Were the revised draft of n+1 saved before the record, the
    next try would pay for a second edit pairing the original draft of n with the revised draft
    of n+1, and a passage the first edit moved out of n+1 would be in neither."""
    r, n = ctx.r, lock.n
    if not lock.text.strip():
        raise StageError(f"Section {n}: there is no text to lock.")
    base.write_json(lock_path(r, n), asdict(lock))
    if lock.next_text and lock.next_text.strip() != (latest_draft(r, n + 1) or ""):
        save_draft(r, n + 1, lock.next_text)
    _write_coverage(ctx, lock)
    if locked_text(r, n) != lock.text.strip():  # a second apply of the same lock records nothing twice
        if text := decision_text(lock):
            r.decide(f"fast lock seg{n:02d}", text)
    base.write_atomic(r.segment_path(n), lock.text)


def _write_coverage(ctx: Ctx, lock: Lock) -> None:
    """The lock's verdicts in the ledger's own coverage columns. A row of this section takes the
    verdict as given; a row carried from an earlier section only ever improves."""
    if not lock.rows:
        return
    r, n = ctx.r, lock.n
    rank = {None: 0, "missing": 0, "thin": 1, "repeated": 2, "covered": 2}
    with base.LEDGER_LOCK:
        led = base.read_json(r.path("ledger"))
        if not isinstance(led, dict) or not isinstance(led.get("rows"), list):
            led = ctx.led
        by_id = {row["id"]: row for row in led["rows"]}
        for v in lock.rows:
            row = by_id.get(v["id"])
            if row is None:
                continue
            if row.get("section") != n and rank[v["coverage"]] <= rank.get(row.get("coverage"), 0):
                continue  # carried from an earlier section, and no better off here
            row["coverage"], row["note"] = v["coverage"], _one(v.get("note"))
            if v["coverage"] in ("covered", "thin"):
                row["segments"] = sorted(set(row.get("segments") or []) | {n})
        ledger.normalize(led)
        ledger.validate(led)
        base.write_atomic(r.path("ledger"), json.dumps(led, ensure_ascii=False, indent=2))
        base.write_atomic(r.path("ledger_md"), ledger.render_md(led, r.name))
        if led is not ctx.led:  # keep the ledger the driver holds in step
            mine = {row.get("id"): row for row in ctx.led.get("rows", [])}
            for row in led["rows"]:
                if row["id"] in mine:
                    mine[row["id"]].update(coverage=row["coverage"], segments=row["segments"], note=row["note"])


def decision_text(lock: Lock) -> str:
    """What decisions.md keeps of a lock: material moved, cuts, thin rows, corrections, a changed
    title, and anything the code checks found. Empty when the edit changed none of these."""
    lines = [f"- Moved from section {m.get('from')} to section {m.get('to')}: "
             f"\"{checks.short(str(m.get('text', '')), 30)}\"" for m in lock.moved]
    lines += [f"- Cut: {c}" for c in lock.cut]  # in full: once narrated, this is the only record of what was removed
    lines += [f"- {v['id']} {v['coverage']}: {v['note']}".rstrip(": ") for v in lock.rows if v["coverage"] != "covered"]
    lines += [f"- Correction: {c}" for c in lock.corrections]
    if lock.title:
        lines.append(f"- Marker title changed to: {lock.title}")
    lines += [f"- Code check: {p}" for p in lock.problems]
    if not lines:
        return ""
    return f"Section {lock.n} locked ({lock.mode} edit, {word_count(lock.text)} words).\n" + "\n".join(lines)
