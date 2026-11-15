"""The listener's memory (Phase 1, milestone 5; docs/phase1-onboarding.md, "Memory store").

Plain files in the listener's folder (listeners/<id>/, common.listener_dir), readable and hand-editable:

    profile.md         what the listener has said about themselves, in their words; under a page
    guide.md           the guide's identity for this listener: name, how it chose it, when they met
    sessions/<id>.jsonl  each conversation's transcript, written during it (stages/guide.py)
    seeds/<run>.md     the seed of each chapter, saved when start_chapter is called
    history.md         chapters made so far: title, angle, the ledger's parked ideas
    library.jsonl, seeds.jsonl  works and topics they mentioned, questions and ideas for future chapters
                       (overnight/library.py; the pass appends to them, never rewrites)

after_session() is the pass after a conversation: Opus reads the transcript and
rewrites profile.md and guide.md under the doc's rules, and lists what the listener
mentioned for the library and seeds (Overnight Chapters, "Change by voice" step 5) (record only what the
listener said, keep it small, honour "forget that"). record_chapter() appends a
finished seed run to history.md, by code. The guide reads profile.md, guide.md and
the last few history.md entries at the start of each conversation, which is what
makes a returning conversation different from a first one.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from pathlib import Path

from . import llm
from .common import ROOT, Run, StageError, listener_dir, load_prompt, parse_json

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["profile", "guide", "forget_chapters", "library", "seeds"],
    "properties": {
        "profile": {"type": "string"}, "guide": {"type": "string"},
        "forget_chapters": {"type": "array", "items": {"type": "string"}},
        # Overnight Chapters: what the listener mentioned, for library.jsonl and seeds.jsonl (overnight/library.py)
        "library": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["kind", "title", "creator", "note"],
            "properties": {"kind": {"type": "string", "enum": ["work", "person", "topic"]},
                           "title": {"type": "string"}, "creator": {"type": "string"}, "note": {"type": "string"}}}},
        "seeds": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["kind", "text", "words"],
            "properties": {"kind": {"type": "string", "enum": ["question", "idea"]},
                           "text": {"type": "string"}, "words": {"type": "string"}}}},
    },
}
# One pass at a time: each reads the files the last one wrote, so an older pass can never
# overwrite a newer one (or bring back something the listener asked to forget).
LOCK = threading.Lock()


def _read(p: Path, missing: str) -> str:
    return p.read_text(encoding="utf-8").strip() if p.exists() else missing


def transcript_text(path: Path) -> str:
    """A session's jsonl as plain lines, tool calls summarized."""
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        who = rec.get("who")
        if who in ("listener", "guide", "page"):
            lines.append(f"{who}: {rec.get('text', '')}")
        elif who == "tool":
            lines.append(f"tool {rec.get('name')}: {json.dumps(rec.get('input'), ensure_ascii=False)} -> "
                         f"{json.dumps(rec.get('result'), ensure_ascii=False)}")
    return "\n".join(lines)


def had_listener(path: Path) -> bool:
    return path.exists() and any('"who": "listener"' in line for line in path.read_text(encoding="utf-8").splitlines())


def session_date(transcript: Path) -> str:
    """The day a conversation happened, from its id (YYYYMMDD-HHMMSS)."""
    m = re.match(r"(\d{4})(\d{2})(\d{2})-", transcript.stem)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else time.strftime("%Y-%m-%d")


def newest_done(cfg: dict) -> str | None:
    done = sorted(p.stem for p in (listener_dir(cfg) / "sessions").glob("*.done"))
    return done[-1] if done else None


def after_session(cfg: dict, transcript: Path) -> bool:
    """Update profile.md and guide.md from one conversation. Returns whether it ran (a session in
    which the listener said nothing is skipped). Each session is passed once (sessions/<id>.done),
    in order: a conversation older than one already passed is skipped, since folding it in now
    would let older facts (or something since forgotten) override newer ones."""
    with LOCK:
        return _after_session(cfg, transcript)


def _after_session(cfg: dict, transcript: Path) -> bool:
    done = transcript.with_suffix(".done")
    if done.exists() or not had_listener(transcript):
        return False
    newest = newest_done(cfg)
    if newest and transcript.stem < newest:
        done.write_text("skipped: older than a conversation already remembered\n", encoding="utf-8")
        return False
    d = listener_dir(cfg)
    from overnight import library  # the listener's library and seeds (overnight/library.py)
    known = [f"- ({w['kind']}) {w['title']}" for w in library.works(d)[-60:]] + \
            [f"- (seed, {s['kind']}) {s['text']}" for s in library.seeds(d)[-40:]]
    prompt = load_prompt("memory", profile=_read(d / "profile.md", "(none yet)"),
                         guide=_read(d / "guide.md", "(none yet: this was a first meeting)"),
                         history=_read(d / "history.md", "(none yet)"), known="\n".join(known) or "(nothing yet)",
                         transcript=transcript_text(transcript), today=session_date(transcript))
    log_run = Run(name="sessions", root=transcript.parent)
    res = llm.call(log_run, cfg, "memory", messages=[{"role": "user", "content": prompt}], json_schema=SCHEMA,
                   label="memory", preapproved=True, log_extra={"session": transcript.stem})
    data = parse_json(log_run, res.text, "memory")
    for name in ("profile", "guide"):
        text = (data.get(name) or "").strip()
        if text:
            p = d / f"{name}.md"
            tmp = p.with_name(f"{name}.md.{transcript.stem}.tmp")
            tmp.write_text(text + "\n", encoding="utf-8")
            tmp.replace(p)
    forget_chapters(cfg, [x for x in data.get("forget_chapters") or [] if isinstance(x, str)])
    remember_library(d, data, transcript)
    done.write_text(time.strftime("%Y-%m-%dT%H:%M:%S") + "\n", encoding="utf-8")
    return True


def remember_library(d: Path, data: dict, transcript: Path) -> None:
    """Append the works, people, topics and seeds the pass found to library.jsonl and seeds.jsonl.
    A seed keeps the listener's words only if they are in the transcript as the listener said them."""
    from overnight import library
    src = {"session": transcript.stem}
    said = []
    for ln in transcript.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if rec.get("who") == "listener":
            said.append(rec.get("text") or "")
    said = library._norm(" ".join(said))
    items = [x for x in data.get("seeds") or [] if isinstance(x, dict)]
    for x in items:
        if x.get("words") and library._norm(x["words"]) not in said:
            x["words"] = ""
    library.add_seeds(d, items, source=src)
    library.add_works(d, [x for x in data.get("library") or [] if isinstance(x, dict)], source=src)


def forget_chapters(cfg: dict, runs: list[str]) -> None:
    """Remove chapters the listener asked to forget from history.md and seeds/ (by run name)."""
    if not runs:
        return
    d = listener_dir(cfg)
    h = d / "history.md"
    if h.exists():
        parts = h.read_text(encoding="utf-8").split("\n## ")
        keep = [parts[0]] + [p for p in parts[1:] if not any(f"({run}," in p.split("\n", 1)[0] for run in runs)]
        h.write_text("\n## ".join(keep).rstrip() + "\n", encoding="utf-8")
    for run in runs:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run):
            (d / "seeds" / f"{run}.md").unlink(missing_ok=True)


def pending_sessions(cfg: dict) -> list[Path]:
    """Conversations that ended without their memory pass (the server stopped, a crash)."""
    s = listener_dir(cfg) / "sessions"
    return sorted(p for p in s.glob("*.jsonl") if not p.with_suffix(".done").exists() and had_listener(p))


def save_seed(cfg: dict, run_root: Path) -> None:
    """listener/seeds/<run>.md: the seed of a chapter the guide started."""
    seed = run_root / "00_source" / "seed.md"
    if seed.exists():
        out = listener_dir(cfg) / "seeds"
        out.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(seed, out / f"{run_root.name}.md")


def record_chapter(cfg: dict, r: Run) -> None:
    """Append a finished seed run to listener/history.md: title, angle, parked ideas. Once per run."""
    p = listener_dir(cfg) / "history.md"
    text = p.read_text(encoding="utf-8") if p.exists() else "# Chapters made so far\n"
    if f"({r.name}," in text:
        return
    angle = r.path("angle").read_text(encoding="utf-8") if r.path("angle").exists() else ""
    title = re.search(r"^#\s+Angle:\s*(.+)$", angle, re.M)
    brief = r.path("brief").read_text(encoding="utf-8") if r.path("brief").exists() else ""
    btitle = re.search(r"^#\s+Brief:\s*(.+)$", brief, re.M)
    thesis = next((ln.strip() for ln in angle.splitlines()[1:] if ln.strip() and not ln.startswith("#")), "")
    parked = []
    if r.path("ledger").exists():
        try:
            parked = [x if isinstance(x, str) else x.get("idea", "")
                      for x in json.loads(r.path("ledger").read_text(encoding="utf-8")).get("parked", [])]
        except (json.JSONDecodeError, AttributeError):
            parked = []
    name = (btitle or title).group(1).strip() if (btitle or title) else r.name
    entry = [f"\n## {name} ({r.name}, {time.strftime('%Y-%m-%d')})", ""]
    if thesis:
        entry.append(f"- Angle: {thesis}")
    if parked:
        entry.append("- Parked, for a later chapter: " + "; ".join(x for x in parked if x))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.rstrip() + "\n" + "\n".join(entry) + "\n", encoding="utf-8")


def run_pending(cfg: dict) -> int:
    """Run the memory pass for every conversation still waiting for one; returns how many ran."""
    n = 0
    for t in pending_sessions(cfg):
        try:
            n += after_session(cfg, t)
        except StageError as e:
            print(f"  memory: {t.name}: {e}")
    return n
