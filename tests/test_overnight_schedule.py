"""overnight/schedule.py: the pure clock rules (build days, cutoff, planning time, labels; check 9)."""

from datetime import datetime, timedelta, timezone

import pytest

from overnight import schedule as S


def st(**over):
    ov = {"enabled": True, "days": ["mon", "tue", "wed", "thu", "fri"], "start": "02:00", "plan_at": "18:00",
          "runaway_stop_usd": 50}
    ov.update(over)
    return {"overnight": ov, "chapter": {"minutes": 20, "mode": "surprise"}}


def at(day, hh=0, mm=0):  # November 2026: the 16th is a Monday
    return datetime(2026, 11, day, hh, mm)


def card(status="planned", for_morning="2026-11-17", **more):
    return {"status": status, "for_morning": for_morning, **more}


# --- next build ---------------------------------------------------------------------

@pytest.mark.parametrize("now, want", [
    (at(16, 18), at(17, 2)),          # Monday evening -> Tuesday 02:00
    (at(17, 1, 59), at(17, 2)),       # just before the cutoff: this morning
    (at(17, 2), at(17, 2)),           # at the start itself
    (at(17, 2, 1), at(18, 2)),        # once it has started: tomorrow
    (at(20, 10), at(23, 2)),          # Friday daytime -> Monday
    (at(20, 18), at(23, 2)),          # Friday evening -> Monday
    (at(21, 12), at(23, 2)),          # Saturday
    (at(22, 23), at(23, 2)),          # Sunday night
])
def test_next_build(now, want):
    assert S.next_build(st(), now) == want


def test_next_build_off_or_without_days():
    assert S.next_build(st(enabled=False), at(16, 18)) is None
    assert S.next_start(st(enabled=False), at(16, 18)) == at(17, 2)  # the planner still dates a card
    assert S.next_build(st(days=[]), at(16, 18)) is None
    assert S.next_build(st(days=["sat"]), at(16, 18)) == at(21, 2)  # weekend builds when wanted


def test_other_start_times_and_aware_datetimes():
    assert S.next_build(st(start="23:30"), at(16, 23, 0)) == at(16, 23, 30)
    tz = timezone(timedelta(hours=-5))
    now = datetime(2026, 11, 16, 18, 0, tzinfo=tz)
    assert S.next_build(st(), now) == datetime(2026, 11, 17, 2, 0, tzinfo=tz)


# --- build due ----------------------------------------------------------------------

def test_build_due_on_a_build_morning():
    c = card()
    assert not S.build_due(c, st(), at(17, 1, 59))  # before the start
    assert S.build_due(c, st(), at(17, 2))
    assert S.build_due(c, st(), at(17, 6, 59))      # a laptop that woke late still builds
    assert not S.build_due(c, st(), at(17, 7))      # not past the window: no afternoon build
    assert not S.build_due(c, st(), at(17, 15))


def test_check_9_off_and_non_build_days():
    assert not S.build_due(card(), st(enabled=False), at(17, 2, 5))
    sat = card(for_morning="2026-11-21")
    assert not S.build_due(sat, st(), at(21, 2, 5))  # Saturday is not a build day
    assert not S.build_due(sat, st(), at(22, 2, 5))  # nor Sunday
    assert S.build_due(card(for_morning="2026-11-23"), st(), at(23, 2, 5))  # Monday is


def test_build_due_by_card_state():
    s, now = st(), at(17, 2, 10)
    assert not S.build_due(None, s, now)
    assert not S.build_due(card("ready"), s, now)
    assert not S.build_due(card(for_morning="2026-11-18"), s, now)  # tomorrow's card waits
    assert S.build_due(card(for_morning="2026-11-16"), s, now)  # a missed card is built at the next build
    assert S.build_due(card("failed", "2026-11-16"), s, now)  # retried once
    assert not S.build_due(card("failed", "2026-11-16", attempts=2), s, now)  # then replanned
    assert not S.build_due(card("building", locked_at="2026-11-17T02:00:03-05:00"), s, now)  # running now
    assert S.build_due(card("building", "2026-11-16", locked_at="2026-11-16T02:00:03"), s, now)  # died last night


def test_a_build_window_across_midnight():
    s = st(start="23:00")
    c = card(for_morning="2026-11-16")
    assert S.build_due(c, s, at(16, 23, 30))
    assert S.build_due(c, s, at(17, 1, 0))  # still Monday's build
    assert not S.build_due(c, s, at(17, 4, 0))


# --- plan due -----------------------------------------------------------------------

def test_plan_due_the_evening_before_a_build():
    ready = card("ready", "2026-11-16")  # Monday's chapter, made this morning
    assert not S.plan_due(ready, st(), at(16, 17, 59))
    assert S.plan_due(ready, st(), at(16, 18))
    assert S.plan_due(ready, st(), at(17, 1))  # the laptop slept through 18:00: plan before the build
    assert not S.plan_due(card("planned", "2026-11-17"), st(), at(16, 19))  # already for Tuesday


def test_friday_evening_writes_mondays_card_and_the_weekend_keeps_it():
    fri = card("ready", "2026-11-20")
    assert not S.plan_due(fri, st(), at(20, 17))
    assert S.plan_due(fri, st(), at(20, 18))
    assert S.plan_due(fri, st(), at(21, 11))  # missed on Friday: Saturday catches up
    monday = card("planned", "2026-11-23")
    for now in (at(20, 18, 5), at(21, 18), at(22, 18), at(22, 23)):
        assert not S.plan_due(monday, st(), now)
    assert S.plan_opens(st(), at(23, 2)) == at(20, 18)
    assert S.plan_opens(st(), at(17, 2)) == at(16, 18)


def test_plan_due_other_cases():
    s = st()
    assert S.plan_due(None, s, at(17, 10))  # a listener with no card gets one at once
    assert not S.plan_due(None, st(enabled=False), at(17, 10))  # check 9: nothing planned while off
    assert not S.plan_due(card("ready", "2026-11-16"), st(enabled=False), at(16, 19))
    assert not S.plan_due(card("building", "2026-11-17"), s, at(17, 18, 30))
    assert not S.plan_due(card("failed", "2026-11-17"), s, at(17, 18, 30))  # the next build retries it
    assert S.plan_due(card("failed", "2026-11-17", attempts=2), s, at(17, 18, 30))
    assert not S.plan_due(card("ready", "2026-11-18"), s, at(17, 18, 30))  # built early (--now): kept
    assert S.plan_opens(st(plan_at="02:00"), at(17, 2)) == at(16, 2)  # plan_at == start: right after the last build


# --- can change ---------------------------------------------------------------------

def test_can_change_before_and_after_the_cutoff():
    c = card()
    assert S.can_change(c, st(), at(16, 23)) == (True, "")
    assert S.can_change(c, st(), at(17, 1, 59))[0]
    ok, why = S.can_change(c, st(), at(17, 2))
    assert not ok and "next build day" in why
    ok, why = S.can_change(card("building", locked_at="2026-11-17T02:00:00"), st(), at(17, 3))
    assert not ok and "being made" in why
    assert S.can_change(card("ready", "2026-11-17"), st(), at(17, 10))[0]  # after the build, before 18:00
    assert S.can_change(card(for_morning="2026-11-23"), st(), at(21, 2, 30))[0]  # Saturday: no build today
    assert S.can_change(c, st(enabled=False), at(17, 2, 30))[0]
    assert S.can_change(None, st(), at(17, 2, 30))[0]


# --- labels -------------------------------------------------------------------------

@pytest.mark.parametrize("now, label", [
    (at(16, 18), "Ready tomorrow morning"),
    (at(17, 1), "Ready this morning"),
    (at(20, 18), "Next chapter Monday"),
    (at(21, 9), "Next chapter Monday"),
    (at(22, 18), "Ready tomorrow morning"),
])
def test_next_label(now, label):
    assert S.next_label(st(), now) == label


def test_next_label_off():
    assert S.next_label(st(enabled=False), at(16, 18)) == "Overnight building is off"
    assert S.next_label(st(start="21:00"), at(16, 18)) == "Ready this evening"
