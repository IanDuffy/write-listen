"""Overnight stage 4: does the chapter keep its card's promise? (docs/overnight-chapters.md, check 7)

Input: 07_script_draft.md, 00_source/up_next.json (its summary is what the listener was told the
chapter would be), 05_outline.json. Output: 08_promise.json, 09_script_final.md, and 05_brief.md
rendered again from the final script (the entries each section cites, the titles the locks gave).

One judge (stage overnight_promise, structured output) reads the script against the summary, point
by point, and says whether it keeps it; where it drifted, a fix note for each section that needs one.
If it drifted, each of those sections gets exactly one revision (stage overnight_revise, the writing
calls' own system prompt, so the marker, quote and style rules hold), checked by code like a lock
(faststart/checks.py: a bad marker or an unbacked quote repaired, a revision that lost the section
dropped). There is no second check: one revision only, and the verdicts are recorded either way. A
chapter that kept its promise is copied unchanged.

The judge's answer and each revision are kept in work/, so a build stopped after either pays nothing
for it again. 09_script_final.md is written last: it is the stage's done-file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from faststart import base, checks, sections, status
from faststart import outline as fast_outline
from stages import ledger, llm
from stages.common import (MARKER, SEGMENT_MARKER, Run, StageError, entry_numbers, join_script_segments, load_prompt,
                           parse_json, split_script_segments, spoken_text, word_count)

from . import profiles
from .outline import card, card_words, work
from .write import context, said

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["keeps", "points", "drift", "sections"],
    "properties": {
        "keeps": {"type": "boolean"},
        "points": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["point", "kept", "where"],
            "properties": {"point": {"type": "string"}, "kept": {"type": "boolean"}, "where": {"type": "string"}}}},
        "drift": {"type": "array", "items": {"type": "string"}},
        "sections": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["n", "fix"],
            "properties": {"n": {"type": "integer"}, "fix": {"type": "string"}}}},
    },
}


def _one(v) -> str:
    return " ".join(str(v or "").split())


def _clean(text: str) -> str:
    """A revised section as stored: no segment marker (the join adds it), no code fence."""
    text = re.sub(r"^```[a-z]*\s*$", "", SEGMENT_MARKER.sub("", str(text or "")), flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def script_text(segs: list[tuple[int, str]], outline: dict) -> str:
    """The chapter as the judge reads it: what the listener hears, section by section."""
    titles = {s["n"]: s.get("title") or "" for s in outline["sections"]}
    return "\n\n".join(f'<section n="{n}" title="{_one(titles.get(n))}">\n{spoken_text(t)}\n</section>'
                       for n, t in segs)


def _check(r: Run, cfg: dict, c: dict, outline: dict, segs: list[tuple[int, str]]) -> dict:
    p = work(r, "promise_check.json")
    data = profiles.read_json(p)
    if data is None:
        prompt = load_prompt("overnight_promise", title=_one(c.get("title")), overview=_one(c.get("overview")),
                             summary=c["summary"].strip(), words=card_words(c) or "(none)",
                             through_line=_one(outline.get("through_line")), total=len(segs),
                             script=script_text(segs, outline))
        st = cfg["stages"]["overnight_promise"]
        res = llm.call(r, cfg, "overnight_promise", label="overnight promise", json_schema=SCHEMA, preapproved=True,
                       messages=[{"role": "user", "content": prompt}], max_tokens=st.get("max_tokens"),
                       effort=st.get("effort"), expected_output_tokens=st.get("expected_output_tokens"))
        data = parse_json(r, res.text, "overnight_promise")
        profiles.write_json(p, data)
    return data


def _revise(r: Run, cfg: dict, ctx, c: dict, verdict: dict, segs: list[tuple[int, str]], n: int,
            fix: str) -> tuple[str, list[str]]:
    """One revision of section n, checked by code. Returns the text to use and what was found."""
    p = work(r, f"revise_seg{n:02d}.md")
    old = dict(segs)[n]
    if p.exists():
        new = p.read_text(encoding="utf-8").strip()
    else:
        others = "\n\n".join(f'<section n="{k}">\n{t.strip()}\n</section>' for k, t in segs if k != n)
        entries = ctx.dossier.entries()
        prompt = load_prompt("overnight_revise", n=n, total=len(segs), summary=c["summary"].strip(),
                             drift="\n".join(f"- {_one(d)}" for d in verdict.get("drift") or []) or "(none listed)",
                             fix=fix.strip(), others=others, section=old.strip(),
                             plan=sections.plan_text(sections.section(ctx.outline, n)),
                             entries="\n\n".join(entries[d] for d in sorted(entries)) or "(none)")
        res = llm.call(r, cfg, "overnight_revise", system=sections.system_prompt(ctx),
                       label=f"overnight revise seg{n:02d}", messages=[{"role": "user", "content": prompt}],
                       preapproved=True, log_extra={"section": n})
        new = _clean(res.text)
        base.write_atomic(p, new)
    keep = cfg["overnight"].get("write", {}).get("revise_keep", 0.6)
    if not new or word_count(new) < keep * word_count(old):
        return old, [f"the revision came back {word_count(new)} words against {word_count(old)}; the section "
                     "stands as it was"]
    dossier_text, words = ctx.dossier.text(), said(r, ctx.outline)
    found = checks.section_problems(new, dossier_text, said=words)
    if not found:
        return new, []
    fixed, done = checks.repair(new, dossier_text, None, words)
    left = checks.section_problems(fixed, dossier_text, said=words) if fixed else ["the revision has no text"]
    if not fixed:
        return old, found + ["the repaired revision had no text; the section stands as it was"]
    return fixed, found + [f"repaired by code: {d}" for d in done] + [f"not repaired: {x}" for x in left]


def write_brief(r: Run, outline: dict, segs: list[tuple[int, str]]) -> None:
    """05_brief.md as the final script stands: each section lists the entries it cites (ask-by-voice
    shows the guide those in full for the section playing and the next) under the title its lock
    gave it, if any."""
    with base.LEDGER_LOCK:
        led = ledger.normalize(json.loads(r.read("ledger")))
    cited = {n: entry_numbers(" ".join(m.group(0) for m in MARKER.finditer(t))) for n, t in segs}
    base.write_atomic(r.path("brief"), fast_outline.render_brief(outline, led, cited, titles=status.titles(r, outline)))


def run(r: Run, cfg: dict) -> Path:
    """Check the draft against the card's summary; if it drifted, revise each section the judge
    names, once. Writes 08_promise.json and 09_script_final.md; returns the latter's path (at once,
    with no call, when it exists)."""
    final = r.path("script_final")
    if final.exists():
        return final
    c = card(r)
    ctx = context(r, cfg)
    segs = split_script_segments(r.read("script_draft"))
    numbers = [n for n, _ in segs]
    if numbers != list(range(1, len(ctx.outline["sections"]) + 1)):
        raise StageError(f"overnight promise: 07_script_draft.md has sections {numbers}; the outline has "
                         f"{len(ctx.outline['sections'])}.")
    verdict = _check(r, cfg, c, ctx.outline, segs)
    keeps = bool(verdict.get("keeps"))
    fixes = {}
    for s in verdict.get("sections") or []:
        if isinstance(s, dict) and s.get("n") in numbers and _one(s.get("fix")):
            fixes[s["n"]] = (fixes.get(s["n"], "") + "\n" + str(s["fix"]).strip()).strip()
    out, revised, problems = dict(segs), [], {}
    if not keeps:
        for n in sorted(fixes):
            text, found = _revise(r, cfg, ctx, c, verdict, segs, n, fixes[n])
            if text != out[n]:
                revised.append(n)
            out[n] = text
            if found:
                problems[n] = found
    script = join_script_segments(sorted(out.items()))
    words = word_count(script)
    record = {"verdict": "kept" if keeps else "drifted", "summary": c["summary"].strip(),
              "points": verdict.get("points") or [], "drift": verdict.get("drift") or [],
              "fixes": {str(n): f for n, f in sorted(fixes.items())}, "revised": revised,
              "problems": {str(n): p for n, p in problems.items()}, "words": words,
              "minutes": round(words / cfg["overnight"]["words_per_minute"], 1)}
    write_brief(r, ctx.outline, sorted(out.items()))
    profiles.write_json(r.root / "08_promise.json", record)
    if keeps:
        r.decide("overnight promise", "The chapter keeps its card's promise; the draft stands as written."
                 + ("".join(f"\n- {_one(d)}" for d in record["drift"]) if record["drift"] else ""))
    else:
        lines = ["The chapter drifted from its card's promise:"] + [f"- {_one(d)}" for d in record["drift"]]
        lines += [f"Revised once: section {', '.join(map(str, revised))}." if revised else
                  "No section was revised" + (" (no fix notes)." if not fixes else " (every revision was dropped).")]
        lines += [f"- Section {n}: {p}" for n, ps in problems.items() for p in ps]
        lines.append("Not checked again: one revision only.")
        r.decide("overnight promise", "\n".join(lines))
    base.write_atomic(final, script)
    print(f"  09_script_final.md: the promise {'kept' if keeps else 'drifted'}"
          + (f"; revised section(s) {', '.join(map(str, revised))}" if revised else ""))
    return final
