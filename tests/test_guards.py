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


def test_the_anthropic_key_comes_from_the_pipelines_own_variables_first(monkeypatch):
    """Ian's cloud environment (2026-11-20) holds the key as ARTICLE_PIPELINE_ANTHROPIC_API_KEY, so the session never
    sees it; it wins over the older WRITE_LISTEN_... and a host app's ANTHROPIC_API_KEY."""
    import anthropic
    from stages import llm as L

    seen = {}
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: seen.update(kw) or object())
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    for var in L.KEY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "host")
    monkeypatch.setenv("WRITE_LISTEN_ANTHROPIC_API_KEY", "older")
    monkeypatch.setenv("ARTICLE_PIPELINE_ANTHROPIC_API_KEY", "cloud")
    monkeypatch.setattr(L, "_client", None)
    L.get_client()
    assert seen["api_key"] == "cloud" and seen["base_url"] == "https://api.anthropic.com"
    monkeypatch.delenv("ARTICLE_PIPELINE_ANTHROPIC_API_KEY")
    monkeypatch.setattr(L, "_client", None)
    L.get_client()
    assert seen["api_key"] == "older"
    monkeypatch.setattr(L, "_client", None)
