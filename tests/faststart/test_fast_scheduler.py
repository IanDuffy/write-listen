"""The fast run's scheduler against made-up durations (acceptance check 3), and the check-in decision.

Every `run(...)` here also checks the rules after every event (fast_scheduler_sim.py lists
them) and raises if the run does not reach its archive, so each scenario is a deadlock test
as well as what it asserts by name.
"""

import json
import random

import pytest

from fast_scheduler_sim import Scenario, default_audio, default_rows, run
from faststart import checkin
from faststart.scheduler import MEDIANS, Scheduler

NS = (3, 4, 5)
FIRST_AUDIO = 75 + 40 * 0.3          # the opening, then its first piece narrated: 87 s
SECTION_1_ENDS = FIRST_AUDIO + 420   # uninterrupted listening reaches section 2 at 507 s


def lane_order(n: int) -> list[tuple]:
    """The writing lane as the contract gives it."""
    out = [("opening",)]
    for k in range(2, n + 1):
        out.append(("draft", k))
        if k >= 3:
            out.append(("lock", k - 1, "paired"))
    return out + [("lock", n, "final")]


def writing(result) -> list[tuple]:
    return [a for a in result.actions() if a[0] in ("opening", "draft", "lock")]


def narrated_in_order(result, n: int) -> None:
    assert [a for a in result.actions() if a[0] == "narrate"] == [("narrate", k) for k in range(1, n + 1)]
    for k in range(1, n + 1):
        earlier = result.narrated[k - 1] if k > 1 else 0
        assert result.when(("narrate", k)) == max(result.locked[k], earlier)   # the moment it is locked
        assert result.ready[k] > result.locked[k]                              # never before its edit
    assert result.archived == result.narrated[n] and result.count(("archive",)) == 1


# --- the scenarios, for 3, 4 and 5 sections ---------------------------------------------

@pytest.mark.parametrize("n", NS)
def test_uninterrupted_listening(n):
    r = run(Scenario(n=n))
    assert writing(r) == lane_order(n)
    narrated_in_order(r, n)
    assert r.ready[1] == FIRST_AUDIO
    assert r.holds() == [] and r.scheduler.holds == [] and r.solo == {} and r.scheduler.solo == []
    assert all(r.ready[k] <= r.needed()[k] for k in range(1, n + 1))   # each section ready before it is reached
    assert r.modes == {1: "opening", **{k: "paired" for k in range(2, n)}, n: "final"}
    # research: every row from the start, section 2's rows first, four at once
    rows = list(default_rows(n))
    assert [a[1] for t, a in r.issued if a[0] == "research" and t == 0] == rows[:4]
    assert r.player.state == "done" and r.player.heard == sum(sum(p) for p in default_audio(n).values())


@pytest.mark.parametrize("n", NS)
def test_skip_early_in_section_1_waits_for_the_paired_edit(n):
    """The listener skips two minutes in. Section 2's paired lock is already running when the
    wait passes max_hold, so there is no solo edit: the fallback is never for a lock that has begun."""
    r = run(Scenario(n=n, skips=(120,)))
    [hold] = r.holds()
    assert (hold["section"], hold["start"], hold["reason"]) == (2, FIRST_AUDIO + 120, "skip")
    assert hold["seconds"] > 180 and r.solo == {} and r.modes[2] == "paired"
    assert r.when(("lock", 2, "paired")) < hold["start"] + 180 < r.locked[2]
    assert hold["end"] == r.ready[2] == r.locked[2] + 60 * 0.3
    assert writing(r) == lane_order(n)
    narrated_in_order(r, n)
    [mine] = r.scheduler.holds                     # the scheduler's own record of the hold agrees
    assert (mine["section"], mine["start"], mine["end"], mine["reason"]) == (2, hold["start"], hold["end"], "skip")
    assert mine["seconds"] == hold["seconds"] and "solo" not in mine


@pytest.mark.parametrize("n", NS)
def test_long_pause_by_the_listener(n):
    """Generation never waits for the listener: the chapter is archived while they are away."""
    r = run(Scenario(n=n, pauses=((60, 1500),)))
    assert r.holds() == [] and r.solo == {}
    assert r.archived < FIRST_AUDIO + 60 + 1500
    assert writing(r) == lane_order(n)
    narrated_in_order(r, n)
    assert [e["event"] for e in r.player.events if e["event"] in ("pause", "resume")] == ["pause", "resume"]


@pytest.mark.parametrize("n", NS)
def test_slow_research_holds_the_audio_for_the_paired_edit(n):
    """One row (section 3's) is far slower than the rest. Section 2 is not narrated until the
    draft of 3 exists and the pair is edited; the listener waits, but under max_hold: no solo edit."""
    r = run(Scenario(n=n, research={"L03": 520, "default": 240}))
    assert r.when(("draft", 3)) == 520 and r.when(("lock", 2, "paired")) == r.drafted[3] == 565
    [hold] = r.holds()
    assert (hold["section"], hold["start"], hold["reason"]) == (2, SECTION_1_ENDS, "end_of_audio")
    assert hold["seconds"] == 136 and hold["end"] == r.ready[2]
    assert r.solo == {} and r.modes[2] == "paired"
    assert writing(r) == lane_order(n)
    narrated_in_order(r, n)


@pytest.mark.parametrize("n", NS)
def test_a_row_that_never_returns_is_timed_out(n):
    r = run(Scenario(n=n, research={"L03": None, "default": 240}))
    assert r.scheduler.timed_out == ["L03"] and r.count(("research", "L03")) == 1
    assert r.when(("draft", 3)) == 600             # row_timeout after it started; not a moment sooner
    assert r.solo == {}                            # the listener's wait had not reached max_hold by then
    assert writing(r) == lane_order(n)
    narrated_in_order(r, n)


@pytest.mark.parametrize("n", NS)
def test_a_row_that_never_returns_with_a_long_timeout_brings_the_solo_edit(n):
    """The same lost row with a 900 s timeout: the listener reaches section 2 at 507 s and the
    draft of 3 is nowhere near, so at 507 + 180 section 2 is locked on its own."""
    r = run(Scenario(n=n, research={"L03": None, "default": 240}, row_timeout=900))
    assert r.solo == {2: SECTION_1_ENDS + 180} and r.modes[2] == "solo" and r.scheduler.solo == [2]
    assert r.when(("draft", 3)) == 900
    after = ("lock", 3, "final") if n == 3 else ("lock", 3, "paired")   # the lane goes on as usual
    assert r.when(after) is not None and r.modes[n] == "final"
    narrated_in_order(r, n)


@pytest.mark.parametrize("n", NS)
def test_fast_everything(n):
    """Generation finishes before the listener gets far."""
    r = run(Scenario(n=n, opening=10, research=5, draft=3, lock=4, synth=0.02))
    assert r.archived < r.needed()[2] and r.archived < 60
    assert r.holds() == [] and r.solo == {}
    assert writing(r) == lane_order(n)
    narrated_in_order(r, n)


@pytest.mark.parametrize("n", NS)
def test_a_note_row_added_before_its_sections_draft_is_waited_for(n):
    r = run(Scenario(n=n, research_parallel=8, add_rows=((100, "N01", 3, 400),)))
    assert r.rows["N01"] == 3 and r.when(("research", "N01")) == 100
    assert r.when(("draft", 3)) == 500             # not at 285, when the draft of 2 landed
    assert writing(r) == lane_order(n)
    narrated_in_order(r, n)


@pytest.mark.parametrize("n", NS)
def test_a_note_row_added_after_its_sections_draft_began_is_ignored(n):
    """While the draft of 2 runs (240-285), after it, and for the opening: no agent is started."""
    r = run(Scenario(n=n, add_rows=((250, "N01", 2, 50), (300, "N02", 2, 50), (10, "N03", 1, 5))))
    assert not {"N01", "N02", "N03"} & set(r.rows)
    assert not [a for a in r.actions() if a[0] == "research" and a[1].startswith("N")]
    assert not any(r.scheduler.has_row(row) for row in ("N01", "N02", "N03"))
    assert r.when(("draft", 3)) == 285 and r.archived == run(Scenario(n=n)).archived


@pytest.mark.parametrize("n", NS)
@pytest.mark.parametrize("action", [("opening",), ("draft", 2), ("lock", 2, "paired"), ("lock", "last", "final"),
                                    ("narrate", 1), ("narrate", 2), ("archive",)])
def test_a_failed_action_is_issued_again(n, action):
    action = tuple(n if x == "last" else x for x in action)
    r = run(Scenario(n=n, fail={action: 1}))
    assert r.count(action) == 2 and r.scheduler.dead is None and r.archived is not None
    assert r.scheduler.failures == {action: 1} and r.scheduler.finished
    once = lambda actions: [a for i, a in enumerate(actions) if a not in actions[:i]]
    assert once(writing(r)) == lane_order(n)                 # the same chapter, one step later
    assert once([a for a in r.actions() if a[0] == "narrate"]) == [("narrate", k) for k in range(1, n + 1)]
    assert sorted(r.narrated) == list(range(1, n + 1)) and r.modes[n] == "final"


@pytest.mark.parametrize("n", NS)
def test_a_failed_research_row_counts_as_done(n):
    r = run(Scenario(n=n, fail={("research", "L01"): 1}))
    assert r.count(("research", "L01")) == 1 and r.when(("draft", 2)) == 240    # written without it
    assert writing(r) == lane_order(n)
    narrated_in_order(r, n)


@pytest.mark.parametrize("n", NS)
def test_a_failed_solo_lock_is_issued_again(n):
    r = run(Scenario(n=n, skips=(120,), draft={3: 400, "default": 45}, fail={("lock", 2, "solo"): 1}))
    assert r.count(("lock", 2, "solo")) == 2 and r.modes[2] == "solo" and r.scheduler.solo == [2]
    assert r.archived is not None and r.scheduler.dead is None


@pytest.mark.parametrize("n", NS)
def test_a_failed_solo_lock_is_decided_again_and_paired_when_the_next_draft_has_landed(n):
    """The solo lock of 2 is issued at 387 s, the draft of 3 lands at 390, the solo lock fails at 392.
    With 3 sections the lane is free, so section 2 gets its paired edit (it used to be issued again as
    solo, with its pair on disk). With more, the lane is busy with the draft of 4 and the listener is
    still past max_hold: solo again."""
    solo = ("lock", 2, "solo")
    r = run(Scenario(n=n, skips=(120,), draft={3: 105, "default": 45}, fail={solo: 1}))
    assert r.when(solo) == FIRST_AUDIO + 120 + 180 == 387 and r.drafted[3] == 390
    assert r.scheduler.failures == {solo: 1} and r.scheduler.dead is None and r.archived is not None
    if n == 3:
        assert r.count(solo) == 1 and r.when(("lock", 2, "paired")) == 392
        assert r.modes[2] == "paired" and r.solo == {} and r.scheduler.solo == []
        assert not any(h.get("solo") for h in r.scheduler.holds)      # no hold says it ended in a solo edit
    else:
        assert r.when(("draft", 4)) == 390 and [t for t, a in r.issued if a == solo] == [387, 392]
        assert r.modes[2] == "solo" and r.scheduler.solo == [2] and r.scheduler.holds[0]["solo"] is True
    narrated = [a for a in r.actions() if a[0] == "narrate"]
    assert narrated == [("narrate", k) for k in range(1, n + 1)]


def test_a_failed_solo_lock_is_not_issued_again_for_a_listener_who_has_gone():
    """The fallback is for someone waiting. If the report has gone stale by the time the solo lock
    fails, the section waits for its paired edit; a second failure of a solo lock still ends the run."""
    waiting = {"state": "waiting", "section": 2, "piece": 0, "t": 0, "waiting_for": 2, "since": 40, "reason": "skip"}

    def held() -> Scheduler:
        s = Scheduler([1, 2, 3, 4], {}, max_hold=180, report_timeout=30)
        s.start(0)
        s.opening_done(10)
        assert s.draft_done(2, 30) == [("draft", 3)]
        assert s.player(waiting, 40) == [] and s.player(waiting, 221) == [("lock", 2, "solo")]
        return s

    s = held()
    assert s.failed(("lock", 2, "solo"), 300) == []                  # the last report is 79 s old
    assert s.solo == [] and 2 not in s.lock_mode and "solo" not in s.holds[0] and s.dead is None
    assert s.draft_done(3, 310) == [("lock", 2, "paired")]

    s = held()
    assert s.player(waiting, 225) == [] and s.failed(("lock", 2, "solo"), 226) == [("lock", 2, "solo")]
    assert s.solo == [2] and s.holds[0]["solo"] is True
    assert s.failed(("lock", 2, "solo"), 230) == [] and s.dead["action"] == ("lock", 2, "solo")


@pytest.mark.parametrize("action", [("opening",), ("draft", 3), ("lock", 2, "paired"), ("narrate", 2), ("archive",)])
def test_an_action_that_keeps_failing_stops_the_run(action):
    """Past its retries nothing more is issued: the scheduler says which action, the driver reports it."""
    r = run(Scenario(n=4, fail={action: 2}))
    dead = r.scheduler.dead
    assert dead["action"] == action and r.count(action) == 2
    assert not [a for t, a in r.issued if t > dead["t"]]
    assert r.scheduler.start(dead["t"] + 1) == [] and r.scheduler.tick(dead["t"] + 2) == []
    assert r.scheduler.snapshot()["dead"] == {"action": list(action), "t": dead["t"]}
    eta = r.scheduler.eta(4, dead["t"] + 1, dict.fromkeys(MEDIANS, 10))
    assert eta == (0.0 if r.scheduler.ready(4) else None)      # no estimate for what will not come


# --- a hold that crosses max_hold --------------------------------------------------------

@pytest.mark.parametrize("n", NS)
def test_hold_crosses_max_hold_while_the_next_draft_is_running(n):
    """Skip at two minutes; the draft of 3 takes 400 s. At exactly max_hold of waiting section 2
    is locked on its own, outside the lane: the draft of 3 carries on and is not issued again."""
    r = run(Scenario(n=n, skips=(120,), draft={3: 400, "default": 45}))
    waiting_since = FIRST_AUDIO + 120
    assert r.solo == {2: waiting_since + 180}
    assert r.when(("draft", 3)) == 285 < r.solo[2] < r.drafted[3] == 685 and r.count(("draft", 3)) == 1
    assert r.when(("narrate", 2)) == r.locked[2] == r.solo[2] + 60
    [hold] = [h for h in r.holds() if h["section"] == 2]
    assert hold["end"] == r.ready[2] and hold["reason"] == "skip"
    entry = next(e for e in r.scheduler.log if e.get("action") == ("lock", 2, "solo"))
    assert entry["waited"] == 180 and r.scheduler.holds[0]["solo"] is True
    if n == 3:
        assert r.when(("lock", 3, "final")) == 685
    else:                                          # then the lane as usual: draft 4, lock 3 paired with it
        assert r.when(("draft", 4)) == 685 and r.when(("lock", 3, "paired")) == 730
    assert [m for k, m in r.modes.items() if m == "solo"] == ["solo"]
    narrated_in_order(r, n)


@pytest.mark.parametrize("n", NS)
def test_hold_crosses_max_hold_while_the_next_draft_waits_on_research(n):
    r = run(Scenario(n=n, skips=(120,), research={"L03": 550, "default": 240}))
    assert r.solo == {2: FIRST_AUDIO + 120 + 180}
    assert r.when(("draft", 3)) == 550 > r.locked[2]       # the lane was idle, waiting for L03
    assert r.modes[2] == "solo" and r.modes[n] == "final"
    narrated_in_order(r, n)


@pytest.mark.parametrize("n", NS)
def test_hold_crosses_max_hold_before_the_held_section_is_drafted(n):
    """Section 2's own research is slow: its draft lands at 545 s, with the listener waiting since
    207 s. The fallback fires the moment the draft lands, and the lane starts the draft of 3 beside it."""
    r = run(Scenario(n=n, skips=(120,), research={"L01": 500, "default": 240}))
    assert r.drafted[2] == 545 and r.solo == {2: 545}
    assert r.when(("draft", 3)) == 545
    assert not [a for t, a in r.issued if a[0] == "lock" and t < 545]
    narrated_in_order(r, n)


@pytest.mark.parametrize("n", NS)
@pytest.mark.parametrize("max_hold", [30, 90, 180, 400])
def test_the_solo_fallback_never_fires_before_max_hold(n, max_hold):
    r = run(Scenario(n=n, skips=(100,), draft={3: 500, "default": 45}, max_hold=max_hold, report_every=7))
    since = FIRST_AUDIO + 100
    earliest = max(since + max_hold, r.drafted[2])          # waited long enough, and there is a draft to lock
    assert 2 in r.solo and earliest <= r.solo[2] < earliest + 7      # at the first report past the threshold
    narrated_in_order(r, n)


def test_a_draft_that_lands_as_the_wait_passes_max_hold_gets_the_paired_lock():
    """The wait is past max_hold at the very event that brings the draft of 3. The pair is there, so
    section 2 gets its paired edit: the solo edit is only for when there is nothing to pair with."""
    s = Scheduler([1, 2, 3, 4], {}, max_hold=180)
    s.start(0)
    s.opening_done(10)
    assert s.draft_done(2, 30) == [("draft", 3)]
    waiting = {"state": "waiting", "section": 2, "piece": 0, "t": 0, "waiting_for": 2, "since": 40, "reason": "skip"}
    assert s.player(waiting, 40) == [] and s.player(waiting, 218) == []      # 178 s: not yet
    assert s.draft_done(3, 225) == [("lock", 2, "paired")]                   # 185 s, and the pair is there
    assert s.tick(226) == [] and s.solo == [] and s.lock_mode[2] == "paired"


def test_a_stale_report_holds_the_solo_fallback_until_the_listener_is_heard_from():
    s = Scheduler([1, 2, 3, 4], {}, max_hold=180, report_timeout=30)
    s.start(0)
    s.opening_done(10)
    assert s.draft_done(2, 20) == [("draft", 3)]
    waiting = {"state": "waiting", "section": 2, "piece": 0, "t": 0, "waiting_for": 2, "since": 50, "reason": "skip"}
    assert s.player(waiting, 50) == []
    assert s.tick(229) == [] and s.player(waiting, 229.5) == []      # not yet 180 s
    assert s.tick(300) == []                                         # past it, but the report is 70 s old
    assert s.player(waiting, 301) == [("lock", 2, "solo")]           # the listener is still there
    assert s.hold()["section"] == 2 and s.hold()["start"] == 50


# --- a randomised sweep -------------------------------------------------------------------

def random_scenario(rng: random.Random, n: int) -> Scenario:
    rows, research = {}, {"default": 240}
    for section in range(2, n + 1):
        for _ in range(rng.choice([0, 1, 1, 2])):
            row = f"L{len(rows) + 1:02d}"
            rows[row] = section
            research[row] = rng.choice([rng.uniform(5, 60), rng.uniform(100, 400), rng.uniform(100, 400),
                                        rng.uniform(400, 900), None])
    audio = {1: [rng.uniform(15, 90) for _ in range(rng.randint(3, 8))]}
    for section in range(2, n + 1):
        audio[section] = [rng.uniform(15, 90) for _ in range(rng.randint(1, 6))]
    fail = {}
    if rng.random() < 0.4:
        k = rng.randint(2, n)
        fail = rng.choice([{("opening",): 1}, {("draft", k): 1}, {("narrate", rng.randint(1, n)): 1},
                           {("archive",): 1}, {("research", rng.choice(list(rows) or ["none"])): 1},
                           {("lock", k, mode): 1 for mode in ("paired", "solo", "final")}])
    return Scenario(
        n=n, rows=rows, research=research, audio=audio,
        opening=rng.uniform(15, 160),
        draft={"default": 45, **{k: rng.uniform(8, 320) for k in range(2, n + 1)}},
        lock={"default": 60, **{k: rng.uniform(8, 220) for k in range(2, n + 1)}},
        synth=rng.choice([0.05, 0.3, 0.3, 0.6, 1.3]),       # 1.3: narration slower than listening
        skips=tuple(rng.uniform(0, 1200) for _ in range(rng.choice([0, 0, 1, 2, 3]))),
        skip_times=tuple(rng.uniform(0, 1500) for _ in range(rng.choice([0, 0, 1, 2]))),
        pauses=tuple((rng.uniform(0, 800), rng.uniform(5, 1200)) for _ in range(rng.choice([0, 0, 1, 2]))),
        max_hold=rng.choice([30, 90, 180, 180]),
        research_parallel=rng.choice([1, 2, 4, 4]),
        row_timeout=rng.choice([150, 600]),
        add_rows=tuple((rng.uniform(0, 700), f"N{i}", rng.randint(1, n), rng.choice([20, 200, None]))
                       for i in range(rng.choice([0, 0, 1, 2]))),
        fail=fail,
        restarts=tuple(rng.uniform(5, 1000) for _ in range(rng.choice([0, 0, 0, 1, 2]))),
        report_every=rng.choice([1, 3, 3, 5]),
        last_flag=rng.random() < 0.5,
        join=rng.choice([0, 0, 0.7, 6]),
    )


@pytest.mark.parametrize("n", NS)
def test_randomised_sweep_keeps_the_rules_and_always_archives(n):
    rng = random.Random(20261112 + n)
    seen = {"solo": 0, "holds": 0, "timed_out": 0, "restarts": 0, "failed": 0, "notes": 0}
    for i in range(120):
        sc = random_scenario(rng, n)
        try:
            r = run(sc)                    # raises on any broken rule and on a run that never archives
        except AssertionError as e:
            raise AssertionError(f"run {i} for {n} sections: {e}\n{sc}") from e
        assert r.scheduler.dead is None and r.archived is not None and r.scheduler.finished, (i, sc)
        assert r.player.state == "done" and len(r.narrated) == n
        assert sorted(r.modes) == list(range(1, n + 1)) and r.modes[1] == "opening" and r.modes[n] == "final"
        for hold in r.holds():
            assert hold["end"] is not None, (i, hold)
        for k, at in r.solo.items():
            waits = [h for h in r.holds() if h["section"] == k and h["start"] <= at <= h["end"]]
            assert waits and at - waits[0]["start"] >= sc.max_hold - 1e-6, (i, k, at)
        for mine in r.scheduler.holds:     # the scheduler's record of each hold against the listener's own
            [theirs] = [h for h in r.holds() if (h["section"], h["start"]) == (mine["section"], mine["start"])]
            assert mine["reason"] == theirs["reason"], (i, mine, theirs)
            if theirs["end"] in r.piece_times:                 # audio ended it: known at once, not at the next report
                assert mine["end"] == theirs["end"], (i, mine, theirs)
            else:                                              # the listener moved on: known from their next report
                assert theirs["end"] <= mine["end"] <= theirs["end"] + sc.report_every + 1e-6, (i, mine, theirs)
        seen["solo"] += len(r.solo)
        seen["holds"] += len(r.holds())
        seen["timed_out"] += len(r.scheduler.timed_out)
        seen["restarts"] += len([e for e in r.trace if e["event"] == "restart"])
        seen["failed"] += len([e for e in r.trace if e["event"] == "failed"])
        seen["notes"] += len([row for row in r.rows if row.startswith("N")])
    assert all(seen.values()), f"the sweep never met one of these: {seen}"    # it must exercise every path


# --- restoring from files mid-run ----------------------------------------------------------

@pytest.mark.parametrize("n", NS)
@pytest.mark.parametrize("at", [5, 80, 150, 250, 300, 345, 392, 400, 470, 520, 600, 700])
def test_restoring_from_a_done_dict_mid_run_continues(n, at):
    """The process dies at `at`; a new scheduler restores from what had finished and carries on:
    nothing finished is done again (the harness checks that), and the chapter is the same."""
    plain = run(Scenario(n=n))
    r = run(Scenario(n=n, restarts=(at,)))
    assert r.archived is not None and r.scheduler.finished
    assert r.modes == plain.modes
    narrated = [a for a in r.actions() if a[0] == "narrate"]
    assert [a for i, a in enumerate(narrated) if a not in narrated[:i]] == [("narrate", k) for k in range(1, n + 1)]
    assert r.scheduler.pieces == {k: p for k, p in default_audio(n).items()}
    assert r.player.state == "done"


@pytest.mark.parametrize("n", NS)
def test_restoring_repeatedly_with_a_listener_waiting(n):
    r = run(Scenario(n=n, restarts=(100, 300, 395, 500, 640), skips=(120,), draft={3: 400, "default": 45}))
    assert r.archived is not None and r.modes[n] == "final"
    assert all(h["end"] is not None for h in r.holds())


@pytest.mark.parametrize("n", NS)
@pytest.mark.parametrize("restarts", [(610,), (610, 630)])
def test_a_timed_out_row_is_not_researched_again_after_a_restart(n, restarts):
    """L03 never returns and is given up at 600 s; the process dies with the draft of 3 in flight.
    The restored scheduler takes the timeout from the done dict: the draft is issued again at once.
    (The row used to come back pending: a second agent, and the draft waiting 600 s more.)"""
    plain = run(Scenario(n=n, research={"L03": None, "default": 240}))
    r = run(Scenario(n=n, research={"L03": None, "default": 240}, restarts=restarts))
    assert r.count(("research", "L03")) == 1
    assert [t for t, a in r.issued if a == ("draft", 3)] == [600, *restarts]
    late = restarts[-1] - 600                      # the draft of 3 began again that much later: nothing else moved
    assert r.archived == plain.archived + late and r.modes == plain.modes
    assert r.scheduler.timed_out == ["L03"] and r.scheduler.row_state["L03"] == "timed_out"
    assert [h["seconds"] for h in r.holds()] == [h["seconds"] + late for h in plain.holds()] == [216 + late]


def test_the_done_dict_carries_timed_out_rows():
    rows = {"L01": 2, "L02": 3, "L03": 4}
    s = Scheduler([1, 2, 3, 4], rows, row_timeout=600)
    s.start(0)
    s.opening_done(75)
    s.row_done("L01", 240)
    s.row_done("L03", 250)
    s.draft_done(2, 285)
    assert s.tick(600) == [("draft", 3)] and s.timed_out == ["L02"]
    done = json.loads(json.dumps(s.snapshot()["done"]))
    assert done["timed_out"] == ["L02"] and done["rows"] == ["L01", "L03"]
    again = Scheduler([1, 2, 3, 4], rows, done=done)
    assert again.start(601) == [("draft", 3), ("narrate", 1)]            # no ("research", "L02")
    assert again.timed_out == ["L02"] and again.row_state["L02"] == "timed_out"
    assert again.snapshot()["done"]["timed_out"] == ["L02"]
    # a row that came in after all is done, a row the ledger no longer has is ignored, and a done
    # dict without the key (an older state file, or the files alone) restores as before
    late = Scheduler([1, 2, 3, 4], rows, done={**done, "rows": ["L01", "L02", "L03"], "timed_out": ["L02", "L09"]})
    assert late.row_state["L02"] == "done" and late.timed_out == []
    old = Scheduler([1, 2, 3, 4], rows, done={k: v for k, v in done.items() if k != "timed_out"})
    assert old.start(601) == [("research", "L02"), ("narrate", 1)] and old.timed_out == []


def test_restore_issues_what_the_files_leave_to_do():
    rows = {"L01": 2, "L02": 3, "L03": 4}
    fresh = Scheduler([1, 2, 3, 4], rows)
    assert fresh.start(0) == [("research", "L01"), ("research", "L02"), ("research", "L03"), ("opening",)]

    # the opening done and part narrated, the draft of 2 on disk, one row still out
    s = Scheduler([1, 2, 3, 4], rows, done={"rows": ["L01", "L03"], "drafts": [1, 2], "locked": [1],
                                           "narrated": [], "pieces": {"1": [40.0, 80.0]}})
    assert s.ready(1) and not s.complete(1) and s.state(2) == "being_written"
    assert s.start(1000) == [("research", "L02"), ("narrate", 1)]
    # the narrator reports every piece again, reused ones included: with or without an index they land once
    s.piece_ready(1, 40.0, 1000)
    s.piece_ready(1, 80.0, 1000, index=1)
    s.piece_ready(1, 60.0, 1010, last=True)
    assert s.pieces[1] == [40.0, 80.0, 60.0] and s.complete(1) and ("narrate", 1) in s.running
    assert s.narrate_done(1, 1011) == []
    assert s.row_done("L02", 1020) == [("draft", 3)]

    # sections 2 and 3 drafted, 2 not locked: the paired lock, not a new draft
    s = Scheduler([1, 2, 3, 4], rows, done={"rows": list(rows), "drafts": [2, 3], "locked": [1], "narrated": [1],
                                           "pieces": {1: [400.0]}})
    assert s.start(0) == [("lock", 2, "paired")]
    assert s.lock_done(2, 60) == [("draft", 4), ("narrate", 2)]

    # a row that never came back, for a section already drafted: not researched again
    s = Scheduler([1, 2, 3, 4], rows, done={"rows": ["L03"], "drafts": [1, 2, 3], "locked": [1, 2], "narrated": [1]})
    assert s.row_state == {"L01": "skipped", "L02": "skipped", "L03": "done"}
    assert s.start(0) == [("draft", 4), ("narrate", 2)]

    # everything narrated: only the archive; and once that has run, nothing
    full = {"rows": list(rows), "drafts": [1, 2, 3, 4], "locked": [1, 2, 3, 4], "narrated": [1, 2, 3, 4],
            "pieces": {k: [100.0] for k in range(1, 5)}}
    s = Scheduler([1, 2, 3, 4], rows, done=full)
    assert s.start(0) == [("archive",)] and s.archive_done(5) == [] and s.finished
    assert Scheduler([1, 2, 3, 4], rows, done={**full, "archived": True}).start(0) == []


def test_restore_cuts_the_files_down_to_what_the_rules_allow():
    """A lock without the one before it, narration without a lock, a draft with a gap: redone, not trusted."""
    s = Scheduler([1, 2, 3, 4, 5], {}, done={"drafts": [1, 2, 4], "locked": [1, 3], "narrated": [1, 2, 3],
                                            "pieces": {1: [10.0], 2: [5.0], 3: [5.0]}})
    assert (s.locked, s.drafted, s.narrated) == ({1}, {1, 2}, {1})
    assert s.pieces == {1: [10.0]}
    assert s.start(0) == [("draft", 3)]
    # no opening on disk: nothing after it counts
    s = Scheduler([1, 2, 3], {}, done={"drafts": [1, 2], "locked": [], "narrated": []})
    assert s.drafted == set() and s.start(0) == [("opening",)]


def test_snapshot_round_trips_through_json():
    r = run(Scenario(n=4, restarts=(350,)))
    snap = json.loads(json.dumps(r.scheduler.snapshot()))
    assert set(snap) >= {"sections", "rows", "running", "holds", "solo", "archived", "finished", "dead", "done"}
    assert snap["sections"]["2"] == {"state": "ready", "marker": "played", "drafted": True, "lock": "paired",
                                     "locked": True, "pieces": [60.0] * 4, "complete": True, "narrated": True}
    again = Scheduler([1, 2, 3, 4], default_rows(4), done=snap["done"])
    assert again.start(0) == [] and again.finished and again.audio() == r.scheduler.audio()


# --- single events ---------------------------------------------------------------------------

def test_research_runs_section_2_first_and_within_its_limit():
    s = Scheduler([1, 2, 3, 4], {"L04": 4, "L03": 3, "L01": 2, "L02": 2, "L05": 4}, research_parallel=2)
    assert s.start(0) == [("research", "L01"), ("research", "L02"), ("opening",)]
    assert s.row_done("L01", 30) == [("research", "L03")]
    assert s.row_done("L01", 31) == []                                  # reported twice: ignored
    assert s.row_done("L02", 40) == [("research", "L04")]
    assert s.row_added("N01", 3, 41) == [] and s.has_row("N01")         # queued: no slot yet
    assert s.row_done("L04", 50) == [("research", "N01")]               # section 3's note row before section 4's L05
    assert s.row_done("unknown", 51) == []


def test_a_timed_out_row_frees_its_slot_and_a_late_answer_is_ignored():
    s = Scheduler([1, 2, 3], {"L01": 2, "L02": 3}, research_parallel=1, row_timeout=100)
    assert s.start(0) == [("research", "L01"), ("opening",)]
    s.opening_done(20)
    assert s.tick(99.9) == []
    assert s.tick(100) == [("research", "L02"), ("draft", 2)]           # L01 timed out: its slot, and the draft
    assert s.timed_out == ["L01"] and s.row_state["L01"] == "timed_out"
    assert s.row_done("L01", 130) == [] and s.row_state["L01"] == "timed_out"


def test_events_for_actions_that_are_not_running_are_refused():
    s = Scheduler([1, 2, 3], {})
    s.start(0)
    for bad in (lambda: s.draft_done(2, 1), lambda: s.lock_done(2, 1), lambda: s.piece_ready(1, 5.0, 1),
                lambda: s.narrate_done(1, 1), lambda: s.failed(("draft", 2), 1), lambda: s.archive_done(1)):
        with pytest.raises(ValueError):
            bad()
    s.opening_done(5)
    with pytest.raises(ValueError):
        s.opening_done(6)
    with pytest.raises(ValueError):
        s.piece_ready(1, 5.0, 7, index=2)                               # a gap
    with pytest.raises(ValueError):
        Scheduler([2, 3], {})


def test_a_failed_narration_reports_its_pieces_again_without_doubling_them():
    s = Scheduler([1, 2, 3], {})
    s.start(0)
    assert s.opening_done(10) == [("draft", 2), ("narrate", 1)]
    s.piece_ready(1, 40.0, 20)
    s.piece_ready(1, 80.0, 30)
    assert s.failed(("narrate", 1), 35) == [("narrate", 1)]
    assert s.ready(1) and s.pieces[1] == [40.0, 80.0]                   # what was playable stays playable
    for seconds in (40.0, 80.0, 70.0):                                  # the narrator starts again from the first
        s.piece_ready(1, seconds, 40)
    assert s.narrate_done(1, 41) == [] and s.pieces[1] == [40.0, 80.0, 70.0]


def test_the_last_piece_completes_a_section_but_only_the_narrations_return_moves_on():
    """The narrator reports its last piece, then joins and times the section. The listener may play
    the section to its end at once; the next narration and the archive wait for the return."""
    s = Scheduler([1, 2], {})
    s.start(0)
    s.opening_done(10)
    s.draft_done(2, 20)
    assert s.piece_ready(1, 30.0, 25, last=True, index=0) == [] and s.complete(1)
    assert s.lock_done(2, 30) == []                       # locked, but narration 1 has not returned
    assert s.narrate_done(1, 31) == [("narrate", 2)]
    assert s.piece_ready(2, 30.0, 40, last=True) == [] and s.complete(2) and not s.archived
    assert s.audio() == {1: {"pieces": [30.0], "complete": True}, 2: {"pieces": [30.0], "complete": True}}
    assert s.narrate_done(2, 42) == [("archive",)]
    assert s.archive_done(50) == [] and s.finished and s.running == {}


def test_a_listener_at_the_end_of_the_pieces_stops_waiting_when_the_section_is_whole():
    s = Scheduler([1, 2], {})
    s.start(0)
    s.opening_done(10)
    s.piece_ready(1, 30.0, 15, index=0)                   # the narrator has not said it was the last
    at_the_end = {"state": "waiting", "section": 1, "piece": 1, "t": 30.0, "waiting_for": 1, "since": 45.0,
                  "reason": "end_of_audio"}
    s.player(at_the_end, 46)
    assert s.hold()["section"] == 1
    s.narrate_done(1, 50)
    assert s.hold() is None and s.holds[0]["end"] == 50 and s.holds[0]["seconds"] == 5.0
    s.player(at_the_end, 51)                              # a report from before the page knew
    assert len(s.holds) == 1


def test_holds_are_recorded_from_the_position_reports():
    s = Scheduler([1, 2, 3], {})
    s.start(0)
    s.opening_done(10)
    s.piece_ready(1, 100.0, 12, last=True)
    s.narrate_done(1, 13)
    playing = {"state": "playing", "section": 1, "piece": 0, "t": 50.0, "waiting_for": None, "since": None}
    waiting = {"state": "waiting", "section": 2, "piece": 0, "t": 0, "waiting_for": 2, "since": 112.0,
               "reason": "end_of_audio"}
    s.player(playing, 62)
    assert s.holds == [] and s.hold() is None and s.marker(1) == "playing" and s.marker(2) == "being_written"
    s.player(waiting, 114)
    s.tick(120)
    assert s.holds == [{"section": 2, "start": 112.0, "end": None, "seconds": 8.0, "reason": "end_of_audio"}]
    assert s.marker(1) == "played" and s.marker(2) == "being_written"
    s.draft_done(2, 130)
    s.draft_done(3, 140)
    s.lock_done(2, 150)
    s.piece_ready(2, 30.0, 160)                                         # audio: the wait is over at once
    assert s.holds[0]["end"] == 160 and s.holds[0]["seconds"] == 48.0 and s.hold() is None
    s.player(waiting, 161)                                              # the page has not caught up yet
    assert len(s.holds) == 1 and s.marker(2) == "ready"
    s.player({**playing, "section": 2, "t": 3.0}, 164)
    assert s.marker(2) == "playing" and len(s.holds) == 1
    # out of audio in the middle of section 2: a hold, and never a solo edit (it is locked)
    s.player({"state": "waiting", "section": 2, "piece": 1, "t": 30.0, "waiting_for": 2, "since": 190,
              "reason": "end_of_audio"}, 191)
    s.player({"state": "waiting", "section": 2, "piece": 1, "t": 30.0, "waiting_for": 2, "since": 190,
              "reason": "end_of_audio"}, 899)
    assert s.tick(900) == [] and s.holds[1]["section"] == 2 and len(s.holds) == 2 and s.solo == []
    s.piece_ready(2, 30.0, 901, last=True)
    assert s.holds[1]["end"] == 901
    # the listener goes back to listening without new audio (a tap on a played marker): the hold ends with the report
    s.player({**waiting, "waiting_for": 3, "section": 3, "since": 950, "reason": "skip"}, 951)
    s.player({**playing, "section": 1}, 960)
    assert s.holds[2] == {"section": 3, "start": 950, "end": 960, "seconds": 10.0, "reason": "skip"}


def test_open_sections_are_those_whose_draft_has_not_begun():
    s = Scheduler([1, 2, 3, 4], {"L01": 3})
    assert s.open_sections() == [2, 3, 4]
    s.start(0)
    assert s.opening_done(10) == [("draft", 2), ("narrate", 1)]
    assert s.open_sections() == [3, 4]
    assert s.row_added("N01", 2, 11) == [] and not s.has_row("N01")
    assert s.row_added("N02", 4, 11) == [("research", "N02")]
    assert s.row_added("N02", 4, 12) == []                               # the same row twice


def test_no_note_row_for_the_section_a_listener_waits_for_or_the_one_its_edit_needs():
    """A listener held on section k waits for the draft of k and, for the paired edit, the draft of
    k+1. A note's agent for either would make them wait for it as well, so neither is open."""
    waiting = {"state": "waiting", "section": 2, "piece": 0, "t": 0, "waiting_for": 2, "since": 80, "reason": "skip"}
    playing = {"state": "playing", "section": 1, "piece": 1, "t": 50, "waiting_for": None, "since": None}
    s = Scheduler([1, 2, 3, 4, 5], {"L01": 2, "L02": 3}, max_hold=180)
    s.start(0)
    s.opening_done(75)
    assert s.open_sections() == [2, 3, 4, 5]
    s.player(waiting, 80)
    assert s.open_sections() == [4, 5]
    assert s.row_added("N01", 2, 81) == [] and s.row_added("N02", 3, 81) == []
    assert not s.has_row("N01") and not s.has_row("N02")
    assert s.row_added("N03", 5, 82) == [("research", "N03")]            # further on is still open
    s.player(playing, 90)                                                # they went back to listening
    assert s.open_sections() == [2, 3, 4, 5]
    s.player({**waiting, "since": 95}, 95)
    s.row_done("L01", 100)
    s.draft_done(2, 120)
    assert s.open_sections() == [4, 5]             # the draft of 3 has not begun (L02 is out), but the edit of 2 needs it
    assert s.player({**waiting, "since": 95}, 275) == [("lock", 2, "solo")]
    assert s.open_sections() == [4, 5]
    s.lock_done(2, 330)
    assert s.hold()["end"] is None and s.open_sections() == [3, 4, 5]    # locked: only its narration is awaited
    # with three sections nothing is open while the listener waits for section 2
    s = Scheduler([1, 2, 3], {"L01": 2})
    s.start(0)
    s.opening_done(75)
    s.player(waiting, 80)
    assert s.open_sections() == []


def test_a_note_during_a_hold_does_not_lengthen_it():
    """Skip at two minutes: the listener waits for section 2 from 207 s. A note at 250 s wants a row
    for section 2, or for section 3; either agent would be waited for (it used to cost 28 s of
    hold, or section 2 its paired edit). The hold is what it would have been without the note."""
    kw = dict(n=4, skips=(120,), research_parallel=8)
    for late, section in (("L01", 2), ("L03", 3)):
        plain = run(Scenario(research={late: 300, "default": 240}, **kw))
        r = run(Scenario(research={late: 300, "default": 240}, add_rows=((250, "N01", section, 120),), **kw))
        assert "N01" not in r.rows and r.count(("research", "N01")) == 0
        assert r.holds() == plain.holds() and r.modes == plain.modes and r.archived == plain.archived
        assert (r.holds()[0]["seconds"], r.modes[2]) == {2: (258, "solo"), 3: (216, "paired")}[section]
    taken = run(Scenario(research={"L03": 300, "default": 240}, add_rows=((250, "N01", 4, 120),), **kw))
    assert taken.rows["N01"] == 4 and taken.when(("research", "N01")) == 250      # further on is still open


# --- eta() -------------------------------------------------------------------------------------

EVEN = {k: [60.0] * 4 for k in range(1, 6)}
MED = {"opening": 75, "research": 240, "draft": 45, "lock": 60, "first_piece": 18, "narrate": 72}


@pytest.mark.parametrize("n", NS)
def test_eta_matches_the_run_when_every_step_takes_its_median(n):
    probes = (1, 50, 100, 200, 250, 300, 340, 395, 420, 500, 560, 650)
    r = run(Scenario(n=n, audio={k: EVEN[k] for k in range(1, n + 1)}, probes=probes, medians=MED,
                     pauses=((30, 2000),)))
    for (t, k), eta in r.etas.items():
        assert eta == pytest.approx(max(0.0, r.ready[k] - t), abs=0.01), (t, k)


def test_eta_follows_the_solo_fallback_and_row_timeouts():
    # a listener waiting for section 2 while the draft of 3 is far off: the estimate is the solo edit's
    r = run(Scenario(n=4, audio={k: EVEN[k] for k in range(1, 5)}, skips=(120,),
                     research={"L03": None, "default": 240},
                     probes=(300, 390, 500), medians=MED))
    assert r.solo == {2: 75 + 18 + 120 + 180}
    for (t, k), eta in r.etas.items():
        if k <= 2:
            assert eta == pytest.approx(max(0.0, r.ready[k] - t), abs=0.01), (t, k)
        else:                              # L03 is expected back at its median; it never comes
            assert eta is not None and eta <= r.ready[k] - t, (t, k)


def test_eta_without_an_estimate():
    s = Scheduler([1, 2, 3], {"L01": 2})
    assert s.eta(1, 0, MED) is None                       # nothing is under way yet
    s.start(0)
    assert s.eta(1, 10, MED) == 75 - 10 + 18
    assert s.eta(1, 10, {}) is None                       # no medians: the first run
    assert s.eta(1, 10, {"opening": 75}) is None          # the opening's, but not the narration's
    assert s.eta(9, 10, MED) is None
    s.opening_done(75)
    s.piece_ready(1, 60.0, 93)
    assert s.eta(1, 100, {}) == 0.0                       # ready needs no estimate
    assert checkin.decide(s.eta(2, 100, {})) == "check_in"


def test_eta_of_an_overdue_action_is_not_zero():
    """A draft twice past its median is given a quarter of it again, not nothing: an estimate of
    'any second now' would hold the check-in back for a section that is minutes away."""
    s = Scheduler([1, 2, 3], {})
    s.start(0)
    s.opening_done(75)
    # the rest of draft 2 (overdue), draft 3, lock 2 paired, then section 2's first piece
    assert s.eta(2, 75 + 100, MED) == pytest.approx(0.25 * 45 + 45 + 60 + 18)
    assert checkin.decide(s.eta(2, 175, MED)) == "check_in"


# --- the check-in decision (acceptance check 5, server side) and the next marker (check 6) ------

def test_checkin_decide():
    assert checkin.decide(14) == "pause"
    assert checkin.decide(15) == "pause"
    assert checkin.decide(16) == "check_in"
    assert checkin.decide(None) == "check_in"             # no estimate: the guide comes in
    assert checkin.decide(0) == "pause"
    assert checkin.decide(20, short_wait=30) == "pause" and checkin.decide(31, short_wait=30) == "check_in"


def test_checkin_decide_reads_the_configured_short_wait(cfg):
    short = cfg["fast"]["short_wait_seconds"]
    assert short == 15
    assert checkin.decide(short, short) == "pause" and checkin.decide(short + 1, short) == "check_in"


def test_next_marker():
    assert [checkin.next_marker(k, 4) for k in (1, 2, 3, 4)] == [2, 3, 4, None]
    assert checkin.next_marker(1, 1) is None
