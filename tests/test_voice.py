"""Phase 1, milestone 4: the guide's voice (an ElevenLabs agent) and the server's voice endpoints."""

import json
import re
from types import SimpleNamespace as NS

import pytest
from fakes import FakeAnthropic, Responder
from fastapi.testclient import TestClient

import server
from faststart import notes as fast_notes
from stages import guide, llm, voice


class FakeEL:
    """The parts of the ElevenLabs client sync_agent and signed_url use, recording every call.
    `fail` maps a call kind to HTTP statuses to raise, in order, before it succeeds."""

    def __init__(self):
        self.calls = []
        self.fail: dict[str, list[int]] = {}
        n = iter(range(1, 100))

        def rec(kind):
            def f(*a, **k):
                self.calls.append((kind, a, k))
                if self.fail.get(kind):
                    from elevenlabs.core.api_error import ApiError
                    raise ApiError(status_code=self.fail[kind].pop(0), body="fake failure")
                if kind == "tools.create":
                    return NS(id=f"tool_{next(n)}")
                if kind == "agents.create":
                    return NS(agent_id="agent_1")
                if kind == "signed_url":
                    return NS(signed_url="wss://example/signed")
                return NS()
            return f
        self.conversational_ai = NS(
            tools=NS(create=rec("tools.create"), update=rec("tools.update")),
            agents=NS(create=rec("agents.create"), update=rec("agents.update")),
            conversations=NS(get_signed_url=rec("signed_url")))

    def kinds(self):
        return [k for k, _, _ in self.calls]


@pytest.fixture
def vcfg(cfg, tmp_path):
    cfg["listener_dir"] = str(tmp_path / "listener")
    return cfg


def test_tools_become_elevenlabs_client_tools(cfg):
    tools = voice.client_tools(cfg)
    # Four tools since Fast Start milestone 5: scout_topic comes first, and is the one the agent does not wait for.
    assert [t["name"] for t in tools] == ["scout_topic", "start_chapter", "add_note", "get_status"]
    assert all(t["type"] == "client" for t in tools)
    assert [t["expects_response"] for t in tools] == [False, True, True, True]
    assert "additionalProperties" not in json.dumps(tools)  # not in ElevenLabs' schema subset
    start = tools[1]["parameters"]
    assert start["required"] == ["topic", "listener_said", "why", "notes"]
    assert all(p.get("description") for p in start["properties"].values())
    assert tools[3]["parameters"] == {"type": "object", "properties": {}, "required": []}
    note = tools[2]["parameters"]  # the note's kind is optional, and one of the five (faststart/notes.py)
    assert note["required"] == ["note"] and note["properties"]["kind"]["enum"] == list(fast_notes.KINDS)


def test_the_agent_is_the_typed_guide(cfg):
    prompt = voice.guide_prompt()
    assert prompt.startswith("You are a guide. You make chapters")
    assert set(re.findall(r"\{\{\s*(\w+)\s*\}\}", prompt)) == set(voice.PLACEHOLDERS)
    conv = voice.conversation_config(cfg, ["t1", "t2", "t3"])
    p = conv["agent"]["prompt"]
    assert p["llm"] == "claude-sonnet-5-5" and p["reasoning_effort"] == "none" and p["tool_ids"] == ["t1", "t2", "t3"]
    assert conv["agent"]["first_message"] == ""  # the page tells it the listener arrived; it speaks first
    assert conv["tts"] == {"model_id": "eleven_v4_turbo", "voice_id": cfg["narration"]["voice_id"]}
    assert set(conv["agent"]["dynamic_variables"]["dynamic_variable_placeholders"]) == set(voice.PLACEHOLDERS)
    assert voice.platform_settings()["auth"]["enable_auth"] is True  # private: signed URLs only


def test_sync_creates_once_and_updates_only_on_change(vcfg):
    el = FakeEL()
    assert voice.sync_agent(vcfg, el) == "agent_1"
    assert el.kinds() == ["tools.create"] * 4 + ["agents.create"]  # four tools since scout_topic (Fast Start)
    create = el.calls[-1][2]
    assert create["conversation_config"]["agent"]["prompt"]["tool_ids"] == ["tool_1", "tool_2", "tool_3", "tool_4"]
    state = json.loads(voice.state_path(vcfg).read_text())
    assert state["agent_id"] == "agent_1" and state["tool_ids"]["get_status"] == "tool_4"

    el.calls.clear()
    assert voice.sync_agent(vcfg, el) == "agent_1" and el.calls == []  # nothing changed: no calls

    vcfg["voice"]["reasoning_effort"] = "low"
    voice.sync_agent(vcfg, el)
    assert el.kinds() == ["tools.update"] * 4 + ["agents.update"]  # same tools and agent, updated
    assert el.calls[-1][1] == ("agent_1",)
    assert el.calls[-1][2]["conversation_config"]["agent"]["prompt"]["reasoning_effort"] == "low"


def test_dynamic_variables_are_the_sessions_prompt_values(vcfg, tmp_path):
    s = guide.Session(vcfg, lambda *a: "run", lambda: {"state": "idle"}, tmp_path)
    dv = voice.dynamic_variables(s)
    assert set(dv) == set(voice.PLACEHOLDERS) and all(isinstance(v, str) for v in dv.values())
    assert dv["identity"].startswith("(none: this is your first meeting)") and dv["names"].count(",") >= 1


@pytest.fixture
def app(vcfg, tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_client", FakeAnthropic(Responder()))
    monkeypatch.setattr(voice, "signed_url", lambda cfg, client=None: "wss://example/signed")
    saved = []
    monkeypatch.setattr(server, "_save_voice", lambda cfg, sid, cid: saved.append((sid, cid)))
    runs = tmp_path / "runs"
    runs.mkdir()
    pipe = server.Pipeline(runs_dir=runs)
    started = []

    def start(args, run):
        started.append(args)
        pipe.proc, pipe.run = NS(returncode=None, poll=lambda: None), run

    monkeypatch.setattr(pipe, "start", start)
    return TestClient(server.create_app(pipe, vcfg, remember_on_start=False)), started, saved, runs


def test_voice_conversation_endpoints(app, vcfg):
    c, started, saved, runs = app
    page = c.get("/").text
    assert "__VOICE_CLIENT_JS__" not in page and "@elevenlabs/client@1.32.1/dist/lib.iife.js" in page

    v = c.post("/api/voice/start").json()
    sid = v["session"]
    assert v["signed_url"] == "wss://example/signed" and set(v["dynamic_variables"]) == set(voice.PLACEHOLDERS)
    assert v["opened"].startswith("[The listener has opened the page") and "{title}" in v["ready_note"]

    assert c.post("/api/voice/line", json={"session": sid, "who": "", "conversation_id": "conv_1"}).json()["ok"]
    c.post("/api/voice/line", json={"session": sid, "who": "listener", "text": "I pour concrete"})
    c.post("/api/voice/line", json={"session": sid, "who": "guide", "text": "Roman harbours, then?"})
    out = c.post("/api/voice/tool", json={"session": sid, "name": "start_chapter", "args": {
        "topic": "Why Roman concrete lasted", "listener_said": "I pour concrete", "why": "", "notes": ""}}).json()
    assert out["started"] is True and started and started[0][:2] == ["new", started[0][1]]
    status = c.post("/api/voice/tool", json={"session": sid, "name": "get_status", "args": {}}).json()
    assert status["state"] in ("being made", "no chapter yet")

    transcript = guide.listener_dir(vcfg) / "sessions" / f"{sid}.jsonl"
    lines = [json.loads(x) for x in transcript.read_text().splitlines()]
    assert [x["who"] for x in lines] == ["session", "page", "session", "listener", "guide", "tool", "tool"]
    assert lines[2]["conversation_id"] == "conv_1" and lines[5]["name"] == "start_chapter"

    assert c.post("/api/chat/end", json={"session": sid}).json()["ended"] is True
    assert saved == [(sid, "conv_1")]  # ElevenLabs' record of the call is fetched afterwards
    assert c.post("/api/voice/tool", json={"session": sid, "name": "get_status"}).status_code == 409


def test_voice_unavailable_says_so(app, monkeypatch):
    c, _, _, _ = app

    def broken(cfg, client=None):
        raise RuntimeError("no ElevenLabs key")
    monkeypatch.setattr(voice, "signed_url", broken)
    res = c.post("/api/voice/start")
    assert res.status_code == 503 and "Voice isn't available" in res.json()["detail"]  # the page then types


def test_a_failed_sync_resumes_without_duplicate_tools(vcfg):
    el = FakeEL()
    el.fail["agents.create"] = [500]
    with pytest.raises(Exception, match="500"):
        voice.sync_agent(vcfg, el)
    state = json.loads(voice.state_path(vcfg).read_text())
    assert state["agent_id"] is None and len(state["tool_ids"]) == 4 and state["digest"] is None  # ids kept
    el.calls.clear()
    assert voice.sync_agent(vcfg, el) == "agent_1"
    assert el.kinds() == ["tools.update"] * 4 + ["agents.create"]  # the tools are reused, not made again


def test_only_a_404_makes_a_new_agent(vcfg):
    el = FakeEL()
    voice.sync_agent(vcfg, el)
    before = voice.state_path(vcfg).read_text()
    el.calls.clear()
    vcfg["voice"]["turn_eagerness"] = "normal"
    el.fail["agents.update"] = [503]
    with pytest.raises(Exception, match="503"):
        voice.sync_agent(vcfg, el)  # a blip: stop, keep the state, try again next time
    assert voice.state_path(vcfg).read_text() == before and "agents.create" not in el.kinds()
    el.calls.clear()
    el.fail["agents.update"] = [404]
    voice.sync_agent(vcfg, el)
    assert el.kinds() == ["tools.update"] * 4 + ["agents.update", "agents.create"]  # deleted: made again


def test_an_unprocessed_call_record_is_not_logged_as_free(vcfg, monkeypatch):
    sessions = voice.listener_dir(vcfg) / "sessions"
    sessions.mkdir(parents=True)
    states = iter([RuntimeError, "processing", "processing"])

    def get(cid):
        st = next(states, "processing")
        if st is RuntimeError:
            from elevenlabs.core.api_error import ApiError
            raise ApiError(status_code=502, body="blip")
        return NS(status=st, model_dump=lambda mode=None: {"status": st, "metadata": {"cost_fiat": None}})
    el = NS(conversational_ai=NS(conversations=NS(get=get)))
    monkeypatch.setattr(voice.time, "sleep", lambda s: None)
    data = voice.save_conversation(vcfg, "s1", "conv_1", client=el, wait_s=0.05)
    assert data["status"] == "processing"  # the blip was ridden out
    line = json.loads((sessions / "log.jsonl").read_text().splitlines()[-1])
    assert line["final"] is False and line["cost_usd"] is None
