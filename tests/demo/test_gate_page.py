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
                 "act_grace_ms": 400, "greet_fallback_ms": 100000,
                 "handover_min_ms": 0}}  # the opening's guard is off unless a test sets it (the tester must still have spoken)
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
        # A Playwright call first: the sync API delivers the page's request events only during one, so a wait that
        # only slept and read self.posts could never see a post made after the last call (the end's post, behind
        # the handover, timed out under load however long the wait).
        self.js("1")
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


def heard(page, text="Yes, that's me."):
    """The tester says something: the page acts on the handover's words only after they have."""
    page.emit({"type": "session.input_transcript.delta", "delta": text})


def test_tap_consents_starts_the_intro_and_hands_over_with_the_name(site, page):
    assert page.page.inner_text("#w-tap") == "Tap to begin"
    started(page)
    assert len(site.consents) == 1 and page.js("window.__mics") == 1
    start = page.posted("api/interview/start")
    assert start == [{"mode": "intro", "sdp": "OFFER"}]
    # It speaks first: the opening note goes as speakable context once the session starts.
    wait_for(lambda: page.sent("session.commentary.append"))
    assert page.sent("session.commentary.append")[0]["content"].startswith("Begin now")
    assert page.js("document.getElementById('w-gate').classList.contains('w-on')")
    page.wait("getComputedStyle(document.getElementById('w-dot')).backgroundColor === 'rgb(107, 99, 92)'")
    page.say("Thanks so much for making the time. What's your name?")
    page.wait("document.getElementById('w-gate').classList.contains('w-speaking')")
    page.page.wait_for_function("() => !document.getElementById('w-gate').classList.contains('w-speaking')", timeout=4000)
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
    assert page.js("document.body.classList.contains('tester') && document.getElementById('w-gate').classList.contains('w-warm')")
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


def test_the_three_words_stay_gone_when_the_next_screen_rewrites_the_bodys_classes(site, page):
    """Ian's second run (2026-11-18): a "tap" label was visible at the handover. The next screen's script sets
    <body>'s whole class list as it draws; this screen kept its state there, so "Tap to begin" faded back in over
    the dot and the dot lost its two seconds of growing. Its state is on #w-gate now."""
    started(page)
    opacity = "getComputedStyle(document.getElementById('w-tap')).opacity"
    page.wait(f"{opacity} === '0'")
    page.js("() => { document.body.className = 'idle connecting talking'; }")  # as the next screen does
    page.page.wait_for_timeout(1000)  # the fade back took 0.8 s
    assert page.js(opacity) == "0"
    assert page.js("document.getElementById('w-gate').classList.contains('w-on')")
    heard(page)
    page.say("Good to meet you, Sam. I'll hand you over now.")
    page.wait("window.__held", timeout=3000)
    page.js("() => { document.body.className = 'idle talk'; }")
    page.page.wait_for_timeout(300)
    assert page.js("document.getElementById('w-gate').classList.contains('w-warm')")
    assert page.js(opacity) == "0"
    assert "2s" in page.js("getComputedStyle(document.getElementById('w-dot')).transitionDuration")  # still growing
    page.wait("getComputedStyle(document.getElementById('w-note')).opacity === '0'", timeout=3000)
    page.wait("window.__handed", timeout=8000)


def test_mic_refused_says_so_and_tries_again(site, page):
    page.js("window.__micDenied = true")
    page.page.click("#w-gate")
    page.wait("document.getElementById('w-gate').classList.contains('w-refused')")
    assert page.page.is_visible("#w-mic") and not page.page.is_visible("#w-tap")
    assert page.page.inner_text("#w-mic").startswith("I need your microphone to talk with you")
    assert not site.consents and not page.posted("api/interview/start")
    page.js("window.__micDenied = false")
    page.page.click("#w-again")
    page.wait("window.Interview.I.started")
    assert page.js("window.__mics") == 2 and len(site.consents) == 1
    assert not page.js("document.getElementById('w-gate').classList.contains('w-refused')")


def test_handover_said_without_the_tool_is_acted_on_by_the_page(site, page):
    started(page)
    heard(page)
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
    heard(page)
    t0 = page.js("Date.now()")
    page.say("Good to meet you, Sam. I'll hand you over now.")
    page.wait("window.__held", timeout=3000)
    held = page.js("window.__held")
    assert held["at"] - t0 < 1000 and held["gate"] and held["open"]  # while the welcome's session is still open
    assert held["same"] and held["live"] == "live" and not page.js("window.__handed")
    assert page.js("document.getElementById('w-gate').classList.contains('w-warm') && !!document.getElementById('next')")
    assert page.js("window.__fetched.filter((u) => u === '/app' || u === '/next.js').length") == 2  # not fetched again
    assert not page.js("[...document.scripts].some((s) => (s.src || '').endsWith('/next.js'))")  # run from memory
    page.wait("window.__handed", timeout=3000)
    handed = page.js("window.__handed")
    assert 250 < handed["at"] - held["at"] < 2500  # after the quiet, not after act_grace_ms or close_quiet_ms
    tool = page.posted("api/interview/tool")
    assert [(t["name"], t["by"]) for t in tool] == [("handoff_to_guide", "page")]
    wait_for(lambda: page.posted("api/interview/end") and page.posted("api/interview/end")[-1]["how"] == "handover",
             timeout=15)  # the end is posted behind the handover (the session's close, then its cost); posted() lets it be seen


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
    page.wait("!document.getElementById('w-gate').classList.contains('w-on') && !window.Interview.busy()")
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


DEBRIEF_JS = """async () => { const s = await navigator.mediaDevices.getUserMedia({ audio: true });
  await Interview.start("debrief", { stream: s, onDone: (x) => { window.__done = x; } }); }"""
FAREWELL = "Thank you, Sam. That really helps Ian. That’s everything from me."  # as GPT-Live writes it


def test_the_farewell_closes_the_debrief_with_no_closing_question_and_no_tool(site, page):
    """Ian's note (2026-11-18): "there was no clear exit for the post product demo interview". He said "let's end
    the conversation here" and got the closing question, the page's only way out. Now the farewell is the end
    wherever it comes: the page acts on it after act_grace_ms when end_debrief hasn't."""
    page.js(DEBRIEF_JS)
    page.wait("window.Interview.I.started && window.Interview.I.sid")
    assert re.search(page.js("window.Interview.I.S.farewell"), FAREWELL, re.I)  # from the server's page settings
    page.say("Welcome back, Sam. What's one thing that stood out to you?")
    time.sleep(0.4)
    page.emit({"type": "session.input_transcript.delta", "delta": "The voice was flat at first. Let's end here."})
    time.sleep(0.4)
    page.say("Thank you, Sam. That really helps.")  # a thanks alone is not the end (no closing question was asked)
    time.sleep(1.0)  # line_quiet_ms 200, act_grace_ms 400
    assert page.posted("api/interview/tool") == [] and not page.js("window.__done")
    page.say("Of course. " + FAREWELL)
    page.wait("window.__done", timeout=6000)
    assert page.js("window.__done") == {"ok": True, "how": "done"}
    tool = page.posted("api/interview/tool")
    assert len(tool) == 1 and tool[0]["name"] == "end_debrief" and tool[0]["by"] == "page"
    state = json.loads((site.study / "debrief.json").read_text())
    assert state["done"] and state["farewell"] and state["closing"] is False


def test_the_farewell_with_the_tool_closes_once_and_nothing_tells_it_to_speak_again(site, page):
    """After the farewell the tester may add a word ("bye!") and wait: the page must never answer that with the
    `waiting` note or a clock note, which would have the voice start talking again."""
    page.js(DEBRIEF_JS)
    page.wait("window.Interview.I.started && window.Interview.I.sid")
    page.js("() => { const S = Interview.I.S; S.reply_wait_ms = 300; S.act_grace_ms = 4000; S.close_quiet_ms = 2500; "
            "S.close_min_ms = 2500; S.debrief_target_minutes = 0.01; S.debrief_max_minutes = 0.02; }")
    page.say("What's one thing that stood out to you?")
    time.sleep(0.4)
    page.emit({"type": "session.input_transcript.delta", "delta": "It was calm. That's about it."})
    time.sleep(0.3)
    page.say(FAREWELL)
    time.sleep(0.4)
    before = len(page.sent("session.instructions.append"))
    page.emit({"type": "session.input_transcript.delta", "delta": "Thanks, bye!"})
    time.sleep(1.6)  # well past reply_wait_ms and both clock notes, inside the act's grace: nothing is sent
    assert page.js("window.Interview.I.farewellAt") and not page.js("window.__done")
    assert len(page.sent("session.instructions.append")) == before
    page.tool("end_debrief")  # the tool arrives inside the grace: one call, by the backend, and the page's is dropped
    page.wait("window.__done", timeout=8000)
    assert page.js("window.__done") == {"ok": True, "how": "done"}
    tool = page.posted("api/interview/tool")
    assert [x.get("by") for x in tool] == [None] and tool[0]["name"] == "end_debrief"
    assert len(page.sent("session.instructions.append")) == before
    assert json.loads((site.study / "debrief.json").read_text())["done"]


def test_the_farewell_ends_the_debrief_even_when_the_server_does_not_answer(site, page):
    page.js(DEBRIEF_JS)
    page.wait("window.Interview.I.started && window.Interview.I.sid")
    page.page.route("**/api/interview/tool", lambda r: r.abort())
    heard(page, "The voice was calm and the wait was long.")  # they answered: a debrief nobody answered is never done
    time.sleep(0.5)  # line_quiet_ms 200: their line is posted
    page.say(FAREWELL)
    page.wait("window.__done", timeout=6000)
    assert page.js("window.__done") == {"ok": True, "how": "done"}
    assert json.loads((site.study / "debrief.json").read_text())["done"]  # the server reads the farewell at the end


def test_a_debrief_nobody_answered_closes_on_the_farewell_but_is_not_done(site, page):
    """Review of the debrief's exit (2026-11-18): a tester who pressed End demo and walked away (or whose mic
    isn't heard) gets the opening question, then at time the clock's note asks for the farewell; the page closed
    and the server marked the debrief done with no word from them, so End demo never came back. The page still
    closes; the debrief stays not done, to be taken again."""
    page.js(DEBRIEF_JS)
    page.wait("window.Interview.I.started && window.Interview.I.sid")
    page.say("Welcome back, Sam. What's one thing that stood out to you?")
    time.sleep(0.4)
    page.say(FAREWELL)
    page.wait("window.__done", timeout=6000)
    tool = page.posted("api/interview/tool")
    assert len(tool) == 1 and tool[0]["name"] == "end_debrief" and tool[0]["by"] == "page"
    wait_for(lambda: (site.study / "debrief.json").exists())
    state = json.loads((site.study / "debrief.json").read_text())
    assert state["done"] is False and state["farewell"] and state["how"] == "no answer"


def test_a_farewell_cut_off_closes_nothing_and_the_waiting_note_never_says_carry_on(site, page):
    """Review of the debrief's exit (2026-11-18): the page keys on the farewell's last sentence, and a tester
    who speaks over it ("no problem, bye") leaves a line without it. Nothing closes, and the page's `waiting`
    note is what the voice gets next: the intro's ends "then carry on", which here is another question. The
    debrief's own note names the farewell."""
    page.js(DEBRIEF_JS)
    page.wait("window.Interview.I.started && window.Interview.I.sid")
    page.js("() => { Interview.I.S.reply_wait_ms = 500; }")
    page.say("Welcome back, Sam. What's one thing that stood out to you?")
    time.sleep(0.4)
    heard(page, "The voice was calm. Let's end here.")
    time.sleep(0.4)
    page.say("Thank you, Sam. That really helps Ian. That’s every")  # cut off by their next words
    time.sleep(0.1)
    heard(page, "No problem, bye.")
    notes = lambda: [e["content"] for e in page.sent("session.instructions.append") if "waiting for you" in e["content"]]  # noqa: E731
    wait_for(lambda: len(notes()) == 1)
    assert "carry on" not in notes()[0] and "your farewell, word for word" in notes()[0]
    assert not page.js("window.Interview.I.farewellAt") and not page.js("window.__done")
    assert page.posted("api/interview/tool") == []
    page.say(FAREWELL)  # said in full: now it closes
    page.wait("window.__done", timeout=6000)
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


def test_a_tester_left_waiting_has_the_voice_told_so(site, page):
    """Ian's debrief (2026-11-16): he asked "what's a different question" and got "Hmm." and then 77 seconds of
    nothing. The page tells the voice they're waiting: once after reply_wait_ms, once more at three times that,
    and not at all when it answered in words."""
    page.js("""async () => { const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      await Interview.start("debrief", { stream: s, onDone: (x) => { window.__done = x; } }); }""")
    page.wait("window.Interview.I.started && window.Interview.I.sid")
    page.js("() => { Interview.I.S.reply_wait_ms = 500; }")
    notes = lambda: [e["content"] for e in page.sent("session.instructions.append") if "waiting for you" in e["content"]]  # noqa: E731
    page.say("So take me through what that was like.")
    time.sleep(0.4)
    page.emit({"type": "session.input_transcript.delta", "delta": "What's a different question?"})
    time.sleep(0.3)
    page.say("Hmm.")  # a backchannel is not a reply
    wait_for(lambda: len(notes()) == 1)
    assert page.sent("session.input_audio.unmute")  # an instruction is only acted on with the mic open
    wait_for(lambda: len(notes()) == 2, timeout=4)
    time.sleep(2.0)
    assert len(notes()) == 2  # twice for one turn of theirs, never a stream of them
    page.emit({"type": "session.input_transcript.delta", "delta": "Hello?"})
    time.sleep(0.1)
    page.say("Sorry. What's one thing that stood out to you?")  # answered in words: nothing more is sent
    time.sleep(2.0)
    assert len(notes()) == 2
    said = [x for x in page.posted("api/interview/line") if x["who"] == "page"]
    assert sum("waiting for you" in x["text"] for x in said) == 2  # in the transcript, for whoever reads it later


def test_a_stall_is_no_reply_so_the_tester_is_still_said_to_be_waiting(site, page):
    """Ian's third run (2026-11-19): to "But what else?" it said "Just a second." and then "I'm still here.", three
    words each, which the page counted as replies: it never told the voice he was waiting. Now a line that only
    stalls is no reply; a real one still stops the notes."""
    page.js(DEBRIEF_JS)
    page.wait("window.Interview.I.started && window.Interview.I.sid")
    page.js("() => { Interview.I.S.reply_wait_ms = 500; }")
    notes = lambda: [e["content"] for e in page.sent("session.instructions.append") if "waiting for you" in e["content"]]  # noqa: E731
    page.say("Welcome back, Sam. What's one thing that stood out to you?")
    time.sleep(0.4)
    heard(page, "The pacing. But what else?")
    time.sleep(0.3)
    page.say("Just a second.")
    wait_for(lambda: len(notes()) == 1)
    page.say("I’m still here.")  # curly, as GPT-Live writes it
    wait_for(lambda: len(notes()) == 2, timeout=4)
    heard(page, "Where did you go?")
    time.sleep(0.1)
    page.say("Right here, still with you.")
    wait_for(lambda: len(notes()) == 3, timeout=4)
    heard(page, "Hello?")
    time.sleep(0.1)
    page.say("Sorry. What would you change first?")  # a reply: nothing more
    time.sleep(2.0)
    assert len(notes()) == 3


def test_a_turn_left_hanging_gets_one_note_and_an_invitation_to_go_on_gets_none(site, page):
    """Ian's third run: "Yeah, I follow you, and that's useful to hear." and then 9.5 s of quiet on both sides
    until he asked "But what else?". After hanging_ms of quiet the page asks once for its next question or its
    farewell. Never after "Sorry, go on." (it is their turn), a question, or while they talk."""
    page.js(DEBRIEF_JS)
    page.wait("window.Interview.I.started && window.Interview.I.sid")
    page.js("() => { const S = Interview.I.S; S.reply_wait_ms = 300; S.hanging_ms = 700; S.note_gap_ms = 1500; }")
    hanging = page.js("Interview.I.notes.hanging")
    assert hanging and "waiting for you" not in hanging
    notes = lambda: [e["content"] for e in page.sent("session.instructions.append") if e["content"] == hanging]  # noqa: E731
    page.say("Welcome back, Sam. What's one thing that stood out to you?")
    time.sleep(0.4)
    heard(page, "It jumped in too soon. There should be something listening on top of it.")
    time.sleep(0.3)
    page.say("Sorry, go on.")  # an invitation: their turn, never a turn left hanging
    time.sleep(1.5)
    assert notes() == []
    heard(page, "That's the idea. Do you see what I mean?")
    time.sleep(0.3)
    page.say("Yeah, I follow you, and that's useful to hear.")
    wait_for(lambda: len(notes()) == 1, timeout=4)
    # The clock's note, due now, waits note_gap_ms behind it rather than stacking on it.
    hung_at = page.js("() => { Interview.I.S.debrief_target_minutes = 0.001; return Interview.I.noteAt; }")
    wait_for(lambda: any("at time" in e["content"] for e in page.sent("session.instructions.append")), timeout=4)
    assert page.js("Interview.I.noteKind") == "time" and page.js("Interview.I.noteAt") - hung_at >= 1500
    time.sleep(0.5)
    assert len(notes()) == 1  # once for that line
    page.say("What would you change first?")  # a question: nothing hangs
    time.sleep(1.5)
    assert len(notes()) == 1


def test_saying_someone_else_comes_next_is_not_the_handover(site, page):
    """The intro now explains how this will go; "I'll hand you over to someone else" in that explanation would
    have counted as the handover itself (the page acts on the words when no tool call follows)."""
    started(page)
    heard(page)
    # None of these is the handover: it explains that one is coming, or asks before it (Ian's intro now says how
    # this will go and takes questions; the looser pattern ended it at "before I hand you over").
    for line in ("In a moment I'll hand you over to someone else, and you'll talk a little.",
                 "Any questions before I hand you over?", "Before I hand you over, any questions?",
                 "In a moment I'll hand you over.", "I'll hand you over whenever you're ready."):
        page.say(line)
        time.sleep(0.9)  # line_quiet_ms 200, act_grace_ms 400
        assert page.posted("api/interview/tool") == [] and page.js("window.__handed") is None, line
    page.say("I'll hand you over now.")
    page.wait("window.__handed")
    assert page.posted("api/interview/tool")[-1]["by"] == "page"


def test_the_handover_words_do_nothing_in_the_intros_opening(site, page):
    """The page acts on the words the moment they are heard, so they must never end the intro in its opening:
    not before the tester has spoken, and not before handover_min_ms. The tool itself still hands over."""
    started(page)
    page.js("() => { Interview.I.S.handover_min_ms = 1500; }")
    page.say("Hi, is this Sam? I'll hand you over now.")  # nobody has spoken yet, and it has only just begun
    time.sleep(0.9)
    assert page.posted("api/interview/tool") == [] and not page.js("window.__held")
    heard(page)
    page.say("Lovely. I'll hand you over now.")  # they have spoken, but the intro is under handover_min_ms old
    time.sleep(0.3)
    assert page.posted("api/interview/tool") == [] and not page.js("window.__held")
    page.wait("Date.now() - window.Interview.I.startedAt > 1600")
    time.sleep(0.6)
    assert page.posted("api/interview/tool") == [] and page.js("window.__handed") is None  # nothing was left armed
    page.say("Right. I'll hand you over now.")
    page.wait("window.__handed", timeout=5000)
    assert page.posted("api/interview/tool")[-1]["by"] == "page"


def test_the_handover_words_need_the_tester_to_have_spoken_but_the_tool_does_not(site, page):
    started(page)
    page.js("() => { Interview.I.S.handover_min_ms = 60000; }")  # (a silent tester is handed over at twice this)
    page.say("I'll hand you over now.")
    time.sleep(0.9)
    assert page.posted("api/interview/tool") == [] and page.js("window.__handed") is None
    page.tool("handoff_to_guide")  # the voice's own call is never held
    page.wait("window.__handed", timeout=8000)


def test_a_tester_who_never_speaks_is_still_handed_over_on_the_words_later(site, page):
    """Review: with the tester silent (a nod, a mic not transcribed) the words never handed over, and they waited
    in silence for the intro's 4-minute clock."""
    started(page)
    page.js("() => { Interview.I.S.handover_min_ms = 300; }")
    time.sleep(1.0)  # past twice the minimum, the tester still silent
    page.say("I'll hand you over now.")
    page.wait("window.__handed", timeout=8000)


def test_a_silent_testers_handover_words_said_early_are_acted_on_once_they_may_be(site, page):
    """Review of Ian's third run (2026-11-19): the 40-word intro says "I'll hand you over now." about 20-28 s in,
    before a silent tester could be handed over on the words (then 3x handover_min_ms, 30 s), and nothing looked
    again: they sat in silence until the 2-minute clock. Words said past handover_min_ms are held and acted on at
    twice it."""
    started(page)
    page.js("() => { Interview.I.S.handover_min_ms = 1000; }")
    page.wait("Date.now() - window.Interview.I.startedAt > 1100")
    page.say("Ready? I'll hand you over now.")  # past the minimum, under twice it, the tester silent
    time.sleep(0.4)
    assert page.js("window.__handed") is None and page.js("window.Interview.I.handHeld") is True
    page.wait("window.__handed", timeout=6000)
    assert page.js("Date.now() - window.Interview.I.startedAt") >= 2000
    assert page.posted("api/interview/tool")[-1]["by"] == "page"


def test_the_name_handed_over_is_the_latest_known(site, page):
    """The page calls the handover as its words are said; a name saved after that call (a correction still
    being delegated) is the one handed on, not the one the call returned."""
    started(page)
    heard(page, "I'm Sam.")
    page.tool("set_listener_name", {"name": "Sam"})
    wait_for(lambda: site.testers.names == [("t-sam", "Sam")])
    page.js("() => { Interview.I.S.handover_quiet_ms = 1500; }")
    page.say("I'll hand you over now.")
    page.wait("window.Interview.I.handover")
    assert page.js("window.Interview.I.handover.name") == "Sam"
    page.tool("set_listener_name", {"name": "Samira"})
    page.wait("window.Interview.I.name === 'Samira'")
    page.wait("window.__handed", timeout=8000)
    assert page.js("window.__handed.name") == "Samira"
