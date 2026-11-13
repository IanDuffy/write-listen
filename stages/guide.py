"""The guide's conversation, in text (Phase 1, milestone 3; docs/phase1-onboarding.md).

One session is one conversation with the listener. Each turn sends the persona
prompt (prompts/guide.md) and the conversation so far to models.guide, with the
three tools of prompts/guide_tools.json, runs any tool the guide calls, and
returns what the guide says. Milestone 4 moves the same prompt and tools onto the
ElevenLabs voice agent, where the tools become client tools in the page.

    start_chapter(topic, why, notes)  starts an unattended seed run (the server's start)
    add_note(note)                     appends to the run's 00_source/notes.md, which the
                                       stages that read the seed and have not run yet use
    get_status()                       the run's status.json, in plain terms
    scout_topic(topic)                 fast runs only (docs/fast-start.md): look into a likely topic
                                       early, silently; offered when the server supplies `fast`
    step_back()                        the guide goes quiet (voice: the page ends the connection
                                       and listens locally for the listener; server.py, page/)

The transcript is written as it happens to listener/sessions/<id>.jsonl, and each
call's tokens and cost to listener/sessions/log.jsonl. A session stops talking
past guide.max_session_usd.
"""

from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path

from . import llm
from .common import PROMPTS_DIR, ROOT, Run, StageError, listener_profile, load_prompt

TOOLS = json.loads((PROMPTS_DIR / "guide_tools.json").read_text(encoding="utf-8"))
# What the Messages API takes: a tool may carry more for the voice agent (expects_response).
API_TOOLS = [{k: t[k] for k in ("name", "description", "input_schema")} for t in TOOLS]
FAST_ONLY = ("scout_topic",)  # offered only in a conversation that has the fast hooks
SEED_STAGES_DONE = "05_brief.md"  # once the brief exists, no later stage reads the seed or its notes
NO_EARLIER = "(nothing yet: the conversation is just starting)"
STEP_BACK_NOTE = ("You are stepping back. If you have not said your parting words"
                  " yet, say them now, in a few words, once. Otherwise produce no text.")
EARLIER_CHARS = 6000  # the end of a long conversation is what the guide needs when it comes back


def listener_dir(cfg: dict) -> Path:
    return ROOT / cfg.get("listener_dir", "listener")


def draw_names(cfg: dict, rng: random.Random) -> list[str]:
    names = [n.strip() for n in load_prompt("guide_names").splitlines() if n.strip()]
    return rng.sample(names, min(cfg["guide"]["name_draw"], len(names)))


def draw_sparks(cfg: dict, rng: random.Random) -> list[str]:
    sparks = [n.strip() for n in load_prompt("guide_sparks").splitlines() if n.strip()]
    return rng.sample(sparks, min(cfg["guide"].get("spark_draw", 3), len(sparks)))


def identity(cfg: dict) -> str | None:
    """listener/guide.md, the guide's own identity for this listener, once they have met."""
    p = listener_dir(cfg) / "guide.md"
    return p.read_text(encoding="utf-8").strip() if p.exists() else None


def recent_history(cfg: dict, n: int = 3) -> str:
    """The last n chapters from listener/history.md ('## ' sections)."""
    p = listener_dir(cfg) / "history.md"
    if not p.exists():
        return "(none yet)"
    parts = p.read_text(encoding="utf-8").split("\n## ")
    return "\n## ".join(parts[-n:]).strip() if len(parts) > 1 else parts[0].strip()


def plain_status(status: dict) -> dict:
    """What get_status tells the guide: the state, the stage in plain words, minutes left, title."""
    state = status.get("state", "idle")
    out = {"state": {"idle": "no chapter yet", "making": "being made", "ready": "ready",
                     "problem": "stopped", "stopped": "stopped by the listener"}.get(state, state)}
    if state == "making":
        out["stage"] = status.get("label") or "starting"
        if status.get("minutes_left") is not None:
            out["minutes_left"] = status["minutes_left"]
    if status.get("title"):
        out["title"] = status["title"]
    if state == "problem" and status.get("message"):
        out["problem"] = status["message"]
    if status.get("mode") == "fast":  # a fast run: its sections too, and whether listening can start
        from faststart import guide_hooks
        out = guide_hooks.plain(out, status)
    return out


def day_part(t: time.struct_time | None = None) -> str:
    """'Thursday evening': enough for a hello that belongs to the moment."""
    t = t or time.localtime()
    part = ("night" if t.tm_hour < 5 else "morning" if t.tm_hour < 12 else "afternoon" if t.tm_hour < 17
            else "evening" if t.tm_hour < 22 else "night")
    return f"{time.strftime('%A', t)} {part}"


def chapter_now(status: dict, plain=plain_status) -> str:
    """The chapter being made or just finished, for the prompt's <chapter_now>. `plain`: how the
    status is put in words (a Session's _plain adds what a fast run changes)."""
    if status.get("state") in ("making", "ready", "problem", "stopped"):
        return json.dumps({"run": status.get("run"), **plain(status)}, ensure_ascii=False)
    return "(none)"


def earlier_text(transcript: Path, limit: int = EARLIER_CHARS) -> str:
    """The conversation so far as the guide should read it when it comes back: what each said
    (its own lines as "you"), the tools it called, and the page's notes. The last `limit` characters."""
    lines = []
    for raw in transcript.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        who, text = rec.get("who"), (rec.get("text") or "").strip()
        if who in ("listener", "guide", "page") and text:
            lines.append(f"{'you' if who == 'guide' else who}: {text}")
        elif who == "tool" and rec.get("name") == "step_back":
            lines.append("(you stepped back)")
        elif who == "tool":
            lines.append(f"(you called {rec.get('name')}: {json.dumps(rec.get('input'), ensure_ascii=False)} -> "
                         f"{json.dumps(rec.get('result'), ensure_ascii=False)})")
    text = "\n".join(lines)
    if len(text) > limit:
        text = "(the start is left out)\n" + text[-limit:].split("\n", 1)[-1]
    return text or NO_EARLIER


class Session:
    """One conversation. `start(topic, why, notes)` starts a chapter and returns its run name
    (raising to refuse); `status()` returns the current run's status.json. `fast`: what a fast run
    changes (faststart.guide_hooks.Hooks); without it the conversation is as it was before them."""

    def __init__(self, cfg: dict, start, status, runs_dir: Path, rng: random.Random | None = None, fast=None):
        self.cfg, self.start_fn, self.status_fn, self.runs_dir = cfg, start, status, runs_dir
        self.fast = fast
        self.tools = [t for t in API_TOOLS if fast is not None or t["name"] not in FAST_ONLY]
        sessions = listener_dir(cfg) / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        self.id = stamp = time.strftime("%Y%m%d-%H%M%S")
        n = 1
        while (sessions / f"{self.id}.jsonl").exists():  # two in one second (voice failed, typing began): never share a file
            n += 1
            self.id = f"{stamp}-{n}"
        self.transcript = sessions / f"{self.id}.jsonl"
        self.log_run = Run(name="sessions", root=sessions)
        self.messages: list[dict] = []
        self.run: str | None = None  # the chapter started in this session
        known = identity(cfg)
        rng = rng or random.Random()
        # A returning guide has its name in its identity and is not offered others. (A first meeting
        # that ended before it said a name leaves no identity: prompts/memory.md.)
        names = self.names = [] if known else draw_names(cfg, rng)
        self.voice_conversations: list[str] = []  # ElevenLabs conversation ids: one per connection
        # The prompt's values: the typed chat renders them here; the voice agent (stages/voice.py) gets
        # them as dynamic variables, since its prompt is prompts/guide.md with the same {{placeholders}}.
        self.values = {
            "identity": known or "(none: this is your first meeting)",
            "names": ", ".join(names) or "(you already have a name: see above)",
            "sparks": ", ".join(draw_sparks(cfg, rng)),
            "profile": listener_profile(cfg), "history": recent_history(cfg),
            "chapter_now": chapter_now(status() or {}, self._plain),
            "minutes": str(round(sum((cfg.get("status_minutes") or {}).values())) or 25),
            "now": day_part(), "earlier": NO_EARLIER,
        }
        if fast is not None:  # fast chapters: the minutes to the first audio when measured, else no figure
            self.values["minutes"] = fast.minutes()
        self.system = load_prompt("guide", **self.values)
        self.record("session", names=names, returning=bool(known))

    def resume_values(self) -> dict:
        """The prompt's values for a new voice connection in the same conversation (the guide stepped
        back and is coming back): the conversation so far, and the chapter as it stands now."""
        values = {**self.values, "chapter_now": chapter_now(self.status_fn() or {}, self._plain), "now": day_part(),
                  "earlier": earlier_text(self.transcript)}
        said = self.name_said()
        if said:  # a long conversation's start is cut from "earlier": the name it chose must survive that
            values["names"] = f"(you have already told them your name is {said}: keep it)"
        return values

    def name_said(self) -> str | None:
        """The drawn name the guide has used in this conversation, if any (its first to appear)."""
        if not self.names:
            return None
        spoken = " ".join(json.loads(x).get("text", "") for x in self.transcript.read_text(encoding="utf-8").splitlines()
                          if '"who": "guide"' in x)
        found = [(m.start(), n) for n in self.names if (m := re.search(rf"\b{re.escape(n)}\b", spoken))]
        return min(found)[1] if found else None

    def record(self, who: str, **fields) -> None:
        line = json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "who": who, **fields}, ensure_ascii=False)
        with open(self.transcript, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def spent(self) -> float:
        return sum(rec.get("cost_usd", 0) or 0 for rec in self.log_run.read_log() if rec.get("session") == self.id)

    def say(self, text: str | None = None) -> str:
        """The listener's turn (None: they have just opened the page); returns the guide's reply."""
        cap = self.cfg["guide"]["max_session_usd"]
        if self.spent() >= cap:
            raise StageError(f"this conversation has reached its ${cap:.2f} limit (guide.max_session_usd).")
        content = text.strip() if text and text.strip() else load_prompt("guide_opened").strip()
        self.messages.append({"role": "user", "content": content})
        self.record("listener" if text and text.strip() else "page", text=content)
        said: list[str] = []  # everything the guide says this turn, before and after its tool calls
        for _ in range(self.cfg["guide"]["max_tool_rounds"] + 1):
            res = llm.call(self.log_run, self.cfg, "guide", role="guide", system=self.system,
                           messages=self.messages, tools=self.tools, label="guide", preapproved=True,
                           allow_empty=bool(said) or len(self.messages) > 1 and self._after_tools(),
                           log_extra={"session": self.id})
            content = llm.echo_after_fallback(res.content)  # what the API accepts back after a fallback
            if content:  # an empty assistant message is a 400 on every later turn
                self.messages.append({"role": "assistant", "content": content})
            text = "".join(getattr(b, "text", "") for b in content if getattr(b, "type", None) == "text").strip()
            if text:
                said.append(text)
            uses = [b for b in content if getattr(b, "type", None) == "tool_use"]
            if res.message.stop_reason != "tool_use" or not uses:
                reply = " ".join(said) or self._quiet_line()
                if not reply:
                    raise StageError("guide: the model returned no text.")
                self.record("guide", text=reply)
                return reply
            results = []
            for b in uses:  # every tool_use gets its tool_result, or the next request is a 400
                try:
                    out = self.tool(b.name, dict(b.input or {}))
                except Exception as e:
                    out = {"error": f"{type(e).__name__}: {e}"}
                self.record("tool", name=b.name, input=b.input, result=out)
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": json.dumps(out, ensure_ascii=False)})
            self.messages.append({"role": "user", "content": results})
            if said and any(b.name == "step_back" for b in uses):
                # It has said its parting words and stepped back: the turn is over. Asked again, the
                # model has nothing to say and calls the tool again, round after round.
                self.record("guide", text=" ".join(said))
                return " ".join(said)
        self.record("guide", text=" ".join(said))
        if said:
            return " ".join(said)
        raise StageError("guide: too many tool calls in one turn.")

    def _quiet_line(self) -> str:
        """When the guide says nothing after its tools ran, a plain line from what they did, so the
        listener still hears that the chapter started (prompts/guide_quiet.md)."""
        last = self.messages[-1]["content"] if self.messages else None
        if not isinstance(last, list):
            return ""
        results = [json.loads(x["content"]) for x in last if isinstance(x, dict) and x.get("type") == "tool_result"]
        lines = dict(ln.split(": ", 1) for ln in load_prompt("guide_quiet").splitlines() if ": " in ln)
        for r in results:
            if r.get("started") is True and "minutes" not in r:  # a fast run: no time unless one was measured
                return lines["started_fast"]
            if r.get("started") is True:
                return lines["started"].replace("{minutes}", str(r.get("minutes", 25)))
            if r.get("added") is True:
                return lines["noted"]
            if r.get("quiet") is True:
                return lines["quiet"]
        if results and all("scouting" in r for r in results):  # scout_topic is silent: never "Done."
            return lines["listening"]
        return lines["done"] if results else ""

    def _after_tools(self) -> bool:
        last = self.messages[-1]["content"]
        return isinstance(last, list) and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in last)

    # --- the three tools -------------------------------------------------------------

    def tool(self, name: str, args: dict) -> dict:
        if name == "start_chapter":
            return self.start_chapter(args.get("topic") or "", args.get("why") or "", args.get("notes") or "",
                                      args.get("listener_said") or "")
        if name == "add_note":
            return self.add_note(args.get("note") or "", args.get("kind"))
        if name == "scout_topic":
            return self.fast.scout_topic(args.get("topic") or "") if self.fast else {"scouting": False}
        if name == "get_status":
            return self._plain(self.status_fn() or {"state": "idle"})
        if name == "step_back":  # typed, nothing else happens; by voice, the page ends the connection
            return {"quiet": True, "note": STEP_BACK_NOTE}
        return {"error": f"no tool named {name}"}

    def _plain(self, status: dict) -> dict:
        out = plain_status(status)
        if status.get("mode") == "fast":  # the wait rules, and no countdown the run can't back
            from faststart import guide_hooks
            out = guide_hooks.for_guide(out, status, self.cfg, self.runs_dir)
        return out

    def start_chapter(self, topic: str, why: str, notes: str, listener_said: str = "") -> dict:
        if not topic.strip():
            return {"started": False, "reason": "The topic was empty."}
        now = self.status_fn() or {}
        if (self.run and now.get("run") == self.run and now.get("state") == "problem"
                and now.get("retryable") is not False):
            return {"started": False, "reason": "This conversation's chapter stopped. Don't start another: the "
                                                "page's Try again button continues it (if it shows one)."}
        try:
            self.run = self.start_fn(topic, why, notes, listener_said)
        except Exception as e:  # busy, a bad seed: the guide says so plainly
            return {"started": False, "reason": str(getattr(e, "detail", None) or e)}
        from . import memory
        memory.save_seed(self.cfg, self.runs_dir / self.run)  # listener/seeds/<run>.md
        if self.fast and (out := self.fast.started(self.runs_dir / self.run)):
            return out  # a fast run: no time unless earlier runs measured one, and how to wait
        return {"started": True, "minutes": round(sum((self.cfg.get("status_minutes") or {}).values())) or 25}

    def _current_run(self) -> str | None:
        if self.run:
            return self.run
        now = self.status_fn() or {}
        return now.get("run") if now.get("state") == "making" else None

    def add_note(self, note: str, kind: str | None = None) -> dict:
        run = self._current_run()
        if not note.strip():
            return {"added": False, "reason": "The note was empty."}
        if not run:
            return {"added": False, "reason": "No chapter is being made yet: put it in start_chapter's notes."}
        root = self.runs_dir / run
        hooks = self.fast
        if hooks is None:  # a conversation without them (cli.py chat) beside a fast run: routed by the run
            from faststart import guide_hooks
            hooks = guide_hooks.Hooks(self.cfg, self.runs_dir, self.status_fn) if guide_hooks.is_fast(root) else None
        if hooks and (out := hooks.add_note(root, note, kind)):
            return out  # a fast run: listener_notes.md, with the kind and the section it was said in
        p = root / "00_source" / "notes.md"
        if not p.exists():
            p.write_text(load_prompt("seed_notes").strip() + "\n\n", encoding="utf-8")
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"- {' '.join(note.split())}\n")
        if (root / SEED_STAGES_DONE).exists():
            return {"added": True, "effect": "The chapter is already planned, so this one may not change it; "
                                             "it is kept with the chapter."}
        return {"added": True, "effect": "The stages still to come will take it into account."}
