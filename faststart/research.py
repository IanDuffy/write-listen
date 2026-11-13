"""Step 4 of a fast run: one research agent per ledger row, in the background, while the
opening is written and played (docs/fast-start.md).

The agent itself is the overnight one (stages/research.py run_agent: the same paused-loop
write-up, the same reply format, the same files in 04_research/). What differs:

- Budgets. 5 searches and 2 fetches a row, and 30 searches for all the outline's rows together.
  The overnight cap is a sum checked once before the agents start; here agents start at different
  times, so the 30 is a counter they share (Budget). An agent reserves its searches before it
  starts and hands back what it did not use, so a seventh row gets nothing unless earlier rows
  left some.
- The prompt for a deep-dive or missing row (prompts/fast_research_row.md). The overnight one
  tells the agent the writer never sees the scout, so it re-sources the scout's basics. Here the
  scout's entries are in the dossier before any agent starts, so the agent is told to spend its
  budget on what the scout does not have. A covered row's verify prompt is the overnight one.
- Each row is filed in the dossier the moment it lands (Dossier.add_row), not when all have.
- No follow-up round. A row that comes back thin is flagged in the ledger (status, gap) and the
  writer works with what there is.
- A failed agent is a result, not an exception: the scheduler treats a row that landed, came
  back thin or failed the same way, and the section is written without it.

A listener's note the dossier cannot serve may add one small row for a section whose drafting
has not begun (note_row; 3 searches, 1 fetch, at most two a chapter). Those agents have their own
searches (Budget.for_notes: max_agents x searches), beside the 30, so the outline's rows cannot
starve them and they take nothing from a row still to start. For a note the dossier can serve,
the judge names the entries that serve it; note_answers hands them to the section writers.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time

from stages import ledger, llm
from stages import research as stage_research
from stages.common import Run, StageError, load_prompt, parse_json, prompt_name

from . import base
from .dossier import Dossier

NOTE_KINDS = ("wants more", "question", "unclear")  # the kinds a missing fact could be behind
NOTE_DECISIONS = "note_rows.json"  # in fast/: what was decided for each note, so a restart does not ask again
_NOTES_LOCK = threading.Lock()

# Structured output for the fast_note call.
NOTE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reason", "entries", "research", "section", "idea", "question", "tier_needed"],
    "properties": {
        "reason": {"type": "string"},
        "entries": {"type": "array", "items": {"type": "integer"}},
        "research": {"type": "boolean"},
        "section": {"type": "integer"},
        "idea": {"type": "string"},
        "question": {"type": "string"},
        "tier_needed": {"type": "integer", "enum": [1, 2, 3, 4]},
    },
}


class Budget:
    """A search counter that agents share. The run's is fast.research.max_searches for the agents
    of the outline's rows together; `notes` on it is the smaller one for the agents listeners'
    notes start (for_notes), so run_row can be handed the run's budget for every row and draw from
    the right one. The scout has its own budget. Thread-safe."""

    def __init__(self, total: int, used: int = 0, notes: "Budget | None" = None):
        self.total = int(total)
        self._left = max(0, self.total - int(used))
        self._lock = threading.Lock()
        self.notes = notes  # None: a note's row draws from this counter too

    @classmethod
    def for_run(cls, r: Run, cfg: dict) -> "Budget":
        """The counter for a run's outline rows, less what its log shows their agents have already
        searched (a continued run does not get the 30 again), with the notes' counter on it."""
        spent = _note_searches(r)
        return cls(base.settings(cfg)["research"]["max_searches"], stage_research.searches_used(r) - spent,
                   notes=cls.for_notes(r, cfg, spent))

    @classmethod
    def for_notes(cls, r: Run, cfg: dict, used: int | None = None) -> "Budget":
        """The counter for the agents listeners' notes start: fast.note_research.max_agents x
        searches, less what the log shows those agents have already searched."""
        nr = base.settings(cfg)["note_research"]
        return cls(nr["max_agents"] * nr["searches"], _note_searches(r) if used is None else used)

    @property
    def left(self) -> int:
        with self._lock:
            return self._left

    def reserve(self, want: int) -> int:
        """Take up to `want` searches; returns how many were granted (0 when none are left)."""
        with self._lock:
            granted = max(0, min(int(want), self._left))
            self._left -= granted
            return granted

    def settle(self, granted: int, used: int) -> int:
        """An agent is done: hand back the searches it reserved and did not use. Returns how many."""
        back = max(0, int(granted) - max(0, int(used)))
        with self._lock:
            self._left = min(self.total, self._left + back)
        return back


def _budget_for(budget: Budget, row: dict) -> Budget:
    """The counter a row's agent draws from: the notes' own for a row a note added, if there is one."""
    notes = getattr(budget, "notes", None)
    return notes if row.get("from_note") and notes is not None else budget


def _note_searches(r: Run) -> int:
    """Searches the log holds for the agents of rows that listeners' notes added."""
    with base.LEDGER_LOCK:
        led = base.read_json(r.path("ledger"), {})
    ids = {x.get("id") for x in _from_notes(led if isinstance(led, dict) else {})}
    return sum(x.get("web_searches", 0) or 0 for x in r.read_log()
               if x.get("row") in ids and x.get("stage") in stage_research.AGENT_STAGES) if ids else 0


# --- the ledger, shared between threads ---------------------------------------------------------

def _update_ledger(r: Run, change) -> dict | None:
    """Read ledger.json, let `change(led)` modify it, and write it back with ledger.md, all under
    base.LEDGER_LOCK and atomically (the server reads these files while the run writes them).
    `change` returns False to leave the files alone. None if there is no ledger."""
    with base.LEDGER_LOCK:
        led = base.read_json(r.path("ledger"))
        if not isinstance(led, dict) or not isinstance(led.get("rows"), list):
            return None
        if change(led) is False:
            return led
        ledger.normalize(led)
        ledger.validate(led)
        base.write_atomic(r.path("ledger"), json.dumps(led, ensure_ascii=False, indent=2))
        base.write_atomic(r.path("ledger_md"), ledger.render_md(led, r.name))
        return led


def _file_verdict(r: Run, row: dict, dossier: Dossier, thin: str | None, error: str | None) -> bool:
    """A landed row's standing, by code alone: filled, partial (thin, with the reason as its gap) or
    open (nothing came back). Written to ledger.json and to the caller's own copy of the row, so the
    writer of its section sees the flag without re-reading the file. Returns whether this is news
    (a resumed run files the same verdict again and has nothing new to report)."""
    numbers = dossier.numbers_of(row["id"])
    status = "open" if not numbers else "partial" if thin else "filled"
    gap = (f"The research agent failed: {error}" if error and not numbers else thin or "")
    verdict = {"status": status, "dossier_refs": [f"D{n:02d}" for n in numbers],
               "best_tier": dossier.best_tier(row["id"]), "gap": gap}
    news = [any(row.get(k) != v for k, v in verdict.items())]
    row.update(verdict)

    def change(led: dict):
        on_disk = ledger.by_id(led).get(row["id"])
        if on_disk is None:
            return False
        news[0] = any(on_disk.get(k) != v for k, v in verdict.items())
        if not news[0]:
            return False
        on_disk.update(verdict)

    _update_ledger(r, change)
    return news[0]


# --- one row ------------------------------------------------------------------------------------

def angle_text(outline: dict, row: dict) -> str:
    """What a row's agent is told the chapter is: the outline's angle and through-line, and the notes
    of the section this row serves. The agent's prompt frames it; these are the outline's own words."""
    lines = [f"# Angle: {outline.get('title', '')}", "", str(outline.get("angle", "")).strip()]
    if outline.get("why"):
        lines += ["", f"Why this angle: {outline['why']}"]
    if outline.get("through_line"):
        lines += ["", f"Through-line: {outline['through_line']}"]
    sections = outline.get("sections") or []
    sec = next((s for s in sections if s.get("n") == row.get("section")), None)
    if sec:
        lines += ["", f"This idea belongs to section {sec['n']} of {len(sections)}, \"{sec.get('title', '')}\"."]
        lines += [f"{label}: {sec[key]}" for label, key in (("The section lands", "lands"),
                                                             ("It sets up", "sets_up"),
                                                             ("How it is told", "notes")) if sec.get(key)]
    if row.get("listener_note"):
        lines += ["", f"The listener, part-way through the chapter, said: \"{row['listener_note']}\""]
    return "\n".join(lines).strip()


def row_prompt(r: Run, cfg: dict, row: dict, led: dict, angle: str, scout_text: str) -> str | None:
    """The prompt for a deep-dive or missing row's agent in a fast run (prompts/fast_research_row.md),
    filled as stages.research.agent_prompt fills the overnight one, with the same reply format
    fragment. None for a covered row: its agent only verifies claims, and the overnight prompt holds.
    `cfg` is the agent's own (_row_cfg), so the budget in the prompt is the one on the tools."""
    if row["type"] == "covered":
        return None
    searches, fetches = ledger.budget(cfg, row)
    others = "\n".join(f"- {o['id']}: {o['idea']}" for o in ledger.active(led) if o["id"] != row["id"])
    return load_prompt(
        "fast_research_row", angle=angle, map=scout_text, row_id=row["id"], idea=row["idea"], rank=row["rank"],
        type=row["type"], scout_has=row["episode_has"] or "Nothing.", research_question=row["research_question"],
        tier_needed=row["tier_needed"], other_rows=others or "(none)", searches=searches, fetches=fetches,
        source_tiers=load_prompt(prompt_name(r, "source_tiers")),
        format=load_prompt(prompt_name(r, "research_format"), row_id=row["id"], start=1))


def _row_cfg(cfg: dict, row: dict, searches: int) -> dict:
    """The config for one agent: the fast budgets for every rank and type, with this agent's searches
    set to what it was granted. A note's row gets the smaller note budget."""
    c = base.agent_cfg(cfg)
    c["research"]["budgets"] = {k: {"searches": searches, "fetches": _per_row(cfg, row)[1]}
                                for k in c["research"]["budgets"]}
    return c


def _per_row(cfg: dict, row: dict) -> tuple[int, int]:
    f = base.settings(cfg)
    b = f["note_research"] if row.get("from_note") else f["research"]["per_row"]
    return b["searches"], b["fetches"]


def _logged(r: Run, row_id: str) -> tuple[int, float]:
    """(searches, cost) the log holds for a row's agent and its write-up."""
    recs = [x for x in r.read_log() if x.get("row") == row_id and str(x.get("stage", "")).startswith("research")]
    return (sum(x.get("web_searches", 0) or 0 for x in recs if x.get("stage") in stage_research.AGENT_STAGES),
            sum(x.get("cost_usd", 0) or 0 for x in recs))


def run_row(r: Run, cfg: dict, row: dict, led: dict, outline: dict, scout_text: str, dossier: Dossier,
            budget: Budget) -> dict:
    """Research one ledger row and file it in the dossier. Never raises for a failed or empty agent.

    Returns {"row", "numbers": [D...], "thin": reason | None, "error": str | None, "searches": n,
    "seconds": s, "cost": usd}. `searches` and `cost` are what this call spent: nothing when the
    row's findings were already on disk. A row whose turn comes after the run's searches are spent
    is not researched: it comes back thin, with no error. `budget` is the run's (Budget.for_run)
    for every row: a row a listener's note added draws from the notes' counter on it."""
    t0 = time.monotonic()
    rid = row["id"]
    own = _budget_for(budget, row)
    for_notes, budget = own is not budget, own
    out = {"row": rid, "numbers": [], "thin": None, "error": None, "searches": 0, "seconds": 0.0, "cost": 0.0}
    starved = False
    try:
        md, _ = stage_research.row_paths(r, rid)
        if not dossier.has(rid) and not md.exists():
            # A tool loop an earlier run paid for only needs its write-up: no searches to reserve.
            pending = stage_research.has_pending(r, row)
            granted = 0 if pending else budget.reserve(_per_row(cfg, row)[0])
            if not pending and not granted:
                starved = True
            else:
                had = (base.read_json(stage_research.pending_path(r, rid), {}).get("budget") or {}).get("searches")
                before = _logged(r, rid)
                try:
                    c, angle = _row_cfg(cfg, row, had if pending and had else granted), angle_text(outline, row)
                    stage_research.run_agent(r, c, row, led, angle, scout_text,
                                             prompt=row_prompt(r, c, row, led, angle, scout_text))
                finally:
                    after = _logged(r, rid)
                    out["searches"], out["cost"] = after[0] - before[0], round(after[1] - before[1], 5)
                    budget.settle(granted, out["searches"])
        if not starved:
            out["numbers"] = dossier.add_row(rid)
    except Exception as e:  # a failed agent is a thin row to the scheduler, never a crashed thread
        out["error"] = str(e) if isinstance(e, StageError) else f"{type(e).__name__}: {e}"
    try:
        if starved:
            whose = (f"the {budget.total} searches kept for listeners' notes" if for_notes else
                     f"the run's {budget.total} searches")
            out["thin"] = f"not researched: {whose} were spent before this row's turn"
        else:
            out["thin"] = dossier.thin(row)
        if _file_verdict(r, row, dossier, out["thin"], out["error"]) and out["thin"]:
            r.decide("research", f"{rid} came back thin ({out['thin']}). A fast run has no follow-up round: "
                                 f"section {row.get('section', '?')} is written with what there is."
                     + (f" The agent failed: {out['error']}" if out["error"] else ""))
    except Exception as e:
        out["error"] = out["error"] or f"{rid}: its verdict was not written to the ledger ({type(e).__name__}: {e})"
    out["seconds"] = round(time.monotonic() - t0, 2)
    return out


# --- a listener's note ----------------------------------------------------------------------------

def _note_key(note: dict) -> str:
    return hashlib.sha256(f"{note.get('time', '')}\0{note.get('text', '')}".encode()).hexdigest()[:12]


def _from_notes(led: dict) -> list[dict]:
    return [x for x in led.get("rows", []) if isinstance(x, dict) and x.get("from_note")]


def _sections_text(outline: dict, open_sections: list[int]) -> str:
    out = []
    for s in outline.get("sections") or []:
        if s["n"] not in open_sections:
            out.append(f"Section {s['n']}, \"{s.get('title', '')}\": closed.")
            continue
        out.append(f"Section {s['n']}, \"{s.get('title', '')}\": open.\n"
                   + "\n".join(f"  {label}: {s[key]}" for label, key in (("Lands", "lands"), ("Sets up", "sets_up"),
                                                                        ("How it is told", "notes")) if s.get(key)))
    return "\n".join(out) or "(none)"


def _ledger_text(led: dict, dossier: Dossier) -> str:
    out = []
    for row in ledger.active(led):
        numbers = dossier.numbers_of(row["id"])
        state = ("still out" if not dossier.has(row["id"]) else "landed with no entries" if not numbers else
                 f"landed: D{numbers[0]:02d}-D{numbers[-1]:02d}" + (f", thin ({row['gap']})" if row.get("gap") else ""))
        out.append(f"- {row['id']}, section {row.get('section', '?')} ({state}): {row['idea']} "
                   f"Question: {row['research_question']}")
    return "\n".join(out) or "(none)"


def note_row(r: Run, cfg: dict, note: dict, outline: dict, led: dict, dossier: Dossier,
             open_sections: list[int], budget: Budget | None = None) -> dict | None:
    """Decide whether a listener's note needs one extra research agent, and if so add its row.

    Only a "wants more", "question" or "unclear" note is considered, only while a section's drafting
    has not begun (`open_sections`), only fast.note_research.max_agents times a chapter, and only
    while the notes' searches are not all spent. Those are checked first, without a call; a note
    turned away by them is not recorded, so it may be put again. `budget` is the run's
    (Budget.for_run) or the notes' own; without one the run's log is read (Budget.for_notes), which
    does not see searches an agent still running has reserved.

    Then one small model call (stage fast_note) reads the note against the outline, the ledger and
    the dossier as it stands. A yes becomes a ledger row ("section", "from_note": true,
    "listener_note", the next free id, type "missing"), appended to ledger.json and to `led` (the
    caller's copy: do not append it again), and returned for run_row, which gives it the note
    budget. Either way the judge names the dossier entries that already serve the note
    (note_answers). Each note is decided once: a second call for the same note returns the same
    answer from fast/note_rows.json without paying."""
    if note.get("kind") not in NOTE_KINDS or not r.path("ledger").exists():
        return None
    key = _note_key(note)
    decisions_path = base.path(r, "work") / NOTE_DECISIONS
    decided = (base.read_json(decisions_path, {}) or {}).get(key)
    if decided is not None:
        return _known_row(r, led, decided["row"]) if decided.get("row") else None
    limit = base.settings(cfg)["note_research"]["max_agents"]
    if not open_sections or len(_from_notes(led)) >= limit:
        return None
    left = (_budget_for(budget, {"from_note": True}) if budget is not None else Budget.for_notes(r, cfg)).left
    if left <= 0:  # its agent could not search: do not pay for the question or add a row no one researches
        return None

    nr = base.settings(cfg)["note_research"]
    where = "before the chapter began" if not note.get("section") else f"during section {note['section']}"
    prompt = load_prompt(
        "fast_note", note=f"({note['kind']}, said {where}) {note['text']}",
        chapter=angle_text(outline, {}), sections=_sections_text(outline, open_sections),
        ledger=_ledger_text(led, dossier), dossier=dossier.text(), searches=min(nr["searches"], left),
        fetches=nr["fetches"],
        left=limit - len(_from_notes(led)), source_tiers=load_prompt(prompt_name(r, "source_tiers")))
    res = llm.call(r, cfg, "fast_note", role="researcher", label=f"fast note {key}",
                   messages=[{"role": "user", "content": prompt}], json_schema=NOTE_SCHEMA, preapproved=True,
                   log_extra={"note": key})
    data = parse_json(r, res.text, f"fast_note_{key}")
    wanted = (data.get("research") is True and data.get("section") in open_sections
              and str(data.get("idea") or "").strip() and str(data.get("question") or "").strip())
    new_id: list[str] = []

    def change(disk: dict):
        if not wanted or len(_from_notes(disk)) >= limit:  # another note took the last one meanwhile
            return False
        new_id.append(ledger.next_free_id(disk))
        disk["rows"].append({
            "id": new_id[0], "rank": max((x["rank"] for x in disk["rows"]), default=0) + 1,
            "idea": " ".join(data["idea"].split()), "type": "missing", "episode_has": "",
            "research_question": " ".join(data["question"].split()),
            "tier_needed": data["tier_needed"] if data.get("tier_needed") in (1, 2, 3, 4) else 2,
            "section": data["section"], "from_note": True, "listener_note": note["text"]})

    _update_ledger(r, change)
    row = _known_row(r, led, new_id[0]) if new_id else None
    if row:
        r.decide("listener note", f"A listener's note ({note['kind']}: \"{note['text']}\") started one extra "
                                  f"research agent, {row['id']}, for section {row['section']}: "
                                  f"{row['research_question']} {str(data.get('reason') or '').strip()}".rstrip())
    with _NOTES_LOCK:
        all_decided = base.read_json(decisions_path, {}) or {}
        all_decided[key] = {"note": note["text"], "time": note.get("time", ""), "kind": note["kind"],
                            "row": row["id"] if row else None, "research": bool(data.get("research")),
                            "reason": str(data.get("reason") or ""),
                            "entries": _numbers(data.get("entries"), dossier.entries())}
        base.write_json(decisions_path, all_decided)
    return row


def _numbers(given, known) -> list[int]:
    """The D numbers a judge named, as integers that are in the dossier, each once, in order."""
    return sorted({n for n in given if isinstance(n, int) and not isinstance(n, bool) and n in known}) \
        if isinstance(given, list) else []


def note_answers(r: Run) -> list[dict]:
    """Every note the judge has decided, in the order decided, with the dossier entries it named as
    already answering or serving the note: [{"time", "text", "kind", "entries": [D numbers]}]. Read
    from fast/note_rows.json. A section's writer is otherwise offered only its own rows' entries and
    the scout's, so an answer filed under another row reaches the listener through this."""
    with _NOTES_LOCK:
        decided = base.read_json(base.path(r, "work") / NOTE_DECISIONS, {})
    return [{"time": str(d.get("time") or ""), "text": str(d.get("note") or ""), "kind": str(d.get("kind") or ""),
             "entries": _numbers(d.get("entries"), d.get("entries") or [])}
            for d in (decided.values() if isinstance(decided, dict) else []) if isinstance(d, dict)]


def _known_row(r: Run, led: dict, row_id: str) -> dict | None:
    """A row by id from the caller's ledger; if it is only on disk yet, the caller's copy gains it."""
    row = ledger.by_id(led).get(row_id)
    if row is None:
        with base.LEDGER_LOCK:
            disk = base.read_json(r.path("ledger"), {}) or {}
        found = next((x for x in disk.get("rows", []) if isinstance(x, dict) and x.get("id") == row_id), None)
        if found is not None:
            row = dict(found)
            led["rows"].append(row)
    return row
