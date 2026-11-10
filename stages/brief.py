"""Stage 5: angle + dossier -> 05_brief.md

The plan the drafter follows: thesis, 6-8 segments with purpose, target words
and the dossier claims each must carry, listener profile, hook, closing line,
and the style guide for the ear. Ian edits it by hand before drafting.
"""

from __future__ import annotations

import re

from . import llm
from .common import PROMPTS_DIR, Run, StageError, claim_numbers, load_prompt

SEGMENT_HEADING = re.compile(r"^##\s+Segment\s+(\d+)\s*[:.\-–—]?\s*(.*)$", re.M | re.I)
# '- Claims: C3, C7', '**Claims:** C3-C5', 'Claims: [C3], [C7]'. Stays on its own line.
CLAIMS_LINE = re.compile(r"^[ \t]*[-*]?[ \t]*\**Claims\**[ \t]*:[ \t]*\**[ \t]*(.*)$", re.M | re.I)
TARGET_LINE = re.compile(
    r"^[ \t]*[-*]?[ \t]*\**Target words\**[ \t]*:[ \t]*\**[ \t]*(?:about|around|approx\.?|~)?[ \t]*(\d[\d,]*)",
    re.M | re.I)


def parse_segments(brief: str) -> list[dict]:
    """Find '## Segment N: Title' sections and the claims and target words in each."""
    heads = list(SEGMENT_HEADING.finditer(brief))
    segs = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(brief)
        # A later level-2 heading that is not a segment ends the section.
        nxt = re.search(r"^##\s+(?!Segment\s+\d)", brief[m.end():end], re.M | re.I)
        if nxt:
            end = m.end() + nxt.start()
        body = brief[m.start():end].strip()
        claims_m = CLAIMS_LINE.search(body)
        claims = claim_numbers(claims_m.group(1)) if claims_m else []
        target_m = TARGET_LINE.search(body)
        segs.append({
            "n": int(m.group(1)),
            "title": m.group(2).strip(),
            "text": body,
            "claims": claims,
            "target_words": int(target_m.group(1).replace(",", "")) if target_m else None,
        })
    nums = [s["n"] for s in segs]
    if nums != list(range(1, len(nums) + 1)):
        raise StageError(f"Brief segments must be numbered 1..N in order; found {nums}.")
    return segs


def run(r: Run, cfg: dict):
    angle = r.read("angle")
    dossier = r.read("dossier")
    r.check_writable("brief")
    ch = cfg["chapter"]
    prompt = load_prompt(
        "brief", angle=angle, dossier=dossier,
        style_guide=(PROMPTS_DIR / "style_guide.md").read_text(encoding="utf-8"),
        target_words=ch["target_words"], target_minutes=ch["target_minutes"],
        min_segments=ch["min_segments"], max_segments=ch["max_segments"],
    )
    res = llm.call(r, cfg, "brief", messages=[{"role": "user", "content": prompt}])
    out = r.write("brief", res.text)
    try:
        segs = parse_segments(res.text)
        total = sum(s["target_words"] or 0 for s in segs)
        print(f"  {len(segs)} segments, {total:,} target words.")
        if not segs:
            print("  Warning: no '## Segment N: Title' headings found. The draft stage needs them.")
    except StageError as e:
        print(f"  Warning: {e} Fix the headings by hand before drafting.")
    print("\n  Next [Ian]: edit 05_brief.md by hand. Keep the '## Segment N: Title' headings and the\n"
          "  'Claims:' and 'Target words:' lines; the draft stage reads them.")
    return out
