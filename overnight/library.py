"""The listener's library, seeds and queue (docs/overnight-chapters.md; formats in overnight/DESIGN.md).

    library.jsonl   works, people and topics the listener has met: {"id": "w-<hash>", "kind": work|person|topic, ...}
    seeds.jsonl     questions and ideas for future chapters: {"id": "s-<hash>", "kind": question|idea|parked, ...}
    queue.jsonl     topics set after a cutoff, for a later night: {"id": "q-<time>", "topic", ...}

All three are append-only. An item's id is a hash of what it is, so adding it twice adds it once.
Using a seed or a queued topic appends {"used": <id>, "by": <plan id>, "t": ...}; nothing is rewritten.

after_chapter() is what a finished chapter adds, by code where code can: the ledger's parked ideas
(seeds, parked), topics the guide saved during asides (seeds, idea, with the listener's words), the
works and people its references cite and the chapter's own topic (library). The one model call: which
of the listener's questions during asides the chapter did not answer (seeds, question, their words).
Telling a question from "go on", and whether the chapter took it up, needs reading; code cannot.
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from stages import llm
from stages.common import Run, StageError, load_prompt, parse_json, spoken_text

from . import profiles

SEEDS, LIBRARY, QUEUE = "seeds.jsonl", "library.jsonl", "queue.jsonl"
SEED_KINDS = ("question", "idea", "parked")
WORK_KINDS = ("work", "person", "topic")
ADDED = "13_library.json"  # what after_chapter added, and the question judgement (so a re-run pays nothing)
QUESTION_WORDS = ("what", "why", "how", "who", "whom", "whose", "when", "where", "which", "is", "are", "was",
                  "were", "do", "does", "did", "can", "could", "would", "will", "should", "has", "have", "had")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _norm(s: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))


def _hash(*parts: str) -> str:
    return hashlib.sha1("|".join(_norm(p) for p in parts).encode()).hexdigest()[:10]


def seed_id(kind: str, text: str) -> str:
    return f"s-{_hash(kind, text)}"


def work_id(kind: str, title: str, creator: str | None = None) -> str:
    return f"w-{_hash(kind, title, creator or '')}"


# --- reading ------------------------------------------------------------------------

def _items(path: Path, unused: bool) -> list[dict]:
    recs = profiles.read_jsonl(path)
    used = {r["used"] for r in recs if isinstance(r.get("used"), str)}
    out, seen = [], set()
    for r in recs:
        if "id" not in r or r["id"] in seen or (unused and r["id"] in used):
            continue
        seen.add(r["id"])
        out.append(r)
    return out


def seeds(listener: Path, unused: bool = True) -> list[dict]:
    """The listener's seeds, oldest first; unused=True leaves out those a plan has used."""
    return _items(listener / SEEDS, unused)


def works(listener: Path) -> list[dict]:
    """library.jsonl: works, people and topics, oldest first."""
    return _items(listener / LIBRARY, False)


def queue(listener: Path, unused: bool = True) -> list[dict]:
    """Topics queued for a later night, oldest first (the oldest unused one goes next)."""
    return _items(listener / QUEUE, unused)


# --- adding -------------------------------------------------------------------------

def _add(path: Path, recs: list[dict]) -> list[dict]:
    have = {r.get("id") for r in profiles.read_jsonl(path)}
    added = []
    for rec in recs:
        if rec["id"] in have:
            continue
        have.add(rec["id"])
        profiles.append_jsonl(path, rec)
        added.append(rec)
    return added


def add_seeds(listener: Path, items: list[dict], source: dict | None = None) -> list[dict]:
    """items: {"kind", "text", "words"?, "source"?}. Returns the ones that were new."""
    recs = []
    for it in items:
        text, kind = (it.get("text") or "").strip(), it.get("kind")
        if not text or kind not in SEED_KINDS:
            continue
        recs.append({"id": seed_id(kind, text), "t": _now(), "kind": kind, "text": text,
                     "words": (it.get("words") or "").strip() or None, "source": it.get("source") or source or {}})
    return _add(listener / SEEDS, recs)


def add_works(listener: Path, items: list[dict], source: dict | None = None) -> list[dict]:
    """items: {"kind", "title", "creator"?, "note"?, "source"?}. Returns the ones that were new."""
    recs = []
    for it in items:
        title, kind = (it.get("title") or "").strip(), it.get("kind")
        if not title or kind not in WORK_KINDS:
            continue
        creator = (it.get("creator") or "").strip() or None
        recs.append({"id": work_id(kind, title, creator), "t": _now(), "kind": kind, "title": title,
                     "creator": creator, "note": (it.get("note") or "").strip(),
                     "source": it.get("source") or source or {}})
    return _add(listener / LIBRARY, recs)


def add_queue(listener: Path, topic: str, notes: str = "", session: str | None = None,
              mode: str = "topic") -> dict:
    """A change made after the cutoff: it takes the next planning pass ahead of the mode."""
    base = f"q-{time.strftime('%Y%m%d-%H%M%S')}"
    have = {r.get("id") for r in profiles.read_jsonl(listener / QUEUE)}
    qid, n = base, 2
    while qid in have:
        qid, n = f"{base}-{n}", n + 1
    rec = {"id": qid, "t": _now(), "mode": mode, "topic": topic.strip(), "notes": (notes or "").strip(),
           "session": session}
    profiles.append_jsonl(listener / QUEUE, rec)
    return rec


def queue_add(listener: Path, request: dict) -> dict:
    """add_queue for a set_next_chapter request ({mode, topic, notes, session}) made after the cutoff."""
    return add_queue(listener, request.get("topic") or "", request.get("notes") or "", request.get("session"),
                     request.get("mode") or "topic")


def mark_used(listener: Path, ids: list[str], by: str, file: str = SEEDS) -> None:
    """Append {"used": id, "by": plan id} for each id (seeds.jsonl, or queue.jsonl with file=QUEUE)."""
    for i in ids:
        if i:
            profiles.append_jsonl(listener / file, {"used": i, "by": by, "t": _now()})


# --- what a finished chapter adds ---------------------------------------------------

def chapter_title(root: Path) -> str:
    card = profiles.read_json(root / "00_source" / "up_next.json") or {}
    if card.get("title"):
        return card["title"]
    ep = profiles.read_json(root / "11_episode" / "episode.json") or {}
    if ep.get("title"):
        return ep["title"]
    for name, head in (("05_brief.md", "Brief"), ("03_angle.md", "Angle")):
        p = root / name
        if p.exists():
            m = re.search(rf"^#\s+{head}:\s*(.+)$", p.read_text(encoding="utf-8"), re.M)
            if m:
                return m.group(1).strip()
    return root.name


def parked(root: Path) -> list[str]:
    led = profiles.read_json(root / "ledger.json") or {}
    out = []
    for x in led.get("parked") or []:
        idea = x if isinstance(x, str) else (x.get("idea") if isinstance(x, dict) else "")
        if idea and idea.strip():
            out.append(idea.strip())
    return out


def asides(root: Path) -> list[dict]:
    return profiles.read_jsonl(root / "12_asks" / "asides.jsonl")


def saved_seeds(root: Path) -> list[dict]:
    """Topics the guide saved during asides (12_asks/seeds.md), each with the listener's last words
    before the save (from the aside's transcript), when there is one."""
    p = root / "12_asks" / "seeds.md"
    if not p.exists():
        return []
    said: dict[str, str] = {}
    for a in asides(root):
        last = None
        for ln in a.get("transcript") or []:
            if ln.get("who") == "listener" and (ln.get("text") or "").strip():
                last = ln["text"].strip()
            elif ln.get("who") == "tool" and ln.get("name") == "save_seed" and last:
                said.setdefault(_norm((ln.get("input") or {}).get("topic")), last)
    out = []
    for block in re.split(r"^##\s+", p.read_text(encoding="utf-8"), flags=re.M)[1:]:
        topic, _, rest = block.partition("\n")
        why = re.search(r"^Why:\s*(.+)$", rest, re.M)
        out.append({"topic": topic.strip(), "why": why.group(1).strip() if why else "",
                    "words": said.get(_norm(topic))})
    return out


def is_question(text: str) -> bool:
    words = re.findall(r"[a-z']+", text.lower())
    return len(words) >= 4 and ("?" in text or words[0] in QUESTION_WORDS)


def aside_questions(root: Path) -> list[dict]:
    """The listener's questions during asides, in their words, with where in the chapter they asked."""
    out, seen = [], set()
    for a in asides(root):
        for ln in a.get("transcript") or []:
            text = (ln.get("text") or "").strip()
            if ln.get("who") == "listener" and is_question(text) and _norm(text) not in seen:
                seen.add(_norm(text))
                out.append({"words": text, "aside": a.get("id"), "segment": a.get("segment")})
    return out


LIBRARY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["open"],
    "properties": {"open": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["n", "question"],
        "properties": {"n": {"type": "integer"}, "question": {"type": "string"}}}}},
}


def open_questions(cfg: dict, root: Path, questions: list[dict]) -> list[dict]:
    """Of the listener's aside questions, those the chapter left open, each restated as a question a
    later chapter could take up. One cheap call (stage overnight_library), logged to the chapter."""
    if not questions:
        return []
    script = root / "09_script_final.md"
    text = spoken_text(script.read_text(encoding="utf-8")) if script.exists() else "(no script)"
    listed = "\n".join(f"{i}. (asked during section {q.get('segment') or '?'}) {q['words']}"
                       for i, q in enumerate(questions, 1))
    r = Run(name=root.name, root=root)
    res = llm.call(r, cfg, "overnight_library", messages=[{"role": "user", "content": load_prompt(
        "overnight_library", title=chapter_title(root), script=text, questions=listed)}],
        json_schema=LIBRARY_SCHEMA, label="library: open questions", preapproved=True)
    data = parse_json(r, res.text, "overnight_library")
    out = []
    for x in data.get("open") or []:
        n = x.get("n")
        if isinstance(n, int) and 1 <= n <= len(questions) and (x.get("question") or "").strip():
            out.append({**questions[n - 1], "question": x["question"].strip()})
    return out


def after_chapter(cfg: dict, listener: Path, chapter_root: Path) -> dict:
    """Add what a finished chapter yields to seeds.jsonl and library.jsonl. Safe to run again: ids
    dedupe, and the question judgement is kept in the chapter's 13_library.json, with the questions
    judged so far ("judged"): questions asked since (asides.jsonl grew) are judged on the next run,
    which each planning pass makes for the latest chapter (its asides come after its build)."""
    if listener is None:
        raise StageError(f"library: {chapter_root} is not in a listener's chapters folder.")
    root, name = Path(chapter_root), Path(chapter_root).name
    done = profiles.read_json(root / ADDED) or {}
    src = {"chapter": name}
    seed_items = [{"kind": "parked", "text": p, "words": None, "source": src} for p in parked(root)]
    for s in saved_seeds(root):
        seed_items.append({"kind": "idea", "text": s["topic"] + (f" ({s['why']})" if s["why"] else ""),
                           "words": s["words"], "source": {**src, "aside": "seeds.md"}})
    questions = aside_questions(root)
    judged = set(done.get("judged") or [])
    if "open_questions" in done and "judged" not in done:  # written before "judged" was kept
        judged = {_norm(q["words"]) for q in done["open_questions"]}
    new_qs = [q for q in questions if _norm(q["words"]) not in judged]
    open_qs = list(done.get("open_questions") or [])
    if new_qs or "open_questions" not in done:  # asides after the last pass (the morning's) get judged
        try:
            open_qs += open_questions(cfg, root, new_qs)
        except StageError as e:  # better a seed too many than a question lost
            print(f"  library: open questions not judged ({e}); keeping every question asked")
            open_qs += [{**q, "question": q["words"]} for q in new_qs]
    judged |= {_norm(q["words"]) for q in new_qs}
    for q in open_qs:
        seed_items.append({"kind": "question", "text": q["question"], "words": q["words"],
                           "source": {**src, "aside": q.get("aside")}})
    work_items = [{"kind": "topic", "title": chapter_title(root), "creator": None,
                   "note": ((profiles.read_json(root / "00_source" / "up_next.json") or {}).get("overview") or ""),
                   "source": src}]
    for ref in profiles.read_json(root / "11_episode" / "references.json") or []:
        if isinstance(ref, dict) and ref.get("kind") in ("work", "person") and ref.get("title"):
            work_items.append({"kind": ref["kind"], "title": ref["title"], "creator": ref.get("creator"),
                               "note": ref.get("note") or "", "source": {**src, "ref": ref.get("id")}})
    new_seeds = add_seeds(listener, seed_items)
    new_works = add_works(listener, work_items)
    profiles.write_json(root / ADDED, {"t": _now(), "open_questions": open_qs, "judged": sorted(judged),
                                       "seeds": sorted({*done.get("seeds", []), *(s["id"] for s in new_seeds)}),
                                       "library": sorted({*done.get("library", []), *(w["id"] for w in new_works)})})
    print(f"  library: {len(new_seeds)} new seeds, {len(new_works)} new library items")
    return {"seeds": len(new_seeds), "library": len(new_works)}
