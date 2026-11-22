"""`wl study report`: every tester's debrief, transcripts and state into study/report.md (docs/tester-demo.md,
"Admin page and study report").

Code gathers the material (demo/state.py for stage, minutes and questions; the study folder for the
summaries and transcripts) and counts the drop-off itself; one Claude call (prompts/study_report.md,
models.writer) writes the report. Its cost goes to study/log.jsonl at the repo root, beside the report
(gitignored: it quotes the testers).
"""

from __future__ import annotations

import time
from pathlib import Path

from stages import common, llm
from stages.common import ROOT, Run, StageError, load_prompt

from . import state, testers

STUDY_DIR = ROOT / "study"
STAGE = "study_report"


def _clock(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def chapter_parts(cfg: dict, root: Path | None) -> list[str]:
    """The chapter's parts with their approximate start, from the locked sections' words (else the
    outline's targets) at the reading pace: so a moment can be matched to a part."""
    if root is None:
        return []
    outline = state.profiles.read_json(root / "05_outline.json") or {}
    wps = float(((cfg.get("live") or {}).get("page") or {}).get("words_per_second") or 2.5)
    out, at = [], 0.0
    for sec in outline.get("sections") or []:
        n = sec.get("n")
        seg = root / "06_segments" / f"seg{int(n):02d}.md" if isinstance(n, int) else None
        words = common.word_count(common.spoken_text(seg.read_text(encoding="utf-8"))) if seg and seg.exists() \
            else int(sec.get("target_words") or 0)
        out.append(f"{_clock(at)} {sec.get('title') or f'Part {n}'}")
        at += words / wps
    return out


def moments(lid: str) -> list[str]:
    """The questions they asked mid-chapter, as the debrief was told them (part C's demo.debrief.moments),
    else from the asides alone (words, no time)."""
    d = testers.folder(lid)
    try:
        from demo import debrief  # part C's
        return [f"at {m.get('clock', '?')}: \"{m.get('words', '')}\"" for m in debrief.moments(d)]
    except Exception:  # not merged yet, or it failed on this folder: the report goes ahead with less
        return [f"(paragraph {a.get('paragraph', '?')}): \"{' / '.join(a['said'])}\""
                for a in state.asks(state.newest_chapter(d))]


def _ended(d: Path, lid: str) -> float | None:
    """When the tester ended, as their records have it: their newest debrief's start, else their last own write
    (file times: a folder copied without them, scp without -p, reads as ended now)."""
    for p in sorted((d / "study").glob("debrief-*.jsonl"), reverse=True):
        for rec in state.read_jsonl(p)[:1]:
            t = state._ts(rec.get("t"))
            if t:
                return t
    return state.last_active(d, testers.record(lid) or {})


def did(cfg: dict, lid: str) -> list[str]:
    """What the tester did (Ian's notes, 2026-11-18): the account their debrief was given, as recorded when it
    started; for a tester with no recorded account, the same account from their records as of when they ended
    (a chapter goes on being made after they leave: read as of now, every drop-off during the wait said "it was
    ready, but they didn't start it"; review, 2026-11-18)."""
    d = testers.folder(lid)
    try:
        from demo import debrief, interviewer
        kept = interviewer.recorded_account(d / "study")
        if kept:
            return [x[2:] if x.startswith("- ") else x for x in kept.splitlines() if x.strip()]
        got = interviewer.account(debrief.chapter_context(d, cfg), debrief.moments(d, cfg),
                                  debrief.activity(d, cfg, now=_ended(d, lid)))
        return [] if got == [interviewer.notes()["did_unknown"]] else got
    except Exception:  # the report goes ahead with less
        return []


def transcript(p: Path) -> str:
    from demo import debrief_earpiece
    lines = []
    for rec in state.read_jsonl(p):
        text = str(rec.get("text") or "").strip()
        if rec.get("who") == "page" and debrief_earpiece.whisper_in(text):
            continue  # the debrief's earpiece notes (Earpiece history shows them): not what the tester said
        if text:
            lines.append(f"[{str(rec.get('t') or '')[11:19]}] {rec.get('who', '?')}: {text}")
    return "\n".join(lines) or "(empty)"


def tester_material(cfg: dict, s: dict) -> str:
    d = testers.folder(s["lid"])
    study = d / "study"
    stage = s["stage"] + (" (left early)" if s.get("left_early") else "")
    ch = d / "chapters" / s["chapter"] if s.get("chapter") else None
    parts = chapter_parts(cfg, ch)
    asked = moments(s["lid"]) if ch else []
    out = [f"## Tester: {s['label']}",
           f"Name: {s.get('name') or '(not given)'}",
           f"Stage reached: {stage}",
           f"Chapter: {s.get('title') or '(none)'}"
           + (f" (rehearsal: replayed {s.get('rehearsal_source')})" if s.get("rehearsal") else "")
           + f"; heard {s['minutes_heard']} of {s['chapter_minutes']} minutes",
           "Chapter parts (approximate start): " + ("; ".join(parts) if parts else "(none)"),
           "Questions asked mid-chapter: " + ("; ".join(asked) if asked else "none"),
           "What they did: " + (" ".join(did(cfg, s["lid"])) or "(no record)")]
    summary = study / "debrief_summary.md"
    out += ["", "### Debrief summary", summary.read_text(encoding="utf-8").strip() if summary.exists() else "(none)"]
    for kind in ("intro", "debrief"):
        for p in sorted(study.glob(f"{kind}-*.jsonl")):
            out += ["", f"### {kind.capitalize()} transcript ({p.stem})", transcript(p)]
    return "\n".join(out)


def stage_counts(states: list[dict]) -> str:
    lines = []
    for stage in state.STAGES:
        who = [s for s in states if s["stage"] == stage]
        if who:
            labels = ", ".join(s["label"] + (" (left early)" if s.get("left_early") else "") for s in who)
            lines.append(f"- {stage}: {len(who)} ({labels})")
    return "\n".join(lines) or "- (no testers)"


def build(cfg: dict, yes: bool = False) -> Path:
    states = state.all_states(cfg)
    if not states:
        raise StageError("No testers yet: nothing to report.")
    if not any(s["stage"] != "not opened" for s in states):
        raise StageError("No tester has opened their link yet: nothing to report.")
    material = "\n\n".join(tester_material(cfg, s) for s in states if s["stage"] != "not opened")
    prompt = load_prompt("study_report", count=str(len(states)), stages=stage_counts(states), material=material,
                         today=time.strftime("%Y-%m-%d"))
    STUDY_DIR.mkdir(parents=True, exist_ok=True)
    run = Run(name="study", root=STUDY_DIR, yes=yes)
    # One long read and a careful write: thinking counts against max_tokens, as in the other long stages.
    res = llm.call(run, cfg, STAGE, role="writer", messages=[{"role": "user", "content": prompt}],
                   max_tokens=32000, effort="medium", expected_output_tokens=12000, label="study report")
    text = (res.text or "").strip()
    if not text:
        raise StageError("The report came back empty.")
    out = STUDY_DIR / "report.md"
    out.write_text(text + "\n", encoding="utf-8")
    return out
