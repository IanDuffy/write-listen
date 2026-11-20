"""Rehearsal chapters (faststart/rehearsal.py): a finished live chapter replayed in place of making one, for
voice tests (Ian, 2026-11-20). Offline: a fake clock, and every paid path made to raise."""

import json
import math
import signal

import pytest

from faststart import base, rehearsal, status
from faststart.scheduler import Scheduler
from stages import common
from stages import new as new_stage

from rehearsal_fakes import T0, Clock, drop_event, make_run, make_source


@pytest.fixture(autouse=True)
def nothing_paid(monkeypatch):
    from faststart import card, timings
    from stages import llm, memory

    def paid(*a, **k):
        raise AssertionError("a rehearsal made a paid or bookkeeping call")
    monkeypatch.setattr(llm, "call", paid)
    monkeypatch.setattr(card, "start", paid)
    monkeypatch.setattr(card, "write", paid)
    monkeypatch.setattr(timings, "update", paid)
    monkeypatch.setattr(memory, "record_chapter", paid)


@pytest.fixture
def src(tmp_path):
    return make_source(tmp_path / "source-chapter")


@pytest.fixture
def rcfg(cfg, src):
    cfg["rehearsal"] = {"own": False, "source": str(src), "sections": None, "opening_seconds": 3, "after_speed": 50}
    return cfg


@pytest.fixture
def statuses(monkeypatch):
    seen = []
    orig = new_stage.write_status

    def write(r, cfg, state, **fields):
        out = orig(r, cfg, state, **fields)
        seen.append(out)
        return out
    monkeypatch.setattr(new_stage, "write_status", write)
    return seen


def timeline(r):
    return base.read_timeline(r)


def make_tester(lid, **rec):
    d = common.LISTENERS_DIR / lid
    d.mkdir(parents=True, exist_ok=True)
    (d / "tester.json").write_text(json.dumps({"lid": lid, **rec}))
    return lid


# --- choosing ----------------------------------------------------------------------------------

def test_resolver_defaults_to_real(cfg, src):
    cfg["rehearsal"] = {"own": False, "source": str(src)}
    cfg["demo"].pop("chapter", None)
    assert rehearsal.source_for(cfg, None) is None
    assert rehearsal.source_for(cfg, make_tester("t-ann")) is None  # a tester with no choice and no demo.chapter
    cfg.pop("rehearsal")
    assert rehearsal.source_for(cfg, None) is None  # no rehearsal block at all


def test_resolver_tester_and_own(cfg, src):
    cfg["rehearsal"] = {"own": False, "source": str(src)}
    assert rehearsal.source_for(cfg, make_tester("t-ian", chapter="rehearsal")) == src.resolve()
    cfg["demo"]["chapter"] = "rehearsal"
    assert rehearsal.source_for(cfg, make_tester("t-bo", chapter="real")) is None  # tester.json wins
    assert rehearsal.source_for(cfg, make_tester("t-cy")) == src.resolve()          # demo.chapter for the rest
    cfg["rehearsal"]["own"] = True
    assert rehearsal.source_for(cfg, None) == src.resolve()                    # no tester: rehearsal.own
    assert rehearsal.source_for(cfg, "ian") == src.resolve()                   # a listener with no tester.json


def test_complete_needs_opening_locks_from_two_and_ready(cfg, tmp_path, monkeypatch):
    shared = common.shared_dir() / "rehearsal"
    good = make_source(shared / "a-good")  # no lock_done for section 1: its opening_done locks it
    assert rehearsal._missing(good) == []
    cfg["rehearsal"] = {"source": None}
    assert rehearsal.find_source(cfg) == good.resolve()

    no_lock = make_source(tmp_path / "no-lock")
    drop_event(no_lock, "lock_done", section=3)
    assert any("lock_done for section 3" in m for m in rehearsal._missing(no_lock))
    no_ready = make_source(tmp_path / "no-ready")
    drop_event(no_ready, "ready")
    assert rehearsal._missing(no_ready)
    cfg["rehearsal"]["source"] = str(no_ready)
    with pytest.raises(common.StageError, match="can't be replayed"):
        rehearsal.find_source(cfg)

    import os
    newer = make_source(shared / "b-newer")
    drop_event(newer, "opening_done")
    os.utime(newer, (os.path.getmtime(good) + 60,) * 2)
    cfg["rehearsal"]["source"] = None
    assert rehearsal.find_source(cfg) == good.resolve()  # the newer, incomplete one is skipped

    import shutil
    shutil.rmtree(shared)
    with pytest.raises(common.StageError, match="No rehearsal chapter to replay"):
        rehearsal.find_source(cfg)


# --- the replay ------------------------------------------------------------------------------------

def test_replay_in_order_and_ends_like_a_live_run(rcfg, src, tmp_path, statuses):
    r = make_run(tmp_path / "runs", "rehearse", src, rcfg)
    seed = r.path("seed").read_text()
    clock = Clock()
    out = rehearsal.run(r, rcfg, clock=clock, sleep=clock.sleep)
    assert out["cost_usd"] == 0.0 and out["sections"] == 3

    stages = []
    for s in statuses:
        st = s["stage"] if s["state"] == "making" else s["state"]
        if not stages or stages[-1] != st:
            stages.append(st)
    assert stages == ["scout", "outline", "opening", "section 2", "section 3", "archive", "ready"]
    first = next(s for s in statuses if s.get("first_audio"))
    assert first["sections"][0]["text"] == "06_segments/seg01.md"
    assert [p["id"] for p in first["sections"][0]["pieces"]][:2] == ["s1-p01", "s1-p02"]

    final = json.loads(r.path("status").read_text())
    assert final["state"] == "ready" and final["audio"] is None and final["pid"]
    assert final["stages_done"] == final["stages_total"] == 2 + 2 * 3 + 1
    assert all(s["ready"] and s["text"] for s in final["sections"])
    saved = base.read_json(r.root / "fast" / "archive.json")
    assert saved["live"] is True and saved["estimated"] is True

    tl = timeline(r)
    names = [e["event"] for e in tl]
    ready = next(e for e in tl if e["event"] == "ready")
    assert ready["cost_usd"] == 0 and ready["rehearsal"] is True
    assert "listener_note" not in names and not any(n.startswith("playback") for n in names)
    assert abs(next(e for e in tl if e["event"] == "first_audio")["t"] - 3.0) < 0.6
    assert all(not e.get("paid") for e in tl if e["event"] == "research_done")

    for decoy in ("cost.json", "fast/player.json", "live", "12_asks", "listener_notes.md", "10_audio/chapter.mp3"):
        assert not (r.root / decoy).exists(), decoy
    assert not any("cost_usd" in rec for rec in r.read_log())
    assert r.spent() == 0
    assert r.path("seed").read_text() == seed
    assert r.path("dossier").read_text() == (src / "04_dossier.md").read_text()
    assert (r.root / "fast" / "dossier_index.json").read_text() == (src / "fast" / "dossier_index.json").read_text()
    assert "rehearsal" in base.read_json(r.root / "fast.json")  # the marker stays


def test_sections_cut(cfg, src, tmp_path, statuses):
    cfg["rehearsal"] = {"source": str(src), "sections": 2, "opening_seconds": 3, "after_speed": 50}
    r = make_run(tmp_path / "runs", "cut", src, cfg)
    clock = Clock()
    rehearsal.run(r, cfg, clock=clock, sleep=clock.sleep)
    assert len(base.read_json(r.root / "05_outline.json")["sections"]) == 2
    assert [x["id"] for x in base.read_json(r.root / "ledger.json")["rows"]] == ["L01", "L02"]
    tl = timeline(r)
    assert not any(e.get("row") == "L03" for e in tl)
    assert not any(e.get("section") == 3 for e in tl)
    lock2 = next(i for i, e in enumerate(tl) if e["event"] == "lock_done" and e["section"] == 2)
    assert [e["event"] for e in tl[lock2:] if e["event"] in ("archive_start", "archive_done", "ready")] == \
        ["archive_start", "archive_done", "ready"]
    assert next(e for e in tl if e["event"] == "ready")["t"] == tl[lock2]["t"]
    dossier = r.path("dossier").read_text()
    assert "### L00" in dossier and "### L02" in dossier and "### L01" in dossier
    assert "### L03" not in dossier and "## Not found" not in dossier
    assert not (r.root / "fast" / "dossier_index.json").exists()
    final = json.loads(r.path("status").read_text())
    assert final["stages_done"] == final["stages_total"] == 2 + 2 * 2 + 1 and len(final["sections"]) == 2


def test_countdowns_follow_the_schedule(cfg, src, tmp_path, statuses):
    cfg["rehearsal"] = {"source": str(src), "sections": None, "opening_seconds": 90, "after_speed": 1.0}
    r = make_run(tmp_path / "runs", "count", src, cfg)
    clock = Clock()
    rehearsal.run(r, cfg, clock=clock, sleep=clock.sleep)
    scaled = lambda t: t * 90 / 50  # opening_done is at 50 s in the source
    w0 = statuses[0]
    assert w0["stage"] == "scout" and abs(w0["first_audio_in"] - 90) <= 1
    assert abs(w0["step_ends_in"] - scaled(10)) <= 1 and w0["minutes_left"] == 2
    w1 = next(s for s in statuses if s["stage"] == "outline")
    assert abs(w1["step_ends_in"] - (scaled(30) - scaled(10))) <= 1
    after = [s for s in statuses if s.get("first_audio")]
    assert after and all(s["first_audio_in"] is None and s["step_ends_in"] is None for s in after)
    ready_in = [x["ready_in"] for s in after if s["state"] == "making" for x in s["sections"] if x["ready_in"]]
    assert ready_in and max(ready_in) <= (96 - 50) + 1  # section 2's lock, at source t 96, after the opening


def test_dossier_prefix_ledger_and_state(rcfg, src, tmp_path):
    rcfg["rehearsal"]["opening_seconds"] = 90
    r = make_run(tmp_path / "runs", "prefix", src, rcfg)
    landed = lambda: any(e["event"] == "research_done" for e in timeline(r))
    clock = Clock(until=landed)
    with pytest.raises(KeyboardInterrupt):
        rehearsal.run(r, rcfg, clock=clock, sleep=clock.sleep)
    dossier = r.path("dossier").read_text()
    assert "### L00" in dossier and "### L02" in dossier  # L02 lands first in the source
    assert "### L01" not in dossier and "### L03" not in dossier
    assert dossier.index("### L00") < dossier.index("### L02")
    rows = {x["id"]: x for x in base.read_json(r.root / "ledger.json")["rows"]}
    assert rows["L02"]["status"] == "filled" and rows["L02"]["dossier_refs"] == ["D03", "D04"]
    assert rows["L01"]["status"] is None and rows["L01"]["dossier_refs"] == [] and rows["L03"]["coverage"] is None

    state = base.read_json(r.root / "fast" / "state.json")
    # the same point in a real run: the opening locked and "narrated" (its paragraphs), then L02 in
    real = Scheduler([1, 2, 3], {"L01": 2, "L02": 2, "L03": 3}, research_parallel=4)
    real.start(T0)
    assert ("narrate", 1) in real.opening_done(T0 + 1)
    pieces = state["sections"]["1"]["pieces"]
    for i, secs in enumerate(pieces):
        real.piece_ready(1, secs, T0 + 1, last=i == len(pieces) - 1, index=i)
    real.narrate_done(1, T0 + 1)
    real.row_done("L02", T0 + 2)
    want = real.snapshot()
    assert set(state) == set(want)
    assert state["dead"] is None and state["holds"] == [] and state["rows"]["L02"]["state"] == "done"
    assert state["rows"]["L01"] == {"section": 2, "state": "running"}
    assert all(set(x) == {"action", "since"} for x in state["running"])
    assert sorted(map(tuple, (x["action"] for x in state["running"]))) == sorted(real.running)
    outline = base.read_json(r.root / "05_outline.json")
    mine = status.fields(r, outline, state, {}, first_audio=True)
    theirs = status.fields(r, outline, json.loads(json.dumps(want)), {}, first_audio=True)
    assert mine == theirs
    assert {k: v for k, v in state["sections"]["1"].items()} == json.loads(json.dumps(want["sections"][1]))


def _outcome(r):
    """What a replay leaves, for comparing an interrupted-and-resumed one with a clean one."""
    names = [e["event"] for e in timeline(r) if e["event"] not in ("problem", "continued")]
    led = base.read_json(r.root / "ledger.json")
    led.pop("run", None)
    lives = {p.parent.name: json.loads(p.read_text()) for p in sorted((r.root / "10_audio").glob("sec*/live.json"))}
    final = json.loads(r.path("status").read_text())
    return {"events": names, "dossier": r.path("dossier").read_text(), "ledger": led, "live": lives,
            "sections": final["sections"], "state": final["state"]}


def _until(r, point):
    names = lambda: [e["event"] for e in timeline(r)]
    locks = lambda: [e.get("section") for e in timeline(r) if e["event"] == "lock_done"]
    return {
        "before outline": lambda: "scout_done" in names() and "outline_done" not in names(),
        "mid research": lambda: names().count("research_done") == 1,
        "after the opening": lambda: "opening_done" in names() and not locks(),
        "after draft 2": lambda: any(e["event"] == "draft_done" and e.get("section") == 2 for e in timeline(r))
        and not locks(),
        "after lock 2": lambda: locks() == [2],
    }[point]


@pytest.mark.parametrize("point,sections", [
    (p, k) for k in (None, 2) for p in ("before outline", "mid research", "after the opening", "after draft 2",
                                        "after lock 2") if not (k == 2 and p == "after lock 2")])  # k=2 ends there
def test_interrupt_then_resume(cfg, src, tmp_path, point, sections):
    cfg["rehearsal"] = {"source": str(src), "sections": sections, "opening_seconds": 3, "after_speed": 50}
    clean = make_run(tmp_path / "runs", "clean", src, cfg)
    c = Clock()
    rehearsal.run(clean, cfg, clock=c, sleep=c.sleep)
    want = _outcome(clean)

    before = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)
    r = make_run(tmp_path / "runs", "stopped", src, cfg)
    clock = Clock(until=_until(r, point))
    with pytest.raises(KeyboardInterrupt):
        rehearsal.run(r, cfg, clock=clock, sleep=clock.sleep)
    st = json.loads(r.path("status").read_text())
    assert st["state"] == "problem" and st["retryable"] is True
    assert st["message"] == new_stage.plain_problem(st["label"], "interrupted")[0]
    assert st["detail"].startswith("KeyboardInterrupt")
    assert any(e["event"] == "problem" for e in timeline(r))
    assert "## stopped" in r.path("decisions").read_text()

    again = Clock(clock.t + 30)
    out = rehearsal.run(r, cfg, clock=again, sleep=again.sleep)
    names = [e["event"] for e in timeline(r)]
    assert names.count("continued") == 1 and names.count("scout_done") == 1 and names.count("outline_done") == 1
    assert names.count("first_audio") == 1 and names[-1] == "ready" and out["sections"] == (sections or 3)
    assert _outcome(r) == want  # the same events, files and sections as a replay never stopped
    assert (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)) == before
    # started again once finished: nothing new
    n = len(names)
    rehearsal.run(r, cfg, clock=again, sleep=again.sleep)
    assert len(timeline(r)) == n


def test_cli_dispatches_on_the_marker(rcfg, src, tmp_path, monkeypatch):
    import cli
    from faststart import run as fast_run

    runs = tmp_path / "runs"
    r = make_run(runs, "marked", src, rcfg)
    monkeypatch.setattr(common, "RUNS_DIR", runs)
    got = []

    def replay(r_, cfg_):  # inside the replay, SIGTERM (Pipeline.stop's second signal) is a KeyboardInterrupt
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler) and handler is not before
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGTERM, None)
        got.append(r_.name)
        return {}
    monkeypatch.setattr(rehearsal, "run", replay)
    monkeypatch.setattr(fast_run, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("made for real")))
    before = signal.getsignal(signal.SIGTERM)
    assert cli.main(["new", r.name, "--unattended"]) == 0  # Retry's command: no --mode
    assert got == ["marked"]
    assert signal.getsignal(signal.SIGTERM) == before


def test_real_fast_run_refuses_a_marked_folder(rcfg, src, tmp_path):
    from faststart import run as fast_run

    r = make_run(tmp_path / "runs", "never-made", src, rcfg)
    with pytest.raises(common.StageError, match="is a rehearsal"):
        fast_run.check_folder(r)


def test_card_from_the_source_outline(rcfg, src, tmp_path):
    r = make_run(tmp_path / "runs", "carded", src, rcfg)
    got = base.read_json(r.root / "fast" / "card.json")
    assert got["title"] == "What the Heron Does"
    assert got["overview"] == "A heron's stillness is the work of the hunt."
    marker = rehearsal.marked(r.root)
    assert marker["sections"] == 3 and marker["opening_seconds"] == 3.0 and marker["source_run"] == src.name
    assert math.isclose(marker["after_speed"], 50.0)


def test_a_late_start_with_nothing_done_starts_its_clock_now(rcfg, src, tmp_path, statuses):
    """Finding 2: a replay that begins long after start_seed's seed_set (stopped before the child wrote anything,
    then Retry) is not finished in one tick: its schedule starts from now."""
    r = make_run(tmp_path / "runs", "late", src, rcfg)  # seed_set at T0
    clock = Clock(T0 + 3600)
    rehearsal.run(r, rcfg, clock=clock, sleep=clock.sleep)
    first = statuses[0]
    assert first["stage"] == "scout" and abs(first["first_audio_in"] - 3) <= 0.6
    stages = []
    for st in statuses:
        x = st["stage"] if st["state"] == "making" else st["state"]
        if not stages or stages[-1] != x:
            stages.append(x)
    assert stages[:3] == ["scout", "outline", "opening"] and stages[-1] == "ready"
    tl = timeline(r)
    assert abs(next(e for e in tl if e["event"] == "first_audio")["t"] - 3603) < 0.6
    assert clock.t - (T0 + 3600) > 3  # it took its time, as a fresh replay does


def test_a_source_without_a_ledger_is_refused(cfg, src, tmp_path, monkeypatch):
    """Finding 3: ledger.json is part of a complete source; and a source that breaks after it was set gives a
    plain problem status, not a crash with none."""
    cfg["rehearsal"] = {"source": str(src), "sections": None, "opening_seconds": 3, "after_speed": 50}
    r = make_run(tmp_path / "runs", "noledger", src, cfg)
    (src / "ledger.json").unlink()
    assert "ledger.json with rows" in rehearsal._missing(src)
    with pytest.raises(common.StageError, match="ledger.json"):
        rehearsal.find_source(cfg)
    with pytest.raises(common.StageError):
        rehearsal.run(r, cfg, clock=Clock(), sleep=Clock().sleep)
    st = json.loads(r.path("status").read_text())
    assert st["state"] == "problem" and "ledger.json" in st["detail"]

    # anything else that breaks while setting up the replay also leaves a problem status
    (src / "ledger.json").write_text(json.dumps({"rows": []}))
    r.path("status").unlink()
    from stages import ledger as ledger_mod
    monkeypatch.setattr(ledger_mod, "normalize", lambda led: (_ for _ in ()).throw(ValueError("bad ledger")))
    with pytest.raises(ValueError):
        rehearsal.run(r, cfg, clock=Clock(), sleep=Clock().sleep)
    st = json.loads(r.path("status").read_text())
    assert st["state"] == "problem" and st["detail"] == "ValueError: bad ledger" and st["message"]


class PlayerClock(Clock):
    """A fake clock whose sleep has the page write fast/player.json: `plan` maps a seconds-after-T0 to what
    it writes (at that clock time)."""

    def __init__(self, r, plan, **kw):
        super().__init__(**kw)
        self.r, self.plan, self.seen = r, sorted(plan.items()), []

    def sleep(self, s):
        super().sleep(s)
        while self.plan and self.t - T0 >= self.plan[0][0]:
            _, body = self.plan.pop(0)
            p = self.r.root / "fast" / "player.json"
            p.write_text(json.dumps({"at": self.t, **body}))


def _waits(monkeypatch, clock):
    seen = []
    orig = new_stage.write_status

    def write(r, cfg, state, **fields):
        out = orig(r, cfg, state, **fields)
        seen.append((clock.t, out))
        return out
    monkeypatch.setattr(new_stage, "write_status", write)
    return seen


def test_a_wait_counts_down_and_its_decision_holds(cfg, src, tmp_path, monkeypatch):
    """Findings 1 and 10: a listener waiting for part 2 gets waiting_for, an eta_seconds rewritten as it runs
    down (nothing else counts it down), the check-in decided once (never back to a pause), and part 1 playing;
    a player.json from before this run began is ignored."""
    cfg["rehearsal"] = {"source": str(src), "sections": None, "opening_seconds": 3, "after_speed": 1.0}
    r = make_run(tmp_path / "runs", "waits", src, cfg)
    (r.root / "fast").mkdir(exist_ok=True)
    (r.root / "fast" / "player.json").write_text(json.dumps({"at": T0 - 100, "state": "waiting", "section": 1,
                                                             "waiting_for": 2}))
    lock2 = 3 + (96 - 50)  # due(lock_done 2), seconds after T0
    clock = PlayerClock(r, {5: {"state": "waiting", "section": 1, "waiting_for": 2, "piece": 3}})
    seen = _waits(monkeypatch, clock)
    rehearsal.run(r, cfg, clock=clock, sleep=clock.sleep)

    early = [st for t, st in seen if t - T0 < 5 and st["state"] == "making"]
    assert early and all(st["waiting_for"] is None for st in early)  # the old player.json counted for nothing
    waiting = [(t, st) for t, st in seen if st.get("waiting_for") == 2]
    assert waiting, "no wait in status.json"
    for t, st in waiting:
        assert abs(st["eta_seconds"] - (lock2 - (t - T0))) <= 1, (t - T0, st["eta_seconds"])
        assert st["check_in"] == "check_in"  # decided at 44 s away, and kept when it comes within 15 s
        assert st["sections"][0]["state"] == "playing"
    etas = [st["eta_seconds"] for _, st in waiting]
    assert len(set(etas)) >= 35 and etas == sorted(etas, reverse=True) and min(etas) < 15
    assert all(st.get("waiting_for") is None for t, st in seen if t - T0 >= lock2)  # part 2 came: the wait is over


def test_a_short_wait_is_a_pause(cfg, src, tmp_path, monkeypatch):
    cfg["rehearsal"] = {"source": str(src), "sections": None, "opening_seconds": 3, "after_speed": 1.0}
    r = make_run(tmp_path / "runs", "pause", src, cfg)
    lock2 = 3 + (96 - 50)
    clock = PlayerClock(r, {lock2 - 8: {"state": "waiting", "section": 1, "waiting_for": 2}})
    seen = _waits(monkeypatch, clock)
    rehearsal.run(r, cfg, clock=clock, sleep=clock.sleep)
    waiting = [st for _, st in seen if st.get("waiting_for") == 2]
    assert waiting and all(st["check_in"] == "pause" for st in waiting)


def test_peek_reads_a_replay(rcfg, src, tmp_path):
    """Finding 12: the guide's peek (faststart/peek.py) works on the files a replay writes, at each step."""
    from faststart import peek

    rcfg["rehearsal"].update(opening_seconds=90, after_speed=1.0)
    points = {
        "outline": lambda r: "scout_done" in [e["event"] for e in timeline(r)]
        and "outline_done" not in [e["event"] for e in timeline(r)],
        "opening": lambda r: "research_start" in [e["event"] for e in timeline(r)]
        and "opening_done" not in [e["event"] for e in timeline(r)],
        "part 2": lambda r: "first_audio" in [e["event"] for e in timeline(r)]
        and "lock_done" not in [e["event"] for e in timeline(r)],
    }
    got = {}
    for name, when in points.items():
        r = make_run(tmp_path / "runs", f"peek-{name.replace(' ', '')}", src, rcfg)
        clock = Clock(until=lambda r=r, when=when: when(r))
        with pytest.raises(KeyboardInterrupt):
            rehearsal.run(r, rcfg, clock=clock, sleep=clock.sleep)
        st = json.loads(r.path("status").read_text())
        st["state"] = "making"  # as it was the moment before the stop
        got[name] = peek.peek(r.root, st)
    assert got["outline"].get("about") == "A heron's stillness is the work of the hunt."
    assert "working_on" not in got["outline"]
    assert got["opening"]["working_on"].startswith('part 1, "The Still Bird"')
    assert got["opening"]["looking_into"]  # the rows the replay has under way, from its timeline
    assert got["part 2"]["working_on"].startswith('part 2, "Water That Lies"')
    assert "about" not in got["part 2"]


def test_a_fixed_runs_folder_is_the_childs_folder(rcfg, src, tmp_path, monkeypatch):
    """Finding 5: a Pipeline with an explicit runs_dir (the page checks, tests) passes it to its child, and the
    child (cli.py new) opens that folder, so it replays the run start_seed marked, never a same-named run in the
    listener's chapters (where an unmarked one would be made for real, and paid for)."""
    import cli
    import server
    from faststart import run as fast_run
    from stages import seed

    runs = tmp_path / "runs"
    r = make_run(runs, "pagecheck-run", src, rcfg)
    launched = {}

    class Popen:
        def __init__(self, cmd, **kw):
            launched.update(cmd=cmd, env=kw["env"])
            self.pid = 4242

        def poll(self):
            return None
    monkeypatch.setattr(server.subprocess, "Popen", Popen)
    monkeypatch.setenv(common.RUNS_ENV, "/somewhere/stale")
    server.Pipeline(runs_dir=runs).start(["new", r.name, "--unattended", "--mode", "fast"], r.name)
    assert launched["env"][common.RUNS_ENV] == str(runs.resolve())

    monkeypatch.setattr(common, "RUNS_DIR", runs)  # a Pipeline with no fixed folder never passes a stale one
    server.Pipeline().start(["new", r.name, "--unattended", "--mode", "fast"], r.name)
    assert common.RUNS_ENV not in launched["env"]

    # the child, with only the environment to go by
    monkeypatch.setattr(common, "RUNS_DIR", None)
    monkeypatch.setattr(seed, "RUNS_DIR", None)
    decoy = common.chapters_dir() / r.name  # same name in the listener's chapters, unmarked
    decoy.mkdir(parents=True)
    (decoy / "00_source").mkdir()
    (decoy / "00_source" / "seed.md").write_text("# Seed\n\n## Topic\n\nherons\n")
    (decoy / "fast.json").write_text(json.dumps({"mode": "fast", "seed_set": T0, "voice": "live"}))
    monkeypatch.setenv(common.RUNS_ENV, str(runs.resolve()))
    got = []
    monkeypatch.setattr(rehearsal, "run", lambda r_, cfg_: got.append(r_.root.resolve()) or {})
    monkeypatch.setattr(fast_run, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("made for real")))
    assert cli.main(["new", r.name, "--unattended", "--mode", "fast"]) == 0
    assert got == [r.root.resolve()]
