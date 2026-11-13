"""The guide's voice (Phase 1, milestone 4; docs/phase1-onboarding.md, docs/elevenlabs-agents-check.md).

The voice guide is an ElevenLabs agent whose prompt is prompts/guide.md and whose
client tools come from prompts/guide_tools.json, so the typed chat (stages/guide.py) and
the voice share one persona. The repo is the source of truth: `sync_agent` creates the
tools and the agent, or updates them when the prompt, tools or config.yaml's `voice`
section change (a hash in listener/voice.json). Nothing about the listener is stored on
ElevenLabs' side: each conversation gets the guide prompt's values as dynamic variables.

The agent is private: the server mints a signed URL per conversation (valid 15 minutes),
so the API key never reaches the page. The page runs the tools by calling the server,
which runs them in the conversation's guide.Session (same code as the typed chat).

`compare` sends the same scripted listener turns to the voice agent (text-only, its tools
mocked) and to the typed chat, for Ian's check of effort `none` against `low`.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .common import PROMPTS_DIR, ROOT, StageError
from .guide import STEP_BACK_NOTE, TOOLS, listener_dir

PLACEHOLDERS = ("identity", "names", "sparks", "profile", "history", "chapter_now", "minutes", "now", "earlier")
# The aside agent (ask mid-chapter, docs/ask-voice.md): the same guide, its aside mode and tools.
ASIDE_PLACEHOLDERS = ("identity", "profile", "chapter", "where", "just_said", "coming", "later",
                      "dossier", "ledger", "earlier_asides")
KINDS = {  # one sync for both agents; "guide" is onboarding's, unchanged
    "guide": {"prompt": "guide", "tools": None, "placeholders": PLACEHOLDERS, "state": "voice.json"},
    "aside": {"prompt": "guide_aside", "tools": "guide_aside_tools.json", "placeholders": ASIDE_PLACEHOLDERS,
              "state": "voice_aside.json"},
}
# The check-in agent (docs/fast-start.md, "When a section isn't ready"): the same guide while a fast
# run's next section is still being made. Its values come from faststart/checkin.py `context`.
CHECKIN_PLACEHOLDERS = ("identity", "profile", "chapter", "heard", "coming", "notes", "earlier_asides",
                        "earlier_openings", "mic")
KINDS["checkin"] = {"prompt": "guide_checkin", "tools": "guide_checkin_tools.json",
                    "placeholders": CHECKIN_PLACEHOLDERS, "state": "voice_checkin.json"}


def guide_prompt(kind: str = "guide") -> str:
    """prompts/guide.md (or the aside's prompt) below its '---' line, with its {{placeholders}} left
    in: ElevenLabs fills them from the conversation's dynamic variables (same syntax)."""
    text = (PROMPTS_DIR / f"{KINDS[kind]['prompt']}.md").read_text(encoding="utf-8")
    return text.split("\n---\n", 1)[1].strip() + "\n"


def _tools(kind: str) -> list[dict]:
    f = KINDS[kind]["tools"]
    return TOOLS if f is None else json.loads((PROMPTS_DIR / f).read_text(encoding="utf-8"))


def agent_name(cfg: dict, kind: str = "guide") -> str:
    if kind == "checkin":
        return cfg["fast"]["checkin"]["agent_name"]
    return cfg["voice"]["agent_name"] if kind == "guide" else cfg["ask"]["agent_name"]


def _el_schema(schema: dict) -> dict:
    """An Anthropic tool input_schema as ElevenLabs' parameter schema: no additionalProperties,
    and every property described (ElevenLabs uses the descriptions to fill the values)."""
    props = {k: {kk: vv for kk, vv in v.items() if kk != "additionalProperties"}
             for k, v in (schema.get("properties") or {}).items()}
    return {"type": "object", "properties": props, "required": list(schema.get("required") or [])}


def client_tools(cfg: dict, kind: str = "guide") -> list[dict]:
    """prompts/guide_tools.json (or the aside's tools) as ElevenLabs client tool configs."""
    return [{"type": "client", "name": t["name"], "description": t["description"],
             "parameters": _el_schema(t["input_schema"]), "expects_response": t.get("expects_response", True),
             "response_timeout_secs": cfg["voice"]["tool_timeout_secs"]} for t in _tools(kind)]


def conversation_config(cfg: dict, tool_ids: list[str], kind: str = "guide") -> dict:
    v = cfg["voice"]
    conv = {
        "agent": {
            # Empty: the guide waits, and the page then tells it the listener has arrived
            # (prompts/guide_opened.md, as the typed chat does), so its first line is its own.
            "first_message": "",
            "language": "en",
            "dynamic_variables": {"dynamic_variable_placeholders": {
                k: f"({k})" for k in KINDS[kind]["placeholders"]}},
            "prompt": {"prompt": guide_prompt(kind), "llm": v["llm"], "reasoning_effort": v["reasoning_effort"],
                       "tool_ids": tool_ids},
        },
        "tts": {"model_id": v["tts_model"], "voice_id": v.get("voice_id") or cfg["narration"]["voice_id"]},
        # turn_timeout: how long a silence the platform waits before sending the agent an empty turn
        # ("...") to answer. On the first voice conversation its default of 7 s had the guide talking to
        # an empty room every 15 s. -1 turns it off (accepted by the API, 2026-11-12; the docs give
        # 1-30); the page also steps the guide back after voice.rest_after_silence_secs.
        "turn": {"turn_eagerness": v["turn_eagerness"], "turn_timeout": v["turn_timeout"]},
        "conversation": {"max_duration_seconds": v["max_duration_seconds"]},
    }
    if kind == "aside":  # the page times silences itself (prompts/guide_aside.md), so the platform waits
        conv["turn"]["turn_timeout"] = cfg["ask"]["turn_timeout_secs"]
        conv["agent"]["prompt"]["reasoning_effort"] = cfg["ask"].get("reasoning_effort") or v["reasoning_effort"]
    if kind == "checkin":  # the same two settings, from fast.checkin: the page times the check-in's silence too
        conv["turn"]["turn_timeout"] = cfg["fast"]["checkin"]["turn_timeout_secs"]
        conv["agent"]["prompt"]["reasoning_effort"] = (cfg["fast"]["checkin"].get("reasoning_effort")
                                                       or v["reasoning_effort"])
    return conv


def platform_settings() -> dict:
    # Private: a conversation needs a signed URL. Text-only may be asked per conversation (voice-compare).
    return {"auth": {"enable_auth": True},
            "overrides": {"conversation_config_override": {"conversation": {"text_only": True}}}}


def state_path(cfg: dict, kind: str = "guide") -> Path:
    return listener_dir(cfg) / KINDS[kind]["state"]


def _client():
    from .tts import elevenlabs_client
    return elevenlabs_client()


def _digest(cfg: dict, kind: str = "guide") -> str:
    # The whole agent as the code would send it (tool ids aside), so any change to the prompt, tools,
    # config or this module's settings is synced.
    blob = json.dumps({"tools": client_tools(cfg, kind), "conversation": conversation_config(cfg, [], kind),
                       "platform": platform_settings(), "name": agent_name(cfg, kind)}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _save_state(p: Path, agent_id: str | None, tool_ids: dict, digest: str | None) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"agent_id": agent_id, "tool_ids": tool_ids, "digest": digest,
                             "synced": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=1), encoding="utf-8")


def sync_agent(cfg: dict, client=None, force: bool = False, kind: str = "guide") -> str:
    """The guide agent's id, after creating or updating its tools and itself to match the repo.

    Ids are saved as soon as each object exists, so a sync that fails partway is resumed (updated),
    never repeated (no duplicate tools). Only a 404 (deleted on the dashboard) makes a new object;
    any other error stops, leaving the saved state for the next try."""
    from elevenlabs.types import ToolRequestModel
    from elevenlabs.types.tool_request_model_tool_config import ToolRequestModelToolConfig_Client

    from .tts import ElevenLabsError, el_call

    client = client or _client()
    p = state_path(cfg, kind)
    state = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    digest = _digest(cfg, kind)
    if state.get("agent_id") and state.get("digest") == digest and not force:
        return state["agent_id"]

    agent_id, tool_ids = state.get("agent_id"), dict(state.get("tool_ids") or {})
    for tool in client_tools(cfg, kind):
        req = ToolRequestModel(tool_config=ToolRequestModelToolConfig_Client(**tool))
        tid = tool_ids.get(tool["name"])
        if tid:
            try:
                el_call(client.conversational_ai.tools.update, tid, request=req)
                continue
            except ElevenLabsError as e:
                if e.status != 404:
                    raise
                print(f"  tool {tool['name']} ({tid}) is gone; creating it again")
        tool_ids[tool["name"]] = el_call(client.conversational_ai.tools.create, request=req).id
        _save_state(p, agent_id, tool_ids, state.get("digest"))  # not the new digest: the agent isn't synced yet

    conv = conversation_config(cfg, [tool_ids[t["name"]] for t in _tools(kind)], kind)
    if agent_id:
        try:
            el_call(client.conversational_ai.agents.update, agent_id, conversation_config=conv,
                    platform_settings=platform_settings(), name=agent_name(cfg, kind))
        except ElevenLabsError as e:
            if e.status != 404:
                raise
            print(f"  agent {agent_id} is gone; creating a new one")
            agent_id = None
    if not agent_id:
        agent_id = el_call(client.conversational_ai.agents.create, conversation_config=conv,
                           platform_settings=platform_settings(), name=agent_name(cfg, kind),
                           tags=["write-listen"]).agent_id
    _save_state(p, agent_id, tool_ids, digest)
    print(f"  voice agent {agent_id} ({kind}): {cfg['voice']['llm']}, effort {cfg['voice']['reasoning_effort']}, "
          f"{cfg['voice']['tts_model']}, voice {conv['tts']['voice_id']}")
    return agent_id


def signed_url(cfg: dict, client=None, kind: str = "guide") -> str:
    from .tts import el_call
    client = client or _client()
    return el_call(client.conversational_ai.conversations.get_signed_url,
                   agent_id=sync_agent(cfg, client, kind=kind)).signed_url


def dynamic_variables(session, resumed: bool = False) -> dict[str, str]:
    """The guide prompt's values for one connection, as strings: guide.Session.values, or for a
    guide coming back after stepping back, the same with the conversation so far."""
    values = session.resume_values() if resumed else session.values
    return {k: str(values[k]) for k in PLACEHOLDERS}


def save_conversation(cfg: dict, session_id: str, conversation_id: str, client=None, wait_s: int = 120,
                      n: int = 1) -> dict | None:
    """After a voice conversation: ElevenLabs' record of it (transcript, tool calls, per-turn latency,
    cost) to listener/sessions/<id>.voice.json, and its cost to the sessions log. Polls until done.
    A conversation in which the guide stepped back has several connections: the n-th after the
    first goes to <id>.voice.<n>.json."""
    from .tts import el_call
    client = client or _client()
    deadline = time.monotonic() + wait_s
    rec, last_error = None, None
    while time.monotonic() < deadline:
        try:
            rec = el_call(client.conversational_ai.conversations.get, conversation_id)
        except StageError as e:  # a blip: keep polling until the deadline
            last_error = e
        if getattr(rec, "status", None) in ("done", "failed"):
            break
        time.sleep(5)
    if rec is None:
        if last_error:
            raise last_error
        return None
    data = rec.model_dump(mode="json") if hasattr(rec, "model_dump") else dict(rec)
    sessions = listener_dir(cfg) / "sessions"
    name = f"{session_id}.voice.json" if n <= 1 else f"{session_id}.voice.{n}.json"
    (sessions / name).write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    meta = data.get("metadata") or {}
    final = data.get("status") in ("done", "failed")
    line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "stage": "voice", "session": session_id,
            "provider": "elevenlabs", "conversation_id": conversation_id, "status": data.get("status"),
            "final": final, "seconds": meta.get("call_duration_secs"), "credits": meta.get("cost"),
            # Unknown until ElevenLabs has processed the call: None, never a made-up 0.
            "cost_usd": meta.get("cost_fiat") if final else None}
    with open(sessions / "log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line, default=str) + "\n")
    return data


# --- effort `none` (voice) against `low` (typed): the same scripted turns to both -------------

MOCK_RESULTS = {  # the voice agent's tools, mocked: a comparison never starts a real chapter
    "start_chapter": {"started": True, "minutes": 25},
    "add_note": {"added": True, "effect": "The stages still to come will take it into account."},
    "get_status": {"state": "being made", "stage": "Researching", "minutes_left": 18},
    "scout_topic": {"scouting": True},  # fast runs' early scout (docs/fast-start.md): silent, never a real search here
    "step_back": {"quiet": True, "note": STEP_BACK_NOTE},
}


QUIET_SECONDS, TURN_SECONDS = 4, 60  # a turn is over after this long with nothing new; it fails after this


def _voice_replies(cfg: dict, session, turns: list[str], client) -> list[dict]:
    # cfg is the real config: the agent's state (listener/voice.json) must be the listener's own.
    """One text-only conversation with the voice agent: the page's opening note, then each turn.
    Returns [{'said', 'reply', 'seconds', 'tools'}]."""
    import threading

    from elevenlabs.conversational_ai.conversation import ClientTools, Conversation, ConversationInitiationData

    from .common import load_prompt
    # Every event is tagged with the turn it arrived in, so a late reply is never counted in the next turn.
    lock, events, turn, done, out = threading.Lock(), [], [0], threading.Event(), []

    def add(kind: str, value: str) -> None:
        with lock:
            events.append((turn[0], kind, time.monotonic(), value))
        done.set()

    # A tool the agent does not wait for (scout_topic) gets no reply after it: never wait for one.
    unawaited = {t["name"] for t in _tools("guide") if t.get("expects_response") is False}
    tools = ClientTools()
    for name, result in MOCK_RESULTS.items():
        tools.register(name, lambda params, _n=name, _r=result: (add("tool", _n), json.dumps(_r))[1])

    def on_reply(text: str):
        add("reply", text)

    conv = Conversation(client, sync_agent(cfg, client), requires_auth=True, audio_interface=None,
                        config=ConversationInitiationData(
                            conversation_config_override={"conversation": {"text_only": True}},
                            dynamic_variables=dynamic_variables(session)),
                        client_tools=tools, callback_agent_response=on_reply)
    conv.start_session()
    deadline = time.monotonic() + 20
    while getattr(conv, "_conversation_id", None) is None:  # connected once the session metadata arrives
        if time.monotonic() > deadline:
            conv.end_session()
            raise StageError("voice agent: the conversation did not connect within 20 s.")
        time.sleep(0.2)
    try:
        for said in [load_prompt("guide_opened").strip()] + turns:
            with lock:
                turn[0] += 1
                n = turn[0]
            t0 = time.monotonic()
            conv.send_user_message(said)
            while True:  # done when 4 s pass with nothing new and every tool call has had a reply after it
                done.clear()
                with lock:
                    mine = [e for e in events if e[0] == n]
                # A tool the agent does not wait on (scout_topic) and step_back, which is followed by
                # silence by design: never wait for words after them.
                tools_pending = any(k == "tool" and v not in unawaited and v != "step_back"
                                    and not any(k2 == "reply" and t2 > t for _, k2, t2, _ in mine)
                                    for _, k, t, v in mine)
                quiet = mine and time.monotonic() - mine[-1][2] >= QUIET_SECONDS and not tools_pending
                if quiet:
                    break
                if time.monotonic() - t0 > TURN_SECONDS:
                    raise StageError(f"voice agent: no complete reply within {TURN_SECONDS} s to {said[:60]!r}")
                done.wait(0.5)
            replies = [e for e in mine if e[1] == "reply"]
            out.append({"said": said, "reply": " ".join(e[3] for e in replies),
                        "seconds": round(replies[0][2] - t0, 2) if replies else None,
                        "tools": [e[3] for e in mine if e[1] == "tool"]})
    finally:
        conv.end_session()
        cid = conv.wait_for_session_end()
    if cid:
        out.append({"conversation_id": cid})
    return out


def compare(cfg: dict, turns: list[str], out_path: Path, rounds: int = 1) -> Path:
    """Send the same listener turns to the voice agent (text-only, effort cfg.voice.reasoning_effort)
    and to the typed chat (models.guide at stages.guide.effort), and write both side by side."""
    import random

    from . import guide

    client = _client()
    agent_cfg = cfg
    # The comparison's conversations (both first meetings) go to their own folder, not the listener's memory;
    # the agent is still the listener's (agent_cfg keeps listener/voice.json).
    cfg = {**cfg, "listener_dir": str(out_path.parent / "listener")}

    def no_start(*a, **k):
        raise StageError("comparison only: not starting a chapter")

    lines = [f"# The guide: voice (effort {cfg['voice']['reasoning_effort']}) against typed "
             f"(effort {cfg['stages']['guide']['effort']})", "",
             "The same scripted listener turns, sent to the ElevenLabs agent in a text-only conversation "
             "(its tools mocked) and to the typed chat (its start_chapter refused). Seconds: from sending "
             "the turn to the agent's first reply (voice) or the whole reply (typed).", ""]
    for n in range(rounds):
        seed = random.Random(n)
        typed = guide.Session(cfg, no_start, lambda: {"state": "idle"}, ROOT / "runs", rng=seed)
        typed_out = []
        for said in [None] + turns:
            t0 = time.monotonic()
            reply = typed.say(said)
            typed_out.append({"said": said or "(opened the page)", "reply": reply,
                              "seconds": round(time.monotonic() - t0, 2)})
        voiced = guide.Session(cfg, no_start, lambda: {"state": "idle"}, ROOT / "runs", rng=random.Random(n))
        voice_out = _voice_replies(agent_cfg, voiced, turns, client)
        cid = voice_out.pop()["conversation_id"] if voice_out and "conversation_id" in voice_out[-1] else None
        lines += [f"## Round {n + 1}", ""]
        for i, (t, v) in enumerate(zip(typed_out, voice_out)):
            lines += [f"### Turn {i}: {t['said']}", "",
                      f"**Typed ({t['seconds']} s):** {t['reply']}", "",
                      f"**Voice ({v['seconds']} s{', tools: ' + ', '.join(v['tools']) if v['tools'] else ''}):** "
                      f"{v['reply']}", ""]
        if cid:
            lines += [f"Voice conversation id: {cid}", ""]
            save_conversation(cfg, voiced.id, cid, client)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path
