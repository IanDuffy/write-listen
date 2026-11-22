"""Live measuring in the intro and the debrief (page/interview.js; the interviewer's line route in
demo/interviewer.py): a tester's line posted with `sig` becomes a turn record in their signals/ folder while the page
measures, and /interview.js carries page/sig.js only then, with no product words in it. Offline: the WebRTC call is
patched out and the routes are mounted on a bare app, as tests/demo/test_interviewer.py does; the page itself runs
on tests/demo/test_gate_page.py's gate, its stub and its server."""

import json
import re
import sys
import time
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import demo
from demo import interviewer as IV
from signals import store
from stages import common
from stages.common import ROOT
from voice.gpt_live import session as live_session

from test_gate_page import Page as GatePage  # tests/demo, on sys.path (tests/signals/conftest.py)
from test_gate_page import site, started  # noqa: F401  (site: the gate's server, a fixture)

# tests/demo/test_gate_page.py's acceptance check 3: nothing served with the welcome may name the product.
PRODUCT = re.compile(r"chapter|library|guide|listen|lesson|story|stories|podcast|research|episode|narrat|"
                     r"overnight|curriculum|topic|audio chapter", re.I)

SIG = {"turn": 1, "gap_ms": 640, "dur_ms": 1200, "speak_ms": 1200, "pauses": 0, "longest_pause_ms": 0,
       "cut_in": False, "rms_db": -24.5, "start_s": 3.2, "end_s": 4.4, "ago_ms": 2100}


@pytest.fixture
def cfg(cfg):
    """Measuring on (the gate's site reads cfg as it is; app_for sets it per test)."""
    cfg["signals"] = {**cfg["signals"], "live": True}
    return cfg


def app_for(cfg, monkeypatch, own_listeners, signals: bool):
    cfg["signals"] = {**cfg["signals"], "live": signals}
    monkeypatch.setenv(common.LISTENER_ENV, "t-sam")
    (own_listeners / "t-sam").mkdir(parents=True)
    fake = types.SimpleNamespace(set_name=lambda lid, name: None, update=lambda lid, **f: None)
    monkeypatch.setitem(sys.modules, "demo.testers", fake)
    monkeypatch.setattr(demo, "testers", fake, raising=False)
    monkeypatch.setattr(live_session, "create_webrtc", lambda cfg, session, sdp, client=None: {"id": "live_9", "sdp": "A"})
    monkeypatch.setattr(IV, "_after_end", lambda cfg, study, s: None)
    app = FastAPI()
    IV.mount(app, cfg)
    return TestClient(app), own_listeners / "t-sam"


def lines(p):
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def test_a_testers_lines_become_turn_records_while_measuring(cfg, monkeypatch, own_listeners):
    c, home = app_for(cfg, monkeypatch, own_listeners, True)
    got = c.post("/api/interview/start", json={"mode": "debrief", "sdp": "OFFER"}).json()
    sid, stem = got["sid"], f"debrief-{got['sid']}"
    assert got["settings"]["signals"]["tick_ms"] == cfg["signals"]["page"]["tick_ms"]
    assert got["settings"]["signals"]["merge_ms"] == cfg["live"]["page"]["engaged_merge_ms"]
    c.post("/api/interview/line", json={"sid": sid, "who": "out", "text": "What stood out?", "sig": SIG})
    c.post("/api/interview/line", json={"sid": sid, "who": "in", "text": "[inhale", "sig": SIG})
    c.post("/api/interview/line", json={"sid": sid, "who": "in", "text": "]The voice, I guess.",
                                        "sig": {**SIG, "dur_ms": 2500, "pauses": 1, "longest_pause_ms": 900}})
    c.post("/api/interview/line", json={"sid": sid, "who": "in", "text": "No numbers here.", "sig": {"gap_ms": 1}})
    rows = store.rows(home, stem)
    assert [(r["turn"], r["part"], r["source"], r["live_id"]) for r in rows] == [(1, "debrief", "page", "live_9")] * 2
    (rec,) = store.records(home, stem)
    assert rec["text"] == "[inhale]The voice, I guess." and rec["tags"] == ["inhale"] and rec["words"] == 4
    assert rec["hedges"] == 1 and rec["dur_ms"] == 2500 and rec["gap_ms"] == 640 and rec["start_s"] == 3.2
    # The study transcript keeps the lines as before: no measures in it, and the one without a turn is still there.
    said = [x for x in lines(home / "study" / f"{stem}.jsonl") if x["who"] in ("listener", "interviewer")]
    assert [x["text"] for x in said] == ["What stood out?", "[inhale", "]The voice, I guess.", "No numbers here."]
    assert all("sig" not in x for x in said)
    assert not (home / "sessions").exists()
    c.post("/api/interview/end", json={"sid": sid, "how": "done"})
    assert c.post("/api/interview/line", json={"sid": sid, "who": "in", "text": "late", "sig": SIG}).status_code == 409


def test_off_nothing_is_recorded_and_the_script_is_as_before(cfg, monkeypatch, own_listeners):
    c, home = app_for(cfg, monkeypatch, own_listeners, False)
    got = c.post("/api/interview/start", json={"mode": "intro", "sdp": "OFFER"}).json()
    assert "signals" not in got["settings"]
    c.post("/api/interview/line", json={"sid": got["sid"], "who": "in", "text": "I'm Sam.", "sig": SIG})
    assert not (home / "signals").exists()
    assert [x["text"] for x in lines(home / "study" / f"intro-{got['sid']}.jsonl") if x["who"] == "listener"] == ["I'm Sam."]
    served = c.get("/interview.js").text
    assert served == common.with_speaker(IV.INTERVIEW_JS.read_text(encoding="utf-8"), cfg) and "function Tracker" not in served


def test_the_script_carries_sig_js_with_no_product_words(cfg, monkeypatch, own_listeners):
    c, home = app_for(cfg, monkeypatch, own_listeners, True)
    served = c.get("/interview.js").text
    assert "function Tracker" in served and served.index("window.Speaker") < served.index("function Tracker") \
        < served.index("window.Interview")
    assert not PRODUCT.search(served), PRODUCT.search(served)
    for f in ("sig.js", "interview.js"):
        text = (ROOT / "page" / f).read_text(encoding="utf-8")
        assert not PRODUCT.search(text), (f, PRODUCT.search(text))


def test_the_earpiece_flag_alone_turns_measuring_on(cfg, monkeypatch, own_listeners):
    cfg["live"]["director"] = {**cfg["live"]["director"], "signals": True}
    c, home = app_for(cfg, monkeypatch, own_listeners, False)
    got = c.post("/api/interview/start", json={"mode": "intro", "sdp": "OFFER"}).json()
    assert "signals" in got["settings"]
    c.post("/api/interview/line", json={"sid": got["sid"], "who": "in", "text": "Yes, ready.", "sig": SIG})
    (rec,) = store.records(home, f"intro-{got['sid']}")
    assert rec["part"] == "intro" and rec["norm"] is None and "no norm yet" in rec["note"]


# The gate's stub keeps its mic to itself: this one is a tone the test plays, over a faint room.
MIC = r"""
(() => {
  const ctx = new AudioContext(), dest = ctx.createMediaStreamDestination();
  const tone = (hz, to, level) => { const o = ctx.createOscillator(), g = ctx.createGain();
    o.frequency.value = hz; g.gain.value = level; o.connect(g).connect(to); o.start(); return g; };
  window.__lv = { ctx, room: tone(440, dest, 0.002), voice: tone(440, dest, 0) };
  navigator.mediaDevices.getUserMedia = async () => {
    window.__mics++;
    window.__stream = new MediaStream([dest.stream.getAudioTracks()[0].clone()]);
    return window.__stream;
  };
})();
"""


def test_the_welcome_page_measures_a_testers_turns(site, tmp_path):  # noqa: F811
    p = GatePage(site.base, init=MIC)
    try:
        started(p)
        p.wait("window.Sig && window.Interview.I.sig")
        p.js("""() => { const v = window.__lv, out = v.ctx.createMediaStreamDestination();
          const o = v.ctx.createOscillator(), g = v.ctx.createGain(); o.frequency.value = 220; g.gain.value = 0;
          o.connect(g).connect(out); o.start(); v.out = g;
          window.__pcs.at(-1).ontrack({ streams: [out.stream] });
          const t0 = v.ctx.currentTime + 0.05;
          for (const [who, level, at] of [["out", 0.3, 0], ["out", 0, 0.6], ["voice", 0.4, 1.1], ["voice", 0, 1.9]])
            v[who].gain.setValueAtTime(level, t0 + at); }""")
        for k, w in enumerate("Hi, is this Sam?".split()):
            p.emit({"type": "session.output_transcript.delta", "delta": " " + w, "start_ms": 200 * k,
                    "end_ms": 200 * k + 200})
        p.page.wait_for_timeout(2400)
        p.emit({"type": "session.input_transcript.delta", "delta": "Yes, that's me."})
        p.wait("window.Interview.I.sig.turns.n === 1")
        deadline = time.monotonic() + 5
        while not [b for b in p.posted("api/interview/line") if b["who"] == "in"] and time.monotonic() < deadline:
            p.page.wait_for_timeout(50)
        (body,) = [b for b in p.posted("api/interview/line") if b["who"] == "in"]
        sig = body["sig"]
        assert body["live_id"] == "live_1" and sig["turn"] == 1 and sig["cut_in"] is False
        assert 250 <= sig["gap_ms"] <= 1200 and 550 <= sig["dur_ms"] <= 1150, sig
        assert sig["start_s"] is not None
        sid = p.js("window.Interview.I.sid")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not store.rows(site.study.parent, f"intro-{sid}"):
            time.sleep(0.05)
        (rec,) = store.records(site.study.parent, f"intro-{sid}")
        assert rec["part"] == "intro" and rec["turn"] == 1 and rec["gap_ms"] == sig["gap_ms"] and rec["words"] == 3
        # The voice's line ends the turn: their next line is turn 2.
        p.say("Great, thanks for coming.")
        p.page.wait_for_timeout(500)
        p.emit({"type": "session.input_transcript.delta", "delta": "Sure."})
        p.wait("window.Interview.I.sig.turns.n === 2")
    finally:
        p.close()
