"""The earpiece (voice/gpt_live/director.py, POST /api/live/direct; Ian, 2026-11-19: "a listening agent on top of
the gptlive model", then "an earpiece ... with a plan that the earpiece agent can use to check off items"),
offline: the conversation it reads (from the transcript, the page's notes marked, its own whispers listed apart
and its own rows left out), the page's recent lines merged in, the plan and read kept in its transcript rows and
given back on the next look, its looks shaped (a whisper only with changed, the note cleaned and capped at 25
words), every failure no whisper with an error, the caps, its cost in the conversations' log (counted toward the
conversation's cap), and that its rows are never read back by earlier_text, memory or explained(). Also the
notes it is wrapped in, the guide's one clause about it, and its config. No model is reached: llm's client is a
fake that answers with the JSON a test gives it. The persona check's mirror is in test_sim.py."""

import json
import re
import time

import pytest
from fastapi.testclient import TestClient

import server
from fakes import FakeAnthropic, Responder
from stages import guide, llm, memory
from voice.gpt_live import director as D
from voice.gpt_live import routes as live_routes
from voice.gpt_live import session as S


def answering(monkeypatch, *answers, delay=0.0):
    """llm's client answers each director call with the next of `answers` (a dict as JSON, or raw text)."""
    calls, it = [], iter(answers)

    def respond(kwargs):
        calls.append(kwargs)
        if delay:
            time.sleep(delay)
        a = next(it)
        return Responder.msg(kwargs, a if isinstance(a, str) else json.dumps(a))
    monkeypatch.setattr(llm, "_client", FakeAnthropic(respond))
    return calls


@pytest.fixture
def app(cfg, tmp_path, monkeypatch):
    cfg["listener_dir"] = str(tmp_path / "listener")
    cfg["live"]["provider"] = "gpt_live"
    cfg["live"]["director"] = {**cfg["live"]["director"], "enabled": True}
    monkeypatch.setattr(llm, "_client", FakeAnthropic(Responder()))
    monkeypatch.setattr(server.memory, "after_session", lambda *a, **k: False)
    monkeypatch.setattr(S, "create_webrtc", lambda cfg, session, sdp, client=None: {"id": "live_1", "sdp": "ANSWER"})
    runs = tmp_path / "runs"
    runs.mkdir()
    pipe = server.Pipeline(runs_dir=runs)
    return TestClient(server.create_app(pipe, cfg, remember_on_start=False))


def started(client):
    return client.post("/api/live/start", json={"sdp": "OFFER"}).json()["session"]


def transcript(cfg, sid):
    p = guide.listener_dir(cfg) / "sessions" / f"{sid}.jsonl"
    return p, [json.loads(x) for x in p.read_text().splitlines()]


def lines(client, sid, *said):
    for who, text in said:
        client.post("/api/live/line", json={"session": sid, "who": who, "text": text})


# --- what it reads -----------------------------------------------------------------------------------

def whisper(note, key="direct"):
    return S.notes()[key].replace("{note}", note)


def look_json(note=None, *, plan=None, read="r", changed=None, kind=None, about=None, nxt=None):
    return {"read": read, "plan": plan if plan is not None else [{"item": "Meet them a little", "state": "open"}],
            "next": nxt, "changed": bool(note) if changed is None else changed, "kind": kind, "about": about,
            "note": note}


def test_it_reads_the_lines_the_notes_marked_and_the_tools_but_not_its_own_rows(tmp_path):
    p = tmp_path / "s.jsonl"
    rows = [{"t": "2026-11-19T19:52:18", "who": "session", "live_id": "x"},
            {"t": "2026-11-19T19:52:20", "who": "page", "text": "[Say hello.]"},
            {"t": "2026-11-19T19:52:25", "who": "guide", "text": "Ian. Hi!"},
            {"t": "2026-11-19T19:52:30", "who": "listener", "text": "Hey, how'd you get that name"},
            {"t": "2026-11-19T19:52:31", "who": "director", "plan": [], "note": "Tease him.", "changed": True},
            {"t": "2026-11-19T19:53:40", "who": "tool", "name": "start_chapter", "input": {}, "result": {}}]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    got = D.lines_from_transcript(p)
    assert [(x["secs"], x["who"]) for x in got] == [(2, "app"), (7, "guide"), (12, "listener"), (82, "tool")]
    text = D.transcript_text(got)
    assert "[0:02] app note to the guide: [Say hello.]" in text and "[1:22] app: (the guide called start_chapter)" in text
    assert "Tease him" not in text


def test_the_pages_recent_lines_not_yet_in_the_transcript_are_added_at_the_end():
    """The page's line posts are fire-and-forget: the request carries its last few lines, and any the transcript
    doesn't have yet (or has in another case) come at the end, once."""
    have = [{"secs": 1, "who": "guide", "text": "Hi there!"}, {"secs": 4, "who": "listener", "text": "Hey."}]
    recent = [{"who": "guide", "text": "hi there"}, {"who": "listener", "text": "Hey."},
              {"who": "guide", "text": "What's your evening like?"}, {"who": "page", "text": "[A note.]"},
              {"who": "event", "text": "ignored"}]
    got = D.merge_recent(have, recent, 9)
    assert [(x["who"], x["text"], x["secs"]) for x in got[2:]] == [("guide", "What's your evening like?", 9),
                                                                    ("app", "[A note.]", 9)]


def test_a_fresh_line_the_listener_said_before_too_is_still_added():
    """Review (2026-11-19): dedupe by text dropped a fresh "Yeah." to the pitch when the listener had said "Yeah." a
    minute before and its line post hadn't landed: the director saw the pitch as the last line. By order now."""
    have = [{"secs": 30, "who": "listener", "text": "Yeah."}, {"secs": 90, "who": "guide", "text": "Want that?"}]
    recent = [{"who": "listener", "text": "Yeah."}, {"who": "guide", "text": "Want that?"},
              {"who": "listener", "text": "Yeah."}]
    got = D.merge_recent(have, recent, 95)
    assert [(x["who"], x["text"]) for x in got] == [("listener", "Yeah."), ("guide", "Want that?"), ("listener", "Yeah.")]
    # lines only the server has (a tool row) between them still match
    have2 = [have[0], {"secs": 60, "who": "tool", "text": "get_status"}, have[1], {"secs": 96, "who": "listener", "text": "Yeah."}]
    assert D.merge_recent(have2, recent, 97) == have2


def test_the_look_has_the_state_its_plan_its_whispers_and_the_transcript():
    """Every placeholder of prompts/live_director_moment.md is supplied (load_prompt raises otherwise). Its own
    whispers are listed with their times and kind and left out of the transcript (the wrapper's words appear
    nowhere); the started note's clear shows as a cleared row."""
    lines = [{"secs": 5, "who": "guide", "text": "Ian. Hi!"},
             {"secs": 30, "who": "listener", "text": "I saw a peregrine on a lamp post today"},
             {"secs": 40, "who": "app", "text": whisper("His spark is the peregrine: pitch a chapter on it.")},
             {"secs": 50, "who": "tool", "text": "start_chapter"},
             {"secs": 52, "who": "app", "text": S.notes()["started_company"].replace("{topic}", "Peregrines")
                                                                       .replace("{when}", "4 minutes")}]
    plan = [{"item": "Meet them a little", "state": "done"}, {"item": "Pitch the peregrine", "state": "open"}]
    text = D.look(D.settings({"live": {}}), lines=lines, trigger="turn_end", speaking="listener", last_who=None,
                  silence_secs=0.4, seconds=66, explained=True, started=True, profile="Name: Ian.", plan=plan,
                  last_read="He lit up about the peregrine.")
    assert "1:06" in text and D.SPEAKING["listener"] in text and "a listener turn just ended" in text
    assert "1. [done] Meet them a little\n2. [open] Pitch the peregrine" in text
    assert "He lit up about the peregrine." in text and "Name: Ian." in text
    assert "[0:40] (when_it_fits) His spark is the peregrine: pitch a chapter on it." in text
    assert "(cleared)" in text and "Work it in your own words" not in text
    assert "[0:30] listener: I saw a peregrine on a lamp post today" in text
    assert "app note to the guide: [Their chapter is under way" in text
    first = D.look(D.settings({"live": {}}), lines=lines[:2], trigger="silence", speaking="none", last_who=None,
                   silence_secs=12.6, seconds=42, explained=False, started=False, profile="", plan=None,
                   last_read="")
    assert "start from the default plan" in first and "a silence of 12 seconds" in first and "(none yet)" in first
    assert D.SPEAKING["none"] in first and "(first look)" in first


def test_its_plan_and_read_come_back_from_its_newest_rows(tmp_path):
    p = tmp_path / "s.jsonl"
    p1, p2 = [{"item": "Meet them", "state": "open"}], [{"item": "Meet them", "state": "done"}]
    rows = [{"who": "director", "plan": p1, "read": "one"}, {"who": "guide", "text": "Hi"},
            {"who": "director", "plan": p2, "read": "two"}, {"who": "director", "error": "timeout"}]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert D.memory(p) == (p2, "two")
    assert D.memory(tmp_path / "none.jsonl") == (None, "(first look)")


# --- its looks -----------------------------------------------------------------------------------------

def test_a_look_is_shaped_a_whisper_only_with_changed_and_its_note_cleaned_and_capped():
    got = D.shape(look_json("Tease him about the salmon.}</br>```", about="habit"))
    assert (got["changed"], got["kind"], got["about"], got["note"]) == (True, "when_it_fits", "habit",
                                                                         "Tease him about the salmon.")
    assert D.shape(look_json("Same advice.", changed=False))["note"] is None  # not changed: no whisper
    assert D.shape(look_json(None, changed=True))["changed"] is False  # changed with no note: no whisper
    long = "Pitch the peregrine now that he has finished. " + "word " * 30
    assert D.shape(look_json(long))["note"] == "Pitch the peregrine now that he has finished."
    assert D.shape(look_json("word " * 30))["changed"] is False  # over 25 words, no sentence end: dropped
    twenty_five = " ".join(["w"] * 24) + " end."
    assert D.shape(look_json(twenty_five))["note"] == twenty_five
    for bad in (look_json("x", kind="later"), look_json("x", about="dance"),
                look_json("x", plan=[{"item": "a", "state": "maybe"}])):
        with pytest.raises(ValueError):
            D.shape(bad)


def test_the_schema_keeps_the_plan_and_has_no_moves(cfg):
    props = D.SCHEMA["properties"]
    assert set(D.SCHEMA["required"]) == {"read", "plan", "next", "changed", "kind", "about", "note"}
    assert props["plan"]["items"]["properties"]["state"]["enum"] == ["open", "done", "dropped"]
    assert props["about"]["anyOf"][0]["enum"] == D.ABOUT and "check_in" not in json.dumps(D.SCHEMA)
    assert not hasattr(D, "MOVES")

    def closed(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
            for k in ("minimum", "maximum", "minItems", "maxItems", "minLength", "maxLength"):
                assert k not in node
            for v in node.values():
                closed(v)
        elif isinstance(node, list):
            for v in node:
                closed(v)
    closed(D.SCHEMA)


def test_any_failure_is_no_whisper_with_the_error(cfg, monkeypatch, tmp_path):
    from stages.common import Run
    log = Run(name="s", root=tmp_path)
    answering(monkeypatch, "not json")
    got = D.decide(cfg, log, "moment")
    assert got["note"] is None and got["changed"] is False and "unreadable" in got["error"]

    def boom(kwargs):
        raise RuntimeError("down")
    monkeypatch.setattr(llm, "_client", FakeAnthropic(boom))
    got = D.decide(cfg, log, "moment")
    assert got["note"] is None and got["kind"] is None and "down" in got["error"]


def test_the_route_looks_records_the_plan_and_gives_it_back_on_the_next_look(app, cfg, monkeypatch):
    c = app
    sid = started(c)
    lines(c, sid, ("guide", "Ian. Hi!"), ("listener", "Making a salmon dinner for my wife and I"))
    plan = [{"item": "Meet them a little", "state": "done"}, {"item": "Say what you do", "state": "open"}]
    calls = answering(monkeypatch, look_json("The salmon thread has closed: offer what you do, bridged from it.",
                                             plan=plan, read="Dinner talk, warm.", about="offer", nxt="Say what you do"),
                      look_json(None, plan=plan, read="Still dinner."))
    req = {"session": sid, "id": 7, "trigger": "turn_end", "speaking": "guide", "silence_ms": 1200,
           "last_who": "listener", "recent": [{"who": "guide", "text": "Fancy!"}],
           "page": {"seconds": 31, "explained": False, "started": False, "turns": 1}}
    got = c.post("/api/live/direct", json=req).json()
    assert got["id"] == 7 and got["changed"] is True and got["kind"] == "when_it_fits" and got["about"] == "offer"
    assert got["note"] == "The salmon thread has closed: offer what you do, bridged from it."
    assert got["plan"] == plan and got["next"] == "Say what you do" and got["read"] == "Dinner talk, warm."
    sent = calls[0]
    assert sent["model"] == "claude-sonnet-5-5" and sent["output_config"]["effort"] == "low"
    assert sent["output_config"]["format"]["schema"] == D.SCHEMA
    user = sent["messages"][0]["content"]
    assert "listener: Making a salmon dinner" in user and "a listener turn just ended" in user
    assert D.SPEAKING["guide"] in user and "0:31" in user and "start from the default plan" in user
    assert user.index("Making a salmon") < user.index("guide: Fancy!")  # the page's line not yet posted, at the end
    # the page delivers it; the next look has its plan, its read and the whisper (not in the transcript)
    lines(c, sid, ("page", whisper(got["note"])))
    got2 = c.post("/api/live/direct", json={**req, "id": 8, "trigger": "cadence", "speaking": "none"}).json()
    assert got2["changed"] is False and got2["note"] is None and got2["kind"] is None
    user2 = calls[1]["messages"][0]["content"]
    assert "1. [done] Meet them a little\n2. [open] Say what you do" in user2 and "Dinner talk, warm." in user2
    assert "(when_it_fits) The salmon thread has closed" in user2 and "Earpiece note from your producer" not in user2
    assert "the regular look (every 15 seconds)" in user2
    _, rows = transcript(cfg, sid)
    d = [r for r in rows if r["who"] == "director"]
    assert [(r["id"], r["trigger"], r["speaking"], r["changed"]) for r in d] == [(7, "turn_end", "guide", True),
                                                                                (8, "cadence", "none", False)]
    assert d[0]["plan"] == plan and d[0]["note"].startswith("The salmon") and all("text" not in r for r in d)
    log = [json.loads(x) for x in (guide.listener_dir(cfg) / "sessions" / "log.jsonl").read_text().splitlines()]
    mine = [r for r in log if r.get("stage") == "director"]
    assert len(mine) == 2 and mine[-1]["session"] == sid and mine[-1]["cost_usd"] > 0


def test_its_rows_are_never_read_back_by_earlier_text_memory_or_explained(app, cfg, monkeypatch):
    c = app
    sid = started(c)
    lines(c, sid, ("guide", "Hey!"), ("listener", "Hi."))
    answering(monkeypatch, look_json("Offer your audio chapters, bridged from the hello.", about="offer",
                                     read="They know nothing of audio chapters yet."))
    c.post("/api/live/direct", json={"session": sid, "id": 1})
    p, rows = transcript(cfg, sid)
    assert any(r["who"] == "director" and "audio chapters" in r["note"] for r in rows)
    assert "audio chapters" not in guide.earlier_text(p)
    assert "audio chapters" not in memory.transcript_text(p)
    assert not S.explained(p)


def test_a_returning_listener_already_knows_what_it_does(app, cfg, monkeypatch):
    """Review (2026-11-19): a returning listener's guide has offer_known.md ("Never offer to explain it again") and
    never says "audio chapters", so the director was told it hadn't said what it does. Known counts as explained,
    and an offer whispered for them comes back as no whisper (the plan still comes back)."""
    gd = guide.listener_dir(cfg)
    gd.mkdir(parents=True, exist_ok=True)
    (gd / "guide.md").write_text("Your name is Ailsa. You have met Ian before.")
    c = app
    sid = started(c)
    lines(c, sid, ("guide", "Ian! Back again."), ("listener", "Hey, yeah."))
    calls = answering(monkeypatch, look_json("Offer to tell him what you do.", about="offer"))
    got = c.post("/api/live/direct", json={"session": sid, "id": 1, "page": {"explained": False}}).json()
    assert "said what it does: yes." in calls[0]["messages"][0]["content"]
    assert got["changed"] is False and got["note"] is None and got["plan"]


def test_off_capped_and_ended_conversations(app, cfg, monkeypatch):
    c = app
    sid = started(c)
    calls = answering(monkeypatch, *([look_json(None)] * 5))
    cfg["live"]["director"]["enabled"] = False
    assert c.post("/api/live/direct", json={"session": sid, "id": 1}).json() == {
        "id": 1, "changed": False, "kind": None, "about": None, "note": None, "error": "off"}
    cfg["live"]["director"]["enabled"] = True
    cfg["live"]["director"]["max_calls"] = 1
    assert "error" not in c.post("/api/live/direct", json={"session": sid, "id": 2}).json()
    assert c.post("/api/live/direct", json={"session": sid, "id": 3}).json()["error"] == "cap"
    assert len(calls) == 1
    cfg["live"]["director"]["max_calls"] = 150
    cfg["guide"]["max_session_usd"] = 0.000001  # the conversation's whole cap: GPT-Live and the director together
    assert c.post("/api/live/direct", json={"session": sid, "id": 4}).json()["error"] == "cap"
    assert c.post("/api/live/direct", json={"session": "nope", "id": 5}).status_code == 409


def test_a_slow_look_is_no_whisper_on_time(app, cfg, monkeypatch):
    c = app
    sid = started(c)
    cfg["live"]["director"]["timeout_ms"] = 200
    answering(monkeypatch, look_json("Pitch it, from the salmon."), delay=1.0)
    t = time.monotonic()
    got = c.post("/api/live/direct", json={"session": sid, "id": 1}).json()
    assert got["note"] is None and got["changed"] is False and got["error"] == "timeout"
    assert time.monotonic() - t < 0.9


def test_an_unknown_trigger_or_speaking_is_read_as_the_plain_case(app, cfg, monkeypatch):
    c = app
    sid = started(c)
    lines(c, sid, ("guide", "Hi!"))
    calls = answering(monkeypatch, look_json(None))
    c.post("/api/live/direct", json={"session": sid, "id": 1, "trigger": "line", "speaking": "both"})
    user = calls[0]["messages"][0]["content"]
    assert "the regular look" in user and D.SPEAKING["none"] in user


# --- its notes, the guide's clause, config ---------------------------------------------------------------

def test_the_whisper_wrappers_and_the_clear():
    """DESIGN section 1: `direct` has neither "now" nor "say" of its own (they made notes speak within 1 to 2 s), and
    gives leave to skip; each wrapper replaces the last whisper; the started notes end the last one."""
    N = S.notes()
    own = N["direct"].replace("{note}", "")
    assert not re.search(r"\b(now|say)\b", own, re.I) and "If it doesn't come, let it go." in own
    assert N["direct"].startswith("[Earpiece note from your producer, for you only: {note} ")
    assert N["direct_now"] == ("[Earpiece, this one matters: {note} At your next turn, never over them. This replaces "
                               "any earlier earpiece note.]")
    assert "start_chapter" in N["direct_start"] and "If they have clearly said yes" in N["direct_start"]
    for k in ("direct", "direct_now", "direct_start"):
        assert N[k].count("{") == 1 and "This replaces any earlier earpiece note." in N[k]
        assert D.whisper_in(N[k].replace("{note}", "Pitch the falcon.")) == (
            "when_it_fits" if k == "direct" else "now", "Pitch the falcon.")
    assert D.CLEARED in N["started_step_back"] and D.CLEARED in N["started_company"]
    assert D.whisper_in("[Say hello.]") is None


def test_the_guide_keeps_its_style_and_gets_one_clause_about_the_earpiece():
    raw = (S.INSTRUCTIONS_DIR / "10_talk.md").read_text()
    notes, body = raw.split("\n---\n", 1)
    assert "2026-11-19" in notes and "earpiece" in notes
    clause = [l for l in body.splitlines() if "earpiece" in l.lower()]
    assert len(clause) == 1 and "producer" in clause[0] and "never mention it" in clause[0]
    assert "Mix your moves" in body and "One question per turn" in body  # the style rules stay


def test_the_page_gets_the_earpieces_settings(cfg):
    cfg["live"]["director"] = {**cfg["live"]["director"], "enabled": True}
    got = live_routes.page_settings(cfg)["director"]
    assert got["enabled"] is True and got["every_secs"] == 15 and got["company_every_secs"] == 30
    assert got["every_words"] == 12 and got["every_lines"] == 4 and got["silence_secs"] == [12]
    assert got["min_gap_secs"] == 20 and got["fallback"]["pitch_nudge_secs"] == [150, 240]
    assert "direct_now" in live_routes.page_settings(cfg)["notes"]


def test_config_turns_it_on_with_sonnet_at_low_and_drops_the_lull_gates():
    from stages.common import load_config
    c = load_config()
    DC = c["live"]["director"]
    assert DC["enabled"] is True and DC["timeout_ms"] == 6000
    for gone in ("min_quiet_ms", "max_age_ms", "deliver_quiet_ms", "deliver_after_guide_ms"):
        assert gone not in DC
    assert {"every_secs", "every_words", "every_lines", "company_every_secs", "silence_secs", "min_gap_secs",
            "max_calls", "max_usd"} <= set(DC)
    assert c["stages"]["director"]["model"] == "claude-sonnet-5-5" and c["stages"]["director"]["effort"] == "low"


def test_the_prompt_keeps_the_judges_rules_its_arc_and_names_only_known_abouts():
    """The blind judge's three required changes, the plan's order (what it does before the spark, as the guide's own
    arc: with the spark first the offer was never `next`), "start" and "quiet" glossed (the page picks direct_start
    on about "start"), and no old moves or clock wording (review, 2026-11-20: nothing pinned the prompt)."""
    from stages.common import ROOT
    raw = (ROOT / "prompts" / "live_director.md").read_text()
    head, body = raw.split("\n---\n", 1)
    assert "2026-11-19" in head and "2026-11-20" in head
    flat = re.sub(r"\s+", " ", body).lower()
    for rule in ("words of assent", "Finish saying what you do", "Re-send, once", "after their answer",
                 "Never whisper when the last line is a filler", "A start look", 'Never the words "now" or "say"'):
        assert rule.lower() in flat, rule
    plan = re.findall(r"^\d\. (.+)$", body, re.M)
    assert plan[:4] == ["Meet them a little", "Say what you do, bridged from them", "Find what lights them up",
                        "Pitch that"]
    para = body[body.index("about names what the whisper is for"):body.index("changed is true")]
    named = set(re.findall(r'"([a-z_]+)"', para)) - {"now", "when_it_fits"}  # the kinds its glosses mention
    assert named == set(D.ABOUT), named
    assert re.search(r'"start" \(a clear yes the guide moved past', para) and re.search(r'"quiet" \(quiet they asked', para)
    for gone in ("draw_out", "check_in", "step_back\"", "offer clock", "seconds in"):
        assert gone not in body, gone
