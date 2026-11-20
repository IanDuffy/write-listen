"""Rehearsal chapters (Ian, 2026-11-20): a finished live fast chapter replayed in place of making one.

When the voices are being tested, nothing needs researching or writing: a chapter that was made for real is
played back into the new run's folder on its own timeline, so every voice interaction still happens (the
making status and its countdowns, get_status, the ready offer, the reading paragraph by paragraph, asking
mid-chapter, pause and play, End demo stopping a chapter being made, the debrief's account) at no cost.

    which runs        a tester's live start_chapter when tester.json "chapter" (else config demo.chapter) is
                      "rehearsal"; with no tester (Ian's own app, the agent's page checks), config rehearsal.own
    the source        config rehearsal.source, else the newest complete live fast chapter under shared/rehearsal/
    the marker        fast.json "rehearsal": {source, source_run, sections, opening_seconds, after_speed,
                      marked_at}, written by the server's start_seed before the process starts. cli.py's `new`
                      dispatches on it (Retry too), and faststart/run.py refuses a marked folder, so a rehearsal
                      is never made for real on top of copied files
    the replay        run(): the source's fast_timeline.jsonl event by event, cut to the first `sections`
                      sections, its steps before the opening scaled to end at `opening_seconds` and its pace after
                      that times `after_speed`. Each event writes the files the real run had written by then (the
                      scout, the outline, research as each row lands, the dossier append-only, each section as it
                      locks, its paragraphs, the archive), the timeline, status.json and fast/state.json, in the
                      shapes faststart/run.py writes them, so the page, the guides, the card, the library and the
                      debrief read a rehearsal as they read a real run. No model, no ElevenLabs, no cost.json; the
                      log may get the live archive's line, which has no cost

A rehearsal never counts in demo.max_builds and is never queued (server.py): it calls no API, and a voice test
must never make a real tester's build wait. It is stopped like a real run (SIGINT to its process group; cli.py
turns SIGTERM into the same KeyboardInterrupt), writes status "problem" as a real run does, and continues
from its files on Retry. A finished rehearsal never enters the listener's history or the timings.

This module imports nothing from demo/ (tests stub those modules).
"""

from __future__ import annotations

import copy
import json
import math
import os
import shutil
import threading
import time
from pathlib import Path

from stages import common
from stages import ledger as ledger_mod
from stages import new as overnight
from stages.common import Run, StageError

from . import archive, base, checkin, live, status
from . import timings as T
from .scheduler import Scheduler

CHAPTER_VALUES = ("real", "rehearsal")
DEFAULTS = {"own": False, "source": None, "sections": 2, "opening_seconds": 90, "after_speed": 1.0}
CARD = "fast/card.json"  # faststart/card.py's file (not imported: it would bring the model client in)
TICK = 0.5
LATE_START = 10.0  # seconds after fast.json's seed_set past which a replay with nothing done yet starts its clock now
# The source events the replay plays; every other event in the source's timeline is skipped (narration,
# pieces and the first audio are regenerated from the copied text; the listener's own events were theirs).
REPLAYED = (T.SCOUT_DONE, T.OUTLINE_DONE, T.RESEARCH_START, T.RESEARCH_DONE, T.OPENING_START, T.OPENING_DONE,
            T.DRAFT_START, T.DRAFT_DONE, T.LOCK_START, T.LOCK_DONE, T.ARCHIVE_START, T.ARCHIVE_DONE, T.READY)
END = (T.ARCHIVE_START, T.ARCHIVE_DONE, T.READY)
# Never copied from the source: its costs, its listener and its session, and what this run writes itself.
NEVER = ("log.jsonl", "cost.json", "status.json", "fast/player.json", "fast/archive.json", "fast/state.json",
         "listener_notes.md", "live", "12_asks", "10_audio", "reading.jsonl", "decisions.md")


def settings(cfg: dict) -> dict:
    """config.yaml's rehearsal: block over the defaults (a test's cfg may have none)."""
    out = {**DEFAULTS, **(cfg.get("rehearsal") or {})}
    for k in ("opening_seconds", "after_speed"):  # null here means the default; sections null means all of them
        if not out.get(k):
            out[k] = DEFAULTS[k]
    return out


# --- the source -----------------------------------------------------------------------------

def _missing(root: Path) -> list[str]:
    """What a folder lacks to be replayed; [] when it is complete."""
    root = Path(root)
    out = []
    if (base.read_json(root / base.FILES["mode"]) or {}).get("voice") != "live":
        out.append('fast.json with "voice": "live"')
    outline = base.read_json(root / base.FILES["outline_json"])
    secs = outline.get("sections") if isinstance(outline, dict) else None
    if not isinstance(secs, list) or not secs:
        return out + ["05_outline.json with sections"]
    numbers = [s.get("n") for s in secs if isinstance(s, dict)]
    lost = [n for n in numbers if not (root / "06_segments" / f"seg{int(n):02d}.md").exists()]
    if lost:
        out.append("06_segments/" + ", ".join(f"seg{int(n):02d}.md" for n in lost))
    led = base.read_json(root / "ledger.json")
    if not (isinstance(led, dict) and isinstance(led.get("rows"), list)):
        out.append("ledger.json with rows")
    try:
        events = _timeline(root)
    except (OSError, ValueError):
        events = []
    names = {e.get("event") for e in events}
    if T.OPENING_DONE not in names:
        out.append("an opening_done in fast_timeline.jsonl")
    locked = {e.get("section") for e in events if e.get("event") == T.LOCK_DONE}
    if gaps := [n for n in numbers if int(n) >= 2 and n not in locked]:
        out.append("a lock_done for section " + ", ".join(str(n) for n in gaps))
    if T.READY not in names:
        out.append("a ready event")
    return out


def _timeline(root: Path) -> list[dict]:
    p = Path(root) / base.FILES["timeline"]
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def find_source(cfg: dict) -> Path:
    """The chapter folder to replay: rehearsal.source (relative to the repo), else the newest complete one
    under shared/rehearsal/. Raises StageError when there is none: never a paid real run instead."""
    named = settings(cfg).get("source")
    if named:
        p = Path(named)
        p = p if p.is_absolute() else common.ROOT / p
        if not p.is_dir():
            raise StageError(f"The rehearsal chapter {p} is not there (rehearsal.source).")
        if missing := _missing(p):
            raise StageError(f"The rehearsal chapter {p} can't be replayed: it has no {'; no '.join(missing)}.")
        return p.resolve()
    d = common.shared_dir() / "rehearsal"
    found = sorted((p for p in d.iterdir() if p.is_dir()), key=lambda p: (p.stat().st_mtime, p.name),
                   reverse=True) if d.is_dir() else []
    for p in found:
        if not _missing(p):
            return p.resolve()
    raise StageError("No rehearsal chapter to replay: put a finished live chapter in shared/rehearsal/ "
                     "or set rehearsal.source.")


def choice(cfg: dict, listener: str | None) -> str:
    """"real" or "rehearsal" for a live start_chapter by this listener. A tester (listeners/<id>/tester.json)
    takes tester.json "chapter", else config demo.chapter; anyone else takes rehearsal.own."""
    rec = base.read_json(common.LISTENERS_DIR / listener / "tester.json", None) if listener else None
    if isinstance(rec, dict):
        mine = rec.get("chapter")
        if mine in CHAPTER_VALUES:
            return mine
        given = (cfg.get("demo") or {}).get("chapter")
        return given if given in CHAPTER_VALUES else "real"
    return "rehearsal" if settings(cfg).get("own") else "real"


def source_for(cfg: dict, listener: str | None) -> Path | None:
    """The folder to replay for this listener's live start_chapter, or None for a real chapter. Raises
    StageError when a rehearsal is wanted and there is nothing to replay."""
    return find_source(cfg) if choice(cfg, listener) == "rehearsal" else None


# --- the marker and the card ------------------------------------------------------------------

def marked(root) -> dict | None:
    """fast.json's rehearsal marker, or None (a real run, or no fast.json). Never raises."""
    try:
        got = (base.read_json(Path(root) / base.FILES["mode"]) or {}).get("rehearsal")
    except Exception:
        return None
    return got if isinstance(got, dict) else None


def mark(r: Run, src: Path, cfg: dict) -> dict:
    """Write the marker into fast.json (which mode_args has written) unless it is there already."""
    p = base.path(r, "mode")
    info = base.read_json(p) or {}
    if isinstance(info.get("rehearsal"), dict):
        return info["rehearsal"]
    s = settings(cfg)
    n = len((base.read_json(Path(src) / base.FILES["outline_json"]) or {}).get("sections") or [])
    want = s.get("sections")
    k = min(int(want), n) if want else n
    info["rehearsal"] = {"source": str(Path(src).resolve()), "source_run": Path(src).name, "sections": max(1, k),
                         "opening_seconds": float(s["opening_seconds"]), "after_speed": float(s["after_speed"]),
                         "marked_at": base.now_iso()}
    base.write_json(p, info)
    return info["rehearsal"]


def card(r: Run, src: Path) -> dict:
    """fast/card.json from the source's outline, at once and for nothing (a real run pays one call for it)."""
    p = r.root / CARD
    have = base.read_json(p)
    if isinstance(have, dict):
        return have
    outline = base.read_json(Path(src) / base.FILES["outline_json"]) or {}
    angle = " ".join(str(outline.get("angle") or "").split())
    first = angle.split(". ")[0].rstrip(".") + "." if angle else ""
    out = {"title": outline.get("title") or "", "overview": first, "summary": angle, "run": r.name,
           "made_at": base.now_iso()}
    base.write_json(p, out)
    return out


# --- the replay ---------------------------------------------------------------------------------

def _copy(src: Path, dst: Path, rel: str) -> bool:
    """Copy one file at the same relative path, atomically. False when the source has none."""
    if rel.split("/")[0] in NEVER or rel in NEVER:
        return False
    a, b = Path(src) / rel, Path(dst) / rel
    if not a.is_file():
        return False
    b.parent.mkdir(parents=True, exist_ok=True)
    tmp = b.with_name(f"{b.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    shutil.copyfile(a, tmp)
    os.replace(tmp, b)
    return True


def _blocks(text: str) -> tuple[str, dict[str, str]]:
    """A dossier's head (to "## Entries", inclusive) and its "### Lnn" blocks, each to the next ### or ##."""
    lines = text.splitlines()
    try:
        at = next(i for i, x in enumerate(lines) if x.strip().lower().startswith("## entries"))
    except StopIteration:
        return text, {}
    head, blocks, cur, buf = "\n".join(lines[:at + 1]), {}, None, []
    for x in lines[at + 1:]:
        if x.startswith("### ") or x.startswith("## "):
            if cur:
                blocks[cur] = "\n".join(buf).rstrip()
            cur, buf = None, []
            if x.startswith("### "):
                cur = x[4:].split(":")[0].strip()
                buf = [x]
            else:
                break
        elif cur:
            buf.append(x)
    if cur:
        blocks[cur] = "\n".join(buf).rstrip()
    return head, blocks


class _Replay:
    def __init__(self, r: Run, cfg: dict, clock, sleep):
        self.r, self.cfg, self.clock, self.sleep = r, cfg, clock, sleep
        self.mark = marked(r.root)
        if not self.mark:
            raise StageError(f"{r.name} is not a rehearsal (no fast.json rehearsal marker).")
        self.src = Path(self.mark["source"])
        if missing := _missing(self.src):
            raise StageError(f"The rehearsal chapter {self.src} can't be replayed: it has no {'; no '.join(missing)}.")
        self.f = base.settings(cfg)
        self.outline_src = base.read_json(self.src / base.FILES["outline_json"])
        self.n_all = len(self.outline_src["sections"])
        self.k = max(1, min(int(self.mark.get("sections") or self.n_all), self.n_all))
        self.ledger_src = ledger_mod.normalize(json.loads((self.src / "ledger.json").read_text(encoding="utf-8")))
        self.rows_src = {row["id"]: row for row in self.ledger_src["rows"]}
        self.kept = [row["id"] for row in self.ledger_src["rows"]
                     if isinstance(row.get("section"), int) and row["section"] <= self.k]
        self.opening_s = float(self.mark.get("opening_seconds") or DEFAULTS["opening_seconds"])
        self.speed = float(self.mark.get("after_speed") or DEFAULTS["after_speed"]) or 1.0
        self.head, self.blocks = _blocks((self.src / "04_dossier.md").read_text(encoding="utf-8")) \
            if (self.src / "04_dossier.md").exists() else ("", {})
        self.schedule = self._schedule()
        self.F = next(e["t"] for e in self.schedule if e["event"] == T.OPENING_DONE) or 1e-9
        self.zero = float((base.read_json(base.path(r, "mode")) or {}).get("seed_set") or clock())
        self.shift = 0.0
        # state
        self.tl: base.Timeline | None = None
        self.phase = "scout"          # scout | outline | sections
        self.sched: Scheduler | None = None
        self.outline: dict | None = None
        self.led: dict | None = None
        self.landed: list[str] = []   # dossier blocks in landing order
        self.first_audio = False
        self.starts: dict[tuple, float] = {}
        self.titles: dict[int, str] = {}
        self.player_at = None
        self.waiting = None           # (section, eta seconds as of now, check_in) or None
        self.wait: dict | None = None  # the wait under way: {section, start, decision} (decided once, as run.py)
        self.last_key = None
        self.last_state = None
        self.stage, self.label = "scout", "Looking into it"
        self.saved: dict = {}

    # --- the schedule -----------------------------------------------------------------------

    def _schedule(self) -> list[dict]:
        """The source events to replay, in file order, cut to k sections; the end synthesized after the last
        kept lock when sections were cut."""
        k, out = self.k, []
        events = _timeline(self.src)
        for e in events:
            name = e.get("event")
            if name not in REPLAYED:
                continue
            if name in (T.RESEARCH_START, T.RESEARCH_DONE) and e.get("row") not in self.kept:
                continue
            if name in (T.DRAFT_START, T.DRAFT_DONE, T.LOCK_START, T.LOCK_DONE) and int(e.get("section") or 0) > k:
                continue
            if name in END and k < self.n_all:
                continue
            out.append(dict(e))
        if k < self.n_all:
            last = next(e for e in reversed(out) if (e["event"] == T.LOCK_DONE and e.get("section") == k)
                        or (k == 1 and e["event"] == T.OPENING_DONE))
            t = max(last["t"], out[-1]["t"])
            out += [{"t": t, "event": T.ARCHIVE_START}, {"t": t, "event": T.ARCHIVE_DONE, "synthesized": True},
                    {"t": t, "event": T.READY, "synthesized": True}]
        return out

    def scaled(self, t: float) -> float:
        t = float(t)
        return t * self.opening_s / self.F if t <= self.F else self.opening_s + (t - self.F) / self.speed

    def due(self, e: dict) -> float:
        return self.zero + self.shift + self.scaled(e["t"])

    def _find(self, name: str, **match) -> dict | None:
        return next((e for e in self.schedule if e["event"] == name
                     and all(e.get(k) == v for k, v in match.items())), None)

    # --- the run ----------------------------------------------------------------------------

    def run(self) -> dict:
        r = self.r
        before = base.read_timeline(r)
        if any(e.get("event") == T.READY for e in before):
            return {"run": r.name, "rehearsal": self.mark.get("source_run"), "finished": True, "cost_usd": 0.0}
        try:
            self.tl = base.Timeline(r, self.clock)
            i = 0
            if before:
                i = self._resume(before)
            else:
                # Nothing replayed yet, but long after start_seed (stopped before the child wrote anything, or a
                # source that was missing then is back, and this is a Retry): the schedule starts now, not from
                # the original seed_set, or every event would be due at once and the chapter "made" instantly.
                late = self.clock() - self.zero
                if late > LATE_START:
                    self.shift = late
                self.tl.event(T.SEED_SET, cap_usd=0.0, rehearsal=self.mark.get("source_run"))
                r.decide("start", f"A rehearsal: replaying {self.mark.get('source_run')} ({self.k} of {self.n_all} "
                                  f"sections); no research, writing or model calls. Opening at "
                                  f"{self.opening_s:g} s, then {self.speed:g}x.")
            self._status()
            while i < len(self.schedule):
                now = self.clock()
                while i < len(self.schedule) and self.due(self.schedule[i]) <= now:
                    e = self.schedule[i]
                    i += 1
                    if e["event"] == T.READY:
                        return self._ready()
                    self._apply(e, now, write=True)
                    if e["event"] == T.ARCHIVE_START:  # "Finishing" is said before the end, as a real run says it
                        self._status()
                self._player(now)
                self._status()
                if i < len(self.schedule):
                    self.sleep(max(0.01, min(TICK, self.due(self.schedule[i]) - self.clock())))
            return self._ready()
        except BaseException as e:  # Ctrl-C (Pipeline.stop's SIGINT, or SIGTERM through cli.py) included
            self._problem(e)
            raise

    def _resume(self, before: list[dict]) -> int:
        """A stopped rehearsal started again: the events it already replayed are re-applied at once (their
        files and state, not their timeline lines), and the clock moves so the next one comes as long after
        now as it came after the last one."""
        done = [e for e in before if e.get("event") in REPLAYED]
        i = 0
        now = self.clock()
        for e in done:
            if i >= len(self.schedule):
                break
            want = self.schedule[i]
            if e["event"] != want["event"] or any(e.get(k) != want.get(k) for k in ("row", "section")
                                                   if k in want and k in e):
                break
            self._apply(want, now, write=False)
            i += 1
        last = self.scaled(self.schedule[i - 1]["t"]) if i else 0.0
        self.shift = now - self.zero - last
        self.tl.event(T.CONTINUED, rehearsal=self.mark.get("source_run"))
        self.r.decide("continue", f"The rehearsal of {self.mark.get('source_run')} goes on from its files "
                                  f"({i} of {len(self.schedule)} steps were done).")
        return i

    # --- one event --------------------------------------------------------------------------

    def _secs(self, key: tuple, now: float) -> float:
        start = self.starts.pop(key, None)
        return round(now - (start if start is not None else self.zero), 2)

    def _apply(self, e: dict, now: float, write: bool) -> None:
        r, src, name = self.r, self.src, e["event"]
        fields = {k: v for k, v in e.items() if k not in ("t", "ts", "event", "synthesized")}
        ev = (lambda nm, **kw: self.tl.event(nm, **kw)) if write else (lambda nm, **kw: None)

        if name == T.SCOUT_DONE:
            for rel in ("04_research/L00.md", "04_research/L00.sources.json", "01_scout.md", "01_scout.sources.json"):
                _copy(src, r.root, rel)
            self._land(base.SCOUT_ROW)
            ev(name, **{**fields, "seconds": self._secs(("scout",), now)})
            self.phase, self.stage, self.label = "outline", "outline", "Planning the chapter"
            self.starts[("outline",)] = now
        elif name == T.OUTLINE_DONE:
            self.outline = {**self.outline_src, "sections": self.outline_src["sections"][:self.k]}
            base.write_json(base.path(r, "outline_json"), self.outline)
            for rel in ("05_outline.md", "03_angle.md", "05_brief.md", "fast/outline_first.json",
                        "fast/outline_edit.json"):
                _copy(src, r.root, rel)
            fresh = copy.deepcopy(ledger_mod.LATER_FIELDS)
            self.led = {**{k: v for k, v in self.ledger_src.items() if k != "rows"}, "run": r.name,
                        "rows": [{**copy.deepcopy(self.rows_src[i]), **copy.deepcopy(fresh)} for i in self.kept]}
            self._save_ledger()
            ev(name, **{**fields, "seconds": self._secs(("outline",), now), "sections": self.k, "rows": len(self.kept)})
            self.sched = Scheduler(list(range(1, self.k + 1)), {i: self.rows_src[i]["section"] for i in self.kept},
                                   max_hold=self.f.get("max_hold_seconds", 180))
            self.titles = status.titles(r, self.outline)
            self.phase = "sections"
        elif name == T.RESEARCH_START:
            row = e["row"]
            self.sched.row_state[row] = "running"
            self.sched.running[("research", row)] = now
            self.starts[("research", row)] = now
            ev(name, **fields)
        elif name == T.RESEARCH_DONE:
            row = e["row"]
            for rel in (f"04_research/{row}.md", f"04_research/{row}.sources.json"):
                _copy(src, r.root, rel)
            self._land(row)
            self._ledger_from_source(row, ("status", "dossier_refs", "best_tier", "gap", "followup"))
            self.sched.row_state[row] = "done"
            self.sched.running.pop(("research", row), None)
            ev(name, **{**fields, "seconds": self._secs(("research", row), now), "paid": False})
        elif name == T.OPENING_START:
            self.sched.running[("opening",)] = now
            self.starts[("opening",)] = now
            ev(name, **fields)
        elif name == T.OPENING_DONE:
            for rel in ("06_segments/seg01.md", "fast/locks/seg01.json"):
                _copy(src, r.root, rel)
            self._drafts(1)
            self.sched.running.pop(("opening",), None)
            self.sched.lock_mode[1] = "opening"
            ev(name, **{**fields, "seconds": self._secs(("opening",), now)})
            self._section_ready(1, ev)
        elif name == T.DRAFT_START:
            n = int(e["section"])
            self.sched.running[("draft", n)] = now
            self.starts[("draft", n)] = now
            ev(name, **fields)
        elif name == T.DRAFT_DONE:
            n = int(e["section"])
            self._drafts(n)
            self.sched.running.pop(("draft", n), None)
            self.sched.drafted.add(n)
            ev(name, **{**fields, "seconds": self._secs(("draft", n), now)})
        elif name == T.LOCK_START:
            n, mode = int(e["section"]), e.get("mode")
            self.sched.running[("lock", n, mode)] = now
            self.sched.lock_mode[n] = mode
            self.starts[("lock", n)] = now
            ev(name, **fields)
        elif name == T.LOCK_DONE:
            n = int(e["section"])
            for rel in (f"06_segments/seg{n:02d}.md", f"fast/locks/seg{n:02d}.json"):
                _copy(src, r.root, rel)
            self._drafts(n)
            for row in [i for i in self.kept if self.rows_src[i]["section"] == n]:
                self._ledger_from_source(row, ("coverage", "segments", "note"))
            self.sched.running.pop(("lock", n, self.sched.lock_mode.get(n)), None)
            self.titles = status.titles(r, self.outline)
            ev(name, **{**fields, "seconds": self._secs(("lock", n), now)})
            self._section_ready(n, ev)
        elif name == T.ARCHIVE_START:
            self.sched.archived = True
            self.sched.running[("archive",)] = now
            self.starts[("archive",)] = now
            ev(name)
        elif name == T.ARCHIVE_DONE:
            if self.k == self.n_all:
                _copy(src, r.root, "04_dossier.md")
                _copy(src, r.root, "fast/dossier_index.json")
            self.saved = archive.run(r, self.cfg, build_episode=False)
            self.sched.running.pop(("archive",), None)
            self.sched.finished = True
            ev(name, seconds=self._secs(("archive",), now), audio_seconds=self.saved.get("seconds"),
               words=self.saved.get("words"), sections=len(self.saved.get("sections") or []), timing=None,
               episode=False)

    def _drafts(self, n: int) -> None:
        d = self.src / "06_segments" / "drafts"
        for p in sorted(d.glob(f"seg{n:02d}.d*.md")) if d.is_dir() else []:
            _copy(self.src, self.r.root, f"06_segments/drafts/{p.name}")

    def _section_ready(self, n: int, ev) -> None:
        """Section n is locked: its paragraphs (live.json, as a live run "narrates"), its pieces, the script
        so far."""
        r, sched = self.r, self.sched
        text = r.segment_path(n).read_text(encoding="utf-8").strip()
        ev(T.NARRATE_START, section=n)
        pieces = live.LiveNarrator(r, self.cfg).section(n, text)
        for p in pieces:
            ev(T.PIECE_READY, section=n, index=p.index, seconds=p.seconds, after=0.0, last=p.last, simulated=False)
            if not self.first_audio and n == 1 and p.index == 0:
                ev(T.FIRST_AUDIO, section=1)
        ev(T.NARRATE_DONE, section=n, seconds=0.0, pieces=len(pieces),
           audio_seconds=round(sum(p.seconds for p in pieces), 2), resumed=False)
        sched.drafted.add(n)
        sched.locked.add(n)
        sched.narrated.add(n)
        sched.whole.add(n)
        sched.pieces[n] = [p.seconds for p in pieces]
        self.first_audio = True
        try:  # the script so far, for a question asked mid-chapter (free)
            archive.rolling(r, self.cfg)
        except Exception as e:
            r.log({"stage": "rehearsal", "label": f"script so far, after section {n}", "error": str(e)})

    def _land(self, row: str) -> None:
        """04_dossier.md as the source had it once this row landed: its head, then the blocks of the rows
        landed so far in landing order (append-only, newest last)."""
        if row in self.landed or not self.head:
            return
        self.landed.append(row)
        body = [self.blocks[x] for x in self.landed if x in self.blocks]
        base.write_atomic(self.r.path("dossier"), "\n\n".join([self.head, *body]) + "\n")

    def _ledger_from_source(self, row: str, keys: tuple) -> None:
        mine = next((x for x in self.led["rows"] if x["id"] == row), None)
        if mine is None:
            return
        for k in keys:
            mine[k] = copy.deepcopy(self.rows_src[row].get(k, ledger_mod.LATER_FIELDS.get(k)))
        self._save_ledger()

    def _save_ledger(self) -> None:
        led = ledger_mod.normalize(self.led)
        with base.LEDGER_LOCK:
            base.write_atomic(self.r.path("ledger"), json.dumps(led, ensure_ascii=False, indent=2))
            base.write_atomic(self.r.path("ledger_md"), ledger_mod.render_md(led, self.r.name))

    # --- the listener -------------------------------------------------------------------------

    def _player(self, now: float) -> None:
        """fast/player.json, when the page wrote it after this run began: the markers, and a wait."""
        p = base.path(self.r, "player")
        try:
            mtime = p.stat().st_mtime_ns
        except OSError:
            mtime = None
        if mtime != self.player_at:
            self.player_at = mtime
            raw = base.read_json(p)
            if isinstance(raw, dict) and float(raw.get("at") or 0) > self.zero and self.sched is not None:
                self.sched.position = {k: raw.get(k) for k in ("state", "section", "piece", "t", "chapter_t",
                                                               "waiting_for", "since", "reason") if k in raw}
        pos = (self.sched.position if self.sched else None) or {}
        want = pos.get("waiting_for") if pos.get("state") == "waiting" else None
        self.waiting = None
        if not (want and self.sched and int(want) not in self.sched.locked):
            self.wait = None
            return
        n = int(want)
        e = self._find(T.LOCK_DONE, section=n)
        eta = max(0.0, self.due(e) - now) if e else None
        short = self.f.get("short_wait_seconds", 15)
        # As run.py's _holds: the wait is decided when it begins, and a pause that outlasts the short wait
        # becomes a check-in; never the other way, however close the section gets.
        if self.wait is None or self.wait["section"] != n:
            self.wait = {"section": n, "start": now, "decision": checkin.decide(eta, short)}
        elif self.wait["decision"] == "pause" and now - self.wait["start"] > short:
            self.wait["decision"] = "check_in"
        self.waiting = (n, eta, self.wait["decision"])

    # --- status.json ----------------------------------------------------------------------------

    def _where(self) -> tuple[str, str]:
        sched = self.sched
        if self.phase == "scout":
            return "scout", "Looking into it"
        if self.phase == "outline":
            return "outline", "Planning the chapter"
        total = sched.last
        if sched.archived:
            return "archive", "Finishing"
        if not self.first_audio:
            return "opening", "Writing the opening"
        n = next((x for x in sched.sections if x not in sched.locked), None)
        if n is None:
            return "archive", "Finishing"
        snap = sched.snapshot()
        step = status.section_step(n, snap["sections"][n], snap["rows"], snap["running"])
        doing = {"researching": "Researching", "writing": "Writing", "editing": "Editing"}.get(step, "Recording")
        return f"section {n}", f"{doing} part {n} of {total}"

    def _fields(self) -> tuple[dict, tuple]:
        now = self.clock()
        snapshot = self.sched.snapshot() if self.sched else None
        self.stage, self.label = self._where()
        total_sections = len(self.outline["sections"]) if self.outline else self.f["sections"][1]
        done, total = status.progress(snapshot, total_sections)
        step = first_by = step_by = None
        if not self.first_audio:
            step = self.phase if self.phase in ("scout", "outline") else "opening"
            first_by = self.due(self._find(T.OPENING_DONE))
            end = {"scout": T.SCOUT_DONE, "outline": T.OUTLINE_DONE, "opening": T.OPENING_DONE}[step]
            step_by = self.due(self._find(end))
        ready = None
        n = status.next_up(snapshot) if self.first_audio and snapshot and not self.sched.finished else None
        if n is not None and (e := self._find(T.LOCK_DONE, section=n)) is not None and self.due(e) > now:
            ready = (n, self.due(e))
        waiting = self.waiting
        left = None if first_by is None else max(1, math.ceil((first_by - now) / 60))
        fields = {"stage": self.stage, "label": self.label, "stages_done": done, "stages_total": total,
                  "minutes_left": left, "title": self.outline["title"] if self.outline else None,
                  **status.fields(self.r, self.outline, snapshot, self.titles, first_audio=self.first_audio,
                                  waiting_for=waiting and waiting[0], eta_seconds=waiting and waiting[1],
                                  check_in=waiting and waiting[2], step=step,
                                  first_audio_in=None if first_by is None else first_by - now,
                                  step_ends_in=None if step_by is None else step_by - now,
                                  ready_in=ready and (ready[0], ready[1] - now))}
        # A wait's eta_seconds stays in status.settled, as in run.py: status.json is rewritten as it runs down
        # (nothing else counts it down: the page's check-in and get_status read it as it is).
        return fields, (first_by, step_by, ready)

    def _status(self) -> None:
        """status.json when what it says has changed (faststart/run.py's rule: a countdown running down is not
        a change), and fast/state.json whenever the snapshot has (a row landing that changes no status field)."""
        fields, deadlines = self._fields()
        settled = status.settled(fields)
        key = (json.dumps(settled, sort_keys=True, default=str), deadlines)
        if key != self.last_key:
            self.last_key = key
            overnight.write_status(self.r, self.cfg, "making", **fields)
        if self.sched is not None:
            snap = self.sched.snapshot()
            sig = json.dumps(snap, sort_keys=True, default=str)
            if sig != self.last_state:
                self.last_state = sig
                base.write_json(base.path(self.r, "state"), snap)

    # --- the two ways out ---------------------------------------------------------------------

    def _ready(self) -> dict:
        r, sched = self.r, self.sched
        saved = self.saved or base.read_json(base.path(r, "work") / "archive.json") or {}
        words, seconds = saved.get("words", 0), saved.get("seconds", 0.0)
        self.tl.event(T.READY, words=words, audio_seconds=seconds, holds=0, held_seconds=0, solo=[], cost_usd=0.0,
                      rehearsal=True)
        r.decide("done", f"Rehearsal finished: {self.k} sections of {self.mark.get('source_run')}, {words:,} words, "
                         f"{seconds / 60:.1f} minutes of reading, estimated (read live); nothing spent.")
        sched.finished = True
        snapshot = sched.snapshot()
        _, total = status.progress(snapshot, len(sched.sections))
        overnight.write_status(r, self.cfg, "ready", audio=None, minutes_left=0, stages_done=total,
                               stages_total=total, title=self.outline["title"],
                               **status.fields(r, self.outline, snapshot, self.titles, first_audio=True))
        base.write_json(base.path(r, "state"), snapshot)
        print(f"\n  {r.name} is ready (a rehearsal of {self.mark.get('source_run')}): read live, {words:,} words")
        return {"run": r.name, "rehearsal": self.mark.get("source_run"), "words": words, "seconds": seconds,
                "sections": len(sched.sections), "cost_usd": 0.0}

    def _problem(self, e: BaseException) -> None:
        """As faststart/run.py's _problem: status "problem" in plain words, the timeline and the decision."""
        r = self.r
        err = "interrupted" if isinstance(e, KeyboardInterrupt) else str(e)
        detail = f"{type(e).__name__}: {e}"
        try:
            fields, _ = self._fields()
            more = {k: v for k, v in fields.items() if k not in ("stage", "label", "minutes_left")}
        except Exception:
            more = {"mode": status.MODE}
        message, retryable = overnight.plain_problem(self.label, err)
        try:
            if self.tl is not None:
                self.tl.event(T.PROBLEM, stage=self.stage, error=detail)
            r.decide("stopped", f"Stopped at {self.stage}: {detail}")
        finally:
            overnight.write_status(r, self.cfg, "problem", stage=self.stage, label=self.label, message=message,
                                   retryable=retryable, detail=detail, minutes_left=None, **more)


def run(r: Run, cfg: dict, *, clock=time.time, sleep=time.sleep) -> dict:
    """Replay (or continue replaying) the rehearsal marked in r's fast.json to its end. Any failure, Ctrl-C
    included, writes status "problem" and is re-raised; the same call again continues from the files."""
    try:
        replay = _Replay(r, cfg, clock, sleep)
    except Exception as e:  # nothing to replay (the source was moved or broken): the page says so in plain
        # words, never "making" for ever or the server's "stopped before it started"
        message, retryable = overnight.plain_problem("Looking into it", str(e))
        overnight.write_status(r, cfg, "problem", stage="scout", label="Looking into it", message=message,
                               retryable=retryable, detail=f"{type(e).__name__}: {e}", minutes_left=None,
                               mode=status.MODE)
        raise
    return replay.run()
