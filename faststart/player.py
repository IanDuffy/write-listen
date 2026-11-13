"""A simulated listener, for the scheduler simulation and the end-to-end build runs.

It does what the page's player does, with no audio: it plays the ready pieces in order and
reports its position in the dict the page posts. Pure code, in whatever clock the caller keeps.
"""

from __future__ import annotations

from .checkin import next_marker

EPS = 1e-9


class SimPlayer:
    """A listener who plays in real time (or made-up time: every method takes now). It plays the
    ready pieces in order, section by section; at the end of the ready audio it waits. A skip
    jumps to the next section marker only (acceptance check 6): if that section is ready it
    plays from its start, if not the player waits for it. A skip while already waiting at a
    marker stays there (the next marker is the one being waited for), and so does a skip while
    paused at the start of a section with no audio yet (an earlier tap in the same pause went
    there); a skip in the last section does nothing. A pause stops the clock on listening for
    its length."""

    def __init__(self, skips: list[float] = (), pauses: list[tuple[float, float]] = (), *,
                 skip_times: list[float] = ()):
        """skips: listening-seconds positions (seconds of audio heard) at which the listener taps skip.
        pauses: (seconds of audio heard, seconds) at which they pause, and for how long.
        skip_times: taps by the clock (`now`), which is the only way to tap while waiting."""
        self.skips = sorted(float(s) for s in skips)
        self.pauses = sorted((float(at), float(secs)) for at, secs in pauses)
        self.skip_times = sorted(float(t) for t in skip_times)
        self.now: float | None = None
        self.section, self.piece, self.offset = 1, 0, 0.0   # the piece to play next, and seconds into it
        self.heard = 0.0                                    # seconds of audio heard
        self.state = "idle"       # idle (before the first audio), playing, waiting, paused, done
        self.since: float | None = None                     # when the wait began
        self.reason: str | None = None                      # end_of_audio | skip
        self.events: list[dict] = []
        self._paused_until: float | None = None
        self._skipped = False     # a skip moved the pointer; the next wait is the skip's
        self._started = False
        self._announced = 0       # the last section whose start was logged
        self._known: dict[int, dict] = {}   # what was playable at the last step

    def step(self, now: float, sections: dict[int, dict], last: int) -> dict:
        """sections: {n: {"pieces": [seconds, ...], "complete": bool}}. Advance to `now` and return
        the position report, the same dict the page posts:
        {"state": "idle"|"playing"|"waiting"|"paused"|"done", "section": n, "piece": i, "t": seconds
         into the section, "chapter_t": seconds of audio heard so far, "position": the place in the
         chapter's own time (skipped audio counted), "waiting_for": n | None, "since": time the wait
         began | None, "reason": "end_of_audio"|"skip"|None}.
        While waiting at a section's start, `section` is that section, `piece` 0 and `t` 0.

        Audio the player sees for the first time in this call counts as arriving at `now`: the time
        since the last call is lived with what was playable then, so nothing is heard before it
        existed. Step it whenever something becomes playable, and every few seconds besides."""
        sections = {int(n): {"pieces": list(s.get("pieces") or []), "complete": bool(s.get("complete"))}
                    for n, s in sections.items()}
        if self.now is None:
            self.now = now
        if now < self.now - EPS:
            raise ValueError(f"the player was stepped back in time ({now} < {self.now})")
        while self.skip_times and self.skip_times[0] <= now:
            self._run(max(self.skip_times.pop(0), self.now), self._known, last)
            self._skip(last)
        self._run(now, self._known, last)
        self._known = sections
        self._run(now, sections, last)
        return self._report(sections)

    # --- the clock ----------------------------------------------------------------------

    def _run(self, limit: float, sections: dict[int, dict], last: int) -> None:
        """Advance to `limit`, through every change of state on the way."""
        while True:
            if self.state == "done":
                break
            if self._paused_until is not None:
                if self._paused_until > limit + EPS:
                    break
                self.now = max(self.now, self._paused_until)
                self._paused_until = None
                self._event("resume")
                continue
            info = sections.get(self.section) or {}
            pieces = info.get("pieces") or []
            if self.piece >= len(pieces) and info.get("complete"):
                if self.section == last:
                    self._end_wait()
                    self.state = "done"
                    self._event("done")
                    break
                self.section, self.piece, self.offset = self.section + 1, 0, 0.0
                continue
            if self.piece >= len(pieces):
                self._wait()
                break
            self._play()
            if self.skips and self.skips[0] <= self.heard + EPS:
                self.skips.pop(0)
                self._skip(last)
                continue
            if self.pauses and self.pauses[0][0] <= self.heard + EPS:
                _, seconds = self.pauses.pop(0)
                self._paused_until = self.now + seconds
                self.state = "paused"
                self._event("pause", seconds=seconds)
                continue
            left = pieces[self.piece] - self.offset
            triggers = [x - self.heard for x in (self.skips[:1] + [at for at, _ in self.pauses[:1]])]
            dt = max(0.0, min([limit - self.now, left] + triggers))
            self.now += dt
            self.offset += dt
            self.heard += dt
            if any(x - dt <= EPS for x in triggers):
                continue                  # a tap or a pause is due: it happens where the listener is now
            if self.offset >= pieces[self.piece] - EPS:
                self.piece, self.offset = self.piece + 1, 0.0
                continue
            break
        self.now = max(self.now, limit)

    def _play(self) -> None:
        if not self._started:
            self._started = True
            self._event("started")
        self._end_wait()
        self.state, self._skipped = "playing", False
        if self.piece == 0 and self.offset == 0 and self._announced != self.section:
            self._announced = self.section
            self._event("section_start")

    def _wait(self) -> None:
        if not self._started:
            self.state = "idle"       # the wait before the first audio is not a hold
        elif self.state != "waiting":
            self.state, self.since = "waiting", self.now
            self.reason = "skip" if self._skipped else "end_of_audio"
            self._skipped = False
            self._event("hold_start", reason=self.reason)

    def _end_wait(self) -> None:
        if self.state == "waiting":
            self._event("hold_end", seconds=round(self.now - self.since, 6), reason=self.reason)
            self.since = self.reason = None

    def _skip(self, last: int) -> None:
        """A tap on skip: to the next section marker only."""
        here = self.section
        # Paused at the start of a section with no audio: the same place as waiting for it.
        unready = not (self._known.get(self.section) or {}).get("pieces")
        at_marker = self.piece == 0 and self.offset == 0 and (
            self.state in ("idle", "waiting") or (self.state == "paused" and unready))
        to = None if self.state == "done" or at_marker else next_marker(here, last)
        if to is None:
            # waiting at a marker (it is the next marker already), or nowhere to go
            self._event("skip", to=here if at_marker else None, moved=False, **{"from": here})
            return
        self._end_wait()
        self.section, self.piece, self.offset = to, 0, 0.0
        self._skipped = True
        if self.state != "paused":
            self.state = "skipping"   # settled by _run: playing if the section is ready, else waiting
        self._event("skip", to=to, moved=True, **{"from": here})

    # --- the report ---------------------------------------------------------------------

    def _report(self, sections: dict[int, dict]) -> dict:
        length = lambda n: sum((sections.get(n) or {}).get("pieces") or [])
        pieces = (sections.get(self.section) or {}).get("pieces") or []
        t = sum(pieces[:self.piece]) + self.offset
        waiting = self.state == "waiting"
        return {"state": self.state, "section": self.section, "piece": self.piece, "t": round(t, 3),
                "chapter_t": round(self.heard, 3),
                "position": round(sum(length(n) for n in range(1, self.section)) + t, 3),
                "waiting_for": self.section if waiting else None,
                "since": self.since if waiting else None, "reason": self.reason if waiting else None}

    def _event(self, name: str, **fields) -> None:
        self.events.append({"event": name, "now": self.now, "section": self.section, **fields})
