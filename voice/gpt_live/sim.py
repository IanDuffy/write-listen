"""A GPT-Live session driven from Python, for the live checks: a simulated listener speaks to the real
guide by voice (spec "One Live Voice", check 14 and the cost measurements; persona.py drives it).

- `Sim`: one session over the primary WebSocket (session.session_config(..., transport="websocket")).
  Input audio streams continuously in real time, 20 ms of PCM16 24 kHz mono at a time: the listener's
  speech when there is some (`say`), silence otherwise, as GPT-Live asks. The guide's output transcript
  is grouped into turns (a turn ends after QUIET_SECS with no new delta and no audible output: the
  output audio stream carries silence too, so "quiet" is measured, not the absence of deltas).
- The guide's tools arrive from the delegation backend as `response.event` envelopes; each nested
  `response.output_item.done` function_call is run by a pluggable handler (`FakeTools` by default)
  and answered with `response.item.create` (function_call_output); once that backend response has
  completed, one `response.create` resumes it.
- Costs: connected seconds from `session.usage.updated` (cumulative) and backend tokens from nested
  `response.completed`, priced by session.cost; the listener's speech (OpenAI TTS, cached on disk) and
  the Claude calls (persona.py) are logged too. Everything goes to build_costs.jsonl, and a session
  that would take the build past config live.build_cap_usd is refused before it connects.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import math
import time
import wave
from array import array
from pathlib import Path

import httpx
import websockets

from stages.common import StageError

from . import session as live

HERE = Path(__file__).parent
CACHE = HERE / "_cache"  # the listener's speech (gitignored)
RUNS = HERE / "_runs"  # transcripts, grades, guide audio, the Claude run log (gitignored)
COSTS = HERE / "build_costs.jsonl"

RATE = 24000
CHUNK = 960  # 20 ms of PCM16 at 24 kHz mono
TTS_URL = "https://api.openai.com/v1/audio/speech"
TTS_MODEL = "gpt-4o-mini-tts"
# gpt-4o-mini-tts: $12 per 1M audio output tokens, about $0.015 a minute of speech (OpenAI pricing,
# 2026-11-13); the text input ($0.60 per 1M tokens) is negligible.
TTS_USD_PER_MINUTE = 0.015
LISTENER_VOICE = "ash"  # not gleam, so the two are told apart in the recording
QUIET_SECS = 1.2  # a guide turn ends after this long with no transcript and no audible output
AUDIO_RMS = 200  # output audio above this RMS is speech (GPT-Live streams silence between turns)


# --- the build's spending ---------------------------------------------------------------------

def log_cost(what: str, usd: float, *, seconds: float | None = None, backend_tokens: dict | None = None,
             path: Path | None = None) -> None:
    path = path or COSTS
    rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "what": what, "seconds": seconds,
           "backend_tokens": backend_tokens, "usd": round(usd, 6)}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def spent(path: Path | None = None) -> float:
    path = path or COSTS
    if not path.exists():
        return 0.0
    return round(sum(json.loads(l).get("usd") or 0 for l in path.read_text(encoding="utf-8").splitlines()
                     if l.strip()), 6)


def check_cap(cfg: dict, expected_usd: float, what: str, path: Path | None = None) -> None:
    """Refuse `what` when the build's spending so far plus its expected cost would pass the cap."""
    cap = float(live.live_cfg(cfg)["build_cap_usd"])
    total = spent(path)
    if total + expected_usd > cap:
        raise StageError(f"{what}: the build has spent ${total:.2f} of its ${cap:.2f} cap "
                         f"(live.build_cap_usd), and this could cost ${expected_usd:.2f}. Not started.")


def session_estimate(cfg: dict, minutes: float) -> float:
    """An upper estimate for a session of `minutes`: connected time plus a margin for backend tokens."""
    return minutes * live.live_cfg(cfg)["price_per_minute"] + 0.10


# --- the listener's voice ---------------------------------------------------------------------

def tts(text: str, voice: str = LISTENER_VOICE, *, http=None, cache: Path | None = None,
        costs: Path | None = None) -> bytes:
    """The listener's line as PCM16 24 kHz mono (OpenAI TTS), cached by model, voice and text."""
    cache = cache or CACHE
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / (hashlib.sha256(f"{TTS_MODEL}|{voice}|{text}".encode()).hexdigest()[:24] + ".pcm")
    if f.exists() and f.stat().st_size:
        return f.read_bytes()
    res = (http or httpx).post(TTS_URL, headers={"Authorization": f"Bearer {live.api_key()}"},
                               json={"model": TTS_MODEL, "voice": voice, "input": text, "response_format": "pcm"},
                               timeout=60)
    if res.status_code >= 400:
        raise StageError(f"OpenAI TTS refused the listener's line ({res.status_code}): {res.text[:200]}")
    pcm = res.content
    f.write_bytes(pcm)
    secs = len(pcm) / (2 * RATE)
    log_cost("tts listener line", secs / 60 * TTS_USD_PER_MINUTE, seconds=round(secs, 2), path=costs)
    return pcm


def rms(pcm: bytes) -> float:
    a = array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    return math.sqrt(sum(x * x for x in a) / len(a)) if a else 0.0


# --- the guide's tools, faked -----------------------------------------------------------------

class FakeTools:
    """Plausible results for the guide's tools, shaped like stages/guide.py Session.tool and
    faststart/guide_hooks.py for a fast run: a chapter that starts playing in `first_audio_minutes`."""

    def __init__(self, first_audio_minutes: int = 4, clock=time.monotonic):
        self.first_audio_minutes = first_audio_minutes
        self.clock = clock
        self.started_at: float | None = None

    def __call__(self, name: str, args: dict) -> dict:
        if name == "start_chapter":
            if not str(args.get("topic") or "").strip():
                return {"started": False, "reason": "The topic was empty."}
            self.started_at = self.clock()
            m = self.first_audio_minutes
            return {"started": True, "first_audio_minutes": m,
                    "when": f"Going by earlier chapters, it can start playing in about {m} minutes."}
        if name == "add_note":
            if not str(args.get("note") or "").strip():
                return {"added": False, "reason": "The note was empty."}
            return {"added": True, "kind": args.get("kind") or "other",
                    "effect": "It can shape the parts of the chapter not yet written."}
        if name == "get_status":
            if self.started_at is None:
                return {"state": "no chapter yet"}
            left = max(1, math.ceil(self.first_audio_minutes - (self.clock() - self.started_at) / 60))
            return {"state": "being made", "stage": "researching and writing the opening",
                    "can_start_listening": False, "starts_playing_in_minutes": left}
        if name == "step_back":
            return {"quiet": True}
        if name == "resume_chapter":
            return {"resuming": True}
        if name == "start_reading":
            return {"starting": self.started_at is not None}
        if name == "scout_topic":
            return {"scouting": True}
        if name == "save_seed":
            return {"saved": True}
        return {"error": f"no tool named {name}"}


# --- the session ------------------------------------------------------------------------------

def ws_url(cfg: dict) -> str:
    """The primary WebSocket: the same address as the WebRTC create call (config live.url), over wss."""
    return "wss://" + live.live_cfg(cfg)["url"].split("://", 1)[1]


async def _ws_connect(url: str, headers: dict):
    return await websockets.connect(url, additional_headers=headers, max_size=None)


class Sim:
    """One GPT-Live session with a simulated listener. `connect()`, then `say(text)` and
    `wait_guide(since, timeout)`; `close()` returns the session's cost and logs it."""

    def __init__(self, cfg: dict, session_cfg: dict, *, tool_handler=None, label: str = "session",
                 max_minutes: float = 6, greet: bool = True, tts_fn=None, connect=None,
                 audio_out: Path | None = None, events_path: Path | None = None,
                 quiet_secs: float = QUIET_SECS, settle_secs: float = 1.0, costs: Path | None = None,
                 verbose: bool = True):
        self.cfg, self.session_cfg, self.label = cfg, session_cfg, label
        self.tool_handler = tool_handler or FakeTools()
        self.max_minutes, self.greet, self.tts_fn = max_minutes, greet, tts_fn or tts
        self._connect = connect or _ws_connect
        self.audio_out, self.events_path, self.costs = audio_out, events_path, costs
        self.quiet_secs, self.settle_secs, self.verbose = quiet_secs, settle_secs, verbose
        self.t0 = time.monotonic()
        self.ws = None
        self.tasks: list[asyncio.Task] = []
        self.started = asyncio.Event()
        self.closed = asyncio.Event()
        self.timeline: list[dict] = []  # guide turns, listener lines, tool calls, backend text, notes, errors
        self.turns: list[dict] = []  # closed guide turns: {"t", "end", "closed", "text"}
        self._cur: dict | None = None  # the guide turn in progress
        self.heard: list[tuple[float, str]] = []  # the input transcript: what GPT-Live heard
        self.calls: list[dict] = []
        self.errors: list[dict] = []
        self.speech = bytearray()  # the listener's audio still to send
        self.audio_last = -1e9  # when the guide's output was last audible
        self.seconds = 0.0  # connected seconds, as GPT-Live reports them (cumulative)
        self.backend = {"model": live.live_cfg(cfg)["backend_model"], "input": 0, "output": 0, "cached": 0}
        self.session_id = self.expires_at = self.closed_reason = None
        self._pending: dict[str, list[asyncio.Task]] = {}  # delegation id -> tool tasks awaiting response.create
        self._seen_calls: set[str] = set()
        self._wav = None
        self._events = None
        self._n = 0
        self.summary: dict | None = None

    def now(self) -> float:
        return round(time.monotonic() - self.t0, 3)

    @property
    def speaking(self) -> bool:
        return self._cur is not None

    def _say(self, msg: str) -> None:
        if self.verbose:
            print(f"[{self.now():6.1f}] {msg}", flush=True)

    # --- connection --------------------------------------------------------------------------

    async def connect(self) -> None:
        check_cap(self.cfg, session_estimate(self.cfg, self.max_minutes), f"GPT-Live {self.label}", self.costs)
        if self.audio_out:
            self.audio_out.parent.mkdir(parents=True, exist_ok=True)
            self._wav = wave.open(str(self.audio_out), "wb")
            self._wav.setnchannels(1)
            self._wav.setsampwidth(2)
            self._wav.setframerate(RATE)
        if self.events_path:
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            self._events = open(self.events_path, "w", encoding="utf-8")
        self.ws = await self._connect(ws_url(self.cfg), {"Authorization": f"Bearer {live.api_key()}"})
        self.t0 = time.monotonic()
        await self.send("session.start", session=self.session_cfg)
        self.tasks = [asyncio.create_task(self._reader()), asyncio.create_task(self._pump())]
        try:
            await asyncio.wait_for(self.started.wait(), 15)
        except asyncio.TimeoutError:
            await self.close()
            raise StageError(f"GPT-Live did not start the session: {self.errors[-1:] or 'no answer in 15 s'}")
        if self.greet:  # the greeting as the app sends it: the developer message is already in the startup input
            await self.note("commentary", live.notes()["opened"])

    async def send(self, typ: str, **kw) -> str:
        self._n += 1
        kw.setdefault("event_id", f"{typ.rsplit('.', 1)[-1]}_{self._n}")
        await self.ws.send(json.dumps({"type": typ, **kw}))
        if typ != "session.input_audio.append" and self._events:
            shown = {k: v for k, v in kw.items() if k != "session"}
            self._events.write(json.dumps({"t": self.now(), "sent": typ, **shown}) + "\n")
        return kw["event_id"]

    async def note(self, kind: str, text: str) -> None:
        """Text into the session as the page sends it: kind "commentary" (speakable), "instructions"
        or "thinking" (silent), each cut to GPT-Live's 500-token appends."""
        self.timeline.append({"t": self.now(), "who": "app", "kind": kind, "text": text})
        for part in live.split_for_append(text):
            await self.send(f"session.{kind}.append", delegation_id=None, content=part)

    async def mute(self, on: bool = True) -> None:
        self.timeline.append({"t": self.now(), "who": "app", "kind": "mute" if on else "unmute"})
        await self.send("session.input_audio.mute" if on else "session.input_audio.unmute")

    async def _pump(self) -> None:
        """Input audio in real time: the listener's speech when queued, silence otherwise."""
        nxt = time.monotonic()
        while True:
            chunk = bytes(self.speech[:CHUNK]).ljust(CHUNK, b"\x00")
            del self.speech[:CHUNK]
            await self.ws.send(json.dumps({"type": "session.input_audio.append",
                                           "audio": base64.b64encode(chunk).decode()}))
            self.tick(self.now())
            nxt += CHUNK / (2 * RATE)
            lag = nxt - time.monotonic()
            if lag < -0.2:  # fell behind (a slow event loop): carry on in real time, no burst
                nxt = time.monotonic()
            await asyncio.sleep(max(0.0, lag))

    async def _reader(self) -> None:
        try:
            async for raw in self.ws:
                await self.on_event(json.loads(raw), self.now())
        except websockets.ConnectionClosed:
            pass
        finally:
            self.closed.set()

    # --- events ------------------------------------------------------------------------------

    async def on_event(self, m: dict, t: float) -> None:
        typ = m.get("type")
        if typ == "session.output_audio.delta":
            pcm = base64.b64decode(m.get("delta") or "")
            if self._wav:
                self._wav.writeframes(pcm)
            if rms(pcm) > AUDIO_RMS:
                self.audio_last = t
            return
        if self._events:
            self._events.write(json.dumps({"t": t, **m}) + "\n")
        if typ == "session.started":
            s = m.get("session") or {}
            self.session_id, self.expires_at = s.get("id"), s.get("expires_at")
            self.started.set()
        elif typ == "session.output_transcript.delta":
            d = m.get("delta") or ""
            if self._cur is None:
                self._cur = {"t": t, "last": t, "parts": [d], "deltas": [[t, d]], "start_ms": m.get("start_ms")}
            else:
                self._cur["parts"].append(d)
                self._cur["deltas"].append([t, d])
                self._cur["last"] = t
        elif typ == "session.input_transcript.delta":
            self.heard.append((t, m.get("delta") or ""))
        elif typ == "session.usage.updated":
            self.seconds = max(self.seconds, float((m.get("usage") or {}).get("seconds") or 0))
        elif typ == "session.closed":
            self.closed_reason = m.get("reason")
            self.seconds = max(self.seconds, float((m.get("usage") or {}).get("seconds") or 0))
            self.closed.set()
        elif typ == "response.event":
            self._backend_event(m.get("delegation_id") or "", m.get("event") or {}, t)
        elif typ == "error":
            err = m.get("error") or {}
            rec = {"t": t, "who": "error", "code": err.get("code"), "message": err.get("message")}
            self.errors.append(rec)
            self.timeline.append(rec)
            self._say(f"ERROR {err.get('code')}: {err.get('message')}")

    def _backend_event(self, delegation: str, e: dict, t: float) -> None:
        et = e.get("type")
        if et == "response.output_item.done" and (e.get("item") or {}).get("type") == "function_call":
            task = asyncio.create_task(self._tool(e["item"], t))
            self._pending.setdefault(delegation, []).append(task)
        elif et == "response.output_text.done" and (e.get("text") or "").strip():
            self.timeline.append({"t": t, "who": "backend", "text": e["text"]})
        elif et == "response.completed":
            r = e.get("response") or {}
            u = r.get("usage") or {}
            self.backend["model"] = r.get("model") or self.backend["model"]
            self.backend["input"] += u.get("input_tokens") or 0
            self.backend["output"] += u.get("output_tokens") or 0
            self.backend["cached"] += (u.get("input_tokens_details") or {}).get("cached_tokens") or 0
            tasks = self._pending.pop(delegation, [])
            if tasks:  # one response.create per backend response, after every output is in
                asyncio.create_task(self._resume_backend(tasks))
        elif et in ("response.failed", "response.incomplete", "error"):
            rec = {"t": t, "who": "error", "code": et, "message": json.dumps(e)[:300]}
            self.errors.append(rec)
            self.timeline.append(rec)

    async def _tool(self, item: dict, t: float) -> None:
        cid = item.get("call_id") or ""
        if cid in self._seen_calls:
            return
        self._seen_calls.add(cid)
        name = item.get("name") or ""
        try:
            args = json.loads(item.get("arguments") or "{}")
        except ValueError:
            args = {"_unparsed": item.get("arguments")}
        try:
            result = self.tool_handler(name, args)
            if inspect.isawaitable(result):
                result = await result
        except Exception as e:  # a broken handler is reported to the guide, as the app would
            result = {"error": str(e)}
        rec = {"t": t, "who": "tool", "name": name, "args": args, "result": result}
        self.calls.append(rec)
        self.timeline.append(rec)
        self._say(f"TOOL {name} {json.dumps(args, ensure_ascii=False)[:200]}")
        await self.send("response.item.create",
                        item={"type": "function_call_output", "call_id": cid, "output": json.dumps(result)})

    async def _resume_backend(self, tasks: list[asyncio.Task]) -> None:
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.send("response.create")

    def tick(self, now: float) -> None:
        """Close the guide's turn once it has been quiet for quiet_secs."""
        cur = self._cur
        if cur and now - max(cur["last"], self.audio_last) >= self.quiet_secs:
            text = "".join(cur["parts"]).strip()
            self._cur = None
            if text:
                turn = {"t": cur["t"], "end": max(cur["last"], min(self.audio_last, now)), "closed": now,
                        "text": text, "start_ms": cur.get("start_ms")}
                self.turns.append(turn)
                self.timeline.append({"t": cur["t"], "who": "guide", "text": text, "deltas": cur["deltas"]})
                self._say(f"GUIDE {text}")

    # --- the listener ------------------------------------------------------------------------

    async def say(self, text: str, voice: str = LISTENER_VOICE) -> dict:
        """Speak a line as the listener (queued into the input audio) and return once it has been sent."""
        pcm = await asyncio.to_thread(self.tts_fn, text, voice)
        rec = {"t": self.now(), "who": "listener", "text": text, "secs": round(len(pcm) / (2 * RATE), 2)}
        self.timeline.append(rec)
        self._say(f"LISTENER {text}")
        self.speech.extend(pcm)
        while self.speech and not self.closed.is_set():
            await asyncio.sleep(0.05)
        rec["end"] = self.now()
        return rec

    async def wait_guide(self, since: int, timeout: float, settle: float | None = None) -> list[dict]:
        """The guide's turns after the first `since`, once at least one has closed and nothing new
        has begun for `settle` seconds (settle_secs); whatever there is after `timeout`. A turn under
        way at the timeout is waited for (up to a minute more): a long turn is not a silence."""
        settle = self.settle_secs if settle is None else settle
        end = time.monotonic() + timeout
        hard = end + 60
        while not self.closed.is_set() and (time.monotonic() < end or (self._cur is not None and time.monotonic() < hard)):
            if len(self.turns) > since and self._cur is None and self.now() - self.turns[-1]["closed"] >= settle:
                break
            await asyncio.sleep(0.05)
        return self.turns[since:]

    def spoken_since(self, t: float) -> str:
        """What the guide has said since `t`, the turn in progress included (the reading's progress)."""
        parts = [x["text"] for x in self.turns if x["t"] >= t]
        if self._cur and self._cur["t"] >= t:
            parts.append("".join(self._cur["parts"]))
        return " ".join(parts)

    def heard_between(self, a: float, b: float) -> str:
        return "".join(d for t, d in self.heard if a <= t < b).strip()

    # --- the end -----------------------------------------------------------------------------

    def cost(self, wall: float | None = None) -> dict:
        seconds = self.seconds or (wall if wall is not None else self.now())
        usd = live.cost(self.cfg, seconds, self.backend)
        return {"seconds": round(seconds, 1), "reported_seconds": self.seconds, "backend": dict(self.backend),
                "usd": usd}

    async def close(self) -> dict:
        if self.summary is not None:
            return self.summary
        wall = self.now()
        if self.ws is not None:
            try:
                await self.send("session.close")
                await asyncio.wait_for(self.closed.wait(), 4)
            except (asyncio.TimeoutError, websockets.ConnectionClosed, RuntimeError):
                pass
        self.tick(self.now() + self.quiet_secs)  # a last turn still open
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:  # already gone
                pass
        if self._wav:
            self._wav.close()
        if self._events:
            self._events.close()
        self.summary = {**self.cost(wall), "wall_seconds": round(wall, 1), "session_id": self.session_id,
                        "closed_reason": self.closed_reason, "expires_at": self.expires_at}
        if self.ws is not None:
            b = self.backend
            log_cost(f"gpt-live {self.label}", self.summary["usd"], seconds=self.summary["seconds"],
                     backend_tokens={"model": b["model"], "input": b["input"], "output": b["output"],
                                     "cached": b["cached"]}, path=self.costs)
        self._say(f"CLOSED {self.closed_reason}: {self.summary['seconds']} s, ${self.summary['usd']:.4f}")
        return self.summary
