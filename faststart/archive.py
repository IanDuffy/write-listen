"""Step 6, the archive: when the last section is narrated, the pieces the listener heard become one
chapter in the overnight layout, so the episode page and ask-by-voice open a fast run like any other.

    09_script_final.md        the locked sections under their <!-- segNN --> markers
    10_audio/chapter.mp3      the same piece WAVs and the same pauses, joined once (never narrated again:
                              that would pay for the chapter twice and archive audio nobody heard)
    10_audio/concat.txt       the join, piece by piece: what episode.timing reads its offsets from
    11_episode/timing.json    the section timings, each moved to where its section starts
    05_brief.md               re-rendered with the entries each section really cites, and the titles
                              the locks gave
    11_episode/episode.json   the episode build (references are its only paid calls)
    fast/archive.json         what this step returned

Conversations during the chapter (questions and check-ins) are text in 12_asks/asides.jsonl, never
audio: this step does not touch them, and the episode page shows each at the sentence it names.
"""

from __future__ import annotations

import copy
import inspect
import itertools
import subprocess
from pathlib import Path

from episode import script as episode_script
from episode import timing
from stages import tts
from stages.common import MARKER, PATHS, Run, StageError, entry_numbers, join_script_segments

from . import base, narrate, status

STAGE = "fast_archive"
LENGTH_TOLERANCE = 0.25  # seconds between the stitched chapter and the sum of its sections
REFERENCES_MAX_USD = 3.0  # the episode build's own default cap for its references calls


def merge_timings(sections: list[dict], offsets: list[float]) -> dict:
    """Section timings (each in its own section's time) as one chapter timing, in the shape of
    11_episode/timing.json: every start and end moved by its section's offset. Sentence ids already
    carry the section number (seg02-s001), so they do not change."""
    segments = []
    for t, offset in zip(sections, offsets):
        for seg in copy.deepcopy(t["segments"]):
            for item in [seg, *seg["sentences"], *(w for x in seg["sentences"] for w in x["words"])]:
                item["start"], item["end"] = round(item["start"] + offset, 3), round(item["end"] + offset, 3)
            segments.append(seg)
    stats: dict[str, int] = {}
    for t in sections:
        for k, v in t.get("stats", {}).items():
            stats[k] = stats.get(k, 0) + v
    return {
        "audio": PATHS["chapter_mp3"],
        "duration": round(offsets[-1] + sections[-1]["duration"], 3) if sections else 0.0,
        "aligned_by": ", ".join(dict.fromkeys(t.get("aligned_by", "") for t in sections)),
        "pieces": sum(t.get("pieces", 0) for t in sections),
        "stats": stats,
        "segments": segments,
    }


def timing_problems(t: dict, script: str, pronunciations: dict[str, str]) -> list[str]:
    """Why a merged timing cannot stand for this script: its sentences are not the script's, a time
    goes backwards, or a word ends after the audio does. [] when it can."""
    problems = []
    want = [x.id for s in episode_script.parse(script, pronunciations) for x in s.sentences]
    if want != [x["id"] for s in t["segments"] for x in s["sentences"]]:
        problems.append("its sentences are not the script's")
    words = [w for s in t["segments"] for x in s["sentences"] for w in x["words"]]
    if any(w["end"] < w["start"] for w in words) or any(b["start"] < a["end"] for a, b in zip(words, words[1:])):
        problems.append("its times go backwards")
    if words and words[-1]["end"] > t["duration"] + LENGTH_TOLERANCE:
        problems.append("its last word ends after the audio")
    return problems


def _outline(r: Run) -> dict | None:
    return base.read_json(base.path(r, "outline_json"))


def _sections(r: Run, so_far: bool = False) -> list[tuple[int, str, list[narrate.Piece]]]:
    """(number, locked text, pieces) for every section of the outline: all must be locked and narrated,
    with every piece still on disk. With so_far, the sections that are, in order from the first."""
    outline = _outline(r)
    out = []
    for n in ([s["n"] for s in outline["sections"]] if outline else itertools.count(1)):
        ps = narrate.pieces(r, n) if r.segment_path(n).exists() and narrate.narrated(r, n) else []
        manifest = base.read_json(base.section_dir(r, n) / "section.json") or {}
        if not ps or len(ps) != manifest.get("pieces"):
            if outline and not so_far:
                raise StageError(f"Section {n} is not locked and narrated in full yet: the archive needs "
                                 "every section.")
            break
        out.append((n, r.segment_path(n).read_text(encoding="utf-8").strip(), ps))
    return out


def _offsets(sections) -> list[dict]:
    out, t = [], 0.0
    for n, _, ps in sections:
        seconds = round(sum(p.seconds for p in ps), 3)
        out.append({"n": n, "seconds": seconds, "offset": round(t, 3)})
        t += seconds
    return out


def _merged(r: Run, cfg: dict, sections, layout: list[dict], script: str, duration: float) -> dict | None:
    """The chapter timing from the section timings, or None if one is missing or the result fails
    its checks (the caller then times the stitched chapter itself)."""
    timings = [narrate.section_timing(r, n) for n, _, _ in sections]
    if not timings or any(t is None for t in timings):
        return None
    t = merge_timings(timings, [x["offset"] for x in layout])
    t["duration"] = round(duration, 3)
    return None if timing_problems(t, script, cfg["narration"].get("pronunciations") or {}) else t


def rolling(r: Run, cfg: dict) -> dict | None:
    """While the chapter is still being made: 09_script_final.md and 11_episode/timing.json for the
    sections narrated and timed so far, in chapter time, and 05_brief.md with the entries each
    section has by now (write_brief). Ask-by-voice needs the first two to answer a question
    mid-chapter, and reads a partial chapter as it reads a whole one; the brief decides which
    entries the guide sees in full. Free; call it after each section is narrated. Returns the timing,
    or None when there is none to write yet. Once the archive has run it writes nothing: the
    archive's files are the chapter."""
    if (base.path(r, "work") / "archive.json").exists():
        return base.read_json(r.root / timing.EPISODE_DIR / "timing.json")
    write_brief(r)
    sections = []
    for sec in _sections(r, so_far=True):
        if narrate.section_timing(r, sec[0]) is None:
            break
        sections.append(sec)
    if not sections:
        return None
    layout = _offsets(sections)
    script = join_script_segments([(n, text) for n, text, _ in sections])
    t = _merged(r, cfg, sections, layout, script, layout[-1]["offset"] + layout[-1]["seconds"])
    if t is None:
        return None
    base.write_atomic(r.path("script_final"), script)
    base.write_json(r.root / timing.EPISODE_DIR / "timing.json", t)
    return t


def episode(r: Run, cfg: dict) -> float:
    """The episode build's last two steps on the archived run: the reference cards (one Sonnet call a
    section, logged in 11_episode/log.jsonl) and episode.json. Returns what the calls cost, and adds
    it to the run's own log so the run's total and its cap see it. Reuses answers already on disk."""
    from episode import bundle, references

    def spent() -> float:
        return Run(name="episode", root=r.root / timing.EPISODE_DIR).spent()

    before = spent()
    cap = REFERENCES_MAX_USD
    if r.max_usd is not None:
        cap = max(0.0, min(cap, r.max_usd - r.spent()))
    try:
        refs, _ = references.build(r.root, cfg, max_usd=cap)
        references.write(r.root, refs)
        bundle.write(r.root)
    except (SystemExit, FileNotFoundError, KeyError, ValueError, subprocess.CalledProcessError) as e:
        raise StageError(f"The episode build failed ({type(e).__name__}): {e}") from None
    finally:
        cost = round(spent() - before, 5)
        if cost:
            r.log({"stage": STAGE, "label": "episode references (each call is in 11_episode/log.jsonl)",
                   "cost_usd": cost})
    return cost


def run(r: Run, cfg: dict, build_episode: bool = True) -> dict:
    """Stitch, assemble, merge, check, and build the episode. Everything the chapter needs is on disk
    before the episode build starts, so a failure there (a StageError) leaves a chapter that plays;
    run again to finish it. A second run of a finished archive returns what the first returned."""
    sections = _sections(r)
    if not sections:
        raise StageError("Nothing to archive: no section is locked and narrated.")
    layout = _offsets(sections)
    result_path = base.path(r, "work") / "archive.json"
    saved = base.read_json(result_path)
    whole = (r.path("chapter_mp3").exists() and r.path("script_final").exists()
             and (r.root / timing.EPISODE_DIR / "timing.json").exists())
    if not (saved and saved.get("sections") == layout and whole):
        saved = _assemble(r, cfg, sections, layout)
        base.write_json(result_path, saved)
    if build_episode and not saved["episode"]:
        saved["episode_cost_usd"] = episode(r, cfg)
        saved["episode"] = True
        base.write_json(result_path, saved)
    return saved


def _assemble(r: Run, cfg: dict, sections, layout: list[dict]) -> dict:
    ncfg = cfg["narration"]
    pronunciations = ncfg.get("pronunciations") or {}
    script = join_script_segments([(n, text) for n, text, _ in sections])
    base.write_atomic(r.path("script_final"), script)

    parts: list[Path | int] = []
    for _, _, ps in sections:
        for p in ps:
            if p.pause_ms:
                parts.append(p.pause_ms)
            parts.append(p.wav)
    audio_dir = r.path("audio_dir")
    try:
        tts.join_to_mp3(parts, r.path("chapter_mp3"), audio_dir, ncfg["mp3_bitrate"])
        (audio_dir / "joined.wav").unlink(missing_ok=True)  # 48 kB a second, and nothing reads it
        try:
            duration = timing.audio_duration(r.path("chapter_mp3"))
        except (subprocess.CalledProcessError, FileNotFoundError, ValueError) as e:
            raise StageError(f"Could not read the stitched chapter's length: {e}") from None
        total = sum(x["seconds"] for x in layout)
        if abs(duration - total) > LENGTH_TOLERANCE:
            raise StageError(f"The stitched chapter is {duration:.2f} s but its sections add up to {total:.2f} s.")

        t = _merged(r, cfg, sections, layout, script, duration)
        how = "merged"
        if t is None:  # a section without timings, or a merge that failed its checks: time the chapter itself
            how = "rebuilt"
            t = _rebuild(r, pronunciations)
            if problems := timing_problems(t, script, pronunciations):
                raise StageError("The chapter's timing does not stand: " + "; ".join(problems) + ".")
        timing.write(r.root, t)
    except Exception:
        # A chapter.mp3 means a finished run (stages.seed.find_run): one without its timing must not
        # stay, or the same seed would start a second paid run instead of continuing this one.
        r.path("chapter_mp3").unlink(missing_ok=True)
        raise

    write_brief(r)

    r.log({"stage": STAGE, "label": "archive", "audio_seconds": round(duration, 3), "sections": len(sections),
           "timing": how})
    words = sum(len(x["words"]) for s in t["segments"] for x in s["sentences"])  # as heard: no [pause], no markers
    return {"seconds": round(duration, 3), "sections": layout, "words": words, "episode": False, "timing": how}


def write_brief(r: Run) -> bool:
    """05_brief.md as the chapter stands: a locked section lists the entries its text cites; one
    still being written lists the scout entries the outline planned for it and the entries of
    its ledger rows that have landed; the titles are the ones the locks gave. Ask-by-voice shows
    the guide an entry in full (kind, tier, verbatim, source) only when the brief lists it for
    the section being heard or the next one, so this is written after each narrated section
    (rolling) as well as at the archive. One file write, no call. False without an outline."""
    outline = _outline(r)
    if not outline:
        return False
    from . import outline as outline_mod

    with base.LEDGER_LOCK:
        led = base.read_json(r.path("ledger"), {"rows": []})
    cited = {}
    for s in outline["sections"]:
        n, seg = s["n"], r.segment_path(s["n"])
        if seg.exists():
            cited[n] = entry_numbers(" ".join(m.group(0) for m in MARKER.finditer(seg.read_text(encoding="utf-8"))))
        else:
            landed = [ref for row in outline_mod.rows_for(led, n) for ref in row.get("dossier_refs") or []]
            cited[n] = sorted({*(s.get("scout_entries") or []), *entry_numbers(" ".join(f"[{x}]" for x in landed))})
    base.write_atomic(r.path("brief"), _brief(outline_mod.render_brief, outline, led, cited, status.titles(r, outline)))
    return True


def _brief(render, outline: dict, led: dict, cited: dict, titles: dict[int, str]) -> str:
    """05_brief.md with the titles the locks gave (fast/locks/segNN.json; the outline itself is never
    rewritten), so ask-by-voice names a section as the listener's marker does. The titles go in
    through a copy of the outline, and as `titles` too when render_brief takes them."""
    retitled = {**outline, "sections": [{**s, "title": titles.get(s["n"]) or s["title"]} for s in outline["sections"]]}
    if titles and "titles" in inspect.signature(render).parameters:
        return render(retitled, led, cited, titles=titles)
    return render(retitled, led, cited)


def _rebuild(r: Run, pronunciations: dict[str, str]) -> dict:
    """Time the stitched chapter itself: from the pieces' character timings when any piece kept them
    (narrate.timed: one that did not is estimated), else by ear (Whisper, local, a minute or two
    for a chapter)."""
    try:
        if t := narrate.timed(r.root, pronunciations):
            return t
        from episode import recognize
        return timing.build(r.root, recognize.WhisperRecognizer(), pronunciations)
    except (ImportError, RuntimeError, IndexError, subprocess.CalledProcessError) as e:
        raise StageError(f"Could not time the stitched chapter ({type(e).__name__}): {e}") from None
