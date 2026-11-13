"""faststart/status.py: the opening steps, the countdown to the first audio, each section's step and
the next section's estimate, as status.json carries them (Ian, 2026-11-13: the steps instead of a timer)."""

from types import SimpleNamespace as NS

import pytest

from faststart import run as fast_run
from faststart import status
from faststart.scheduler import Scheduler
from stages.common import Run

MEDIANS = {"scout": 60.0, "outline": 80.0, "opening": 50.0, "first_piece": 10.0, "first_audio": 210.0}


def states(listed):
    return [(s["key"], s["state"]) for s in listed]


# --- the four steps to the first audio ---------------------------------------------------------------

def test_the_steps_before_the_current_one_are_done_and_the_rest_to_do():
    assert [s["label"] for s in status.steps("scout")] == [
        "Looking into it", "Planning the chapter", "Writing the opening", "Recording the opening"]
    assert states(status.steps("scout")) == [("scout", "doing"), ("outline", "todo"), ("opening", "todo"),
                                             ("first_piece", "todo")]
    assert states(status.steps("outline")) == [("scout", "done"), ("outline", "doing"), ("opening", "todo"),
                                               ("first_piece", "todo")]
    assert states(status.steps("opening")) == [("scout", "done"), ("outline", "done"), ("opening", "doing"),
                                               ("first_piece", "todo")]
    assert states(status.steps("first_piece")) == [("scout", "done"), ("outline", "done"), ("opening", "done"),
                                                   ("first_piece", "doing")]
    assert {s["state"] for s in status.steps(None)} == {"done"}  # the first audio is playable


# --- the countdown -----------------------------------------------------------------------------------

def test_the_countdown_is_the_current_steps_median_and_every_later_one():
    assert status.first_audio_by("scout", 100.0, 130.0, MEDIANS) == 100.0 + 60 + 80 + 50 + 10
    assert status.first_audio_by("outline", 200.0, 200.0, MEDIANS) == 200.0 + 80 + 50 + 10
    assert status.first_audio_by("first_piece", 400.0, 405.0, MEDIANS) == 410.0
    # one fixed end time while the step runs: the seconds left shrink, the status does not change
    assert status.first_audio_by("scout", 100.0, 101.0, MEDIANS) == status.first_audio_by("scout", 100.0, 159.0, MEDIANS)


@pytest.mark.parametrize("medians", [None, {}, {**MEDIANS, "opening": None}, {k: None for k in MEDIANS}])
def test_no_medians_no_countdown(medians):
    assert status.first_audio_by("scout", 0.0, 1.0, medians) is None


def test_a_step_past_its_median_gives_no_countdown():
    assert status.first_audio_by("scout", 100.0, 160.0, MEDIANS) is None  # its 60 s are up
    assert status.first_audio_by("first_piece", 400.0, 470.0, MEDIANS) is None
    assert status.first_audio_by("outline", 200.0, 279.0, MEDIANS) == 340.0  # a second left in it


def test_no_countdown_without_a_start_or_after_the_first_audio(tmp_path):
    assert status.first_audio_by("opening", None, 0.0, MEDIANS) is None  # between two tries of the step
    assert status.first_audio_by(None, 0.0, 0.0, MEDIANS) is None
    r = Run("run", tmp_path / "run")
    f = status.fields(r, None, None, step="outline", first_audio_in=95.4)
    assert f["first_audio_in"] == 95 and states(f["steps"])[1] == ("outline", "doing")
    assert status.fields(r, None, None, step="outline", first_audio_in=-3)["first_audio_in"] == 0
    done = status.fields(r, None, None, first_audio=True, step="first_piece", first_audio_in=5)
    assert done["first_audio_in"] is None and {s["state"] for s in done["steps"]} == {"done"}
    assert status.fields(r, None, None)["steps"][0]["state"] == "doing" and status.fields(r, None, None)["first_audio_in"] is None


def test_the_status_carries_the_end_of_the_step_under_way(tmp_path):
    """step_ends_in: the page and the guide drop the countdown once the step passes its median, though the
    scout and the outline write no status while they run."""
    r = Run("run", tmp_path / "run")
    assert status.fields(r, None, None, step="scout", first_audio_in=200, step_ends_in=60)["step_ends_in"] == 60
    assert status.fields(r, None, None, step="scout", first_audio_in=200, step_ends_in=59.6)["step_ends_in"] == 60
    assert status.fields(r, None, None, step="scout", first_audio_in=200, step_ends_in=-2)["step_ends_in"] == 0
    assert status.fields(r, None, None, first_audio=True, first_audio_in=200, step_ends_in=60)["step_ends_in"] is None
    assert status.fields(r, None, None, step="scout", first_audio_in=None, step_ends_in=60)["step_ends_in"] is None
    assert status.fields(r, None, None)["step_ends_in"] is None


def test_settled_leaves_out_only_the_countdowns_seconds():
    a = {"label": "Planning the chapter", "first_audio_in": 120, "step_ends_in": 50,
         "sections": [{"n": 1, "step": "writing", "ready_in": 40}]}
    b = {"label": "Planning the chapter", "first_audio_in": 95, "step_ends_in": 25,
         "sections": [{"n": 1, "step": "writing", "ready_in": 15}]}
    assert status.settled(a) == status.settled(b) == {"label": "Planning the chapter", "sections": [{"n": 1, "step": "writing"}]}
    assert status.settled(a) != status.settled({**b, "label": "Writing the opening"})
    assert a["first_audio_in"] == 120 and a["sections"][0]["ready_in"] == 40  # the fields themselves are untouched


# --- each section's step -----------------------------------------------------------------------------

def sched_of(rows=None, sections=4) -> Scheduler:
    s = Scheduler(list(range(1, sections + 1)), rows or {"L01": 2, "L02": 3, "L03": 4}, research_parallel=1)
    s.start(0.0)
    return s


def steps_of(s: Scheduler) -> dict[int, str]:
    snap = s.snapshot()
    return {n: status.section_step(n, info, snap["rows"], snap["running"]) for n, info in snap["sections"].items()}


def test_each_section_shows_what_is_happening_to_it():
    s = sched_of()
    assert steps_of(s) == {1: "writing", 2: "researching", 3: "researching", 4: "researching"}  # one row at a time
    s.opening_done(1.0)
    assert steps_of(s)[1] == "recording"  # locked: narration under way
    s.piece_ready(1, 5.0, 2.0)
    assert steps_of(s)[1] == "ready"
    s.row_done("L01", 3.0)
    assert steps_of(s) == {1: "ready", 2: "writing", 3: "researching", 4: "researching"}  # its draft is running
    s.row_done("L02", 4.0)
    s.draft_done(2, 5.0)
    assert steps_of(s)[2] == "editing"  # drafted: its lock waits for the draft of 3, beside which it is edited
    assert steps_of(s)[3] == "writing"
    s.draft_done(3, 6.0)
    assert steps_of(s)[2] == "editing" and steps_of(s)[3] == "editing"
    s.lock_done(2, 7.0)
    assert steps_of(s)[2] == "recording"  # queued behind section 1's narration: still its next step
    s.row_done("L03", 8.0)
    assert steps_of(s)[4] == "writing"


def test_a_section_with_its_research_done_waits_its_turn_as_writing():
    s = sched_of(rows={"L01": 2})  # sections 3 and 4 have no rows
    assert steps_of(s) == {1: "writing", 2: "researching", 3: "writing", 4: "writing"}


def test_the_section_step_reads_the_snapshot_as_fast_state_json_holds_it():
    import json

    s = sched_of()
    snap = json.loads(json.dumps(s.snapshot()))  # section numbers become strings, actions lists
    assert status.section_step(2, snap["sections"]["2"], snap["rows"], snap["running"]) == "researching"


def test_the_next_section_up_is_the_first_not_ready():
    s = sched_of()
    assert status.next_up(s.snapshot()) == 1
    s.opening_done(1.0)
    s.piece_ready(1, 5.0, 2.0)
    assert status.next_up(s.snapshot()) == 2
    assert status.next_up(None) is None
    assert status.next_up({"sections": {"1": {"state": "ready"}}}) is None


def test_sections_carry_their_step_and_only_the_next_one_its_estimate(tmp_path):
    r = Run("run", tmp_path / "run")
    outline = {"title": "T", "sections": [{"n": n, "title": f"Part {n}"} for n in (1, 2, 3)]}
    s = sched_of(rows={"L01": 2, "L02": 3}, sections=3)
    s.opening_done(1.0)
    s.piece_ready(1, 5.0, 2.0)
    listed = status.sections(r, outline, s.snapshot(), ready_in=(2, 179.6))
    assert [(x["n"], x["step"], x["ready_in"]) for x in listed] == [(1, "ready", None), (2, "researching", 180),
                                                                     (3, "researching", None)]
    assert all(x["ready_in"] is None for x in status.sections(r, outline, s.snapshot()))


def test_the_guide_hears_the_step_of_a_section_being_written():
    st = {"mode": "fast", "first_audio": True, "sections": [
        {"n": 1, "title": "One", "state": "playing", "step": "ready"},
        {"n": 2, "title": "Two", "state": "being_written", "step": "editing"},
        {"n": 3, "title": "Three", "state": "being_written"}]}  # a status from before the steps
    assert status.plain(st)["sections"] == [{"title": "One", "state": "playing"}, {"title": "Two", "state": "editing"},
                                            {"title": "Three", "state": "being written"}]


@pytest.mark.parametrize("done,word,step", [
    ({"rows": ["L01", "L02"], "drafts": [1, 2], "locked": [1, 2], "narrated": [1, 2],
      "pieces": {1: [3.0], 2: [3.0]}}, "Researching", "researching"),
    ({"rows": ["L01", "L02", "L03"], "drafts": [1, 2, 3], "locked": [1, 2], "narrated": [1, 2],
      "pieces": {1: [3.0], 2: [3.0]}}, "Editing", "editing"),
    ({"rows": ["L01", "L02", "L03"], "drafts": [1, 2, 3], "locked": [1, 2, 3], "narrated": [1, 2],
      "pieces": {1: [3.0], 2: [3.0]}}, "Recording", "recording"),
])
def test_the_line_while_waiting_names_the_same_step_as_the_marker(done, word, step):
    """After the first audio, a listener waiting for a part reads the run's label on the line and the
    part's step on its marker just below: the two say the same thing."""
    s = Scheduler([1, 2, 3, 4], {"L01": 2, "L02": 3, "L03": 3}, done=done)
    s.start(0.0)
    snap = s.snapshot()
    assert status.section_step(3, snap["sections"][3], snap["rows"], snap["running"]) == step
    assert fast_run._Driver._where(NS(sched=s, first_audio=True)) == ("section 3", f"{word} part 3 of 4")
