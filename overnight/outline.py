"""Overnight stage 3a: the outline, written after the research (docs/overnight-chapters.md, DESIGN.md).

Inputs: 00_source/up_next.json (the card: its summary is the promise the chapter keeps), 01_map.md,
03_angle.md, ledger.json with stage 04b's verdicts, 04_dossier.md. Output: 05_outline.json (the source
of truth), 05_outline.md, 05_brief.md, and each ledger row's `section`.

Fast Start writes its outline before any research and has to guess (faststart/outline.py). Here the
research is finished, so the outline is planned from the whole dossier: each section names the ledger
rows it lands and the dossier entries it draws on. The pattern is fast start's: one call writes the
outline, a second call in a fresh context edits it once, code checks it (`validate`), word targets
wrong only in their arithmetic are scaled by code (faststart's repair_targets), anything else gets at
most one repair call, and if that fails too the first outline stands when it was sound. Every answer
is kept in work/, so a run stopped after a call never pays for it again.

The section fields are the ones faststart/sections.py reads (title, lands, sets_up, hand_off,
target_words, notes), plus `rows` (ledger ids) and `entries` (D numbers, any in the dossier).
"""

from __future__ import annotations

import copy
import json

from faststart import base
from faststart import outline as fast_outline
from stages import ledger, llm
from stages.common import (Run, StageError, entry_id, listener_profile, load_prompt, parse_json, prompt_name,
                           seed_inputs)

from . import profiles
from .write import Dossier

TITLE_WORDS = fast_outline.TITLE_WORDS
WORK = "work"  # this chapter's working files: every paid answer, kept so a resumed build pays nothing twice

_SECTION = {
    "type": "object",
    "additionalProperties": False,
    "required": ["n", "title", "lands", "sets_up", "hand_off", "target_words", "notes", "rows", "entries"],
    "properties": {
        "n": {"type": "integer"},
        "title": {"type": "string"},
        "lands": {"type": "string"},
        "sets_up": {"type": "string"},
        "hand_off": {"type": "string"},
        "target_words": {"type": "integer"},
        "notes": {"type": "string"},
        "rows": {"type": "array", "items": {"type": "string"}},
        "entries": {"type": "array", "items": {"type": "integer"}},
    },
}
# The outline call's and the repair call's structured output. The title is the card's (set by code):
# the listener saw it on the Up next card, and the chapter keeps it.
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["angle", "why", "through_line", "closing_idea", "listener_words", "sections"],
    "properties": {
        "angle": {"type": "string"},
        "why": {"type": "string"},
        "through_line": {"type": "string"},
        "closing_idea": {"type": "string"},
        "listener_words": {"type": "array", "items": {"type": "string"}},
        "sections": {"type": "array", "items": _SECTION},
    },
}
EDIT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["changes", "outline"],
    "properties": {"changes": {"type": "array", "items": {"type": "string"}}, "outline": SCHEMA},
}


# --- inputs ---------------------------------------------------------------------------------

def card(r: Run) -> dict:
    """00_source/up_next.json, the plan the build locked (overnight/runner.py)."""
    data = profiles.read_json(r.root / "00_source" / "up_next.json")
    if not isinstance(data, dict) or not str(data.get("summary") or "").strip():
        raise StageError("overnight: 00_source/up_next.json is missing or has no summary; lock the plan first.")
    return data


def card_words(c: dict) -> str:
    """The listener's own words the plan came from, if any."""
    return str(((c.get("source") or {}).get("words")) or "").strip()


def work(r: Run, name: str):
    return r.root / WORK / name


def limits(cfg: dict) -> dict:
    """The overnight sizes in words (config.yaml overnight and overnight.write), computed by fast
    start's own limits() from the same settings in its shape."""
    o, w = cfg["overnight"], cfg["overnight"].get("write") or {}
    shape = {"fast": {"words_per_minute": o["words_per_minute"], "target_minutes": w.get("target_minutes", 20),
                      "sections": o["sections"], "total_minutes": o["total_minutes"],
                      "opening_min_minutes": w.get("opening_min_minutes", 3),
                      "section_min_minutes": w.get("section_min_minutes", 2),
                      "research": {"per_row": {"searches": 1, "fetches": 0}, "max_searches": 0}}}
    lim = fast_outline.limits(shape)
    lim.pop("max_questions"), lim.pop("row_searches"), lim.pop("row_fetches")
    return {**lim, "_shape": shape}


def _said(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _mins(words, lim: dict) -> str:
    return f"{words / lim['words_per_minute']:.2f}".rstrip("0").rstrip(".")


def _ids(numbers) -> str:
    return ", ".join(entry_id(k) for k in sorted(set(numbers)))


# --- the validator --------------------------------------------------------------------------

def validate(outline: dict, led: dict, numbers: set[int], cfg: dict) -> list[str]:
    """Every rule the outline breaks, in plain words (the edit and repair calls read them); [] when
    valid. overnight.sections sections numbered 1..N, each with a title of at most six words and its
    lands, sets_up and hand_off; word targets at or above their floors, adding up to inside
    overnight.total_minutes; every ledger row landed by exactly one section, and no unknown row;
    every section citing at least one entry, and only entries the dossier has."""
    lim = limits(cfg)
    errors: list[str] = []
    if not isinstance(outline, dict):
        return ["The outline is not a JSON object."]
    for field in ("title", "angle", "through_line", "closing_idea"):
        if not _said(outline.get(field)):
            errors.append(f"The outline's {field} is empty.")
    sections = outline.get("sections")
    if not isinstance(sections, list) or not all(isinstance(s, dict) for s in sections):
        return errors + ["The outline has no list of sections."]
    lo, hi = lim["min_sections"], lim["max_sections"]
    if not lo <= len(sections) <= hi:
        errors.append(f"The outline has {len(sections)} sections; it needs {lo} to {hi}.")
    got = [s.get("n") for s in sections]
    if got != list(range(1, len(sections) + 1)):
        errors.append(f"The sections must be numbered 1 to {len(sections)} in order; they are numbered {got}.")
    known = {row["id"] for row in led.get("rows") or []}
    landed: dict[str, list[int]] = {}
    total, sized = 0, True
    for i, s in enumerate(sections, 1):
        name = f"Section {i}" + (" (the opening)" if i == 1 else "")
        title = s.get("title")
        if not _said(title):
            errors.append(f"{name} has no title.")
        elif len(title.split()) > TITLE_WORDS:
            errors.append(f"{name}'s title is {len(title.split())} words (\"{title.strip()}\"); a marker title is "
                          f"at most {TITLE_WORDS}.")
        for field, what in (("lands", "what it lands"), ("sets_up", "what it sets up"),
                            ("hand_off", "how it hands off")):
            if not _said(s.get(field)):
                errors.append(f"{name} does not say {what} ({field} is empty).")
        words = s.get("target_words")
        if not _int(words) or words <= 0:
            errors.append(f"{name}'s target_words must be a whole number of words.")
            sized = False
        else:
            total += words
            floor, minutes = ((lim["opening_min_words"], lim["opening_min_minutes"]) if i == 1 else
                              (lim["section_min_words"], lim["section_min_minutes"]))
            if words < floor:
                errors.append(f"{name} is {words} words, {_mins(words, lim)} minutes; "
                              + (f"the opening needs at least {floor} words ({minutes} minutes)." if i == 1 else
                                 f"no section may be under {floor} words ({minutes} minutes)."))
        rows = s.get("rows")
        if not isinstance(rows, list) or not all(isinstance(x, str) for x in rows):
            errors.append(f"{name}'s rows must be a list of ledger row ids.")
            rows = []
        for rid in rows:
            if rid not in known:
                errors.append(f"{name} lists {rid}, which is not a ledger row ({', '.join(sorted(known))}).")
            else:
                landed.setdefault(rid, []).append(i)
        cited = s.get("entries")
        if not isinstance(cited, list) or not all(_int(x) for x in cited):
            errors.append(f"{name}'s entries must be a list of entry numbers.")
            cited = []
        unknown = sorted({x for x in cited if x not in numbers})
        if unknown:
            errors.append(f"{name} lists {_ids(unknown)}, which "
                          f"{'is not an entry' if len(unknown) == 1 else 'are not entries'} in the dossier.")
        if not cited:
            errors.append(f"{name} lists no dossier entries; every fact it tells must come from one.")
    for rid in sorted(known):
        if rid not in landed:
            errors.append(f"Ledger row {rid} is landed by no section; every row needs exactly one.")
        elif len(landed[rid]) > 1:
            errors.append(f"Ledger row {rid} is listed by sections {', '.join(map(str, landed[rid]))}; "
                          "one section lands it (another may recall it in a clause).")
    if sized and sections and not lim["min_words"] <= total <= lim["max_words"]:
        errors.append(f"The sections add up to {total} words, {_mins(total, lim)} minutes; the chapter must be "
                      f"between {lim['min_words']} and {lim['max_words']} words "
                      f"({_mins(lim['min_words'], lim)} to {_mins(lim['max_words'], lim)} minutes).")
    return errors


def repair_targets(outline: dict, cfg: dict) -> dict:
    """A copy with its word targets scaled into range (faststart's arithmetic, at the overnight sizes)."""
    return fast_outline.repair_targets(outline, limits(cfg)["_shape"])


# --- reading and the files written from it ---------------------------------------------------

def load(r: Run) -> dict:
    return json.loads(r.read(base.path(r, "outline_json")))


def _t(d: dict, key: str) -> str:
    return str(d.get(key) or "").strip()


def render_md(outline: dict) -> str:
    """05_outline.md: the outline for a person to read."""
    total = sum(s["target_words"] for s in outline["sections"])
    out = [f"# Outline: {_t(outline, 'title')}", "",
           "Rendered from 05_outline.json. Written after the research, from the whole dossier, then edited once "
           "by a second editor.", "",
           "## The promise (the Up next card)", "", _t(outline, "promise"), "",
           "## Angle", "", _t(outline, "angle"), "",
           "## Why this one", "", _t(outline, "why") or "(not said)", "",
           "## Through-line", "", _t(outline, "through_line"), "",
           "## Closing idea", "", _t(outline, "closing_idea"), "",
           "## The listener's words", ""]
    out += [f"- {w.strip()}" for w in outline.get("listener_words") or [] if _said(w)] or ["(none to echo)"]
    out += ["", f"## Sections ({len(outline['sections'])}, {total} words)", ""]
    for s in outline["sections"]:
        out += [f"### {s['n']}. {_t(s, 'title')} ({s['target_words']} words)", "",
                f"- Lands: {_t(s, 'lands')}",
                f"- Sets up: {_t(s, 'sets_up')}",
                f"- Hands off: {_t(s, 'hand_off')}",
                f"- How to tell it: {_t(s, 'notes') or '(no notes)'}",
                f"- Ledger rows: {', '.join(s.get('rows') or []) or 'none'}",
                f"- Entries: {_ids(s.get('entries') or []) or 'none'}", ""]
    return "\n".join(out)


def cited(outline: dict, led: dict, dossier: Dossier) -> dict[int, list[int]]:
    """Each section's entries for 05_brief.md: the ones the outline lists and its rows' own."""
    return {s["n"]: sorted({*(s.get("entries") or []),
                            *(d for row in fast_outline.rows_for(led, s["n"]) for d in dossier.numbers_of(row["id"]))})
            for s in outline["sections"]}


def _assign(r: Run, outline: dict) -> dict:
    """Each ledger row's `section`, from the outline, in ledger.json and ledger.md. Returns the ledger."""
    home = {rid: s["n"] for s in outline["sections"] for rid in s.get("rows") or []}
    with base.LEDGER_LOCK:
        led = ledger.normalize(json.loads(r.read("ledger")))
        if any(row.get("section") != home.get(row["id"]) for row in led["rows"]):
            for row in led["rows"]:
                row["section"] = home.get(row["id"])
            ledger.save(r, led)
    return led


def _write_files(r: Run, outline: dict, dossier: Dossier) -> dict:
    """The ledger's sections, 05_outline.md and 05_brief.md where missing, then 05_outline.json, the
    stage's done-file, last."""
    led = _assign(r, outline)
    if not base.path(r, "outline").exists():
        base.write_atomic(base.path(r, "outline"), render_md(outline))
    if not r.path("brief").exists():
        base.write_atomic(r.path("brief"), fast_outline.render_brief(outline, led, cited(outline, led, dossier)))
    if not base.path(r, "outline_json").exists():
        base.write_json(base.path(r, "outline_json"), outline)
    return led


# --- the stage ------------------------------------------------------------------------------

def _clean(outline: dict, c: dict) -> dict:
    """The model's outline with stray whitespace gone, entry numbers sorted, row ids in order and
    once each, and the card's title and summary added: the chapter keeps the card's title, and the
    summary is the promise every later stage reads."""
    def tidy(v):
        if isinstance(v, str):
            return " ".join(v.split())
        if isinstance(v, list):
            return [tidy(x) for x in v]
        if isinstance(v, dict):
            return {k: tidy(x) for k, x in v.items()}
        return v

    out = tidy(outline) if isinstance(outline, dict) else outline
    if not isinstance(out, dict):
        return out
    for s in out.get("sections") or []:
        if not isinstance(s, dict):
            continue
        if isinstance(s.get("entries"), list) and all(_int(x) for x in s["entries"]):
            s["entries"] = sorted(set(s["entries"]))
        if isinstance(s.get("rows"), list) and all(isinstance(x, str) for x in s["rows"]):
            s["rows"] = list(dict.fromkeys(x.strip().upper() for x in s["rows"] if x.strip()))
    return {"title": " ".join(str(c.get("title") or "").split()), "promise": " ".join(c["summary"].split()),
            **{k: v for k, v in out.items() if k not in ("title", "promise")}}


def ledger_text(led: dict, dossier: Dossier) -> str:
    """The ledger as the outline's editors read it: each row with how its research came back."""
    lines = []
    for row in ledger.active(led):
        nums = dossier.numbers_of(row["id"])
        status = row.get("status") or "not judged"
        tier = f" (best tier {row['best_tier']})" if row.get("best_tier") else ""
        lines += [f"- {row['id']} (rank {row['rank']}, {row['type']}, needs tier {row['tier_needed']}): "
                  f"{_t(row, 'idea')}",
                  f"  Research question: {_t(row, 'research_question')}",
                  f"  Research came back {status}{tier}"
                  + (f"; gap: {_t(row, 'gap')}" if _t(row, "gap") else "")
                  + (f"; entries {_ids(nums)}" if nums else "; no entries")]
    for c in led.get("connections") or []:
        lines.append(f"- Connection ({', '.join(c.get('rows') or [])}): {_t(c, 'link')}")
    parked = [p if isinstance(p, str) else p.get("idea", "") for p in led.get("parked") or []]
    if any(_said(p) for p in parked):
        lines += ["", "Parked (not researched; for a later chapter):"] + [f"- {p.strip()}" for p in parked if _said(p)]
    return "\n".join(lines) or "(no rows)"


def _ask(r: Run, cfg: dict, stage: str, label: str, prompt: str, schema: dict, saved: str) -> dict:
    """One structured call, its answer kept in work/<saved> so a build stopped after it does not pay again."""
    p = work(r, saved)
    data = profiles.read_json(p)
    if data is None:
        st = cfg["stages"].get(stage) or cfg["stages"]["overnight_outline_edit"]  # the repair borrows the edit's
        res = llm.call(r, cfg, stage, label=label, messages=[{"role": "user", "content": prompt}],
                       json_schema=schema, preapproved=True, max_tokens=st.get("max_tokens"), effort=st.get("effort"),
                       expected_output_tokens=st.get("expected_output_tokens"))
        data = parse_json(r, res.text, stage)
        profiles.write_json(p, data)
    return data


def run(r: Run, cfg: dict) -> dict:
    """Write, edit and check the outline, and the files made from it. Returns the outline. With
    05_outline.json there, it is returned without a call (and any file made from it that is missing
    is written)."""
    dossier = Dossier(r)
    if base.path(r, "outline_json").exists():
        outline = load(r)
        _write_files(r, outline, dossier)
        return outline
    c = card(r)
    led = ledger.normalize(json.loads(r.read("ledger")))
    numbers = set(dossier.entries())
    if not numbers:
        raise StageError("overnight outline: 04_dossier.md has no entries; run the research first.")
    lim = {k: v for k, v in limits(cfg).items() if not k.startswith("_")}
    inputs = seed_inputs(r, cfg) or {"seed": "(not recorded)", "profile": listener_profile(cfg)}
    shared = {**inputs, **lim, "title": c.get("title") or "", "overview": c.get("overview") or "",
              "summary": c["summary"], "words": card_words(c) or "(none: the plan did not come from their words)",
              "angle": r.read("angle").strip(), "map": r.read("map").strip(), "ledger": ledger_text(led, dossier),
              "dossier": dossier.text().strip(), "row_ids": ", ".join(row["id"] for row in ledger.active(led)),
              "source_tiers": load_prompt(prompt_name(r, "source_tiers")).strip()}
    as_json = lambda o: json.dumps({k: v for k, v in o.items() if k not in ("title", "promise")}, ensure_ascii=False,
                                   indent=1)
    problems = lambda errs: "\n".join(f"- {e}" for e in errs) or "None."
    check = lambda o: validate(o, led, numbers, cfg)

    first = _clean(_ask(r, cfg, "overnight_outline", "overnight outline", load_prompt("overnight_outline", **shared),
                        SCHEMA, "outline_first.json"), c)
    found = check(first)
    edit = _ask(r, cfg, "overnight_outline_edit", "overnight outline edit", load_prompt(
        "overnight_outline_edit", **shared, outline=as_json(first), problems=problems(found)), EDIT_SCHEMA,
        "outline_edit.json")
    outline, changes = edit.get("outline"), [x.strip() for x in edit.get("changes") or [] if _said(x)]
    if not isinstance(outline, dict):
        work(r, "outline_edit.json").unlink(missing_ok=True)  # the next try asks again
        raise StageError("overnight outline edit: the reply has no outline.")
    outline = _clean(outline, c)
    errors, repaired, scaled, dropped = check(outline), [], None, []

    def mend(o):
        """(o with its word targets scaled, the targets before and after) if the arithmetic is all
        that is wrong with it; None otherwise."""
        fixed = repair_targets(o, cfg) if isinstance(o, dict) else o
        if check(fixed):
            return None
        return fixed, ([s.get("target_words") for s in o["sections"]], [s["target_words"] for s in fixed["sections"]])

    if errors and (mended := mend(outline)):
        (outline, scaled), errors = mended, []
    if errors:
        print(f"  overnight outline: the edited outline breaks {len(errors)} rule(s); one repair call")
        repaired = errors
        outline = _clean(_ask(r, cfg, "overnight_outline_repair", "overnight outline repair", load_prompt(
            "overnight_outline_repair", **shared, outline=as_json(outline), problems=problems(errors)), SCHEMA,
            "outline_repair.json"), c)
        errors = check(outline)
        if errors and (mended := mend(outline)):
            (outline, scaled), errors = mended, []
    if errors and (back := (first, None) if not found else mend(first)):
        print("  overnight outline: the edited outline is still invalid after the repair call; the first stands")
        (outline, scaled), dropped, errors, changes, repaired = back, errors, [], [], []
    if errors:
        partial = r.root / "_partial_overnight_outline.json"
        base.write_json(partial, outline)
        for name in ("outline_edit.json", "outline_repair.json"):  # the next try asks the editor again
            work(r, name).unlink(missing_ok=True)
        raise StageError("overnight outline: still invalid after the repair call:\n  - " + "\n  - ".join(errors)
                         + f"\nThe outline is saved as {partial.name}.")

    led = _write_files(r, copy.deepcopy(outline), dossier)
    total = sum(s["target_words"] for s in outline["sections"])
    r.decide("overnight outline", f"{len(outline['sections'])} sections, {total} words, from the whole dossier:\n\n"
             + "\n".join(f"- {s['n']}. {s['title']} ({s['target_words']} words; {', '.join(s['rows']) or 'no rows'}): "
                         f"{s['lands']}" for s in outline["sections"])
             + f"\n\nThrough-line: {outline['through_line']}")
    r.decide("overnight outline edit",
             "The edited outline was still invalid after the repair call, so the first outline stands unedited. "
             "What was wrong with the edit:\n\n" + "\n".join(f"- {e}" for e in dropped) if dropped else
             ("The outline editor's changes:\n\n" + "\n".join(f"- {x}" for x in changes)) if changes else
             "The outline editor changed nothing.")
    if repaired:
        r.decide("overnight outline repair", "The edited outline broke these rules, and one repair call mended "
                 "them:\n\n" + "\n".join(f"- {e}" for e in repaired))
    if scaled:
        r.decide("overnight outline repair", "Word targets scaled into range by code: "
                 f"{', '.join(map(str, scaled[0]))} became {', '.join(map(str, scaled[1]))}.")
    print(f"  05_outline.json: \"{outline['title']}\", {len(outline['sections'])} sections, {total} words"
          + ("; the edit was invalid, so the first outline stands" if dropped else
             f"; the editor made {len(changes)} change(s)" if changes else "; the editor changed nothing"))
    return outline
