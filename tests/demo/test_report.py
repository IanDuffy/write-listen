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
    # Ian's notes (2026-11-18): what each tester did, from their own records, beside what they said.
    assert p.count("What they did: They talked with the voice for about 3 minutes before it started making an audio "
                   "piece for them. The piece is titled \"How Bridges Stand\". They heard about 2 of its 8 "
                   "minutes. About 2 minutes in, they stopped it to ask: \"Why don't arches fall?\"") == 2
    assert p.count("What they did: (no record)") == 1  # Pilot 2 never got past the intro
    assert "What they did\" lines" in p and "who pressed End demo and when" in p
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


def test_the_report_keeps_the_account_the_debrief_was_given(dcfg, writer):
    """What was true when they ended (the debrief transcript's first record), not what the folder shows later:
    a piece that became ready after they pressed End demo was still never ready for them."""
    lid = seed(dcfg, "Purnima", "done")
    from demo import testers
    study = testers.folder(lid) / "study"
    (study / "debrief-s3.jsonl").write_text(json.dumps({
        "t": "2026-11-16T10:20:00", "who": "session", "mode": "debrief",
        "did": "- They heard none of the piece: it wasn't ready yet when they ended.\n- They pressed End demo while the voice was quiet."}) + "\n")
    report.build(dcfg, yes=True)
    assert ("What they did: They heard none of the piece: it wasn't ready yet when they ended. "
            "They pressed End demo while the voice was quiet.") in writer.prompt()


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


def test_a_tester_who_left_before_the_piece_was_ready_is_not_said_to_have_left_it_unstarted(dcfg, tmp_path):
    """The chapter goes on being made after they leave, so read as of now every drop-off during the wait said
    "it was ready, but they didn't start it" (Ian's own second run did). The account is read as of when they
    ended: their debrief's start, else their last own write."""
    import os
    from datetime import datetime
    when = lambda clock: datetime.fromisoformat(f"2026-11-16T{clock}").timestamp()  # noqa: E731
    want_not_ready = "They heard none of the piece: it wasn't ready yet when they ended."

    def waiting(label, first_audio="10:07:00"):
        lid = seed(dcfg, label, "onboarding")
        d = testers.folder(lid)
        testers.update(lid, opened_at="2026-11-16T10:00:00", consent_at="2026-11-16T10:00:00")
        ch = d / "chapters" / "tides-20261116"
        ch.mkdir(parents=True)
        (ch / "fast.json").write_text(json.dumps({"mode": "fast", "voice": "live"}))
        (ch / "status.json").write_text(json.dumps({"state": "ready", "first_audio": True, "started": "2026-11-16T10:04:00"}))
        (ch / "fast_timeline.jsonl").write_text(json.dumps({"ts": f"2026-11-16T{first_audio}", "event": "first_audio"}) + "\n")
        for p in d.rglob("*"):
            if p.is_file():
                os.utime(p, (when("10:05:00"), when("10:05:00")))  # their last own write: two minutes before the opening
        return lid, d

    # (a) A debrief with no account recorded (Ian's run predates it): its start is when they ended.
    lid, d = waiting("Pilot 1")
    (d / "study" / "debrief-s2.jsonl").write_text(json.dumps({"t": "2026-11-16T10:06:00", "who": "session", "mode": "debrief"}) + "\n")
    assert report._ended(d, lid) == when("10:06:00")
    assert report.did(dcfg, lid)[-1] == want_not_ready
    # (b) No debrief: the window closed while it was made.
    lid, d = waiting("Pilot 2")
    assert report._ended(d, lid) == when("10:05:00")
    assert report.did(dcfg, lid)[-1] == want_not_ready
    # (c) Still there when the opening landed (the guide's ready line is in their transcript) and never started it.
    lid, d = waiting("Pilot 3")
    t = when("10:08:00")
    os.utime(d / "sessions" / "s-guide.jsonl", (t, t))
    assert report.did(dcfg, lid)[-1] == "They heard none of the piece: it was ready, but they didn't start it."
