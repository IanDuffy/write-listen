"""demo/debrief.py: a tester's moments and their chapter, as the debrief is told them, from a live chapter's files."""

import json
import os
import time

from demo import debrief

TEXT1 = ("Picture a woman in a meeting. [D01] Her name is Donella Meadows. [D01, D14]\n\n"
         "The meeting is about trade. It angers her. So she gets up.")
TEXT2 = "Years later engineers still argue about her list. [D02]\n\nSome call it obvious."


def write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")


def live_run(chapters, name, *, voice="live", age=0, locked=(1, 2), asides=(), player=None, card=None):
    root = chapters / name
    write(root / "fast.json", {"mode": "fast", "voice": voice})
    if age:
        t = time.time() - age
        os.utime(root / "fast.json", (t, t))
    write(root / "05_outline.json", {"title": "The List", "angle": "Why her list of levers still matters.",
                                     "sections": [{"n": 1, "title": "One", "target_words": 750},
                                                  {"n": 2, "title": "Two", "target_words": 750}]})
    for n in locked:
        write(root / "06_segments" / f"seg{n:02d}.md", {1: TEXT1, 2: TEXT2}[n])
    if asides:
        write(root / "12_asks" / "asides.jsonl", "".join(json.dumps(a) + "\n" for a in asides))
    if player is not None:
        write(root / "fast" / "player.json", player)
    if card is not None:
        write(root / "fast" / "card.json", card)
    return root


def ask(pid, sentence, *said, kind="ask"):
    lines = []
    for i, text in enumerate(said):
        lines.append({"who": "listener", "text": text})
        lines.append({"who": "guide", "text": f"Answer {i}."})
    return {"id": f"a-{pid}-{sentence}", "kind": kind, "voice": "live", "paragraph": pid, "sentence": sentence,
            "transcript": lines}


def test_moments_are_their_questions_at_about_where_the_reading_was(tmp_path):
    lis = tmp_path / "t-sam"
    live_run(lis / "chapters", "older-run", age=3600, asides=[ask("s1-p01", 0, "Old question?")])
    live_run(lis / "chapters", "not-live", voice=None, asides=[ask("s1-p01", 0, "Not live?")])
    live_run(lis / "chapters", "the-run", asides=[
        ask("s2-p01", 0, "Which engineers?"),
        ask("s1-p02", 1, "Why did trade anger her?", "And then?"),
        {"kind": "check_in", "paragraph": "s2-p01", "sentence": 0, "transcript": [{"who": "listener", "text": "ok"}]},
        ask("s1-p01", 0),  # a tap where they said nothing
    ])
    got = debrief.moments(lis)
    # s1-p01 has 11 words; the first sentence of s1-p02 has 5 more: 16 words at 2.5 a second.
    assert got[0] == {"at_s": 6.4, "clock": "0:06", "words": "Why did trade anger her?"}
    p1 = len("Picture a woman in a meeting. Her name is Donella Meadows.".split())
    p2 = len("The meeting is about trade. It angers her. So she gets up.".split())
    assert got[1] == {"at_s": round((p1 + p2) / 2.5, 1), "clock": debrief.clock((p1 + p2) / 2.5), "words": "Which engineers?"}
    assert len(got) == 2


def test_a_moment_in_a_paragraph_not_found_has_no_time(tmp_path):
    lis = tmp_path / "t-sam"
    live_run(lis / "chapters", "r", locked=(1,), asides=[ask("s9-p01", 0, "Huh?")])
    assert debrief.moments(lis) == [{"at_s": None, "clock": None, "words": "Huh?"}]


def test_no_chapter_no_moments(tmp_path):
    assert debrief.moments(tmp_path) == []
    assert debrief.chapter_context(tmp_path) == {"run": None, "title": None, "summary": None, "minutes_heard": 0,
                                                 "minutes": None}


def test_chapter_context_heard_part_way(tmp_path):
    lis = tmp_path / "t-sam"
    live_run(lis / "chapters", "r", player={"state": "playing", "chapter_t": 9.0},
             card={"title": "A card title", "overview": "o", "summary": "The card's summary."})
    got = debrief.chapter_context(lis)
    words = len(" ".join([TEXT1, TEXT2]).split()) - 4  # the four [D..] markers' words come out
    assert got["run"] == "r" and got["title"] == "The List" and got["summary"] == "The card's summary."
    assert got["minutes"] == round(words / 2.5 / 60, 1) and got["minutes_heard"] == 0.1


def test_im_done_part_way_is_heard_part_way(cfg, tmp_path):
    """Review: I'm done writes state "done" too, and was told as the whole chapter heard (10 minutes for 2)."""
    lis = tmp_path / "t-sam"
    live_run(lis / "chapters", "r", locked=(1,), player={"state": "done", "chapter_t": 120.0})
    got = debrief.chapter_context(lis, cfg)
    assert got["minutes"] == 10.0 and got["minutes_heard"] == 2.0
    from demo import interviewer
    assert interviewer.values_from("Sam", got, [])["heard"] == "They heard about 2 of its 10 minutes."


def test_chapter_context_heard_to_the_end_and_unfinished_chapters(cfg, tmp_path):
    lis = tmp_path / "t-sam"
    root = live_run(lis / "chapters", "r", locked=(1,), player={"state": "done"})
    got = debrief.chapter_context(lis, cfg)
    assert got["minutes"] == round(1500 / 2.5 / 60, 1) == got["minutes_heard"]  # the outline's target words
    assert got["summary"] == "Why her list of levers still matters."  # no card: the outline's angle
    (root / "05_outline.json").write_text(json.dumps({"title": "T", "sections": [{"n": 1}, {"n": 2}]}))
    cfg.setdefault("demo", {})["chapter_minutes"] = 10
    assert debrief.chapter_context(lis, cfg)["minutes"] == 10


# --- what they did (Ian's notes, 2026-11-18) ------------------------------------------------------------

QUIET_ASK = r"\b(some quiet|quiet time|be quiet|stop talking)\b"   # config.yaml live.page.quiet_ask, shortened


def said(t, who, text=None, **more):
    return {"t": f"2026-11-18T{t}", "who": who, **({"text": text} if text is not None else {}), "mode": "live", **more}


def ians_run(lis, *, timeline=True):
    """Ian's own second run (listeners/t-ian-run-2, 2026-11-18), as its records were when he ended: five minutes
    of talk, a chapter started, quiet asked for, one tap to ask how it was going, quiet again, and the debrief
    begun at 18:52:39, 49 seconds before the opening was ready. He heard none of it."""
    write(lis / "sessions" / "20261118-184440.jsonl", "".join(json.dumps(x) + "\n" for x in [
        said("18:44:40", "session", names=["Ailsa"], returning=False),
        said("18:44:41", "page", "This session picks up from the person who welcomed them; they just told them their name is Ian."),
        said("18:44:43", "guide", "Hey, Ian. I'm Ailsa."),
        said("18:44:48", "listener", "Hey Ailsa, how's it going"),
        said("18:46:34", "guide", "Want to hear what I do?"),
        said("18:46:37", "listener", "Yeah, sure"),
        said("18:48:11", "page", "[You've been talking a few minutes. At the next natural pause, move toward their chapter.]"),
        said("18:49:51", "guide", "Wanna hear that one?"),
        said("18:49:56", "listener", "Yeah, sure. That, that sounds like a good story"),
        said("18:49:57", "tool", name="start_chapter", input={"topic": "Early electrical lines."},
             result={"started": True, "first_audio_minutes": 4}),
        said("18:49:57", "page", "[Their chapter is under way; the opening should be ready in about 4 minutes.]"),
        said("18:50:09", "guide", "Or if you'd rather have some quiet while I do."),
        said("18:50:15", "listener", "[tongue click ]Yeah, some quiet is fine"),
        said("18:50:17", "guide", "Sure thing, I'll just wait."),
        said("18:50:18", "page", "[The guide stepped back.]"),
        said("18:50:48", "session", live_id="live_u2"),
        said("18:50:48", "page", "[A new voice session: tap.]"),
        said("18:50:52", "listener", "Hey"),
        said("18:50:54", "guide", "Hey, I'm right here. What's up?"),
        said("18:50:57", "listener", "Yeah so how's the progress"),
        said("18:50:57", "tool", name="get_status", input={}, result={"state": "being made", "starts_playing_in_minutes": 3}),
        said("18:51:05", "guide", "It should be ready to start in about 3 minutes."),
        said("18:51:15", "listener", ", in that case, no worries. Uh I'll, let's, have some quiet again. That's fine"),
        said("18:51:17", "guide", "Sure thing. Okay, I'll just wait."),
        said("18:51:19", "page", "[The guide stepped back.]")]))
    write(lis / "sessions" / "log.jsonl", json.dumps({"stage": "voice", "cost_usd": 0.29}) + "\n")
    root = live_run(lis / "chapters", "when-early-electrical-lines-20261118", locked=(),
                    card={"title": "When Electric Lines Reached Town", "overview": "o", "summary": "The card's summary."})
    write(root / "status.json", {"state": "making", "started": "2026-11-18T18:49:58-05:00", "first_audio": False})
    if timeline:
        write(root / "fast_timeline.jsonl", "".join(json.dumps(x) + "\n" for x in [
            {"t": 0.14, "ts": "2026-11-18T18:49:58", "event": "seed_set"},
            {"t": 210.97, "ts": "2026-11-18T18:53:28", "event": "first_audio", "section": 1}]))
    return root


def at(clock):
    from datetime import datetime
    return datetime.fromisoformat(f"2026-11-18T{clock}").timestamp()


def test_ians_run_he_asked_for_quiet_tapped_once_and_heard_none_of_it(tmp_path):
    """Ian's debrief said "Earlier, you chose not to cut in" though he had heard none of the piece: the
    interviewer was told only "They heard less than a minute of it" and "they didn't stop it to ask anything"."""
    from demo import interviewer
    lis = tmp_path / "t-ian-run-2"
    ians_run(lis)
    cfg = {"live": {"page": {"quiet_ask": QUIET_ASK}}}
    act = debrief.activity(lis, cfg, now=at("18:52:39"))
    assert act == {"talk_s": 314.0, "started": True, "quiet_asks": 2, "step_backs": 2, "taps": 1, "status_asks": 1,
                   "ready": False, "listened": False, "pauses": 0, "seeks": 0, "asks": 0, "end": None,
                   "stopped": False}
    ctx = debrief.chapter_context(lis, cfg)
    assert ctx["minutes_heard"] == 0
    v = interviewer.values_from("Ian", ctx, debrief.moments(lis, cfg), act)
    assert v["did"].splitlines() == [
        "- They talked with the voice for about 5 minutes before it started making an audio piece for them.",
        '- The piece is titled "The List".',
        "- They asked for quiet twice while the piece was being made.",
        "- While it was quiet, they tapped to talk once.",
        "- They asked how the piece was coming along once.",
        "- They heard none of the piece: it wasn't ready yet when they ended."]
    assert v["asking"].startswith("They heard none of the piece, so there is nothing to ask here: don't ask about listening")
    text = interviewer.instructions("debrief", v)
    for wrong in ("less than a minute of it", "didn't stop it to ask anything", "Ask whether anything made them want to",
                  "The card's summary."):  # no summary of a piece they never heard
        assert wrong not in text, wrong
    assert "never say they chose not to" in text and "anything not listed here didn't happen" in text
    assert v["_activity"] == act
    # Read later, once the opening exists, with the End demo press on record: still not ready when they ended.
    debrief.add_event(lis, "end_demo", "resting")
    ev = lis / "study" / "events.jsonl"
    ev.write_text(ev.read_text().replace(json.loads(ev.read_text())["t"], "2026-11-18T18:52:38"))
    act = debrief.activity(lis, cfg, now=at("19:30:00"))
    assert act["ready"] is False and act["end"] == {"phase": "resting", "chapter_t": None, "t": "2026-11-18T18:52:38"}
    assert interviewer.account(ctx, [], act)[-2:] == ["They heard none of the piece: it wasn't ready yet when they ended.",
                                                      "They pressed End demo while the voice was quiet."]
    # Without the press, a later reading can only say what the folder shows now: it was ready, never started.
    ev.unlink()
    assert interviewer.account(ctx, [], debrief.activity(lis, cfg, now=at("19:30:00")))[-1] == \
        "They heard none of the piece: it was ready, but they didn't start it."


def test_the_account_says_only_what_the_records_show(tmp_path):
    from demo import interviewer
    cfg = {"live": {"page": {"quiet_ask": QUIET_ASK}}}
    # Nothing at all: no claim.
    empty = debrief.activity(tmp_path / "nobody", cfg)
    assert empty == {"talk_s": None, "started": False, "quiet_asks": 0, "step_backs": 0, "taps": 0, "status_asks": 0,
                     "ready": None, "listened": False, "pauses": 0, "seeks": 0, "asks": 0, "end": None,
                     "stopped": False}
    none = {"run": None, "title": None, "summary": None, "minutes_heard": 0, "minutes": None}
    assert interviewer.account(none, [], empty) == ["The app has no record of what they did: ask, don't assume."]
    # A conversation and End demo, no chapter started.
    lis = tmp_path / "t-talk"
    write(lis / "sessions" / "s1.jsonl", "".join(json.dumps(x) + "\n" for x in [
        said("10:00:00", "guide", "Hey."), said("10:00:05", "listener", "Hi."), said("10:02:10", "listener", "I should go.")]))
    debrief.add_event(lis, "end_demo", "talk")
    act = debrief.activity(lis, cfg)
    assert act["talk_s"] == 130.0 and not act["started"] and act["end"]["phase"] == "talk"
    assert interviewer.account(none, [], act) == ["They talked with the voice for about 2 minutes. No piece was started.",
                                                  "They pressed End demo while talking with the voice."]
    # No piece was made: nothing about "the piece" or "the wait" (review, 2026-11-18: the unheard line invited
    # "How was the wait?" of a tester who pressed End demo forty seconds into the conversation).
    asking = interviewer.values_from("Sam", none, [], act)["asking"]
    assert asking.startswith("No piece was made for them") and "the piece" not in asking and "the wait" not in asking
    assert "If you ask here, ask about the conversation they had" in asking  # never an owed question (review, 2026-11-18)
    # The guide stepped back by itself (nobody asked for quiet), no start_chapter in the transcript (the run's
    # own start is used), and no timeline: readiness is the status's.
    lis = tmp_path / "t-rest"
    root = ians_run(lis, timeline=False)
    write(lis / "sessions" / "20261118-184440.jsonl", "".join(json.dumps(x) + "\n" for x in [
        said("18:48:58", "guide", "Hey."), said("18:49:00", "listener", "Bridges, please."),
        said("18:50:18", "page", "[The guide stepped back.]")]))
    act = debrief.activity(lis, cfg, now=at("18:52:39"))
    assert (act["talk_s"], act["quiet_asks"], act["step_backs"], act["ready"]) == (60.0, 0, 0, False)  # the rest came before any start record
    write(lis / "sessions" / "20261118-184440.jsonl", "".join(json.dumps(x) + "\n" for x in [
        said("18:48:58", "guide", "Hey."), said("18:49:00", "listener", "Bridges, please."),
        said("18:49:57", "tool", name="start_chapter", input={}, result={"started": True}),
        said("18:50:18", "page", "[The guide stepped back.]")]))
    act = debrief.activity(lis, cfg, now=at("18:52:39"))
    assert (act["quiet_asks"], act["step_backs"]) == (0, 1)
    ctx = debrief.chapter_context(lis, cfg)
    assert "The voice went quiet while the piece was being made." in interviewer.account(ctx, [], act)
    (root / "status.json").unlink()
    assert debrief.activity(lis, cfg, now=at("18:52:39"))["ready"] is None
    assert interviewer.account(ctx, [], debrief.activity(lis, cfg, now=at("18:52:39")))[-1] == "They heard none of the piece."


def test_a_tester_who_heard_it_asked_paused_and_ended_part_way(cfg, tmp_path):
    from demo import interviewer
    lis = tmp_path / "t-sam"
    root = ians_run(lis)
    for n in (1, 2):
        write(root / "06_segments" / f"seg{n:02d}.md", {1: TEXT1, 2: TEXT2}[n])
    write(root / "fast" / "player.json", {"state": "paused", "chapter_t": 240.0})
    write(root / "live" / "reading.jsonl", json.dumps({"t": "2026-11-18T18:54:00", "id": "s1-p01", "heard": "picture a woman"}) + "\n")
    write(root / "12_asks" / "asides.jsonl", json.dumps(ask("s1-p02", 1, "Why did trade anger her?")) + "\n")
    for name, phase, t in (("play", "resting", None), ("ask", "reading", 6.4), ("pause", "reading", 200), ("pause", "reading", 230),
                           ("seek", "paused", 100), ("end_demo", "paused", 240)):
        assert debrief.add_event(lis, name, phase, t)
    act = debrief.activity(lis, cfg)
    assert act["listened"] and act["ready"] is True and (act["pauses"], act["seeks"]) == (2, 1)
    assert act["end"]["phase"] == "paused" and act["end"]["chapter_t"] == 240.0
    ctx = {**debrief.chapter_context(lis, cfg), "minutes": 10.0, "minutes_heard": 4.0}
    got = debrief.moments(lis, cfg)
    v = interviewer.values_from("Sam", ctx, got, act)
    assert v["did"].splitlines()[-5:] == [
        "- They heard about 4 of its 10 minutes.",
        '- A few seconds in, they stopped it to ask: "Why did trade anger her?"',
        "- They paused it twice.",
        "- They moved to another place in it once.",
        "- They pressed End demo while the piece was paused."]
    assert "The card's summary." in v["did"]
    assert v["asking"].startswith('A few seconds in, they stopped it to ask "Why did trade anger her?"')


def test_minutes_heard_fall_back_to_the_reading_log(tmp_path):
    """A page that closed before its position arrived: what the reading log shows was heard still counts."""
    lis = tmp_path / "t-sam"
    root = live_run(lis / "chapters", "r")
    write(root / "live" / "reading.jsonl", "".join(json.dumps(x) + "\n" for x in [
        {"id": "s1-p01", "heard": " ".join(["w"] * 50)}, {"id": "s1-p01", "heard": " ".join(["w"] * 150)},
        {"id": "s1-p02", "heard": " ".join(["w"] * 150)}]))
    assert debrief.chapter_context(lis)["minutes_heard"] == min(2.0, debrief.chapter_context(lis)["minutes"])
    assert debrief.activity(lis)["listened"]


def test_events_are_bounded(tmp_path, monkeypatch):
    """POST /api/demo/event writes through add_event: known events only, short strings, a bounded file."""
    lis = tmp_path / "t-sam"
    assert debrief.add_event(lis, "delete_everything") is None and not (lis / "study").exists()
    rec = debrief.add_event(lis, "pause", "somewhere", "12.34", "x" * 5000)
    assert rec["phase"] is None and rec["chapter_t"] == 12.3 and len(rec["detail"]) == debrief.DETAIL_MAX
    assert debrief.add_event(lis, "seek", "reading", float("nan"))["chapter_t"] is None
    assert debrief.add_event(lis, "seek", "reading", -5)["chapter_t"] is None
    assert debrief.add_event(lis, "tap_talk", "resting", "soon")["chapter_t"] is None
    assert [e["event"] for e in debrief.events(lis)] == ["pause", "seek", "seek", "tap_talk"]
    assert debrief.activity(lis)["taps"] == 1  # a tap the transcript never recorded still counts
    assert sorted(p.name for p in lis.rglob("*") if p.is_file()) == ["events.jsonl"]  # study/, never sessions/
    monkeypatch.setattr(debrief, "EVENTS_MAX_BYTES", 10)
    assert debrief.add_event(lis, "pause") is None and len(debrief.events(lis)) == 4


# --- review of the 2026-11-18 change: the account must not say what didn't happen ------------------------

def session(lis, name, recs):
    write(lis / "sessions" / f"{name}.jsonl", "".join(json.dumps(x) + "\n" for x in recs))


def test_quiet_words_from_earlier_in_the_conversation_are_not_an_ask_for_quiet(tmp_path):
    """Every line since the conversation began counted at the first rest, and the guide rests by itself once
    the chapter is started: "I finally have some quiet", said of their evening, became "They asked for quiet
    once while the piece was being made", which the debrief's own example then asks about."""
    from demo import interviewer
    cfg = {"live": {"page": {"quiet_ask": QUIET_ASK + r"|\bi'?ll just listen\b"}}}
    lis = tmp_path / "t-evening"
    root = ians_run(lis, timeline=False)
    session(lis, "20261118-184440", [
        said("10:00:00", "guide", "Hey there."),
        said("10:00:05", "listener", "Long day, I finally got the kids down and have some quiet."),
        said("10:00:20", "guide", "Oh, that's the good part of the day."),
        said("10:00:30", "listener", "It really is."),
        said("10:01:20", "guide", "Want a chapter on lighthouses?"),
        said("10:01:30", "listener", "Sure, I'll just listen."),  # agrees to the chapter: said before it was started
        said("10:01:36", "tool", name="start_chapter", input={}, result={"started": True}),
        said("10:01:40", "guide", "On it. I'll be back when it's ready."),
        said("10:01:45", "page", "[The guide stepped back.]")])
    act = debrief.activity(lis, cfg, now=at("10:03:00"))
    assert (act["quiet_asks"], act["step_backs"]) == (0, 1)
    got = interviewer.account(debrief.chapter_context(lis, cfg), [], act)
    assert "The voice went quiet while the piece was being made." in got and not [x for x in got if "asked for quiet" in x]
    assert root.exists()


def test_talk_is_summed_within_visits_never_across_a_gap_or_a_rest(tmp_path):
    """A tester back the next day "talked with the voice for about 1441 minutes": talk ran from the first line
    of their first visit to the chapter's start."""
    from demo import interviewer
    cfg = {"live": {"page": {"quiet_ask": QUIET_ASK}}}
    lis = tmp_path / "t-back"
    two = [{**said("19:00:00", "guide", "Hey."), "t": "2026-11-17T19:00:00"},
           {**said("19:02:00", "listener", "I have to go, sorry."), "t": "2026-11-17T19:02:00"}]
    session(lis, "20261117-190000", two)
    session(lis, "20261118-190000", [said("19:00:00", "guide", "Welcome back."), said("19:00:10", "listener", "Hi again."),
                                     said("19:01:00", "tool", name="start_chapter", input={}, result={"started": True})])
    act = debrief.activity(lis, cfg, now=at("19:02:00"))
    assert act["talk_s"] == 180.0 and act["started"]
    none = {"run": None, "title": None, "summary": None, "minutes_heard": 0, "minutes": None}
    assert interviewer.account(none, [], act)[0].startswith("They talked with the voice for about 3 minutes before")
    # No chapter started: each visit's own span.
    lis = tmp_path / "t-twice"
    session(lis, "20261117-190000", two)
    session(lis, "20261118-190000", [said("19:00:00", "guide", "Welcome back."), said("19:01:00", "listener", "Hi again.")])
    assert debrief.activity(lis, cfg)["talk_s"] == 180.0
    # A rest before the start: the ten minutes away are not talk.
    lis = tmp_path / "t-rested"
    session(lis, "20261118-190000", [
        said("19:00:00", "guide", "Hey."), said("19:02:00", "listener", "Give me a minute."),
        said("19:02:01", "page", "[The guide stepped back.]"),
        said("19:12:00", "session", live_id="live_2"), said("19:12:00", "page", "[A new voice session: tap.]"),
        said("19:12:00", "listener", "Back."), said("19:12:30", "guide", "A chapter on tides?"),
        said("19:13:00", "tool", name="start_chapter", input={}, result={"started": True})])
    assert debrief.activity(lis, cfg, now=at("19:14:00"))["talk_s"] == 180.0


def test_a_tap_to_ask_with_no_words_recorded_is_told_as_a_tap(cfg, tmp_path):
    """The page's `ask` presses were recorded and never read: a tester who tapped to ask twice and got no word
    in (or wasn't heard) was told as "They never stopped it to ask anything"."""
    from demo import interviewer
    lis = tmp_path / "t-sam"
    root = ians_run(lis)
    for n in (1, 2):
        write(root / "06_segments" / f"seg{n:02d}.md", {1: TEXT1, 2: TEXT2}[n])
    write(root / "fast" / "player.json", {"state": "paused", "chapter_t": 120.0})
    write(root / "live" / "reading.jsonl", json.dumps({"id": "s1-p01", "heard": "picture a woman"}) + "\n")
    write(root / "12_asks" / "asides.jsonl", json.dumps(ask("s1-p02", 1)) + "\n")  # a tap, nothing of theirs heard
    ctx = {**debrief.chapter_context(lis, cfg), "minutes": 10.0, "minutes_heard": 2.0}
    act = debrief.activity(lis, cfg)
    v = interviewer.values_from("Sam", ctx, debrief.moments(lis, cfg), act)
    assert act["asks"] == 0 and "- They never stopped it to ask anything." in v["did"].splitlines()
    assert v["asking"].startswith("They heard the piece and never stopped it to ask anything.")
    for name, phase, t in (("play", "making", None), ("ask", "reading", 62), ("ask", "reading", 120), ("end_demo", "paused", 120)):
        assert debrief.add_event(lis, name, phase, t)
    act = debrief.activity(lis, cfg)
    assert act["asks"] == 2 and debrief.moments(lis, cfg) == []
    v = interviewer.values_from("Sam", ctx, [], act)
    assert "- They never stopped it to ask anything." not in v["did"].splitlines()
    assert "- They tapped to ask it something twice, but no question of theirs was recorded." in v["did"].splitlines()
    assert v["asking"].startswith("They tapped to ask it something twice") and "never stopped it to ask anything" not in v["did"]
    assert "{" not in v["did"] + v["asking"]


def test_audio_route_records_are_not_presses(tmp_path, monkeypatch):
    """page/speaker.js's audio_route records share study/events.jsonl but are no press: events() (the debrief's
    activity, the End demo lookups) never sees them, and they stop at AUDIO_MAX_BYTES so presses keep room."""
    lis = tmp_path / "t-sam"
    debrief.add_event(lis, "pause", "reading", 12)
    rec = debrief.add_event(lis, "audio_route", "reading", 5, "detail", data={"reason": "mic", "inputs": ["Speakerphone"],
                                                                        "live_mic_tracks": True, "result": "made up"})
    assert set(rec) == {"t", "event", "data"} and rec["data"]["reason"] == "mic" and rec["data"]["result"] is None
    assert rec["data"]["live_mic_tracks"] is None and rec["data"]["inputs"] == ["Speakerphone"]
    debrief.add_event(lis, "tap_talk", "resting")
    before = debrief.activity(lis)
    assert [e["event"] for e in debrief.events(lis)] == ["pause", "tap_talk"]
    assert [e["data"]["reason"] for e in debrief.audio_routes(lis)] == ["mic"]
    assert before["pauses"] == 1 and before["taps"] == 1
    for _ in range(5):
        debrief.add_event(lis, "audio_route", data={"reason": "playing"})
    assert debrief.activity(lis) == before
    monkeypatch.setattr(debrief, "AUDIO_MAX_BYTES", 10)
    assert debrief.add_event(lis, "audio_route", data={"reason": "mic"}) is None
    assert debrief.add_event(lis, "seek", "reading", 3) is not None   # presses still recorded
    assert debrief.audio_fields("not a dict") == {} and debrief.audio_fields(None) == {}
