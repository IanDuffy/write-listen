"""Fakes for Fast Start narration and the archive: a voice in tts.synthesize's shape (real PCM, real
lengths, ElevenLabs-shaped character timings, no client), three short locked sections, and a run
folder laid out the way the other areas leave it for narration. Plain functions, for any test."""

import json
import math
import struct

from stages import tts

SECONDS_PER_CHAR = 0.01


def alignment(text: str, seconds: float) -> dict:
    """Character timings as ElevenLabs returns them with a piece: one entry per character of the text
    sent, seconds from the start of the audio. Here every character takes the same time."""
    step = seconds / max(1, len(text))
    return {"characters": list(text),
            "character_start_times_seconds": [round(i * step, 4) for i in range(len(text))],
            "character_end_times_seconds": [round((i + 1) * step, 4) for i in range(len(text))]}


def tone(samples: int, rate: int) -> bytes:
    """PCM s16le mono, a quiet tone whose period is a whole number of samples (so it is cheap to make)."""
    period = max(2, rate // 240)
    one = b"".join(struct.pack("<h", int(800 * math.sin(2 * math.pi * i / period))) for i in range(period))
    return (one * (samples // period + 1))[:samples * 2]


class FakeVoice:
    """Stands in for tts.synthesize (pass it to Narrator, or patch tts.synthesize with it). Records every
    call; request ids count up (req_001, ...); 10 ms of tone per character, so lengths are real and small.
    alignments=False is a voice that returns no character timings; bare=(5,) one whose fifth call alone comes
    back without them; ids=False one that returns no request id; fail(call_number) may return an exception to
    raise for that call."""

    def __init__(self, seconds_per_char: float = SECONDS_PER_CHAR, alignments: bool = True, ids: bool = True,
                 fail=None, bare=()):
        self.seconds_per_char, self.alignments, self.ids, self.fail = seconds_per_char, alignments, ids, fail
        self.bare = set(bare)
        self.calls: list[dict] = []

    def __call__(self, text, *, voice_id, ncfg, previous_request_ids=None, previous_text=None, next_text=None,
                 timestamps=False):
        self.calls.append({"text": text, "voice_id": voice_id, "previous_request_ids": previous_request_ids,
                           "previous_text": previous_text, "next_text": next_text, "timestamps": timestamps})
        if self.fail and (err := self.fail(len(self.calls))):
            raise err
        rate = int(ncfg["output_format"].split("_")[1])
        samples = int(rate * len(text) * self.seconds_per_char)
        return tts.Synth(audio=tone(samples, rate), request_id=f"req_{len(self.calls):03d}" if self.ids else None,
                         alignment=alignment(text, samples / rate)
                         if timestamps and self.alignments and len(self.calls) not in self.bare else None)

    @property
    def texts(self) -> list[str]:
        return [c["text"] for c in self.calls]


# --- three locked sections (what areas A-C leave behind) --------------------------------------------

SECTIONS = {
    1: """Picture a harbour wall at Caesarea, two thousand years old, still standing in the surf. [D01]

A modern sea wall beside it would be rubble in fifty years. The Roman one has spent twenty centuries getting stronger, and for most of that time nobody could say why. [D02]

[pause]

So here is the question we are going to follow. What did Roman builders know about concrete that we forgot? [D01, D03]

Pliny wrote that "[t]he waves make it a single stone mass." [D04] He was closer than he knew.""",
    2: """Start with the recipe, because the recipe is where the argument begins. [D05]

Vitruvius gives it plainly: lime, volcanic ash from the bay near Naples, and seawater. Nothing in that list is exotic. [D05, D06]

What is strange is the lumps. Roman concrete is full of small white clasts of lime, and for a century engineers read them as sloppy mixing. [D07]

[pause]

They were not sloppy. They were the point.""",
    3: """Here is what the lumps do. [D08] When a crack opens and water gets in, the lime dissolves, travels, and sets again as new stone inside the crack. [D08]

The wall heals. Boethius, centuries later, would have called that a kind of constancy: a thing that stays itself by changing. [D09]

So the harbour wall we began with is not surviving the sea. It is being rebuilt by it, a little, every tide. [D01]""",
}

TITLES = {1: "The wall in the surf", 2: "The recipe and the lumps", 3: "A wall that heals"}

DOSSIER = "# Dossier: The Wall That Heals\n\n## Entries\n\n" + "\n\n".join(
    f"[D{k:02d}] ({'L00' if k < 5 else ('L01' if k < 8 else 'L02')}, claim, tier 2, verbatim: "
    f"{'yes' if k == 4 else 'no'}) "
    + ("Pliny: \"The waves make it a single stone mass.\"" if k == 4 else f"Finding {k} about Roman marine concrete.")
    + f" Source: Example {k}, https://example.org/{k}" for k in range(1, 10)) + "\n"


def outline(sections=SECTIONS) -> dict:
    """An outline in the contract's JSON shape, for these sections."""
    return {"title": "The Wall That Heals", "angle": "Roman concrete repairs itself.", "why": "It is a good story.",
            "through_line": "A thing can last by changing.", "closing_idea": "The sea rebuilds the wall.",
            "listener_words": ["why is it still standing"],
            "sections": [{"n": n, "title": TITLES.get(n, f"Part {n}"), "lands": "x", "sets_up": "y", "hand_off": "z",
                          "target_words": 1000, "notes": "", "scout_entries": [1, 2, 3, 4] if n == 1 else [],
                          "research_questions": []} for n in sorted(sections)]}


LEDGER = {"rows": [{"id": "L01", "rank": 1, "idea": "The lumps.", "type": "deep-dive", "episode_has": "",
                    "research_question": "What are the lime clasts for?", "tier_needed": 2, "section": 2},
                   {"id": "L02", "rank": 2, "idea": "Self-healing.", "type": "deep-dive", "episode_has": "",
                    "research_question": "How does a crack close?", "tier_needed": 2, "section": 3}],
          "parked": [], "connections": []}


def lay_out(r, sections=SECTIONS) -> None:
    """The run folder as it stands when narration starts: the outline, the dossier, the locked sections.
    The brief is area A's own render_brief, as the outline step leaves it."""
    from faststart.outline import render_brief

    (r.root / "05_outline.json").write_text(json.dumps(outline(sections)), encoding="utf-8")
    (r.root / "03_angle.md").write_text("# Angle: The Wall That Heals\n\nRoman concrete repairs itself.\n", encoding="utf-8")
    (r.root / "04_dossier.md").write_text(DOSSIER, encoding="utf-8")
    (r.root / "ledger.json").write_text(json.dumps(LEDGER), encoding="utf-8")
    (r.root / "05_brief.md").write_text(render_brief(outline(sections), LEDGER), encoding="utf-8")
    r.path("segments_dir").mkdir(exist_ok=True)
    for n, text in sections.items():
        r.segment_path(n).write_text(text + "\n", encoding="utf-8")


def narrate_all(r, cfg, voice=None, sections=SECTIONS, only=None, on_piece=None):
    """Narrate the locked sections in order with a FakeVoice. Returns (narrator, voice)."""
    from faststart import narrate

    voice = voice or FakeVoice()
    narrator = narrate.Narrator(r, cfg, synth=voice, sleep=lambda s: None)
    for n in sorted(sections):
        if only is None or n in only:
            narrator.section(n, sections[n], on_piece=on_piece)
    return narrator, voice
