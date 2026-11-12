"""Stage 9: draft + edit notes -> 09_script_final.md

Apply the editor's notes segment by segment. Each call sees the style guide,
brief, dossier, the whole draft and the global notes (cached, identical across
calls), the already-revised earlier segments, and this segment's own notes.
"""

from __future__ import annotations

import re

from . import llm
from .common import (Run, StageError, join_script_segments, load_prompt,
                     split_script_segments, word_count)

# '## Global', '## seg01', '## Segment 1: Title', '## **seg01**'
NOTES_HEADING = re.compile(r"^##\s+\**\s*(Global|seg(?:ment)?\s*0*(\d+))\b.*$", re.M | re.I)


def parse_notes(notes: str) -> tuple[str, dict[int, str]]:
    """Split edit notes into (global notes, {segment number: notes})."""
    heads = list(NOTES_HEADING.finditer(notes))
    global_notes, per_seg = "", {}
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(notes)
        nxt = re.search(r"^##\s+", notes[m.end():end], re.M)  # another level-2 section ends it
        if nxt:
            end = m.end() + nxt.start()
        body = notes[m.end():end].strip()
        if m.group(2):
            per_seg[int(m.group(2))] = body
        else:
            global_notes = body
    return global_notes, per_seg


def check_fact_notes(text: str, fact_global: str, fact_seg: dict[int, str]) -> None:
    """Every [FACT] note must sit under a heading revise can read, or it would be silently dropped."""
    parsed = fact_global.count("[FACT]") + sum(t.count("[FACT]") for t in fact_seg.values())
    if text.count("[FACT]") > parsed:
        raise StageError(f"08b_factcheck.md has {text.count('[FACT]')} [FACT] notes but only {parsed} under "
                         "'## Global' or '## segNN' headings that revise can read. Fix the headings, or run "
                         "factcheck --force.")


FACT_HEAD = "Fact-checker's notes (they win where they conflict with the editor's):"


def merge_notes(global_notes: str, per_seg: dict[int, str], fact_global: str,
                fact_seg: dict[int, str]) -> tuple[str, dict[int, str]]:
    """Add the fact-checker's notes under the editor's, section by section. 'No changes.' adds nothing."""
    def add(editor: str, fact: str) -> str:
        fact = fact.strip()
        if not fact or re.fullmatch(r"no changes\.?", fact, re.I):
            return editor
        return f"{editor.strip()}\n\n{FACT_HEAD}\n{fact}".strip() if editor.strip() else f"{FACT_HEAD}\n{fact}"
    merged = dict(per_seg)
    for n, fact in fact_seg.items():
        merged[n] = add(per_seg.get(n, ""), fact)
    return add(global_notes, fact_global), merged


def run(r: Run, cfg: dict):
    draft = r.read("script_draft")
    notes = r.read("edit_notes")
    fact_notes = r.read("factcheck")
    brief = r.read("brief")
    dossier = r.read("dossier")
    r.check_writable("script_final")

    segments = split_script_segments(draft)
    global_notes, per_seg = parse_notes(notes)
    fact_global, fact_seg = parse_notes(fact_notes)
    if not per_seg and not global_notes:
        raise StageError("08_edit_notes.md has no '## Global' or '## segNN' sections.")
    check_fact_notes(fact_notes, fact_global, fact_seg)
    in_draft = {n for n, _ in segments}
    unknown = sorted(set(per_seg) - in_draft)
    if unknown:
        print(f"  warning: notes for segments not in the draft: {unknown}")
    if lost := sorted(n for n, t in fact_seg.items() if n not in in_draft and "[FACT]" in t):
        raise StageError(f"08b_factcheck.md has fact notes for segment(s) {lost}, which the draft does not have. "
                         "Fix the section headings to the draft's segment numbers, or run factcheck --force.")
    missing = [n for n, _ in segments if n not in per_seg]
    if missing and not r.force:
        raise StageError(
            "08_edit_notes.md has no section for segment(s) "
            f"{', '.join(f'seg{n:02d}' for n in missing)}. The editor is asked for one per segment, so this "
            "usually means a heading the parser could not read. Fix the headings ('## seg01'), "
            "or use --force to revise those segments with the global notes only.")

    global_notes, per_seg = merge_notes(global_notes, per_seg, fact_global, fact_seg)
    system = load_prompt(
        "revise_context",
        style_guide=load_prompt("style_guide"),
        brief=brief, dossier=dossier, draft=draft, global_notes=global_notes or "(none)",
    )
    st = cfg["stages"]["revise"]
    est = sum(llm.estimate_stage(cfg, "revise", llm.estimate_tokens(system) + i * 1100,
                                 st["expected_output_tokens"]) for i in range(len(segments)))
    llm.guard(r, cfg, f"revise ({len(segments)} segments)", est)

    revised: list[tuple[int, str]] = []
    for n, text in segments:
        content = []
        if revised:
            content.append({
                "type": "text",
                "text": load_prompt("revise_earlier", earlier="\n\n".join(
                    f"<segment n=\"{k}\">\n{t}\n</segment>" for k, t in revised)),
                "cache_control": {"type": "ephemeral"},
            })
        content.append({"type": "text", "text": load_prompt(
            "revise", n=n, total=len(segments), segment=text,
            notes=per_seg.get(n, "(no notes for this segment; apply the global notes only)"),
        )})
        res = llm.call(r, cfg, "revise", system=system,
                       messages=[{"role": "user", "content": content}], label=f"revise seg{n:02d}")
        revised.append((n, res.text.strip()))
        print(f"  seg{n:02d}: {word_count(text)} -> {word_count(res.text)} words")

    script = join_script_segments(revised)
    out = r.write("script_final", script)
    print(f"  final script: {word_count(script):,} words")
    print(f"\n  Next: python cli.py narrate {r.name}. (Optional: read 09_script_final.md first.)")
    return out
