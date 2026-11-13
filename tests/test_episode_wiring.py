"""Narration keeps ElevenLabs' timings, and an unattended run builds the timed transcript from them, so
a new chapter can be read along and asked about (docs/ask-voice.md; episode/INTEGRATION.md steps 1, 3)."""

import base64
import builtins
import json
from types import SimpleNamespace as NS

import pytest
from fakes import FakeAnthropic, Responder

import cli
from episode import bundle
from stages import common, llm, new, seed, tts
from stages.common import Run

ALIGN = {"characters": list("Hi."), "character_start_times_seconds": [0.0, 0.1, 0.2],
         "character_end_times_seconds": [0.1, 0.2, 0.3]}


class FakeTTS:
    """client.text_to_speech.with_raw_response, recording which call was made."""
    def __init__(self):
        self.calls = []
        outer = self

        class Raw:
            def convert_with_timestamps(self, voice_id, **kw):
                outer.calls.append(("timestamps", kw))
                data = NS(audio_base_64=base64.b64encode(b"\x01\x00" * 10).decode(),
                          alignment=NS(model_dump=lambda: dict(ALIGN)))
                return NS(data=data, headers={"request-id": "req_ts"})

            def convert(self, voice_id, **kw):
                outer.calls.append(("stream", kw))

                class Ctx:
                    def __enter__(self_):
                        return NS(data=iter([b"\x01\x00" * 10]), headers={"request-id": "req_stream"})

                    def __exit__(self_, *a):
                        return False
                return Ctx()
        self.text_to_speech = NS(with_raw_response=Raw())


@pytest.mark.parametrize("timestamps", [True, False])
def test_synthesize_keeps_timings_with_the_same_body(cfg, monkeypatch, timestamps):
    fake = FakeTTS()
    monkeypatch.setattr(tts, "elevenlabs_client", lambda: fake)
    ncfg = {**cfg["narration"], "timestamps": timestamps}
    syn = tts.synthesize("Hi.", voice_id="v", ncfg=ncfg, previous_request_ids=["a", "b"], next_text="Next.")
    kind, kw = fake.calls[0]
    assert kind == ("timestamps" if timestamps else "stream")
    assert kw["previous_request_ids"] == ["a", "b"] and kw["next_text"] == "Next."  # stitching kept
    assert syn.audio == b"\x01\x00" * 10
    assert syn.request_id == ("req_ts" if timestamps else "req_stream")
    assert syn.alignment == (ALIGN if timestamps else None)


def test_bundle_reads_along_without_references(tmp_path):
    run = tmp_path / "r"
    (run / "11_episode").mkdir(parents=True)
    (run / "11_episode" / "timing.json").write_text(json.dumps(
        {"duration": 1.0, "audio": "10_audio/chapter.mp3", "segments": []}))
    assert bundle.make(run)["references"] == []


@pytest.fixture
def env(tmp_path, cfg, monkeypatch, fake_tts):
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(common, "RUNS_DIR", runs)
    monkeypatch.setattr(seed, "RUNS_DIR", runs)
    cfg["listener_dir"] = str(tmp_path / "listener")
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(builtins, "input", lambda prompt: pytest.fail(f"an unattended run asked: {prompt}"))
    monkeypatch.setattr(llm, "_client", FakeAnthropic(Responder()))
    return runs, cfg


def strip_timings(r):
    """Make the chunks look like narration before timings were kept."""
    for c in (r.path("audio_dir") / "chunks").glob("*.json"):
        m = json.loads(c.read_text())
        m.pop("alignment", None)
        c.write_text(json.dumps(m))


def finished_run(runs, timestamps=True, cfg=None):
    if cfg is not None:
        cfg["narration"]["timestamps"] = timestamps
    assert cli.main(["new", "--seed", "Why Roman concrete lasted", "--unattended"]) == 0
    (root,) = runs.iterdir()
    return Run.open(root.name, runs_dir=runs)


def test_a_chapter_without_timings_is_still_delivered_and_stays_finished(env, capsys):
    runs, cfg = env
    r = finished_run(runs, timestamps=False, cfg=cfg)  # as narration was before: no timings kept
    st = json.loads(r.path("status").read_text())
    assert st["state"] == "ready" and r.path("chapter_mp3").exists() and r.path("report").exists()
    assert not r.path("timing").exists()
    d = r.read("decisions")
    assert "No timed transcript: the narration has no ElevenLabs timings" in d
    assert f"python cli.py episode {r.name}" in d and "--timings" not in d  # advice cli.py accepts
    assert json.loads(r.path("episode_step").read_text())["built"] == []
    assert st["stages_done"] == st["stages_total"] == len(new.STEPS)
    # Running it again: finished, nothing redone, nothing more written down.
    capsys.readouterr()
    assert cli.main(["new", r.name, "--unattended"]) == 0
    assert "is finished" in capsys.readouterr().out and r.read("decisions") == d


def test_a_finished_run_over_its_cap_stays_ready(env):
    """The free steps (episode, report) may run even when spend is already past the cap."""
    runs, cfg = env
    r = finished_run(runs, cfg=cfg)
    for k in ("timing", "episode", "episode_step"):
        r.path(k).unlink()
    r.log({"stage": "test", "provider": "anthropic", "cost_usd": 100.0})  # spend far past the cap
    assert cli.main(["new", r.name, "--unattended"]) == 0
    assert json.loads(r.path("status").read_text())["state"] == "ready" and r.path("timing").exists()


def test_rerunning_new_adds_only_the_missing_episode_step(env, monkeypatch):
    """A finished run without the step: running `new` again builds the timing from the timings it kept,
    with no narration and no model calls."""
    runs, cfg = env
    r = finished_run(runs, cfg=cfg)
    for k in ("timing", "episode", "episode_step"):
        r.path(k).unlink()
    calls = len(llm._client.responder.calls)
    synth = []
    monkeypatch.setattr(tts, "synthesize", lambda *a, **k: synth.append(1))
    assert cli.main(["new", r.name, "--unattended"]) == 0
    assert r.path("timing").exists() and r.path("episode").exists()
    assert synth == [] and len(llm._client.responder.calls) == calls


def test_a_run_narrated_before_timings_were_kept(env):
    """Its chunks have no timings: re-running `new` records why and points to `cli.py episode` (Whisper)."""
    runs, cfg = env
    r = finished_run(runs, cfg=cfg)
    for k in ("timing", "episode", "episode_step"):
        r.path(k).unlink()
    strip_timings(r)
    assert cli.main(["new", r.name, "--unattended"]) == 0
    assert not r.path("timing").exists()
    assert r.read("decisions").count("No timed transcript: the narration has no ElevenLabs timings") == 1


def test_a_failed_read_along_page_still_leaves_the_chapter_askable(env):
    runs, cfg = env
    r = finished_run(runs, cfg=cfg)
    for k in ("timing", "episode", "episode_step"):
        r.path(k).unlink()
    # references.json from an earlier build, pointing at a sentence this script no longer has
    r.path("timing").parent.mkdir(exist_ok=True)
    (r.path("timing").parent / "references.json").write_text(json.dumps([{"sentence_id": "seg09-s999"}]))
    assert cli.main(["new", r.name, "--unattended"]) == 0
    assert r.path("timing").exists() and not r.path("episode").exists()
    assert "The timed transcript was built, so the chapter can be asked about, but the read-along page failed" \
        in r.read("decisions")
    assert json.loads(r.path("episode_step").read_text())["built"] == ["timing"]


def test_cli_lists_the_episode_stage():
    assert ("timing", "episode") in cli.STAGES_IN_ORDER
    assert common.PATHS["timing"] == "11_episode/timing.json"
