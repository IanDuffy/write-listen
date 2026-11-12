"""The guide's voice (Phase 1, milestone 4; docs/phase1-onboarding.md, docs/elevenlabs-agents-check.md).

The voice guide is an ElevenLabs agent whose prompt is prompts/guide.md and whose three
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
from .guide import TOOLS, listener_dir

PLACEHOLDERS = ("identity", "names", "profile", "history", "chapter_now", "minutes")


def guide_prompt() -> str:
    """prompts/guide.md below its '---' line, with its {{placeholders}} left in: ElevenLabs fills
    them from the conversation's dynamic variables (same syntax)."""
    text = (PROMPTS_DIR / "guide.md").read_text(encoding="utf-8")
    return text.split("\n---\n", 1)[1].strip() + "\n"


def _el_schema(schema: dict) -> dict:
    """An Anthropic tool input_schema as ElevenLabs' parameter schema: no additionalProperties,
    and every property described (ElevenLabs uses the descriptions to fill the values)."""
    props = {k: {kk: vv for kk, vv in v.items() if kk != "additionalProperties"}
             for k, v in (schema.get("properties") or {}).items()}
    return {"type": "object", "properties": props, "required": list(schema.get("required") or [])}


def client_tools(cfg: dict) -> list[dict]:
    """prompts/guide_tools.json as ElevenLabs client tool configs."""
    return [{"type": "client", "name": t["name"], "description": t["description"],
             "parameters": _el_schema(t["input_schema"]), "expects_response": True,
             "response_timeout_secs": cfg["voice"]["tool_timeout_secs"]} for t in TOOLS]


def conversation_config(cfg: dict, tool_ids: list[str]) -> dict:
    v = cfg["voice"]
    return {
        "agent": {
            # Empty: the guide waits, and the page then tells it the listener has arrived
            # (prompts/guide_opened.md, as the typed chat does), so its first line is its own.
            "first_message": "",
            "language": "en",
            "dynamic_variables": {"dynamic_variable_placeholders": {k: f"({k})" for k in PLACEHOLDERS}},
            "prompt": {"prompt": guide_prompt(), "llm": v["llm"], "reasoning_effort": v["reasoning_effort"],
                       "tool_ids": tool_ids},
        },
        "tts": {"model_id": v["tts_model"], "voice_id": v.get("voice_id") or cfg["narration"]["voice_id"]},
        "turn": {"turn_eagerness": v["turn_eagerness"]},
        "conversation": {"max_duration_seconds": v["max_duration_seconds"]},
    }


def platform_settings() -> dict:
    # Private: a conversation needs a signed URL. Text-only may be asked per conversation (voice-compare).
    return {"auth": {"enable_auth": True},
            "overrides": {"conversation_config_override": {"conversation": {"text_only": True}}}}


def state_path(cfg: dict) -> Path:
    return listener_dir(cfg) / "voice.json"


def _client():
    from .tts import elevenlabs_client
    return elevenlabs_client()


def _digest(cfg: dict) -> str:
    # The whole agent as the code would send it (tool ids aside), so any change to the prompt, tools,
    # config or this module's settings is synced.
    blob = json.dumps({"tools": client_tools(cfg), "conversation": conversation_config(cfg, []),
                       "platform": platform_settings(), "name": cfg["voice"]["agent_name"]}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _save_state(p: Path, agent_id: str | None, tool_ids: dict, digest: str | None) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"agent_id": agent_id, "tool_ids": tool_ids, "digest": digest,
                             "synced": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=1), encoding="utf-8")


def sync_agent(cfg: dict, client=None, force: bool = False) -> str:
    """The guide agent's id, after creating or updating its tools and itself to match the repo.

    Ids are saved as soon as each object exists, so a sync that fails partway is resumed (updated),
    never repeated (no duplicate tools). Only a 404 (deleted on the dashboard) makes a new object;
    any other error stops, leaving the saved state for the next try."""
    from elevenlabs.types import ToolRequestModel
    from elevenlabs.types.tool_request_model_tool_config import ToolRequestModelToolConfig_Client

    from .tts import ElevenLabsError, el_call

    client = client or _client()
    p = state_path(cfg)
    state = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    digest = _digest(cfg)
    if state.get("agent_id") and state.get("digest") == digest and not force:
        return state["agent_id"]

    agent_id, tool_ids = state.get("agent_id"), dict(state.get("tool_ids") or {})
    for tool in client_tools(cfg):
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

    conv = conversation_config(cfg, [tool_ids[t["name"]] for t in TOOLS])
    if agent_id:
        try:
            el_call(client.conversational_ai.agents.update, agent_id, conversation_config=conv,
                    platform_settings=platform_settings(), name=cfg["voice"]["agent_name"])
        except ElevenLabsError as e:
            if e.status != 404:
                raise
            print(f"  agent {agent_id} is gone; creating a new one")
            agent_id = None
    if not agent_id:
        agent_id = el_call(client.conversational_ai.agents.create, conversation_config=conv,
                           platform_settings=platform_settings(), name=cfg["voice"]["agent_name"],
                           tags=["write-listen"]).agent_id
    _save_state(p, agent_id, tool_ids, digest)
    print(f"  voice agent {agent_id}: {cfg['voice']['llm']}, effort {cfg['voice']['reasoning_effort']}, "
          f"{cfg['voice']['tts_model']}, voice {conv['tts']['voice_id']}")
    return agent_id


def signed_url(cfg: dict, client=None) -> str:
    from .tts import el_call
    client = client or _client()
    return el_call(client.conversational_ai.conversations.get_signed_url, agent_id=sync_agent(cfg, client)).signed_url


def dynamic_variables(session) -> dict[str, str]:
    """The guide prompt's values for one conversation (guide.Session.values), as strings."""
    return {k: str(session.values[k]) for k in PLACEHOLDERS}


def save_conversation(cfg: dict, session_id: str, conversation_id: str, client=None, wait_s: int = 120) -> dict | None:
    """After a voice conversation: ElevenLabs' record of it (transcript, tool calls, per-turn latency,
    cost) to listener/sessions/<id>.voice.json, and its cost to the sessions log. Polls until done."""
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
    (sessions / f"{session_id}.voice.json").write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
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
}


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
                tools_pending = any(k == "tool" and not any(k2 == "reply" and t2 > t for _, k2, t2, _ in mine)
                                    for _, k, t, _ in mine)
                quiet = mine and time.monotonic() - mine[-1][2] >= 4 and not tools_pending
                if quiet:
                    break
                if time.monotonic() - t0 > 60:
                    raise StageError(f"voice agent: no complete reply within 60 s to {said[:60]!r}")
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
