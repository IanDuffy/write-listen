"""overnight/live.py's session bookkeeping, with GPT-Live's session opening faked and its clock replaced:
the billed seconds, stale sessions, an aside's seeds and questions into seeds.jsonl, a replan that fails,
and the conversation recordings."""

import json
import time as real_time
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient
from overnight_plan_fakes import offline  # noqa: F401

import server
from overnight import library, live, planner, profiles, schedule
from stages import memory


@pytest.fixture
def app(cfg, monkeypatch, own_listeners):
    cfg.pop("listener_dir", None)
    listener = profiles.create("ian")
    profiles.set_active("ian")
    monkeypatch.setattr(live, "create_webrtc", lambda cfg, session, sdp, client=None: {"id": "live_1", "sdp": "answer"})
    passes = []
    monkeypatch.setattr(memory, "after_session", lambda cfg, path: passes.append(path) or False)
    clock = [1000.0]
    monkeypatch.setattr(live, "time", NS(monotonic=lambda: clock[0], strftime=real_time.strftime))
    c = TestClient(server.create_app(server.Pipeline(), cfg, remember_on_start=False))
    return NS(c=c, listener=listener, passes=passes, clock=clock, cfg=cfg)


def log(app):
    return [json.loads(x) for x in (app.listener / "sessions" / "log.jsonl").read_text().splitlines()]


def chapter(app):
    import ask_fixture
    root = app.listener / "chapters" / "2026-11-17-why-herons-wait"
    ask_fixture.build(root, audio=True)
    return root


def test_the_billed_seconds_are_the_wall_clock_when_the_page_reports_less(app):
    """S2: the page's last usage figure lags (its session.closed never arrives), so the server's clock counts."""
    s = app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"}).json()
    app.clock[0] += 70
    end = app.c.post("/api/overnight/live/end", json={"session": s["session"], "seconds": 40}).json()
    assert end["cost_usd"] == pytest.approx(70 / 60 * 0.05, abs=1e-5)
    assert log(app)[-1]["seconds"] == 70 and log(app)[-1]["page_seconds"] == 40
    s = app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"}).json()
    app.clock[0] += 5
    app.c.post("/api/overnight/live/end", json={"session": s["session"], "seconds": 9})
    assert log(app)[-1]["seconds"] == 9  # the page's figure when it is the larger


def test_a_session_never_ended_is_ended_when_the_next_starts(app):
    """C13: a closed tab whose beacon was lost: logged up to its last line once quiet for stale_minutes."""
    a = app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"}).json()
    app.clock[0] += 10
    app.c.post("/api/overnight/live/line", json={"session": a["session"], "who": "listener", "text": "Something on tides"})
    app.clock[0] += 29 * 60
    app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"})
    assert not (app.listener / "sessions" / "log.jsonl").exists()  # quiet, not yet stale
    app.clock[0] += 2 * 60
    app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"})
    ended = [x for x in log(app) if x["session"] == a["session"]]
    assert len(ended) == 1 and ended[0]["ended"] == "stale" and ended[0]["seconds"] == 10
    real_time.sleep(0.05)
    assert any(p.name == f"{a['session']}.jsonl" for p in app.passes)  # its memory pass still runs
    assert app.c.post("/api/overnight/live/end", json={"session": a["session"]}).status_code == 409


def test_an_aside_adds_its_saved_topics_and_the_listeners_questions_to_seeds(app):
    """C15: after_chapter ran at build time, so a morning aside's seeds go to seeds.jsonl when it ends. (With the
    after-conversation pass on for asks, the pass writes the questions instead: test_library_capture.py.)"""
    app.cfg["overnight"]["live"]["memory_on_asks"] = False
    root = chapter(app)
    s = app.c.post("/api/overnight/live/start", json={"mode": "ask", "sdp": "offer", "run": root.name, "t": 20}).json()
    for text in ("Wait, why do herons stand so still?", "Huh, OK"):
        app.c.post("/api/overnight/live/line", json={"session": s["session"], "who": "listener", "text": text})
    app.c.post("/api/overnight/live/tool", json={"session": s["session"], "name": "save_seed",
                                                 "args": {"topic": "Heron eyesight", "why": "they asked"}})
    app.c.post("/api/overnight/live/end", json={"session": s["session"], "seconds": 12})
    seeds = {x["kind"]: x for x in library.seeds(app.listener)}
    assert seeds["question"]["text"] == "Wait, why do herons stand so still?"
    assert seeds["question"]["words"] == "Wait, why do herons stand so still?"
    assert seeds["idea"]["text"] == "Heron eyesight (they asked)" and seeds["idea"]["words"] == "Huh, OK"
    assert seeds["idea"]["source"]["chapter"] == root.name and seeds["idea"]["source"]["session"] == s["session"]
    assert len(library.seeds(app.listener)) == 2  # "Huh, OK" is no question


def test_an_aside_abandoned_before_anything_was_said_keeps_no_aside(app):
    """C9: a tap back while it connected ends the server's session; nothing was said, so nothing is kept."""
    from ask import aside as A
    root = chapter(app)
    s = app.c.post("/api/overnight/live/start", json={"mode": "ask", "sdp": "offer", "run": root.name, "t": 20}).json()
    end = app.c.post("/api/overnight/live/end", json={"session": s["session"], "how": "abandoned"}).json()
    assert end["restart"] and A.load_asides(root) == [] and log(app)[-1]["ended"] == "abandoned"


def test_a_replan_that_fails_queues_the_request(app, monkeypatch):
    monkeypatch.setattr(schedule, "can_change", lambda card, settings, now: (True, ""))

    def fail(*a, **k):
        raise RuntimeError("the planner fell over")
    monkeypatch.setattr(planner, "plan", fail)
    s = app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"}).json()
    out = app.c.post("/api/overnight/live/tool", json={"session": s["session"], "name": "set_next_chapter",
                                                         "args": {"mode": "topic", "topic": "Tides", "notes": "short"}}).json()
    assert out["changing"] is True
    for _ in range(100):
        if (app.listener / "queue.jsonl").exists():
            break
        real_time.sleep(0.02)
    q = profiles.read_jsonl(app.listener / "queue.jsonl")
    assert q[-1]["topic"] == "Tides" and q[-1]["notes"] == "short" and q[-1]["session"] == s["session"]


def test_a_conversations_audio_is_kept_beside_its_transcript(app):
    """C29: the page uploads its recording after /end."""
    s = app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"}).json()
    app.c.post("/api/overnight/live/end", json={"session": s["session"], "seconds": 3})
    sid = s["session"]
    r = app.c.post(f"/api/overnight/live/audio/{sid}", content=b"webm-bytes", headers={"Content-Type": "audio/webm;codecs=opus"})
    assert r.json() == {"saved": f"{sid}.webm", "bytes": 10}
    assert (app.listener / "sessions" / f"{sid}.webm").read_bytes() == b"webm-bytes"
    assert app.c.post(f"/api/overnight/live/audio/{sid}", content=b"").status_code == 400
    assert app.c.post("/api/overnight/live/audio/20991231-000000", content=b"x").status_code == 404
    assert app.c.post("/api/overnight/live/audio/..%2Fprofile", content=b"x").status_code == 404


# --- round 2: every session knows the situation; notes for the next chapter ---------------------------------

@pytest.fixture
def opened(app, monkeypatch):
    got = []
    monkeypatch.setattr(live, "create_webrtc", lambda cfg, session, sdp, client=None:
                        got.append(session) or {"id": f"live_{len(got)}", "sdp": "answer"})
    return got


def plan_card(listener, **over):
    data = {"id": "plan-1", "for_morning": "2026-11-18", "title": "Why Herons Wait", "overview": "Patience.",
            "summary": "Why do herons stand still so long?", "mode": "surprise", "decided": "branch",
            "source": {"kind": "seed", "ref": "s-1", "words": "why so still"}, "research_questions": ["Why?"],
            "angle": "One morning.", "status": "planned", "chapter": None, **over}
    profiles.write_json(listener / "up_next.json", data)
    return data


def known_listener(app):
    """A listener the guide has met: guide.md with its name, a finished chapter in history.md, a past conversation."""
    (app.listener / "guide.md").write_text("# The guide\n- Name: Wren. Chose it for the bird.\n- Met: 2026-11-12.\n")
    (app.listener / "history.md").write_text("# Chapters made so far\n\n## Concrete That Heals Itself "
                                             "(concrete-20261112, 2026-11-12)\n\n- Angle: Lime clasts mend it.\n")
    root = chapter(app)
    (app.listener / "sessions").mkdir(exist_ok=True)
    (app.listener / "sessions" / "20261114-080000.jsonl").write_text("\n".join(json.dumps(x) for x in [
        {"who": "session", "mode": "intro", "run": root.name},
        {"who": "guide", "text": "Morning. This one's about herons."},
        {"who": "listener", "text": "Lovely, I watched one on the river yesterday."}]) + "\n")
    return root


GREETED_AS_NEW = ("good to meet you", "nice to meet you")


def test_change_opens_with_why_they_are_here_for_a_returning_listener(app, opened, monkeypatch):
    """Ian, round 2: Change said 'hey, good to meet you, today I have...' to a listener with chapters."""
    known_listener(app)
    plan_card(app.listener)
    monkeypatch.setattr(schedule, "can_change", lambda card, settings, now: (True, ""))
    s = app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"}).json()
    text = opened[-1]["instructions"]
    assert "WHY THIS SESSION OPENED" in text and 'They tapped Change on the Up next card' in text
    assert '"Why Herons Wait"' in text and "ask what they'd like instead" in text
    assert "This is not a first meeting" in text and "your name with them is Wren" in text
    assert "you've made them 1 chapter" in text and "you've talked with them 1 time before" in text
    assert 'the latest chapter was "Concrete That Heals Itself"' in text
    assert "listener: Lovely, I watched one on the river yesterday." in text  # {{recent}}
    assert "(concrete-20261112, 2026-11-12)" in text  # the latest history entry
    assert "This is your first meeting" not in text and "{" not in text.replace("{{", "")
    opening = s["notes"]["change_opened"].lower()
    assert "hello" not in opening and "why they're here" in opening
    assert not any(g in s["notes"]["intro_opened"].lower() for g in ("say hello",))


def test_only_a_real_first_meeting_is_told_it_is_one(app, opened, monkeypatch):
    plan_card(app.listener)
    monkeypatch.setattr(schedule, "can_change", lambda card, settings, now: (True, ""))
    app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"})
    text = opened[-1]["instructions"]
    assert "This is your first meeting" in text and "Introduce yourself lightly" in text
    assert "This is not a first meeting" not in text
    # a conversation in which they only listened is not a meeting; one in which they spoke is
    sessions = app.listener / "sessions"
    (sessions / "20261114-080000.jsonl").write_text(json.dumps({"who": "guide", "text": "Hi."}) + "\n")
    app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"})
    assert "This is your first meeting" in opened[-1]["instructions"]
    (sessions / "20261114-080000.jsonl").write_text(json.dumps({"who": "listener", "text": "Hello?"}) + "\n")
    app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"})
    text = opened[-1]["instructions"]
    assert "This is not a first meeting" in text and "no name of yours is on record" in text
    assert "don't introduce yourself" in text.split("<identity>")[1].split("</identity>")[0]


def test_change_after_the_cutoff_and_with_no_plan_say_so_first(app, opened, monkeypatch):
    known_listener(app)
    plan_card(app.listener)
    monkeypatch.setattr(schedule, "can_change", lambda card, settings, now: (False, "Tonight's chapter is already being made, so a change goes to the next build day."))
    app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"})
    assert "can't change any more (tonight's chapter is already being made" in opened[-1]["instructions"]
    (app.listener / "up_next.json").unlink()
    app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"})
    assert "where nothing is planned yet" in opened[-1]["instructions"]


def test_the_intro_greets_a_returning_listener_by_the_time_of_day(app, opened):
    root = known_listener(app)
    (root / "intro_brief.md").write_text("## Title\n\nWhy Herons Wait\n\n## Findings you may mention\n\n- One.\n")
    s = app.c.post("/api/overnight/live/start", json={"mode": "intro", "sdp": "offer", "run": root.name}).json()
    text = opened[-1]["instructions"]
    assert "They just pressed Play on the newest chapter you made them" in text
    assert "fits the time of day, as someone you know" in text and "This is not a first meeting" in text
    assert "say hello" not in s["notes"]["intro_opened"].lower()


def test_an_aside_just_listens(app, opened):
    root = known_listener(app)
    app.c.post("/api/overnight/live/start", json={"mode": "ask", "sdp": "offer", "run": root.name, "t": 65})
    text = opened[-1]["instructions"]
    assert "They tapped to ask you something while the recording of" in text and "paused 1:05 in" in text
    assert "Don't greet them: they speak first." in text and "This is not a first meeting" in text
    assert "your name with them is Wren" in text and "Name: Wren" in text  # guide.md as its identity


def test_a_note_on_a_planned_card_before_the_cutoff_goes_on_the_card(app, monkeypatch):
    plan_card(app.listener)
    monkeypatch.setattr(schedule, "can_change", lambda card, settings, now: (True, ""))
    out = app.c.post("/api/overnight/note", json={"text": "  More about the fishermen,\n please  "}).json()
    assert out["where"] == "card" and out["note"]["text"] == "More about the fishermen, please"
    assert out["note"]["via"] == "typed" and out["message"].startswith("Added to the plan for")
    card = profiles.read_json(app.listener / "up_next.json")
    assert [n["text"] for n in card["notes_added"]] == ["More about the fishermen, please"]
    seed = planner.seed_text(card)
    assert "## Notes the listener added to this plan" in seed and "- More about the fishermen, please" in seed
    assert app.c.post("/api/overnight/note", json={"text": "   "}).status_code == 400
    assert app.c.post("/api/overnight/note", json={}).status_code == 400


def test_the_card_message_names_whose_chapter(app):
    from datetime import datetime
    plan_card(app.listener, for_morning="2026-11-18")
    out = live.add_note(app.cfg, "short on dates", now=datetime(2026, 11, 17, 19, 0))
    assert out == {"where": "card", "note": out["note"], "message": "Added to the plan for tomorrow's chapter."}


def test_a_note_while_research_runs_goes_to_tonights_chapter(app, monkeypatch):
    import os
    name = "2026-11-18-why-herons-wait"
    plan_card(app.listener, status="building", chapter=name)
    root = app.listener / "chapters" / name
    (root / "00_source").mkdir(parents=True)
    profiles.write_json(root / "status.json", {"state": "making", "pid": os.getpid()})  # alive
    out = app.c.post("/api/overnight/note", json={"text": "Mention the grey heron colony"}).json()
    assert out["where"] == "tonight" and "tonight's chapter" in out["message"]
    notes = (root / "00_source" / "notes.md").read_text()
    assert notes.startswith("## Added during the conversation") and "- Mention the grey heron colony" in notes
    assert profiles.read_json(app.listener / "up_next.json")["notes_added"][0]["text"] == "Mention the grey heron colony"
    (root / "04_research").mkdir()
    (root / "04_research" / "ledger_update.md").write_text("done")  # research finished: too late for tonight
    out = app.c.post("/api/overnight/note", json={"text": "And egrets"}).json()
    assert out["where"] == "next" and "the one after" in out["message"]
    assert [n["text"] for n in library.next_notes(app.listener)] == ["And egrets"]
    assert "And egrets" not in (root / "00_source" / "notes.md").read_text()


def test_a_note_with_no_card_waits_for_the_next_plan(app):
    out = app.c.post("/api/overnight/note", json={"text": "Something on tides"}).json()
    assert out["where"] == "next" and out["message"] == "Saved for the next chapter you plan."


def test_a_note_under_a_failed_card_goes_to_its_retry_while_research_is_unfinished(app):
    """R12: the failed card is Up next and is retried with the same chapter folder: a note typed under it
    reaches that retry (notes.md, the card) unless its research is done; then it goes to the one after."""
    name = "2026-11-18-why-herons-wait"
    plan_card(app.listener, status="building", chapter=name)  # no live build: reconcile marks it failed
    root = app.listener / "chapters" / name
    (root / "00_source").mkdir(parents=True)
    out = app.c.post("/api/overnight/note", json={"text": "and the moon"}).json()
    assert profiles.read_json(app.listener / "up_next.json")["status"] == "failed"
    assert out["where"] == "tonight" and "didn't finish" in out["message"]
    assert "- and the moon" in (root / "00_source" / "notes.md").read_text()
    assert [n["text"] for n in profiles.read_json(app.listener / "up_next.json")["notes_added"]] == ["and the moon"]
    assert library.next_notes(app.listener) == []
    (root / "04_research").mkdir()
    (root / "04_research" / "ledger_update.md").write_text("done")
    out = app.c.post("/api/overnight/note", json={"text": "and the tides"}).json()
    assert out["where"] == "next" and "goes ahead without this note" in out["message"]
    assert [n["text"] for n in library.next_notes(app.listener)] == ["and the tides"]
    assert "and the tides" not in (root / "00_source" / "notes.md").read_text()


def test_a_note_under_a_card_that_failed_before_its_chapter_started_goes_on_the_card(app):
    """A preflight failure leaves no chapter: the retry's lock writes seed.md from the card's notes_added."""
    plan_card(app.listener, status="failed", chapter=None, error="preflight: no model")
    out = app.c.post("/api/overnight/note", json={"text": "and the moon"}).json()
    card = profiles.read_json(app.listener / "up_next.json")
    assert out["where"] == "card" and [n["text"] for n in card["notes_added"]] == ["and the moon"]
    assert "- and the moon" in planner.seed_text(card)


def test_a_note_for_the_next_chapter_is_saved_under_the_card_lock(app, monkeypatch):
    """R13: the planner reads next_notes under the card lock; the append happens under it too, so no note
    falls between a pass's read and its write, and two notes in one second never share an id."""
    from overnight import locks
    held = []
    real = library.add_next_note

    def add(listener, text, via="typed"):
        held.append(locks._held.depth.get(str(listener.resolve()), 0))
        return real(listener, text, via)
    monkeypatch.setattr(library, "add_next_note", add)
    app.c.post("/api/overnight/note", json={"text": "tides"})
    assert held == [1]
    import threading
    ts = [threading.Thread(target=live.add_note, args=(app.cfg, f"note {i}")) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len({n["id"] for n in library.next_notes(app.listener)}) == 5


def test_change_keep_with_notes_adds_them_by_voice(app, opened, monkeypatch):
    plan_card(app.listener)
    monkeypatch.setattr(schedule, "can_change", lambda card, settings, now: (True, ""))
    monkeypatch.setattr(planner, "plan", lambda *a, **k: pytest.fail("keep never replans"))
    s = app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"}).json()
    out = app.c.post("/api/overnight/live/tool", json={"session": s["session"], "name": "set_next_chapter",
                                                         "args": {"mode": "keep", "notes": "but more on the fishermen"}}).json()
    assert out["kept"] is True and out["added"] == "card" and out["note"].startswith("Added to the plan")
    n = profiles.read_json(app.listener / "up_next.json")["notes_added"]
    assert [(x["text"], x["via"]) for x in n] == [("but more on the fishermen", "voice")]
    assert app.c.post("/api/overnight/live/tool", json={"session": s["session"], "name": "set_next_chapter",
                                                          "args": {"mode": "keep"}}).json() == {"kept": True}
    # the next Change session sees the notes on the plan
    app.c.post("/api/overnight/live/start", json={"mode": "change", "sdp": "offer"})
    assert "Notes they added to it:\n- but more on the fishermen" in opened[-1]["instructions"]


# --- review round 2: instructions within GPT-Live's limit; a session's own chapter is not "the latest" ----

def big_chapter(app, entry_chars=900, per_segment=18, others=150):
    """The ask fixture made real-sized: the brief gives each segment `per_segment` long entries (the guide
    gets the current and next segment's in full) and `others` more go one line each, as a dossier of about
    60,000 characters does in a chapter like Ian's bridges one."""
    root = known_listener(app)
    entries, brief = [], (root / "05_brief.md").read_text()
    k = 0
    for s in range(1, 4):
        nums = []
        for _ in range(per_segment):
            k += 1
            nums.append(f"D{k:02d}")
            entries.append(f"[D{k:02d}] (L0{s}, claim, tier 2, verbatim: no) " + f"Entry {k} for segment {s}: "
                           + "a long finding with every detail kept " * (entry_chars // 38) + f"Source: https://example.org/{k}")
        first = (s - 1) * 3 + 1
        brief = brief.replace("- **Dossier:** " + ", ".join(f"D{x:02d}" for x in range(first, first + 3)),
                              "- **Dossier:** " + ", ".join(nums))
    for _ in range(others):
        k += 1
        entries.append(f"[D{k:02d}] (L01, context, tier 3, verbatim: no) Background entry {k} in a good many words "
                       "so that its one line runs to the full twenty-four words and then some more. Source: https://e.org")
    (root / "05_brief.md").write_text(brief)
    (root / "04_dossier.md").write_text("# Dossier\n\n## Entries\n\n" + "\n\n".join(entries) + "\n")
    # a long last conversation and a long history entry for this very chapter
    (app.listener / "sessions" / "20261116-070000.jsonl").write_text("\n".join(json.dumps(
        {"who": "listener" if i % 2 else "guide", "text": f"line {i} " + "and a good deal more said " * 12})
        for i in range(10)) + "\n")
    with open(app.listener / "history.md", "a") as f:
        f.write(f"\n## Why Herons Wait ({root.name}, 2026-11-17)\n\n- Angle: SECRET TURN that herons wait "
                + "because of a long argument " * 30 + "\n- Parked, for a later chapter: egrets; cranes\n")
    return root


def test_every_ask_in_a_real_sized_chapter_fits_gpt_live(app, opened):
    """R7: on Ian's bridges chapter 18 of 79 positions raised 'too long' and Ask returned 503. Every position
    now fits; the dossier is cut (one-line entries first) only where it must be, never the current segment's."""
    from ask import aside as A
    root = big_chapter(app)
    ch = A.Chapter(root, app.cfg)
    trimmed = 0
    for t in range(0, int(ch.duration) + 1, 15):
        values = live.ask_values(app.cfg, ch, t, [])
        assert "SECRET TURN" not in values["recent"] and "(concrete-20261112" not in values["recent"]
        assert len(values["recent"].split("\n", 1)[1]) <= live.RECENT_CHARS["ask"]
        text = live.session_config(app.cfg, "ask", values)["instructions"]
        assert len(text) <= live.INSTRUCTIONS_LIMIT_CHARS
        seg = ch.sents[ch.locate(t)].seg
        assert f"Entry {seg * 18 + 1} for segment {seg + 1}:" in text  # the current segment's entries stay whole
        trimmed += "more entries left out, for room" in text
    assert trimmed  # the case the finding saw: without the cut these were refused
    s = app.c.post("/api/overnight/live/start", json={"mode": "ask", "sdp": "offer", "run": root.name, "t": 150})
    assert s.status_code == 200 and len(opened[-1]["instructions"]) <= live.INSTRUCTIONS_LIMIT_CHARS


def test_session_config_cuts_recent_before_the_dossier(app):
    values = {"identity": "x", "meeting": "m", "recent": "R" * 3000, "profile": "p", "situation": "s",
              "chapter": "c", "where": "w", "just_said": "j", "later": "l", "ledger": "", "earlier_asides": "",
              "dossier": "\n".join(f"[D{k:02d}] (L01, claim) " + "x" * 500 for k in range(1, 101))}
    bare = len(live._render("ask", {**values, "recent": "", "dossier": ""}))
    values["dossier"] = values["dossier"][:live.INSTRUCTIONS_LIMIT_CHARS - bare - 1500]  # cutting recent is enough
    assert len(live._render("ask", values)) > live.INSTRUCTIONS_LIMIT_CHARS
    text = live.session_config(app.cfg, "ask", values)["instructions"]
    assert "left out this time, for room" in text and values["dossier"] in text
    with pytest.raises(live.StageError, match="too long"):
        live.session_config(app.cfg, "intro", {**values, "brief": "b" * 60000, "now": "n", "after": "a"})


def test_the_intro_never_presents_its_own_chapter_as_the_latest(app, opened):
    """R8: history.md gets the chapter's entry when its build finishes, so at Play the latest entry is the
    chapter being introduced: its angle and turns stay out of {{recent}}, and the last chapter named is the
    one before it."""
    root = big_chapter(app)
    (root / "intro_brief.md").write_text("## Title\n\nWhy Herons Wait\n")
    app.c.post("/api/overnight/live/start", json={"mode": "intro", "sdp": "offer", "run": root.name})
    text = opened[-1]["instructions"]
    assert "SECRET TURN" not in text and f"({root.name}," not in text
    assert 'the chapter before this one was "Concrete That Heals Itself"' in text
    assert "The chapter you made them before this one:\nConcrete That Heals Itself (concrete-20261112" in text
    assert 'the latest chapter was "Why Herons Wait"' not in text
    app.c.post("/api/overnight/live/start", json={"mode": "ask", "sdp": "offer", "run": root.name, "t": 30})
    text = opened[-1]["instructions"]
    assert "SECRET TURN" not in text and "the latest chapter was" not in text and "before this one" not in text
