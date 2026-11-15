"""Fakes for the overnight writing chain (overnight/outline.py, write.py, promise.py): a chapter folder
as the research stage leaves it, and one responder for every prompt of stages 3 and 4.

    chapter(tmp_path, cfg, sections=5)   a chapter folder with the card, the map, the angle, a ledger
                                         judged by 04b and the dossier; returns (Run, cfg) with the run
                                         config applied as the runner does
    Chain(sections=5)                    answers the outline, its edit and repair, every draft, lock and
                                         fix (tests/faststart/fast_sections_fakes.py's stock replies), the
                                         promise check and the revisions; records ((what, key), request)
    chain.on(what, key, fn)              fn(kwargs) runs before that reply; what it returns, if anything,
                                         is the reply
    install(monkeypatch, **chain)        the Chain as the Claude client
    Killed                               raise it from a hook to stop a build the way a kill would
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "faststart"))

import fast_sections_fakes as C  # noqa: E402
from fakes import FakeAnthropic, Responder, all_text  # noqa: E402

from stages import common, llm  # noqa: E402
from stages.common import Run  # noqa: E402

OUTLINE_PHRASE = "You are the commissioning editor for one narrated audio chapter"
EDIT_PHRASE = "You are the second editor on one narrated audio chapter"
REPAIR_PHRASE = "This is the outline of a narrated audio chapter made overnight"
PROMISE_PHRASE = "whether it is the chapter the card promised"
REVISE = re.compile(r"^Revise section (\d+) of \d+", re.M)
ADDED = "This is the line the revision added so the promise is kept."


class Killed(Exception):
    """A build stopped mid-call (a kill, a crash): not an API error, so nothing retries it."""


CARD = {"id": "plan-20261116-180012", "for_morning": "2026-11-17", "title": "The Wall That Grew",
        "overview": "Why Roman harbour concrete outlasted ours.",
        "summary": "Why did Roman sea walls last two thousand years when modern concrete cracks in thirty? "
                   "The chapter argues the sea itself kept building them. It follows from the listener's "
                   "own work pouring concrete.",
        "mode": "topic", "decided": "topic",
        "source": {"kind": "request", "ref": "s-1", "words": "I pour concrete for a living and ours cracks"},
        "research_questions": ["What went into the mix?"], "angle": "The sea was an ingredient, not an enemy.",
        "status": "building", "chapter": "2026-11-17-the-wall-that-grew"}

ROWS = [("L01", "The recipe", "filled", ""), ("L02", "Crystals in the wall", "filled", ""),
        ("L03", "Cracks that close", "partial", "no tier 1 source for the healing rate"),
        ("L04", "Why modern concrete cracks", "open", "nothing beyond one engineering page")]

DOSSIER = """# Dossier: The Wall That Grew

## Entries

### L01: The recipe

[D01] (L01, material, tier 2) Herod's builders sank concrete into open sea at Caesarea to make a harbour where the coast had none. Source: https://example.org/caesarea

[D02] (L01, quote, tier 1, verbatim: yes) "The sea itself would not dissolve it." A Roman writer on harbour concrete. Source: https://example.org/pliny

[D03] (L01, claim, tier 2) The marine mix was lime, volcanic ash and seawater. Source: https://example.org/ash

### L02: Crystals in the wall

[D04] (L02, claim, tier 1) Seawater moving through the wall grew a rare mineral in its pores. Source: https://example.org/mineral

[D05] (L02, material, tier 2) A drilled core from the harbour wall still shows the lumps of ash in it. Source: https://example.org/core

### L03: Cracks that close

[D06] (L03, material, tier 2) A cracked sample left under running water sealed itself within weeks. Source: https://example.org/sample

[D07] (L03, claim, tier 2) White lumps of lime in the mix dissolve into a crack and fill it. Source: https://example.org/clasts

### L04: Why modern concrete cracks

[D08] (L04, claim, tier 2) Steel inside modern concrete rusts, swells and breaks it from within. Source: https://example.org/steel

## Sources cited

- https://example.org/caesarea
"""

# Which rows and entries each section of the stock outline takes, by section count.
PLANS = {
    4: [([], [1, 2]), (["L01", "L02"], [3, 4, 5]), (["L03"], [6, 7]), (["L04"], [8])],
    5: [([], [1, 2]), (["L01"], [3]), (["L02"], [4, 5]), (["L03"], [6, 7]), (["L04"], [8])],
    6: [([], [1]), (["L01"], [2, 3]), (["L02"], [4]), ([], [5]), (["L03"], [6, 7]), (["L04"], [8])],
}


def outline(sections: int = 5, words: int = 3000) -> dict:
    """The outline the fake model returns: valid for the fixture at the overnight sizes."""
    each = words // sections // 10 * 10
    out = {"angle": "Roman harbour concrete lasted because the sea kept building it.",
           "why": "The listener pours concrete; the surprise is that the Romans' enemy was their ingredient.",
           "through_line": "What we call decay was, for this material, a way of growing.",
           "closing_idea": "A wall that is still being made, two thousand years on.",
           "listener_words": ["I pour concrete for a living"], "sections": []}
    for n, (rows, entries) in enumerate(PLANS[sections], 1):
        out["sections"].append({"n": n, "title": f"Part {n} of the wall", "lands": f"Section {n}'s idea.",
                                "sets_up": f"What section {n} plants.", "hand_off": f"The question section {n} leaves.",
                                "target_words": each + (words - each * sections if n == 1 else 0),
                                "notes": f"How to tell section {n}.", "rows": rows, "entries": entries})
    return out


def chapter(tmp_path, cfg: dict, name: str = "2026-11-17-the-wall-that-grew") -> tuple[Run, dict]:
    """A chapter folder as the research stage leaves it (DESIGN.md stage 2's files), as an unattended
    Run with a runaway stop; the config the runner would give its stages."""
    root = tmp_path / "listener" / "chapters" / name
    (root / "00_source").mkdir(parents=True)
    (root / "04_research").mkdir()
    (root / "00_source" / "up_next.json").write_text(json.dumps(CARD), encoding="utf-8")
    (root / "00_source" / "seed.md").write_text(
        f"# {CARD['title']}\n\n{CARD['summary']}\n\nIn their words: {CARD['source']['words']}\n", encoding="utf-8")
    (root / "01_map.md").write_text("# Map\n\nThe territory: Roman marine concrete, Caesarea, modern rebar.\n",
                                    encoding="utf-8")
    (root / "03_angle.md").write_text(f"# Angle: {CARD['title']}\n\n{CARD['angle']}\n", encoding="utf-8")
    led = {"run": name, "source": "seed", "parked": ["Roman roads"], "connections": [], "rows": [
        {"id": rid, "rank": k, "idea": idea, "type": "deep-dive", "episode_has": "",
         "research_question": f"What does the research say about {idea.lower()}?", "tier_needed": 2,
         "status": status, "gap": gap, "dossier_refs": [], "best_tier": 2, "followup": False,
         "coverage": None, "segments": [], "note": ""}
        for k, (rid, idea, status, gap) in enumerate(ROWS, 1)]}
    (root / "ledger.json").write_text(json.dumps(led, indent=2), encoding="utf-8")
    (root / "04_dossier.md").write_text(DOSSIER, encoding="utf-8")
    (root / "04_research" / "ledger_update.md").write_text("# Ledger update\n", encoding="utf-8")
    r = Run(name=name, root=root, unattended=True, max_usd=50.0)
    return r, common.run_config(r, cfg)


def promise_reply(keeps: bool = True, fixes: dict | None = None) -> str:
    fixes = fixes or {}
    return json.dumps({"keeps": keeps,
                       "points": [{"point": "the question", "kept": True, "where": "section 1 asks it"},
                                  {"point": "the listener's work", "kept": keeps, "where": "section 5"}],
                       "drift": [] if keeps else ["It never ties back to the listener's own work."],
                       "sections": [{"n": n, "fix": f} for n, f in fixes.items()]})


def revise_reply(kwargs) -> str:
    """The stock revision: the section as it stood, plus one line citing an entry it offered."""
    text = kwargs["messages"][-1]["content"]
    old = C.tag(text, "section")
    first = sorted(C.offered(text))[0]
    return f"{old}\n\n{ADDED} [D{first:02d}]"


class Chain:
    """One responder for every prompt of the writing chain, keyed by what the prompt is."""

    def __init__(self, sections: int = 5):
        self.sections = sections
        self.outline = outline(sections)
        self.promise = promise_reply()
        self.calls: list[tuple[tuple, dict]] = []
        self.hooks: dict[tuple, object] = {}

    def on(self, what: str, key, fn) -> "Chain":
        self.hooks[(what, key)] = fn
        return self

    def of(self, what: str, key=None) -> list[dict]:
        return [kw for (w, k), kw in self.calls if w == what and (key is None or k == key)]

    def whats(self) -> list[tuple]:
        return [k for k, _ in self.calls]

    @staticmethod
    def kind(kwargs) -> tuple:
        content = kwargs["messages"][-1]["content"]
        if isinstance(content, str):
            if m := REVISE.search(content):
                return "revise", int(m.group(1))
            for phrase, what in ((OUTLINE_PHRASE, "outline"), (EDIT_PHRASE, "outline_edit"),
                                 (REPAIR_PHRASE, "outline_repair"), (PROMISE_PHRASE, "promise")):
                if phrase in content:
                    return what, None
        if (k := C.kind(kwargs)) is not None:
            return k
        return "unknown", None

    def __call__(self, kwargs):
        k = self.kind(kwargs)
        self.calls.append((k, kwargs))
        hook = self.hooks.get(k) or self.hooks.get((k[0], None))
        out = hook(kwargs) if hook else None
        if out is None:
            out = self.reply(k, kwargs)
        return Responder.msg(kwargs, out) if isinstance(out, str) else out

    def reply(self, k: tuple, kwargs) -> str:
        what = k[0]
        if what in ("outline", "outline_repair"):
            return json.dumps(self.outline)
        if what == "outline_edit":
            return json.dumps({"changes": ["Gave the turn to section 3."], "outline": self.outline})
        if what == "promise":
            return self.promise
        if what == "revise":
            return revise_reply(kwargs)
        if (out := C.reply(kwargs)) is not None:
            return out
        raise AssertionError("fake API got an unexpected prompt: " + all_text(kwargs)[:300])


def install(monkeypatch, sections: int = 5) -> Chain:
    chain = Chain(sections)
    monkeypatch.setattr(llm, "_client", FakeAnthropic(chain))
    return chain


@pytest.fixture(autouse=True, scope="session")
def offline():
    """No test may build a live client: a real .env with keys sits beside the code."""
    import anthropic
    import elevenlabs.client

    def live(*a, **k):
        raise AssertionError("a test reached a live API client")

    mp = pytest.MonkeyPatch()
    mp.setattr(anthropic, "Anthropic", live)
    mp.setattr(elevenlabs.client, "ElevenLabs", live)
    yield
    mp.undo()
