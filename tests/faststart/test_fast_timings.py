"""faststart/timings.py: a run's step lengths from its timeline, and the medians of earlier runs."""

import json

from fast_run_fakes import no_leaks, offline, world  # noqa: F401

from faststart import base
from faststart import run as fast_run
from faststart import timings as T
from faststart.scheduler import MEDIANS, Scheduler

REQUIRED = {*MEDIANS, "scout", "outline", "first_audio"}


def timeline() -> list[dict]:
    """One whole run, as the driver logs it (the fields the medians read)."""
    e = lambda t, name, **f: {"t": t, "event": name, **f}
    return [
        e(0, T.SEED_SET), e(70, T.SCOUT_DONE, seconds=70.0, adopted=False), e(150, T.OUTLINE_DONE, seconds=80.0),
        e(150, T.OPENING_START), e(151, T.RESEARCH_START, row="L01"),
        e(205, T.OPENING_DONE, seconds=55.0), e(205, T.NARRATE_START, section=1),
        e(212, T.PIECE_READY, section=1, index=0, seconds=24.0, after=7.0), e(212, T.FIRST_AUDIO, section=1),
        e(240, T.PIECE_READY, section=1, index=1, seconds=60.0, after=35.0),
        e(300, T.RESEARCH_DONE, row="L01", seconds=149.0, paid=True),
        e(301, T.RESEARCH_DONE, row="L02", seconds=0.1, paid=False),  # its findings were on disk already
        e(330, T.NARRATE_DONE, section=1, seconds=125.0, resumed=False),
        e(360, T.DRAFT_DONE, section=2, seconds=60.0), e(420, T.DRAFT_DONE, section=3, seconds=50.0),
        e(500, T.LOCK_DONE, section=2, mode="paired", seconds=80.0),
        e(510, T.PIECE_READY, section=2, index=0, seconds=30.0, after=9.0),
        e(560, T.NARRATE_DONE, section=2, seconds=40.0, resumed=True),  # picked up pieces from an earlier try
        e(600, T.LOCK_DONE, section=3, mode="solo", seconds=70.0),
        e(700, T.LOCK_DONE, section=4, mode="final", seconds=95.0),
        e(800, T.HOLD_END, section=3, seconds=33.0),  # a hold's length is not a step
    ]


def test_steps_are_read_from_the_timeline_and_reused_work_is_left_out():
    got = T.steps(timeline())
    assert got == {"opening": [55.0], "research": [149.0], "draft": [60.0, 50.0], "lock": [80.0], "first_piece": [7.0, 9.0],
                   "narrate": [125.0], "scout": [70.0], "outline": [80.0], "first_audio": [212.0],
                   "lock_solo": [70.0], "lock_final": [95.0]}


def test_an_adopted_scout_and_a_continued_run_do_not_count_where_they_would_mislead():
    events = timeline()
    events[1]["adopted"] = True  # the scout ran during the conversation: its length here is the copying
    assert T.steps(events)["scout"] == []
    stopped = [events[0], events[1], {"t": 400, "event": T.CONTINUED}, *events[2:]]
    got = T.steps(stopped)
    assert got["first_audio"] == [] and got["opening"] == [55.0]  # the wait included the stop; the step did not
    late = [*timeline(), {"t": 900, "event": T.CONTINUED}]  # stopped after the first audio: that time stands
    assert T.steps(late)["first_audio"] == [212.0]


def test_update_load_and_estimate(tmp_path, cfg, run):
    cfg["fast"]["timings_file"] = str(tmp_path / "state" / "timings.json")
    assert T.load(cfg) is None and T.estimate(cfg) is None  # no run measured yet: the guide gives no time
    T_path = base.path(run, "timeline")
    T_path.write_text("".join(json.dumps(e) + "\n" for e in timeline()), encoding="utf-8")

    medians = T.update(cfg, run)
    assert set(medians) == REQUIRED | {"lock_solo", "lock_final"}
    assert medians["draft"] == 55.0 and medians["first_piece"] == 8.0 and medians["lock"] == 80.0
    assert T.load(cfg) == medians and T.estimate(cfg) == 212.0
    saved = json.loads(T.path(cfg).read_text(encoding="utf-8"))
    assert [x["run"] for x in saved["runs"]] == ["test-run"] and saved["runs"][0]["steps"]["draft"] == [60.0, 50.0]

    T.update(cfg, run)  # the same run again (it was continued): replaced, not counted twice
    assert len(json.loads(T.path(cfg).read_text(encoding="utf-8"))["runs"]) == 1


def test_medians_are_over_the_last_twenty_runs_and_keep_the_schedulers_names(tmp_path, cfg):
    from stages.common import Run

    cfg["fast"]["timings_file"] = str(tmp_path / "timings.json")
    for i in range(1, 24):  # run i took i seconds to draft, and 100 + i to the first audio
        r = Run.open(f"run-{i:02d}", create=True, runs_dir=tmp_path / "runs")
        events = [{"t": 0, "event": T.SEED_SET}, {"t": 100 + i, "event": T.FIRST_AUDIO},
                  {"t": 200, "event": T.DRAFT_DONE, "section": 2, "seconds": float(i)}]
        base.path(r, "timeline").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        medians = T.update(cfg, r)
    saved = json.loads(T.path(cfg).read_text(encoding="utf-8"))
    assert [x["run"] for x in saved["runs"]] == [f"run-{i:02d}" for i in range(4, 24)]
    assert medians["draft"] == 13.5 and medians["first_audio"] == 113.5  # runs 4..23
    assert set(medians) == REQUIRED  # every name the scheduler reads, and no solo or final lock nobody measured
    assert medians["lock"] is None and medians["research"] is None
    # a run with no timeline adds nothing and breaks nothing
    T.update(cfg, Run.open("empty", create=True, runs_dir=tmp_path / "runs"))
    assert len(json.loads(T.path(cfg).read_text(encoding="utf-8"))["runs"]) == 20


def test_the_schedulers_eta_runs_on_these_medians():
    medians = T.medians_of([{"steps": T.steps(timeline())}])
    sched = Scheduler([1, 2, 3], {"L01": 2, "L02": 3})
    sched.start(0.0)
    assert sched.eta(1, 0.0, medians) == 55.0 + 8.0  # the opening written and edited, then a first piece
    assert sched.eta(2, 0.0, medians) is not None and sched.eta(3, 0.0, medians) > sched.eta(2, 0.0, medians)
    assert sched.eta(1, 0.0, {**medians, "first_piece": None}) is None  # a step never measured: no estimate
    assert sched.eta(1, 0.0, T.medians_of([])) is None


def test_a_real_run_leaves_medians_the_next_run_estimates_from(tmp_path, cfg, monkeypatch):
    w = world(tmp_path, cfg, monkeypatch)
    fast_run.run(w.r, w.cfg, episode=False)
    medians = T.load(w.cfg)
    assert all(medians[k] is not None for k in REQUIRED) and "lock_final" in medians and "lock_solo" not in medians
    file = w.cfg["fast"]["timings_file"]
    again = world(tmp_path / "next", cfg, monkeypatch, name="next-run")
    again.cfg["fast"]["timings_file"] = file  # (the two configs share their fast section: both now point here)
    seen = []
    from stages import new

    real = new.write_status
    monkeypatch.setattr(new, "write_status", lambda r, c, state, **f: seen.append(real(r, c, state, **f)) or seen[-1])
    fast_run.run(again.r, again.cfg, episode=False)
    assert seen[0]["minutes_left"] == 1 and seen[-1]["minutes_left"] == 0  # a time now, where the first run had none
    assert len(json.loads(T.path(w.cfg).read_text(encoding="utf-8"))["runs"]) == 2
