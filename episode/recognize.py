"""Recognizers with word timestamps, behind one interface.

    recognizer(path) -> [{"w": str, "start": float, "end": float, "p": float}], times from the start of path

WhisperRecognizer runs faster-whisper locally (no API, no cost). Audio is decoded with ffmpeg,
not PyAV: faster-whisper 1.2.1's decoder calls PyAV with an argument the installed PyAV dropped.
RecordedRecognizer replays saved output, for the tests (no model, no audio work).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

SAMPLE_RATE = 16000


def decode(path: Path):
    """Mono float32 samples at 16 kHz, as Whisper wants them."""
    import numpy as np

    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1",
                          "-ar", str(SAMPLE_RATE), "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


class WhisperRecognizer:
    """faster-whisper with word timestamps. device 'auto' tries Apple's MPS backend (about 12x real
    time on this Mac) and falls back to the CPU (about 6x)."""

    def __init__(self, model: str = "small.en", device: str = "auto"):
        from faster_whisper import WhisperModel

        self.name = f"faster-whisper {model}"
        tries = [("mps", "float16"), ("cpu", "int8")] if device == "auto" else [(device, "default")]
        err = None
        for dev, compute in tries:
            try:
                self.model = WhisperModel(model, device=dev, compute_type=compute)
                self.device = dev
                break
            except Exception as e:  # MPS is experimental in ctranslate2: fall back
                err = e
        else:
            raise RuntimeError(f"could not load Whisper {model}: {err}")

    def __call__(self, path: Path) -> list[dict]:
        return self.transcribe(decode(path))

    def transcribe(self, samples) -> list[dict]:
        # No conditioning on earlier text: it can make Whisper repeat or skip on long audio.
        segs, _ = self.model.transcribe(samples, language="en", word_timestamps=True, beam_size=5,
                                        condition_on_previous_text=False)
        return [{"w": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3),
                 "p": round(w.probability, 3)} for s in segs for w in (s.words or []) if w.word.strip()]


class RecordedRecognizer:
    """Saved recognizer output: {piece file name: [words]}."""

    def __init__(self, path: Path):
        self.name = f"recorded ({Path(path).name})"
        self.data = json.loads(Path(path).read_text(encoding="utf-8"))

    def __call__(self, path: Path) -> list[dict]:
        return self.data[Path(path).name]
