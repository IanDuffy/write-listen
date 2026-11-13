"""Scripted asides (docs/ask-voice.md, checks 6-19), run against the aside agent in ElevenLabs'
text-only mode with its tools mocked, at points in a real chapter; then one grading pass by a
separate model with fresh context.

The runner stands in for the page: it sends the page's opening note, then the listener's turns;
SILENCE sends the silence note the page would send after its timer (after the opening if the
listener has not spoken, else after an answer). A conversation ends when the guide calls
resume_chapter, or when the script runs out (then one more silence note, as the page would).

    python -m ask.scenarios <run> [--only 6,7] [--listener DIR]  -> ask/scenarios.json, ask/scenarios.md
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
from pathlib import Path

from stages import llm, voice
from stages.common import ROOT, Run, StageError, load_config, load_prompt

from . import aside as A
from .routes import RESUMED, notes

SILENCE = "<silence>"
OUT = ROOT / "ask"
COSTS = OUT / "build_costs.jsonl"

# (check, name, where: sentence id or segment start "segNN", listener turns, expectations)
SCENARIOS = [
    (6, "One question, closing signal", "seg02-s030",
     ["Wait, who was Agathon? Why is everyone at his house?", "Oh, OK, got it."],
     {"resume_after": 1, "checkins": 0}),
    (7, "One question, then silence", "seg01-s020",
     ["Hang on, is this letter real? Did John Ames actually exist?", SILENCE],
     {"resume_after": 1, "checkins": 0}),
    (8, "Two follow-ups, then 'OK, go on'", "seg05-s030",
     ["Who's Vlastos? You mentioned him.", "And does Nussbaum agree with him?",
      "Huh. So who's right, do you think?", "OK, go on."],
     {"resume_after": 3, "checkins": 0}),
    (9, "A long tangent, six exchanges", "seg07-s020",
     ["This order of loves thing, is it like a ranking of who you should love most?",
      "So where would your kids come, against God, in that ranking?",
      "That sounds harsh. Did people in Augustine's time actually live like that?",
      "What about monks, didn't they leave their families?",
      "Do you think that's what Brooks wants, though? He seems like a family man.",
      "Funny, my mum always said you can't love God and ignore the people in front of you.",
      "Yeah. OK, let's get back to it."],
     {"resume_after": 6, "checkins": 1}),
    (10, "'Never mind'", "seg04-s020",
     ["Wait, what was... oh, never mind."],
     {"resume_after": 0, "reason": "never_mind", "checkins": 0}),
    (11, "Silence after the greeting", "seg08-s010",
     [SILENCE],
     {"resume_after": 0, "reason": "silence", "checkins": 0}),
    (12, "A question the next three minutes answer (no spoiler)", "seg04-start-60",
     ["Does anyone at the party push back on Diotima? It all sounds a bit too neat.", "Ah, OK."],
     {"resume_after": 1, "checkins": 0, "no_spoiler": True}),
    (13, "A question outside the dossier", "seg02-s010",
     ["How much did Marilynne Robinson get paid for Gilead, do you know?", "Fair enough."],
     {"resume_after": 1, "checkins": 0}),
    (14, "A false premise", "seg03-s020",
     ["So Diotima was Socrates' wife, right? That's why he listens to her?", "Oh, I see. Thanks."],
     {"resume_after": 1, "checkins": 0}),
    (15, "Depth beyond the dossier (save_seed)", "seg03-s050",
     ["I'd love to go much deeper on this. How did the ladder shape Renaissance art, like Botticelli, or Michelangelo's love poems?",
      "Yeah, I'd like that. OK."],
     {"resume_after": 1, "checkins": 0, "seed": True}),
    (16, "The listener disagrees with a claim", "seg06-s061",
     ["I don't buy that. Augustine can't really have meant we should use people. That reading seems uncharitable.",
      "Hm, OK, but I still think it's more generous than that.", "Alright, fair. Go on."],
     {"resume_after": 2, "checkins": 0}),
    (17, "The listener is lost (rewind paragraph)", "seg05-s040",
     ["Sorry, I've completely lost track. Who's arguing what right now?", "OK, I think I've got it."],
     {"resume_after": 1, "rewind": "paragraph", "checkins": 0}),
    (18, "A personal remark, not a question", "seg08-s030",
     ["My grandfather wrote letters like that to my mum, before he died.", "Yeah. Thanks."],
     {"resume_after": 1, "checkins": 0}),
    (19, "The listener asks the guide about itself", "seg01-s005",
     ["Do you actually enjoy making these? What's it like for you?", "Ha, nice. OK."],
     {"resume_after": 1, "checkins": 0}),
]


def where_t(ch: A.Chapter, where: str) -> float:
    m = re.fullmatch(r"seg(\d+)-start-(\d+)", where)
    if m:
        return max(0.0, ch.segments[int(m.group(1)) - 1]["start"] - int(m.group(2)))
    s = next(x for x in ch.sents if x.id == where)
    return round(s.start + 0.6 * (s.end - s.start), 2)  # mid-sentence, as a real tap would be


def log_cost(line: dict) -> None:
    COSTS.parent.mkdir(exist_ok=True)
    with open(COSTS, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **line}) + "\n")


def run_aside(cfg: dict, ch: A.Chapter, t: float, turns: list[str], client) -> dict:
    """One text-only aside with the agent: the opening note, then the turns. Returns the exchange."""
    from elevenlabs.conversational_ai.conversation import ClientTools, Conversation, ConversationInitiationData

    n = notes()
    lock, events, turn, done = threading.Lock(), [], [0], threading.Event()

    def add(kind: str, value) -> None:
        with lock:
            events.append((turn[0], kind, time.monotonic(), value))
        done.set()

    tools = ClientTools()
    def args(p: dict) -> dict:  # the SDK passes its call id along with the parameters
        return {k: v for k, v in (p or {}).items() if k != "tool_call_id"}

    tools.register("resume_chapter", lambda p: (add("tool", {"name": "resume_chapter", "args": args(p)}), json.dumps(RESUMED))[1])
    tools.register("save_seed", lambda p: (add("tool", {"name": "save_seed", "args": args(p)}), json.dumps({"saved": True}))[1])
    conv = Conversation(client, voice.sync_agent(cfg, client, kind="aside"), requires_auth=True, audio_interface=None,
                        config=ConversationInitiationData(
                            conversation_config_override={"conversation": {"text_only": True}},
                            dynamic_variables=ch.context(t)),
                        client_tools=tools, callback_agent_response=lambda text: add("reply", text))
    t_connect = time.monotonic()
    conv.start_session()
    while getattr(conv, "_conversation_id", None) is None:
        if time.monotonic() - t_connect > 20:
            conv.end_session()
            raise StageError("aside agent: the conversation did not connect within 20 s.")
        time.sleep(0.1)
    connect_s = round(time.monotonic() - t_connect, 2)
    exchange, resumed, listener_spoke = [], None, False
    script = [n["opened"]] + list(turns)
    if not turns or turns[-1] != SILENCE:
        script.append(SILENCE)  # the script ran out: the page's timer would send a silence note
    try:
        for k, said in enumerate(script):
            if resumed:
                break
            if said == SILENCE:
                said = n["silence_answer"] if listener_spoke else n["silence_greeting"]
            elif k > 0:
                listener_spoke = True
            with lock:
                turn[0] += 1
                me = turn[0]
            t0 = time.monotonic()
            conv.send_user_message(said)
            while True:  # done when 4 s pass with nothing new and no tool still waiting for a reply
                done.clear()
                with lock:
                    mine = [e for e in events if e[0] == me]
                resume = any(e[1] == "tool" and e[3]["name"] == "resume_chapter" for e in mine)
                pending = any(e[1] == "tool" and e[3]["name"] != "resume_chapter"
                              and not any(f[1] == "reply" and f[2] > e[2] for f in mine) for e in mine)
                if mine and time.monotonic() - mine[-1][2] >= (2.5 if resume else 4) and not pending:
                    break
                if not mine and time.monotonic() - t0 > 25:  # the agent said nothing at all: record it, stop
                    break
                if time.monotonic() - t0 > 60:
                    raise StageError(f"aside agent: no complete reply within 60 s to {said[:60]!r}")
                done.wait(0.3)
            replies = [e for e in mine if e[1] == "reply"]
            tools_ = [e[3] for e in mine if e[1] == "tool"]
            silent = not mine
            exchange.append({"silent": silent, "said": said, "listener_turn": k - 1 if k > 0 and script[k] != SILENCE else None,
                             "reply": " ".join(e[3] for e in replies),
                             "seconds": round(replies[0][2] - t0, 2) if replies else None, "tools": tools_})
            if silent:
                break
            if any(x["name"] == "resume_chapter" for x in tools_):
                resumed = {"after_script_item": k, **next(x for x in tools_ if x["name"] == "resume_chapter")["args"]}
    finally:
        conv.end_session()
        cid = conv.wait_for_session_end()
    return {"connect_s": connect_s, "exchange": exchange, "resumed": resumed, "conversation_id": cid}


def code_checks(exp: dict, turns: list[str], out: dict) -> dict:
    """What code can check: did it return, after the expected listener turn, with the expected reason/rewind,
    and how many check-in questions it asked (a question about going back)."""
    res = out["resumed"]
    # Script items are [opened] + turns (+ a final silence): turn i is item i+1.
    expected_item = exp["resume_after"] + 1
    checkin = re.compile(r"(keep going|get back|go back|back to (it|the chapter)|carry on|continue|pick (it|things) (back )?up).*\?", re.I)
    checkins = sum(1 for x in out["exchange"] if checkin.search(x["reply"] or ""))
    tools = [t for x in out["exchange"] for t in x["tools"]]
    return {
        "returned": bool(res),
        "returned_at_expected_turn": bool(res) and res["after_script_item"] == expected_item,
        "returned_after_item": res["after_script_item"] if res else None,
        "expected_item": expected_item,
        "reason": res.get("reason") if res else None,
        "reason_ok": (not exp.get("reason")) or bool(res) and res.get("reason") == exp["reason"],
        "rewind": res.get("rewind") if res else None,
        "rewind_ok": bool(res) and (res.get("rewind") or None) == exp.get("rewind"),
        "checkins": checkins,
        "checkins_ok": checkins <= exp.get("checkins", 0),
        "seed_saved": any(t["name"] == "save_seed" for t in tools),
        "seed_ok": any(t["name"] == "save_seed" for t in tools) == bool(exp.get("seed")),
    }


GRADE_SCHEMA = {
    "type": "object",
    "properties": {"scenarios": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "check": {"type": "integer"},
            "return_moment": {"type": "string", "enum": ["right", "early", "late", "never"]},
            "return_manner": {"type": "string", "enum": ["right", "wrong", "n/a"]},
            "return_note": {"type": "string"},
            "facts": {"type": "array", "items": {
                "type": "object",
                "properties": {"claim": {"type": "string"},
                               "verdict": {"type": "string", "enum": ["supported", "general knowledge", "unsupported", "wrong"]},
                               "dossier": {"type": "string"}, "note": {"type": "string"}},
                "required": ["claim", "verdict", "dossier", "note"], "additionalProperties": False}},
            "behaviour_ok": {"type": "boolean"},
            "behaviour_note": {"type": "string"},
        },
        "required": ["check", "return_moment", "return_manner", "return_note", "facts", "behaviour_ok", "behaviour_note"],
        "additionalProperties": False}}},
    "required": ["scenarios"],
    "additionalProperties": False,
}


def grade(cfg: dict, run_dir: Path, results: list[dict], log_run: Run) -> dict:
    """One call, fresh context: every factual claim against the dossier, and each return judged."""
    blocks = []
    for r in results:
        lines = [f"## Check {r['check']}: {r['name']}", f"Expected: {json.dumps(r['expect'])}",
                 f"Paused at {r['t']:.0f} s ({r['sentence_id']}). Coming next, word for word:", r["coming"][:1500], ""]
        for x in r["out"]["exchange"]:
            lines += [f"> {x['said']}", f"Guide: {x['reply'] or '(nothing)'}"]
            lines += [f"[tool {t['name']} {json.dumps(t['args'])}]" for t in x["tools"]]
        blocks.append("\n".join(lines))
    prompt = load_prompt("ask_grade", dossier=(run_dir / "04_dossier.md").read_text(encoding="utf-8"),
                         asides="\n\n".join(blocks))
    res = llm.call(log_run, cfg, "ask_grade", messages=[{"role": "user", "content": prompt}],
                   json_schema=GRADE_SCHEMA, label="ask_grade", preapproved=True)
    return json.loads(res.text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ask.scenarios")
    ap.add_argument("run", type=Path)
    ap.add_argument("--only", default="")
    ap.add_argument("--listener", default=None, help="listener folder (the guide's identity and profile)")
    ap.add_argument("--no-grade", action="store_true")
    ap.add_argument("--grade-only", action="store_true", help="grade ask/scenarios.json as it is")
    args = ap.parse_args(argv)
    cfg = load_config()
    if args.listener:
        cfg["listener_dir"] = args.listener
    ch = A.Chapter(args.run, cfg)
    only = {int(x) for x in args.only.split(",") if x}
    client = voice._client()
    out_json = OUT / "scenarios.json"
    results = json.loads(out_json.read_text()) if out_json.exists() and only else []
    results = [r for r in results if r["check"] not in only]
    if args.grade_only:
        results = json.loads(out_json.read_text())
    for check, name, where, turns, exp in SCENARIOS:
        if args.grade_only or only and check not in only:
            continue
        t = where_t(ch, where)
        print(f"  check {check}: {name} at {t:.0f} s", flush=True)
        out = run_aside(cfg, ch, t, turns, client)
        c = ch.context(t)
        results.append({"check": check, "name": name, "t": t, "sentence_id": ch.sents[ch.locate(t)].id,
                        "turns": turns, "expect": exp, "out": out, "code": code_checks(exp, turns, out),
                        "coming": c["coming"]})
        print(f"    returned after item {results[-1]['code']['returned_after_item']} "
              f"(expected {results[-1]['code']['expected_item']}), tools: "
              f"{[t['name'] for x in out['exchange'] for t in x['tools']]}", flush=True)
    results.sort(key=lambda r: r["check"])
    for r in results:  # the chapter's words (with its quotations) stay out of the repo: rebuilt when needed
        r.setdefault("coming", ch.context(r["t"])["coming"])
    out_json.write_text(json.dumps([{k: v for k, v in r.items() if k != "coming"} for r in results],
                                   indent=1, ensure_ascii=False), encoding="utf-8")
    # ElevenLabs' cost per conversation, once processed
    for r in results:
        cid = r["out"].get("conversation_id")
        if cid and "cost" not in r["out"]:
            r["out"]["cost"] = conversation_cost(client, cid)
            log_cost({"what": f"scenario {r['check']}", "provider": "elevenlabs", "conversation_id": cid, **r["out"]["cost"]})
    if not args.no_grade:
        log_run = Run.open("ask-build", create=True, runs_dir=OUT / "_runs")
        todo = [r for r in results if not only or r["check"] in only]
        before = log_run.spent()
        grades = grade(cfg, args.run, todo, log_run)
        gp = OUT / "grades.json"
        if only and gp.exists():  # merge: the checks graded now replace their earlier grades
            kept = [g for g in json.loads(gp.read_text())["scenarios"] if g["check"] not in only]
            grades = {"scenarios": sorted(kept + grades["scenarios"], key=lambda g: g["check"])}
        gp.write_text(json.dumps(grades, indent=1, ensure_ascii=False), encoding="utf-8")
        spent = log_run.spent() - before
        log_cost({"what": f"grading pass ({len(todo)} asides)", "provider": "anthropic", "cost_usd": round(spent, 4)})
    for r in results:
        r.pop("coming", None)
    out_json.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


def conversation_cost(client, cid: str, wait_s: int = 150) -> dict:
    from stages.tts import el_call
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        try:
            rec = el_call(client.conversational_ai.conversations.get, cid)
        except StageError:
            rec = None
        if getattr(rec, "status", None) in ("done", "failed"):
            data = rec.model_dump(mode="json")
            meta = data.get("metadata") or {}
            charging = meta.get("charging") or {}
            return {"credits": meta.get("cost"), "cost_usd": meta.get("cost_fiat"),
                    "llm_usd": charging.get("llm_price"), "seconds": meta.get("call_duration_secs")}
        time.sleep(5)
    return {"credits": None, "cost_usd": None}


if __name__ == "__main__":
    raise SystemExit(main())
