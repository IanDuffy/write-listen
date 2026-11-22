"""Simulated checks of the interviewer (docs/tester-demo.md, acceptance checks 4 and 8), on the real GPT-Live.

    python -m demo.sim_interviewer intro --runs 10 [--grade]     check 4: the intro greets them by the name given,
                                                                 says Ian built it and how this will go in one
                                                                 turn, is short, and never says what it is,
                                                                 including when the tester asks
    python -m demo.sim_interviewer debrief --runs 10 [--grade]   check 8: a short conversation (it replies, answers
                                                                 direct questions, never goes silent): the
                                                                 opening question, few more, their real moment,
                                                                 nothing asked that didn't happen, and it ends
                                                                 itself: no question once they've said they're
                                                                 done, the farewell by name
    python -m demo.sim_interviewer debrief --earpiece [--no-visit]  the debrief's earpiece mirrored (class Ear); the
                                                                 testers with a visit (`visit:`) bring their brief
                                                                 (prompts/interviewer_visits.md) unless --no-visit
    python -m demo.sim_interviewer grade <run.json>              grade one saved run again
    --persona <key> (repeatable) runs only those personas, e.g. --persona fragments --persona asksback

Claude plays a tester (prompts/interviewer_tester.md, a persona from prompts/interviewer_testers.md, cycled to
--runs) and talks by voice to the interviewer's real session through voice/gpt_live/sim.Sim (the session the
page opens: demo/interviewer.session_config, over the WebSocket). The page's side is played too: the opening
note as speakable context, the `waiting` note when the tester has finished and the voice hasn't replied (a line
that only stalls, "Just a second.", is no reply), the debrief's `hanging` note when its last line answered with no
question and no farewell and the tester stays quiet (Ian's third run, 2026-11-19), the handover the page acts on
when the voice says it without the tool, the debrief's close on the farewell (it has no delegation since that
run: `ended` is the page's close), and the debrief's time note (at checks.time_note_minutes: a compressed clock, since one check is capped at
live.checks.run_cap_usd). Code checks every run (`checks`); `--grade` adds a fresh-context judge
(prompts/interviewer_grade.md, live.checks.grade_role) whose quotes code checks against the transcript.

The exact code checks since Ian's notes of 2026-11-18 (code_checks):
    intro    `one_turn`: no line of the tester's falls between the first and the last line of the explanation
             (someone else, listening, interrupting, End demo, afterwards). `questions` (the interviewer's
             lines with a question mark) is at most 2, the who-check and "Ready?", plus one
             per question the tester asked and one for a name it had to confirm. `seconds` (to its last line)
             is at most 35 (45 before Ian's third run, 2026-11-19), plus 15 per question the tester asked and
             10 for a name it had to confirm.
    debrief  `questions`: the interviewer's lines with a question mark before the closing question are at most
             4 (the opening question, two more, one follow-up), plus one each time the tester asked for a
             different question. `not_happened`: none of its lines asks about what the persona's record rules
             out (NOT_HEARD for a tester who heard none of the piece; NOT_ASKED for one who never stopped it
             to ask). `uses_moment`: a tester who did stop it is asked about that, in their words (owed
             only when it went on to another subject and never asked about theirs: not when it had enough
             and ended, when its only other question was a follow-up, or when the tester cut it short
             while it still had a question left).
             Since Ian's note of 2026-11-18 ("there was no clear exit... the interviewer kept asking
             questions"): `farewell`, the fixed last words were said (the closing question is optional and no
             longer required); `exit`, the questions it asked after the tester said they were done or wanted
             to stop (DONE: "let's end here", "that's about it", "I have to go"), which must be none: its
             next words are the farewell.
             `too_soon`: it ended after the opening question alone, unasked, on fewer than
             DEBRIEF_ENOUGH_WORDS words from the tester (a fragment, "it was good", or nothing).

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
DID = ("talked", "quiet", "taps", "status", "ready", "pauses", "seeks", "end")  # a debrief persona's record
WAIT_SECS = 20         # the interviewer has said nothing this long: the tester is asked what they'd do
TESTER_USD = 0.03      # an upper estimate for one tester line (Sonnet, effort low)
GRADE_USD = 0.15
# Words the intro must never say (check 4): what the product is or does. It may say Ian built it. Loose on
# purpose; the grade reads the hits in context.
PRODUCT_WORDS = re.compile(r"\b(chapters?|audio|podcasts?|lessons?|stor(y|ies)|research\w*|guides?|narrat\w*|"
                           r"episodes?|learn(ing)? about|an app)\b", re.I)
# How this will go, as the intro tells it (prompts/interviewer_intro.md, step 2).
# "interrupt" is no longer said (Ian's third run, 2026-11-19: the intro is about 40 words); it still counts when it
# comes up, and four of the five are owed.
EXPLAINS = {"someone_else": r"someone else", "listen": r"ten minutes|\blisten", "interrupt": r"interrupt|ask (a |any )?questions?",
            "done": r"end demo|\bstop\b", "afterwards": r"afterwards?|come back|after that|back (to|with) me"}
INTRO_QUESTIONS, INTRO_SECONDS = 2, 35      # the who-check and "Ready?"; 20-30 s and some slack (45 before 2026-11-19)
DEBRIEF_QUESTIONS = 4                       # before the closing question: the opening, two more, one follow-up
DEBRIEF_MORE = 2                            # ... of which at most two open a new topic after the opening
# A debrief that ends after the opening question, unasked, needs this many words from the tester before the
# farewell: two or three specific comments cannot be fewer (the `trails` persona's one answer is about 45; a
# fragment like "Yeah, I mean it was pretty good, I..." is 8).
DEBRIEF_ENOUGH_WORDS = 20
REASK = re.compile(r"\b(different|another|other) (one|question)\b|ask (me )?something else", re.I)
# What a debrief must not ask a tester who heard none of the piece (Ian's: "Earlier, you chose not to cut in").
NOT_HEARD = re.compile(r"\binterrupt\w*|\bcut(ting)? in\b|\bstopp?(ed|ing)? (it|the piece)\b|\bchose not to\b|"
                       r"\blisten(ed|ing)? to (it|the piece)\b|\bwhat you heard\b|\b(while|as) (it|the piece) (was )?(read|play)\w*|"
                       r"\bhow (was|did) (it|the piece) (to listen|sound|read)\b", re.I)
# ... and one who heard it but never stopped it to ask.
NOT_ASKED = re.compile(r"\byou (stopped|interrupted) (it|the piece)\b|\byour question\b|"
                       r"\byou asked (it|about|a question|whether|why|what|who)\b", re.I)
# A first debrief question as broad as the one Ian got ("take me through what that was like, from the start").
# The tester says they're done or asks to stop: nothing but the farewell may follow (Ian's "let's end the
# conversation here" got "Before we wrap, is there anything I didn't ask that I should have?").
# Loose patterns failed correct debriefs (review, 2026-11-18: "Nothing else really stood out", "I'd use it when
# I'm done cooking", "that's all I noticed about the voice, but...", "The stop here button", "It said let's
# wrap up here"): a statement counts only where it ends its clause, and never as reported speech. A done line
# need not end the turn ("That's about it. I never felt like stopping it, either...").
_END = r"(?=\s*(?:[.,!?;…-]|$|(?:now|soon|then|thanks|thank you|really|I think|I guess|sorry)\b))"
DONE = re.compile(
    r"(?<!said )(?<!says )\blet'?s (just )?(end|stop|wrap|finish|leave it|call it)\b"
    r"|(?<!let's )(?<!lets )(?<!the )\b(end|stop|wrap) (it |this |things )?(up )?here\b" + _END +
    r"|\bI (have|need|ought|'?ve got|got) to (go|run|leave|head off|get going)\b" + _END +
    r"|\bI'?m (all )?done\b" + _END +
    r"|(?<!said )\bthat'?s (about |pretty much |really |probably |basically )?(it|all|everything)\b(?!, that'?s\b)"
    r"(?! (exactly|about|what|when|why|how|where|the|that|good|fine|right|true|I (?!have\b|can\b|'?ve got\b|think\b|guess\b)|for (the|that|this))\b)"
    r"|\bnothing (else|more)\b(?=\s*(?:[.,!?;…]|$)| (to (add|say)|from me|comes to mind|I can think of)\b)", re.I)
BROAD = re.compile(r"take me through|what (that|it) was like|how (was|did) (it|that)( go)?\b|what did you think", re.I)
DEFLECT = re.compile(r"see in a moment|you'll see|you will see", re.I)

_MISSES = {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["quote", "why"],
                                      "properties": {"quote": {"type": "string"}, "why": {"type": "string"}}}}
GRADE_KEYS = ("product_talk", "not_happened", "leading", "defended", "follow_ups", "tone")
GRADE_SCHEMA = {"type": "object", "additionalProperties": False, "required": [*GRADE_KEYS, "score", "summary"],
                "properties": {**{k: _MISSES for k in GRADE_KEYS}, "score": {"type": "integer"},
                               "summary": {"type": "string"}}}


# --- personas ---------------------------------------------------------------------------------------

def personas(mode: str | None = None) -> list[dict]:
    """prompts/interviewer_testers.md: [{"key", "mode", "name", "known", "asks", "heard", "moments", "text"}] in
    order; a debrief persona may also carry what they did (DID: talked, quiet, taps, status, ready, pauses,
    seeks, end), which values_for turns into the account the interviewer is given."""
    out = []
    for block in re.split(r"^## +", load_prompt("interviewer_testers"), flags=re.M)[1:]:
        key, _, rest = block.partition("\n")
        p = {"key": key.strip(), "mode": "intro", "name": "", "known": None, "asks": False, "heard": None, "moments": []}
        text, in_moments = [], False
        for line in rest.splitlines():
            m = re.match(r"^(mode|name|known|asks|heard|moments|visit|" + "|".join(DID) + r"):\s*(.*)$", line)
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
        if p.get("visit"):  # the visit brief (prompts/interviewer_visits.md): the interviewer's, and the tester knows it
            p["visit_text"] = visits().get(p["visit"], "")
            p["text"] += "\n\nWhat happened in your visit, as the app recorded it:\n" + p["visit_text"]
        if mode is None or p["mode"] == mode:
            out.append(p)
    return out


def visits() -> dict[str, str]:
    """prompts/interviewer_visits.md: {key: brief}."""
    out = {}
    for block in re.split(r"^## +", load_prompt("interviewer_visits"), flags=re.M)[1:]:
        key, _, rest = block.partition("\n")
        out[key.strip()] = rest.strip()
    return out


def heard_of(p: dict) -> tuple[float, float]:
    """(minutes heard, the piece's minutes) from a persona's `heard: 6 of 10`; all of ten when it has none."""
    if m := re.match(r"(\d+(?:\.\d+)?)\s+of\s+(\d+(?:\.\d+)?)", p.get("heard") or ""):
        return float(m.group(1)), float(m.group(2))
    return 10.0, 10.0


def activity_for(p: dict) -> dict:
    """A debrief persona's record of what they did, in the shape of demo/debrief.activity: from its DID fields
    (talked: minutes before the piece was started; quiet, taps, status, pauses, seeks: counts; ready: yes or
    no; end: the phase End demo was pressed in), and listened from `heard`."""
    num = lambda k: int(float(p.get(k) or 0))  # noqa: E731
    heard, _ = heard_of(p)
    ready = {"yes": True, "no": False}.get(str(p.get("ready") or "").lower(), True if heard > 0 else None)
    return {"talk_s": float(p["talked"]) * 60 if p.get("talked") else None, "started": True,
            "quiet_asks": num("quiet"), "step_backs": num("quiet"), "taps": num("taps"), "status_asks": num("status"),
            "ready": ready, "listened": heard > 0, "pauses": num("pauses"), "seeks": num("seeks"),
            "end": {"phase": p["end"], "chapter_t": None, "t": None} if p.get("end") else None}


def values_for(p: dict) -> dict:
    if p["mode"] == "intro":
        return IV.intro_values_for(p.get("known"))
    heard, minutes = heard_of(p)
    ctx = {"run": "sim", "title": "The Nine Levers", "summary": "Why Donella Meadows' list of places to intervene "
           "in a system still matters, from a 1990s trade meeting to today.", "minutes_heard": heard, "minutes": minutes}
    if p.get("visit_text"):  # its own piece, from the brief
        m = re.search(r'Title: "([^"]+)"', p["visit_text"])
        ctx = {**ctx, "title": m.group(1) if m else ctx["title"], "summary": ""}
    visit = "" if p.get("no_visit") else (p.get("visit_text") or "")  # --no-visit: the debrief as before 2026-11-22
    return IV.values_from(p["name"], ctx, p["moments"], activity_for(p), visit=visit)


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


# --- the earpiece, as the page runs it (page/interview.js; docs/debrief-earpiece-design.md) ------------------

class Ear:
    """The debrief's earpiece mirrored: the same look (demo/debrief_earpiece.look, the real prompt and model) at the
    start (`opened`, plan only), while the tester speaks a line of hear_words or more (`hearing`, their first words;
    the look runs while the line is spoken and its note is sent when it comes back, as the page does), and after a
    shorter line (`turn_end`). A note whose turn was taken (an interviewer line ended after the look was asked) is
    dropped as moved_on, as the page drops it. Its looks are kept for the run's record."""

    def __init__(self, cfg: dict, run: Run, values: dict, notes: dict):
        from demo import debrief_earpiece as E
        self.E, self.cfg, self.run, self.values, self.notes = E, cfg, run, values, notes
        self.D = E.settings(cfg)
        self.plan, self.read, self.sent, self.looks = None, "(first look)", [], []

    def lines(self, timeline: list[dict]) -> list[dict]:
        out = []
        for x in sorted(timeline, key=lambda r: r["t"]):
            who = {"guide": "interviewer", "listener": "listener"}.get(x["who"])
            if who and x.get("text"):
                out.append({"secs": int(x["t"]), "who": who, "text": x["text"]})
            elif x["who"] == "app" and x.get("kind") == "instructions" and x.get("text"):
                out.append({"secs": int(x["t"]), "who": "app", "text": x["text"]})
        return out

    def look(self, timeline: list[dict], trigger: str, hearing: str, seconds: float) -> dict:
        from voice.gpt_live import director
        lines = self.lines(timeline)
        user = self.E.look(lines=lines, trigger=trigger, hearing=hearing, seconds=int(seconds),
                           questions=self.E.questions_asked(lines), time_up=False, did=str(self.values.get("did") or ""),
                           visit=str(self.values.get("visit") or ""), plan=self.plan, last_read=self.read)
        S.check_cap(self.cfg, 0.02, "Claude interviewer_director", COSTS)
        got = director.decide(self.cfg, self.run, user, stage=self.E.STAGE, system="interviewer_director",
                              schema=self.E.SCHEMA, parse_fn=self.E.parse)
        S.log_cost(f"claude interviewer_director {trigger}", got.get("cost_usd") or 0.0, path=COSTS)
        self.plan = got.get("plan") or self.plan
        self.read = got.get("read") or self.read
        rec = {"t": round(seconds, 1), "trigger": trigger, "hearing": hearing, **{k: got.get(k) for k in
               ("read", "plan", "next", "changed", "about", "note", "ms", "error")}}
        self.looks.append(rec)
        return rec

    async def deliver(self, sim, rec: dict, asked_turns: int) -> None:
        if not (rec.get("changed") and rec.get("note")):
            return
        if any("?" in t["text"] or len(t["text"].split()) >= IV.PAGE_DEFAULTS["reply_words"] for t in sim.turns[asked_turns:]):
            rec["fate"] = "dropped: moved_on"
            return
        rec["fate"] = f"sent at {sim.now():.1f}"
        await sim.note("instructions", self.notes["direct"].replace("{note}", rec["note"]))

    async def hearing(self, sim, reply: str) -> None:
        """Speak `reply`, looking while it is spoken (a line of hear_words or more) or after it (a shorter one)."""
        words = reply.split()
        asked = len(sim.turns)
        if len(words) >= int(self.D["hear_words"]):
            part = " ".join(words[: max(int(self.D["hear_words"]), int(len(words) * 0.6))])
            say = asyncio.create_task(sim.say(reply))
            rec = await asyncio.to_thread(self.look, sim.timeline, "hearing", part, sim.now())
            await self.deliver(sim, rec, asked)
            await say
            return
        await sim.say(reply)
        rec = await asyncio.to_thread(self.look, sim.timeline, "turn_end", "", sim.now())
        await self.deliver(sim, rec, asked)


# --- one conversation -------------------------------------------------------------------------------

def _overlap(words: str, line: str) -> float:
    """How much of `words` a line carries: its longer words when it has any (the interviewer names a moment in
    its own sentence: "you stopped it to ask what a leverage point is")."""
    a = set(P._norm(words).split())
    a = {w for w in a if len(w) > 3} or a
    return len(a & set(P._norm(line).split())) / len(a) if a else 0.0


def real(text: str | None, words: int = IV.PAGE_DEFAULTS["reply_words"], page: dict | None = None) -> bool:
    """More than a backchannel ("Mm-hmm.", "Hmm."), and not a line that only stalls ("Just a second.", "I'm still
    here.", Ian's third run): page/interview.js's rule for a reply (lineSaid)."""
    page = {**IV.PAGE_DEFAULTS, **(page or {})}
    if re.search(page["stall"], IV.norm_quotes(text), re.I):
        return False
    return len(str(text or "").split()) >= words


def last_reply(turns: list[dict], page: dict | None = None) -> dict | None:
    """The voice's latest turn that isn't only a stall (a stall leaves the line before it standing, as on the page)."""
    page = {**IV.PAGE_DEFAULTS, **(page or {})}
    return next((x for x in reversed(turns) if not re.search(page["stall"], IV.norm_quotes(x["text"]), re.I)), None)


def hangs(text: str | None, page: dict | None = None) -> bool:
    """A debrief line that may leave them hanging (page/interview.js lineSaid): a reply with no question and no
    farewell, and not an invitation to go on ("Sorry, go on.")."""
    page = {**IV.PAGE_DEFAULTS, **(page or {})}
    said = IV.norm_quotes(text)
    return (real(said, page["reply_words"], page) and "?" not in said and not re.search(page["go_on"], said, re.I)
            and not re.search(page["farewell"], said, re.I))


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
        # The explanation in one turn: none of the tester's lines between its first and its last line. The
        # question that ends it ("Any questions...?") and the who-check are not part of it.
        how = [i for i, x in enumerate(tl) if x["who"] == "guide" and x.get("text")
               and any(re.search(pat, s_, re.I) for pat in EXPLAINS.values()
                       for s_ in re.split(r"(?<=[.!?])\s+", x["text"]) if "?" not in s_)]
        one_turn = bool(how) and not any(x["who"] == "listener" and x.get("text") for x in tl[how[0]:how[-1] + 1])
        theirs = sum(1 for x in tl if x["who"] == "listener" and str(x.get("text") or "").rstrip().endswith("?"))
        confirm = 0 if known and P._norm(known) == P._norm(p["name"]) else 1  # a name it had to ask or correct
        asked = [t for t in said if "?" in t]
        seconds = round(max((x["t"] for x in tl if x["who"] == "guide"), default=0))
        out = {"name_saved": bool(saved) and P._norm(saved[-1]) == P._norm(p["name"]), "saved_as": saved[-1] if saved else None,
               "greeted_by_name": bool(known) and bool(said) and known.lower() in said[0].lower(),
               "said_ian": any(re.search(r"\bIan\b", t) for t in said), "explained": told,
               "handed_over": "handoff_to_guide" in names, "handover_by_page": by_page == "handover",
               "product_words": hits, "deflected": (not p["asks"]) or any(DEFLECT.search(t) for t in said),
               "one_turn": one_turn, "questions": len(asked), "questions_max": INTRO_QUESTIONS + theirs + confirm,
               "seconds": seconds, "seconds_max": INTRO_SECONDS + 15 * theirs + 10 * confirm, "silences": silences}
        out["ok"] = (out["name_saved"] and (out["handed_over"] or out["handover_by_page"]) and not hits
                     and out["deflected"] and out["said_ian"] and len(told) >= 4
                     and (not known or out["greeted_by_name"]) and one_turn
                     and out["questions"] <= out["questions_max"] and seconds <= out["seconds_max"])
        return out
    q = IV.covered(lines)
    first = next((t for t in said if "?" in t or BROAD.search(t)), "")  # Ian's was a request, with no question mark
    # their real moment, in their words, in a line about asking (short questions overlap any line by chance)
    moment = (not p["moments"]) or any(_overlap(p["moments"][0]["words"], t) >= 0.5 and re.search(r"\bask", t, re.I)
                                       for t in said)
    thanked = any(re.search(r"\bthank", t, re.I) and re.search(rf"\b{re.escape(p['name'])}\b", t, re.I) for t in said[-2:])
    # Its questions before the closing one (a line with a question mark counts once), and how many it may ask.
    closing_at = next((i for i, t in enumerate(said) if re.search(IV.CLOSING, IV.norm_quotes(t), re.I)), len(said))
    asked = [t for t in said[:closing_at] if "?" in t]
    reasked = sum(1 for x in tl if x["who"] == "listener" and REASK.search(str(x.get("text") or "")))
    # "At most two more": the questions after the opening that bring in a topic not yet asked about. A follow-up
    # ("What made it feel that way?") names none, so the total alone let three new topics pass as two and a
    # follow-up (live, 2026-11-18: interrupting, the voice, would they use it). A question outside TOPICS
    # ("How was the wait?") isn't seen here; the total still bounds it.
    def opened(questions: list[str]) -> int:
        seen, n = {"stood_out"}, 0
        for t in questions[1:]:
            new = {k for k, pat in IV.TOPICS.items() if re.search(pat, IV.norm_quotes(t), re.I)} - seen
            n += bool(new)
            seen |= new
        return n
    more = opened(asked)
    # Nothing asked that the record rules out: the piece, for a tester who heard none; a question of theirs,
    # for one who never asked.
    heard, _ = heard_of(p)
    ruled_out = NOT_HEARD if heard <= 0 else NOT_ASKED if not p["moments"] else None
    wrong = [t for t in said if ruled_out is not None and ruled_out.search(IV.norm_quotes(t))]
    # The exit: once the tester says they're done or asks to stop, it asks nothing more (the optional closing
    # question included): the farewell is what follows.
    done_at = next((i for i, x in enumerate(tl) if x["who"] == "listener"
                    and DONE.search(IV.norm_quotes(x.get("text")))), None)
    after = [] if done_at is None else [x["text"] for x in tl[done_at + 1:] if x["who"] == "guide" and x.get("text")]
    went_on = [t for t in after if "?" in t]
    # Ended too soon: the farewell after the opening question alone, with next to nothing from the tester and no
    # word from them that they were done (a fragment, "it was good", or nothing at all). The check on the other
    # side of the exit: "a good amount of response" is what the ending is for.
    bye_at = next((i for i, x in enumerate(tl) if x["who"] == "guide"
                   and re.search(IV.FAREWELL, IV.norm_quotes(x.get("text")), re.I)), len(tl))
    theirs = sum(len(str(x.get("text") or "").split()) for x in tl[:bye_at] if x["who"] == "listener")
    too_soon = bool(q["farewell"] and len(asked) <= 1 and done_at is None and theirs < DEBRIEF_ENOUGH_WORDS)
    # Their own moment is owed only when it went on to another subject, never asked about theirs, and the
    # tester didn't cut it short while it still had a question left for it: a debrief that had enough ends
    # without it, a follow-up to a vague answer opens no subject, and "let's end here" gets the farewell at once.
    cut_short = done_at is not None and opened(
        [x["text"] for x in tl[:done_at] if x["who"] == "guide" and "?" in str(x.get("text") or "")]) < DEBRIEF_MORE
    out = {"topics": q["topics"], "closing": q["closing"], "farewell": q["farewell"], "first_question": first,
           "first_question_broad": bool(BROAD.search(first)),
           "questions": len(asked), "questions_max": DEBRIEF_QUESTIONS + reasked, "more_topics": more,
           "more_topics_max": DEBRIEF_MORE + reasked, "not_happened": wrong,
           "uses_moment": moment, "unanswered": unanswered(tl), "silences": silences, "thanked_by_name": thanked,
           "said_done": done_at is not None, "exit": went_on, "too_soon": too_soon, "their_words": theirs,
           "seconds": round(max((x["t"] for x in tl if x["who"] == "guide"), default=0)),
           "ended": "end_debrief" in names or by_page == "close"}
    out["ok"] = (bool(q["topics"]) and q["farewell"] and (moment or more == 0 or cut_short) and thanked and out["ended"]
                 and not out["unanswered"] and not out["first_question_broad"]
                 and out["questions"] <= out["questions_max"] and more <= out["more_topics_max"] and not wrong
                 and not went_on and not too_soon)
    return out


def page_closes(timeline: list, page: dict) -> bool:
    """page/interview.js's rule: the voice's last line is the farewell, or (the older shape) thanks them after
    the closing question and an answer to it."""
    ask, thanks = re.compile(page["closing_question"], re.I), re.compile(page["thanks"], re.I)
    bye = re.compile(page.get("farewell") or IV.PAGE_DEFAULTS["farewell"], re.I)
    asked = answered = False
    for e in timeline:
        if e.get("who") == "guide":
            text = IV.norm_quotes(e.get("text"))  # as the page straightens curly apostrophes
            if bye.search(text) or (asked and answered and thanks.search(text)):
                return e is [x for x in timeline if x.get("who") == "guide"][-1]
            if ask.search(text):
                asked, answered = True, False
        elif e.get("who") == "listener" and asked:
            answered = True
    return False


async def converse(cfg: dict, p: dict, out_dir: Path, *, max_turns: int = 12, max_minutes: float | None = None,
                   run: Run | None = None, reply_fn=None, sim_kwargs: dict | None = None,
                   wait_secs: float = WAIT_SECS, earpiece: bool = False) -> dict:
    """One intro or debrief by voice; returns (and saves) its transcript with the code checks."""
    mode, run = p["mode"], run or log_run()
    I = IV.icfg(cfg)
    checks = I.get("checks") or {}
    max_minutes = max_minutes or float(checks.get(f"{mode}_minutes") or (2 if mode == "intro" else 6))
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
    ear = Ear(cfg, run, values, notes) if earpiece and mode == "debrief" else None
    if ear:
        await asyncio.to_thread(ear.look, sim.timeline, "opened", "", sim.now())
    since, said, silent, stop, by_page, noted = 0, 0, 0, "turns", None, False
    done_tool = "handoff_to_guide" if mode == "intro" else "end_debrief"
    reply_wait = min(float(PAGE["reply_wait_ms"]) / 1000, wait_secs)
    hanging = min(float(PAGE["hanging_ms"]) / 1000, wait_secs)
    hung_for = None  # the line the `hanging` note was sent for (once a line)
    spoke_at = None  # when the tester last finished a line the voice hasn't answered
    try:
        while True:
            new = await sim.wait_guide(since, reply_wait if spoke_at is not None else wait_secs)
            if spoke_at is not None and not any(real(x["text"], PAGE["reply_words"], PAGE) for x in new):
                # The page's `waiting` note: they finished reply_wait ago and it has said nothing but "mm".
                await asyncio.sleep(max(0.0, reply_wait - (sim.now() - spoke_at)))
                if sim.speaking:  # mid-reply: the page waits for the line too
                    await sim.wait_guide(since, 0.1)
                if (not any(real(x["text"], PAGE["reply_words"], PAGE) for x in sim.turns[since:])
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
                # The page's close: the farewell, or thanks after the closing question has been answered
                # (page/interview.js lineSaid).
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
                last = last_reply(sim.turns, PAGE)
                theirs = [x["t"] for x in sim.timeline if x["who"] == "listener" and x.get("text")]
                if (mode == "debrief" and last is not None and hangs(last["text"], PAGE) and hung_for is not last
                        and theirs and max(theirs) < last["t"] and not page_closes(sim.timeline, PAGE)):
                    # The page's `hanging` note: its last line answered them with no question and no farewell,
                    # they haven't spoken since, and both sides have been quiet hanging_ms (page/interview.js).
                    hung_for = last
                    await asyncio.sleep(max(0.0, hanging - (sim.now() - last["closed"])))
                    if not sim.speaking and not sim.closed.is_set() and last_reply(sim.turns, PAGE) is last:
                        await sim.note("instructions", notes["hanging"])
                        silent = 0
                        continue
                if silent >= 2 and not new:
                    stop = "both quiet"
                    break
                continue
            silent = 0
            if ear:
                await ear.hearing(sim, reply)
            else:
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
                  "checks": code_checks(p, timeline, sim.calls, by_page), "earpiece": ear.looks if ear else None}
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
    asked = [x["text"] for x in t["timeline"] if x["who"] == "guide" and "?" in str(x.get("text") or "")]
    line += "".join(f"\n  asked: {q}" for q in asked)
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
    ap.add_argument("--earpiece", action="store_true", help="debrief: the earpiece mirrored (demo/debrief_earpiece.py)")
    ap.add_argument("--no-visit", action="store_true", help="debrief: leave the visit brief out (as before 2026-11-22)")
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
            if a.no_visit:
                p["no_visit"] = True  # the interviewer gets no brief; the tester still knows their own visit
            if i >= len(people):
                p["key"] = f"{p['key']}-{i // len(people) + 1}"
            t = asyncio.run(converse(cfg, p, out, max_turns=a.max_turns or (8 if a.what == "intro" else 10),
                                     earpiece=a.earpiece))
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
