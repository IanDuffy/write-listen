"""Stage 5, record: the final script read by GPT-Live, server-side, one paragraph at a time
(docs/overnight-chapters.md "Overnight runner" stage 5; checks 6 and 10 of the One Live Voice spec).

    10_audio/takes/<id>.t<k>.wav + .json   every take: the audio cut from the session, what the voice
                                           said (its output transcript), the check, the session's times
    10_audio/paragraphs/<id>.wav + .json   the take kept for the chapter (the first that passed, else the
                                           best, flagged); its WAV marks the paragraph done
    10_audio/recording.jsonl               one line per take
    10_audio/recorded.json                 the stage's result (written last: it marks the stage done)

Paragraphs (`paragraphs`): 09_script_final.md as narration speaks it (markers stripped, [pause] beats
split, editorial brackets made speakable, no bracket left), split at blank lines, a long paragraph cut
at sentences to fit one 500-token append. Ids `s<seg>-p<kk>`; each has the pause assembly puts before
it: `start` (the chapter's first), `section`, `beat` (after [pause]) or `paragraph`.

A session (`LiveSession`): wss .../v1/live/sessions, `session.start` with these instructions
(prompts/overnight_read.md) and no delegation, the mic muted and fed silence in real time, each
paragraph sent as commentary. GPT-Live sends no event at the end of a response, so a paragraph is
followed word by word in the output transcript (`Follow`, a port of page/align.js) and is done when
every word is heard and the audio has gone quiet; else after a quiet with most words heard, or a longer
quiet. Every threshold is measured on the session's own audio clock (its output audio is a continuous
real-time stream, silence included), so the logic runs the same against a fast local fake server. The
take is cut from the session audio at start_ms * 24 samples and trimmed to speech by energy, with a
small, fixed pad.

The check (`deviation`) compares the output transcript with the text after normalising what a
transcript may write differently (case, punctuation, hyphens, apostrophes, numbers in digits, British
and American spellings). A take passes with at most `record.max_word_diffs` words missing, added or
changed and no difference touching a negation or a number. A failing paragraph is read again in the
same session up to `record.retakes` times; the best take is kept and flagged if none passed.

Up to `record.sessions` sessions run in parallel, each on a contiguous run of sections. Each session's
connected seconds are logged to the run's log.jsonl as they accrue (after every paragraph, the rest at
close), so Run.spent() sees them and the runaway stop (Run.max_usd), checked before every take, holds.
A paragraph whose WAV exists is never recorded again (resume).
"""

from __future__ import annotations

import asyncio
import base64
import difflib
import json
import os
import re
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from faststart.narrate import BRACKET, _fit
from stages import tts
from stages.common import LEFTOVER_MARKER, ROOT, Run, StageError, load_prompt, spoken_text, split_script_segments

STAGE = "record"
RATE = 24000                  # GPT-Live's output: PCM16, 24 kHz, mono
SAMPLES_PER_MS = RATE // 1000
PARA_MAX_CHARS = 1400         # about 350 words: one 500-token append with the request in front
READ = "Read this passage aloud now, word for word, exactly as written, with nothing before or after:"

# Following a reading (page/live.js watchReading, in session audio time).
TAIL_QUIET_MS = 700           # every word heard: done once the audio has been quiet this long
QUIET_MS = 2500               # done after this quiet if at least MOST of the words were heard
MOST = 0.6
LONG_QUIET_MS = 5000          # done after this quiet whatever was heard (a cut take; the check fails it)
RESEND_MS = 3500              # nothing started: send the passage once more
GIVE_UP_MS = 10000            # nothing started at all: the take is empty
WALL_SLACK_MS = 1500          # if the audio stream stalls, wall time drives the clock after this
STALL_SECONDS = 20            # no event at all for this long: the session is lost
AUDIBLE_RMS = 200             # a 20 ms frame louder than this is speech (GPT-Live's silence is digital zero)

# Cutting a take (voice/gpt_live/archive.py onset and speech_end, on an in-memory buffer).
FRAME = 0.01                  # seconds per RMS frame
SPEECH_RMS = 150
ONSET_BACK, ONSET_FWD, ONSET_QUIET = 0.35, 0.5, 0.15
PAD_LEAD, PAD_TAIL = 0.06, 0.15   # kept before the first and after the last speech frame, every take

# Reopening a session before it runs out (sessions expire after about 2 hours; context 128k).
REOPEN_USAGE_RATIO = 0.8
REOPEN_EXPIRY_SECONDS = 600
MAX_RECONNECTS = 3            # lost connections tolerated per session lane before the stage stops

_FILE_LOCK = threading.Lock()


# --- paragraphs ----------------------------------------------------------------------------------

def paragraphs(r: Run) -> list[dict]:
    """09_script_final.md as the paragraphs the voice reads: [{"id", "seg", "text", "pause"}], pause one
    of start, section, beat, paragraph (what assembly puts before it)."""
    out = []
    for seg, body in split_script_segments(r.read("script_final")):
        clean = spoken_text(body).replace("*", "")  # italics markers: never for the voice
        if m := LEFTOVER_MARKER.search(clean):
            raise StageError(f"Segment {seg} still has a dossier marker after stripping, near "
                             f"{clean[max(0, m.start() - 40):m.start() + 20]!r}.")
        k = 0
        for b, beat in enumerate(tts.split_beats(clean)):
            beat = tts.speak_editorial_brackets(beat)
            if m := BRACKET.search(beat):
                raise StageError(f"Segment {seg} has a bracketed tag the voice would read aloud: {m.group()!r}.")
            first_in_beat = True
            for para in re.split(r"\n\s*\n", beat):
                para = re.sub(r"\s+", " ", para).strip()
                if not para:
                    continue
                for piece in _fit(para, PARA_MAX_CHARS):
                    k += 1
                    pause = ("start" if not out else "section" if k == 1
                             else "beat" if b > 0 and first_in_beat else "paragraph")
                    out.append({"id": f"s{seg}-p{k:02d}", "seg": seg, "text": piece, "pause": pause})
                    first_in_beat = False
    if not out:
        raise StageError("09_script_final.md has nothing to read.")
    return out


def pronunciations(cfg: dict) -> str:
    """The pronunciation table for the instructions (voice/gpt_live/session.py pronunciations)."""
    table = (cfg.get("narration") or {}).get("pronunciations") or {}
    return "\n".join(f"- {word}: say it like \"{said}\"" for word, said in table.items()) or "- (none)"


def instructions(cfg: dict) -> str:
    return load_prompt("overnight_read", pronunciations=pronunciations(cfg))


# --- the check -------------------------------------------------------------------------------------

_NUM = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
        "seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand "
        "million billion and oh a").split()
_NUM_WORD = re.compile("^(?:" + "|".join(sorted(_NUM, key=len, reverse=True)) + ")+$")
NEGATIONS = {"not", "no", "never", "nor", "none", "nothing", "nobody", "neither", "cannot", "cant", "dont",
             "doesnt", "didnt", "isnt", "wasnt", "arent", "werent", "wont", "wouldnt", "shouldnt", "couldnt",
             "hasnt", "havent", "hadnt", "without"}
SPELLING = {"grey": "gray", "greys": "grays", "programme": "program", "programmes": "programs",
            "cheque": "check", "mould": "mold", "plough": "plow", "sceptic": "skeptic", "sceptics": "skeptics",
            "sceptical": "skeptical", "scepticism": "skepticism", "aluminium": "aluminum", "tyre": "tire",
            "tyres": "tires", "storey": "story", "storeys": "stories", "ageing": "aging",
            "judgement": "judgment", "acknowledgement": "acknowledgment", "okay": "ok", "practise": "practice",
            "practised": "practiced", "manoeuvre": "maneuver", "encyclopaedia": "encyclopedia",
            "mediaeval": "medieval", "foetus": "fetus", "whisky": "whiskey", "jewellery": "jewelry",
            "draught": "draft", "pyjamas": "pajamas", "artefact": "artifact", "artefacts": "artifacts"}
OUR_SUFFIX = "(|s|ed|ing|ite|ites|able|ably|ful|less|er|ers|ism|ist|ists)"
LL_STEMS = {"travel", "cancel", "model", "label", "level", "signal", "marvel", "fuel", "dial", "total", "counsel",
            "jewel", "quarrel", "tunnel", "channel", "equal", "rival", "libel", "pencil", "shovel", "grovel",
            "duel", "funnel", "panel", "revel", "unravel", "enamel", "chisel", "dishevel", "initial"}


def canon(w: str) -> str:
    """One spelling for words a transcript may spell either way: British and American (-our/-or,
    -ise/-ize, -yse/-yze, -ence/-ense, -tre/-ter, -ogue/-og, travelled/traveled) and a few pairs."""
    if w in SPELLING:
        return SPELLING[w]
    if m := re.fullmatch(rf"([a-z]{{2,}})our{OUR_SUFFIX}", w):
        return f"{m[1]}or{m[2]}"
    if m := re.fullmatch(r"([a-z]{2,})(i|y)s(e|es|ed|ing|er|ers|ation|ations)", w):
        return f"{m[1]}{m[2]}z{m[3]}"
    if m := re.fullmatch(r"([a-z]{2,})ence(s?)", w):
        return f"{m[1]}ense{m[2]}"
    if m := re.fullmatch(r"([a-z]{2,}[tb])re(s?)", w):
        return f"{m[1]}er{m[2]}"
    if m := re.fullmatch(r"([a-z]{3,})ogue(s?)", w):
        return f"{m[1]}og{m[2]}"
    if m := re.fullmatch(r"([a-z]+l)l(ed|ing|er|ers)", w):
        if m[1] in LL_STEMS:
            return m[1] + m[2]
    return w


def norm_words(text: str) -> list[str]:
    """Words as the check compares them: lower case, curly quotes straight, sound tags and punctuation
    dropped, hyphenated words joined (a transcript writes "relistening"), apostrophes dropped
    (Vlastos's / vlastos s: the split is then the same letters run apart), digit ranges read with "to",
    and one spelling per word (canon)."""
    t = text.lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"\[[^\]]*\]", " ", t)  # "[laughs]" in a transcript
    t = t.replace("%", " percent ").replace("&", " and ")
    t = re.sub(r"(\d)\s*[–—-]\s*(?=\d)", r"\1 to ", t)  # 1914–1918
    t = re.sub(r"(\d),(?=\d{3})", r"\1", t)  # 1,000
    t = re.sub(r"([a-z0-9])-(?=[a-z0-9])", r"\1", t)
    t = t.replace("'", "")
    return [canon(w) for w in re.sub(r"[^a-z0-9 ]+", " ", t).split()]


def _numeric(w: str) -> bool:
    return w.isdigit() or bool(_NUM_WORD.match(w)) and w not in ("and", "oh", "a")


def _same_number(a: list[str], b: list[str]) -> bool:
    """A number written in words on one side and in digits on the other ("nineteen ninety eight" / "1998")."""
    def words(x):
        return bool(x) and all(_NUM_WORD.match(w) for w in x)

    def digits(x):
        return bool(x) and all(w.isdigit() for w in x)
    return (words(a) and digits(b)) or (digits(a) and words(b))


def _contraction(a: list[str], b: list[str]) -> bool:
    """The same words with or without a contraction ("do not" / "dont", "cannot" / "cant")."""
    x, y = "".join(a), "".join(b)
    for long, short in (("cannot", "cant"), ("willnot", "wont"), ("not", "nt")):
        x, y = x.replace(long, short), y.replace(long, short)
    return x == y


def deviation(text: str, heard: str) -> dict:
    """How the take differs from the text, word by word: {"words", "missing", "added", "changed",
    "critical" (differences touching a negation or a number), "diffs": [[op, text words, heard words]]}.
    Not differences: the same letters run together or apart ("check in" / "checkin"), a number in digits."""
    a, b = norm_words(text), norm_words(heard)
    diffs, missing, added, changed, critical = [], 0, 0, 0, 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        x, y = a[i1:i2], b[j1:j2]
        if op == "equal" or (op == "replace" and ("".join(x) == "".join(y) or _same_number(x, y))):
            continue
        diffs.append([op, " ".join(x), " ".join(y)])
        if op == "delete":
            missing += len(x)
        elif op == "insert":
            added += len(y)
        else:
            changed += max(len(x), len(y))
        if any(w in NEGATIONS or _numeric(w) for w in x + y) and not (op == "replace" and _contraction(x, y)):
            critical += 1
    return {"words": len(a), "missing": missing, "added": added, "changed": changed, "critical": critical,
            "diffs": diffs}


def passes(d: dict, max_diffs: int) -> bool:
    return d["critical"] == 0 and d["missing"] + d["added"] + d["changed"] <= max_diffs


def _score(d: dict) -> tuple:
    return (d["critical"], d["missing"] + d["added"] + d["changed"])


# --- following a reading (a port of page/align.js) ---------------------------------------------

NEAR, FAR, FAR_MIN_LEN = 4, 15, 4


class Tokens:
    """Transcript deltas in, whole raw words out, each with the start_ms of the delta it began in: a
    delta that doesn't end in a space leaves a partial word to join with the next ("list" + "ening")."""

    def __init__(self):
        self.partial, self.partial_ms = "", 0

    def push(self, delta: str, ms: int = 0) -> list[tuple[str, int]]:
        lead_ms = self.partial_ms if self.partial else ms
        text = self.partial + delta
        if not text or text[-1].isspace():
            whole, self.partial = text, ""
        else:
            m = re.search(r"\S+$", text)
            whole, self.partial = text[:m.start()], m.group()
            # the partial began in an earlier delta only if it was carried over and this delta adds no space
            self.partial_ms = lead_ms if m.start() == 0 else ms
        words = whole.split()
        if not words:
            return []
        first_carried = bool(text) and not text[0].isspace() and lead_ms != ms
        return [(w, lead_ms if i == 0 and first_carried else ms) for i, w in enumerate(words)]

    def flush(self) -> list[tuple[str, int]]:
        out = [(w, self.partial_ms) for w in self.partial.split()]
        self.partial = ""
        return out


def match(words: list[str], start: int, w: str) -> int:
    """The index just after `w` in `words`, looking from `start`; -1 if it isn't there."""
    near = min(len(words), start + NEAR + 1)
    for j in range(start, near):
        if words[j] == w:
            return j + 1
    for j in range(start, near - 1):
        if words[j] + words[j + 1] == w:
            return j + 2
    if len(w) >= FAR_MIN_LEN:
        for j in range(near, min(len(words), start + FAR + 1)):
            if words[j] == w:
                return j + 1
    return -1


def opens(words: list[str], w: str, k: int = 6) -> bool:
    return w in words[:k]


class Follow:
    """One paragraph followed through the transcript: started (an opening word heard; anything said
    before it is not this paragraph), the words matched so far, and what was heard from the opening on."""

    def __init__(self, text: str):
        self.words = norm_words(text)
        self.tokens = Tokens()
        self.pos = 0
        self.started = False
        self.start_ms: int | None = None
        self.count = 0          # words heard since the opening
        self.heard: list[str] = []

    def push(self, delta: str, start_ms: int) -> None:
        self._take(self.tokens.push(delta, start_ms))

    def flush(self) -> None:
        self._take(self.tokens.flush())

    def _take(self, raw: list[tuple[str, int]]) -> None:
        for tok, ms in raw:
            for w in norm_words(tok):
                if not self.started:
                    if not opens(self.words, w):
                        continue  # said before the paragraph opened: not this paragraph
                    self.started, self.start_ms = True, ms
                self.count += 1
                j = match(self.words, self.pos, w)
                if j >= 0:
                    self.pos = j
            if self.started:
                self.heard.append(tok)

    @property
    def done(self) -> bool:
        """Every word matched, and about as many heard: a long word found far ahead (match's FAR look)
        near the end must not end a paragraph still being read."""
        n, pos, count = len(self.words), self.pos, self.count
        for w in norm_words(self.tokens.partial):  # the last word stays partial: no space follows it
            if self.started and (j := match(self.words, pos, w)) >= 0:
                pos, count = j, count + 1
        return self.started and pos >= n and count >= 0.9 * n - 1


# --- audio -----------------------------------------------------------------------------------------

def frame_rms(samples: np.ndarray, frame: int) -> np.ndarray:
    n = len(samples) // frame
    if not n:
        return np.zeros(0)
    x = samples[: n * frame].astype(np.float64).reshape(n, frame)
    return np.sqrt((x ** 2).mean(axis=1))


def trim(samples: np.ndarray, hint: int) -> tuple[int, int] | None:
    """(first, end) sample indices of the take in `samples`: from the speech onset near `hint` (the
    first speech frame within [hint - ONSET_BACK, hint + ONSET_FWD] that follows ONSET_QUIET of quiet,
    so the tail of anything said before is left out) to the last speech frame, each with its pad.
    None if there is no speech."""
    f = int(RATE * FRAME)
    speech = frame_rms(samples, f) >= SPEECH_RMS
    if not speech.any():
        return None
    q = int(round(ONSET_QUIET / FRAME))
    lo = max(0, int((hint / RATE - ONSET_BACK) / FRAME))
    hi = min(len(speech), int((hint / RATE + ONSET_FWD) / FRAME) + 1)
    first = next((i for i in range(lo, hi) if speech[i] and not speech[max(0, i - q):i].any()), None)
    if first is None:
        first = next((i for i in range(lo, len(speech)) if speech[i]), None)
        if first is None:
            return None
    last = max(i for i in range(first, len(speech)) if speech[i])
    a = max(0, first * f - int(PAD_LEAD * RATE))
    b = min(len(samples), (last + 1) * f + int(PAD_TAIL * RATE))
    return a, b


def write_wav(path: Path, pcm: bytes, rate: int = RATE) -> None:
    """Mono PCM16, written beside and moved into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    os.replace(tmp, path)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# --- sessions --------------------------------------------------------------------------------------

@dataclass
class Take:
    pcm: bytes                 # PCM16 24 kHz mono, trimmed to speech with the pads; b"" if nothing was read
    heard: str                 # the output transcript from the paragraph's first word on
    start_ms: int | None       # session-relative, of the cut
    end_ms: int | None
    status: str = "done"       # done (every word heard), quiet (a quiet with most heard, or a long quiet),
                               # timeout, never_started
    matched: int = 0
    words: int = 0
    resent: bool = False

    @property
    def seconds(self) -> float:
        return len(self.pcm) / 2 / RATE


class SessionLost(Exception):
    """The connection dropped or stalled: open another session and read the paragraph again."""


def api_key() -> str:
    """The OpenAI key, from the environment or the repo's .env (never logged)."""
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise StageError("OPENAI_API_KEY is not set (in the environment or .env): GPT-Live can't record.")
    return key


class LiveSession:
    """One GPT-Live session over a server-side WebSocket, with a synchronous API (read, close) for a
    worker thread; the protocol runs on its own event loop in its own thread."""

    def __init__(self, cfg: dict, instructions: str, label: str = "", *, key: str | None = None,
                 url: str | None = None):
        rc = cfg["overnight"]["record"]
        self.model, self.voice, self.label = rc["model"], rc["voice"], label
        self.url = url or rc["url"]
        self.id: str | None = None
        self.expires_at: float | None = None
        self.usage_seconds: float | None = None
        self.usage_ratio = 0.0
        self.closed_seconds: float | None = None
        self.errors: list[dict] = []
        self._buf = bytearray()
        self._base = 0              # samples dropped from the front of _buf (already cut into takes)
        self._loud_ms = 0.0         # session ms at the end of the last audible frame
        self._deltas: list[dict] = []
        self._seq = 0
        self._t0: float | None = None
        self._last_event = time.monotonic()
        self._lost = False
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name=f"record-live-{label}", daemon=True)
        self.thread.start()
        try:
            self._call(self._open(instructions, key or api_key()), 30)
        except (StageError, SessionLost):
            self._shutdown()
            raise
        except Exception as e:  # network, refused handshake, timeout: another session may do
            self._shutdown()
            raise SessionLost(f"could not open a GPT-Live session: {type(e).__name__}: {e}") from None
        except BaseException:
            self._shutdown()
            raise

    # sync API ------------------------------------------------------------------------------------

    def read(self, text: str) -> Take:
        if self._lost:
            raise SessionLost("the session's connection was lost")
        return self._call(self._read(text), GIVE_UP_MS / 1000 * 3 + len(text.split()) * 1.5 + STALL_SECONDS)

    def close(self) -> float:
        try:
            self._call(self._close(), 10)
        except Exception:
            pass
        self._shutdown()
        return self.seconds

    @property
    def seconds(self) -> float:
        """Connected seconds so far (billed): the server's figure, or the wall clock if that is larger."""
        if self.closed_seconds is not None:
            return self.closed_seconds
        wall = time.monotonic() - self._t0 if self._t0 else 0.0
        return max(self.usage_seconds or 0.0, wall)

    def needs_new(self) -> bool:
        soon = self.expires_at is not None and self.expires_at - time.time() < REOPEN_EXPIRY_SECONDS
        return self._lost or soon or self.usage_ratio > REOPEN_USAGE_RATIO

    # internals -----------------------------------------------------------------------------------

    def _call(self, coro, timeout: float):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return fut.result(timeout)
        except TimeoutError:
            fut.cancel()
            self._lost = True
            raise SessionLost(f"no answer from GPT-Live within {timeout:.0f} s") from None

    def _shutdown(self) -> None:
        async def stop():
            for t in [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]:
                t.cancel()
            ws = getattr(self, "ws", None)
            if ws is not None:
                try:
                    await asyncio.wait_for(ws.close(), 3)
                except Exception:
                    pass
        if self.loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(stop(), self.loop).result(5)
            except Exception:
                pass
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        if not self.loop.is_running():
            self.loop.close()

    async def _send(self, typ: str, **kw) -> None:
        self._seq += 1
        await self.ws.send(json.dumps({"type": typ, "event_id": f"rec{self.label}_{self._seq}", **kw}))

    async def _open(self, instructions: str, key: str) -> None:
        import websockets

        self.ws = await websockets.connect(self.url, additional_headers={"Authorization": f"Bearer {key}"},
                                           max_size=None, open_timeout=20)
        await self._send("session.start", session={
            "model": self.model, "instructions": instructions,
            "audio": {"output": {"voice": self.voice}, "format": {"type": "audio/pcm", "rate": RATE}},
            "input": [], "store": False})
        while True:
            e = json.loads(await asyncio.wait_for(self.ws.recv(), 20))
            if e.get("type") == "session.started":
                s = e.get("session") or {}
                self.id, self.expires_at = s.get("id"), s.get("expires_at")
                break
            if e.get("type") == "error":
                err = e.get("error") or {}
                raise StageError(f"GPT-Live refused the session: {err.get('code')}: {err.get('message')}")
        self._t0 = time.monotonic()
        self._changed = asyncio.Event()
        self._reader = asyncio.create_task(self._events())
        self._pump = asyncio.create_task(self._silence())
        await self._send("session.input_audio.mute")

    async def _silence(self) -> None:
        """Input audio must keep flowing, silence included: 20 ms of zeros every 20 ms."""
        chunk = base64.b64encode(bytes(960)).decode()
        nxt = self.loop.time()
        try:
            while True:
                await self._send("session.input_audio.append", audio=chunk)
                nxt += 0.02
                await asyncio.sleep(max(0.0, nxt - self.loop.time()))
        except asyncio.CancelledError:
            raise
        except Exception:
            self._lost = True

    async def _events(self) -> None:
        try:
            async for raw in self.ws:
                self._last_event = time.monotonic()
                e = json.loads(raw)
                typ = e.get("type")
                if typ == "session.output_audio.delta":
                    pcm = base64.b64decode(e.get("delta") or "")
                    at = self._base + len(self._buf) // 2
                    self._buf += pcm
                    rms = frame_rms(np.frombuffer(pcm[: len(pcm) // 2 * 2], np.int16), RATE // 50)
                    loud = np.nonzero(rms > AUDIBLE_RMS)[0]
                    if len(loud):
                        self._loud_ms = (at + (loud[-1] + 1) * (RATE // 50)) / SAMPLES_PER_MS
                elif typ == "session.output_transcript.delta":
                    self._deltas.append({"start_ms": e.get("start_ms") or 0, "end_ms": e.get("end_ms") or 0,
                                         "delta": e.get("delta") or ""})
                elif typ == "session.usage.updated":
                    self.usage_seconds = (e.get("usage") or {}).get("seconds", self.usage_seconds)
                    self.usage_ratio = (e.get("context_window") or {}).get("usage_ratio", self.usage_ratio)
                elif typ == "session.closed":
                    self.closed_seconds = (e.get("usage") or {}).get("seconds")
                    if self.closed_seconds is None:
                        self.closed_seconds = self.seconds
                    self._lost = True
                elif typ == "error":
                    self.errors.append(e.get("error") or {})
                self._changed.set()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        self._lost = True
        self._changed.set()

    def _audio_ms(self) -> float:
        return (self._base + len(self._buf) // 2) / SAMPLES_PER_MS

    def _now_ms(self) -> float:
        wall = (time.monotonic() - self._t0) * 1000 - WALL_SLACK_MS
        return max(self._audio_ms(), wall)

    async def _wait(self) -> None:
        self._changed.clear()
        try:
            await asyncio.wait_for(self._changed.wait(), 0.05)
        except TimeoutError:
            pass

    async def _read(self, text: str) -> Take:
        follow, k = Follow(text), len(self._deltas)
        content = f"{READ} {text}"
        await self._send("session.commentary.append", delegation_id=None, content=content)
        first_sent = sent = self._now_ms()
        resent, status, last_end = False, None, 0.0
        while status is None:
            await self._wait()
            while k < len(self._deltas):
                d = self._deltas[k]
                k += 1
                follow.push(d["delta"], d["start_ms"])
                if follow.started:
                    last_end = max(last_end, float(d["end_ms"]))
            now = self._now_ms()
            if self._lost:
                raise SessionLost("the session ended or its connection dropped")
            if time.monotonic() - self._last_event > STALL_SECONDS:
                self._lost = True
                raise SessionLost(f"no event from GPT-Live for {STALL_SECONDS} s")
            if not follow.started:
                if now - first_sent >= GIVE_UP_MS:
                    status = "never_started"
                elif not resent and now - sent >= RESEND_MS:
                    await self._send("session.commentary.append", delegation_id=None, content=content)
                    resent, sent = True, now
                continue
            quiet = now - max(last_end, self._loud_ms)
            if follow.done and quiet >= TAIL_QUIET_MS:
                status = "done"
            elif quiet >= QUIET_MS and follow.pos >= MOST * len(follow.words):
                status = "quiet"
            elif quiet >= LONG_QUIET_MS:
                status = "quiet"
        follow.flush()
        if status == "never_started":
            return Take(b"", "", None, None, status, 0, len(follow.words), resent)
        return self._cut(follow, status, resent)

    def _cut(self, follow: Follow, status: str, resent: bool) -> Take:
        """The take from the session audio: from start_ms * 24 samples, trimmed to speech. Everything up
        to now is then dropped from the buffer (the next paragraph starts after it)."""
        samples = np.frombuffer(bytes(self._buf[: len(self._buf) // 2 * 2]), np.int16)
        hint = int((follow.start_ms or 0) * SAMPLES_PER_MS) - self._base
        span = trim(samples, max(0, hint))
        heard = " ".join(follow.heard)
        drop = len(samples)
        if span is None:
            out = Take(b"", heard, None, None, status, follow.pos, len(follow.words), resent)
        else:
            a, b = span
            out = Take(samples[a:b].tobytes(), heard, round((self._base + a) / SAMPLES_PER_MS),
                       round((self._base + b) / SAMPLES_PER_MS), status, follow.pos, len(follow.words), resent)
        del self._buf[: drop * 2]
        self._base += drop
        return out

    async def _close(self) -> None:
        if self._lost:
            return
        await self._send("session.close")
        deadline = time.monotonic() + 4
        while self.closed_seconds is None and time.monotonic() < deadline:
            await self._wait()


# --- the stage -------------------------------------------------------------------------------------

def _groups(todo: list[dict], n: int) -> list[list[dict]]:
    """Contiguous runs of whole sections, about equal in length, at most n of them."""
    sections: list[list[dict]] = []
    for p in todo:
        if sections and sections[-1][0]["seg"] == p["seg"]:
            sections[-1].append(p)
        else:
            sections.append([p])
    k = max(1, min(n, len(sections)))
    total = sum(len(p["text"]) for p in todo)
    groups: list[list[dict]] = [[]]
    done_chars = 0
    for i, sec in enumerate(sections):
        left_sections, left_groups = len(sections) - i, k - len(groups) + 1
        if groups[-1] and (done_chars >= total * len(groups) / k or left_sections < left_groups):
            groups.append([])
        groups[-1] += sec
        done_chars += sum(len(p["text"]) for p in sec)
    return groups


@dataclass
class _Lane:
    """One worker's session and the seconds of it already logged."""
    label: str
    session: object = None
    logged: float = 0.0
    reconnects: int = 0
    opened: list = field(default_factory=list)


class _Recorder:
    def __init__(self, r: Run, cfg: dict, factory):
        self.r, self.cfg, self.factory = r, cfg, factory
        self.rc = cfg["overnight"]["record"]
        self.retakes = int(self.rc.get("retakes", 3))
        self.max_diffs = int(self.rc.get("max_word_diffs", 2))
        self.price = float(self.rc.get("price_per_minute", 0.05))
        self.audio = r.path("audio_dir")
        self.instructions = instructions(cfg)
        self.stop = threading.Event()
        self.errors: list[BaseException] = []

    # costs ---------------------------------------------------------------------------------------

    def log_cost(self, lane: _Lane, final: bool = False, what: str | None = None) -> None:
        s = lane.session
        if s is None:
            return
        total = s.seconds
        delta = max(0.0, total - lane.logged)
        if not delta and not final:
            return
        lane.logged += delta
        rec = {"stage": STAGE, "provider": "openai", "model": self.rc["model"], "session": s.id or lane.label,
               "seconds": round(delta, 2), "cost_usd": round(delta / 60 * self.price, 5)}
        if what:
            rec["paragraph"] = what
        if final:
            rec["closed"] = True
            rec["session_seconds"] = round(total, 2)
        self.r.log(rec)

    def check_cap(self) -> None:
        r = self.r
        if r.max_usd is not None and r.spent() >= r.max_usd:
            raise StageError(f"Recording stopped at the runaway stop: ${r.spent():.2f} spent of ${r.max_usd:.2f}.")

    # sessions ------------------------------------------------------------------------------------

    def open(self, lane: _Lane) -> None:
        lane.session = self.factory(self.cfg, self.instructions, lane.label)
        lane.logged = 0.0
        lane.opened.append(getattr(lane.session, "id", None))

    def close(self, lane: _Lane) -> None:
        s = lane.session
        if s is None:
            return
        try:
            s.close()
        finally:
            self.log_cost(lane, final=True)
            lane.session = None

    # one paragraph -------------------------------------------------------------------------------

    def next_take(self, pid: str) -> int:
        taken = [int(m[1]) for p in (self.audio / "takes").glob(f"{pid}.t*.wav")
                 if (m := re.fullmatch(rf"{re.escape(pid)}\.t(\d+)\.wav", p.name))]
        return max(taken, default=0) + 1

    def record(self, lane: _Lane, p: dict) -> None:
        best = None
        attempts = 0
        while attempts <= self.retakes:
            if self.stop.is_set():
                return
            self.check_cap()
            try:
                if lane.session is None or lane.session.needs_new():
                    self.close(lane)
                    self.open(lane)
                take = lane.session.read(p["text"])
            except SessionLost as e:
                lane.reconnects += 1
                self.close(lane)
                if lane.reconnects > MAX_RECONNECTS:
                    raise StageError(f"GPT-Live failed {lane.reconnects} times in one lane (last at paragraph "
                                     f"{p['id']}: {e}).") from None
                continue
            attempts += 1
            d = deviation(p["text"], take.heard)
            ok = bool(take.pcm) and passes(d, self.max_diffs)
            k = self.next_take(p["id"])
            info = {"id": p["id"], "take": k, "session": lane.session.id, "status": take.status,
                    "heard": take.heard, "deviation": d, "passed": ok, "resent": take.resent,
                    "times": {"start_ms": take.start_ms, "end_ms": take.end_ms},
                    "seconds": round(take.seconds, 3), "t": time.strftime("%Y-%m-%dT%H:%M:%S")}
            if take.pcm:
                write_wav(self.audio / "takes" / f"{p['id']}.t{k}.wav", take.pcm)
            write_json(self.audio / "takes" / f"{p['id']}.t{k}.json", info)
            with _FILE_LOCK:
                with open(self.audio / "recording.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(info, ensure_ascii=False) + "\n")
            if take.pcm and (best is None or _score(d) < _score(best[1]["deviation"])):
                best = (take, info)
            if ok:
                break
            if take.status == "never_started":  # the session may be stuck: the next attempt gets a new one
                self.close(lane)
        self.log_cost(lane, what=p["id"])
        if best is None:
            raise StageError(f"GPT-Live never read paragraph {p['id']} ({attempts} attempts).")
        take, info = best
        write_json(self.audio / "paragraphs" / f"{p['id']}.json",
                   {**info, "seg": p["seg"], "pause": p["pause"], "text": p["text"], "takes": attempts})
        write_wav(self.audio / "paragraphs" / f"{p['id']}.wav", take.pcm)  # last: it marks the paragraph done

    def lane(self, label: str, group: list[dict]) -> None:
        lane = _Lane(label)
        try:
            for p in group:
                if self.stop.is_set():
                    break
                self.record(lane, p)
        except BaseException as e:  # noqa: BLE001 - every failure stops the other lanes, and is raised once
            self.errors.append(e)
            self.stop.set()
        finally:
            try:
                self.close(lane)
            except Exception:
                pass


def result(r: Run, paras: list[dict]) -> dict:
    """recorded.json: from the paragraph files and every take and session logged, this run and earlier."""
    audio = r.path("audio_dir")
    chosen = [json.loads((audio / "paragraphs" / f"{p['id']}.json").read_text(encoding="utf-8")) for p in paras]
    takes = 0
    if (audio / "recording.jsonl").exists():
        takes = sum(1 for line in (audio / "recording.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())
    costs = [x for x in r.read_log() if x.get("stage") == STAGE and x.get("provider") == "openai"]
    return {
        "paragraphs": len(paras),
        "passed": sum(1 for c in chosen if c["passed"]),
        "flagged": [{"id": c["id"], "diffs": c["deviation"]["diffs"]} for c in chosen if not c["passed"]],
        "retakes_total": max(0, takes - len(paras)),
        "sessions": len({x.get("session") for x in costs}),
        "seconds": round(sum(x.get("seconds", 0) or 0 for x in costs), 1),
        "cost_usd": round(sum(x.get("cost_usd", 0) or 0 for x in costs), 4),
        "audio_seconds": round(sum(c.get("seconds", 0) or 0 for c in chosen), 1),
    }


def run(r: Run, cfg: dict, session_factory=None) -> dict:
    """Record every paragraph not yet recorded; write recorded.json once all are. Returns it."""
    done_path = r.path("audio_dir") / "recorded.json"
    if done_path.exists():
        return json.loads(done_path.read_text(encoding="utf-8"))
    paras = paragraphs(r)
    ids = [p["id"] for p in paras]
    if len(set(ids)) != len(ids):
        raise StageError("Two paragraphs share an id: the script's segment markers repeat a number.")
    rec = _Recorder(r, cfg, session_factory or LiveSession)
    todo = [p for p in paras if not (rec.audio / "paragraphs" / f"{p['id']}.wav").exists()]
    if todo:
        groups = _groups(todo, int(rec.rc.get("sessions", 4)))
        threads = [threading.Thread(target=rec.lane, args=(f"{i + 1}", g), name=f"record-lane-{i + 1}")
                   for i, g in enumerate(groups)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if rec.errors:
            raise rec.errors[0]
    out = result(r, paras)
    write_json(done_path, out)
    return out
