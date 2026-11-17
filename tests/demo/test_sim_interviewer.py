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
    assert {"fragments", "asksback"} <= {p["key"] for p in debrief}  # Ian's own way of answering; a tester who asks back
    known = {p["key"]: p["known"] for p in intro}
    assert known["curious"] == "Priya" and known["corrected"] == "Rolando" and known["mumble"] is None
    assert "Hi, is this Rolando?" in IV.instructions("intro", SI.values_for(next(p for p in intro if p["key"] == "corrected")))
    enjoyed = next(p for p in debrief if p["key"] == "enjoyed")
    assert enjoyed["moments"] == [{"at_s": 200.0, "clock": "3:20", "words": "Wait, what's a leverage point?"}]
    assert enjoyed["heard"] == "10 of 10" and "moments:" not in enjoyed["text"] and enjoyed["text"].startswith("It went well")
    v = SI.values_for(enjoyed)
    assert 'About 3 minutes in, they asked "Wait, what\'s a leverage point?"' in v["asking"]
    assert "{{" not in IV.instructions("debrief", v)


INTRO = [
    [says("Hi, is this Priya?")],
    [says("Welcome, Priya, and thank you for making the time. Ian built what I'm about to show you, and he asked me to gather your feedback.")],
    [says("You'll see in a moment. Here's how this goes: I'll hand you over to someone else, you'll talk a little, "
          "then listen to something for about ten minutes.")],
    [says("You can interrupt and ask questions whenever you like, and there's an I'm done button. Afterwards you "
          "come back to me for a few minutes. Any questions before we start?")],
    [says("I'll hand you over now."), *calls("handoff_to_guide", d="d2")],
]


def test_an_intro_run_end_to_end(cfg, monkeypatch, tmp_path):
    ws = FakeWS(INTRO)
    use_ws(monkeypatch, ws)
    lines = iter(["Yes, that's me.", "Thanks. So what is this?", "Okay.", "No, I'm ready."])
    p = next(x for x in SI.personas("intro") if x["key"] == "curious")
    t = arun(SI.converse(cfg, p, tmp_path / "out", reply_fn=lambda tl, m: next(lines), sim_kwargs=FAST, wait_secs=2))
    assert t["stop"] == "handed over"
    assert t["checks"] == {**t["checks"], "ok": True, "name_saved": True, "saved_as": "Priya", "greeted_by_name": True,
                           "said_ian": True, "handed_over": True, "product_words": [], "deflected": True}
    assert set(t["checks"]["explained"]) == {"someone_else", "listen", "interrupt", "done", "afterwards"}
    sent = [m for m in ws.sent if m["type"] != "session.input_audio.append"]
    assert sent[0]["session"]["audio"]["output"]["voice"] == "cedar" and sent[0]["session"]["store"] is True
    assert "Hi, is this Priya?" in sent[0]["session"]["instructions"]
    assert sent[1]["type"] == "session.commentary.append" and sent[1]["content"].startswith("Begin now")
    assert "is this Priya" in sent[1]["content"]
    outs = [json.loads(m["item"]["output"]) for m in sent if m["type"] == "response.item.create"]
    assert outs == [{"handed_over": True}]
    saved = json.loads((tmp_path / "out" / "intro-curious.json").read_text())
    assert saved["checks"]["ok"] and SI.S.spent(SI.COSTS) > 0  # the session's cost is in the demo's own file


def test_a_rushed_intro_like_ians_fails_the_checks():
    """Ian's own intro (2026-11-16): three lines in 15 seconds."""
    p = {"mode": "intro", "name": "Ian", "known": "Ian", "asks": False, "moments": []}
    tl = [{"t": 3.7, "who": "guide", "text": "Thank you for making the time. What's your name?"},
          {"t": 7.3, "who": "listener", "text": "I'm, I'm Ian"},
          {"t": 8.1, "who": "guide", "text": "Ian. Did I get that right?"}, {"t": 10.9, "who": "listener", "text": "Yes"},
          {"t": 14.9, "who": "guide", "text": "Thanks, Ian. I'd like a few minutes afterwards to hear how it went. I'll hand you over now."}]
    k = SI.code_checks(p, tl, [], "handover")
    assert k["name_saved"] and not k["greeted_by_name"] and k["explained"] == ["afterwards"] and not k["ok"]


def test_a_corrected_name_is_the_one_saved():
    p = {"mode": "intro", "name": "Roland", "known": "Rolando", "asks": False, "moments": []}
    tl = [{"t": 1, "who": "guide", "text": "Hi, is this Rolando?"}, {"t": 2, "who": "listener", "text": "No, it's Roland."},
          {"t": 3, "who": "guide", "text": "Roland. Did I get that right?"}]
    assert SI.code_checks(p, tl, [])["saved_as"] == "Roland"
    assert SI.code_checks(p, tl[:1] + [{"t": 2, "who": "listener", "text": "Yes."}], [])["saved_as"] == "Rolando"


def test_a_handover_said_without_the_tool_counts_as_the_page_would(cfg, monkeypatch, tmp_path):
    cfg.setdefault("demo", {}).setdefault("interviewer", {}).setdefault("page", {})["act_grace_ms"] = 50
    use_ws(monkeypatch, FakeWS([*INTRO[:4], [says("I'll hand you over now.")]]))
    lines = iter(["Yes.", "Okay.", "Okay.", "No."])
    p = {**next(x for x in SI.personas("intro") if x["key"] == "terse"), "name": "Priya", "known": "Priya"}
    t = arun(SI.converse(cfg, p, tmp_path, reply_fn=lambda tl, m: next(lines), sim_kwargs=FAST, wait_secs=2))
    assert t["stop"] == "handover by page" and t["checks"]["handover_by_page"] and t["checks"]["ok"]


DEBRIEF = [
    [says("Welcome back, Priya, and thanks for staying on. What's one thing that stood out to you?")],
    [says("The meeting story, okay. About 3 minutes in, you asked \"Wait, what's a leverage point?\". How did that go?")],
    [says("Quick and clear, good to know. How was the voice for you?")],
    [says("Ian wants to know what worked and what didn't. Is this something you'd use?")],
    [says("Is there anything I didn't ask that I should have?")],
    [says("Thank you, Priya. That really helps.")],
]
ANSWERS = ["The meeting story.", "Quick and clear.", "Warm. What is Ian looking for?", "Yes, if I picked it.", "No, that's it.", "Bye!"]


def test_a_debrief_run_end_to_end(cfg, monkeypatch, tmp_path):
    use_ws(monkeypatch, FakeWS([*DEBRIEF[:5], [*DEBRIEF[5], *calls("end_debrief")]]))
    lines = iter(ANSWERS)
    p = next(x for x in SI.personas("debrief") if x["key"] == "enjoyed")
    t = arun(SI.converse(cfg, p, tmp_path, reply_fn=lambda tl, m: next(lines), sim_kwargs=FAST, wait_secs=2))
    assert t["stop"] == "ended"
    k = t["checks"]
    assert k["topics"] == ["stood_out", "asking", "voice", "use"] and k["closing"] and not k["first_question_broad"]
    assert k["uses_moment"] and k["unanswered"] == [] and k["silences"] == 0
    assert k["thanked_by_name"] and k["ended"] and k["ok"]


def test_a_voice_that_only_hums_is_told_they_are_waiting_and_fails_the_checks(cfg, monkeypatch, tmp_path):
    """Ian's debrief, replayed: an "Mm-hmm." for an answer and nothing after a direct question. The harness
    sends the page's `waiting` note, and the code checks fail the run."""
    cfg.setdefault("demo", {}).setdefault("interviewer", {}).setdefault("page", {})["reply_wait_ms"] = 300
    ws = FakeWS([[says("Welcome back. So take me through what that was like, from the start.")], [says("Mm-hmm.")], []])
    use_ws(monkeypatch, ws)
    lines = iter(["Yeah, I mean it was pretty good.", "What's a different question?", SI.SILENT, SI.SILENT])
    p = next(x for x in SI.personas("debrief") if x["key"] == "fragments")
    t = arun(SI.converse(cfg, p, tmp_path, reply_fn=lambda tl, m: next(lines), sim_kwargs=FAST, wait_secs=1))
    notes = [m["content"] for m in ws.sent if m["type"] == "session.instructions.append"]
    assert len(notes) == 2 and all("waiting for you" in n for n in notes)  # after each of their two lines
    k = t["checks"]
    assert k["first_question_broad"] and k["unanswered"] == ["What's a different question?"] and k["silences"] >= 1
    assert not k["ok"]


def test_code_checks_catch_product_talk_and_a_missed_deflection():
    p = {"mode": "intro", "name": "Hannah", "asks": True, "moments": []}
    tl = [{"t": 1, "who": "guide", "text": "Thanks for coming. You're about to hear an audio chapter made for you."}]
    k = SI.code_checks(p, tl, [{"name": "set_listener_name", "args": {"name": "hannah"}}])
    assert k["product_words"][0]["word"].lower() == "audio" and not k["deflected"] and not k["ok"]
    assert k["name_saved"]  # case doesn't matter
    d = {"mode": "debrief", "name": "Tom", "asks": False, "moments": [{"at_s": 250.0, "words": "Is that actually true?"}]}
    tl = [{"t": i, "who": "guide", "text": x} for i, x in enumerate([
        "Take me through what that was like, from the start.", "How would you describe it to a friend?",
        "Did anything make you want to stop it and ask something?", "Thanks so much."])]
    k = SI.code_checks(d, tl, [])
    assert k["first_question_broad"] and not k["uses_moment"] and not k["thanked_by_name"] and not k["closing"] and not k["ok"]


def test_unanswered_is_a_direct_question_with_no_reply_in_words():
    g = lambda t, s: {"t": t, "who": "guide", "text": s}  # noqa: E731
    me = lambda t, s: {"t": t, "who": "listener", "text": s}  # noqa: E731
    assert SI.unanswered([me(1, "What does Ian want to know?"), g(2, "Hmm."), me(3, "Hello?"), g(4, "I'm here, sorry.")]) == [
        "What does Ian want to know?"]
    assert SI.unanswered([me(1, "What does Ian want to know?"), g(2, "What worked and what didn't."), me(3, "Okay.")]) == []
    assert SI.unanswered([me(1, "Are you there?"), {"t": 2, "who": "quiet", "secs": 20}]) == ["Are you there?"]
    assert SI.unanswered([me(1, "Anything else?")]) == []  # the run ended on it


def test_grade_checks_quotes(cfg, monkeypatch, tmp_path):
    t = {"kind": "intro", "persona": "curious", "description": "Asks what this is.",
         "values": {"meeting": "You haven't met them before.", "first_step": "Say hello and ask their name."},
         "checks": {"ok": True},
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
    assert "WHAT YOU MAY AND MAY NOT SAY" in seen["prompt"] and "person: What is this?" in seen["prompt"]
    assert [m["found"] for m in g["product_talk"]] == [True, False] and g["quotes_not_found"] == 1
    assert json.loads((tmp_path / "intro-curious.grade.json").read_text())["score"] == 9


def test_a_debrief_thanks_without_the_tool_closes_as_the_page_would(cfg, monkeypatch, tmp_path):
    # Live (2026-11-16): "Thank you, Hannah. That really helps." after the closing question, and no end_debrief.
    cfg.setdefault("demo", {}).setdefault("interviewer", {}).setdefault("page", {})["act_grace_ms"] = 50
    use_ws(monkeypatch, FakeWS(DEBRIEF))
    lines = iter(ANSWERS)
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


def test_the_page_close_reads_curly_apostrophes():
    """Review: GPT-Live writes didn’t; the closing question never matched, so the debrief never closed."""
    g = lambda s: {"who": "guide", "text": s}
    me = lambda s: {"who": "listener", "text": s}
    assert SI.page_closes([g("Is there anything I didn’t ask?"), me("No."), g("Thank you, Sam.")], IV.PAGE_DEFAULTS)
    old = {**IV.PAGE_DEFAULTS, "closing_question": r"\banything I didn'?t ask\b"}  # a config with the old pattern
    assert SI.page_closes([g("Is there anything I didn’t ask?"), me("No."), g("Thank you, Sam.")], old)
