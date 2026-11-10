"""Stage 8: draft + brief + dossier -> 08_edit_notes.md

One editor pass in a fresh context: cuts, additions, reordering, repetition,
pacing, tone drift, and every factual claim checked against the dossier.
The output is notes, not a rewrite, grouped under '## Global' and '## segNN'
headings so the revise stage can apply them segment by segment.
"""

from . import llm
from .common import Run, load_prompt


def run(r: Run, cfg: dict):
    draft = r.read("script_draft")
    brief = r.read("brief")
    dossier = r.read("dossier")
    r.check_writable("edit_notes")
    prompt = load_prompt(
        "edit", draft=draft, brief=brief, dossier=dossier,
        style_guide=load_prompt("style_guide"),
    )
    res = llm.call(r, cfg, "edit", messages=[{"role": "user", "content": prompt}])
    return r.write("edit_notes", res.text)
