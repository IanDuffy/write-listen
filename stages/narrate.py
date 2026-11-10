"""Stage 10: script -> MP3

  narrate <run>                     09_script_final.md -> 10_audio/chapter.mp3
  narrate <run> --pilot             06_segments (default 1-2) -> 10_audio/pilot.mp3
  narrate <run> --baseline          baseline/script.md -> baseline/chapter.mp3

Splits at paragraph breaks to fit the per-request limit, synthesizes each chunk,
joins them with short pauses (longer at segment breaks) and exports one MP3.
Claim markers [Cn] and segment markers are stripped before synthesis.
"""

from .brief import parse_segments
from .common import Run, StageError, parse_range, split_script_segments
from .tts import narrate_segments


def run(r: Run, cfg: dict, pilot: bool = False, baseline: bool = False, segments: str | None = None):
    if pilot and baseline:
        raise StageError("Choose --pilot or --baseline, not both.")
    if baseline:
        out = r.check_writable("baseline_mp3")
        script = r.read("baseline_script")
        # The baseline has no segment markers, so it is narrated as one segment.
        parts = [(1, script)]
        work = r.path("baseline_dir") / "audio"
        stage = "narrate_baseline"
    elif pilot:
        out = r.check_writable(r.path("audio_dir") / "pilot.mp3")
        nums = [s["n"] for s in parse_segments(r.read("brief"))]
        wanted = parse_range(segments or "1-2", nums)
        parts = [(n, r.read(r.segment_path(n))) for n in wanted]
        work = r.path("audio_dir")
        stage = "narrate_pilot"
    else:
        parts = split_script_segments(r.read("script_final"))
        if segments:
            # A partial narration never goes to chapter.mp3, which compare and report use.
            wanted = set(parse_range(segments, [n for n, _ in parts]))
            parts = [(n, t) for n, t in parts if n in wanted]
            out = r.check_writable(r.path("audio_dir") / f"segments_{segments.replace(',', '_')}.mp3")
        else:
            out = r.check_writable("chapter_mp3")
        work = r.path("audio_dir")
        stage = "narrate"
    work.mkdir(parents=True, exist_ok=True)
    return narrate_segments(r, cfg, parts, out, work, stage=stage)
