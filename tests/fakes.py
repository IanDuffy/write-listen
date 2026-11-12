"""Offline stand-ins for the Claude and ElevenLabs APIs, and a tiny PDF writer."""

from __future__ import annotations

import json
import math
import re
import struct
from types import SimpleNamespace as NS


def usage(inp=1000, out=500, cache_read=0, cache_write=0, searches=0, fetches=0):
    return NS(input_tokens=inp, output_tokens=out, cache_read_input_tokens=cache_read,
              cache_creation_input_tokens=cache_write, cache_creation=None,
              server_tool_use=NS(web_search_requests=searches, web_fetch_requests=fetches),
              iterations=None)


def text_block(t):
    return NS(type="text", text=t, citations=None)


def all_text(kwargs) -> str:
    parts = []
    for b in kwargs.get("system") or []:
        parts.append(b["text"])
    for m in kwargs["messages"]:
        c = m["content"]
        if isinstance(c, str):
            parts.append(c)
        else:
            for b in c:
                if isinstance(b, dict) and "text" in b:
                    parts.append(b["text"])
    return "\n".join(parts)


PAGE_TEXT = ("The Symposium, translated by Benjamin Jowett. He who has been instructed thus far in the "
             "things of love, and who has learned to see the beautiful in due order and succession, will suddenly "
             "perceive a nature of wondrous beauty.")


LEDGER = {
    "rows": [  # deliberately out of rank order: code numbers rows by rank
        {"idea": "Beauty prompts a desire to replicate.", "rank": 3, "type": "covered",
         "episode_has": "Scarry, early on: 'it makes us want to copy it'.",
         "research_question": "1. The Symposium is set in 399 BC (Brooks). 2. Diotima is from Mantinea (Scarry).",
         "tier_needed": 2},
        {"idea": "Plato's ladder ends in a beauty no one beloved can hold.", "rank": 1, "type": "deep-dive",
         "episode_has": "Brooks mentions the ladder in passing.",
         "research_question": "What does Diotima's ladder say at Symposium 210a-212a?", "tier_needed": 1},
        {"idea": "Weil thought attention to beauty is a form of prayer.", "rank": 2, "type": "missing",
         "episode_has": "Nothing.", "research_question": "Where does Weil link beauty and attention?",
         "tier_needed": 2},
    ],
    "parked": ["Kant on disinterested pleasure."],
}

SHARED = ("In the Symposium, Socrates attributes his teaching on love to Diotima of Mantinea. "
          "Source: Symposium, https://www.gutenberg.org/ebooks/1600")


def row_findings(rid, start=1, kind="deep"):
    """An agent's <research> block for a row. kind: deep, verify or followup."""
    n = start
    if kind == "verify":
        entries = [
            f"[{rid}.{n}] (claim, tier 2, corrected) The episode says the Symposium is set in 399 BC. In fact "
            "the dinner is set in 416 BC. Source: SEP, https://plato.stanford.edu/entries/plato/",
            f"[{rid}.{n + 1}] (claim, tier 2, confirmed) Diotima is from Mantinea. "
            "Source: SEP, https://plato.stanford.edu/entries/plato/",
            f"[{rid}.{n + 2}] (material, tier episode) Scarry describes a palm tree she keeps drawing. Source: episode",
        ]
    elif kind == "followup":
        entries = [f"[{rid}.{n}] (material, tier 2) Weil at Solesmes in 1938, listening to the chant. "
                   "Source: https://plato.stanford.edu/entries/weil/"]
    else:
        entries = [
            f"[{rid}.{n}] (claim, tier 1) {SHARED}",
            f"[{rid}.{n + 1}] (quote, tier 1, verbatim: yes) \"He who has been instructed thus far in the things "
            f"of love, and who has learned to see the beautiful\" Plato, Symposium 210e, tr. Jowett, for {rid}. "
            "Source: Symposium, https://www.gutenberg.org/ebooks/1600",
            f"- [{rid}.{n + 2}] (quote, tier 1, verbatim: yes) \"A sentence that appears on no page the agent "
            f"ever fetched at all.\" Found for {rid}. Source: https://invented.example.com/page",
            f"[{rid}.{n + 3}] (tension, tier 2) Some readers say {rid}'s idea abandons persons; compare {rid}.{n}. "
            "Source: https://en.wikipedia.org/wiki/Symposium_(Plato)",
            f"[{rid}.{n + 4}] (connection, {'L01' if rid == 'L02' else 'L02'}) The ladder and Weil's attention "
            "both turn away from the self.",
            f"[{rid}.{n + 5}] (In 1933) a claim with no tags and no source, for {rid}.",
        ]
        if rid == "L01":
            entries.insert(2, f"[{rid}.{n + 6}] (material, tier 1) Agathon's dinner party, the night after his "
                              "victory, with Alcibiades arriving drunk. Source: https://www.gutenberg.org/ebooks/1600")
    return (f"Writing up now.\n<research>\n# {rid}: an idea\n\n## Summary\nFound things.\n\n## Entries\n"
            + "\n".join(entries) + "\n\n## Not found\n" + ("Nothing." if kind != "deep" else "Where Diotima lived.")
            + "\n</research>")


def brief_text(n_segments=3, rows=("L01", "L02", "L03")):
    segs = "\n\n".join(
        f"## Segment {i}: Part {i}\n- Purpose: move {i}.\n- Target words: 120\n"
        f"- Ledger: {rows[(i - 1) % len(rows)]}\n- Dossier: D{i:02d}, D{i + 1:02d}\n"
        f"- Beats: one, two.\n- Opens with: x\n- Hands off to: y" for i in range(1, n_segments + 1))
    return ("# Brief: Beauty\n\n## Through-line\nCan looking at beauty change us?\n\n## Thesis\nBeauty teaches.\n\n"
            "## Listener\nCurious.\n\n## Opening hook\nA dinner party.\n\n## Closing line\nThe end.\n\n"
            "## Style guide for the ear\nShort sentences.\n\n## Arc\nUp the ladder.\n\n" + segs)


def segment_prose(n):
    paras = [f"Segment {n} opens at a dinner party in Athens. Socrates tells the guests about Diotima. [D01] "
             "Plato has her say: \u201cHe who has been instructed thus far in the things of love.\u201d [D02]",
             f"This is the second paragraph of segment {n}. It moves the argument along, slowly and plainly. "
             f"Scarry calls it \"a sentence nobody ever wrote down anywhere\". [D03]"]
    return "\n\n".join(paras)


class Responder:
    """Return a plausible response for each stage, keyed off the prompt text."""

    def __init__(self, n_segments=3):
        self.n_segments = n_segments
        self.calls: list[dict] = []
        self.row_calls: dict[str, int] = {}
        self.judge_calls = 0
        self.pause_at: dict[str, tuple[int, int]] = {}  # row -> (searches, fetches) used when it pauses
        self.judge_rounds: list[dict] = [  # one verdict set per judge call; the last repeats
            {"L01": "filled", "L02": "partial", "L03": "filled"},
            {"L01": "filled", "L02": "filled", "L03": "filled"},
        ]

    def __call__(self, kwargs):
        self.calls.append(kwargs)
        t = all_text(kwargs)
        if "single word: ready" in t:
            return self.msg(kwargs, "ready")
        if "Map the episode" in t:
            return self.msg(kwargs, "## Episode in brief\nA map.")
        if "You are the commissioning editor" in t:  # before "candidate angles": the choose prompt quotes them
            return self.msg(kwargs, "# Angle: The ladder and the self\n\nBeauty teaches.\n\n"
                                    "## Notes for the writer\n- More Weil, please.")
        if "candidate angles" in t:
            return self.msg(kwargs, "### Angle 1: The ladder\n- **Thesis:** beauty teaches.")
        if "Before any research, we decide what the chapter must land" in t:
            return self.msg(kwargs, json.dumps(LEDGER))
        m = re.search(r"(?:Your idea is row|This idea is row) (L\d+)", t)
        if m and "your search and fetch budget is now spent" in t:
            return self.msg(kwargs, row_findings(m.group(1)))
        if m and ("You are a research agent for one idea" in t or "You are a fact-checker for one idea" in t):
            rid = m.group(1)
            key = rid + (" follow-up" if "This is a follow-up." in t else "")
            self.row_calls[key] = self.row_calls.get(key, 0) + 1
            fetched = NS(type="web_fetch_tool_result", tool_use_id="f1", content=NS(
                type="web_fetch_result", url="https://www.gutenberg.org/ebooks/1600",
                content=NS(type="document", title="Symposium",
                           source=NS(type="text", media_type="text/plain", data=PAGE_TEXT))))
            searched = NS(type="web_search_tool_result", tool_use_id="s1",
                          content=[NS(type="web_search_result", url="https://www.gutenberg.org/ebooks/1600",
                                      title="Symposium")])
            if self.pause_at and key == rid and self.row_calls[key] == 1 and rid in self.pause_at:
                s_used, f_used = self.pause_at[rid]  # pauses part-way through its budget
                return self.msg(kwargs, None, stop="pause_turn", content=[searched, fetched],
                                u=usage(searches=s_used, fetches=f_used))
            if rid == "L01" and key == rid and self.row_calls[key] == 1:
                # Pauses with its whole budget spent: must be written up, not resumed.
                return self.msg(kwargs, None, stop="pause_turn", content=[
                    NS(type="server_tool_use", id="s1", name="web_search", input={"query": "symposium"}),
                    searched, fetched,
                    NS(type="server_tool_use", id="s9", name="web_search", input={"query": "more"})],
                    u=usage(searches=8, fetches=1))
            if rid == "L02" and key == rid and self.row_calls[key] == 1:
                # Pauses before using any of its budget: resumed, with the same tools.
                return self.msg(kwargs, None, stop="pause_turn", content=[
                    NS(type="server_tool_use", id="s1", name="web_search", input={"query": "weil"})],
                    u=usage(searches=0))
            if key.endswith("follow-up"):
                start = int(re.search(r"\[" + rid + r"\.(\d+)\]", t.split("</earlier>")[-1]).group(1))
                text = row_findings(rid, start=start, kind="followup")
            else:
                text = row_findings(rid, kind="verify" if "fact-checker" in t else "deep")
            return self.msg(kwargs, None, content=[fetched, text_block(text)], u=usage(searches=1, fetches=1))
        if "You are the research editor for a long-form audio chapter" in t:
            verdicts = self.judge_rounds[min(self.judge_calls, len(self.judge_rounds) - 1)]
            self.judge_calls += 1
            rows = [{"id": rid, "status": st, "dossier_refs": ["D01", "D99"], "best_tier": 1,
                     "gap": "" if st == "filled" else "No tellable material for Weil: find a scene."}
                    for rid, st in verdicts.items()]
            return self.msg(kwargs, json.dumps({"rows": rows, "connections": [
                {"rows": ["L01", "L02", "L09"], "link": "Both turn the self outward."}]}))
        if "Write the brief" in t:
            return self.msg(kwargs, brief_text(self.n_segments))
        if "You are checking the draft of a long-form audio chapter against the ledger" in t:
            return self.msg(kwargs, json.dumps({
                "rows": [{"id": "L01", "coverage": "covered", "segments": [1, 2], "note": ""},
                         {"id": "L02", "coverage": "thin", "segments": [2], "note": "Weil needs her scene."},
                         {"id": "L03", "coverage": "missing", "segments": [], "note": "Never lands."}],
                "drift": [{"idea": "Dinner-party logistics", "segments": [1], "note": "Cut."}],
                "through_line": "Clear until segment 3.",
                "unbacked_quotes": [{"quote": "a sentence nobody ever wrote down anywhere", "segment": 2,
                                     "note": "No entry."}]}))
        m = re.search(r"Write segment (\d+) of", t)
        if m:
            return self.msg(kwargs, segment_prose(int(m.group(1))))
        if "You are the editor of an audio chapter" in t:
            notes = "## Global\n1. [TONE] Warmer.\n\n" + "\n\n".join(
                f"## seg{i:02d}\n1. [CUT] Cut 'slowly and plainly'." for i in range(1, self.n_segments + 1))
            return self.msg(kwargs, notes + "\n\n## Claim audit\nFine.")
        m = re.search(r"Revise segment (\d+) of", t)
        if m:
            return self.msg(kwargs, segment_prose(int(m.group(1))).replace(", slowly and plainly", ""))
        if "complete narration script" in t:
            return self.msg(kwargs, None, content=[text_block(
                "Let me research.<script>\nA baseline paragraph about Diotima.\n\nAnother one.\n</script>")])
        raise AssertionError(f"fake API got an unexpected prompt: {t[:200]}")

    @staticmethod
    def msg(kwargs, text, stop="end_turn", content=None, u=None):
        return NS(content=content if content is not None else [text_block(text)], stop_reason=stop,
                  usage=u or usage(), model=kwargs["model"], stop_details=None, _request_id="req_fake")


class _Stream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for block in self.message.content:
            yield NS(type="content_block_stop", content_block=block)

    def get_final_message(self):
        return self.message


class FakeAnthropic:
    def __init__(self, responder):
        self.responder = responder
        self.messages = NS(stream=lambda **kw: _Stream(responder(kw)))
        self.beta = NS(messages=NS(stream=lambda **kw: _Stream(responder(kw))))


def fake_synthesize(text, *, voice_id, ncfg, **kw):
    """PCM s16le mono: a quiet tone, 20 ms per character (capped), so durations are real."""
    from stages.tts import Synth

    rate = int(ncfg["output_format"].split("_")[1])
    n = int(rate * min(len(text) * 0.02, 3.0))
    frames = b"".join(struct.pack("<h", int(800 * math.sin(2 * math.pi * 220 * i / rate))) for i in range(n))
    return Synth(audio=frames, request_id=f"req_{len(text)}")


# --- a minimal PDF writer, for testing ingest ---------------------------------

def make_pdf(path, pages: list[list[str]]) -> None:
    objs: list[bytes] = []

    def add(b: bytes) -> int:
        objs.append(b)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_id = len(objs) + 1 + 2 * len(pages)  # reserve: each page = content + page obj
    page_ids = []
    for lines in pages:
        ops = ["BT", "/F1 10 Tf", "12 TL", "50 780 Td"]
        for ln in lines:
            esc = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({esc}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_ids.append(add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
            b"/Resources << /Font << /F1 %d 0 R >> >> >>" % (pages_id, content, font)))
    kids = " ".join(f"{i} 0 R" for i in page_ids).encode()
    assert add(b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % len(page_ids)) == pages_id
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, catalog, xref)
    path.write_bytes(bytes(out))
