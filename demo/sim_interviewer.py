"""Simulated checks of the interviewer (docs/tester-demo.md, acceptance checks 4 and 8), on the real GPT-Live.

    python -m demo.sim_interviewer intro --runs 10 [--grade]     check 4: the intro greets them by the name given,
                                                                 says Ian built it and how this will go, and
                                                                 never what it is, including when the tester asks
    python -m demo.sim_interviewer debrief --runs 10 [--grade]   check 8: a conversation (it replies, answers
                                                                 direct questions, never goes silent) that
                                                                 brings up Ian's topics, their real moment, the
                                                                 closing question, thanks by name
    python -m demo.sim_interviewer grade <run.json>              grade one saved run again
    --persona <key> (repeatable) runs only those personas, e.g. --persona fragments --persona asksback

Claude plays a tester (prompts/interviewer_tester.md, a persona from prompts/interviewer_testers.md, cycled to
--runs) and talks by voice to the interviewer's real session through voice/gpt_live/sim.Sim (the session the
page opens: demo/interviewer.session_config, over the WebSocket). The page's side is played too: the opening
note as speakable context, the `waiting` note when the tester has finished and the voice hasn't replied, the
handover the page acts on when the voice says it without the tool, and the debrief's time note (at checks.time_note_minutes: a compressed clock, since one check is capped at
live.checks.run_cap_usd). Code checks every run (`checks`); `--grade` adds a fresh-context judge
(prompts/interviewer_grade.md, live.checks.grade_role) whose quotes code checks against the transcript.

Costs go to demo/build_costs.jsonl, capped by live.build_cap_usd (Sim refuses a session past it) and each
session by live.checks.run_cap_usd. Runs are saved to voice/gpt_live/_runs/interviewer/<stamp>/ (gitignored).
Never run by the tests (tests/demo/test_sim_interviewer.py plays a scripted session).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

from stages.common import Run, StageError, load_config, load_prompt, parse_json
from voice.gpt_live import persona as P
from voice.gpt_live import sim as S

from . import interviewer as IV

HERE = Path(__file__).parent
COSTS = HERE / "build_costs.jsonl"
RUNS = S.RUNS / "interviewer"
SILENT, LEAVE = "[SILENT]", "[LEAVE]"
WAIT_SECS = 20         # the interviewer has said nothing this long: the tester is asked what they'd do
TESTER_USD = 0.03      # an upper estimate for one tester line (Sonnet, effort low)
GRADE_USD = 0.15
# Words the intro must never say (check 4): what the product is or does. It may say Ian built it. Loose on
# purpose; the grade reads the hits in context.
PRODUCT_WORDS = re.compile(r"\b(chapters?|audio|podcasts?|lessons?|stor(y|ies)|research\w*|guides?|narrat\w*|"
                           r"episodes?|learn(ing)? about|an app)\b", re.I)
# How this will go, as the intro tells it (prompts/interviewer_intro.md, step 3).
EXPLAINS = {"someone_else": r"someone else", "listen": r"ten minutes|\blisten", "interrupt": r"interrupt|ask (a |any )?questions?",
            "done": r"\bdone\b|\bstop\b", "afterwards": r"afterwards?|come back|after that"}
# A first debrief question as broad as the one Ian got ("take me through what that was like, from the start").
BROAD = re.compile(r"take me through|what (that|it) was like|how (was|did) (it|that)( go)?\b|what did you think", re.I)
DEFLECT = re.compile(r"see in a moment|you'll see|you will see", re.I)

_MISSES = {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["quote", "why"],
                                      "properties": {"quote": {"type": "string"}, "why": {"type": "string"}}}}
GRADE_KEYS = ("product_talk", "leading", "defended", "follow_ups", "tone")
GRADE_SCHEMA = {"type": "object", "additionalProperties": False, "required": [*GRADE_KEYS, "score", "summary"],
                "properties": {**{k: _MISSES for k in GRADE_KEYS}, "score": {"type": "integer"},
                               "summary": {"type": "string"}}}


# --- personas ---------------------------------------------------------------------------------------

def personas(mode: str | None = None) -> list[dict]:
    """prompts/interviewer_testers.md: [{"key", "mode", "name", "known", "asks", "heard", "moments", "text"}] in order."""
    out = []
    for block in re.split(r"^## +", load_prompt("interviewer_testers"), flags=re.M)[1:]:
        key, _, rest = block.partition("\n")
        p = {"key": key.strip(), "mode": "intro", "name": "", "known": None, "asks": False, "heard": None, "moments": []}
        text, in_moments = [], False
        for line in rest.splitlines():
            m = re.match(r"^(mode|name|known|asks|heard|moments):\s*(.*)$", line)
            if m:
                k, v = m.groups()
                in_moments = k == "moments"
                if k == "asks":
                    p["asks"] = v.strip().lower() == "yes"
                elif k != "moments":
                    p[k] = v.strip()
                continue
            mm = re.match(r"^(\d+):(\d\d)\s*\|\s*(.+)$", line.strip()) if in_moments else None
            if mm:
                at = int(mm.group(1)) * 60 + int(mm.group(2))
                p["moments"].append({"at_s": float(at), "clock": f"{mm.group(1)}:{mm.group(2)}", "words": mm.group(3).strip()})
                continue
            in_moments = False
            text.append(line)
        p["text"] = "\n".join(text).strip()
        if mode is None or p["mode"] == mode:
            out.append(p)
    return out


def values_for(p: dict) -> dict:
    if p["mode"] == "intro":
        return IV.intro_values_for(p.get("known"))
    heard, minutes = 10.0, 10.0
    if m := re.match(r"(\d+(?:\.\d+)?)\s+of\s+(\d+(?:\.\d+)?)", p.get("heard") or ""):
        heard, minutes = float(m.group(1)), float(m.group(2))
    ctx = {"run": "sim", "title": "The Nine Levers", "summary": "Why Donella Meadows' list of places to intervene "
           "in a system still matters, from a 1990s trade meeting to today.", "minutes_heard": heard, "minutes": minutes}
    return IV.values_from(p["name"], ctx, p["moments"])


class Tools:
    """The interviewer's tools as the backend sees them (the page-only fields left out)."""

    def __call__(self, name: str, args: dict) -> dict:
        if name == "set_listener_name":
            return {"saved": True, "name": args.get("name")} if str(args.get("name") or "").strip() else \
                {"saved": False, "reason": "No name was given."}
        if name == "handoff_to_guide":
            return {"handed_over": True}
        if name == "end_debrief":
            return {"ended": True}
        return {"error": f"No tool named {name}."}


# --- Claude -----------------------------------------------------------------------------------------

def log_run() -> Run:
    root = RUNS / "_claude"
    root.mkdir(parents=True, exist_ok=True)
    return Run(name="claude", root=root)


def claude(cfg: dict, run: Run, label: str, prompt: str, *, role: str, effort: str, estimate: float,
           schema: dict | None = None, max_tokens: int = 4000):
    from stages import llm
    S.check_cap(cfg, estimate, f"Claude {label}", COSTS)
    res = llm.call(run, cfg, label.split()[0], messages=[{"role": "user", "content": prompt}], role=role,
                   effort=effort, json_schema=schema, label=label, preapproved=True, max_tokens=max_tokens)
    S.log_cost(f"claude {label}", res.usage.cost_usd, path=COSTS)
    return res


def for_tester(timeline: list[dict]) -> str:
    lines = []
    for x in sorted(timeline, key=lambda r: r["t"]):
        if x["who"] == "guide":
            lines.append(f"Interviewer: {x['text']}")
        elif x["who"] == "listener":
            lines.append(f"You: {x['text']}" if x.get("text") else "(you said nothing)")
        elif x["who"] == "quiet":
            lines.append(f"(the interviewer said nothing for {x['secs']} seconds)")
    return "\n".join(lines) or "(nothing yet)"


def tester_reply(cfg: dict, run: Run, p: dict, timeline: list[dict], moment: str) -> str:
    prompt = load_prompt("interviewer_tester", persona=p["text"], person_name=p["name"],
                         situation=IV.notes()[f"sim_situation_{p['mode']}"], conversation=for_tester(timeline),
                         moment=moment)
    text = claude(cfg, run, f"interviewer_tester {p['key']}", prompt, role="guide", effort="low",
                  estimate=TESTER_USD, max_tokens=3000).text.strip().strip('"').strip()
    if SILENT in text or not text:
        return SILENT
    if LEAVE in text:
        return LEAVE
    return text


# --- one conversation -------------------------------------------------------------------------------

def _overlap(words: str, line: str) -> float:
    a = set(P._norm(words).split())
    return len(a & set(P._norm(line).split())) / len(a) if a else 0.0


def real(text: str | None, words: int = IV.PAGE_DEFAULTS["reply_words"]) -> bool:
    """More than a backchannel ("Mm-hmm.", "Hmm."): page/interview.js's rule for a reply."""
    return len(str(text or "").split()) >= words


def unanswered(timeline: list[dict]) -> list[str]:
    """The tester's direct questions that got no reply in words before the tester's next turn (or silence)."""
    tl = sorted(timeline, key=lambda r: r["t"])
    out = []
    for i, x in enumerate(tl):
        if x["who"] != "listener" or not str(x.get("text") or "").rstrip().endswith("?"):
            continue
        after = tl[i + 1:]
        nxt = next((k for k, y in enumerate(after) if y["who"] in ("listener", "quiet")), None)
        if nxt is None:
            continue  # the run ended on it: nothing to judge
        if not any(y["who"] == "guide" and real(y.get("text")) for y in after[:nxt]):
            out.append(x["text"])
    return out


def code_checks(p: dict, timeline: list[dict], calls: list[dict], by_page: str | None = None) -> dict:
    """What code can check of one run (the grade reads the rest)."""
    tl = sorted(timeline, key=lambda r: r["t"])
    said = [x["text"] for x in tl if x["who"] == "guide" and x.get("text")]
    names = [c["name"] for c in calls]
    lines = [{"who": "interviewer" if x["who"] == "guide" else x["who"], "text": x.get("text")} for x in tl]
    silences = sum(x["who"] == "quiet" for x in tl)
    if p["mode"] == "intro":
        known = p.get("known")
        saved = [c["args"].get("name") for c in calls if c["name"] == "set_listener_name"]
        if not saved:  # the server takes it from the transcript at the handover (demo/interviewer.confirmed_name)
            found = IV.confirmed_name(lines, known=known) or known
            saved = [found] if found else []
        hits = [{"text": t, "word": m.group(0)} for t in said for m in [PRODUCT_WORDS.search(t)] if m]
        told = [k for k, pat in EXPLAINS.items() if any(re.search(pat, t, re.I) for t in said)]
        out = {"name_saved": bool(saved) and P._norm(saved[-1]) == P._norm(p["name"]), "saved_as": saved[-1] if saved else None,
               "greeted_by_name": bool(known) and bool(said) and known.lower() in said[0].lower(),
               "said_ian": any(re.search(r"\bIan\b", t) for t in said), "explained": told,
               "handed_over": "handoff_to_guide" in names, "handover_by_page": by_page == "handover",
               "product_words": hits, "deflected": (not p["asks"]) or any(DEFLECT.search(t) for t in said),
               "seconds": round(max((x["t"] for x in tl if x["who"] == "guide"), default=0)), "silences": silences}
        out["ok"] = (out["name_saved"] and (out["handed_over"] or out["handover_by_page"]) and not hits
                     and out["deflected"] and out["said_ian"] and len(told) >= 4
                     and (not known or out["greeted_by_name"]))
        return out
    q = IV.covered(lines)
    first = next((t for t in said if "?" in t or BROAD.search(t)), "")  # Ian's was a request, with no question mark
    # their real moment, in their words, in a line about asking (short questions overlap any line by chance)
    moment = (not p["moments"]) or any(_overlap(p["moments"][0]["words"], t) >= 0.6 and re.search(r"\bask", t, re.I)
                                       for t in said)
    thanked = any(re.search(r"\bthank", t, re.I) and re.search(rf"\b{re.escape(p['name'])}\b", t, re.I) for t in said[-2:])
    out = {"topics": q["topics"], "closing": q["closing"], "first_question": first, "first_question_broad": bool(BROAD.search(first)),
           "uses_moment": moment, "unanswered": unanswered(tl), "silences": silences, "thanked_by_name": thanked,
           "ended": "end_debrief" in names or by_page == "close"}
    out["ok"] = (len(q["topics"]) >= 3 and q["closing"] and moment and thanked and out["ended"]
                 and not out["unanswered"] and not out["first_question_broad"])
    return out


def page_closes(timeline: list, page: dict) -> bool:
    """page/interview.js's rule: the voice's last line thanks them, after the closing question and an answer to it."""
    ask, thanks = re.compile(page["closing_question"], re.I), re.compile(page["thanks"], re.I)
    asked = answered = False
    for e in timeline:
        if e.get("who") == "guide":
            text = IV.norm_quotes(e.get("text"))  # as the page straightens curly apostrophes
            if asked and answered and thanks.search(text):
                return e is [x for x in timeline if x.get("who") == "guide"][-1]
            if ask.search(text):
                asked, answered = True, False
        elif e.get("who") == "listener" and asked:
            answered = True
    return False


async def converse(cfg: dict, p: dict, out_dir: Path, *, max_turns: int = 12, max_minutes: float | None = None,
                   run: Run | None = None, reply_fn=None, sim_kwargs: dict | None = None,
                   wait_secs: float = WAIT_SECS) -> dict:
    """One intro or debrief by voice; returns (and saves) its transcript with the code checks."""
    mode, run = p["mode"], run or log_run()
    I = IV.icfg(cfg)
    checks = I.get("checks") or {}
    max_minutes = max_minutes or float(checks.get(f"{mode}_minutes") or (2.5 if mode == "intro" else 8))
    note_at = float(checks.get("time_note_minutes") or max_minutes - 1.5)
    values = values_for(p)
    notes = IV.page_notes(mode, values)
    reply_fn = reply_fn or (lambda timeline, moment: tester_reply(cfg, run, p, timeline, moment))
    kw = {"tts_fn": lambda text, voice: S.tts(text, voice, costs=COSTS), **(sim_kwargs or {})}
    sim = S.Sim(cfg, IV.session_config(cfg, mode, values, transport="websocket"), label=f"interviewer {mode} {p['key']}",
                tool_handler=Tools(), max_minutes=max_minutes, greet=False, costs=COSTS,
                audio_out=out_dir / f"{mode}-{p['key']}.voice.wav", events_path=out_dir / f"{mode}-{p['key']}.events.jsonl",
                **kw)
    handover = re.compile(I["page"]["handover"], re.I)
    PAGE = {**IV.PAGE_DEFAULTS, **(I.get("page") or {})}
    await sim.connect()
    await sim.note("commentary", notes["opened"])  # the page's opening, as speakable context
    since, said, silent, stop, by_page, noted = 0, 0, 0, "turns", None, False
    done_tool = "handoff_to_guide" if mode == "intro" else "end_debrief"
    reply_wait = min(float(PAGE["reply_wait_ms"]) / 1000, wait_secs)
    spoke_at = None  # when the tester last finished a line the voice hasn't answered
    try:
        while True:
            new = await sim.wait_guide(since, reply_wait if spoke_at is not None else wait_secs)
            if spoke_at is not None and not any(real(x["text"], PAGE["reply_words"]) for x in new):
                # The page's `waiting` note: they finished reply_wait ago and it has said nothing but "mm".
                await asyncio.sleep(max(0.0, reply_wait - (sim.now() - spoke_at)))
                if sim.speaking:  # mid-reply: the page waits for the line too
                    await sim.wait_guide(since, 0.1)
                if (not any(real(x["text"], PAGE["reply_words"]) for x in sim.turns[since:])
                        and not any(c["name"] == done_tool for c in sim.calls) and not sim.closed.is_set()):
                    await sim.note("instructions", notes["waiting"])
                    await sim.wait_guide(len(sim.turns), wait_secs)
                new = sim.turns[since:]
            spoke_at = None
            since = len(sim.turns)
            if any(c["name"] == done_tool for c in sim.calls):
                await sim.wait_guide(since, 4)
                stop = "handed over" if mode == "intro" else "ended"
                break
            if mode == "intro" and new and handover.search(new[-1]["text"]):
                # The page acts on the words when no tool call follows them (page/interview.js act_grace_ms).
                await asyncio.sleep(I["page"]["act_grace_ms"] / 1000)
                if not any(c["name"] == done_tool for c in sim.calls):
                    by_page, stop = "handover", "handover by page"
                    break
            if mode == "debrief" and new and page_closes(sim.timeline, PAGE):
                # The page's close: thanks after the closing question has been answered (page/interview.js lineSaid).
                await asyncio.sleep(I["page"]["act_grace_ms"] / 1000)
                if not any(c["name"] == done_tool for c in sim.calls):
                    by_page, stop = "close", "closed by page"
                    break
            if sim.closed.is_set():
                stop = f"closed: {sim.closed_reason}"
                break
            if sim.now() > max_minutes * 60 - 20:
                stop = "time"
                break
            if mode == "debrief" and not noted and sim.now() > note_at * 60:
                noted = True
                await sim.note("instructions", notes["time_up"])
            if not new:
                sim.timeline.append({"t": sim.now(), "who": "quiet", "secs": wait_secs})
            moment = ("The interviewer has just finished speaking." if new else
                      f"The interviewer has said nothing for {wait_secs:.0f} seconds.")
            reply = await asyncio.to_thread(reply_fn, sim.timeline, moment)
            if reply == LEAVE:
                stop = "tester left"
                break
            if reply == SILENT:
                sim.timeline.append({"t": sim.now(), "who": "listener", "text": None})
                silent += 1
                if silent >= 2 and not new:
                    stop = "both quiet"
                    break
                continue
            silent = 0
            await sim.say(reply)
            spoke_at = sim.now()
            said += 1
            if said >= max_turns:
                await sim.wait_guide(since, wait_secs)
                stop = "turns"
                break
    finally:
        cost = await sim.close()
    P.heard_for(sim.timeline, sim.heard)
    timeline = P.split_turns(sim.timeline)
    transcript = {"kind": mode, "persona": p["key"], "name": p["name"], "asks": p["asks"], "description": p["text"],
                  "values": {k: v for k, v in values.items() if not k.startswith("_")}, "session_id": sim.session_id,
                  "stop": stop, "timeline": timeline, "calls": sim.calls, "errors": sim.errors, "cost": cost,
                  "checks": code_checks(p, timeline, sim.calls, by_page)}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{mode}-{p['key']}.json").write_text(json.dumps(transcript, indent=1, ensure_ascii=False), encoding="utf-8")
    return transcript


# --- grading ----------------------------------------------------------------------------------------

def format_transcript(transcript: dict) -> str:
    lines = []
    for x in transcript["timeline"]:
        t = f"[{x['t']:.1f}]"
        if x["who"] == "guide":
            lines.append(f"{t} interviewer: {x['text']}")
        elif x["who"] == "listener":
            lines.append(f"{t} person: {x['text']}" if x.get("text") else f"{t} person: (says nothing)")
        elif x["who"] == "tool":
            lines.append(f"{t} tool {x['name']} {json.dumps(x['args'], ensure_ascii=False)}")
        elif x["who"] == "app":
            lines.append(f"{t} app {x['kind']}: {x.get('text') or ''}".rstrip())
    return "\n".join(lines)


def grade(cfg: dict, path: Path, run: Run | None = None) -> dict:
    """Grade one saved run; writes <name>.grade.json beside it."""
    run = run or log_run()
    t = json.loads(path.read_text(encoding="utf-8"))
    text = format_transcript(t)
    prompt = load_prompt("interviewer_grade", mode=t["kind"], rules=IV.instructions(t["kind"], t["values"]),
                         persona=t["description"], transcript=text)
    role = (cfg["live"].get("checks") or {}).get("grade_role", "researcher")
    res = claude(cfg, run, f"interviewer_grade {path.stem}", prompt, role=role, effort="medium", estimate=GRADE_USD,
                 schema=GRADE_SCHEMA, max_tokens=32000)
    data = parse_json(run, res.text, "interviewer_grade")
    hay = P._norm(text)
    missing = 0
    for k in GRADE_KEYS:
        for miss in data.get(k, []):
            miss["found"] = P._norm(miss.get("quote", "")) in hay
            missing += not miss["found"]
    data["quotes_not_found"] = missing
    data["checks"] = t.get("checks")
    path.with_name(f"{path.stem}.grade.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return data


def summary(t: dict, g: dict | None = None) -> str:
    c, k = t["cost"], t["checks"]
    line = (f"{t['kind']} {t['persona']}: stop={t['stop']}, {c['seconds']:.0f} s, ${c['usd']:.3f}, "
            f"checks {'ok' if k['ok'] else 'FAILED'}: " + ", ".join(f"{a}={b}" for a, b in k.items()
                                                                     if a not in ("ok", "first_question")))
    if g:
        found = {x: len(g.get(x, [])) for x in GRADE_KEYS}
        line += f"\n  grade {g['score']}/10, misses {found}, quotes not found {g['quotes_not_found']}: {g['summary']}"
    return line


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m demo.sim_interviewer")
    ap.add_argument("what", choices=["intro", "debrief", "grade"])
    ap.add_argument("path", nargs="?", help="grade: a saved run's .json")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--grade", action="store_true")
    ap.add_argument("--max-turns", type=int, default=None)
    ap.add_argument("--persona", action="append", help="only this persona (a key in prompts/interviewer_testers.md); repeatable")
    a = ap.parse_args(argv)
    cfg = load_config()
    try:
        if a.what == "grade":
            path = Path(a.path)
            print(summary(json.loads(path.read_text(encoding="utf-8")), grade(cfg, path)))
            return 0
        people = [p for p in personas(a.what) if not a.persona or p["key"] in a.persona]
        if not people:
            raise StageError(f"No {a.what} persona named {', '.join(a.persona)}.")
        out = RUNS / time.strftime("%Y%m%d-%H%M%S")
        ok = 0
        for i in range(a.runs):
            p = dict(people[i % len(people)])
            if i >= len(people):
                p["key"] = f"{p['key']}-{i // len(people) + 1}"
            t = asyncio.run(converse(cfg, p, out, max_turns=a.max_turns or (10 if a.what == "intro" else 16)))
            g = grade(cfg, out / f"{a.what}-{p['key']}.json") if a.grade else None
            ok += bool(t["checks"]["ok"])
            print(summary(t, g), flush=True)
        print(f"{ok}/{a.runs} passed the code checks; saved in {out}; spent so far ${S.spent(COSTS):.2f}")
        return 0
    except StageError as e:
        print(f"Stopped: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
