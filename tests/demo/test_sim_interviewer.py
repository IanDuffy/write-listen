"""The interviewer's simulated checks (demo/sim_interviewer.py), offline: a scripted fake WebSocket plays
GPT-Live, the tester's lines are scripted, and nothing reaches OpenAI or Anthropic."""

import asyncio
import base64
import json

import httpx
import pytest

from demo import interviewer as IV
from demo import sim_interviewer as SI
from voice.gpt_live import session as live
from voice.gpt_live import sim as S


def arun(coro):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, coro).result()


class FakeWS:
    """GPT-Live's side: answers session.start and close; speaks the next scripted turn after the opening note
    and after each line the tester finishes."""

    def __init__(self, turns):
        self.sent, self.q, self.turns, self.talking = [], asyncio.Queue(), iter(turns), False

    async def send(self, raw):
        m = json.loads(raw)
        self.sent.append(m)
        if m["type"] == "session.start":
            self.push({"type": "session.started", "session": {"id": "live_test", "expires_at": 1}})
        elif m["type"] == "session.close":
            self.push({"type": "session.closed", "reason": "close_requested", "usage": {"seconds": 3.0}})
        elif m["type"] == "session.commentary.append":
            self.next_turn()
        elif m["type"] == "session.input_audio.append":
            audible = any(base64.b64decode(m["audio"]))
            if audible:
                self.talking = True
            elif self.talking:
                self.talking = False
                self.next_turn()

    def next_turn(self):
        for e in next(self.turns, []):
            self.push(e)

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


def says(text):
    return {"type": "session.output_transcript.delta", "delta": text}


def calls(name, args=None, d="d1"):
    return [{"type": "response.event", "delegation_id": d, "event": {"type": "response.output_item.done", "item": {
                "type": "function_call", "call_id": f"c_{name}", "name": name, "arguments": json.dumps(args or {})}}},
            {"type": "response.event", "delegation_id": d, "event": {"type": "response.completed", "response": {}}}]


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    def no_network(*a, **k):
        raise AssertionError("a test tried to reach the network")
    monkeypatch.setattr(httpx, "post", no_network)
    monkeypatch.setattr(S.websockets, "connect", no_network)
    monkeypatch.setattr(live, "api_key", lambda: "sk-test")
    monkeypatch.setattr(SI, "COSTS", tmp_path / "costs.jsonl")
    monkeypatch.setattr(SI, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(S, "CACHE", tmp_path / "cache")
    from stages import llm
    monkeypatch.setattr(llm, "call", no_network)


def use_ws(monkeypatch, ws):
    async def connect(url, additional_headers=None, max_size=None):
        return ws
    monkeypatch.setattr(S.websockets, "connect", connect)


FAST = {"quiet_secs": 0.15, "settle_secs": 0.1, "verbose": False, "tts_fn": lambda text, voice: b"\x00\x40" * 2400}


def test_personas_cover_both_modes_and_testers_who_ask():
    intro, debrief = SI.personas("intro"), SI.personas("debrief")
    assert len(intro) >= 5 and sum(p["asks"] for p in intro) >= 3
    assert len(debrief) >= 5 and any(not p["moments"] for p in debrief)
    enjoyed = next(p for p in debrief if p["key"] == "enjoyed")
    assert enjoyed["moments"] == [{"at_s": 200.0, "clock": "3:20", "words": "Wait, what's a leverage point?"}]
    assert enjoyed["heard"] == "10 of 10" and "moments:" not in enjoyed["text"] and enjoyed["text"].startswith("It went well")
    v = SI.values_for(enjoyed)
    assert 'About 3 minutes in, you asked "Wait, what\'s a leverage point?"' in v["question_three"]
    assert "{{" not in IV.instructions("debrief", v)


def test_an_intro_run_end_to_end(cfg, monkeypatch, tmp_path):
    ws = FakeWS([
        [says("Hi, thanks for making the time. What's your name?")],
        [says("Priya. Did I get that right?")],
        [*calls("set_listener_name", {"name": "Priya"}), says("You'll see in a moment.")],
        [says("I'd like a few minutes with you afterwards. I'll hand you over now."), *calls("handoff_to_guide", d="d2")],
    ])
    use_ws(monkeypatch, ws)
    lines = iter(["I'm Priya.", "Yes. So what is this?", "Okay."])
    p = next(x for x in SI.personas("intro") if x["key"] == "curious")
    t = arun(SI.converse(cfg, p, tmp_path / "out", reply_fn=lambda tl, m: next(lines), sim_kwargs=FAST, wait_secs=2))
    assert t["stop"] == "handed over"
    assert t["checks"] == {**t["checks"], "ok": True, "name_saved": True, "handed_over": True, "product_words": [],
                           "deflected": True}
    sent = [m for m in ws.sent if m["type"] != "session.input_audio.append"]
    assert sent[0]["session"]["audio"]["output"]["voice"] == "cedar" and sent[0]["session"]["store"] is True
    assert sent[1]["type"] == "session.commentary.append" and sent[1]["content"].startswith("Begin now")
    outs = [json.loads(m["item"]["output"]) for m in sent if m["type"] == "response.item.create"]
    assert outs == [{"saved": True, "name": "Priya"}, {"handed_over": True}]
    saved = json.loads((tmp_path / "out" / "intro-curious.json").read_text())
    assert saved["checks"]["ok"] and SI.S.spent(SI.COSTS) > 0  # the session's cost is in the demo's own file


def test_a_handover_said_without_the_tool_counts_as_the_page_would(cfg, monkeypatch, tmp_path):
    cfg.setdefault("demo", {}).setdefault("interviewer", {}).setdefault("page", {})["act_grace_ms"] = 50
    use_ws(monkeypatch, FakeWS([[says("Thanks for coming. Your name?")],
                                [*calls("set_listener_name", {"name": "Tom"}), says("Tom. I'll hand you over now.")]]))
    lines = iter(["Tom."])
    p = next(x for x in SI.personas("intro") if x["key"] == "terse")
    t = arun(SI.converse(cfg, p, tmp_path, reply_fn=lambda tl, m: next(lines), sim_kwargs=FAST, wait_secs=2))
    assert t["stop"] == "handover by page" and t["checks"]["handover_by_page"] and t["checks"]["ok"]


def test_a_debrief_run_end_to_end(cfg, monkeypatch, tmp_path):
    use_ws(monkeypatch, FakeWS([
        [says("Welcome back, Priya. Take me through what that was like, from the start.")],
        [says("Was there a moment you were most into it? And a moment you drifted, or something felt off?")],
        [says("About 3 minutes in, you asked \"Wait, what's a leverage point?\". How did that go?")],
        [says("How would you describe it to a friend?")],
        [says("If another one were waiting for you tomorrow morning, would you listen?")],
        [says("Is there anything I didn't ask that I should have?")],
        [says("Thank you, Priya. That really helps."), *calls("end_debrief")],
    ]))
    lines = iter(["It was lovely.", "The meeting story; the numbers lost me.", "Quick and clear.",
                  "A podcast that knows you.", "Yes, if I picked it.", "No, that's it."])
    p = next(x for x in SI.personas("debrief") if x["key"] == "enjoyed")
    t = arun(SI.converse(cfg, p, tmp_path, reply_fn=lambda tl, m: next(lines), sim_kwargs=FAST, wait_secs=2))
    assert t["stop"] == "ended"
    k = t["checks"]
    assert k["questions_asked"] == [1, 2, 3, 4, 5] and k["all_five_in_order"] and k["closing"]
    assert k["q3_uses_moment"] and k["thanked_by_name"] and k["ended"] and k["ok"]


def test_code_checks_catch_product_talk_and_a_missed_deflection():
    p = {"mode": "intro", "name": "Hannah", "asks": True, "moments": []}
    tl = [{"t": 1, "who": "guide", "text": "Thanks for coming. You're about to hear an audio chapter made for you."}]
    k = SI.code_checks(p, tl, [{"name": "set_listener_name", "args": {"name": "hannah"}}])
    assert k["product_words"][0]["word"].lower() == "audio" and not k["deflected"] and not k["ok"]
    assert k["name_saved"]  # case doesn't matter
    d = {"mode": "debrief", "name": "Tom", "asks": False, "moments": [{"at_s": 250.0, "words": "Is that actually true?"}]}
    tl = [{"t": i, "who": "guide", "text": x} for i, x in enumerate([
        "How would you describe it to a friend?", "Take me through what that was like, from the start.",
        "Did anything make you want to stop it and ask something?", "Thanks so much."])]
    k = SI.code_checks(d, tl, [])
    assert not k["all_five_in_order"] and not k["q3_uses_moment"] and not k["thanked_by_name"] and not k["ok"]


def test_grade_checks_quotes(cfg, monkeypatch, tmp_path):
    t = {"kind": "intro", "persona": "curious", "description": "Asks what this is.",
         "values": {"meeting": "You haven't met them before."}, "checks": {"ok": True},
         "timeline": [{"t": 1.0, "who": "guide", "text": "Hi, thanks for making the time."},
                      {"t": 2.0, "who": "listener", "text": "What is this?"},
                      {"t": 3.0, "who": "guide", "text": "You'll see in a moment."}]}
    path = tmp_path / "intro-curious.json"
    path.write_text(json.dumps(t))
    seen = {}

    class Res:
        text = json.dumps({"product_talk": [{"quote": "You'll see in a moment", "why": "fine"},
                                            {"quote": "made up", "why": "x"}],
                           "leading": [], "defended": [], "follow_ups": [], "tone": [], "score": 9, "summary": "Good."})

        class usage:
            cost_usd = 0.05

    def call(run, cfg_, stage, *, messages, json_schema=None, role=None, effort=None, **kw):
        seen.update(stage=stage, role=role, schema=json_schema, prompt=messages[0]["content"])
        return Res()
    from stages import llm
    monkeypatch.setattr(llm, "call", call)
    g = SI.grade(cfg, path)
    assert seen["stage"] == "interviewer_grade" and seen["role"] == "researcher" and seen["schema"] is SI.GRADE_SCHEMA
    assert "WHAT YOU NEVER SAY" in seen["prompt"] and "person: What is this?" in seen["prompt"]
    assert [m["found"] for m in g["product_talk"]] == [True, False] and g["quotes_not_found"] == 1
    assert json.loads((tmp_path / "intro-curious.grade.json").read_text())["score"] == 9


def test_a_debrief_thanks_without_the_tool_closes_as_the_page_would(cfg, monkeypatch, tmp_path):
    # Live (2026-11-16): "Thank you, Hannah. That really helps." after the closing question, and no end_debrief.
    cfg.setdefault("demo", {}).setdefault("interviewer", {}).setdefault("page", {})["act_grace_ms"] = 50
    use_ws(monkeypatch, FakeWS([
        [says("Welcome back, Priya. Take me through what that was like, from the start.")],
        [says("Was there a moment you were most into it? And a moment you drifted, or something felt off?")],
        [says("About 3 minutes in, you asked \"Wait, what's a leverage point?\". How did that go?")],
        [says("How would you describe it to a friend?")],
        [says("If another one were waiting for you tomorrow morning, would you listen?")],
        [says("Is there anything I didn't ask that I should have?")],
        [says("Thank you, Priya. That really helps.")],
    ]))
    lines = iter(["It was lovely.", "The meeting story; the numbers lost me.", "Quick and clear.",
                  "A podcast that knows you.", "Yes, if I picked it.", "No, that's it.", "Bye!"])
    p = next(x for x in SI.personas("debrief") if x["key"] == "enjoyed")
    t = arun(SI.converse(cfg, p, tmp_path, reply_fn=lambda tl, m: next(lines), sim_kwargs=FAST, wait_secs=2))
    assert t["stop"] == "closed by page" and t["checks"]["ended"] and t["checks"]["ok"]


def test_the_page_close_needs_the_closing_question_answered_first():
    page = IV.PAGE_DEFAULTS
    g = lambda s: {"who": "guide", "text": s}
    me = lambda s: {"who": "listener", "text": s}
    assert not SI.page_closes([g("Thanks, that helps. How would you describe it?")], page)
    assert not SI.page_closes([g("Is there anything I didn't ask?"), g("Thank you.")], page)
    assert SI.page_closes([g("Is there anything I didn't ask?"), me("No."), g("Thank you, Sam.")], page)
    assert not SI.page_closes([g("Is there anything I didn't ask?"), me("No."), g("Thank you, Sam."), g("Oh, one more:")], page)
