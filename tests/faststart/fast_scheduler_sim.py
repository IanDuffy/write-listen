"""A discrete-event harness for the fast run's scheduler (acceptance check 3).

run(Scenario(...)) plays a Scheduler and a SimPlayer against made-up step durations: every
action the scheduler issues is "executed" by putting its completion on a heap, the player is
stepped at every event, and the player's position reaches the scheduler every few seconds,
as the page's reports do. No threads, no sleeping, no files.

The harness keeps its own books of what really finished (it never asks the scheduler what is
locked or drafted) and checks the rules against them after every event:

- nothing is narrated before it is locked, and narration is in section order;
- one writing action at a time (the opening, a draft, a paired or final lock) plus at most
  one solo lock; research within its limit, section 2's rows first;
- a lock of k never before the lock of k-1; a paired lock only with the draft of k+1 there;
  a draft of n only with the draft of n-1, the lock that revises it, and n's rows in or timed out;
- the solo fallback only after max_hold seconds of the listener waiting for that section,
  never for a section whose lock has begun, and never when a paired lock was possible (the
  next draft is there and the writing lane is free);
- a note's row only for a section whose draft has not begun, and not for the section a
  listener is waiting for or the one after it (its paired edit needs that draft);
- a row given up on (timed out) is not researched again, after a restart either;
- the fallback does fire when it is due, and work that may start has started (no idle lane);
- archive once, only after the last section's narration has returned, and nothing after it;
- no action issued twice (unless it failed, or died with the process before a restart).

A run that does not reach the archive by Scenario.limit raises: that is a deadlock.
Other tests may import this: `from fast_scheduler_sim import Scenario, run`.
"""

from __future__ import annotations

import heapq
import json
from dataclasses import dataclass, field

from faststart.player import SimPlayer
from faststart.scheduler import Scheduler

EPS = 1e-6
ARCHIVE_SECONDS = 5.0


def default_rows(n: int) -> dict[str, int]:
    """Two rows for section 2, one for each later section."""
    rows = {"L01": 2, "L02": 2}
    for section in range(3, n + 1):
        rows[f"L{len(rows) + 1:02d}"] = section
    return rows


def default_audio(n: int) -> dict[int, list[float]]:
    """A seven-minute opening whose first piece is short, then four-minute sections."""
    return {1: [40.0, 80.0, 80.0, 80.0, 80.0, 60.0], **{s: [60.0] * 4 for s in range(2, n + 1)}}


@dataclass
class Scenario:
    n: int = 4
    rows: dict | None = None            # row id -> section; default_rows(n)
    opening: float = 75                 # seconds to write and edit section 1
    research: object = 240              # a number, or {row: seconds, "default": seconds}; None: it never returns
    draft: object = 45                  # a number, or {n: seconds, "default": seconds}
    lock: object = 60                   # a number, or {n or mode: seconds, "default": seconds}
    audio: dict | None = None           # {n: [seconds of each piece]}; default_audio(n)
    synth: float = 0.3                  # seconds to narrate one second of audio
    skips: tuple = ()                   # see SimPlayer
    pauses: tuple = ()
    skip_times: tuple = ()
    max_hold: float = 180
    research_parallel: int = 4
    row_timeout: float = 600
    retries: int = 1
    add_rows: tuple = ()                # (time, row id, section, seconds): a note's extra agent
    fail: dict = field(default_factory=dict)   # action -> how many times it fails before it works
    fail_after: float = 5
    restarts: tuple = ()                # times at which the process dies and a new scheduler restores from files
    report_every: float = 3             # how often the player's position reaches the scheduler
    last_flag: bool = True              # the last piece says last=True; False: only narrate_done tells
    join: float = 0                     # seconds from a section's last piece to its narration returning
    probes: tuple = ()                  # times at which eta() of every section is recorded
    medians: dict | None = None         # for the probes
    limit: float = 4 * 3600


@dataclass
class Result:
    scenario: Scenario
    scheduler: Scheduler
    player: SimPlayer
    trace: list[dict]                   # every event in order: issues, completions, the player's events
    issued: list[tuple[float, tuple]]
    archived: float | None              # when the archive was issued
    ready: dict[int, float]             # when each section's first piece became playable
    narrated: dict[int, float]          # when each section was fully narrated
    drafted: dict[int, float]
    locked: dict[int, float]
    modes: dict[int, str]               # how each section was locked
    solo: dict[int, float]              # section -> when its solo lock was issued
    etas: dict[tuple[float, int], float | None]
    rows: dict[str, int]                # the rows the run ended with
    piece_times: set[float]             # when a new piece became playable

    def actions(self) -> list[tuple]:
        return [a for _, a in self.issued]

    def when(self, action: tuple) -> float | None:
        return next((t for t, a in self.issued if a == action), None)

    def count(self, action: tuple) -> int:
        return sum(1 for _, a in self.issued if a == action)

    def holds(self) -> list[dict]:
        """The listener's waits as the player lived them (the scheduler's `holds` are its own record)."""
        out = []
        for e in self.player.events:
            if e["event"] == "hold_start":
                out.append({"section": e["section"], "start": e["now"], "end": None, "reason": e["reason"]})
            elif e["event"] == "hold_end":
                out[-1]["end"] = e["now"]
                out[-1]["seconds"] = e["seconds"]
        return out

    def needed(self) -> dict[int, float]:
        """When playback first needed each section: it started playing, or the listener began waiting at its start."""
        out: dict[int, float] = {}
        for e in self.player.events:
            if e["event"] in ("section_start", "hold_start"):
                out.setdefault(e["section"], e["now"])
        return out


class Sim:
    def __init__(self, sc: Scenario, check: bool = True):
        self.sc, self.check, self.last = sc, check, sc.n
        self.rows = dict(default_rows(sc.n) if sc.rows is None else sc.rows)
        self.audio = {int(n): list(p) for n, p in (sc.audio or default_audio(sc.n)).items()}
        self.research_seconds = sc.research if isinstance(sc.research, dict) else {"default": sc.research}
        self.sched = self._scheduler(None)
        self.player = SimPlayer(sc.skips, sc.pauses, skip_times=sc.skip_times)
        self.heap: list[tuple] = []
        self.seq = self.epoch = 0
        self.trace: list[dict] = []
        self.issued: list[tuple[float, tuple]] = []
        # the harness's own books
        self.inflight: dict[tuple, float] = {}
        self.finished: set[tuple] = set()
        self.failed_once: set[tuple] = set()
        self.rows_done: set[str] = set()
        self.lost: set[str] = set()                  # rows given up on before a restart (the timeline keeps them)
        self.draft_done: dict[int, float] = {}
        self.lock_done: dict[int, float] = {}
        self.lock_mode: dict[int, str] = {}
        self.solo: dict[int, float] = {}
        self.narr_done: dict[int, float] = {}        # the narration returned
        self.whole: set[int] = set()                 # every piece delivered and said to be the last
        self.delivered: dict[int, list[float]] = {n: [] for n in range(1, sc.n + 1)}
        self.ready: dict[int, float] = {}
        self.piece_times: set[float] = set()
        self.fail_left = dict(sc.fail)
        self.archive_issued: float | None = None
        self.archive_done: float | None = None
        self.etas: dict[tuple[float, int], float | None] = {}
        self.rep: dict | None = None
        self.sent: dict | None = None     # the last report that reached this scheduler (none yet after a restart)
        self.seen = 0                     # player events already copied into the trace
        self.at = 1                       # the player's section, to check it only ever moves one marker

    def _scheduler(self, done: dict | None) -> Scheduler:
        sc = self.sc
        return Scheduler(list(range(1, sc.n + 1)), self.rows, max_hold=sc.max_hold,
                         research_parallel=sc.research_parallel, row_timeout=sc.row_timeout,
                         retries=sc.retries, done=done)

    # --- the loop -----------------------------------------------------------------------

    def run(self) -> Result:
        sc = self.sc
        self._push(0.0, "start", mortal=False)
        self._push(sc.report_every, "report", mortal=False)
        for t in sc.restarts:
            self._push(t, "restart", mortal=False)
        for t, row, section, seconds in sc.add_rows:
            self._push(t, "add_row", row, section, seconds, mortal=False)
        for t in sc.probes:
            self._push(t, "probe", mortal=False)
        ended = False
        while self.heap:
            now, _, epoch, ev = heapq.heappop(self.heap)
            if epoch is not None and epoch != self.epoch:
                continue                  # this action died with the process
            assert now <= sc.limit, (f"no archive by {sc.limit} s: deadlock. running={self.sched.running} "
                                     f"snapshot={self.sched.snapshot()['sections']}")
            self._step(now)               # the listener up to now, with what was playable before this event
            for action in self._apply(ev, now):
                self._issue(action, now)
            self._step(now)               # and what this event made playable
            if self.check:
                self._check_state(now, reported=ev[0] == "report")
            if self.sched.dead or (self.archive_done is not None and self.player.state == "done"):
                ended = True
                break
        assert ended, "the event heap ran dry before the archive"
        return Result(sc, self.sched, self.player, self.trace, self.issued, self.archive_issued, self.ready,
                      self.narr_done, self.draft_done, self.lock_done, self.lock_mode, self.solo, self.etas,
                      dict(self.rows), self.piece_times)

    def _push(self, t: float, *ev, mortal: bool = True) -> None:
        self.seq += 1
        heapq.heappush(self.heap, (t, self.seq, self.epoch if mortal else None, ev))

    def _step(self, now: float) -> None:
        self.rep = self.player.step(now, self.sched.audio(), self.last)
        for e in self.player.events[self.seen:]:
            self.trace.append({"t": e["now"], "event": "player_" + e["event"],
                               **{k: v for k, v in e.items() if k not in ("event", "now")}})
            if self.check and e["event"] == "skip":
                assert e["to"] in (None, e["from"], e["from"] + 1), f"a skip went past the next marker: {e}"
        self.seen = len(self.player.events)
        if self.check:
            assert self.rep["section"] >= self.at, f"the player went backwards: {self.rep}"
            for n in range(self.at, self.rep["section"]):     # it left n: heard to its end, or skipped out of it
                assert n in self.whole or self._skipped_over(n), f"the player left section {n} early"
            self.at = self.rep["section"]
            if self.rep["state"] == "playing":
                assert self.delivered[self.rep["section"]], "playing a section with no audio"

    def _skipped_over(self, n: int) -> bool:
        return any(e["event"] == "skip" and e.get("moved") and e["from"] == n for e in self.player.events)

    # --- events -------------------------------------------------------------------------

    def _apply(self, ev: tuple, now: float) -> list[tuple]:
        kind, s = ev[0], self.sched
        if kind != "report":
            self.trace.append({"t": now, "event": kind, "args": list(ev[1:])})
        if kind == "start":
            return s.start(now)
        if kind == "report":
            if not (self.archive_done is not None and self.player.state == "done"):
                self._push(now + self.sc.report_every, "report", mortal=False)
            self.sent = dict(self.rep)
            return s.player(self.rep, now) + s.tick(now)
        if kind == "row":
            self._finish(("research", ev[1]))
            self.rows_done.add(ev[1])
            return s.row_done(ev[1], now)
        if kind == "opening":
            self._finish(("opening",))
            self.draft_done[1] = self.lock_done[1] = now
            self.lock_mode[1] = "opening"
            return s.opening_done(now)
        if kind == "draft":
            self._finish(("draft", ev[1]))
            self.draft_done[ev[1]] = now
            return s.draft_done(ev[1], now)
        if kind == "lock":
            self._finish(("lock", ev[1], ev[2]))
            self.lock_done[ev[1]] = now
            return s.lock_done(ev[1], now)
        if kind == "piece":
            _, n, i, seconds, last = ev
            if i == len(self.delivered[n]):
                self.delivered[n].append(seconds)
                self.piece_times.add(now)
            self.ready.setdefault(n, now)
            if last:
                self.whole.add(n)
            return s.piece_ready(n, seconds, now, last=last, index=i)
        if kind == "narrated":
            self._finish(("narrate", ev[1]))
            self.narr_done[ev[1]] = now
            self.whole.add(ev[1])
            return s.narrate_done(ev[1], now)
        if kind == "archived":
            self._finish(("archive",))
            self.archive_done = now
            return s.archive_done(now)
        if kind == "failed":
            action = ev[1]
            del self.inflight[action]
            self.failed_once.add(action)
            if action[0] == "research":
                self.rows_done.add(action[1])      # a failed row is a finished row: written without it
                self.finished.add(action)
            if action[0] == "lock" and action[2] == "solo":
                del self.lock_mode[action[1]]      # decided again: paired, solo once more, or nothing yet
                del self.solo[action[1]]
            return s.failed(action, now)
        if kind == "add_row":
            _, row, section, seconds = ev
            open_ = section > 1 and section not in self.draft_done and ("draft", section) not in self.inflight
            r = self.sent                          # what the scheduler was last told: a listener waiting for k
            k = r["waiting_for"] if r and r["state"] == "waiting" else None
            if k is not None and k not in self.lock_done and section in (k, k + 1):
                open_ = False                      # the wait is for this draft: a new row would lengthen it
            assert section in s.open_sections() if open_ else section not in s.open_sections()
            out = s.row_added(row, section, now)
            assert s.has_row(row) == open_, f"{row} for section {section}: accepted={s.has_row(row)}, open={open_}"
            if open_:
                self.rows[row] = section
                self.research_seconds[row] = seconds
            else:
                assert out == []
            return out
        if kind == "restart":
            return self._restart(now)
        if kind == "probe":
            for n in range(1, self.sc.n + 1):
                self.etas[(now, n)] = s.eta(n, now, self.sc.medians or {})
            return []
        raise AssertionError(f"unknown event {ev}")

    def _finish(self, action: tuple) -> None:
        assert action in self.inflight, f"{action} finished but was not running"
        del self.inflight[action]
        self.finished.add(action)

    def _restart(self, now: float) -> list[tuple]:
        """The process dies: whatever was running is lost, and a new scheduler restores from what
        finished (through JSON, as it would be read from files)."""
        done = {"rows": sorted(self.rows_done), "drafts": sorted(self.draft_done), "locked": sorted(self.lock_done),
                "narrated": sorted(self.narr_done), "solo": sorted(n for n in self.solo if n in self.lock_done),
                "pieces": {n: p for n, p in self.delivered.items() if p}, "archived": self.archive_done is not None}
        # the driver logs each timeout the scheduler reports; after a restart it reads them back
        self.lost |= {a[1] for a, t in self.inflight.items()
                      if a[0] == "research" and self.sched.row_state.get(a[1]) == "timed_out"}
        done["timed_out"] = sorted(self.lost - self.rows_done)
        self.epoch += 1
        self.sent = None
        self.inflight.clear()
        self.whole = set(self.narr_done)
        for n in list(self.lock_mode):
            if n not in self.lock_done:
                del self.lock_mode[n]
                self.solo.pop(n, None)
        if self.archive_done is None:
            self.archive_issued = None
        self.sched = self._scheduler(json.loads(json.dumps(done)))
        return self.sched.start(now)

    # --- executing an action ------------------------------------------------------------

    def _issue(self, action: tuple, now: float) -> None:
        if self.check:
            self._check_issue(action, now)
        self.issued.append((now, action))
        self.trace.append({"t": now, "event": "issue", "action": action})
        self.inflight[action] = now
        kind = action[0]
        if kind == "lock":
            self.lock_mode[action[1]] = action[2]
            if action[2] == "solo":
                self.solo[action[1]] = now
        if kind == "archive":
            self.archive_issued = now
        self._execute(action, now)

    def _execute(self, action: tuple, now: float) -> None:
        sc, kind = self.sc, action[0]
        failing = self.fail_left.get(action, 0) > 0
        if failing:
            self.fail_left[action] -= 1
        if kind == "narrate":
            n, t = action[1], now
            for i, seconds in enumerate(self.audio[n]):
                fresh = i >= len(self.delivered[n])
                if fresh:
                    t += seconds * sc.synth          # pieces already on disk are reused at once
                last = i == len(self.audio[n]) - 1
                self._push(t, "piece", n, i, seconds, last and sc.last_flag and not failing)
                if failing and fresh:
                    break                            # it dies after one new piece
            if failing:
                self._push(t + sc.fail_after, "failed", action)
            else:
                self._push(t + sc.join, "narrated", n)
        elif failing:
            self._push(now + sc.fail_after, "failed", action)
        elif kind == "research":
            seconds = self.research_seconds.get(action[1], self.research_seconds.get("default"))
            if seconds is not None:
                self._push(now + seconds, "row", action[1])
        elif kind == "opening":
            self._push(now + sc.opening, "opening")
        elif kind == "draft":
            self._push(now + _pick(sc.draft, action[1]), "draft", action[1])
        elif kind == "lock":
            self._push(now + _pick(sc.lock, action[1], action[2]), "lock", action[1], action[2])
        elif kind == "archive":
            self._push(now + ARCHIVE_SECONDS, "archived")

    # --- the rules ----------------------------------------------------------------------

    def _writing(self) -> list[tuple]:
        return [a for a in self.inflight if a[0] in ("opening", "draft") or (a[0] == "lock" and a[2] != "solo")]

    def _researching(self) -> list[str]:
        return [a[1] for a in self.inflight if a[0] == "research" and self.sched.row_state.get(a[1]) != "timed_out"]

    def _row_in(self, row: str, now: float) -> bool:
        if row in self.rows_done or row in self.lost:
            return True
        started = self.inflight.get(("research", row))
        return started is not None and now - started >= self.sc.row_timeout - EPS

    def _check_issue(self, action: tuple, now: float) -> None:
        kind, sc = action[0], self.sc
        say = f"{action} at {now}"
        assert action not in self.inflight, f"{say}: issued while it is running"
        assert action not in self.finished, f"{say}: issued again after it finished"
        if action in [a for _, a in self.issued]:
            before = max(t for t, a in self.issued if a == action)
            excused = any(e["t"] >= before and (e["event"] == "restart" or
                                                (e["event"] == "failed" and e["args"][0] == action))
                          for e in self.trace)
            assert excused, f"{say}: issued twice"
        assert self.archive_issued is None or kind == "archive", f"{say}: after the archive"
        if kind == "research":
            row = action[1]
            assert len(self._researching()) < sc.research_parallel, f"{say}: too many research agents"
            assert self.rows[row] not in self.draft_done, f"{say}: its section is already drafted"
            assert row not in self.lost, f"{say}: it timed out before the restart"
            waiting = [r for r in self.rows if r not in self.rows_done and ("research", r) not in self.inflight
                       and r != row and r not in self.lost and self.rows[r] not in self.draft_done]
            assert all(self.rows[row] <= self.rows[r] for r in waiting), f"{say}: an earlier section's row waits"
        elif kind == "opening":
            assert not self._writing() and 1 not in self.lock_done, say
        elif kind == "draft":
            n = action[1]
            assert not self._writing(), f"{say}: the writing lane is busy with {self._writing()}"
            assert n not in self.draft_done and n - 1 in self.draft_done, f"{say}: out of order"
            if n - 2 >= 1:
                solo_running = ("lock", n - 2, "solo") in self.inflight
                assert n - 2 in self.lock_done or solo_running, f"{say}: the draft of {n - 1} may still be revised"
            late = [r for r, s in self.rows.items() if s == n and not self._row_in(r, now)]
            assert not late, f"{say}: rows {late} are neither in nor timed out"
        elif kind == "lock":
            _, n, mode = action
            assert n not in self.lock_done and n in self.draft_done, f"{say}: not drafted, or locked already"
            assert n - 1 in self.lock_done, f"{say}: before the lock of {n - 1}"
            assert not [a for a in self.inflight if a[0] == "lock" and a[1] == n], f"{say}: its lock already runs"
            if mode == "solo":
                assert 1 < n < self.last, f"{say}: only a middle section can fall back to a solo edit"
                assert not [a for a in self.inflight if a[0] == "lock" and a[2] == "solo"], f"{say}: two solo locks"
                assert n + 1 not in self.draft_done or self._writing(), \
                    f"{say}: the draft of {n + 1} is there and the lane is free, so pair it"
                r = self.rep
                assert r["state"] == "waiting" and r["waiting_for"] == n, f"{say}: nobody is waiting for it ({r})"
                assert now - r["since"] >= sc.max_hold - EPS, f"{say}: after only {now - r['since']:.1f} s of waiting"
            else:
                assert not self._writing(), f"{say}: the writing lane is busy with {self._writing()}"
                assert mode == ("final" if n == self.last else "paired"), say
                if mode == "paired":
                    assert n + 1 in self.draft_done, f"{say}: paired without the draft of {n + 1}"
        elif kind == "narrate":
            n = action[1]
            assert n in self.lock_done, f"{say}: narrated before it is locked"
            assert all(m in self.narr_done for m in range(1, n)), f"{say}: narration out of order"
            assert n not in self.narr_done and not [a for a in self.inflight if a[0] == "narrate"], say
        elif kind == "archive":
            assert self.last in self.narr_done and len(self.narr_done) == self.last, f"{say}: before the last section"
        else:
            raise AssertionError(f"unknown action {action}")

    def _check_state(self, now: float, reported: bool) -> None:
        s, sc = self.sched, self.sc
        assert len(self._writing()) <= 1, f"two writing actions at {now}: {self._writing()}"
        assert sum(1 for a in self.inflight if a[0] == "lock" and a[2] == "solo") <= 1
        assert sum(1 for a in self.inflight if a[0] == "narrate") <= 1
        assert len(self._researching()) <= sc.research_parallel
        # the scheduler's books against ours
        if not s.dead:
            ours = {a for a in self.inflight if not (a[0] == "research" and s.row_state.get(a[1]) == "timed_out")}
            assert set(s.running) == ours, f"at {now}: scheduler runs {set(s.running) ^ ours} differently"
        assert s.locked == set(self.lock_done) and s.drafted == set(self.draft_done), now
        assert s.narrated == set(self.narr_done), now
        for n in range(1, sc.n + 1):
            assert s.pieces.get(n, []) == self.delivered[n], (now, n)
            assert s.ready(n) == bool(self.delivered[n]) and s.complete(n) == (n in self.whole)
            assert not self.delivered[n] or n in self.lock_done, f"section {n} has audio but no lock"
        for a, started in self.inflight.items():
            if a[0] == "research" and s.row_state.get(a[1]) == "timed_out":
                assert now - started >= sc.row_timeout - EPS, f"{a[1]} timed out after {now - started} s"
        if s.dead:
            return
        # nothing that may start is left waiting
        writing = self._writing()
        assert writing or 1 in self.lock_done, f"at {now}: no opening under way"
        first = len(self.narr_done) + 1
        if first <= self.last and first in self.lock_done:
            assert ("narrate", first) in self.inflight, f"at {now}: section {first} is locked but not being narrated"
        assert (self.last in self.narr_done) == (self.archive_issued is not None), f"at {now}: archive"
        if not writing and 1 in self.lock_done:
            for n in range(2, self.last + 1):
                if n not in self.draft_done:
                    rows_in = all(r in self.rows_done or r in self.lost or s.row_state.get(r) == "timed_out"
                                  for r, sec in self.rows.items() if sec == n)
                    revised = n - 2 < 1 or n - 2 in self.lock_done or ("lock", n - 2, "solo") in self.inflight
                    assert not (n - 1 in self.draft_done and revised and rows_in), f"at {now}: draft {n} could run"
                    break
            for k in range(2, self.last + 1):
                if k not in self.lock_done and not [a for a in self.inflight if a[0] == "lock" and a[1] == k]:
                    partner = k == self.last or k + 1 in self.draft_done
                    assert not (k in self.draft_done and k - 1 in self.lock_done and partner), \
                        f"at {now}: lock {k} could run"
                    break
        if reported:
            for a, started in self.inflight.items():
                if a[0] == "research" and now - started >= sc.row_timeout:
                    assert s.row_state[a[1]] == "timed_out", f"{a[1]} has run {now - started} s without a timeout"
            r = self.rep
            if r["state"] == "waiting" and now - r["since"] >= sc.max_hold:
                k = r["waiting_for"]
                due = (1 < k < self.last and k in self.draft_done and k - 1 in self.lock_done
                       and k not in self.lock_done and k not in self.lock_mode)
                assert not due, f"at {now}: the listener has waited {now - r['since']:.0f} s for {k}, no solo edit"


def _pick(spec, *keys) -> float:
    if not isinstance(spec, dict):
        return spec
    for key in keys:
        if key in spec:
            return spec[key]
    return spec["default"]


def run(sc: Scenario, check: bool = True) -> Result:
    """Run one scenario to its archive (and the listener to the end of the chapter)."""
    return Sim(sc, check).run()
