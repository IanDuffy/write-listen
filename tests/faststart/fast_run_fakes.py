"""Fakes for the fast run's driver tests: every area's fake replies chained into one responder,
a fake voice, and a run folder in a temp dir with everything personal pointed away from the
real files.

    Chain(sections=4, rows=..., note=...)   answers the scout, the outline and its edit (area A), the
                                            opening, drafts, locks and fixes (area C), the row agents
                                            and the note judge (area B) and the comparison; records
                                            every request with what it was
    chain.on(what, key, fn)                 fn(kwargs) runs before that reply (a hook: add a note, wait,
                                            raise); what it returns, if anything, is the reply
    world(tmp_path, cfg, monkeypatch)       a seed run ready for faststart.run.run, the fakes installed
    install(monkeypatch)                    the fakes alone, for a second run on a folder that exists
    Balance(used, limit)                    stands in for narrate.balance (the ElevenLabs account); install()
                                            puts one in with room to spare and returns it as voice.balance
    fast_clock(speed)                       a clock that runs `speed` times faster than the wall's
    Patient()                               a simulated listener who presses play once the whole opening
                                            is narrated (so the first wait is for section 2, on any machine)
    slow_section_3(w)                       section 3's research held back until section 2 is locked:
                                            the listener runs out of audio, and section 2 goes solo
    wait_for(test), happened(r, event)      for hooks that hold a reply until something has happened
    offline, no_leaks                       fixtures: no live client for the whole session, and no
                                            worker thread left behind by a test
"""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace as NS

import pytest
from fakes import FakeAnthropic, Responder, all_text

import fast_outline_fakes as A
import fast_research_fakes as B
import fast_sections_fakes as C
from fast_narrate_fakes import FakeVoice

from faststart import narrate
from faststart.player import SimPlayer
from stages import common, llm, tts

COMPARE_PHRASE = "Two scripts for the same narrated audio chapter"


def compare_verdict(**changes) -> dict:
    """The comparison's JSON as the model returns it: a verdict per criterion, an overall one, and
    the differences that matter, each quoting a script by its blind label."""
    out = {"criteria": [
        {"name": "arc", "verdict": "A", "reason": "A builds to its ending; B circles.",
         "quotes": [{"script": "A", "text": "So the opening rests here", "why": "it hands off"}]},
        {"name": "repetition", "verdict": "tie", "reason": "Neither repeats itself.", "quotes": []},
        {"name": "accuracy", "verdict": "B", "reason": "A states one thing its dossier does not hold.",
         "quotes": [{"script": "A", "text": "a wall going into the water", "why": "no entry says so"}]}],
        "overall": {"verdict": "A", "reason": "The arc matters most to a listener."},
        "differences": [{"script": "B", "quote": "Segment 1 opens", "why_it_matters": "It opens on a summary."}]}
    out.update(changes)
    return out


class Chain:
    """One responder for FakeAnthropic that answers every prompt of a fast run. Replies are keyed
    by what the prompt is, never by order, so threads may call it in any order."""

    def __init__(self, sections: int = 4, rows: dict | None = None, note: str | None = None):
        self.script = A.Script(sections=sections)
        self.rows, self.note = dict(rows or {}), note
        self.compare: dict | None = None
        self.calls: list[tuple[tuple, dict]] = []   # ((what, key), the request)
        self.hooks: dict[tuple, object] = {}

    def on(self, what: str, key, fn) -> "Chain":
        self.hooks[(what, key)] = fn
        return self

    def of(self, what: str, key=None) -> list[dict]:
        return [kw for (w, k), kw in self.calls if w == what and (key is None or k == key)]

    def whats(self) -> list[tuple]:
        return [k for k, _ in self.calls]

    @staticmethod
    def kind(kwargs) -> tuple:
        """What a request is: ("scout", None), ("outline", None), ("outline_edit", None), ("opening", 1),
        ("opening_edit", 1), ("draft", n), ("lock", n), ("fix", n), ("row", "L01"), ("row_writeup",
        "L01"), ("note", None), ("compare", None)."""
        t = all_text(kwargs)
        for phrase, what in ((A.WRITEUP_PHRASE, "scout_writeup"), (A.SCOUT_PHRASE, "scout"), (A.MATCH_PHRASE, "match"),
                             (A.REPAIR_PHRASE, "outline_repair"), (A.EDIT_PHRASE, "outline_edit"),
                             (A.OUTLINE_PHRASE, "outline")):
            if phrase in t:
                return what, None
        if (k := C.kind(kwargs)) is not None:
            return k
        if B.NOTE_PHRASE in t:
            return "note", None
        if m := B.ROW.search(t):
            return ("row_writeup" if "your search and fetch budget is now spent" in t else "row"), m.group(1)
        if COMPARE_PHRASE in t:
            return "compare", None
        return "unknown", None

    def __call__(self, kwargs):
        k = self.kind(kwargs)
        self.calls.append((k, kwargs))
        hook = self.hooks.get(k) or self.hooks.get((k[0], None))
        out = hook(kwargs) if hook else None
        if out is None:
            out = A.reply(kwargs, self.script)
        if out is None:
            out = C.reply(kwargs)
        if out is None:
            out = B.reply(kwargs, self.rows, self.note)
        if out is None and k[0] == "compare":
            out = json.dumps(self.compare or compare_verdict())
        if out is None:
            raise AssertionError("fake API got an unexpected prompt: " + all_text(kwargs)[:200])
        return Responder.msg(kwargs, out) if isinstance(out, str) else out


def fast_clock(speed: float):
    """A clock for run(clock=...): the wall's, `speed` times faster, so a simulated listener gets
    through seconds of audio in a fraction of one."""
    t0 = time.time()
    return lambda: t0 + (time.time() - t0) * speed


class Patient(SimPlayer):
    """A listener who starts once every piece of the opening is there. On a fast clock a plain
    SimPlayer can catch the narrator up inside section 1 when the machine is busy; this one's first
    wait is always the one a test set up."""

    def step(self, now: float, sections: dict, last: int) -> dict:
        if not self._started and not (sections.get(1) or {}).get("complete"):
            sections = {}
        return super().step(now, sections, last)


class Balance:
    """Stands in for narrate.balance: (characters used, the plan's limit), counting how often it was
    read. `error`: an exception to raise instead, as a client that cannot be built would."""

    def __init__(self, used: int = 0, limit: int = 1_000_000, error: Exception | None = None):
        self.used, self.limit, self.error, self.reads = used, limit, error, 0

    def __call__(self) -> tuple[int, int] | None:
        self.reads += 1
        if self.error is not None:
            raise self.error
        return None if self.limit is None else (self.used, self.limit)


def cards(r, cfg, seg, prompt) -> dict:
    """The episode page's references call, replaced: one source card on each section's first cited sentence."""
    first = next((s for s in seg.sentences if s.dossier), None)
    return {"sentences": [] if first is None else [
        {"id": first.id, "sources": [{"dossier": first.dossier[0], "title": f"Source for {seg.id}", "creator": "",
                                      "note": ""}], "works": [], "people": [], "quotes": []}]}


def install(monkeypatch, sections: int = 4, **chain) -> tuple[Chain, FakeVoice]:
    """Every fake a fast run needs: the model, the voice, the episode page's references call."""
    from episode import recognize, references

    fake, voice = Chain(sections, **chain), FakeVoice()
    voice.balance = Balance()
    monkeypatch.setattr(llm, "_client", FakeAnthropic(fake))
    monkeypatch.setattr(tts, "synthesize", voice)
    monkeypatch.setattr(narrate, "balance", voice.balance)  # the account is never read over the network
    monkeypatch.setattr(references, "ask", cards)
    monkeypatch.setattr(recognize, "WhisperRecognizer", lambda *a, **k: pytest.fail("Whisper was loaded"))
    return fake, voice


def settings(cfg: dict, tmp_path) -> dict:
    """Point everything personal or shared at the temp folder: the listener's memory, the timings file."""
    cfg["listener_dir"] = str(tmp_path / "listener")
    cfg["fast"]["timings_file"] = str(tmp_path / "timings.json")
    cfg["fast"]["tick_seconds"] = 0.02
    cfg["fast"]["exit_wait_seconds"] = 20  # a test whose fake never answers fails in seconds, not minutes
    return cfg


def world(tmp_path, cfg, monkeypatch, sections: int = 4, name: str = "fast-run", **chain) -> NS:
    """A seed run in a temp folder, ready for faststart.run.run, with every fake installed."""
    r = A.seed_run(tmp_path, cfg, name=name)
    cfg = settings(common.run_config(r, cfg), tmp_path)  # run_config: as cli.py does for a seed run
    fake, voice = install(monkeypatch, sections, **chain)
    return NS(r=r, cfg=cfg, chain=fake, voice=voice, tmp=tmp_path)


def wait_for(test, seconds: float = 8.0) -> bool:
    """Hold a fake reply until `test()` is true (or give up, so a broken run fails instead of hanging)."""
    deadline = time.monotonic() + seconds
    while not test():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.005)
    return True


def happened(r, event: str, **match) -> bool:
    from faststart import base

    return any(e["event"] == event and all(e.get(k) == v for k, v in match.items()) for e in base.read_timeline(r))


def slow_section_3(w: NS, max_hold: float = 3, speed: float = 20):
    """Section 3's research (row L03 of a four-section outline) does not come back until the lock
    of section 2 has been asked for. Section 2 can then only be locked on its own: its paired lock
    needs the draft of 3, which needs L03. With a short max_hold and a fast clock the listener
    finishes the opening, waits max_hold, and the solo fallback fires. Returns the clock to run with."""
    gate = threading.Event()
    w.cfg["fast"]["max_hold_seconds"] = max_hold
    w.chain.rows["L03"] = lambda kw: (gate.wait(8), B.row_message(kw, "L03"))[1]
    w.chain.on("lock", 2, lambda kw: gate.set())
    return fast_clock(speed)


def fast_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith("fast-") and t.is_alive()]


@pytest.fixture(autouse=True, scope="session")
def offline():
    """No test may build a live client: a real .env with keys sits beside the code. Session-wide,
    so a thread that outlived its test (after the fake client was taken away again) fails here
    instead of paying."""
    import anthropic
    import elevenlabs.client

    def live(*a, **k):
        raise AssertionError("a test reached a live API client")

    mp = pytest.MonkeyPatch()
    mp.setattr(anthropic, "Anthropic", live)
    mp.setattr(elevenlabs.client, "ElevenLabs", live)
    yield
    mp.undo()


@pytest.fixture(autouse=True)
def no_leaks():
    """Every worker thread of a fast run has ended when its test does."""
    yield
    deadline = time.monotonic() + 5
    for t in fast_threads():
        t.join(max(0.0, deadline - time.monotonic()))
    assert fast_threads() == [], "a fast run left worker threads behind"
