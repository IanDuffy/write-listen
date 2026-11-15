"""intro_brief.md: the only things the morning intro may use (docs/overnight-chapters.md, Morning playback).

    ## Title                        the card's title
    ## Summary                      the card's summary
    ## Findings you may mention     two or three findings from the dossier: surprising, not the chapter's turns
    ## Why this one, for them       the link back to the listener, with their words

The model (Sonnet, stage overnight_intro) picks the findings from the dossier, told what each section
of the outline lands so it can stay clear of those, and writes the link back. It never sees the script.
Code then checks: each finding cites a real dossier entry, the listener's words are words they said (the
card's source and request), and nothing written shares a run of twelve words with the script beyond
what the card itself says (a dossier quote the script also uses would give a line away).
"""

from __future__ import annotations

import re
from pathlib import Path

from stages import llm
from stages.common import Run, StageError, dossier_entries, load_prompt, parse_json, spoken_text

from . import profiles

OUT = "intro_brief.md"
RUN_WORDS = 12

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["findings", "why_for_them"],
    "properties": {
        "findings": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "entry"],
            "properties": {"text": {"type": "string"}, "entry": {"type": "string"}}}},
        "why_for_them": {"type": "string"}},
}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace("’", "'"))


def runs(text: str, n: int = RUN_WORDS) -> set[tuple[str, ...]]:
    w = _words(text)
    return {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}


def gives_away(text: str, script_runs: set, allowed_runs: set) -> bool:
    """Whether text shares a run of RUN_WORDS words with the script that the card does not also have."""
    return bool((runs(text) & script_runs) - allowed_runs)


def _turns(outline: dict) -> str:
    out = []
    if outline.get("through_line"):
        out.append(f"- Through-line: {outline['through_line']}")
    if outline.get("closing_idea"):
        out.append(f"- Closing idea: {outline['closing_idea']}")
    for s in outline.get("sections") or []:
        out.append(f"- Section {s.get('n')}, {s.get('title', '')}: lands {s.get('lands', '')}")
    return "\n".join(out) or "(no outline)"


def run(r: Run, cfg: dict) -> Path:
    out = r.root / OUT
    if out.exists():
        return out
    card = profiles.read_json(r.need(r.root / "00_source" / "up_next.json"))
    if not card:
        raise StageError("intro: 00_source/up_next.json is not readable.")
    dossier = r.read("dossier")
    entries = dossier_entries(dossier)
    outline = profiles.read_json(r.root / "05_outline.json") or {}
    src, req = card.get("source") or {}, card.get("request") or {}
    words = [w for w in (src.get("words"), req.get("topic"), req.get("notes"), card.get("notes")) if w]
    prompt = load_prompt(
        "overnight_intro", title=card["title"], summary=card.get("summary", ""), turns=_turns(outline),
        dossier=dossier, source=f"{card.get('decided') or card.get('mode')}: {src.get('kind')} {src.get('ref') or ''}".strip(),
        words="\n".join(f'- "{w}"' for w in words) or "(none: it came from what the planner knew of them)")
    res = llm.call(r, cfg, "overnight_intro", label="intro brief", json_schema=SCHEMA,
                   messages=[{"role": "user", "content": prompt}])
    data = parse_json(r, res.text, "overnight_intro")

    script = r.root / "09_script_final.md"
    script_runs = runs(spoken_text(script.read_text(encoding="utf-8"))) if script.exists() else set()
    allowed = runs(" ".join([card["title"], card.get("summary", "")] + words))
    findings, dropped = [], []
    for f in data.get("findings") or []:
        text = " ".join(str(f.get("text") or "").split())
        m = re.search(r"\d+", str(f.get("entry") or ""))
        if not text or not m or int(m.group()) not in entries:
            dropped.append(f"{text[:60]!r}: no dossier entry {f.get('entry')!r}")
        elif gives_away(text, script_runs, allowed):
            dropped.append(f"{text[:60]!r}: shares a line with the script")
        else:
            findings.append(text)
    findings = findings[:3]
    why = " ".join(s for s in re.split(r"(?<=[.!?])\s+", " ".join(str(data.get("why_for_them") or "").split()))
                   if s and not gives_away(s, script_runs, allowed))
    if not findings:
        raise StageError(f"intro: no finding passed the checks ({'; '.join(dropped) or 'none returned'}).")
    if dropped:
        r.decide("intro", "Left out of the intro brief: " + "; ".join(dropped))

    lines = [f"# Intro brief: {card['title']}", "",
             "The only things the morning intro may use. The guide has not heard the chapter; it must not "
             "promise or reveal anything beyond this page.", "",
             "## Title", "", card["title"], "", "## Summary", "", card.get("summary", ""), "",
             "## Findings you may mention", ""]
    lines += [f"- {f}" for f in findings]
    lines += ["", "## Why this one, for them", "", why or "(no link back was written)"]
    if words:
        lines += ["", "Their words: " + " / ".join(f'"{w}"' for w in words[:2])]
    tmp = out.with_name(f".{OUT}.tmp")
    tmp.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    tmp.replace(out)
    print(f"  {OUT}: {len(findings)} findings" + (f", {len(dropped)} left out" if dropped else ""))
    return out
