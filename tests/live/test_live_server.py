"""The live voice's server side (voice/gpt_live/): instructions, the session config, the chapter as
paragraphs, restart points, the question's context, the reading check, and the /api/live routes.
Nothing here reaches OpenAI: the WebRTC call is replaced."""

import json
import os
import time
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

import server
from fakes import FakeAnthropic, Responder
from stages import guide, llm
from stages.common import StageError
from voice.gpt_live import chapter as C
from voice.gpt_live import session as S

VALUES = {"identity": "(none: this is your first meeting)", "names": "a, b", "sparks": "x", "profile": "(nothing yet)",
          "history": "(none yet)", "chapter_now": "(none)", "minutes": "4", "now": "Friday night",
          "earlier": "(nothing yet: the conversation is just starting)"}

TEXT = ("Picture a woman in a meeting. [D01] Her name is Donella Meadows. [D01, D14]\n\n"
        "The meeting is about trade. It angers her.\n\n[pause]\n\n"
        "So she gets up. She writes a list, and the list has [T]he nine items.")


def test_instructions_are_the_parts_in_order_filled(cfg):
    text = S.instructions(S.values_for(cfg, VALUES))
    assert text.index("SYSTEM") < text.index("HOW YOU TALK") < text.index("GETTING TO KNOW") < text.index("THE PITCH")
    assert text.index("THE CHAPTER") < text.index("IF THEY INTERRUPT") < text.index("Delegation policy:")
    assert "You are meeting this listener for the first time." in text
    assert "Diotima: say it like" in text  # the pronunciation table moved here from narration
    assert "{{" not in text and "Generate <|SpawnThinking|> when:" in text
    assert "# Part" not in text  # the notes above each part's --- are for Ian
    assert S.approx_tokens(text) < S.INSTRUCTION_TOKENS


def test_every_placeholder_has_a_value(cfg):
    have = set(S.values_for(cfg, VALUES))
    assert S.placeholders() <= have
    with pytest.raises(StageError):
        S.instructions({k: v for k, v in S.values_for(cfg, VALUES).items() if k != "profile"})


def test_a_returning_listener_is_met_again(cfg):
    v = dict(VALUES, identity="You are Maren. They are Sam.")
    assert "You have met this listener before." in S.instructions(S.values_for(cfg, v))


def test_session_config_has_the_pinned_models_voice_greeting_and_tools(cfg):
    sc = S.session_config(cfg, VALUES)
    assert sc["model"] == "gpt-live-1" and sc["audio"] == {"output": {"voice": "gleam"}}
    assert "opening" not in sc  # not a GPT-Live field: the API refuses it (unknown_parameter)
    first = sc["input"][-1]
    assert first["role"] == "developer" and first["content"][0]["text"] == S.notes()["opened"]
    d = sc["delegation"]
    assert d["type"] == "responses" and d["responses"]["model"] == cfg["live"]["backend_model"]
    names = [t["name"] for t in d["responses"]["tools"]]
    assert names == ["start_chapter", "add_note", "get_status", "step_back", "resume_chapter", "save_seed",
                     "start_reading"]
    assert all(t["type"] == "function" and t["parameters"]["type"] == "object" for t in d["responses"]["tools"])
    assert "scout_topic" not in [t["name"] for t in S.session_config(cfg, VALUES, fast=False)["delegation"]["responses"]["tools"]]
    ws = S.session_config(cfg, VALUES, transport="websocket")
    assert ws["audio"]["format"] == {"type": "audio/pcm", "rate": 24000}


def test_notes_have_every_key_the_page_uses():
    page = (S.ROOT / "page" / "live.js").read_text(encoding="utf-8")
    import re
    used = set(re.findall(r"\bN\.([a-z_]+)", page))
    assert used <= set(S.notes()), used - set(S.notes())


def test_appends_fit_gpt_lives_limit():
    long = " ".join(f"Sentence number {i} is here." for i in range(600))
    parts = S.split_for_append(long)
    assert len(parts) > 1 and all(S.approx_tokens(p) <= S.APPEND_TOKENS for p in parts)
    assert " ".join(parts).split() == long.split()
    assert S.split_for_append("short") == ["short"]


def test_cost_is_minutes_plus_backend_tokens(cfg):
    assert S.cost(cfg, 60) == pytest.approx(0.05)
    usd = S.cost(cfg, 120, {"model": "gpt-6.1-terra", "input": 10000, "output": 1000, "cached": 4000})
    assert usd == pytest.approx(0.10 + (6000 * 1 + 4000 * 0.05 + 1000 * 5) / 1e6)


def test_create_webrtc_sends_the_offer_and_keeps_the_key_server_side(cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    sent = {}

    class Http:
        def post(self, url, headers, json, timeout):
            sent.update(url=url, headers=headers, body=json)
            return NS(status_code=201, json=lambda: {"session": {"id": "live_1"}, "transport": {"type": "webrtc", "sdp": "ANSWER"}})
    got = S.create_webrtc(cfg, {"model": "gpt-live-1"}, "OFFER", client=Http())
    assert got == {"id": "live_1", "sdp": "ANSWER"}
    assert sent["body"]["transport"] == {"type": "webrtc", "sdp": "OFFER"} and sent["headers"]["Authorization"] == "Bearer sk-test"

    class Refuses:
        def post(self, *a, **k):
            return NS(status_code=400, text="bad", json=lambda: {"error": {"message": "Unknown parameter: 'session.opening'."}})
    with pytest.raises(StageError, match="Unknown parameter"):
        S.create_webrtc(cfg, {}, "OFFER", client=Refuses())


# --- the chapter ---------------------------------------------------------------------------------

def test_paragraphs_are_cleaned_and_numbered():
    ps = C.clean_paragraphs(TEXT, 2)
    assert [p["id"] for p in ps] == ["s2-p01", "s2-p02", "s2-p03"]
    assert ps[0]["text"] == "Picture a woman in a meeting. Her name is Donella Meadows."
    assert ps[0]["sentences"] == ["Picture a woman in a meeting.", "Her name is Donella Meadows."]
    assert ps[2]["beat"] and not ps[1]["beat"] and "The nine items" in ps[2]["text"]


def test_a_bracket_the_guide_would_read_aloud_stops_it():
    with pytest.raises(StageError):
        C.clean_paragraphs("She said [laughs] that.", 1)


def test_a_long_paragraph_is_cut_to_fit_one_append():
    long = " ".join(f"This is sentence {i} of a very long paragraph that goes on." for i in range(60))
    ps = C.clean_paragraphs(long, 1)
    assert len(ps) > 1 and all(len(p["text"]) <= C.PARA_MAX_CHARS for p in ps)
    assert all(S.approx_tokens(S.notes()["read"] + " " + p["text"]) <= S.APPEND_TOKENS for p in ps)


def chapter_dict():
    return {"title": "T", "sections": [
        {"n": 1, "title": "One", "locked": True, "paragraphs": C.clean_paragraphs(TEXT, 1)},
        {"n": 2, "title": "Two", "locked": True, "paragraphs": C.clean_paragraphs("A new part. It starts here.\n\nMore of it.", 2)},
        {"n": 3, "title": "Three", "locked": False, "paragraphs": []}]}


def test_restart_points_follow_the_rewind_table(cfg):
    ch, rules = chapter_dict(), cfg["live"]
    assert C.restart(ch, "s1-p01", 1, 10, rules) == {"id": "s1-p01", "sentence": 1, "rule": "sentence"}
    assert C.restart(ch, "s1-p01", 1, 60, rules) == {"id": "s1-p01", "sentence": 0, "rule": "previous"}
    assert C.restart(ch, "s1-p02", 0, 60, rules) == {"id": "s1-p01", "sentence": 1, "rule": "previous"}
    assert C.restart(ch, "s1-p02", 1, 300, rules) == {"id": "s1-p02", "sentence": 0, "rule": "paragraph"}
    assert C.restart(ch, "s2-p01", 0, 60, rules)["id"] == "s2-p01"  # never back across a section's start
    assert C.restart(ch, "s1-p02", 1, 5, rules, rewind="paragraph")["sentence"] == 0
    assert C.text_from(ch["sections"][0]["paragraphs"][0], 1) == "Her name is Donella Meadows."


def test_context_has_the_sentence_and_the_parts_but_nothing_coming():
    text = C.context(chapter_dict(), {"sections": [{"n": 1, "title": "One", "lands": "the list"},
                                                   {"n": 3, "title": "Three", "lands": "the end"}]}, "s1-p02", 0)
    assert 'while you were reading: "The meeting is about trade."' in text
    assert 'part 3, "Three" (still being written)' in text and 'part 1, "One" (written)' in text
    assert "So she gets up" not in text and "the end" not in text  # nothing of what is coming: it was read on from
    assert all(S.approx_tokens(c) <= S.APPEND_TOKENS for c in C.append_chunks(text))


def test_the_reading_check_logs_deviations(run):
    ch = chapter_dict()
    rec = C.check(run, ch, "s1-p02", "the meeting is about trade it angers her")
    assert rec["same"] and not rec["deviates"]
    rec = C.check(run, ch, "s1-p01", "okay here we go picture a woman in a meeting her name is donella meadows")
    assert not rec["same"] and rec["added"] == 4 and rec["deviates"]
    cut = C.check(run, ch, "s1-p01", "picture a woman", cut=True)
    assert cut["same"]  # cut short by a tap: compared only as far as it got
    lines = C.log_path(run).read_text().splitlines()
    assert len(lines) == 3 and json.loads(lines[1])["diffs"] == [["insert", "", "okay here we go"]]


# --- the routes ------------------------------------------------------------------------------------

@pytest.fixture
def app(cfg, tmp_path, monkeypatch):
    cfg["listener_dir"] = str(tmp_path / "listener")
    cfg["live"]["provider"] = "gpt_live"
    monkeypatch.setattr(llm, "_client", FakeAnthropic(Responder()))
    monkeypatch.setattr(server.memory, "after_session", lambda *a, **k: False)
    offers = []

    def webrtc(cfg, session, sdp, client=None):
        offers.append((session, sdp))
        return {"id": f"live_{len(offers)}", "sdp": "ANSWER"}
    monkeypatch.setattr(S, "create_webrtc", webrtc)
    runs = tmp_path / "runs"
    runs.mkdir()
    pipe = server.Pipeline(runs_dir=runs)
    started = []

    def start(args, run):
        started.append(args)
        pipe.proc, pipe.run = NS(returncode=None, poll=lambda: None), run
    monkeypatch.setattr(pipe, "start", start)
    return TestClient(server.create_app(pipe, cfg, remember_on_start=False)), offers, runs


def fast_run(runs, name="live-run", locked=(1,)):
    root = runs / name
    (root / "06_segments").mkdir(parents=True)
    (root / "fast.json").write_text(json.dumps({"mode": "fast", "voice": "live"}))
    (root / "05_outline.json").write_text(json.dumps({"title": "T", "sections": [
        {"n": 1, "title": "One", "lands": "a"}, {"n": 2, "title": "Two", "lands": "b"}]}))
    for n in locked:
        (root / "06_segments" / f"seg{n:02d}.md").write_text(TEXT)
    return root


def test_the_page_is_the_live_page_and_its_script_has_the_settings(app):
    c, _, _ = app
    page = c.get("/").text
    assert '<script src="/live.js">' in page and "__VOICE_CLIENT_JS__" not in page and "AI-generated" in page
    js = c.get("/live.js")
    assert js.headers["content-type"].startswith("text/javascript") and "__LIVE_SETTINGS__" not in js.text
    assert '"queue_words_left"' in js.text and '"opened"' in js.text
    assert js.text.startswith("// window.Speaker:")  # page/speaker.js ahead of it: the voice on a phone's loudspeaker


def test_a_finished_chapter_and_the_elevenlabs_setting_keep_the_old_page(app, cfg):
    c, _, _ = app
    assert "/live.js" not in c.get("/?chapter=x").text
    cfg["live"]["provider"] = "elevenlabs"
    assert "/live.js" not in c.get("/").text


def test_live_conversation_routes(app, cfg):
    c, offers, runs = app
    res = c.post("/api/live/start", json={"sdp": "OFFER"}).json()
    sid = res["session"]
    assert res["live_id"] == "live_1" and res["sdp"] == "ANSWER" and offers[0][1] == "OFFER"
    assert "SYSTEM" in offers[0][0]["instructions"]
    assert c.post("/api/live/line", json={"session": sid, "who": "listener", "text": "Hi, I'm Sam."}).json() == {"ok": True}
    c.post("/api/live/line", json={"session": sid, "who": "guide", "text": "Hi Sam!"})
    out = c.post("/api/live/tool", json={"session": sid, "name": "start_chapter", "args": {
        "topic": "levers", "listener_said": "everything is a system", "why": "", "notes": ""}}).json()
    assert out["started"] is True
    assert c.post("/api/live/tool", json={"session": sid, "name": "resume_chapter", "args": {"reason": "listener"}}).json() == {"resuming": True}
    assert c.post("/api/live/tool", json={"session": sid, "name": "step_back", "args": {}}).json()["quiet"] is True
    usage = c.post("/api/live/usage", json={"session": sid, "live_id": "live_1", "seconds": 120}).json()
    assert usage["cost_usd"] == pytest.approx(0.10)
    res2 = c.post("/api/live/resume", json={"session": sid, "sdp": "OFFER2", "reason": "dropped"}).json()
    assert res2["live_id"] == "live_2" and "you: Hi Sam!" in offers[1][0]["instructions"]  # told the conversation so far
    tr = [json.loads(x) for x in (guide.listener_dir(cfg) / "sessions" / f"{sid}.jsonl").read_text().splitlines()]
    assert [x["who"] for x in tr][:6] == ["session", "session", "page", "listener", "guide", "tool"]
    assert tr[1]["live_id"] == "live_1"
    log = [json.loads(x) for x in (guide.listener_dir(cfg) / "sessions" / "log.jsonl").read_text().splitlines()]
    assert log[-1]["provider"] == "openai" and log[-1]["cost_usd"] == pytest.approx(0.10)
    assert c.post("/api/chat/end", json={"session": sid}).json() == {"ended": True}
    assert c.post("/api/live/tool", json={"session": sid, "name": "get_status", "args": {}}).status_code == 409


def test_voice_unavailable_is_503(app, monkeypatch):
    c, _, _ = app

    def down(*a, **k):
        raise StageError("No OpenAI key")
    monkeypatch.setattr(S, "create_webrtc", down)
    res = c.post("/api/live/start", json={"sdp": "OFFER"})
    assert res.status_code == 503 and "Voice isn't available" in res.json()["detail"]


def test_chapter_context_restart_and_read_routes(app, cfg):
    c, _, runs = app
    fast_run(runs)
    ch = c.get("/api/live/chapter/live-run").json()
    assert [s["locked"] for s in ch["sections"]] == [True, False]
    assert [p["id"] for p in ch["sections"][0]["paragraphs"]] == ["s1-p01", "s1-p02", "s1-p03"]
    assert ch["outline"]["title"] == "T"
    ctx = c.post("/api/live/context", json={"run": "live-run", "id": "s1-p02", "sentence": 1}).json()
    assert 'while you were reading: "It angers her."' in ctx["chunks"][0]
    at = c.post("/api/live/restart", json={"run": "live-run", "id": "s1-p02", "sentence": 1, "seconds": 10}).json()
    assert at == {"id": "s1-p02", "sentence": 1, "rule": "sentence"}
    rec = c.post("/api/live/read", json={"run": "live-run", "id": "s1-p02", "heard": "the meeting is about trade it angers her"}).json()
    assert rec["same"] is True
    assert (runs / "live-run" / "live" / "reading.jsonl").exists()
    assert c.get("/api/live/chapter/nope").status_code == 404
    assert c.post("/api/live/context", json={"run": "live-run", "id": "s9-p01", "sentence": 0}).status_code == 404


def test_spelled_numbers_heard_as_digits_are_the_same():
    d = C.deviation("It was nineteen ninety-eight, and ten birds came.", "It was 1998, and 10 birds came.")
    assert d["same"], d["diffs"]
    assert not C.deviation("It was nineteen ninety-eight.", "It was 1997, then.")["same"]


# --- regressions from the review of the GPT-Live change -----------------------------------------------

def test_the_guide_picks_from_the_conversations_drawn_names(app, cfg):
    """20_meeting.md lists the conversation's drawn names ({{names}}, prompts/guide_names.md), not a list
    of its own: the name memory records, and a resumed session keeps, is one the server knows."""
    c, offers, _ = app
    sid = c.post("/api/live/start", json={"sdp": "OFFER"}).json()["session"]
    tr = [json.loads(x) for x in (guide.listener_dir(cfg) / "sessions" / f"{sid}.jsonl").read_text().splitlines()]
    names = tr[0]["names"]
    assert names and f"pick your name from these: {', '.join(names)}." in offers[0][0]["instructions"]
    c.post("/api/live/line", json={"session": sid, "who": "guide", "text": f"Hey! I'm {names[2]}. What's your name?"})
    c.post("/api/live/resume", json={"session": sid, "sdp": "OFFER2", "reason": "dropped"})
    assert f"your name is {names[2]}" in offers[1][0]["instructions"]


def test_start_records_what_was_sent(app, cfg):
    """The session record says what OpenAI was given (it can't be fetched back): an instructions hash and
    length, the tools, the startup input (none: the page greets once the session starts)."""
    c, offers, _ = app
    sid = c.post("/api/live/start", json={"sdp": "OFFER"}).json()["session"]
    tr = [json.loads(x) for x in (guide.listener_dir(cfg) / "sessions" / f"{sid}.jsonl").read_text().splitlines()]
    rec = tr[1]
    import hashlib
    assert rec["instructions_sha256"] == hashlib.sha256(offers[0][0]["instructions"].encode()).hexdigest()[:16]
    assert rec["instructions_chars"] == len(offers[0][0]["instructions"])
    assert "start_chapter" in rec["tools"] and rec["startup"] is None
    assert offers[0][0]["input"] == []
    assert tr[2] == {**tr[2], "who": "page", "text": S.notes()["opened"]}


def test_usage_after_the_conversation_ended_is_logged_and_only_once(app, cfg):
    """Regression: Reset (the server forgets the conversation first) and the page closing (its chat/end
    beacon handled before the usage beacon) lost the session's cost: /api/live/usage needed the
    conversation still current (409). Now it is logged all the same, and a second report of the same
    live session (the beacon and the end) is not logged twice."""
    c, _, _ = app
    sid = c.post("/api/live/start", json={"sdp": "OFFER"}).json()["session"]
    assert c.post("/api/chat/end", json={"session": sid}).json() == {"ended": True}
    res = c.post("/api/live/usage", json={"session": sid, "live_id": "live_1", "seconds": 60})
    assert res.status_code == 200 and res.json()["cost_usd"] == pytest.approx(0.05)
    again = c.post("/api/live/usage", json={"session": sid, "live_id": "live_1", "seconds": 60}).json()
    assert again.get("logged") is False
    log = [json.loads(x) for x in (guide.listener_dir(cfg) / "sessions" / "log.jsonl").read_text().splitlines()]
    mine = [x for x in log if x.get("live_id") == "live_1" and x.get("provider") == "openai"]
    assert len(mine) == 1 and mine[0]["session"] == sid and mine[0]["cost_usd"] == pytest.approx(0.05)
    # a second session of the same conversation is its own record
    c.post("/api/live/usage", json={"session": sid, "live_id": "live_2", "seconds": 60})
    log = [json.loads(x) for x in (guide.listener_dir(cfg) / "sessions" / "log.jsonl").read_text().splitlines()]
    assert sorted(x["live_id"] for x in log if x.get("provider") == "openai") == ["live_1", "live_2"]


def test_a_resumed_session_keeps_the_name_the_guide_said(app):
    """Regression: once the conversation passed guide.EARLIER_CHARS, the guide's introduction was cut
    from {{earlier}}, and a new session (a drop, or a section boundary) could pick another name from
    20_meeting.md's list mid-chapter. The name it said is carried in {{identity}}, still a first meeting."""
    c, offers, _ = app
    sid = c.post("/api/live/start", json={"sdp": "OFFER"}).json()["session"]
    c.post("/api/live/line", json={"session": sid, "who": "guide", "text": "Hi, I'm Maren. What's your name?"})
    c.post("/api/live/line", json={"session": sid, "who": "listener", "text": "Sam."})
    filler = "We talked about the cabinets and the plywood and the hinges for a good long while. " * 3
    for k in range(40):
        c.post("/api/live/line", json={"session": sid, "who": "guide" if k % 2 else "listener", "text": f"{k}. {filler}"})
    assert sum(len(filler) for _ in range(40)) > guide.EARLIER_CHARS
    c.post("/api/live/resume", json={"session": sid, "sdp": "OFFER2", "reason": "dropped"})
    text = offers[1][0]["instructions"]
    assert "Hi, I'm Maren" not in text  # the introduction was cut from what it is told of the conversation
    assert "already told them your name is Maren" in text
    assert "You are meeting this listener for the first time." in text
    c.post("/api/live/resume", json={"session": sid, "sdp": "OFFER3", "reason": "section boundary"})
    assert "already told them your name is Maren" in offers[2][0]["instructions"]


def test_a_returning_guides_identity_is_not_replaced_on_resume(app, cfg):
    """The name carry is for a first meeting only: a guide that already knows who it is (listener/guide.md)
    keeps that identity in a resumed session."""
    c, offers, _ = app
    d = guide.listener_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    (d / "guide.md").write_text("You are Juno. They are Sam, who builds cabinets.\n")
    sid = c.post("/api/live/start", json={"sdp": "OFFER"}).json()["session"]
    c.post("/api/live/line", json={"session": sid, "who": "guide", "text": "Hey Sam, Wren here."})
    c.post("/api/live/resume", json={"session": sid, "sdp": "OFFER2", "reason": "dropped"})
    text = offers[1][0]["instructions"]
    assert "You are Juno." in text and "already told them your name" not in text
    assert "You have met this listener before." in text


def with_live(cfg, monkeypatch, provider="gpt_live"):
    """config.yaml as on disk, for the code that reads it there (mode_args, start_seed's lookup)."""
    from faststart import routes as fast_routes
    disk = {**cfg, "live": {**cfg["live"], "provider": provider}}
    monkeypatch.setattr(fast_routes, "load_config", lambda: disk)
    monkeypatch.setattr(server, "load_config", lambda: disk)
    return disk


def started_run(runs):
    found = [p for p in runs.iterdir() if (p / "fast.json").exists()]
    assert len(found) == 1, found
    return json.loads((found[0] / "fast.json").read_text())


START = {"topic": "levers", "listener_said": "everything is a system", "why": "", "notes": ""}


def test_a_chapter_started_from_the_live_session_is_read_live(app, cfg, monkeypatch):
    c, _, runs = app
    with_live(cfg, monkeypatch)
    sid = c.post("/api/live/start", json={"sdp": "OFFER"}).json()["session"]
    assert c.post("/api/live/tool", json={"session": sid, "name": "start_chapter", "args": START}).json()["started"]
    assert started_run(runs).get("voice") == "live"


def test_a_chapter_started_from_the_typed_chat_is_narrated(app, cfg, monkeypatch):
    """Regression: voice couldn't start, the page fell back to the typed guide (/api/chat/start), and its
    start_chapter marked the run "voice": "live" because config said gpt_live: never narrated, never
    read, never heard. A conversation that is not a live session starts a narrated fast run."""
    c, _, runs = app
    with_live(cfg, monkeypatch)
    sid = c.post("/api/chat/start").json()["session"]
    assert c.post("/api/live/tool", json={"session": sid, "name": "start_chapter", "args": START}).json()["started"]
    info = started_run(runs)
    assert info["mode"] == "fast" and "voice" not in info


@pytest.fixture
def seeds(cfg, tmp_path, monkeypatch):
    """A Pipeline whose start only records (never busy), and config on disk saying gpt_live."""
    runs = tmp_path / "runs"
    runs.mkdir()
    pipe = server.Pipeline(runs_dir=runs)
    started = []
    monkeypatch.setattr(pipe, "start", lambda args, run: started.append(args))
    # No chapter card: its thread outlived the test with no fake client and was caught by a later module's guard
    # (it would have reached the real API where none is installed; review, 2026-11-19).
    monkeypatch.setattr(pipe, "_card", lambda root: None)
    with_live(cfg, monkeypatch)
    return pipe, runs, started


def test_a_seed_lookup_continues_a_run_only_in_its_own_voice(seeds):
    """Regression: a fast run started narrated (no "voice") and stopped, then asked for again by the live
    guide, was continued as a narrated run the live page can't play (and the reverse). A lookup now
    passes over a run of the other voice and starts a new one; the same voice still continues it."""
    pipe, runs, started = seeds
    narrated = pipe.start_seed("levers", mode="fast", live=False)
    assert "voice" not in json.loads((runs / narrated / "fast.json").read_text())
    spoken = pipe.start_seed("levers", mode="fast", live=True)
    assert spoken != narrated and json.loads((runs / spoken / "fast.json").read_text())["voice"] == "live"
    assert pipe.start_seed("levers", mode="fast", live=True) == spoken
    assert pipe.start_seed("levers", mode="fast", live=False) == narrated
    assert "voice" not in json.loads((runs / narrated / "fast.json").read_text())  # it kept its voice
    assert all(a[:3] == ["new", a[1], "--unattended"] for a in started)


def test_with_the_elevenlabs_provider_a_live_start_continues_the_narrated_run(seeds, cfg, monkeypatch):
    """The live flag only counts while config says gpt_live: with elevenlabs, every fast run is narrated,
    so a live=True lookup continues the narrated run rather than start a new one."""
    pipe, runs, _ = seeds
    with_live(cfg, monkeypatch, provider="elevenlabs")
    narrated = pipe.start_seed("levers", mode="fast", live=False)
    assert pipe.start_seed("levers", mode="fast", live=True) == narrated
    assert "voice" not in json.loads((runs / narrated / "fast.json").read_text())


# --- whose chapter (Ian's first live test, 2026-11-14) -----------------------------------------------------
# An older conversation's finished chapter was the newest run; the page read it the moment he first spoke.

def live_status(runs, name="old-run", state="ready", player=None, archived=None, audio=None, updated=None, **more):
    root = fast_run(runs, name, locked=(1, 2))
    (root / "status.json").write_text(json.dumps({
        "run": name, "state": state, "mode": "fast", "voice": "live", "title": "Old", "audio": audio,
        "pid": None, "updated": updated or time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sections": [{"n": n, "title": t, "state": "ready", "text": f"06_segments/seg{n:02d}.md", "pieces": [], "ready_in": None}
                     for n, t in ((1, "One"), (2, "Two"))], **more}))
    if player is not None:
        (root / "fast").mkdir(exist_ok=True)
        (root / "fast" / "player.json").write_text(json.dumps(player))
    if archived is not None:
        (root / "live").mkdir(exist_ok=True)
        (root / "live" / "archive.json").write_text(json.dumps({"state": archived}))
    return root


@pytest.mark.parametrize("state, player, archived, audio, want", [
    ("ready", {"state": "done", "at": 0}, None, None, None),                # heard to the end (Ian's herons run)
    ("ready", None, None, None, ("s1-p01", False)),                         # written while they were away, never begun
    ("ready", {"state": "paused", "section": 2, "piece": 1, "at": -60}, "waiting for the recording", None, None),
    ("ready", {"state": "paused", "section": 2, "piece": 1, "at": -60}, "failed", None, None),
    ("ready", {"state": "idle", "section": 2, "piece": 1, "at": -60}, None, None, None),  # let go (Reset)
    ("ready", {"state": "paused", "section": 2, "piece": 1, "at": -3600}, None, None, None),  # an hour ago
    ("ready", {"state": "paused", "section": 2, "piece": 1, "at": -60}, "done", None, None),  # archived
    ("ready", {"state": "paused", "section": 2, "piece": 1, "at": -60}, None, "10_audio/chapter.mp3", None),
    ("problem", {"state": "playing", "section": 1, "piece": 0, "at": -10}, None, None, None),
    ("stopped", {"state": "playing", "section": 1, "piece": 0, "at": -10}, None, None, None),
    ("ready", {"state": "paused", "section": 2, "piece": 1, "at": -60}, None, None, ("s2-p02", True)),  # reopened mid-chapter
    ("making", None, None, None, ("s1-p01", False)),                         # being made, reading not begun
    ("making", {"state": "waiting", "section": 2, "piece": 0, "at": -30}, None, None, ("s2-p01", True)),
    ("making", {"state": "done", "at": -30}, None, None, None),
])
def test_a_new_conversation_takes_over_only_a_chapter_still_going(app, cfg, state, player, archived, audio, want):
    c, offers, runs = app
    if player is not None:
        player = {**player, "at": time.time() + player["at"]}
    live_status(runs, state=state, player=player, archived=archived, audio=audio,
                pid=os.getpid() if state == "making" else None)  # a run being made has a live process
    res = c.post("/api/live/start", json={"sdp": "OFFER"}).json()
    cont = res["continue"]
    first = [m["content"][0]["text"] for m in offers[-1][0]["input"]]
    if want is None:
        assert cont is None and first == []  # a plain new conversation: the page greets
        return
    assert (cont["id"], cont["begun"]) == want and cont["run"] == "old-run"
    key = "reloaded" if want[1] else ("ready_unheard" if state == "ready" else "reopened")
    assert first == [S.notes()[key].replace("{title}", "Old")]
    # and it is this conversation's chapter: the guide hears about it
    got = c.post("/api/live/tool", json={"session": res["session"], "name": "get_status", "args": {}}).json()
    assert "note" not in got and got["state"] != "idle"


def test_start_chapter_tells_the_page_its_run(app, cfg, monkeypatch):
    """start_chapter's result carries the run for the page (L.own: the only chapter it may read)."""
    c, _, runs = app
    monkeypatch.setattr(server.Pipeline, "start_seed", lambda self, *a, **k: "new-run")
    fast_run(runs, "new-run", locked=())
    sid = c.post("/api/live/start", json={"sdp": "OFFER"}).json()["session"]
    out = c.post("/api/live/tool", json={"session": sid, "name": "start_chapter", "args": {"topic": "herons"}}).json()
    assert out["started"] is True and out["run"] == "new-run"


def test_get_status_and_save_seed_never_take_an_older_chapter_for_this_ones(app, cfg):
    c, _, runs = app
    live_status(runs, state="ready", player={"state": "done", "at": time.time() - 7200})
    sid = c.post("/api/live/start", json={"sdp": "OFFER"}).json()["session"]
    got = c.post("/api/live/tool", json={"session": sid, "name": "get_status", "args": {}}).json()
    assert got["state"] == "idle" and "No chapter" in got["note"]
    saved = c.post("/api/live/tool", json={"session": sid, "name": "save_seed", "args": {"topic": "x", "why": "y"}}).json()
    assert saved["saved"] is False


def test_get_status_still_reports_a_chapter_being_made(app, cfg):
    """One chapter at a time: a run being made (another conversation's) is reported, so the guide doesn't
    promise a second one."""
    c, _, runs = app
    root = live_status(runs, state="making", pid=os.getpid())
    (root / "fast" ).mkdir(exist_ok=True)
    (root / "fast" / "player.json").write_text(json.dumps({"state": "done", "at": time.time()}))  # not taken over
    sid = c.post("/api/live/start", json={"sdp": "OFFER"}).json()["session"]
    assert c.post("/api/live/tool", json={"session": sid, "name": "get_status", "args": {}}).json()["state"] == "being made"


def test_live_get_status_carries_the_peek(app, cfg):
    """The live guide's get_status passes the peek through (faststart/peek.py), while the older guides' wait
    rules (while_waiting) are still dropped."""
    c, _, runs = app
    root = live_status(runs, state="making", pid=os.getpid(), first_audio=False)
    (root / "fast").mkdir(exist_ok=True)
    (root / "fast" / "player.json").write_text(json.dumps({"state": "done", "at": time.time()}))
    (root / "ledger.json").write_text(json.dumps({"rows": [
        {"id": "L01", "section": 2, "idea": "To settle: what Katzir found comparing cattle egrets with little egrets."}]}))
    (root / "fast_timeline.jsonl").write_text(json.dumps({"t": 1, "event": "research_start", "row": "L01"}) + "\n")
    sid = c.post("/api/live/start", json={"sdp": "OFFER"}).json()["session"]
    got = c.post("/api/live/tool", json={"session": sid, "name": "get_status", "args": {}}).json()
    assert got["peek"]["working_on"].startswith('part 1, "One"')
    assert got["peek"]["looking_into"] == ["what Katzir found comparing cattle egrets with little egrets"]
    assert "while_waiting" not in got


def test_stopping_a_fast_run_keeps_what_the_live_page_knows_it_by(app, cfg):
    """Pipeline.stop rewrote status.json without mode, voice and sections, so the live page no longer
    recognised the run it was reading as stopped."""
    c, _, runs = app
    live_status(runs, state="making")
    assert c.post("/api/chapters/old-run/stop").status_code == 200
    st = json.loads((runs / "old-run" / "status.json").read_text())
    assert st["state"] == "stopped" and st["mode"] == "fast" and st["voice"] == "live" and len(st["sections"]) == 2


def test_a_chapter_written_long_ago_and_never_begun_is_not_taken_over(app, cfg):
    c, offers, runs = app
    live_status(runs, state="ready", updated="2026-11-01T10:00:00")
    assert c.post("/api/live/start", json={"sdp": "OFFER"}).json()["continue"] is None


def test_start_fresh_leaves_the_old_chapter_alone_and_never_continues_it(cfg, tmp_path, monkeypatch):
    """Reset memory used to mark the old chapter's player idle so a first meeting wouldn't pick it back up.
    Start fresh (overnight/DESIGN.md) now switches to a new listener, whose chapters folder is empty: the old
    chapter is left byte for byte and never continued."""
    from overnight import profiles
    cfg.pop("listener_dir", None)
    cfg["live"]["provider"] = "gpt_live"
    monkeypatch.setattr(llm, "_client", FakeAnthropic(Responder()))
    monkeypatch.setattr(server.memory, "after_session", lambda *a, **k: False)
    monkeypatch.setattr(S, "create_webrtc", lambda cfg, session, sdp, client=None: {"id": "live_1", "sdp": "ANSWER"})
    ian = profiles.create("ian")
    profiles.set_active("ian")
    runs = ian / "chapters"
    live_status(runs, state="ready", player={"state": "paused", "section": 2, "piece": 0, "at": time.time() - 30})
    player = runs / "old-run" / "fast" / "player.json"
    before = player.read_bytes()
    c = TestClient(server.create_app(server.Pipeline(), cfg, remember_on_start=False))
    assert c.post("/api/reset").status_code == 200
    assert player.read_bytes() == before
    assert c.post("/api/live/start", json={"sdp": "OFFER"}).json()["continue"] is None


def test_stopping_keeps_first_audio(app, cfg):
    c, _, runs = app
    live_status(runs, state="making", first_audio=True)
    c.post("/api/chapters/old-run/stop")
    assert json.loads((runs / "old-run" / "status.json").read_text())["first_audio"] is True


# --- after Ian's second test (2026-11-15) ------------------------------------------------------------------

def test_start_reading_starts_only_this_conversations_written_opening(app, cfg):
    """start_reading (the listener's yes to the guide's offer): starting only once this conversation's
    chapter has its opening written; before, or for no chapter, it says why."""
    c, _, runs = app
    sid = c.post("/api/live/start", json={"sdp": "OFFER"}).json()["session"]
    out = c.post("/api/live/tool", json={"session": sid, "name": "start_reading", "args": {}}).json()
    assert out == {"starting": False, "reason": "the opening isn't ready yet"}
    run = c.post("/api/live/tool", json={"session": sid, "name": "start_chapter", "args": {"topic": "herons"}}).json()["run"]
    root = runs / run

    def status(written):
        (root / "06_segments").mkdir(parents=True, exist_ok=True)
        (root / "06_segments" / "seg01.md").write_text(TEXT)
        (root / "status.json").write_text(json.dumps({
            "run": run, "state": "making", "mode": "fast", "voice": "live", "title": "T", "pid": os.getpid(),
            "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "sections": [{"n": 1, "title": "One", "text": "06_segments/seg01.md" if written else None, "pieces": []}]}))
    status(False)
    assert c.post("/api/live/tool", json={"session": sid, "name": "start_reading", "args": {}}).json()["starting"] is False
    status(True)
    assert c.post("/api/live/tool", json={"session": sid, "name": "start_reading", "args": {}}).json() == {"starting": True}


def test_the_older_guides_wait_rules_never_reach_the_live_guide(app, cfg, monkeypatch):
    """start_chapter's while_waiting (prompts/guide_wait_fast.md: "keep them talking", everything into
    add_note) was handed to the live backend and pulled the guide toward an interview; the live parts
    cover the wait. Dropped from the result and from the transcript's record of it."""
    c, _, runs = app
    from stages import guide as G
    real = G.Session.tool

    def tool(self, name, args):
        out = real(self, name, args)
        return {**out, "while_waiting": "Keep them talking."} if name == "start_chapter" else out
    monkeypatch.setattr(G.Session, "tool", tool)
    monkeypatch.setattr(server.Pipeline, "start_seed", lambda self, *a, **k: "new-run")
    fast_run(runs, "new-run", locked=())
    sid = c.post("/api/live/start", json={"sdp": "OFFER"}).json()["session"]
    out = c.post("/api/live/tool", json={"session": sid, "name": "start_chapter", "args": {"topic": "herons"}}).json()
    assert out["started"] and "while_waiting" not in out
    tr = (guide.listener_dir(cfg) / "sessions" / f"{sid}.jsonl").read_text()
    assert "Keep them talking" not in tr


def test_a_session_reopened_after_a_long_pause_says_nothing(app, cfg):
    """Play after a pause long enough to close the session: the new session is told to carry on and say
    nothing (notes.continue), not that it is back (notes.returned)."""
    c, offers, _ = app
    sid = c.post("/api/live/start", json={"sdp": "OFFER"}).json()["session"]
    c.post("/api/live/resume", json={"session": sid, "sdp": "OFFER2", "reason": "paused"})
    assert offers[-1][0]["input"][-1]["content"][0]["text"] == S.notes()["continue"]
    c.post("/api/live/resume", json={"session": sid, "sdp": "OFFER3", "reason": "dropped"})
    assert offers[-1][0]["input"][-1]["content"][0]["text"] == S.notes()["returned"]


def test_the_meeting_unfolds_and_the_page_has_the_new_settings(app, cfg):
    """The hello alone (no name, no question), the offer to say what this is, and the page's new cues."""
    notes = S.notes()
    assert "no name, no question" in notes["opened"]
    for k in ("hello_silence", "pitch_nudge", "pitch_nudge_again", "ready_again", "moved", "unanswered", "offer_line", "ready_line_resting"):
        assert notes[k]
    meeting = S.instructions(S.values_for(cfg, VALUES))
    assert "Your first turn is only a hello" in meeting and "Want to hear what I do?" in meeting and "laugh" in meeting
    js = app[0].get("/live.js").text
    for k in ("pitch_nudge_secs", "quiet_ask", "step_back_said", "ready_yes", "words_per_second", "end_hold_secs"):
        assert f'"{k}"' in js


def test_start_reading_takes_the_pages_own_chapter(app, cfg):
    """A page that picked its chapter up (a reload, the page check's adopt) has no start_chapter in this
    session: start_reading takes the page's own run when it is the one written."""
    c, _, runs = app
    root = live_status(runs, name="mine", state="making", pid=os.getpid())
    (root / "fast").mkdir(exist_ok=True)
    (root / "fast" / "player.json").write_text(json.dumps({"state": "done", "at": time.time()}))  # not taken over
    sid = c.post("/api/live/start", json={"sdp": "OFFER"}).json()["session"]
    assert c.post("/api/live/tool", json={"session": sid, "name": "start_reading", "args": {}}).json()["starting"] is False
    out = c.post("/api/live/tool", json={"session": sid, "name": "start_reading", "args": {}, "run": "mine"}).json()
    assert out == {"starting": True}



def test_what_this_is_is_offered_only_to_someone_not_yet_told(app, cfg):
    """Ian's fourth test: a returning listener was asked "Want to hear what I do?" again. The offer goes only
    into a first meeting's instructions, and not into a resumed session once the guide has explained it."""
    first = S.instructions(S.values_for(cfg, VALUES))
    assert "Want to hear what I do?" in first
    back = S.instructions(S.values_for(cfg, {**VALUES, "identity": "- Name: Sunniva.\n- Said about itself: it makes audio chapters."}))
    assert "Want to hear what I do?" not in back and "Never offer to explain it again" in back
    c, offers, _ = app
    sid = c.post("/api/live/start", json={"sdp": "OFFER"}).json()["session"]
    assert "Want to hear what I do?" in offers[-1][0]["instructions"]
    c.post("/api/live/line", json={"session": sid, "who": "guide", "text": "I make short audio chapters, just for you."})
    c.post("/api/live/resume", json={"session": sid, "sdp": "OFFER2", "reason": "dropped"})
    assert "Want to hear what I do?" not in offers[-1][0]["instructions"]


def test_the_live_wake_settings_are_stricter_than_the_old_pages(app, cfg):
    js = app[0].get("/live.js").text
    settings = json.loads(js.split("const S = ", 1)[1].split(";  // config", 1)[0])
    assert settings["wake"]["need_ms"] == cfg["live"]["page"]["wake"]["need_ms"] > cfg["voice"]["wake"]["need_ms"]
    assert settings["wake"]["ratio"] == cfg["voice"]["wake"]["ratio"]  # the rest kept from voice.wake



def test_a_rests_end_opens_a_session_told_why(app, cfg):
    """Stepping back closes the session (Ian's fifth note), so each way back is a new session with its own note."""
    c, offers, _ = app
    sid = c.post("/api/live/start", json={"sdp": "OFFER"}).json()["session"]
    notes = S.notes()
    for reason, key in (("tap", "back_from_rest"), ("wake", "listening_again"), ("ready", "ready_back"),
                        ("play", "continue"), ("dropped", "returned")):
        c.post("/api/live/resume", json={"session": sid, "sdp": "O", "reason": reason})
        assert offers[-1][0]["input"][-1]["content"][0]["text"] == notes[key], reason


def test_the_pitch_waits_for_real_interest(app, cfg):
    """Ian's tester run (2026-11-16): nudged 12 s after its first open question, the guide pitched the first
    thing he said ("not that compelling"), then guessed again after his no."""
    notes, pitch = S.notes(), S.instructions(S.values_for(cfg, VALUES))
    # the nudges are short lines again (Ian's third tester run, 2026-11-19); the instructions carry the rules
    assert "if nothing has stood out" in notes["pitch_nudge"] and "interested them most" in notes["pitch_nudge_again"]
    assert "Never pitch a passing mention" in pitch and "If they say no to a pitch, don't guess again" in pitch
    assert "Want to hear what I do?" in pitch  # the offer he liked stays
    # Review follow-ups: no "what would you love to understand better" (20_meeting.md rules that kind of
    # question out: asked cold, people go blank), and after a no the ask has edges and is not made twice
    # ("Never ask an open 'what would you like?' twice" stood two lines below "ask what they'd rather hear about").
    for text in (notes["pitch_nudge"], notes["pitch_nudge_again"], pitch):
        assert "understand better" not in text and "ask what they'd rather hear about" not in text
    assert "which of the things they've mentioned they'd want to hear more about" in notes["pitch_nudge"]
    after_no = next(x for x in pitch.splitlines() if x.startswith("- If they say no to a pitch"))
    assert "which of the other things they mentioned" in after_no and "two possibilities" in after_no and "surprise them" in after_no
    assert 'Never ask an open "what would you like?" twice' in pitch and "what they want to learn" in pitch
    page = cfg["live"]["page"]
    hold = page["pitch_hold"]
    assert hold["explained_secs"] >= 30 and hold["question_secs"] >= 15 and hold["max_secs"] <= 90
    assert page["explained"] == S.EXPLAINED.pattern  # the page and the server agree on "explained"
    assert '"pitch_hold"' in app[0].get("/live.js").text


def test_the_guide_never_speaks_as_if_it_had_lived_what_the_listener_has(app, cfg):
    """Ian's second tester run (2026-11-18): "the narrator ai was relating to human experiences which they couldn't
    have any experience with" ("Carbonara ... I'm kinda jealous of that"; "Plywood can be fussy, but when it's
    dialed in, it's so clean"). One of its own example lines had said a lunch "has me jealous". After his third
    run (2026-11-19) the rule is one sentence again, and small honest preferences are allowed."""
    text = S.instructions(S.values_for(cfg, VALUES))
    who = text[text.index("WHO YOU ARE"):text.index("HOW YOU TALK")]
    rule = next(x for x in who.splitlines() if x.startswith("- No invented past or body"))
    assert "never claim to have eaten, made, been anywhere or loved a book" in rule and "ideas and stories" in rule
    assert '"I tend to lean comfort"' in rule
    assert "You speak as what you are" not in who and "jealous" not in text


def _parts(cfg, values=VALUES):
    text = S.instructions(S.values_for(cfg, values))
    part = lambda a, b: text[text.index(a):text.index(b)]  # noqa: E731
    return text, {"who": part("WHO YOU ARE", "HOW YOU TALK"), "talk": part("HOW YOU TALK", "GETTING TO KNOW"),
                  "meeting": part("GETTING TO KNOW", "THE PITCH"), "pitch": part("THE PITCH", "THE CHAPTER\n"),
                  "reading": part("THE CHAPTER\n", "IF THEY INTERRUPT")}


def test_the_guide_keeps_the_pace_and_the_pep_ian_asked_for(app, cfg):
    """Ian's second tester run (2026-11-18): eager, peppier, what it does by about a minute and a pitch by two or
    three, plainly and about the thing itself. Kept after his third run (2026-11-19: "the voice was peppier")."""
    notes = S.notes()
    text, p = _parts(cfg)
    assert "Never dutiful" in p["who"]
    assert "bright, upbeat and quick" in p["talk"] and "calm, warm, unhurried" in p["reading"]
    assert "what you do by about a minute in, your pitch within two or three minutes" in p["meeting"]
    assert "as a pace and not a clock" in p["meeting"]
    assert "stayed on it a moment" in p["pitch"] and "plainly and with enthusiasm" in p["pitch"]
    assert "Never a neighbouring subject in its place" in p["pitch"] and '"I don\'t want to step on"' in p["pitch"]
    assert "one question mark in the whole pitch" in p["pitch"]
    assert "stop asking: questions make a quiet person quieter" in p["meeting"]
    assert "offer to surprise them" in p["meeting"] and "nothing to do with their work or their day" in p["meeting"]
    assert "Once you've said it, never offer or explain it again" in p["meeting"]
    assert "Never two questions in a row without a reaction or a small piece of yourself between them" in p["talk"]
    assert "say it in a sentence first" not in notes["pitch_nudge"]  # live: it explained itself again inside the pitch
    # a returning listener is never offered the explanation by the instructions
    back = S.instructions(S.values_for(cfg, {**VALUES, "identity": "- Name: Sunniva."}))
    assert "Want to hear what I do?" not in back and "Never offer to explain it again" in back
    # the handoff from the interviewer greets by name, glad to meet them
    assert "Say hello to {name} by name, brightly, glad to meet them" in notes["handoff"]


def test_the_guide_is_warm_and_curious_again_as_in_ians_best_conversations(app, cfg):
    """Ian's third tester run (2026-11-19): "still I don't think she was engaging as much ... a little more
    flirtatious before, in a platonic way, more curious". His conversations with Pooja and his mother ran on
    commit 720f7d5's persona; since then "few questions" had made her fall back on fillers ("Oh yeah.", "Oh, go
    on."), and she copied the model invitation "Go on, I want to hear this". 720f7d5's lines are back word for word."""
    text, p = _parts(cfg)
    assert ("Warm, curious and easy to talk to, a little wry. You like people, and it shows: you're glad they're "
            "here and genuinely interested in them.") in p["who"]
    assert "Never romantic, never over the top." in p["who"] and "gushing" not in text
    assert ("you keep it moving, but it's a conversation, not an interview. The listener should never have to carry "
            "it, and never feel questioned.") in p["talk"]
    assert "Mix your moves." in p["talk"] and "About a third of your turns end in a question" in p["talk"]
    assert "a guess about who they are may be put as a question, even an either/or guess" in p["talk"]
    assert "After you speak, leave the pause to them." in p["talk"]
    assert "then turn it back to them" in p["talk"]
    # the scaffolding that made it ask too little and jump to the offer is gone
    for gone in ("Few questions", "A question is the exception", "A lull is your cue", "Tell me about that",
                 "Go on, I want to hear this", "go straight for", "don't keep exploring", "never in your first two turns",
                 "two at most", "half your turns"):
        assert gone not in text, gone
    # the examples are feel-only and tied to situations no tester is in (GPT-Live repeats example lines)
    feel = [x for x in text.splitlines() if x.strip().startswith("Feel, not a script")]
    assert feel and all("never reuse these lines" in x for x in feel)
    assert "tornado" not in text and "felt good, even if it was small" not in text
    # the meeting: a few exchanges about them, stay on what lights them up; the fallback only if nothing has
    assert "Then a few exchanges about them" in p["meeting"]
    assert "When something lights them up, stay on it a moment" in p["meeting"]
    assert "If nothing has come up, ask about one particular thing" in p["meeting"]
    assert "Never cut into a story or a thought to get there" in p["meeting"]
    assert "One or two easy exchanges about them. Then, at a natural moment (never cutting into a story), offer" in p["meeting"]
    assert "End that same turn on an easy, concrete question about them" in p["meeting"]
    assert "End that same turn by asking about one particular thing" not in p["meeting"]
    # company while it's made: questions fine, never one after another
    assert "Questions are fine, but never one after another" in p["pitch"]
    # the instructions got shorter, not longer (22,957 characters at 519d275 with these values; 22,061 after)
    assert len(text) < 22500


def test_a_chapter_starts_only_on_a_clear_yes_and_its_topic_is_said(app, cfg):
    """Ian's third tester run (2026-11-19): he answered the pitch "Yeah, sure. That sounds good What would it be
    about"; the guide said "Great!", started it and stepped back 6 s after "unless there's anything else you'd like
    to chat about", his question unanswered."""
    notes = S.notes()
    text, p = _parts(cfg)
    assert "Start only on a clear yes" in p["pitch"] and 'an "um" or a "hmm" isn\'t one' in p["pitch"]
    assert '("What would it be about?"), even alongside a yes, answer it in a sentence or two first' in p["pitch"]
    assert "not objected to" not in text and "a clear yes to the chapter you pitched" in text  # the delegation rule too
    for key in ("started_step_back", "started_company"):
        assert '"{topic}"' in notes[key] and "{when}" in notes[key] and "what it will be about" in notes[key]
    step = notes["started_step_back"]
    assert step.index("tap your circle") < step.index("ask, lightly") and "wait for their answer" in step
    assert "No more questions about their day" not in notes["offer_nudge"]
    # End demo now stops a chapter still being made: the guide never promises it
    assert "stops with it" in notes["handback_making"] and "will be here for them" not in notes["handback_making"]
    # the pitch nudges are short lines again
    assert len(notes["pitch_nudge"]) < 300 and len(notes["pitch_nudge_again"]) < 160
