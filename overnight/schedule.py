"""The overnight clock rules (docs/overnight-chapters.md: Decisions, Planning, Overnight runner).

Pure: no files, no clock. Every function takes the listener's settings (profiles.settings) and `now`.
Datetimes are naive local times (or all aware, in one zone): days and times are combined with
date + time, never by adding hours across a night, so a change of clocks moves nothing.

    build      at overnight.start on a morning in overnight.days (02:00, Mon-Fri). The start is also the
               change cutoff. A build that has not started BUILD_WINDOW_HOURS after the start (07:00) waits
               for the next build day: a laptop that slept through the night does not build at lunch.
    plan       the card for the next build is written at overnight.plan_at (18:00) on the first evening
               after the build before it: Monday 18:00 for Tuesday, Friday 18:00 for Monday (the eve of a
               non-build day writes the card for the next build). A listener with no card at all gets one
               at once. A failed card is not replanned: the next build retries it (once more; see below).
    change     before the cutoff a change rewrites the card; from the cutoff, and while building, it goes
               to queue.jsonl for the next build day.

Retry: a failed card is built again on a later build morning than its last attempt (`attempt_at`, set
by the runner at each build start), never the same night: an outage that failed the build at 03:00 is
likely still there at 03:15. The runner counts builds in `attempts` and scheduled builds in
`scheduled_attempts`; from MAX_ATTEMPTS scheduled builds on, a failed card is replanned instead (a
chapter that cannot finish, say one past its runaway stop, must not block every night after it).
Manual builds (`run --now`, `resume`) do not use up the scheduled tries, so two failed daytime tests
of tonight's card still leave tonight's build. A card without `scheduled_attempts` counts its
`attempts` (without either, a failed card counts as tried once).

Planning failures: plan_backoff() keeps a pass that keeps failing from being paid for every quarter
hour: one failed pass an hour at most, PLAN_TRIES in a planning window (from the last build start),
then nothing until the next window or a manual `wl overnight plan`.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
BUILD_WINDOW_HOURS = 5  # 02:00 -> 07:00: the latest a scheduled build may still start
MAX_ATTEMPTS = 2  # scheduled builds of one card (the first and one retry) before a failed card is replanned
PLAN_TRIES = 3  # failed planning passes in one planning window before the tick waits for the next one
PLAN_RETRY_MINUTES = 60  # at most one failed planning pass in this long


def _hhmm(v: str) -> time:
    h, m = str(v).strip().split(":")
    return time(int(h), int(m))


def _ov(settings: dict) -> dict:
    return settings.get("overnight") or {}


def _days(settings: dict) -> set[int]:
    return {DAYS.index(d[:3].lower()) for d in (_ov(settings).get("days") or []) if d[:3].lower() in DAYS}


def _at(d: date, t: time, like: datetime) -> datetime:
    return datetime.combine(d, t, tzinfo=like.tzinfo)


def enabled(settings: dict) -> bool:
    return bool(_ov(settings).get("enabled", True))


def next_start(settings: dict, now: datetime) -> datetime | None:
    """The next build start at or after now, whether or not building is on (None: no build days)."""
    days, start = _days(settings), _hhmm(_ov(settings).get("start", "02:00"))
    for k in range(8):
        d = now.date() + timedelta(days=k)
        if d.weekday() in days and _at(d, start, now) >= now:
            return _at(d, start, now)
    return None


def last_start(settings: dict, now: datetime) -> datetime | None:
    """The latest build start at or before now (None: no build days)."""
    days, start = _days(settings), _hhmm(_ov(settings).get("start", "02:00"))
    for k in range(8):
        d = now.date() - timedelta(days=k)
        if d.weekday() in days and _at(d, start, now) <= now:
            return _at(d, start, now)
    return None


def next_build(settings: dict, now: datetime) -> datetime | None:
    """The next build start (at or after now); None if overnight building is off."""
    return next_start(settings, now) if enabled(settings) else None


def plan_opens(settings: dict, build: datetime) -> datetime:
    """When the card for this build is written: plan_at on the first evening after the build before
    it (Friday 18:00 for Monday). If that is not before the build (plan_at == start), straight after
    the build before."""
    prev = last_start(settings, build - timedelta(seconds=1)) or build - timedelta(days=7)
    plan_at = _hhmm(_ov(settings).get("plan_at", "18:00"))
    first = _at(prev.date(), plan_at, build)
    if first <= prev:
        first = _at(prev.date() + timedelta(days=1), plan_at, build)
    return first if first < build else prev


def _attempts(card: dict) -> int:
    if "scheduled_attempts" in card:
        return int(card.get("scheduled_attempts") or 0)
    return int(card.get("attempts") or 1)


def _stamp(t: datetime) -> str:
    """Local wall-clock time as the cards write it (ISO, to the second, any offset dropped)."""
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def _tried_before(card: dict, start: datetime) -> bool:
    """Did the card's last build attempt begin before this build start? (No record: yes.)"""
    last = str(card.get("attempt_at") or card.get("locked_at") or "")[:19]
    return not last or last < _stamp(start)


def _stale_building(card: dict, morning: date) -> bool:
    """A card still 'building' from a build that started before this morning's: that build died."""
    locked = str(card.get("locked_at") or "")[:10]
    return bool(locked) and locked < morning.isoformat()


def build_due(card: dict | None, settings: dict, now: datetime) -> bool:
    """Should the scheduled build start now? Building is on, a build start has passed within the
    window (BUILD_WINDOW_HOURS), and the card is planned for this morning (or an earlier one it
    missed), or failed and due its retry (tried before this morning's start, scheduled tries left), or
    left 'building' by a build that died on an earlier night. A ready card is never rebuilt."""
    if not card or not enabled(settings):
        return False
    start = last_start(settings, now)
    if start is None or now >= start + timedelta(hours=BUILD_WINDOW_HOURS):
        return False
    morning = start.date()
    status = card.get("status")
    if status == "planned":
        return str(card.get("for_morning") or "") <= morning.isoformat()
    if status == "failed":
        return (_attempts(card) < MAX_ATTEMPTS and _tried_before(card, start)
                and str(card.get("for_morning") or "") <= morning.isoformat())
    if status == "building":
        return _stale_building(card, morning)
    return False


is_build_time = build_due


def plan_due(card: dict | None, settings: dict, now: datetime) -> bool:
    """Should the planning pass write a new card now? Building is on, and: there is no card at all;
    or the card is ready (or failed past its retries) and not already for the next build, and the
    next build's planning time has come. A planned card is kept (a missed one is built at the next
    build), a building one is left alone, and a failed one is retried by the next build, not replanned."""
    nb = next_build(settings, now)
    if nb is None:
        return False
    if not card:
        return True
    status = card.get("status")
    if status in ("planned", "building"):
        return False
    if status == "failed" and _attempts(card) < MAX_ATTEMPTS:
        return False
    if card.get("for_morning") == nb.date().isoformat():
        return False
    return now >= plan_opens(settings, nb)


def plan_backoff(failed: list[str], settings: dict, now: datetime) -> str | None:
    """failed: when each failed planning pass began (ISO, local). A reason for the tick not to plan now,
    or None: a pass failed less than PLAN_RETRY_MINUTES ago, or PLAN_TRIES failed in this planning
    window (since the last build start). A manual `wl overnight plan` is not held back."""
    opened = last_start(settings, now)
    since, upto = (_stamp(opened) if opened else ""), _stamp(now)
    recent = sorted(t[:19] for t in failed if since <= str(t)[:19] <= upto)
    if len(recent) >= PLAN_TRIES:
        return (f"{len(recent)} planning passes failed since {since or 'the last build'}; the next try is in "
                "the next planning window, or run `wl overnight plan`")
    if recent and recent[-1] > _stamp(now - timedelta(minutes=PLAN_RETRY_MINUTES)):
        return f"a planning pass failed at {recent[-1][11:16]}; the next try waits an hour"
    return None


def can_change(card: dict | None, settings: dict, now: datetime) -> tuple[bool, str]:
    """Can tonight's card still change? (True, "") before the cutoff. From the cutoff, while a build
    is due or running, (False, reason): the change goes to queue.jsonl for the next build day."""
    if card and card.get("status") == "building" and not (
            (start := last_start(settings, now)) and _stale_building(card, start.date())):
        return False, "Tonight's chapter is already being made, so a change goes to the next build day."
    if build_due(card, settings, now):
        return False, "It's past the cutoff and tonight's chapter is about to be made, so a change goes to the next build day."
    return True, ""


def morning_word(at: datetime) -> str:
    return "morning" if at.hour < 12 else "evening"


def next_label(settings: dict, now: datetime) -> str:
    """The card's small line: 'Ready tomorrow morning', 'Ready this morning', 'Next chapter Monday'."""
    nb = next_build(settings, now)
    if nb is None:
        return "Overnight building is off" if _days(settings) else "No build days are set"
    days = (nb.date() - now.date()).days
    if days == 0:
        return f"Ready this {morning_word(nb)}"
    if days == 1:
        return f"Ready tomorrow {morning_word(nb)}"
    return f"Next chapter {DAY_NAMES[nb.weekday()]}"
