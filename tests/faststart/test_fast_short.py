"""A short fast chapter (Tester Demo: 10 minutes, faststart.base.sized_overrides): two sections, so the lane is
the opening, section 2's draft and its final lock, with no paired lock. The scheduler against made-up durations,
and the run end to end with every fake, as test_fast_scheduler.py and test_fast_run.py do for three to five."""

import json

import yaml

import fast_outline_fakes as A
from fast_run_fakes import fast_threads, no_leaks, offline, world  # noqa: F401
from fast_scheduler_sim import Scenario, run
from faststart import base, checks, sections
from faststart import run as fast_run
from faststart import timings as T
from faststart.player import SimPlayer


def two_sections() -> dict:
    """A valid 10-minute outline of two sections: A.outline(3) cut down, the opening 800 words, section 2 750."""
    o = A.outline(3)
    o["sections"] = o["sections"][:2]
    o["sections"][0]["target_words"], o["sections"][1]["target_words"] = 800, 750
    return o


def test_two_sections_in_the_scheduler():
    r = run(Scenario(n=2))
    assert [a for a in r.actions() if a[0] in ("opening", "draft", "lock")] == [("opening",), ("draft", 2), ("lock", 2, "final")]
    assert r.modes == {1: "opening", 2: "final"}
    assert [a for a in r.actions() if a[0] == "narrate"] == [("narrate", 1), ("narrate", 2)]
    assert r.archived == r.narrated[2] and r.count(("archive",)) == 1
    assert r.player.state == "done" and r.holds() == []


def test_a_ten_minute_run_of_two_sections_end_to_end(tmp_path, cfg, monkeypatch):
    # The server writes the run's config.yaml (part A's hook); run_config merges it in, as cli.py does.
    root = tmp_path / "runs" / "fast-run"
    root.mkdir(parents=True)
    (root / "config.yaml").write_text(yaml.safe_dump({"fast": base.sized_overrides(10)}))
    w = world(tmp_path, cfg, monkeypatch, sections=3)
    assert w.cfg["fast"]["sections"] == [2, 3] and w.cfg["fast"]["research"]["max_searches"] == 15
    w.chain.script.first = two_sections()
    out = fast_run.run(w.r, w.cfg, player=SimPlayer(), episode=False)
    assert out["sections"] == 2 and json.loads(w.r.path("status").read_text())["state"] == "ready"
    assert [e["mode"] for e in base.read_timeline(w.r) if e["event"] == T.LOCK_DONE] == ["final"]
    locked = [(k, sections.locked_text(w.r, k)) for k in (1, 2)]
    assert all(text for _, text in locked)
    assert checks.chapter_problems(locked, w.r.read("dossier")) == []
    assert len(json.loads(base.path(w.r, "outline_json").read_text())["sections"]) == 2
