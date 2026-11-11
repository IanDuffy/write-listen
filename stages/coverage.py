"""Stage 07b: 07_script_draft.md + ledger.json + 05_brief.md -> 07b_coverage.md (+ ledger columns)

A fresh-context reader (Opus, structured output) checks the assembled draft
against the ledger. For each active row it sets coverage (covered, thin,
missing, repeated), the segments where the idea appears, and a one-line note for
Ian. It also reports drift (substantial ideas that map to no ledger row),
whether the brief's through-line is audible from start to finish, and direct
quotes not backed by a verbatim: yes dossier entry.

Code checks quotes too: any passage in quotation marks that matches no
verbatim: yes entry is listed. The stage 08 editor reads 07b_coverage.md
alongside the draft. There is no score: this is a report, not a number to optimize.
"""

from __future__ import annotations

import re

from . import ledger, llm
from .common import (Run, StageError, dossier_entries, entry_tags, load_prompt, parse_json,
                     spoken_text, split_script_segments)
from .research import ELLIPSIS, QUOTE, _norm_text

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rows", "drift", "through_line", "unbacked_quotes"],
    "properties": {
        "rows": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "coverage", "segments", "note"],
            "properties": {
                "id": {"type": "string"},
                "coverage": {"type": "string", "enum": list(ledger.COVERAGES)},
                "segments": {"type": "array", "items": {"type": "integer"}},
                "note": {"type": "string"},
            },
        }},
        "drift": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["idea", "segments", "note"],
            "properties": {"idea": {"type": "string"}, "segments": {"type": "array", "items": {"type": "integer"}},
                           "note": {"type": "string"}},
        }},
        "through_line": {"type": "string"},
        "unbacked_quotes": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["quote", "segment", "note"],
            "properties": {"quote": {"type": "string"}, "segment": {"type": "integer"}, "note": {"type": "string"}},
        }},
    },
}


def verbatim_entries(dossier: str) -> dict[int, str]:
    entries = dossier_entries(dossier)
    return {n: text for n, text in entries.items() if entry_tags(text)["verbatim"] == "yes"}


def unbacked_quotes(script: str, dossier: str) -> list[tuple[int, str]]:
    """Passages in quotation marks (four words or more) that appear in no verbatim: yes entry.
    An ellipsis marks a cut: each piece must appear in the same entry."""
    # Only the quoted passages of verbatim entries, not their attributions or paraphrase.
    backed = [f" {_norm_text(q)} " for t in verbatim_entries(dossier).values()
              for q in QUOTE.findall(re.split(r"\bSource:", t, maxsplit=1)[0])]
    out = []
    for n, body in split_script_segments(script):
        for q in QUOTE.findall(spoken_text(body)):
            pieces = [p for p in (_norm_text(x) for x in ELLIPSIS.split(q)) if p]
            if sum(len(p.split()) for p in pieces) < 4:
                continue
            if not any(all(f" {p} " in passage for p in pieces) for passage in backed):
                out.append((n, " ".join(q.split())))
    return out


def render(name: str, led: dict, data: dict, code_quotes: list[tuple[int, str]]) -> str:
    def segs(xs) -> str:
        return ", ".join(str(x) for x in xs) or "-"

    lines = [f"# Coverage check: {name}", "",
             "Stage 07b: the draft checked against the ledger, in a fresh context. A report for Ian and the "
             "editor, not a score.", "", "## Ledger rows", "",
             "| Row | Rank | Idea | Coverage | Segments | Note |", "| --- | ---: | --- | --- | --- | --- |"]
    for row in ledger.active(led):
        lines.append(f"| {row['id']} | {row['rank']} | {ledger.cell(row['idea'])} | {row['coverage'] or '-'} | "
                     f"{segs(row['segments'])} | {ledger.cell(row['note'])} |")
    lines += ["", "## Through-line", "", data.get("through_line", "").strip() or "(not assessed)", "",
              "## Drift", "", "Substantial ideas in the draft that map to no ledger row.", ""]
    lines += [f"- Segments {segs(d.get('segments', []))}: {d.get('idea', '')}. {d.get('note', '')}".rstrip()
              for d in data.get("drift", [])] or ["None found."]
    lines += ["", "## Quotes not backed by a verbatim: yes entry", "", "Found by the reader:", ""]
    lines += [f"- seg{q.get('segment', 0):02d}: \"{q.get('quote', '')}\" {q.get('note', '')}".rstrip()
              for q in data.get("unbacked_quotes", [])] or ["- None found."]
    lines += ["", "Found by code (words in quotation marks that match no verbatim: yes entry):", ""]
    lines += [f"- seg{n:02d}: \"{q}\"" for n, q in code_quotes] or ["- None found."]
    return "\n".join(lines) + "\n"


def run(r: Run, cfg: dict):
    draft = r.read("script_draft")
    brief = r.read("brief")
    dossier = r.read("dossier")
    led = ledger.load(r, cfg)
    r.check_writable("coverage")
    code_quotes = unbacked_quotes(draft, dossier)
    verbatim = "\n\n".join(verbatim_entries(dossier).values()) or "(none)"
    prompt = load_prompt(
        "coverage", ledger=ledger.describe(ledger.active(led), research=True), brief=brief, draft=draft,
        verbatim=verbatim,
        quote_check="\n".join(f"- seg{n:02d}: \"{q}\"" for n, q in code_quotes) or "(none)",
    )
    res = llm.call(r, cfg, "coverage", messages=[{"role": "user", "content": prompt}], json_schema=SCHEMA)
    data = parse_json(r, res.text, "coverage")
    verdicts = {v.get("id"): v for v in data.get("rows", []) if isinstance(v, dict)}
    missing = [row["id"] for row in ledger.active(led) if row["id"] not in verdicts]
    if missing:
        (r.root / "_partial_coverage.json").write_text(res.text, encoding="utf-8")
        raise StageError(f"coverage: no verdict for {', '.join(missing)}; output saved to _partial_coverage.json.")
    for row in ledger.active(led):
        v = verdicts[row["id"]]
        row.update(coverage=v["coverage"] if v.get("coverage") in ledger.COVERAGES else None,
                   segments=sorted({s for s in v.get("segments", []) if isinstance(s, int)}),
                   note=" ".join(str(v.get("note", "")).split()))
    ledger.save(r, led)
    out = r.write("coverage", render(r.name, led, data, code_quotes))
    counts = {c: sum(1 for row in ledger.active(led) if row["coverage"] == c) for c in ledger.COVERAGES}
    print(f"  coverage: {', '.join(f'{c} {n}' for c, n in counts.items() if n)}; "
          f"{len(data.get('drift', []))} drift, {len(data.get('unbacked_quotes', [])) + len(code_quotes)} "
          "unbacked quote(s). See 07b_coverage.md and ledger.md.")
    print(f"\n  Next: python cli.py edit {r.name} (the editor reads 07b_coverage.md).")
    return out
