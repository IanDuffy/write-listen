"""Stage 8: draft + brief + dossier + coverage check -> 08_edit_notes.md

One editor pass in a fresh context: cuts, additions, reordering, repetition,
pacing, tone drift, and every factual claim checked against the dossier. The
editor also reads 07b_coverage.md, the check of the draft against the ledger.
The output is notes, not a rewrite, grouped under '## Global' and '## segNN'
headings so the revise stage can apply them segment by segment.
"""

from . import llm
from .common import Run, load_prompt


def run(r: Run, cfg: dict):
    draft = r.read("script_draft")
    brief = r.read("brief")
    dossier = r.read("dossier")
    coverage = r.read("coverage")
    r.check_writable("edit_notes")
    prompt = load_prompt(
        "edit", draft=draft, brief=brief, dossier=dossier, coverage=coverage,
        style_guide=load_prompt("style_guide"), source_tiers=load_prompt("source_tiers"),
    )
    res = llm.call(r, cfg, "edit", messages=[{"role": "user", "content": prompt}])
    return r.write("edit_notes", res.text)
