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
    # the listener the 2026-11-18 guide changes were checked against (Ian's second tester run), kept for repeats
    assert people["evening"]["now"] == "Wednesday evening" and "write ... wherever you pause" in people["evening"]["text"]


def test_the_listener_from_ians_third_run_and_the_grade_follow_the_new_rules():
    """Ian's third tester run (2026-11-19): alone in the evening, fragments, a story still going when the guide cut
    in, and "What would it be about?" with his yes. The persona check gets that listener, and the grade the rules
    the guide now has (warm and curious, a clear yes, the topic said after the start), not the ones it replaced."""
    ian3 = P.personas()["ian3"]
    assert ian3["now"] == "Thursday evening" and "now:" not in ian3["text"]
    for bit in ("alone", "write ... wherever you pause", "omega-3", "Robert Wright", "What would it be about?"):
        assert bit in ian3["text"], bit
    from stages.common import load_prompt
    grade = load_prompt("live_grade", rules="R", persona="X", transcript="T")
    assert "- warm:" in grade and "- eager:" not in grade
    assert "a question is the exception" not in grade and "never in its first two turns" not in grade
    assert "only on a clear yes" in grade and "never cuts into a story" in grade
    assert "what the chapter will be about" in grade and "waits for their answer" in grade


def test_the_persona_check_can_open_as_the_tester_demo_does(cfg, monkeypatch, tmp_path, capsys):
    """`persona --handoff NAME`: the tester handoff opening was reachable only from a scratch driver."""
    from voice.gpt_live import __main__ as M
    seen = []

    async def converse(cfg_, persona, out, **kw):
        seen.append((persona["name"], kw.get("handoff")))
        return {}
    monkeypatch.setattr(M, "load_config", lambda: cfg)
    monkeypatch.setattr(P, "converse", converse)
    monkeypatch.setattr(P, "new_out_dir", lambda: tmp_path)
    monkeypatch.setattr(P, "claude_run", lambda: None)
    monkeypatch.setattr(P, "summary", lambda name, t, g: name)
    assert M.main(["persona", "--personas", "evening", "--handoff", "Ian"]) == 0
    assert M.main(["persona", "--personas", "dad"]) == 0
    assert seen == [("evening", "Ian"), ("dad", None)]


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


def scripted_guide(lines):
    """A fake GPT-Live whose guide says the next of `lines` when context is appended at the start and each
    time the listener finishes a line."""
    lines, state = iter(lines), {"talking": False}

    async def react(m, ws):
        if m["type"] == "session.commentary.append":
            ws.push({"type": "session.output_transcript.delta", "delta": next(lines)})
        elif m["type"] == "session.input_audio.append":
            if any(base64.b64decode(m["audio"])):
                state["talking"] = True
            elif state["talking"]:
                state["talking"] = False
                line = next(lines, None)
                if line:
                    ws.push({"type": "session.output_transcript.delta", "delta": line})
    return FakeWS(react)


FAST = {"quiet_secs": 0.15, "settle_secs": 0.1, "verbose": False, "tts_fn": lambda text, voice: loud(0.1)}
DAN = {"name": "builder", "now": "Friday evening", "text": "Dan builds cabinets."}


def test_a_tester_handed_over_is_greeted_by_name_not_with_the_hello(cfg, monkeypatch, tmp_path):
    """The Tester Demo's opening (Ian's runs): the interviewer has their name, and the page's `handoff` note
    opens the guide's conversation instead of the hello."""
    ws = scripted_guide(["Hey, Ian! I'm Ailsa.", "Ha, a long day earns that."])
    use_ws(monkeypatch, ws)
    monkeypatch.setattr(P, "GUIDE_WAIT_SECS", 2)
    said = iter(["Hey Ailsa."])
    t = arun(P.converse(cfg, DAN, tmp_path / "out", max_turns=1, reply_fn=lambda timeline, moment: next(said),
                        sim_kwargs=FAST, handoff="Ian"))
    spoken = [m["content"] for m in ws.sent if m["type"] == "session.commentary.append"]
    assert spoken == [live.notes()["handoff"].replace("{name}", "Ian")] and "Ian by name" in spoken[0]
    start = next(m for m in ws.sent if m["type"] == "session.start")
    assert not [m for m in start["session"]["input"] if m.get("role") == "developer"]  # no hello asked for as well
    assert [x["text"] for x in t["timeline"] if x["who"] == "guide"][0] == "Hey, Ian! I'm Ailsa."


def test_a_guide_slow_to_say_what_it_does_is_cued_once_as_the_page_does(cfg, monkeypatch, tmp_path):
    """Ian's second tester run (2026-11-18): the offer to say what it does came two minutes in. The page cues it
    (live.page.offer_nudge); the persona check sends the same note, once, as the listener speaks, and never once
    the guide has said it. A listener's line with "..." in it is spoken in pieces, a pause at each."""
    cfg["live"]["page"]["offer_nudge"] = {"after_secs": 0, "max_secs": 0, "offered": "\\bwhat i do\\b"}
    ws = scripted_guide(["Hi there!", "Ooh, cabinets.", "Mm.", "Plywood, of course.", "Ha. Brave.", "Right."])
    use_ws(monkeypatch, ws)
    monkeypatch.setattr(P, "GUIDE_WAIT_SECS", 6)  # a slow machine must not look like a silent guide (which is cued)
    said = iter(["Hi. Building cabinets.", "Well ... plywood ones.", "Yep."])
    t = arun(P.converse(cfg, DAN, tmp_path / "out", max_turns=3, reply_fn=lambda timeline, moment: next(said),
                        sim_kwargs=FAST, pause_secs=0.3))
    notes = [m["content"] for m in ws.sent if m["type"] == "session.instructions.append"]
    assert notes.count(live.notes()["offer_nudge"]) == 1
    order = [(x["who"], x.get("text")) for x in t["timeline"] if x["who"] in ("listener", "app")]
    assert order.index(("listener", "Hi. Building cabinets.")) < order.index(("app", live.notes()["offer_nudge"])) \
        < order.index(("listener", "Well"))  # not before they've said a word; then as they speak
    assert ("listener", "plywood ones.") in order  # the line's second piece, after the pause
    # A guide that has said what it does is not cued; nor one whose own offer is the line being answered.
    for lines in (["Hi there!", "I make short audio chapters, just for you.", "Lovely."],
                  ["Hi there!", "Want to hear what I do?", "Great."]):
        ws = scripted_guide(lines)
        use_ws(monkeypatch, ws)
        said = iter(["Hi.", "Sure."])
        arun(P.converse(cfg, DAN, tmp_path / "out2", max_turns=2, reply_fn=lambda timeline, moment: next(said),
                        sim_kwargs=FAST))
        assert live.notes()["offer_nudge"] not in [m["content"] for m in ws.sent if m["type"] == "session.instructions.append"]


def test_pace_says_when_it_explained_pitched_and_how_much_it_asked():
    """The measures Ian set (2026-11-18): what it does by about a minute, a pitch by two or three, few questions."""
    tl = [{"t": 2.0, "who": "guide", "text": "Hey, Ian! I'm Ailsa."},
          {"t": 9.0, "who": "guide", "text": "Mm."},  # a backchannel is not a turn
          {"t": 20.0, "who": "guide", "text": "Ha, a carbonara night. Want to hear what I do?"},
          {"t": 31.0, "who": "guide", "text": "I make short audio chapters, just for you. What have you been listening to lately?"},
          {"t": 80.0, "who": "guide", "text": "Oh, that book sounds huge."},
          {"t": 95.0, "who": "guide", "text": "I've got it: the three moments AI nearly died. Want that?"},
          {"t": 101.0, "who": "tool", "name": "start_chapter", "args": {}, "result": {}},
          {"t": 104.0, "who": "guide", "text": "It's under way. Want some quiet?"}]
    t = {"timeline": tl, "stop": "chapter started", "calls": [{"name": "start_chapter"}],
         "cost": {"seconds": 110, "usd": 0.1, "backend": {"input": 0, "output": 0}}}
    assert P.pace(t) == {"explained": 31.0, "pitched": 95.0, "started": 101.0, "turns": 5, "questions": 3}
    assert "said what it does at 0:31, pitched at 1:35, started at 1:41; 3 of its 5 turns" in P.summary("ian", t, None)
    assert P.pace({"timeline": tl[:2]})["explained"] is None and P.pace({"timeline": tl[:2]})["pitched"] is None


# --- the earpiece, mirrored in the persona check (live.director; Ian, 2026-11-19) -------------------------------

def looks(*answers, delay=0.0):
    """A director_fn answering each look with the next of `answers` (then no whisper); the users it was given."""
    users, it = [], iter(answers)

    def fn(user):
        users.append(user)
        if delay:
            import time
            time.sleep(delay)
        return next(it, {"read": "", "plan": [], "next": None, "changed": False, "kind": None, "about": None,
                         "note": None})
    return fn, users


def look(note, *, kind="when_it_fits", about="pitch", plan=None, read="r"):
    return {"read": read, "plan": plan or [{"item": "Meet them a little", "state": "open"}], "next": None,
            "changed": True, "kind": kind, "about": about, "note": note}


def earpiece_cfg(cfg, **over):
    cfg["live"]["director"] = {**cfg["live"]["director"], "enabled": True, "every_lines": 1, "every_words": 1,
                               "every_secs": 1000, "company_every_secs": 1000, "silence_secs": [], "min_gap_secs": 0,
                               **over}
    cfg["live"]["page"]["engaged_merge_ms"] = 100
    return cfg


def test_the_earpiece_looks_after_a_listener_turn_keeps_its_plan_and_whispers_at_once(cfg, monkeypatch, tmp_path):
    """`persona --director`: one background task on the page's triggers (here turn_end, after each listener turn),
    its plan given back on the next look, a whisper sent at once as notes.md `direct`, the offer cue off, and each
    whisper recorded with the guide's next turn."""
    earpiece_cfg(cfg)
    cfg["live"]["page"]["offer_nudge"] = {"after_secs": 0, "max_secs": 0, "offered": "\\bwhat i do\\b"}
    monkeypatch.setattr(P, "TICK_SECS", 0.05)
    monkeypatch.setattr(P, "GUIDE_WAIT_SECS", 3)
    ws = scripted_guide(["Hey, Dan! I'm Ailsa.", "Ooh, cabinets. Plywood or the real thing?", "Ha. Brave.", "Mm."])
    use_ws(monkeypatch, ws)
    plan = [{"item": "Meet them a little", "state": "done"}, {"item": "Find what lights them up", "state": "open"}]
    fn, users = looks(look("His cabinets are the thread: guess what he's building them for.", about="interest",
                           plan=plan, read="Cabinets, warm."))

    def reply(timeline, moment, said=iter(["I build cabinets for a living, mostly kitchens these days.",
                                           "Plywood, mostly, honestly."])):
        import time
        time.sleep(0.3)
        return next(said)
    t = arun(P.converse(cfg, DAN, tmp_path / "out", max_turns=2, reply_fn=reply, sim_kwargs=FAST, handoff="Dan",
                        director=True, director_fn=fn))
    notes = [m["content"] for m in ws.sent if m["type"] == "session.instructions.append"]
    want = live.notes()["direct"].replace("{note}", "His cabinets are the thread: guess what he's building them for.")
    assert notes.count(want) == 1 and live.notes()["offer_nudge"] not in notes
    first = t["director"][0]
    assert first["trigger"] == "turn_end" and first["delivered"] and first["template"] == "direct"
    assert first["sent_t"] is not None and first["next_turn"] and first["next_turn_t"] >= first["sent_t"]
    assert "guide: Hey, Dan! I'm Ailsa." in users[0] and "start from the default plan" in users[0]
    assert len(users) >= 2 and "1. [done] Meet them a little" in users[1] and "Cabinets, warm." in users[1]
    assert "(when_it_fits) His cabinets are the thread" in users[1]  # its whisper, listed apart
    assert "earpiece: " in P.summary("builder", t, None)


def test_the_verdict_is_the_pages():
    """live.js directorVerdict, mirrored: whispers go at once (even while the listener speaks), only the gap from
    the last whisper holds one back; "now" notes are exempt from it but dropped once the listener has spoken since
    the look was asked for, and held while they are mid-line; stale kinds go after the start."""
    base = dict(here=True, started=False, explained=False, offered=False, spoke_since_ask=False,
                listener_speaking=False, now=100.0, sent_at=None, min_gap=20)
    w = look("Pitch the peregrine.")
    assert P.verdict(w, **{**base, "listener_speaking": True, "spoke_since_ask": True}) == "send"
    assert P.verdict(w, **{**base, "sent_at": 90.0}) == "drop" and P.verdict(w, **{**base, "sent_at": 79.0}) == "send"
    now = look("They said yes: start it.", kind="now", about="start")
    assert P.verdict(now, **{**base, "sent_at": 95.0}) == "send"
    assert P.verdict(now, **{**base, "spoke_since_ask": True}) == "drop"
    assert P.verdict(now, **{**base, "listener_speaking": True}) == "hold"
    for about in ("offer", "pitch", "interest", "start"):
        assert P.verdict(look("x.", about=about), **{**base, "started": True}) == "drop"
    assert P.verdict(look("Answer their question.", about="unanswered"), **{**base, "started": True}) == "send"
    assert P.verdict(look("Offer it.", about="offer"), **{**base, "explained": True}) == "drop"
    assert P.verdict(look("Offer it.", about="offer"), **{**base, "offered": True}) == "drop"
    assert P.verdict({**w, "changed": False}, **base) == "drop" and P.verdict(w, **{**base, "here": False}) == "drop"
    N = live.notes()
    assert P.whisper_note(N, now) == ("direct_start", N["direct_start"].replace("{note}", now["note"]))
    assert P.whisper_note(N, look("Quiet, as asked.", kind="now", about="quiet"))[0] == "direct_now"
    assert P.whisper_note(N, w)[0] == "direct"


def test_a_second_whisper_within_the_gap_is_dropped_in_the_conversation(cfg, monkeypatch, tmp_path):
    earpiece_cfg(cfg, min_gap_secs=60)
    monkeypatch.setattr(P, "TICK_SECS", 0.05)
    monkeypatch.setattr(P, "GUIDE_WAIT_SECS", 3)
    ws = scripted_guide(["Hey, Dan!", "Ooh, cabinets.", "Ha. Brave.", "Mm."])
    use_ws(monkeypatch, ws)
    fn, _ = looks(look("Guess what the cabinets are for.", about="interest"),
                  look("Pitch the cabinets, a chapter on plywood's history.", about="pitch"))

    def reply(timeline, moment, said=iter(["I build cabinets.", "Plywood, mostly."])):
        import time
        time.sleep(0.3)
        return next(said)
    t = arun(P.converse(cfg, DAN, tmp_path / "out", max_turns=2, reply_fn=reply, sim_kwargs=FAST, handoff="Dan",
                        director=True, director_fn=fn))
    got = [(x["about"], x.get("delivered")) for x in t["director"] if x.get("note")]
    assert got[:2] == [("interest", True), ("pitch", False)]


def test_a_look_in_flight_is_awaited_and_its_cost_logged_when_the_conversation_ends(cfg, monkeypatch, tmp_path):
    """Default path (no director_fn): director.decide, its cost logged to build_costs.jsonl. A look still running
    when the conversation ends is waited for, so its cost is never lost; it is not delivered."""
    earpiece_cfg(cfg)
    monkeypatch.setattr(P, "TICK_SECS", 0.05)
    monkeypatch.setattr(P, "GUIDE_WAIT_SECS", 2)
    ws = scripted_guide(["Hey, Dan!", "Ooh, cabinets."])
    use_ws(monkeypatch, ws)
    import time as _t

    def slow(cfg_, log_run, user, log_extra=None):
        _t.sleep(1.5)
        return {**look("Pitch the cabinets."), "ms": 1500, "cost_usd": 0.004}
    monkeypatch.setattr(P.live_director, "decide", slow)
    t = arun(P.converse(cfg, DAN, tmp_path / "out", max_turns=1, reply_fn=lambda tl, m: "I build cabinets.",
                        sim_kwargs=FAST, handoff="Dan", director=True))
    assert t["director"] and t["director"][0]["ms"] == 1500 and not t["director"][0].get("delivered")
    costs = [json.loads(l) for l in S.COSTS.read_text().splitlines()]
    assert [c["usd"] for c in costs if c["what"] == "claude director"] == [0.004]
    assert not [m for m in ws.sent if m["type"] == "session.instructions.append"]


def test_two_failed_looks_take_the_earpiece_down(cfg, monkeypatch, tmp_path):
    earpiece_cfg(cfg)
    monkeypatch.setattr(P, "TICK_SECS", 0.05)
    monkeypatch.setattr(P, "GUIDE_WAIT_SECS", 3)
    ws = scripted_guide(["Hey, Dan!", "Ooh, cabinets.", "Ha.", "Mm.", "Right."])
    use_ws(monkeypatch, ws)
    fn, users = looks({"error": "timeout"}, {"error": "timeout"}, look("Pitch it."))

    def reply(timeline, moment, said=iter(["I build cabinets.", "Plywood.", "Yep."])):
        import time
        time.sleep(0.3)
        return next(said)
    t = arun(P.converse(cfg, DAN, tmp_path / "out", max_turns=3, reply_fn=reply, sim_kwargs=FAST, handoff="Dan",
                        director=True, director_fn=fn))
    assert len(users) == 2 and [x["error"] for x in t["director"]] == ["timeout", "timeout"]


# --- earpiece probes (persona --probe) ---------------------------------------------------------------------

def test_a_probe_injects_its_whisper_at_its_step_and_records_the_guides_next_turn(cfg, monkeypatch, tmp_path):
    """No Claude at all (llm.call is refused here): a scripted listener and an injected whisper; the result has
    the whisper's next guide turn, the turn that took it up and the turns before each mark."""
    monkeypatch.setattr(P, "GUIDE_WAIT_SECS", 2)
    monkeypatch.setitem(P.PROBES, "T", {"what": "test", "steps": [
        ("guide",), ("say", "Hi, I saw a falcon."),
        ("whisper", "when_it_fits", "pitch", "When the falcon story is over, pitch a chapter on falcons.", r"chapter"),
        ("guide",), ("mark", "end"), ("say", "Yeah."), ("guide",)]})
    ws = scripted_guide(["Hey, Sam!", "Ooh, a falcon!", "Want a chapter on how falcons came back?"])
    use_ws(monkeypatch, ws)
    r = arun(P.probe(cfg, "T", tmp_path / "out", sim_kwargs=FAST))
    notes = [m["content"] for m in ws.sent if m["type"] == "session.instructions.append"]
    assert notes == [live.notes()["direct"].replace("{note}", "When the falcon story is over, pitch a chapter on falcons.")]
    w = r["whispers"][0]
    assert w["next_turn"] == "Ooh, a falcon!" and w["took_up"]["turn"] == 2 and "came back" in w["took_up"]["text"]
    assert w["turns_before"] == {"end": 1} and w["took_up_before"] == {"end": False}
    assert (tmp_path / "out" / "probe-T.json").exists() and "taken up at turn 2" in P.probe_summary("T", r)
    spoken = [m["content"] for m in ws.sent if m["type"] == "session.commentary.append"]
    assert spoken == [live.notes()["handoff"].replace("{name}", "Sam")]


def test_the_probes_are_scripted_short_and_their_whispers_avoid_now_and_say():
    import re as _re
    assert set(P.PROBES) == {"A", "B", "C", "D"}
    for name, spec in P.PROBES.items():
        ws_ = [s for s in spec["steps"] if s[0] == "whisper"]
        assert ws_ and all(not _re.search(r"\b(now|say)\b", s[3], _re.I) and len(s[3].split()) <= 25 for s in ws_)
    assert len([s for s in P.PROBES["D"]["steps"] if s[0] == "whisper"]) == 2
    assert [s[1] for s in P.PROBES["A"]["steps"] if s[0] == "gap"] == [1.5, 1.5]


# --- review of the earpiece (2026-11-20): the persona mirror keeps to the page ---------------------------------

class EarSim:
    """Just what the Earpiece reads of a Sim: the timeline, the guide's turns, tool calls, who is audible, the clock."""

    def __init__(self, timeline=(), turns=(), t=0.0, speaking=False):
        self.timeline, self.turns, self.calls, self.t = list(timeline), list(turns), [], t
        self.speech, self.speaking, self.audio_last, self.notes = b"", speaking, 0.0, []
        self.closed = type("Closed", (), {"is_set": lambda self: False})()

    def now(self):
        return self.t

    async def note(self, kind, text):
        self.notes.append((kind, text))


def ear(cfg, sim, fn=None):
    return P.Earpiece(cfg, sim, profile="(new)", notes=live.notes(), decide_fn=fn or looks()[0])


def test_the_persona_counts_a_turn_as_the_page_does(cfg):
    """live.js countLine merges a line into the turn only if it began within engaged_merge_ms of the last one's end;
    the persona summed every line since the guide's, so three 5-word fragments 2.5 s apart made a 15-word turn_end
    the page never makes."""
    cfg["live"]["director"] = {**cfg["live"]["director"], "every_words": 12, "every_lines": 4, "silence_secs": []}
    cfg["live"]["page"]["engaged_merge_ms"] = 1500
    frag = "so I saw this bird"

    def timeline(gap):
        out, t = [{"t": 0.0, "who": "guide", "text": "Hi there!"}], 1.0
        for _ in range(3):
            out.append({"t": t, "end": t + 1.0, "who": "listener", "text": frag + " today"})
            t += 1.0 + gap
        return out
    apart = EarSim(timeline(2.5), t=20.0)
    e = ear(cfg, apart)
    e.seen = len(e.talk())  # no new lines: only the turn's words could fire it
    e.asked_at = 20.0
    assert e.trigger(20.0) is None
    close = EarSim(timeline(0.5), t=20.0)
    e = ear(cfg, close)
    e.seen, e.asked_at = len(e.talk()), 20.0
    assert e.trigger(20.0) == "turn_end"


def test_the_persona_drops_an_offer_whisper_when_the_guide_just_offered(cfg):
    """live.js counts the guide's own "Want to hear what I do?" (offer_nudge.offered) as offered; the persona only
    counted its own whispers."""
    cfg["live"]["page"]["offer_nudge"] = {"after_secs": 0, "max_secs": 0, "offered": "\\bwhat i do\\b"}
    sim = EarSim([{"t": 0.0, "who": "guide", "text": "Hey! Want to hear what I do?"}],
                 turns=[{"t": 0.0, "end": 1.0, "text": "Hey! Want to hear what I do?"}], t=5.0)
    e = ear(cfg, sim)
    rec = {"t": 4.0}
    arun(e.deliver(look("Offer what you do, bridged from the hello.", about="offer"), rec))
    assert rec["delivered"] is False and not sim.notes


def test_a_plain_no_to_the_offer_to_chat_ends_the_persona_earpiece_and_its_started_look(cfg):
    """The page rests on a plain no after started_step_back and forgets the started look (dirForget); the persona
    used to ask it as soon as the listener answered, no or yes."""
    sim = EarSim([{"t": 0.0, "who": "guide", "text": "Hi!"}], t=10.0)
    e = ear(cfg, sim)
    e.on_started(step_back=True)
    assert not e.here()
    sim.timeline.append({"t": 11.0, "end": 12.0, "who": "listener", "text": "No thanks, go ahead."})
    assert not e.here() and e.event is None and e.trigger(15.0) != "started"
    sim2 = EarSim([{"t": 0.0, "who": "guide", "text": "Hi!"}], t=10.0)
    e2 = ear(cfg, sim2)
    e2.on_started(step_back=True)
    sim2.timeline.append({"t": 11.0, "end": 12.0, "who": "listener", "text": "Sure, let's chat a bit."})
    assert e2.here() and e2.trigger(15.0) == "started"
    assert P.declines_chat("Um, no, go ahead.") and not P.declines_chat("No wait, tell me about falcons?")


def test_the_persona_says_how_long_ago_the_last_line_ended_while_the_guide_talks(cfg):
    """silence_secs is the time since the last line ended, as on the page; it was 0 whenever anyone spoke."""
    sim = EarSim([{"t": 0.0, "who": "guide", "text": "Hi!"},
                  {"t": 2.0, "end": 5.0, "who": "listener", "text": "I made salmon tonight."}],
                 turns=[{"t": 0.0, "end": 1.0, "text": "Hi!"}], t=20.0, speaking=True)
    users = []
    e = ear(cfg, sim, fn=lambda user: users.append(user) or {**looks()[0](user)})

    async def go():
        e.ask("cadence", 20.0)
        await e.inflight
    arun(go())
    assert "the guide is speaking" in users[0] and "the listener's, 15 seconds ago" in users[0]


def test_with_the_persona_earpiece_down_the_pages_clocks_come_back_in_a_lull(cfg, monkeypatch, tmp_path):
    """live.js: two failures and the offer clock and the plain pitch nudges come back, lull only (fb); the persona
    kept the earpiece's clocks (no offer clock) for the whole run."""
    earpiece_cfg(cfg)
    cfg["live"]["page"]["offer_nudge"] = {"after_secs": 0, "max_secs": 0, "offered": "\\bwhat i do\\b"}
    monkeypatch.setattr(P, "TICK_SECS", 0.05)
    monkeypatch.setattr(P, "GUIDE_WAIT_SECS", 2)
    ws = scripted_guide(["Hey, Dan!", "Ooh, cabinets.", "Ha."])  # then it says nothing: a lull
    use_ws(monkeypatch, ws)
    fn, users = looks({"error": "timeout"}, {"error": "timeout"})

    def reply(timeline, moment, said=iter(["I build cabinets.", "Plywood.", "Yep.", "Hello?"])):
        import time
        time.sleep(0.3)
        return next(said)
    t = arun(P.converse(cfg, DAN, tmp_path / "out", max_turns=4, reply_fn=reply, sim_kwargs=FAST, handoff="Dan",
                        director=True, director_fn=fn))
    assert [x["error"] for x in t["director"]] == ["timeout", "timeout"]
    notes = [m["content"] for m in ws.sent if m["type"] == "session.instructions.append"]
    assert notes.count(live.notes()["offer_nudge"]) == 1
    order = [(x["who"], x.get("text")) for x in t["timeline"] if x["who"] in ("listener", "app")]
    assert order.index(("listener", "Yep.")) < order.index(("app", live.notes()["offer_nudge"])) \
        < order.index(("listener", "Hello?"))  # not while the guide answered every line: in the lull after "Ha."
