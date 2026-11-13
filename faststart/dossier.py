"""04_dossier.md for a fast run: append-only, so an entry never changes its D number.

The overnight dossier is rebuilt whole once every row has findings, numbered in rank order
(stages/research.py build_dossier). A fast run cannot wait for that: the opening is written and
played from the scout's entries while the other rows are still out, and each later section is
locked citing what had landed by then. So here a row's entries take the next free numbers the
moment the row lands, and nothing already in the file is ever touched again: not its number,
not its tags, not its text.

    add_row("L00")   the scout's entries      -> D01..D07
    add_row("L02")   whichever row lands next -> D08..D12
    add_row("L01")                            -> D13..D18

Each row goes through the overnight checks as it lands (research.check_entries): a
`verbatim: yes` quote that is not word for word on a fetched page is downgraded to
`verbatim: no`, so no section can quote it; tiers better than the source's domain allows are
flagged. A row is checked against every page fetched so far, the scout's included, and its
verdict then stands.

The file keeps the overnight layout (`## Entries`, one `###` section per row, tags
`(L03, kind, tier N, verbatim: yes|no)`, then Not found, Sources cited and the source check), so
common.dossier_entries, ask/ and episode/ read it unchanged. It is rewritten whole, atomically,
from memory on every add: ask-by-voice reads it while the run is writing it.

fast/dossier_index.json holds every entry as numbered and checked. A new Dossier on the same
run folder loads it, so a restarted run gives the same numbers and never re-reads a row's
findings to decide them. One Dossier per run in a process: the driver makes it and hands it on.
"""

from __future__ import annotations

import json
import re
import threading
from collections import Counter

from stages import research
from stages.common import KINDS, Run, StageError, entry_id, format_tags

from . import base

INDEX = "dossier_index.json"  # in fast/
ANGLE_TITLE = re.compile(r"^#+\s+(?:Angle:\s*)?(.+)$", re.M)
FINDINGS_TITLE = re.compile(r"^#\s+L\d+:\s*(.+)$", re.M)
NOTHING = ("nothing", "none", "n/a")
CHECK_LISTS = ("untagged", "no_tier", "no_url", "tier_flags", "episode", "verified_page", "downgraded",
               "unseen_urls")


def _one_line(text: str) -> str:
    """An entry is one line in the file: a wrapped line that began '[D03]' or '#' inside an
    entry's text would read as a new entry or a heading to every parser downstream."""
    return " ".join(str(text).split())


class Dossier:
    def __init__(self, r: Run, cfg: dict):
        self.r, self.cfg = r, cfg
        self._lock = threading.RLock()
        self._rows: list[dict] = []  # in landing order; see _land for a row's fields
        self._seen = {"urls": {}, "fetched": [], "failed": []}  # every landed row's sources, for the checks
        self._index = base.path(r, "work") / INDEX
        saved = base.read_json(self._index)
        if saved:
            self._rows = saved["rows"]
            for row in self._rows:
                self._see(row["id"])
            if not r.path("dossier").exists() or r.path("dossier").read_text(encoding="utf-8") != self._render():
                self._write()  # a run stopped between the index and the file

    # --- what has landed ---------------------------------------------------------------------

    def has(self, row_id: str) -> bool:
        with self._lock:
            return any(row["id"] == row_id for row in self._rows)

    def numbers_of(self, row_id: str) -> list[int]:
        with self._lock:
            return [e["d"] for row in self._rows if row["id"] == row_id for e in row["entries"]]

    def refs(self, row_id: str) -> dict[int, int]:
        """A landed row's own entry numbers -> D numbers (L00.3 -> 3 -> D03), for rewriting text
        that cites the row's entries by their local numbers."""
        with self._lock:
            return {e["n"]: e["d"] for row in self._rows if row["id"] == row_id for e in row["entries"]}

    def entries(self) -> dict[int, str]:
        """Entry number -> the full entry line, exactly as common.dossier_entries reads it from the file."""
        with self._lock:
            return {e["d"]: self._line(e) for row in self._rows for e in row["entries"]}

    def for_rows(self, row_ids: list[str]) -> dict[int, str]:
        with self._lock:
            return {e["d"]: self._line(e) for row in self._rows if row["id"] in row_ids for e in row["entries"]}

    def text(self) -> str:
        """04_dossier.md as it stands."""
        with self._lock:
            return self._render()

    # --- a row lands -------------------------------------------------------------------------

    def add_row(self, row_id: str) -> list[int]:
        """File a row's findings (04_research/<row_id>.md and its .sources.json) under the next free
        D numbers, checked, and rewrite 04_dossier.md. A row already filed returns its numbers and
        changes nothing, whatever has happened to its findings file since. It reads the row's idea
        from ledger.json, so never call it while holding base.LEDGER_LOCK."""
        if self.has(row_id):
            return self.numbers_of(row_id)
        # The ledger is read before taking this object's lock, never while holding it: a thread that
        # holds LEDGER_LOCK may call entries() or thin().
        meta = self._ledger_row(row_id)
        with self._lock:
            if self.has(row_id):
                return self.numbers_of(row_id)
            md, _ = research.row_paths(self.r, row_id)
            if not md.exists():
                raise StageError(f"no findings for {row_id}: 04_research/{md.name} is missing.")
            if not self._rows and not self._index.exists() and self.r.path("dossier").exists():
                raise StageError("04_dossier.md exists but was not built by a fast run (there is no "
                                 f"fast/{INDEX}); a fast run will not overwrite it.")
            row = self._land(row_id, md.read_text(encoding="utf-8"), meta)
            self._rows.append(row)
            try:
                base.write_json(self._index, {"rows": self._rows})
                self._write()
            except BaseException:
                self._rows.pop()
                raise
            return [e["d"] for e in row["entries"]]

    def _land(self, row_id: str, findings: str, meta: dict) -> dict:
        """Number, renumber and check one row's entries. Called with the lock held."""
        nxt = max((e["d"] for row in self._rows for e in row["entries"]), default=0) + 1
        rnum = int(row_id[1:])
        entries, refs, dropped = [], {}, []
        for e in research.parse_entries(findings):
            if e["n"] in refs:  # the agent used a number twice: the first keeps it, as in the overnight merge
                dropped.append(f"{row_id}.{e['n']}")
                continue
            refs[e["n"]] = nxt + len(entries)
            e["tags"]["rows"] = [row_id]
            entries.append({"d": refs[e["n"]], "n": e["n"], "tags": e["tags"], "body": e["body"], "tagged": e["tagged"]})
        for e in entries:  # 'compare L03.2' -> 'compare D11'
            e["body"] = _one_line(research._renumber(e["body"], rnum, refs))
        stats = self._see(row_id)
        chk = research.check_entries(self.cfg, entries, self._seen, "")  # downgrades unmatched quotes in place
        nf = research._section(findings, "Not found")
        idea = meta.get("idea") or (m.group(1) if (m := FINDINGS_TITLE.search(findings)) else "")
        return {"id": row_id, "idea": _one_line(idea), "about": meta.get("about", ""), "type": meta.get("type"),
                "entries": entries,
                "not_found": "" if nf.strip().rstrip(".").lower() in ("", *NOTHING) else
                             research._renumber(nf, rnum, refs),
                "checks": {**{k: sorted(chk[k]) for k in CHECK_LISTS}, "dropped": dropped}, **stats}

    def _see(self, row_id: str) -> dict:
        """Add a row's sources to what the checks may match against; return its search figures."""
        _, src = research.row_paths(self.r, row_id)
        s = base.read_json(src, {}) or {}
        self._seen["urls"].update(s.get("urls", {}))
        self._seen["fetched"].extend(s.get("fetched", []))
        self._seen["failed"] += [{**f, "row": row_id} for f in s.get("failed", [])]
        return {"searches": s.get("searches", 0), "budget": (s.get("budget") or {}).get("searches"),
                "written_up": bool(s.get("written_up"))}

    def _ledger_row(self, row_id: str) -> dict:
        """The heading for a row's section: the scout's, or the row's idea and where it belongs."""
        if row_id == base.SCOUT_ROW:
            return {"idea": "what the scout found",
                    "about": "Found before the outline was written. The opening is written from these alone."}
        with base.LEDGER_LOCK:
            led = base.read_json(self.r.path("ledger"), {}) or {}
        row = next((x for x in led.get("rows", []) if isinstance(x, dict) and x.get("id") == row_id), None)
        if not row:
            return {}
        parts = [f"Section {row['section']}"] if row.get("section") else []
        parts += [str(row.get("type") or "")] if row.get("type") else []
        about = ", ".join(parts)
        if row.get("tier_needed"):
            about += f"{'; ' if about else ''}needs tier {row['tier_needed']} or better"
        if row.get("from_note"):
            about += f"{'; ' if about else ''}added to answer something the listener said"
        return {"idea": row.get("idea", ""), "type": row.get("type"), "about": about + "." if about else ""}

    # --- thin rows ---------------------------------------------------------------------------

    def _tier(self, e: dict) -> int | None:
        """An entry's tier as stage 04b's code check counts it (ledger_update.apply): none for a
        connection or an entry citing no URL that an agent retrieved; no better than its domain allows."""
        t = e["tags"]
        if t["kind"] == "connection" or not isinstance(t["tier"], int):
            return None
        seen = {research._norm(u) for u in self._seen["urls"]}
        urls = [u for u in (x.rstrip(".,;:") for x in research.URL_IN_TEXT.findall(e["body"])) if research._norm(u) in seen]
        if not urls:
            return None
        dt = research.domain_tier(self.cfg, urls)
        return max(t["tier"], dt[0]) if dt else t["tier"]

    def best_tier(self, row_id: str) -> int | None:
        """The best tier among a row's own entries, counted as _tier counts them."""
        with self._lock:
            tiers = [t for row in self._rows if row["id"] == row_id for e in row["entries"]
                     if (t := self._tier(e)) is not None]
            return min(tiers) if tiers else None

    def thin(self, row: dict) -> str | None:
        """Why a ledger row counts as thin, or None. A fast run has no judge and no follow-up round,
        so this is stage 04b's code check alone: the row has entries, one of them is at the tier the
        row needs, and there is something to tell (a material entry or a quote the script may use;
        for a covered row, a claim confirmed or corrected)."""
        with self._lock:
            if not self.has(row["id"]):
                return "no findings"
            mine = [e for x in self._rows if x["id"] == row["id"] for e in x["entries"]]
            if not mine:
                return "no entries"
            problems = []
            best, need = self.best_tier(row["id"]), row.get("tier_needed")
            if best is None or (isinstance(need, int) and best > need):
                problems.append(f"best source tier is {best or 'none'}, and the row needs {need}")
            tags = [e["tags"] for e in mine]
            if row.get("type") == "covered":
                if not any(t["kind"] == "claim" and t["verdict"] in ("confirmed", "corrected") for t in tags):
                    problems.append("none of its claims is confirmed or corrected")
            elif not any(t["kind"] == "material" or t["verbatim"] == "yes" for t in tags):
                problems.append("no tellable material and no verbatim quote")
            return "; ".join(problems) or None

    # --- the file ----------------------------------------------------------------------------

    @staticmethod
    def _line(e: dict) -> str:
        return f"[{entry_id(e['d'])}] {format_tags(e['tags'])} {e['body']}".rstrip()

    def _write(self) -> None:
        base.write_atomic(self.r.path("dossier"), self._render())

    def _render(self) -> str:
        angle = self.r.path("angle").read_text(encoding="utf-8") if self.r.path("angle").exists() else ""
        title = ANGLE_TITLE.search(angle)
        every = [e for row in self._rows for e in row["entries"]]
        lines = [f"# Dossier: {title.group(1).strip() if title else self.r.name}", "",
                 "Built by code from 04_research/ as each row's research landed, one section per row in landing "
                 "order. An entry keeps its number, its row, its kind and its source tier for good; nothing "
                 "here is renumbered or rewritten. See the source check at the end.", "", "## Entries", ""]
        for row in self._rows:
            lines += [f"### {row['id']}: {row['idea']}" if row["idea"] else f"### {row['id']}", ""]
            if row["about"]:
                lines += [row["about"], ""]
            lines += [self._line(e) + "\n" for e in row["entries"]] or ["(no entries)\n"]
        if not self._rows:
            lines += ["(nothing has landed yet)", ""]
        if any(row["not_found"] for row in self._rows):
            lines += ["## Not found", ""] + [f"From {row['id']}:\n{row['not_found']}\n" for row in self._rows
                                             if row["not_found"]]
        cited = sorted({u.rstrip(".,;:") for e in every for u in research.URL_IN_TEXT.findall(e["body"])})
        titles = self._seen["urls"]
        lines += ["## Sources cited", ""]
        lines += [f"- {titles[u]}: {u}" if titles.get(u) else f"- {u}" for u in cited] or ["(none)"]
        lines += ["", "## Source check (by code, as each row landed)", ""]
        kinds = Counter(e["tags"]["kind"] or "claim" for e in every)
        lines.append(f"- Entries: {len(every)}" + (f" ({', '.join(f'{k} {kinds[k]}' for k in KINDS if kinds[k])})"
                                                   if every else ""))
        if self._rows:
            lines.append("- Rows, in the order they landed: " + ", ".join(
                row["id"] + (f" ({entry_id(row['entries'][0]['d'])}-{entry_id(row['entries'][-1]['d'])})"
                             if len(row["entries"]) > 1 else f" ({entry_id(row['entries'][0]['d'])})"
                             if row["entries"] else " (no entries)") for row in self._rows))
        no_material = [row["id"] for row in self._rows if row["id"] != base.SCOUT_ROW and row["type"] != "covered"
                       and not any(e["tags"]["kind"] == "material" for e in row["entries"])]
        if no_material:
            lines.append(f"- Rows with no tellable material: {', '.join(no_material)}")

        def listed(key: str) -> list[str]:
            return [x for row in self._rows for x in row["checks"][key]]

        for key, text in (("dropped", "Entry numbers an agent used twice (the first was kept)"),
                          ("untagged", "Entries with no tags (treated as claims)"),
                          ("no_tier", "Entries with no source tier"),
                          ("no_url", "Entries with no source URL"),
                          ("tier_flags", "Tiers better than the source's domain allows (flagged, not changed)"),
                          ("episode", "Entries with no page behind them")):
            if listed(key):
                lines.append(f"- {text}: {', '.join(listed(key))}")
        if self._seen["failed"]:
            lines.append("- Fetches that failed: " + "; ".join(
                f"{f['row']} {f['url']} ({f['error']})" for f in self._seen["failed"]))
        if listed("unseen_urls"):
            lines.append("- Cited URLs that no search or fetch had returned when the row landed. The agent may "
                         "have written them from memory:")
            lines += [f"  - {u}" for u in listed("unseen_urls")]
        verified = listed("verified_page")
        lines.append(f"- Verbatim quotes matched word for word on a fetched page: {len(verified)}"
                     + (f" ({', '.join(verified)})" if verified else ""))
        if listed("downgraded"):
            lines.append("- Quotes downgraded to verbatim: no, so the script may not quote them: "
                         + ", ".join(listed("downgraded")))
        agents = [row for row in self._rows if row["budget"] is not None]
        if agents:
            cap = base.settings(self.cfg)["research"]["max_searches"]
            lines.append(f"- Searches (the outline's rows share {cap}; the scout, and a row added for a "
                         "listener's note, have their own): " + ", ".join(
                f"{row['id']} {row['searches']} of {row['budget']}"
                + (", written up after its tool loop paused" if row["written_up"] else "") for row in agents))
        return "\n".join(lines).rstrip() + "\n"
