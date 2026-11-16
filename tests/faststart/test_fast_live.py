"""A fast run read live (faststart/live.py; Spec One Live Voice): no ElevenLabs narration, a locked
section's paragraphs go straight to the page's queue ("ready" means locked), and the archive keeps
the script without audio. A run that is not live is unchanged."""

import json
from types import SimpleNamespace as NS

import pytest

from fast_narrate_fakes import LEDGER, SECTIONS, lay_out
from fast_run_fakes import no_leaks, offline, world  # noqa: F401

from faststart import archive, base, live, narrate, routes, status
from faststart import run as fast_run
from faststart import timings as T
from faststart.player import SimPlayer
from faststart.scheduler import Scheduler
from stages import new, seed
from stages.common import StageError, split_script_segments
from voice.gpt_live.chapter import clean_paragraphs


def with_provider(cfg: dict, provider: str) -> dict:
    return {**cfg, "live": {**(cfg.get("live") or {}), "provider": provider}}


def live_run(r, cfg):
    base.mark_fast(r, cfg=with_provider(cfg, "gpt_live"))
    return r


class NoRows:
    def has(self, row):
        return False


def narrate_live(r, cfg, numbers=(1, 2, 3)):
    narrator = live.LiveNarrator(r, cfg)
    return {n: narrator.section(n, SECTIONS[n]) for n in numbers}


# --- the narrator ----------------------------------------------------------------------------------

def test_the_live_narrator_gives_each_paragraph_as_an_indexed_piece_and_writes_live_json(run, cfg):
    live_run(run, cfg)
    got = []
    out = live.LiveNarrator(run, cfg).section(1, SECTIONS[1], on_piece=got.append)
    paras = clean_paragraphs(SECTIONS[1], 1)
    assert got == out and [p.index for p in out] == list(range(len(paras))) == [0, 1, 2, 3]
    assert [p.last for p in out] == [False, False, False, True]
    assert all(p.wav is None and p.mp3 is None and p.section == 1 and not p.simulated for p in out)
    assert [p.text for p in out] == [p["text"] for p in paras]
    n, speech = cfg["narration"], cfg["fast"]["narration"]["speech_chars_per_second"]
    # nothing before the chapter; [pause] opens the third paragraph
    assert [p.pause_ms for p in out] == [0, n["chunk_pause_ms"], n["beat_pause_ms"], n["chunk_pause_ms"]]
    assert out[1].seconds == pytest.approx(n["chunk_pause_ms"] / 1000 + len(out[1].text) / speech, abs=0.001)

    saved = json.loads((base.section_dir(run, 1) / "live.json").read_text(encoding="utf-8"))
    assert saved["section"] == 1 and [p["id"] for p in saved["paragraphs"]] == ["s1-p01", "s1-p02", "s1-p03", "s1-p04"]
    assert [p["beat"] for p in saved["paragraphs"]] == [False, False, True, False]
    assert saved["seconds"] == pytest.approx(sum(p.seconds for p in out), abs=0.001)
    assert live.pieces(run, 1) == out and live.narrated(run, 1) and not live.narrated(run, 2)
    assert "[D01]" not in out[0].text and "[pause]" not in " ".join(p.text for p in out)

    second = live.LiveNarrator(run, cfg).section(2, SECTIONS[2])
    assert second[0].pause_ms == n["segment_pause_ms"]  # a section break


def test_a_stop_raised_from_on_piece_leaves_the_section_whole_on_disk(run, cfg):
    live_run(run, cfg)

    def stop(p):
        raise StageError("The run is stopping")
    with pytest.raises(StageError):
        live.LiveNarrator(run, cfg).section(2, SECTIONS[2], on_piece=stop)
    assert live.narrated(run, 2) and live.pieces(run, 2)[-1].last


def test_a_bracket_the_voice_would_read_stops_before_anything_is_written(run, cfg):
    live_run(run, cfg)
    with pytest.raises(StageError, match="bracketed"):
        live.LiveNarrator(run, cfg).section(1, "A line with [laughs] in it.")
    assert not (base.section_dir(run, 1) / "live.json").exists()


# --- marking a run live --------------------------------------------------------------------------

def test_mode_args_marks_a_fast_run_live_only_when_the_provider_is_gpt_live(tmp_path, cfg):
    from stages.common import Run

    a, b, c = (Run.open(x, create=True, runs_dir=tmp_path) for x in ("a", "b", "c"))
    assert routes.mode_args(a, "fast", with_provider(cfg, "gpt_live"), live=True) == ["--mode", "fast"]
    assert routes.mode_args(b, "fast", with_provider(cfg, "elevenlabs"), live=True) == ["--mode", "fast"]
    assert routes.mode_args(c, "overnight", with_provider(cfg, "gpt_live"), live=True) == [] and not base.is_fast(c)
    assert base.is_live(a) and base.is_fast(b) and not base.is_live(b)
    # a continued run keeps the voice it was started with
    routes.mode_args(b, "fast", with_provider(cfg, "gpt_live"), live=True)
    assert not base.is_live(b)


def test_mode_args_marks_no_run_live_unless_a_live_session_started_it(tmp_path, cfg, monkeypatch):
    """Regression: a chapter started from the typed chat (voice couldn't start) was marked "voice":
    "live" because config said gpt_live, so it was never narrated and nobody could play it. Only a
    live session's start (live=True) marks it; config.yaml is read from disk when no cfg is passed."""
    from stages.common import Run

    monkeypatch.setattr(routes, "load_config", lambda: with_provider(cfg, "gpt_live"))
    typed, spoken, given = (Run.open(x, create=True, runs_dir=tmp_path) for x in ("typed", "spoken", "given"))
    assert routes.mode_args(typed, "fast") == ["--mode", "fast"]
    assert base.is_fast(typed) and not base.is_live(typed)
    routes.mode_args(spoken, "fast", live=True)
    assert base.is_live(spoken)
    routes.mode_args(given, "fast", with_provider(cfg, "gpt_live"))  # a cfg alone is not a live session
    assert base.is_fast(given) and not base.is_live(given)


def test_voice_matches_tells_a_live_run_from_a_narrated_one(tmp_path, cfg):
    """Regression: a seed lookup continued a run in the other voice (the page could not play it);
    voice_matches is what start_seed asks."""
    from stages.common import Run

    narrated, spoken = (Run.open(x, create=True, runs_dir=tmp_path) for x in ("narrated", "spoken"))
    base.mark_fast(narrated, cfg=with_provider(cfg, "elevenlabs"))
    base.mark_fast(spoken, cfg=with_provider(cfg, "gpt_live"))
    assert base.voice_matches(narrated.root, False) and not base.voice_matches(narrated.root, True)
    assert base.voice_matches(spoken.root, True) and not base.voice_matches(spoken.root, False)


# --- the files: done_from_files, archive, status ---------------------------------------------------

def test_a_live_runs_files_restore_narrated_pieces_and_archived(run, cfg):
    live_run(run, cfg)
    lay_out(run)
    outline = json.loads(base.path(run, "outline_json").read_text(encoding="utf-8"))
    narrate_live(run, cfg, (1, 2))
    done = fast_run.done_from_files(run, outline, LEDGER, NoRows())
    assert done["locked"] == [1, 2, 3] and done["narrated"] == [1, 2] and not done["archived"]
    assert {n: len(v) for n, v in done["pieces"].items()} == {1: 4, 2: 4, 3: 0}
    assert done["pieces"][1] == [p.seconds for p in live.pieces(run, 1)]

    narrate_live(run, cfg, (3,))
    assert archive.rolling(run, cfg) is None  # no timing for a live run; it must not fail
    saved = archive.run(run, cfg)  # build_episode is True: a live run builds none
    done = fast_run.done_from_files(run, outline, LEDGER, NoRows())
    assert done["narrated"] == [1, 2, 3] and done["archived"]
    assert fast_run.chars_ahead(cfg, run) == 0  # nothing to pay ElevenLabs for

    assert saved["live"] is True and saved["episode"] is False and saved["timing"] is None
    assert [s["n"] for s in saved["sections"]] == [1, 2, 3]
    assert saved["seconds"] == pytest.approx(sum(p.seconds for n in (1, 2, 3) for p in live.pieces(run, n)), abs=0.01)
    assert saved["words"] > 100
    assert [n for n, _ in split_script_segments(run.read("script_final"))] == [1, 2, 3]
    assert run.path("brief").exists() and json.loads((base.path(run, "work") / "archive.json").read_text())["live"]
    assert not run.path("chapter_mp3").exists() and not (run.root / "11_episode" / "timing.json").exists()
    assert not (run.root / "11_episode" / "episode.json").exists()
    assert archive.run(run, cfg) == saved  # a second run returns what the first did
    assert seed.finished(run.root)  # the same seed asked for again starts a new chapter


def test_status_for_a_live_run_lists_paragraph_ids_and_drops_the_recording_step(run, cfg):
    live_run(run, cfg)
    lay_out(run)
    outline = json.loads(base.path(run, "outline_json").read_text(encoding="utf-8"))
    narrate_live(run, cfg, (1,))
    done = fast_run.done_from_files(run, outline, LEDGER, NoRows())
    snap = Scheduler([1, 2, 3], {}, done=done).snapshot()
    got = status.fields(run, outline, snap, first_audio=False, step="opening")
    assert got["voice"] == "live"
    assert [s["key"] for s in got["steps"]] == ["scout", "outline", "opening"]
    one, two = got["sections"][0], got["sections"][1]
    assert [p["id"] for p in one["pieces"]] == ["s1-p01", "s1-p02", "s1-p03", "s1-p04"]
    assert all("src" not in p and p["seconds"] > 0 for p in one["pieces"]) and one["ready"]
    # locked but not narrated yet: ready, never "recording"
    assert two["step"] == "ready" and two["ready"]
    assert status.section_step(2, {"locked": True}, {}, [], live=True) == "ready"
    assert status.section_step(2, {"locked": True}, {}, []) == "recording"  # ElevenLabs, unchanged

    medians = {"scout": 10.0, "outline": 10.0, "opening": 10.0, "first_piece": 100.0}
    assert status.first_audio_by("opening", 0.0, 1.0, medians, live=True) == 10.0
    assert status.first_audio_by("opening", 0.0, 1.0, medians) == 110.0


def test_a_run_that_is_not_live_is_unchanged(run, cfg):
    base.mark_fast(run, cfg=with_provider(cfg, "elevenlabs"))
    lay_out(run)
    outline = json.loads(base.path(run, "outline_json").read_text(encoding="utf-8"))
    assert live.module(run) is narrate and fast_run.chars_ahead(cfg, run) > 0
    got = status.fields(run, outline, Scheduler([1, 2, 3], {}).snapshot(), step="opening")
    assert "voice" not in got and [s["key"] for s in got["steps"]][-1] == "first_piece"


# --- the medians ---------------------------------------------------------------------------------

def test_live_medians_are_kept_apart_and_have_no_recording_step(tmp_path, cfg):
    cfg = {**cfg, "fast": {**cfg["fast"], "timings_file": str(tmp_path / "timings.json")}}
    runs = [{"run": "el", "steps": {"scout": [60.0], "outline": [80.0], "opening": [50.0], "first_piece": [9.0],
                                    "narrate": [120.0], "first_audio": [200.0]}},
            {"run": "lv", "voice": "live", "steps": {"scout": [70.0], "first_piece": [0.0], "narrate": [0.01],
                                                     "first_audio": [150.0]}}]
    base.write_json(T.path(cfg), {"medians": T.medians_of(runs), "runs": runs})
    el, lv = T.load(cfg), T.load(cfg, live=True)
    assert el["first_audio"] == 200.0 and el["narrate"] == 120.0 and el["first_piece"] == 9.0  # no live run in them
    assert el["scout"] == 65.0  # the writing steps are the same work either way
    assert lv["first_piece"] == lv["narrate"] == 0.0 and lv["first_audio"] == 150.0
    assert T.estimate(with_provider(cfg, "gpt_live")) == 150.0 and T.estimate(with_provider(cfg, "elevenlabs")) == 200.0
    # before any live run: the steps to the opening's lock, added up
    base.write_json(T.path(cfg), {"medians": T.medians_of(runs[:1]), "runs": runs[:1]})
    assert T.load(cfg, live=True)["first_audio"] == 190.0


# --- a whole run --------------------------------------------------------------------------------

def test_a_live_fast_run_end_to_end_reaches_its_archive_without_elevenlabs(tmp_path, cfg, monkeypatch):
    w = world(tmp_path, cfg, monkeypatch)
    r = live_run(w.r, w.cfg)
    seen, real = [], new.write_status
    monkeypatch.setattr(new, "write_status", lambda r, cfg, state, **f: seen.append(real(r, cfg, state, **f)) or seen[-1])

    out = fast_run.run(r, w.cfg, player=SimPlayer())

    assert out["sections"] == 4 and out["episode"] is False and out["seconds"] > 0
    assert w.voice.calls == [] and w.voice.balance.reads == 0  # nothing asked of ElevenLabs
    assert not r.path("chapter_mp3").exists() and not list(r.path("audio_dir").glob("sec*/p*.mp3"))
    saved = json.loads((base.path(r, "work") / "archive.json").read_text(encoding="utf-8"))
    assert saved["live"] and [n for n, _ in split_script_segments(r.read("script_final"))] == [1, 2, 3, 4]

    timeline = base.read_timeline(r)
    paragraphs = sum(len(live.paragraphs(r, n)) for n in range(1, 5))
    assert sum(e["event"] == T.PIECE_READY for e in timeline) == paragraphs
    assert sum(e["event"] == T.ARCHIVE_DONE for e in timeline) == 1

    assert all(s.get("voice") == "live" for s in seen)
    assert not any("Recording" in (s["label"] or "") for s in seen)
    assert not any(x["key"] == "first_piece" for s in seen for x in s["steps"])
    last = json.loads(r.path("status").read_text(encoding="utf-8"))
    assert last["state"] == "ready" and last["audio"] is None and last["first_audio"]
    for sec in last["sections"]:
        assert sec["ready"] and sec["complete"] and sec["step"] == "ready"
        assert [p["id"] for p in sec["pieces"]] == [p["id"] for p in live.paragraphs(r, sec["n"])]

    saved_timings = json.loads(T.path(w.cfg).read_text(encoding="utf-8"))
    assert saved_timings["runs"][-1]["voice"] == "live"
    assert saved_timings["medians"]["narrate"] is None  # the live run's ~0 s never reaches the ElevenLabs medians
    assert T.load(w.cfg, live=True)["first_audio"] == next(e["t"] for e in timeline if e["event"] == T.FIRST_AUDIO)

    # started again, a finished live run stays finished and asks nothing
    again = fast_run.run(r, w.cfg)
    assert again["sections"] == 4 and w.voice.calls == []
