"""Stage 04b: ledger.json + 04_dossier.md -> ledger.json, ledger.md, 04_research/ledger_update.md

1. Judge (Opus, structured output). For each active row: is the research
   question answered at the tier needed, with tellable material? Sets status
   (filled, partial, open), dossier_refs, best_tier, and for rows not filled
   the gap. Merges the cross-row connections the agents flagged; ledger.md shows
   them at the top, and the brief uses them for transitions.
2. Code check. 'filled' also needs, in the dossier itself: an entry at the tier
   needed or better, and a material entry (for a covered row, an episode claim
   confirmed or corrected). For this check an entry counts at no better than its
   source's domain allows, and not at all if it cites only URLs no agent
   retrieved. Otherwise the row becomes partial, and the reason is added to its
   gap. dossier_refs always include the row's own entries.
3. Follow-up round. Each open or partial row gets one more agent, with its gap
   stated (research.budgets.followup: 4 searches, 2 fetches), at most once per
   row. The dossier is rebuilt with the new entries and step 1 runs again.
4. Rows still open or partial are flagged in ledger.md for Ian.

This stage rewrites 04_dossier.md when a follow-up round runs. If the dossier
has changed since the pipeline last wrote it (its hash is in
04_research/dossier.sha256), the stage stops before any paid call rather than
risk losing the edits; --force goes ahead. With --force the judge runs again;
a row's follow-up is never repeated.
"""

from __future__ import annotations

import json
from collections import defaultdict

from . import ledger, llm, research
from .common import (Run, StageError, dossier_entries, entry_id, entry_numbers, entry_tags,
                     load_prompt, parse_json)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rows", "connections"],
    "properties": {
        "rows": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "status", "dossier_refs", "best_tier", "gap"],
            "properties": {
                "id": {"type": "string"},
                "status": {"type": "string", "enum": list(ledger.STATUSES)},
                "dossier_refs": {"type": "array", "items": {"type": "string"}},
                "best_tier": {"anyOf": [{"type": "integer", "enum": [1, 2, 3, 4]}, {"type": "null"}]},
                "gap": {"type": "string"},
            },
        }},
        "connections": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["rows", "link"],
            "properties": {"rows": {"type": "array", "items": {"type": "string"}}, "link": {"type": "string"}},
        }},
    },
}


def judge(r: Run, cfg: dict, led: dict, dossier: str, round_no: int) -> dict:
    fb = cfg["research"]["budgets"]["followup"]
    prompt = load_prompt("ledger_update", ledger=ledger.describe(ledger.active(led)), dossier=dossier,
                         source_tiers=load_prompt("source_tiers"),
                         followup_searches=fb["searches"], followup_fetches=fb["fetches"])
    res = llm.call(r, cfg, "ledger_update", messages=[{"role": "user", "content": prompt}],
                   label=f"ledger update round {round_no}", json_schema=SCHEMA, preapproved=True)
    data = parse_json(r, res.text, "ledger_update")
    (r.path("research_dir") / f"ledger_update_round{round_no}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


def apply(led: dict, data: dict, dossier: str, cfg: dict, seen_urls: set[str] | None = None) -> None:
    """Write the judge's verdicts into the ledger, with the code checks on 'filled'."""
    entries = dossier_entries(dossier)
    tags = {n: entry_tags(text) for n, text in entries.items()}
    seen_n = {research._norm(u) for u in seen_urls} if seen_urls is not None else None

    def tier(n: int) -> int | None:
        """The entry's tier for the code check: none for a connection, an episode entry, or an entry
        citing only URLs no agent retrieved; no better than its source's domain allows."""
        t = tags[n]
        if t["kind"] == "connection" or not isinstance(t["tier"], int):
            return None
        urls = [u.rstrip(".,;:") for u in research.URL_IN_TEXT.findall(entries[n])]
        if seen_n is not None and urls and not any(research._norm(u) in seen_n for u in urls):
            return None
        dt = research.domain_tier(cfg, urls) if urls else None
        return max(t["tier"], dt[0]) if dt else t["tier"]

    own = defaultdict(set)
    for n, t in tags.items():
        for rid in t["rows"]:
            own[rid].add(n)
    verdicts = {v.get("id"): v for v in data.get("rows", []) if isinstance(v, dict)}
    missing = [row["id"] for row in ledger.active(led) if row["id"] not in verdicts]
    if missing:
        raise StageError(f"ledger update: no verdict for {', '.join(missing)}; the judge's output is in "
                         "04_research/ledger_update_round*.json. Re-run ledger-update.")
    for row in ledger.active(led):
        v = verdicts[row["id"]]
        refs = {n for n in entry_numbers(" ".join(v.get("dossier_refs") or [])) if n in entries} | own[row["id"]]
        status, gap = v.get("status"), (v.get("gap") or "").strip()
        if status not in ledger.STATUSES:
            status = "open"
        tiers = [t for t in (tier(n) for n in refs) if t is not None]
        judged = v.get("best_tier") if v.get("best_tier") in (1, 2, 3, 4) else None
        if not tiers:
            best = None
        elif judged is not None:
            best = max(judged, min(tiers))  # no better than the best entry it cites
        else:
            best = None if status == "open" else min(tiers)
        if status == "filled":
            problems = []
            if best is None or best > row["tier_needed"]:
                problems.append(f"best source tier is {best or 'none'}, and the row needs {row['tier_needed']}")
            if row["type"] == "covered":
                if not any(tags[n]["kind"] == "claim" and tags[n]["verdict"] in ("confirmed", "corrected")
                           for n in refs):
                    problems.append("no episode claim is confirmed or corrected")
            elif not any(tags[n]["kind"] == "material" for n in refs):
                problems.append("no tellable material entry")
            if problems:
                status = "partial"
                gap = (gap + " " if gap else "") + "Code check: " + "; ".join(problems) + "."
        row.update(status=status, dossier_refs=[entry_id(n) for n in sorted(refs)], best_tier=best,
                   gap="" if status == "filled" else gap)
    ids = {row["id"] for row in led["rows"]}
    led["connections"] = [
        {"rows": [x for x in c.get("rows", []) if x in ids], "link": " ".join(str(c.get("link", "")).split())}
        for c in data.get("connections", []) if isinstance(c, dict) and c.get("link")]


def _followup_job(r: Run, row: dict) -> tuple[dict, dict]:
    md, _ = research.row_paths(r, row["id"])
    earlier = md.read_text(encoding="utf-8") if md.exists() else "(none)"
    start = max((e["n"] for e in research.parse_entries(earlier)), default=0) + 1
    return row, {"earlier": earlier, "gap": row["gap"] or "The research question is not answered yet.",
                 "start": start}


def _record(led: dict, rounds: list[str], ran_now: list[str]) -> str:
    lines = ["# Ledger update", "", "Stage 04b. The full ledger is in ledger.md.", ""]
    lines += rounds
    done = [r["id"] + ("" if r["id"] in ran_now else " (an earlier run)") for r in ledger.active(led) if r["followup"]]
    lines += ["", "Follow-ups: " + (", ".join(done) if done else "none needed"), ""]
    still = [r for r in ledger.active(led) if r["status"] in ("partial", "open")]
    lines += ["Still open (for Ian): " + ("; ".join(f"{r['id']} {r['status']}: {r['gap']}" for r in still)
                                         if still else "none")]
    return "\n".join(lines) + "\n"


def _statuses(led: dict, round_no: int) -> str:
    return f"Round {round_no}: " + ", ".join(f"{r['id']} {r['status']}" for r in ledger.active(led))


def run(r: Run, cfg: dict):
    led = ledger.load(r, cfg)
    dossier = r.read("dossier")
    angle, episode_map = r.read("angle"), r.read("map")
    transcript = r.path("transcript").read_text(encoding="utf-8") if r.path("transcript").exists() else ""
    if r.path("ledger_update").exists() and not r.force:
        raise StageError("Output already exists: 04_research/ledger_update.md. Use --force to judge again "
                         "(a row's follow-up is never repeated).")
    edited = research.dossier_edited(r, dossier)
    if edited is None:  # no record of what the pipeline wrote: compare with a rebuild
        edited = research.build_dossier(r, cfg, led, transcript) != dossier
    if edited and not r.force:
        raise StageError("04_dossier.md has changed since the pipeline wrote it (edited by hand?). If a row "
                         "needs a follow-up, this stage rebuilds the dossier from 04_research/, and the edits "
                         "would be lost. Put them in 04_research/Lnn.md and run research --force, or run "
                         "ledger-update --force to go ahead.")
    seen = research.gather(r, led)["seen"]["urls"]
    judge_est = 2 * llm.estimate_call(cfg, cfg["models"]["writer"], llm.estimate_tokens(dossier) + 3000,
                                      cfg["stages"]["ledger_update"]["expected_output_tokens"])
    llm.guard(r, cfg, "ledger update (two judge calls)", judge_est)

    apply(led, judge(r, cfg, led, dossier, 1), dossier, cfg, seen)
    ledger.save(r, led)
    rounds = [_statuses(led, 1)]
    print("  " + rounds[0])

    need = [row for row in ledger.active(led) if row["status"] in ("partial", "open")
            and not research.row_paths(r, row["id"], followup=True)[0].exists()]
    if need:
        jobs = [_followup_job(r, row) for row in need]
        s = cfg["research"]["budgets"]["followup"]["searches"]
        research.check_search_cap(r, cfg, s * len(jobs), f"The follow-up round ({len(jobs)} agents)")
        llm.guard(r, cfg, f"follow-up round ({len(jobs)} agents)",
                  research.estimate(cfg, [(row, True) for row, _ in jobs]))
        errors = research.run_agents(r, cfg, jobs, led, angle, episode_map, research.spend_checker(r, cfg))
        dossier = research.build_dossier(r, cfg, led, transcript)
        research.write_dossier(r, dossier)  # with every follow-up that finished, even if others failed
        if errors:
            raise StageError("some follow-up agents failed; re-run ledger-update to retry them (finished "
                             "follow-ups are kept, and are in 04_dossier.md):\n  " + "\n  ".join(errors))
        print("  04_dossier.md rebuilt with the follow-up entries")
        seen = research.gather(r, led)["seen"]["urls"]
        apply(led, judge(r, cfg, led, dossier, 2), dossier, cfg, seen)
        rounds.append(_statuses(led, 2))
        print("  " + rounds[-1])
    for row in led["rows"]:
        row["followup"] = research.row_paths(r, row["id"], followup=True)[0].exists()
    ledger.save(r, led)
    r.path("ledger_update").write_text(_record(led, rounds, [row["id"] for row in need]), encoding="utf-8")
    still = [row["id"] for row in ledger.active(led) if row["status"] in ("partial", "open")]
    if still:
        print(f"  Still open or partial, flagged in ledger.md for Ian: {', '.join(still)}")
    if led["connections"]:
        print(f"  {len(led['connections'])} connection(s) between rows, at the top of ledger.md")
    print(f"\n  Next: python cli.py brief {r.name}")
    return r.path("ledger")
