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


def test_debrief_session_has_the_five_questions_and_their_real_moment(cfg, tmp_path, monkeypatch):
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
    assert [t["name"] for t in sc["delegation"]["responses"]["tools"]] == ["end_debrief"]
    order = ["Take me through what that was like, from the start.",
             "Was there a moment you were most into it?",
             'About 3 minutes in, you asked "What\'s a parameter?". How did that go?',
             "How would you describe it to a friend?",
             "If another one were waiting for you tomorrow morning, would you listen?",
             "Is there anything I didn't ask that I should have?"]
    at = [text.index(x) for x in order]
    assert at == sorted(at)
    assert "The List" in text and "Why nine levers matter." in text and "about 6 of its 10 minutes" in text
    assert "Was she right?" in text and "Thank you, Sam." in text
    assert "Generate <|SpawnThinking|> when:" in text.split("Delegation policy:")[1]


def test_debrief_without_moments_asks_whether_anything_made_them_want_to_stop(cfg, tmp_path, monkeypatch):
    from demo import debrief
    monkeypatch.setattr(debrief, "chapter_context", lambda d, c=None: {
        "run": None, "title": None, "summary": None, "minutes_heard": 0, "minutes": None})
    monkeypatch.setattr(debrief, "moments", lambda d, c=None: [])
    text = IV.session_config(cfg, "debrief", IV.debrief_values(cfg, tmp_path))["instructions"]
    assert "Did anything make you want to stop it and ask something?" in text
    assert "less than a minute" in text and "{" not in text.replace("{{", "")


def test_questions_asked_in_order_and_out_of_order():
    said = ["Take me through what that was like, from the start.", "Say more about that.",
            "Was there a moment you were most into it?", "At 3:20 you asked about levers. How did that go?",
            "How would you describe it to a friend?", "If another one were waiting tomorrow morning, would you?",
            "Is there anything I didn't ask that I should have?"]
    got = IV.questions_asked([{"who": "interviewer", "text": x} for x in said] + [{"who": "listener", "text": "a friend"}])
    assert got == {"asked": [1, 2, 3, 4, 5], "in_order": True, "closing": True}
    swapped = IV.questions_asked([{"who": "interviewer", "text": said[4]}, {"who": "interviewer", "text": said[0]}])
    assert swapped == {"asked": [4, 1], "in_order": False, "closing": False}


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
    assert got["notes"]["opened"].startswith("Begin now") and {"time_up", "wrap_now"} <= set(got["notes"])
    assert got["settings"]["debrief_target_minutes"] == 8
    for who, text in said:
        c.post("/api/interview/line", json={"sid": got["sid"], "who": who, "text": text})
    return got["sid"]


FULL = [("out", "Welcome back. Take me through what that was like, from the start."), ("in", "It was calm."),
        ("out", "Was there a moment you were most into it? And a moment you drifted?"), ("in", "The list part."),
        ("out", "Did anything make you want to stop it and ask something?"), ("in", "No."),
        ("out", "How would you describe it to a friend?"), ("in", "A radio show for me."),
        ("out", "If another one were waiting for you tomorrow morning, would you listen?"), ("in", "Yes."),
        ("out", "Is there anything I didn't ask that I should have?"), ("in", "No."),
        ("out", "Thank you, Sam. That really helps.")]


def test_end_debrief_marks_it_done_and_starts_the_summary(world):
    sid = debrief_session(world, FULL)
    got = world.client.post("/api/interview/tool", json={"sid": sid, "name": "end_debrief"}).json()
    assert got == {"ended": True, "act": "close"}
    state = json.loads((world.dir / "study" / "debrief.json").read_text())
    assert state["done"] and state["finished_at"] and state["questions_asked"] == [1, 2, 3, 4, 5] and state["session"] == sid
    end = world.client.post("/api/interview/end", json={"sid": sid, "how": "done"}).json()
    assert end["done"] and end["questions_asked"] == [1, 2, 3, 4, 5]
    assert world.after == [(world.dir / "study", sid, "debrief")]
    assert (world.dir / "study" / f"debrief-{sid}.jsonl").exists()


def test_a_complete_debrief_without_the_tool_counts_as_done_and_a_partial_one_does_not(world):
    sid = debrief_session(world, FULL)
    assert world.client.post("/api/interview/end", json={"sid": sid, "how": "dropped"}).json()["done"]
    (world.dir / "study" / "debrief.json").unlink()
    sid = debrief_session(world, FULL[:4])
    assert world.client.post("/api/interview/end", json={"sid": sid, "how": "pagehide"}).json()["done"] is False
    state = json.loads((world.dir / "study" / "debrief.json").read_text())
    assert state == {**state, "done": False, "questions_asked": [1, 2]}


def test_a_stale_session_is_closed_on_the_next_start(world, monkeypatch, cfg):
    sid = world.client.post("/api/interview/start", json={"mode": "intro", "sdp": "x"}).json()["sid"]
    cfg.setdefault("demo", {}).setdefault("interviewer", {})["stale_minutes"] = 0
    world.client.post("/api/interview/start", json={"mode": "intro", "sdp": "x"})
    rec = lines(world.dir / "study" / f"intro-{sid}.jsonl")
    assert rec[-1]["ended"] == "stale"


def test_interview_js_is_served(world):
    r = world.client.get("/interview.js")
    assert r.status_code == 200 and "window.Interview" in r.text and r.headers["content-type"].startswith("text/javascript")


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


def summary_client(monkeypatch, text="# Debrief: Sam\n\n## 1. What it was like, from the start\nCalm."):
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
                                          "heard": "They heard all of it.", "moments": "none"})
    assert out.read_text().startswith("# Debrief: Sam")
    prompt = seen[0]["messages"][0]["content"]
    assert "[1:15] them: It was calm." in prompt and "[1:10] interviewer: Welcome back." in prompt
    assert "[0:03] them: I'm Sam." in prompt and "The List" in prompt
    assert seen[0]["model"] == cfg["models"]["researcher"]
    log = lines(study / "log.jsonl")
    assert log[0]["stage"] == "debrief_summary" and log[0]["cost_usd"] > 0 and log[0]["kind"] == "interviewer"


def test_summary_skips_a_silent_debrief_and_never_overwrites_a_finished_one(cfg, tmp_path, monkeypatch):
    seen = summary_client(monkeypatch)
    study = tmp_path / "study"
    write_debrief(study, "quiet", [("out", "Take me through what that was like, from the start.")], done=False)
    assert IV.summarize(cfg, study, "quiet", {}) is None and not seen
    write_debrief(study, "full", FULL, done=True)
    assert IV.summarize(cfg, study, "full", {"person": "Sam"})
    write_debrief(study, "later", FULL[:2], done=False)
    (study / "debrief.json").write_text(json.dumps({"done": True, "session": "full"}))
    assert IV.summarize(cfg, study, "later", {"person": "Sam"}) is None and len(seen) == 1


def test_after_end_summarises_a_debrief_then_fetches_the_audio(cfg, tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr(IV, "summarize", lambda cfg, study, sid, values: order.append(("summary", sid)))
    monkeypatch.setattr(IV, "fetch_audio", lambda cfg, study, live_id: order.append(("audio", live_id)))
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
