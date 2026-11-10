"""Offline stand-ins for the Claude and ElevenLabs APIs, and a tiny PDF writer."""

from __future__ import annotations

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


def findings(n):
    return f"""Now writing my findings.
<findings>
# T{n}: Topic {n}

## Summary
Found things.

## Claims
[T{n}.1] In the Symposium, Socrates attributes his teaching on love to Diotima of Mantinea. Source: Symposium, https://www.gutenberg.org/ebooks/1600
[T{n}.2] "He who has been instructed thus far in the things of love, and who has learned to see the beautiful" Source: Symposium, https://www.gutenberg.org/ebooks/1600 Translator: Jowett.
[T{n}.3] Scarry argues that beauty prompts a desire to replicate. Source: episode
- [T{n}.4] "A sentence that appears on no page the agent ever fetched at all." Source: https://invented.example.com/page
[T{n}.5] A claim with no source at all.

## Conflicts
T{n}.1 and T{n}.3 do not conflict.

## Could not establish
- Where Diotima lived.
</findings>"""


def brief_text(n_segments=3):
    segs = "\n\n".join(
        f"## Segment {i}: Part {i}\n- Purpose: move {i}.\n- Target words: 120\n- Claims: C{i}, C{i + 1}\n"
        f"- Beats: one, two.\n- Opens with: x\n- Hands off to: y" for i in range(1, n_segments + 1))
    return ("# Brief: Beauty\n\n## Thesis\nBeauty teaches.\n\n## Listener\nCurious.\n\n"
            "## Opening hook\nA dinner party.\n\n## Closing line\nThe end.\n\n"
            "## Style guide for the ear\nShort sentences.\n\n## Arc\nUp the ladder.\n\n" + segs)


def segment_prose(n):
    paras = [f"Segment {n} opens at a dinner party in Athens. Socrates tells the guests about Diotima. [C1]",
             f"This is the second paragraph of segment {n}. It moves the argument along, slowly and plainly. "
             f"Scarry thinks beauty makes us want to copy it. [C3]"]
    return "\n\n".join(paras)


class Responder:
    """Return a plausible response for each stage, keyed off the prompt text."""

    def __init__(self, n_segments=3):
        self.n_segments = n_segments
        self.calls: list[dict] = []
        self.topic_calls: dict[int, int] = {}
        self.critic_calls = 0

    def __call__(self, kwargs):
        self.calls.append(kwargs)
        t = all_text(kwargs)
        if "single word: ready" in t:
            return self.msg(kwargs, "ready")
        if "Map the episode" in t:
            return self.msg(kwargs, "## Episode in brief\nA map.")
        if "candidate angles" in t:
            return self.msg(kwargs, "### Angle 1: The ladder\n- **Thesis:** beauty teaches.")
        if "You are planning the research" in t:
            return self.msg(kwargs, "# Research plan: Beauty as a ladder\n\n## Overview\nTwo topics.\n\n"
                            "## T1: Diotima\n- Goal: the ladder.\n\n## T2: Scarry\n- Goal: replication.")
        m = re.search(r"<topic>\s*## T(\d+)", t)
        if m and "You are one of several research agents" in t:
            n = int(m.group(1))
            self.topic_calls[n] = self.topic_calls.get(n, 0) + 1
            if n == 1 and self.topic_calls[n] == 1:  # first request pauses mid tool loop
                return self.msg(kwargs, None, stop="pause_turn", content=[
                    text_block("I'll start with the primary text."),
                    NS(type="server_tool_use", id="s1", name="web_search", input={"query": "symposium"}),
                    NS(type="web_search_tool_result", tool_use_id="s1",
                       content=[NS(type="web_search_result", url="https://www.gutenberg.org/ebooks/1600",
                                   title="Symposium")]),
                ], u=usage(searches=4))
            return self.msg(kwargs, None, content=[
                NS(type="web_fetch_tool_result", tool_use_id="f1", content=NS(
                    type="web_fetch_result", url="https://www.gutenberg.org/ebooks/1600",
                    content=NS(type="document", title="Symposium",
                               source=NS(type="text", media_type="text/plain", data=PAGE_TEXT)))),
                text_block(findings(n))], u=usage(searches=2, fetches=1))
        if "You are the research editor" in t:
            self.critic_calls += 1
            nxt = int(re.search(r"numbered from T(\d+)", t).group(1))
            if self.critic_calls == 1:
                return self.msg(kwargs, f"## Assessment\nT1.5 has no source.\n\n## T{nxt}: Sources for T1.5\n"
                                "- Goal: find a source.\n- Why: T1.5.")
            return self.msg(kwargs, "## Assessment\nGood.\n\nNo follow-up research needed.")
        if "You are writing the guide at the top of a research dossier" in t:
            return self.msg(kwargs, "## Overview\nThe ladder [C1].\n\n## Strongest material\n- [C2]")
        if "Write the brief" in t:
            return self.msg(kwargs, brief_text(self.n_segments))
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
