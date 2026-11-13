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

The transcript is written as it happens to listener/sessions/<id>.jsonl, and each
call's tokens and cost to listener/sessions/log.jsonl. A session stops talking
past guide.max_session_usd.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

from . import llm
from .common import PROMPTS_DIR, ROOT, Run, StageError, listener_profile, load_prompt

TOOLS = json.loads((PROMPTS_DIR / "guide_tools.json").read_text(encoding="utf-8"))
# What the Messages API takes: a tool may carry more for the voice agent (expects_response).
API_TOOLS = [{k: t[k] for k in ("name", "description", "input_schema")} for t in TOOLS]
FAST_ONLY = ("scout_topic",)  # offered only in a conversation that has the fast hooks
SEED_STAGES_DONE = "05_brief.md"  # once the brief exists, no later stage reads the seed or its notes


def listener_dir(cfg: dict) -> Path:
    return ROOT / cfg.get("listener_dir", "listener")


def draw_names(cfg: dict, rng: random.Random) -> list[str]:
    names = [n.strip() for n in load_prompt("guide_names").splitlines() if n.strip()]
    return rng.sample(names, min(cfg["guide"]["name_draw"], len(names)))


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
                     "problem": "stopped"}.get(state, state)}
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
        self.id = time.strftime("%Y%m%d-%H%M%S")
        self.transcript = sessions / f"{self.id}.jsonl"
        self.log_run = Run(name="sessions", root=sessions)
        self.messages: list[dict] = []
        self.run: str | None = None  # the chapter started in this session
        known = identity(cfg)
        names = [] if known else draw_names(cfg, rng or random.Random())
        now = status() or {}
        chapter_now = (json.dumps({"run": now.get("run"), **plain_status(now)}, ensure_ascii=False)
                       if now.get("state") in ("making", "ready", "problem") else "(none)")
        # The prompt's values: the typed chat renders them here; the voice agent (stages/voice.py) gets
        # them as dynamic variables, since its prompt is prompts/guide.md with the same {{placeholders}}.
        self.values = {
            "identity": known or "(none: this is your first meeting)",
            "names": ", ".join(names) or "(you already have a name: see above)",
            "profile": listener_profile(cfg), "history": recent_history(cfg), "chapter_now": chapter_now,
            "minutes": str(round(sum((cfg.get("status_minutes") or {}).values())) or 25),
        }
        self.system = load_prompt("guide", **self.values)
        self.record("session", names=names, returning=bool(known))

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
            return plain_status(self.status_fn() or {"state": "idle"})
        return {"error": f"no tool named {name}"}

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
        if self.fast and (out := self.fast.add_note(root, note, kind)):
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
