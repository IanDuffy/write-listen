import builtins

import pytest

from stages import llm, map as map_stage
from stages.common import StageError


def test_missing_input(run, cfg, responder):
    with pytest.raises(StageError, match="Missing input: 00_source/transcript.txt"):
        map_stage.run(run, cfg)


def test_never_overwrites_without_force(run, cfg, responder):
    run.path("source_dir").mkdir()
    run.path("transcript").write_text("Some transcript.")
    run.path("map").write_text("Ian's edits")
    with pytest.raises(StageError, match="--force"):
        map_stage.run(run, cfg)
    assert run.path("map").read_text() == "Ian's edits"
    run.force = True
    map_stage.run(run, cfg)
    assert "A map." in run.path("map").read_text()


def test_cost_guard_asks(run, cfg, monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda prompt: "n")
    with pytest.raises(StageError, match="cost guard"):
        llm.guard(run, cfg, "big stage", 6.0)
    monkeypatch.setattr(builtins, "input", lambda prompt: "y")
    llm.guard(run, cfg, "big stage", 6.0)
    llm.guard(run, cfg, "small stage", 1.0)  # under the limit: no question
    run.yes = True
    monkeypatch.setattr(builtins, "input", lambda prompt: pytest.fail("should not ask"))
    llm.guard(run, cfg, "big stage", 6.0)
