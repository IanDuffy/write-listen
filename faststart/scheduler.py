"""The fast run's scheduler: what to start now, given what has finished and where the listener is.

Pure code: no I/O, no threads, no clock of its own (every method takes `now`, in whatever
clock the caller keeps; seconds). The driver executes the actions it returns in threads and
feeds each completion back as an event, from ONE thread: nothing here is locked. The
simulation in tests/faststart/fast_scheduler_sim.py does the same against made-up durations.

Three lanes (docs/fast-start.md, steps 3-6):

- Research: every ledger row from `start`, section 2's rows first, `research_parallel` at once.
  A row that has run for `row_timeout` counts as done (and frees its slot), so one agent that
  never returns cannot stop the chapter.
- Writing, one action at a time, in this order: the opening, draft 2, draft 3, lock 2 (paired
  with the draft of 3), draft 4, lock 3, ..., draft N, lock N-1, lock N (final). A draft of n
  needs the draft of n-1, the paired lock of n-2 (it revises the draft of n-1) and every row
  of section n in or timed out. A lock of k needs the lock of k-1.
- Narration: strictly in section order, started the moment a section is locked.

Audio is held for the paired edit. The one exception is the solo fallback: when the listener
has been waiting `max_hold` seconds for section k and k is drafted, its lock not begun and
k-1 locked, k is locked on its own, outside the writing lane (the draft of k+1 carries on).
If the draft of k+1 lands in time, the paired lock is taken instead: it is no slower.

`archive` is issued once, when the last section's narration has returned (narrate_done).
"""

from __future__ import annotations

import copy
import heapq

Action = tuple  # ("research", row) | ("opening",) | ("draft", n) | ("lock", n, mode) | ("narrate", n) | ("archive",)

# What eta() reads from the medians of earlier runs (faststart/timings.json), in seconds:
# "opening" (write and edit), "research" (one row), "draft", "lock" (and, if kept apart,
# "lock_solo" and "lock_final"), "first_piece" (from the start of a section's narration to its
# first playable piece) and "narrate" (a whole section).
MEDIANS = ("opening", "research", "draft", "lock", "first_piece", "narrate")

OVERDUE = 0.25  # an action already past its median is assumed to need this share of it again


class _NoEstimate(Exception):
    """eta() needs a median that earlier runs have not given yet."""


class Scheduler:
    def __init__(self, sections: list[int], rows: dict[str, int], *, max_hold: float = 180,
                 research_parallel: int = 4, row_timeout: float = 600, done: dict | None = None,
                 retries: int = 1, report_timeout: float = 30):
        """rows maps a ledger row id to its section. `done` restores from files after a restart:
        {"rows": [...], "drafts": [n...], "locked": [n...], "narrated": [n...], "pieces": {n: [seconds...]}}
        (and, if known, "solo": [n...], "timed_out": [row...] for rows given up on before the
        restart and still not in, and "archived": true once the archive has run). Then call
        start(): it issues whatever is still to do.

        `retries`: how often a failed action is issued again before the run is given up
        (`dead`). `report_timeout`: the solo fallback needs a position report no older than
        this, so a listener who closed the page does not cost a section its paired edit."""
        self.sections = [int(n) for n in sections]
        if not self.sections or self.sections != list(range(1, len(self.sections) + 1)):
            raise ValueError(f"sections must be 1..N in order, got {sections!r}")
        self.last = self.sections[-1]
        self.max_hold, self.row_timeout = float(max_hold), float(row_timeout)
        self.research_parallel = max(1, int(research_parallel))
        self.retries, self.report_timeout = int(retries), float(report_timeout)

        self.rows: dict[str, int] = {str(k): int(v) for k, v in rows.items()}
        # pending, running, done, timed_out, or skipped (restored with its section already drafted)
        self.row_state: dict[str, str] = {k: "pending" for k in self.rows}
        self.running: dict[Action, float] = {}       # every action in flight -> when it was issued
        self.drafted: set[int] = set()
        self.locked: set[int] = set()
        self.lock_mode: dict[int, str] = {}          # a lock begun in this process -> its mode
        self.narrated: set[int] = set()              # its narration has returned: joined and timed
        self.whole: set[int] = set()                 # every piece is playable (a moment before that)
        self.pieces: dict[int, list[float]] = {}     # playable pieces, seconds each
        self._cursor: dict[int, int] = {}            # where a piece reported without an index goes
        self.archived = False                        # the archive has been issued
        self.finished = False                        # and has run
        self.dead: dict | None = None                # {"action", "t"}: an action failed past its retries
        self.failures: dict[Action, int] = {}
        self.timed_out: list[str] = []
        self.holds: list[dict] = []
        self.solo: list[int] = []
        self.log: list[dict] = []
        self.position: dict | None = None            # the player's last report
        self._reported: float | None = None          # when it came
        self._restore(done or {})

    # --- restoring ----------------------------------------------------------------------

    def _restore(self, done: dict) -> None:
        """Take what the files show, cut down to what the rules allow: locks, drafts and
        narration are each a run 1..k, a locked section is drafted, a narrated one is locked.
        Anything past that is redone (the stage functions return a finished file without a
        paid call)."""
        def run_of(numbers) -> set[int]:
            out = set()
            for n in self.sections:
                if n not in numbers:
                    break
                out.add(n)
            return out

        ints = lambda key: {int(n) for n in done.get(key) or []}
        self.locked = run_of(ints("locked"))
        self.drafted = run_of(ints("drafts") | self.locked) if 1 in self.locked else set()
        self.narrated = run_of(ints("narrated") & self.locked)
        self.whole = set(self.narrated)
        for key, seconds in (done.get("pieces") or {}).items():
            n = int(key)
            # narration is in order, so only the first section not fully narrated can be part-done
            if n in self.narrated or (n in self.locked and n == len(self.narrated) + 1):
                self.pieces[n] = [float(s) for s in seconds]
        for row in done.get("rows") or []:
            if row in self.row_state:
                self.row_state[row] = "done"
        for row in done.get("timed_out") or []:
            if self.row_state.get(row) == "pending":     # given up on before the restart: not researched
                self.row_state[row] = "timed_out"        # again, and its section does not wait for it
                self.timed_out.append(row)
        for row, section in self.rows.items():
            if self.row_state[row] == "pending" and section in self.drafted:
                self.row_state[row] = "skipped"      # its section was written without it: no use now
        self.solo = [int(n) for n in done.get("solo") or [] if int(n) in self.locked]
        if done.get("archived") and self.last in self.narrated:
            self.archived = self.finished = True

    # --- events: each returns the actions to start now ----------------------------------

    def start(self, now: float) -> list[Action]:
        """The outline is done: the opening, and research for every row (or, after a restart,
        whatever the files leave to do)."""
        return self._advance(now)

    def row_done(self, row_id: str, now: float) -> list[Action]:
        """Landed, thin or failed: all the same here. A row that came back after its timeout
        was already counted and is ignored."""
        if self.row_state.get(row_id) in ("pending", "running"):
            self.row_state[row_id] = "done"
            self.running.pop(("research", row_id), None)
        return self._advance(now)

    def row_added(self, row_id: str, section: int, now: float) -> list[Action]:
        """A note's extra agent. Ignored (no action, and has_row stays False) if the section is
        not open (open_sections): its draft has begun, so nothing would wait for the row, or a
        listener is waiting and would."""
        section = int(section)
        if row_id in self.rows or section not in self.open_sections():
            return []
        self.rows[row_id] = section
        self.row_state[row_id] = "pending"
        return self._advance(now)

    def opening_done(self, now: float) -> list[Action]:
        """Section 1 is written and edited, which is its lock."""
        self._finish(("opening",))
        self.drafted.add(1)
        self.locked.add(1)
        self.lock_mode[1] = "opening"
        return self._advance(now)

    def draft_done(self, n: int, now: float) -> list[Action]:
        self._finish(("draft", n))
        self.drafted.add(n)
        return self._advance(now)

    def lock_done(self, n: int, now: float) -> list[Action]:
        self._finish(("lock", n, self.lock_mode.get(n)))
        self.locked.add(n)
        return self._advance(now)

    def piece_ready(self, n: int, seconds: float, now: float, last: bool = False,
                    index: int | None = None) -> list[Action]:
        """A narrated piece is playable. Pass `index` (the piece's place in its section) when
        you have it: a piece reported twice then overwrites itself. Without it pieces are
        taken in order, starting again from the first after a restart or a failed narration
        (the narrator reuses what is on disk and reports every piece again). `last=True` makes
        the section complete for the player; the narration itself is over at narrate_done."""
        if ("narrate", n) not in self.running:
            raise ValueError(f"a piece of section {n}, which is not being narrated")
        have = self.pieces.setdefault(n, [])
        i = self._cursor.get(n, len(have)) if index is None else int(index)
        if i > len(have):
            raise ValueError(f"piece {i} of section {n} before piece {len(have)}")
        if i == len(have):
            have.append(float(seconds))
            hold = self._hold()
            if hold and hold["section"] == n:      # the listener was out of audio in n; now there is some
                self._close(hold, now)
        else:
            have[i] = float(seconds)
        self._cursor[n] = i + 1
        if last:
            self._whole(n, now)
        return self._advance(now)

    def narrate_done(self, n: int, now: float) -> list[Action]:
        """The narration of section n has returned: every piece is playable and the section is
        joined and timed. Only this starts the next narration and, after the last section, the
        archive, which reads the joined files. (Not in the first contract, where the last piece
        was the only signal: the narrator reports its last piece before it joins the section.)"""
        self._finish(("narrate", n))
        self.narrated.add(n)
        self._whole(n, now)
        return self._advance(now)

    def archive_done(self, now: float) -> list[Action]:
        """The episode is built: the run is finished. (Also not in the first contract; it only
        clears the action from `running` and sets `finished`.)"""
        self._finish(("archive",))
        self.finished = True
        return []

    def player(self, report: dict, now: float) -> list[Action]:
        """The player's position report (player.py; the page posts the same dict). Opens and
        closes holds; `since` must be in the caller's clock."""
        self.position = dict(report)
        self._reported = now
        waiting = report.get("state") == "waiting" and report.get("waiting_for") is not None
        want = int(report["waiting_for"]) if waiting else None
        hold = self._hold()
        if hold and hold["section"] != want:
            self._close(hold, now)
            hold = None
        if want is not None and hold is None and not self._has_audio(report, want):
            since = report.get("since")
            start = now if since is None else min(float(since), now)
            self.holds.append({"section": want, "start": start, "end": None, "seconds": round(now - start, 2),
                               "reason": report.get("reason") or "end_of_audio"})
        return self._advance(now)

    def tick(self, now: float) -> list[Action]:
        """Time passing: row timeouts and the hold threshold."""
        for row, state in self.row_state.items():
            if state == "running" and now - self.running[("research", row)] >= self.row_timeout:
                self.row_state[row] = "timed_out"
                self.timed_out.append(row)
                del self.running[("research", row)]
        hold = self._hold()
        if hold:
            hold["seconds"] = round(now - hold["start"], 2)
        return self._advance(now)

    def failed(self, action: Action, now: float) -> list[Action]:
        """An action raised; the driver has already retried once. A research row counts as
        done (the section is written without it). Anything else is issued again, `retries`
        times; past that the run is `dead` and nothing more is issued: no later step can do
        without it, and a restart resumes from the files. A solo lock is not issued again as
        it was but decided again: the paired lock if the next draft has landed and the lane is
        free, the solo lock if the listener is still waiting past max_hold, else nothing yet."""
        action = tuple(action)
        if action[0] == "research":
            self.failures[action] = self.failures.get(action, 0) + 1
            return self.row_done(action[1], now)
        self._finish(action)
        self.failures[action] = self.failures.get(action, 0) + 1
        if self.failures[action] > self.retries:
            self.dead = {"action": action, "t": now}
            return []
        if action[0] == "lock" and action[2] == "solo":
            n = action[1]
            del self.lock_mode[n]
            self.solo.remove(n)
            for hold in self.holds:
                if hold["section"] == n:
                    hold.pop("solo", None)
            return self._advance(now)
        return self._issue(action, now, retry=self.failures[action]) + self._advance(now)

    # --- queries ------------------------------------------------------------------------

    def ready(self, n: int) -> bool:
        """Its first piece is playable."""
        return bool(self.pieces.get(n)) or n in self.whole

    def complete(self, n: int) -> bool:
        """Every piece is playable."""
        return n in self.whole

    def state(self, n: int) -> str:
        return "ready" if self.ready(n) else "being_written"

    def marker(self, n: int) -> str:
        """The section marker's state on the page: played, playing, ready or being_written."""
        pos = self.position
        if pos and pos.get("state") not in (None, "idle"):
            at = int(pos.get("section") or 0)
            if n < at or (n == at and pos.get("state") == "done"):
                return "played"
            if n == at and self.ready(n) and (pos.get("state") != "waiting" or pos.get("piece") or pos.get("t")):
                return "playing"
        return self.state(n)

    def audio(self) -> dict[int, dict]:
        """What is playable, in the shape SimPlayer.step takes."""
        return {n: {"pieces": list(self.pieces.get(n, [])), "complete": n in self.whole} for n in self.sections}

    def open_sections(self) -> list[int]:
        """Sections whose draft has not begun: the only ones a note may still start research for.
        While a listener waits for a section k that is not locked yet, k and k+1 are not open
        either: the wait is for the draft of k and, for the paired edit, the draft of k+1, and a
        new row would hold that draft for up to row_timeout more."""
        hold = self._hold()
        held = {hold["section"], hold["section"] + 1} if hold and hold["section"] not in self.locked else set()
        return [n for n in self.sections if n > 1 and n not in self.drafted and ("draft", n) not in self.running
                and n not in held]

    def has_row(self, row_id: str) -> bool:
        return row_id in self.rows

    def hold(self) -> dict | None:
        """The wait the listener is in now, if any."""
        return self._hold()

    def eta(self, n: int, now: float, medians: dict) -> float | None:
        """Seconds until section n is ready, from the medians of earlier runs (see MEDIANS).

        A copy of this scheduler is run forward with every outstanding action taking its
        median, so the estimate follows the same rules as the run: research still out, the
        writing order, narration in order, row timeouts, and the solo fallback if the listener
        is waiting now. None when a median it needs is missing or nothing is under way."""
        if self.ready(n):
            return 0.0
        if self.dead or n not in self.sections:
            return None
        sim = copy.deepcopy(self)
        hold = sim._hold()
        if hold and (self._reported is None or now - self._reported > self.report_timeout):
            sim._close(hold, now)                      # nobody is waiting any more
        sim.report_timeout, sim._reported = float("inf"), now
        heap: list[tuple] = []

        def push(t: float, *event) -> None:
            heapq.heappush(heap, (t, len(heap), event))

        def due(key: str, start: float) -> float:
            if medians.get(key) is None:
                raise _NoEstimate(key)
            length = float(medians[key])
            return start + length if start + length > now else now + OVERDUE * length

        def plan(action: Action, start: float) -> None:
            kind = action[0]
            if kind == "research":
                push(due("research", start), "row_done", action[1])
                push(max(start + sim.row_timeout, now), "tick")
            elif kind == "opening":
                push(due("opening", start), "opening_done")
            elif kind == "draft":
                push(due("draft", start), "draft_done", action[1])
            elif kind == "lock":
                key = f"lock_{action[2]}" if f"lock_{action[2]}" in medians else "lock"
                push(due(key, start), "lock_done", action[1])
            elif kind == "narrate":
                end = due("narrate", start)
                if not sim.pieces.get(action[1]):
                    push(min(due("first_piece", start), end), "piece", action[1])
                push(end, "narrate_done", action[1])

        try:
            for action, start in self.running.items():
                plan(action, start)
            if sim._hold():
                push(max(sim._hold()["start"] + sim.max_hold, now), "tick")
            for _ in range(50 * (len(self.sections) + len(self.rows) + 1)):
                if not heap:
                    break
                t, _, (name, *args) = heapq.heappop(heap)
                if name == "piece":
                    started = sim.piece_ready(args[0], 1.0, t)
                else:
                    started = getattr(sim, name)(*args, t)
                for action in started:
                    plan(action, t)
                if sim.ready(n):
                    return round(max(0.0, t - now), 2)
        except _NoEstimate:
            return None
        return None

    def snapshot(self) -> dict:
        """For fast/state.json and the status. `done` is the dict the constructor restores from."""
        return {
            "sections": {n: {"state": self.state(n), "marker": self.marker(n), "drafted": n in self.drafted,
                             "lock": self.lock_mode.get(n), "locked": n in self.locked,
                             "pieces": list(self.pieces.get(n, [])), "complete": n in self.whole,
                             "narrated": n in self.narrated}
                         for n in self.sections},
            "rows": {row: {"section": self.rows[row], "state": state} for row, state in self.row_state.items()},
            "running": [{"action": list(a), "since": t} for a, t in self.running.items()],
            "holds": [dict(h) for h in self.holds],
            "solo": list(self.solo),
            "timed_out": list(self.timed_out),
            "failures": [{"action": list(a), "times": k} for a, k in self.failures.items()],
            "archived": self.archived,
            "finished": self.finished,
            "dead": {"action": list(self.dead["action"]), "t": self.dead["t"]} if self.dead else None,
            "position": dict(self.position) if self.position else None,
            "done": {"rows": [r for r, s in self.row_state.items() if s == "done"],
                     "drafts": sorted(self.drafted), "locked": sorted(self.locked),
                     "narrated": sorted(self.narrated), "solo": list(self.solo),
                     "timed_out": list(self.timed_out), "archived": self.finished,
                     "pieces": {n: list(p) for n, p in self.pieces.items() if p}},
        }

    # --- the lanes ----------------------------------------------------------------------

    def _advance(self, now: float) -> list[Action]:
        """Everything that may start now. Writing comes before the solo check, so a paired lock
        that has just become possible is taken in place of a solo one."""
        if self.dead:
            return []
        return (self._research(now) + self._writing(now) + self._solo(now) + self._narration(now)
                + self._archive(now))

    def _research(self, now: float) -> list[Action]:
        out = []
        free = self.research_parallel - sum(1 for s in self.row_state.values() if s == "running")
        order = {row: i for i, row in enumerate(self.rows)}
        pending = sorted((r for r, s in self.row_state.items() if s == "pending"),
                         key=lambda r: (self.rows[r], order[r]))
        for row in pending[:max(0, free)]:
            self.row_state[row] = "running"
            out += self._issue(("research", row), now)
        return out

    def _writing(self, now: float) -> list[Action]:
        if any(a[0] in ("opening", "draft") or (a[0] == "lock" and a[2] != "solo") for a in self.running):
            return []
        if 1 not in self.locked:
            return self._issue(("opening",), now)
        for kind, n in self._order():
            if kind == "draft":
                if n in self.drafted:
                    continue
                # The lock that revises the draft of n-1 (the paired lock of n-2) stands before this
                # step in the order, so it is done, or it is a solo lock, which revises nothing.
                rows_in = all(self.row_state[r] not in ("pending", "running") for r, s in self.rows.items() if s == n)
                if n - 1 in self.drafted and rows_in:
                    return self._issue(("draft", n), now)
                return []
            if n in self.locked or n in self.lock_mode:
                continue
            if n - 1 not in self.locked or n not in self.drafted:
                return []
            if n == self.last:
                return self._lock(n, "final", now)
            if n + 1 in self.drafted:
                return self._lock(n, "paired", now)
            return []
        return []

    def _order(self) -> list[tuple[str, int]]:
        """draft 2, draft 3, lock 2, draft 4, lock 3, ..., draft N, lock N-1, lock N."""
        steps = []
        for n in self.sections[1:]:
            steps.append(("draft", n))
            if n >= 3:
                steps.append(("lock", n - 1))
        if self.last >= 2:
            steps.append(("lock", self.last))
        return steps

    def _solo(self, now: float) -> list[Action]:
        hold = self._hold()
        if not hold or now - hold["start"] < self.max_hold:
            return []
        if self._reported is None or now - self._reported > self.report_timeout:
            return []
        k = hold["section"]
        if not 1 < k < self.last:                      # the opening and the last section are never paired
            return []
        if k in self.locked or k in self.lock_mode or k not in self.drafted or k - 1 not in self.locked:
            return []
        if any(a[0] == "lock" and a[2] == "solo" for a in self.running):
            return []
        self.solo.append(k)
        hold["solo"] = True
        return self._lock(k, "solo", now, waited=round(now - hold["start"], 2))

    def _narration(self, now: float) -> list[Action]:
        if any(a[0] == "narrate" for a in self.running):
            return []
        for n in self.sections:
            if n in self.narrated:
                continue
            if n in self.locked:
                return self._issue(("narrate", n), now)
            break
        return []

    def _archive(self, now: float) -> list[Action]:
        if self.archived or self.last not in self.narrated:
            return []
        self.archived = True
        return self._issue(("archive",), now)

    # --- helpers ------------------------------------------------------------------------

    def _lock(self, n: int, mode: str, now: float, **extra) -> list[Action]:
        self.lock_mode[n] = mode
        return self._issue(("lock", n, mode), now, **extra)

    def _issue(self, action: Action, now: float, **extra) -> list[Action]:
        if action in self.running:
            raise RuntimeError(f"{action} issued twice")
        self.running[action] = now
        if action[0] == "narrate":
            self._cursor[action[1]] = 0
        self.log.append({"t": now, "action": action, **extra})
        return [action]

    def _finish(self, action: Action) -> None:
        if action not in self.running:
            raise ValueError(f"{action} is not running")
        del self.running[action]

    def _whole(self, n: int, now: float) -> None:
        """Every piece of n is playable: a listener waiting at its end waits no longer."""
        hold = self._hold()
        if n not in self.whole and hold and hold["section"] == n:
            self._close(hold, now)
        self.whole.add(n)

    def _hold(self) -> dict | None:
        return self.holds[-1] if self.holds and self.holds[-1]["end"] is None else None

    def _close(self, hold: dict, now: float) -> None:
        hold["end"] = now
        hold["seconds"] = round(now - hold["start"], 2)

    def _has_audio(self, report: dict, n: int) -> bool:
        """The report says the listener is out of audio in n, but a piece it has not played has
        arrived since: the wait is already over (the page learns of it a moment later)."""
        at = int(report.get("piece") or 0) if report.get("section") == n else 0
        return len(self.pieces.get(n, [])) > at or n in self.whole
