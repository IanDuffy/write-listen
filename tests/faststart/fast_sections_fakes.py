"""Fake replies for area C of Fast Start (faststart/sections.py): the opening, its edit, a draft,
a lock and a fix, plus a small outline, ledger and dossier to run them against.

Replies are keyed by the prompt (the last text block of the request), never by call order, so
they chain with the other areas' fakes: `reply(kwargs)` answers any of these prompts and returns
None for anything else. The stock replies cite only entries the request offers, so they pass the
code checks against whatever dossier the caller built.
"""

from __future__ import annotations

import json
import re

from fakes import FakeAnthropic, Responder

from stages import llm
from stages.common import dossier_entries, entry_id, entry_tags

FILLER = "This sentence is throat-clearing that the editor of the section will cut."

SEED = "# Seed\n\nWhy did Roman concrete last? I pour concrete for a living and ours cracks in thirty years.\n"

OUTLINE = {
    "title": "The wall that grew",
    "angle": "Roman harbour concrete lasted because the sea kept building it.",
    "why": "The listener pours concrete; the surprise is that the Romans' enemy was their ingredient.",
    "through_line": "What we call decay was, for this material, a way of growing.",
    "closing_idea": "A wall that is still being made, two thousand years on.",
    "listener_words": ["I pour concrete for a living", "ours cracks in thirty years"],
    "sections": [
        {"n": 1, "title": "A harbour from nothing", "lands": "The harbour at Caesarea and the question of why it stands.",
         "sets_up": "What was in the mix.", "hand_off": "The answer starts with what they poured.",
         "target_words": 950, "notes": "Open on the barges at Caesarea; hold the chemistry back.",
         "scout_entries": [1, 2, 3], "research_questions": []},
        {"n": 2, "title": "Ash from the bay", "lands": "The recipe and where the ash came from.",
         "sets_up": "Why seawater mattered.", "hand_off": "The mix alone does not explain it.",
         "target_words": 500, "notes": "Start at the quarry.", "scout_entries": [3],
         "research_questions": [{"idea": "The recipe", "question": "What went into the mix?", "type": "deep-dive",
                                 "tier_needed": 2, "scout_has": "lime, ash, seawater"}]},
        {"n": 3, "title": "The sea gets in", "lands": "Seawater grows new crystals inside the wall.",
         "sets_up": "What that means for cracks.", "hand_off": "A crack is where it starts again.",
         "target_words": 500, "notes": "Tell it through one drilled core.", "scout_entries": [],
         "research_questions": [{"idea": "Crystals", "question": "What grows in the wall?", "type": "deep-dive",
                                 "tier_needed": 2, "scout_has": ""}]},
        {"n": 4, "title": "Cracks that close", "lands": "Lime clasts heal cracks.",
         "sets_up": "Why ours does not.", "hand_off": "So what did we lose?",
         "target_words": 550, "notes": "The broken sample that sealed itself.", "scout_entries": [],
         "research_questions": [{"idea": "Self-healing", "question": "How do cracks close?", "type": "deep-dive",
                                 "tier_needed": 2, "scout_has": ""}]},
        {"n": 5, "title": "Thirty years", "lands": "Modern concrete is built to be finished; theirs was not.",
         "sets_up": "", "hand_off": "End on the wall that is still being made.",
         "target_words": 500, "notes": "Come back to the listener's own pour.", "scout_entries": [2],
         "research_questions": [{"idea": "Modern concrete", "question": "Why does ours crack?", "type": "missing",
                                 "tier_needed": 2, "scout_has": ""}]},
    ],
}


def ledger_rows() -> dict:
    rows = []
    for i, s in enumerate(OUTLINE["sections"][1:], 1):
        q = s["research_questions"][0]
        rows.append({"id": f"L{i:02d}", "rank": i, "idea": q["idea"], "type": q["type"], "episode_has": q["scout_has"],
                     "research_question": q["question"], "tier_needed": q["tier_needed"], "section": s["n"]})
    return {"run": "test-run", "source": "seed", "rows": rows, "parked": [], "connections": []}


DOSSIER = """# Dossier: The wall that grew

## Entries

### L00: the scout

[D01] (L00, material, tier 2) Herod's builders sank concrete into open sea at Caesarea to make a harbour where the coast had none. Source: https://example.org/caesarea

[D02] (L00, quote, tier 1, verbatim: yes) "The sea itself would not dissolve it." A Roman writer on harbour concrete. Source: https://example.org/pliny

[D03] (L00, claim, tier 2) The marine mix was lime, volcanic ash and seawater. Source: https://example.org/ash

[D04] (L00, quote, tier 3, verbatim: no) "It gets stronger every single year it sits there." A geologist, in an interview. Source: https://example.org/interview

### L01: The recipe

[D05] (L01, claim, tier 2) The ash was quarried on the bay near Pozzuoli and shipped across the sea. Source: https://example.org/pozzuoli

[D06] (L01, material, tier 2) A drilled core from the harbour wall still shows the lumps of ash in it. Source: https://example.org/core

### L02: Crystals

[D07] (L02, claim, tier 1) Seawater moving through the wall grew a rare mineral in its pores. Source: https://example.org/mineral

[D08] (L02, quote, tier 1, verbatim: yes) "As soon as the waves touch it, it becomes a single mass of stone." A Roman writer on the harbour mix. Source: https://example.org/mass

### L03: Self-healing

[D09] (L03, material, tier 2) A cracked sample left under running water sealed itself within weeks. Source: https://example.org/sample

[D10] (L03, claim, tier 2) White lumps of lime in the mix dissolve into a crack and fill it. Source: https://example.org/clasts

### L04: Modern concrete

[D11] (L04, claim, tier 2) Steel inside modern concrete rusts, swells and breaks it from within. Source: https://example.org/steel

## Sources cited

- https://example.org/caesarea
"""


class FakeDossier:
    """A stand-in for faststart.dossier.Dossier with the contract's read methods, over a dossier
    text. `thin_rows` gives a row's reason for being thin; `add` lands another entry."""

    def __init__(self, text: str = DOSSIER, thin_rows: dict | None = None):
        self._text, self.thin_rows = text, dict(thin_rows or {})

    def text(self) -> str:
        return self._text

    def entries(self) -> dict[int, str]:
        return dossier_entries(self._text)

    def numbers_of(self, row_id: str) -> list[int]:
        return [n for n, t in self.entries().items() if row_id in entry_tags(t)["rows"]]

    def for_rows(self, row_ids: list[str]) -> dict[int, str]:
        return {n: t for n, t in self.entries().items() if set(entry_tags(t)["rows"]) & set(row_ids)}

    def has(self, row_id: str) -> bool:
        return bool(self.numbers_of(row_id))

    def thin(self, row: dict) -> str | None:
        if not self.has(row["id"]):
            return "no findings"
        return self.thin_rows.get(row["id"])

    def add(self, line: str) -> None:
        self._text = self._text.replace("## Sources cited", f"{line}\n\n## Sources cited")


# --- reading a request ----------------------------------------------------------------------

def last_block(kwargs) -> str:
    """The request's own block: the last text block of the user turn (the earlier sections are
    separate blocks before it)."""
    content = kwargs["messages"][-1]["content"]
    return content if isinstance(content, str) else content[-1]["text"]


def blocks(kwargs) -> list[dict]:
    return kwargs["messages"][-1]["content"]


def tag(text: str, name: str, attr: str = "") -> str:
    """The inside of the first <name ...attr...> block of a prompt."""
    m = re.search(rf"<{name}\b[^>]*{re.escape(attr)}[^>]*>\n(.*?)\n</{name}>", text, re.S)
    return m.group(1).strip() if m else ""


def offered(text: str, name: str = "entries") -> list[int]:
    return sorted(dossier_entries(tag(text, name)))


def kind(kwargs) -> tuple[str, int] | None:
    """Which of area C's prompts a request is, and its section: ("opening", 1), ("opening_edit", 1),
    ("draft", n), ("lock", n) or ("fix", n). None for any other prompt."""
    content = kwargs["messages"][-1]["content"]
    if isinstance(content, str):
        return None
    text = content[-1]["text"]
    if text.startswith("An editor has done the job above.") and len(content) > 1:
        base = kind({"messages": [{"role": "user", "content": content[:-1]}]})
        return ("fix", base[1]) if base else None
    if text.startswith("Write section 1 of") and "the opening" in text[:80]:
        return "opening", 1
    if text.startswith("Edit section 1 of"):
        return "opening_edit", 1
    if m := re.match(r"Write section (\d+) of \d+", text):
        return "draft", int(m.group(1))
    if m := re.match(r"Lock section (\d+) of \d+", text):
        return "lock", int(m.group(1))
    return None


# --- the stock replies ----------------------------------------------------------------------

def _marks(nums: list[int], k: int) -> str:
    return f" [{entry_id(nums[k % len(nums)])}]" if nums else ""


def opening_text(nums: list[int]) -> str:
    return "\n\n".join([
        "A barge rides low off a coast with no harbour, and the men aboard are about to pour a wall into the sea."
        + _marks(nums, 0),
        "You pour concrete for a living, and you asked why theirs lasted when ours cracks in thirty years. "
        "That is the question this chapter follows from the first stone to the last.",
        f"{FILLER} The builders mixed what they had carried across the water and let it sink where the waves broke."
        + _marks(nums, 1),
        "[pause]",
        "So the opening rests here, on a wall going into the water and a question about what was in the mix.",
    ])


def section_text(n: int, nums: list[int]) -> str:
    return "\n\n".join([
        f"Section {n} picks up the thread where the one before it stopped and carries the question further."
        + _marks(nums, 0),
        f"{FILLER} In section {n} the evidence arrives as something a person could hold in the hand and turn over."
        + _marks(nums, 1),
        f"And section {n} comes to rest on its own hand-off, a thought landed and a question left standing.",
    ])


def without_filler(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text.replace(FILLER + " ", "").replace(FILLER, "")).strip()


def lock_reply(kwargs, **changes) -> str:
    """The stock editor: cuts the filler sentence from section n and declares it, leaves the next
    draft alone, and finds every listed row covered. `changes` overrides fields of the JSON."""
    text = last_block(kwargs)
    n = kind(kwargs)[1]
    draft = tag(text, "draft", f'n="{n}"')
    out = {"text": without_filler(draft), "next_text": "", "moved": []}
    out.update(rows=[{"id": rid, "coverage": "covered", "note": "Lands with its material."}
                     for rid in re.findall(r"^- (L\d+)", tag(text, "rows"), re.M)],
               cut=[{"text": FILLER, "reason": "throat-clearing"}] if FILLER in draft else [],
               title="", corrections=[])
    out.update(changes)
    return json.dumps(out)


def reply(kwargs) -> str | None:
    """The stock reply to any of area C's prompts, or None if the request is not one of them."""
    k = kind(kwargs)
    if k is None:
        return None
    what, n = k
    text = last_block(kwargs)
    if what == "opening":
        return opening_text(offered(text))
    if what == "draft":
        return section_text(n, offered(text) or offered(text, "other_entries"))
    if what == "opening_edit":
        return without_filler(tag(text, "draft"))
    if what == "lock":
        return lock_reply(kwargs)
    return tag(text, "returned")  # a fix that changes nothing; tests script a real one


class Fake:
    """A responder for FakeAnthropic: area C's prompts first (`on` scripts a kind, or a kind and a
    section), everything else as the stock Responder. `calls` holds every request, as Responder's does."""

    def __init__(self, fallback=None):
        self.calls, self.script, self.fallback = [], {}, fallback or Responder()

    def on(self, what: str, fn, n: int | None = None) -> "Fake":
        self.script[(what, n)] = fn
        return self

    def of(self, what: str, n: int | None = None) -> list[dict]:
        return [c for c in self.calls if (k := kind(c)) and k[0] == what and (n is None or k[1] == n)]

    def __call__(self, kwargs):
        k = kind(kwargs)
        if k is None:
            return self.fallback(kwargs)
        self.calls.append(kwargs)
        fn = self.script.get(k) or self.script.get((k[0], None))
        out = fn(kwargs) if fn else reply(kwargs)
        return Responder.msg(kwargs, out) if isinstance(out, str) else out


def install(monkeypatch, fallback=None) -> Fake:
    fake = Fake(fallback)
    monkeypatch.setattr(llm, "_client", FakeAnthropic(fake))
    return fake
