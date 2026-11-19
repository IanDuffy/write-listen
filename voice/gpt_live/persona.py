"""The live persona check for the GPT-Live guide (spec "One Live Voice", check 14), and scripted
questions mid-chapter.

- `converse`: Claude plays a listener (prompts/live_listener.md, a persona from
  prompts/live_personas.md) and talks by voice to the real guide through sim.Sim, until start_chapter
  has been called and a couple of waiting exchanges have happened, the guide steps back, or the turns
  run out. Each conversation is saved to voice/gpt_live/_runs/<stamp>/<persona>.json (with the
  guide's audio as .guide.wav and every event as .events.jsonl).
- `grade`: a separate Claude pass with a fresh context (prompts/live_grade.md) grades one transcript
  against the guide's own instruction parts, per rule, quoting every miss; code then checks each
  quote is in the transcript. Saved beside it as <persona>.grade.json.
- `aside`: a scripted question mid-chapter: the guide reads two paragraphs of a real chapter as the
  page feeds them (commentary, mic muted), the listener taps (unmute, the "tapped" note, the silent
  context) and speaks the scenario's lines; code checks when resume_chapter came, and
  prompts/live_grade_aside.md grades the rest.

Every paid call is logged to build_costs.jsonl and checked against live.build_cap_usd (sim.py).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

from stages import llm
from stages.common import Run, StageError, load_prompt, parse_json
from stages.guide import NO_EARLIER, day_part

from . import chapter as live_chapter
from . import session as live
from . import sim as S

SILENT, LEAVE = "[SILENT]", "[LEAVE]"
GUIDE_WAIT_SECS = 20  # the guide has said nothing for this long: the listener is asked what they'd do
LISTENER_USD = 0.03  # an upper estimate for one listener reply (Sonnet, effort low)
GRADE_USD = 0.15  # and for one grade (Sonnet, effort medium: config live.checks.grade_role)
ONBOARDING_PARTS = ("00_system.md", "10_talk.md", "20_meeting.md", "30_pitch.md", "80_policies.md")
ASIDE_PARTS = ("40_reading.md", "50_asides.md", "80_policies.md")

_MISSES = {"type": "array", "items": {
    "type": "object", "additionalProperties": False, "required": ["quote", "why"],
    "properties": {"quote": {"type": "string"}, "why": {"type": "string"}}}}
GRADE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["rules", "other_issues", "score", "summary"],
    "properties": {
        "rules": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "verdict", "misses"],
            "properties": {"id": {"type": "string"},
                           "verdict": {"type": "string", "enum": ["pass", "fail", "not_tested"]},
                           "misses": _MISSES}}},
        "other_issues": _MISSES,
        "score": {"type": "integer"},
        "summary": {"type": "string"},
    },
}


# --- personas -----------------------------------------------------------------------------------

def personas() -> dict[str, dict]:
    """prompts/live_personas.md: {name: {"name", "now", "text"}} in file order."""
    out = {}
    for block in re.split(r"^## +", load_prompt("live_personas"), flags=re.M)[1:]:
        name, _, rest = block.partition("\n")
        now = re.search(r"^now:\s*(.+)$", rest, re.M)
        text = re.sub(r"^now:.*\n?", "", rest, flags=re.M).strip()
        out[name.strip()] = {"name": name.strip(), "now": now.group(1).strip() if now else day_part(), "text": text}
    return out


def first_meeting_values(now: str, minutes: str = "4") -> dict:
    """The instruction parts' values for a listener the guide has never met (nothing is read from
    listener/: the check must not depend on, or touch, Ian's own memory files)."""
    return {"identity": "(none: this is your first meeting)", "names": ", ".join(live.LIVE_NAMES[:5]),
            "profile": "(no profile yet: this is a first meeting)",
            "history": "(none yet)", "minutes": minutes, "now": now, "earlier": NO_EARLIER}


def claude_run() -> Run:
    root = S.RUNS / "_claude"
    root.mkdir(parents=True, exist_ok=True)
    return Run(name="claude", root=root)


def claude(cfg: dict, log_run: Run, label: str, prompt: str, *, role: str, effort: str, estimate: float,
           schema: dict | None = None, max_tokens: int = 4000) -> llm.Result:
    S.check_cap(cfg, estimate, f"Claude {label}")
    res = llm.call(log_run, cfg, label.split()[0], messages=[{"role": "user", "content": prompt}], role=role,
                   effort=effort, json_schema=schema, label=label, preapproved=True, max_tokens=max_tokens)
    S.log_cost(f"claude {label}", res.usage.cost_usd)
    return res


# --- the conversation -----------------------------------------------------------------------------

def _cost_lines() -> int:
    return len(S.COSTS.read_text(encoding="utf-8").splitlines()) if S.COSTS.exists() else 0


def _listener_usd(after_line: int, name: str) -> float:
    """The simulated listener's cost (Claude replies and TTS) logged since `after_line`: other live checks
    may be writing to the same file meanwhile, so their lines are left out by name."""
    lines = S.COSTS.read_text(encoding="utf-8").splitlines()[after_line:] if S.COSTS.exists() else []
    recs = [json.loads(l) for l in lines if l.strip()]
    return round(sum(r.get("usd") or 0 for r in recs
                     if r.get("what") in (f"claude live_listener {name}", "tts listener line")), 4)


def heard_for(timeline: list[dict], heard: list[tuple[float, str]]) -> None:
    """Attach to each listener line what GPT-Live transcribed of it (input deltas until the next one)."""
    said = [x for x in timeline if x["who"] == "listener" and x.get("text")]
    for i, x in enumerate(said):
        nxt = said[i + 1]["t"] if i + 1 < len(said) else float("inf")
        x["heard"] = "".join(d for t, d in heard if x["t"] <= t < nxt).strip()


def split_turns(timeline: list[dict]) -> list[dict]:
    """The timeline in time order, with a guide turn cut where a tool call, backend reply or app note lands in
    the middle of it, so the transcript shows what was said before the tool and what after."""
    cuts = sorted(x["t"] for x in timeline if x["who"] in ("tool", "backend", "app"))
    out = []
    for x in timeline:
        deltas = x.get("deltas")
        if x["who"] != "guide" or not deltas:
            out.append({k: v for k, v in x.items() if k != "deltas"})
            continue
        inside = [c for c in cuts if deltas[0][0] < c <= deltas[-1][0]]
        groups, cur = [], []
        for t, d in deltas:
            if inside and t >= inside[0] and cur:
                groups.append(cur)
                cur = []
            while inside and t >= inside[0]:
                inside.pop(0)
            cur.append((t, d))
        groups.append(cur)
        for g in groups:
            text = "".join(d for _, d in g).strip()
            if text:
                out.append({"t": g[0][0], "who": "guide", "text": text})
    return sorted(out, key=lambda r: r["t"])


def for_listener(timeline: list[dict]) -> str:
    lines = []
    for x in sorted(timeline, key=lambda r: r["t"]):
        if x["who"] == "guide":
            lines.append(f"Guide: {x['text']}")
        elif x["who"] == "listener":
            lines.append(f"You: {x['text']}" if x.get("text") else "(you said nothing)")
        elif x["who"] == "quiet":
            lines.append(f"(the guide said nothing for {x['secs']} seconds)")
    return "\n".join(lines) or "(nothing yet: the guide hasn't spoken)"


def listener_reply(cfg: dict, log_run: Run, persona: dict, timeline: list[dict], moment: str) -> str:
    prompt = load_prompt("live_listener", persona=persona["text"], conversation=for_listener(timeline), moment=moment)
    res = claude(cfg, log_run, f"live_listener {persona['name']}", prompt, role="guide", effort="low",
                 estimate=LISTENER_USD, max_tokens=3000)
    text = res.text.strip().strip('"').strip()
    if SILENT in text or not text:
        return SILENT
    if LEAVE in text:
        return LEAVE
    return text


PAUSE = re.compile(r"\s*(?:\.\.\.|…)\s*")  # a listener's line with "..." in it is spoken in pieces, a pause at each
PAUSE_SECS = 2.5


def explained_at(timeline: list[dict]) -> float | None:
    """When the guide first said what it does (session.EXPLAINED, as the page's L.explainedAt)."""
    return next((x["t"] for x in timeline if x["who"] == "guide" and live.EXPLAINED.search(x.get("text") or "")), None)


async def converse(cfg: dict, persona: dict, out_dir: Path, *, max_turns: int = 12, after_start: int = 2,
                   max_minutes: float = 6, log_run: Run | None = None, reply_fn=None, sim_kwargs: dict | None = None,
                   handoff: str | None = None, pause_secs: float = PAUSE_SECS) -> dict:
    """One onboarding conversation by voice; returns (and saves) its transcript. `handoff`: the name a
    tester gave the interviewer: the conversation opens with the page's `handoff` note (the guide greets
    them by name) instead of the hello, as in the Tester Demo. A listener's line with "..." in it is
    spoken in pieces with `pause_secs` of silence at each (someone who talks in fragments: does the guide
    wait, or cut in?)."""
    log_run = log_run or claude_run()
    name = persona["name"]
    values = first_meeting_values(persona["now"])
    reply_fn = reply_fn or (lambda timeline, moment: listener_reply(cfg, log_run, persona, timeline, moment))
    notes = live.notes()
    kw = dict(sim_kwargs or {})
    if handoff:
        kw["greet"] = False
    sim = S.Sim(cfg, live.session_config(cfg, values, transport="websocket", greet=not handoff), label=f"persona {name}",
                tool_handler=S.FakeTools(), max_minutes=max_minutes, audio_out=out_dir / f"{name}.guide.wav",
                events_path=out_dir / f"{name}.events.jsonl", **kw)
    before = _cost_lines()
    await sim.connect()
    if handoff:
        await sim.note("commentary", notes["handoff"].replace("{name}", handoff))
    since, said, silent, stop = 0, 0, 0, "turns"
    page = cfg["live"]["page"]
    nudges, nudged = page.get("pitch_nudge_secs") or [], 0
    offer, offered, hold = page.get("offer_nudge") or {}, False, page.get("pitch_hold") or {}
    after_noted = False
    try:
        while True:
            new = await sim.wait_guide(since, GUIDE_WAIT_SECS)
            since = len(sim.turns)
            if not after_noted and any(c["name"] == "start_chapter" for c in sim.calls):
                # The page's note once the chapter starts (live.js afterStart): step back, or keep them company
                # if they've been chatting (engaged_lines lines, the last four averaging engaged_words words).
                after_noted = True
                lines = [x["text"] for x in sim.timeline if x["who"] == "listener" and x.get("text")]
                recent = [len(x.split()) for x in lines[-4:]]
                P = cfg["live"]["page"]
                engaged = len(lines) >= P["engaged_lines"] and recent and sum(recent) / len(recent) >= P["engaged_words"]
                key = "started_company" if engaged else "started_step_back"
                await sim.note("instructions", notes[key].replace("{when}", "about 4 minutes"))
                new = await sim.wait_guide(since, GUIDE_WAIT_SECS) or new
                since = len(sim.turns)
            if sim.closed.is_set():
                stop = f"closed: {sim.closed_reason}"
                break
            if sim.now() > max_minutes * 60 - 30:
                stop = "time"
                break
            stepped = any(c["name"] == "step_back" for c in sim.calls)
            if not new:
                sim.timeline.append({"t": sim.now(), "who": "quiet", "secs": GUIDE_WAIT_SECS})
                if stepped:
                    stop = "stepped back"
                    break
            moment = ("The guide has just finished speaking." if new else
                      f"The guide has said nothing for {GUIDE_WAIT_SECS} seconds.")
            reply = await asyncio.to_thread(reply_fn, sim.timeline, moment)
            if sim.speaking or len(sim.turns) > since:  # it spoke again while the listener was thinking
                await sim.wait_guide(since, GUIDE_WAIT_SECS)
                since = len(sim.turns)
                reply = await asyncio.to_thread(reply_fn, sim.timeline, "The guide has just finished speaking.")
            if reply == LEAVE:
                stop = "listener left"
                break
            if reply == SILENT:
                sim.timeline.append({"t": sim.now(), "who": "listener", "text": None})
                silent += 1
                if silent >= 2 and not new:
                    stop = "both quiet"
                    break
                continue
            silent = 0
            # The page's clock (live.js conversationCues), the notes sent as the listener speaks. A guide that
            # hasn't said what it does by offer_nudge.max_secs is cued to offer it (the page also cues it at
            # the first lull from after_secs: here, when the guide has just let a silence run); a conversation
            # running long with no chapter is nudged toward one, not within pitch_hold.explained_secs of the
            # guide's explanation.
            no_chapter = not any(c["name"] == "start_chapter" for c in sim.calls)
            told = explained_at(sim.timeline + ([{"t": sim.now(), "who": "guide", "text": sim.spoken_since(0)}]))
            if (offer and not offered and told is None and no_chapter and said >= 1
                    and sim.now() > (offer["after_secs"] if not new else offer["max_secs"])
                    and not (offer.get("offered") and sim.turns
                             and re.search(offer["offered"], sim.turns[-1]["text"], re.I))):
                await sim.note("instructions", notes["offer_nudge"])
                offered = True
            elif (nudged < len(nudges) and sim.now() > nudges[nudged] and no_chapter
                    and not (told is not None and sim.now() - told < (hold.get("explained_secs") or 0))):
                await sim.note("instructions", notes["pitch_nudge" if nudged == 0 else "pitch_nudge_again"])
                nudged += 1
            pieces = [x for x in PAUSE.split(reply) if x.strip()] if pause_secs else [reply]
            for i, piece in enumerate(pieces or [reply]):
                if i:
                    await asyncio.sleep(pause_secs)
                await sim.say(piece)
            said += 1
            start = next((c for c in sim.calls if c["name"] == "start_chapter"), None)
            after = sum(1 for x in sim.timeline if x["who"] == "listener" and x.get("text") and start
                        and x["t"] > start["t"])
            if said >= max_turns or (start and after >= after_start):
                stop = "turns" if said >= max_turns else "chapter started"
                await sim.wait_guide(since, GUIDE_WAIT_SECS)
                break
    finally:
        cost = await sim.close()
    heard_for(sim.timeline, sim.heard)
    transcript = {
        "kind": "onboarding", "persona": name, "now": persona["now"], "description": persona["text"],
        "values": values, "session_id": sim.session_id, "stop": stop,
        "timeline": split_turns(sim.timeline), "calls": sim.calls, "errors": sim.errors,
        "cost": cost, "listener_usd": _listener_usd(before, name),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.json").write_text(json.dumps(transcript, indent=1, ensure_ascii=False), encoding="utf-8")
    return transcript


# --- grading --------------------------------------------------------------------------------------

def rules_text(cfg: dict, values: dict, parts=ONBOARDING_PARTS) -> str:
    v = live.values_for(cfg, values)
    return "\n\n".join(live.fill(live._body((live.INSTRUCTIONS_DIR / p).read_text(encoding="utf-8")), v, p)
                       for p in parts)


def format_transcript(transcript: dict) -> str:
    lines = []
    for x in transcript["timeline"]:
        t = f"[{x['t']:.1f}]"
        who = x["who"]
        if who == "guide":
            lines.append(f"{t} guide: {x['text']}")
        elif who == "listener":
            if not x.get("text"):
                lines.append(f"{t} listener: (says nothing)")
                continue
            heard = x.get("heard")
            extra = f"   (GPT-Live heard: {heard})" if heard and _norm(heard) != _norm(x["text"]) else ""
            lines.append(f"{t} listener: {x['text']}{extra}")
        elif who == "quiet":
            lines.append(f"{t} (the guide said nothing for {x['secs']} s)")
        elif who == "tool":
            lines.append(f"{t} tool {x['name']} {json.dumps(x['args'], ensure_ascii=False)} -> "
                         f"{json.dumps(x['result'], ensure_ascii=False)[:400]}")
        elif who == "backend":
            lines.append(f"{t} backend: {x['text']}")
        elif who == "app":
            lines.append(f"{t} app {x['kind']}" + (f": {x['text']}" if x.get("text") else ""))
        elif who == "error":
            lines.append(f"{t} error {x.get('code')}: {x.get('message')}")
    return "\n".join(lines)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower().replace("’", "'")).split())


def check_quotes(grade: dict, transcript_text: str) -> int:
    """Mark each quoted miss found or not in the transcript; return how many were not found."""
    hay = _norm(transcript_text)
    missing = 0
    for miss in [m for r in grade.get("rules", []) for m in r.get("misses", [])] + grade.get("other_issues", []):
        miss["found"] = _norm(miss.get("quote", "")) in hay
        missing += not miss["found"]
    return missing


def grade(cfg: dict, path: Path, log_run: Run | None = None) -> dict:
    """Grade one saved transcript (<name>.json); writes <name>.grade.json beside it."""
    log_run = log_run or claude_run()
    transcript = json.loads(path.read_text(encoding="utf-8"))
    text = format_transcript(transcript)
    if transcript.get("kind") == "aside":
        prompt = load_prompt("live_grade_aside", rules=rules_text(cfg, transcript["values"], ASIDE_PARTS),
                             scenario=transcript["scenario"], context=transcript["context"], transcript=text)
    else:
        prompt = load_prompt("live_grade", rules=rules_text(cfg, transcript["values"]),
                             persona=transcript["description"], transcript=text)
    checks = cfg["live"].get("checks") or {}
    res = claude(cfg, log_run, f"live_grade {path.stem}", prompt, role=checks.get("grade_role", "researcher"),
                 effort="medium", estimate=GRADE_USD, schema=GRADE_SCHEMA, max_tokens=32000)
    data = parse_json(log_run, res.text, "live_grade")
    data["quotes_not_found"] = check_quotes(data, text)
    data["grade_usd"] = round(res.usage.cost_usd, 4)
    if transcript.get("checks"):
        data["checks"] = transcript["checks"]
    path.with_name(f"{path.stem}.grade.json").write_text(json.dumps(data, indent=1, ensure_ascii=False),
                                                          encoding="utf-8")
    return data


def pace(transcript: dict) -> dict:
    """The pace Ian asked for (2026-11-18): when the guide said what it does (seconds in), when it pitched
    (its last question before start_chapter) and started the chapter, and how many of its turns up to the
    start asked something (backchannels like "Mm." are not counted as turns)."""
    tl = transcript["timeline"]
    start = next((x["t"] for x in tl if x["who"] == "tool" and x.get("name") == "start_chapter"), None)
    turns = [x for x in tl if x["who"] == "guide" and (start is None or x["t"] <= start)
             and len(re.findall(r"[A-Za-z']+", x["text"])) > 2]
    asked = [x for x in turns if "?" in x["text"]]
    return {"explained": explained_at(tl), "pitched": asked[-1]["t"] if asked and start is not None else None,
            "started": start, "turns": len(turns), "questions": len(asked)}


def summary(name: str, transcript: dict, g: dict | None) -> str:
    c = transcript["cost"]
    lines = [f"{name}: stop={transcript['stop']}, {c['seconds']:.0f} s connected, GPT-Live ${c['usd']:.3f}"
             f" (backend {c['backend']['input']} in / {c['backend']['output']} out), "
             f"tools: {', '.join(x['name'] for x in transcript['calls']) or 'none'}"]
    k = pace(transcript)
    at = lambda v: "never" if v is None else f"{int(v) // 60}:{int(v) % 60:02d}"  # noqa: E731
    lines.append(f"  said what it does at {at(k['explained'])}, pitched at {at(k['pitched'])}, started at "
                 f"{at(k['started'])}; {k['questions']} of its {k['turns']} turns up to then asked something")
    if g:
        failed = [r for r in g["rules"] if r["verdict"] == "fail"]
        lines.append(f"  score {g['score']}/10; failed: {', '.join(r['id'] for r in failed) or 'none'}; "
                     f"quotes not found: {g['quotes_not_found']}")
        for r in failed:
            for m in r["misses"][:2]:
                lines.append(f"    {r['id']}: \"{m['quote']}\" ({m['why']})")
        for m in g.get("other_issues", [])[:4]:
            lines.append(f"    other: \"{m['quote']}\" ({m['why']})")
        lines.append(f"  {g['summary']}")
    return "\n".join(lines)


# --- questions mid-chapter (asides) -------------------------------------------------------------------

# (name, the listener's lines after the tap, what should happen: resume_chapter after this many
# lines, its reason or rewind; the grade judges the rest)
ASIDES = [
    ("closing", ["Wait, what does she mean by a parameter?", "Oh, okay. Got it."],
     {"resume_after": 2, "reason": "closing"}),
    ("follow_up", ["Hang on, what was GATT?", "And was Meadows against trade, then?", "Huh. Okay, go on."],
     {"resume_after": 3, "reason": "listener"}),
    ("never_mind", ["Wait, what was... oh, never mind."], {"resume_after": 1, "reason": "never_mind"}),
    ("spoiler", ["So what's at the top of her list? What's the strongest one?", "Ah, okay."],
     {"resume_after": 2, "no_spoiler": True}),
    ("lost", ["Sorry, I've completely lost track. What's this about?", "Okay, I think I've got it."],
     {"resume_after": 2, "rewind": "paragraph"}),
]


def load_chapter(run_dir: Path) -> tuple[dict, dict]:
    """A fast run's locked sections as the page reads them, read-only: (chapter, outline)."""
    outline = json.loads((run_dir / "05_outline.json").read_text(encoding="utf-8"))
    secs = []
    for s in outline["sections"]:
        f = run_dir / "06_segments" / f"seg{s['n']:02d}.md"
        text = f.read_text(encoding="utf-8") if f.exists() else None
        secs.append({"n": s["n"], "title": s.get("title", ""), "locked": text is not None,
                     "paragraphs": live_chapter.clean_paragraphs(text, s["n"]) if text else []})
    return {"run": run_dir.name, "title": outline.get("title", ""), "sections": secs}, outline


def _sentence_at(p: dict, words_in: int) -> int:
    n = 0
    for i, s in enumerate(p["sentences"]):
        n += len(s.split())
        if words_in < n:
            return i
    return len(p["sentences"]) - 1


async def aside(cfg: dict, run_dir: Path, scenario: tuple, out_dir: Path, *, tap_words: int = 12,
                max_minutes: float = 4, sim_kwargs: dict | None = None) -> dict:
    """One scripted question mid-chapter; returns (and saves) its transcript with the code checks."""
    name, lines, expect = scenario
    chap, outline = load_chapter(run_dir)
    paras = chap["sections"][0]["paragraphs"]
    p1, p2 = paras[0], paras[1]
    notes = live.notes()
    values = first_meeting_values(day_part())
    sim = S.Sim(cfg, live.session_config(cfg, values, transport="websocket"), label=f"aside {name}",
                tool_handler=S.FakeTools(), max_minutes=max_minutes, audio_out=out_dir / f"aside-{name}.guide.wav",
                events_path=out_dir / f"aside-{name}.events.jsonl", **(sim_kwargs or {}))
    await sim.connect()
    context = ""
    try:
        await sim.wait_guide(0, 15)
        await sim.say("Hi. I'm Sam. I'm just here for my chapter.")
        await sim.wait_guide(len(sim.turns), 12)
        await sim.note("commentary", notes["opening_words"].replace("{words}", notes["offer_line"].replace("{title}", chap["title"])))
        await sim.wait_guide(len(sim.turns), 12)
        await sim.mute()
        fed = sim.now()
        await sim.note("commentary", f"{notes['read']} {p1['text']}")
        await sim.note("commentary", f"{notes['read']} {p2['text']}")
        target = len(p1["text"].split()) + tap_words
        while len(sim.spoken_since(fed).split()) < target and sim.now() - fed < 120:
            await asyncio.sleep(0.1)
        tap = sim.now()
        sentence = _sentence_at(p2, len(sim.spoken_since(fed).split()) - len(p1["text"].split()))
        context = live_chapter.context(chap, outline, p2["id"], sentence)
        await sim.mute(False)
        await sim.note("instructions", notes["tapped"])
        await sim.wait_guide(len(sim.turns), 4)
        for k, line in enumerate(lines):
            if any(c["name"] == "resume_chapter" for c in sim.calls):
                break
            await sim.say(line)
            if k == 0:  # as the page does: the silent context once the listener has started to ask
                await sim.note("thinking", f"{notes['context']}\n{context}")
            await sim.wait_guide(len(sim.turns), 12)
        quiet = cfg["live"]["page"]["silence_after_answer_secs"]
        if not any(c["name"] == "resume_chapter" for c in sim.calls):
            await asyncio.sleep(quiet)
            if not any(c["name"] == "resume_chapter" for c in sim.calls):
                await sim.note("instructions", notes["silence"])
                await sim.wait_guide(len(sim.turns), 12)
        call = next((c for c in sim.calls if c["name"] == "resume_chapter"), None)
        if call:  # the page: the lead-in is said, then the reading picks up from the restart point
            await sim.wait_guide(len(sim.turns), 8)
            at = live_chapter.restart(chap, p2["id"], sentence, sim.now() - tap, cfg["live"],
                                      call["args"].get("rewind"))
            hit = live_chapter.find(chap, at["id"])
            await sim.mute()
            await sim.note("commentary", f"{notes['read']} {live_chapter.text_from(hit[2], at['sentence'])}")
            await asyncio.sleep(6)
    finally:
        cost = await sim.close()
    heard_for(sim.timeline, sim.heard)
    call = next((c for c in sim.calls if c["name"] == "resume_chapter"), None)
    resume_after = (sum(1 for x in sim.timeline if x["who"] == "listener" and x.get("text") and x["t"] > tap
                        and x["t"] < call["t"]) if call else None)
    checks = {"resume_called": call is not None, "resume_after": resume_after,
              "expected_after": expect["resume_after"], "reason": call["args"].get("reason") if call else None,
              "rewind": call["args"].get("rewind") if call else None,
              "ok": bool(call) and resume_after == expect["resume_after"]
              and (not expect.get("reason") or call["args"].get("reason") == expect["reason"])
              and (not expect.get("rewind") or call["args"].get("rewind") == expect["rewind"])}
    scenario_text = (f"After the tap the listener says, one line at a time, waiting for the guide: "
                     + " / ".join(f'"{x}"' for x in lines)
                     + f". Expected: resume_chapter after line {expect['resume_after']}"
                     + (f", reason {expect['reason']}" if expect.get("reason") else "")
                     + (f", rewind {expect['rewind']}" if expect.get("rewind") else "")
                     + (", and no spoiler of what is coming" if expect.get("no_spoiler") else "") + ".")
    transcript = {"kind": "aside", "persona": f"aside-{name}", "scenario": scenario_text, "context": context,
                  "values": values, "session_id": sim.session_id, "stop": "resumed" if call else "no resume",
                  "timeline": split_turns(sim.timeline), "calls": sim.calls, "errors": sim.errors,
                  "checks": checks, "cost": cost}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"aside-{name}.json").write_text(json.dumps(transcript, indent=1, ensure_ascii=False), encoding="utf-8")
    return transcript


def new_out_dir() -> Path:
    return S.RUNS / time.strftime("%Y%m%d-%H%M%S")
