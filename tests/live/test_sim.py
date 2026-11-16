"""The GPT-Live live-test harness (voice/gpt_live/sim.py, persona.py), offline: a fake WebSocket stands
in for GPT-Live, and nothing reaches OpenAI or Anthropic."""

import asyncio
import base64
import json

import httpx
import pytest

from stages.common import StageError
from voice.gpt_live import persona as P
from voice.gpt_live import session as live
from voice.gpt_live import sim as S


def arun(coro):
    """asyncio.run in a thread of its own: a browser test earlier in the session (Playwright's sync API)
    can leave an event loop running in the main thread, where asyncio.run refuses to start."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, coro).result()


class FakeWS:
    """GPT-Live's side of the socket: answers session.start and session.close; `react(msg, ws)` scripts the rest."""

    def __init__(self, react=None):
        self.sent, self.q, self.react = [], asyncio.Queue(), react

    async def send(self, raw):
        m = json.loads(raw)
        self.sent.append(m)
        if m["type"] == "session.start":
            self.push({"type": "session.started", "session": {"id": "live_test", "expires_at": 1}})
        elif m["type"] == "session.close":
            self.push({"type": "session.closed", "reason": "close_requested", "usage": {"seconds": 3.0}})
        if self.react:
            await self.react(m, self)

    def push(self, m):
        self.q.put_nowait(json.dumps(m))

    def __aiter__(self):
        return self

    async def __anext__(self):
        raw = await self.q.get()
        if raw is None:
            raise StopAsyncIteration
        return raw

    async def close(self):
        self.q.put_nowait(None)

    def types(self):
        return [m["type"] for m in self.sent if m["type"] != "session.input_audio.append"]


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    """No network, no real key, costs and audio into tmp_path."""
    def no_network(*a, **k):
        raise AssertionError("a test tried to reach the network")

    monkeypatch.setattr(httpx, "post", no_network)
    monkeypatch.setattr(S.websockets, "connect", no_network)
    monkeypatch.setattr(live, "api_key", lambda: "sk-test")
    monkeypatch.setattr(S, "COSTS", tmp_path / "costs.jsonl")
    monkeypatch.setattr(S, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(S, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(P.llm, "call", no_network)


def use_ws(monkeypatch, ws):
    async def connect(url, additional_headers=None, max_size=None):
        assert url == "wss://api.openai.com/v1/live/sessions"
        assert additional_headers == {"Authorization": "Bearer sk-test"}
        return ws
    monkeypatch.setattr(S.websockets, "connect", connect)


def session_cfg(cfg):
    return live.session_config(cfg, P.first_meeting_values("Friday evening"), transport="websocket")


def loud(secs=0.1):
    return (b"\x00\x40" * int(24000 * secs))  # 16384 in little-endian PCM16: clearly audible


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


def test_transcript_deltas_become_turns(cfg):
    sim = S.Sim(cfg, {}, verbose=False)

    async def go():
        for t, d in [(1.0, "Hi! "), (1.2, "I'm Maren."), (1.4, " What's your name?")]:
            await sim.on_event({"type": "session.output_transcript.delta", "delta": d}, t)
        await sim.on_event({"type": "session.output_audio.delta", "delta": base64.b64encode(loud()).decode()}, 2.0)
        sim.tick(2.6)  # 1.2 s after the last delta, but the audio was audible at 2.0: still speaking
        assert sim.speaking and not sim.turns
        sim.tick(3.3)
        await sim.on_event({"type": "session.output_audio.delta", "delta": base64.b64encode(bytes(1920)).decode()}, 3.4)
        await sim.on_event({"type": "session.output_transcript.delta", "delta": "Nice."}, 5.0)
        sim.tick(6.3)
        await sim.on_event({"type": "session.input_transcript.delta", "delta": "I'm Dan"}, 4.0)
    arun(go())
    assert [t["text"] for t in sim.turns] == ["Hi! I'm Maren. What's your name?", "Nice."]
    assert sim.audio_last == 2.0  # silent audio does not count as speech
    assert sim.heard_between(3.5, 4.5) == "I'm Dan"
    assert [x["who"] for x in sim.timeline] == ["guide", "guide"]


def test_function_call_answered_then_resumed(cfg, monkeypatch):
    ws = FakeWS()
    use_ws(monkeypatch, ws)
    handled = []

    def tools(name, args):
        handled.append((name, args))
        return {"started": True, "first_audio_minutes": 4}

    async def go():
        sim = S.Sim(cfg, session_cfg(cfg), tool_handler=tools, verbose=False)
        await sim.connect()
        assert ws.types()[:2] == ["session.start", "session.commentary.append"]
        greet = [m for m in ws.sent if m["type"] == "session.commentary.append"][0]
        assert greet["delegation_id"] is None and greet["content"] == live.notes()["opened"]
        item = {"type": "function_call", "call_id": "call_1", "name": "start_chapter",
                "arguments": json.dumps({"topic": "Cabinets", "listener_said": "I build cabinets"})}
        ws.push({"type": "response.event", "delegation_id": "d1",
                 "event": {"type": "response.output_item.done", "item": item}})
        ws.push({"type": "response.event", "delegation_id": "d1",  # the same call reported twice: run once
                 "event": {"type": "response.output_item.done", "item": item}})
        ws.push({"type": "response.event", "delegation_id": "d1", "event": {
            "type": "response.completed", "response": {"model": "gpt-6.1-terra", "usage": {
                "input_tokens": 1000, "output_tokens": 20, "input_tokens_details": {"cached_tokens": 400}}}}})
        for _ in range(50):
            await asyncio.sleep(0.01)
            if "response.create" in ws.types():
                break
        summary = await sim.close()
        return sim, summary

    sim, summary = arun(go())
    assert handled == [("start_chapter", {"topic": "Cabinets", "listener_said": "I build cabinets"})]
    types = ws.types()
    i = types.index("response.item.create")
    assert types.index("response.create") > i and types.count("response.create") == 1
    out = [m for m in ws.sent if m["type"] == "response.item.create"][0]["item"]
    assert out["type"] == "function_call_output" and out["call_id"] == "call_1"
    assert json.loads(out["output"]) == {"started": True, "first_audio_minutes": 4}
    assert sim.calls[0]["name"] == "start_chapter"
    assert sim.backend == {"model": "gpt-6.1-terra", "input": 1000, "output": 20, "cached": 400}
    assert summary["session_id"] == "live_test" and summary["closed_reason"] == "close_requested"
    assert all(m["type"] != "session.input_audio.append" or len(base64.b64decode(m["audio"])) == S.CHUNK
               for m in ws.sent)


def test_session_cost_is_logged(cfg, monkeypatch):
    ws = FakeWS()
    use_ws(monkeypatch, ws)

    async def go():
        sim = S.Sim(cfg, session_cfg(cfg), verbose=False, label="persona x")
        await sim.connect()
        ws.push({"type": "session.usage.updated", "usage": {"seconds": 60.0}})
        ws.push({"type": "session.usage.updated", "usage": {"seconds": 30.0}})  # cumulative: never goes down
        ws.push({"type": "response.event", "delegation_id": "d", "event": {"type": "response.completed", "response": {
            "usage": {"input_tokens": 2_000_000, "output_tokens": 100_000,
                      "input_tokens_details": {"cached_tokens": 1_000_000}}}}})
        await asyncio.sleep(0.05)
        return await sim.close()

    summary = arun(go())
    # 60 s at $0.05 a minute, plus Terra: 1M uncached in at $1, 1M cached at $0.05, 0.1M out at $5
    assert summary["seconds"] == 60.0
    assert summary["usd"] == pytest.approx(0.05 + 1.0 + 0.05 + 0.5)
    rec = json.loads(S.COSTS.read_text().splitlines()[-1])
    assert rec["what"] == "gpt-live persona x" and rec["usd"] == pytest.approx(1.6)
    assert rec["backend_tokens"]["input"] == 2_000_000
    assert S.spent() == pytest.approx(1.6)


def test_build_cap_refuses_a_new_session(cfg, monkeypatch):
    S.log_cost("earlier", cfg["live"]["build_cap_usd"] - 0.05)
    connected = []

    async def connect(*a, **k):
        connected.append(1)
        return FakeWS()
    monkeypatch.setattr(S.websockets, "connect", connect)
    with pytest.raises(StageError, match="cap"):
        arun(S.Sim(cfg, {}, verbose=False, max_minutes=2).connect())
    assert not connected
    with pytest.raises(StageError, match="cap"):  # Claude calls are capped the same way
        P.claude(cfg, None, "live_listener x", "hi", role="guide", effort="low", estimate=0.10)


def test_tts_cached_and_costed(monkeypatch, tmp_path):
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append((url, json))
        return httpx.Response(200, content=bytes(48000 * 6))  # six seconds
    monkeypatch.setattr(httpx, "post", post)
    a = S.tts("Hi, I'm Dan.")
    b = S.tts("Hi, I'm Dan.")
    assert a == b and len(calls) == 1
    assert calls[0][1]["voice"] != "gleam" and calls[0][1]["response_format"] == "pcm"
    assert S.spent() == pytest.approx(6 / 60 * S.TTS_USD_PER_MINUTE)


def test_fake_tools_shapes():
    clock = [0.0]
    tools = S.FakeTools(clock=lambda: clock[0])
    assert tools("get_status", {}) == {"state": "no chapter yet"}
    out = tools("start_chapter", {"topic": "Cabinets"})
    # no while_waiting: the live guide's own parts cover the wait (routes.tool drops it from the real result)
    assert out["started"] and out["first_audio_minutes"] == 4 and "while_waiting" not in out
    clock[0] = 130
    assert tools("get_status", {})["starts_playing_in_minutes"] == 2
    assert tools("start_chapter", {"topic": " "})["started"] is False
    assert tools("add_note", {"note": "grandfather", "kind": "detail"})["added"]
    assert tools("step_back", {}) == {"quiet": True}
    assert tools("resume_chapter", {"reason": "closing"}) == {"resuming": True}
    assert "error" in tools("nope", {})


def test_converse_end_to_end_with_a_scripted_guide(cfg, monkeypatch, tmp_path):
    """The persona loop against a scripted guide: greeting, two exchanges, start_chapter, one more."""
    lines = iter([
        [{"type": "session.output_transcript.delta", "delta": "Hi! I'm Maren. What's your name?"}],
        [{"type": "session.output_transcript.delta", "delta": "Nice to meet you, Dan. Cabinets, then?"}],
        [{"type": "response.event", "delegation_id": "d1", "event": {"type": "response.output_item.done", "item": {
            "type": "function_call", "call_id": "c1", "name": "start_chapter",
            "arguments": json.dumps({"topic": "How cabinets were made", "listener_said": "Sure, cabinets."})}}},
         {"type": "response.event", "delegation_id": "d1", "event": {"type": "response.completed", "response": {}}},
         {"type": "session.output_transcript.delta", "delta": "I'm off to write it."}],
        [{"type": "session.output_transcript.delta", "delta": "Mm, glue-ups take patience."}],
    ])
    state = {"talking": False}

    async def react(m, ws):
        if m["type"] == "session.commentary.append":
            for e in next(lines):
                ws.push(e)
        elif m["type"] == "session.input_audio.append":
            audible = any(base64.b64decode(m["audio"]))
            if audible:
                state["talking"] = True
            elif state["talking"]:  # the listener has finished a line: the guide answers
                state["talking"] = False
                for e in next(lines, []):
                    ws.push(e)

    use_ws(monkeypatch, FakeWS(react))
    monkeypatch.setattr(P, "GUIDE_WAIT_SECS", 2)
    said = iter(["I'm Dan.", "Sure, cabinets.", "Cool, I'll keep gluing."])
    persona = {"name": "builder", "now": "Friday evening", "text": "Dan builds cabinets."}
    t = arun(P.converse(
        cfg, persona, tmp_path / "out", max_turns=6, after_start=1,
        reply_fn=lambda timeline, moment: next(said),
        sim_kwargs={"quiet_secs": 0.15, "settle_secs": 0.1, "verbose": False,
                    "tts_fn": lambda text, voice: loud(0.1)}))
    assert t["stop"] == "chapter started"
    whos = [(x["who"], x.get("text") or x.get("name")) for x in t["timeline"] if x["who"] in ("guide", "listener", "tool")]
    assert whos == [("guide", "Hi! I'm Maren. What's your name?"), ("listener", "I'm Dan."),
                    ("guide", "Nice to meet you, Dan. Cabinets, then?"), ("listener", "Sure, cabinets."),
                    ("tool", "start_chapter"), ("guide", "I'm off to write it."),
                    ("listener", "Cool, I'll keep gluing."), ("guide", "Mm, glue-ups take patience.")]
    saved = json.loads((tmp_path / "out" / "builder.json").read_text())
    assert saved["calls"][0]["args"]["listener_said"] == "Sure, cabinets."
    assert (tmp_path / "out" / "builder.guide.wav").exists()
    text = P.format_transcript(saved)
    assert "[" in text and "tool start_chapter" in text and "listener: I'm Dan." in text


def test_grade_checks_quotes_and_saves(cfg, monkeypatch, tmp_path):
    transcript = {"kind": "onboarding", "persona": "builder", "description": "Dan builds cabinets.",
                  "values": P.first_meeting_values("Friday evening"), "stop": "turns", "calls": [],
                  "cost": {"seconds": 10, "usd": 0.01, "backend": {"input": 0, "output": 0}},
                  "timeline": [{"t": 1.0, "who": "guide", "text": "Hi there! I'm Wren. What's your name?"},
                               {"t": 3.0, "who": "listener", "text": "Dan.", "heard": "Dan."}]}
    path = tmp_path / "builder.json"
    path.write_text(json.dumps(transcript))
    seen = {}

    class Res:
        text = json.dumps({"rules": [{"id": "hello", "verdict": "fail", "misses": [
            {"quote": "Hi there! I'm Wren", "why": "fine really"}, {"quote": "invented words", "why": "x"}]}],
            "other_issues": [], "score": 7, "summary": "OK."})

        class usage:
            cost_usd = 0.2

    def call(run, cfg_, stage, *, messages, json_schema=None, role=None, effort=None, **kw):
        seen.update(stage=stage, role=role, effort=effort, schema=json_schema, prompt=messages[0]["content"])
        return Res()
    monkeypatch.setattr(P.llm, "call", call)
    g = P.grade(cfg, path)
    # graded on Sonnet at medium effort (config live.checks.grade_role; Opus at high was most of the checks' bill)
    assert seen["stage"] == "live_grade" and seen["role"] == "researcher" and seen["effort"] == "medium"
    assert seen["schema"] is P.GRADE_SCHEMA
    assert "HOW YOU TALK" in seen["prompt"] and "Hi there! I'm Wren" in seen["prompt"]
    assert "{{" not in seen["prompt"]
    assert g["quotes_not_found"] == 1
    assert [m["found"] for m in g["rules"][0]["misses"]] == [True, False]
    assert json.loads((tmp_path / "builder.grade.json").read_text())["score"] == 7
    assert S.spent() == pytest.approx(0.2)


def test_personas_and_rules():
    people = P.personas()
    assert {"builder", "reserved", "teacher"} <= set(people)
    assert people["builder"]["now"] == "Friday evening" and "now:" not in people["builder"]["text"]


def test_listener_reply_tokens(cfg, monkeypatch):
    out = iter(["[SILENT]", "  [LEAVE]", '"Yeah, cabinets."'])

    class Res:
        def __init__(self, text):
            self.text = text

        class usage:
            cost_usd = 0.001
    monkeypatch.setattr(P.llm, "call", lambda *a, **k: Res(next(out)))
    persona = {"name": "builder", "text": "Dan."}
    assert P.listener_reply(cfg, None, persona, [], "now") == P.SILENT
    assert P.listener_reply(cfg, None, persona, [], "now") == P.LEAVE
    assert P.listener_reply(cfg, None, persona, [], "now") == "Yeah, cabinets."


def test_a_turn_is_cut_where_a_tool_call_lands():
    """Live (2026-11-13): "Good to know! I'll see what's out there." was said while start_chapter ran and
    "All right, I'm on it..." after its result, in one turn; the grade read it all as before the tool."""
    timeline = [
        {"t": 170.6, "who": "guide", "text": "Good to know! I'll see what's out there. All right, I'm on it.",
         "deltas": [[170.6, "Good to know!"], [171.6, " I'll see what's out there."], [174.7, " All right,"],
                    [175.4, " I'm on it."]]},
        {"t": 172.7, "who": "tool", "name": "start_chapter", "args": {}, "result": {}},
        {"t": 174.1, "who": "backend", "text": "It's underway."},
        {"t": 161.8, "who": "listener", "text": "Yeah, that works."},
    ]
    out = P.split_turns(timeline)
    assert [(x["who"], x.get("text")) for x in out] == [
        ("listener", "Yeah, that works."), ("guide", "Good to know! I'll see what's out there."),
        ("tool", None), ("backend", "It's underway."), ("guide", "All right, I'm on it.")]
    assert all("deltas" not in x for x in out)


def test_wait_guide_does_not_time_out_mid_turn(cfg):
    """Live (2026-11-13): a 19-second pitch ran past the 20 s wait, so the listener was told the guide had
    been silent. A turn under way at the timeout is waited for."""
    sim = S.Sim(cfg, {}, verbose=False, quiet_secs=0.1, settle_secs=0.05)

    async def go():
        sim.t0 = __import__("time").monotonic()
        await sim.on_event({"type": "session.output_transcript.delta", "delta": "A long pitch"}, sim.now())

        async def finish():
            await asyncio.sleep(0.3)
            await sim.on_event({"type": "session.output_transcript.delta", "delta": " ends here."}, sim.now())
            for _ in range(20):
                await asyncio.sleep(0.02)
                sim.tick(sim.now())
        task = asyncio.create_task(finish())
        turns = await sim.wait_guide(0, timeout=0.1)
        await task
        return turns
    turns = arun(go())
    assert [t["text"] for t in turns] == ["A long pitch ends here."]



def test_a_checks_session_is_capped_by_its_own_budget(cfg):
    """config live.checks.run_cap_usd bounds a check's connected time ($0.40 at $0.05 a minute: 8 minutes)."""
    cfg["live"]["checks"] = {"run_cap_usd": 0.10}
    sim = S.Sim(cfg, {}, max_minutes=6)
    assert sim.max_minutes == pytest.approx(2.0)
