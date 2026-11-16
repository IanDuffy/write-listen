"""The live chapter's archive (voice/gpt_live/archive.py): the reading cut from a session recording into
the ElevenLabs piece layout, stitched, timed and given its asides' places. The recording is a synthetic
stereo WAV made by ffmpeg (tones on the guide's right channel, others on the listener's left, at known
times); no network (the download is replaced) and no Whisper (a fake ear, as tests/episode use)."""

import json
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest
from fastapi.testclient import TestClient

import server
from episode import recognize
from episode.timing import audio_duration
from fakes import FakeAnthropic, Responder
from faststart import archive as fast_archive
from stages import llm
from stages.common import Run, StageError
from voice.gpt_live import archive as A
from voice.gpt_live import session as S

LID = "live_test_A"
SEG1 = ("Alpha begins here. [D01] Alpha ends now.\n\n"
        "Bravo one goes first. Bravo two comes next. Bravo three closes it.")
SEG2 = "Charlie speaks plainly. Charlie stops.\n\nDelta is never heard. Delta stays quiet."

# The guide (right): s1-p01 1-4 s; s1-p02 read from its start 5-6.4 and 6.8-8 (cut by a tap in its second
# sentence); the guide's answer 10.5-12 (not a reading); s1-p02 again from its second sentence 13-16;
# s2-p01 17-19. The listener (left): 2-3 s and 8.5-10 (the question). s2-p02 is never heard.
GUIDE = [(1, 4), (5, 6.4), (6.8, 8), (10.5, 12), (13, 16), (17, 19)]
LISTENER = [(2, 3), (8.5, 10)]
DURATION = 21


def _expr(spans, hz):
    on = "+".join(f"between(t,{a},{b})" for a, b in spans)
    return f"if({on},0.3*sin(2*PI*{hz}*t),0)"


@pytest.fixture(scope="module")
def session_wav(tmp_path_factory):
    out = tmp_path_factory.mktemp("rec") / "session.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"aevalsrc=exprs='{_expr(LISTENER, 220)}|{_expr(GUIDE, 440)}':s=24000:d={DURATION}",
                    "-c:a", "pcm_s16le", str(out)], check=True)
    return out


def reading(pid, sentence, cut, start_ms, end_ms, heard, live_id=LID):
    return {"t": "2026-11-14T00:00:00", "id": pid, "sentence": sentence, "cut": cut, "heard": heard,
            "deviates": False, "times": {"start_ms": start_ms, "end_ms": end_ms}, "live_id": live_id}


READINGS = [
    reading("s1-p01", 0, False, 1000, 3400, "alpha begins here alpha ends now"),  # the end comes early
    reading("s1-p02", 0, True, 5000, 7600, "bravo one goes first bravo two"),
    reading("s1-p02", 1, False, 13000, 15200, "bravo two comes next bravo three closes it"),
    reading("s2-p01", 0, False, 17000, 18600, "charlie speaks plainly charlie stops"),
]
ASIDES = [
    {"id": "a1-live", "kind": "ask", "voice": "live", "started": "2026-11-14T00:00:08", "paragraph": "s1-p02",
     "sentence": 1, "ended": "guide", "seconds": 4.5, "restart": {"id": "s1-p02", "sentence": 1, "rule": "sentence"},
     "transcript": [{"who": "listener", "text": "What was that?"}, {"who": "guide", "text": "The second one."}],
     "live_id": LID, "session": "sess"},
    {"id": "c1-live", "kind": "check_in", "voice": "live", "started": "2026-11-14T00:00:19", "paragraph": "s2-p01",
     "sentence": 0, "ended": "ready", "seconds": 3.0, "restart": None,
     "transcript": [{"who": "guide", "text": "Nearly there."}], "live_id": LID, "session": "sess"},
]


def live_run(runs: Path, name="live-run", recs=READINGS, asides=ASIDES) -> Run:
    root = runs / name
    (root / "06_segments").mkdir(parents=True)
    (root / "fast.json").write_text(json.dumps({"mode": "fast", "voice": "live"}))
    (root / "05_outline.json").write_text(json.dumps({"title": "Levers", "sections": [
        {"n": 1, "title": "One", "lands": "a", "target_words": 20}, {"n": 2, "title": "Two", "lands": "b",
                                                                      "target_words": 20}]}))
    (root / "06_segments" / "seg01.md").write_text(SEG1)
    (root / "06_segments" / "seg02.md").write_text(SEG2)
    (root / "live").mkdir()
    (root / "live" / "reading.jsonl").write_text("".join(json.dumps(x) + "\n" for x in recs))
    if asides:
        (root / "12_asks").mkdir()
        (root / "12_asks" / "asides.jsonl").write_text("".join(json.dumps(x) + "\n" for x in asides))
    (root / "status.json").write_text(json.dumps({"run": name, "state": "ready", "audio": None, "mode": "fast"}))
    return Run.open(name, runs_dir=runs)


class FakeEar:
    """Whisper's interface: a piece (a WAV with its .json) is heard as its text spread over its length; the
    join's clip of the interrupted s1-p02 is heard with its second sentence starting where the tone does."""
    name = "fake ear"
    cacheable = False
    reheard = False
    clips: list = []

    def __init__(self, *a, **k):
        pass

    def __call__(self, path):
        meta = Path(path).with_suffix(".json")
        if meta.exists():
            words = json.loads(meta.read_text())["text"].split()
            d = audio_duration(Path(path))
            step = d / max(1, len(words))
            return [{"w": w, "start": round(i * step, 3), "end": round((i + 0.8) * step, 3), "p": 0.9}
                    for i, w in enumerate(words)]
        FakeEar.clips.append(audio_duration(Path(path)))
        # The clip starts at the reading's onset (4.97 s): sentence two's first word at 6.8 s.
        first = [("Bravo", 0.05), ("one", 0.4), ("goes", 0.75), ("first.", 1.1)]
        second = [("Bravo", 1.85), ("two", 2.3)]
        return [{"w": w, "start": t, "end": t + 0.3, "p": 0.9} for w, t in first + second]


@pytest.fixture
def offline(monkeypatch, session_wav):
    fetched = []

    def recording(cfg, live_id, out, client=None):
        fetched.append(live_id)
        if live_id != LID:
            return None
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(session_wav, out)
        return out
    monkeypatch.setattr(S, "recording", recording)
    monkeypatch.setattr(recognize, "WhisperRecognizer", FakeEar)
    FakeEar.clips = []
    return fetched


def piece(r, n, i):
    d = r.root / "10_audio" / f"sec{n:02d}"
    return d / f"p{i:02d}", json.loads((d / f"p{i:02d}.json").read_text())


# --- fetch -----------------------------------------------------------------------------------

def test_fetch_downloads_each_session_once(tmp_path, cfg, offline):
    r = live_run(tmp_path)
    got = A.fetch(r, cfg)
    assert got["downloaded"] == [LID] and (r.root / "live" / f"{LID}.wav").exists()
    assert A.fetch(r, cfg)["have"] == [LID] and offline == [LID]  # kept, not fetched again


def test_fetch_stops_clearly_when_no_recording_is_to_be_had(tmp_path, cfg, offline):
    r = live_run(tmp_path, recs=[reading("s1-p01", 0, False, 1000, 3400, "x", live_id="live_gone")])
    with pytest.raises(StageError, match="live.store"):
        A.fetch(r, cfg)
    assert not list((r.root / "live").glob("*.wav*"))
    r2 = live_run(tmp_path, name="old-page", recs=[{**READINGS[0], "live_id": None}])
    with pytest.raises(StageError, match="names no GPT-Live session"):
        A.fetch(r2, cfg)


# --- choosing readings -------------------------------------------------------------------------

def test_the_latest_whole_reading_wins_and_a_restart_is_joined():
    p = {"id": "s1-p02", "sentences": ["Bravo one goes first.", "Bravo two comes next.", "Bravo three closes it."]}
    recs = [{**x, "n": i} for i, x in enumerate(READINGS) if x["id"] == "s1-p02"]
    c = A.choose(recs, p)
    assert c["how"] == "joined" and [(x["n"], s, k) for x, s, k in c["parts"]] == [(1, 0, 1), (2, 1, None)]
    again = recs + [{**reading("s1-p02", 0, False, 20000, 22000, "all of it"), "n": 9}]
    assert A.choose(again, p) == {"how": "whole", "parts": [(again[-1], 0, None)]}
    alone = [recs[1]]  # the interrupted reading was never logged: kept from where it was heard
    assert A.choose(alone, p)["how"] == "partial" and A.choose(alone, p)["missing"] == [0, 1]
    assert A.choose([], p) == {"skip": "never heard"}
    assert "never picked up" in A.choose([recs[0]], p)["skip"]


def test_the_script_loses_only_paragraphs_never_heard(cfg):
    from faststart import live as fast_live

    text = "One [D01] here.\n\n[pause]\n\nTwo [T]he end.\n\nThree last."
    paras = fast_live.plan(cfg, 1, text)
    assert [p["id"] for p in paras] == ["s1-p01", "s1-p02", "s1-p03"] and paras[1]["beat"]
    assert A.heard_text(text, paras, {"s1-p02"}) == ("One [D01] here.\n\n[pause]\n\nThree last.", ["s1-p02"])
    assert A.heard_text(text, paras, set()) == (text, [])
    assert A.heard_text(text, paras[:2], {"s1-p02"}) == (text, [])  # live.json doesn't match: kept whole


# --- the archive -------------------------------------------------------------------------------

def test_the_archive_cuts_joins_stitches_times_and_places(tmp_path, cfg, offline):
    r = live_run(tmp_path)
    result = A.archive(r, cfg)
    ncfg = cfg["narration"]

    # The pieces: speech only, cut around the guide's tones with a little lead and tail.
    stem, m = piece(r, 1, 0)
    wav = audio_duration(stem.with_suffix(".wav"))
    assert m["id"] == "s1-p01" and m["pause_ms"] == 0 and m["simulated"] is False and m["live_id"] == LID
    assert 0.95 <= m["times"]["start"] <= 1.0 and 4.0 <= m["times"]["end"] <= 4.2  # the transcript's end was 3.4
    assert 3.0 <= wav <= 3.25 and m["seconds"] == pytest.approx(wav, abs=0.002)
    with wave.open(str(stem.with_suffix(".wav"))) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(float)
        assert w.getnchannels() == 1
    peak = np.argmax(np.abs(np.fft.rfft(x))) * 24000 / len(x)
    assert abs(peak - 440) < 5  # the guide's channel, not the listener's 220 Hz
    assert audio_duration(stem.with_suffix(".mp3")) == pytest.approx(wav, abs=0.08)

    # The restart: the interrupted reading up to its second sentence (in the pause before it), then the rest.
    stem, m = piece(r, 1, 1)
    assert m["id"] == "s1-p02" and m["how"] == "joined" and m["pause_ms"] == ncfg["chunk_pause_ms"] and m["last"]
    first, second = m["parts"]
    assert first["sentence_to"] == 1 and first["cut_by"] == "whisper" and 6.4 <= first["end"] <= 6.7
    assert 4.9 <= first["start"] <= 5.0 and second["sentence_from"] == 1 and 12.9 <= second["start"] <= 13.0
    assert 16.0 <= second["end"] <= 16.2  # never the guide's answer at 10.5-12
    wav = audio_duration(stem.with_suffix(".wav"))
    assert wav == pytest.approx((first["end"] - first["start"]) + A.JOIN_GAP_MS / 1000
                                + (second["end"] - second["start"]), abs=0.01)
    assert audio_duration(stem.with_suffix(".mp3")) == pytest.approx(wav + 0.45, abs=0.08)
    assert FakeEar.clips  # the join asked the ear

    # Section two: its first paragraph, its second never heard (skipped, reported, not a failure).
    stem, m = piece(r, 2, 0)
    assert m["id"] == "s2-p01" and m["pause_ms"] == ncfg["segment_pause_ms"] and m["last"]
    assert not (r.root / "10_audio" / "sec02" / "p01.json").exists()
    assert result["skipped"] == [{"id": "s2-p02", "reason": "never heard"}]
    assert [j["id"] for j in result["joined"]] == ["s1-p02"]
    for n in (1, 2):
        assert (r.root / "10_audio" / f"sec{n:02d}" / "section.mp3").exists()

    # The chapter is exactly its pieces, pauses included; fast/archive.json has the real seconds.
    total = sum(piece(r, n, i)[1]["seconds"] for n, i in ((1, 0), (1, 1), (2, 0)))
    assert audio_duration(r.path("chapter_mp3")) == pytest.approx(total, abs=0.15)
    saved = json.loads((r.root / "fast" / "archive.json").read_text())
    assert saved["live"] is True and saved["estimated"] is False and saved["seconds"] == pytest.approx(total, abs=0.15)
    assert [x["seconds"] for x in saved["sections"]] == pytest.approx([piece(r, 1, 0)[1]["seconds"] + piece(r, 1, 1)[1]["seconds"],
                                                                       piece(r, 2, 0)[1]["seconds"]], abs=0.002)
    assert saved["pieces"] == 3 and saved["paragraphs"] == 4
    assert fast_archive.run(r, cfg) == saved  # the fast archive leaves a recorded one as it is

    # The episode: timing over the chapter (the fake ear in Whisper's place), and episode.json.
    t = json.loads((r.root / "11_episode" / "timing.json").read_text())
    ep = json.loads((r.root / "11_episode" / "episode.json").read_text())
    assert t["aligned_by"] == "fake ear" and t["audio"] == "10_audio/chapter.mp3"
    assert [s["id"] for s in ep["segments"]] == ["seg01", "seg02"]
    script = r.path("script_final").read_text()
    assert "Delta" not in script and "Charlie stops." in script and "[D01]" in script  # only what was heard

    # The asides: the question at the sentence it interrupted, the check-in where section two begins.
    asides = {a["id"]: a for a in map(json.loads, (r.root / "12_asks" / "asides.jsonl").read_text().splitlines())}
    sents = {x["id"]: x for s in t["segments"] for x in s["sentences"]}
    q = asides["a1-live"]
    assert q["sentence_id"] == "seg01-s004" and q["segment"] == 1 and q["t"] == pytest.approx(sents["seg01-s004"]["start"])
    p01_start = piece(r, 1, 0)[1]["seconds"] + 0.45
    assert p01_start < q["t"] < p01_start + piece(r, 1, 1)[1]["seconds"]
    assert q["restart"] == {"id": "s1-p02", "sentence": 1, "rule": "sentence", "t": q["t"], "sentence_id": "seg01-s004"}
    c = asides["c1-live"]
    sec2 = saved["sections"][1]["offset"] + ncfg["segment_pause_ms"] / 1000
    assert c["sentence_id"] == "seg02-s001" and c["t"] == pytest.approx(sec2, abs=0.3) and c["restart"] is None
    assert result["asides"] == {"placed": 2, "unplaced": []}

    # status.json points at the chapter; the rest of it is kept.
    status = json.loads(r.path("status").read_text())
    assert status["audio"] == "10_audio/chapter.mp3" and status["state"] == "ready" and status["mode"] == "fast"
    prog = json.loads((r.root / "live" / "archive.json").read_text())
    assert prog["state"] == "done" and prog["result"]["pieces"] == 3 and prog["fetched"]["downloaded"] == [LID]


def test_a_second_archive_replaces_its_pieces(tmp_path, cfg, offline):
    r = live_run(tmp_path)
    A.archive(r, cfg)
    (r.root / "live" / "reading.jsonl").write_text(json.dumps(READINGS[0]) + "\n")  # only s1-p01 now
    result = A.build(r, cfg)
    assert result["pieces"] == 1 and not (r.root / "10_audio" / "sec01" / "p01.wav").exists()
    assert not (r.root / "10_audio" / "sec02" / "p00.wav").exists()


def test_a_section_whose_opening_was_never_heard_still_opens_with_a_section_break(tmp_path, cfg, offline):
    r = live_run(tmp_path, recs=[READINGS[0], reading("s2-p02", 0, False, 17000, 18600, "delta is never heard delta stays quiet")])
    A.archive(r, cfg)
    _, m = piece(r, 2, 0)
    assert m["id"] == "s2-p02" and m["pause_ms"] == cfg["narration"]["segment_pause_ms"]
    assert "Charlie" not in r.path("script_final").read_text() and "Delta" in r.path("script_final").read_text()
    r2 = live_run(tmp_path, name="second-only", recs=[reading("s1-p02", 0, False, 13000, 15200, "bravo")])
    A.archive(r2, cfg)
    _, m = piece(r2, 1, 0)
    assert m["id"] == "s1-p02" and m["pause_ms"] == 0  # nothing before the chapter
    assert not (r2.root / "10_audio" / "sec02" / "section.mp3").exists()
    assert "<!-- seg02 -->" not in r2.path("script_final").read_text()


def test_an_archive_with_nothing_usable_fails_and_says_so(tmp_path, cfg, offline):
    r = live_run(tmp_path, recs=[reading("s1-p02", 0, True, 5000, 7600, "bravo")])
    with pytest.raises(StageError, match="No paragraph"):
        A.archive(r, cfg)
    prog = json.loads((r.root / "live" / "archive.json").read_text())
    assert prog["state"] == "failed" and "No paragraph" in prog["error"]
    assert not r.path("chapter_mp3").exists()


def test_a_run_not_read_live_is_refused(tmp_path, cfg, offline):
    r = live_run(tmp_path)
    (r.root / "fast.json").write_text(json.dumps({"mode": "fast"}))
    with pytest.raises(StageError, match="not read live"):
        A.build(r, cfg)


# --- in the background, and the route ------------------------------------------------------------

@pytest.fixture
def app(cfg, tmp_path, monkeypatch):
    cfg["listener_dir"] = str(tmp_path / "listener")
    cfg["live"]["provider"] = "gpt_live"
    monkeypatch.setattr(llm, "_client", FakeAnthropic(Responder()))
    monkeypatch.setattr(server.memory, "after_session", lambda *a, **k: False)
    runs = tmp_path / "runs"
    runs.mkdir()
    pipe = server.Pipeline(runs_dir=runs)
    monkeypatch.setattr(pipe, "start", lambda args, run: setattr(pipe, "proc", NS(returncode=None, poll=lambda: None)))
    return TestClient(server.create_app(pipe, cfg, remember_on_start=False)), runs


def test_the_route_starts_one_archive_at_a_time(app, monkeypatch):
    c, runs = app
    r = live_run(runs)
    go, calls = threading.Event(), []

    def slow(run, cfg, references=False, **k):
        calls.append((run.name, references))
        go.wait(5)
        A.progress(run, state="done")
    monkeypatch.setattr(A, "archive", slow)
    assert c.post("/api/live/archive", json={"run": "live-run"}).json() == {"started": True}
    again = c.post("/api/live/archive", json={"run": "live-run"}).json()
    assert again["started"] is False and "already" in again["reason"]
    go.set()
    A._RUNNING[str(r.root.resolve())].join(5)
    assert calls == [("live-run", False)]
    assert json.loads((r.root / "live" / "archive.json").read_text())["state"] == "done"
    assert c.post("/api/live/archive", json={"run": "live-run", "references": True}).json() == {"started": True}
    A._RUNNING[str(r.root.resolve())].join(5)
    assert calls[-1] == ("live-run", True)


def test_the_route_refuses_what_it_cannot_archive(app):
    c, runs = app
    live_run(runs, name="silent", recs=[])
    (runs / "silent" / "live" / "reading.jsonl").unlink()
    res = c.post("/api/live/archive", json={"run": "silent"}).json()
    assert res == {"started": False, "reason": "Nothing in this chapter was read live."}
    assert c.post("/api/live/archive", json={"run": "nope"}).status_code == 404


def test_a_failure_in_the_background_lands_in_the_progress_file(tmp_path, cfg, monkeypatch):
    r = live_run(tmp_path)
    monkeypatch.setattr(S, "recording", lambda *a, **k: None)
    cfg["live"]["archive_wait_seconds"], cfg["live"]["archive_retry_seconds"] = 0.3, 0.1
    assert A.start(r, cfg) == {"started": True}
    A._RUNNING[str(r.root.resolve())].join(10)
    prog = json.loads((r.root / "live" / "archive.json").read_text())
    assert prog["state"] == "failed" and "No recording" in prog["error"]


def test_a_recording_not_ready_yet_is_waited_for(tmp_path, cfg, monkeypatch):
    """Asked for as the session ends, the recording may not be ready: fetch is tried again."""
    r = live_run(tmp_path)
    tries = []
    real = S.recording

    def late(cfg_, lid, out, client=None):
        tries.append(lid)
        return None if len(tries) < 3 else real(cfg_, lid, out, client=client)
    monkeypatch.setattr(S, "recording", late)
    cfg["live"]["archive_wait_seconds"], cfg["live"]["archive_retry_seconds"] = 5, 0.05
    try:
        A.archive(r, cfg)
    except Exception:
        pass  # what the fake recording gives after that is the other tests' business
    assert len(tries) >= 3


def test_the_cli_runs_the_archive(tmp_path, cfg, offline, monkeypatch, capsys):
    from voice.gpt_live import __main__ as M

    live_run(tmp_path / "elsewhere")
    monkeypatch.setattr(M, "load_config", lambda: cfg)
    assert M.main(["archive", "live-run", "--runs-dir", str(tmp_path / "elsewhere")]) == 0
    out = capsys.readouterr().out
    assert "skipped s2-p02: never heard" in out and "joined s1-p02" in out
    assert (tmp_path / "elsewhere" / "live-run" / "10_audio" / "chapter.mp3").exists()
