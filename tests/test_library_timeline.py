"""The library timeline's data (docs/library-timeline.md; overnight/timeline.py): moments.jsonl, playback.json,
the outcomes the planner sets, and the reader the two screens use."""

import json
import threading
from datetime import date

from fastapi.testclient import TestClient

from overnight import library, planner, profiles, timeline


def chapter(listener, name, title, *, minutes=20, audio=False, topics=None, card_id=None, finished=True):
    root = listener / "chapters" / name
    (root / "00_source").mkdir(parents=True)
    card = {"title": title, "overview": f"About {title}.", "summary": f"{title}: the summary.", "minutes": minutes}
    if topics:
        card["topics"] = topics
    if card_id:
        card["id"] = card_id
    profiles.write_json(root / "00_source" / "up_next.json", card)
    if audio:
        (root / "10_audio").mkdir()
        (root / "10_audio" / "chapter.mp3").write_bytes(b"ID3")
    if finished:
        profiles.write_json(root / "finished.json", {"t": f"{name[:10]}T06:00:00"})
    return root


# --- moments ------------------------------------------------------------------------------

def test_moments_get_ids_in_order_and_are_not_added_twice(tmp_path):
    root = tmp_path / "ch"
    a = timeline.add_moments(root, [
        {"kind": "question", "position_s": 402, "text": "Why do they stand on one leg?", "session": "s1",
         "outcome": "answered", "reply": "To keep warm."},
        {"kind": "mention", "position_s": 402, "text": "Upstream, Mary Oliver", "session": "s1",
         "outcome": "in_library", "outcome_ref": "w-1", "title": "Upstream", "creator": "Mary Oliver"},
        {"kind": "note", "position_s": 90, "text": "  save   this ", "session": "s0", "outcome": "saved", "seed": "s-9"},
        {"kind": "nonsense", "text": "x"}, {"kind": "question", "text": " "}])
    assert [m["id"] for m in a] == ["m_0001", "m_0002", "m_0003"]
    assert [m["id"] for m in timeline.moments(root)] == ["m_0003", "m_0001", "m_0002"]  # time order
    assert timeline.moments(root)[0]["text"] == "save this" and timeline.moments(root)[0]["seed"] == "s-9"
    assert timeline.moments(root)[2]["title"] == "Upstream"
    again = timeline.add_moments(root, [{"kind": "question", "position_s": 402, "text": "why do they stand on one leg",
                                         "session": "s1"},
                                        {"kind": "question", "position_s": 500, "text": "Another?", "session": "s1"}])
    assert [m["id"] for m in again] == ["m_0004"]
    assert len((root / "moments.jsonl").read_text().splitlines()) == 4


def test_moments_written_from_threads_keep_every_one(tmp_path):
    root = tmp_path / "ch"
    ts = [threading.Thread(target=timeline.add_moments,
                           args=(root, [{"kind": "question", "position_s": i, "text": f"Question {i}?", "session": "s"}]))
          for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    ms = timeline.moments(root)
    assert len(ms) == 12 and len({m["id"] for m in ms}) == 12


# --- playback -----------------------------------------------------------------------------

def test_playback_keeps_the_furthest_point_and_finishes_past_95_percent(tmp_path):
    root = tmp_path / "ch"
    root.mkdir()
    assert timeline.playback(root) == {"furthest_s": 0.0, "duration_s": None, "finished": False,
                                       "last_played": None, "plays": 0}
    p = timeline.record_playback(root, 120, 1000, start=True, now="2026-11-16T07:00:00")
    assert p == {"furthest_s": 120.0, "duration_s": 1000.0, "finished": False, "last_played": "2026-11-16T07:00:00",
                 "plays": 1}
    p = timeline.record_playback(root, 60, None)  # a seek back: furthest stays; the duration is remembered
    assert p["furthest_s"] == 120.0 and p["duration_s"] == 1000.0 and p["plays"] == 1
    assert timeline.status_of(timeline.playback(root), 1000) == ("partial", "2 of 17 min")
    p = timeline.record_playback(root, 949, 1000)
    assert not p["finished"]
    p = timeline.record_playback(root, 951, 1000)
    assert p["finished"] and timeline.status_of(p, 1000) == ("finished", "Finished")
    p = timeline.record_playback(root, 10, 1000, start=True)  # heard again from the start: still finished
    assert p["finished"] and p["plays"] == 2 and p["furthest_s"] == 951.0
    assert timeline.record_playback(root, 5000, 1000)["furthest_s"] == 1000.0  # never past the end
    assert timeline.status_of(timeline.playback(tmp_path / "none"), 600) == ("not_started", "Not started")


def test_counts_line():
    ms = [{"kind": "question"}, {"kind": "question"}, {"kind": "mention"}]
    assert timeline.counts_line(ms) == "2 questions · 1 mention"
    assert timeline.counts_line([{"kind": "note"}]) == "1 note"
    assert timeline.counts_line([]) == ""


# --- outcomes the planner sets ----------------------------------------------------------------

def test_a_seed_taken_for_the_card_marks_its_moment_and_a_replaced_card_puts_it_back(tmp_path):
    lis = tmp_path / "l"
    root = chapter(lis, "2026-11-14-herons", "Herons")
    s = library.add_seeds(lis, [{"kind": "question", "text": "Do gardens make us notice like this?",
                                 "source": {"chapter": root.name, "session": "s1"}}])[0]
    timeline.add_moments(root, [{"kind": "question", "position_s": 850, "text": "Do gardens make us notice like this?",
                                 "session": "s1", "outcome": "saved", "seed": s["id"]},
                                {"kind": "question", "position_s": 400, "text": "Why one leg?", "session": "s0",
                                 "outcome": "answered"}])
    card = {"id": "plan-1", "status": "planned", "source": {"kind": "seed", "ref": s["id"]}, "title": "Borrowed Scenery"}
    library.mark_used(lis, [s["id"]], "plan-1")
    planner.moments_follow(lis, None, card)
    m = {x["text"]: x for x in timeline.moments(root)}
    assert m["Do gardens make us notice like this?"]["outcome"] == "became_chapter"
    assert m["Do gardens make us notice like this?"]["outcome_ref"] == "plan-1"
    assert m["Why one leg?"]["outcome"] == "answered"
    assert s["id"] not in {x["id"] for x in library.seeds(lis)}
    # The listener changes Up next to something else: the moment reads Saved again, and its seed is back.
    planner.moments_follow(lis, card, {"id": "plan-2", "status": "planned", "source": {"kind": "request", "ref": "x"}})
    m = {x["text"]: x for x in timeline.moments(root)}
    assert m["Do gardens make us notice like this?"]["outcome"] == "saved"
    assert m["Do gardens make us notice like this?"]["outcome_ref"] is None
    assert s["id"] in {x["id"] for x in library.seeds(lis)}


def test_a_built_card_keeps_its_moments_and_they_link_to_the_chapter(tmp_path):
    lis = tmp_path / "l"
    root = chapter(lis, "2026-11-14-herons", "Herons", audio=True)
    built = chapter(lis, "2026-11-16-borrowed-scenery", "Borrowed Scenery", audio=True, card_id="plan-1")
    timeline.add_moments(root, [{"kind": "question", "position_s": 850, "text": "Gardens?", "session": "s1",
                                 "outcome": "became_chapter", "outcome_ref": "plan-1"}])
    ready = {"id": "plan-1", "status": "ready", "source": {"kind": "seed", "ref": "s-x"}}
    planner.moments_follow(lis, ready, {"id": "plan-2", "status": "planned", "source": {"kind": "chapter", "ref": "y"}})
    st = timeline.state(lis, None, "waiting", built.name, "Wren", today=date(2026, 11, 16))
    m = st["chapters"][0]["moments"][0]
    assert m["outcome"] == "became_chapter" and m["outcome_run"] == built.name
    assert m["outcome_label"] == "Borrowed Scenery" and not m["tonight"]


def test_a_seed_written_by_code_finds_its_moment_by_session(tmp_path):
    lis = tmp_path / "l"
    root = chapter(lis, "2026-11-14-herons", "Herons")
    timeline.add_moments(root, [{"kind": "question", "position_s": 100, "text": "Why do herons stand still?",
                                 "session": "s1", "outcome": "answered"},
                                {"kind": "question", "position_s": 100, "text": "Do gardens make us notice?",
                                 "session": "s1", "outcome": "saved"}])
    seed = {"id": "s-raw", "text": "uh do gardens like make us notice", "source": {"chapter": root.name, "session": "s1"}}
    got = timeline.moments_for_seed(lis, seed)
    assert [m["text"] for _, m in got] == ["Do gardens make us notice?"]
    assert timeline.moments_for_seed(lis, {"id": "s-none", "source": {"session": "nope"}}) == []


def test_unused_puts_a_seed_back_in_order(tmp_path):
    lis = tmp_path / "l"
    s = library.add_seeds(lis, [{"kind": "idea", "text": "Gardens"}])[0]
    library.mark_used(lis, [s["id"]], "plan-1")
    library.mark_unused(lis, [s["id"]], "replaced")
    assert [x["id"] for x in library.seeds(lis)] == [s["id"]]
    library.mark_used(lis, [s["id"]], "plan-3")
    assert library.seeds(lis) == []
    assert library.seed(lis, s["id"])["text"] == "Gardens"


# --- the reader -----------------------------------------------------------------------------

def test_state_lists_chapters_oldest_first_with_moments_status_and_colors(tmp_path):
    lis = tmp_path / "l"
    profiles.append_jsonl(lis / "library.jsonl", {"id": "c-1", "kind": "conversation", "date": "2026-10-24",
                                                  "title": "the heron by the pond"})
    a = chapter(lis, "2026-11-03-why-herons", "Why Herons Wait", topics=["Birds"])
    b = chapter(lis, "2026-11-11-beauty", "What Beauty Asks of Us", topics=["Beauty"], audio=True)
    c = chapter(lis, "2026-11-14-herons", "Herons: Hunting by Standing Still", topics=["Birds"], audio=True)
    chapter(lis, "2026-11-15-unbuilt", "Not built", finished=False)  # no audio, no finished.json: not listed
    timeline.record_playback(b, 2500, 2570)
    timeline.record_playback(a, 300, 1200)
    timeline.add_moments(c, [{"kind": "question", "position_s": 850, "text": "Gardens?", "session": "s1",
                              "outcome": "became_chapter", "outcome_ref": "plan-9"},
                             {"kind": "mention", "position_s": 1000, "text": "Upstream, Mary Oliver",
                              "session": "s1", "outcome": "in_library", "outcome_ref": "w-1"}])
    card = {"id": "plan-9", "title": "Borrowed Scenery: Why Japanese Gardens Hide the View", "for_morning": "2026-11-17",
            "status": "planned"}
    st = timeline.state(lis, card, "planned", c.name, "Wren", today=date(2026, 11, 20))
    runs = [r["run"] for r in st["chapters"]]
    assert runs == [a.name, b.name, c.name]
    by = {r["run"]: r for r in st["chapters"]}
    assert [by[x]["color"] for x in runs] == ["teal", "coral", "teal"]  # Birds, Beauty, Birds again
    assert by[a.name]["status_line"] == "5 of 20 min" and by[b.name]["status_line"] == "Finished"
    assert by[c.name]["status_line"] == "Not started"
    assert by[c.name]["counts"] == "1 question · 1 mention"
    assert [r["playable"] for r in st["chapters"]] == [False, False, True]
    assert by[a.name]["audio"] is False and by[a.name]["minutes"] == 20 and by[a.name]["duration_s"] == 1200
    ms = by[c.name]["moments"]
    assert ms[0]["tonight"] and ms[0]["outcome_label"] == "Became tonight's chapter"
    assert ms[1]["outcome_label"] == "In your library"
    assert st["first"] == {"date": "2026-10-24", "title": "the heron by the pond"}
    assert st["tonight"] == "2026-11-16" and st["up_next_short"] == "Borrowed Scenery" and st["guide"] == "Wren"


def test_tonight_follows_the_last_chapter_when_nothing_is_planned(tmp_path):
    lis = tmp_path / "l"
    chapter(lis, "2026-11-14-herons", "Herons", audio=True)
    assert timeline.state(lis, None, "none", None, None, today=date(2026, 11, 14))["tonight"] == "2026-11-15"
    assert timeline.state(lis, None, "none", None, None, today=date(2026, 11, 20))["tonight"] == "2026-11-20"
    assert timeline.state(tmp_path / "empty", None, "none", None, None, today=date(2026, 11, 20)) == {
        "chapters": [], "latest": None, "first": None, "tonight": "2026-11-20", "guide": None,
        "up_next_state": "none", "up_next_short": None}


# --- the routes ---------------------------------------------------------------------------

def test_library_api_has_the_timeline_and_the_player_writes_playback(cfg, tmp_path):
    import server
    lis = profiles.create("ian")
    profiles.set_active("ian")
    (lis / "guide.md").write_text("# Your guide\n\n- Name: Wren.\n")
    root = chapter(lis, "2026-11-14-herons", "Herons", audio=True)
    client = TestClient(server.create_app(server.Pipeline(), cfg, remember_on_start=False))
    got = client.get("/api/library").json()
    assert [r["run"] for r in got["timeline"]["chapters"]] == [root.name]
    assert got["timeline"]["latest"] == root.name and got["timeline"]["guide"] == "Wren"
    r = client.post(f"/api/chapters/{root.name}/playback", json={"t": 30, "duration": 600, "start": True})
    assert r.status_code == 200 and r.json()["furthest_s"] == 30 and r.json()["plays"] == 1
    assert json.loads((root / "playback.json").read_text())["plays"] == 1
    assert client.get("/api/library").json()["timeline"]["chapters"][0]["status_line"] == "1 of 10 min"
    assert client.post("/api/chapters/nope/playback", json={"t": 1}).status_code == 404
    assert client.post("/api/chapters/..%2Fx/playback", json={"t": 1}).status_code == 404
    assert client.post(f"/api/chapters/{root.name}/playback", json={"t": "soon"}).status_code == 400


def test_colours_follow_dates_not_folder_names(tmp_path):
    """Review: seed and fast runs are named <slug>-YYYYMMDD, so folder order is not time order."""
    lis = tmp_path / "l"
    late = chapter(lis, "aaa-herons-20261114", "Herons", topics=["Birds"])
    early = chapter(lis, "zzz-beauty-20261111", "Beauty", topics=["Beauty"])
    profiles.write_json(late / "finished.json", {"t": "2026-11-14T06:00:00"})
    profiles.write_json(early / "finished.json", {"t": "2026-11-11T06:00:00"})
    st = timeline.state(lis, None, "none", None, None, today=date(2026, 11, 20))
    assert [(r["title"], r["color"]) for r in st["chapters"]] == [("Beauty", "teal"), ("Herons", "coral")]


def test_a_days_chapters_keep_the_order_they_finished_in(tmp_path):
    lis = tmp_path / "l"
    later = chapter(lis, "aaa-later-20261112", "Later")
    earlier = chapter(lis, "zzz-earlier-20261112", "Earlier")
    profiles.write_json(later / "finished.json", {"t": "2026-11-12T20:00:00"})
    profiles.write_json(earlier / "finished.json", {"t": "2026-11-12T09:00:00"})
    st = timeline.state(lis, None, "none", None, None, today=date(2026, 11, 20))
    assert [r["title"] for r in st["chapters"]] == ["Earlier", "Later"]


def test_a_planned_card_still_counts_as_tonight_with_overnight_off(cfg, tmp_path):
    from overnight import routes
    lis = profiles.create("ian")
    profiles.set_active("ian")
    root = chapter(lis, "2026-11-14-herons", "Herons", audio=True)
    timeline.add_moments(root, [{"kind": "question", "position_s": 10, "text": "Gardens?", "session": "s",
                                 "outcome": "became_chapter", "outcome_ref": "plan-up"}])
    profiles.write_json(lis / "up_next.json", {"id": "plan-up", "title": "Borrowed Scenery", "status": "planned",
                                               "for_morning": "2026-11-17"})
    profiles.save_settings(cfg, {"overnight": {"enabled": False}}, listener=lis)
    st = routes.library_state(cfg)
    assert st["up_next"]["state"] == "off"
    assert st["timeline"]["chapters"][0]["moments"][0]["outcome_label"] == "Became tonight's chapter"
