import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from stages import llm, tts  # noqa: E402
from stages.common import Run, load_config  # noqa: E402

from fakes import FakeAnthropic, Responder, fake_synthesize  # noqa: E402


@pytest.fixture
def cfg():
    c = load_config()
    c["narration"]["voice_id"] = "voice_fake"
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
