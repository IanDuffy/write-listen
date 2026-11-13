"""Narration in pieces: a locked section is narrated paragraph by paragraph, and each piece is
playable the moment it lands, so playback starts before the section is synthesized (step 3).

stages/tts.py narrate_segments is whole-job shaped (it returns when the MP3 is joined) and stays
as it is for overnight. This is a paragraph loop over the same primitives: the same text cleaning
and bracket checks, tts.synthesize with request stitching, the same pauses.

    10_audio/secNN/pKK.wav      one piece, speech only (what the section and the chapter are joined from)
    10_audio/secNN/pKK.json     request id, text sent, ElevenLabs' character timings, the pause before it
    10_audio/secNN/pKK.mp3      the pause, then the speech: what the page plays, piece after piece
    10_audio/secNN/section.mp3  the section, joined when its last piece lands (concat.txt beside it)
    10_audio/secNN/script.md    the text that was narrated, under its <!-- segNN --> marker
    10_audio/secNN/section.json written last: the section is narrated
    11_episode/sections/segNN.json   the section's word timing, in section time

A piece's pause is part of the piece (and so of its section): the gap the listener hears between two
pieces is the gap the archive joins into the chapter, and the chapter is exactly as long as its
pieces. The timing comes from the character timings ElevenLabs returns with each piece (the pKK.json
files satisfy episode.timing.ElevenLabsTimings), so no Whisper runs while a listener waits.
"""

from __future__ import annotations

import os
import re
import time
import wave
from dataclasses import dataclass
from pathlib import Path

from episode import timing
from stages import tts
from stages.common import LEFTOVER_MARKER, Run, StageError, join_script_segments, spoken_text
from stages.llm import guard

from . import base

STAGE = "fast_narrate"
BRACKET = re.compile(r"\[[^\]\n]{0,40}\]")  # after [pause] and the editorial forms are gone: the voice would say it
RETRY_WAITS = (2, 6)  # seconds before a second and a third try when ElevenLabs is busy or unreachable
REQUEST_SECONDS = 0.6  # allowed for a synthesis request itself, on top of its characters at synth_chars_per_second
CHARS_PER_WORD = 6  # a spoken word and its space, a little high (the 20-minute chapters ran at about 5.7)


@dataclass
class Piece:
    section: int      # 1-based
    index: int        # 0-based within the section
    wav: Path         # speech only
    mp3: Path         # pause, then speech
    seconds: float    # pause + speech, exactly as the section and the chapter are joined (the MP3 matches it)
    pause_ms: int     # silence before the speech
    chars: int
    text: str         # what was sent to the voice
    simulated: bool
    last: bool = False  # the section's final piece


# --- the plan: what is sent, piece by piece --------------------------------------------------

def _fit(paragraph: str, max_chars: int) -> list[str]:
    """A paragraph as pieces of at most max_chars, split at sentences (a sentence longer than that,
    which nobody is there to fix, is cut at a space)."""
    if len(paragraph) <= max_chars:
        return [paragraph]
    sentences = []
    for s in tts.split_sentences(paragraph):
        while len(s) > max_chars:
            cut = s.rfind(" ", 0, max_chars + 1)
            cut = cut if cut > 0 else max_chars
            sentences.append(s[:cut].strip())
            s = s[cut:].strip()
        if s:
            sentences.append(s)
    out, cur = [], ""
    for s in sentences:
        if cur and len(cur) + 1 + len(s) > max_chars:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}" if cur else s
    return out + [cur] if cur else out


def _head(paragraph: str, max_chars: int) -> tuple[str, str]:
    """The paragraph if it is short, else its first sentences up to max_chars (always at least one
    whole sentence). Returns (the piece, the rest of the paragraph)."""
    if len(paragraph) <= max_chars:
        return paragraph, ""
    sentences = tts.split_sentences(paragraph)
    k, size = 1, len(sentences[0])
    while k < len(sentences) and size + 1 + len(sentences[k]) <= max_chars:
        size += 1 + len(sentences[k])
        k += 1
    return " ".join(sentences[:k]), " ".join(sentences[k:])


def _take(paragraphs: list[str], max_chars: int, min_chars: int) -> tuple[str, list[str]]:
    """The next piece, from the front of the paragraphs: at most max_chars (but always one whole
    sentence), short paragraphs together until it reaches min_chars. Returns (the piece, what is left)."""
    piece, rest = _head(paragraphs[0], max_chars)
    left = ([rest] if rest else []) + paragraphs[1:]
    while not rest and left and len(piece) < min_chars and len(piece) + 2 + len(left[0]) <= max_chars:
        piece, left = f"{piece}\n\n{left[0]}", left[1:]
    return piece, left


def plan(cfg: dict, n: int, text: str) -> list[tuple[str, int]]:
    """Section n as [(the text to send, the pause before it in ms)]. The text is cleaned exactly as
    narrate_segments cleans it, and anything the voice would read aloud (a leftover dossier marker, a
    bracket other than [pause] and a quote's editorial ones) raises StageError: before any synthesis.

    The chapter's first piece is one short paragraph, so the first audio comes soon. After that short
    paragraphs go together up to fast.narration.piece_min_chars. Pieces never cross a [pause].

    Pieces are made one after another while the ones before them play, so no piece is longer than
    can be made (REQUEST_SECONDS, then its characters at synth_chars_per_second) in the playing time
    of the audio queued ahead of it (speech_chars_per_second; a listener who was waiting starts
    playing as the section's first piece lands). Sizes grow as that lead builds: after a one-line
    opening the next piece is a sentence or two, not a long paragraph the player would run dry
    waiting for. One whole sentence is the smallest piece."""
    ncfg, f = cfg["narration"], base.settings(cfg)["narration"]
    speech, synth = f["speech_chars_per_second"], f["synth_chars_per_second"]
    clean = tts.apply_pronunciations(spoken_text(text), ncfg.get("pronunciations") or {})
    if m := LEFTOVER_MARKER.search(clean):
        raise StageError(f"Section {n} still has a dossier marker after stripping, near "
                         f"{clean[max(0, m.start() - 40):m.start() + 20]!r}.")
    out: list[tuple[str, int]] = []
    lead = 0.0  # seconds of this section's audio queued ahead of the player when the next piece starts being made
    for beat in tts.split_beats(clean):
        beat = tts.speak_editorial_brackets(beat)
        if m := BRACKET.search(beat):
            raise StageError(f"Section {n} has a bracketed tag the narrator would read aloud: {m.group()!r}. "
                             "Only [pause] is understood.")
        paragraphs = [p for para in re.split(r"\n\s*\n", beat) if para.strip()
                      for p in _fit(para.strip(), f["piece_max_chars"])]
        opens_beat = True
        while paragraphs:
            if not out:  # the section's first piece: nothing before the chapter, a section break before the rest
                pause = 0 if n == 1 else ncfg["segment_pause_ms"]
                limit, least = (f["first_piece_max_chars"], 0) if n == 1 else (f["piece_max_chars"], f["piece_min_chars"])
            else:
                pause = ncfg.get("beat_pause_ms", 1100) if opens_beat else ncfg["chunk_pause_ms"]
                limit = min(f["piece_max_chars"], int((lead - REQUEST_SECONDS) * synth))
                least = f["piece_min_chars"]
            piece, paragraphs = _take(paragraphs, limit, least)
            # It plays for its pause and its speech. Making it used up that much of the lead (not the
            # section's first piece: playback starts when that one lands).
            lead += pause / 1000 + len(piece) / speech - ((REQUEST_SECONDS + len(piece) / synth) if out else 0)
            out.append((piece, pause))
            opens_beat = False
    if not out:
        raise StageError(f"Section {n} has no text to narrate.")
    return out


# --- what is on disk -------------------------------------------------------------------------

def _stem(r: Run, n: int, i: int) -> Path:
    return base.section_dir(r, n) / f"p{i:02d}"


def _meta(r: Run, n: int, i: int) -> dict | None:
    """A piece's .json, if the piece is whole (the .json is written last, after its WAV and MP3)."""
    stem = _stem(r, n, i)
    meta = base.read_json(stem.with_suffix(".json"))
    if not meta or not stem.with_suffix(".wav").exists() or not stem.with_suffix(".mp3").exists():
        return None
    return meta


def _piece(r: Run, n: int, i: int, meta: dict) -> Piece:
    stem = _stem(r, n, i)
    return Piece(section=n, index=i, wav=stem.with_suffix(".wav"), mp3=stem.with_suffix(".mp3"),
                 seconds=meta["seconds"], pause_ms=meta["pause_ms"], chars=len(meta["text"]), text=meta["text"],
                 simulated=bool(meta.get("simulated")), last=bool(meta.get("last")))


def pieces(r: Run, n: int) -> list[Piece]:
    """The playable pieces of section n, in order (while it is being narrated: those so far)."""
    out = []
    while (meta := _meta(r, n, len(out))) is not None:
        out.append(_piece(r, n, len(out), meta))
    return out


def narrated(r: Run, n: int) -> bool:
    """Every piece of section n is on disk and joined."""
    d = base.section_dir(r, n)
    return (d / "section.json").exists() and (d / "section.mp3").exists()


def section_timing(r: Run, n: int) -> dict | None:
    """The section's word timing in section time (0 is the start of its first piece's pause), or None
    while there is none."""
    return base.read_json(base.path(r, "section_timings") / f"seg{n:02d}.json")


# --- audio -----------------------------------------------------------------------------------

def _play_mp3(wav: Path, pause_ms: int, mp3: Path, bitrate: str) -> None:
    """The MP3 the page plays for a piece: its pause as silence, then the speech."""
    src, tmp = wav, mp3.with_name(mp3.name + ".tmp")
    if pause_ms:
        src = wav.with_name(wav.stem + ".play.wav")
        with wave.open(str(wav), "rb") as w:
            params, frames = w.getparams(), w.readframes(w.getnframes())
        with wave.open(str(src), "wb") as w:
            w.setparams(params)
            w.writeframes(b"\x00" * (int(params.framerate * pause_ms / 1000) * params.sampwidth * params.nchannels))
            w.writeframes(frames)
    try:
        tts.ffmpeg(["-i", str(src), "-c:a", "libmp3lame", "-b:a", bitrate, "-f", "mp3", str(tmp)])
        os.replace(tmp, mp3)
    finally:
        tmp.unlink(missing_ok=True)
        if src != wav:
            src.unlink(missing_ok=True)


def _rate(output_format: str) -> int:
    m = re.search(r"_(\d+)", output_format)
    return int(m.group(1)) if m else 24000


def even_alignment(text: str, seconds: float) -> dict:
    """Character timings in ElevenLabs' shape, every character the same length."""
    step = seconds / max(1, len(text))
    return {"characters": list(text),
            "character_start_times_seconds": [round(i * step, 3) for i in range(len(text))],
            "character_end_times_seconds": [round((i + 1) * step, 3) for i in range(len(text))]}


def simulated(cfg: dict, sleep=time.sleep):
    """A stand-in synth for build tests, where ElevenLabs characters are scarce: silence as long as the
    text would take to say (fast.narration.speech_chars_per_second), returned after as long as
    ElevenLabs would take to make it (synth_chars_per_second), with an even character alignment. So a
    run's timeline and the section lengths stay true while nothing is spent."""
    f = base.settings(cfg)["narration"]

    def synth(text: str, *, voice_id=None, ncfg=None, **kw) -> tts.Synth:
        seconds = len(text) / f["speech_chars_per_second"]
        sleep(len(text) / f["synth_chars_per_second"])
        rate = _rate((ncfg or cfg["narration"])["output_format"])
        return tts.Synth(audio=b"\x00\x00" * int(rate * seconds), request_id=None,
                         alignment=even_alignment(text, seconds))
    return synth


class PieceTimings(timing.ElevenLabsTimings):
    """ElevenLabs' character timings where a piece kept them. A piece that came back without them
    (the SDK allows it) gives no words, so the aligner marks that piece's words estimated; with
    `spread` it gives the text it was sent, evenly over its own audio."""

    def __init__(self, spread: bool = False):
        self.spread = spread

    def __call__(self, wav: Path) -> list[dict]:
        a = timing.chunk_alignment(wav)
        if a is None and self.spread:
            text = (base.read_json(wav.with_suffix(".json")) or {}).get("text") or ""
            a = even_alignment(text, timing.audio_duration(wav))
        return timing.alignment_words(a) if a else []


def timed(run: Path, pronunciations: dict[str, str], audio_rel: str = "10_audio/chapter.mp3",
          script_rel: str = "09_script_final.md", cache: bool = True) -> dict | None:
    """Word timing for a joined file (a section, or the chapter) from its pieces' character timings,
    in episode.timing.build's shape; None when no piece kept them (only Whisper can time it then).
    One piece without them does not cost the rest their timing: its words are marked estimated, and
    placed evenly over that piece's own audio (the aligner alone would run them into the pauses
    either side, or past the end of the audio when nothing follows)."""
    speech = [p["path"] for p in timing.audio_pieces(run, run / audio_rel) if p["speech"]]
    kept = [bool(timing.chunk_alignment(p)) for p in speech]
    if not any(kept):
        return None
    t = timing.build(run, PieceTimings(), pronunciations, audio_rel=audio_rel, script_rel=script_rel, cache=cache)
    if not all(kept):
        placed = timing.build(run, PieceTimings(spread=True), pronunciations, audio_rel=audio_rel,
                              script_rel=script_rel, cache=cache)
        words = lambda x: [w for seg in x["segments"] for sent in seg["sentences"] for w in sent["words"]]
        for marked, w in zip(words(t), words(placed)):
            if marked.get("estimated"):
                w["estimated"] = True
        t = {**placed, "stats": t["stats"]}
    return t


# --- the ElevenLabs balance (for the driver, before a run pays for anything) --------------------

def balance() -> tuple[int, int] | None:
    """(characters used this period, the plan's limit) on the ElevenLabs account, read as
    stages/ping.py reads it. None if it cannot be read: a failed read must not stop a run."""
    try:
        sub = tts.el_call(tts.elevenlabs_client().user.subscription.get)
        return int(sub.character_count), int(sub.character_limit)
    except (StageError, AttributeError, TypeError, ValueError):
        return None


def needed_chars(cfg: dict) -> int:
    """The characters a fast chapter's narration may take, at the upper end: the longest chapter
    fast.total_minutes allows, at fast.words_per_minute and CHARS_PER_WORD."""
    f = base.settings(cfg)
    return int(max(f["total_minutes"]) * f["words_per_minute"] * CHARS_PER_WORD)


# --- the narrator ----------------------------------------------------------------------------

class Narrator:
    """One per run: it carries the request ids from piece to piece and from section to section, so
    the voice is continuous across them. Not for two threads at once (narration is one lane, strictly
    in order: a piece needs the id of the one before it)."""

    def __init__(self, r: Run, cfg: dict, synth=None, sleep=time.sleep):
        """synth(text, *, voice_id, ncfg, previous_request_ids, previous_text, next_text, timestamps)
        -> tts.Synth; by default tts.synthesize, looked up at each call. With
        fast.narration.simulate_after_chars set, once the next piece would take the run's real
        characters past that number, it and every later piece use simulated(). A run that has
        simulated pieces stays simulated while that setting is there, and is refused without it."""
        self.r, self.cfg, self.synth, self.sleep = r, cfg, synth, sleep
        self.ncfg, self.f = cfg["narration"], base.settings(cfg)["narration"]
        self.prev_ids: list[str] = []
        self.prev_text: str | None = None
        self._chained = False  # the chain has been picked up from the pieces on disk (a restart)
        metas = [m for p in sorted(r.path("audio_dir").glob("sec*/p*.json")) if (m := base.read_json(p))]
        self.real_chars = sum(len(m["text"]) for m in metas if not m.get("simulated"))
        self.simulating = any(m.get("simulated") for m in metas)
        if self.simulating and self.f.get("simulate_after_chars") is None:
            # Carrying on would keep the silent pieces as the voice (a piece is reused by its text) and
            # sections already narrated as silence are never narrated again: half a chapter of silence.
            raise StageError(f"{r.name} has simulated narration (a build test), so it cannot be continued as a real "
                             f"chapter. Continue it as a build test (python -m faststart e2e --name {r.name}, "
                             "with --narrate sim or first), or start a new run.")

    def section(self, n: int, text: str, on_piece=None) -> list[Piece]:
        """Narrate locked section n, calling on_piece(piece) as each piece becomes playable: once per
        piece, in order, pieces reused from disk included (a caller that restored them from pieces()
        tells them apart by piece.index). The last one has piece.last set. Strictly sequential.
        Returns when the section is joined and timed."""
        voice_id = self.ncfg.get("voice_id")
        if not voice_id:
            raise StageError("No narrator voice set. Run the voice test, then set narration.voice_id in config.yaml.")
        todo = plan(self.cfg, n, text)  # raises before anything is paid for
        d = base.section_dir(self.r, n)
        d.mkdir(parents=True, exist_ok=True)
        if not self._chained:
            self._pick_up_chain(n)

        kept = [self._kept(n, i, t) for i, (t, _) in enumerate(todo)]
        new_chars = sum(len(t) for (t, _), meta in zip(todo, kept) if meta is None)
        if (limit := self.f.get("simulate_after_chars")) is not None:  # only the real characters cost anything
            new_chars = 0 if self.simulating else min(new_chars, max(0, limit - self.real_chars))
        if new_chars:  # silence, and pieces already on disk, cost nothing: a spent cap must not refuse them
            guard(self.r, self.cfg, f"narrating section {n}", new_chars * self.ncfg["price_per_1000_chars"] / 1000)

        out, changed = [], False
        for i, (t, pause) in enumerate(todo):
            last = i == len(todo) - 1
            meta = kept[i]
            if meta is None:
                nxt = todo[i + 1][0] if not last else None
                meta = self._make(n, i, t, nxt, voice_id, len(todo))
                changed = True
            playable = _stem(self.r, n, i).with_suffix(".mp3").exists()
            if not playable or (meta["pause_ms"], bool(meta.get("last"))) != (pause, last):
                meta = self._publish(n, i, {**meta, "pause_ms": pause, "last": last})
                changed = True
            rid = meta.get("request_id")
            self.prev_ids = (self.prev_ids + [rid])[-3:] if rid else []
            self.prev_text = t
            piece = _piece(self.r, n, i, meta)
            out.append(piece)
            if on_piece:
                on_piece(piece)
        for stale in d.glob("p*.*"):  # pieces past the end of this text, from an earlier one
            if (m := re.fullmatch(r"p(\d+)\.(?:wav|json|mp3)", stale.name)) and int(m.group(1)) >= len(todo):
                stale.unlink()
                changed = True

        script = join_script_segments([(n, text)])
        script_path = d / "script.md"
        if changed or not narrated(self.r, n) or not script_path.exists() or script_path.read_text("utf-8") != script:
            self._finish(n, script, out)
        return out

    # --- one piece ---

    def _kept(self, n: int, i: int, text: str) -> dict | None:
        """The piece on disk, if it is this text (a resumed run pays for nothing twice)."""
        stem = _stem(self.r, n, i)
        meta = base.read_json(stem.with_suffix(".json"))
        return meta if meta and meta.get("text") == text and stem.with_suffix(".wav").exists() else None

    def _simulate(self, chars: int) -> bool:
        limit = self.f.get("simulate_after_chars")
        if limit is not None and not self.simulating and self.real_chars + chars > limit:
            self.simulating = True
        return self.simulating

    def _call(self, synth, text: str, nxt: str | None, voice_id: str) -> tts.Synth:
        """One synthesis, tried again when ElevenLabs is busy or unreachable: a listener is waiting.
        If it refuses the request ids (they expire), the piece before is sent as text instead."""
        kw = dict(voice_id=voice_id, ncfg=self.ncfg, previous_request_ids=self.prev_ids or None,
                  previous_text=None if self.prev_ids else (self.prev_text[-500:] if self.prev_text else None),
                  next_text=nxt[:500] if nxt else None, timestamps=True)
        waits = list(RETRY_WAITS)
        while True:
            try:
                return synth(text, **kw)
            except tts.ElevenLabsError as e:
                if e.status in (400, 404, 422) and kw["previous_request_ids"]:
                    self.prev_ids = []
                    kw["previous_request_ids"] = None
                    kw["previous_text"] = self.prev_text[-500:] if self.prev_text else None
                elif (e.status is None or e.status == 429 or e.status >= 500) and waits:
                    self.sleep(waits.pop(0))
                else:
                    raise

    def _make(self, n: int, i: int, text: str, nxt: str | None, voice_id: str, total: int) -> dict:
        """Synthesize one piece: its WAV and the facts for its .json (published by _publish)."""
        sim = self._simulate(len(text))
        synth = simulated(self.cfg, self.sleep) if sim else (self.synth or tts.synthesize)
        t0 = time.monotonic()
        syn = self._call(synth, text, nxt, voice_id)
        took = time.monotonic() - t0
        stem = _stem(self.r, n, i)
        stem.with_suffix(".json").unlink(missing_ok=True)  # an older piece here is no longer whole
        tmp = stem.with_name(stem.name + ".tmp.wav")
        fmt = self.ncfg["output_format"]
        tts.write_wav(tmp, syn.audio, f"pcm_{_rate(fmt)}" if sim else fmt)
        os.replace(tmp, stem.with_suffix(".wav"))
        if not sim:
            self.real_chars += len(text)
        cost = 0.0 if sim else len(text) * self.ncfg["price_per_1000_chars"] / 1000
        self.r.log({"stage": STAGE, "label": f"section {n} piece {i + 1}/{total}",
                    "provider": "simulated" if sim else "elevenlabs", "model": self.ncfg["model_id"],
                    "voice_id": voice_id, "characters": len(text), "request_id": syn.request_id,
                    "cost_usd": round(cost, 5), "seconds": round(took, 2)})
        return {"request_id": syn.request_id, "segment": n, "index": i, "text": text, "alignment": syn.alignment,
                "pause_ms": None, "seconds": None, "simulated": sim, "last": False}

    def _publish(self, n: int, i: int, meta: dict) -> dict:
        """The piece's MP3, then its .json: once that is there the piece is playable."""
        stem = _stem(self.r, n, i)
        wav = stem.with_suffix(".wav")
        _play_mp3(wav, meta["pause_ms"], stem.with_suffix(".mp3"), self.ncfg["mp3_bitrate"])
        meta["seconds"] = round(meta["pause_ms"] / 1000 + timing.audio_duration(wav), 3)
        base.write_json(stem.with_suffix(".json"), meta)
        return meta

    def _pick_up_chain(self, n: int) -> None:
        """After a restart: the request ids of the sections already narrated, as if they had just been."""
        self._chained = True
        for k in range(1, n):
            i = 0
            while (meta := _meta(self.r, k, i)) is not None:
                rid = meta.get("request_id")
                self.prev_ids = (self.prev_ids + [rid])[-3:] if rid else []
                self.prev_text = meta["text"]
                i += 1

    # --- the section, once its last piece has landed ---

    def _finish(self, n: int, script: str, out: list[Piece]) -> None:
        d = base.section_dir(self.r, n)
        (d / "section.json").unlink(missing_ok=True)
        parts: list[Path | int] = []
        for p in out:
            if p.pause_ms:
                parts.append(p.pause_ms)
            parts.append(p.wav)
        tts.join_to_mp3(parts, d / "section.mp3", d, self.ncfg["mp3_bitrate"])
        (d / "joined.wav").unlink(missing_ok=True)  # 48 kB a second, and nothing reads it
        base.write_atomic(d / "script.md", script)
        seconds = round(sum(p.seconds for p in out), 3)
        timed = self._time(n, seconds)
        base.write_json(d / "section.json", {"section": n, "pieces": len(out), "seconds": seconds,
                                             "chars": sum(p.chars for p in out), "timing": timed})

    def _time(self, n: int, seconds: float) -> bool:
        """The section's word timing, from the pieces' character timings (timed(): a piece without
        them is estimated). If it cannot be built the section still plays (the page shows its text
        without the highlight) and the archive times the chapter itself; the reason is logged."""
        d = base.section_dir(self.r, n)
        out = base.path(self.r, "section_timings") / f"seg{n:02d}.json"
        here = str(d.relative_to(self.r.root))
        try:
            t = timed(self.r.root, self.ncfg.get("pronunciations") or {}, audio_rel=f"{here}/section.mp3",
                      script_rel=f"{here}/script.md", cache=False)
            if t is None:
                raise StageError("no piece came back with character timings")
        except Exception as e:  # whatever went wrong, the audio is good: timing must not stop the chapter
            out.unlink(missing_ok=True)
            self.r.log({"stage": STAGE, "label": f"section {n} timing", "error": f"{type(e).__name__}: {e}"})
            print(f"  (no word timing for section {n}: {e})")
            return False
        base.write_json(out, {**t, "section": n, "seconds": seconds})
        return True
