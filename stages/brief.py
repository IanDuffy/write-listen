"""Stage 5: angle + dossier + ledger -> 05_brief.md

The plan the drafter follows. It states the chapter's through-line first: the
one question or tension that carries the listener from the first segment to the
last, which the ledger's rows serve. Then the thesis, listener, hook, closing
line, style notes for the ear, and 6-8 segments, each with its purpose, target
words, the ledger rows it lands ('Ledger: L01, L03') and the dossier entries it
should use ('Dossier: D03, D07'). Every active row is assigned to at least one
segment; the stage warns about any that is not.

Facts the verify agents corrected are listed by code at the end of the brief, so
the draft uses the corrected version. Ian edits the brief by hand before drafting.
"""

from __future__ import annotations

import re

from . import ledger, llm
from .common import (Run, StageError, dossier_entries, entry_id, entry_numbers, entry_tags,
                     load_prompt, row_id)

SEGMENT_HEADING = re.compile(r"^##\s+Segment\s+(\d+)\s*[:.\-–—]?\s*(.*)$", re.M | re.I)
# '- Dossier: D3, D07', '**Dossier:** D3-D5', 'Dossier: [D03], [D07]'. Stays on its own line.
# 'Entries:' and 'Claims:' are read the same way, in case Ian relabels the line.
ENTRIES_LINE = re.compile(
    r"^[ \t]*[-*]?[ \t]*\**(?:Dossier|Entries|Claims)\**[ \t]*:[ \t]*\**[ \t]*(.*)$", re.M | re.I)
LEDGER_LINE = re.compile(r"^[ \t]*[-*]?[ \t]*\**Ledger\**[ \t]*:[ \t]*\**[ \t]*(.*)$", re.M | re.I)
TARGET_LINE = re.compile(
    r"^[ \t]*[-*]?[ \t]*\**Target words\**[ \t]*:[ \t]*\**[ \t]*(?:about|around|approx\.?|~)?[ \t]*(\d[\d,]*)",
    re.M | re.I)


def _listed(body: str, line: re.Pattern, parse) -> list:
    m = line.search(body)
    if not m:
        return []
    items = parse(m.group(1))
    if not items:
        # The list on the following lines, as a sub-list, up to the next 'Label:' line.
        rest = body[m.end():]
        label = re.search(r"^[ \t]*[-*]?[ \t]*\**[A-Z][\w ]{1,30}\**[ \t]*:", rest, re.M)
        items = parse(rest[:label.start()] if label else rest)
    return items


def row_ids(text: str) -> list[str]:
    return sorted({row_id(int(n)) for n in re.findall(r"\bL0*(\d+)\b", text)})


def parse_segments(brief: str) -> list[dict]:
    """Find '## Segment N: Title' sections, with the dossier entries, ledger rows and target words of each."""
    heads = list(SEGMENT_HEADING.finditer(brief))
    segs = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(brief)
        # A later level-2 heading that is not a segment ends the section.
        nxt = re.search(r"^##\s+(?!Segment\s+\d)", brief[m.end():end], re.M | re.I)
        if nxt:
            end = m.end() + nxt.start()
        body = brief[m.start():end].strip()
        target_m = TARGET_LINE.search(body)
        segs.append({
            "n": int(m.group(1)),
            "title": m.group(2).strip(),
            "text": body,
            "entries": _listed(body, ENTRIES_LINE, entry_numbers),
            "rows": _listed(body, LEDGER_LINE, row_ids),
            "target_words": int(target_m.group(1).replace(",", "")) if target_m else None,
        })
    nums = [s["n"] for s in segs]
    if nums != list(range(1, len(nums) + 1)):
        raise StageError(f"Brief segments must be numbered 1..N in order; found {nums}.")
    return segs


def corrections(dossier: str) -> list[str]:
    """Every dossier entry a verify agent marked corrected, in full."""
    entries = dossier_entries(dossier)
    return [entries[n] for n in sorted(entries) if entry_tags(entries[n])["verdict"] == "corrected"]


def check(brief: str, dossier: str, led: dict) -> list[str]:
    """Warnings about a brief: segments with no entries, unknown entries or rows, unassigned rows."""
    segs = parse_segments(brief)
    if not segs:
        return ["no '## Segment N: Title' headings found. The draft stage needs them."]
    entries = dossier_entries(dossier)
    ids = {row["id"] for row in led["rows"]}
    out = []
    empty = [f"Segment {s['n']}" for s in segs if not s["entries"]]
    if empty:
        out.append(f"no dossier entries parsed for {', '.join(empty)}; the drafter gets no entries for them. "
                   "Check the 'Dossier:' lines.")
    unknown = sorted({n for s in segs for n in s["entries"] if n not in entries})
    if unknown:
        out.append(f"entries not in the dossier: {', '.join(entry_id(n) for n in unknown)}")
    bad_rows = sorted({x for s in segs for x in s["rows"] if x not in ids})
    if bad_rows:
        out.append(f"rows not in the ledger: {', '.join(bad_rows)}")
    unassigned = [row["id"] for row in ledger.active(led) if not any(row["id"] in s["rows"] for s in segs)]
    if unassigned:
        out.append(f"active ledger rows assigned to no segment: {', '.join(unassigned)}. Every row needs at "
                   "least one segment's 'Ledger:' line, or should be parked in ledger.json.")
    return out


def run(r: Run, cfg: dict):
    angle = r.read("angle")
    dossier = r.read("dossier")
    r.need("ledger_update")  # the brief plans from each row's research status
    led = ledger.load(r, cfg)
    r.check_writable("brief")
    ch = cfg["chapter"]
    fixed = corrections(dossier)
    prompt = load_prompt(
        "brief", angle=angle, dossier=dossier,
        ledger=ledger.describe(ledger.active(led), research=True),
        connections=ledger.connections_text(led),
        corrections="\n".join(fixed) or "(none)",
        style_guide=load_prompt("style_guide"), source_tiers=load_prompt("source_tiers"),
        target_words=ch["target_words"], target_minutes=ch["target_minutes"],
        min_segments=ch["min_segments"], max_segments=ch["max_segments"],
    )
    res = llm.call(r, cfg, "brief", messages=[{"role": "user", "content": prompt}])
    text = res.text.strip()
    if fixed:
        text += "\n\n" + load_prompt("brief_corrections", corrections="\n".join(f"- {c}" for c in fixed)).strip()
    out = r.write("brief", text)
    try:
        segs = parse_segments(text)
        print(f"  {len(segs)} segments, {sum(s['target_words'] or 0 for s in segs):,} target words"
              + (f"; {len(fixed)} corrected fact(s) listed at the end" if fixed else "") + ".")
        for w in check(text, dossier, led):
            print(f"  Warning: {w}")
    except StageError as e:
        print(f"  Warning: {e} Fix the headings by hand before drafting.")
    print("\n  Next [Ian]: edit 05_brief.md by hand. Keep the '## Segment N: Title' headings and the\n"
          "  'Target words:', 'Ledger:' and 'Dossier:' lines; the draft and coverage stages read them.")
    return out
