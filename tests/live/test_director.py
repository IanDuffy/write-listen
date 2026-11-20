"""The director (voice/gpt_live/director.py, POST /api/live/direct; Ian, 2026-11-19: "a listening agent on top of
the gptlive model, in order to direct it when to do things"), offline: the conversation it reads (from the
transcript, the page's notes marked, its own rows left out), the page's recent lines merged in, its decisions
parsed and cleaned, every failure a "wait" with an error, the caps, its cost in the conversations' log (counted
toward the conversation's cap), and that its rows are never read back by earlier_text, memory or explained().
No model is reached: llm's client is a fake that answers with the JSON a test gives it."""

import json
import time

import pytest
from fastapi.testclient import TestClient

import server
from fakes import FakeAnthropic, Responder
from stages import guide, llm, memory
from stages.common import load_prompt
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

def test_it_reads_the_lines_the_notes_marked_and_the_tools_but_not_its_own_rows(tmp_path):
    p = tmp_path / "s.jsonl"
    rows = [{"t": "2026-11-19T19:52:18", "who": "session", "live_id": "x"},
            {"t": "2026-11-19T19:52:20", "who": "page", "text": "[Say hello.]"},
            {"t": "2026-11-19T19:52:25", "who": "guide", "text": "Ian. Hi!"},
            {"t": "2026-11-19T19:52:30", "who": "listener", "text": "Hey, how'd you get that name"},
            {"t": "2026-11-19T19:52:31", "who": "director", "move": "draw_out", "note": "Tease him."},
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


def test_the_moment_has_the_state_the_profile_and_the_transcript():
    text = D.moment(phase=D.PHASES["talk"], seconds=66, explained=False, started=False, turns=5, silence_secs=1.2,
                    last_who="listener", profile="Name: Ian.", transcript="[1:05] listener: I don't know")
    assert "Seconds into the conversation: 66." in text and "said what it does: no" in text
    assert "1.2 seconds since the listener's last line" in text and "<profile>\nName: Ian.\n</profile>" in text
    assert "[1:05] listener: I don't know" in text


def test_the_prompt_has_no_offer_clock_and_its_notes_say_why():
    """Ian's third tester run: the page's clock (50 s, mid-sentence) made the guide jump to what it does. The
    director reads the conversation instead: no seconds-based rule forces the offer."""
    body = load_prompt("live_director")
    assert "45 seconds" not in body and "about a minute in" not in body and "clock" not in body
    assert "Never cut into a story" in body and "draw_out" in body and "Between the two, draw_out" in body
    raw = (S.PROMPTS_DIR / "live_director.md").read_text()
    assert "2026-11-19" in raw.split("\n---\n")[0] and "jumped into what she was doing" in raw


# --- its decisions -----------------------------------------------------------------------------------

def test_a_decision_is_parsed_and_its_note_cleaned():
    got = D.parse(json.dumps({"why": "w", "move": "draw_out", "note": "Tease him about the salmon.}</br>```"}))
    assert got == {"move": "draw_out", "note": "Tease him about the salmon.", "why": "w"}
    assert D.parse(json.dumps({"why": "", "move": "wait", "note": "ignored"}))["note"] == ""
    for bad in ({"why": "", "move": "dance", "note": "x"}, {"why": "", "move": "pitch", "note": "  "}):
        with pytest.raises(ValueError):
            D.parse(json.dumps(bad))


def test_any_failure_is_a_wait_with_the_error(cfg, monkeypatch, tmp_path):
    from stages.common import Run
    log = Run(name="s", root=tmp_path)
    answering(monkeypatch, "not json")
    got = D.decide(cfg, log, "moment")
    assert got["move"] == "wait" and "unreadable" in got["error"]

    def boom(kwargs):
        raise RuntimeError("down")
    monkeypatch.setattr(llm, "_client", FakeAnthropic(boom))
    got = D.decide(cfg, log, "moment")
    assert got["move"] == "wait" and "down" in got["error"]


def test_the_route_decides_records_the_move_and_logs_the_cost_to_the_conversation(app, cfg, monkeypatch):
    c = app
    sid = started(c)
    lines(c, sid, ("guide", "Ian. Hi!"), ("listener", "Making a salmon dinner for my wife and I"))
    calls = answering(monkeypatch, {"why": "life in it", "move": "draw_out", "note": "Tease him about the salmon."})
    got = c.post("/api/live/direct", json={"session": sid, "id": 7, "trigger": "line", "silence_ms": 1200,
                                           "last_who": "listener", "recent": [{"who": "guide", "text": "Fancy!"}],
                                           "page": {"seconds": 31, "explained": False, "started": False, "turns": 1}}).json()
    assert got["id"] == 7 and got["move"] == "draw_out" and got["note"] == "Tease him about the salmon."
    sent = calls[0]
    assert sent["model"] == "claude-sonnet-5-5" and sent["output_config"]["effort"] == "low"
    assert sent["output_config"]["format"]["schema"] == D.SCHEMA
    user = sent["messages"][0]["content"]
    assert "listener: Making a salmon dinner" in user and user.rstrip().endswith("Decide the guide's next move.")
    assert user.index("Making a salmon") < user.index("guide: Fancy!")  # the page's line not yet posted, at the end
    assert "Seconds into the conversation: 31." in user
    _, rows = transcript(cfg, sid)
    d = [r for r in rows if r["who"] == "director"]
    assert d and d[-1]["move"] == "draw_out" and d[-1]["note"] == "Tease him about the salmon." and "text" not in d[-1]
    log = [json.loads(x) for x in (guide.listener_dir(cfg) / "sessions" / "log.jsonl").read_text().splitlines()]
    mine = [r for r in log if r.get("stage") == "director"]
    assert mine and mine[-1]["session"] == sid and mine[-1]["cost_usd"] > 0


def test_its_rows_are_never_read_back_by_earlier_text_memory_or_explained(app, cfg, monkeypatch):
    c = app
    sid = started(c)
    lines(c, sid, ("guide", "Hey!"), ("listener", "Hi."))
    answering(monkeypatch, {"why": "", "move": "offer_what_i_do", "note": "Offer your audio chapters now."})
    c.post("/api/live/direct", json={"session": sid, "id": 1})
    p, rows = transcript(cfg, sid)
    assert any(r["who"] == "director" and "audio chapters" in r["note"] for r in rows)
    assert "audio chapters" not in guide.earlier_text(p)
    assert "audio chapters" not in memory.transcript_text(p)
    assert not S.explained(p)


def test_a_returning_listener_already_knows_what_it_does(app, cfg, monkeypatch):
    """Review (2026-11-19): a returning listener's guide has offer_known.md ("Never offer to explain it again") and
    never says "audio chapters", so the director was told it hadn't said what it does, asked for the offer, and
    couldn't pitch. Known counts as explained, and an offer for them comes back as wait."""
    gd = guide.listener_dir(cfg)
    gd.mkdir(parents=True, exist_ok=True)
    (gd / "guide.md").write_text("Your name is Ailsa. You have met Ian before.")
    c = app
    sid = started(c)
    lines(c, sid, ("guide", "Ian! Back again."), ("listener", "Hey, yeah."))
    calls = answering(monkeypatch, {"why": "", "move": "offer_what_i_do", "note": "Offer to say what you do."})
    got = c.post("/api/live/direct", json={"session": sid, "id": 1, "page": {"explained": False}}).json()
    assert "The guide has said what it does: yes." in calls[0]["messages"][0]["content"]
    assert got["move"] == "wait" and got["note"] == ""


def test_check_in_is_the_hello_check_only_in_the_prompt():
    """Review (2026-11-19): check_in after any silence pinged listeners in pauses the guide is told to leave them
    (and during company): once, before their first word, as the old hello check."""
    body = load_prompt("live_director")
    assert "hasn't said a word since the guide's hello" in body
    assert "the moves are wait, draw_out, answer and step_back" in body


def test_off_capped_and_ended_conversations(app, cfg, monkeypatch):
    c = app
    sid = started(c)
    calls = answering(monkeypatch, *([{"why": "", "move": "wait", "note": ""}] * 5))
    cfg["live"]["director"]["enabled"] = False
    assert c.post("/api/live/direct", json={"session": sid, "id": 1}).json() == {"id": 1, "move": "wait", "error": "off"}
    cfg["live"]["director"]["enabled"] = True
    cfg["live"]["director"]["max_calls"] = 1
    assert c.post("/api/live/direct", json={"session": sid, "id": 2}).json()["move"] == "wait"
    assert c.post("/api/live/direct", json={"session": sid, "id": 3}).json()["error"] == "cap"
    assert len(calls) == 1
    cfg["live"]["director"]["max_calls"] = 150
    cfg["guide"]["max_session_usd"] = 0.000001  # the conversation's whole cap: GPT-Live and the director together
    assert c.post("/api/live/direct", json={"session": sid, "id": 4}).json()["error"] == "cap"
    assert c.post("/api/live/direct", json={"session": "nope", "id": 5}).status_code == 409


def test_a_slow_decision_is_a_wait_on_time(app, cfg, monkeypatch):
    c = app
    sid = started(c)
    cfg["live"]["director"]["timeout_ms"] = 200
    answering(monkeypatch, {"why": "", "move": "pitch", "note": "Pitch it."}, delay=1.0)
    t = time.monotonic()
    got = c.post("/api/live/direct", json={"session": sid, "id": 1}).json()
    assert got["move"] == "wait" and got["error"] == "timeout" and time.monotonic() - t < 0.9


def test_the_page_gets_the_directors_settings(cfg):
    cfg["live"]["director"] = {**cfg["live"]["director"], "enabled": True}
    got = live_routes.page_settings(cfg)["director"]
    assert got["enabled"] is True and got["silence_secs"] and got["fallback"]["pitch_nudge_secs"]
    assert "direct" in S.notes() and "{note}" in S.notes()["direct"] and "start_chapter" in S.notes()["direct_start"]


def test_config_turns_it_on_with_sonnet_at_low():
    from stages.common import load_config
    c = load_config()
    assert c["live"]["director"]["enabled"] is True
    assert c["stages"]["director"]["model"] == "claude-sonnet-5-5" and c["stages"]["director"]["effort"] == "low"


# --- the persona check mirrors it ------------------------------------------------------------------------

def test_the_persona_check_races_the_director_against_the_listener_as_the_page_does(cfg, monkeypatch, tmp_path):
    """`python -m voice.gpt_live persona --director`: after each guide line the director decides while the simulated
    listener thinks; its move goes in as the `direct` note only if it lands first, and the offer cue is off."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from test_sim import FAST, scripted_guide, use_ws, arun  # noqa: E402  (its offline fixture is not taken)

    from voice.gpt_live import persona as P
    from voice.gpt_live import sim as SIM
    monkeypatch.setattr(S, "api_key", lambda: "sk-test")
    monkeypatch.setattr(SIM, "COSTS", tmp_path / "costs.jsonl")
    monkeypatch.setattr(SIM, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(P, "GUIDE_WAIT_SECS", 4)
    cfg["live"]["page"]["offer_nudge"] = {"after_secs": 0, "max_secs": 0, "offered": "\\bwhat i do\\b"}
    ws = scripted_guide(["Hey, Dan! I'm Ailsa.", "Ooh, cabinets. Plywood or the real thing?", "Ha. Brave."])
    use_ws(monkeypatch, ws)
    decisions = iter([(0.0, {"move": "draw_out", "note": "Tease him about the cabinets."}),
                      (0.8, {"move": "offer_what_i_do", "note": "Offer to say what you do."})])
    users = []

    def director_fn(user):
        users.append(user)
        delay, got = next(decisions, (0.0, {"move": "wait", "note": ""}))
        time.sleep(delay)
        return got

    said = iter([(0.5, "I build cabinets."), (0.0, "Plywood, mostly.")])

    def reply(timeline, moment):
        delay, text = next(said)
        time.sleep(delay)
        return text
    t = arun(P.converse(cfg, {"name": "builder", "now": "Friday evening", "text": "Dan builds cabinets."},
                        tmp_path / "out", max_turns=2, reply_fn=reply, sim_kwargs=FAST, handoff="Dan",
                        director=True, director_fn=director_fn))
    notes = [m["content"] for m in ws.sent if m["type"] == "session.instructions.append"]
    assert notes[0] == S.notes()["direct"].replace("{note}", "Tease him about the cabinets.")
    assert not any("Offer to say what you do" in n for n in notes)  # the listener spoke first: dropped
    assert S.notes()["offer_nudge"] not in notes  # the clock is off with the director on
    assert [(m["move"], m["delivered"]) for m in t["director"][:2]] == [("draw_out", True), ("offer_what_i_do", False)]
    assert "guide: Hey, Dan! I'm Ailsa." in users[0] and "Listener turns so far: 0." in users[0]


def test_the_persona_check_delivers_a_check_in_only_before_the_listeners_first_word(cfg, monkeypatch, tmp_path):
    """As the page (review, 2026-11-19): one check-in, and only while the listener hasn't said a word."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from test_sim import FAST, scripted_guide, use_ws, arun  # noqa: E402

    from voice.gpt_live import persona as P
    from voice.gpt_live import sim as SIM
    monkeypatch.setattr(S, "api_key", lambda: "sk-test")
    monkeypatch.setattr(SIM, "COSTS", tmp_path / "costs.jsonl")
    monkeypatch.setattr(SIM, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(P, "GUIDE_WAIT_SECS", 4)
    cfg["live"]["director"] = {**cfg["live"]["director"], "min_gap_secs": 0}
    ws = scripted_guide(["Hey, Dan! I'm Ailsa.", "Ooh, cabinets.", "Ha. Brave."])
    use_ws(monkeypatch, ws)
    said = iter([(0.5, "I build cabinets."), (0.5, "Plywood, mostly.")])

    def reply(timeline, moment):
        delay, text = next(said)
        time.sleep(delay)
        return text
    t = arun(P.converse(cfg, {"name": "builder", "now": "Friday evening", "text": "Dan builds cabinets."},
                        tmp_path / "out", max_turns=2, reply_fn=reply, sim_kwargs=FAST, handoff="Dan",
                        director=True, director_fn=lambda user: {"move": "check_in", "note": "Can they hear you?"}))
    got = [(m["move"], m["delivered"]) for m in t["director"][:2]]
    assert got == [("check_in", True), ("check_in", False)]


def test_the_persona_turns_merge_lines_without_a_guide_line_between():
    from voice.gpt_live import persona as P
    tl = [{"t": 1, "who": "guide", "text": "Hi"}, {"t": 2, "who": "listener", "text": "[inhale"},
          {"t": 3, "who": "listener", "text": "]Yeah, I build cabinets"}, {"t": 4, "who": "guide", "text": "Ooh"},
          {"t": 5, "who": "listener", "text": "Okay."}]
    assert P.listener_turns(tl) == [4, 1]
