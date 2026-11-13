"""A synthetic chapter for the ask tests: 3 segments x 3 paragraphs x 6 sentences x 10 words, every
word 0.5 s and unique ("S2P1X3W7." style), with a 0.5 s gap after each sentence and 2 s after each
segment. The script, brief, dossier, ledger and 11_episode/timing.json agree, so sentence ids are the
ones episode.script gives. About 5 minutes, long enough for the 90 s and 3 minute windows."""

import json
import subprocess
from pathlib import Path

SEGS, PARAS, SENTS, WORDS = 3, 3, 6, 10
WORD, GAP, SEG_GAP = 0.5, 0.5, 2.0


def word(s, p, x, w):
    return f"S{s}P{p}X{x}W{w}"


def build(root: Path, audio: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    t, segments, script, entry = 0.0, [], [], 1
    for s in range(1, SEGS + 1):
        sents, paras_text, n = [], [], 0
        for p in range(1, PARAS + 1):
            para = []
            for x in range(1, SENTS + 1):
                n += 1
                ws = []
                for w in range(1, WORDS + 1):
                    text = word(s, p, x, w) + ("." if w == WORDS else "")
                    ws.append({"w": text, "start": round(t, 3), "end": round(t + WORD, 3)})
                    t += WORD
                t += GAP
                sents.append({"id": f"seg{s:02d}-s{n:03d}", "start": ws[0]["start"], "end": ws[-1]["end"],
                              "text": " ".join(w["w"] for w in ws), "words": ws})
                para.append(" ".join(w["w"] for w in ws) + (f" [D{entry:02d}]" if x == 1 else ""))
                entry += x == 1
            paras_text.append(" ".join(para))
        t += SEG_GAP - GAP
        segments.append({"id": f"seg{s:02d}", "start": sents[0]["start"], "end": sents[-1]["end"], "sentences": sents})
        script.append(f"<!-- seg{s:02d} -->\n\n" + "\n\n".join(paras_text))
    duration = round(segments[-1]["end"] + 1.0, 3)
    (root / "11_episode").mkdir(exist_ok=True)
    (root / "11_episode" / "timing.json").write_text(json.dumps(
        {"audio": "10_audio/chapter.mp3", "duration": duration, "aligned_by": "synthetic", "segments": segments}))
    (root / "09_script_final.md").write_text("\n\n".join(script) + "\n")
    (root / "03_angle.md").write_text("# Angle: A Synthetic Chapter\n\nA chapter made of numbered words, for tests.\n")
    brief = ["# Brief: A Synthetic Chapter", "", "## Through-line", "", "Numbers all the way down.", ""]
    for s in range(1, SEGS + 1):
        first = (s - 1) * PARAS + 1
        brief += [f"## Segment {s}: Part {s}", "", f"- **Purpose:** Say segment {s}'s words.", "- **Target words:** 180",
                  f"- **Ledger:** L0{s}", f"- **Dossier:** " + ", ".join(f"D{k:02d}" for k in range(first, first + PARAS)), ""]
    (root / "05_brief.md").write_text("\n".join(brief))
    entries = [f"[D{k:02d}] (L0{(k - 1) // PARAS + 1}, claim, tier 2, verbatim: no) Entry {k} says a thing about "
               f"segment {(k - 1) // PARAS + 1} in quite a few words so that the one line version is shorter than "
               f"the full one. Source: https://example.org/{k}" for k in range(1, SEGS * PARAS + 1)]
    (root / "04_dossier.md").write_text("# Dossier: A Synthetic Chapter\n\n## Entries\n\n" + "\n\n".join(entries) + "\n")
    (root / "ledger.json").write_text(json.dumps({"rows": [{"id": f"L0{s}", "rank": s, "type": "claim",
                                                            "idea": f"Idea {s}."} for s in range(1, SEGS + 1)],
                                                  "parked": ["A parked idea."], "connections": []}))
    if audio:
        (root / "10_audio").mkdir(exist_ok=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", str(duration),
                        "-b:a", "8k", str(root / "10_audio" / "chapter.mp3")], check=True)
    return root


def sentence_starts(root: Path) -> dict:
    t = json.loads((root / "11_episode" / "timing.json").read_text())
    return {x["id"]: x["start"] for s in t["segments"] for x in s["sentences"]}
