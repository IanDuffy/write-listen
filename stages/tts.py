"""Narration, kept behind one function so the provider can be swapped.

synthesize() is the only provider-specific call. Everything else (chunking,
pronunciation, caching chunks on disk, joining with pauses, MP3 export) works
on plain text and WAV files.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from .common import LEFTOVER_MARKER, ROOT, Run, StageError, spoken_text
from .llm import guard

# --- provider ---------------------------------------------------------------

_el_client = None


def elevenlabs_client():
    global _el_client
    if _el_client is None:
        from dotenv import load_dotenv
        from elevenlabs.client import ElevenLabs

        load_dotenv(ROOT / ".env")
        # In a cloud session the network proxy injects the saved key into every request
        # to api.elevenlabs.io and overwrites this header, so a placeholder is enough.
        key = os.environ.get("ELEVENLABS_API_KEY") or "proxy-injected"
        # Pass the key explicitly: the SDK's default is read at import time.
        _el_client = ElevenLabs(api_key=key, timeout=300)
    return _el_client


class ElevenLabsError(StageError):
    """An ElevenLabs API error, with its HTTP status (None when the service could not be reached)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def el_call(fn, *args, **kwargs):
    """Call the ElevenLabs SDK, turning its errors into a StageError with a readable message."""
    import httpx
    from elevenlabs.core.api_error import ApiError

    try:
        return fn(*args, **kwargs)
    except ApiError as e:
        hint = (" Check the ElevenLabs key: ELEVENLABS_API_KEY in .env locally, or the saved "
                "network secret for api.elevenlabs.io in a cloud session.") if e.status_code in (401, 403) else ""
        raise ElevenLabsError(f"ElevenLabs error {e.status_code}: {e.body}.{hint}", e.status_code) from None
    except httpx.HTTPError as e:
        raise ElevenLabsError(f"Could not reach ElevenLabs: {e}") from None


@dataclass
class Synth:
    audio: bytes      # raw PCM s16le mono when the format is pcm_*, else encoded audio
    request_id: str | None
    # ElevenLabs' character timings for the text sent ({characters, character_start_times_seconds,
    # character_end_times_seconds}), when narration.timestamps asks for them: the episode page and
    # asking mid-chapter time every word from these (episode/timing.py), with no recognizer pass.
    alignment: dict | None = None


def synthesize(text: str, *, voice_id: str, ncfg: dict,
               previous_request_ids: list[str] | None = None,
               previous_text: str | None = None, next_text: str | None = None,
               timestamps: bool | None = None) -> Synth:
    """Text -> audio for one chunk. The only function that knows about ElevenLabs.

    Character timings come back with the audio when narration.timestamps is on (the default), or
    when the caller asks with timestamps=True (Fast Start's pieces always do); timestamps=False
    forces the plain call. Same request fields and characters billed either way."""
    from elevenlabs import VoiceSettings

    client = elevenlabs_client()
    kwargs = dict(
        text=text,
        model_id=ncfg["model_id"],
        output_format=ncfg["output_format"],
        voice_settings=VoiceSettings(**ncfg["voice_settings"]),
    )
    if ncfg.get("seed") is not None:
        kwargs["seed"] = ncfg["seed"]
    # Request stitching keeps prosody continuous across chunks. Request ids win over text.
    if previous_request_ids:
        kwargs["previous_request_ids"] = previous_request_ids[-3:]
    elif previous_text:
        kwargs["previous_text"] = previous_text
    if next_text:
        kwargs["next_text"] = next_text
    def request_id(headers) -> str | None:
        return headers.get("request-id") or headers.get("x-request-id")

    def convert():
        if (ncfg.get("timestamps", True) if timestamps is None else timestamps):
            # Same body, same billing; the audio comes back whole as base64 with the timings. The raw
            # response is not a context manager here (unlike the streaming convert below).
            resp = client.text_to_speech.with_raw_response.convert_with_timestamps(voice_id, **kwargs)
            a = resp.data.alignment
            return (base64.b64decode(resp.data.audio_base_64 or ""), request_id(resp.headers),
                    a.model_dump() if a is not None and hasattr(a, "model_dump") else a)
        with client.text_to_speech.with_raw_response.convert(voice_id, **kwargs) as resp:
            return b"".join(resp.data), request_id(resp.headers), None

    audio, rid, alignment = el_call(convert)
    if not audio:
        raise StageError("ElevenLabs returned no audio.")
    return Synth(audio=audio, request_id=rid, alignment=alignment)


def model_char_limit(model_id: str) -> int | None:
    """The provider's per-request character limit for this model, read live."""
    try:
        for m in elevenlabs_client().models.list():
            if m.model_id == model_id:
                return getattr(m, "maximum_text_length_per_request", None)
    except StageError:
        raise
    except Exception as e:  # a listing failure should not block narration; config still caps it
        print(f"  (could not read model limits: {e})")
    return None


# --- text preparation --------------------------------------------------------

def apply_pronunciations(text: str, table: dict[str, str]) -> str:
    """Replace hard names with spellings the voice says correctly (whole words, case-sensitive)."""
    for word in sorted(table, key=len, reverse=True):
        text = re.sub(rf"(?<![\w-]){re.escape(word)}(?![\w-])", table[word], text)
    return text


def split_sentences(paragraph: str) -> list[str]:
    marked = re.sub(r"([.!?][\"'”’)]?)\s+(?=[\"'“‘(]?[A-Z0-9])", "\\1\n", paragraph)
    return [s.strip() for s in marked.split("\n") if s.strip()]


PAUSE = re.compile(r"\s*\[pause\]\s*", re.I)


def split_beats(text: str) -> list[str]:
    """Split spoken text at the script's [pause] marks. The pieces are synthesized
    separately and joined with a held silence (narration.beat_pause_ms): eleven_v4
    has no break tag, and its bracket tags are sometimes read aloud."""
    return [p.strip() for p in PAUSE.split(text) if p.strip()]


def speak_editorial_brackets(text: str) -> str:
    """Make a verbatim quote's editorial brackets speakable: [T]he -> The, want[ed] ->
    wanted, [...] -> an ellipsis, [sic] dropped. Any other bracket is left for the check."""
    text = re.sub(r"\s*\[sic\]", "", text, flags=re.I)
    text = re.sub(r"\[\s*(?:\.\s*){3}\]|\[…\]", "…", text)
    return re.sub(r"(?<=\w)\[([A-Za-z]{1,12})\]|\[([A-Za-z]{1,12})\](?=\w)",
                  lambda m: m.group(1) or m.group(2), text)


def chunk_paragraphs(text: str, max_chars: int) -> list[str]:
    """Group paragraphs into chunks under max_chars, splitting only at paragraph
    breaks, or at sentence breaks for a paragraph that is too long on its own."""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    pieces: list[str] = []
    for p in paras:
        if len(p) <= max_chars:
            pieces.append(p)
            continue
        cur = ""
        for s in split_sentences(p):
            if len(s) > max_chars:
                raise StageError(f"A single sentence is longer than {max_chars} characters: {s[:80]}...")
            if cur and len(cur) + 1 + len(s) > max_chars:
                pieces.append(cur)
                cur = s
            else:
                cur = f"{cur} {s}" if cur else s
        if cur:
            pieces.append(cur)

    chunks: list[str] = []
    cur = ""
    for p in pieces:
        if cur and len(cur) + 2 + len(p) > max_chars:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks


# --- audio -------------------------------------------------------------------

def pcm_rate(output_format: str) -> int | None:
    m = re.fullmatch(r"pcm_(\d+)", output_format)
    return int(m.group(1)) if m else None


def write_wav(path: Path, audio: bytes, output_format: str) -> None:
    """Save one chunk as a 16-bit mono WAV, whatever format the provider sent."""
    rate = pcm_rate(output_format)
    if rate:
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(audio)
        return
    tmp = path.with_suffix(".src")
    tmp.write_bytes(audio)
    try:
        ffmpeg(["-i", str(tmp), "-ac", "1", "-c:a", "pcm_s16le", str(path)])
    finally:
        tmp.unlink(missing_ok=True)


def ffmpeg(args: list[str]) -> None:
    try:
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
                       check=True, capture_output=True, text=True)
    except FileNotFoundError:
        raise StageError("ffmpeg is not installed.") from None
    except subprocess.CalledProcessError as e:
        raise StageError(f"ffmpeg failed: {e.stderr.strip()}") from None


def silence_wav(path: Path, ms: int, like: Path) -> None:
    with wave.open(str(like), "rb") as w:
        rate, width, ch = w.getframerate(), w.getsampwidth(), w.getnchannels()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00" * int(rate * ms / 1000) * width * ch)


def join_to_mp3(parts: list[Path | int], out_mp3: Path, work: Path, bitrate: str) -> None:
    """parts is a list of WAV paths and pause lengths in ms. Join them and encode one MP3."""
    wavs = [p for p in parts if isinstance(p, Path)]
    if not wavs:
        raise StageError("Nothing to join.")
    listing = work / "concat.txt"
    lines = []
    for p in parts:
        if isinstance(p, int):
            with wave.open(str(wavs[0]), "rb") as w:
                fmt = f"{w.getframerate()}_{w.getsampwidth()}_{w.getnchannels()}"
            sil = work / f"silence_{p}ms_{fmt}.wav"
            if not sil.exists():
                silence_wav(sil, p, wavs[0])
            p = sil
        quoted = str(p.resolve()).replace("'", "'\\''")  # ffmpeg concat-list quoting
        lines.append(f"file '{quoted}'")
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    joined = work / "joined.wav"
    ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(joined)])
    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    # Encoded beside it, then moved into place: the MP3 marks narration done, and a stop (SIGINT to the
    # run's process group, ffmpeg included) mid-encode leaves a shorter but valid file behind.
    part = out_mp3.with_name(out_mp3.stem + ".part" + out_mp3.suffix)
    ffmpeg(["-i", str(joined), "-c:a", "libmp3lame", "-b:a", bitrate, str(part)])
    part.replace(out_mp3)


# --- narrate a script ----------------------------------------------------------

def _chunk_key(text: str, voice_id: str, ncfg: dict, first: bool, next_text: str | None) -> str:
    # The key covers the chunk's own text, the start of the next chunk (sent as
    # stitching context) and every voice setting, so a re-run reuses unchanged chunks.
    blob = json.dumps({
        "text": text, "voice": voice_id, "model": ncfg["model_id"], "format": ncfg["output_format"],
        "settings": ncfg["voice_settings"], "seed": ncfg.get("seed"), "next": next_text,
        "first": first,
    }, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def narrate_segments(r: Run, cfg: dict, segments: list[tuple[int, str]], out_mp3: Path,
                     work: Path, *, stage: str, voice_id: str | None = None) -> Path:
    """Narrate [(segment number, script text)] into one MP3, with longer pauses at segment breaks.

    Chunks are cached in work/chunks by content, so a re-run only pays for text that changed.
    """
    ncfg = cfg["narration"]
    voice_id = voice_id or ncfg.get("voice_id")
    if not voice_id:
        raise StageError("No narrator voice set. Run the voice test, then set narration.voice_id in config.yaml.")

    max_chars = ncfg["max_chars_per_request"]
    live = model_char_limit(ncfg["model_id"])
    if live and live < max_chars:
        max_chars = live

    plan: list[tuple[int, str]] = []  # (segment, chunk text)
    beats: set[int] = set()  # plan indexes that follow a [pause]
    for n, body in segments:
        clean = apply_pronunciations(spoken_text(body), ncfg.get("pronunciations") or {})
        if m := LEFTOVER_MARKER.search(clean):  # e.g. an unclosed '[D12': stop before paying to read it aloud
            raise StageError(f"Segment {n} still has a dossier marker after stripping, near "
                             f"{clean[max(0, m.start() - 40):m.start() + 20]!r}. Fix it in the script first.")
        for j, piece in enumerate(split_beats(clean)):
            piece = speak_editorial_brackets(piece)
            if m := re.search(r"\[[^\]\n]{0,40}\]", piece):  # the voice would say it, or treat it as a tag
                raise StageError(f"Segment {n} has a bracketed tag the narrator would read aloud: {m.group()!r}. "
                                 "Only [pause] is understood; fix the script first.")
            for k, c in enumerate(chunk_paragraphs(piece, max_chars)):
                if j > 0 and k == 0:
                    beats.add(len(plan))
                plan.append((n, c))
    if not plan:
        raise StageError("No text to narrate.")

    chunks_dir = work / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    keys, todo_chars = [], 0
    for i, (n, text) in enumerate(plan):
        nxt = plan[i + 1][1] if i + 1 < len(plan) else None
        k = _chunk_key(text, voice_id, ncfg, i == 0, nxt[:500] if nxt else None)
        keys.append(k)
        if not (chunks_dir / f"{k}.wav").exists():
            todo_chars += len(text)
    total_chars = sum(len(t) for _, t in plan)
    est = todo_chars * ncfg["price_per_1000_chars"] / 1000
    print(f"  {len(plan)} chunks, {total_chars:,} characters; {todo_chars:,} to synthesize (~${est:.2f})")
    guard(r, cfg, f"{stage} narration", est)

    parts: list[Path | int] = []
    prev_ids: list[str] = []
    prev_text: str | None = None
    for i, ((n, text), k) in enumerate(zip(plan, keys)):
        wav_path = chunks_dir / f"{k}.wav"
        meta_path = chunks_dir / f"{k}.json"
        if wav_path.exists():
            meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            rid = meta.get("request_id")
        else:
            nxt = plan[i + 1][1] if i + 1 < len(plan) else None
            syn = synthesize(text, voice_id=voice_id, ncfg=ncfg,
                             previous_request_ids=prev_ids or None,
                             previous_text=None if prev_ids else (prev_text[-500:] if prev_text else None),
                             next_text=nxt[:500] if nxt else None)
            write_wav(wav_path, syn.audio, ncfg["output_format"])
            rid = syn.request_id
            meta = {"request_id": rid, "segment": n, "text": text}
            if syn.alignment:  # episode/timing.py reads it from here
                meta["alignment"] = syn.alignment
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
            cost = len(text) * ncfg["price_per_1000_chars"] / 1000
            r.log({"stage": stage, "label": f"{stage} chunk {i + 1}/{len(plan)}", "provider": "elevenlabs",
                   "model": ncfg["model_id"], "voice_id": voice_id, "characters": len(text),
                   "request_id": rid, "cost_usd": round(cost, 5)})
            print(f"  chunk {i + 1}/{len(plan)} (seg{n:02d}, {len(text):,} chars)")
        prev_ids = (prev_ids + [rid])[-3:] if rid else []
        prev_text = text

        if parts:
            if plan[i - 1][0] != n:
                parts.append(ncfg["segment_pause_ms"])
            elif i in beats:
                parts.append(ncfg.get("beat_pause_ms", 1100))
            else:
                parts.append(ncfg["chunk_pause_ms"])
        parts.append(wav_path)

    join_to_mp3(parts, out_mp3, work, ncfg["mp3_bitrate"])
    print(f"  -> {out_mp3.relative_to(r.root) if out_mp3.is_relative_to(r.root) else out_mp3}")
    return out_mp3
