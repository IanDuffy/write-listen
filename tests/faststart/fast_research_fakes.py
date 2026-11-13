"""Fake replies and run-folder helpers for the fast dossier and research tests (area B).

Plain functions, so another area's test (the driver's end to end) can chain them:

    reply(kwargs, rows=..., note=...)   a message for a row agent, its write-up or the fast_note
                                        call; None for any other prompt
    findings(rid, kind)                 a row agent's <research> block
    note_reply(...)                     the fast_note call's JSON
    file_row(r, rid, lines, ...)        a row's findings and sources written straight to 04_research/
    scout_row(r)                        the scout's row, L00, filed the way faststart/scout.py files it
    outline(), ledger_for(outline)      a small outline and the ledger its research questions give
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace as NS

from fakes import PAGE_TEXT, Responder, all_text, text_block, usage

from stages import ledger

PAGE_URL = "https://www.gutenberg.org/ebooks/1600"
SEP_URL = "https://plato.stanford.edu/entries/plato/"
SEP_TEXT = ("Plato. The dinner the Symposium describes is set in 416 BC, at the house of Agathon. "
            "Diotima of Mantinea is known only from this dialogue.")
NOTE_PHRASE = "The listener has just said something to the guide"
ROW = re.compile(r"(?:Your idea is row|This idea is row) (L\d+)")
ON_PAGE = "He who has been instructed thus far in the things of love"
NOT_ON_PAGE = "A sentence that appears on no page any agent ever fetched"


# --- what an agent writes ---------------------------------------------------------------------

def findings(rid: str, kind: str = "full") -> str:
    """A row agent's reply text. full: a claim and material at tier 1, a quote that is on the fetched
    page and one that is not, a tension that cites the row's own entries, a connection. thin: one
    claim from a page no one retrieved. empty: no entries at all. none: no <research> block."""
    if kind == "none":
        return "I searched and found little worth reporting."
    if kind == "empty":
        entries = []
    elif kind == "thin":
        entries = [f"[{rid}.1] (claim, tier 3) A magazine piece repeats the story for {rid}, without a source. "
                   "Source: https://magazine.example.com/ladder"]
    else:
        entries = [
            f"[{rid}.1] (claim, tier 1) Socrates credits his account of love to Diotima of Mantinea, for {rid}. "
            f"Source: Symposium, {PAGE_URL}",
            f"[{rid}.2] (material, tier 1) Agathon's dinner party, the night after his victory, with "
            f"Alcibiades arriving drunk, for {rid}. Source: Symposium, {PAGE_URL}",
            f"[{rid}.3] (quote, tier 1, verbatim: yes) \"{ON_PAGE}\" Plato, Symposium 210e, tr. Jowett, "
            f"for {rid}. Source: Symposium, {PAGE_URL}",
            f"[{rid}.4] (quote, tier 1, verbatim: yes) \"{NOT_ON_PAGE}.\" Found for {rid}. Source: {PAGE_URL}",
            f"[{rid}.5] (tension, tier 2) Some readers say {rid}'s idea abandons persons; compare {rid}.1 "
            f"and {rid}.3. Source: SEP, {SEP_URL}",
            f"[{rid}.6] (connection, {'L01' if rid != 'L01' else 'L02'}) Both turn away from the self.",
        ]
    return (f"Writing up now.\n<research>\n# {rid}: an idea\n\n## Summary\nFound things.\n\n## Entries\n"
            + "\n".join(entries) + f"\n\n## Not found\nWhere Diotima lived; {rid}.2 is the nearest thing.\n</research>")


def _fetched(url=PAGE_URL, title="Symposium", text=PAGE_TEXT):
    return NS(type="web_fetch_tool_result", tool_use_id="f1", content=NS(
        type="web_fetch_result", url=url,
        content=NS(type="document", title=title, source=NS(type="text", media_type="text/plain", data=text))))


def _searched():
    return NS(type="web_search_tool_result", tool_use_id="s1",
              content=[NS(type="web_search_result", url=PAGE_URL, title="Symposium"),
                       NS(type="web_search_result", url=SEP_URL, title="SEP")])


def row_message(kwargs, rid: str, kind: str = "full", searches: int = 3, fetches: int = 1):
    """A row agent's finished turn: a search, a fetched page, then its findings."""
    return Responder.msg(kwargs, None, content=[_searched(), _fetched(), text_block(findings(rid, kind))],
                         u=usage(searches=searches, fetches=fetches))


def paused_message(kwargs, searches: int = 5, fetches: int = 2):
    """A tool loop that paused with budget used: research.run_agent writes it up without tools."""
    return Responder.msg(kwargs, None, stop="pause_turn", content=[_searched(), _fetched()],
                         u=usage(searches=searches, fetches=fetches))


def note_reply(research: bool = False, section: int = 0, idea: str = "", question: str = "", tier_needed: int = 2,
               reason: str = "D02 already tells the dinner party.", entries: list | None = None) -> str:
    """`entries`: the D numbers the judge names as already serving the note (default: D02 for a no)."""
    return json.dumps({"reason": reason, "entries": ([] if research else [2]) if entries is None else entries,
                       "research": research, "section": section, "idea": idea, "question": question,
                       "tier_needed": tier_needed})


def reply(kwargs, rows: dict | None = None, note: str | None = None):
    """The message for a research prompt, or None if the prompt is not one of this area's.
    `rows` maps a row id to how its agent behaves: a kind for findings() ("full" by default), "pause"
    (the loop pauses with its budget spent, then is written up), an Exception to raise, or a callable
    kwargs -> message. `note` is the fast_note call's JSON (default: no research)."""
    t = all_text(kwargs)
    if NOTE_PHRASE in t:
        return Responder.msg(kwargs, note if note is not None else note_reply())
    m = ROW.search(t)
    if not m:
        return None
    rid, how = m.group(1), (rows or {}).get(m.group(1), "full")
    if "your search and fetch budget is now spent" in t:  # the write-up of a paused loop
        return Responder.msg(kwargs, findings(rid, "full" if how == "pause" else how))
    # prompts/fast_research_row.md opens with the same words as the overnight row prompt
    if "You are a research agent for one idea" not in t and "You are a fact-checker for one idea" not in t:
        return None
    if isinstance(how, BaseException):
        raise how
    if callable(how):
        return how(kwargs)
    if how == "pause":
        return paused_message(kwargs)
    return row_message(kwargs, rid, how)


class ResearchFake:
    """A responder for FakeAnthropic that answers only this area's prompts and records every request."""

    def __init__(self, rows: dict | None = None, note: str | None = None):
        self.rows, self.note, self.calls = dict(rows or {}), note, []

    def __call__(self, kwargs):
        self.calls.append(kwargs)
        out = reply(kwargs, self.rows, self.note)
        if out is None:
            raise AssertionError("fake API got an unexpected prompt: " + all_text(kwargs)[:200])
        return out

    def agent_calls(self, rid: str | None = None) -> list[dict]:
        return [c for c in self.calls if c.get("tools") and (m := ROW.search(all_text(c)))
                and (rid is None or m.group(1) == rid)]

    def note_calls(self) -> list[dict]:
        return [c for c in self.calls if NOTE_PHRASE in all_text(c)]


# --- run-folder helpers -----------------------------------------------------------------------

def file_row(r, rid: str, lines: list[str], pages: dict | None = None, urls: dict | None = None,
             not_found: str = "Nothing.", searches: int = 3, budget: int | None = 5, extra: str = "") -> None:
    """Write 04_research/<rid>.md and <rid>.sources.json as a finished agent leaves them.
    pages: {url: text} fetched; urls: {url: title} seen in searches (fetched pages are added)."""
    d = r.path("research_dir")
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rid}.md").write_text(f"# {rid}: an idea\n\n## Summary\nFound things.\n\n{extra}## Entries\n"
                                 + "\n".join(lines) + f"\n\n## Not found\n{not_found}\n", encoding="utf-8")
    pages = pages or {}
    (d / f"{rid}.sources.json").write_text(json.dumps({
        "urls": {**{u: "" for u in pages}, **(urls or {})},
        "fetched": [{"url": u, "title": "", "text": t} for u, t in pages.items()], "failed": [],
        "row": rid, "searches": searches, "fetches": len(pages),
        "budget": {"searches": budget, "fetches": 2} if budget is not None else None, "written_up": False,
        "cost_usd": 0.1}), encoding="utf-8")


SCOUT_LINES = [
    f"[L00.1] (claim, tier 1) The Symposium is Plato's dialogue on love, told at a dinner party. Source: {PAGE_URL}",
    f"[L00.2] (material, tier 1) Agathon hosts the dinner the night after winning the prize for tragedy. Source: {PAGE_URL}",
    f"[L00.3] (quote, tier 1, verbatim: yes) \"{ON_PAGE}\" Plato, Symposium 210e, tr. Jowett. Source: {PAGE_URL}",
    f"[L00.4] (quote, tier 2, verbatim: yes) \"{NOT_ON_PAGE}.\" Source: {SEP_URL}",
    f"[L00.5] (claim, tier 2) The dinner is set in 416 BC; see L00.2 for the scene. Source: SEP, {SEP_URL}",
]


def scout_row(r) -> None:
    """L00 as faststart/scout.py files it: the <scout> block, with Territory and Scenes around the entries."""
    file_row(r, "L00", SCOUT_LINES, pages={PAGE_URL: PAGE_TEXT, SEP_URL: SEP_TEXT}, searches=4, budget=6,
             extra="## Territory\nWhat is known, what is contested.\n\n")
    p = r.path("research_dir") / "L00.md"
    p.write_text(p.read_text(encoding="utf-8").replace("\n\n## Not found\nNothing.\n", "")
                 + "\n\n## Scenes\n1. The dinner party at Agathon's house (L00.2, L00.5).\n", encoding="utf-8")


def outline(n_sections: int = 4) -> dict:
    """An outline in the contract's shape: section 1 from the scout alone, one research question each after."""
    sections = [{"n": 1, "title": "The dinner party", "lands": "Why a dinner party is where this starts.",
                 "sets_up": "The ladder.", "hand_off": "What Diotima says next.", "target_words": 950,
                 "notes": "Open on Agathon's table.", "scout_entries": [1, 2, 3], "research_questions": []}]
    for n in range(2, n_sections + 1):
        sections.append({
            "n": n, "title": f"Rung {n}", "lands": f"What rung {n} of the ladder asks of the lover.",
            "sets_up": f"Rung {n + 1}.", "hand_off": "Up one more.", "target_words": 680,
            "notes": f"Tell rung {n} through one scene.", "scout_entries": [],
            "research_questions": [{"idea": f"Rung {n} turns the lover from one body to all.",
                                    "question": f"What does Diotima say at rung {n}, Symposium 210a-212a?",
                                    "type": "deep-dive", "tier_needed": 2, "scout_has": "The ladder, in passing."}]})
    return {"title": "The ladder and the self", "angle": "Beauty is a ladder you climb away from yourself.",
            "why": "The listener asked whether beauty can make us good.",
            "through_line": "Can looking at beauty change who you are?", "closing_idea": "The top of the ladder.",
            "listener_words": ["can beauty make us good"], "sections": sections}


def ledger_for(o: dict) -> dict:
    """The ledger the outline's research questions give: L01.. in section order, each tagged with its section."""
    rows = []
    for s in o["sections"]:
        for q in s["research_questions"]:
            n = len(rows) + 1
            rows.append({"id": f"L{n:02d}", "rank": n, "idea": q["idea"], "type": q["type"],
                         "episode_has": q["scout_has"], "research_question": q["question"],
                         "tier_needed": q["tier_needed"], "section": s["n"]})
    return ledger.normalize({"run": "test-run", "source": "seed", "rows": rows, "parked": [], "connections": []})
