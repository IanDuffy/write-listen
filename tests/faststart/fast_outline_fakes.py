"""Fake replies and run-folder helpers for the fast scout and outline tests (area A).

Plain functions, so another area's test (the driver's end to end) can chain them:

    reply(kwargs, script=None)     a message for the scout, its write-up, the early-scout match, the
                                   outline, the outline edit or the outline repair; None for any other prompt
    Script                         what those replies say (pause the scout, break the edited outline, ...)
    AreaA                          a Responder that answers area A's prompts first, then the stock ones
    scout_block(), outline(n)      the scout's <scout> block and a valid outline of n sections
    seed_run(tmp_path, cfg)        an unattended seed run in a temp folder
    StubDossier                    the part of faststart.dossier.Dossier that the scout and outline call
    dossiers()                     ["stub"] plus "real" when faststart.dossier can be imported
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from types import SimpleNamespace as NS

import pytest
from fakes import Responder, all_text, text_block, usage

from stages import research
from stages.common import Run, entry_id, format_tags

TOPIC = "Why Roman concrete has lasted two thousand years"
SAID = "I pour concrete for a living and ours cracks in thirty years. Theirs is still standing in the sea."

VITRUVIUS_URL = "https://www.gutenberg.org/ebooks/20239"
VITRUVIUS_TEXT = ("The Ten Books on Architecture, by Vitruvius, translated by Morris Hicky Morgan. Book II, "
                  "chapter VI. There is also a kind of powder which from natural causes produces astonishing "
                  "results. It is found in the neighbourhood of Baiae and in the country belonging to the towns "
                  "round about Mt. Vesuvius.")
HARBOR_URL = "https://www.britannica.com/place/Caesarea"
CLASTS_URL = "https://www.science.org/doi/10.1126/sciadv.add1602"
ON_PAGE = "There is also a kind of powder which from natural causes produces astonishing results."
NOT_ON_PAGE = "The sea itself is the mason that finishes what the builders began."

# One phrase from each prompt, to tell them apart. The write-up prompt quotes the scout's whole
# prompt, so it is looked for first.
WRITEUP_PHRASE = "You were scouting for a listener's audio chapter"
SCOUT_PHRASE = "You are the scout, and the listener is waiting"
MATCH_PHRASE = "Is one of them the same subject as the chapter they asked for"
REPAIR_PHRASE = "A code check then found that it breaks these rules"
EDIT_PHRASE = "You are the second editor on one narrated audio chapter"
OUTLINE_PHRASE = "You are the commissioning editor for one narrated audio chapter"


# --- the scout ------------------------------------------------------------------------------

def scout_block() -> str:
    """The scout's reply text: a territory sketch, six entries as row L00 (one quote on the fetched
    page, one on no page, a tension citing another entry) and two scenes citing their entries."""
    return ("Writing up.\n<scout>\n## Territory\n"
            "Roman marine concrete has stood in seawater for two thousand years. How it lasts is partly settled "
            "(volcanic ash and lime) and partly contested (whether the lime lumps were a flaw or a method).\n\n"
            "## Entries\n"
            "[L00.1] (claim, tier 2) Herod's builders raised the harbor at Caesarea between 22 and 10 BC, "
            f"on a coast with no natural shelter. Source: Caesarea, {HARBOR_URL}\n"
            "[L00.2] (material, tier 1) Vitruvius, writing for Augustus, describes a powder found near Baiae and "
            f"round Mount Vesuvius that sets hard under water. Source: Vitruvius, {VITRUVIUS_URL}\n"
            f"[L00.3] (quote, tier 1, verbatim: yes) \"{ON_PAGE}\" Vitruvius, Ten Books on Architecture, "
            f"book 2, chapter 6, translated by Morgan. Source: Vitruvius, {VITRUVIUS_URL}\n"
            f"[L00.4] (quote, tier 1, verbatim: yes) \"{NOT_ON_PAGE}\" Vitruvius, as a magazine gives it. "
            f"Source: Vitruvius, {VITRUVIUS_URL}\n"
            "[L00.5] (claim, tier 2) White lumps of lime in the concrete dissolve when a crack lets water in, and "
            f"harden again to close it. Source: Science Advances, {CLASTS_URL}\n"
            "[L00.6] (tension, tier 2) Older accounts called the lime lumps a sign of careless mixing; this "
            f"conflicts with L00.5. Source: Science Advances, {CLASTS_URL}\n\n"
            "## Scenes\n"
            "### Forms sunk in the open sea\n"
            "On a coast with no shelter, Herod's builders lowered wooden forms into the water and filled them "
            "with ash and lime, the powder Vitruvius had described.\n"
            "Entries: L00.1, L00.2, L00.3\n\n"
            "### The lumps that heal\n"
            "A crack opens, water gets in, and the lime the old accounts called a flaw closes it again.\n"
            "Entries: L00.5, L00.6\n</scout>")


def _fetched():
    return NS(type="web_fetch_tool_result", tool_use_id="f1", content=NS(
        type="web_fetch_result", url=VITRUVIUS_URL,
        content=NS(type="document", title="Vitruvius", source=NS(type="text", media_type="text/plain",
                                                                 data=VITRUVIUS_TEXT))))


def _searched():
    return NS(type="web_search_tool_result", tool_use_id="s1",
              content=[NS(type="web_search_result", url=VITRUVIUS_URL, title="Vitruvius"),
                       NS(type="web_search_result", url=HARBOR_URL, title="Caesarea"),
                       NS(type="web_search_result", url=CLASTS_URL, title="Science Advances")])


def scout_message(kwargs, pause: bool = False, text: str | None = None):
    """The scout's turn: searches and one fetched page, then its block; or, paused at its budget,
    the same material and only a note."""
    if pause:
        return Responder.msg(kwargs, None, stop="pause_turn",
                             content=[_searched(), _fetched(), text_block("Vitruvius has the powder.")],
                             u=usage(searches=6, fetches=3))
    return Responder.msg(kwargs, None, content=[_searched(), _fetched(), text_block(text or scout_block())],
                         u=usage(searches=5, fetches=1))


# --- the outline ----------------------------------------------------------------------------

def outline(n: int = 4) -> dict:
    """A valid outline of n sections (3-5): an opening of 900 words from scout entries D01-D03,
    then sections with one research question each (section 2 has two when n is 3 or 4)."""
    later = {3: [1050, 1050], 4: [700, 700, 700], 5: [525, 525, 525, 525]}[n]
    titles = ["Ash and lime", "The lumps that heal", "What we lost", "The sea as mason"]
    sections = [{
        "n": 1, "title": "Forms in the sea", "lands": "Rome built in open water a harbor we could not build today.",
        "sets_up": "The powder Vitruvius names, which section 2 opens up.",
        "hand_off": "What was in the powder that the sea could not undo?", "target_words": 900,
        "notes": "Open on the forms going down at Caesarea. Within the first minute, tie it to the listener's cracked slabs.",
        "scout_entries": [1, 2, 3], "research_questions": []}]
    for k, words in enumerate(later, 2):
        questions = [{"idea": f"Idea {k}: seawater makes this concrete stronger.",
                      "question": f"What does the drill-core study of Roman marine concrete show, for section {k}?",
                      "type": "deep-dive", "tier_needed": 2, "scout_has": "D05 names the lime lumps."}]
        if k == 2 and n < 5:
            questions.append({"idea": "Pliny saw the same thing and said so.",
                              "question": "What does Pliny, Natural History 35.166, say of the harbor piers?",
                              "type": "missing", "tier_needed": 1, "scout_has": ""})
        sections.append({
            "n": k, "title": titles[k - 2], "lands": f"The idea section {k} lands.",
            "sets_up": f"What section {k} plants.", "hand_off": f"The question section {k} leaves open.",
            "target_words": words, "notes": f"How to tell section {k}.", "scout_entries": [5] if k == 2 else [],
            "research_questions": questions})
    return {"title": "The harbor that heals itself",
            "angle": "Roman concrete lasted because it was built to change, not to resist change.",
            "why": "It answers the listener's own question about cracking, and leaves out the Pantheon.",
            "through_line": "What would it mean to build something that mends?",
            "closing_idea": "The harbor is still setting.",
            "listener_words": ["ours cracks in thirty years"], "sections": sections}


@dataclass
class Script:
    """What area A's fake replies say. An outline of None means the default for that call."""
    scout_pause: bool = False
    scout_text: str | None = None       # the scout's reply text, in place of scout_block()
    writeup_text: str | None = None     # the write-up's reply text
    match: str = "0"                    # the early-scout match call's answer
    first: dict | None = None           # the outline call's outline
    edited: dict | None = None          # the edit call's outline
    changes: list[str] = field(default_factory=lambda: ["Moved the lime lumps before the loss, so the turn "
                                                        "comes in section 2."])
    repaired: dict | None = None        # the repair call's outline
    sections: int = 4


def reply(kwargs, script: Script | None = None):
    """The message for one of area A's prompts, or None if the request is not one of them."""
    s, t = script or Script(), all_text(kwargs)
    if WRITEUP_PHRASE in t:
        return Responder.msg(kwargs, s.writeup_text or scout_block())
    if SCOUT_PHRASE in t:
        return scout_message(kwargs, pause=s.scout_pause, text=s.scout_text)
    if MATCH_PHRASE in t:
        return Responder.msg(kwargs, s.match)
    if REPAIR_PHRASE in t:
        return Responder.msg(kwargs, json.dumps(s.repaired or outline(s.sections)))
    if EDIT_PHRASE in t:
        return Responder.msg(kwargs, json.dumps({"changes": s.changes,
                                                 "outline": s.edited or s.first or outline(s.sections)}))
    if OUTLINE_PHRASE in t:
        return Responder.msg(kwargs, json.dumps(s.first or outline(s.sections)))
    return None


class AreaA(Responder):
    """Area A's prompts first, keyed off a phrase in each; everything else as the stock fake."""

    def __init__(self, script: Script | None = None, **kw):
        super().__init__(**kw)
        self.script = script or Script()

    def __call__(self, kwargs):
        msg = reply(kwargs, self.script)
        if msg is None:
            return super().__call__(kwargs)
        self.calls.append(kwargs)
        return msg

    def with_phrase(self, phrase: str) -> list[dict]:
        return [c for c in self.calls if phrase in all_text(c)]


# --- run folders and the dossier --------------------------------------------------------------

def seed_run(tmp_path, cfg, name: str = "fast-run", topic: str = TOPIC, said: str = SAID) -> Run:
    """An unattended seed run in a temp folder, with the listener's memory pointed away from the real one."""
    from stages import seed

    cfg["listener_dir"] = str(tmp_path / "listener")
    r = Run.open(name, create=True, runs_dir=tmp_path / "runs")
    r.unattended, r.max_usd = True, 8.0
    seed.write(r, topic=topic, said=said, why="It is my trade.")
    return r


class StubDossier:
    """What the scout and the outline need of the dossier: a row's entries numbered D01... in file
    order (a number used twice keeps its first entry), checked the way the real one checks them
    (a verbatim quote that is on no fetched page is downgraded). It has no refs(), so the scout
    works the numbering out from add_row's list."""

    def __init__(self, r: Run, cfg: dict):
        self.r, self.cfg, self.rows = r, cfg, {}

    def has(self, row_id: str) -> bool:
        return row_id in self.rows

    def numbers_of(self, row_id: str) -> list[int]:
        return [e["d"] for e in self.rows.get(row_id, [])]

    def entries(self) -> dict[int, str]:
        return {e["d"]: f"[{entry_id(e['d'])}] {format_tags(e['tags'])} {e['body']}"
                for row in self.rows.values() for e in row}

    def add_row(self, row_id: str) -> list[int]:
        if row_id not in self.rows:
            md, src = research.row_paths(self.r, row_id)
            nxt, refs, entries = len(self.entries()) + 1, {}, []
            for e in research.parse_entries(md.read_text(encoding="utf-8")):
                if e["n"] in refs:
                    continue
                refs[e["n"]] = nxt + len(entries)
                e["tags"]["rows"] = [row_id]
                entries.append({"d": refs[e["n"]], "tags": e["tags"], "body": e["body"], "tagged": e["tagged"]})
            for e in entries:  # 'conflicts with L00.5' -> 'conflicts with D05'
                e["body"] = " ".join(research._renumber(e["body"], int(row_id[1:]), refs).split())
            research.check_entries(self.cfg, entries, json.loads(src.read_text(encoding="utf-8")), "")
            self.rows[row_id] = entries
        return self.numbers_of(row_id)


def dossiers() -> list[str]:
    try:
        import faststart.dossier  # noqa: F401
    except ImportError:
        return ["stub"]
    return ["stub", "real"]


def make_dossier(kind: str, r: Run, cfg: dict):
    if kind == "real":
        from faststart.dossier import Dossier

        return Dossier(r, cfg)
    return StubDossier(r, cfg)


def file_scout(r: Run, cfg: dict, dossier) -> list[int]:
    """The scout's files written straight to the run folder, as faststart.scout.run leaves them,
    without a model call: for tests that start at the outline."""
    from faststart import base, scout

    md, src = research.row_paths(r, base.SCOUT_ROW)
    block = research.extract_tagged(scout_block(), "scout")
    record = {"urls": {VITRUVIUS_URL: "Vitruvius", HARBOR_URL: "Caesarea", CLASTS_URL: "Science Advances"},
              "fetched": [{"url": VITRUVIUS_URL, "title": "Vitruvius", "text": VITRUVIUS_TEXT}], "failed": [],
              "searches": 5, "fetches": 1, "budget": {"searches": 6, "fetches": 3}, "written_up": False,
              "cost_usd": 0.0}
    base.write_json(r.path("scout_sources"), record)
    base.write_json(src, {**record, "row": base.SCOUT_ROW, "followup": False, "fingerprint": "scout"})
    base.write_atomic(md, block)
    scout.run(r, cfg, dossier)
    return dossier.numbers_of(base.SCOUT_ROW)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No test here may build a live client: a real .env with keys sits beside the code."""
    import anthropic
    import elevenlabs.client

    def live(*a, **k):
        raise AssertionError("a test reached a live API client")

    monkeypatch.setattr(anthropic, "Anthropic", live)
    monkeypatch.setattr(elevenlabs.client, "ElevenLabs", live)


def clone(o):
    return copy.deepcopy(o)
