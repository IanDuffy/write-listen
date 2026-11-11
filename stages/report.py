"""report.md: lengths, cost per stage, ten claims to spot-check, and blanks for Ian's verdict."""

from __future__ import annotations

import random
import re
import subprocess
from collections import defaultdict
from pathlib import Path

from .common import Run, dossier_entries, entry_id, entry_numbers, spoken_text, word_count

MARKERS = re.compile(r"(?:\s*\[D\d+[^\[\]\n]{0,40}\])+")


def duration_seconds(mp3: Path) -> float | None:
    if not mp3.exists():
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mp3)],
            capture_output=True, text=True, check=True).stdout.strip()
        return float(out)
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return None


def fmt_duration(s: float | None) -> str:
    if s is None:
        return "not narrated yet"
    return f"{int(s // 60)}:{int(s % 60):02d}"


def claim_sentences(script: str) -> list[tuple[str, list[int]]]:
    """Sentences that carry a [Dn] marker, with the dossier entry numbers they cite."""
    # Anchored on the markers, not on sentence boundaries, so 'St. Augustine' or
    # '3.5' never cuts a sample short: each sample is the text since the previous
    # marker (or the paragraph start) up to this run of markers.
    body = re.sub(r"<!--.*?-->", "", script, flags=re.S)
    out = []
    for para in body.split("\n\n"):
        start = 0
        for m in MARKERS.finditer(para):
            text = spoken_text(para[start:m.start()])
            refs = entry_numbers(m.group(0))
            if text and refs:
                out.append((text, refs))
            start = m.end()
    return out


def cost_table(log: list[dict]) -> tuple[list[tuple[str, int, float]], float]:
    by_stage: dict[str, list] = defaultdict(lambda: [0, 0.0])
    for rec in log:
        st = rec.get("stage", "?")
        by_stage[st][0] += 1 if rec.get("provider") not in (None, "none") else 0
        by_stage[st][1] += rec.get("cost_usd", 0) or 0
    rows = [(st, calls, cost) for st, (calls, cost) in by_stage.items()]
    return rows, sum(c for _, _, c in rows)


def run(r: Run, cfg: dict, sample: int = 10, seed: int | None = None):
    final = r.read("script_final")
    dossier = r.read("dossier")
    r.check_writable("report")
    baseline = r.path("baseline_script").read_text(encoding="utf-8") if r.path("baseline_script").exists() else None

    lines = [f"# Report: {r.name}", "", "## Length", "",
             "| | Words | Duration |", "| --- | ---: | ---: |",
             f"| Pipeline chapter | {word_count(final):,} | {fmt_duration(duration_seconds(r.path('chapter_mp3')))} |",
             f"| Baseline | {word_count(baseline) if baseline else 'not written'} | "
             f"{fmt_duration(duration_seconds(r.path('baseline_mp3')))} |", ""]

    rows, total = cost_table(r.read_log())
    lines += ["## Cost by stage", "", "Claude figures come from the API's reported usage; "
              "ElevenLabs figures are characters times the rate in config.yaml.", "",
              "| Stage | API calls | Cost (USD) |", "| --- | ---: | ---: |"]
    lines += [f"| {st} | {calls} | {cost:.2f} |" for st, calls, cost in rows]
    lines += [f"| **Total** | | **{total:.2f}** |", ""]

    entries = dossier_entries(dossier)
    claims = claim_sentences(final)
    rng = random.Random(seed)
    picked = rng.sample(claims, min(sample, len(claims)))
    lines += ["## Spot-check: claims sampled from the final script", "",
              f"{len(claims)} sentences in the final script carry a dossier reference; "
              f"{len(picked)} sampled at random. For each, check the sentence against the source.", ""]
    for i, (sentence, refs) in enumerate(picked, 1):
        lines += [f"### {i}. {sentence}", ""]
        for c in refs:
            entry = entries.get(c)
            lines += [f"> {entry}".replace("\n", "\n> ") if entry else f"> {entry_id(c)}: not found in the dossier!", ""]
        lines += ["Verdict (supported / not supported / invented): ", ""]
    unmarked_note = ("Sentences without a marker are not sampled. A factual sentence with no marker "
                     "is itself a finding: note any you hear.")
    lines += [unmarked_note, ""]

    lines += ["## Ian's verdict", "",
              "1. Finished the chapter without skipping: ",
              "2. Learned something the episode did not give me: ",
              "3. Want chapter two: ",
              "4. Preferred the pipeline chapter in the blind listen (A or B, before opening the key): ",
              "5. Spot-check found no invented facts: ", "",
              "If 1-4 fail, the idea is not worth building further. If only 5 fails, "
              "the fix is in research and fact-checking.", ""]
    out = r.write("report", "\n".join(lines))
    print(f"  report.md: {len(picked)} claims sampled, total cost ${total:.2f}")
    return out
