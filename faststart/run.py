"""The fast run, end to end (docs/fast-start.md, "The run, in order").

    python cli.py new --seed "..." --unattended --mode fast

Scout, outline, then one loop until the archive is done. The loop is the only caller of the
Scheduler (which is pure code and not thread-safe): it starts the actions the scheduler returns
in worker threads, and each worker puts its result on a queue for the loop to feed back.

    research    one agent per ledger row, fast.research.parallel at once (the lane has spare
                threads: a row the scheduler has given up on may still be holding one)
    writing     the opening, drafts and locks in the scheduler's order, a solo lock beside them
                when the listener has waited fast.max_hold_seconds, and the archive at the end
    narration   one section at a time, piece by piece
    notes       the small call that decides whether a listener's note needs research

Generation never waits for the listener: it runs straight through to the archive, whatever the
player does. The listener only changes one thing, the solo lock.

The listener's position comes from a SimPlayer (tests, python -m faststart e2e) or from
fast/player.json, which the server writes when the page reports where it is (milestone 5).
That file is the report dict of faststart/player.py with wall-clock times:

    {"at": 1794000000.0,            when the page reported: epoch seconds
     "state": "idle" | "playing" | "waiting" | "paused" | "done",
     "section": 2, "piece": 1,      the piece being played (or to play next), 0-based
     "t": 41.2,                     seconds into the section
     "chapter_t": 388.0,            seconds of audio heard so far
     "waiting_for": 3 | null,       the section the listener is waiting for
     "since": 1793999990.0 | null,  when that wait began: epoch seconds
     "reason": "end_of_audio" | "skip" | null}

A report older than the scheduler's report_timeout is not passed on: nobody is listening. With
no file and no player the run goes through all the same.

Every step is resumable from its files, so the same command again continues a stopped run
(cli.py sends a run marked fast here). The files also rebuild the scheduler's state.

Before anything is paid for, the ElevenLabs balance is read: a chapter that would run out of
narration characters part-way is not started (a balance that cannot be read stops nothing).
When a run stops, calls already in flight are waited for (fast.exit_wait_seconds at most), so
what they cost is in the log and what they found is on disk for the run that continues.
"""

from __future__ import annotations

import json
import math
import queue
import threading
import time

from stages import ledger, llm
from stages import new as overnight
from stages.common import PATHS, Run, StageError, word_count

from . import archive, base, checkin, narrate, notes, research, scout, sections, status
from . import outline as outline_mod
from . import timings as T
from .dossier import Dossier
from .scheduler import Action, Scheduler

GRACE = 5.0   # seconds a finished run waits for its worker threads before leaving them (they are daemons)
OVERNIGHT_FILES = ("map", "angle", "ledger", "dossier")  # stage outputs: in a folder not marked fast, another mode's
REPORT_KEYS = ("state", "section", "piece", "t", "chapter_t", "position", "waiting_for", "since", "reason")


class _Lane:
    """A few worker threads that take jobs in order. Daemon threads: a model call that never
    returns must not keep the process alive after the run has reported a problem."""

    def __init__(self, name: str, size: int):
        self.jobs: queue.Queue = queue.Queue()
        self.closed = threading.Event()
        self.threads = [threading.Thread(target=self._work, name=f"fast-{name}-{i}", daemon=True) for i in range(size)]
        for t in self.threads:
            t.start()

    def submit(self, job) -> None:
        self.jobs.put(job)

    def _work(self) -> None:
        while True:
            job = self.jobs.get()
            if job is None or self.closed.is_set():  # closed: a job still queued is dropped, not started
                return
            job()

    def close(self, deadline: float) -> None:
        """Stop taking jobs and wait for the threads until `deadline` (time.monotonic)."""
        self.closed.set()
        for _ in self.threads:
            self.jobs.put(None)
        for t in self.threads:
            t.join(max(0.0, deadline - time.monotonic()))


def done_from_files(r: Run, outline: dict, led: dict, dossier: Dossier, episode: bool = True) -> dict:
    """What a run folder shows as finished, in the shape Scheduler(done=...) restores from: rows
    filed in the dossier, rows given up on, sections drafted, locked and narrated, the pieces on
    disk, solo locks, and whether the archive has run (with its episode, when one is wanted)."""
    numbers = [s["n"] for s in outline["sections"]]
    locked = [n for n in numbers if sections.locked_text(r, n) is not None]
    saved = base.read_json(base.path(r, "work") / "archive.json")
    gone = {e.get("row") for e in base.read_timeline(r) if e["event"] == T.RESEARCH_TIMEOUT}
    return {
        "rows": [row["id"] for row in led["rows"] if dossier.has(row["id"])],
        # given up on before a restart and still not in: not researched a second time (findings that
        # did come back, and only wait to be filed, are: that costs nothing)
        "timed_out": [row["id"] for row in led["rows"] if row["id"] in gone and not dossier.has(row["id"])
                      and not (r.path("research_dir") / f"{row['id']}.md").exists()],
        "drafts": [n for n in numbers if n in locked or sections.latest_draft(r, n) is not None],
        "locked": locked,
        "narrated": [n for n in locked if narrate.narrated(r, n)],
        "pieces": {n: [p.seconds for p in narrate.pieces(r, n)] for n in locked},
        "solo": [n for n in locked if (lk := sections.saved_lock(r, n)) is not None and lk.mode == "solo"],
        "archived": bool(saved and (saved.get("episode") or not episode) and r.path("chapter_mp3").exists()),
    }


class NoCharacters(StageError):
    """The ElevenLabs account has too few characters left this month for the chapter."""


def chars_ahead(cfg: dict, r: Run | None = None) -> int:
    """The real ElevenLabs characters a fast run may still ask for, at the upper end: the longest
    chapter the outline may plan (narrate.needed_chars), less what is already narrated by the
    voice. With fast.narration.simulate_after_chars set (a build test) only up to that many are
    real, and none once the run has gone over to silence. Nothing for a chapter whose sections
    are all narrated. `r` None: a run that does not exist yet."""
    limit = base.settings(cfg)["narration"].get("simulate_after_chars")
    real = 0
    if r is not None:
        outline = base.read_json(base.path(r, "outline_json"))
        if r.path("chapter_mp3").exists() or (outline and all(narrate.narrated(r, s["n"]) for s in outline["sections"])):
            return 0
        metas = [m for p in r.path("audio_dir").glob("sec*/p*.json") if (m := base.read_json(p))]
        if limit is not None and any(m.get("simulated") for m in metas):
            return 0
        real = sum(len(m["text"]) for m in metas if not m.get("simulated"))
    ahead = max(0, narrate.needed_chars(cfg) - real)
    return ahead if limit is None else min(ahead, max(0, limit - real))


def read_balance(r: Run | None = None) -> tuple[int, int] | None:
    """(characters used this month, the plan's limit) on the ElevenLabs account, or None when it
    cannot be read. Never raises: a failed read is logged (with `r`) and stops nothing."""
    try:
        got, why = narrate.balance(), "no answer"
    except Exception as e:
        got, why = None, _error(e)
    if got is None and r is not None:
        r.log({"stage": "fast_run", "label": "ElevenLabs balance", "error": f"could not be read ({why}); going on"})
    return got


def page_report(raw: dict, now: float, wall: float) -> tuple[dict, float]:
    """fast/player.json as the scheduler takes it: the report with `since` moved from wall-clock
    time into the loop's clock, and how old the report is in seconds."""
    age = max(0.0, wall - float(raw.get("at") or wall))
    report = {k: raw.get(k) for k in REPORT_KEYS if k in raw}
    if report.get("since") is not None:
        report["since"] = now - max(0.0, wall - float(report["since"]))
    return report, age


def _error(e: BaseException) -> str:
    return str(e) if isinstance(e, StageError) else f"{type(e).__name__}: {e}"


def _note_id(note: dict) -> tuple:
    return note.get("time"), note.get("text")


def _name(action: Action) -> str:
    kind = action[0]
    if kind == "research":
        return f"research {action[1]}"
    if kind == "lock":
        return f"lock {action[1]} ({action[2]})"
    return kind if len(action) == 1 else f"{kind} {action[1]}"


class _Driver:
    def __init__(self, r: Run, cfg: dict, player, listener, clock, episode: bool, again: str | None = None):
        self.r, self.cfg, self.player, self.listener, self.clock, self.episode = r, cfg, player, listener, clock, episode
        self.again = again or f"python cli.py new {r.name} --unattended"   # the command that continues this run
        self.f = base.settings(cfg)
        self.q: queue.Queue = queue.Queue()
        self.lanes: dict[str, _Lane] = {}
        self.tl: base.Timeline | None = None
        self.sched: Scheduler | None = None
        self.outline: dict | None = None
        self.stage, self.label = "scout", "Looking into it"
        self.live: dict[Action, dict] = {}        # an action in flight -> {"token", "since"}
        self.token = 0
        self.retried: set[Action] = set()         # the driver's own retry is used once per action
        self.errors: dict[Action, str] = {}
        self.failed_at: tuple[str, str] | None = None   # (stage, label) of the step that stopped the run
        self.mutex: dict[tuple, threading.Lock] = {}   # one try at a time per draft or lock (see _locked)
        self.titles: dict[int, str] = {}
        self.pieces_seen: set[tuple[int, int]] = set()
        self.resumed: dict[int, bool] = {}        # a narration that began with pieces already on disk
        self.first_audio = False
        self.hold_log: list[dict] = []            # beside sched.holds: {"decision", "ended"}
        self.timeouts_seen = 0
        self.notes_seen, self.notes_sig = 0, None
        self.judging: dict[tuple, float] = {}     # a note with the judge -> since when
        self.solo_logged: set[int] = set()        # sections whose solo fallback is in the timeline
        self.stopping = threading.Event()         # the run is leaving: narration ends with the piece it is making
        self.worked = False                       # this start had something to do (not a finished run started again)
        self.needed: set[int] = set()
        self.player_seen = 0
        self.playing = self.played_out = False
        self.heard = 0                            # the section the listener was last playing
        self.page_at = None
        self.last_now = 0.0
        self.last_fields: dict | None = None
        self.idle_turns = 0
        self.archived: dict | None = None
        self.medians: dict = {}
        self.estimate: float | None = None

    # --- the run ------------------------------------------------------------------------

    def run(self) -> dict:
        r, hurry = self.r, False
        try:
            continued = base.path(r, "timeline").exists()
            self.tl = base.Timeline(r, self.clock)
            self.tl.event(T.CONTINUED if continued else T.SEED_SET, cap_usd=r.max_usd)
            r.decide("continue" if continued else "start",
                     f"A fast run: scout, outline, then sections researched, written, edited and narrated while "
                     f"the listener listens. Cap ${r.max_usd:.2f}, ${r.spent():.2f} spent before this.")
            self._prepare()
            self._loop()
            return self._finish()
        except BaseException as e:  # anything at all, Ctrl-C included: the page must not keep saying "making"
            hurry = isinstance(e, KeyboardInterrupt)
            self._problem(e)
            raise
        finally:
            self._close(hurry)

    def _close(self, hurry: bool) -> None:
        """Leave the lanes. From here nothing queued is started, and narration ends with the piece
        it is making. A call in flight is paid for whether or not anyone waits for it, and its
        findings and its line in the log are written only when it returns: so, unless the run
        was interrupted, the actions in flight are waited for first (_wait_out)."""
        self.stopping.set()
        for lane in self.lanes.values():
            lane.closed.set()
        if self.lanes and not hurry:
            self._wait_out()
        deadline = time.monotonic() + GRACE
        for lane in self.lanes.values():
            lane.close(deadline)

    def _allowed(self, action: Action) -> float:
        """Seconds an action may take before it counts as lost: a research row its own timeout,
        a step fast.step_timeout_seconds (the opening and the archive, two steps each, twice that)."""
        if action[0] == "research":
            return self.f["research"]["row_timeout_seconds"]
        return self.f.get("step_timeout_seconds", 600) * (2 if action[0] in ("opening", "archive") else 1)

    def _wait_out(self) -> None:
        """Wait, fast.exit_wait_seconds at most, for the calls in flight to come back and be filed.
        Only for those still inside their own timeout: one past it is taken for hung and is left
        after GRACE like any other thread, so a call that never returns cannot hold the process
        (and the server's Try again) for long. The status already says what happened."""
        now = self._now()
        waiting = {a: live["token"] for a, live in self.live.items() if now - live["since"] < self._allowed(a)}
        judging = {k for k, since in self.judging.items() if now - since < self._allowed(("note",))}
        if not waiting and not judging:
            return
        limit = self.f.get("exit_wait_seconds", 150)
        print(f"  waiting up to {limit:.0f} s for {len(waiting) + len(judging)} call(s) in flight, so their cost "
              "is logged and their results are kept")
        deadline = time.monotonic() + limit
        while (waiting or judging) and (left := deadline - time.monotonic()) > 0:
            try:
                kind, token, action, payload, _ = self.q.get(timeout=min(left, 0.25))
            except queue.Empty:
                continue
            if kind == "note":
                judging.discard(_note_id(payload[0]))
            elif kind in ("done", "failed") and waiting.get(action) == token:
                del waiting[action]

    def _now(self) -> float:
        """The loop's clock, never running backwards (the player and the scheduler assume so)."""
        self.last_now = max(self.last_now, self.clock())
        return self.last_now

    def _prepare(self) -> None:
        """Scout and outline, in this thread; then everything the loop needs."""
        r, cfg, f = self.r, self.cfg, self.f
        self.narrator = narrate.Narrator(r, cfg)  # first: it refuses a build test continued as a real chapter
        self._voice()
        self.estimate = T.estimate(cfg)
        self.medians = T.load(cfg) or {}
        had, t0 = base.path(r, "scout").exists(), self.clock()
        if not had:  # a continued run's first status is the loop's, with its sections: never "making" from nothing
            self._status()
        dossier = self.dossier = Dossier(r, cfg)

        adopted = scout.adopt(r, cfg, r.root.parent)
        scout.run(r, cfg, dossier)
        if not had:
            self.tl.event(T.SCOUT_DONE, seconds=round(self.clock() - t0, 2), adopted=adopted,
                          entries=len(dossier.numbers_of(base.SCOUT_ROW)))

        self.stage, self.label = "outline", "Planning the chapter"
        had, t0 = base.path(r, "outline_json").exists(), self.clock()
        if not had:
            self._status()
        self.outline = outline_mod.run(r, cfg, dossier)
        with base.LEDGER_LOCK:
            self.led = ledger.normalize(json.loads(r.path("ledger").read_text(encoding="utf-8")))
        if not had:
            self.tl.event(T.OUTLINE_DONE, seconds=round(self.clock() - t0, 2), title=self.outline["title"],
                          sections=len(self.outline["sections"]), rows=len(self.led["rows"]))

        self.scout_text = scout.text(r)
        self.ctx = sections.Ctx(r, cfg, self.outline, self.led, dossier)
        self.budget = research.Budget.for_run(r, cfg)
        self.titles = status.titles(r, self.outline)
        done = done_from_files(r, self.outline, self.led, dossier, self.episode)
        self.pieces_seen = {(n, i) for n, lengths in done["pieces"].items() for i in range(len(lengths))}
        self.first_audio = bool(self.pieces_seen)
        before = base.read_timeline(r)
        self.notes_seen = sum(1 for e in before if e["event"] == T.LISTENER_NOTE)
        self.solo_logged = {e["section"] for e in before if e["event"] == T.SOLO_FALLBACK}
        rows = {row["id"]: row["section"] for row in ledger.active(self.led) if isinstance(row.get("section"), int)}
        self.sched = Scheduler([s["n"] for s in self.outline["sections"]], rows, max_hold=f["max_hold_seconds"],
                               research_parallel=f["research"]["parallel"],
                               row_timeout=f["research"]["row_timeout_seconds"], done=done)
        self.timeouts_seen = len(self.sched.timed_out)  # given up on before a restart: already in the timeline
        parallel = f["research"]["parallel"]
        self.lanes = {"research": _Lane("research", 2 * parallel), "writing": _Lane("writing", 2),
                      "narration": _Lane("narration", 1), "notes": _Lane("notes", 1)}

    def _voice(self) -> None:
        """Before anything is paid for: will the ElevenLabs account cover the narration? A chapter
        that ran out part-way would stop mid-listen with its research and writing already paid."""
        ahead = chars_ahead(self.cfg, self.r)
        got = read_balance(self.r) if ahead else None
        if got is not None and got[1] - got[0] < ahead:
            raise NoCharacters(f"Not enough narration characters this month: the chapter may still need up to "
                               f"{ahead:,}, and the ElevenLabs account has {max(0, got[1] - got[0]):,} left "
                               f"({got[0]:,} of {got[1]:,} used). Stopped before paying for anything.")

    def _loop(self) -> None:
        sched = self.sched
        if sched.finished:
            return
        self.worked = True
        llm.get_client()  # built here, once, not by five threads at the same moment
        tick = self.f.get("tick_seconds", 0.5)
        now = self._now()
        self._begin(sched.start(now), now)
        self._status()  # the sections, and what is being written, before the first result is in
        while not sched.finished:
            try:
                event = self.q.get(timeout=tick)
            except queue.Empty:
                event = None
            while event is not None:
                self._handle(event)
                try:
                    event = self.q.get_nowait()
                except queue.Empty:
                    event = None
            if sched.finished:
                break
            now = self._now()
            self._begin(self._position(now), now)
            actions = sched.tick(now)
            for row in sched.timed_out[self.timeouts_seen:]:  # given up on: its section is written without it
                self.tl.event(T.RESEARCH_TIMEOUT, row=row, section=sched.rows.get(row))
            self.timeouts_seen = len(sched.timed_out)
            self._begin(actions, now)
            self._timeouts(now)
            self._holds(now)
            self._notes()
            self._dead()
            # Nothing running and nothing queued, yet not finished: the scheduler has nothing more to
            # give, so waiting would be for ever. (Two turns, so an event in flight is not mistaken for it.)
            self.idle_turns = 0 if (sched.running or self.live or not self.q.empty()) else self.idle_turns + 1
            if self.idle_turns >= 2:
                raise StageError("The fast run stalled: nothing is running and the chapter is not finished. "
                                 f"State: {base.path(self.r, 'state')}")
            self._status()

    def _dead(self) -> None:
        dead = self.sched.dead
        if dead:
            action = tuple(dead["action"])
            self.failed_at = self._step(action)
            tries = self.sched.failures.get(action, 0) + (action in self.retried)
            raise StageError(f"{_name(action)} failed {tries} times, the last with: "
                             f"{self.errors.get(action, 'no error recorded')}")

    # --- starting actions ---------------------------------------------------------------

    def _begin(self, actions: list[Action], now: float) -> None:
        """Start the actions the scheduler has just issued, each with its line in the timeline."""
        for action in actions:
            kind = action[0]
            if kind == "research":
                self.tl.event(T.RESEARCH_START, row=action[1], section=self.sched.rows.get(action[1]))
            elif kind == "opening":
                self.tl.event(T.OPENING_START, section=1)
            elif kind == "draft":
                self.tl.event(T.DRAFT_START, section=action[1])
            elif kind == "lock":
                if action[2] == "solo" and action[1] not in self.solo_logged:  # once a section, however often issued
                    self.solo_logged.add(action[1])
                    waited = next((e.get("waited") for e in reversed(self.sched.log) if e["action"] == action), None)
                    self.tl.event(T.SOLO_FALLBACK, section=action[1], waited=waited)
                    self.r.decide("solo edit", f"Section {action[1]} is being locked on its own: the listener had "
                                  f"waited {waited or self.sched.max_hold:.0f} s for it and the next section's draft "
                                  "was not there to read beside it. It is still edited; it loses the look ahead.")
                self.tl.event(T.LOCK_START, section=action[1], mode=action[2])
            elif kind == "narrate":
                self.tl.event(T.NARRATE_START, section=action[1])
            elif kind == "archive":
                self.tl.event(T.ARCHIVE_START)
            self._start(action, now)

    def _start(self, action: Action, now: float) -> None:
        self.token += 1
        token = self.token
        self.live[action] = {"token": token, "since": now}
        if action[0] == "narrate":
            self.resumed[action[1]] = any(n == action[1] for n, _ in self.pieces_seen)
        lane, work = self._work(action, token)

        def job() -> None:
            t0 = self.clock()
            try:
                result = work()
            except BaseException as e:  # whatever it is, the loop must hear of it
                self.q.put(("failed", token, action, e, self.clock() - t0))
            else:
                self.q.put(("done", token, action, result, self.clock() - t0))

        self.lanes[lane].submit(job)

    def _locked(self, key: tuple, work):
        """Run `work` with one try at a time per draft, lock or archive. After a step timeout the
        first try may still be running when the scheduler issues the step again; the second then
        waits, finds the first one's file and returns it, instead of writing a second version
        beside it (or, for the archive, paying for the same reference cards twice)."""
        with self.mutex.setdefault(key, threading.Lock()):
            return work()

    def _work(self, action: Action, token: int):
        """(lane, what the worker runs) for an action. Nothing here touches the scheduler."""
        r, cfg, ctx, kind = self.r, self.cfg, self.ctx, action[0]

        if kind == "research":
            row = ledger.by_id(self.led)[action[1]]
            return "research", lambda: research.run_row(r, cfg, row, self.led, self.outline, self.scout_text,
                                                        self.dossier, self.budget)

        if kind == "opening":
            def opening() -> str:
                draft = sections.write_opening(ctx, notes.load(r.root))
                return sections.edit_opening(ctx, draft, notes.load(r.root))  # the notes as they stand now
            return "writing", lambda: self._locked(("lock", 1), opening)

        if kind == "draft":
            n = action[1]

            def draft() -> str:
                earlier = []
                for k in range(1, n):  # every earlier section in full: its locked text, else its latest draft
                    locked = sections.locked_text(r, k)
                    text = locked if locked is not None else sections.latest_draft(r, k)
                    if text is None:
                        raise StageError(f"Section {n} cannot be drafted: section {k} has no text yet.")
                    earlier.append((k, text, locked is not None))
                return sections.draft(ctx, n, earlier, notes.load(r.root))
            return "writing", lambda: self._locked(("draft", n), draft)

        if kind == "lock":
            _, n, mode = action

            def lock() -> sections.Lock:
                text = sections.latest_draft(r, n)  # from the files: an earlier lock may have revised it
                if text is None and sections.locked_text(r, n) is None:
                    raise StageError(f"Section {n} cannot be locked: it has no draft.")
                nxt = sections.latest_draft(r, n + 1) if mode == "paired" else None
                before = [(k, sections.locked_text(r, k)) for k in range(1, n)]
                got = sections.lock(ctx, n, text or "", nxt, before, notes.load(r.root), mode)
                sections.apply(ctx, got)
                return got
            return "writing", lambda: self._locked(("lock", n), lock)

        if kind == "narrate":
            n = action[1]

            def heard(p) -> None:
                self.q.put(("piece", token, action, p, self.clock()))
                if self.stopping.is_set() and not p.last:  # the pieces made are kept; the rest wait for the next start
                    raise StageError(f"The run is stopping: section {n}'s narration ends with piece {p.index + 1}.")

            def narration() -> list:
                out = self.narrator.section(n, sections.locked_text(r, n), on_piece=heard)
                try:  # the script and timing so far, for a question asked mid-chapter (free; the archive redoes it)
                    archive.rolling(r, cfg)
                except Exception as e:
                    r.log({"stage": "fast_run", "label": f"script and timing so far, after section {n}",
                           "error": _error(e)})
                return out
            return "narration", narration

        if kind == "archive":
            def finish() -> tuple[dict, str | None]:
                result = archive.run(r, cfg, build_episode=False)
                if not self.episode:
                    return result, None
                try:  # the chapter is whole from here on: a failed episode build is recorded, not a problem
                    return archive.run(r, cfg, build_episode=True), None
                except StageError as e:
                    return result, str(e)
            return "writing", lambda: self._locked(("archive",), finish)

        raise StageError(f"The scheduler issued an action the driver does not know: {action!r}")

    # --- results ------------------------------------------------------------------------

    def _handle(self, event: tuple) -> None:
        kind, token, action, payload, secs = event
        if kind == "note":
            return self._note_decided(*payload)
        live = self.live.get(action)
        if live is None or live["token"] != token:
            return  # from a try that was given up on (a step timeout): its files are found by the next try
        now = self._now()
        if kind == "piece":
            return self._piece(payload, now, payload_at=secs, started=live["since"])
        del self.live[action]
        if kind == "failed":
            return self._failed(action, payload, now)
        self._done(action, payload, round(secs, 2), now)
        self._dead()

    def _done(self, action: Action, result, secs: float, now: float) -> None:
        sched, tl, kind = self.sched, self.tl, action[0]
        if kind == "research":
            tl.event(T.RESEARCH_DONE, row=action[1], section=sched.rows.get(action[1]), seconds=secs,
                     entries=len(result["numbers"]), thin=result["thin"], error=result["error"],
                     searches=result["searches"], paid=bool(result["cost"]))
            acts = sched.row_done(action[1], now)
        elif kind == "opening":
            tl.event(T.OPENING_DONE, section=1, seconds=secs, words=word_count(result))
            acts = sched.opening_done(now)
        elif kind == "draft":
            tl.event(T.DRAFT_DONE, section=action[1], seconds=secs, words=word_count(result))
            acts = sched.draft_done(action[1], now)
        elif kind == "lock":
            n = action[1]
            if result.title:
                self.titles[n] = result.title
            tl.event(T.LOCK_DONE, section=n, mode=action[2], seconds=secs, words=word_count(result.text),
                     moved=len(result.moved), cut=len(result.cut), problems=len(result.problems),
                     corrections=len(result.corrections), title=result.title)
            acts = sched.lock_done(n, now)
        elif kind == "narrate":
            n = action[1]
            tl.event(T.NARRATE_DONE, section=n, seconds=secs, pieces=len(result),
                     audio_seconds=round(sum(p.seconds for p in result), 2), resumed=self.resumed.get(n, False))
            acts = sched.narrate_done(n, now)
        else:  # archive
            saved, episode_error = result
            self.archived = saved
            tl.event(T.ARCHIVE_DONE, seconds=secs, audio_seconds=saved["seconds"], words=saved["words"],
                     sections=len(saved["sections"]), timing=saved.get("timing"), episode=bool(saved.get("episode")))
            if episode_error:
                tl.event(T.EPISODE_FAILED, error=episode_error)
                self.r.decide("episode", "The chapter is finished and plays, but its episode page was not built: "
                              f"{episode_error}\nTo build it: python -m episode build {self.r.name}")
            acts = sched.archive_done(now)
        self._begin(acts, now)

    def _piece(self, piece, now: float, payload_at: float, started: float) -> None:
        """A narrated piece is playable. The narrator reports pieces it reused from disk as well, so
        the scheduler is told every time (by index, which makes a repeat harmless) and the timeline once."""
        key = (piece.section, piece.index)
        acts = self.sched.piece_ready(piece.section, piece.seconds, now, last=piece.last, index=piece.index)
        if key not in self.pieces_seen:
            self.pieces_seen.add(key)
            self.tl.event(T.PIECE_READY, section=piece.section, index=piece.index, seconds=piece.seconds,
                          after=round(max(0.0, payload_at - started), 2), last=piece.last, simulated=piece.simulated)
            if not self.first_audio:
                self.first_audio = True
                self.tl.event(T.FIRST_AUDIO, section=piece.section)
                self._status()
        self._begin(acts, now)

    def _failed(self, action: Action, e: BaseException, now: float) -> None:
        """A worker raised. Out of money stops the run at once. A research row is a thin row. Anything
        else is tried once more here, then handed to the scheduler, which issues it one more time and
        after that gives the run up: three tries in all."""
        if isinstance(e, KeyboardInterrupt):
            raise e
        text = self.errors[action] = _error(e)
        if any(m in text for m in overnight.MONEY_CAP):
            self.failed_at = self._step(action)
            raise StageError(text)
        if action[0] == "research":
            self.tl.event(T.RESEARCH_DONE, row=action[1], section=self.sched.rows.get(action[1]), seconds=None,
                          entries=0, thin="the research agent failed", error=text, searches=0, paid=False)
            return self._begin(self.sched.failed(action, now), now)
        if action not in self.retried:
            self.retried.add(action)
            self.tl.event(T.RETRY, action=_name(action), error=text)
            print(f"  {_name(action)} failed ({text}); trying once more")
            return self._start(action, now)
        self.tl.event(T.ACTION_FAILED, action=_name(action), error=text)
        acts = self.sched.failed(action, now)
        self._dead()
        self._begin(acts, now)

    def _timeouts(self, now: float) -> None:
        """A draft, lock or narration that has not come back in fast.step_timeout_seconds counts as
        failed (the opening and the archive, two steps each, get twice that). Its thread may still be
        busy, so there is no retry here: the scheduler issues it again, and that try waits its turn."""
        for action, live in list(self.live.items()):
            allowed = self._allowed(action)
            if action[0] == "research" or now - live["since"] < allowed:
                continue  # a research row's timeout is the scheduler's own
            del self.live[action]
            self.errors[action] = f"no result after {allowed:.0f} s"
            self.tl.event(T.STEP_TIMEOUT, action=_name(action), seconds=round(now - live["since"], 1))
            acts = self.sched.failed(action, now)
            self._dead()
            self._begin(acts, now)

    # --- the listener -------------------------------------------------------------------

    def _position(self, now: float) -> list[Action]:
        """Where the listener is: step the simulated player, or read what the page last reported."""
        sched = self.sched
        if self.player is not None:
            report = self.player.step(now, sched.audio(), sched.last)
        else:
            raw = base.read_json(base.path(self.r, "player"))
            if not isinstance(raw, dict) or raw.get("at") == self.page_at:
                return []
            self.page_at = raw.get("at")
            report, age = page_report(raw, now, time.time())
            if age > sched.report_timeout:
                return []
        self._playback(report)
        return sched.player(report, now)

    def _playback(self, report: dict) -> None:
        """What the listener did, for the timeline: began, reached a section, skipped, finished."""
        tl, state = self.tl, report.get("state")
        if state == "playing" and report.get("section"):
            self.heard = int(report["section"])
        events = getattr(self.player, "events", None)
        if events is not None:  # a simulated player keeps its own exact record
            for e in events[self.player_seen:]:
                t, name = round(e["now"] - tl.zero, 2), e["event"]
                if name == "started":
                    tl.event(T.PLAYBACK_START, t=t)
                elif name in ("section_start", "hold_start"):
                    self._needed(e["section"], "playing" if name == "section_start" else "waiting", t)
                elif name == "skip" and e.get("moved"):
                    tl.event(T.SKIP, t=t, to=e["to"], **{"from": e["from"]})
                elif name == "done":
                    tl.event(T.PLAYBACK_DONE, t=t)
            self.player_seen = len(events)
            return
        if state in ("playing", "waiting", "paused", "done") and not self.playing:
            self.playing = True
            tl.event(T.PLAYBACK_START)
        at = report.get("waiting_for") or report.get("section")
        if state in ("playing", "waiting") and at:
            self._needed(int(at), state, tl.now())
        if state == "done" and not self.played_out:
            self.played_out = True
            tl.event(T.PLAYBACK_DONE)

    def _needed(self, n: int, state: str, t: float, **more) -> None:
        if n not in self.needed:
            self.needed.add(n)
            self.tl.event(T.SECTION_NEEDED, t=t, section=n, state=state, **more)

    def _holds(self, now: float) -> None:
        """The timeline's record of each wait, and what the wait is: a pause when the section is
        expected within fast.short_wait_seconds, else a check-in, where the guide comes in. A pause
        that outlasts that estimate becomes a check-in too: the guess was wrong, and silence is the
        one thing the check-in is there to prevent."""
        sched, tl, short = self.sched, self.tl, self.f["short_wait_seconds"]
        for i, hold in enumerate(sched.holds):
            if i == len(self.hold_log):
                self.hold_log.append({"decision": None, "ended": False})
                tl.event(T.HOLD_START, t=round(hold["start"] - tl.zero, 2), section=hold["section"],
                         reason=hold["reason"])
                eta = sched.eta(hold["section"], now, self.medians)
                self._check_in(i, hold, checkin.decide(eta, short), eta, now)
            log = self.hold_log[i]
            if log["ended"]:
                continue
            if hold["end"] is None:
                if log["decision"] == "pause" and now - hold["start"] > short:
                    self._check_in(i, hold, "check_in", sched.eta(hold["section"], now, self.medians), now, late=True)
                continue
            log["ended"] = True
            tl.event(T.HOLD_END, t=round(hold["end"] - tl.zero, 2), section=hold["section"],
                     seconds=hold["seconds"], reason=hold["reason"], decision=log["decision"],
                     solo=bool(hold.get("solo")))

    def _check_in(self, i: int, hold: dict, decision: str, eta: float | None, now: float, late: bool = False) -> None:
        self.hold_log[i]["decision"] = decision
        self.tl.event(T.CHECK_IN, section=hold["section"], decision=decision, eta=eta, late=late)
        if decision == "check_in" and self.listener is not None:
            heard, root = self.heard, self.r.root
            try:  # the scripted listener is a test's; its mistake must not cost a run that is being paid for
                self.listener.on_check_in(now, hold["section"],
                                          lambda text, kind=None: notes.add(root, text, kind, section=heard))
            except Exception as e:
                self.r.log({"stage": "fast_run", "label": "the scripted listener at a check-in", "error": _error(e)})

    def _notes(self) -> None:
        """New lines in listener_notes.md (the server appends them). Each goes to the timeline; the
        writers and editors read the file themselves when they start. A note that a missing fact
        could be behind is also put to the note judge, off this thread."""
        r, sched = self.r, self.sched
        try:
            st = notes.notes_path(r.root).stat()
        except FileNotFoundError:
            return
        if (st.st_mtime_ns, st.st_size) == self.notes_sig:
            return
        self.notes_sig = (st.st_mtime_ns, st.st_size)
        every = notes.load(r.root)
        for note in every[self.notes_seen:]:
            self.tl.event(T.LISTENER_NOTE, kind=note["kind"], section=note["section"], text=note["text"],
                          said=note["time"])
            open_now = sched.open_sections()
            if note["kind"] not in research.NOTE_KINDS or not open_now:
                continue

            def judge(note=note, open_now=open_now) -> None:
                try:  # the run's budget: its notes' counter also knows what a running agent has reserved
                    row = research.note_row(r, self.cfg, note, self.outline, self.led, self.dossier, open_now,
                                            budget=self.budget)
                except BaseException as e:
                    self.q.put(("note", None, None, (note, None, e), None))
                else:
                    self.q.put(("note", None, None, (note, row, None), None))

            self.judging[_note_id(note)] = self.last_now
            self.lanes["notes"].submit(judge)
        self.notes_seen = len(every)

    def _note_decided(self, note: dict, row: dict | None, error: BaseException | None) -> None:
        """The note judge has answered. A new row is researched like any other, if its section's
        draft has still not begun (the scheduler says no otherwise, and the row stays unresearched)."""
        sched, now = self.sched, self._now()
        self.judging.pop(_note_id(note), None)
        if error is not None:
            self.tl.event(T.NOTE_ROW, row=None, error=_error(error), text=note["text"])
            return
        if row is None or sched.has_row(row["id"]):
            return
        acts = sched.row_added(row["id"], row["section"], now)
        self.tl.event(T.NOTE_ROW, row=row["id"], section=row["section"], question=row["research_question"],
                      researched=sched.has_row(row["id"]), text=note["text"])
        self._begin(acts, now)

    # --- status.json --------------------------------------------------------------------

    def _step(self, action: Action) -> tuple[str, str]:
        """(stage, what the page says) for one action: where a problem happened."""
        kind, total = action[0], self.sched.last
        if kind == "opening":
            return "opening", "Writing the opening"
        if kind == "archive":
            return "archive", "Finishing"
        if kind == "research":
            return f"research {action[1]}", "Researching"
        doing = {"draft": "Writing", "lock": "Editing", "narrate": "Recording"}[kind]
        return f"{kind} {action[1]}", f"{doing} part {action[1]} of {total}"

    def _where(self) -> tuple[str, str]:
        """(stage, what the page says) from what is running."""
        sched = self.sched
        if sched is None:
            return self.stage, self.label
        total = sched.last
        if sched.archived:
            return "archive", "Finishing"
        if not self.first_audio:
            return "opening", "Writing the opening" if 1 not in sched.locked else "Recording the opening"
        n = next((k for k in sched.sections if k not in sched.narrated), total)
        doing = "Recording" if n in sched.locked else "Writing"
        return f"section {n}", f"{doing} part {n} of {total}"

    def _fields(self) -> dict:
        sched = self.sched
        snapshot = sched.snapshot() if sched else None
        hold = sched.hold() if sched else None
        waiting = hold["section"] if hold else None
        eta = sched.eta(waiting, self.last_now, self.medians) if hold else None
        logged = hold is not None and len(self.hold_log) == len(sched.holds)
        decision = self.hold_log[-1]["decision"] if logged else None
        self.stage, self.label = self._where()
        done, total = status.progress(snapshot, len(self.outline["sections"]) if self.outline else self.f["sections"][1])
        left = None
        if self.estimate is not None and not self.first_audio and self.tl is not None:
            left = max(1, math.ceil((self.estimate - self.tl.now()) / 60))
        return {"stage": self.stage, "label": self.label, "stages_done": done, "stages_total": total,
                "minutes_left": left, "title": self.outline["title"] if self.outline else None,
                **status.fields(self.r, self.outline, snapshot, self.titles, first_audio=self.first_audio,
                                waiting_for=waiting, eta_seconds=eta, check_in=decision)}

    def _status(self) -> None:
        """Write status.json when what it says has changed. Only ever called from the loop's thread."""
        fields = self._fields()
        if fields == self.last_fields:
            return
        self.last_fields = fields
        overnight.write_status(self.r, self.cfg, "making", **fields)
        if self.sched is not None:
            base.write_json(base.path(self.r, "state"), self.sched.snapshot())

    # --- the two ways out ---------------------------------------------------------------

    def _finish(self) -> dict:
        r, cfg, sched, tl = self.r, self.cfg, self.sched, self.tl
        saved = self.archived or base.read_json(base.path(r, "work") / "archive.json") or {}
        if getattr(self.player, "events", None) is not None:
            # Everything is ready, so the rest of the listening holds no waits: play it out in made-up
            # time to learn when each remaining section is reached (the report's "needed" column).
            now = self._now()
            self._playback(self.player.step(now, sched.audio(), sched.last))  # all of it is there for the listener now
            self.player.step(now + 1e7, sched.audio(), sched.last)
            for e in self.player.events[self.player_seen:]:
                if e["event"] == "section_start":
                    self._needed(e["section"], "playing", round(e["now"] - tl.zero, 2), projected=True)
                elif e["event"] == "done":
                    tl.event(T.PLAYBACK_DONE, t=round(e["now"] - tl.zero, 2), projected=True)
            self.player_seen = len(self.player.events)
        held = sum(h["seconds"] for h in sched.holds)
        words, seconds = saved.get("words", 0), saved.get("seconds", 0.0)
        cost = round(r.spent(), 2)
        if self.worked:  # a finished run started again did nothing: it is not ready a second time
            tl.event(T.READY, words=words, audio_seconds=seconds, holds=len(sched.holds), held_seconds=round(held, 1),
                     solo=list(sched.solo), cost_usd=cost)
            r.decide("done", f"Finished: {len(sched.sections)} sections, {words:,} words, {seconds / 60:.1f} minutes "
                             f"of audio ({r.path('chapter_mp3').relative_to(r.root)}); {len(sched.holds)} hold(s), "
                             f"{held:.0f} s in all; {len(sched.solo)} solo edit(s); ${cost:.2f} spent in all.")
        for what, step in (("the listener's history", lambda: _record(cfg, r)),
                           ("the timings", lambda: T.update(cfg, r))):
            try:  # the chapter is made: bookkeeping that fails is recorded, never a problem for the listener
                step()
            except Exception as e:
                r.decide("after the chapter", f"Could not update {what}: {_error(e)}")
        snapshot = sched.snapshot()
        _, total = status.progress(snapshot, len(sched.sections))
        overnight.write_status(r, cfg, "ready", audio=str(r.path("chapter_mp3").relative_to(r.root)), minutes_left=0,
                               stages_done=total, stages_total=total, title=self.outline["title"],
                               **status.fields(r, self.outline, snapshot, self.titles, first_audio=True))
        base.write_json(base.path(r, "state"), snapshot)
        print(f"\n  {r.name} is ready: {r.path('chapter_mp3').relative_to(r.root)}, {words:,} words, "
              f"{seconds / 60:.1f} min, ${cost:.2f}\n  Decisions: {r.path('decisions')}")
        return {"run": r.name, "words": words, "seconds": seconds, "sections": len(sched.sections),
                "holds": [dict(h) for h in sched.holds], "solo": list(sched.solo), "cost_usd": cost,
                "episode": bool(saved.get("episode"))}

    def _problem(self, e: BaseException) -> None:
        """Like stages/new.py: status "problem" in plain words, the decision recorded, and for a
        StageError the way to continue. The caller re-raises."""
        r = self.r
        err = "interrupted" if isinstance(e, KeyboardInterrupt) else str(e)
        detail = f"{type(e).__name__}: {e}"
        try:
            more = {k: v for k, v in self._fields().items() if k not in ("stage", "label", "minutes_left")}
        except Exception:  # the problem must be written whatever state the run was left in
            more = {"mode": status.MODE}
        stage, label = self.failed_at or (self.stage, self.label)  # the step that failed, else what was going on
        message, retryable = overnight.plain_problem(label, err)
        if isinstance(e, NoCharacters):  # trying again cannot help until the month turns or the plan grows
            message, retryable = "There are not enough narration characters this month to make this chapter.", False
        if self.tl is not None:
            self.tl.event(T.PROBLEM, stage=stage, error=detail)
        r.decide("stopped", f"Stopped at {stage}: {detail}")
        overnight.write_status(r, self.cfg, "problem", stage=stage, label=label, message=message,
                               retryable=retryable, detail=detail, minutes_left=None, **more)
        if isinstance(e, StageError):
            raise StageError(f"stopped at {stage}: {e}\n  To continue: {self.again} "
                             f"(raise --max-usd if the cap stopped it). Decisions so far: {r.path('decisions')}") from None


def check_folder(r: Run) -> None:
    """Refuse a folder the overnight pipeline has worked in (stage outputs, and no fast.json).
    Its ledger rows have no sections and its research answers other questions: a fast run on top
    of them would crash, or finish "ready" with no research of its own."""
    theirs = [PATHS[k] for k in OVERNIGHT_FILES if r.path(k).exists()]
    if theirs and not base.is_fast(r):
        raise StageError(f"{r.name} is not a fast run: it already has {', '.join(theirs)} from the overnight "
                         "pipeline. A fast run needs its own folder: start a new run.")


def _record(cfg: dict, r: Run) -> None:
    from stages import memory

    memory.record_chapter(cfg, r)  # listener/history.md: what the guide has made for this listener


def run(r: Run, cfg: dict, *, player=None, listener=None, clock=time.time, episode: bool = True,
        again: str | None = None) -> dict:
    """Run (or continue) a fast run to its archive. Returns {"run", "words", "seconds", "sections",
    "holds", "solo", "cost_usd", "episode"}.

    `player`: a faststart.player.SimPlayer, stepped on every turn of the loop (tests and the
    end-to-end harness); without one the listener's position is read from fast/player.json.
    `listener`: an object whose on_check_in(now, section, add_note) is called when a wait becomes
    a check-in; add_note(text, kind=None) saves a listener note as the guide's add_note would.
    `clock`: the loop's clock, seconds (tests speed it up). `episode`: build the episode bundle.
    `again`: the command that continues this run, for the message of a stop (by default cli.py's).

    Any failure writes status "problem" and is re-raised; a StageError says how to continue.
    A folder that another mode has worked in is refused before anything is written to it."""
    if not r.unattended:
        raise StageError("A fast run is unattended: nobody is asked anything on the way (new --unattended --mode fast).")
    check_folder(r)
    if r.max_usd is None:
        r.max_usd = base.settings(cfg)["max_run_usd"]
    return _Driver(r, cfg, player, listener, clock, episode, again).run()
