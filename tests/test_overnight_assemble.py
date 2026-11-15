"""Overnight assembly (overnight/assemble.py): the recorded paragraphs as chapter.mp3, timing.json,
references and episode.json, with real ffmpeg and fake recognizer and references."""

import json
import wave

import pytest

from episode import recognize, references, script
from overnight import assemble, record
from overnight_record_fakes import FakeLive, GAP_MS, make_run, no_threads_left, offline, word_ms  # noqa: F401
from stages.common import StageError


class FakeRecognizer:
    """Word times for a paragraph WAV, laid out exactly as FakeSession synthesized it."""
    name = "fake recognizer"
    reheard = False

    def __init__(self, r):
        self.text = {p["id"]: p["text"] for p in record.paragraphs(r)}
        self.calls = []

    def __call__(self, path):
        self.calls.append(path.name)
        t, out = record.PAD_LEAD, []
        for w in self.text[path.stem].split():
            out.append({"w": w, "start": round(t, 3), "end": round(t + word_ms(w) / 1000, 3)})
            t += (word_ms(w) + GAP_MS) / 1000
        return out


def cards(r, cfg, seg, prompt):
    """The references call, replaced: a source card on each section's first cited sentence, priced."""
    r.log({"stage": "episode_references", "cost_usd": 0.01})
    first = next((s for s in seg.sentences if s.dossier), None)
    return {"sentences": [] if first is None else [
        {"id": first.id, "sources": [{"dossier": first.dossier[0], "title": f"Source for {seg.id}", "creator": "",
                                      "note": ""}], "works": [], "people": [], "quotes": []}]}


@pytest.fixture
def recorded(tmp_path, cfg, monkeypatch):
    r = make_run(tmp_path)
    record.run(r, cfg, session_factory=FakeLive())
    asked = []
    monkeypatch.setattr(references, "ask", lambda *a: asked.append(a[3]) or cards(*a))
    monkeypatch.setattr(recognize, "WhisperRecognizer", lambda *a, **k: pytest.fail("Whisper was loaded"))
    return r, asked


def wav_seconds(p):
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


def test_the_chapter_is_joined_timed_and_bundled(recorded, cfg):
    r, asked = recorded
    rec = FakeRecognizer(r)
    p = assemble.run(r, cfg, recognizer=rec)
    assert p == r.root / "11_episode" / "episode.json"

    concat = (r.root / "10_audio" / "concat.txt").read_text().splitlines()
    names = [line.split("/")[-1].rstrip("'") for line in concat]
    n = cfg["narration"]
    assert names[:6] == ["s1-p01.wav", f"silence_{n['chunk_pause_ms']}ms_24000_2_1.wav", "s1-p02.wav",
                         f"silence_{n['segment_pause_ms']}ms_24000_2_1.wav", "s2-p01.wav",
                         f"silence_{n['beat_pause_ms']}ms_24000_2_1.wav"]
    assert names[6] == "s2-p02.wav" and len([x for x in names if not x.startswith("silence_")]) == 9
    assert (r.root / "10_audio" / "chapter.mp3").exists() and not (r.root / "10_audio" / "joined.wav").exists()

    t = json.loads((r.root / "11_episode" / "timing.json").read_text())
    segs = script.parse((r.root / "09_script_final.md").read_text(), n.get("pronunciations") or {})
    assert [x["id"] for s in t["segments"] for x in s["sentences"]] == [x.id for s in segs for x in s.sentences]
    assert t["stats"]["estimated"] == 0 and t["pieces"] == 9 and sorted(rec.calls) == sorted(
        f"{q['id']}.wav" for q in record.paragraphs(r))
    total = sum(wav_seconds(r.root / "10_audio" / "paragraphs" / f"{q['id']}.wav") for q in record.paragraphs(r))
    pauses = n["chunk_pause_ms"] * 4 + n["segment_pause_ms"] * 3 + n["beat_pause_ms"]
    assert t["duration"] == pytest.approx(total + pauses / 1000, abs=0.15)
    s2 = next(s for s in t["segments"] if s["id"] == "seg02")
    first_s2 = (wav_seconds(r.root / "10_audio" / "paragraphs" / "s1-p01.wav")
                + wav_seconds(r.root / "10_audio" / "paragraphs" / "s1-p02.wav")
                + (n["chunk_pause_ms"] + n["segment_pause_ms"]) / 1000)
    assert s2["start"] == pytest.approx(first_s2 + record.PAD_LEAD, abs=0.02)  # each word at its offset

    ep = json.loads(p.read_text())
    assert ep["title"] == "Why Herons Wait" and ep["audio"] == "10_audio/chapter.mp3" and ep["run"] == r.name
    assert len(ep["references"]) == 4 and len(asked) == 4
    costs = [x for x in r.read_log() if x.get("stage") == "assemble" and x.get("cost_usd")]
    assert sum(x["cost_usd"] for x in costs) == pytest.approx(0.04)
    recorded_usd = json.loads((r.root / "10_audio" / "recorded.json").read_text())["cost_usd"]
    assert r.spent() == pytest.approx(0.04 + recorded_usd, abs=1e-4)


def test_a_second_run_redoes_nothing(recorded, cfg):
    r, asked = recorded
    rec = FakeRecognizer(r)
    assemble.run(r, cfg, recognizer=rec)
    mp3 = r.root / "10_audio" / "chapter.mp3"
    before = (mp3.stat().st_mtime_ns, len(rec.calls), len(asked))
    (r.root / "11_episode" / "episode.json").unlink()  # killed before the bundle was written
    assemble.run(r, cfg, recognizer=rec)
    assert (mp3.stat().st_mtime_ns, len(rec.calls), len(asked)) == before
    assert (r.root / "11_episode" / "episode.json").exists()


def test_whisper_is_the_default_recognizer_and_loaded_only_when_needed(recorded, cfg, monkeypatch):
    r, _ = recorded
    made = []
    monkeypatch.setattr(recognize, "WhisperRecognizer", lambda *a, **k: made.append(a) or FakeRecognizer(r))
    assemble.run(r, cfg)
    assert made == [("small.en",)]  # overnight.record.whisper_model: the model preflight checks
    (r.root / "11_episode" / "episode.json").unlink()
    assemble.run(r, cfg)
    assert len(made) == 1


def test_assembly_needs_the_recording(tmp_path, cfg):
    r = make_run(tmp_path)
    with pytest.raises(StageError, match="recorded.json"):
        assemble.run(r, cfg, recognizer=FakeRecognizer(r))


def test_a_missing_paragraph_stops_assembly(recorded, cfg):
    r, _ = recorded
    (r.root / "10_audio" / "paragraphs" / "s3-p02.wav").unlink()
    with pytest.raises(StageError, match="s3-p02 has no recording"):
        assemble.run(r, cfg, recognizer=FakeRecognizer(r))
