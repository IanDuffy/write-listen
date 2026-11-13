"""Fast Start step 2: seed + scout -> 05_outline.json, 05_outline.md, 03_angle.md, ledger.json, 05_brief.md

In a fast run the arc is decided here, before the research, because once the opening has
played nothing can reorder the chapter (docs/fast-start.md). One call writes the outline: the
angle, the through-line, the closing idea, and three to five sections, each with what it lands,
what it sets up, how it hands off, its length and its research questions. A second call, in a
fresh context, critiques the arc and returns a revised outline with a list of what it changed.
One round only.

Code then holds the outline to the rules a fast run depends on (`validate`, acceptance check 1):
the opening long enough for section 2's research to arrive and citing scout entries only, no
section too short to be worth a marker, research questions within the run's search budget. Word
targets are the one thing code can mend safely, so an edited outline wrong only in its arithmetic
is scaled into range without another call. One that breaks any other rule gets one repair call
that is told the errors (and is scaled afterwards if only its word targets are still off). If
that fails too, the first outline stands unedited when it is sound; only when it is not does the
run stop.

The outline's JSON is the source of truth. From it, code writes the files the rest of the
pipeline already knows: 03_angle.md ("# Angle: <title>"), ledger.json (one row per research
question, in the existing row format, each tagged with its section) and 05_brief.md (the
outline in the brief's shape, so the episode page and ask-by-voice read a fast run unchanged:
ask/aside.py takes each segment's title, its bold Purpose line and its Dossier line from it).
"""

from __future__ import annotations

import copy
import json
import math

from stages import ledger, llm
from stages.common import Run, StageError, entry_id, load_prompt, parse_json, prompt_name, row_id, seed_inputs

from . import base, notes, scout

TITLE_WORDS = 6

_QUESTION = {
    "type": "object",
    "additionalProperties": False,
    "required": ["idea", "question", "type", "tier_needed", "scout_has"],
    "properties": {
        "idea": {"type": "string"},
        "question": {"type": "string"},
        "type": {"type": "string", "enum": list(ledger.TYPES)},
        "tier_needed": {"type": "integer", "enum": [1, 2, 3, 4]},
        "scout_has": {"type": "string"},
    },
}
_SECTION = {
    "type": "object",
    "additionalProperties": False,
    "required": ["n", "title", "lands", "sets_up", "hand_off", "target_words", "notes", "scout_entries",
                 "research_questions"],
    "properties": {
        "n": {"type": "integer"},
        "title": {"type": "string"},
        "lands": {"type": "string"},
        "sets_up": {"type": "string"},
        "hand_off": {"type": "string"},
        "target_words": {"type": "integer"},
        "notes": {"type": "string"},
        "scout_entries": {"type": "array", "items": {"type": "integer"}},
        "research_questions": {"type": "array", "items": _QUESTION},
    },
}
# Structured output for the outline call and the repair call.
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "angle", "why", "through_line", "closing_idea", "listener_words", "sections"],
    "properties": {
        "title": {"type": "string"},
        "angle": {"type": "string"},
        "why": {"type": "string"},
        "through_line": {"type": "string"},
        "closing_idea": {"type": "string"},
        "listener_words": {"type": "array", "items": {"type": "string"}},
        "sections": {"type": "array", "items": _SECTION},
    },
}
# The outline editor's: what it changed, then the outline as it now stands.
EDIT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["changes", "outline"],
    "properties": {"changes": {"type": "array", "items": {"type": "string"}}, "outline": SCHEMA},
}


# --- sizes ------------------------------------------------------------------------------

def limits(cfg: dict) -> dict:
    """config.yaml's fast sizes and research budget, in words: what the validator enforces and
    what the three outline prompts are told."""
    f = base.settings(cfg)
    wpm = f["words_per_minute"]
    words = lambda minutes: round(minutes * wpm) if float(minutes * wpm).is_integer() else minutes * wpm
    per = f["research"]["per_row"]
    return {"min_sections": f["sections"][0], "max_sections": f["sections"][1], "words_per_minute": wpm,
            "target_minutes": f["target_minutes"], "target_words": words(f["target_minutes"]),
            "min_words": words(f["total_minutes"][0]), "max_words": words(f["total_minutes"][1]),
            "opening_min_minutes": f["opening_min_minutes"], "opening_min_words": words(f["opening_min_minutes"]),
            "section_min_minutes": f["section_min_minutes"], "section_min_words": words(f["section_min_minutes"]),
            "max_questions": f["research"]["max_searches"] // per["searches"],
            "row_searches": per["searches"], "row_fetches": per["fetches"]}


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _said(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _mins(words, lim: dict) -> str:
    return f"{words / lim['words_per_minute']:.2f}".rstrip("0").rstrip(".")


# --- the validator (acceptance check 1) -------------------------------------------------

def validate(outline: dict, scout_numbers: set[int], cfg: dict) -> list[str]:
    """Every rule the outline breaks, in plain words (the repair call reads them); [] when valid.

    Three to five sections, numbered 1..N in order, each with a short title and its lands,
    sets_up and hand_off notes. The opening (section 1) at least fast.opening_min_minutes, no
    section under fast.section_min_minutes, the total inside fast.total_minutes. The opening has
    no research questions and cites at least one scout entry; every entry any section lists is a
    scout entry, the only entries there are when the outline is written. Sections 2 onward have
    one or two research questions each, and the chapter has no more than the run's search budget
    can serve (fast.research.max_searches // fast.research.per_row.searches)."""
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
    numbers = [s.get("n") for s in sections]
    if numbers != list(range(1, len(sections) + 1)):
        errors.append(f"The sections must be numbered 1 to {len(sections)} in order; they are numbered {numbers}.")
    total, sized, questions = 0, True, 0
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
        cited = s.get("scout_entries")
        if not isinstance(cited, list) or not all(_int(x) for x in cited):
            errors.append(f"{name}'s scout_entries must be a list of entry numbers.")
            cited = []
        unknown = sorted({x for x in cited if x not in scout_numbers})
        if unknown:
            errors.append(f"{name} lists {', '.join(entry_id(x) for x in unknown)}, which "
                          f"{'is not a scout entry' if len(unknown) == 1 else 'are not scout entries'}"
                          + ("; the opening may cite only scout entries." if i == 1 else
                             "; no other entries exist until the research is back."))
        asked = s.get("research_questions")
        if not isinstance(asked, list) or not all(isinstance(q, dict) for q in asked):
            errors.append(f"{name}'s research_questions must be a list.")
            asked = []
        questions += len(asked)
        if i == 1:
            if asked:
                errors.append(f"{name} has {len(asked)} research question(s); it is written from the scout alone "
                              "and may have none.")
            if not cited:
                errors.append(f"{name} cites no scout entry; every fact in it must come from one.")
        elif not 1 <= len(asked) <= 2:
            errors.append(f"{name} has {len(asked)} research questions; sections after the opening need 1 or 2.")
        for k, q in enumerate(asked, 1):
            for field in ("idea", "question"):
                if not _said(q.get(field)):
                    errors.append(f"{name}, research question {k}: {field} is empty.")
            if q.get("type") not in ledger.TYPES:
                errors.append(f"{name}, research question {k}: type must be one of {', '.join(ledger.TYPES)}.")
            if not _int(q.get("tier_needed")) or q["tier_needed"] not in (1, 2, 3, 4):
                errors.append(f"{name}, research question {k}: tier_needed must be 1, 2, 3 or 4.")
    if sized and sections and not lim["min_words"] <= total <= lim["max_words"]:
        errors.append(f"The sections add up to {total} words, {_mins(total, lim)} minutes; the chapter must be "
                      f"between {lim['min_words']} and {lim['max_words']} words "
                      f"({_mins(lim['min_words'], lim)} to {_mins(lim['max_words'], lim)} minutes).")
    if questions > lim["max_questions"]:
        errors.append(f"The outline has {questions} research questions; the search budget serves at most "
                      f"{lim['max_questions']}.")
    return errors


def repair_targets(outline: dict, cfg: dict) -> dict:
    """A copy of the outline with its word targets scaled into range: the total inside
    fast.total_minutes (an outline outside it is brought to fast.target_minutes), the opening and
    every other section at or above its floor, and the sections otherwise in the proportions the
    outline gave them. Whole tens of words. Nothing else is touched."""
    out = copy.deepcopy(outline)
    sections = [s for s in out.get("sections") or [] if isinstance(s, dict)]
    if not sections:
        return out
    lim = limits(cfg)
    floors = [float(lim["opening_min_words"])] + [float(lim["section_min_words"])] * (len(sections) - 1)
    given = [float(s["target_words"]) if _int(s.get("target_words")) and s["target_words"] > 0 else 0.0
             for s in sections]
    lo, hi = lim["min_words"], lim["max_words"]
    goal = sum(given) if lo <= sum(given) <= hi else float(lim["target_words"])
    goal = max(goal, sum(floors))  # more sections than the range has room for: the floors win
    t = [g * goal / sum(given) for g in given] if sum(given) else [goal * f / sum(floors) for f in floors]
    for _ in sections:  # lift what is under its floor, taking the words from the others' room above theirs
        short = [i for i, x in enumerate(t) if x < floors[i]]
        if not short:
            break
        need = sum(floors[i] - t[i] for i in short)
        for i in short:
            t[i] = floors[i]
        room = {i: x - floors[i] for i, x in enumerate(t) if x > floors[i]}
        for i, slack in room.items():
            t[i] -= need * slack / sum(room.values())
    tens = [max(int((x + 1e-6) // 10) * 10, math.ceil(floors[i])) for i, x in enumerate(t)]
    if not lo <= sum(tens) <= hi:  # rounding, or a floor, took the total past an edge: the roomiest section gives
        widest = max(range(len(tens)), key=lambda i: tens[i] - floors[i])
        tens[widest] += min(max(sum(tens), math.ceil(lo)), math.floor(hi)) - sum(tens)
    for s, words in zip(sections, tens):
        s["target_words"] = words
    return out


# --- reading an outline -----------------------------------------------------------------

def load(r: Run) -> dict:
    """05_outline.json."""
    return json.loads(r.read(base.path(r, "outline_json")))


def section(outline: dict, n: int) -> dict:
    for s in outline["sections"]:
        if s["n"] == n:
            return s
    raise StageError(f"The outline has no section {n}.")


def rows_for(led: dict, n: int) -> list[dict]:
    """The ledger rows tagged with section n, in rank order."""
    rows = sorted(led.get("rows") or [], key=lambda row: row.get("rank") or 0)
    return [row for row in rows if row.get("section") == n]


# --- the files written from it ----------------------------------------------------------

def _t(d: dict, key: str) -> str:
    """A text field of the outline, or "" if the model left it out."""
    return str(d.get(key) or "").strip()


def _ids(numbers) -> str:
    return ", ".join(entry_id(k) for k in sorted(set(numbers)))


def render_md(outline: dict) -> str:
    """05_outline.md: the outline for a person to read."""
    total = sum(s["target_words"] for s in outline["sections"])
    out = [f"# Outline: {_t(outline, 'title')}", "",
           "Rendered from 05_outline.json. Written before the research, then revised once by a second editor; "
           "nothing after it reorders the chapter.", "",
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
                f"- How to tell it: {_t(s, 'notes') or '(no notes)'}"]
        if s.get("scout_entries"):
            out.append(f"- Scout entries: {_ids(s['scout_entries'])}")
        for q in s.get("research_questions") or []:
            out.append(f"- Research ({q['type']}, tier {q['tier_needed']}): {_t(q, 'idea')} "
                       f"Question: {_t(q, 'question')}")
        out.append("")
    return "\n".join(out)


def render_angle(outline: dict) -> str:
    """03_angle.md: the title line every later reader looks for, then the angle and why."""
    return "\n".join([f"# Angle: {_t(outline, 'title')}", "", _t(outline, "angle"), "",
                      "## Why this one", "", _t(outline, "why") or "(not said)", "",
                      "## Through-line", "", _t(outline, "through_line"), "",
                      "## Closing idea", "", _t(outline, "closing_idea"), ""])


def render_brief(outline: dict, led: dict, cited: dict[int, list[int]] | None = None,
                 titles: dict[int, str] | None = None) -> str:
    """The outline in the brief's shape, so episode/ and ask/ read a fast run unchanged:
    `# Brief: <title>`, `## Through-line`, and `## Segment N: Title` sections each with
    `Target words:`, `Ledger:` and `Dossier:` lines (brief.parse_segments reads them) and a bold
    `Purpose:` line, what the section lands (ask/aside.py tells the guide the outline from it).

    `cited` gives a section's D numbers once known: the archive step re-renders with what each
    locked section actually cites. A section it does not name (every section, before the research)
    lists the scout entries the outline planned for it, the only entries of its own there are yet.
    `titles` gives the titles changed when a section locked (section number -> title); the outline
    itself is never rewritten."""
    out = [f"# Brief: {_t(outline, 'title')}", "",
           "## Through-line", "", _t(outline, "through_line"), "",
           "## Thesis", "", _t(outline, "angle"), "",
           "## Closing idea", "", _t(outline, "closing_idea"), ""]
    for s in outline["sections"]:
        n = s["n"]
        entries = cited[n] if cited and n in cited else s.get("scout_entries") or []
        rows = [row["id"] for row in rows_for(led, n)]
        title = str((titles or {}).get(n) or "").strip() or _t(s, "title")
        # The Dossier line is followed by a labelled line, so parse_segments never reads on past an empty one.
        out += [f"## Segment {n}: {title}",
                f"- Target words: {s['target_words']}",
                f"- Ledger: {', '.join(rows) or 'none'}",
                f"- Dossier: {_ids(entries) or 'none yet'}",
                f"- **Purpose:** {_t(s, 'lands')}",
                f"- Sets up: {_t(s, 'sets_up')}",
                f"- Hands off to: {_t(s, 'hand_off')}",
                f"- Notes: {_t(s, 'notes') or '(none)'}", ""]
    return "\n".join(out)


def ledger_of(r: Run, outline: dict, scout_text: str) -> dict:
    """The ledger a fast run researches from: one row per research question, in the existing row
    format, each tagged with its section. Ids and ranks follow the order of the sections, so
    section 2's rows are researched first. episode_has holds what the scout already has (a seed
    run's ledger labels it "Scout has")."""
    rows = []
    for s in outline["sections"]:
        for q in s.get("research_questions") or []:
            k = len(rows) + 1
            rows.append({"id": row_id(k), "rank": k, "idea": _t(q, "idea"), "type": q["type"],
                         "episode_has": _t(q, "scout_has"), "research_question": _t(q, "question"),
                         "tier_needed": q["tier_needed"], "section": s["n"]})
    return {"run": r.name, "source": "seed",
            "inputs": ledger.inputs_hash(r.read("seed"), scout_text, render_angle(outline)),
            "rows": rows, "parked": [], "connections": []}


def _write_files(r: Run, outline: dict, scout_text: str) -> dict:
    """Write whichever of the outline's files are not there yet, and return the ledger. Never
    rewrites one: by the time a run is resumed, ledger.json holds what research and the locks
    have added to it."""
    if not base.path(r, "outline_json").exists():
        base.write_json(base.path(r, "outline_json"), outline)
    if not base.path(r, "outline").exists():
        base.write_atomic(base.path(r, "outline"), render_md(outline))
    if not r.path("angle").exists():
        base.write_atomic(r.path("angle"), render_angle(outline))
    with base.LEDGER_LOCK:
        if not r.path("ledger").exists():
            ledger.save(r, ledger_of(r, outline, scout_text))
        led = ledger.normalize(json.loads(r.path("ledger").read_text(encoding="utf-8")))
    if not r.path("brief").exists():
        base.write_atomic(r.path("brief"), render_brief(outline, led))
    return led


# --- the stage --------------------------------------------------------------------------

def _clean(outline: dict) -> dict:
    """The model's outline with stray whitespace gone. Code adds nothing the model did not write."""
    def tidy(v):
        if isinstance(v, str):
            return " ".join(v.split())
        if isinstance(v, list):
            return [tidy(x) for x in v]
        if isinstance(v, dict):
            return {k: tidy(x) for k, x in v.items()}
        return v

    out = tidy(outline)
    for s in out.get("sections") or []:
        cited = s.get("scout_entries") if isinstance(s, dict) else None
        if isinstance(cited, list) and all(_int(x) for x in cited):
            s["scout_entries"] = sorted(set(cited))
    return out


def _ask(r: Run, cfg: dict, stage: str, label: str, prompt: str, schema: dict, saved: str) -> dict:
    """One structured call, its answer kept in fast/<saved> so a run stopped after it does not pay again."""
    p = base.path(r, "work") / saved
    data = base.read_json(p)
    if data is None:
        st = cfg["stages"].get(stage) or cfg["stages"]["fast_outline_edit"]  # the repair call borrows the edit's
        res = llm.call(r, cfg, stage, label=label, messages=[{"role": "user", "content": prompt}],
                       json_schema=schema, preapproved=True, max_tokens=st.get("max_tokens"), effort=st.get("effort"),
                       expected_output_tokens=st.get("expected_output_tokens"))
        data = parse_json(r, res.text, stage)
        base.write_json(p, data)
    return data


def run(r: Run, cfg: dict, dossier) -> dict:
    """Write, edit and check the outline, and the files made from it. Returns the outline. If
    05_outline.json is there, it is returned without a paid call (and any of its files that are
    missing are written)."""
    scout_text = scout.text(r)
    if base.path(r, "outline_json").exists():
        outline = load(r)
        _write_files(r, outline, scout_text)
        return outline
    scout_numbers = set(dossier.numbers_of(base.SCOUT_ROW))
    if not scout_numbers:
        raise StageError("fast outline: the dossier has no scout entries; run the scout first.")
    lim = limits(cfg)
    shared = {**seed_inputs(r, cfg), **lim, "notes": notes.as_prompt(notes.load(r.root)), "scout": scout_text.strip(),
              "scout_numbers": _ids(scout_numbers),
              "source_tiers": load_prompt(prompt_name(r, "source_tiers")).strip()}
    as_json = lambda o: json.dumps(o, ensure_ascii=False, indent=1)
    problems = lambda errs: "\n".join(f"- {e}" for e in errs) or "None."

    first = _clean(_ask(r, cfg, "fast_outline", "fast outline", load_prompt("fast_outline", **shared), SCHEMA,
                        "outline_first.json"))
    # The editor starts from the outline alone: a new conversation, with none of the first call's reasoning.
    found = validate(first, scout_numbers, cfg)
    edit = _ask(r, cfg, "fast_outline_edit", "fast outline edit", load_prompt(
        "fast_outline_edit", **shared, outline=as_json(first), problems=problems(found)), EDIT_SCHEMA,
        "outline_edit.json")
    outline, changes = edit.get("outline"), [c.strip() for c in edit.get("changes") or [] if _said(c)]
    work = base.path(r, "work")
    if not isinstance(outline, dict):
        (work / "outline_edit.json").unlink(missing_ok=True)  # the next try asks again
        raise StageError("fast outline edit: the reply has no outline.")
    outline = _clean(outline)
    errors, repaired, scaled, dropped = validate(outline, scout_numbers, cfg), [], None, []

    def mend(o):
        """(o with its word targets scaled into range, the numbers before and after) if the
        arithmetic is all that is wrong with it; None if it breaks some other rule as well."""
        fixed = repair_targets(o, cfg) if isinstance(o, dict) else o
        if validate(fixed, scout_numbers, cfg):
            return None
        return fixed, ([s.get("target_words") for s in o["sections"]], [s["target_words"] for s in fixed["sections"]])

    if errors and (mended := mend(outline)):  # the one thing code can mend safely: no call, no wait
        (outline, scaled), errors = mended, []
    if errors:
        print(f"  fast outline: the edited outline breaks {len(errors)} rule(s); one repair call")
        repaired = errors
        outline = _clean(_ask(r, cfg, "fast_outline_repair", "fast outline repair", load_prompt(
            "fast_outline_repair", **shared, outline=as_json(outline), problems=problems(errors)), SCHEMA,
            "outline_repair.json"))
        errors = validate(outline, scout_numbers, cfg)
        if errors and (mended := mend(outline)):
            (outline, scaled), errors = mended, []
    if errors and (back := (first, None) if not found else mend(first)):
        # The edit and its repair both failed, but the first outline is sound (or needs only its
        # arithmetic done): a chapter from it, unedited, beats a problem state for a waiting listener.
        print("  fast outline: the edited outline is still invalid after the repair call; the first outline stands")
        (outline, scaled), dropped, errors, changes, repaired = back, errors, [], [], []
    if errors:
        partial = base.write_json(r.root / "_partial_fast_outline.json", outline)
        for name in ("outline_edit.json", "outline_repair.json"):  # the next try asks the editor again,
            (work / name).unlink(missing_ok=True)                  # rather than replaying this answer
        raise StageError("fast outline: still invalid after the repair call:\n  - " + "\n  - ".join(errors)
                         + f"\nThe outline is saved as {partial.name}.")

    led = _write_files(r, outline, scout_text)
    r.decide("angle", f"Chose \"{outline['title']}\".\n\n{outline['angle']}\n\nWhy: {_t(outline, 'why') or '(not said)'}"
                      f"\n\nThrough-line: {outline['through_line']}")
    r.decide("outline", f"{len(outline['sections'])} sections, "
                        f"{sum(s['target_words'] for s in outline['sections'])} words:\n\n" + "\n".join(
                            f"- {s['n']}. {s['title']} ({s['target_words']} words): {s['lands']}"
                            for s in outline["sections"]))
    r.decide("outline edit",
             "The edited outline was still invalid after the repair call, so the first outline stands unedited. "
             "What was wrong with the edit:\n\n" + "\n".join(f"- {e}" for e in dropped) if dropped else
             ("The outline editor's changes:\n\n" + "\n".join(f"- {c}" for c in changes)) if changes else
             "The outline editor changed nothing.")
    if repaired:
        r.decide("outline repair", "The edited outline broke these rules, and one repair call mended them:\n\n"
                 + "\n".join(f"- {e}" for e in repaired))
    if scaled:
        r.decide("outline repair", "Word targets scaled into range by code: "
                 f"{', '.join(map(str, scaled[0]))} became {', '.join(map(str, scaled[1]))}.")
    r.decide("ledger", "Research questions from the outline, one agent each:\n\n" + "\n".join(
        f"- {row['id']} (section {row['section']}, {row['type']}, tier {row['tier_needed']}): {row['idea']}"
        for row in ledger.active(led)))
    print(f"  05_outline.json: \"{outline['title']}\", {len(outline['sections'])} sections, "
          f"{sum(s['target_words'] for s in outline['sections'])} words, {len(led['rows'])} research rows"
          + ("; the edit was invalid, so the first outline stands" if dropped else
             f"; the editor made {len(changes)} change(s)" if changes else "; the editor changed nothing"))
    return outline
