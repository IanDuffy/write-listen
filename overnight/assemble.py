"""Stage 6, assemble: the recorded paragraphs as one chapter the episode page and ask-by-voice open
like any other (docs/overnight-chapters.md "Overnight runner" stage 6).

    10_audio/chapter.mp3       the paragraph WAVs in script order, joined with fixed pauses: before a
                               paragraph narration.chunk_pause_ms, a section narration.segment_pause_ms,
                               after a [pause] narration.beat_pause_ms (tts.join_to_mp3)
    10_audio/concat.txt        that join, piece by piece: the offsets timing reads
    11_episode/timing.json     every word's time: local Whisper on each paragraph WAV at its offset,
                               aligned to 09_script_final.md (sentence ids from episode.script)
    11_episode/references.json the reference cards (Sonnet, one call a section), charged to the run
    11_episode/episode.json    the episode page's one file (written last: it marks the stage done)

Each part is skipped when it is already done (resume): the MP3 when concat.txt lists exactly these
pieces, the timing when its sentence ids match the script, the references when the file exists.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from episode import bundle, recognize, references, script, timing
from stages import tts
from stages.common import Run, StageError

from . import record

STAGE = "assemble"
REFERENCES_MAX_USD = 3.0  # the episode build's own default cap for its reference calls


def parts(r: Run, cfg: dict) -> list[Path | int]:
    """The join, in order: a pause (ms) before every paragraph but the first, then its WAV."""
    n = cfg["narration"]
    pause = {"section": int(n["segment_pause_ms"]), "beat": int(n.get("beat_pause_ms", 1100)),
             "paragraph": int(n["chunk_pause_ms"]), "start": 0}
    out: list[Path | int] = []
    folder = r.path("audio_dir") / "paragraphs"
    for p in record.paragraphs(r):
        wav = folder / f"{p['id']}.wav"
        if not wav.exists():
            raise StageError(f"Paragraph {p['id']} has no recording ({wav.relative_to(r.root)}): record first.")
        if out and pause[p["pause"]]:
            out.append(pause[p["pause"]])
        out.append(wav)
    return out


def _listing(r: Run, pieces: list[Path | int]) -> list[str]:
    """What concat.txt would name, file names only (silences by length)."""
    return [f"silence_{p}ms" if isinstance(p, int) else p.name for p in pieces]


def _joined_as(r: Run) -> list[str] | None:
    concat = r.path("audio_dir") / "concat.txt"
    if not concat.exists():
        return None
    out = []
    for line in concat.read_text(encoding="utf-8").splitlines():
        if line.startswith("file "):
            name = Path(line[5:].strip().strip("'").replace("'\\''", "'")).name
            out.append(name.split("ms_")[0] + "ms" if name.startswith("silence_") else name)
    return out


def stitch(r: Run, cfg: dict) -> Path:
    mp3 = r.path("chapter_mp3")
    pieces = parts(r, cfg)
    if mp3.exists() and _joined_as(r) == _listing(r, pieces):
        return mp3
    audio = r.path("audio_dir")
    tts.join_to_mp3(pieces, mp3, audio, cfg["narration"].get("mp3_bitrate", "128k"))
    (audio / "joined.wav").unlink(missing_ok=True)  # the same audio as the MP3 and the paragraphs, uncompressed
    return mp3


def _timing_ok(r: Run, cfg: dict) -> bool:
    p = r.path("timing")
    if not p.exists():
        return False
    try:
        t = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return False
    segs = script.parse(r.read("script_final"), cfg["narration"].get("pronunciations") or {})
    return [x.id for s in segs for x in s.sentences] == [x["id"] for x in timing.sentences(t)] and \
        abs(t.get("duration", 0) - timing.audio_duration(r.path("chapter_mp3"))) < 0.5


def time_it(r: Run, cfg: dict, recognizer=None) -> dict:
    if _timing_ok(r, cfg):
        return json.loads(r.path("timing").read_text(encoding="utf-8"))
    rec = recognizer or recognize.WhisperRecognizer()
    t = timing.build(r.root, rec, cfg["narration"].get("pronunciations") or {})
    timing.write(r.root, t)
    return t


def cards(r: Run, cfg: dict) -> float:
    """The reference cards, as faststart/archive.py episode() builds them: their calls are logged in
    11_episode/log.jsonl, and the total is added to the run's own log so its cost and its runaway stop
    see it. Reuses answers already on disk. Returns what was spent."""
    if (r.root / timing.EPISODE_DIR / "references.json").exists():
        return 0.0

    def spent() -> float:
        return Run(name="episode", root=r.root / timing.EPISODE_DIR).spent()

    before = spent()
    cap = REFERENCES_MAX_USD
    if r.max_usd is not None:
        cap = max(0.0, min(cap, r.max_usd - r.spent()))
    try:
        refs, _ = references.build(r.root, cfg, max_usd=cap)
        references.write(r.root, refs)
    except (SystemExit, FileNotFoundError, KeyError, ValueError, subprocess.CalledProcessError) as e:
        raise StageError(f"The reference cards failed ({type(e).__name__}): {e}") from None
    finally:
        cost = round(spent() - before, 5)
        if cost:
            r.log({"stage": STAGE, "label": "episode references (each call is in 11_episode/log.jsonl)",
                   "cost_usd": cost})
    return cost


def run(r: Run, cfg: dict, recognizer=None) -> Path:
    """Stitch, time, references, episode.json. Returns episode.json's path. `recognizer`: anything with
    the episode.recognize interface (default: local Whisper, loaded only if the timing must be built)."""
    r.need(r.path("audio_dir") / "recorded.json")
    stitch(r, cfg)
    t = time_it(r, cfg, recognizer)
    cards(r, cfg)
    try:
        p = bundle.write(r.root)
    except ValueError as e:
        raise StageError(f"episode.json could not be built: {e}") from None
    r.log({"stage": STAGE, "label": "chapter assembled", "duration": t["duration"], "timing": t.get("stats")})
    return p
