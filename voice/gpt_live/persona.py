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
- `Earpiece`: the page's earpiece (live.director) mirrored in `converse` with `director=True`: a background task
  on the page's triggers, the same verdict (`verdict`), whispers sent at once as instructions.
- `probe`: an earpiece probe (PROBES: A mid-story, B after the guide's question, C skippable, D supersede): a
  scripted listener and an injected whisper, no Claude at all; it records when the guide's next turn came and
  whether a turn took the note up.

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
from . import director as live_director
from . import session as live
from . import sim as S

SILENT, LEAVE = "[SILENT]", "[LEAVE]"
GUIDE_WAIT_SECS = 20  # the guide has said nothing for this long: the listener is asked what they'd do
LISTENER_USD = 0.03  # an upper estimate for one listener reply (Sonnet, effort low)
DIRECTOR_USD = 0.015  # and for one director decision (Sonnet, effort low; measured about $0.002-0.007)
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


TAG = re.compile(r"\[[^\]]*\]?|\]")


def listener_turns(timeline: list[dict]) -> list[int]:
    """The listener's turns as the page counts them for engaged() (live.js countLine): their lines with no guide
    line between are one turn (the page merges fragments heard within engaged_merge_ms; the simulated listener's
    pieces are one line apart), words without sound tags, turns of no words left out."""
    out, open_ = [], False
    for x in sorted(timeline, key=lambda r: r["t"]):
        if x["who"] == "guide":
            open_ = False
        elif x["who"] == "listener" and x.get("text"):
            n = len([w for w in TAG.sub(" ", x["text"]).split() if re.search(r"[A-Za-z0-9]", w)])
            if open_ and out:
                out[-1] += n
            else:
                out.append(n)
                open_ = True
    return [n for n in out if n > 0]


def director_lines(timeline: list[dict]) -> list[dict]:
    """The simulated conversation as the director reads the page's (director.lines_from_transcript)."""
    out = []
    for x in sorted(timeline, key=lambda r: r["t"]):
        secs = int(x["t"])
        if x["who"] in ("guide", "listener") and x.get("text"):
            out.append({"secs": secs, "who": x["who"], "text": x["text"]})
        elif x["who"] == "app" and x.get("kind") in ("instructions", "commentary") and x.get("text"):
            out.append({"secs": secs, "who": "app", "text": x["text"]})
        elif x["who"] == "tool":
            out.append({"secs": secs, "who": "tool", "text": x.get("name", "")})
    return out


def explained_at(timeline: list[dict]) -> float | None:
    """When the guide first said what it does (session.EXPLAINED, as the page's L.explainedAt)."""
    return next((x["t"] for x in timeline if x["who"] == "guide" and live.EXPLAINED.search(x.get("text") or "")), None)


TICK_SECS = 0.5  # the earpiece's clock in the persona check
STALE_AFTER_START = ("offer", "pitch", "interest", "start")


def verdict(got: dict, *, here: bool, started: bool, explained: bool, offered: bool, spoke_since_ask: bool,
            listener_speaking: bool, now: float, sent_at: float | None, min_gap: float,
            guide_asked_since: bool = False, trigger: str | None = None) -> str:
    """The page's verdict on a look (live.js directorVerdict), mirrored: "send", "hold" or "drop". Only a whisper
    (a note with changed) is sent; stale kinds go once a chapter has started, an offer once the guide has said
    what it does or was whispered it; a "now" note goes unless the listener has spoken since the look was asked
    for (held while they are mid-line); any other whisper goes at once unless it is within min_gap seconds of
    the last whisper (other page notes don't count); an "interest" question goes once the guide's last line, ended
    after the look was asked, asked something (Pooja, 2026-11-20). The offered look's "interest" question is exempt
    from the gap (live.js dirOfferQuestion). No lull hold, no maximum age."""
    if not (got.get("changed") and got.get("note")) or not here:
        return "drop"
    about = got.get("about")
    if started and about in STALE_AFTER_START:
        return "drop"
    if about == "offer" and (explained or offered):
        return "drop"
    if about == "interest" and guide_asked_since:
        return "drop"
    if got.get("kind") == "now":
        if spoke_since_ask:
            return "drop"
        return "hold" if listener_speaking else "send"
    if sent_at is not None and now - sent_at < min_gap and not (trigger == "offered" and about == "interest"):
        return "drop"
    return "send"


def whisper_note(notes: dict, got: dict) -> tuple[str, str]:
    """(template key, the instruction) for a whisper: notes.md direct_start, direct_now or direct."""
    key = ("direct_start" if got.get("about") == "start" else "direct_now") if got.get("kind") == "now" else "direct"
    return key, notes[key].replace("{note}", got["note"])


def _words(text: str) -> int:
    return len([w for w in TAG.sub(" ", text or "").split() if re.search(r"[A-Za-z0-9]", w)])


# The page's reading of an answer to started_step_back's offer to chat (live.js declinesChat; keep in step).
NO_CHAT = re.compile(r"^\W*(?:(?:no|nope|nah|not really|not right now|nothing(?: else)?|maybe later|i'?m (?:good|fine|all set)"
                     r"|all good|that'?s (?:all|fine|it)|go ahead|go for it|thanks|thank you|ok(?:ay)?|sounds good|perfect"
                     r"|great)\b\W*)+$", re.I)
NO_LEAD = re.compile(r"^\W*((um+|uh+|er+|hmm+|mm+|ah+|oh|well|so|yeah|yep|sure)\b\W*)+", re.I)
NO_ANY = re.compile(r"\b(go ahead|go for it|i'?m (good|fine|all set)|all good|not (right )?now|maybe later)\b", re.I)
YES_CHAT = re.compile(r"\b(tell me|let'?s|chat|talk)\b", re.I)


def declines_chat(said: str) -> bool:
    """live.js declinesChat: a plain no to the offer to chat (the page then rests)."""
    if "?" in said or _words(said) > 6:
        return False
    core = NO_LEAD.sub("", said).strip()
    return bool(NO_CHAT.search(said) or (core and NO_CHAT.search(core)) or (NO_ANY.search(said) and not YES_CHAT.search(said)))


class Earpiece:
    """The page's earpiece (live.js director*), mirrored in the persona check: one background task that ticks
    every TICK_SECS of the conversation and asks on the page's triggers (config live.director): "started" once
    after the started note (after the listener's answer to started_step_back's offer to chat), "turn_end" when a
    listener turn ends (no line from them for live.page.engaged_merge_ms) with every_words words or every_lines
    new lines since the last look, "offered" once when the guide first offers to say what it does or says it after
    a listener line, "cadence" every every_secs with a new line (company_every_secs once started), "silence" once
    per stretch of silence_secs with nobody speaking. One look in flight; a newer trigger waits.
    Each look gets the earpiece's previous plan and read (as the server reads them from its rows) and goes
    through the same verdict; a whisper is sent at once as an instruction, even while the listener's audio is
    streaming. `stop()` awaits a look in flight, so its cost is logged. Two failures in a row and it is down
    (the page's clocks come back, in a lull only)."""

    def __init__(self, cfg: dict, sim, *, profile: str, notes: dict, decide_fn, known: bool = False):
        self.cfg, self.sim, self.profile, self.notes, self.decide_fn, self.known = cfg, sim, profile, notes, decide_fn, known
        self.D = live_director.settings(cfg)
        self.merge = float(cfg["live"]["page"].get("engaged_merge_ms") or 1500) / 1000
        self.looks: list[dict] = []
        self.plan, self.read = None, "(first look)"
        self.asked_at, self.seen = 0.0, 0
        self.silence_ix, self.silence_seen = 0, -1
        self.turn_seen = None
        self.event: str | None = None
        self.reply_wait_from: float | None = None
        self.sent_at: float | None = None
        self.offered, self.fails, self.down, self.stopped, self.rested = False, 0, False, False, False
        self.offer_looked = False  # the "offered" look, once a conversation (live.js L.dir.offerLook)
        self.inflight: asyncio.Task | None = None
        self.task: asyncio.Task | None = None
        self.held: tuple[dict, dict] | None = None

    # --- what the page knows ---
    def talk(self) -> list[dict]:
        return [x for x in director_lines(self.sim.timeline) if x["who"] in ("guide", "listener")]

    def speaking(self) -> str:
        return "listener" if self.sim.speech else "guide" if self.sim.speaking else "none"

    def started(self) -> bool:
        return any(c["name"] == "start_chapter" for c in self.sim.calls)

    def explained(self) -> bool:
        return self.known or explained_at(self.sim.timeline) is not None

    def last_end(self) -> float:
        ends = [x["end"] for x in self.sim.turns] + [x.get("end") or x["t"] for x in self.sim.timeline
                                                     if x["who"] == "listener" and x.get("text")]
        return max(ends + [self.sim.audio_last if self.sim.audio_last > 0 else 0.0, 0.0])

    def line_end(self) -> float:
        """When the newest guide or listener line ended (the page's lastGuideAt / lastListenerAt)."""
        ends = [x["end"] for x in self.sim.turns if x.get("end") is not None]
        ends += [x["end"] for x in self.sim.timeline if x["who"] == "listener" and x.get("text") and x.get("end") is not None]
        return max(ends + [0.0])

    def offered_by_guide(self) -> bool:
        """The guide's last line offered to say what it does (live.page.offer_nudge.offered, as the page reads it)."""
        pat = (self.cfg["live"]["page"].get("offer_nudge") or {}).get("offered")
        return bool(pat and self.sim.turns and re.search(pat, self.sim.turns[-1].get("text") or "", re.I))

    def offers(self, text: str) -> bool:
        """A guide line offering to say what it does (live.page.offer_nudge.offered) or saying it (EXPLAINED)."""
        pat = (self.cfg["live"]["page"].get("offer_nudge") or {}).get("offered")
        return bool((pat and re.search(pat, text or "", re.I)) or live.EXPLAINED.search(text or ""))

    def offer_due(self) -> bool:
        """live.js: the guide's first finished line that offers to say what it does, or says it, after a listener
        line (an offer line before the listener has spoken waits for a later one), with no chapter started."""
        if self.offer_looked or self.started():
            return False
        heard = [x.get("end") or x["t"] for x in self.sim.timeline
                 if x["who"] == "listener" and (x.get("text") or "").strip()]
        return bool(heard) and any(x.get("end") is not None and x["end"] > min(heard) and self.offers(x.get("text") or "")
                                   for x in self.sim.turns)

    def guide_asked_since(self, t: float) -> bool:
        """live.js guideAskedAt: the guide's last line asked something and ended after t."""
        last = self.sim.turns[-1] if self.sim.turns else None
        return bool(last and last.get("end") is not None and last["end"] > t
                    and re.search(r"\?\s*$", last.get("text") or ""))

    def spoke_since(self, t: float) -> bool:
        return any(x["who"] == "listener" and x.get("text") and x.get("end") is not None and x["end"] > t
                   for x in self.sim.timeline)

    def here(self) -> bool:
        if (self.stopped or self.down or self.rested or self.sim.closed.is_set()
                or any(c["name"] == "step_back" for c in self.sim.calls)):
            return False
        if self.reply_wait_from is not None:  # the step-back reply wait (live.js): asked again once they answer
            answer = next((x for x in self.sim.timeline
                           if x["who"] == "listener" and x.get("text") and x["t"] > self.reply_wait_from), None)
            if answer is None:
                return False
            self.reply_wait_from = None
            if declines_chat(answer["text"]):  # a plain no: the page rests, and forgets the started look (dirForget)
                self.rested, self.event, self.held = True, None, None
                return False
        return True

    def on_started(self, step_back: bool) -> None:
        self.event = "started"
        if step_back:
            self.reply_wait_from = self.sim.now()

    # --- the clock ---
    async def run(self) -> None:
        while not self.stopped and not self.sim.closed.is_set():
            await self.tick(self.sim.now())
            await asyncio.sleep(TICK_SECS)

    def trigger(self, now: float) -> str | None:
        D, talk = self.D, self.talk()
        n = len(talk)
        if n != self.silence_seen:
            self.silence_ix, self.silence_seen = 0, n
        if self.event:
            e, self.event = self.event, None
            return e
        if self.offer_due():
            self.offer_looked = True
            return "offered"
        lis = [x for x in self.sim.timeline if x["who"] == "listener" and x.get("text")]
        if lis and lis[-1] is not self.turn_seen and lis[-1].get("end") is not None and now - lis[-1]["end"] >= self.merge:
            last = self.turn_seen = lis[-1]
            g = max((x["t"] for x in self.sim.timeline if x["who"] == "guide" and x["t"] < last["t"]), default=-1.0)
            # The turn as the page counts it (live.js countLine): a line joins the one before only if it began
            # within engaged_merge_ms of that one's end, with no guide line between.
            turn = [last]
            for x in reversed(lis[:-1]):
                if x["t"] <= g or x.get("end") is None or turn[0]["t"] - x["end"] >= self.merge:
                    break
                turn.insert(0, x)
            words = sum(_words(x["text"]) for x in turn)
            if words >= int(D["every_words"]) or n - self.seen >= int(D["every_lines"]):
                return "turn_end"
        every = D["company_every_secs"] if self.started() else D["every_secs"]
        if n > self.seen and now - self.asked_at >= float(every):
            return "cadence"
        secs = D.get("silence_secs") or []
        if (n and self.silence_ix < len(secs) and self.speaking() == "none"
                and now - self.last_end() >= float(secs[self.silence_ix])):
            self.silence_ix += 1
            return "silence"
        return None

    async def tick(self, now: float) -> None:
        if self.inflight is not None or not self.here():
            return
        if self.held:
            got, rec = self.held
            self.held = None
            await self.deliver(got, rec)
            return
        trig = self.trigger(now)
        if trig:
            self.ask(trig, now)

    def ask(self, trig: str, now: float) -> None:
        lines = director_lines(self.sim.timeline)
        sp = self.speaking()
        last_who = next((x["who"] for x in reversed(lines) if x["who"] in ("guide", "listener")), None)
        silence = max(0.0, now - self.line_end())  # since the last line ended, whoever is speaking (live.js)
        started = self.started()
        user = live_director.look(self.D, lines=lines, trigger=trig, speaking=sp, last_who=last_who,
                                  silence_secs=silence, seconds=int(now), explained=self.explained(), started=started,
                                  profile=self.profile, plan=self.plan, last_read=self.read)
        self.asked_at, self.seen = now, len(self.talk())
        rec = {"t": now, "trigger": trig, "speaking": sp}
        self.inflight = asyncio.create_task(self._look(user, rec))

    async def _look(self, user: str, rec: dict) -> None:
        try:
            got = await asyncio.to_thread(self.decide_fn, user)
            if not got.get("error"):
                got = {**got, **live_director.shape(got)}
        except Exception as e:  # a look never breaks the conversation (a cap refusal, a bad answer)
            got = {**live_director.NO_WHISPER, "error": f"{type(e).__name__}: {e}"[:300]}
        rec.update({k: got.get(k) for k in live_director.FIELDS + ("ms", "error")})
        rec["arrived"] = self.sim.now()
        self.looks.append(rec)
        self.fails = self.fails + 1 if got.get("error") else 0
        self.down = self.down or self.fails >= 2
        self.plan = got.get("plan") or self.plan
        self.read = got.get("read") or self.read
        try:
            if not self.stopped:
                await self.deliver(got, rec)
        finally:
            self.inflight = None

    async def deliver(self, got: dict, rec: dict) -> None:
        now = self.sim.now()
        v = verdict(got, here=self.here(), started=self.started(), explained=self.explained(),
                    offered=self.offered or self.offered_by_guide(),
                    spoke_since_ask=self.spoke_since(rec["t"]), listener_speaking=bool(self.sim.speech), now=now,
                    sent_at=self.sent_at, min_gap=float(self.D["min_gap_secs"] or 0),
                    guide_asked_since=self.guide_asked_since(rec["t"]), trigger=rec.get("trigger"))
        if v == "hold":
            self.held = (got, rec)
            return
        rec["delivered"] = v == "send"
        if v != "send":
            return
        key, text = whisper_note(self.notes, got)
        await self.sim.note("instructions", text)
        self.sent_at = rec["sent_t"] = self.sim.now()
        rec["template"] = key
        self.offered = self.offered or got.get("about") == "offer"

    async def stop(self) -> None:
        self.stopped = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.inflight is not None:  # its cost is logged when it returns
            await asyncio.gather(self.inflight, return_exceptions=True)


def next_turns(looks: list[dict], turns: list[dict]) -> None:
    """For each whisper sent, the guide's next turn after it: when it began and what it said."""
    for rec in looks:
        if rec.get("sent_t") is None:
            continue
        nxt = next((x for x in turns if x["t"] >= rec["sent_t"]), None)
        rec["next_turn_t"] = nxt["t"] if nxt else None
        rec["next_turn"] = nxt["text"] if nxt else None


async def converse(cfg: dict, persona: dict, out_dir: Path, *, max_turns: int = 12, after_start: int = 2,
                   max_minutes: float = 6, log_run: Run | None = None, reply_fn=None, sim_kwargs: dict | None = None,
                   handoff: str | None = None, pause_secs: float = PAUSE_SECS, director: bool = False,
                   director_fn=None) -> dict:
    """One onboarding conversation by voice; returns (and saves) its transcript. `handoff`: the name a
    tester gave the interviewer: the conversation opens with the page's `handoff` note (the guide greets
    them by name) instead of the hello, as in the Tester Demo. A listener's line with "..." in it is
    spoken in pieces with `pause_secs` of silence at each (someone who talks in fragments: does the guide
    wait, or cut in?).

    `director` (live.director, Ian 2026-11-19: the earpiece): as the page does with it on, an Earpiece looks at
    the conversation in the background on the page's triggers and sends its whispers at once as instructions
    (notes.md direct, direct_now, direct_start), even while the listener speaks; the offer cue is off and the
    pitch nudges are late fallbacks, in silences only, their gap counted from the last whisper (the first is the
    offer cue if the guide hasn't said what it does). Every look is in the transcript's "director" list, each
    whisper with the guide's next turn. `director_fn(user) -> look`: for tests (default: director.decide, a
    Claude call logged to build_costs.jsonl)."""
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
    DC = cfg["live"].get("director") or {}
    nudged, offered, hold = 0, False, page.get("pitch_hold") or {}
    after_noted = False
    pitched = 0  # pitch nudges sent (the offer cue can take the first fallback's place, as on the page)
    ear = None
    if director:
        def decide(user: str) -> dict:
            if director_fn:
                return director_fn(user)
            S.check_cap(cfg, DIRECTOR_USD, "Claude director")
            got = live_director.decide(cfg, log_run, user)
            S.log_cost("claude director", got.get("cost_usd") or 0.0)
            return got
        ear = Earpiece(cfg, sim, profile=values["profile"], notes=notes, decide_fn=decide)
        ear.task = asyncio.create_task(ear.run())
    try:
        while True:
            new = await sim.wait_guide(since, GUIDE_WAIT_SECS)
            since = len(sim.turns)
            if not after_noted and any(c["name"] == "start_chapter" for c in sim.calls):
                # The page's note once the chapter starts (live.js afterStart): step back, or keep them company
                # if they've been chatting (engaged_lines lines, the last four averaging engaged_words words).
                after_noted = True
                turns = listener_turns(sim.timeline)
                recent = turns[-4:]
                P = cfg["live"]["page"]
                engaged = len(turns) >= P["engaged_turns"] and recent and sum(recent) / len(recent) >= P["engaged_words"]
                key = "started_company" if engaged else "started_step_back"
                call = next(c for c in sim.calls if c["name"] == "start_chapter")
                topic = str((call.get("args") or {}).get("topic") or "").strip() or "the one you pitched"
                await sim.note("instructions", notes[key].replace("{when}", "about 4 minutes").replace("{topic}", topic))
                if ear:
                    ear.on_started(step_back=key == "started_step_back")
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
            # With the earpiece up the offer clock is off and the nudges are its late fallbacks; down (two failures in
            # a row), the page's own clocks come back, in a lull only (live.js conversationCues, fb).
            up = director and not (ear and ear.down)
            nudges = ((DC.get("fallback") or {}).get("pitch_nudge_secs") if up else page.get("pitch_nudge_secs")) or []
            offer = {} if up else page.get("offer_nudge") or {}
            no_chapter = not any(c["name"] == "start_chapter" for c in sim.calls)
            told = explained_at(sim.timeline + ([{"t": sim.now(), "who": "guide", "text": sim.spoken_since(0)}]))
            if (offer and not offered and told is None and no_chapter and said >= 1 and not (director and new)
                    and sim.now() > (offer["after_secs"] if not new else offer["max_secs"])
                    and not (offer.get("offered") and sim.turns
                             and re.search(offer["offered"], sim.turns[-1]["text"], re.I))):
                await sim.note("instructions", notes["offer_nudge"])
                offered = True
            elif (nudged < len(nudges) and sim.now() > nudges[nudged] and no_chapter and not (director and new)
                    and not (up and ear and ear.sent_at is not None and sim.now() - ear.sent_at < (DC.get("min_gap_secs") or 0))
                    and not (told is not None and sim.now() - told < (hold.get("explained_secs") or 0))):
                nudged += 1
                if ear and not ear.down and told is None and not ear.offered and not offered:
                    offered = True  # with the earpiece up the first fallback is the offer cue (live.js conversationCues)
                    await sim.note("instructions", notes["offer_nudge"])
                else:
                    await sim.note("instructions", notes["pitch_nudge" if pitched == 0 else "pitch_nudge_again"])
                    pitched += 1
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
        if ear:
            await ear.stop()
        cost = await sim.close()
    heard_for(sim.timeline, sim.heard)
    if ear:
        next_turns(ear.looks, sim.turns)
    transcript = {
        "kind": "onboarding", "persona": name, "now": persona["now"], "description": persona["text"],
        "values": values, "session_id": sim.session_id, "stop": stop,
        "timeline": split_turns(sim.timeline), "calls": sim.calls, "errors": sim.errors,
        "cost": cost, "listener_usd": _listener_usd(before, name),
        **({"director": ear.looks} if ear else {}),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.json").write_text(json.dumps(transcript, indent=1, ensure_ascii=False), encoding="utf-8")
    return transcript


# --- earpiece probes ------------------------------------------------------------------------------

# Scripted listeners with a whisper injected at a scripted moment, the earpiece's model off: no Claude, only
# GPT-Live and the listener's TTS (scratch earpiece/DESIGN.md section 5). Steps: ("guide",) waits for the guide's
# turn; ("say", text); ("gap", secs) of silence; ("whisper", kind, about, note, topic) sends the note as the page
# would (topic: a pattern for a guide turn that takes it up); ("question", answers) answers until the guide's
# last turn ends in "?"; ("after", secs) waits until secs after the last whisper; ("mark", name) notes the time.
OFFER_TOPIC = r"\bwhat i do\b|\baudio chapters?\b|\bchapters?\b|\bwhat i make\b"
PROBES = {
    "A": {"what": "mid-story: a pitch whispered after the first of three story fragments (1.5 s gaps). Pass: no "
                  "pitch in the gaps; a pitch after the story, or none.",
          "steps": [("guide",), ("say", "Hey! Good, thanks. I just got back from a walk by the lake."), ("guide",),
                    ("say", "So on the walk I saw this huge bird, a peregrine falcon, sitting right on top of a lamp post"),
                    ("whisper", "when_it_fits", "pitch",
                     "When the falcon story is over, pitch a chapter on how peregrines came back from near extinction.",
                     r"\bchapters?\b|extinct|came back|comeback|\bDDT\b"),
                    ("gap", 1.5),
                    ("say", "and it just stared at me for a while, and then it dropped off the post and went straight "
                            "down after a pigeon"),
                    ("gap", 1.5), ("say", "and the pigeon got away, just barely. I had no idea they were that fast."),
                    ("mark", "story_end"), ("guide",), ("say", "Yeah, it was kind of wild."), ("guide",),
                    ("say", "Ha, maybe."), ("guide",)]},
    "B": {"what": "after the guide's question: an offer whispered right after a guide turn ending in a question. "
                  "Does it act after the answer, a turn later, or never?",
          "steps": [("guide",), ("say", "Hi! Pretty good. I'm cooking dinner right now."), ("guide",),
                    ("question", ["Ha, yeah, it's a lemon pasta thing.", "Mm, it's been a long week, honestly.",
                                  "Yeah, pretty much."]),
                    ("mark", "question"),
                    ("whisper", "when_it_fits", "offer",
                     "Once they answer, offer to tell them what you do, bridged from their dinner.", OFFER_TOPIC),
                    ("gap", 2.0), ("say", "Honestly, a bit of everything. Whatever's in the fridge."), ("guide",),
                    ("say", "Sure."), ("guide",), ("say", "Okay, cool."), ("guide",)]},
    "C": {"what": "skippable: an offer whispered while the listener keeps adding to a live thread. Does \"if it "
                  "doesn't come, let it go\" hold it past the next turn, until the thread winds down?",
          "steps": [("guide",), ("say", "Hi! Honestly I'm still buzzing from my daughter's school play tonight."),
                    ("guide",),
                    ("say", "She was cast as a tree, but halfway through she improvised a whole line, and the entire "
                            "audience burst out laughing."),
                    ("whisper", "when_it_fits", "offer",
                     "When the school play thread winds down, offer to tell them what you do.", OFFER_TOPIC),
                    ("guide",), ("say", "And afterwards she told her teacher she'd been planning it for weeks. Weeks!"),
                    ("guide",), ("say", "I mean, I was proud and mortified at the same time."), ("guide",),
                    ("mark", "wind_down"), ("say", "Yeah. Anyway, that was my night."), ("guide",), ("say", "Sure."),
                    ("guide",)]},
    "D": {"what": "supersede: a pitch on X, then 20 s later a whisper that replaces it with Y. Does X come up "
                  "after the second whisper?",
          "steps": [("guide",),
                    ("say", "Hi! I spent the afternoon reading about octopuses, and then somehow about old lighthouses."),
                    ("whisper", "when_it_fits", "pitch",
                     "Their spark is octopuses: pitch a chapter on how an octopus tastes with its arms.",
                     r"octopus|octopi|tentacle|\barms?\b"),
                    ("guide",), ("say", "Yeah, they're such strange animals."), ("guide",), ("after", 20),
                    ("whisper", "when_it_fits", "pitch",
                     "Drop the octopus idea: their real spark is the lighthouses. Pitch a chapter on the last "
                     "lighthouse keepers.", r"lighthouse|keeper"),
                    ("say", "Mm, I don't know."), ("guide",), ("say", "The lighthouse stuff was interesting too, actually."),
                    ("guide",), ("say", "Hm, maybe."), ("guide",)]},
}


def probe_result(whispers: list[dict], marks: dict, turns: list[dict]) -> list[dict]:
    """Per whisper: the guide's next turn, the first turn that takes it up (its topic), the turns before each
    mark, and for a later whisper how often an earlier whisper's topic still came up after it."""
    out = []
    for i, w in enumerate(whispers):
        after = [x for x in turns if x["t"] >= w["t"]]
        nxt = after[0] if after else None
        took = next(((n + 1, x) for n, x in enumerate(after) if re.search(w["topic"], x["text"], re.I)), None)
        rec = {**w, "next_turn_t": nxt["t"] if nxt else None, "next_turn": nxt["text"] if nxt else None,
               "took_up": ({"turn": took[0], "t": took[1]["t"], "after_secs": round(took[1]["t"] - w["t"], 1),
                            "text": took[1]["text"]} if took else None),
               "turns_before": {k: sum(1 for x in after if x["t"] < v) for k, v in marks.items() if v > w["t"]}}
        if took:
            rec["took_up_before"] = {k: took[1]["t"] < v for k, v in marks.items() if v > w["t"]}
        if i:
            rec["earlier_topic_after"] = sum(1 for x in after for e in whispers[:i] if re.search(e["topic"], x["text"], re.I))
        out.append(rec)
    return out


async def probe(cfg: dict, name: str, out_dir: Path, *, handoff: str = "Sam", max_minutes: float = 2.5,
                sim_kwargs: dict | None = None, label: str | None = None) -> dict:
    """One earpiece probe by voice (PROBES[name]); returns (and saves) its result as probe-<label>.json."""
    spec, label = PROBES[name], label or name
    notes = live.notes()
    values = first_meeting_values(day_part())
    sim = S.Sim(cfg, live.session_config(cfg, values, transport="websocket", greet=False), label=f"probe {label}",
                tool_handler=S.FakeTools(), max_minutes=max_minutes, audio_out=out_dir / f"probe-{label}.guide.wav",
                events_path=out_dir / f"probe-{label}.events.jsonl", **{**(sim_kwargs or {}), "greet": False})
    await sim.connect()
    await sim.note("commentary", notes["handoff"].replace("{name}", handoff))
    since, whispers, marks, done = 0, [], {}, []
    try:
        for step in spec["steps"]:
            if sim.closed.is_set() or sim.now() > max_minutes * 60 - 10:
                break
            op = step[0]
            if op == "guide":
                await sim.wait_guide(since, GUIDE_WAIT_SECS)
                since = len(sim.turns)
            elif op == "say":
                await sim.say(step[1])
            elif op == "gap":
                await asyncio.sleep(step[1])
            elif op == "after" and whispers:
                await asyncio.sleep(max(0.0, whispers[-1]["t"] + step[1] - sim.now()))
            elif op == "mark":
                marks[step[1]] = sim.now()
            elif op == "question":
                for answer in step[1]:
                    if sim.turns and sim.turns[-1]["text"].rstrip().endswith("?"):
                        break
                    await sim.say(answer)
                    await sim.wait_guide(since, GUIDE_WAIT_SECS)
                    since = len(sim.turns)
            elif op == "whisper":
                got = {"kind": step[1], "about": step[2], "note": step[3], "changed": True}
                key, text = whisper_note(notes, got)
                await sim.note("instructions", text)
                whispers.append({"t": sim.now(), "kind": step[1], "about": step[2], "note": step[3], "template": key,
                                 "topic": step[4], "guide_speaking": sim.speaking, "listener_speaking": bool(sim.speech)})
            done.append(op)
    finally:
        cost = await sim.close()
    result = {"kind": "probe", "probe": name, "what": spec["what"], "session_id": sim.session_id,
              "steps_done": len(done), "steps": len(spec["steps"]), "marks": marks,
              "whispers": probe_result(whispers, marks, sim.turns),
              "timeline": split_turns(sim.timeline), "calls": sim.calls, "errors": sim.errors, "cost": cost}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"probe-{label}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return result


def probe_summary(label: str, result: dict) -> str:
    c = result["cost"]
    lines = [f"probe {label}: {result['what']}",
             f"  {result['steps_done']} of {result['steps']} steps, {c['seconds']:.0f} s connected, GPT-Live ${c['usd']:.3f}"]
    for w in result["whispers"]:
        t = lambda v: f"{int(v) // 60}:{int(v) % 60:02d}"  # noqa: E731
        nxt = (f"next turn {w['next_turn_t'] - w['t']:.1f} s later: \"{(w['next_turn'] or '')[:70]}\""
               if w.get("next_turn_t") is not None else "no guide turn after it")
        took = (f"taken up at turn {w['took_up']['turn']} ({w['took_up']['after_secs']} s): \"{w['took_up']['text'][:70]}\""
                if w.get("took_up") else "never taken up")
        lines.append(f"  {t(w['t'])} whisper {w['about']}: {nxt}; {took}")
        for k, n in (w.get("turns_before") or {}).items():
            lines.append(f"    guide turns before {k}: {n}" + (f"; taken up before it: {w['took_up_before'][k]}"
                                                                  if k in (w.get("took_up_before") or {}) else ""))
        if "earlier_topic_after" in w:
            lines.append(f"    the earlier whisper's topic came up {w['earlier_topic_after']} times after this one")
    return "\n".join(lines)


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


def earpiece_summary(looks: list[dict]) -> list[str]:
    """The earpiece's looks by trigger, and each whisper with when the guide's next turn began after it."""
    by: dict[str, int] = {}
    for x in looks:
        by[x.get("trigger") or "?"] = by.get(x.get("trigger") or "?", 0) + 1
    errors = sum(1 for x in looks if x.get("error"))
    out = [f"  earpiece: {len(looks)} looks ({', '.join(f'{k} {v}' for k, v in by.items()) or 'none'})"
           + (f", {errors} failed" if errors else "")]
    for x in looks:
        if x.get("sent_t") is not None:
            after = (f"next turn {x['next_turn_t'] - x['sent_t']:.1f} s later: \"{(x.get('next_turn') or '')[:80]}\""
                     if x.get("next_turn_t") is not None else "no guide turn after it")
            out.append(f"    {int(x['sent_t']) // 60}:{int(x['sent_t']) % 60:02d} {x.get('kind')}/{x.get('about')} "
                       f"\"{x.get('note')}\"; {after}")
        elif x.get("note") and x.get("changed"):
            out.append(f"    (dropped) {x.get('kind')}/{x.get('about')} \"{x.get('note')}\"")
    return out


def summary(name: str, transcript: dict, g: dict | None) -> str:
    c = transcript["cost"]
    lines = [f"{name}: stop={transcript['stop']}, {c['seconds']:.0f} s connected, GPT-Live ${c['usd']:.3f}"
             f" (backend {c['backend']['input']} in / {c['backend']['output']} out), "
             f"tools: {', '.join(x['name'] for x in transcript['calls']) or 'none'}"]
    k = pace(transcript)
    at = lambda v: "never" if v is None else f"{int(v) // 60}:{int(v) % 60:02d}"  # noqa: E731
    lines.append(f"  said what it does at {at(k['explained'])}, pitched at {at(k['pitched'])}, started at "
                 f"{at(k['started'])}; {k['questions']} of its {k['turns']} turns up to then asked something")
    looks = transcript.get("director")
    if looks is not None:
        lines.extend(earpiece_summary(looks))
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
        if ear:
            await ear.stop()
        cost = await sim.close()
    heard_for(sim.timeline, sim.heard)
    if ear:
        next_turns(ear.looks, sim.turns)
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
