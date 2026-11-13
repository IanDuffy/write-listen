"""Fast Start milestone 5, the onboarding guide's half (stages/guide.py through
faststart/guide_hooks.py): scout_topic, start_chapter's result, add_note into listener_notes.md,
get_status. Offline: a fake model, the pipeline's command recorded, never run."""

import functools
import json
import random
import threading
import time
from types import SimpleNamespace as NS

import pytest
from fakes import FakeAnthropic, Responder, all_text, text_block
from fast_outline_fakes import SCOUT_PHRASE, Script
from fast_outline_fakes import reply as scout_reply
from fast_run_fakes import no_leaks, offline, settings, world  # noqa: F401

import server
from faststart import base, guide_hooks, notes, scout
from faststart import run as fast_run
from faststart import status as fast_status
from faststart import timings as T
from faststart.player import SimPlayer
from stages import common, guide, llm
from stages.common import Run


class ScoutingGuide(Responder):
    """The stock fake guide, and one that scouts. "maybe <topic>": it calls scout_topic and says
    nothing with it; "perhaps <topic>": it says a line and calls scout_topic. `after` is what it
    says once the tool has run (None: the stock fake's line; "": nothing at all). It also answers
    the fast scout's own prompt, for the early scout run for real."""

    after: str | None = None

    def __call__(self, kwargs):
        msg = scout_reply(kwargs, Script())
        if msg is None:
            return super().__call__(kwargs)
        self.calls.append(kwargs)
        return msg

    def guide(self, kwargs):
        last = kwargs["messages"][-1]["content"]
        if isinstance(last, str) and last.lower().startswith(("maybe ", "perhaps ")):
            self.guide_calls = getattr(self, "guide_calls", 0) + 1
            blocks = [NS(type="tool_use", id=f"tu{self.guide_calls}", name="scout_topic",
                         input={"topic": last.split(" ", 1)[1]})]
            if last.lower().startswith("perhaps"):
                blocks.insert(0, text_block("Harbours that set under water, then."))
            return self.msg(kwargs, None, stop="tool_use", content=blocks)
        if isinstance(last, list) and "scouting" in json.loads(last[0]["content"]) and self.after is not None:
            return self.msg(kwargs, self.after) if self.after else self.msg(kwargs, None, content=[])
        return super().guide(kwargs)


class FakeProc:
    returncode = None

    def poll(self):
        return self.returncode


@pytest.fixture
def chat(tmp_path, cfg, monkeypatch):
    """A conversation as the server builds it: start_seed in fast mode, the fast hooks, a fake model."""
    cfg = settings(cfg, tmp_path)
    runs = tmp_path / "runs"
    runs.mkdir()
    resp = ScoutingGuide()
    monkeypatch.setattr(llm, "_client", FakeAnthropic(resp))
    pipe = server.Pipeline(runs_dir=runs)
    started = []

    def start(args, run):
        started.append(args)
        pipe.proc, pipe.run = FakeProc(), run

    monkeypatch.setattr(pipe, "start", start)
    scouted, gate = [], threading.Event()
    gate.set()

    def early(cfg, runs_dir, topic):
        gate.wait(5)
        scouted.append(topic)
        return runs_dir / "_scouts" / "x"

    def session(**kw):
        hooks = guide_hooks.Hooks(cfg, runs, pipe.status, **{"early": early, **kw})
        return guide.Session(cfg, functools.partial(pipe.start_seed, mode="fast"), pipe.status, runs,
                             rng=random.Random(1), fast=hooks)
    return NS(session=session, resp=resp, pipe=pipe, started=started, runs=runs, cfg=cfg, scouted=scouted, gate=gate)


def join_scouts(s):
    for t in s.fast.threads:
        t.join(5)


def guide_calls(resp):
    return [c for c in resp.calls if "You are a guide. You make chapters" in all_text(c)]


# --- the tools the model is offered ---------------------------------------------------------------

def test_the_typed_chat_sends_only_the_keys_the_messages_api_takes(chat, cfg):
    s = chat.session()
    s.say(None)
    tools = guide_calls(chat.resp)[-1]["tools"]
    assert [t["name"] for t in tools] == ["scout_topic", "start_chapter", "add_note", "get_status"]
    assert all(set(t) == {"name", "description", "input_schema"} for t in tools)  # no expects_response
    # the voice agent's copy keeps it: scout_topic is the one tool it does not wait for
    assert [t.get("expects_response", True) for t in guide.TOOLS] == [False, True, True, True]
    kind = tools[2]["input_schema"]["properties"]["kind"]
    assert kind["enum"] == list(notes.KINDS) and tools[2]["input_schema"]["required"] == ["note"]

    # a conversation without the fast hooks (cli.py chat) is offered what it was before
    plain = guide.Session(cfg, lambda *a: "run", lambda: {"state": "idle"}, chat.runs, rng=random.Random(1))
    plain.say(None)
    assert [t["name"] for t in guide_calls(chat.resp)[-1]["tools"]] == ["start_chapter", "add_note", "get_status"]
    assert plain.tool("scout_topic", {"topic": "Roman concrete"}) == {"scouting": False}


# --- scout_topic ----------------------------------------------------------------------------------

def test_scout_topic_returns_at_once_and_scouts_in_the_background(chat):
    s = chat.session()
    chat.gate.clear()  # the search is held
    t0 = time.monotonic()
    out = s.tool("scout_topic", {"topic": "  Roman   sea concrete "})
    assert out["scouting"] is True and time.monotonic() - t0 < 1 and chat.scouted == []
    assert chat.pipe.busy() is False and chat.started == []  # never through the pipeline
    assert s.tool("scout_topic", {"topic": "roman sea CONCRETE"})["scouting"] is True  # the same topic: no second search
    assert len(s.fast.threads) == 1
    chat.gate.set()
    join_scouts(s)
    assert chat.scouted == ["Roman sea concrete"]


def test_the_third_topic_of_a_conversation_is_not_scouted(chat):
    s = chat.session()
    assert chat.cfg["fast"]["early_scouts"] == 2
    assert s.tool("scout_topic", {"topic": "Roman sea concrete"})["scouting"] is True
    assert s.tool("scout_topic", {"topic": "Octopus minds"})["scouting"] is True
    third = s.tool("scout_topic", {"topic": "The Antikythera mechanism"})
    assert third["scouting"] is False and "2 topics" in third["reason"]
    assert s.tool("scout_topic", {"topic": " "}) == {"scouting": False, "reason": "The topic was empty."}
    join_scouts(s)
    assert sorted(chat.scouted) == ["Octopus minds", "Roman sea concrete"]
    # the count is the conversation's: the next one starts again
    other = chat.session()
    assert other.tool("scout_topic", {"topic": "The Antikythera mechanism"})["scouting"] is True
    join_scouts(other)


def test_a_scout_that_fails_is_logged_and_never_reaches_the_conversation(chat, capsys):
    def broken(cfg, runs_dir, topic):
        raise RuntimeError("no route to host")

    s = chat.session(early=broken)
    assert s.tool("scout_topic", {"topic": "Roman sea concrete"})["scouting"] is True
    join_scouts(s)
    assert 'early scout of "Roman sea concrete" failed: RuntimeError: no route to host' in capsys.readouterr().out
    assert s.say("how's it going?")  # the conversation goes on


def test_no_scout_once_a_chapter_is_being_made(chat):
    s = chat.session()
    assert s.tool("start_chapter", {"topic": "Why Roman concrete lasted", "listener_said": "I pour concrete"})["started"]
    assert s.tool("scout_topic", {"topic": "Octopus minds"}) == \
        {"scouting": False, "reason": "A chapter is already being made."}
    assert s.fast.threads == []


def test_the_scouts_cost_stays_out_of_the_conversations_log(chat):
    """The real early scout, with a fake model: its calls are logged in its own folder under
    runs/_scouts/, so the conversation's $2 limit (guide.max_session_usd) never counts them."""
    s = chat.session(early=scout.early)
    s.say(None)
    spent = s.spent()
    assert s.say("maybe Roman sea concrete")
    join_scouts(s)
    (root,) = (chat.runs / "_scouts").iterdir()
    scouting = Run(name=root.name, root=root)
    assert [rec["stage"] for rec in scouting.read_log()] == ["fast_scout"] and scouting.spent() > 0
    assert len([c for c in chat.resp.calls if SCOUT_PHRASE in all_text(c)]) == 1
    mine = s.log_run.read_log()
    assert {rec["stage"] for rec in mine} == {"guide"} and all(rec["session"] == s.id for rec in mine)
    guide_cost = sum(rec["cost_usd"] for rec in mine)
    assert s.spent() == pytest.approx(guide_cost) and s.spent() - spent < scouting.spent()
    assert chat.pipe.status() == {"state": "idle"} and chat.pipe.busy() is False  # not a run the page follows


def test_a_lone_scout_in_the_typed_chat_is_never_answered_with_done(chat):
    s = chat.session()
    # the model scouts and then answers the listener: its own words are the reply
    assert s.say("maybe Roman sea concrete") == "Tell me more about the pouring."
    result = json.loads(guide_calls(chat.resp)[-1]["messages"][-1]["content"][0]["content"])
    assert result["scouting"] is True and "Carry on with the conversation" in result["now"]
    # it says a line with the call and nothing after: the line stands alone
    chat.resp.after = ""
    assert s.say("perhaps Octopus minds") == "Harbours that set under water, then."
    # it says nothing at all in the turn: a line that keeps the listener talking, and no word of the scout
    other = chat.session()
    reply = other.say("maybe The Antikythera mechanism")
    assert reply == "Go on, I'm listening." and "Done" not in reply
    join_scouts(s), join_scouts(other)
    tools = [json.loads(x) for x in other.transcript.read_text().splitlines() if '"who": "tool"' in x]
    assert tools[-1]["name"] == "scout_topic" and tools[-1]["result"]["scouting"] is True


# --- start_chapter --------------------------------------------------------------------------------

def start(s):
    return s.tool("start_chapter", {"topic": "Why Roman concrete lasted", "listener_said": "I pour concrete for a living",
                                    "why": "It is my trade.", "notes": ""})


def test_start_chapter_gives_no_time_until_a_fast_run_has_been_measured(chat):
    s = chat.session()
    out = start(s)
    assert set(out) == {"started", "while_waiting"} and out["started"] is True
    wait = out["while_waiting"]
    assert wait == common.load_prompt("guide_wait_fast").strip() and "{{" not in wait and "—" not in wait
    for rule in ("a sentence at most", "React to what they said before you ask", "never ask two questions in a row",
                 "add_note, with its kind", "Give a time only when a tool result has given you one",
                 "do not say twenty minutes"):
        assert rule in wait, rule
    assert chat.started == [["new", s.run, "--unattended", "--mode", "fast"]]
    assert base.is_fast(Run.open(s.run, runs_dir=chat.runs))


def test_start_chapter_gives_the_time_to_first_audio_earlier_runs_took(chat):
    base.write_json(T.path(chat.cfg), {"medians": {"first_audio": 185.0}, "runs": []})
    assert T.estimate(chat.cfg) == 185.0
    s = chat.session()
    out = start(s)
    assert out["first_audio_minutes"] == 4 and "minutes" not in out  # rounded up, and never the overnight 25
    assert out["when"] == "Going by earlier chapters, it can start playing in about 4 minutes."
    assert out["while_waiting"]


def test_a_silent_guide_after_a_fast_start_states_no_time(chat):
    s = chat.session()
    s.messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": json.dumps(
        {"started": True, "first_audio_minutes": 4, "while_waiting": "..."})}]})
    line = s._quiet_line()
    assert line == "It's under way. The page will tell you when it can start playing."
    assert not any(ch.isdigit() for ch in line)
    # the overnight result still gets its own line, with its minutes
    s.messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu2", "content": json.dumps(
        {"started": True, "minutes": 25})}]})
    assert s._quiet_line() == "It's under way. It takes about 25 minutes; I'll let you know when it's ready."


def test_without_the_hooks_start_chapter_answers_as_it_did(chat, cfg):
    s = guide.Session(cfg, functools.partial(chat.pipe.start_seed, mode="fast"), chat.pipe.status, chat.runs)
    assert start(s) == {"started": True, "minutes": round(sum(cfg["status_minutes"].values()))}


# --- add_note -------------------------------------------------------------------------------------

def test_a_note_in_a_fast_run_is_saved_with_its_kind_and_the_section_it_was_said_in(chat):
    s = chat.session()
    start(s)
    root = chat.runs / s.run
    assert s.tool("add_note", {"note": "My grandfather  was a mason\nin Naples.", "kind": "connection"}) == \
        {"added": True, "kind": "connection", "effect": "It can shape the parts of the chapter not yet written."}
    player = lambda **report: base.write_json(root / "fast" / "player.json", {"at": time.time(), **report})
    player(state="playing", section=2, piece=0, t=4.0)
    s.tool("add_note", {"note": "The barge going down stays with me.", "kind": "landed"})
    player(state="waiting", section=3, piece=0, t=0, waiting_for=3)
    s.tool("add_note", {"note": "Tell me more about the ash.", "kind": "wants_more"})
    player(state="paused", section=3, piece=1, t=2.0)
    s.tool("add_note", {"note": "Did they know why it worked?", "kind": "question"})
    player(state="done", section=4, piece=0, t=0)
    s.tool("add_note", {"note": "I liked that.", "kind": "something else"})
    s.tool("add_note", {"note": "No kind given."})
    saved = notes.load(root)
    assert [(n["section"], n["kind"], n["text"]) for n in saved] == [
        (0, "connection", "My grandfather was a mason in Naples."),
        (2, "landed", "The barge going down stays with me."),
        (3, "wants more", "Tell me more about the ash."),
        (3, "question", "Did they know why it worked?"),
        (0, "connection", "I liked that."),          # a kind that is not one of the five: the plain case
        (0, "connection", "No kind given.")]
    assert all(n["time"] for n in saved)
    assert not (root / "00_source" / "notes.md").exists()
    assert s.tool("add_note", {"note": "  ", "kind": "landed"}) == {"added": False, "reason": "The note was empty."}

    # once every section has its final text, the guide is told a note can no longer change the chapter
    base.write_json(root / "status.json", {"run": s.run, "state": "making", "mode": "fast", "sections": [
        {"n": 1, "title": "One", "state": "played", "text": "06_segments/seg01.md"},
        {"n": 2, "title": "Two", "state": "playing", "text": "06_segments/seg02.md"}]})
    assert "already written" in s.add_note("One more.", "landed")["effect"]


def test_a_note_in_an_overnight_run_goes_where_it_always_did(chat):
    """The hooks leave a run that is not fast alone: 00_source/notes.md, read with the seed."""
    s = chat.session()
    name = chat.pipe.start_seed("Why Roman concrete lasted")  # the text box's chapter, made overnight
    assert chat.pipe.status()["state"] == "making"
    out = s.tool("add_note", {"note": "Mention the harbor at Caesarea.", "kind": "wants more"})
    assert out == {"added": True, "effect": "The stages still to come will take it into account."}
    r = Run.open(name, runs_dir=chat.runs)
    assert "- Mention the harbor at Caesarea." in r.path("notes").read_text()
    assert notes.load(r.root) == [] and "Caesarea" in common.seed_inputs(r, chat.cfg)["seed"]


# --- get_status -----------------------------------------------------------------------------------

FAST_STATUS = {"run": "lesson", "state": "making", "label": "Writing the next part", "minutes_left": None,
               "title": "The Sea Finishes the Wall", "mode": "fast", "first_audio": True, "waiting_for": 3,
               "eta_seconds": 40, "check_in": "check_in", "sections": [
                   {"n": 1, "title": "The barge", "state": "played", "pieces": [], "text": "06_segments/seg01.md"},
                   {"n": 2, "title": "The ash", "state": "playing", "pieces": [], "text": "06_segments/seg02.md"},
                   {"n": 3, "title": "The clasts", "state": "being_written", "pieces": [], "text": None},
                   {"n": 4, "title": "What lasts", "state": "being_written", "pieces": [], "text": None}]}


def test_get_status_reports_a_fast_runs_sections_and_what_the_listener_waits_for():
    assert guide.plain_status(FAST_STATUS) == {
        "state": "being made", "stage": "Writing the next part", "title": "The Sea Finishes the Wall",
        "can_start_listening": True,
        "sections": [{"title": "The barge", "state": "played"}, {"title": "The ash", "state": "playing"},
                     {"title": "The clasts", "state": "being written"}, {"title": "What lasts", "state": "being written"}],
        "waiting_for": "The clasts", "expected_in_seconds": 40}
    assert guide.plain_status(FAST_STATUS) == {**guide.plain_status({k: v for k, v in FAST_STATUS.items() if k != "mode"}),
                                               **fast_status.plain(FAST_STATUS)}


def test_get_status_gives_the_time_to_first_audio_only_when_the_run_has_one():
    """The run counts down to its first audio from the medians of earlier runs (status.json's
    minutes_left); with none measured there is no number, and the guide is given none."""
    early = {**FAST_STATUS, "first_audio": False, "waiting_for": None, "eta_seconds": None, "check_in": None,
             "label": "Looking into it", "title": None, "sections": []}
    assert guide.plain_status(early) == {"state": "being made", "stage": "Looking into it", "can_start_listening": False}
    assert guide.plain_status({**early, "minutes_left": 3}) == {
        "state": "being made", "stage": "Looking into it", "can_start_listening": False, "starts_playing_in_minutes": 3}
    # an overnight run's status is worded as it was
    assert guide.plain_status({"state": "making", "label": "Researching", "minutes_left": 12}) == \
        {"state": "being made", "stage": "Researching", "minutes_left": 12}


def test_the_guide_reads_a_fast_runs_status_through_the_tool(chat):
    s = chat.session()
    start(s)
    base.write_json(chat.runs / s.run / "status.json", {**FAST_STATUS, "run": s.run, "pid": None})
    out = s.tool("get_status", {})
    assert out["can_start_listening"] is True and out["waiting_for"] == "The clasts"
    assert [x["state"] for x in out["sections"]] == ["played", "playing", "being written", "being written"]
    assert "minutes_left" not in out and "starts_playing_in_minutes" not in out  # never a guessed time


# --- listener notes reach the writers (acceptance check 5b, the guide's half) -----------------------

def writing(chain):
    return [(k, kw) for k, kw in chain.calls if k[0] in ("opening", "opening_edit", "draft", "lock")]


def run_session(w):
    """The guide's conversation beside a run that is being made: it did not start this run (the
    status says one is being made), as when the listener comes back to the page."""
    status = lambda: {"run": w.r.name, "state": "making", "mode": "fast"}
    hooks = guide_hooks.Hooks(w.cfg, w.r.root.parent, status)
    return guide.Session(w.cfg, lambda *a: w.r.name, status, w.r.root.parent, rng=random.Random(1), fast=hooks)


def test_a_note_through_the_guide_mid_run_reaches_every_later_section_and_none_already_locked(tmp_path, cfg, monkeypatch):
    w = world(tmp_path, cfg, monkeypatch)
    base.mark_fast(w.r)  # as the server does when the guide starts the chapter
    s, r, said, before = run_session(w), w.r, "The barge going down into the water stays with me.", {}

    def at_lock_2(kwargs):  # the lock of section 2 has been asked for: its request is already written
        before[1] = r.segment_path(1).read_text(encoding="utf-8")
        base.write_json(base.path(r, "player"), {"at": time.time(), "state": "playing", "section": 1, "piece": 0, "t": 2.0})
        assert s.tool("add_note", {"note": said, "kind": "landed"})["added"] is True

    w.chain.on("lock", 2, at_lock_2)
    fast_run.run(r, w.cfg, player=SimPlayer(), episode=False)

    calls = writing(w.chain)
    cut = next(i for i, (k, _) in enumerate(calls) if k == ("lock", 2))
    earlier, later = calls[:cut + 1], calls[cut + 1:]
    assert [k for k, _ in later] == [("draft", 4), ("lock", 3), ("lock", 4)]
    assert all(all_text(kw).count(said) == 1 for _, kw in later)  # each sees it, once
    assert not any(said in all_text(kw) for _, kw in earlier)
    assert r.segment_path(1).read_text(encoding="utf-8") == before[1]  # what was locked when it was said is as it was
    assert [(n["section"], n["kind"], n["text"]) for n in notes.load(r.root)] == [(1, "landed", said)]
    events = [e for e in base.read_timeline(r) if e["event"] == T.LISTENER_NOTE]
    assert [(e["kind"], e["section"], e["text"]) for e in events] == [("landed", 1, said)]
    assert not r.path("notes").exists()


def test_a_note_said_before_the_outline_reaches_each_stage_still_to_run_exactly_once(tmp_path, cfg, monkeypatch):
    """Where a fast run's notes go (faststart/guide_hooks.py): the outline reads the seed with
    00_source/notes.md and listener_notes.md as well, so a note written to both would reach it
    twice. It goes to listener_notes.md alone."""
    w = world(tmp_path, cfg, monkeypatch)
    base.mark_fast(w.r)
    s, said = run_session(w), "My grandfather was a mason in Naples."
    w.chain.on("scout", None, lambda kw: s.tool("add_note", {"note": said, "kind": "connection"}) and None)
    fast_run.run(w.r, w.cfg, player=SimPlayer(), episode=False)

    assert said not in all_text(w.chain.of("scout")[0])  # its prompt was written before the note was said
    planned = w.chain.of("outline") + w.chain.of("outline_edit")
    assert len(planned) == 2 and [all_text(kw).count(said) for kw in planned] == [1, 1]
    wrote = writing(w.chain)
    assert len(wrote) >= 8 and all(all_text(kw).count(said) == 1 for _, kw in wrote)
    assert [(n["section"], n["kind"]) for n in notes.load(w.r.root)] == [(0, "connection")]


# --- review fixes (milestone 5) ------------------------------------------------------------------

def overnight_minutes(cfg):
    return str(round(sum(cfg["status_minutes"].values())))


def test_a_fast_conversation_is_never_handed_the_overnight_time(chat, cfg):
    """The guide prompt's {{minutes}} ("about {{minutes}} minutes, or what start_chapter says") for a
    conversation that makes fast chapters: the time to the first audio when earlier runs measured it,
    else no figure; never status_minutes. The voice agent gets the same value."""
    from stages import voice
    s = chat.session()
    assert s.values["minutes"] == guide_hooks.NO_FIGURE and not any(ch.isdigit() for ch in guide_hooks.NO_FIGURE)
    assert voice.dynamic_variables(s)["minutes"] == guide_hooks.NO_FIGURE
    assert f"about {overnight_minutes(cfg)} minutes" not in s.system and "give no time unless" in s.system
    base.write_json(T.path(chat.cfg), {"medians": {"first_audio": 185.0}, "runs": []})
    s = chat.session()
    assert s.values["minutes"] == "4"  # the time to first audio, not the overnight 27
    assert "about 4 minutes, or what start_chapter says" in s.system
    # without the hooks (cli.py chat, the text box's chapters) the value is what it was
    plain = guide.Session(cfg, lambda *a: "run", lambda: {"state": "idle"}, chat.runs, rng=random.Random(1))
    assert plain.values["minutes"] == overnight_minutes(cfg)
    # and the tool itself no longer promises twenty minutes
    (start_tool,) = [t for t in guide.TOOLS if t["name"] == "start_chapter"]
    assert "twenty" not in start_tool["description"] and "minutes" not in start_tool["description"]


def making_early(s, chat, **more):
    """A fast run this conversation started, its status written as the run writes it before first audio."""
    status = {**FAST_STATUS, "run": s.run, "pid": None, "first_audio": False, "waiting_for": None,
              "eta_seconds": None, "check_in": None, "label": "Looking into it", "title": None, "sections": [], **more}
    base.write_json(chat.runs / s.run / "status.json", status)
    return status


def test_a_conversation_opened_during_the_wait_has_the_wait_rules(chat):
    """A reload or a return before the first audio: the new conversation is opened with the wait rules
    (chapter_now, and get_status), not only the conversation whose start_chapter returned them."""
    first = chat.session()
    start(first)
    making_early(first, chat)
    wait = common.load_prompt("guide_wait_fast").strip()
    s = chat.session()
    assert json.loads(s.values["chapter_now"])["while_waiting"] == wait
    assert s.tool("get_status", {})["while_waiting"] == wait
    # once it can play, the ordinary rules: the key is gone
    making_early(first, chat, first_audio=True)
    assert "while_waiting" not in s.tool("get_status", {})
    assert "while_waiting" not in json.loads(chat.session().values["chapter_now"])


def test_get_status_drops_a_countdown_the_run_can_no_longer_back(chat):
    """The run clamps its countdown to the first audio at 1 minute once the median has passed (and for
    a run continued after a stop): that 1 is no measurement, so the guide is given no figure."""
    base.write_json(T.path(chat.cfg), {"medians": {"first_audio": 185.0}, "runs": []})
    s = chat.session()
    start(s)
    making_early(s, chat, minutes_left=3)
    mode = chat.runs / s.run / base.FILES["mode"]
    base.write_json(mode, {**base.read_json(mode), "seed_set": time.time() - 30})
    assert s.tool("get_status", {})["starts_playing_in_minutes"] == 3
    making_early(s, chat, minutes_left=1)
    base.write_json(mode, {**base.read_json(mode), "seed_set": time.time() - 600})  # past the median: the clamp
    out = s.tool("get_status", {})
    assert "starts_playing_in_minutes" not in out and "minutes_left" not in out
    assert not any(ch.isdigit() for ch in json.dumps({k: v for k, v in out.items() if k != "while_waiting"}))
    # no earlier run measured: never a figure
    T.path(chat.cfg).unlink()
    base.write_json(mode, {**base.read_json(mode), "seed_set": time.time() - 30})
    making_early(s, chat, minutes_left=3)
    assert "starts_playing_in_minutes" not in s.tool("get_status", {})


def test_a_note_from_a_conversation_without_the_hooks_reaches_a_fast_run(chat, cfg):
    """cli.py chat builds its Session without the fast hooks but follows the newest run, which can be a
    fast one started from the page: its notes are routed by the run, to listener_notes.md."""
    s = chat.session()
    start(s)
    root = chat.runs / s.run
    hookless = guide.Session(chat.cfg, chat.pipe.start_seed, chat.pipe.status, chat.runs, rng=random.Random(1))
    assert hookless.fast is None
    out = hookless.tool("add_note", {"note": "Mention the harbour at Caesarea.", "kind": "wants more"})
    assert out == {"added": True, "kind": "wants more", "effect": "It can shape the parts of the chapter not yet written."}
    assert [(n["kind"], n["text"]) for n in notes.load(root)] == [("wants more", "Mention the harbour at Caesarea.")]
    assert not (root / "00_source" / "notes.md").exists()


def test_voice_compare_never_waits_for_a_reply_to_a_tool_the_agent_does_not_wait_for(monkeypatch, cfg, tmp_path):
    """voice-compare's turn ends when every tool call has had a reply after it; scout_topic
    (expects_response false) gets none, so it must not hold the turn open until the timeout."""
    from elevenlabs.conversational_ai import conversation as el

    from stages import voice

    class Tools:
        def __init__(self):
            self.handlers = {}

        def register(self, name, handler):
            self.handlers[name] = handler

    class Conversation:
        def __init__(self, client, agent_id, client_tools, callback_agent_response, **kw):
            self.tools, self.reply = client_tools, callback_agent_response
            self._conversation_id = None

        def start_session(self):
            self._conversation_id = "conv1"

        def send_user_message(self, text):  # Claude's usual order: the words, then the silent tool, nothing after
            self.reply("Harbours that set under water, then.")
            if "concrete" in text:
                self.tools.handlers["scout_topic"]({"topic": "Roman sea concrete"})

        def end_session(self):
            pass

        def wait_for_session_end(self):
            return "conv1"

    monkeypatch.setattr(el, "ClientTools", Tools)
    monkeypatch.setattr(el, "Conversation", Conversation)
    monkeypatch.setattr(el, "ConversationInitiationData", lambda **kw: kw)
    monkeypatch.setattr(voice, "sync_agent", lambda cfg, client: "agent1")
    monkeypatch.setattr(voice, "QUIET_SECONDS", 0.05)
    monkeypatch.setattr(voice, "TURN_SECONDS", 1.5)
    session = NS(values={k: "x" for k in voice.PLACEHOLDERS})
    out = voice._voice_replies(cfg, session, ["I pour concrete for a living"], client=None)
    assert out[1]["tools"] == ["scout_topic"] and out[1]["reply"] == "Harbours that set under water, then."
    assert out[-1] == {"conversation_id": "conv1"}
