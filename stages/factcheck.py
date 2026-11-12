"""Stage 8b: draft + edit notes + dossier -> 08b_factcheck.md

A fact-checker in a fresh context checks every claim in the draft, marked or
not, and the replacement wording the editor's notes propose, against the
dossier. Its notes take the edit notes' shape ('## Global', '## segNN'); the
revise stage applies them with the editor's, and they win where the two
conflict. Added after beauty-scarry-short, where a blind panel found slips the
editor had missed or written itself.
"""

from . import llm
from .common import Run, load_prompt, prompt_name
from .revise import check_fact_notes, parse_notes


def run(r: Run, cfg: dict):
    draft = r.read("script_draft")
    notes = r.read("edit_notes")
    dossier = r.read("dossier")
    r.check_writable("factcheck")
    prompt = load_prompt(prompt_name(r, "factcheck"), draft=draft, edit_notes=notes, dossier=dossier,
                         source_tiers=load_prompt(prompt_name(r, "source_tiers")))
    res = llm.call(r, cfg, "factcheck", messages=[{"role": "user", "content": prompt}])
    out = r.write("factcheck", res.text)
    check_fact_notes(res.text, *parse_notes(res.text))  # stop here, not at revise, if a heading is unreadable
    flagged = res.text.count("[FACT]")
    print(f"  {flagged} fact note(s)\n\n  Next: python cli.py revise {r.name}")
    return out
