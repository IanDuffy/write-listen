"""Stage 1: timing.json, a start and end time for every segment, sentence and word.

Audio pieces: the narration joins one WAV per chunk with silences, and records the join in
10_audio/concat.txt. When that list and its files are there, each chunk is recognized on its own
and placed at its exact offset (sum of the WAV durations before it): shorter audio, and no drift
across the chapter. Otherwise the whole chapter file is recognized at once. Either way the script
(all segments) is aligned against all the recognized words in order, so segment boundaries come
from the script.

Recognizer output is cached per piece in 11_episode/recognizer.json (keyed by recognizer, file name
and size),
so re-running timing after a script fix costs seconds, not minutes.

Also here, for later features (interruptions, streaming): nearest_sentence_boundary and current_word.
"""

from __future__ import annotations

import bisect
import json
import subprocess
import wave
from pathlib import Path

from . import align, script

EPISODE_DIR = "11_episode"


def audio_duration(path: Path) -> float:
    if path.suffix.lower() == ".wav":
        with wave.open(str(path)) as w:
            return w.getnframes() / w.getframerate()
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def audio_pieces(run: Path, audio: Path) -> list[dict]:
    """[{'path', 'offset', 'speech'}]: the chunks and silences of concat.txt, or the whole file."""
    concat = audio.parent / "concat.txt"
    if concat.exists():
        pieces, t, ok = [], 0.0, True
        for line in concat.read_text(encoding="utf-8").splitlines():
            if not line.startswith("file "):
                continue
            p = Path(line[5:].strip().strip("'").replace("'\\''", "'"))
            if not p.is_absolute():
                p = concat.parent / p
            if not p.exists():
                ok = False
                break
            d = audio_duration(p)
            pieces.append({"path": p, "offset": t, "speech": not p.name.startswith("silence_")})
            t += d
        if ok and pieces and abs(t - audio_duration(audio)) < 1.0:  # the list describes this audio file
            return pieces
    return [{"path": audio, "offset": 0.0, "speech": True}]


def recognize_pieces(pieces: list[dict], recognizer, cache_path: Path | None) -> list[dict]:
    """All recognized words, in chapter time."""
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path and cache_path.exists() else {}
    words = []
    for p in pieces:
        if not p["speech"]:
            continue
        key = f"{getattr(recognizer, 'name', 'recognizer')}|{p['path'].name}:{p['path'].stat().st_size}"
        if key not in cache:
            print(f"  recognizing {p['path'].name} ({audio_duration(p['path']):.0f} s)", flush=True)
            cache[key] = recognizer(p["path"])
            if cache_path:
                cache_path.write_text(json.dumps(cache), encoding="utf-8")
        words += [{**w, "start": w["start"] + p["offset"], "end": w["end"] + p["offset"]} for w in cache[key]]
    return words


REPAIR_CONTEXT = 4  # matched words kept on each side of a gap when it is heard again


def repair_gaps(audio: Path, words: list[dict], times: list[dict], recognizer, cache_path: Path | None,
                duration: float) -> int:
    """Whisper sometimes skips a sentence or two of a long piece (it heard all of seg05-s056 and
    seg08-s028/29 of chapter one in a 9-second clip, none of it in the 4-minute chunk). Each run of
    estimated words is heard again in a short clip around it, with REPAIR_CONTEXT words either side
    (the word just before a gap is often matched in the wrong place), and the slice is re-aligned.
    The slice's new times are kept if fewer of its words are estimated. Returns the gaps repaired."""
    import tempfile

    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path and cache_path.exists() else {}
    n, i, repaired = len(times), 0, 0
    while i < n:
        if times[i]["match"] != "estimated":
            i += 1
            continue
        j = i
        while j < n and times[j]["match"] == "estimated":
            j += 1
        lo, hi = max(0, i - REPAIR_CONTEXT), min(n, j + REPAIR_CONTEXT)
        a, b = max(0.0, times[lo]["start"] - 1.0), min(duration, times[hi - 1]["end"] + 1.0)
        key = f"{getattr(recognizer, 'name', 'recognizer')}|clip|{audio.name}|{a:.2f}-{b:.2f}"
        if key not in cache:
            with tempfile.TemporaryDirectory() as tmp:
                clip = Path(tmp) / "clip.wav"
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i",
                                str(audio), "-ac", "1", "-ar", "16000", str(clip)], check=True)
                try:
                    cache[key] = recognizer(clip)
                except KeyError:  # a recorded recognizer without this clip: leave the gap estimated
                    i = j
                    continue
            if cache_path:
                cache_path.write_text(json.dumps(cache), encoding="utf-8")
        local = align.align(words[lo:hi], [{**w, "start": w["start"] + a, "end": w["end"] + a} for w in cache[key]],
                            limit=duration)
        before = sum(t["match"] == "estimated" for t in times[lo:hi])
        if sum(t["match"] == "estimated" for t in local) < before:
            times[lo:hi] = local
            repaired += 1
        i = j
    align.tidy(times, duration)  # the repaired slices meet their neighbours in order
    return repaired


def build(run: Path, recognizer, pronunciations: dict[str, str], audio_rel: str = "10_audio/chapter.mp3",
          script_rel: str = "09_script_final.md", cache: bool = True) -> dict:
    audio = run / audio_rel
    segments = script.parse((run / script_rel).read_text(encoding="utf-8"), pronunciations)
    flat = [(si, xi, w) for si, s in enumerate(segments) for xi, x in enumerate(s.sentences) for w in x.words]
    pieces = audio_pieces(run, audio)
    cache_path = (run / EPISODE_DIR / "recognizer.json") if cache else None
    if cache_path:
        cache_path.parent.mkdir(exist_ok=True)
    rec = recognize_pieces(pieces, recognizer, cache_path)
    duration = audio_duration(audio)
    words = [{"w": w.w, "spoken": w.spoken} for _, _, w in flat]
    times = align.align(words, rec, limit=duration)
    repaired = repair_gaps(audio, words, times, recognizer, cache_path, duration)

    out_segments, k = [], 0
    for s in segments:
        sents = []
        for x in s.sentences:
            ws = []
            for w in x.words:
                t = times[k]
                k += 1
                item = {"w": w.w, "start": t["start"], "end": t["end"]}
                if w.spoken != w.w:
                    item["spoken"] = w.spoken
                if t["match"] == "estimated":
                    item["estimated"] = True
                ws.append(item)
            sents.append({"id": x.id, "start": ws[0]["start"], "end": ws[-1]["end"], "text": x.text, "words": ws})
        out_segments.append({"id": s.id, "start": sents[0]["start"], "end": sents[-1]["end"], "sentences": sents})
    matches = [t["match"] for t in times]
    return {
        "audio": audio_rel,
        "duration": round(duration, 3),
        "aligned_by": getattr(recognizer, "name", "recognizer"),
        "pieces": len([p for p in pieces if p["speech"]]),
        "stats": {"words": len(times), "exact": matches.count("exact"), "fuzzy": matches.count("fuzzy"),
                  "estimated": matches.count("estimated"), "gaps_reheard": repaired},
        "segments": out_segments,
    }


def write(run: Path, timing: dict) -> Path:
    p = run / EPISODE_DIR / "timing.json"
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(timing, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


# --- lookups, for the page's Python twin and later features -------------------------------------

def sentences(timing: dict) -> list[dict]:
    return [x for s in timing["segments"] for x in s["sentences"]]


def nearest_sentence_boundary(timing: dict, t: float, direction: str = "nearest") -> float:
    """The closest sentence start or end to time t: 'before' (at or before t), 'after' (at or after
    t) or 'nearest'. Clamped to [0, duration]. An interruption pauses here."""
    marks = sorted({0.0, timing["duration"], *(x["start"] for x in sentences(timing)),
                    *(x["end"] for x in sentences(timing))})
    t = min(max(t, 0.0), timing["duration"])
    i = bisect.bisect_left(marks, t)
    before = marks[i] if i < len(marks) and marks[i] == t else marks[max(0, i - 1)]
    after = marks[min(i, len(marks) - 1)]
    if direction == "before":
        return before
    if direction == "after":
        return after
    if direction != "nearest":
        raise ValueError("direction must be 'before', 'after' or 'nearest'")
    return before if t - before <= after - t else after


def current_word(timing: dict, t: float) -> tuple[int, int, int] | None:
    """(segment, sentence, word) indexes of the word being spoken at t: the last word that has
    started. In a gap between words it stays on the word just said; before the first, None."""
    flat = [(si, xi, wi, w["start"]) for si, s in enumerate(timing["segments"])
            for xi, x in enumerate(s["sentences"]) for wi, w in enumerate(x["words"])]
    starts = [f[3] for f in flat]
    i = bisect.bisect_right(starts, t) - 1
    return None if i < 0 else flat[i][:3]
