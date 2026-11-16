"""The listening notes loop's capture (docs/library-timeline.md, "Capture"): an ask during a chapter gets the
after-conversation pass, which writes its moments at the place the listener tapped; save_seed writes a note;
a question saved for later becomes a seed the planner can take."""

import json
import time as real_time
from types import SimpleNamespace as NS

import pytest
from fakes import FakeAnthropic, Responder, all_text
from fastapi.testclient import TestClient

import server
from ask import aside as A
from overnight import library, live, planner, profiles, timeline
from stages import llm, memory

RUN = "2026-11-14-herons"


class Aside(Responder):
    """The fake memory pass, returning `moments` for an aside (keyed by a phrase in its transcript)."""

    def __init__(self, moments_by_phrase):
        super().__init__()
        self.moments_by_phrase = moments_by_phrase
        self.prompts = []

    def __call__(self, kwargs):
        out = super().__call__(kwargs)
        t = all_text(kwargs)
        if "You keep the memory of a guide" in t:
            self.prompts.append(t)
            transcript = t.split("<transcript>")[1]
            data = json.loads(out.content[0].text)
            got = next((m for phrase, m in self.moments_by_phrase.items() if phrase in transcript), [])
            seeds = [{"kind": "question", "text": "Do gardens make us notice?", "words": ""}] if got else []
            data.update(moments=got, library=[], seeds=seeds, forgot=False)  # the pass lists the question as a seed too
            out.content[0].text = json.dumps(data)
        return out


@pytest.fixture
def world(tmp_path, cfg, monkeypatch):
    lis = tmp_path / "listener"
    (lis / "sessions").mkdir(parents=True)
    cfg["listener_dir"] = str(lis)
    root = lis / "chapters" / RUN
    profiles.write_json(root / "00_source" / "up_next.json", {"id": "plan-14", "title": "Herons: Hunting by Standing Still"})
    (lis / "profile.md").write_text("# Profile\n- Walks by a pond.\n")

    def use(moments):
        resp = Aside(moments)
        monkeypatch.setattr(llm, "_client", FakeAnthropic(resp))
        return resp
    return NS(lis=lis, root=root, use=use, cfg=cfg)


def ask_session(lis, sid, t, lines, sentence="seg02-s004", para="seg02-p02"):
    p = lis / "sessions" / f"{sid}.jsonl"
    first = {"who": "session", "mode": "ask", "run": RUN, "live_id": "live_1", "t": t, "sentence_id": sentence,
             "paragraph_id": para}
    p.write_text("\n".join(json.dumps(x) for x in [first] + lines) + "\n")
    return p


def test_two_asks_make_two_moments_at_their_places(world, cfg):  # check 9, offline
    resp = world.use({
        "stand on one leg": [{"kind": "question", "text": "Why do they stand on one leg?", "outcome": "answered",
                              "reply": "Most likely to keep warm.", "title": "", "creator": ""}],
        "gardens": [{"kind": "question", "text": "Do gardens make us notice like this?", "outcome": "saved",
                     "reply": "", "title": "", "creator": ""}]})
    a = ask_session(world.lis, "20261114-091000", 402.4, [
        {"who": "listener", "text": "Hey, why do they"}, {"who": "listener", "text": "stand on one leg?"},
        {"who": "guide", "text": "Most likely to keep warm: one leg tucked up loses less heat."}])
    b = ask_session(world.lis, "20261114-091800", 850.0, [
        {"who": "listener", "text": "Um, do gardens make us notice like this?"},
        {"who": "guide", "text": "That's a big one. Let me keep it for another chapter."}], sentence="seg04-s001", para="seg04-p01")
    assert memory.after_session(cfg, a) is True and memory.after_session(cfg, b) is True
    ms = timeline.moments(world.root)
    assert [(m["kind"], m["position_s"], m["text"], m["outcome"]) for m in ms] == [
        ("question", 402.4, "Why do they stand on one leg?", "answered"),
        ("question", 850.0, "Do gardens make us notice like this?", "saved")]
    assert ms[0]["reply"] == "Most likely to keep warm." and ms[0]["session"] == "20261114-091000"
    assert (ms[0]["sentence_id"], ms[0]["paragraph_id"]) == ("seg02-s004", "seg02-p02")
    assert ms[1]["paragraph_id"] == "seg04-p01" and ms[1]["reply"] is None
    seed = library.seed(world.lis, ms[1]["seed"])  # the saved question is planner input, in the listener's words
    assert [x["text"] for x in library.seeds(world.lis)] == ["Do gardens make us notice like this?"]  # and only once
    assert seed["kind"] == "question" and seed["source"] == {"chapter": RUN, "session": "20261114-091800"}
    assert 'into the chapter "Herons: Hunting by Standing Still"' in resp.prompts[0] and "6:42 into" in resp.prompts[0]
    assert memory.after_session(cfg, b) is False and len(timeline.moments(world.root)) == 2  # once per session


def test_a_mention_goes_into_the_library_and_reads_in_your_library(world, cfg):
    world.use({"Upstream": [{"kind": "mention", "text": "Upstream, Mary Oliver", "title": "Upstream",
                             "creator": "Mary Oliver", "outcome": "saved", "reply": ""}]})
    s = ask_session(world.lis, "20261114-092000", 1000, [{"who": "listener", "text": "This is like Upstream, the Mary Oliver book."}])
    memory.after_session(cfg, s)
    m = timeline.moments(world.root)[0]
    w = library.works(world.lis)[0]
    assert (m["kind"], m["outcome"], m["title"], m["creator"]) == ("mention", "in_library", "Upstream", "Mary Oliver")
    assert m["outcome_ref"] == w["id"] and (w["title"], w["creator"], w["kind"]) == ("Upstream", "Mary Oliver", "work")
    assert w["source"] == {"chapter": RUN, "session": "20261114-092000"}


def test_save_seed_writes_a_note_and_a_saved_question_takes_it_over(world, cfg):
    world.use({"gardens": [{"kind": "question", "text": "Do gardens make us notice like this?", "outcome": "saved",
                            "reply": "", "title": "", "creator": ""}]})
    sid = "20261114-091800"
    A.save_seed(world.root, "Japanese gardens and attention", "they asked during the herons chapter", 850.0,
                "seg04-s001", session=sid, paragraph_id="seg04-p01")
    note = timeline.moments(world.root)[0]
    assert (note["kind"], note["text"], note["outcome"], note["position_s"]) == ("note", "Japanese gardens and attention", "saved", 850.0)
    assert note["seed"] == library.seed_id("idea", "Japanese gardens and attention (they asked during the herons chapter)")
    s = ask_session(world.lis, sid, 850.0, [
        {"who": "listener", "text": "Do gardens make us notice like this?"},
        {"who": "tool", "name": "save_seed", "input": {"topic": "Japanese gardens and attention"}, "result": {"saved": True}}])
    memory.after_session(cfg, s)
    ms = timeline.moments(world.root)
    assert len(ms) == 1  # one ask, one moment: the question, keeping the note's seed
    assert (ms[0]["kind"], ms[0]["text"], ms[0]["seed"]) == ("question", "Do gardens make us notice like this?", note["seed"])


def test_a_save_with_no_question_stays_a_note(world, cfg):
    world.use({})
    A.save_seed(world.root, "Borrowed scenery", "", 300.0, "seg02-s001", session="20261114-093000")
    s = ask_session(world.lis, "20261114-093000", 300.0, [{"who": "listener", "text": "Save that for later."}])
    memory.after_session(cfg, s)
    assert [(m["kind"], m["text"]) for m in timeline.moments(world.root)] == [("note", "Borrowed scenery")]


def test_only_an_ask_writes_moments(world, cfg):
    world.use({"concrete": [{"kind": "question", "text": "Should not appear", "outcome": "answered", "reply": "x",
                             "title": "", "creator": ""}]})
    p = world.lis / "sessions" / "20261114-100000.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in [{"who": "session", "mode": "change"},
                                                   {"who": "listener", "text": "Something on concrete"}]) + "\n")
    memory.after_session(cfg, p)
    assert timeline.moments(world.root) == [] and not (world.root / "moments.jsonl").exists()


def test_an_ask_older_than_a_remembered_conversation_still_gets_its_moments(world, cfg):
    world.use({"one leg": [{"kind": "question", "text": "Why one leg?", "outcome": "answered", "reply": "Warmth.",
                            "title": "", "creator": ""}]})
    a = ask_session(world.lis, "20261114-091000", 402, [{"who": "listener", "text": "Why one leg?"}])
    newer = world.lis / "sessions" / "20261114-120000.jsonl"
    newer.write_text(json.dumps({"who": "listener", "text": "I pour concrete."}) + "\n")
    memory.after_session(cfg, newer)
    profile = (world.lis / "profile.md").read_text()
    assert memory.after_session(cfg, a) is True
    assert [m["text"] for m in timeline.moments(world.root)] == ["Why one leg?"]
    assert (world.lis / "profile.md").read_text() == profile  # the newer pass's profile stands


def test_a_moments_seed_taken_by_the_planner_reads_became_tonights_chapter(world, cfg):  # the loop, closed
    world.use({"gardens": [{"kind": "question", "text": "Do gardens make us notice like this?", "outcome": "saved",
                            "reply": "", "title": "", "creator": ""}]})
    s = ask_session(world.lis, "20261114-091800", 850.0, [{"who": "listener", "text": "Do gardens make us notice like this?"}])
    memory.after_session(cfg, s)
    m = timeline.moments(world.root)[0]
    card = {"id": "plan-up", "status": "planned", "source": {"kind": "seed", "ref": m["seed"]}}
    planner.moments_follow(world.lis, None, card)
    st = timeline.state(world.lis, card, "planned", None, None)
    assert timeline.moments(world.root)[0]["outcome"] == "became_chapter"
    profiles.write_json(world.root / "finished.json", {"t": "2026-11-14T06:00:00"})
    st = timeline.state(world.lis, card, "planned", None, None)
    assert st["chapters"][0]["moments"][0]["outcome_label"] == "Became tonight's chapter"


# --- the live session's side: where they tapped, the pass for asks, save_seed's note ------------------------

@pytest.fixture
def app(cfg, monkeypatch, own_listeners):
    import ask_fixture
    cfg.pop("listener_dir", None)
    lis = profiles.create("ian")
    profiles.set_active("ian")
    root = lis / "chapters" / RUN
    ask_fixture.build(root, audio=True)
    monkeypatch.setattr(live, "create_webrtc", lambda cfg, session, sdp, client=None: {"id": "live_1", "sdp": "answer"})
    passes = []
    monkeypatch.setattr(memory, "after_session", lambda cfg, path: passes.append(path) or False)
    c = TestClient(server.create_app(server.Pipeline(), cfg, remember_on_start=False))
    return NS(c=c, lis=lis, root=root, passes=passes, cfg=cfg)


def test_an_ask_records_where_they_tapped_and_gets_the_pass(app):
    s = app.c.post("/api/overnight/live/start", json={"mode": "ask", "sdp": "offer", "run": RUN, "t": 20}).json()
    app.c.post("/api/overnight/live/line", json={"session": s["session"], "who": "listener", "text": "Why so still?"})
    app.c.post("/api/overnight/live/tool", json={"session": s["session"], "name": "save_seed",
                                                 "args": {"topic": "Heron eyesight", "why": "they asked"}})
    app.c.post("/api/overnight/live/end", json={"session": s["session"], "seconds": 12})
    first = json.loads((app.lis / "sessions" / f"{s['session']}.jsonl").read_text().splitlines()[0])
    ch = A.Chapter(app.root, app.cfg)
    here = ch.sents[ch.locate(20)]
    assert (first["mode"], first["run"], first["t"], first["sentence_id"]) == ("ask", RUN, 20, here.id)
    assert first["paragraph_id"] == f"seg{here.seg + 1:02d}-p{here.para + 1:02d}"
    for _ in range(100):
        if app.passes:
            break
        real_time.sleep(0.02)
    assert [p.name for p in app.passes] == [f"{s['session']}.jsonl"]
    note = timeline.moments(app.root)[0]  # save_seed's note, at once
    assert (note["kind"], note["text"], note["session"], note["position_s"]) == ("note", "Heron eyesight", s["session"], 20.0)
    assert note["seed"] in {x["id"] for x in library.seeds(app.lis)}  # the seed the aside wrote is the note's


def test_asks_can_be_left_without_the_pass(app):
    app.cfg["overnight"]["live"]["memory_on_asks"] = False
    s = app.c.post("/api/overnight/live/start", json={"mode": "ask", "sdp": "offer", "run": RUN, "t": 20}).json()
    app.c.post("/api/overnight/live/line", json={"session": s["session"], "who": "listener", "text": "Why so still?"})
    app.c.post("/api/overnight/live/end", json={"session": s["session"], "seconds": 5})
    real_time.sleep(0.1)
    assert app.passes == []


def test_a_saved_question_takes_over_only_the_note_it_matches(world, cfg):
    """Review: with two saves in one aside, the question replaces the note about it, not the first one."""
    world.use({"gardens": [{"kind": "question", "text": "Do Japanese gardens hide the view on purpose?",
                            "outcome": "saved", "reply": "", "title": "", "creator": ""}]})
    sid = "20261114-094000"
    A.save_seed(world.root, "Heron eyesight", "", 850.0, "seg04-s001", session=sid)
    A.save_seed(world.root, "Japanese gardens and hidden views", "", 850.0, "seg04-s001", session=sid)
    s = ask_session(world.lis, sid, 850.0, [{"who": "listener", "text": "Do Japanese gardens hide the view on purpose?"}])
    memory.after_session(cfg, s)
    got = sorted((m["kind"], m["text"]) for m in timeline.moments(world.root))
    assert got == [("note", "Heron eyesight"), ("question", "Do Japanese gardens hide the view on purpose?")]


# --- after the multi-agent review --------------------------------------------------------------------------

def test_a_note_about_something_else_is_left_alone(world, cfg):
    """A save_seed note and an unrelated saved question in one aside: two moments, each with its own seed."""
    world.use({"gardens": [{"kind": "question", "text": "Why do Japanese gardens hide the view?", "outcome": "saved",
                            "reply": "", "title": "", "creator": ""}]})
    sid = "20261114-095000"
    A.save_seed(world.root, "Heron eyesight", "", 850.0, "seg04-s001", session=sid)
    s = ask_session(world.lis, sid, 850.0, [{"who": "listener", "text": "Why do Japanese gardens hide the view?"}])
    memory.after_session(cfg, s)
    ms = {m["kind"]: m for m in timeline.moments(world.root)}
    assert ms["note"]["text"] == "Heron eyesight" and ms["question"]["text"] == "Why do Japanese gardens hide the view?"
    assert ms["question"]["seed"] != ms["note"]["seed"] and library.seed(world.lis, ms["question"]["seed"])


def test_a_person_mentioned_is_filed_as_a_person(world, cfg, monkeypatch):
    resp = Aside({"Oliver": [{"kind": "mention", "text": "Mary Oliver", "title": "Mary Oliver", "creator": "",
                              "outcome": "saved", "reply": ""}]})
    orig = resp.__call__

    def with_library(kwargs):  # the pass also lists her under library, as a person
        out = orig(kwargs)
        if "You keep the memory of a guide" in all_text(kwargs):
            data = json.loads(out.content[0].text)
            data["library"] = [{"kind": "person", "title": "Mary Oliver", "creator": "", "note": "loves her"}]
            out.content[0].text = json.dumps(data)
        return out
    resp.__call__ = with_library
    monkeypatch.setattr(llm, "_client", FakeAnthropic(with_library))
    s = ask_session(world.lis, "20261114-096000", 500, [{"who": "listener", "text": "I love Mary Oliver."}])
    memory.after_session(cfg, s)
    works = library.works(world.lis)
    assert [(w["kind"], w["title"]) for w in works] == [("person", "Mary Oliver")]
    m = timeline.moments(world.root)[0]
    assert m["work_kind"] == "person" and m["outcome_ref"] == works[0]["id"]


def test_an_asks_pass_never_makes_an_open_conversation_look_older(world, cfg):
    """An ask's .done must not count when ordering conversations: the guide conversation open while it happened
    still gets its whole pass when it ends."""
    world.use({"leg": [{"kind": "question", "text": "Why one leg?", "outcome": "answered", "reply": "Warmth.",
                        "title": "", "creator": ""}]})
    talk = world.lis / "sessions" / "20261116-080000.jsonl"
    talk.write_text(json.dumps({"who": "listener", "text": "I pour concrete."}) + "\n")
    ask = ask_session(world.lis, "20261116-082000", 402, [{"who": "listener", "text": "Why one leg?"}])
    assert memory.after_session(cfg, ask) is True
    assert memory.newest_done(cfg) is None
    assert memory.after_session(cfg, talk) is True and "concrete" in (world.lis / "profile.md").read_text()


def test_an_open_question_seed_from_after_chapter_finds_its_moment_by_aside(world, cfg):
    world.use({"gardens": [{"kind": "question", "text": "Do gardens make us notice like this?", "outcome": "saved",
                            "reply": "", "title": "", "creator": ""}]})
    p = ask_session(world.lis, "20261114-091800", 850.0, [{"who": "listener", "text": "Do gardens make us notice like this?"}])
    lines = p.read_text().splitlines()
    first = {**json.loads(lines[0]), "aside": "20261114-091800-live"}
    p.write_text("\n".join([json.dumps(first)] + lines[1:]) + "\n")
    memory.after_session(cfg, p)
    assert timeline.moments(world.root)[0]["aside"] == "20261114-091800-live"
    seed = {"id": "s-open", "text": "Can gardens train attention?", "source": {"chapter": RUN, "aside": "20261114-091800-live"}}
    assert [m["text"] for _, m in timeline.moments_for_seed(world.lis, seed)] == ["Do gardens make us notice like this?"]


def test_after_chapter_leaves_out_asides_the_pass_captured(world, cfg):
    A.append_aside(world.root, {"id": "a-live", "kind": "ask", "segment": 2, "transcript": [
        {"who": "listener", "text": "Do gardens make us notice like this?"}]})
    timeline.add_moments(world.root, [{"kind": "question", "position_s": 850, "text": "Do gardens make us notice like this?",
                                       "session": "s", "aside": "a-live", "outcome": "saved"}])
    profiles.write_json(world.root / "13_library.json", {"open_questions": [], "judged": []})
    library.after_chapter(cfg, world.lis, world.root)
    assert [s["kind"] for s in library.seeds(world.lis)] == []  # no question seed: the moment made its own


def test_a_chapter_read_live_writes_no_note(world):
    A.save_seed(world.root, "Borrowed scenery", "", 0.0, "live", note=False)
    assert timeline.moments(world.root) == []
