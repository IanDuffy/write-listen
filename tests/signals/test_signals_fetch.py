"""signals/fetch.py and session.recording's `why`: keeping the GPT-Live recordings (package 1)."""
import os
import shutil
import types
from datetime import datetime

import pytest

import signals_synth as syn
from signals import fetch as F
from signals import store
from stages.common import StageError
from voice.gpt_live import session as live_session

NOW = datetime(2026, 11, 30, 12, 0, 0)


def wav_bytes(tmp_path) -> bytes:
    return syn.wav(tmp_path / "_src.wav", 1.0, [(0.2, 0.6)], [(0.0, 0.1)]).read_bytes()


class Http:
    """OpenAI's /content: `have` maps live_id to the body it serves (200); anything else is a 404."""

    def __init__(self, have=None, on_get=None):
        self.have, self.calls, self.on_get = have or {}, [], on_get

    def get(self, url, headers=None, timeout=None, follow_redirects=None):
        live_id = url.rsplit("/", 2)[-2]
        self.calls.append(live_id)
        if self.on_get:
            self.on_get(live_id)
        if isinstance(self.have.get(live_id), Exception):
            raise self.have[live_id]
        body = self.have.get(live_id)
        return types.SimpleNamespace(status_code=200 if body else 404, content=body or b"")


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setattr(live_session, "api_key", lambda: "sk-test")


def visit(root):
    """A tester's folder: a guide conversation of three GPT-Live sessions (the first reused from a chapter's
    archive), a library session (never stored), an intro whose mp3 is kept and a debrief with nothing."""
    lst = root / "t-ruth"
    base = "2026-11-21T10:15:00"
    convo = [{"t": base, "who": "session", "names": ["Hanne"], "returning": False}]
    convo += syn.guide_session(base, "g1", 0, [(5, "Hi.", True)])
    convo += syn.guide_session(base, "g2", 100, [(5, "Back.", True)])
    convo += syn.guide_session(base, "g3", 200, [(5, "Again.", True)])
    syn.jsonl(lst / "sessions" / "20261121-101500.jsonl", convo)
    syn.jsonl(lst / "sessions" / "20261121-130000.jsonl",
              [{"t": "2026-11-21T13:00:00", "who": "session", "mode": "change", "live_id": "lib"}])
    syn.jsonl(lst / "sessions" / "log.jsonl", [{"ts": "x", "live_id": "nope"}])
    syn.jsonl(lst / "study" / "intro-20261121-101000.jsonl",
              syn.study_session("2026-11-21T10:10:00", "intro", "iv1", [(5, "Yes.", True)]))
    syn.jsonl(lst / "study" / "debrief-20261121-110000.jsonl",
              syn.study_session("2026-11-21T11:00:00", "debrief", "dv1", [(5, "Fine.", True)]))
    (lst / "study" / "iv1.mp3").write_bytes(b"ID3 lossy")
    syn.wav(lst / "chapters" / "20261121-heron" / "live" / "g1.wav", 1.0, [(0.2, 0.6)], [(0.0, 0.1)])
    return lst


def test_targets_are_every_stored_session_oldest_first(tmp_path):
    got = F.targets(visit(tmp_path))
    assert [(t["live_id"], t["part"], t["stem"]) for t in got] == [
        ("iv1", "intro", "intro-20261121-101000"), ("g1", "guide", "20261121-101500"),
        ("g2", "guide", "20261121-101500"), ("g3", "guide", "20261121-101500"),
        ("dv1", "debrief", "debrief-20261121-110000")]
    assert got[1]["t"] == "2026-11-21T10:15:00" and got[1]["path_hint"] == "sessions/20261121-101500.jsonl"
    assert F.targets(tmp_path / "nobody") == []


def test_fetch_keeps_reuses_downloads_and_says_why_not(tmp_path):
    lst = visit(tmp_path)
    body = wav_bytes(tmp_path)
    http = Http({"g2": body, "iv1": body, "g3": b"<html>not here</html>"})
    got = {r["live_id"]: r for r in F.fetch(lst, {"live": {"url": "https://x.test/v1/live/sessions"}}, client=http,
                                             now=NOW)}
    assert got["g1"]["status"] == "have" and got["g1"]["detail"] == "reused chapters/20261121-heron/live/g1.wav"
    assert os.path.samefile(store.audio_path(lst, "g1"), lst / "chapters/20261121-heron/live/g1.wav")   # a link
    assert got["g2"]["status"] == "fetched" and store.audio_path(lst, "g2").read_bytes() == body
    assert got["g2"]["bytes"] == len(body)
    assert got["iv1"]["status"] == "fetched" and F.is_wav(store.audio_path(lst, "iv1"))   # the lossless one too
    assert got["g3"]["status"] == "error" and "not a WAV" in got["g3"]["detail"]
    assert got["dv1"]["status"] == "missing"
    assert "HTTP 404" in got["dv1"]["detail"] and "9 days ago" in got["dv1"]["detail"]
    assert "30 days" in got["dv1"]["detail"] and "not stored" in got["dv1"]["detail"]
    assert "g1" not in http.calls and "lib" not in http.calls
    assert sorted(p.name for p in store.audio_dir(lst).iterdir()) == ["g1.wav", "g2.wav", "iv1.wav"]   # no .part
    # again: everything kept is kept, nothing asked twice
    http2 = Http()
    again = {r["live_id"]: r for r in F.fetch(lst, {"live": {"url": "https://x.test"}}, client=http2, now=NOW)}
    assert again["g2"] == {**got["g2"], "status": "have", "detail": "already kept"}
    assert sorted(http2.calls) == ["dv1", "g3"]


def test_the_mp3_is_the_fallback_and_old_sessions_are_named(tmp_path):
    lst = visit(tmp_path)
    got = {r["live_id"]: r for r in F.fetch(lst, {"live": {"url": "https://x.test"}}, client=Http(),
                                             now=datetime(2026, 12, 25))}
    assert got["iv1"]["status"] == "have" and got["iv1"]["detail"].startswith("only study/iv1.mp3 (lossy)")
    assert "past the 30 days" in got["dv1"]["detail"] and "34 days ago" in got["dv1"]["detail"]
    assert F.audio_for(lst, "iv1") == lst / "study" / "iv1.mp3"
    assert F.audio_for(lst, "g1") == store.audio_path(lst, "g1")


def test_no_key_and_network_errors(tmp_path, monkeypatch):
    lst = visit(tmp_path)

    def no_key():
        raise StageError("No OpenAI key: set OPENAI_API_KEY in .env")
    monkeypatch.setattr(live_session, "api_key", no_key)
    http = Http()
    got = {r["live_id"]: r for r in F.fetch(lst, {"live": {"url": "https://x.test"}}, client=http, now=NOW)}
    assert got["g1"]["status"] == "have"                                 # reuse needs no key
    assert got["g2"]["status"] == got["dv1"]["status"] == "error" and "No OpenAI key" in got["g2"]["detail"]
    assert got["iv1"]["status"] == "have" and "No OpenAI key" in got["iv1"]["detail"]
    assert http.calls == []
    monkeypatch.setattr(live_session, "api_key", lambda: "sk-test")
    got = {r["live_id"]: r for r in F.fetch(lst, {"live": {"url": "https://x.test"}},
                                             client=Http({"g2": ConnectionError("reset")}), now=NOW)}
    assert got["g2"]["status"] == "error" and got["g2"]["detail"] == "ConnectionError: reset"
    assert got["g3"]["status"] == "missing"                              # the others still tried


def test_a_folder_reset_mid_run_is_never_written_into(tmp_path):
    lst = visit(tmp_path)
    shutil.rmtree(lst / "chapters")
    cleared = tmp_path / "_cleared_x"

    def reset(live_id):                     # a tester reset while the first download is under way
        if live_id == "iv1":
            lst.rename(cleared)
    got = F.fetch(lst, {"live": {"url": "https://x.test"}}, client=Http({"iv1": wav_bytes(tmp_path)}, reset), now=NOW)
    assert {r["status"] for r in got} == {"error"} and len(got) == 5
    assert all("folder is gone" in r["detail"] for r in got)
    assert not lst.exists() and not (cleared / "signals").exists()


def test_recording_says_why():
    why = {}
    http = Http({})
    assert live_session.recording({"live": {"url": "https://x.test"}}, "zz", None, client=http, why=why) is None
    assert why == {"status": 404}
    # callers that don't ask are unchanged
    assert live_session.recording({"live": {"url": "https://x.test"}}, "zz", None, client=http) is None
