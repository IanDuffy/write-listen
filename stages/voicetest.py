"""Milestone 2: narrate the same sample paragraph in several candidate voices.

  voicetest                      voices from narration.candidate_voices in config.yaml
  voicetest --voices ID1,ID2,ID3

Writes voice_test/<name>.mp3 for each voice. Listen, pick one, and set
narration.voice_id in config.yaml. Also check the pronunciation table: the
sample uses Weil, Diotima, Boethius and Aquinas.
"""

import re

from .common import ROOT, Run, StageError
from .tts import narrate_segments

SAMPLE = ROOT / "voice_test" / "sample.md"


def run(r: Run, cfg: dict, voices: str | None = None):
    if voices:
        candidates = [{"name": v.strip(), "voice_id": v.strip()} for v in voices.split(",") if v.strip()]
    else:
        candidates = cfg["narration"].get("candidate_voices") or []
    if not candidates:
        raise StageError("No candidate voices. List some with `python cli.py voices`, then pass --voices.")
    text = SAMPLE.read_text(encoding="utf-8")
    text = text.split("\n---\n", 1)[-1]  # drop the notes header
    out = []
    for c in candidates:
        name = re.sub(r"[^A-Za-z0-9_-]+", "-", c["name"]).strip("-").lower()
        mp3 = r.check_writable(r.root / f"{name}.mp3")
        print(f"  {c['name']} ({c['voice_id']})")
        out.append(narrate_segments(r, cfg, [(1, text)], mp3, r.root / "_work" / name,
                                    stage="voicetest", voice_id=c["voice_id"]))
    print("\n  Next [Ian]: listen to voice_test/*.mp3, pick one, and set narration.voice_id in config.yaml.")
    return out


def list_voices(cfg: dict, search: str | None = None) -> None:
    """Print the voices on the account and the TTS models with their per-request limits."""
    from .tts import el_call, elevenlabs_client

    client = elevenlabs_client()
    print("Models that can do text to speech:")
    for m in el_call(client.models.list):
        if getattr(m, "can_do_text_to_speech", False):
            mark = "  <- configured" if m.model_id == cfg["narration"]["model_id"] else ""
            print(f"  {m.model_id:28} max {m.maximum_text_length_per_request or '?'} chars/request{mark}")
    print("\nVoices:")
    token = None
    while True:
        page = el_call(client.voices.search, page_size=100, search=search, next_page_token=token)
        for v in page.voices:
            labels = ", ".join(f"{k}: {val}" for k, val in (v.labels or {}).items())
            print(f"  {v.voice_id}  {(v.name or ''):28} {v.category or '':12} {labels}")
        if not page.has_more:
            break
        token = page.next_page_token
