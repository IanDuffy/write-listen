"""Seeded tester folders for the Tester Demo's tests (part B): a tester at each stage, with the files the
other parts write (study/, the live chapter's reading log and asides, cost logs) in their contract shapes."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

import server
from demo import testers

LONG = " ".join(f"w{i}" for i in range(150))   # 150 words: one minute at 2.5 words a second
MID = " ".join(f"m{i}" for i in range(225))    # 225 words: a minute and a half


def demo_cfg(cfg: dict, enabled: bool = True) -> dict:
    cfg["demo"] = {**cfg.get("demo", {}), "enabled": enabled, "cookie": "wl_tester", "secure_cookie": "auto",
                   "chapter_minutes": 10, "base_url": None}
    cfg["live"]["provider"] = "gpt_live"
    return cfg


def client(cfg: dict, tmp_path: Path, host: str = "127.0.0.1") -> TestClient:
    runs = tmp_path / "runs"
    runs.mkdir(exist_ok=True)
    app = server.create_app(server.Pipeline(runs_dir=runs), cfg, remember_on_start=False)
    return TestClient(app, client=(host, 50000), follow_redirects=False)


def jsonl(p: Path, recs: list[dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")


def write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")


def age(d: Path, seconds: float) -> None:
    """Every file under d as if last written `seconds` ago."""
    t = time.time() - seconds
    for p in d.rglob("*"):
        if p.is_file():
            os.utime(p, (t, t))


def seed(cfg: dict, label: str, stage: str = "done", *, name: str | None = None, quiet_for: float = 0,
         summary: str | None = None, debrief_lines: list[tuple[str, str]] | None = None) -> str:
    """A tester who got as far as `stage` (not opened, intro, onboarding, chapter, debrief, done). Returns the lid."""
    lid = testers.create(cfg, label)["lid"]
    d = testers.folder(lid)
    if stage == "not opened":
        return lid
    opened = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - quiet_for))
    testers.update(lid, opened_at=opened, consent_at=opened)
    jsonl(d / "study" / "intro-s1.jsonl", [
        {"t": "2026-11-16T10:00:01", "who": "interviewer", "text": "Thanks for making the time. What's your name?",
         "live_id": "live_intro"},
        {"t": "2026-11-16T10:00:09", "who": "listener", "text": f"I'm {name or label}."},
        {"t": "2026-11-16T10:00:50", "who": "interviewer", "text": "I'll hand you over now."}])
    jsonl(d / "study" / "log.jsonl", [{"stage": "interview", "provider": "openai", "live_id": "live_intro",
                                       "seconds": 50, "cost_usd": 0.05}])
    if name:
        testers.set_name(lid, name)
    if stage == "intro":
        age(d, quiet_for)
        return lid
    testers.update(lid, intro_done=True)
    jsonl(d / "sessions" / "s-guide.jsonl", [{"t": "2026-11-16T10:01:00", "who": "listener", "text": "Bridges, please."}])
    jsonl(d / "sessions" / "log.jsonl", [
        {"stage": "guide", "provider": "anthropic", "cost_usd": 0.02},
        {"stage": "voice", "provider": "openai", "session": "s-guide", "live_id": "live_1", "seconds": 360,
         "cost_usd": 0.30}])
    if stage == "onboarding":
        age(d, quiet_for)
        return lid
    ch = d / "chapters" / "bridges-20261116"
    write(ch / "status.json", {"run": ch.name, "state": "making", "title": "How Bridges Stand",
                               "started": "2026-11-16T10:04:00", "mode": "fast"})
    write(ch / "fast.json", {"mode": "fast", "voice": "live"})
    write(ch / "05_outline.json", {"title": "How Bridges Stand", "sections": [
        {"n": 1, "title": "The arch", "lands": "a", "target_words": 600},
        {"n": 2, "title": "The cable", "lands": "b", "target_words": 600}]})
    write(ch / "06_segments" / "seg01.md", LONG + " [D01]\n\n" + MID + " [D02]\n")
    jsonl(ch / "live" / "reading.jsonl", [
        {"t": "2026-11-16T10:05:00", "id": "s1-p01", "sentence": 0, "cut": True, "heard": " ".join(LONG.split()[:50]),
         "live_id": "live_1", "times": {"start_ms": 1000, "end_ms": 20000}},
        {"t": "2026-11-16T10:06:00", "id": "s1-p01", "sentence": 0, "cut": False, "heard": LONG,
         "live_id": "live_1", "times": {"start_ms": 30000, "end_ms": 90000}},
        {"t": "2026-11-16T10:08:00", "id": "s1-p02", "sentence": 0, "cut": False, "heard": MID,
         "live_id": "live_2", "times": {"start_ms": 1000, "end_ms": 120000}}])
    jsonl(ch / "12_asks" / "asides.jsonl", [
        {"id": "a1", "kind": "ask", "voice": "live", "paragraph": "s1-p02", "sentence": 1, "live_id": "live_2",
         "transcript": [{"who": "listener", "text": "Why don't arches fall?"}, {"who": "guide", "text": "Each stone pushes."}]},
        {"id": "a2", "kind": "ask", "voice": "live", "paragraph": "s1-p02", "sentence": 2,
         "transcript": [{"who": "guide", "text": "Yes?"}]},  # a tap with nothing said: not a question
        {"id": "c1", "kind": "check_in", "voice": "live", "paragraph": "s1-p02",
         "transcript": [{"who": "listener", "text": "Still there?"}]}])
    jsonl(ch / "log.jsonl", [
        {"stage": "fast_outline", "provider": "anthropic", "cost_usd": 1.00},
        {"stage": "scout", "provider": "anthropic", "cost_usd": 0.50, "early_scout": "bridges-x"}])  # counted in _scouts
    jsonl(d / "chapters" / "_scouts" / "bridges-x" / "log.jsonl", [{"stage": "scout", "provider": "anthropic",
                                                                     "cost_usd": 0.50}])
    if stage == "chapter":
        age(d, quiet_for)
        return lid
    jsonl(d / "study" / "debrief-s2.jsonl", [
        {"t": "2026-11-16T10:20:00", "who": "interviewer", "text": "What's one thing that stood out to you?"},
        *[{"t": "2026-11-16T10:20:10", "who": who, "text": text} for who, text in (debrief_lines or [
            ("listener", "It felt like someone was talking just to me."),
            ("interviewer", "At three minutes you asked why arches don't fall. How did that go?"),
            ("listener", "The answer was good but it took a second to come back.")])]])
    if stage == "debrief":
        age(d, quiet_for)
        return lid
    write(d / "study" / "debrief.json", {"done": True, "finished_at": "2026-11-16T10:28:00",
                                         "topics": ["stood_out", "asking"], "closing": True})
    write(d / "study" / "debrief_summary.md", summary or "# Debrief\n1. \"Like someone talking just to me\" (0:10)\n")
    jsonl(d / "study" / "log.jsonl", [
        {"stage": "interview", "provider": "openai", "live_id": "live_intro", "seconds": 50, "cost_usd": 0.05},
        {"stage": "debrief_summary", "provider": "anthropic", "cost_usd": 0.10}])
    age(d, quiet_for)
    return lid
