"""The interviewer (demo/interviewer.py): its GPT-Live sessions, routes and what it keeps in study/, offline.

GPT-Live's WebRTC call is patched out (session.create_webrtc), part B's demo.testers is a small stand-in, and
Claude is tests/fakes.py's FakeAnthropic. The routes are mounted on a bare FastAPI app; the listener is
resolved per request as part A's middleware will (WRITE_LISTEN_LISTENER here, via common.listener_dir).
"""

import json
import re
import sys
import types
import wave
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fakes import FakeAnthropic, Responder
from stages import common, llm
from voice.gpt_live import session as live_session

import demo
from demo import interviewer as IV

PRODUCT = re.compile(r"\b(chapters?|library|guides?|lessons?|podcasts?|research\w*|stor(y|ies)|episodes?|narrat\w*|"
                     r"audio|overnight|curriculum|topics?)\b", re.I)


@pytest.fixture
def testers(monkeypatch):
    """Part B's demo.testers, as the contract gives it: set_name(lid, name), update(lid, **fields)."""
    fake = types.SimpleNamespace(names=[], updates=[])
    fake.set_name = lambda lid, name: fake.names.append((lid, name))
    fake.update = lambda lid, **f: fake.updates.append((lid, f))
    monkeypatch.setitem(sys.modules, "demo.testers", fake)
    monkeypatch.setattr(demo, "testers", fake, raising=False)
    return fake


@pytest.fixture
def world(cfg, monkeypatch, own_listeners, testers):
    """A bare app with the interviewer mounted, tester t-sam active, no network, no waiting."""
    monkeypatch.setenv(common.LISTENER_ENV, "t-sam")
    (own_listeners / "t-sam").mkdir(parents=True)
    offers = []

    def webrtc(cfg, session, sdp, client=None):
        offers.append(session)
        return {"id": f"live_{len(offers)}", "sdp": "ANSWER"}
    monkeypatch.setattr(live_session, "create_webrtc", webrtc)
    after = []
    monkeypatch.setattr(IV, "_after_end", lambda cfg, study, s: after.append((study, s["id"], s["mode"])))
    app = FastAPI()
    IV.mount(app, cfg)
    return types.SimpleNamespace(client=TestClient(app), offers=offers, after=after, dir=own_listeners / "t-sam",
                                 testers=testers, root=own_listeners)


def lines(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def without_tool_names(text: str) -> str:
    return re.sub(r"\b(set_listener_name|handoff_to_guide|end_debrief)\b", "", text)


# --- the sessions ---------------------------------------------------------------------------------

def test_intro_session_is_its_own_voice_with_a_delegation_policy_for_its_two_tools(cfg, tmp_path):
    sc = IV.session_config(cfg, "intro", IV.intro_values(tmp_path))
    assert sc["audio"]["output"]["voice"] == "cedar" != cfg["live"]["voice"]
    assert sc["store"] is True and sc["model"] == cfg["live"]["model"] and sc["input"] == []
    back = sc["delegation"]["responses"]
    assert [t["name"] for t in back["tools"]] == ["set_listener_name", "handoff_to_guide"]
    assert back["model"] == cfg["live"]["backend_model"] and "empty message" in back["instructions"]
    text = sc["instructions"]
    policy = text.split("Delegation policy:")[1]
    assert "Generate <|SpawnThinking|> when:" in policy and "set_listener_name" in policy and "handoff_to_guide" in policy
    assert "I'll hand you over now." in text and "You'll see in a moment." in text
    assert "{{" not in text and "---" not in text.splitlines()[0]
    # Nothing in what the intro is told describes the product (check 3's spirit for the voice).
    assert not PRODUCT.search(without_tool_names(text)), PRODUCT.search(without_tool_names(text))


def test_intro_knows_a_name_given_before(cfg, tmp_path):
    (tmp_path / "tester.json").write_text(json.dumps({"name": "Purnima"}))
    assert "Purnima" in IV.session_config(cfg, "intro", IV.intro_values(tmp_path))["instructions"]


def test_the_intro_checks_who_it_is_says_ian_built_it_and_how_this_will_go(cfg, tmp_path):
    """Ian's notes (2026-11-16): his intro was three lines in 15 seconds. It is given the name, opens "Hi, is
    this Rolando?", says Ian built it and how the session goes, takes questions, and only then hands over."""
    (tmp_path / "tester.json").write_text(json.dumps({"name": "Rolando"}))
    v = IV.intro_values(tmp_path)
    text = IV.instructions("intro", v)
    order = ['"Hi, is this Rolando?"', "Ian built this and asked me to get your feedback",
             "You'll talk with someone else, then listen", "about ten minutes",
             "Press End demo at the top when you're done", "back to me for a couple of minutes",
             '"Ready?"', '"I\'ll hand you over now."']
    at = [text.index(x) for x in order]
    assert at == sorted(at)
    assert "You'll see in a moment." in text
    notes = IV.page_notes("intro", v)
    assert notes["opened"] == 'Begin now: say hello warmly and check who it is: "Hi, is this Rolando?" Nothing else yet.'
    assert "waiting for you" in notes["waiting"] and "handover_now" in notes
    # The page hands over on the words when no tool call comes: only on the handover itself, not on the
    # explanation that someone else comes next.
    hand = re.compile(IV.page_settings(cfg, "intro")["handover"], re.I)
    assert hand.search("I'll hand you over now.") and hand.search("Okay, handing you over now.")
    assert not hand.search("In a moment I'll hand you over to someone else, and you'll talk a little.")

    nameless = IV.intro_values(tmp_path / "nobody")
    text = IV.instructions("intro", nameless)
    assert "ask their name" in text and "Rolando" not in text and "{" not in text.replace("{{", "")
    assert IV.page_notes("intro", nameless)["opened"] == "Begin now: say hello warmly and ask their name. Nothing else yet."


def test_the_intro_is_one_question_and_one_turn(cfg, tmp_path):
    """Ian's notes (2026-11-18): "spoke a little too much... maybe just one question, then it saying what this
    will be like and what to expect, then directly any questions or are you ready to try it out". His intro
    took 49 seconds over six turns: the old text asked for two or three turns with a pause after each."""
    text = IV.instructions("intro", IV.intro_values_for("Rolando"))
    for gone in ("two or three short turns", "a small pause after each", "a minute to a minute and a half",
                 "I'm done", "no right answers", "One step a turn"):
        assert gone not in text, gone
    for want in ("twenty to thirty seconds in all", "say this in ONE short turn, about forty words",
                 "without stopping for an answer", "that isn't an interruption", "silence is a yes",
                 "unless the steps below ask for one longer turn"):
        assert want in text, want
    # The explanation is step 2 alone, and the only questions the prompt gives it to ask are the who-check
    # and the one that ends that turn (the name's confirmation is for a correction).
    step2 = text[text.index("\n2. "):text.index("\n3. ")]
    for part in ("Ian built", "someone else", "ten minutes", "End demo", "back to me", "Ready?"):
        assert part in step2, part
    assert [q for q in re.findall(r'"([^"]*)"', text) if q.endswith("?")] == [
        "Hi, is this Rolando?", "Roland. Did I get that right?", "Ready?"]
    # Ian's third run (2026-11-19): "it could be more brief". About 40 words in three beats (it was about 85),
    # without "interrupt with questions any time" (the next voice tells them) or "Any questions, or".
    said = " ".join(q for line in step2.splitlines() if line.lstrip().startswith('- "') for q in re.findall(r'"([^"]*)"', line))
    assert 38 <= len(said.split()) <= 48, len(said.split())
    for gone in ("interrupt with questions", "Any questions, or", "Afterwards you'll come back"):
        assert gone not in text, gone
    # What it says is given in the second person: told about the tester as "they", it said "They'll talk a
    # little, and then listen" (live check, 2026-11-18, and Ian's own intro).
    assert "They'll talk a little" not in text and "You'll talk with someone else" in step2
    # The clocks fit an intro of 20-30 seconds: the words count from 10 s (a tester never heard: from 20 s,
    # page/interview.js mayHandOver's twice, with words said earlier held until then), and the page steps in well
    # after a slow one.
    S = IV.page_settings(cfg, "intro")
    assert S["handover_min_ms"] == 10000 and 2 * S["handover_min_ms"] <= 20000
    assert 2 <= S["intro_max_minutes"] <= 3
    assert cfg["demo"]["interviewer"]["checks"]["intro_minutes"] <= S["intro_max_minutes"]


def test_only_the_final_line_is_the_handover(cfg):
    """The page acts on the handover's words the moment it hears them (page/interview.js), and the intro now
    explains what comes next and asks for questions: only "hand you over now" counts, config.yaml doesn't
    put an older, looser pattern back, and the prompt keeps the words for its final line."""
    assert "handover" not in (cfg["demo"]["interviewer"].get("page") or {})
    S = IV.page_settings(cfg, "intro")
    assert S["handover"] == IV.PAGE_DEFAULTS["handover"] and 0 < S["handover_min_ms"] <= 30000
    hand = re.compile(S["handover"], re.I)
    for line in ("Any questions before I hand you over?", "Before I hand you over, any questions?",
                 "In a moment I'll hand you over.", "I'll hand you over whenever you're ready.",
                 "After I hand you over, you can interrupt whenever you like."):
        assert not hand.search(line), line
    assert hand.search("I'll hand you over now.")
    # The intro's instructions, apart from the lines that give the final line itself, never match: the model
    # repeats its prompt's wording. They tell it to say "pass you to someone else" until then.
    final = '"I\'ll hand you over now."'
    for name in ("Rolando", None):
        text = IV.instructions("intro", IV.intro_values_for(name))
        rest = [x for x in text.splitlines() if final not in x]
        assert len(rest) < len(text.splitlines())
        assert not [x for x in rest if hand.search(x)]
        rule = next(x for x in rest if 'Never use the words "hand you over" before that final line' in x)
        assert "talk with someone else" in rule
        assert [x for x in rest if "hand you over" in x.lower()] == [rule]  # and nowhere else, in any form
    notes = IV.page_notes("intro", IV.intro_values_for("Rolando"))
    assert [k for k, v in notes.items() if hand.search(v)] == ["handover_now"]


def test_debrief_session_is_a_conversation_around_ians_topics_with_their_real_moment(cfg, tmp_path, monkeypatch):
    """Ian's notes (2026-11-16): the five fixed questions are gone. An easy first question, a reply to every
    answer, direct questions answered, his topics in any order, the same closing question."""
    from demo import debrief
    (tmp_path / "tester.json").write_text(json.dumps({"name": "Sam"}))
    monkeypatch.setattr(debrief, "chapter_context", lambda d, c=None: {
        "run": "r", "title": "The List", "summary": "Why nine levers matter.", "minutes_heard": 6.2, "minutes": 10})
    monkeypatch.setattr(debrief, "moments", lambda d, c=None: [
        {"at_s": 200.0, "clock": "3:20", "words": "What's a parameter?"},
        {"at_s": 400.0, "clock": "6:40", "words": "Was she right?"}])
    v = IV.debrief_values(cfg, tmp_path)
    sc = IV.session_config(cfg, "debrief", v)
    text = sc["instructions"]
    assert "delegation" not in sc  # Ian's third run (2026-11-19): it stalled waiting on a backend with nothing to bring
    for gone in ("Take me through what that was like", "to a friend", "tomorrow morning", "in this order"):
        assert gone not in text, gone
    for want in ("What's one thing that stood out to you?", "respond before you ask", "Always answer a direct question",
                 "something they would use, and when", "Where they imagine this going", "The voice they talked with:",
                 'About 3 minutes in, they stopped it to ask "What\'s a parameter?". Not owed if you already have enough; '
                 'if you do go on to another subject, make it this one: how that went.',
                 "Anything confusing, or that got in the way", "gather their feedback for Ian",
                 "Is there anything I didn't ask that I should have?", "Thank you, Sam."):
        assert want in text, want
    assert "The List" in text and "Why nine levers matter." in text and "about 6 of its 10 minutes" in text
    assert "Was she right?" in text
    assert "SpawnThinking" not in text and "Delegation policy" not in text and "backend" not in text
    assert re.search(IV.PAGE_DEFAULTS["closing_question"], text)  # the page's close keys on the closing question


def test_the_interviewer_is_told_to_reply_never_to_sit_in_silence(cfg, tmp_path):
    """Ian's debrief: "Mm-hmm", "Mm.", "Hmm.", no answer to a direct question, then 77 seconds of nothing. The
    old text said to leave pauses, not to fill a silence, and allowed an "mm"."""
    for mode, v in (("intro", IV.intro_values(tmp_path)), ("debrief", IV.debrief_values(cfg, tmp_path))):
        text = IV.instructions(mode, v)
        assert "don't fill the silence" not in text and "Leave pauses" not in text
        assert "It never stands in for a reply" in text and "When they ask you something, answer it." in text
        assert "A silence longer than a few seconds after they've spoken is always yours to end." in text
        note = IV.page_notes(mode, v)["waiting"]
        assert note.startswith("[") and "Speak now" in note and "waiting for you" in note
    # The debrief's own note (review, 2026-11-18): "then carry on" after "let's end here", or after a farewell
    # they spoke over, pushed it on to another question. The intro's is unchanged.
    assert IV.page_notes("intro", IV.intro_values(tmp_path))["waiting"].endswith("in a sentence, then carry on.]")
    note = IV.page_notes("debrief", IV.debrief_values(cfg, tmp_path))["waiting"]
    assert "carry on" not in note and "Then your farewell, word for word, if they asked to stop" in note
    assert "or you had begun it; only otherwise your next question.]" in note
    assert IV.page_settings(cfg, "debrief")["reply_wait_ms"] == 6000


def test_debrief_without_moments_asks_whether_they_tried_only_if_they_heard_the_piece(cfg, tmp_path, monkeypatch):
    """Ian's notes (2026-11-18): "Did you give that a try?" is for a tester who heard the piece and never
    asked. One who heard none of it is not asked about it at all (his debrief said "you chose not to cut in")."""
    from demo import debrief
    monkeypatch.setattr(debrief, "moments", lambda d, c=None: [])
    monkeypatch.setattr(debrief, "chapter_context", lambda d, c=None: {
        "run": "r", "title": "The List", "summary": "Why.", "minutes_heard": 4.0, "minutes": 10})
    real = debrief.activity
    monkeypatch.setattr(debrief, "activity", lambda d, c=None, now=None: {"started": True, "listened": True, "ready": True})
    text = IV.session_config(cfg, "debrief", IV.debrief_values(cfg, tmp_path))["instructions"]
    assert "never stopped it to ask anything. You may ask whether they tried interrupting it" in text
    assert "- They never stopped it to ask anything." in text and "about 4 of its 10 minutes" in text
    # No chapter and no record at all: nothing is claimed, and nothing about the piece is asked.
    monkeypatch.setattr(debrief, "chapter_context", lambda d, c=None: {
        "run": None, "title": None, "summary": None, "minutes_heard": 0, "minutes": None})
    monkeypatch.setattr(debrief, "activity", real)  # an empty folder: no conversation, no chapter, no presses
    text = IV.session_config(cfg, "debrief", IV.debrief_values(cfg, tmp_path))["instructions"]
    assert "- The app has no record of what they did: ask, don't assume." in text
    assert "No piece was made for them, so there is nothing to ask here" in text
    assert "They heard none of the piece" not in text and "or the wait" not in text
    assert "less than a minute" not in text and "{" not in text.replace("{{", "")


def test_the_debrief_is_three_or_four_questions_about_what_they_did(cfg, tmp_path, world):
    """Ian's notes (2026-11-18): "much less questions, and... pass a bit of context about the things that it did
    or buttons it pressed". His debrief asked seven questions in 5 min 20 s, one of them "Would you want to be
    able to ask it things as you go?" (it exists) and one "Earlier, you chose not to cut in" (he heard none)."""
    from demo import debrief
    debrief.add_event(world.dir, "end_demo", "talk")
    v = IV.debrief_values(cfg, world.dir)
    text = IV.instructions("debrief", v)
    for gone in ("five to eight minutes", "Don't close early", "bring it up before your closing question",
                 "Let these come up naturally", "Would you want"):
        assert gone not in text, gone
    assert "three or four questions in all" not in text
    assert "four questions at most" not in text and "the next question you ask is the closing one" not in text
    order = ["three minutes at the very most", "WHAT THEY DID",
             "- They pressed End demo while talking with the voice.",
             '"What\'s one thing that stood out to you?"', "one more question, chosen from what they did",
             "Two more questions after your opening is the ceiling, never a target; keep count",
             "Follow up only when an answer is vague or general", "once in the whole conversation",
             "A clear answer needs no follow-up",
             "Choose at most two", "A question about something they did names it",
             "never say they chose not to do something", "never ask whether they would want a thing that is already there"]
    at = [text.index(x) for x in order]
    assert at == sorted(at), [x for x, a, b in zip(order, at, sorted(at)) if a != b]
    # The clocks: no more questions from four minutes, the farewell now at six.
    I = IV.icfg(cfg)
    assert (I["debrief_target_minutes"], I["debrief_max_minutes"]) == (4, 6) == (
        cfg["demo"]["interviewer"]["debrief_target_minutes"], cfg["demo"]["interviewer"]["debrief_max_minutes"])
    notes = IV.page_notes("debrief", v)
    assert notes["time_up"].startswith("[You're at time. No more questions:") and "say your farewell" in notes["time_up"]
    assert cfg["demo"]["interviewer"]["checks"]["debrief_minutes"] <= I["debrief_max_minutes"]
    # What it was told is kept with the transcript (the summary and the study report read it from there).
    sid = world.client.post("/api/interview/start", json={"mode": "debrief", "sdp": "OFFER"}).json()["sid"]
    first = lines(world.dir / "study" / f"debrief-{sid}.jsonl")[0]
    assert first["did"] == "- They pressed End demo while talking with the voice." == v["did"]
    assert first["activity"]["end"]["phase"] == "talk" and first["activity"]["listened"] is False
    assert IV.recorded_account(world.dir / "study") == first["did"]
    assert "They pressed End demo while talking with the voice." in world.offers[-1]["instructions"]


def test_the_debrief_ends_itself_with_one_farewell(cfg, tmp_path):
    """Ian's note (2026-11-18): "there was no clear exit for the post product demo interview, the interviewer
    kept asking questions, it should have an exit if the interview seems to be coming to a close. Even just
    evaluating if it received a good amount of response, even just a few comments would be more than enough."
    He said "let's end the conversation here" and was asked the closing question."""
    (tmp_path / "tester.json").write_text(json.dumps({"name": "Sam"}))
    v = IV.debrief_values(cfg, tmp_path)
    bye = "Thank you, Sam. That really helps Ian. That's everything from me."
    assert v["farewell"] == bye == IV.farewell("Sam")
    text = IV.instructions("debrief", v)
    for gone in ("Then your closing question, exactly", "go straight to your closing question",
                 "When they've answered it, thank them by name", "go to your closing question, then thank them",
                 "four questions at most"):
        assert gone not in text, gone
    order = ["A few real comments are all Ian needs.",
             "After every answer, weigh whether you have enough",
             "is the ceiling, never a target",
             "You have enough as soon as one of these is true",
             "two or three specific comments, in one answer or across several. That is more than enough",
             "don't go on to another subject to use up your questions",
             "They sound finished", '"that\'s about it"', "they trail off",
             "This counts only once they've given you at least one specific comment.",
             'A short or vague first answer ("it was good") is not finished: follow up once (below)',
             'A pause in the middle of a sentence ("it was good, I, um...") is not trailing off: wait for the rest.',
             "natural close",
             "Don't ask whether they have more.",
             'If they ask to stop or say they\'re done ("let\'s end here", "that\'s all", "I have to go"): your farewell, at once.',
             "No question of any kind first.",
             "One optional question, only when they plainly have more they want to say",
             '"Is there anything I didn\'t ask that I should have?"',
             "Never after they've said they're done, and never as a step you owe.",
             "YOUR FAREWELL", f'Exactly these words, once: "{bye}"',
             "If they start speaking before you've finished it, stop and listen, respond in a few words, then say it in full.",
             "Then stop: the app ends the conversation. Say nothing after it, whatever they say.",
             "If the app tells you you're at time, no more questions: your farewell."]
    at = [text.index(x) for x in order]
    assert at == sorted(at), [x for x, a, b in zip(order, at, sorted(at)) if a != b]
    assert text.count(bye) == 1  # one wording, in one place
    # The page and the server key on its last sentence, straight or curly, whatever the case.
    for said in (bye, bye.replace("'", "’"), bye.upper()):
        assert re.search(IV.PAGE_DEFAULTS["farewell"], said, re.I)
        assert re.search(IV.FAREWELL, IV.norm_quotes(said), re.I)
    assert IV.page_settings(cfg, "debrief")["farewell"] == IV.PAGE_DEFAULTS["farewell"]
    # No name known: the same words without one, never "Thank you, them."
    anon = IV.values_from("", {"run": None}, [], None)
    assert anon["farewell"] == "Thank you. That really helps Ian. That's everything from me." == IV.farewell(None)
    assert "Thank you, them" not in IV.instructions("debrief", anon)
    assert re.search(IV.PAGE_DEFAULTS["farewell"], anon["farewell"], re.I)
    # The clock's notes ask for the farewell, never for the closing question; a run saved before it still fills.
    notes = IV.page_notes("debrief", v)
    assert notes["wrap_now"] == "[Time is up. Say your farewell now, word for word, and end.]"
    assert "closing question" not in notes["time_up"] + notes["wrap_now"]
    old = {k: x for k, x in v.items() if k != "farewell"}
    assert bye in IV.instructions("debrief", old)
    # The tool (posted by the page) is told the same end; the backend is the intro's alone (2026-11-19).
    from stages.common import load_prompt
    assert "end_debrief" not in load_prompt("interviewer_backend")
    assert "That's everything from me." in next(t for t in IV.tools("debrief") if t["name"] == "end_debrief")["description"]
    # No line of {{asking}} owes a question (review, 2026-11-18: "One of your questions is how that went" stood
    # in a list of choices, against "the ceiling, never a target").
    n = IV.notes()
    for k in ("asking_moment", "asking_tapped", "asking_unheard", "asking_no_piece", "asking_none"):
        assert "One of your questions is" not in n[k] and not re.search(r"(^|[.:] )Ask (about|what)\b", n[k]), k
    assert "Not owed if you already have enough" in n["asking_moment"]
    assert all("If you ask here, ask" in n[k] for k in ("asking_tapped", "asking_unheard", "asking_no_piece"))


def test_you_asked_for_quiet_is_not_the_topic_of_asking_questions():
    said = lambda *xs: [{"who": "interviewer", "text": x} for x in xs]  # noqa: E731
    assert IV.covered(said("You asked for quiet while it was being made. How was the wait?"))["topics"] == []
    assert IV.covered(said("You asked how it was coming along. Was the wait okay?"))["topics"] == []
    assert IV.covered(said("You stopped it about three minutes in to ask about the levers. How did that go?"))["topics"] == ["asking"]
    assert IV.covered(said("Did you try interrupting it?"))["topics"] == ["asking"]
    # live check, 2026-11-18: this question about using it wasn't counted
    assert IV.covered(said("Is this something you could see yourself using, say in a different moment?"))["topics"] == ["use"]


def test_covered_topics_in_the_order_they_came_up_and_the_close():
    said = [("interviewer", "Welcome back. What's one thing that stood out to you?"), ("listener", "The voice."),
            ("interviewer", "The voice, okay. How was the voice for you?"), ("listener", "Calm."),
            ("interviewer", "About 3 minutes in, you asked about levers. How did that go?"), ("listener", "Fine."),
            ("interviewer", "Is this something you'd use?"), ("listener", "On my commute."),
            ("interviewer", "Where could you imagine this going?"), ("listener", "Languages."),
            ("interviewer", "Was anything confusing?"), ("listener", "The button."),
            ("interviewer", "Is there anything I didn't ask that I should have?")]
    lines = [{"who": w, "text": t} for w, t in said]
    got = IV.covered(lines)
    assert got == {"topics": ["stood_out", "voice", "asking", "use", "future", "confusing"], "closing": True,
                   "answered": False, "thanked": False, "farewell": False, "said": True}
    assert not IV.finished(got)
    lines += [{"who": "interviewer", "text": "Thank you."}]  # thanks before they answered the closing question
    assert not IV.covered(lines)["thanked"]
    lines += [{"who": "listener", "text": "No."}, {"who": "interviewer", "text": "Thank you, Sam. That really helps."}]
    assert IV.covered(lines) == {**got, "answered": True, "thanked": True}
    assert IV.finished(IV.covered(lines))  # the older shape still counts
    assert IV.covered(lines[:2]) == {"topics": ["stood_out"], "closing": False, "answered": False, "thanked": False,
                                     "farewell": False, "said": True}
    # The farewell ends it wherever it comes: no closing question needed (Ian's note, 2026-11-18).
    early = lines[:2] + [{"who": "interviewer", "text": "Thank you, Sam. That really helps Ian. That’s everything from me."}]
    assert IV.covered(early) == {"topics": ["stood_out"], "closing": False, "answered": False, "thanked": False,
                                 "farewell": True, "said": True}
    assert IV.finished(IV.covered(early))
    # ... but never with no word from the tester: the opening question and the farewell alone (they walked away,
    # the clock's note asked for the farewell) is not a finished debrief. Review, 2026-11-18.
    alone = IV.covered([lines[0], early[-1]])
    assert alone["farewell"] and not alone["said"] and not IV.finished(alone)
    assert not IV.finished(IV.covered([lines[0], {"who": "listener", "text": "  "}, early[-1]]))
    assert not IV.covered(lines[:2] + [{"who": "listener", "text": "That's everything from me."}])["farewell"]  # theirs
    assert set(IV.TOPICS) == {"stood_out", "use", "future", "voice", "asking", "confusing"}


# --- the routes -----------------------------------------------------------------------------------

def test_intro_routes_save_the_name_hand_over_and_keep_everything_in_study(world):
    c = world.client
    got = c.post("/api/interview/start", json={"mode": "intro", "sdp": "OFFER"}).json()
    assert got["live_id"] == "live_1" and got["sdp"] == "ANSWER"
    assert got["notes"]["opened"].startswith("Begin now") and "handover_now" in got["notes"]
    assert got["settings"]["acts"] == {"handover": "handoff_to_guide", "close": "end_debrief"}
    sid = got["sid"]
    assert c.post("/api/interview/line", json={"sid": sid, "who": "out", "text": "Thanks for making the time."}).json()
    c.post("/api/interview/line", json={"sid": sid, "who": "in", "text": "I'm Sam."})
    c.post("/api/interview/line", json={"sid": sid, "who": "bogus", "text": "ignored"})
    named = c.post("/api/interview/tool", json={"sid": sid, "name": "set_listener_name", "args": {"name": "  Sam  "}}).json()
    assert named == {"saved": True, "act": "name", "name": "Sam"}
    assert world.testers.names == [("t-sam", "Sam")]
    wrong = c.post("/api/interview/tool", json={"sid": sid, "name": "end_debrief", "args": {}}).json()
    assert "error" in wrong
    hand = c.post("/api/interview/tool", json={"sid": sid, "name": "handoff_to_guide", "args": {}}).json()
    assert hand["handed_over"] and hand["act"] == "handover" and hand["name"] == "Sam"
    assert hand["scripts"] == ["/align.js", "/live.js", "/library.js"] and hand["entry"] == "Live"
    lid, fields = world.testers.updates[0]
    assert lid == "t-sam" and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", fields["intro_done"])
    assert c.post("/api/interview/usage", json={"sid": sid, "live_id": "live_1", "seconds": 61.5,
                                                "backend": {"model": "gpt-6.1-terra", "input": 1000, "output": 50,
                                                            "cached": 0}}).json()["cost_usd"] > 0
    assert c.post("/api/interview/end", json={"sid": sid, "how": "handover"}).json()["ended"]
    assert c.post("/api/interview/line", json={"sid": sid, "who": "in", "text": "late"}).status_code == 409

    study = world.dir / "study"
    assert not (world.dir / "sessions").exists()  # the guide must not think it has met them
    rec = lines(study / f"intro-{sid}.jsonl")
    assert rec[0]["who"] == "session" and rec[0]["voice"] == "cedar" and rec[0]["tools"] == ["set_listener_name", "handoff_to_guide"]
    said = [(x["who"], x.get("text")) for x in rec if x["who"] in ("listener", "interviewer", "page")]
    assert said == [("interviewer", "Thanks for making the time."), ("listener", "I'm Sam.")]
    tools = [x for x in rec if x["who"] == "tool"]
    assert [t["name"] for t in tools] == ["set_listener_name", "end_debrief", "handoff_to_guide"]
    assert "scripts" not in tools[-1]["result"] and "act" not in tools[-1]["result"]
    assert rec[-1] == {**rec[-1], "who": "session", "ended": "handover"} and all("t" in x and "s" in x for x in rec)
    log = lines(study / "log.jsonl")
    assert len(log) == 1 and log[0]["kind"] == "interviewer" and log[0]["provider"] == "openai"
    assert log[0]["live_id"] == "live_1" and log[0]["seconds"] >= 61.5 and log[0]["mode"] == "intro"
    assert world.after == [(study, sid, "intro")]


def test_usage_is_logged_once_and_a_session_never_reported_is_billed_by_the_clock(world):
    c = world.client
    sid = c.post("/api/interview/start", json={"mode": "intro", "sdp": "OFFER"}).json()["sid"]
    for _ in range(2):  # a beacon and the end
        c.post("/api/interview/usage", json={"sid": sid, "live_id": "live_1", "seconds": 30})
    c.post("/api/interview/end", json={"sid": sid})
    c.post("/api/interview/usage", json={"sid": sid, "live_id": "live_1", "seconds": 31})  # after the end: still known
    sid2 = c.post("/api/interview/start", json={"mode": "intro", "sdp": "OFFER"}).json()["sid"]
    c.post("/api/interview/end", json={"sid": sid2, "how": "pagehide"})
    log = lines(world.dir / "study" / "log.jsonl")
    assert [x["live_id"] for x in log] == ["live_1", "live_2"]
    assert log[1]["backend"] is None and log[1]["seconds"] >= 0


def test_a_session_belongs_to_its_tester(world, monkeypatch):
    sid = world.client.post("/api/interview/start", json={"mode": "intro", "sdp": "OFFER"}).json()["sid"]
    monkeypatch.setenv(common.LISTENER_ENV, "t-other")
    for path, body in (("tool", {"name": "set_listener_name", "args": {"name": "Eve"}}), ("line", {"who": "in", "text": "x"}),
                       ("usage", {"live_id": "live_1"}), ("end", {})):
        assert world.client.post(f"/api/interview/{path}", json={"sid": sid, **body}).status_code == 409
    assert world.testers.names == []
    monkeypatch.setenv(common.LISTENER_ENV, "t-sam")
    assert world.client.post("/api/interview/end", json={"sid": sid}).json()["ended"]


def test_unknown_mode_and_a_refused_session(world, monkeypatch):
    assert world.client.post("/api/interview/start", json={"mode": "chat", "sdp": "x"}).status_code == 400

    def refuse(*a, **k):
        raise common.StageError("GPT-Live refused the session (400): nope")
    monkeypatch.setattr(live_session, "create_webrtc", refuse)
    r = world.client.post("/api/interview/start", json={"mode": "intro", "sdp": "x"})
    assert r.status_code == 503 and "refused" in r.json()["detail"]


def test_handover_happens_even_if_the_tester_record_cannot_be_written(world):
    def broken(lid, **f):
        raise OSError("disk full")
    world.testers.update = broken
    sid = world.client.post("/api/interview/start", json={"mode": "intro", "sdp": "x"}).json()["sid"]
    got = world.client.post("/api/interview/tool", json={"sid": sid, "name": "handoff_to_guide"}).json()
    assert got["act"] == "handover" and "disk full" in got["not_saved"]


def debrief_session(world, said):
    c = world.client
    got = c.post("/api/interview/start", json={"mode": "debrief", "sdp": "OFFER"}).json()
    assert got["notes"]["opened"].startswith("Begin now") and {"time_up", "wrap_now", "waiting"} <= set(got["notes"])
    assert got["settings"]["debrief_target_minutes"] == 4 and got["settings"]["debrief_max_minutes"] == 6
    for who, text in said:
        c.post("/api/interview/line", json={"sid": got["sid"], "who": who, "text": text})
    return got["sid"]


FULL = [("out", "Welcome back, Sam. What's one thing that stood out to you?"), ("in", "It was calm."),
        ("out", "Calm, okay. How was the voice for you?"), ("in", "Easy to listen to."),
        ("out", "Is this something you'd use?"), ("in", "On a walk, yes."),
        ("out", "Is there anything I didn't ask that I should have?"), ("in", "No."),
        ("out", "Thank you, Sam. That really helps.")]
TOPICS = ["stood_out", "voice", "use"]


def test_end_debrief_marks_it_done_and_starts_the_summary(world):
    sid = debrief_session(world, FULL)
    got = world.client.post("/api/interview/tool", json={"sid": sid, "name": "end_debrief"}).json()
    assert got == {"ended": True, "act": "close"}
    state = json.loads((world.dir / "study" / "debrief.json").read_text())
    assert state["done"] and state["finished_at"] and state["topics"] == TOPICS and state["session"] == sid
    end = world.client.post("/api/interview/end", json={"sid": sid, "how": "done"}).json()
    assert end["done"] and end["topics"] == TOPICS
    assert world.after == [(world.dir / "study", sid, "debrief")]
    assert (world.dir / "study" / f"debrief-{sid}.jsonl").exists()


def test_a_complete_debrief_without_the_tool_counts_as_done_and_a_partial_one_does_not(world):
    """Done without end_debrief: the closing question asked and answered, then thanks. No fixed set of
    questions is needed any more (Ian's notes, 2026-11-16)."""
    sid = debrief_session(world, FULL)
    assert world.client.post("/api/interview/end", json={"sid": sid, "how": "dropped"}).json()["done"]
    for partial in (FULL[:4], FULL[:7], FULL[:8], FULL[:7] + FULL[8:]):  # no close; unanswered; no thanks; thanks unanswered
        (world.dir / "study" / "debrief.json").unlink()
        sid = debrief_session(world, partial)
        assert world.client.post("/api/interview/end", json={"sid": sid, "how": "pagehide"}).json()["done"] is False
    state = json.loads((world.dir / "study" / "debrief.json").read_text())
    assert state == {**state, "done": False, "topics": TOPICS, "closing": True}


def test_a_debrief_that_ended_on_the_farewell_is_done_without_the_closing_question(world):
    """Ian's note (2026-11-18): the debrief ends itself once it has enough. Done is the farewell (the tool
    often never comes), and a missing closing question is not an unfinished debrief."""
    said = FULL[:4] + [("in", "I think that's about it, let's end here."),
                       ("out", "Thank you, Sam. That really helps Ian. That’s everything from me.")]
    sid = debrief_session(world, said)
    end = world.client.post("/api/interview/end", json={"sid": sid, "how": "dropped"}).json()
    assert end["done"] and end["topics"] == ["stood_out", "voice"]
    state = json.loads((world.dir / "study" / "debrief.json").read_text())
    assert state == {**state, "done": True, "closing": False, "farewell": True, "how": "complete", "session": sid}
    assert IV.debrief_done(world.dir)
    # By the page (it heard the farewell and no tool call came) or by the tool: the same, and closing stays false.
    (world.dir / "study" / "debrief.json").unlink()
    sid = debrief_session(world, said)
    got = world.client.post("/api/interview/tool", json={"sid": sid, "name": "end_debrief", "by": "page"}).json()
    assert got == {"ended": True, "act": "close"}
    state = json.loads((world.dir / "study" / "debrief.json").read_text())
    assert state == {**state, "done": True, "closing": False, "farewell": True, "how": "end_debrief"}
    assert world.client.post("/api/interview/end", json={"sid": sid, "how": "done"}).json()["done"]
    # Without the farewell (and no tool), a debrief that stopped after two answers is not done.
    (world.dir / "study" / "debrief.json").unlink()
    sid = debrief_session(world, said[:5])
    assert world.client.post("/api/interview/end", json={"sid": sid, "how": "pagehide"}).json()["done"] is False
    state = json.loads((world.dir / "study" / "debrief.json").read_text())
    assert state == {**state, "done": False, "closing": False, "farewell": False}


def test_a_debrief_nobody_answered_is_never_done(world):
    """Review of the debrief's exit (2026-11-18): the farewell needed no word from the tester. Someone who
    pressed End demo and walked away (or whose mic wasn't heard) got the opening question, then `time_up` asked
    for the farewell, the page called end_debrief and debrief.json said done: End demo gone for good, "Done" on
    the admin page, no summary. Now neither the farewell nor the tool marks it done until they have spoken."""
    bye = ("out", "Thank you, Sam. That really helps Ian. That’s everything from me.")
    sid = debrief_session(world, [FULL[0], bye])
    got = world.client.post("/api/interview/tool", json={"sid": sid, "name": "end_debrief", "by": "page"}).json()
    assert got == {"ended": True, "act": "close"}  # the page still closes
    assert not (world.dir / "study" / "debrief.json").exists() and not IV.debrief_done(world.dir)
    assert world.client.post("/api/interview/end", json={"sid": sid, "how": "done"}).json()["done"] is False
    state = json.loads((world.dir / "study" / "debrief.json").read_text())
    assert state == {**state, "done": False, "farewell": True, "finished_at": None, "how": "no answer"}
    assert not IV.debrief_done(world.dir)
    # The farewell alone, no tool: the same.
    sid = debrief_session(world, [FULL[0], bye])
    assert world.client.post("/api/interview/end", json={"sid": sid, "how": "dropped"}).json()["done"] is False
    assert json.loads((world.dir / "study" / "debrief.json").read_text())["how"] == "no answer"
    # One line of theirs and it is done; a line that reaches the server after the tool call counts at the close.
    sid = debrief_session(world, [FULL[0], FULL[1], bye])
    world.client.post("/api/interview/tool", json={"sid": sid, "name": "end_debrief"})
    assert json.loads((world.dir / "study" / "debrief.json").read_text())["done"]
    (world.dir / "study" / "debrief.json").unlink()
    sid = debrief_session(world, [FULL[0], bye])
    world.client.post("/api/interview/tool", json={"sid": sid, "name": "end_debrief"})
    world.client.post("/api/interview/line", json={"sid": sid, "who": "in", "text": "It was calm, and the wait was long."})
    assert world.client.post("/api/interview/end", json={"sid": sid, "how": "done"}).json()["done"]
    state = json.loads((world.dir / "study" / "debrief.json").read_text())
    assert state == {**state, "done": True, "how": "end_debrief"}


def test_ians_own_debrief_is_not_done(world):
    said = [("out", "So take me through what that was like, from the start."), ("in", "Yeah, I mean it was pretty good. I"),
            ("out", "Mm-hmm."), ("in", "give me a different sort of question that you should start with"), ("out", "Hmm."),
            ("in", "What's a different question")]
    sid = debrief_session(world, said)
    assert world.client.post("/api/interview/end", json={"sid": sid, "how": "pagehide"}).json() == {
        "ended": True, "topics": [], "done": False}


def test_a_stale_session_is_closed_on_the_next_start(world, monkeypatch, cfg):
    sid = world.client.post("/api/interview/start", json={"mode": "intro", "sdp": "x"}).json()["sid"]
    cfg.setdefault("demo", {}).setdefault("interviewer", {})["stale_minutes"] = 0
    world.client.post("/api/interview/start", json={"mode": "intro", "sdp": "x"})
    rec = lines(world.dir / "study" / f"intro-{sid}.jsonl")
    assert rec[-1]["ended"] == "stale"


def test_interview_js_is_served(world):
    r = world.client.get("/interview.js")
    assert r.status_code == 200 and "window.Interview" in r.text and r.headers["content-type"].startswith("text/javascript")
    assert r.text.index("window.Speaker = window.Speaker ||") < r.text.index("window.Interview")  # page/speaker.js first


# --- after the session: audio and summary ------------------------------------------------------------

def tiny_wav(n=2400) -> bytes:
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x01" * 2 * n)
    return buf.getvalue()


def test_fetch_audio_waits_retries_and_makes_an_mp3(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(live_session, "api_key", lambda: "sk-test")
    calls, slept = [], []

    class Http:
        def get(self, url, headers=None, timeout=None, follow_redirects=None):
            calls.append(url)
            ok = len(calls) >= 2  # not ready the first time
            return types.SimpleNamespace(status_code=200 if ok else 404, content=tiny_wav() if ok else b"")
    got = IV.fetch_audio(cfg, tmp_path, "live_9", sleep=slept.append, client=Http())
    assert got == tmp_path / "live_9.mp3" and got.stat().st_size > 0 and not (tmp_path / "live_9.wav").exists()
    assert calls == [f"{cfg['live']['url']}/live_9/content"] * 2
    assert slept == [cfg["live"]["archive_wait_seconds"], cfg["live"]["archive_retry_seconds"]]


def test_fetch_audio_gives_up_quietly(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(live_session, "api_key", lambda: "sk-test")
    cfg.setdefault("demo", {}).setdefault("interviewer", {})["audio_tries"] = 3

    class Http:
        def get(self, *a, **k):
            return types.SimpleNamespace(status_code=404, content=b"")
    assert IV.fetch_audio(cfg, tmp_path, "live_9", sleep=lambda s: None, client=Http()) is None
    assert not list(tmp_path.iterdir())


def write_debrief(study: Path, sid: str, said, *, done: bool):
    study.mkdir(parents=True, exist_ok=True)
    recs = [{"t": "2026-11-16T10:00:00", "s": 0, "who": "session", "mode": "debrief"}]
    recs += [{"t": "x", "s": 5.0 * i + 70, "who": "listener" if w == "in" else "interviewer", "text": t}
             for i, (w, t) in enumerate(said)]
    (study / f"debrief-{sid}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    if done:
        (study / "debrief.json").write_text(json.dumps({"done": True, "session": sid}))


def summary_client(monkeypatch, text="# Debrief: Sam\n\n## What stood out\nCalm."):
    seen = []

    def respond(kw):
        seen.append(kw)
        return Responder.msg(kw, text)
    monkeypatch.setattr(llm, "_client", FakeAnthropic(respond))
    return seen


def test_summary_is_one_claude_call_costed_in_study(cfg, tmp_path, monkeypatch):
    seen = summary_client(monkeypatch)
    study = tmp_path / "study"
    write_debrief(study, "s1", FULL, done=True)
    (study / "intro-s0.jsonl").write_text(json.dumps({"s": 3, "who": "listener", "text": "I'm Sam."}) + "\n")
    out = IV.summarize(cfg, study, "s1", {"person": "Sam", "title": "The List", "summary": "Nine levers.",
                                          "did": "- They heard none of the piece: it wasn't ready yet when they ended."})
    assert out.read_text().startswith("# Debrief: Sam")
    prompt = seen[0]["messages"][0]["content"]
    assert "[1:15] them: It was calm." in prompt and "[1:10] interviewer: Welcome back, Sam." in prompt
    # What they did is in the summary's material and its first heading (Ian's notes, 2026-11-18: his own
    # summary said "He did not cut in during the piece" when he had heard none of it).
    assert "- They heard none of the piece: it wasn't ready yet when they ended." in prompt
    assert prompt.index("## What they did") < prompt.index("## What stood out")
    assert "never infer it from the interviewer's questions" in prompt
    for heading in ("## What stood out", "## Would they use it, and when", "## Where they imagine it going", "## The voice",
                    "## Asking questions", "## Confusing, or got in the way", "## Anything else they raised", "## Notable quotes"):
        assert heading in prompt, heading
    assert "## 1." not in prompt and "{{" not in prompt
    assert "[0:03] them: I'm Sam." in prompt and "The List" in prompt
    assert seen[0]["model"] == cfg["models"]["researcher"]
    log = lines(study / "log.jsonl")
    assert log[0]["stage"] == "debrief_summary" and log[0]["cost_usd"] > 0 and log[0]["kind"] == "interviewer"


def test_summary_skips_a_silent_debrief_and_never_overwrites_a_finished_one(cfg, tmp_path, monkeypatch):
    seen = summary_client(monkeypatch)
    study = tmp_path / "study"
    write_debrief(study, "quiet", [("out", "What's one thing that stood out to you?")], done=False)
    assert IV.summarize(cfg, study, "quiet", {}) is None and not seen
    write_debrief(study, "full", FULL, done=True)
    assert IV.summarize(cfg, study, "full", {"person": "Sam"})
    first = (study / "debrief_summary.md").read_text()
    write_debrief(study, "later", FULL[:2], done=False)
    (study / "debrief.json").write_text(json.dumps({"done": True, "session": "full"}))
    # a later debrief (a second chapter's) writes its own summary; the first completed one stays
    assert IV.summarize(cfg, study, "later", {"person": "Sam"}) == study / "debrief_summary-later.md"
    assert (study / "debrief_summary.md").read_text() == first and len(seen) == 2


def test_after_end_summarises_a_debrief_then_fetches_the_audio(cfg, tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr(IV, "summarize", lambda cfg, study, sid, values, **k: order.append(("summary", sid)))
    monkeypatch.setattr(IV, "fetch_audio", lambda cfg, study, live_id, **k: order.append(("audio", live_id)))
    IV._after_end(cfg, tmp_path, {"id": "s1", "mode": "debrief", "live_id": "live_3", "values": {}})
    IV._after_end(cfg, tmp_path, {"id": "s2", "mode": "intro", "live_id": "live_4", "values": {}})
    assert order == [("summary", "s1"), ("audio", "live_3"), ("audio", "live_4")]


def test_the_name_comes_from_the_transcript_when_the_tool_never_came():
    """Live check 2026-11-16: in four intros the voice never delegated, so set_listener_name never ran. The
    server takes the name the interviewer last confirmed (a correction confirms again), else the tester's own."""
    from demo.interviewer import confirmed_name
    said = lambda who, text: {"who": who, "text": text}  # noqa: E731
    assert confirmed_name([said("interviewer", "You'll see in a moment. Priya. Did I get that right?")]) == "Priya"
    assert confirmed_name([said("interviewer", "Hana. Did I get that right?"), said("listener", "It's Hannah."),
                           said("interviewer", "Hannah. Did I get that right?")]) == "Hannah"
    assert confirmed_name([said("listener", "Hi, I'm Siobhan, sorry")]) == "Siobhan"
    assert confirmed_name([said("interviewer", "Sorry, did I get that right?"), said("listener", "I'm not sure")]) is None


def test_a_name_needs_a_real_capital_and_is_never_a_stop_word():
    """Review: CONFIRM_RE was case-insensitive throughout, so "Sam. Got it. Did I get that right?" saved "Got it"."""
    from demo.interviewer import confirmed_name
    said = lambda who, text: {"who": who, "text": text}  # noqa: E731
    assert confirmed_name([said("interviewer", "Sam. Got it. Did I get that right?")]) == "Sam"
    assert confirmed_name([said("interviewer", "So it's Sam. Did I get that right?")]) == "Sam"
    assert confirmed_name([said("interviewer", "Okay, Sam Lee. Is that right?")]) == "Sam Lee"
    assert confirmed_name([said("interviewer", "Got it. Did I get that right?")]) is None
    assert confirmed_name([said("interviewer", "Great, thanks. Is that right?")]) is None
    for line in ("I'm good.", "it's fine", "I'm Good, thanks", "It's Fine.", "Yeah, I'm Okay"):
        assert confirmed_name([said("listener", line)]) is None, line


def test_a_short_answer_to_the_name_question_is_the_name():
    from demo.interviewer import confirmed_name
    said = lambda who, text: {"who": who, "text": text}  # noqa: E731
    ask = said("interviewer", "Before we start, what's your name?")
    assert confirmed_name([ask, said("listener", "Sam.")]) == "Sam"
    assert confirmed_name([ask, said("listener", "It’s Sam Lee")]) == "Sam Lee"
    assert confirmed_name([ask, said("listener", "Oh, Priya")]) == "Priya"
    assert confirmed_name([ask, said("listener", "Good.")]) is None
    assert confirmed_name([ask, said("listener", "Fine, thanks.")]) is None
    # only the answer right after the question; a capitalised word later on is not a name
    assert confirmed_name([ask, said("listener", "Why do you ask?"), said("listener", "London.")]) is None
    assert confirmed_name([said("interviewer", "How's your day?"), said("listener", "Busy.")]) is None
    # a confirmation still wins over the answer
    assert confirmed_name([ask, said("listener", "Hana."), said("interviewer", "Hannah. Did I get that right?")]) == "Hannah"


def test_curly_apostrophes_count_server_side():
    """Review: GPT-Live writes didn’t, so the closing question and I’m never matched."""
    got = IV.covered([{"who": "interviewer", "text": "Is there anything I didn’t ask that I should have?"}])
    assert got["closing"]
    assert IV.confirmed_name([{"who": "listener", "text": "Hi, I’m Siobhan"}]) == "Siobhan"
    assert re.search(IV.PAGE_DEFAULTS["closing_question"], "Anything I didn’t ask?", re.I)


def test_a_complete_debrief_with_curly_apostrophes_counts_as_done(world):
    said = [(w, t.replace("didn't", "didn’t")) for w, t in FULL]
    sid = debrief_session(world, said)
    assert world.client.post("/api/interview/end", json={"sid": sid, "how": "closed"}).json()["done"]


def test_a_second_debrief_never_overwrites_the_first_completed_one(world):
    sid1 = debrief_session(world, FULL)
    world.client.post("/api/interview/tool", json={"sid": sid1, "name": "end_debrief"})
    world.client.post("/api/interview/end", json={"sid": sid1, "how": "done"})
    study = world.dir / "study"
    first = json.loads((study / "debrief.json").read_text())
    sid2 = debrief_session(world, FULL)  # a second chapter's, if one happens anyway
    world.client.post("/api/interview/tool", json={"sid": sid2, "name": "end_debrief"})
    assert world.client.post("/api/interview/end", json={"sid": sid2, "how": "done"}).json()["done"]
    assert json.loads((study / "debrief.json").read_text()) == first and first["session"] == sid1
    own = json.loads((study / f"debrief-{sid2}.json").read_text())
    assert own["done"] and own["session"] == sid2
    sid3 = debrief_session(world, FULL[:2])  # one left halfway: its own state too
    assert world.client.post("/api/interview/end", json={"sid": sid3, "how": "pagehide"}).json()["done"] is False
    assert json.loads((study / "debrief.json").read_text()) == first
    assert json.loads((study / f"debrief-{sid3}.json").read_text())["done"] is False
    assert IV.debrief_done(world.dir)


def recording_client(calls, on_get=None):
    class Http:
        def get(self, url, headers=None, timeout=None, follow_redirects=None):
            calls.append(url)
            if on_get:
                on_get()
            return types.SimpleNamespace(status_code=200, content=tiny_wav())
    return Http()


def test_after_end_writes_nothing_into_a_deleted_or_reset_testers_folder(cfg, tmp_path, monkeypatch):
    """Review: the recording came minutes later and recreated listeners/t-x/study/ after a delete, or put the
    old voice into the reset folder."""
    import shutil
    monkeypatch.setattr(live_session, "api_key", lambda: "sk-test")
    lis = tmp_path / "t-sam"
    (lis / "study").mkdir(parents=True)
    (lis / "tester.json").write_text(json.dumps({"created": "2026-11-16T09:00:00"}))
    who = IV.identity(lis)
    check = lambda: IV.still_theirs(lis, who)  # noqa: E731

    calls = []
    deleted = lambda: shutil.rmtree(lis)  # noqa: E731 (deleted in /admin while the download ran)
    assert IV.fetch_audio(cfg, lis / "study", "live_1", sleep=lambda s: None, client=recording_client(calls, deleted),
                          check=check) is None
    assert calls and not lis.exists()

    (lis / "study").mkdir(parents=True)
    (lis / "tester.json").write_text(json.dumps({"created": "2026-11-16T09:00:00", "reset_at": "2026-11-16T10:00:00"}))
    calls.clear()  # reset before it started: not one try
    assert IV.fetch_audio(cfg, lis / "study", "live_2", sleep=lambda s: None, client=recording_client(calls),
                          check=check) is None
    assert not calls and not list((lis / "study").iterdir())

    who = IV.identity(lis)  # the same tester still: it lands
    got = IV.fetch_audio(cfg, lis / "study", "live_3", sleep=lambda s: None, client=recording_client(calls),
                         check=lambda: IV.still_theirs(lis, who))
    assert got == lis / "study" / "live_3.mp3" and got.exists()


def test_the_summary_is_not_written_for_a_reset_tester(cfg, tmp_path, monkeypatch):
    seen = summary_client(monkeypatch)
    lis = tmp_path / "t-sam"
    write_debrief(lis / "study", "s1", FULL, done=True)
    (lis / "tester.json").write_text(json.dumps({"created": "c"}))
    s = {"id": "s1", "mode": "debrief", "live_id": None, "values": {"person": "Sam"}, "listener": lis,
         "who": ("c", "reset later")}
    IV._after_end(cfg, lis / "study", s)
    assert not seen and not (lis / "study" / "debrief_summary.md").exists()
    s["who"] = IV.identity(lis)
    IV._after_end(cfg, lis / "study", s)
    assert len(seen) == 1 and (lis / "study" / "debrief_summary.md").exists()


def test_a_stop_word_before_the_name_is_dropped_and_yes_words_are_never_names():
    """Review of the fix: "Okay Sam, did I get that right?" gave None, and "Absolutely." after "Can I call you
    Sam?" was saved as the name."""
    from demo.interviewer import confirmed_name
    said = lambda who, text: {"who": who, "text": text}  # noqa: E731
    for line in ("It's Sam. Did I get that right?", "Okay Sam, did I get that right?", "Thanks Sam. Did I get that right?"):
        assert confirmed_name([said("interviewer", line)]) == "Sam", line
    ask = said("interviewer", "Can I call you Sam?")
    for yes in ("Absolutely.", "Definitely!", "Please.", "Certainly", "Of course."):
        assert confirmed_name([ask, said("listener", yes)]) is None, yes
    assert confirmed_name([said("listener", "It's Tuesday, I'm tired")]) is None


def test_a_name_given_with_the_link_is_kept_unless_the_transcript_clearly_corrects_it():
    """Ian's notes (2026-11-16): the interviewer greets them by the name Ian gave ("Hi, is this Rolando?"). A
    correction is saved; a yes, a mishearing after a yes, or a capitalised word later on never replaces it."""
    from demo.interviewer import confirmed_name
    said = lambda who, text: {"who": who, "text": text}  # noqa: E731
    hi = said("interviewer", "Hi, is this Rolando?")
    for yes in ("Yes.", "Yeah, that's me.", "Yes, it's Orlando.", "Speaking.", "It is."):
        assert confirmed_name([hi, said("listener", yes)], known="Rolando") is None, yes
    assert confirmed_name([hi, said("listener", "No, it's Roland.")], known="Rolando") == "Roland"
    assert confirmed_name([hi, said("listener", "This is Maria, actually")], known="Rolando") == "Maria"
    assert confirmed_name([hi, said("listener", "Roland.")], known="Rolando") == "Roland"
    # said back and confirmed by the interviewer: that wins, whatever else was said
    assert confirmed_name([hi, said("listener", "Yes, but it's Roland"), said("interviewer", "Roland. Did I get that right?"),
                           said("listener", "Yes.")], known="Rolando") == "Roland"
    # later talk is not a name: Ian's is in the conversation now
    later = [hi, said("listener", "Yes."), said("interviewer", "Ian built what I'm about to show you."),
             said("listener", "Oh, it's Ian's thing? I'm Curious now.")]
    assert confirmed_name(later, known="Rolando") is None
    assert confirmed_name([said("listener", "It's Ian's thing?")]) is None


def test_with_a_name_known_only_the_answer_to_the_who_check_can_replace_it():
    """The intro says "you'll be speaking with someone else next", which once counted as asking who they are:
    "It's Ian who sent me" then replaced Rolando. With a name known, a tester's line is a name only as the
    answer to the who-check itself; later it must come through the interviewer's confirmation."""
    from demo.interviewer import confirmed_name
    said = lambda who, text: {"who": who, "text": text}  # noqa: E731
    hi, yes = said("interviewer", "Hi, is this Rolando?"), said("listener", "Yes.")
    later = said("interviewer", "Any questions? You'll be speaking with someone else next.")
    for line in ("Is this Ian's thing? It's Ian who sent me.", "I'm Canadian, does that matter?", "It's Ian.",
                 "This is Maria, by the way."):
        assert confirmed_name([hi, yes, later, said("listener", line)], known="Rolando") is None, line
    talking = said("interviewer", "Is this a good time? You'll be talking with someone else in a moment.")
    assert confirmed_name([hi, yes, talking, said("listener", "I'm Canadian.")], known="Rolando") is None
    # the who-check's own answer still corrects it, also when it is asked again
    assert confirmed_name([hi, said("listener", "It's Rolo, actually.")], known="Rolando") == "Rolo"
    again = said("interviewer", "Sorry, what's your name?")
    assert confirmed_name([hi, said("listener", "Sorry?"), again, said("listener", "It's Maria.")], known="Rolando") == "Maria"
    # and a later correction comes through the interviewer saying it back
    assert confirmed_name([hi, yes, later, said("listener", "Call me Rolo."),
                           said("interviewer", "Rolo. Did I get that right?")], known="Rolando") == "Rolo"
    # with no name known the looser reading stays (they may introduce themselves anywhere)
    assert confirmed_name([later, said("listener", "It's Maria.")]) == "Maria"


def test_the_handover_saves_a_correction_and_keeps_a_confirmed_name(world):
    c = world.client
    (world.dir / "tester.json").write_text(json.dumps({"name": "Rolando"}))
    got = c.post("/api/interview/start", json={"mode": "intro", "sdp": "OFFER"}).json()
    assert got["notes"]["opened"] == 'Begin now: say hello warmly and check who it is: "Hi, is this Rolando?" Nothing else yet.'
    sid = got["sid"]
    c.post("/api/interview/line", json={"sid": sid, "who": "out", "text": "Hi, is this Rolando?"})
    c.post("/api/interview/line", json={"sid": sid, "who": "in", "text": "Yes, it's Orlando."})
    hand = c.post("/api/interview/tool", json={"sid": sid, "name": "handoff_to_guide", "by": "page"}).json()
    assert hand["name"] == "Rolando" and "name_from" not in hand and world.testers.names == []
    c.post("/api/interview/end", json={"sid": sid, "how": "handover"})

    sid = c.post("/api/interview/start", json={"mode": "intro", "sdp": "OFFER"}).json()["sid"]
    c.post("/api/interview/line", json={"sid": sid, "who": "out", "text": "Hi, is this Rolando?"})
    c.post("/api/interview/line", json={"sid": sid, "who": "in", "text": "No, it's Roland."})
    hand = c.post("/api/interview/tool", json={"sid": sid, "name": "handoff_to_guide", "by": "page"}).json()
    assert hand["name"] == "Roland" and hand["name_from"] == "transcript" and world.testers.names == [("t-sam", "Roland")]


def test_the_study_cli_never_loads_the_repos_dot_env():
    import demo.cli
    from stages.common import ROOT
    assert demo.cli.ROOT != ROOT  # tests/demo/conftest.py


def test_end_demo_with_the_opening_ready_and_unstarted_is_while_talking_not_while_it_was_made():
    """The page reports "making" until the piece is read, also once its opening is ready and offered: the account
    then said both "it was ready, but they didn't start it" and "pressed End demo while the piece was being made"."""
    n = IV.notes()
    ctx = {"run": "r", "title": "The List", "summary": "Why.", "minutes_heard": 0, "minutes": 10}
    act = {"started": True, "ready": True, "listened": False, "end": {"phase": "making"}}
    got = IV.account(ctx, [], act)
    assert got[-2:] == [n["did_none_ready"], n["did_end_talk"]] and n["did_end_making"] not in got
    for ready in (False, None):
        assert IV.account(ctx, [], {**act, "ready": ready})[-1] == n["did_end_making"]


# --- Ian's third run (2026-11-19) -------------------------------------------------------------------

def test_the_debrief_has_no_delegation_and_its_header_still_names_its_tool(world, cfg):
    """Ian's third run: the debrief stalled on a delegation that could only come back empty ("Just a second.",
    "I'm still here."). Its session has none; the intro keeps its own (it never stalled)."""
    c = world.client
    sid = c.post("/api/interview/start", json={"mode": "debrief", "sdp": "OFFER"}).json()["sid"]
    assert "delegation" not in world.offers[-1]
    first = lines(world.dir / "study" / f"debrief-{sid}.jsonl")[0]
    assert first["backend"] is None and first["tools"] == ["end_debrief"]
    # The page still closes it on the farewell and posts end_debrief itself.
    assert c.post("/api/interview/tool", json={"sid": sid, "name": "end_debrief", "by": "page"}).json()["act"] == "close"
    sid = c.post("/api/interview/start", json={"mode": "intro", "sdp": "OFFER"}).json()["sid"]
    back = world.offers[-1]["delegation"]["responses"]
    assert [t["name"] for t in back["tools"]] == ["set_listener_name", "handoff_to_guide"]
    first = lines(world.dir / "study" / f"intro-{sid}.jsonl")[0]
    assert first["backend"] == cfg["live"]["backend_model"] and first["tools"] == ["set_listener_name", "handoff_to_guide"]
    # What a delegation brought back can be put in the transcript (the page's `backend` lines).
    c.post("/api/interview/line", json={"sid": sid, "who": "backend", "text": "completed: empty"})
    assert lines(world.dir / "study" / f"intro-{sid}.jsonl")[-1] == {**lines(world.dir / "study" / f"intro-{sid}.jsonl")[-1],
                                                                       "who": "backend", "text": "completed: empty"}


def test_stall_lines_are_never_replies_and_an_invitation_never_leaves_a_turn_hanging():
    """page/interview.js and demo/sim_interviewer.py match these on the line (straight apostrophes)."""
    stall, go_on = (re.compile(IV.PAGE_DEFAULTS[k], re.I) for k in ("stall", "go_on"))
    for said in ("Just a second.", "I'm still here.", "Right here, still with you.", "Hang on.", "One moment, please.",
                 "Sorry, just a second. I'm still here.", "Okay, let me think.", "Hmm, hold on.", "Still with you."):
        assert stall.search(said), said
    for said in ("Yeah, I follow you, and that's useful to hear.", "What would you change first?",
                 "Just a second thing: how was the voice?", "I'm here to hear what you thought. What stood out?",
                 "Sorry, go on.", IV.farewell("Sam")):
        assert not stall.search(said), said
    for said in ("Sorry, go on.", "Mm.", "Go on.", "Mm-hmm.", "Take your time.", "No, go ahead.",
                 # review (2026-11-19): a line that ends with an invitation is one too
                 "Got it, take your time.", "No rush, take your time.", "I see, go on.", "Sorry, I cut you off. Go on.",
                 "Of course, go ahead.", "Sorry about that, please continue."):
        assert go_on.search(said), said
    for said in ("Yeah, I follow you, and that's useful to hear.", "Okay, that makes sense.", "That helps.",
                 "That makes sense, the opening was long.", "So the next part, we'll continue."):
        assert not go_on.search(said), said
    S = IV.PAGE_DEFAULTS
    assert S["hanging_ms"] > S["reply_wait_ms"] and 8000 <= S["hanging_ms"] < 9500  # past his pauses, before his 9.5 s
    assert S["note_gap_ms"] > 0


def test_the_debrief_prompt_answers_what_else_and_never_ends_a_turn_on_an_acknowledgement(cfg, tmp_path):
    text = IV.instructions("debrief", IV.values_from("Sam", {"run": None}, [], None))
    for want in ("Never end a turn on an acknowledgement alone", 'say their idea back in a few of their own words',
                 'If they ask "what else?", ask your next question, or, if you have enough, say your farewell.',
                 "Don't thank them for answers or call them useful", "When they offer an idea of their own",
                 '"Just a second", "I\'m still here"'):
        assert want in text, want
    assert "Thank you, that's useful" not in text and "once at most" not in text
    notes = IV.page_notes("debrief", IV.values_from("Sam", {"run": None}, [], None))
    assert notes["hanging"].startswith("[") and "your farewell, word for word" in notes["hanging"]
    assert "waiting for you" not in notes["hanging"]  # that phrase marks the `waiting` notes
    assert "hanging" not in IV.page_notes("intro", IV.intro_values_for("Sam"))


def _press(lis, phase, ago=5):
    from datetime import datetime, timedelta
    from demo import debrief
    (lis / "study").mkdir(parents=True, exist_ok=True)
    t = (datetime.now() - timedelta(seconds=ago)).strftime("%Y-%m-%dT%H:%M:%S")
    with open(lis / "study" / debrief.EVENTS_FILE, "a") as f:
        f.write(json.dumps({"t": t, "event": "end_demo", "phase": phase, "chapter_t": None, "detail": None}) + "\n")


def test_end_demo_stops_a_chapter_still_being_made_and_nothing_else(tmp_path):
    """Ian's third run: "We definately need to stop the run of the chapter once the post inverview starts, if
    the user clicks stop demo." Not at the chapter's own end (no press), not after it finished playing, not while
    it is only finishing (stage archive), not on an old press (a debrief taken again later)."""
    making = {"run": "r", "state": "making", "stage": "writing"}
    lis = tmp_path / "t-sam"
    assert not IV.stop_on_end(lis, making)  # no press: the chapter's end handed back
    _press(lis, "reading")
    assert IV.stop_on_end(lis, making)
    assert IV.stop_on_end(lis, {**making, "queued": True})  # waiting its turn: still being made
    for st in ({**making, "state": "ready"}, {**making, "state": "stopped"}, {**making, "stage": "archive"},
               {"state": "idle"}, None):
        assert not IV.stop_on_end(lis, st), st
    _press(lis, "ended")
    assert not IV.stop_on_end(lis, making)  # pressed after the piece had finished
    old = tmp_path / "t-old"
    _press(old, "making", ago=IV.END_PRESS_SECONDS + 60)
    assert not IV.stop_on_end(old, making)


def test_a_debrief_that_stops_the_chapter_is_told_so(cfg, monkeypatch, own_listeners, testers):
    monkeypatch.setenv(common.LISTENER_ENV, "t-sam")
    lis = own_listeners / "t-sam"
    lis.mkdir(parents=True)
    monkeypatch.setattr(live_session, "create_webrtc", lambda cfg, session, sdp, client=None: {"id": "live_1", "sdp": "A"})
    monkeypatch.setattr(IV, "_after_end", lambda *a: None)
    asked = []
    app = FastAPI()
    IV.mount(app, cfg, on_debrief=lambda ld: asked.append(ld) or True)
    c = TestClient(app)
    sid = c.post("/api/interview/start", json={"mode": "debrief", "sdp": "OFFER"}).json()["sid"]
    first = lines(lis / "study" / f"debrief-{sid}.jsonl")[0]
    stopped = IV.notes()["did_stopped"]
    assert asked == [lis] and first["did"].splitlines()[-1] == f"- {stopped}" and first["activity"]["stopped"] is True
    assert "No piece was started" not in first["did"]  # a stopped piece was a piece
    # The intro never asks; a hook that fails leaves the debrief as it was, unstopped.
    c.post("/api/interview/start", json={"mode": "intro", "sdp": "OFFER"})
    assert len(asked) == 1
    app = FastAPI()
    IV.mount(app, cfg, on_debrief=lambda ld: 1 / 0)
    sid = TestClient(app).post("/api/interview/start", json={"mode": "debrief", "sdp": "OFFER"}).json()["sid"]
    assert stopped not in lines(lis / "study" / f"debrief-{sid}.jsonl")[0]["did"]


def test_the_account_reads_a_stopped_chapter_from_its_status(cfg, own_listeners):
    from demo import debrief
    lis = own_listeners / "t-sam"
    root = lis / "chapters" / "r1"
    root.mkdir(parents=True)
    (root / "fast.json").write_text(json.dumps({"voice": "live"}))
    (root / "status.json").write_text(json.dumps({"run": "r1", "state": "stopped", "message": "Stopped when the demo ended."}))
    act = debrief.activity(lis, cfg)
    assert act["stopped"] is True and act["started"] is True
    assert IV.account({"run": "r1"}, [], act)[-1] == IV.notes()["did_stopped"]
    (root / "status.json").write_text(json.dumps({"run": "r1", "state": "ready"}))
    assert debrief.activity(lis, cfg)["stopped"] is False


def _session_file(study, mode, sid, live_id, ended="ended", age=0):
    import os
    import time as _t
    study.mkdir(parents=True, exist_ok=True)
    p = study / f"{mode}-{sid}.jsonl"
    recs = [{"t": "x", "s": 0, "who": "session", "mode": mode, "live_id": live_id}]
    if ended:
        recs.append({"t": "x", "s": 9, "who": "session", "ended": ended})
    p.write_text("".join(json.dumps(r) + "\n" for r in recs))
    if age:
        os.utime(p, (_t.time() - age, _t.time() - age))
    return p


def test_recordings_a_stopped_server_never_fetched_are_fetched_at_the_next_start(cfg, own_listeners, monkeypatch):
    """Ian's third run: the debrief's recording was lost (the server stopped within the two minutes _after_end
    waits). At start every interviewer session without its recording is fetched, as memory.run_pending does."""
    monkeypatch.setattr(live_session, "api_key", lambda: "sk-test")
    study = own_listeners / "t-sam" / "study"
    _session_file(study, "debrief", "a", "live_old", age=3600)      # an hour ago: fetched with no wait
    _session_file(study, "intro", "b", "live_done", age=3600)
    (study / "live_done.mp3").write_bytes(b"x")                      # already there
    _session_file(study, "intro", "c", "live_failed", ended="failed")  # never talked
    _session_file(study, "debrief", "d", "live_gone", age=40 * 86400)  # past OpenAI's 30 days
    _session_file(study, "debrief", "e", "live_new", age=30)          # 30 s ago: the rest of the wait
    (study / "events.jsonl").write_text("")
    found = sorted(x["live_id"] for x in IV.pending_recordings(cfg))
    assert found == ["live_new", "live_old"]
    calls, slept = [], []
    t = IV.fetch_pending(cfg, sleep=slept.append, client=recording_client(calls))
    t.join(30)
    assert (study / "live_old.mp3").exists() and (study / "live_new.mp3").exists()
    assert sorted(u.rsplit("/", 2)[1] for u in calls) == ["live_new", "live_old"]
    wait = float(cfg["live"]["archive_wait_seconds"])
    assert len(slept) == 1 and wait - 32 <= slept[0] <= wait - 28  # only the new one waits, and only what's left
    assert IV.pending_recordings(cfg) == [] and IV.fetch_pending(cfg) is None  # nothing left: no thread
    # Review: a session the server restarted under (no `ended` row) waits until it must be over before its first
    # try, and goes after the ended ones.
    _session_file(study, "debrief", "g", "live_open", ended=None, age=20)
    _session_file(study, "intro", "h", "live_late", age=10)
    order, slept = [], []
    t = IV.fetch_pending(cfg, sleep=slept.append, client=recording_client(order))
    t.join(30)
    assert [u.rsplit("/", 2)[1] for u in order] == ["live_late", "live_open"]
    live_max = (float(IV.icfg(cfg)["debrief_max_minutes"]) + 1) * 60
    assert wait - 12 <= slept[0] <= wait - 8 and wait + live_max - 22 <= slept[1] <= wait + live_max - 18
    cfg["demo"]["interviewer"]["store"] = False
    _session_file(study, "debrief", "f", "live_more")
    assert IV.fetch_pending(cfg) is None  # recordings off


def test_the_farewell_to_ian_himself_does_not_name_him_twice():
    """Live check, 2026-11-19: Ian's own debrief ended "Thank you, Ian. That really helps Ian." Testers keep
    the line that names him; the page keys on its end, which every form keeps."""
    assert IV.farewell("Ian") == "Thank you, Ian. That really helps. That's everything from me."
    assert IV.farewell(" ian ") == "Thank you, ian. That really helps. That's everything from me."
    assert IV.farewell("Sam") == "Thank you, Sam. That really helps Ian. That's everything from me."
    for name in ("Ian", "Sam", None):
        assert re.search(IV.PAGE_DEFAULTS["farewell"], IV.farewell(name), re.I)
