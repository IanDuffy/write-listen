"""Stage 7: 06_segments/ -> 07_script_draft.md

Concatenate the segments in brief order. Each segment is preceded by a
<!-- segNN --> marker so the edit, revise and narrate stages can find the
boundaries again. The markers are never read aloud.
"""

from .brief import parse_segments
from .common import Run, StageError, join_script_segments, word_count


def run(r: Run, cfg: dict):
    segs = parse_segments(r.read("brief"))
    r.check_writable("script_draft")
    missing = [s["n"] for s in segs if not r.segment_path(s["n"]).exists()]
    if missing:
        raise StageError(f"Missing segments: {', '.join(f'seg{n:02d}' for n in missing)}. Run draft first.")
    parts = [(s["n"], r.read(r.segment_path(s["n"]))) for s in segs]
    script = join_script_segments(parts)
    out = r.write("script_draft", script)
    print(f"  {len(parts)} segments, {word_count(script):,} words")
    r.log({"stage": "assemble", "provider": "none", "words": word_count(script), "cost_usd": 0})
    return out
