"""`wl study report`, `wl study testers` and `wl study create` (demo/report.py, demo/cli.py; acceptance check 13:
a report from three seeded test runs). Claude is a fake; nothing reaches the network."""

import json

import pytest

import cli
from demo import report, testers
from stages import llm

from fakes import FakeAnthropic, Responder
from tester_fakes import demo_cfg, seed

REPORT = "# Tester Demo: study report\n\nThree testers opened the link.\n"


class Writer:
    """Answers the report call with REPORT and keeps what it was sent."""

    def __init__(self, text=REPORT):
        self.text, self.calls = text, []

    def __call__(self, kwargs):
        self.calls.append(kwargs)
        return Responder.msg(kwargs, self.text)

    def prompt(self) -> str:
        return self.calls[0]["messages"][0]["content"]


@pytest.fixture
def dcfg(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(report, "STUDY_DIR", tmp_path / "study")
    monkeypatch.setattr(llm, "_client", None)
    return demo_cfg(cfg)


@pytest.fixture
def writer(monkeypatch):
    w = Writer()
    monkeypatch.setattr(llm, "_client", FakeAnthropic(w))
    return w


def three(cfg):
    """Three seeded runs: one through the debrief, one gone mid-chapter, one gone after the intro; and one
    link never opened."""
    seed(cfg, "Purnima", "done", name="Purnima",
         summary="## What stood out\n\"It felt like a radio show made for me\" [0:40]\n")
    seed(cfg, "Pilot 1", "chapter", name="Sam", quiet_for=3 * 3600)
    seed(cfg, "Pilot 2", "intro", name="Lee", quiet_for=3 * 3600)
    seed(cfg, "Pilot 3", "not opened")


def test_the_report_from_three_seeded_runs(dcfg, writer, tmp_path):
    three(dcfg)
    out = report.build(dcfg, yes=True)
    assert out == tmp_path / "study" / "report.md" and out.read_text() == REPORT
    assert len(writer.calls) == 1 and writer.calls[0]["model"] == dcfg["models"]["writer"]
    p = writer.prompt()
    # the testers who opened the link, with what code knows of each
    for want in ("## Tester: Purnima", "## Tester: Pilot 1", "## Tester: Pilot 2", "Name: Sam",
                 "Stage reached: chapter (left early)", "Stage reached: intro (left early)",
                 "heard 2.5 of 10 minutes", "radio show made for me",
                 "listener: It felt like someone was talking just to me.",
                 "Why don't arches fall?", "0:00 The arch; 2:30 The cable"):
        assert want in p, want
    assert "## Tester: Pilot 3" not in p  # never opened: only counted
    for want in ("- not opened: 1 (Pilot 3)", "- intro: 1 (Pilot 2 (left early))",
                 "- chapter: 1 (Pilot 1 (left early))", "- done: 1 (Purnima)"):
        assert want in p, want
    assert "{{" not in p
    # Ian's notes (2026-11-16): by topic, not by five fixed questions
    for want in ("## By topic", "`### What stood out`", "`### Would they use it, and when`", "`### The voice`",
                 "`### Asking questions`", "## Moments in the chapter", "## Bugs and confusions", "## Worth a follow-up call"):
        assert want in p, want
    assert "five core questions" not in p and "## The five questions" not in p
    log = [json.loads(x) for x in (tmp_path / "study" / "log.jsonl").read_text().splitlines()]
    assert log[0]["stage"] == "study_report" and log[0]["provider"] == "anthropic" and log[0]["cost_usd"] > 0


def test_the_report_uses_the_interviewers_moments_when_part_c_is_there(dcfg, writer, monkeypatch):
    import sys
    import types
    fake = types.ModuleType("demo.debrief")
    fake.moments = lambda d: [{"at_s": 192, "clock": "3:12", "words": "Why don't arches fall?"}]
    monkeypatch.setitem(sys.modules, "demo.debrief", fake)
    import demo
    monkeypatch.setattr(demo, "debrief", fake, raising=False)  # the real part C module, once imported, is the package's attribute
    seed(dcfg, "Purnima", "done")
    report.build(dcfg, yes=True)
    assert "at 3:12: \"Why don't arches fall?\"" in writer.prompt()


def test_no_testers_or_none_opened_is_nothing_to_report(dcfg, writer):
    from stages.common import StageError
    with pytest.raises(StageError, match="No testers"):
        report.build(dcfg, yes=True)
    seed(dcfg, "Pilot 3", "not opened")
    with pytest.raises(StageError, match="opened"):
        report.build(dcfg, yes=True)
    assert writer.calls == []


def test_wl_study_report_through_cli(dcfg, writer, tmp_path, monkeypatch, capsys):
    import demo.cli
    monkeypatch.setattr(demo.cli, "load_config", lambda: dcfg)
    three(dcfg)
    assert cli.main(["study", "report", "--yes"]) == 0
    assert "report.md" in capsys.readouterr().out and (tmp_path / "study" / "report.md").exists()


def test_wl_study_create_and_testers(dcfg, monkeypatch, capsys):
    import demo.cli
    monkeypatch.setattr(demo.cli, "load_config", lambda: dcfg)
    monkeypatch.setenv("WL_BASE_URL", "https://listen.example.com")
    assert cli.main(["study", "create", "Pilot", "1"]) == 0
    out = capsys.readouterr().out
    token = testers.record("t-pilot-1")["token"]
    assert f"https://listen.example.com/t/{token}" in out and "no name: the interviewer asks" in out
    assert cli.main(["study", "create", "Pilot", "2", "--name", "Rolando"]) == 0
    assert "greeted as Rolando" in capsys.readouterr().out and testers.record("t-pilot-2")["name"] == "Rolando"
    seed(dcfg, "Purnima", "done", name="Purnima")
    assert cli.main(["study", "testers"]) == 0
    out = capsys.readouterr().out
    assert "Purnima" in out and "not opened" in out and "\"How Bridges Stand\", 2.5 of 10 min" in out
    assert "Total $2.07" in out


def test_wl_study_testers_with_none(dcfg, monkeypatch, capsys):
    import demo.cli
    monkeypatch.setattr(demo.cli, "load_config", lambda: dcfg)
    assert cli.main(["study", "testers"]) == 0
    assert "No testers yet" in capsys.readouterr().out
