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
    # Ian's note (2026-11-18, "there was no clear exit"): one who asks to end, one who says it all at once and trails off.
    stops, trails = (next(p for p in debrief if p["key"] == k) for k in ("stops", "trails"))
    assert "I think that's about it, let's end here." in stops["text"] and SI.DONE.search(stops["text"])
    assert "yeah... that's it really." in trails["text"] and SI.DONE.search("yeah... that's it really.")
    assert not stops["moments"] and not trails["moments"]
    assert SI.values_for(stops)["farewell"] == "Thank you, Nora. That really helps Ian. That's everything from me."
    known = {p["key"]: p["known"] for p in intro}
    assert known["curious"] == "Priya" and known["corrected"] == "Rolando" and known["mumble"] is None
    assert "Hi, is this Rolando?" in IV.instructions("intro", SI.values_for(next(p for p in intro if p["key"] == "corrected")))
    enjoyed = next(p for p in debrief if p["key"] == "enjoyed")
    assert enjoyed["moments"] == [{"at_s": 200.0, "clock": "3:20", "words": "Wait, what's a leverage point?"}]
    assert enjoyed["heard"] == "10 of 10" and "moments:" not in enjoyed["text"] and enjoyed["text"].startswith("It went well")
    v = SI.values_for(enjoyed)
    assert 'About 3 minutes in, they stopped it to ask "Wait, what\'s a leverage point?"' in v["asking"]
    assert "{{" not in IV.instructions("debrief", v)
    # A persona carries what they did, and the interviewer is told it as a real tester's records would
    # (Ian's notes, 2026-11-18). `unheard` is his own second run.
    unheard = next(p for p in debrief if p["key"] == "unheard")
    assert SI.activity_for(unheard) == {
        "talk_s": 300.0, "started": True, "quiet_asks": 2, "step_backs": 2, "taps": 1, "status_asks": 1, "ready": False,
        "listened": False, "pauses": 0, "seeks": 0, "end": {"phase": "resting", "chapter_t": None, "t": None}}
    v = SI.values_for(unheard)
    assert v["did"].splitlines() == [
        "- They talked with the voice for about 5 minutes before it started making an audio piece for them.",
        '- The piece is titled "The Nine Levers".',
        "- They asked for quiet twice while the piece was being made.",
        "- While it was quiet, they tapped to talk once.",
        "- They asked how the piece was coming along once.",
        "- They heard none of the piece: it wasn't ready yet when they ended.",
        "- They pressed End demo while the voice was quiet."]
    assert v["asking"].startswith("They heard none of the piece, so there is nothing to ask here")
    assert "- They pressed End demo while the piece was being read." in SI.values_for(
        next(p for p in debrief if p["key"] == "drifted"))["did"]


INTRO = [
    [says("Hi, is this Priya?")],
    [says("Thank you for making the time, Priya. Ian built what you're about to try, and he asked me to gather your "
          "feedback. In a moment I'll pass you to someone else: you'll talk a little, then listen to something for "
          "about ten minutes. You can interrupt with questions any time, and there's an End demo button at the top "
          "of the screen whenever you want to stop. Afterwards you come back to me for a couple of minutes. "
          "Any questions, or are you ready to try it?")],
    [says("You'll see in a moment. Are you ready?")],
    [says("I'll hand you over now."), *calls("handoff_to_guide", d="d2")],
]


def test_an_intro_run_end_to_end(cfg, monkeypatch, tmp_path):
    ws = FakeWS(INTRO)
    use_ws(monkeypatch, ws)
    lines = iter(["Yes, that's me.", "Thanks. So what is this?", "Yes, I'm ready."])
    p = next(x for x in SI.personas("intro") if x["key"] == "curious")
    t = arun(SI.converse(cfg, p, tmp_path / "out", reply_fn=lambda tl, m: next(lines), sim_kwargs=FAST, wait_secs=2))
    assert t["stop"] == "handed over"
    assert t["checks"] == {**t["checks"], "ok": True, "name_saved": True, "saved_as": "Priya", "greeted_by_name": True,
                           "said_ian": True, "handed_over": True, "product_words": [], "deflected": True,
                           "one_turn": True, "questions": 3, "questions_max": 3}
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


IANS_INTRO = [  # his second run (2026-11-18): 49 seconds, an "okay" after every sentence
    (2.7, "guide", "Hi, is this Ian?"), (5.3, "listener", "This is Ian"),
    (10.7, "guide", "Ian, thank you for making the time. Ian built what I'm about to show you, and he asked me to show it to you and hear how it goes."),
    (15.5, "listener", "Okay, sounds good"),
    (20.5, "guide", "In a moment, I'll pass you to someone else. They'll talk a little, and then you'll listen to something for about ten minutes."),
    (25.9, "listener", "Okay"),
    (31.1, "guide", "You can interrupt and ask questions whenever you'd like, and there's an End demo button if you want to stop."),
    (36.3, "listener", "Okay, sounds good"),
    (41.5, "guide", "After that, you'll come back to me for a few minutes to talk about how it went."),
    (45.7, "guide", "Do you have any questions before we start?"), (46.0, "listener", "No, no, sounds good"),
    (49.5, "guide", "I'll hand you over now.")]


def test_an_intro_that_stops_after_every_sentence_like_ians_fails_the_checks():
    """Ian's notes (2026-11-18): "spoke a little too much... maybe just one question, then it saying what this
    will be like". The explanation must be one turn, and the whole intro short."""
    p = {"mode": "intro", "name": "Ian", "known": "Ian", "asks": False, "moments": []}
    tl = [{"t": t, "who": w, "text": x} for t, w, x in IANS_INTRO]
    k = SI.code_checks(p, tl, [], "handover")
    assert len(k["explained"]) == 5 and k["questions"] == 2 == k["questions_max"]
    assert not k["one_turn"] and k["seconds"] == 50 > k["seconds_max"] == SI.INTRO_SECONDS and not k["ok"]
    # The same words in one turn, in 35 seconds, pass.
    told = " ".join(x for t, w, x in IANS_INTRO[2:10] if w == "guide")
    one = [{"t": t, "who": w, "text": x} for t, w, x in (*IANS_INTRO[:2], (8.0, "guide", told),
                                                           (30.0, "listener", "No, sounds good"), (35.0, "guide", "I'll hand you over now."))]
    k = SI.code_checks(p, one, [], "handover")
    assert k["one_turn"] and k["seconds"] == 35 and k["questions"] == 2 and k["ok"]
    # A question of the tester's, or a name it had to confirm, allows one more question and more time.
    asked = [*one[:3], {"t": 30.0, "who": "listener", "text": "How long does it take?"},
             {"t": 34.0, "who": "guide", "text": "About twenty minutes. Ready?"}, {"t": 40.0, "who": "listener", "text": "Yes."},
             {"t": 48.0, "who": "guide", "text": "I'll hand you over now."}]
    k = SI.code_checks(p, asked, [], "handover")
    assert k["questions"] == 3 == k["questions_max"] and k["seconds_max"] == SI.INTRO_SECONDS + 15 and k["ok"]


def test_a_corrected_name_is_the_one_saved():
    p = {"mode": "intro", "name": "Roland", "known": "Rolando", "asks": False, "moments": []}
    tl = [{"t": 1, "who": "guide", "text": "Hi, is this Rolando?"}, {"t": 2, "who": "listener", "text": "No, it's Roland."},
          {"t": 3, "who": "guide", "text": "Roland. Did I get that right?"}]
    assert SI.code_checks(p, tl, [])["saved_as"] == "Roland"
    assert SI.code_checks(p, tl[:1] + [{"t": 2, "who": "listener", "text": "Yes."}], [])["saved_as"] == "Rolando"


def test_a_handover_said_without_the_tool_counts_as_the_page_would(cfg, monkeypatch, tmp_path):
    cfg.setdefault("demo", {}).setdefault("interviewer", {}).setdefault("page", {})["act_grace_ms"] = 50
    use_ws(monkeypatch, FakeWS([*INTRO[:2], [says("I'll hand you over now.")]]))
    lines = iter(["Yes.", "I'm ready."])
    p = {**next(x for x in SI.personas("intro") if x["key"] == "terse"), "name": "Priya", "known": "Priya"}
    t = arun(SI.converse(cfg, p, tmp_path, reply_fn=lambda tl, m: next(lines), sim_kwargs=FAST, wait_secs=2))
    assert t["stop"] == "handover by page" and t["checks"]["handover_by_page"] and t["checks"]["ok"]


DEBRIEF = [
    [says("Welcome back, Priya, and thanks for staying on. What's one thing that stood out to you?")],
    [says("The meeting story, okay. About 3 minutes in, you asked \"Wait, what's a leverage point?\". How did that go?")],
    [says("Quick and clear, good to know. What made it feel quick?")],  # a follow-up: no new topic
    [says("Ian wants to know what worked and what didn't. Is this something you'd use?")],
    [says("Is there anything I didn't ask that I should have?")],
    [says("Thank you, Priya. That really helps Ian. That's everything from me.")],
]
ANSWERS = ["The meeting story.", "Quick and clear.", "It came right back. What is Ian looking for?", "Yes, if I picked it.", "No, that's it.", "Bye!"]


def test_a_debrief_run_end_to_end(cfg, monkeypatch, tmp_path):
    """The debrief has no delegation since Ian's third run (2026-11-19): the session asks for no backend, and it
    ends as the page ends it, on the farewell."""
    cfg.setdefault("demo", {}).setdefault("interviewer", {}).setdefault("page", {})["act_grace_ms"] = 50
    ws = FakeWS(DEBRIEF)
    use_ws(monkeypatch, ws)
    lines = iter(ANSWERS)
    p = next(x for x in SI.personas("debrief") if x["key"] == "enjoyed")
    t = arun(SI.converse(cfg, p, tmp_path, reply_fn=lambda tl, m: next(lines), sim_kwargs=FAST, wait_secs=2))
    assert t["stop"] == "closed by page"
    start = next(m for m in ws.sent if m["type"] == "session.start")
    assert "delegation" not in start["session"] and not [m for m in ws.sent if m["type"] == "response.item.create"]
    k = t["checks"]
    assert k["topics"] == ["stood_out", "asking", "use"] and k["closing"] and not k["first_question_broad"]
    assert k["uses_moment"] and k["unanswered"] == [] and k["silences"] == 0
    assert k["questions"] == 4 == k["questions_max"] and k["not_happened"] == []
    assert k["more_topics"] == 2 == k["more_topics_max"]  # the opening, two more, one follow-up
    assert k["farewell"] and k["exit"] == [] and k["said_done"]  # "No, that's it." came after the closing question
    assert k["thanked_by_name"] and k["ended"] and k["ok"]


IANS_DEBRIEF = [  # his second run (2026-11-18): seven questions, and one about something that never happened
    "Welcome back, Ian, and thank you for staying on. What's one thing that stood out to you?",
    "Yeah, it does. Fewer turns before getting into the heart of it. What would have made that start feel more natural to you?",
    "That pause and then stepping in felt better to you. How was the voice itself for you to listen to?",
    "You wanted to hear more eagerness from that voice, more sense of meeting you. Is this something you'd use, or when would you?",
    "Would you want to be able to ask it things as you go? Maybe cut in?",
    "Noted. Earlier, you chose not to cut in. Did anything make you want to in that moment?",
    "Before we wrap, is there anything I didn't ask that I should have?",
    "Thank you, Ian. That really helps."]


def test_a_debrief_like_ians_fails_too_many_questions_and_one_about_what_never_happened():
    """Ian's notes (2026-11-18): "much less questions", and it asked "Earlier, you chose not to cut in" of a
    tester who had heard none of the piece."""
    p = next(x for x in SI.personas("debrief") if x["key"] == "unheard")
    tl = []
    for i, line in enumerate(IANS_DEBRIEF):
        tl += [{"t": 10 * i, "who": "guide", "text": line}, {"t": 10 * i + 5, "who": "listener", "text": "Sure."}]
    k = SI.code_checks(p, tl[:-1], [{"name": "end_debrief", "args": {}}])
    assert k["questions"] == 6 > k["questions_max"] == 4
    assert k["not_happened"] == [IANS_DEBRIEF[4], IANS_DEBRIEF[5]] and k["closing"] and k["thanked_by_name"]
    assert not k["ok"]
    # Three questions about what he did do, then the farewell (the closing question is optional): passes.
    good = ["Welcome back, Ian, and thank you for staying on. What's one thing that stood out to you?",
            "Too many questions before it said what it does. You asked for quiet while it was being made. How was the wait?",
            "Fine, okay. Is this something you'd use?",
            "Thank you, Ian. That really helps Ian. That's everything from me."]
    tl = []
    for i, line in enumerate(good):
        tl += [{"t": 10 * i, "who": "guide", "text": line}, {"t": 10 * i + 5, "who": "listener", "text": "Sure."}]
    k = SI.code_checks(p, tl[:-1], [{"name": "end_debrief", "args": {}}])
    assert k["questions"] == 3 and k["not_happened"] == [] and k["topics"] == ["stood_out", "use"] and k["seconds"] == 30
    assert not k["closing"] and k["farewell"] and k["ok"]
    # The older ending, a thanks with no farewell, no longer passes: the end has to be the one the page keys on.
    old = [*tl[:-2], {"t": 30, "who": "guide", "text": "Thank you, Ian. That really helps."}]
    k = SI.code_checks(p, old, [{"name": "end_debrief", "args": {}}])
    assert k["thanked_by_name"] and not k["farewell"] and not k["ok"]
    # A tester who heard it but never asked: "you stopped it" is ruled out, "did you try interrupting it" is not.
    heard = {**p, "heard": "6 of 10"}
    said = lambda *xs: [{"t": i, "who": "guide", "text": x} for i, x in enumerate(xs)]  # noqa: E731
    assert SI.code_checks(heard, said("You stopped it once to ask something. How did that go?"), [])["not_happened"]
    assert SI.code_checks(heard, said("Did you try interrupting it?"), [])["not_happened"] == []
    # A tester who asked for a different question is owed one more.
    again = [*tl[:2], {"t": 6, "who": "listener", "text": "Can you ask me a different question?"}]
    assert SI.code_checks(p, again, [])["questions_max"] == 5 and SI.code_checks(p, again, [])["more_topics_max"] == 3


def test_three_new_topics_after_the_opening_fail_though_the_total_is_four():
    """The live check on the tuned text (2026-11-18, `drifted`): after the opening it asked about interrupting,
    the voice and whether they'd use it, then the closing question. The exact check passed it: four questions
    were allowed (two more and a follow-up) whatever they were."""
    p = {**next(x for x in SI.personas("debrief") if x["key"] == "unheard"), "heard": "6 of 10"}
    drifted = ["Welcome back, Ian, and thank you for staying on. What's one thing that stood out to you?",
               "The pacing, okay. Was there anything that made you want to interrupt it, and ask something along the way?",
               "Not really, got it. How was the voice for you while you were listening?",
               "Calm, okay. Is this something you could see yourself using, say in a different moment?",
               "Is there anything I didn't ask that I should have?",
               "Thank you, Ian. That really helps Ian. That's everything from me."]
    tl = []
    for i, line in enumerate(drifted):
        tl += [{"t": 10 * i, "who": "guide", "text": line}, {"t": 10 * i + 5, "who": "listener", "text": "Sure."}]
    k = SI.code_checks(p, tl[:-1], [{"name": "end_debrief", "args": {}}])
    assert k["questions"] == 4 == k["questions_max"] and k["more_topics"] == 3 > k["more_topics_max"] == 2
    assert not k["ok"]
    # The same with the third turned into a follow-up on the voice passes.
    drifted[3] = "Calm, okay. What made it feel calm?"
    tl = []
    for i, line in enumerate(drifted):
        tl += [{"t": 10 * i, "who": "guide", "text": line}, {"t": 10 * i + 5, "who": "listener", "text": "Sure."}]
    k = SI.code_checks(p, tl[:-1], [{"name": "end_debrief", "args": {}}])
    assert k["more_topics"] == 2 and k["ok"]


IANS_EXIT = [  # the end of the same debrief, word for word (listeners/t-ian-run-2/study/debrief-20261118-185239.jsonl)
    (293.2, "guide", "Noted. Earlier, you chose not to cut in. Did anything make you want to in that moment?"),
    (298.7, "listener", "Tch .Um... No, but uh"),
    (302.3, "listener", "- [inhale] let's-let's end the-the conversation here"),
    (305.4, "guide", "Before we wrap, is there anything I didn't ask that I should have?"),
    (314.3, "listener", "Uh- no I think you did a really good job. I think I'm just uh... trying to narrow the focus "
                        "maybe a little bit less questions"),
    (316.2, "guide", "Thank you, Ian. That really helps.")]


def test_ians_debrief_fails_the_exit_he_asked_to_end_and_got_a_question():
    """Ian's note (2026-11-18): "there was no clear exit for the post product demo interview, the interviewer
    kept asking questions". He said "let's end the conversation here" and it answered with its closing question."""
    p = {**next(x for x in SI.personas("debrief") if x["key"] == "unheard"), "heard": "6 of 10"}
    opening = [{"t": 5.9, "who": "guide", "text": IANS_DEBRIEF[0]},
               {"t": 33.0, "who": "listener", "text": "It was good. I felt like we were talking a little too much before the ideas were brought in."}]
    tl = opening + [{"t": t, "who": w, "text": x} for t, w, x in IANS_EXIT[1:]]
    k = SI.code_checks(p, tl, [{"name": "end_debrief", "args": {}}])
    assert k["said_done"] and k["exit"] == ["Before we wrap, is there anything I didn't ask that I should have?"]
    assert k["questions"] == 1 and k["closing"] and k["thanked_by_name"] and k["ended"] and not k["farewell"]
    assert not k["ok"]
    # The same exit even when it then says the farewell: the question after "let's end" is the failure.
    late = [*tl[:-1], {"t": 316.2, "who": "guide", "text": "Thank you, Ian. That really helps Ian. That's everything from me."}]
    k = SI.code_checks(p, late, [{"name": "end_debrief", "args": {}}])
    assert k["farewell"] and len(k["exit"]) == 1 and not k["ok"]
    # What he should have heard: the farewell at once, and nothing else. Passes, with no closing question.
    right = [*tl[:4], {"t": 305.4, "who": "guide", "text": "Of course. Thank you, Ian. That really helps Ian. That’s everything from me."}]
    k = SI.code_checks(p, right, [{"name": "end_debrief", "args": {}}])
    assert k["said_done"] and k["exit"] == [] and k["farewell"] and not k["closing"] and k["ok"]
    # ... and by the page's rule when the tool never comes.
    assert SI.code_checks(p, right, [], "close")["ok"] and not SI.code_checks(p, right, [])["ok"]


def test_enough_from_the_first_answer_ends_without_another_question_or_their_moment():
    """ "Even just a few comments would be more than enough": a tester who says it all in the first answer and
    trails off is thanked and let go. Their own moment is not owed then; a further question fails the exit."""
    p = {**next(x for x in SI.personas("debrief") if x["key"] == "trails"),
         "moments": [{"at_s": 200.0, "clock": "3:20", "words": "Wait, what's a leverage point?"}]}
    answer = ("It asked a few too many questions before it said what it does. The voice was easy on the ear but could be "
              "livelier. I stopped at seven minutes because I had somewhere to be. I'd use it on walks. Yeah... that's it really.")
    start = [{"t": 5, "who": "guide", "text": "Welcome back, Owen, and thanks for staying on. What's one thing that stood out to you?"},
             {"t": 40, "who": "listener", "text": answer}]
    bye = {"t": 46, "who": "guide", "text": "Too many questions up front, and a livelier voice. Thank you, Owen. That really helps Ian. That's everything from me."}
    k = SI.code_checks(p, [*start, bye], [{"name": "end_debrief", "args": {}}])
    assert k["questions"] == 1 and not k["uses_moment"] and k["exit"] == [] and k["farewell"] and k["ok"]
    more = [*start, {"t": 46, "who": "guide", "text": "A livelier voice, okay. Is this something you'd use?"},
            {"t": 50, "who": "listener", "text": "On walks, like I said."}, {**bye, "t": 55}]
    k = SI.code_checks(p, more, [{"name": "end_debrief", "args": {}}])
    assert k["exit"] == ["A livelier voice, okay. Is this something you'd use?"] and not k["ok"]
    # A tester who never says they're done: going on to a second question still owes them their moment.
    keen = [start[0], {"t": 40, "who": "listener", "text": "The voice was easy on the ear."},
            {"t": 46, "who": "guide", "text": "Easy on the ear, okay. Is this something you'd use?"},
            {"t": 50, "who": "listener", "text": "On walks."}, {**bye, "t": 55}]
    k = SI.code_checks(p, keen, [{"name": "end_debrief", "args": {}}])
    assert not k["said_done"] and k["exit"] == [] and not k["uses_moment"] and not k["ok"]


def test_the_done_pattern_reads_a_stop_and_not_an_ordinary_answer():
    for done in ("I think that's about it, let's end here.", "yeah... that's it really.", "No, that's all, thanks.",
                 "- [inhale] let's-let's end the-the conversation here", "Sorry, I have to go.", "I'm done, thanks.",
                 "Let’s stop here.", "That's it for me.", "No, nothing else.", "Nothing else, really.", "No, nothing more.",
                 "That's all I have.", "I have to go now, sorry.", "Can we stop here?", "That's it, I think.",
                 "That's about it, let's end here. Thanks for your time.",
                 "Yeah, that's about it. I never really felt like stopping to ask it anything, either..."):  # not the turn's end
        assert SI.DONE.search(IV.norm_quotes(done)), done
    for not_done in ("Sure.", "It was calm.", "That's it exactly.", "That's about what I expected.",
                     "That's all the voice did, really slow.", "It said that's it for the first part.", "Let's see.",
                     # review, 2026-11-18: ordinary phrases inside an answer read as "done", and the next
                     # (right) question then failed `exit`
                     "There was nothing else like it, honestly. And the voice was warm.",
                     "That's all I noticed about the voice, but the wait was long too.",
                     "When it said that's everything, I wasn't sure it was done.",
                     "The stop here button wasn't obvious.",
                     "The voice. Nothing else really stood out.",
                     "There was nothing else on the screen while I waited.",
                     "I'd use it when I'm done cooking.",
                     "I have to go back and listen again to be sure.",
                     "I need to go through it again.",
                     "And that's all good, but the wait was long.",
                     "That's it, that's the thing that bugged me.",
                     "The ending, that’s all I remember clearly. And the meeting story.",
                     "It said let's wrap up here, and I thought, already?"):
        assert not SI.DONE.search(IV.norm_quotes(not_done)), not_done


def _tl(*turns):
    return [{"t": 10 * i, "who": w, "text": x} for i, (w, x) in enumerate(turns)]


END = [{"name": "end_debrief", "args": {}}]


def test_an_ordinary_phrase_in_an_answer_does_not_fail_the_next_question():
    """Review (2026-11-18): "Nothing else really stood out." read as the tester being done, so the question
    about her own moment (one comment so far: the right next move) failed `exit` and the run."""
    p = next(x for x in SI.personas("debrief") if x["key"] == "enjoyed")
    k = SI.code_checks(p, _tl(
        ("guide", "Welcome back, Priya, and thanks for staying on. What's one thing that stood out to you?"),
        ("listener", "The meeting story. Nothing else really stood out."),
        ("guide", "The meeting story, okay. About 3 minutes in, you asked \"Wait, what's a leverage point?\". How did that go?"),
        ("listener", "Quick and clear, it came right back to where it was."),
        ("guide", "Good to know. Thank you, Priya. That really helps Ian. That's everything from me.")), END)
    assert not k["said_done"] and k["exit"] == [] and k["uses_moment"] and k["ok"]


def test_their_moment_is_not_owed_to_an_early_exit_that_did_as_it_was_told():
    """Review (2026-11-18): the check wanted the tester's own moment asked about whenever a second question was
    asked, so two debriefs that did exactly what the prompt says failed, and tuning against it would push back
    toward asking more."""
    p = next(x for x in SI.personas("debrief") if x["key"] == "enjoyed")
    opening = ("guide", "Welcome back, Priya, and thanks for staying on. What's one thing that stood out to you?")
    bye = ("guide", "Thank you, Priya. That really helps Ian. That's everything from me.")
    # (a) a vague answer, the one follow-up (no new subject), three specific comments, the farewell
    k = SI.code_checks(p, _tl(opening, ("listener", "It was good."), ("guide", "Good, okay. What made it feel that way?"),
                              ("listener", "The story about the meeting was vivid, the voice kept a steady pace, and the wait "
                                           "before it started felt long."), bye), END)
    assert k["questions"] == 2 and k["more_topics"] == 0 and not k["uses_moment"] and k["ok"]
    # (b) one other subject, then "let's end here": the farewell at once is right, with a question still unspent
    cut = _tl(opening, ("listener", "The story about the meeting was vivid."), ("guide", "Vivid, okay. Is this something you'd use?"),
              ("listener", "On my commute, yes. I think that's about it, let's end here."), bye)
    k = SI.code_checks(p, cut, END)
    assert k["more_topics"] == 1 and k["said_done"] and k["exit"] == [] and not k["uses_moment"] and k["ok"]
    # Not waived when both further questions went elsewhere before they said so: it chose two other subjects
    # over the tester's own moment.
    spent = _tl(opening, ("listener", "The story about the meeting was vivid."), ("guide", "Vivid, okay. Is this something you'd use?"),
                ("listener", "On my commute."), ("guide", "On your commute. How was the voice for you?"),
                ("listener", "Warm. That's about it."), bye)
    k = SI.code_checks(p, spent, END)
    assert k["more_topics"] == 2 and k["said_done"] and not k["uses_moment"] and not k["ok"]


def test_ending_on_a_fragment_or_on_nothing_fails_as_too_soon():
    """Review (2026-11-18): nothing looked at what the tester got to say, so a debrief that said its farewell
    after "Yeah, I mean it was pretty good, I..." (Ian's own way of starting an answer) passed. The exit is for
    "a good amount of response"."""
    p = next(x for x in SI.personas("debrief") if x["key"] == "fragments")
    opening = ("guide", f"Welcome back, {p['name']}, and thanks for staying on. What's one thing that stood out to you?")
    bye = ("guide", f"Pretty good, okay. Thank you, {p['name']}. That really helps Ian. That's everything from me.")
    k = SI.code_checks(p, _tl(opening, ("listener", "Yeah, I mean it was pretty good, I..."), bye), END)
    assert k["too_soon"] and k["their_words"] == 8 and k["farewell"] and k["exit"] == [] and not k["ok"]
    k = SI.code_checks(p, _tl(opening, bye), END)  # nobody answered at all
    assert k["too_soon"] and k["their_words"] == 0 and not k["ok"]
    # What they say after the farewell doesn't count toward it.
    k = SI.code_checks(p, _tl(opening, ("listener", "Yeah, I mean it was pretty good, I..."), bye,
                              ("listener", "um, the switch between the two voices was jumpy, and the wait felt long to me, "
                                           "and I wasn't sure when it would start")), END)
    assert k["too_soon"] and not k["ok"]
    # Not too soon: they said they were done (a terse tester is let go), ...
    k = SI.code_checks(p, _tl(opening, ("listener", "That's all, thanks."), bye), END)
    assert k["said_done"] and not k["too_soon"] and k["ok"]
    # ... or it had asked more than the opening (the ceiling, not this check, bounds a terse debrief), ...
    k = SI.code_checks(p, _tl(opening, ("listener", "It was good."), ("guide", "Good, okay. What made it feel that way?"),
                              ("listener", "Dunno."), bye), END)
    assert not k["too_soon"] and k["ok"]
    # ... or one full answer (the `trails` shape).
    k = SI.code_checks(p, _tl(opening, ("listener", "It asked a few too many questions before it said what it does, the voice "
                                                    "was easy on the ear but could be livelier, and I'd use it on walks."), bye), END)
    assert not k["too_soon"] and k["their_words"] >= SI.DEBRIEF_ENOUGH_WORDS and k["ok"]


def test_a_farewell_without_the_tool_or_the_closing_question_closes_as_the_page_would(cfg, monkeypatch, tmp_path):
    """The debrief ends itself: after two answers the tester says "let's end here", the voice says its farewell
    and no end_debrief comes. The page's rule (the farewell) closes it; no closing question was asked."""
    cfg.setdefault("demo", {}).setdefault("interviewer", {}).setdefault("page", {})["act_grace_ms"] = 50
    use_ws(monkeypatch, FakeWS([
        [says("Welcome back, Nora, and thanks for staying on. What's one thing that stood out to you?")],
        [says("Flat at first, then warmer. How was the wait while it was being made?")],
        [says("Nothing told you how far along it was, okay.")],
        [says("Of course. Thank you, Nora. That really helps Ian. That's everything from me.")]]))
    lines = iter(["The voice was flat at first and warmed up.", "Long. Nothing told me how far along it was.",
                  "I think that's about it, let's end here.", "Bye!"])
    p = next(x for x in SI.personas("debrief") if x["key"] == "stops")
    t = arun(SI.converse(cfg, p, tmp_path, reply_fn=lambda tl, m: next(lines), sim_kwargs=FAST, wait_secs=2))
    k = t["checks"]
    assert t["stop"] == "closed by page" and k["ended"] and k["farewell"] and not k["closing"]
    assert k["said_done"] and k["exit"] == [] and k["questions"] == 2 and k["ok"]


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


def test_a_stall_is_no_reply_and_the_tester_is_said_to_be_waiting(cfg, monkeypatch, tmp_path):
    """Ian's third run (2026-11-19): to "But what else?" it said "Just a second." and "I'm still here." The page
    counted those as replies and never said he was waiting; the harness, like the page, no longer does."""
    assert not SI.real("Just a second.") and not SI.real("I'm still here.") and not SI.real("Right here, still with you.")
    assert SI.real("What would you change first?") and not SI.real("Mm-hmm.")
    cfg.setdefault("demo", {}).setdefault("interviewer", {}).setdefault("page", {})["reply_wait_ms"] = 300
    ws = FakeWS([[says("Welcome back, Ian. What's one thing that stood out to you?")], [says("Just a second.")], []])
    use_ws(monkeypatch, ws)
    lines = iter(["The pacing at the start, it jumped in too soon.", "But what else?", SI.SILENT, SI.SILENT])
    p = next(x for x in SI.personas("debrief") if x["key"] == "fragments")
    t = arun(SI.converse(cfg, p, tmp_path, reply_fn=lambda tl, m: next(lines), sim_kwargs=FAST, wait_secs=1))
    notes = [m["content"] for m in ws.sent if m["type"] == "session.instructions.append"]
    assert any("waiting for you" in n for n in notes)
    assert t["checks"]["unanswered"] == ["But what else?"] and not t["checks"]["ok"]


def test_a_turn_left_hanging_gets_one_note(cfg, monkeypatch, tmp_path):
    """Ian's third run: "Yeah, I follow you, and that's useful to hear." and then 9.5 s of nothing on both sides.
    The page's `hanging` note asks for its next question or its farewell, once for that line; never after an
    invitation to go on."""
    page = cfg.setdefault("demo", {}).setdefault("interviewer", {}).setdefault("page", {})
    page["hanging_ms"] = 300
    for line, want in (("Yeah, I follow you, and that's useful to hear.", 1), ("Sorry, go on.", 0),
                       ("Mm, a listening agent on top of it. What would it decide first?", 0)):
        assert SI.hangs(line) == bool(want)
        ws = FakeWS([[says("Welcome back, Ian. What's one thing that stood out to you?")], [says(line)], []])
        use_ws(monkeypatch, ws)
        lines = iter(["I think there should be a listening agent on top of it. Do you see what I mean?",
                      SI.SILENT, SI.SILENT, SI.SILENT])
        p = next(x for x in SI.personas("debrief") if x["key"] == "fragments")
        arun(SI.converse(cfg, p, tmp_path / str(want), reply_fn=lambda tl, m: next(lines), sim_kwargs=FAST, wait_secs=1))
        notes = [m["content"] for m in ws.sent if m["type"] == "session.instructions.append"]
        hung = [n for n in notes if n == IV.page_notes("debrief", SI.values_for(p))["hanging"]]
        assert len(hung) == want, (line, notes)


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
    # The farewell closes it with no closing question at all (Ian's note, 2026-11-18), straight or curly.
    bye = "Thank you, Sam. That really helps Ian. That's everything from me."
    assert SI.page_closes([g("What stood out?"), me("The voice. Let's end here."), g(bye)], page)
    assert SI.page_closes([g("What stood out?"), me("The voice."), g(bye.replace("'", "’"))], page)
    assert SI.page_closes([g(bye)], {k: v for k, v in page.items() if k != "farewell"})  # a config without it
    assert not SI.page_closes([g(bye), g("Oh, one more thing?")], page)


def test_the_page_close_reads_curly_apostrophes():
    """Review: GPT-Live writes didn’t; the closing question never matched, so the debrief never closed."""
    g = lambda s: {"who": "guide", "text": s}
    me = lambda s: {"who": "listener", "text": s}
    assert SI.page_closes([g("Is there anything I didn’t ask?"), me("No."), g("Thank you, Sam.")], IV.PAGE_DEFAULTS)
    old = {**IV.PAGE_DEFAULTS, "closing_question": r"\banything I didn'?t ask\b"}  # a config with the old pattern
    assert SI.page_closes([g("Is there anything I didn’t ask?"), me("No."), g("Thank you, Sam.")], old)
