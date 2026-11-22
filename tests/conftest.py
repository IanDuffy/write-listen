import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from stages import common, llm, tts  # noqa: E402
from stages.common import Run, load_config  # noqa: E402

from fakes import FakeAnthropic, Responder, fake_synthesize  # noqa: E402


@pytest.fixture(autouse=True)
def own_listeners(tmp_path, monkeypatch):
    """No test reads or writes the real listeners/ or shared/ (the resolver's defaults)."""
    monkeypatch.setattr(common, "LISTENERS_DIR", tmp_path / "_listeners")
    monkeypatch.setattr(common, "SHARED_DIR", tmp_path / "_shared")
    monkeypatch.delenv(common.LISTENER_ENV, raising=False)
    from overnight import planner  # a finished chapter starts a planning process: never from a test
    monkeypatch.setattr(planner, "spawn_if_needed", lambda cfg, root: None)
    return tmp_path / "_listeners"


@pytest.fixture
def cfg():
    c = load_config()
    c["narration"]["voice_id"] = "voice_fake"
    # The ElevenLabs page and agents are what these tests pin; tests of the live voice
    # (tests/live/) set live.provider to gpt_live themselves.
    c["live"]["provider"] = "elevenlabs"
    # The live guide's earpiece calls Claude in the background (live.director, on in config.yaml); tests about it
    # turn it on themselves, so no page test without a fake client can reach the real API through it.
    c["live"]["director"] = {**(c["live"].get("director") or {}), "enabled": False}
    # Likewise the debrief's earpiece (demo.interviewer.director, on in config.yaml): every page test that starts a
    # debrief would otherwise post looks that reach Claude. Tests about it turn it on themselves.
    iv = c.setdefault("demo", {}).setdefault("interviewer", {})
    iv["director"] = {**(iv.get("director") or {}), "enabled": False}
    return c


@pytest.fixture
def run(tmp_path):
    return Run.open("test-run", create=True, runs_dir=tmp_path)


@pytest.fixture
def responder(monkeypatch):
    r = Responder()
    monkeypatch.setattr(llm, "_client", FakeAnthropic(r))
    return r


@pytest.fixture
def fake_tts(monkeypatch):
    calls = []

    def synth(text, **kw):
        calls.append({"text": text, **kw})
        return fake_synthesize(text, **kw)

    monkeypatch.setattr(tts, "synthesize", synth)
    monkeypatch.setattr(tts, "model_char_limit", lambda model_id: None)
    return calls
