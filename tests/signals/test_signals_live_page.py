"""Live measuring on the guide's page (page/sig.js in page/live.js; the line route in voice/gpt_live/routes.py), in
headless Chromium against the real server, as tests/live/test_live_page.py runs it: GPT-Live is a stub (its
RTCPeerConnection replaced, every client event recorded, its events played by the test), the WebRTC call to OpenAI
is patched out, and no model is reached. The mic and the guide's voice are tones the test plays into the page's
streams, so the page's own analysers measure them.

With signals.live on, a spoken line's post carries `sig` and the session's `live_id`, and a turn record lands in
<listener>/signals/<stem>.jsonl; typed lines carry none. With it off the post and /live.js are as they were."""

import json
import socket
import threading
import time

import pytest

import server
from fakes import FakeAnthropic, Responder
from signals import store
from stages import common, llm, memory
from voice.gpt_live import routes as live_routes
from voice.gpt_live import session as S

sync_api = pytest.importorskip("playwright.sync_api")

# tests/live/test_live_page.py's stub: RTCPeerConnection replaced, the mic a MediaStreamDestination the test plays into.
STUB = r"""
(() => {
  window.__sent = [];
  window.__channels = [];
  window.__autoStart = true;
  window.__offers = 0;
  const emitTo = (ch, ev) => { if (ch.onmessage) ch.onmessage({ data: JSON.stringify(ev) }); };
  window.__emit = (ev) => emitTo(window.__channels[window.__channels.length - 1], ev);
  class FakeChannel {
    constructor(label, k) { this.label = label; this.k = k; this.readyState = "open"; this.onmessage = null; }
    send(s) { window.__sent.push({ ch: this.k, ...JSON.parse(s) }); }
    close() { this.readyState = "closed"; }
  }
  class FakePC {
    constructor() { this.iceGatheringState = "complete"; this.connectionState = "new"; this.localDescription = null;
                    this.tracks = []; this.channel = null; window.__pcs = (window.__pcs || []).concat([this]); }
    addTrack(t, s) { this.tracks.push(t); }
    addTransceiver(kind, init) { this.transceivers = (this.transceivers || []).concat([kind]); }
    createDataChannel(label) {
      this.channel = new FakeChannel(label, window.__channels.length);
      window.__channels.push(this.channel);
      return this.channel;
    }
    async createOffer() { window.__offers++; return { type: "offer", sdp: "OFFER" + window.__offers }; }
    async setLocalDescription(d) { this.localDescription = d; }
    async setRemoteDescription(d) {
      this.remote = d; this.connectionState = "connected";
      const ch = this.channel;
      if (window.__autoStart) setTimeout(() => emitTo(ch, { type: "session.started",
        session: { id: "sess_" + ch.k, expires_at: Date.now() / 1000 + 7200 } }), 30);
    }
    addEventListener() {}
    close() { this.connectionState = "closed"; if (this.channel) this.channel.readyState = "closed"; }
  }
  window.RTCPeerConnection = FakePC;
  const ctx = new AudioContext();
  const dest = ctx.createMediaStreamDestination();
  window.__mic = { ctx, dest };
  navigator.mediaDevices.getUserMedia = async () => new MediaStream([dest.stream.getAudioTracks()[0].clone()]);
})();
"""


def serve(cfg, tmp_path, monkeypatch, signals: bool):
    import uvicorn

    cfg["listener_dir"] = str(tmp_path / "listener")
    cfg["live"]["provider"] = "gpt_live"
    cfg["live"]["page"].update(line_quiet_ms=300, greet_fallback_ms=60000, hello_silence_secs=1000,
                               pitch_nudge_secs=[1000, 1000], ready_remind_secs=1000, offer_nudge=None,
                               pitch_hold={"explained_secs": 0, "question_secs": 0, "max_secs": 0},
                               aside_quiet_secs=1000, end_hold_secs=1000, talk_rest_secs=1000, wake_grace_secs=0,
                               engaged_turns=0, engaged_words=0)
    cfg["live"]["director"] = {**(cfg["live"].get("director") or {}), "enabled": False, "signals": False}
    cfg["signals"] = {**cfg["signals"], "live": signals}
    monkeypatch.setattr(llm, "_client", FakeAnthropic(Responder()))
    monkeypatch.setattr(memory, "after_session", lambda *a, **k: False)
    offers = []

    def webrtc(cfg, session, sdp, client=None):
        offers.append(session)
        return {"id": f"live_{len(offers)}", "sdp": "ANSWER"}
    monkeypatch.setattr(S, "create_webrtc", webrtc)
    runs = tmp_path / "runs"
    runs.mkdir()
    pipe = server.Pipeline(runs_dir=runs)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(server.create_app(pipe, cfg, remember_on_start=False), host="127.0.0.1",
                                        port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    deadline = time.monotonic() + 10
    while not srv.started and time.monotonic() < deadline:
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}", srv


class Page:
    def __init__(self, base):
        self.pw = sync_api.sync_playwright().start()
        self.browser = self.pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        self.page = self.browser.new_context().new_page()
        self.posts, self.errors = [], []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("request", lambda r: r.method == "POST" and self.posts.append(
            (r.url.split("://", 1)[1].split("/", 1)[1], json.loads(r.post_data or "{}"))))
        self.page.add_init_script(STUB)
        self.page.goto(base + "/")
        self.page.wait_for_function("window.Live && window.Align")
        self.ms = 0

    def close(self):
        self.browser.close()
        self.pw.stop()

    def js(self, code, arg=None):
        return self.page.evaluate(code, arg)

    def L(self, expr):
        return self.js(f"(() => {{ const L = window.Live.L; return {expr}; }})()")

    def wait(self, cond, timeout=8000):
        self.page.wait_for_function(f"(() => {{ const L = window.Live.L; return !!({cond}); }})()", timeout=timeout)

    def say(self, text):
        for w in text.split():
            self.js("(e) => window.__emit(e)", {"type": "session.output_transcript.delta", "delta": " " + w,
                                                "start_ms": self.ms, "end_ms": self.ms + 200})
            self.ms += 200

    def hear(self, text):
        self.js("(e) => window.__emit(e)", {"type": "session.input_transcript.delta", "delta": " " + text})

    def lines(self, who="listener"):
        return [b for p, b in self.posts if p == "api/live/line" and b.get("who") == who]

    def wait_lines(self, n, who="listener", timeout=6):
        deadline = time.monotonic() + timeout
        while len(self.lines(who)) < n and time.monotonic() < deadline:
            self.page.wait_for_timeout(50)  # Playwright delivers the request events while it runs
        got = self.lines(who)
        assert len(got) >= n, got
        return got


def connect(p):
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")


def sounds(p):
    """The mic and the guide's voice as two tones in the stub's AudioContext: the mic over a faint room, the guide's
    voice through a MediaStreamDestination handed to the page as GPT-Live's incoming track."""
    p.js("""() => {
      const m = window.__mic, c = m.ctx;
      const tone = (hz, dest, level) => { const o = c.createOscillator(), g = c.createGain();
        o.frequency.value = hz; g.gain.value = level; o.connect(g).connect(dest); o.start(); return g; };
      m.room = tone(440, m.dest, 0.002);
      m.voice = tone(440, m.dest, 0);
      const out = c.createMediaStreamDestination();
      m.guide = tone(220, out, 0);
      window.__pcs.at(-1).ontrack({ streams: [out.stream] });
    }""")


def play(p, steps):
    """Sample-accurate levels: [(who, level, at_seconds_from_now)], who "voice" (the mic) or "guide"."""
    p.js("""(steps) => { const m = window.__mic, t0 = m.ctx.currentTime + 0.05;
      for (const [who, v, at] of steps) m[who].gain.setValueAtTime(v, t0 + at); }""", steps)


def records(tmp_path, stem):
    return store.records(tmp_path / "listener", stem)


def wait_rows(tmp_path, stem, n, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            got = store.rows(tmp_path / "listener", stem)
        except ValueError:
            got = []
        if len(got) >= n:
            return got
        time.sleep(0.05)
    raise AssertionError(f"{n} rows never came: {store.rows(tmp_path / 'listener', stem)}")


@pytest.fixture
def on(cfg, tmp_path, monkeypatch):
    base, srv = serve(cfg, tmp_path, monkeypatch, True)
    pages = []

    def open_page():
        pages.append(Page(base))
        return pages[-1]
    yield open_page, base, cfg
    for p in pages:
        p.close()
    srv.should_exit = True


@pytest.fixture
def off(cfg, tmp_path, monkeypatch):
    base, srv = serve(cfg, tmp_path, monkeypatch, False)
    pages = []

    def open_page():
        pages.append(Page(base))
        return pages[-1]
    yield open_page, base, cfg
    for p in pages:
        p.close()
    srv.should_exit = True


def test_a_spoken_turn_is_measured_and_recorded_over_its_lines(on, tmp_path):
    open_page, base, cfg = on
    p = open_page()
    assert p.js("!!window.Sig && !!window.Live.S.signals") and p.L("!!L.sig")
    connect(p)
    sounds(p)
    p.js("() => { window.Live.S.engaged_merge_ms = 6000; }")  # two lines of one turn, however slow the machine
    # The guide speaks for 0.6 s; 0.5 s later the listener speaks for 0.8 s; their words come after.
    play(p, [("guide", 0.3, 0), ("guide", 0, 0.6), ("voice", 0.4, 1.1), ("voice", 0, 1.9)])
    p.say("Hi there, how was your day?")
    p.page.wait_for_timeout(2400)
    p.hear("It was good, thanks.")
    first = p.wait_lines(1)[0]
    stem = p.L("L.session")
    assert first["live_id"] == "live_1" and first["session"] == stem and first["text"] == "It was good, thanks."
    sig = first["sig"]
    assert sig["turn"] == 1 and sig["cut_in"] is False
    assert 250 <= sig["gap_ms"] <= 1200, sig
    assert 550 <= sig["dur_ms"] <= 1150 and sig["pauses"] == 0, sig
    assert sig["rms_db"] is not None and -30 < sig["rms_db"] < 0
    assert sig["start_s"] is not None and sig["end_s"] - sig["start_s"] == pytest.approx(sig["dur_ms"] / 1000, abs=0.02)
    assert sig["ago_ms"] >= sig["dur_ms"]
    rows = wait_rows(tmp_path, stem, 1)
    rec = rows[0]
    assert rec["source"] == "page" and rec["part"] == "guide" and rec["turn"] == 1 and rec["live_id"] == "live_1"
    assert rec["gap_ms"] == sig["gap_ms"] and rec["dur_ms"] == sig["dur_ms"] and rec["words"] == 4
    # The transcript keeps the line, never the measures.
    tr = [json.loads(x) for x in (tmp_path / "listener" / "sessions" / f"{stem}.jsonl").read_text().splitlines()]
    said = [r for r in tr if r.get("who") == "listener"]
    assert said[-1] == {"t": said[-1]["t"], "who": "listener", "text": "It was good, thanks.", "mode": "live"}

    # A second line of the same turn: the turn's measures so far, the gap still the first line's.
    play(p, [("voice", 0.4, 0.1), ("voice", 0, 0.5)])
    p.page.wait_for_timeout(800)
    p.hear("And you?")
    second = p.wait_lines(2)[1]["sig"]
    assert second["turn"] == 1 and second["gap_ms"] == sig["gap_ms"]
    assert second["dur_ms"] >= sig["dur_ms"] + 300, (sig, second)
    rows = wait_rows(tmp_path, stem, 2)
    (rec,) = records(tmp_path, stem)
    assert rec["turn"] == 1 and rec["text"] == "It was good, thanks. And you?" and rec["words"] == 6
    assert rec["dur_ms"] == second["dur_ms"]

    # The guide answers: the next spoken line is a new turn.
    p.say("Pretty good.")
    p.wait("L.lastGuideAt > L.turnEnd", timeout=4000)
    play(p, [("voice", 0.4, 0.1), ("voice", 0, 0.6)])
    p.page.wait_for_timeout(900)
    p.hear("Nice.")
    third = p.wait_lines(3)[2]["sig"]
    assert third["turn"] == 2 and p.L("L.sigTurn") == 2
    wait_rows(tmp_path, stem, 3)
    assert [r["turn"] for r in records(tmp_path, stem)] == [1, 2]
    assert not p.errors


def test_a_typed_line_carries_no_measures(on, tmp_path):
    open_page, base, cfg = on
    p = open_page()
    connect(p)
    p.page.click("#type")
    p.page.fill("#say", "Tell me about octopuses")
    p.page.click("#send")
    (body,) = p.wait_lines(1)
    assert body == {"session": p.L("L.session"), "who": "listener", "text": "Tell me about octopuses"}
    p.page.wait_for_timeout(300)
    assert not (tmp_path / "listener" / "signals").exists()
    assert p.L("L.sigTurn") == 0


def test_a_look_carries_the_turn_it_waits_for(on, tmp_path):
    open_page, base, cfg = on
    p = open_page()
    connect(p)
    asked = []

    def handle(route):
        body = json.loads(route.request.post_data or "{}")
        asked.append(body)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"id": body["id"], "read": "", "plan": [], "next": "", "changed": False, "kind": None, "about": None,
             "note": None}))
    p.page.route("**/api/live/direct", handle)
    p.js("""() => { window.Live.S.director = { enabled: true, every_secs: 1000, company_every_secs: 1000,
      every_words: 1000, every_lines: 1, silence_secs: [], timeout_ms: 1500, min_gap_secs: 0,
      fallback: { pitch_nudge_secs: [1000, 1000] } }; }""")
    p.say("Hi there!")
    p.wait("L.lastGuideAt")
    p.hear("Hi, I'm making salmon tonight.")
    deadline = time.monotonic() + 6
    while not asked and time.monotonic() < deadline:
        p.page.wait_for_timeout(50)
    assert asked and asked[0]["trigger"] == "turn_end" and asked[0]["sig_turn"] == 1


def test_off_the_post_and_the_script_are_as_before(off, tmp_path):
    open_page, base, cfg = off
    import urllib.request
    served = urllib.request.urlopen(base + "/live.js").read().decode()
    want = common.with_speaker(live_routes.LIVE_JS.read_text(encoding="utf-8").replace(
        "__LIVE_SETTINGS__", json.dumps(live_routes.page_settings(cfg))), cfg)
    assert served == want and "signals" not in live_routes.page_settings(cfg)
    assert "Sig.Meter = " not in served and "function Tracker" not in served
    p = open_page()
    assert p.js("window.Sig === undefined") and p.L("L.sig") is None
    connect(p)
    sounds(p)
    play(p, [("voice", 0.4, 0), ("voice", 0, 0.6)])
    p.page.wait_for_timeout(700)
    p.hear("It was good.")
    (body,) = p.wait_lines(1)
    assert body == {"session": p.L("L.session"), "who": "listener", "text": "It was good."}
    assert not (tmp_path / "listener" / "signals").exists()
    assert not p.errors


def test_the_page_settings_carry_signals_only_while_measuring(cfg):
    assert "signals" not in live_routes.page_settings(cfg)
    cfg["live"]["director"] = {**cfg["live"]["director"], "signals": True}   # the earpiece's note needs them
    got = live_routes.page_settings(cfg)["signals"]
    assert got["tick_ms"] == cfg["signals"]["page"]["tick_ms"] and got["pause_ms"] == cfg["signals"]["pause_ms"]
    assert got["merge_ms"] == cfg["live"]["page"]["engaged_merge_ms"] and got["bleed_tail_ms"] == 150
