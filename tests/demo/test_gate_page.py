"""The gate (page/gate.html) and the interviewer's client (page/interview.js) in headless Chromium.

A bare FastAPI app serves the gate at /, mounts demo/interviewer.py for real, and stands in for part B's
/api/demo/consent and /app and for part D's next screen (/next.js, window.Next.handoff). GPT-Live is the stub
from tests/live/test_live_page.py: RTCPeerConnection is replaced, every client event is recorded
(window.__sent) and the test plays GPT-Live's side (window.__emit). Nothing reaches OpenAI.
"""

import json
import re
import socket
import sys
import threading
import time
import types
import urllib.request

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response

import demo
from demo import interviewer as IV
from stages import common
from stages.common import ROOT
from voice.gpt_live import session as live_session

sync_api = pytest.importorskip("playwright.sync_api")

# Acceptance check 3: no product wording in the gate or its client (text, comments, ids, classes, URLs).
PRODUCT = re.compile(r"chapter|library|guide|listen|lesson|story|stories|podcast|research|episode|narrat|"
                     r"overnight|curriculum|topic|audio chapter", re.I)

STUB = r"""
(() => {
  window.__sent = [];
  window.__channels = [];
  window.__micDenied = false;
  window.__mics = 0;
  const emitTo = (ch, ev) => { if (ch.onmessage) ch.onmessage({ data: JSON.stringify(ev) }); };
  window.__emit = (ev) => emitTo(window.__channels[window.__channels.length - 1], ev);
  class FakeChannel {
    constructor(label, k) { this.label = label; this.k = k; this.readyState = "open"; this.onmessage = null; }
    send(s) { window.__sent.push({ ch: this.k, ...JSON.parse(s) }); }
    close() { this.readyState = "closed"; }
  }
  class FakePC {
    constructor() { this.iceGatheringState = "complete"; this.connectionState = "new"; this.tracks = [];
                    window.__pcs = (window.__pcs || []).concat([this]); }
    addTrack(t, s) { this.tracks.push(t); }
    addTransceiver() {}
    createDataChannel(label) { this.channel = new FakeChannel(label, window.__channels.length); window.__channels.push(this.channel); return this.channel; }
    async createOffer() { return { type: "offer", sdp: "OFFER" }; }
    async setLocalDescription(d) { this.localDescription = d; }
    async setRemoteDescription(d) {
      this.remote = d; const ch = this.channel;
      setTimeout(() => emitTo(ch, { type: "session.started", session: { id: "sess_" + ch.k } }), window.__startDelay || 30);
    }
    close() { this.closed = true; if (this.channel) this.channel.readyState = "closed"; }
  }
  window.RTCPeerConnection = FakePC;
  const ctx = new AudioContext();
  const dest = ctx.createMediaStreamDestination();
  navigator.mediaDevices.getUserMedia = async () => {
    window.__mics++;
    if (window.__micDenied) throw new DOMException("denied", "NotAllowedError");
    window.__stream = new MediaStream([dest.stream.getAudioTracks()[0].clone()]);
    return window.__stream;
  };
})();
"""

# The next part: `hold` when the handover is near (it gets ready), then the handover itself with the name.
NEXT_JS = """window.Next = {
  handoff(h) {
    const rec = { name: h.name === undefined ? null : h.name, same: h.stream === window.__stream,
                  live: h.stream.getAudioTracks()[0].readyState };
    if (h.hold) window.__held = { ...rec, at: Date.now(), gate: !!document.getElementById("w-gate"),
                                  open: !!(window.Interview.I.sid) };
    else window.__handed = { ...rec, at: Date.now() };
  },
  spot() { return { x: 40, y: 60, w: 156, h: 156, color: "rgb(1, 2, 3)" }; },
};"""


@pytest.fixture
def site(cfg, monkeypatch, own_listeners):
    import uvicorn

    monkeypatch.setenv(common.LISTENER_ENV, "t-sam")
    (own_listeners / "t-sam").mkdir(parents=True)
    fake = types.SimpleNamespace(names=[], updates=[])
    fake.set_name = lambda lid, name: fake.names.append((lid, name))
    fake.update = lambda lid, **f: fake.updates.append((lid, f))
    monkeypatch.setitem(sys.modules, "demo.testers", fake)
    monkeypatch.setattr(demo, "testers", fake, raising=False)
    monkeypatch.setattr(live_session, "create_webrtc", lambda cfg, session, sdp, client=None: {"id": "live_1", "sdp": "ANSWER"})
    monkeypatch.setattr(IV, "_after_end", lambda *a: None)
    cfg.setdefault("demo", {})["interviewer"] = {
        **(cfg.get("demo", {}).get("interviewer") or {}), "scripts": ["/next.js"], "entry": "Next",
        "page": {"line_quiet_ms": 200, "close_quiet_ms": 300, "close_min_ms": 100, "close_max_ms": 4000,
                 "act_grace_ms": 400, "greet_fallback_ms": 100000}}
    consents = []
    app = FastAPI()

    @app.get("/")
    def gate() -> HTMLResponse:
        return HTMLResponse((ROOT / "page" / "gate.html").read_text(encoding="utf-8"))

    @app.post("/api/demo/consent")
    def consent() -> dict:
        consents.append(time.time())
        return {"ok": True}

    @app.get("/app")
    def next_screen() -> dict:
        return {"style": "#next { color: rgb(1, 2, 3); }", "body": '<main id="next">Next screen</main>'}

    @app.get("/next.js")
    def next_js() -> Response:
        return Response(NEXT_JS, media_type="text/javascript")

    IV.mount(app, cfg)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    deadline = time.monotonic() + 10
    while not srv.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield types.SimpleNamespace(base=f"http://127.0.0.1:{port}", consents=consents, testers=fake,
                                study=own_listeners / "t-sam" / "study")
    srv.should_exit = True


class Page:
    def __init__(self, base):
        self.pw = sync_api.sync_playwright().start()
        self.browser = self.pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        self.page = self.browser.new_context().new_page()
        self.posts = []
        self.page.on("request", lambda r: r.method == "POST" and self.posts.append(
            (r.url.split("://", 1)[1].split("/", 1)[1], json.loads(r.post_data or "{}"))))
        self.page.add_init_script(STUB)
        self.page.goto(base + "/")
        self.page.wait_for_function("window.Interview && window.Welcome")

    def close(self):
        self.browser.close()
        self.pw.stop()

    def js(self, code, arg=None):
        return self.page.evaluate(code, arg)

    def wait(self, cond, timeout=6000):
        self.page.wait_for_function(f"() => !!({cond})", timeout=timeout)

    def emit(self, ev):
        self.js("(e) => window.__emit(e)", ev)

    def say(self, text):
        for w in text.split():
            self.emit({"type": "session.output_transcript.delta", "delta": " " + w})

    def tool(self, name, args=None):
        self.emit({"type": "response.event", "event": {"type": "response.output_item.done", "item": {
            "type": "function_call", "call_id": f"call_{name}_{time.time_ns()}", "name": name,
            "arguments": json.dumps(args or {})}}})

    def sent(self, type_):
        return [e for e in self.js("window.__sent") if e["type"] == type_]

    def posted(self, path):
        return [b for p, b in self.posts if p == path]


@pytest.fixture
def page(site):
    p = Page(site.base)
    yield p
    p.close()


def wait_for(cond, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    raise AssertionError("timed out")


def started(page):
    page.page.click("#w-gate")
    page.wait("window.Interview.I.sid && window.Interview.I.started && !window.Interview.I.connecting")


def test_tap_consents_starts_the_intro_and_hands_over_with_the_name(site, page):
    assert page.page.inner_text("#w-tap") == "Tap to begin"
    started(page)
    assert len(site.consents) == 1 and page.js("window.__mics") == 1
    start = page.posted("api/interview/start")
    assert start == [{"mode": "intro", "sdp": "OFFER"}]
    # It speaks first: the opening note goes as speakable context once the session starts.
    wait_for(lambda: page.sent("session.commentary.append"))
    assert page.sent("session.commentary.append")[0]["content"].startswith("Begin now")
    assert page.js("document.body.classList.contains('w-on')")
    page.wait("getComputedStyle(document.getElementById('w-dot')).backgroundColor === 'rgb(107, 99, 92)'")
    page.say("Thanks so much for making the time. What's your name?")
    page.wait("document.body.classList.contains('w-speaking')")
    page.page.wait_for_function("() => !document.body.classList.contains('w-speaking')", timeout=4000)
    page.emit({"type": "session.input_transcript.delta", "delta": "I'm Sam."})
    page.tool("set_listener_name", {"name": "Sam"})
    wait_for(lambda: site.testers.names == [("t-sam", "Sam")])
    page.say("Sam, good. I'd like a few minutes with you afterwards.")
    page.tool("handoff_to_guide")
    page.say("I'll hand you over now.")
    page.wait("window.__handed", timeout=8000)
    handed = page.js("window.__handed")
    assert {k: handed[k] for k in ("name", "same", "live")} == {"name": "Sam", "same": True, "live": "live"}  # the mic goes on, unstopped
    outputs = [json.loads(e["item"]["output"]) for e in page.sent("response.item.create")]
    assert outputs[-1] == {"handed_over": True, "name": "Sam"}  # act, scripts, entry are the page's alone
    assert page.sent("session.close") and page.posted("api/interview/usage") and page.posted("api/interview/end")
    assert page.posted("api/interview/end")[-1]["how"] == "handover"
    # The next screen came into this same document; the gate warmed and then went.
    assert page.js("getComputedStyle(document.getElementById('next')).color") == "rgb(1, 2, 3)"
    assert page.js("document.body.classList.contains('tester') && document.body.classList.contains('w-warm')")
    assert page.js("!document.getElementById('w-base')")
    # The dot grows into exactly the next screen's circle (place, size, colour), then the gate goes: no jump.
    page.wait("""(() => { const r = document.getElementById('w-dot').getBoundingClientRect();
      return Math.abs(r.left - 40) < 1 && Math.abs(r.top - 60) < 1 && Math.abs(r.width - 156) < 1; })()""", timeout=4000)
    assert page.js("getComputedStyle(document.getElementById('w-dot')).backgroundColor") == "rgb(1, 2, 3)"
    page.wait("!document.getElementById('w-gate')", timeout=3000)
    assert site.testers.updates and site.testers.updates[0][0] == "t-sam"
    rec = [json.loads(x) for x in next(site.study.glob("intro-*.jsonl")).read_text().splitlines()]
    said = [(x["who"], x.get("text")) for x in rec if x["who"] in ("listener", "interviewer")]
    assert ("listener", "I'm Sam.") in said and said[0][0] == "interviewer"
    assert rec[-1]["who"] == "session" and rec[-1]["ended"] == "handover"


def test_mic_refused_says_so_and_tries_again(site, page):
    page.js("window.__micDenied = true")
    page.page.click("#w-gate")
    page.wait("document.body.classList.contains('w-refused')")
    assert page.page.is_visible("#w-mic") and not page.page.is_visible("#w-tap")
    assert page.page.inner_text("#w-mic").startswith("I need your microphone to talk with you")
    assert not site.consents and not page.posted("api/interview/start")
    page.js("window.__micDenied = false")
    page.page.click("#w-again")
    page.wait("window.Interview.I.started")
    assert page.js("window.__mics") == 2 and len(site.consents) == 1
    assert not page.js("document.body.classList.contains('w-refused')")


def test_handover_said_without_the_tool_is_acted_on_by_the_page(site, page):
    started(page)
    page.say("Good to meet you, Sam. I'll hand you over now.")
    page.wait("window.__handed", timeout=8000)
    tool = page.posted("api/interview/tool")
    assert tool == [{"sid": tool[0]["sid"], "name": "handoff_to_guide", "args": {}, "by": "page"}]
    assert page.js("window.__handed.name") is None  # no name was saved: the next part asks


def test_the_next_part_is_got_ready_while_the_handover_words_are_said(site, page):
    """Ian's notes (2026-11-16): about 20 s from the interviewer's last words to the next voice. The page waited
    for the line to end (0.9 s), then for a tool call that never came (3.5 s), then for quiet (1.4 s), then
    2 s for the dot, then fetched the next screen and its scripts, and only then began to connect. Now the
    next screen and scripts are in memory from the tap, the page calls the tool as the words are said, the
    next part opens (held) at once, and the handover comes once both sides are quiet."""
    page.js("() => { window.__fetched = []; const f = window.fetch; window.fetch = (u, o) => { window.__fetched.push(String(u)); return f(u, o); }; }")
    started(page)
    page.wait("window.__fetched.includes('/app') && window.__fetched.includes('/next.js')")  # during the talk
    assert page.js("!window.Next && !document.getElementById('next')")  # in memory only: the document is as it was
    assert not PRODUCT.search(page.page.content())
    page.js("() => { Interview.I.S.act_grace_ms = 60000; Interview.I.S.close_quiet_ms = 60000; Interview.I.S.handover_quiet_ms = 300; }")
    page.say("Good to meet you, Sam. I'll hand you over")
    t0 = page.js("Date.now()")
    page.wait("window.__held", timeout=3000)
    held = page.js("window.__held")
    assert held["at"] - t0 < 1000 and held["gate"] and held["open"]  # while the welcome's session is still open
    assert held["same"] and held["live"] == "live" and not page.js("window.__handed")
    assert page.js("document.body.classList.contains('w-warm') && !!document.getElementById('next')")
    assert page.js("window.__fetched.filter((u) => u === '/app' || u === '/next.js').length") == 2  # not fetched again
    assert not page.js("[...document.scripts].some((s) => (s.src || '').endsWith('/next.js'))")  # run from memory
    page.say("now.")
    page.wait("window.__handed", timeout=3000)
    handed = page.js("window.__handed")
    assert 250 < handed["at"] - held["at"] < 2500  # after the quiet, not after act_grace_ms or close_quiet_ms
    tool = page.posted("api/interview/tool")
    assert [(t["name"], t["by"]) for t in tool] == [("handoff_to_guide", "page")]
    wait_for(lambda: page.posted("api/interview/end") and page.posted("api/interview/end")[-1]["how"] == "handover")


def test_nothing_is_timed_before_the_session_has_started(site, page):
    """A regression: the clocks counted from 0 while it connected, and the intro was handed over at once."""
    page.js("window.__startDelay = 800")
    page.page.click("#w-gate")
    page.wait("window.Interview.I.started && window.Interview.I.startedAt")
    time.sleep(0.3)
    page.js("1")  # let the page's posts be seen
    assert not page.posted("api/interview/tool") and not page.js("window.__handed")
    assert not page.sent("session.instructions.append")


def test_a_dropped_intro_goes_back_to_the_dot(site, page):
    started(page)
    page.emit({"type": "session.closed", "reason": "server_error"})
    page.wait("!document.body.classList.contains('w-on') && !window.Interview.busy()")
    assert page.posted("api/interview/end")[-1]["how"] == "dropped"
    assert not page.js("window.__handed")
    page.page.click("#w-gate")  # tap again: a new intro
    page.wait("window.Interview.I.started && window.Interview.I.sid")
    assert len(page.posted("api/interview/start")) == 2


def test_debrief_ends_after_the_thanks_and_reports_done(site, page):
    page.js("""async () => { const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      await Interview.start("debrief", { stream: s, onDone: (x) => { window.__done = x; } }); }""")
    page.wait("window.Interview.I.started && window.Interview.I.sid")
    assert page.posted("api/interview/start")[-1]["mode"] == "debrief"
    page.say("Is there anything I didn't ask that I should have?")
    time.sleep(0.4)
    page.emit({"type": "session.input_transcript.delta", "delta": "No, that's all."})
    time.sleep(0.4)
    page.say("Thank you, Sam. That really helps.")
    page.tool("end_debrief")
    page.wait("window.__done", timeout=6000)
    assert page.js("window.__done") == {"ok": True, "how": "done"}
    assert json.loads((site.study / "debrief.json").read_text())["done"]


def test_curly_apostrophes_still_close_the_debrief_on_the_thanks(site, page):
    """Review: GPT-Live writes didn’t; the closing question never matched, the thanks never closed it and
    debrief.json stayed not done (the card came back for ever)."""
    page.js("""async () => { const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      await Interview.start("debrief", { stream: s, onDone: (x) => { window.__done = x; } }); }""")
    page.wait("window.Interview.I.started && window.Interview.I.sid")
    page.js("() => { Interview.I.S.closing_question = String.raw`\\banything I didn'?t ask\\b`; }")  # a config's old pattern
    page.say("Is there anything I didn’t ask that I should have?")
    time.sleep(0.4)
    page.emit({"type": "session.input_transcript.delta", "delta": "No, that’s all."})
    time.sleep(0.4)
    page.say("Thank you, Sam. That really helps.")  # and no end_debrief: the page closes after act_grace_ms
    page.wait("window.__done", timeout=6000)
    tool = page.posted("api/interview/tool")
    assert tool and tool[-1]["name"] == "end_debrief" and tool[-1]["by"] == "page"
    assert json.loads((site.study / "debrief.json").read_text())["done"]


def test_no_product_words_in_the_gate_or_its_client(site, page):
    for path in ("/", "/interview.js"):
        text = urllib.request.urlopen(site.base + path).read().decode()
        assert not PRODUCT.search(text), (path, PRODUCT.search(text))
    for f in ("gate.html", "interview.js"):
        text = (ROOT / "page" / f).read_text(encoding="utf-8")
        assert not PRODUCT.search(text), (f, PRODUCT.search(text))
    started(page)  # and the live document, once the talk has begun
    assert not PRODUCT.search(page.page.content())
