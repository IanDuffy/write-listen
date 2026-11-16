"""What the interviewer is told for a tester's debrief (docs/tester-demo.md, "The interviewer"): the chapter
they heard and their moments. Never the script or the guide's instructions.

    moments(listener_dir)         [{at_s, clock, words}]: each question they asked mid-chapter (a tap), in
                                  their words, at about where the reading was
    chapter_context(listener_dir) {run, title, summary, minutes_heard, minutes}

Both read the tester's newest live chapter (listeners/<lid>/chapters/<run>/ with fast.json "voice": "live").
A live chapter has no word timing until its archive is built, so a moment's time is estimated from where its
paragraph and sentence sit in the text, at live.page.words_per_second (the page's own estimate for its slider).
"""

from __future__ import annotations

import json
from pathlib import Path

WORDS_PER_SECOND = 2.5  # live.page.words_per_second (voice/gpt_live/chapter.py WORDS_PER_SECOND)


def _read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def latest_chapter(listener_dir: Path) -> Path | None:
    """The tester's newest live chapter folder, by when it was started (fast.json is written first)."""
    found = []
    for d in (Path(listener_dir) / "chapters").glob("*"):
        mode = d / "fast.json"
        if d.is_dir() and mode.exists() and (_read_json(mode, {}) or {}).get("voice") == "live":
            found.append((mode.stat().st_mtime, d.name, d))
    return max(found)[2] if found else None


def _paragraphs(root: Path) -> list[dict]:
    """The chapter's paragraphs in reading order (voice/gpt_live/chapter.py), or [] if it can't be read."""
    from stages.common import Run
    from voice.gpt_live import chapter as live_chapter
    try:
        ch = live_chapter.paragraphs(Run(name=root.name, root=root))
    except Exception:  # noqa: BLE001 (a chapter that can't be split still has a debrief, without times)
        return []
    return [p for s in ch["sections"] for p in s["paragraphs"]]


def _words_before(paras: list[dict], pid: str | None, sentence: int) -> int | None:
    n = 0
    for p in paras:
        if p["id"] == pid:
            return n + sum(len(s.split()) for s in p["sentences"][:max(0, sentence)])
        n += len(p["text"].split())
    return None


def clock(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _wps(cfg: dict | None) -> float:
    try:
        return float(((cfg or {}).get("live") or {}).get("page", {}).get("words_per_second") or WORDS_PER_SECOND)
    except (TypeError, ValueError):
        return WORDS_PER_SECOND


def moments(listener_dir: Path, cfg: dict | None = None) -> list[dict]:
    """Each question asked mid-chapter (12_asks/asides.jsonl, kind "ask"): the first thing they said in it,
    at about where the reading was. A tap where they said nothing is not a moment."""
    root = latest_chapter(listener_dir)
    if root is None:
        return []
    asides = root / "12_asks" / "asides.jsonl"
    if not asides.exists():
        return []
    paras, wps = _paragraphs(root), _wps(cfg)
    out = []
    for line in asides.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("kind") != "ask":
            continue
        said = [str(x.get("text") or "").strip() for x in rec.get("transcript") or [] if x.get("who") == "listener"]
        said = [x for x in said if x]
        if not said:
            continue
        words = _words_before(paras, rec.get("paragraph"), int(rec.get("sentence") or 0))
        at = round(words / wps, 1) if words is not None else None
        out.append({"at_s": at, "clock": clock(at) if at is not None else None, "words": said[0]})
    return sorted(out, key=lambda m: (m["at_s"] is None, m["at_s"] or 0))


def chapter_context(listener_dir: Path, cfg: dict | None = None) -> dict:
    """{run, title, summary, minutes_heard, minutes} for the newest live chapter; Nones when there is none.
    The title is the outline's (it replaces the card's when it lands), the summary the card's (part D writes
    fast/card.json at start_chapter), else the outline's angle."""
    root = latest_chapter(listener_dir)
    if root is None:
        return {"run": None, "title": None, "summary": None, "minutes_heard": 0, "minutes": None}
    outline = _read_json(root / "05_outline.json", {}) or {}
    card = _read_json(root / "fast" / "card.json", {}) or {}
    status = _read_json(root / "status.json", {}) or {}
    player = _read_json(root / "fast" / "player.json", {}) or {}
    title = outline.get("title") or card.get("title") or status.get("title") or root.name
    summary = card.get("summary") or card.get("overview") or outline.get("angle") or ""
    wps = _wps(cfg)
    paras = _paragraphs(root)
    sections = outline.get("sections") or []
    all_locked = bool(sections) and all((root / "06_segments" / f"seg{int(s.get('n', 0)):02d}.md").exists()
                                        for s in sections)
    if all_locked and paras:
        minutes = sum(len(p["text"].split()) for p in paras) / wps / 60
    elif sections and all(s.get("target_words") for s in sections):
        minutes = sum(int(s["target_words"]) for s in sections) / wps / 60
    else:
        minutes = float(((cfg or {}).get("demo") or {}).get("chapter_minutes") or 10)
    # chapter_t is how far they heard, also when they tapped I'm done (state "done" too, part-way: the page's
    # chapter end reports the whole chapter); only a finished reading without one counts as heard to the end.
    try:
        heard = float(player["chapter_t"]) / 60 if player.get("chapter_t") is not None else None
    except (TypeError, ValueError):
        heard = None
    if heard is None:
        heard = minutes if player.get("state") == "done" else 0.0
    return {"run": root.name, "title": title, "summary": summary,
            "minutes_heard": round(min(heard, minutes), 1), "minutes": round(minutes, 1)}
