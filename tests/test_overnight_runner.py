"""The overnight runner end to end, offline (docs/overnight-chapters.md; acceptance checks 5, 8 and 9 in
code): plan, full ledger research, outline, paired write, promise, recording on a fake GPT-Live,
assembly with real ffmpeg, intro brief, finish. Kills mid-research and mid-recording, the runaway stop,
the scheduler's gates."""

import builtins
import json
from datetime import datetime

import pytest
from fakes import Responder, all_text
import overnight_plan_fakes as P
from overnight_plan_fakes import PlanFake, offline  # noqa: F401
from overnight_record_fakes import GAP_MS, FakeLive, word_ms
from overnight_write_fakes import Chain

from episode import recognize, references
from overnight import commands, profiles, record, runner
from stages import common, llm
from stages.common import StageError, dossier_entries, entry_tags

FILES = ["00_source/up_next.json", "00_source/seed.md", "01_map.md", "03_angle.md", "ledger.json",
         "04_dossier.md", "04_research/ledger_update.md", "05_outline.json", "05_brief.md", "06_segments/seg01.md",
         "07_script_draft.md", "08_promise.json", "09_script_final.md", "10_audio/recorded.json",
         "10_audio/chapter.mp3", "11_episode/timing.json", "11_episode/episode.json", "intro_brief.md",
         "finished.json", "status.json", "cost.json", "decisions.md", "log.jsonl"]


class Killed(BaseException):
    """A kill: not an error anything retries."""


class Composite:
    """The planner and intro's fake first, then the writing chain's, then the seed pipeline's stock
    Responder (scout, ledger, research rows, judge)."""

    def __init__(self, plan, chain, base):
        self.parts, self.calls, self.hooks = (plan, chain, base), [], []

    def __call__(self, kwargs):
        self.calls.append(all_text(kwargs))
        for h in self.hooks:
            h(kwargs)
        plan, chain, base = self.parts
        t = all_text(kwargs)
        if any(p in t for p in (P.CHOOSE, P.SCOUT, P.CARD, P.OPEN_QS, P.INTRO, P.MEMORY)):
            return plan(kwargs)
        try:
            k = chain.kind(kwargs)
        except (TypeError, KeyError, IndexError):  # a tool loop's continuation: not the writing chain's
            k = ("unknown", None)
        return chain(kwargs) if k[0] != "unknown" else base(kwargs)


def outline_from_dossier(root) -> dict:
    """A valid four-section outline over whatever the fake research filed: rows L01.. in sections 2-4."""
    entries = dossier_entries((root / "04_dossier.md").read_text())
    rows = json.loads((root / "ledger.json").read_text())["rows"]
    active = [x["id"] for x in rows if x.get("status") != "dropped"]
    by_row = {rid: [n for n, line in entries.items() if rid in entry_tags(line)["rows"]] for rid in active}
    first = sorted(entries)[:2]
    plan = [([], first)] + [([rid], by_row[rid] or first) for rid in active]
    while len(plan) < 4:
        plan.append(([], first))
    secs = []
    for n, (rs, es) in enumerate(plan, 1):
        secs.append({"n": n, "title": f"Part {n}", "lands": f"Idea {n}.", "sets_up": f"Plant {n}.",
                     "hand_off": f"Question {n}.", "target_words": 750, "notes": "Tell it plainly.",
                     "rows": rs, "entries": es})
    return {"angle": "An angle.", "why": "Why.", "through_line": "A line through it.", "closing_idea": "An end.",
            "listener_words": [], "sections": secs}


class FakeRecognizer:
    name = "fake recognizer"
    reheard = False

    def __init__(self, root):
        self.root = root

    def __call__(self, path):
        from stages.common import Run
        r = Run(name=self.root.name, root=self.root)
        text = {p["id"]: p["text"] for p in record.paragraphs(r)}[path.name.split(".")[0]]
        t, out = record.PAD_LEAD, []
        for w in text.split():
            out.append({"w": w, "start": round(t, 3), "end": round(t + word_ms(w) / 1000, 3)})
            t += (word_ms(w) + GAP_MS) / 1000
        return out


@pytest.fixture
def world(tmp_path, cfg, monkeypatch, own_listeners):
    cfg.pop("listener_dir", None)
    listener = profiles.create("ian")
    profiles.set_active("ian")
    (listener / "profile.md").write_text("- Watches herons at the lake every morning.\n")
    plan, chain, base = PlanFake(), Chain(4), Responder()
    comp = Composite(plan, chain, base)
    from fakes import FakeAnthropic
    monkeypatch.setattr(llm, "_client", FakeAnthropic(comp))
    chain.on("outline", None, lambda kw: json.dumps(outline_from_dossier(chapter_root(listener))))
    chain.on("outline_edit", None, lambda kw: json.dumps(
        {"changes": [], "outline": outline_from_dossier(chapter_root(listener))}))
    live = FakeLive()
    monkeypatch.setattr(record, "LiveSession", live)
    monkeypatch.setattr(recognize, "WhisperRecognizer", lambda *a, **k: FakeRecognizer(chapter_root(listener)))
    monkeypatch.setattr(references, "ask", lambda r, cfg, seg, prompt: {"sentences": []})
    monkeypatch.setattr(runner, "_keep_awake", lambda: None)
    monkeypatch.setattr(record, "preflight", lambda cfg: None)  # not this machine's model cache
    monkeypatch.setattr(builtins, "input", lambda prompt: pytest.fail(f"an unattended build asked: {prompt}"))
    return type("W", (), {"listener": listener, "cfg": cfg, "comp": comp, "plan": plan, "chain": chain,
                          "base": base, "live": live})


def chapter_root(listener):
    card = profiles.read_json(listener / "up_next.json", {}) or {}
    if card.get("chapter"):
        return listener / "chapters" / card["chapter"]
    found = sorted((listener / "chapters").iterdir())
    return found[-1]


def test_run_now_plans_builds_and_finishes_a_chapter(world):
    """Acceptance check 5, offline: one command, a complete chapter folder."""
    out = runner.run(world.cfg, "ian", now=True)
    assert out["ran"] and out["state"] == "ready"
    card = profiles.read_json(world.listener / "up_next.json")
    assert card["status"] == "ready" and card["attempts"] == 1
    root = world.listener / "chapters" / card["chapter"]
    assert card["chapter"].startswith(card["for_morning"])
    missing = [f for f in FILES if not (root / f).exists()]
    assert missing == []
    assert not (root / "fast.json").exists()  # never read as a fast run
    st = json.loads((root / "status.json").read_text())
    assert st["state"] == "ready" and st["stages_done"] == st["stages_total"] == len(runner.STAGES)
    assert [h["stage"] for h in st["history"]] == [s[0] for s in runner.STAGES]
    cost = json.loads((root / "cost.json").read_text())
    assert set(cost["by_stage"]) >= {"research", "write", "promise", "record", "intro"}
    assert cost["by_provider"]["openai"] > 0 and cost["runaway_stop_usd"] == 50
    assert abs(cost["total_usd"] - st["cost_usd"]) < 0.01
    rec = json.loads((root / "10_audio" / "recorded.json").read_text())
    assert rec["passed"] == rec["paragraphs"] and rec["flagged"] == []
    assert (root / "00_source" / "up_next.json").exists() and "# Angle: " in (root / "03_angle.md").read_text()
    brief = (root / "intro_brief.md").read_text()
    assert "## Findings you may mention" in brief and card["title"] in brief
    assert "(" + card["chapter"] + "," in (world.listener / "history.md").read_text()
    assert any(json.loads(x).get("kind") == "topic" for x in (world.listener / "library.jsonl").read_text().splitlines())
    angles = [c for c in world.comp.calls if "candidate angles" in c]
    assert angles == []  # the card's angle stands in for angles and choose

    n = len(world.comp.calls)  # a second build of the same card pays for nothing
    assert runner.build(world.cfg, world.listener, card)["state"] == "ready"
    assert len(world.comp.calls) == n


def test_a_build_killed_mid_research_resumes_without_redoing_finished_work(world):
    """Acceptance check 8 (research half)."""
    killed = {"done": False}

    def kill(kwargs):
        t = all_text(kwargs)
        if not killed["done"] and ("Your idea is row L02" in t or "This idea is row L02" in t):
            killed["done"] = True
            raise Killed()
    world.comp.hooks.append(kill)
    with pytest.raises(Killed):
        runner.run(world.cfg, "ian", now=True)
    card = profiles.read_json(world.listener / "up_next.json")
    assert card["status"] == "failed" and card["error"].startswith("research")
    root = world.listener / "chapters" / card["chapter"]
    st = json.loads((root / "status.json").read_text())
    assert st["state"] == "problem" and st["stage"] == "research" and st["message"] == "Last night's chapter didn't finish."
    before = list(world.comp.calls)
    scouts = sum("scout the territory" in c for c in before)
    out = runner.resume(world.cfg, "ian")
    assert out["state"] == "ready"
    after = world.comp.calls[len(before):]
    assert not any("scout the territory" in c for c in after) and scouts == 1  # the scout was not paid twice
    assert not any("Before any research, we decide" in c for c in after)  # nor the ledger
    assert profiles.read_json(world.listener / "up_next.json")["attempts"] == 2


def test_a_build_killed_mid_recording_resumes_only_the_missing_paragraphs(world, monkeypatch):
    """Acceptance check 8 (recording half)."""
    reads = {"n": 0}

    def kill(session, text):
        reads["n"] += 1
        if reads["n"] == 5:
            raise Killed()
    world.live.before_read = kill
    with pytest.raises(Killed):
        runner.run(world.cfg, "ian", now=True)
    card = profiles.read_json(world.listener / "up_next.json")
    root = world.listener / "chapters" / card["chapter"]
    assert card["status"] == "failed" and card["error"].startswith("record")
    done_before = {p.name: p.read_bytes() for p in (root / "10_audio" / "paragraphs").glob("*.wav")}
    assert 1 <= len(done_before) < len(record.paragraphs(common.Run(name=root.name, root=root)))
    calls_before = len(world.comp.calls)
    texts_before = len(world.live.texts())
    world.live.before_read = None
    assert runner.resume(world.cfg, "ian")["state"] == "ready"
    assert len(world.comp.calls) == calls_before + 1  # only the intro brief: nothing before recording redone
    redone = world.live.texts()[texts_before:]
    paras = {p["text"]: p["id"] for p in record.paragraphs(common.Run(name=root.name, root=root))}
    assert not any(paras.get(t) + ".wav" in done_before for t in redone if t in paras)
    assert all((root / "10_audio" / "paragraphs" / k).read_bytes() == v for k, v in done_before.items())


def test_the_runaway_stop_fails_the_build_and_says_so(world):
    profiles.save_settings(world.cfg, {"overnight": {"runaway_stop_usd": 0.05}}, listener=world.listener)
    with pytest.raises(StageError) as e:
        runner.run(world.cfg, "ian", now=True)
    assert "wl overnight resume" in str(e.value)
    card = profiles.read_json(world.listener / "up_next.json")
    assert card["status"] == "failed"
    st = json.loads((world.listener / "chapters" / card["chapter"] / "status.json").read_text())
    assert st["state"] == "problem" and "cap" in st["error"].lower() or "max" in st["error"].lower()


def test_scheduled_runs_build_only_when_due(world):
    """Acceptance check 9: overnight off, or a non-build day, and nothing runs."""
    from overnight import planner
    card = planner.plan(world.cfg, world.listener, now=datetime(2026, 11, 16, 18, 5))  # Monday evening
    assert card["for_morning"] == "2026-11-17"
    calls = len(world.comp.calls)
    assert runner.run(world.cfg, "ian", clock=datetime(2026, 11, 16, 23, 0))["ran"] is False  # not yet
    profiles.save_settings(world.cfg, {"overnight": {"enabled": False}}, listener=world.listener)
    assert runner.run(world.cfg, "ian", clock=datetime(2026, 11, 17, 2, 0))["ran"] is False  # off
    assert commands.tick(world.cfg, "ian", clock=datetime(2026, 11, 17, 2, 0))["did"] is None
    profiles.save_settings(world.cfg, {"overnight": {"enabled": True, "days": ["mon", "wed"]}}, listener=world.listener)
    assert runner.run(world.cfg, "ian", clock=datetime(2026, 11, 17, 2, 0))["ran"] is False  # Tuesday: not a build day
    assert len(world.comp.calls) == calls and not any((world.listener / "chapters").iterdir())
    profiles.save_settings(world.cfg, {"overnight": {"days": ["mon", "tue", "wed", "thu", "fri"]}}, listener=world.listener)
    out = commands.tick(world.cfg, "ian", clock=datetime(2026, 11, 17, 2, 0))
    assert out["did"] == "build" and out["state"] == "ready"


def test_the_overnight_cli(world, monkeypatch, capsys):
    import cli
    from overnight import commands as cmds
    monkeypatch.setattr(cmds, "load_config", lambda: world.cfg)
    assert cli.main(["overnight", "plan", "--mode", "topic", "--topic", "How herons see underwater"]) == 0
    card = profiles.read_json(world.listener / "up_next.json")
    assert card["request"]["topic"] == "How herons see underwater"
    assert cli.main(["overnight", "resume"]) == 1  # nothing stopped to resume
    assert cli.main(["overnight", "run", "--now"]) == 0
    assert profiles.read_json(world.listener / "up_next.json")["status"] == "ready"
    assert cli.main(["overnight", "status"]) == 0
    assert '"status": "ready"' in capsys.readouterr().out
    assert "<string>tick</string>" in commands.plist_text() and "<integer>45</integer>" in commands.plist_text()


# --- review fixes: the card through retries, dead builds, planning back-off ----------

def _kill_at_research(world, times=1):
    left = {"n": times}

    def kill(kwargs):
        t = all_text(kwargs)
        if left["n"] and ("Your idea is row L02" in t or "This idea is row L02" in t):
            left["n"] -= 1
            raise Killed()
    world.comp.hooks.append(kill)


def _dead_pid() -> int:
    import subprocess
    import sys
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_the_card_lock_holds_across_processes_and_nests_in_a_thread(tmp_path):
    """C2/C11/C28: the tick process and the server's replan thread share listeners/<id>/.card.lock."""
    import subprocess
    import sys
    import time as _time
    from overnight import locks
    child = subprocess.Popen([sys.executable, "-c", (
        "import fcntl, sys, time\n"
        f"f = open({str(tmp_path / locks.LOCK_NAME)!r}, 'a'); fcntl.flock(f, fcntl.LOCK_EX)\n"
        "print('held', flush=True); time.sleep(0.6)\n")], stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == "held"
    t0 = _time.monotonic()
    with locks.card_lock(tmp_path):
        waited = _time.monotonic() - t0
        with locks.card_lock(tmp_path):  # the planner's write reconciles inside its own lock
            pass
    child.wait()
    assert waited > 0.3


def test_a_resumed_build_puts_the_card_back_to_building(world):
    """C1: during a retry or resume the card says building, so Change is queued, not replanned, and the
    finished build marks its own card ready."""
    from overnight import schedule
    _kill_at_research(world)
    with pytest.raises(Killed):
        runner.run(world.cfg, "ian", now=True)
    assert profiles.read_json(world.listener / "up_next.json")["status"] == "failed"
    seen = []

    def look(kwargs):
        c = profiles.read_json(world.listener / "up_next.json")
        s = profiles.settings(world.cfg, listener=world.listener)
        seen.append((c["status"], c.get("error"), schedule.can_change(c, s, datetime.now())[0]))
    world.comp.hooks.append(look)
    assert runner.resume(world.cfg, "ian")["state"] == "ready"
    assert seen and all(x == ("building", None, False) for x in seen)
    card = profiles.read_json(world.listener / "up_next.json")
    assert card["status"] == "ready" and card["attempts"] == 2


def test_a_build_refuses_a_card_a_new_plan_replaced(world):
    """C2/C28: the build re-reads the card under the card lock before it starts."""
    from overnight import planner
    old = planner.plan(world.cfg, world.listener, now=datetime(2026, 11, 16, 18, 5))
    profiles.write_json(world.listener / "up_next.json", {**old, "id": "plan-newer"})
    with pytest.raises(StageError, match="replaced the card"):
        runner.build(world.cfg, world.listener, old)
    assert not any((world.listener / "chapters").iterdir())  # nothing made, nothing paid
    assert profiles.read_json(world.listener / "up_next.json")["status"] == "planned"


def test_a_dead_build_is_marked_failed_and_retried_like_one(world):
    """C4/C24: kill -9 leaves the card building and status.json making with a dead pid."""
    import os
    _kill_at_research(world)
    with pytest.raises(Killed):
        runner.run(world.cfg, "ian", now=True)
    card = profiles.read_json(world.listener / "up_next.json")
    root = world.listener / "chapters" / card["chapter"]
    st = json.loads((root / "status.json").read_text())
    profiles.write_json(root / "status.json", {**st, "state": "making", "pid": _dead_pid()})
    profiles.write_json(world.listener / "up_next.json", {**card, "status": "building", "error": None})
    (world.listener / runner.LOCK_FILE).write_text(f"{_dead_pid()} 2026-11-17T02:00:00\n")
    out = commands.tick(world.cfg, "ian", clock=datetime(2026, 11, 15, 12, 0))  # a Sunday: nothing due
    assert out["did"] is None
    card = profiles.read_json(world.listener / "up_next.json")
    assert card["status"] == "failed" and card["error"] == runner.STOPPED
    st = json.loads((root / "status.json").read_text())
    assert st["state"] == "problem" and st["error"] == runner.STOPPED and st["pid"] is None
    assert "build process was gone" in (root / "decisions.md").read_text()
    # A live build is left alone: its status pid is this process.
    profiles.write_json(world.listener / "up_next.json", {**card, "status": "building"})
    profiles.write_json(root / "status.json", {**st, "state": "making", "pid": os.getpid()})
    assert runner.reconcile(world.listener)["status"] == "building"
    profiles.write_json(root / "status.json", {**st, "state": "making", "pid": _dead_pid()})
    assert runner.resume(world.cfg, "ian")["state"] == "ready"  # resume reconciles, then continues


def test_manual_runs_leave_tonights_scheduled_build(world):
    """C5/C27: two failed `run --now` tests of tonight's card; the 02:00 tick still builds it."""
    from datetime import timedelta
    _kill_at_research(world, times=2)
    for _ in range(2):
        with pytest.raises(Killed):
            runner.run(world.cfg, "ian", now=True)
    card = profiles.read_json(world.listener / "up_next.json")
    assert card["status"] == "failed" and card["attempts"] == 2 and card["scheduled_attempts"] == 0
    morning = datetime.fromisoformat(card["for_morning"])
    while morning.weekday() > 4:  # build days here are Monday to Friday
        morning += timedelta(days=1)
    two = morning.replace(hour=2)
    out = commands.tick(world.cfg, "ian", clock=two)
    assert out["did"] == "build" and out["state"] == "ready"
    card = profiles.read_json(world.listener / "up_next.json")
    assert card["scheduled_attempts"] == 1 and card["attempts"] == 3
    assert card["attempt_at"] == two.isoformat(timespec="seconds")


def test_the_tick_backs_off_a_planning_pass_that_keeps_failing(world, monkeypatch):
    """C3/C23: one failed pass an hour, three in a planning window, then the next window."""
    from overnight import planner
    tried = []

    def fail(cfg, listener, request, now, info):
        tried.append(now)
        raise StageError("plan choose: no topic came back.")
    monkeypatch.setattr(planner, "_plan", fail)

    def tick(h, m=0, day=17):
        return commands.tick(world.cfg, "ian", clock=datetime(2026, 11, day, h, m))
    with pytest.raises(StageError):
        tick(18)
    assert tick(18, 15)["did"] is None and len(tried) == 1
    for h in (19, 20):
        with pytest.raises(StageError):
            tick(h)
    out = tick(23)
    assert out["did"] is None and "next planning window" in out["reason"] and len(tried) == 3
    assert tick(1, 45, day=18)["did"] is None and len(tried) == 3
    with pytest.raises(StageError):
        tick(2, 15, day=18)  # Wednesday's build start opened a new window
    assert len(tried) == 4 and len(planner.failures(world.listener)) == 4


def test_a_note_added_while_a_manual_build_starts_reaches_the_seed(world, monkeypatch):
    """R14: run() reads the card, then preflight takes seconds; a note typed then went on the card on disk,
    but lock() wrote seed.md from the stale copy, so research and writing never saw it."""
    from overnight import live, planner
    planner.plan(world.cfg, world.listener, now=datetime(2026, 11, 16, 18, 5))

    def preflight(cfg):
        out = live.add_note(world.cfg, "Mention the grey heron colony", now=datetime(2026, 11, 16, 18, 10))
        assert out["where"] == "card"
    monkeypatch.setattr(record, "preflight", preflight)
    _kill_at_research(world)
    with pytest.raises(Killed):
        runner.run(world.cfg, "ian", now=True)
    root = chapter_root(world.listener)
    assert "- Mention the grey heron colony" in (root / "00_source" / "seed.md").read_text()
    locked = json.loads((root / "00_source" / "up_next.json").read_text())
    assert [n["text"] for n in locked["notes_added"]] == ["Mention the grey heron colony"]
