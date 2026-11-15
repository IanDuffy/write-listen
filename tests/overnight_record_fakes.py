"""Fakes for overnight recording and assembly (overnight/record.py, overnight/assemble.py).

FakeLive: a session factory whose sessions return takes of synthesized speech (a tone per word, as
long as a word takes to say) with the transcript of what was "said", per paragraph and attempt:
"ok", "misread" (three words dropped), "slip" (one word dropped: still passes), "cut" (stops halfway),
"never" (never starts), "lost" (the connection drops), or a function text -> heard.

FakeServer: a local WebSocket server speaking GPT-Live's protocol (session.start/started, muted input,
commentary in, a continuous output audio stream with digital silence, transcript deltas in 200 ms
steps arriving 800 ms late, words split across deltas, usage, close/closed), ten times faster than
real time, for the real client's event handling.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from overnight import record
from stages.common import Run

SCRIPT = """<!-- seg01 -->

The heron stands in the shallows for a long time. [D01] Nothing about it moves, except, now and then, the eye.

It is waiting for something it cannot see yet, and it is in no hurry. [D02]

<!-- seg02 -->

Herons have been studied for centuries. [D03] The *Natural History* of Pliny describes them, [T]hough not kindly.

[pause]

What the old writers missed was the patience itself. [D03, D04]

Patience, it turns out, is a strategy with a cost.

<!-- seg03 -->

A heron that strikes too early loses the fish and the hour. [D05]

So it waits, and it does not move.

<!-- seg04 -->

That is the whole art of it: to wait well. [D06]

And that is where this chapter ends.
"""

DOSSIER = """# Dossier

## Entries

""" + "\n".join(f"[D{n:02d}] (L01, claim, tier 2, verbatim: no, confirmed) Fact {n} about herons. "
                f"Source: https://example.org/{n}" for n in range(1, 7)) + "\n"


def make_run(tmp_path: Path, script: str = SCRIPT, name: str = "2026-11-17-herons") -> Run:
    r = Run.open(name, create=True, runs_dir=tmp_path / "chapters")
    (r.root / "09_script_final.md").write_text(script, encoding="utf-8")
    (r.root / "04_dossier.md").write_text(DOSSIER, encoding="utf-8")
    (r.root / "03_angle.md").write_text("# Angle: Why herons wait\n\nPatience as a strategy.\n", encoding="utf-8")
    (r.root / "05_brief.md").write_text("# Brief: Why Herons Wait\n\n## Segment 1: The wait\n\nDossier: D01, D02\n",
                                        encoding="utf-8")
    return r


# --- synthesized speech ----------------------------------------------------------------------------

RATE = record.RATE


def word_ms(word: str) -> int:
    return max(120, 25 * len(word))


GAP_MS = 60


def tone(ms: float, amp: int = 3000) -> np.ndarray:
    n = int(RATE * ms / 1000)
    return (amp * np.sin(2 * math.pi * 220 * np.arange(n) / RATE)).astype(np.int16)


def silence(ms: float) -> np.ndarray:
    return np.zeros(int(RATE * ms / 1000), np.int16)


def speech(text: str) -> bytes:
    """A take as record.trim leaves it: the pad, a tone per word with short gaps, the pad."""
    parts = [silence(record.PAD_LEAD * 1000)]
    for i, w in enumerate(text.split()):
        if i:
            parts.append(silence(GAP_MS))
        parts.append(tone(word_ms(w)))
    parts.append(silence(record.PAD_TAIL * 1000))
    return np.concatenate(parts).tobytes()


# --- the fake session ------------------------------------------------------------------------------

class Killed(BaseException):
    """The process killed mid-recording (a BaseException, like KeyboardInterrupt)."""


def drop_words(text: str, n: int) -> str:
    words = text.split()
    k = len(words) // 2
    return " ".join(words[:k] + words[k + n:])


class FakeSession:
    def __init__(self, live: "FakeLive", label: str):
        self.live, self.label = live, label
        with live.lock:
            live.opened += 1
            self.id = f"live_fake_{live.opened}"
        self.seconds = 0.5
        self.closed = False
        self.texts: list[str] = []

    def needs_new(self) -> bool:
        return False

    def read(self, text: str) -> record.Take:
        assert not self.closed, "read on a closed session"
        if self.live.before_read:
            self.live.before_read(self, text)
        with self.live.lock:
            todo = self.live.plan.get(text, [])
            how = todo.pop(0) if todo else "ok"
            self.live.reads.append((self.id, self.label, text, how))
        self.texts.append(text)
        if how == "lost":
            self.seconds += 1.0
            raise record.SessionLost("fake drop")
        if how == "never":
            self.seconds += record.GIVE_UP_MS / 1000
            return record.Take(b"", "", None, None, "never_started", 0, len(text.split()))
        heard = (how(text) if callable(how) else drop_words(text, 3) if how == "misread"
                 else drop_words(text, 1) if how == "slip" else " ".join(text.split()[: len(text.split()) // 2])
                 if how == "cut" else text)
        pcm = speech(heard)
        start = int(self.seconds * 1000)
        self.seconds += len(pcm) / 2 / RATE + self.live.overhead
        return record.Take(pcm, heard, start, start + len(pcm) // 2 // record.SAMPLES_PER_MS,
                           "quiet" if how == "cut" else "done", len(heard.split()), len(text.split()))

    def close(self) -> float:
        self.closed = True
        with self.live.lock:
            self.live.closed += 1
        return self.seconds


class FakeLive:
    """The session factory: FakeLive(plan)(cfg, instructions, label) -> FakeSession."""

    def __init__(self, plan: dict | None = None, overhead: float = 0.8, before_read=None):
        self.plan = {k: list(v) for k, v in (plan or {}).items()}
        self.overhead = overhead
        self.before_read = before_read
        self.lock = threading.Lock()
        self.reads: list[tuple] = []
        self.sessions: list[FakeSession] = []
        self.instructions: list[str] = []
        self.opened = self.closed = 0

    def __call__(self, cfg, instructions, label):
        s = FakeSession(self, label)
        with self.lock:
            self.sessions.append(s)
            self.instructions.append(instructions)
        return s

    def texts(self) -> list[str]:
        return [x[2] for x in self.reads]


# --- the fake GPT-Live server ----------------------------------------------------------------------

STEP_MS = 20
SPEED = 10            # audio ms per real ms
LATENCY_MS = 600      # commentary to first word
LAG_MS = 800          # a transcript delta arrives this long after its start_ms


class FakeServer:
    """plan: one behaviour per commentary received: "ok", "ignore" (say nothing), "preamble" ("Right."
    half a second before the passage)."""

    def __init__(self, plan=None):
        self.plan = list(plan or [])
        self.start: dict | None = None
        self.headers: dict = {}
        self.received: list[dict] = []
        self.words: list[dict] = []     # {"w", "start", "end"} in session ms
        self.deltas: list[tuple] = []   # (emit_at, start_ms, text)
        self.clock = 0
        self.closed_with: float | None = None
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._serve, name="record-fake-server", daemon=True)
        self.thread.start()
        assert self.ready.wait(5)

    def _serve(self):
        import websockets

        asyncio.set_event_loop(self.loop)

        async def main():
            self.server = await websockets.serve(self.handler, "127.0.0.1", 0, max_size=None)
            self.port = self.server.sockets[0].getsockname()[1]
            self.ready.set()
            await self.server.wait_closed()
        try:
            self.loop.run_until_complete(main())
        finally:
            self.loop.close()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def stop(self):
        self.loop.call_soon_threadsafe(self.server.close)
        self.thread.join(5)

    def schedule(self, text: str, at: int) -> None:
        t = at
        for w in text.split():
            d = word_ms(w)
            self.words.append({"w": w, "start": t, "end": t + d})
            win = t // 200 * 200
            if len(w) >= 6 and (t + d - 1) // 200 * 200 != win:  # split across two deltas: "list" + "ening"
                half = len(w) // 2
                self.deltas.append((win + LAG_MS, win, " " + w[:half]))
                self.deltas.append((win + 200 + LAG_MS, win + 200, w[half:]))
            else:
                self.deltas.append((win + LAG_MS, win, " " + w))
            t += d + GAP_MS

    def chunk(self) -> bytes:
        a, b = self.clock, self.clock + STEP_MS
        out = np.zeros(STEP_MS * record.SAMPLES_PER_MS, np.int16)
        for w in self.words:
            if w["end"] > a and w["start"] < b:
                lo, hi = max(a, w["start"]), min(b, w["end"])
                i, j = (lo - a) * record.SAMPLES_PER_MS, (hi - a) * record.SAMPLES_PER_MS
                n = np.arange(lo * record.SAMPLES_PER_MS, hi * record.SAMPLES_PER_MS)
                out[i:j] = (3000 * np.sin(2 * math.pi * 220 * n / RATE)).astype(np.int16)
        return out.tobytes()

    async def handler(self, ws):
        self.headers = dict(ws.request.headers)
        self.start = json.loads(await ws.recv())
        await ws.send(json.dumps({"type": "session.started", "session": {
            "id": "live_test_1", "expires_at": time.time() + 7200, "model": self.start["session"]["model"]}}))
        producer = asyncio.create_task(self.produce(ws))
        try:
            async for raw in ws:
                e = json.loads(raw)
                self.received.append(e)
                typ = e["type"]
                if typ == "session.input_audio.mute":
                    await ws.send(json.dumps({"type": "session.input_audio.muted", "client_event_id": e["event_id"]}))
                elif typ == "session.commentary.append":
                    await ws.send(json.dumps({"type": "session.commentary.appended", "client_event_id": e["event_id"]}))
                    how = self.plan.pop(0) if self.plan else "ok"
                    text = e["content"].split(":", 1)[1].strip()
                    at = self.clock + LATENCY_MS
                    if how == "ignore":
                        continue
                    if how == "preamble":
                        self.schedule("Right.", at)
                        at += word_ms("Right.") + 500
                    self.schedule(text, at)
                elif typ == "session.close":
                    self.closed_with = self.clock / 1000
                    await ws.send(json.dumps({"type": "session.closed", "reason": "close_requested",
                                              "usage": {"seconds": self.closed_with}}))
                    break
        finally:
            producer.cancel()

    async def produce(self, ws):
        """The output stream in real time, ten times over: audio every step, transcript deltas when due."""
        while True:
            await ws.send(json.dumps({"type": "session.output_audio.delta",
                                      "delta": base64.b64encode(self.chunk()).decode()}))
            self.clock += STEP_MS
            for d in [d for d in self.deltas if d[0] <= self.clock]:
                self.deltas.remove(d)
                await ws.send(json.dumps({"type": "session.output_transcript.delta", "start_ms": d[1],
                                          "end_ms": d[1] + 200, "delta": d[2]}))
            if self.clock % 1000 == 0:
                await ws.send(json.dumps({"type": "session.usage.updated", "usage": {"seconds": self.clock / 1000},
                                          "context_window": {"usage_ratio": 0.01}}))
            await asyncio.sleep(STEP_MS / SPEED / 1000)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No test reaches a live API: no Anthropic or ElevenLabs client, and no OpenAI key (the real
    GPT-Live client is only ever pointed at a local fake server, with a test key)."""
    import anthropic
    import elevenlabs.client

    def live(*a, **k):
        raise AssertionError("a test reached a live API client")

    monkeypatch.setattr(anthropic, "Anthropic", live)
    monkeypatch.setattr(elevenlabs.client, "ElevenLabs", live)
    monkeypatch.setattr(record, "api_key", live)
    yield


@pytest.fixture(autouse=True)
def no_threads_left():
    """Every recording lane and session thread has ended when its test does."""
    yield
    deadline = time.monotonic() + 5
    for t in threading.enumerate():
        if t.name.startswith(("record-lane", "record-live")):
            t.join(max(0.0, deadline - time.monotonic()))
    assert not [t.name for t in threading.enumerate() if t.name.startswith(("record-lane", "record-live"))]
