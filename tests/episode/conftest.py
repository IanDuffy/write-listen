"""Shared fixtures for the episode add-on: a temp copy of tests/fixtures/run_small, timed with the
recorded recognizer output (no model, no API)."""

import shutil
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "run_small"


@pytest.fixture
def small_run(tmp_path):
    run = tmp_path / "run_small"
    shutil.copytree(FIXTURE, run)
    return run


@pytest.fixture
def timed(small_run, cfg):
    from episode import recognize, timing

    rec = recognize.RecordedRecognizer(small_run / "recognizer_recorded.json")
    t = timing.build(small_run, rec, cfg["narration"]["pronunciations"])
    timing.write(small_run, t)
    return small_run, t
