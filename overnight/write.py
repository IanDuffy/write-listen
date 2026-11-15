"""Overnight stage 3b: write and edit the chapter section by section (docs/overnight-chapters.md, DESIGN.md).

Input: 05_outline.json (overnight/outline.py), ledger.json, 04_dossier.md. Output: 06_segments/segNN.md
(each locked section), 06_segments/drafts/, fast/locks/segNN.json, 07_script_draft.md.

The writing and the paired edit are Fast Start's (faststart/sections.py), run in order with nobody
waiting: draft 1, then for each n from 2, draft n (with every earlier section's full text: locked
1..n-2 and the edited draft of n-1) and lock n-1 beside it (the editor may move material between the
two); the last section is locked as the ending, reading the whole chapter. Section 1 is drafted like
any other and locked paired, not written from a scout and edited alone as in a fast run.

What differs from a fast run is in the prompts (Ctx.prompts): the research was finished before the
outline, nothing has been heard yet, and nothing in the chapter speaks of how it was made. The
dossier is the overnight one (`Dossier`, below), read through the five methods sections.py uses.
Nothing here writes fast.json or a timeline, so the folder never reads as a fast run.

Each step is skipped when its file exists (a draft, a lock record, a locked section), so a build
stopped anywhere resumes without paying for a finished call twice.
"""

from __future__ import annotations

import json
from pathlib import Path

from faststart import base, checks, sections
from stages import ledger
from stages.common import Run, dossier_entries, entry_tags, join_script_segments, word_count

PROMPTS = {
    "fast_context": "overnight_context",
    "fast_earlier": "overnight_earlier",
    "fast_draft": "overnight_draft",
    "fast_draft_first": "overnight_draft_first",
    "fast_draft_middle": "overnight_draft_middle",
    "fast_lock": "overnight_lock",
}
NOTES: list[dict] = []  # no listener notes arrive while an overnight chapter is written


class Dossier:
    """The overnight 04_dossier.md, read the way faststart/sections.py reads a fast run's dossier.

    sections.py offers every call "the scout's entries" (row L00) as the background it may draw on:
    a fast run has nothing else before its research lands. Here every entry was gathered before the
    outline, so that row stands for the whole dossier: a draft is offered the entries the outline does
    not give its own section as the rest of the dossier, and the editor sees every entry."""

    def __init__(self, r: Run):
        self._text = r.read("dossier")
        self._entries = dossier_entries(self._text)

    def text(self) -> str:
        return self._text

    def entries(self) -> dict[int, str]:
        return dict(self._entries)

    def numbers_of(self, row_id: str) -> list[int]:
        if row_id == base.SCOUT_ROW:
            return sorted(self._entries)
        return sorted(d for d, e in self._entries.items() if row_id in entry_tags(e)["rows"])

    def for_rows(self, row_ids: list[str]) -> dict[int, str]:
        if base.SCOUT_ROW in row_ids:
            return self.entries()
        want = set(row_ids)
        return {d: e for d, e in self._entries.items() if want & set(entry_tags(e)["rows"])}

    def thin(self, row: dict) -> str | None:
        """Why a row counts as thin, from stage 04b's verdict in ledger.json (its code checks
        included); None for a filled row, or one never judged."""
        if not self.numbers_of(row["id"]):
            return "no findings"
        if row.get("status") in (None, "filled"):
            return None
        return " ".join(str(row.get("gap") or "").split()) or f"its research came back {row['status']}"


def load_outline(r: Run) -> dict:
    return json.loads(r.read(base.path(r, "outline_json")))


def context(r: Run, cfg: dict) -> sections.Ctx:
    """The writing context for this chapter: the outline, the ledger, the dossier, the overnight prompts."""
    with base.LEDGER_LOCK:
        led = ledger.normalize(json.loads(r.read("ledger")))
    return sections.Ctx(r, cfg, load_outline(r), led, Dossier(r), prompts=dict(PROMPTS))


def said(r: Run, outline: dict) -> str:
    """The listener's own words (what they asked for, the phrases the outline kept): quoted back,
    they are not a quotation a dossier entry has to back (checks.section_problems)."""
    seed = r.path("seed").read_text(encoding="utf-8") if r.path("seed").exists() else ""
    return "\n".join([seed, *(str(w) for w in outline.get("listener_words") or [])])


def _lock(ctx: sections.Ctx, n: int, mode: str) -> None:
    """Lock section n (paired with the draft of n+1, or as the ending) and persist it. A section
    whose locked text exists is done: apply() writes that file last."""
    r = ctx.r
    if sections.locked_text(r, n) is not None:
        return
    nxt = sections.latest_draft(r, n + 1) if mode == "paired" else None
    before = [(k, sections.locked_text(r, k)) for k in range(1, n)]
    got = sections.lock(ctx, n, sections.latest_draft(r, n), nxt, before, NOTES, mode)
    sections.apply(ctx, got)


def run(r: Run, cfg: dict) -> Path:
    """Draft, edit and lock every section, then join them into 07_script_draft.md. Returns its path;
    with the file there already, returns it without a call."""
    out = r.path("script_draft")
    if out.exists():
        return out
    ctx = context(r, cfg)
    total = len(ctx.outline["sections"])
    text = lambda k: sections.locked_text(r, k) or sections.latest_draft(r, k)

    sections.draft(ctx, 1, [], NOTES)
    for n in range(2, total + 1):
        earlier = [(k, text(k), sections.locked_text(r, k) is not None) for k in range(1, n)]
        sections.draft(ctx, n, earlier, NOTES)
        _lock(ctx, n - 1, "paired")
    _lock(ctx, total, "final")

    segs = [(n, sections.locked_text(r, n)) for n in range(1, total + 1)]
    problems = checks.chapter_problems(segs, ctx.dossier.text(), said(r, ctx.outline))
    words = sum(word_count(t) for _, t in segs)
    wpm = cfg["overnight"]["words_per_minute"]
    lo, hi = cfg["overnight"]["total_minutes"]
    planned = sum(int(s["target_words"]) for s in ctx.outline["sections"])
    note = [f"{total} sections written and edited (paired), {words} words, about {words / wpm:.1f} minutes "
            f"(planned {planned} words)."]
    note += [f"- Section {n}: {word_count(t)} words (target {sections.section(ctx.outline, n)['target_words']})"
             for n, t in segs]
    if not lo <= words / wpm <= hi:
        note.append(f"Outside {lo} to {hi} minutes at {wpm} words a minute.")
    if problems:
        note += ["Code checks on the whole script (left as they are; each lock repaired what it could):"]
        note += [f"- {p}" for p in problems]
    r.decide("overnight write", "\n".join(note))
    base.write_atomic(out, join_script_segments(segs))
    print(f"  07_script_draft.md: {total} sections, {words} words"
          + (f", {len(problems)} code check(s) left" if problems else ""))
    return out
