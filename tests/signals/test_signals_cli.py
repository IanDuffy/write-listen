"""signals/cli.py and signals/report.py: wl signals fetch | run | report, through cli.main."""
import json
import shutil

import pytest

import cli
import signals_synth as syn
from demo import testers
from signals import cli as signals_cli
from signals import measure, report, store
from stages import common
from stages.common import StageError
from voice.gpt_live import session as live_session


@pytest.fixture(autouse=True)
def no_real_dot_env(monkeypatch, tmp_path_factory):
    """wl signals loads ROOT/.env, which on Ian's machine holds the real keys: never in a test."""
    monkeypatch.setattr(signals_cli, "ROOT", tmp_path_factory.mktemp("no-dot-env"))
    monkeypatch.delenv("WL_DEMO", raising=False)


@pytest.fixture
def no_key(monkeypatch):
    def refuse():
        raise StageError("No OpenAI key: set OPENAI_API_KEY in .env (see README, 'API keys').")
    monkeypatch.setattr(live_session, "api_key", refuse)


def make_tester(cfg, tmp_path, label="Pooja", **kw):
    lid = testers.create(cfg, label)["lid"]
    syn.visit_files(testers.folder(lid), tmp_path, **kw)
    return lid


def phone_on_loudspeaker(lst):
    syn.jsonl(lst / "study" / "events.jsonl", [
        {"t": "2026-11-21T10:09:50", "event": "audio_route",
         "data": {"reason": "speakerphone", "result": "switched", "ua": "Mozilla/5.0 (Linux; Android 16)",
                  "ua_hints": {"model": "Pixel 8", "platform": "Android"},
                  "inputs": ["Speakerphone", "Headset earpiece"], "track_label": "Speakerphone"}},
        {"t": "2026-11-21T10:20:00", "event": "play", "phase": "talk"}])


def test_run_then_report_through_the_cli(cfg, tmp_path, capsys):
    lid = make_tester(cfg, tmp_path, bleed_g2=0.15)
    lst = testers.folder(lid)
    assert cli.main(["signals", "run", lid]) == 0
    out = capsys.readouterr().out
    assert f"{lid} ({lst}): 8 turn records in 2 conversations" in out
    assert "intro-20261121-101000      intro    1 recording, 2 turns; 4 sounds with no transcript" in out
    assert "20261121-101500            guide    2 recordings, 6 turns; 6 sounds with no transcript" in out
    assert len(store.records(lst, syn.GUIDE)) == 6

    phone_on_loudspeaker(lst)
    store.add_mark(lst, syn.GUIDE, 12.0, "lost them here")
    store.add_mark(lst, syn.GUIDE, 300.0, "nothing near")
    assert cli.main(["signals", "report", lid]) == 0
    text = capsys.readouterr().out
    rep = report.build(lst, common.load_config(), lid)
    assert text == rep["text"]
    assert rep["tags"] == {"counts": {"inhale": 1}, "turns": 9, "tagged": 1}
    assert "Sound tags: [inhale] 1 (1 of 9 listener turns had one)" in text
    assert rep["phone"] == {"records": 1, "phone": True, "speakerphone": True, "devices": ["Pixel 8 Android"]}
    rec = {r["live_id"]: r for r in rep["recordings"]}
    assert set(rec) == {"live_i", "live_g1", "live_g2"} and rec["live_i"]["file"] == "live_i.mp3"
    assert rec["live_g1"]["bleed_db"] < -30 < -15 < rec["live_g2"]["bleed_db"]   # the second on the loudspeaker
    s = rep["sources"]["recording"]
    assert s["turns"] == 8 and s["with_norm"] == 5
    assert s["parts"] == {"intro": {"turns": 2, "with_norm": 0}, "guide": {"turns": 6, "with_norm": 5}}
    assert s["first"]["stem"] == syn.GUIDE and s["first"]["turn"] == 1
    assert s["first"]["read"] == {"intro": 2, "guide": 1}                     # the intro counts toward the norm
    assert "first at turn 1 of 20261121-101500 (guide), from intro 2, guide 1" in text
    assert s["mentions"]["tags"] == 1 and s["mentions"]["cut_in"] == 1 and s["mentions"]["trailed"] == 1
    near, far = rep["marks"]
    assert near["text"] == "lost them here" and [t["turn"] for t in near["turns"]] == [0, 1, 2, 3]
    assert "tags" in near["turns"][1]["mention"]
    assert far["turns"] == [] and "no turn within 15 s" in text
    assert "Phone: a phone (Pixel 8 Android), on the loudspeaker; 1 audio_route records" in text


def test_report_before_any_records(cfg, tmp_path):
    lst = tmp_path / "solo"
    syn.jsonl(lst / "sessions" / "20261121-090000.jsonl", [
        {"t": "2026-11-21T09:00:00", "who": "session", "live_id": "x1"},
        {"t": "2026-11-21T09:00:05", "who": "listener", "text": "[tongue click ]Yeah"},
        {"t": "2026-11-21T09:00:06", "who": "guide", "text": "Hi."},
        {"t": "2026-11-21T09:00:09", "who": "listener", "text": "[inhale"},
        {"t": "2026-11-21T09:00:12", "who": "listener", "text": "]Sure."}])
    rep = report.build(lst, cfg)
    assert rep["tags"] == {"counts": {"tongue click": 1, "inhale": 1}, "turns": 2, "tagged": 2}
    assert rep["sources"] == {} and "No records yet (wl signals run)" in rep["text"]
    assert rep["recordings"] == [{"live_id": "x1", "stem": "20261121-090000", "part": "guide", "file": None}]
    assert "no audio (wl signals fetch)" in rep["text"] and "Phone: not known" in rep["text"]


def test_fetch_through_the_cli(cfg, tmp_path, capsys, no_key):
    lid = make_tester(cfg, tmp_path)
    lst = testers.folder(lid)
    store.audio_path(lst, "live_g2").unlink()
    syn.wav(lst / "chapters" / "20261121-heron" / "live" / "live_g2.wav", 1.0, [(0.2, 0.6)], [(0.0, 0.1)])
    assert cli.main(["signals", "fetch", lid]) == 0
    out = capsys.readouterr().out
    assert f"{lid} ({lst}): 3 have" in out
    assert "have     20261121-101500            live_g2: reused chapters/20261121-heron/live/live_g2.wav" in out
    assert "live_i: only study/live_i.mp3 (lossy): No OpenAI key" in out


def test_which_listeners(cfg, tmp_path, capsys, monkeypatch):
    a = testers.create(cfg, "Ann")["lid"]
    b = testers.create(cfg, "Ben")["lid"]
    gone = testers.create(cfg, "Cy")["lid"]
    shutil.rmtree(testers.folder(gone))
    own = common.listener_dir(cfg)
    assert signals_cli.listeners(cfg, None, False) == [("own", own)]
    assert signals_cli.listeners(cfg, "own", False) == [("own", own)]
    assert signals_cli.listeners(cfg, a, False) == [(a, testers.folder(a))]
    assert signals_cli.listeners(cfg, None, True) == [(a, testers.folder(a)), (b, testers.folder(b)), ("own", own)]
    assert [x for x, _ in signals_cli.listeners({**cfg, "demo": {"enabled": True}}, None, True)] == [a, b]
    monkeypatch.setenv("WL_DEMO", "1")
    assert [x for x, _ in signals_cli.listeners(cfg, None, True)] == [a, b]
    for bad in ("t-nobody", "../t-ann", gone):
        with pytest.raises(StageError, match="No tester"):
            signals_cli.listeners(cfg, bad, False)
    monkeypatch.delenv("WL_DEMO")
    assert cli.main(["signals", "run", "t-nobody"]) == 1
    assert "Stopped: No tester t-nobody" in capsys.readouterr().out
    assert cli.main(["signals", "fetch"]) == 0                       # the active listener has no folder yet
    assert f"own: no folder at {own}" in capsys.readouterr().out
    assert cli.main(["signals", "run", "--all"]) == 0
    out = capsys.readouterr().out
    assert f"{a} ({testers.folder(a)}): 0 turn records in 0 conversations" in out and gone not in out


def test_measure_run_is_what_the_cli_runs(cfg, tmp_path, monkeypatch, capsys):
    lid = make_tester(cfg, tmp_path, intro_mp3=False)
    seen = []
    real = measure.run
    monkeypatch.setattr(measure, "run",
                        lambda folder, c, force=False: seen.append(force) or real(folder, c, force=force))
    assert cli.main(["signals", "run", lid, "--force"]) == 0
    assert seen == [True]
    assert "no audio for live_i (wl signals fetch)" in capsys.readouterr().out
    assert json.loads(store.path(testers.folder(lid), syn.GUIDE).read_text().splitlines()[0])["source"] == "recording"
