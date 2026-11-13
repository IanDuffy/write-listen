"""Acceptance check 11: the fast script against another run's script for the same seed, blind.

    python -m faststart compare <fast_run> <other_run_folder>

A separate model pass with fresh context reads the two final scripts, each beside its own
dossier's entries, and compares them on arc, repetition and accuracy. It sees them as A and B,
in an order fixed by a hash of the two run names, and is never told how either was made (the
dossiers go in as their entries alone: a fast dossier's own headings would give it away).
Code unblinds the answer, checks that every quotation is in the script it names, and writes
fast_compare.json and fast_compare.md into the fast run folder.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from stages import llm
from stages.common import MARKER, PATHS, Run, StageError, dossier_entries, load_prompt, parse_json

from . import base

CRITERIA = ("arc", "repetition", "accuracy")
VERDICTS = ("A", "B", "tie")
OUT_JSON, OUT_MD = "fast_compare.json", "fast_compare.md"

_TEXT = {"type": "string"}
_LABEL = {"type": "string", "enum": ["A", "B"]}
_VERDICT = {"type": "string", "enum": list(VERDICTS)}


def _obj(**props) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


# Structured output for the fast_compare call.
SCHEMA = _obj(
    criteria={"type": "array", "items": _obj(
        name={"type": "string", "enum": list(CRITERIA)}, verdict=_VERDICT, reason=_TEXT,
        quotes={"type": "array", "items": _obj(script=_LABEL, text=_TEXT, why=_TEXT)})},
    overall=_obj(verdict=_VERDICT, reason=_TEXT),
    differences={"type": "array", "items": _obj(script=_LABEL, quote=_TEXT, why_it_matters=_TEXT)})


def labels(fast_name: str, other_name: str) -> dict[str, str]:
    """Which script is A: fixed by the two run names, so a second pass on the same pair is the
    same test, and nobody chose."""
    first = int(hashlib.sha256(f"{fast_name}\0{other_name}".encode()).hexdigest(), 16) % 2 == 0
    return {"A": "fast", "B": "other"} if first else {"A": "other", "B": "fast"}


def _script(root: Path) -> str:
    p = root / PATHS["script_final"]
    if not p.exists():
        raise StageError(f"No final script at {p}: the comparison needs two finished runs.")
    return p.read_text(encoding="utf-8").strip()


def _entries(root: Path) -> str:
    """A run's dossier as its entries alone, in number order."""
    p = root / PATHS["dossier"]
    if not p.exists():
        raise StageError(f"No dossier at {p}.")
    entries = dossier_entries(p.read_text(encoding="utf-8"))
    return "\n\n".join(" ".join(entries[n].split()) for n in sorted(entries)) or "(no entries)"


def _plain(text: str) -> str:
    """Text for finding a quotation in: no markers, one kind of quotation mark, lower case, single spaces."""
    text = MARKER.sub("", text).translate(str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"}))
    return " ".join(re.sub(r"<!--.*?-->", " ", text, flags=re.S).lower().split())


def _prompt(r: Run, other_root: Path) -> tuple[str, dict[str, str], dict[str, str]]:
    """(the prompt, which run is A and which B, the two scripts by label)."""
    if other_root == r.root.resolve():
        raise StageError("The comparison needs two different runs.")
    roots = {"fast": r.root, "other": other_root}
    who = labels(r.name, other_root.name)
    scripts = {label: _script(roots[side]) for label, side in who.items()}
    prompt = load_prompt("fast_compare", script_a=scripts["A"], dossier_a=_entries(roots[who["A"]]),
                         script_b=scripts["B"], dossier_b=_entries(roots[who["B"]]))
    return prompt, who, scripts


def estimate(r: Run, other_root: Path, cfg: dict) -> float:
    """The call's upper-end cost, for the build's cap."""
    prompt, _, _ = _prompt(r, Path(other_root).resolve())
    return llm.estimate_stage(cfg, "fast_compare", llm.estimate_tokens(prompt))


def run(r: Run, other_root: Path, cfg: dict) -> dict:
    """Compare the fast run `r` with the run folder `other_root` (anywhere on disk). One paid
    call, logged in the fast run's log under stage fast_compare. Returns what fast_compare.json holds."""
    other_root = Path(other_root).resolve()
    prompt, who, scripts = _prompt(r, other_root)
    names = {"fast": r.name, "other": other_root.name}
    before = r.spent()
    res = llm.call(r, cfg, "fast_compare", label="fast compare", messages=[{"role": "user", "content": prompt}],
                   json_schema=SCHEMA, preapproved=True)
    verdict = parse_json(r, res.text, "fast_compare")

    plain = {label: _plain(text) for label, text in scripts.items()}
    side = lambda v: who.get(v, "tie")
    found = lambda label, quote: bool(_plain(quote)) and _plain(quote) in plain.get(label, "")
    unblinded = {
        "criteria": [{"name": c.get("name"), "winner": side(c.get("verdict")), "reason": c.get("reason", ""),
                      "quotes": [{"from": side(q.get("script")), "text": q.get("text", ""), "why": q.get("why", ""),
                                  "found": found(q.get("script"), q.get("text", ""))}
                                 for q in c.get("quotes") or []]}
                     for c in verdict.get("criteria") or []],
        "overall": {"winner": side((verdict.get("overall") or {}).get("verdict")),
                    "reason": (verdict.get("overall") or {}).get("reason", "")},
        "differences": [{"from": side(d.get("script")), "quote": d.get("quote", ""),
                         "why_it_matters": d.get("why_it_matters", ""),
                         "found": found(d.get("script"), d.get("quote", ""))}
                        for d in verdict.get("differences") or []]}
    missing = [c for c in CRITERIA if c not in [x["name"] for x in unblinded["criteria"]]]
    if missing:
        raise StageError(f"fast compare: the reply has no verdict on {', '.join(missing)}.")
    out = {"fast": names["fast"], "other": names["other"], "other_path": str(other_root), "labels": who,
           "verdict": verdict, "unblinded": unblinded, "cost_usd": round(r.spent() - before, 4),
           "compared_at": base.now_iso()}
    base.write_json(r.root / OUT_JSON, out)
    base.write_atomic(r.root / OUT_MD, render(out))
    print(f"  {OUT_MD}: overall {unblinded['overall']['winner']} "
          f"({', '.join(c['name'] + ' ' + c['winner'] for c in unblinded['criteria'])}), ${out['cost_usd']:.2f}")
    return out


def render(out: dict) -> str:
    """fast_compare.md: the verdicts with the labels undone, and the quotations."""
    who, u = out["labels"], out["unblinded"]
    name = {"fast": f"fast ({out['fast']})", "other": f"the other run ({out['other']})", "tie": "a tie"}
    flag = lambda q: "" if q["found"] else " (not found word for word in that script)"
    lines = [f"# Fast against {out['other']}: a blind comparison", "",
             f"The judge read two scripts labelled A and B and was not told how either was made. "
             f"A was {name[who['A']]}; B was {name[who['B']]}.", "",
             f"**Overall: {name[u['overall']['winner']]}.** {u['overall']['reason']}", "",
             "| Criterion | Better | Why |", "| --- | --- | --- |"]
    lines += [f"| {c['name']} | {name[c['winner']]} | {' '.join(c['reason'].split())} |" for c in u["criteria"]]
    for c in u["criteria"]:
        if c["quotes"]:
            lines += ["", f"## {c['name'].capitalize()}", ""]
            lines += [f"- From {name[q['from']]}: \"{' '.join(q['text'].split())}\"{flag(q)} {q['why']}"
                      for q in c["quotes"]]
    if u["differences"]:
        lines += ["", "## The differences that matter", ""]
        lines += [f"{i}. From {name[d['from']]}: \"{' '.join(d['quote'].split())}\"{flag(d)} {d['why_it_matters']}"
                  for i, d in enumerate(u["differences"], 1)]
    lines += ["", f"One call, ${out['cost_usd']:.2f}. In the judge's replies above, \"A\" and \"B\" are the labels it saw."]
    return "\n".join(lines) + "\n"
