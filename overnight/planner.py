"""The planning pass (docs/overnight-chapters.md, "Planning the next chapter"): writes up_next.json.

Cheap by design (Sonnet, a few web searches, no deep research; the overnight build does that):

    1 choose   structured: continue, branch, or shape the listener's topic; the source; scout queries
    2 scout    Sonnet with web search only (overnight.plan.scout_searches): what exists on it, briefly
    3 card     structured: title, overview, summary, research questions, angle. Code checks the title
               against every past chapter's; a near repeat gets one retry, then the pass stops.

Which way it goes, in order: a request from Change by voice (its mode); else the oldest unused topic in
queue.jsonl (then marked used); else the listener's chapter.mode. Surprise lets the model choose
continue or branch, except that after three continuations in a row (each chapter keeps its card in
00_source/up_next.json, with `decided`), or with no finished chapter, only branch is offered. A seed the
card came from is marked used.

Files: listeners/<id>/plans/<plan id>/ (log.jsonl: every call, priced; inputs.md, choice.json, scout.md,
card.json), then up_next.json, whole. seed_text(card) is 00_source/seed.md for the build's lock stage.

Before reading, the pass brings the latest chapter's yield up to date (library.after_chapter: the
questions asked and topics saved during its asides, which happen after the build). The final write is
under the card lock (overnight/locks.py), shared with the runner in another process, and re-checks the
card first: a request whose cutoff has passed meanwhile (or whose card is now building) is queued
instead (the result carries "queued": True); a pass without a request whose card changed meanwhile
(id or status) is dropped, and the card as it is returned. A request past the cutoff at the start is
queued the same way. A pass without a request that fails is recorded in plan_failures.jsonl (the
tick backs off on it, schedule.plan_backoff); a queued topic whose passes fail QUEUE_TRIES times is
marked used, so it cannot block every night.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from stages import llm, research
from stages.common import Run, StageError, load_prompt, parse_json

from . import library, locks, profiles, schedule

FAILURES = "plan_failures.jsonl"  # {"t", "plan", "queued", "error", "reason"}: failed passes without a request
_monotonic = time.monotonic  # the pass's own clock (tests move it)
QUEUE_TRIES = 2  # failed passes with one queued topic before it is set aside (marked used)
STREAK = 3  # continuations in a row, after which surprise branches
LOCK = threading.Lock()  # one pass at a time in a process (the 18:00 pass and a Change can meet)
TITLE_STOP = {"the", "a", "an", "of", "and", "in", "on", "to", "for", "why", "how", "what", "is", "are", "it",
              "its", "do", "does"}
SOURCE_KINDS = ["chapter", "seed", "request", "queue", "profile", "library"]


def plan_settings(cfg: dict) -> dict:
    p = (cfg.get("overnight") or {}).get("plan") or {}
    return {"scout_searches": int(p.get("scout_searches", 4)), "max_usd": float(p.get("max_usd", 1.0)),
            "seeds_shown": int(p.get("seeds_shown", 30)), "library_shown": int(p.get("library_shown", 40))}


# --- what the planner reads ---------------------------------------------------------

def _text(p: Path, cap: int = 0) -> str:
    try:
        t = p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return t[:cap].rstrip() + " ..." if cap and len(t) > cap else t


def _first_paragraph(md: str) -> str:
    for para in re.split(r"\n\s*\n", md):
        para = para.strip()
        if para and not para.startswith("#"):
            return " ".join(para.split())
    return ""


def chapter_card(root: Path) -> dict:
    return profiles.read_json(root / "00_source" / "up_next.json") or {}


def chapter_summary(root: Path) -> dict:
    """{folder, title, summary, decided}: the card's when the chapter has one, else its episode title
    and the angle's first paragraph."""
    card = chapter_card(root)
    summary = card.get("summary") or _first_paragraph(_text(root / "03_angle.md"))
    return {"folder": root.name, "title": library.chapter_title(root), "summary": summary,
            "decided": card.get("decided")}


def continuations(past: list[dict]) -> int:
    """How many of the latest chapters in a row continued the one before."""
    n = 0
    for ch in reversed(past):
        if ch.get("decided") != "continue":
            break
        n += 1
    return n


def through_line(root: Path) -> str:
    outline = profiles.read_json(root / "05_outline.json") or {}
    if outline.get("through_line"):
        parts = [outline["through_line"]]
        if outline.get("closing_idea"):
            parts.append(f"It closed on: {outline['closing_idea']}")
        return " ".join(parts)
    brief = _text(root / "05_brief.md")
    return brief.split("\n## Segment")[0].strip()[:1200] if brief else ""


def last_chapter(root: Path) -> dict:
    notes = "\n".join(t for t in (_text(root / "00_source" / "notes.md", 1500),
                                  _text(root / "listener_notes.md", 1500)) if t)
    return {**chapter_summary(root), "through_line": through_line(root), "parked": library.parked(root),
            "notes": notes, "questions": [q["words"] for q in library.aside_questions(root)],
            "saved": library.saved_seeds(root), "words": (chapter_card(root).get("source") or {}).get("words")}


def gather(cfg: dict, listener: Path, request: dict | None) -> dict:
    ps = plan_settings(cfg)
    chapters = profiles.chapters(listener=listener)
    latest = profiles.latest_chapter(listener=listener)
    return {
        "request": request,
        "queued": None if request else next(iter(library.queue(listener)), None),
        "past": [chapter_summary(p) for p in chapters],
        "last": last_chapter(latest) if latest else None,
        "seeds": library.seeds(listener)[-ps["seeds_shown"]:],
        "library": library.works(listener)[-ps["library_shown"]:],
        "profile": _text(listener / "profile.md"),
    }


def render_inputs(ctx: dict) -> str:
    """inputs.md: everything the planner read, as the model sees it."""
    out = [f"Mode: {ctx['mode']}", f"Continuations in a row: {ctx['streak']}", ""]
    if ctx.get("request"):
        q = ctx["request"]
        out += ["## The listener's request (Change by voice)", "", f"Mode: {q.get('mode') or 'topic'}",
                f"Topic: {q.get('topic') or '(none)'}", f"Notes: {q.get('notes') or '(none)'}", ""]
    if ctx.get("queued"):
        q = ctx["queued"]
        out += [f"## A topic the listener queued for the next build ({q['id']})", "", f"Topic: {q.get('topic')}",
                f"Notes: {q.get('notes') or '(none)'}", ""]
    if ctx.get("queued_mode"):
        q = ctx["queued_mode"]
        out += [f"## What the listener asked for the next build, after a cutoff ({q['id']})", "",
                f"Mode: {q.get('mode')}", f"Notes: {q.get('notes') or '(none)'}", ""]
    last = ctx.get("last")
    out += ["## The last chapter", ""]
    if last:
        out += [f"{last['title']} (chapter {last['folder']})", "", f"Summary: {last['summary'] or '(none)'}",
                f"Through-line: {last['through_line'] or '(none)'}"]
        if last.get("words"):
            out.append(f'It came from the listener saying: "{last["words"]}"')
        out += ["", "Parked ideas (research found them, the chapter left them out):"]
        out += [f"- {p}" for p in last["parked"]] or ["- (none)"]
        out += ["", "Questions the listener asked during it, in their words:"]
        out += [f'- "{q}"' for q in last["questions"]] or ["- (none)"]
        out += ["", "Topics saved for later during it:"]
        out += [f"- {s['topic']}" + (f' (they said: "{s["words"]}")' if s.get("words") else "")
                for s in last["saved"]] or ["- (none)"]
        out += ["", f"The listener's notes while it was made: {last['notes'] or '(none)'}", ""]
    else:
        out += ["(no chapter yet: this is the first)", ""]
    out += ["## Seeds: questions and ideas for future chapters, unused", ""]
    out += [f"- [{s['id']}] ({s['kind']}) {s['text']}" + (f' (their words: "{s["words"]}")' if s.get("words") else "")
            for s in ctx["seeds"]] or ["- (none)"]
    out += ["", "## Library: works, people and topics the listener has met", ""]
    out += [f"- [{w['id']}] ({w['kind']}) {w['title']}" + (f", {w['creator']}" if w.get("creator") else "")
            + (f": {w['note']}" if w.get("note") else "") for w in ctx["library"]] or ["- (none)"]
    out += ["", "## The listener's profile (what they have said about themselves)", "", ctx["profile"] or "(none yet)",
            "", "## Every past chapter (never repeat one)", ""]
    out += [f"- {c['title']} ({c['folder']}): {c['summary'] or '(no summary)'}" for c in ctx["past"]] or ["- (none)"]
    return "\n".join(out).strip() + "\n"


def listener_corpus(ctx: dict) -> str:
    """Everything the listener said that the planner saw: the words a card may quote."""
    parts = [ctx.get("profile") or ""]
    for key in ("request", "queued", "queued_mode"):
        if ctx.get(key):
            parts += [ctx[key].get("topic") or "", ctx[key].get("notes") or ""]
    last = ctx.get("last") or {}
    parts += [last.get("notes") or "", last.get("words") or ""] + list(last.get("questions") or [])
    parts += [s.get("words") or "" for s in last.get("saved") or []]
    parts += [s.get("words") or "" for s in ctx["seeds"]]
    return library._norm(" \n ".join(parts))


# --- titles -------------------------------------------------------------------------

def _title_words(t: str) -> set[str]:
    words = [w for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in TITLE_STOP]
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words}


def sentences(text: str) -> int:
    """Sentences in a summary: ends of . ! ? before a capital or the end (decimals like 2.5 don't count)."""
    return len([x for x in re.split(r"(?<=[.!?])\s+(?=[A-Z\"'])", (text or "").strip()) if x.strip()])


def near_duplicate(title: str, past: list[str]) -> str | None:
    """The past title this one repeats (case and punctuation aside, or nearly), or None."""
    a, wa = library._norm(title), _title_words(title)
    for p in past:
        b, wb = library._norm(p), _title_words(p)
        if not b:
            continue
        if a == b or SequenceMatcher(None, a, b).ratio() >= 0.85:
            return p
        if wa and wb and len(wa & wb) / len(wa | wb) >= 0.75:
            return p
    return None


# --- the calls ----------------------------------------------------------------------

def choice_schema(allowed: list[str]) -> dict:
    return {"type": "object", "additionalProperties": False,
            "required": ["decided", "topic", "angle", "why", "source", "scout_queries"],
            "properties": {
                "decided": {"type": "string", "enum": allowed}, "topic": {"type": "string"},
                "angle": {"type": "string"}, "why": {"type": "string"},
                "source": {"type": "object", "additionalProperties": False, "required": ["kind", "ref", "words"],
                           "properties": {"kind": {"type": "string", "enum": SOURCE_KINDS},
                                          "ref": {"type": "string"}, "words": {"type": "string"}}},
                "scout_queries": {"type": "array", "items": {"type": "string"}}}}


CARD_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "overview", "summary", "angle", "research_questions"],
    "properties": {"title": {"type": "string"}, "overview": {"type": "string"}, "summary": {"type": "string"},
                   "angle": {"type": "string"}, "research_questions": {"type": "array", "items": {"type": "string"}}},
}


def allowed_decisions(mode: str, ctx: dict) -> list[str]:
    if ctx.get("request") and mode == "topic":
        return ["topic"]
    if ctx.get("queued"):
        return ["queue"]
    if not ctx.get("last"):
        return ["branch"]
    if mode == "continue":
        return ["continue"]
    return ["branch"] if ctx["streak"] >= STREAK else ["continue", "branch"]


def fix_source(choice: dict, ctx: dict) -> dict:
    """The card's source, by code where code knows it; the listener's words only if they really said them."""
    got = choice.get("source") or {}
    kind, ref, words = got.get("kind"), (got.get("ref") or "").strip(), (got.get("words") or "").strip()
    if choice["decided"] == "topic":
        q = ctx["request"]
        return {"kind": "request", "ref": q.get("session"), "words": q.get("topic") or None}
    if choice["decided"] == "queue":
        q = ctx["queued"]
        return {"kind": "queue", "ref": q["id"], "words": q.get("topic") or None}
    seeds = {s["id"]: s for s in ctx["seeds"]}
    if choice["decided"] == "continue":
        kind, ref = "chapter", ctx["last"]["folder"]
    elif kind == "seed" and ref not in seeds:
        kind, ref = "profile", ""
    elif kind == "library" and ref not in {w["id"] for w in ctx["library"]}:
        kind, ref = "profile", ""
    elif kind == "chapter" and ref not in {c["folder"] for c in ctx["past"]}:
        kind, ref = "profile", ""
    elif kind not in ("seed", "library", "chapter", "profile"):
        kind, ref = "profile", ""
    if words and library._norm(words) not in ctx["corpus"]:
        words = ""
    if not words and kind == "seed":
        words = seeds[ref].get("words") or ""
    if kind == "profile":
        ref = "profile.md" if ctx.get("profile") else None
    return {"kind": kind, "ref": ref or None, "words": words or None}


def scout(r: Run, cfg: dict, choice: dict, minutes: int) -> str:
    n = plan_settings(cfg)["scout_searches"]
    prompt = load_prompt("overnight_plan_scout", topic=choice["topic"], angle=choice["angle"],
                         queries="\n".join(f"- {q}" for q in choice.get("scout_queries") or []) or "- (your own)",
                         searches=n, minutes=minutes)
    res = llm.call(r, cfg, "overnight_plan_scout", role="researcher", label="plan scout",
                   messages=[{"role": "user", "content": prompt}],
                   tools=[{"type": cfg["tools"]["web_search"], "name": "web_search", "max_uses": n}],
                   expected_searches=n, resume_if=lambda u: u.web_searches == 0, allow_empty=True)
    seen = research.retrieved(res.content)["urls"]
    notes = res.text.strip() or "(the scout wrote no notes)"
    if seen:
        notes += "\n\nSources seen:\n" + "\n".join(f"- {t or u}: {u}" for u, t in list(seen.items())[:15])
    return notes


def write_card(r: Run, cfg: dict, ctx: dict, choice: dict, notes: str, minutes: int, retry: str) -> dict:
    last = ctx.get("last") or {}
    prompt = load_prompt(
        "overnight_plan_card", decided=choice["decided"], topic=choice["topic"], angle=choice["angle"],
        why=choice["why"], words=choice["source"].get("words") or "(none)", scout=notes,
        profile=ctx["profile"] or "(none yet)",
        last=f"{last['title']}: {last['summary']}" if last else "(no chapter yet)",
        past="\n".join(f"- {c['title']}" for c in ctx["past"]) or "- (none)",
        notes=((ctx.get("request") or ctx.get("queued") or ctx.get("queued_mode") or {}).get("notes") or "(none)"),
        minutes=minutes, retry=retry)
    res = llm.call(r, cfg, "overnight_plan_card", label="plan card", json_schema=CARD_SCHEMA,
                   messages=[{"role": "user", "content": prompt}])
    card = parse_json(r, res.text, "overnight_plan_card")
    for k in ("title", "overview", "summary", "angle"):
        card[k] = " ".join(str(card.get(k) or "").split())
        if not card[k]:
            raise StageError(f"plan card: the model left out the {k}.")
    card["research_questions"] = [q.strip() for q in card.get("research_questions") or [] if q.strip()][:6]
    if not card["research_questions"]:
        raise StageError("plan card: no research questions.")
    return card


# --- the pass -----------------------------------------------------------------------

def seed_text(card: dict) -> str:
    """00_source/seed.md for the build: the card, and the listener's words."""
    src = card.get("source") or {}
    where = f"{src.get('kind') or 'unknown'}" + (f": {src['ref']}" if src.get("ref") else "")
    req = card.get("request") or {}
    return load_prompt(
        "overnight_seed_file", title=card["title"], overview=card.get("overview") or "", summary=card.get("summary") or "",
        angle=card.get("angle") or "(the summary above)",
        questions="\n".join(f"- {q}" for q in card.get("research_questions") or []) or "- (none)",
        said=src.get("words") or req.get("topic") or "(the listener gave no words for this one; it came from "
                                                      "what the planner knew of them)",
        source=f"{card.get('decided') or card.get('mode')} ({where})",
        notes=card.get("notes") or req.get("notes") or "(nothing)").strip() + "\n"


def _plan_id(listener: Path, now: datetime) -> str:
    base, n = f"plan-{now:%Y%m%d-%H%M%S}", 2
    pid = base
    while (listener / "plans" / pid).exists():
        pid, n = f"{base}-{n}", n + 1
    return pid


def failures(listener: Path) -> list[dict]:
    return profiles.read_jsonl(Path(listener) / FAILURES)


def plain_reason(e: BaseException) -> str:
    """The first sentence of an error, without the stage prefix: for the library page's line."""
    first = (str(e).strip().splitlines() or [type(e).__name__])[0]
    first = re.sub(r"^plan( \w+)?:\s*", "", re.split(r"(?<=\.)\s", first)[0])
    return (first[:157] + "...") if len(first) > 160 else first


def _record_failure(listener: Path, now: datetime, e: BaseException, info: dict) -> None:
    rec = {"t": now.isoformat(timespec="seconds"), "plan": info.get("plan"), "queued": info.get("queued"),
           "error": f"{type(e).__name__}: {e}", "reason": plain_reason(e)}
    profiles.append_jsonl(listener / FAILURES, rec)
    qid = info.get("queued")
    if qid and sum(1 for f in failures(listener) if f.get("queued") == qid) >= QUEUE_TRIES:
        library.mark_used(listener, [qid], f"set aside: {QUEUE_TRIES} planning passes failed with it", library.QUEUE)
        print(f"  queued topic {qid} set aside after {QUEUE_TRIES} failed planning passes")


def plan(cfg: dict, listener: Path, *, request: dict | None = None, now: datetime | None = None) -> dict:
    """The planning pass. `request` is {mode, topic, notes, session} from Change by voice. Returns the
    card written to up_next.json; or, with a request that could no longer change tonight's card, the
    card as it is with "queued": True (the request went to queue.jsonl)."""
    listener, now, info = Path(listener), now or datetime.now(), {}
    with LOCK:
        try:
            return _plan(cfg, listener, request, now, info)
        except Exception as e:
            if request is None:
                _record_failure(listener, now, e, info)
            raise


def _queued(listener: Path, request: dict, current: dict | None, why: str) -> dict:
    q = library.queue_add(listener, request)
    print(f"  {why} The request went to the queue ({q['id']}).")
    return {**(current or {}), "queued": True, "queue_id": q["id"], "note": why}


def _plan(cfg: dict, listener: Path, request: dict | None, now: datetime, info: dict) -> dict:
    from . import runner
    t0 = _monotonic()
    settings = profiles.settings(cfg, listener=listener)
    current = runner.reconcile(listener)  # a dead build's card is failed, not building
    if request:
        ok, why = schedule.can_change(current, settings, now)
        if not ok:
            return _queued(listener, request, current, why)
    seen = ((current or {}).get("id"), (current or {}).get("status"))
    latest = profiles.latest_chapter(listener=listener)
    if latest is not None:  # its asides happened after its build: bring its questions and saved topics in
        try:
            library.after_chapter(cfg, listener, latest)
        except Exception as e:  # the pass can plan without them; they stay in the chapter's 12_asks/
            print(f"  library: {latest.name} not brought up to date ({type(e).__name__}: {e})")
    ps = plan_settings(cfg)
    minutes = int(settings["chapter"].get("minutes", 20))
    ctx = gather(cfg, listener, request)
    queued_mode = None  # a "surprise me" or "continue" queued after a cutoff: it sets the mode, no topic
    q = ctx["queued"]
    if q and (q.get("mode") not in (None, "topic") or not (q.get("topic") or "").strip()):
        queued_mode, ctx["queued"] = q, None
        ctx["queued_mode"] = q
    if request:
        mode = request.get("mode") or "topic"
        if mode == "topic" and not (request.get("topic") or "").strip():
            mode = "surprise"  # "surprise me" said with no topic
    elif ctx["queued"]:
        mode = "topic"
    elif queued_mode:
        mode = queued_mode["mode"] if queued_mode.get("mode") in ("surprise", "continue") else "surprise"
    else:
        mode = settings["chapter"]["mode"]
    ctx.update(mode=mode, streak=continuations(ctx["past"]), corpus="")
    ctx["corpus"] = listener_corpus(ctx)
    allowed = allowed_decisions(mode, ctx)
    info["queued"] = (ctx["queued"] or queued_mode or {}).get("id")

    pid = _plan_id(listener, now)
    info["plan"] = pid
    pdir = listener / "plans" / pid
    pdir.mkdir(parents=True)
    r = Run(name=pid, root=pdir, unattended=True, max_usd=ps["max_usd"])
    inputs = render_inputs(ctx)
    (pdir / "inputs.md").write_text(f"# What the planner read ({pid})\n\n{inputs}", encoding="utf-8")

    res = llm.call(r, cfg, "overnight_plan_choose", label="plan choose", json_schema=choice_schema(allowed),
                   messages=[{"role": "user", "content": load_prompt(
                       "overnight_plan_choose", inputs=inputs, mode=mode, allowed=", ".join(allowed),
                       streak=STREAK, minutes=minutes)}])
    choice = parse_json(r, res.text, "overnight_plan_choose")
    if choice.get("decided") not in allowed:
        choice["decided"] = allowed[-1]
    choice["topic"] = (choice.get("topic") or "").strip() or ((request or ctx["queued"] or {}).get("topic") or "")
    choice["angle"], choice["why"] = (choice.get("angle") or "").strip(), (choice.get("why") or "").strip()
    if not choice["topic"]:
        raise StageError("plan choose: no topic came back.")
    choice["source"] = fix_source(choice, ctx)
    profiles.write_json(pdir / "choice.json", {**choice, "mode": mode, "allowed": allowed, "streak": ctx["streak"]})

    notes = scout(r, cfg, choice, minutes)
    (pdir / "scout.md").write_text(f"# Scout notes: {choice['topic']}\n\n{notes}\n", encoding="utf-8")

    past_titles = [c["title"] for c in ctx["past"]]
    card = write_card(r, cfg, ctx, choice, notes, minutes, retry="")
    tries = [card["title"]]
    clash = near_duplicate(card["title"], past_titles)
    if clash:
        card = write_card(r, cfg, ctx, choice, notes, minutes,
                          retry=load_prompt("overnight_plan_card_retry", title=card["title"], clash=clash))
        tries.append(card["title"])
        clash = near_duplicate(card["title"], past_titles)
    if not clash and sentences(card["summary"]) > 4:  # the spec: three or four sentences
        card = write_card(r, cfg, ctx, choice, notes, minutes,
                          retry=load_prompt("overnight_plan_card_long", n=str(sentences(card["summary"]))))
        tries.append(card["title"])
        clash = near_duplicate(card["title"], past_titles)
    cost = round(r.spent(), 4)
    profiles.write_json(pdir / "card.json", {**card, "titles_tried": tries, "repeats": clash, "cost_usd": cost})
    if clash:
        raise StageError(f"plan: the title {card['title']!r} repeats the past chapter {clash!r}, after a retry. "
                         f"The card was not changed; see {pdir}.")

    queued = ctx["queued"] if choice["decided"] == "queue" else queued_mode
    with locks.card_lock(listener):  # the runner moves the card to building under the same lock
        at = now + timedelta(seconds=_monotonic() - t0)  # the pass took a minute: the cutoff may have come
        current = runner.reconcile(listener)
        if request:
            ok, why = schedule.can_change(current, settings, at)
            if not ok:
                profiles.write_json(pdir / "outcome.json", {"written": False, "queued": True, "why": why})
                return _queued(listener, request, current, why)
        elif ((current or {}).get("id"), (current or {}).get("status")) != seen:
            why = "The card changed while this pass ran (a Change, or the build started); this pass was dropped."
            profiles.write_json(pdir / "outcome.json", {"written": False, "why": why})
            print(f"  {why}")
            return current
        nb = schedule.next_build(settings, at) or schedule.next_start(settings, at)
        up = {"id": pid, "for_morning": nb.date().isoformat() if nb else None,
              "title": card["title"], "overview": card["overview"], "summary": card["summary"],
              "mode": mode, "decided": choice["decided"], "source": choice["source"],
              "research_questions": card["research_questions"], "angle": card["angle"],
              "request": request or None, "notes": ((request or queued or {}).get("notes") or None),
              "status": "planned", "planned_at": now.isoformat(timespec="seconds"), "locked_at": None,
              "chapter": None, "error": None, "plan_dir": f"plans/{pid}"}
        profiles.write_json(listener / "up_next.json", up)
        if queued:
            library.mark_used(listener, [queued["id"]], pid, library.QUEUE)
        if choice["source"]["kind"] == "seed":
            library.mark_used(listener, [choice["source"]["ref"]], pid)
    print(f"  planned {up['title']!r} for {up['for_morning']} ({mode}, {choice['decided']}), ${cost:.3f}")
    return up
