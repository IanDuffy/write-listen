"""The simulated listener: real-time play, waiting, pauses, and skipping to the next marker only (check 6)."""

import random

import pytest

from faststart.player import SimPlayer


def sec(*pieces, complete=True):
    return {"pieces": list(pieces), "complete": complete}


THREE = {1: sec(40.0, 60.0), 2: sec(30.0, 30.0), 3: sec(50.0)}


def names(player, *only):
    return [e["event"] for e in player.events if not only or e["event"] in only]


# --- playing ---------------------------------------------------------------------------------

def test_plays_in_real_time_and_reports_like_the_page():
    p = SimPlayer()
    r = p.step(100, THREE, 3)
    assert r == {"state": "playing", "section": 1, "piece": 0, "t": 0.0, "chapter_t": 0.0, "position": 0.0,
                 "waiting_for": None, "since": None, "reason": None}
    r = p.step(150, THREE, 3)
    assert (r["section"], r["piece"], r["t"], r["chapter_t"]) == (1, 1, 50.0, 50.0)
    r = p.step(215, THREE, 3)                      # 100 s of section 1, then 15 s into section 2
    assert (r["state"], r["section"], r["piece"], r["t"]) == ("playing", 2, 0, 15.0)
    assert (r["chapter_t"], r["position"]) == (115.0, 115.0)
    r = p.step(1000, THREE, 3)
    assert (r["state"], r["section"], r["chapter_t"]) == ("done", 3, 210.0)
    assert names(p) == ["started", "section_start", "section_start", "section_start", "done"]
    assert [e["now"] for e in p.events] == [100, 100, 200, 260, 310]
    assert p.step(2000, THREE, 3)["state"] == "done" and len(p.events) == 5
    with pytest.raises(ValueError):
        p.step(1999, THREE, 3)


def test_the_wait_before_the_first_audio_is_idle_not_a_hold():
    p = SimPlayer()
    r = p.step(0, {}, 3)
    assert (r["state"], r["waiting_for"], r["since"]) == ("idle", None, None)
    assert p.step(90, {1: sec(complete=False)}, 3)["state"] == "idle" and p.events == []
    r = p.step(90, {"1": sec(40.0, complete=False)}, 3)        # keys as JSON gives them
    assert r["state"] == "playing" and names(p) == ["started", "section_start"]
    assert p.step(100, {1: sec(40.0, complete=False)}, 3)["t"] == 10.0


def test_waits_at_the_end_of_the_ready_audio_and_goes_on_when_more_comes():
    p = SimPlayer()
    partly = {1: sec(40.0, complete=False)}
    p.step(0, partly, 3)
    r = p.step(50, partly, 3)                      # the second piece is not narrated yet
    assert r == {"state": "waiting", "section": 1, "piece": 1, "t": 40.0, "chapter_t": 40.0, "position": 40.0,
                 "waiting_for": 1, "since": 40.0, "reason": "end_of_audio"}
    more = {1: sec(40.0, 60.0)}
    r = p.step(55, more, 3)                        # it arrived at 55 (the harness steps at every arrival)
    assert (r["state"], r["piece"], r["t"], r["since"]) == ("playing", 1, 40.0, None)
    r = p.step(200, more, 3)                       # section 1 over at 115; section 2 is not there
    assert (r["state"], r["section"], r["piece"], r["t"]) == ("waiting", 2, 0, 0.0)
    assert (r["waiting_for"], r["since"]) == (2, 115.0)
    assert r["chapter_t"] == 100.0 and r["position"] == 100.0
    r = p.step(230, {**more, 2: sec(30.0, complete=False)}, 3)
    assert (r["state"], r["section"], r["t"]) == ("playing", 2, 0.0)
    holds = [e for e in p.events if e["event"] in ("hold_start", "hold_end")]
    assert [(e["event"], e["section"], e["now"]) for e in holds] == [
        ("hold_start", 1, 40.0), ("hold_end", 1, 55), ("hold_start", 2, 115.0), ("hold_end", 2, 230)]
    assert holds[1]["seconds"] == 15.0 and holds[3]["seconds"] == 115.0 and holds[3]["reason"] == "end_of_audio"


def test_the_last_section_ends_only_when_it_is_complete():
    p = SimPlayer()
    p.step(0, {1: sec(10.0)}, 2)
    part = {1: sec(10.0), 2: sec(5.0, complete=False)}
    assert p.step(30, part, 2)["state"] == "playing"
    r = p.step(40, part, 2)
    assert (r["state"], r["waiting_for"], r["piece"], r["since"]) == ("waiting", 2, 1, 35.0)   # more of it may come
    r = p.step(41, {1: sec(10.0), 2: sec(5.0)}, 2)
    assert r["state"] == "done" and names(p)[-2:] == ["hold_end", "done"]


def test_a_pause_stops_the_clock_on_listening_for_its_length():
    p = SimPlayer(pauses=[(20, 300)])
    p.step(0, THREE, 3)
    r = p.step(100, THREE, 3)
    assert (r["state"], r["chapter_t"], r["t"], r["waiting_for"]) == ("paused", 20.0, 20.0, None)
    assert p.step(319, THREE, 3)["state"] == "paused"
    r = p.step(330, THREE, 3)                      # resumed at 320
    assert (r["state"], r["chapter_t"]) == ("playing", 30.0)
    stops = [(e["event"], e["now"]) for e in p.events if e["event"] in ("pause", "resume")]
    assert stops == [("pause", 20.0), ("resume", 320.0)]
    assert p.step(600, THREE, 3)["state"] == "done" and p.heard == 210.0


@pytest.mark.parametrize("every", [0.5, 3, 7, 1000])
def test_how_often_it_is_stepped_does_not_change_what_happens(every):
    def play(step):
        p = SimPlayer(skips=[25, 150], pauses=[(10, 40), (60, 5)], skip_times=[33.3])
        t = 0.0
        while t < 700:
            p.step(t, THREE, 3)
            t += step
        return p.step(700, THREE, 3), [(e["event"], round(e["now"], 6), e["section"]) for e in p.events]
    assert play(every) == play(1)


# --- skipping: to the next marker only (acceptance check 6) -----------------------------------

def test_skip_goes_to_the_next_marker_and_plays_it_from_its_start_when_ready():
    p = SimPlayer(skips=[25])
    p.step(0, THREE, 3)
    r = p.step(30, THREE, 3)
    assert (r["state"], r["section"], r["piece"], r["t"]) == ("playing", 2, 0, 5.0)     # section 2, not 3
    assert r["chapter_t"] == 30.0 and r["position"] == 105.0       # 30 s heard; 100 s of section 1 lie behind
    [skip] = [e for e in p.events if e["event"] == "skip"]
    assert (skip["from"], skip["to"], skip["moved"], skip["now"]) == (1, 2, True, 25.0)
    assert names(p, "hold_start") == []


def test_skip_to_a_section_that_is_not_ready_waits_for_it():
    p = SimPlayer(skips=[25])
    only_1 = {1: sec(40.0, 60.0)}
    p.step(0, only_1, 3)
    r = p.step(60, only_1, 3)
    assert r == {"state": "waiting", "section": 2, "piece": 0, "t": 0.0, "chapter_t": 25.0, "position": 100.0,
                 "waiting_for": 2, "since": 25.0, "reason": "skip"}
    r = p.step(90, {**only_1, 2: sec(30.0, 30.0), 3: sec(50.0)}, 3)
    # section 2 from its start, though 3 is ready too
    assert (r["state"], r["section"], r["t"], r["chapter_t"]) == ("playing", 2, 0.0, 25.0)
    end = p.events[-2]
    assert (end["event"], end["seconds"], end["reason"]) == ("hold_end", 65.0, "skip")


def test_skip_while_waiting_stays_at_the_marker_being_waited_for():
    """Already at the end of the ready audio, waiting for section 2: the next marker is section 2.
    Tapping skip (twice) does not move past it, restart the wait or change why it began."""
    p = SimPlayer(skip_times=[150, 160])
    only_1 = {1: sec(40.0, 60.0)}
    p.step(0, only_1, 3)
    before = p.step(140, only_1, 3)
    after = p.step(170, only_1, 3)
    assert before == after
    assert (after["waiting_for"], after["since"], after["reason"]) == (2, 100.0, "end_of_audio")
    skips = [e for e in p.events if e["event"] == "skip"]
    assert [(e["from"], e["to"], e["moved"]) for e in skips] == [(2, 2, False), (2, 2, False)]
    assert names(p, "hold_start", "hold_end") == ["hold_start"]
    r = p.step(200, {**only_1, 2: sec(30.0), 3: sec(50.0)}, 3)
    assert (r["state"], r["section"], r["t"]) == ("playing", 2, 0.0)


def test_skip_before_any_audio_does_nothing():
    p = SimPlayer(skip_times=[5])
    assert p.step(10, {}, 3)["state"] == "idle"
    r = p.step(20, THREE, 3)
    assert (r["state"], r["section"], r["t"]) == ("playing", 1, 0.0)
    assert [(e["from"], e["to"], e["moved"]) for e in p.events if e["event"] == "skip"] == [(1, 1, False)]


def test_skip_on_the_last_section_does_nothing():
    p = SimPlayer(skips=[170], skip_times=[330])
    p.step(0, THREE, 3)
    r = p.step(200, THREE, 3)                      # the tap came 10 s into section 3 (170 s heard)
    assert (r["state"], r["section"], r["t"]) == ("playing", 3, 40.0)
    assert p.step(300, THREE, 3)["state"] == "done" and p.heard == 210.0
    p.step(400, THREE, 3)                          # and a tap after the end
    skips = [e for e in p.events if e["event"] == "skip"]
    assert [(e["from"], e["to"], e["moved"]) for e in skips] == [(3, None, False), (3, None, False)]


def test_each_tap_is_one_marker():
    p = SimPlayer(skips=[10, 20])
    p.step(0, THREE, 3)
    r = p.step(15, THREE, 3)
    assert (r["section"], r["t"]) == (2, 5.0)
    r = p.step(25, THREE, 3)
    assert (r["section"], r["t"], r["position"]) == (3, 5.0, 165.0)
    assert [(e["from"], e["to"]) for e in p.events if e["event"] == "skip"] == [(1, 2), (2, 3)]


def test_skip_when_out_of_audio_mid_section_goes_to_the_next_marker():
    p = SimPlayer(skip_times=[60])
    partly = {1: sec(40.0, complete=False)}
    p.step(0, partly, 3)
    assert p.step(50, partly, 3)["waiting_for"] == 1
    r = p.step(70, partly, 3)
    assert (r["state"], r["section"], r["waiting_for"], r["since"], r["reason"]) == ("waiting", 2, 2, 60.0, "skip")
    assert [(e["event"], e["section"], e["now"]) for e in p.events if e["event"].startswith("hold")] == [
        ("hold_start", 1, 40.0), ("hold_end", 1, 60.0), ("hold_start", 2, 60.0)]


def test_skip_during_a_pause_moves_the_marker_and_stays_paused():
    p = SimPlayer(pauses=[(10, 100)], skip_times=[50])
    p.step(0, THREE, 3)
    r = p.step(60, THREE, 3)
    assert (r["state"], r["section"], r["t"], r["chapter_t"]) == ("paused", 2, 0.0, 10.0)
    r = p.step(115, THREE, 3)
    assert (r["state"], r["section"], r["t"]) == ("playing", 2, 5.0)


def test_taps_during_a_pause_do_not_pass_a_section_with_no_audio():
    """Paused in section 1, the first tap moves to section 2, which has no audio yet: that is the
    marker being waited for, so a second tap stays there, as it does while waiting. (It used to
    move on to section 3, and section 2 was never played.)"""
    only_1 = {1: sec(40.0, 60.0)}
    p = SimPlayer(pauses=[(30, 300)], skip_times=[150, 160])
    p.step(0, only_1, 3)
    r = p.step(200, only_1, 3)
    assert (r["state"], r["section"], r["piece"], r["t"]) == ("paused", 2, 0, 0.0)
    skips = [(e["from"], e["to"], e["moved"]) for e in p.events if e["event"] == "skip"]
    assert skips == [(1, 2, True), (2, 2, False)]
    r = p.step(340, only_1, 3)                     # the pause is over (30 + 300): waiting for section 2
    assert (r["state"], r["waiting_for"], r["since"], r["reason"]) == ("waiting", 2, 330.0, "skip")
    r = p.step(350, {**only_1, 2: sec(30.0), 3: sec(50.0)}, 3)
    assert (r["state"], r["section"], r["t"]) == ("playing", 2, 0.0)


def test_taps_during_a_pause_each_move_a_marker_when_the_sections_are_ready():
    p = SimPlayer(pauses=[(10, 100)], skip_times=[50, 60])
    p.step(0, THREE, 3)
    r = p.step(70, THREE, 3)
    assert (r["state"], r["section"], r["t"]) == ("paused", 3, 0.0)
    skips = [(e["from"], e["to"], e["moved"]) for e in p.events if e["event"] == "skip"]
    assert skips == [(1, 2, True), (2, 3, True)]


def test_random_taps_never_move_more_than_one_marker():
    rng = random.Random(6)
    for _ in range(200):
        last = rng.randint(2, 5)
        lengths = {n: [rng.uniform(5, 60) for _ in range(rng.randint(1, 4))] for n in range(1, last + 1)}
        arrives = sorted(rng.uniform(0, 600) for _ in range(sum(len(p) for p in lengths.values())))
        p = SimPlayer(skips=[rng.uniform(0, 300) for _ in range(rng.randint(0, 3))],
                      pauses=[(rng.uniform(0, 200), rng.uniform(1, 200)) for _ in range(rng.randint(0, 2))],
                      skip_times=[rng.uniform(0, 900) for _ in range(rng.randint(0, 4))])
        t, at = 0.0, 1
        while t < 3000:
            ready, left = {}, sum(1 for a in arrives if a <= t)       # pieces arrive in order, section by section
            for n in range(1, last + 1):
                take = min(left, len(lengths[n]))
                ready[n] = {"pieces": lengths[n][:take], "complete": take == len(lengths[n])}
                left -= take
            r = p.step(t, ready, last)
            assert r["section"] >= at and r["chapter_t"] <= t + 1e-6
            assert r["state"] != "playing" or ready[r["section"]]["pieces"]
            at = r["section"]
            t += rng.uniform(0.5, 9)
        assert r["state"] == "done" and r["section"] == last and not p.skip_times
        moved = [e for e in p.events if e["event"] == "skip" and e["moved"]]
        for e in p.events:
            if e["event"] != "skip":
                continue
            if e["moved"]:
                assert e["to"] == e["from"] + 1 <= last
            else:                          # at a marker already (waiting or before any audio), or nowhere to go
                assert (e["to"] is None and e["from"] == last) or e["to"] == e["from"]
        # every section the listener was in shows in the events, and they only ever go up by one
        visited = [e["section"] for e in p.events]
        assert all(b - a in (0, 1) for a, b in zip(visited, visited[1:])) and visited[0] == 1 and visited[-1] == last
        total = sum(sum(v) for v in lengths.values())
        if moved:                          # a skip leaves the rest of its section unheard
            assert p.heard < total
        else:
            assert p.heard == pytest.approx(total)
