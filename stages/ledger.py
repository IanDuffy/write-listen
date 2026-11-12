"""Stage 03b: transcript + map + angle -> ledger.json and ledger.md

The ledger is the list of ideas the chapter must land: 5-7 active rows, ranked by
importance to the angle, plus a parked list of good ideas that did not make the
cut (one line each, never researched). ledger.json is the source of truth. Ian
edits it by hand at the checkpoint after this stage; every stage that reads or
updates it re-renders ledger.md, a table he can read.

Each column is written by one stage:

    03b ledger         id, idea, rank, type, episode_has, research_question, tier_needed
    04b ledger update  status, dossier_refs, best_tier, gap, followup; connections (top level)
    07b coverage       coverage, segments, note

Running `ledger <run>` again without --force checks Ian's edits to ledger.json
and re-renders ledger.md, without calling the model.
"""

from __future__ import annotations

import hashlib
import json
import re
import time

from . import llm
from .common import (Run, StageError, is_seed, load_prompt, parse_json, prompt_name, row_id, seed_inputs,
                     source_text)

TYPES = ("deep-dive", "missing", "covered")
STATUSES = ("filled", "partial", "open")
COVERAGES = ("covered", "thin", "missing", "repeated")
ROW_ID = re.compile(r"^L\d{2,}$")

PLAN_FIELDS = ("id", "rank", "idea", "type", "episode_has", "research_question", "tier_needed")
LATER_FIELDS = {  # written by 04b and 07b; empty until then
    "status": None, "dossier_refs": [], "best_tier": None, "gap": "", "followup": False,
    "coverage": None, "segments": [], "note": "",
}

# Structured output for stage 03b. Ids and final ranks are assigned by code.
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rows", "parked"],
    "properties": {
        "rows": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["idea", "rank", "type", "episode_has", "research_question", "tier_needed"],
            "properties": {
                "idea": {"type": "string"},
                "rank": {"type": "integer"},
                "type": {"type": "string", "enum": list(TYPES)},
                "episode_has": {"type": "string"},
                "research_question": {"type": "string"},
                "tier_needed": {"type": "integer", "enum": [1, 2, 3, 4]},
            },
        }},
        "parked": {"type": "array", "items": {"type": "string"}},
    },
}


# --- the data model ---------------------------------------------------------------

def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def next_free_id(led: dict) -> str:
    nums = [int(r["id"][1:]) for r in led.get("rows", []) if isinstance(r, dict)
            and isinstance(r.get("id"), str) and ROW_ID.match(r["id"])]
    return row_id(max(nums, default=0) + 1)


def validate(led, cfg: dict | None = None) -> list[str]:
    """Check a ledger (as Ian may have edited it). Raises StageError listing every
    problem; returns warnings that should not stop a stage."""
    if not isinstance(led, dict) or not isinstance(led.get("rows"), list):
        raise StageError("ledger.json must be a JSON object with a \"rows\" list.")
    errors, warnings = [], []
    seen_ids: set[str] = set()
    ranks: dict[int, str] = {}
    for i, row in enumerate(led["rows"], 1):
        if not isinstance(row, dict):
            errors.append(f"row {i} is not an object")
            continue
        rid = row.get("id")
        if not isinstance(rid, str) or not ROW_ID.match(rid):
            errors.append(f"row {i}: id {rid!r} should look like L01 (the next free id is {next_free_id(led)})")
            continue
        if rid in seen_ids:
            errors.append(f"{rid} appears twice")
        seen_ids.add(rid)
        for f in ("idea", "research_question"):
            if not isinstance(row.get(f), str) or not row[f].strip():
                errors.append(f"{rid}: {f} must be a non-empty string")
        if not isinstance(row.get("episode_has", ""), str):
            errors.append(f"{rid}: episode_has must be a string")
        rank = row.get("rank")
        if not _int(rank) or rank < 1:
            errors.append(f"{rid}: rank must be a whole number from 1")
        elif rank in ranks:
            errors.append(f"{rid} and {ranks[rank]} are both ranked {rank}")
        else:
            ranks[rank] = rid
        if row.get("type") not in TYPES:
            errors.append(f"{rid}: type must be one of {', '.join(TYPES)}")
        if row.get("tier_needed") not in (1, 2, 3, 4) or not _int(row.get("tier_needed")):
            errors.append(f"{rid}: tier_needed must be 1, 2, 3 or 4")
        if row.get("status") not in (None, *STATUSES):
            errors.append(f"{rid}: status must be one of {', '.join(STATUSES)}, or null")
        if row.get("coverage") not in (None, *COVERAGES):
            errors.append(f"{rid}: coverage must be one of {', '.join(COVERAGES)}, or null")
        if row.get("best_tier") not in (None, 1, 2, 3, 4):
            errors.append(f"{rid}: best_tier must be 1, 2, 3, 4 or null")
        refs = row.get("dossier_refs", [])
        if not isinstance(refs, list) or not all(isinstance(x, str) and re.fullmatch(r"D\d+", x) for x in refs):
            errors.append(f"{rid}: dossier_refs must be a list like [\"D04\", \"D07\"]")
        segs = row.get("segments", [])
        if not isinstance(segs, list) or not all(_int(x) for x in segs):
            errors.append(f"{rid}: segments must be a list of segment numbers")
        unknown = set(row) - set(PLAN_FIELDS) - set(LATER_FIELDS)
        if unknown:
            warnings.append(f"{rid} has fields the pipeline does not use: {', '.join(sorted(unknown))}")
    conns = led.get("connections", [])
    if not isinstance(conns, list) or not all(
            isinstance(c, dict) and isinstance(c.get("link", ""), str) and isinstance(c.get("rows", []), list)
            and all(isinstance(x, str) for x in c.get("rows", [])) for c in conns):
        errors.append("connections must be a list of {\"rows\": [\"L01\", \"L03\"], \"link\": \"...\"} objects")
    parked = led.get("parked", [])
    if not isinstance(parked, list) or not all(
            isinstance(p, str) or (isinstance(p, dict) and isinstance(p.get("idea"), str)) for p in parked):
        errors.append("parked must be a list of one-line strings (or rows with an \"idea\")")
    if errors:
        raise StageError("ledger.json has problems:\n  - " + "\n  - ".join(errors))
    if cfg is not None:
        lo, hi = cfg["research"]["min_rows"], cfg["research"]["max_rows"]
        if not lo <= len(led["rows"]) <= hi:
            warnings.append(f"{len(led['rows'])} active rows; the add-on asks for {lo}-{hi} "
                            "(depth beats breadth). Park rows rather than adding more.")
    return warnings


def normalize(led: dict) -> dict:
    """Fill in the later stages' columns, put each row's fields in a fixed order, and close any gaps
    in the ranks (1, 3, 4 -> 1, 2, 3), since budgets go by rank."""
    for i, row in enumerate(sorted(led["rows"], key=lambda x: x["rank"]), 1):
        row["rank"] = i
    rows = []
    for row in led["rows"]:
        out = {f: row[f] for f in PLAN_FIELDS if f in row}
        out.setdefault("episode_has", "")
        for f, default in LATER_FIELDS.items():
            out[f] = row.get(f, default if not isinstance(default, list) else [])
        out.update({k: v for k, v in row.items() if k not in out})  # Ian's extra fields are kept
        rows.append(out)
    led["rows"] = rows
    led.setdefault("parked", [])
    led.setdefault("connections", [])
    return led


def active(led: dict) -> list[dict]:
    return sorted(led["rows"], key=lambda r: r["rank"])


def by_id(led: dict) -> dict[str, dict]:
    return {r["id"]: r for r in led["rows"]}


def load(r: Run, cfg: dict | None = None, render: bool = True) -> dict:
    """Read, check and normalize ledger.json; re-render ledger.md so it matches."""
    p = r.need("ledger")
    try:
        led = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise StageError(f"ledger.json is not valid JSON: {e.msg} at line {e.lineno}, column {e.colno}.") from None
    for w in validate(led, cfg):
        print(f"  ledger: {w}")
    ranks = [x["rank"] for x in sorted(led["rows"], key=lambda x: x["rank"])]
    if ranks != list(range(1, len(ranks) + 1)):
        print(f"  ledger: ranks {ranks} read as 1-{len(ranks)} in that order (budgets go by rank)")
    normalize(led)
    if render:
        r.path("ledger_md").write_text(render_md(led, r.name), encoding="utf-8")
    return led


def save(r: Run, led: dict) -> None:
    normalize(led)
    validate(led)
    r.path("ledger").write_text(json.dumps(led, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    r.path("ledger_md").write_text(render_md(led, r.name), encoding="utf-8")


def fingerprint(row: dict) -> str:
    """What a row's research depends on. Rank is left out: re-ranking changes the
    budget of the next run, not what earlier findings answer."""
    text = "\0".join(" ".join(str(row.get(f, "")).split()) for f in ("idea", "type", "research_question", "tier_needed"))
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def inputs_hash(transcript: str, episode_map: str, angle: str) -> str:
    return hashlib.sha256("\0".join((transcript, episode_map, angle)).encode()).hexdigest()[:12]


def budget(cfg: dict, row: dict, followup: bool = False) -> tuple[int, int]:
    """(searches, fetches) for the agent researching this row."""
    rc = cfg["research"]
    if followup:
        key = "followup"
    elif row["type"] == "covered":
        key = "covered"
    elif row["rank"] <= rc["top_ranks"]:
        key = "top"
    else:
        key = "rest"
    b = rc["budgets"][key]
    return b["searches"], b["fetches"]


def budgets_text(cfg: dict) -> str:
    b, top = cfg["research"]["budgets"], cfg["research"]["top_ranks"]
    return (f"rows ranked 1-{top}: {b['top']['searches']} searches and {b['top']['fetches']} page reads; "
            f"lower-ranked rows: {b['rest']['searches']} and {b['rest']['fetches']}; "
            f"covered rows (fact-check only): {b['covered']['searches']} and {b['covered']['fetches']}")


# --- rendering ------------------------------------------------------------------------

def cell(v) -> str:
    if v is None or v == "" or v == []:
        return "-"
    if isinstance(v, list):
        v = ", ".join(str(x) for x in v)
    return " ".join(str(v).split()).replace("|", "\\|")


def _parked_line(p) -> str:
    return p if isinstance(p, str) else p["idea"]


def render_md(led: dict, name: str) -> str:
    rows = active(led)
    out = [f"# Ledger: {name}", "",
           "Rendered from ledger.json, which is the file to edit. "
           f"{len(rows)} active rows, ranked by importance to the angle; {len(led.get('parked', []))} parked.", ""]
    if led.get("connections"):
        out += ["## Connections", ""]
        out += [f"- {' and '.join(c.get('rows', []))}: {c.get('link', '')}" for c in led["connections"]]
        out.append("")
    out += ["## Rows", "",
            "| Row | Rank | Type | Idea | Tier needed | Status | Best tier | Dossier | Coverage | Segments |",
            "| --- | ---: | --- | --- | ---: | --- | ---: | --- | --- | --- |"]
    out += [f"| {r['id']} | {r['rank']} | {r['type']} | {cell(r['idea'])} | {r['tier_needed']} | "
            f"{cell(r['status'])} | {cell(r['best_tier'])} | {cell(r['dossier_refs'])} | "
            f"{cell(r['coverage'])} | {cell(r['segments'])} |" for r in rows]
    out.append("")
    still_open = [r for r in rows if r["status"] in ("partial", "open")]
    if still_open:
        out += ["## Still open (for Ian)", ""]
        out += [f"- {r['id']} ({r['status']}{', after the follow-up round' if r['followup'] else ''}): "
                f"{cell(r['gap']) if r['gap'] else 'no gap recorded'}" for r in still_open]
        out.append("")
    noted = [r for r in rows if r["note"]]
    if noted:
        out += ["## Coverage notes (for Ian and the editor)", ""]
        out += [f"- {r['id']} ({r['coverage'] or 'not checked'}"
                f"{', segments ' + cell(r['segments']) if r['segments'] else ''}): {cell(r['note'])}" for r in noted]
        out.append("")
    out += ["## Details", ""]
    for r in rows:
        out += [f"### {r['id']}: {cell(r['idea'])}", "",
                f"- Rank {r['rank']}, {r['type']}; needs tier {r['tier_needed']} or better.",
                f"- {has_label(led)}: {cell(r['episode_has'])}",
                f"- {'Claims to verify' if r['type'] == 'covered' else 'Research question'}: "
                f"{cell(r['research_question'])}"]
        if r["status"]:
            out.append(f"- Status: {r['status']}" + (f". Gap: {cell(r['gap'])}" if r["gap"] else "")
                       + (" (follow-up done)" if r["followup"] else ""))
        if r["coverage"]:
            out.append(f"- Coverage: {r['coverage']}" + (f", segments {cell(r['segments'])}" if r["segments"] else "")
                       + (f". Note: {cell(r['note'])}" if r["note"] else ""))
        out.append("")
    out += ["## Parked", ""]
    out += [f"- {cell(_parked_line(p))}" for p in led.get("parked", [])] or ["(none)"]
    return "\n".join(out) + "\n"


def has_label(led: dict | None) -> str:
    """What episode_has means in this ledger: what the episode says, or (seed runs) what the scout found."""
    return "Scout has" if led and led.get("source") == "seed" else "Episode has"


def describe(rows: list[dict], research: bool = False, coverage: bool = False, led: dict | None = None) -> str:
    """Rows as plain text for a prompt. `research` adds the 04b columns; `coverage` the 07b ones."""
    parts = []
    for r in rows:
        lines = [f"### {r['id']} (rank {r['rank']}, {r['type']}, needs tier {r['tier_needed']} or better)",
                 f"Idea: {r['idea']}",
                 f"{has_label(led)}: {r['episode_has'] or 'Nothing.'}",
                 f"{'Claims to verify' if r['type'] == 'covered' else 'Research question'}: {r['research_question']}"]
        if research and r["status"]:
            lines.append(f"Research status: {r['status']}; best tier found: {r['best_tier'] or 'none'}; "
                         f"dossier entries: {', '.join(r['dossier_refs']) or 'none'}"
                         + (f"; still missing: {r['gap']}" if r["gap"] else ""))
        if coverage and r["coverage"]:
            lines.append(f"Coverage: {r['coverage']}, segments {', '.join(map(str, r['segments'])) or 'none'}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def connections_text(led: dict) -> str:
    return "\n".join(f"- {' and '.join(c.get('rows', []))}: {c.get('link', '')}"
                     for c in led.get("connections", [])) or "(none recorded)"


# --- the stage --------------------------------------------------------------------------

def summary(led: dict) -> str:
    return "\n".join(f"    {r['id']}  rank {r['rank']}  {r['type']:9}  tier {r['tier_needed']}  {r['idea']}"
                     for r in active(led))


def next_research(r: Run, led: dict) -> str:
    """The research commands for a fresh ledger: the rank-1 row's agent first, for Ian to read."""
    first = active(led)[0]["id"] if led["rows"] else "L01"
    return (f"python cli.py research {r.name} --plan-only, then python cli.py research {r.name} "
            f"--rows {first} (the rank-1 row first).")


def run(r: Run, cfg: dict):
    if r.path("ledger").exists() and not r.force:
        led = load(r, cfg)
        print(f"  ledger.json checked, ledger.md re-rendered:\n{summary(led)}\n"
              "  (Use --force to write a new ledger from scratch; edits in ledger.json would be lost.)\n"
              f"  Next: {next_research(r, led)}")
        return r.path("ledger")
    source, episode_map, angle = source_text(r), r.read("map"), r.read("angle")
    r.check_writable("ledger")
    rc = cfg["research"]
    prompt = load_prompt(prompt_name(r, "ledger"), transcript=source, map=episode_map, angle=angle,
                         min_rows=rc["min_rows"], max_rows=rc["max_rows"],
                         target_minutes=cfg["chapter"]["target_minutes"], **seed_inputs(r, cfg),
                         budgets=budgets_text(cfg), source_tiers=load_prompt(prompt_name(r, "source_tiers")))
    res = llm.call(r, cfg, "ledger", messages=[{"role": "user", "content": prompt}], json_schema=SCHEMA)
    data = parse_json(r, res.text, "ledger")
    rows = sorted(data.get("rows") or [], key=lambda x: x.get("rank", 99) if _int(x.get("rank")) else 99)
    if not rows:
        raise StageError("ledger: the model returned no rows.")
    extra = []
    if is_seed(r) and len(rows) > cfg["research"]["max_rows"]:  # a lesson lands 3-4 ideas: park the rest
        rows, extra = rows[:cfg["research"]["max_rows"]], rows[cfg["research"]["max_rows"]:]
        print(f"  parked {len(extra)} row(s) past the lesson's {cfg['research']['max_rows']}")
    for i, row in enumerate(rows, 1):  # ids follow rank; ranks become 1..N without gaps or ties
        row["rank"] = i
        row["id"] = row_id(i)
    led = {"run": r.name, "source": "seed" if is_seed(r) else "episode",
           "inputs": inputs_hash(source, episode_map, angle),
           "rows": rows, "parked": [x["idea"] for x in extra] + (data.get("parked") or []), "connections": []}
    try:
        warnings = validate(led, cfg)
    except StageError as e:  # keep the paid output: fix it by hand and save it as ledger.json
        partial = r.root / "_partial_ledger.json"
        partial.write_text(json.dumps(led, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise StageError(f"{e}\nThe model's ledger is saved as {partial.name}: fix those rows and save it as "
                         "ledger.json, then run ledger again to check it.") from None
    for w in warnings:
        print(f"  ledger: {w}")
    research_dir = r.path("research_dir")
    if research_dir.exists() and any(research_dir.glob("L*.md")):
        # Ids follow rank, so the old L01.md would pass as findings for the new L01.
        old = r.root / f"04_research.old-{time.strftime('%Y%m%d-%H%M%S')}"
        research_dir.rename(old)
        print(f"  the old research answered the old ledger: moved 04_research/ to {old.name}/")
    save(r, led)
    print(f"  {len(rows)} active rows, {len(led['parked'])} parked:\n{summary(led)}")
    r.decide("ledger", "Approved the ledger without review:\n\n" + "\n".join(
        f"- {x['id']} ({x['type']}, tier {x['tier_needed']}): {x['idea']}" for x in active(led))
        + ("\n\nParked: " + "; ".join(_parked_line(p) for p in led["parked"]) if led["parked"] else ""))
    print(f"\n  Next: {next_research(r, led)}\n  (Optional: edit ledger.json first; "
          f"`python cli.py ledger {r.name}` re-checks it and re-renders ledger.md.)")
    return r.path("ledger")
