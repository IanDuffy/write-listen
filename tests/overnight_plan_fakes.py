"""Fakes for the planning pass, the library and the intro brief (overnight/planner.py, library.py, intro.py):
a responder keyed by each prompt's own words, and a small realistic listener folder."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fakes import FakeAnthropic, Responder, all_text, text_block, usage

from overnight import profiles
from stages import llm

CHOOSE = "You plan the next narrated audio chapter for one listener"
SCOUT = "take a quick look at what exists"
CARD = "Write the Up next card"
OPEN_QS = "Pick the questions the chapter itself did not answer"
INTRO = "In the morning a guide will greet a listener live"
MEMORY = "You keep the memory of a guide"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No test may build a live client: a real .env with keys sits beside the code."""
    import anthropic
    import elevenlabs.client

    def live(*a, **k):
        raise AssertionError("a test reached a live API client")

    monkeypatch.setattr(anthropic, "Anthropic", live)
    monkeypatch.setattr(elevenlabs.client, "ElevenLabs", live)


def schema_enum(kwargs) -> list[str]:
    return kwargs["output_config"]["format"]["schema"]["properties"]["decided"]["enum"]


class PlanFake:
    """One responder for every overnight planning, library, intro and memory prompt."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.prefer = "continue"          # what choose decides when it may
        self.source = None                # choose's source, or a default per decision
        self.titles = ["The Ledger of Small Hours"]  # card titles in order; the last repeats
        self.open = [{"n": 1, "question": "Why do herons stand on one leg?"}]
        self.findings = [{"text": "Grey herons can wait motionless for over an hour.", "entry": "D02"},
                         {"text": "A heron's neck has a special vertebra that works like a spring trigger.",
                          "entry": "D03"}]
        self.why = 'You asked "why do they wait so long" during the herons chapter.'
        self.memory = {"library": [{"kind": "work", "title": "H is for Hawk", "creator": "Helen Macdonald",
                                    "note": "read it twice"}],
                       "seeds": [{"kind": "question", "text": "How do falconers train a hawk?",
                                  "words": "how do you even train a hawk"},
                                 {"kind": "idea", "text": "Tides", "words": "something I never said"}]}

    def kinds(self) -> list[str]:
        return [k for k, _ in self.calls]

    def of(self, kind: str) -> list[dict]:
        return [kw for k, kw in self.calls if k == kind]

    def __call__(self, kwargs):
        t = all_text(kwargs)
        msg = Responder.msg
        if CHOOSE in t:
            self.calls.append(("choose", kwargs))
            allowed = schema_enum(kwargs)
            decided = self.prefer if self.prefer in allowed else allowed[0]
            src = self.source or {"continue": {"kind": "chapter", "ref": "", "words": ""},
                                  "branch": {"kind": "seed", "ref": "s-unknown", "words": ""},
                                  "topic": {"kind": "request", "ref": "", "words": ""},
                                  "queue": {"kind": "queue", "ref": "", "words": ""}}[decided]
            return msg(kwargs, json.dumps({"decided": decided, "topic": f"A {decided} topic", "angle": "An angle.",
                                           "why": "Because they asked.", "source": src,
                                           "scout_queries": ["heron patience", "heron hunting study"]}))
        if SCOUT in t:
            self.calls.append(("scout", kwargs))
            searched = NS(type="web_search_tool_result", tool_use_id="s1", content=[
                NS(type="web_search_result", url="https://example.org/herons", title="Herons at rest")])
            return msg(kwargs, None, content=[searched, text_block("Enough material: two field studies.")],
                       u=usage(inp=3000, out=600, searches=3))
        if CARD in t:
            self.calls.append(("card", kwargs))
            n = len(self.of("card"))
            title = self.titles[min(n, len(self.titles)) - 1]
            return msg(kwargs, json.dumps({
                "title": title, "overview": "What the night shift of birds is doing.",
                "summary": "What happens at night? This chapter follows it. It builds on the question you asked.",
                "angle": "The way in is one night.", "research_questions": ["What is known?", " ", "Who studied it?"]}))
        if OPEN_QS in t:
            self.calls.append(("open", kwargs))
            return msg(kwargs, json.dumps({"open": self.open}))
        if INTRO in t:
            self.calls.append(("intro", kwargs))
            return msg(kwargs, json.dumps({"findings": self.findings, "why_for_them": self.why}))
        if MEMORY in t:
            self.calls.append(("memory", kwargs))
            return msg(kwargs, json.dumps({"profile": "# Profile\n- Watches birds.", "guide": "# Wren",
                                           "forget_chapters": [], **self.memory}))
        raise AssertionError("fake API got an unexpected prompt: " + t[:300])


def install(monkeypatch) -> PlanFake:
    fake = PlanFake()
    monkeypatch.setattr(llm, "_client", FakeAnthropic(fake))
    return fake


# --- a small realistic listener folder ----------------------------------------------

HERON_SCRIPT = (
    "<!-- seg01 -->\n\nA grey heron stands in the shallows at dawn and does not move for a very long time, "
    "and the fish beneath it never notice the patience that is about to end their morning. [D01]\n\n"
    "<!-- seg02 -->\n\nThe strike itself takes less than a tenth of a second, faster than the fish can turn "
    "away from the shadow that has been there all along. [D02]\n")


def chapter(listener: Path, folder: str, *, title: str, summary: str | None, decided: str | None,
            parked=(), asides=(), saved=(), refs=(), script: str | None = None, finished: bool = True,
            outline: dict | None = None, notes: str | None = None, words: str | None = None) -> Path:
    root = listener / "chapters" / folder
    (root / "00_source").mkdir(parents=True)
    if summary is not None:
        profiles.write_json(root / "00_source" / "up_next.json", {
            "id": "plan-x", "title": title, "overview": f"About {title}.", "summary": summary, "decided": decided,
            "mode": "surprise", "source": {"kind": "chapter", "ref": None, "words": words}, "status": "ready"})
    else:  # a migrated chapter: no card, only its angle and episode
        (root / "03_angle.md").write_text(f"# Angle: {title}\n\nThe first paragraph of the angle.\n\nMore.\n")
        (root / "11_episode").mkdir()
        profiles.write_json(root / "11_episode" / "episode.json", {"title": title})
    profiles.write_json(root / "ledger.json", {"rows": [], "parked": list(parked)})
    if outline:
        profiles.write_json(root / "05_outline.json", outline)
    if notes:
        (root / "00_source" / "notes.md").write_text(notes)
    if asides or saved:
        (root / "12_asks").mkdir()
        for a in asides:
            profiles.append_jsonl(root / "12_asks" / "asides.jsonl", a)
        if saved:
            (root / "12_asks" / "seeds.md").write_text("# Seeds from asides\n" + "".join(
                f"\n## {topic}\n\nWhy: {why}\n\nFrom an aside at 3:10 (seg01-s004), 2026-11-17 07:40.\n"
                for topic, why in saved))
    if refs:
        (root / "11_episode").mkdir(exist_ok=True)
        profiles.write_json(root / "11_episode" / "references.json", list(refs))
    if script:
        (root / "09_script_final.md").write_text(script)
    if finished:
        (root / "10_audio").mkdir()
        (root / "10_audio" / "chapter.mp3").write_bytes(b"ID3")
    return root


HERON_ASIDE = {"id": "ask-1", "kind": "ask", "segment": 1, "sentence_id": "seg01-s002", "seeds": [
    {"topic": "Herons and fishermen", "why": "he fishes"}], "transcript": [
    {"s": 0.5, "who": "guide", "text": "Yes?"},
    {"s": 1.0, "who": "listener", "text": "Wait, why do they stand on one leg?"},
    {"s": 3.0, "who": "guide", "text": "Mostly to keep warm."},
    {"s": 5.0, "who": "listener", "text": "okay go on"},
    {"s": 6.0, "who": "listener", "text": "I fish there too, I'd love to know what herons and anglers share"},
    {"s": 7.0, "who": "tool", "name": "save_seed", "input": {"topic": "Herons and fishermen", "why": "he fishes"}},
]}


def listener_folder(root: Path, cfg: dict, *, chapters: bool = True) -> Path:
    """listeners/ian with a profile, seeds, a library and (by default) two finished chapters with cards."""
    profiles.create("ian")
    d = profiles.folder("ian")
    profiles.set_active("ian")
    (d / "profile.md").write_text("# Listener profile\n- Fishes on the river most mornings.\n- Likes birds.\n")
    from overnight import library
    library.add_seeds(d, [{"kind": "question", "text": "How do kingfishers see under water?",
                           "words": "how do kingfishers even see the fish"},
                          {"kind": "idea", "text": "River tides", "words": None}], source={"session": "s1"})
    library.add_works(d, [{"kind": "work", "title": "The Peregrine", "creator": "J. A. Baker", "note": "loved it"}])
    if chapters:
        chapter(d, "2026-11-16-why-herons-wait", title="Why Herons Wait",
                summary="Why do herons wait so long? This follows one heron. It came from your river mornings.",
                decided="branch", parked=["How egrets hunt differently", {"idea": "Heron colonies"}])
        chapter(d, "2026-11-17-the-strike", title="The Heron's Strike",
                summary="How a heron strikes. This follows the strike. It follows the herons chapter.",
                decided="continue", parked=["Fish that see the shadow"], asides=[HERON_ASIDE],
                saved=[("Herons and fishermen", "he fishes")], script=HERON_SCRIPT,
                refs=[{"id": "ref001", "kind": "work", "title": "The Heron", "creator": "Ted Hughes", "note": ""},
                      {"id": "ref002", "kind": "person", "title": "Konrad Lorenz", "creator": None, "note": "ethologist"},
                      {"id": "ref003", "kind": "source", "title": "A field study", "url": "https://example.org"}],
                outline={"through_line": "Patience is a hunting tool.", "closing_idea": "The wait is the work.",
                         "sections": [{"n": 1, "title": "Dawn", "lands": "the heron's stillness"},
                                      {"n": 2, "title": "Strike", "lands": "the strike's speed"}]},
                notes="He said: more about the fish, please.", words="why do they wait so long")
    return d
