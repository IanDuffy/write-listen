"""Tester Demo, part D, server side: the 10-minute sizing of a fast run, the chapter card (faststart/card.py and
/api/live/card), the handoff into the guide's first session (/api/live/start's `handoff`), and the library's
debrief card (library_state's `debrief`). Nothing reaches a model or OpenAI."""

import json

import pytest
from fastapi.testclient import TestClient

import server
from fakes import FakeAnthropic, Responder, all_text
from faststart import base, card, outline
from overnight import routes as lib_routes
from stages import guide, llm
from stages.common import Run, StageError, _merge
from voice.gpt_live import session as S

CARD = {"title": "Why Roman harbors heal", "overview": "How a concrete set in seawater got stronger for two thousand years.",
        "summary": "You asked why your slabs crack. This chapter takes on why Rome's did not. It asks what the "
                   "builders knew, and what we forgot."}


def sized(cfg, minutes=10):
    return _merge(cfg, {"fast": base.sized_overrides(minutes)})


# --- 1. the sizing -----------------------------------------------------------------------------------

def test_ten_minutes_has_the_contract_values():
    o = base.sized_overrides(10)
    assert o["target_minutes"] == 10 and o["total_minutes"] == [9, 12] and o["sections"] == [2, 3]
    assert o["opening_min_minutes"] == 4 and o["section_min_minutes"] == 2
    assert o["research"] == {"per_row": {"searches": 3, "fetches": 1}, "max_searches": 15, "row_timeout_seconds": 300}
    assert o["scout"] == {"searches": 4, "fetches": 2} and o["max_run_usd"] == 4


def test_twenty_minutes_gives_back_config_yamls_own_values(cfg):
    f = cfg["fast"]
    o = base.sized_overrides(f["target_minutes"])
    assert {k: o[k] for k in ("target_minutes", "total_minutes", "sections", "opening_min_minutes",
                              "section_min_minutes", "max_run_usd", "scout")} == \
        {k: f[k] for k in ("target_minutes", "total_minutes", "sections", "opening_min_minutes",
                           "section_min_minutes", "max_run_usd", "scout")}
    assert o["research"]["per_row"] == f["research"]["per_row"]
    assert o["research"]["max_searches"] == f["research"]["max_searches"]
    assert o["research"]["row_timeout_seconds"] == f["research"]["row_timeout_seconds"]


def short_outline(words: list[int]) -> dict:
    """A 10-minute outline: the opening from scout entries, then one or two questions a section."""
    secs = [{"n": 1, "title": "Forms in the sea", "lands": "Rome built a harbor in open water.", "sets_up": "The powder.",
             "hand_off": "What was in the powder?", "target_words": words[0], "notes": "Open at Caesarea.",
             "scout_entries": [1, 2], "research_questions": []}]
    for k, w in enumerate(words[1:], 2):
        secs.append({"n": k, "title": f"Part {k}", "lands": f"What section {k} settles.", "sets_up": "x",
                     "hand_off": "y", "target_words": w, "notes": "z", "scout_entries": [],
                     "research_questions": [{"idea": "i", "question": "q", "type": "deep-dive", "tier_needed": 2,
                                             "scout_has": ""}] * (2 if k == 2 else 1)})
    return {"title": "The harbor", "angle": "a", "why": "w", "through_line": "t", "closing_idea": "c",
            "listener_words": [], "sections": secs}


@pytest.mark.parametrize("words", [[800, 750], [650, 450, 450]])
def test_two_and_three_section_ten_minute_outlines_validate(cfg, words):
    c = sized(cfg)
    assert outline.validate(short_outline(words), {1, 2, 3}, c) == []
    lim = outline.limits(c)
    assert (lim["min_sections"], lim["max_sections"]) == (2, 3)
    assert (lim["opening_min_words"], lim["section_min_words"], lim["min_words"], lim["max_words"]) == (600, 300, 1350, 1800)
    assert lim["max_questions"] == 5


def test_ten_minute_rules_still_refuse_what_they_should(cfg):
    c = sized(cfg)
    assert any("needs 2 to 3" in e for e in outline.validate(short_outline([700, 400, 400, 300]), {1, 2}, c))
    assert any("the opening needs at least 600" in e for e in outline.validate(short_outline([500, 1000]), {1, 2}, c))
    assert any("between 1350 and 1800" in e for e in outline.validate(short_outline([1000, 1000]), {1, 2}, c))
    # the 20-minute rules make a 10-minute outline invalid: why the overrides exist
    assert outline.validate(short_outline([800, 750]), {1, 2}, cfg)


def test_repair_targets_brings_a_short_outline_into_the_ten_minute_range(cfg):
    c = sized(cfg)
    fixed = outline.repair_targets(short_outline([1500, 900]), c)
    assert outline.validate(fixed, {1, 2}, c) == []


# --- 2. the chapter card -----------------------------------------------------------------------------

class CardFake(Responder):
    def __call__(self, kwargs):
        if "Write the card for a narrated audio chapter" in all_text(kwargs):
            self.calls.append(kwargs)
            return self.msg(kwargs, json.dumps(CARD))
        return super().__call__(kwargs)


def seed_run(tmp_path, minutes=None):
    from stages import seed
    r = Run.open("roman-concrete-20261116", create=True, runs_dir=tmp_path / "runs")
    seed.write(r, topic="Why Roman concrete lasted", said="Ours cracks in thirty years.", why="It is my trade.")
    if minutes:
        import yaml
        (r.root / "config.yaml").write_text(yaml.safe_dump({"fast": base.sized_overrides(minutes)}))
    return r


def test_the_card_is_one_call_from_the_seed_written_and_logged(cfg, tmp_path, monkeypatch):
    fake = CardFake()
    monkeypatch.setattr(llm, "_client", FakeAnthropic(fake))
    cfg["listener_dir"] = str(tmp_path / "listener")
    r = seed_run(tmp_path, minutes=10)
    out = card.write(r.root, cfg)
    assert out["title"] == CARD["title"] and out["run"] == r.name
    assert json.loads((r.root / "fast" / "card.json").read_text())["summary"] == CARD["summary"]
    [call] = fake.calls
    text = all_text(call)
    assert "about 10 minutes" in text and "Why Roman concrete lasted" in text and "Ours cracks in thirty years." in text
    assert call["model"] == cfg["stages"]["fast_card"]["model"]
    assert call["output_config"]["format"]["schema"] == card.SCHEMA
    logged = [x for x in r.read_log() if x.get("stage") == "fast_card"]
    assert len(logged) == 1 and logged[0]["cost_usd"] > 0


def test_a_card_missing_a_field_is_an_error_and_writes_nothing(cfg, tmp_path, monkeypatch):
    class Empty(CardFake):
        def __call__(self, kwargs):
            return self.msg(kwargs, json.dumps({**CARD, "overview": "  "}))
    monkeypatch.setattr(llm, "_client", FakeAnthropic(Empty()))
    cfg["listener_dir"] = str(tmp_path / "listener")
    r = seed_run(tmp_path)
    with pytest.raises(StageError, match="overview"):
        card.write(r.root, cfg)
    assert not (r.root / "fast" / "card.json").exists()


def test_start_runs_once_in_a_thread_and_never_raises(cfg, tmp_path, monkeypatch):
    calls = []

    def fake_write(root, c):
        calls.append(root)
        raise RuntimeError("model down")
    monkeypatch.setattr(card, "write", fake_write)
    r = seed_run(tmp_path)
    t = card.start(r.root, cfg)
    t.join(5)
    assert calls == [r.root]  # failed quietly
    base.write_json(r.root / card.CARD, {**CARD, "run": r.name})
    assert card.start(r.root, cfg) is None  # a run with a card already: nothing more


def test_read_gives_the_outlines_title_once_there_is_one(tmp_path):
    root = tmp_path / "run"
    assert card.read(root) is None
    base.write_json(root / card.CARD, {**CARD, "run": "run"})
    got = card.read(root)
    assert got["title"] == CARD["title"] and not got["outlined"] and got["summary"] == CARD["summary"]
    base.write_json(root / "05_outline.json", {"title": "The harbor that heals itself", "sections": []})
    got = card.read(root)
    assert got["title"] == "The harbor that heals itself" and got["card_title"] == CARD["title"] and got["outlined"]


# --- routes ------------------------------------------------------------------------------------------

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
    monkeypatch.setattr(pipe, "start", lambda args, run: setattr(pipe, "run", run))
    return TestClient(server.create_app(pipe, cfg, remember_on_start=False)), offers, runs


def transcript(cfg, sid):
    return [json.loads(x) for x in (guide.listener_dir(cfg) / "sessions" / f"{sid}.jsonl").read_text().splitlines()]


def test_the_card_route(app):
    c, _, runs = app
    root = runs / "live-run"
    root.mkdir()
    assert c.get("/api/live/card/live-run").status_code == 404
    assert c.get("/api/live/card/nope").status_code == 404
    base.write_json(root / card.CARD, {**CARD, "run": "live-run"})
    assert c.get("/api/live/card/live-run").json()["title"] == CARD["title"]
    base.write_json(root / "05_outline.json", {"title": "The harbor", "sections": []})
    got = c.get("/api/live/card/live-run").json()
    assert got["title"] == "The harbor" and got["outlined"] and got["overview"] == CARD["overview"]


def test_a_handoff_session_opens_with_nothing_sent_or_recorded_and_is_no_meeting(app, cfg):
    """Ian's notes (2026-11-16): the guide's session is opened a moment ahead of the handover, so the server
    sends it no first message (the page says who they are, with the name as known at the handover) and records
    no opening. Left unused (the tester went during the intro), it is not a conversation they spoke in: the
    guide still meets them for the first time."""
    from stages import memory
    c, offers, _ = app
    res = c.post("/api/live/start", json={"sdp": "OFFER", "handoff": {"name": ""}}).json()
    assert res["handoff"] is True
    assert offers[0][0]["input"] == []
    assert not [x for x in transcript(cfg, res["session"]) if x["who"] in ("page", "listener", "guide")]
    c.post("/api/chat/end", json={"session": res["session"]})
    assert memory.conversations(cfg) == [] and memory.meeting(cfg)["first"] is True
    # at the handover the page records what it told the guide
    res = c.post("/api/live/start", json={"sdp": "OFFER2", "handoff": {"name": "Purnima"}}).json()
    want = S.notes()["handoff"].replace("{name}", "Purnima")
    assert "Say hello to Purnima by name" in want and "Don't ask their name again" in want
    c.post("/api/live/line", json={"session": res["session"], "who": "page", "text": want})
    assert [x["text"] for x in transcript(cfg, res["session"]) if x["who"] == "page"] == [want]


def test_an_ordinary_start_is_unchanged(app, cfg):
    c, offers, _ = app
    res = c.post("/api/live/start", json={"sdp": "OFFER"}).json()
    assert res["handoff"] is False and offers[0][0]["input"] == []
    assert [x["text"] for x in transcript(cfg, res["session"]) if x["who"] == "page"] == [S.notes()["opened"]]


def test_the_handback_notes_exist():
    n = S.notes()
    assert "handing them back to the person who welcomed them" in n["handback_end"]
    assert "I'm done" in n["handback_done"] and "Stop reading now" in n["handback_done"]
    assert "{name}" in n["handoff"]


# --- 6. the library's debrief card -------------------------------------------------------------------

@pytest.mark.parametrize("tester, debrief, chapter, show", [
    (None, None, True, False),                                 # not a tester: never
    ({"debrief_card": "auto"}, None, False, False),            # auto: not before a chapter was started
    ({"debrief_card": "auto"}, None, True, True),
    ({}, None, True, True),                                    # auto is the default
    ({"debrief_card": "auto"}, {"done": True}, True, False),   # done: gone
    ({"debrief_card": "off"}, None, True, False),              # Ian turned it off
    ({"debrief_card": "on"}, None, False, True),               # Ian turned it on
    ({"debrief_card": "on"}, {"done": True}, False, False),    # the card stays until the debrief is done
])
def test_the_debrief_card(tmp_path, tester, debrief, chapter, show):
    lis = tmp_path / "listeners" / "t-pilot-1"
    lis.mkdir(parents=True)
    if tester is not None:
        (lis / "tester.json").write_text(json.dumps({"label": "Pilot 1", "lid": "t-pilot-1", **tester}))
    if debrief is not None:
        (lis / "study").mkdir()
        (lis / "study" / "debrief.json").write_text(json.dumps(debrief))
    if chapter:
        (lis / "chapters" / "roman-concrete-20261116").mkdir(parents=True)
    got = lib_routes.debrief_card(lis)
    assert got["show"] is show
    if tester is not None:
        assert got["line"] == "Got five minutes to tell Ian how it went?"


def test_library_state_carries_the_debrief_field(cfg, tmp_path):
    lis = tmp_path / "listener"
    lis.mkdir()
    cfg["listener_dir"] = str(lis)
    assert lib_routes.library_state(cfg)["debrief"] == {"show": False, "line": ""}
    (lis / "tester.json").write_text(json.dumps({"debrief_card": "on"}))
    assert lib_routes.library_state(cfg)["debrief"]["show"] is True
