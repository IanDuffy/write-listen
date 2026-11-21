"""The whole tester flow on the real server with tester mode on (parts A-D together), in headless Chromium.

A tester link from demo.testers.create; the browser opens /t/<token> (cookie, redirect), gets the gate, taps;
the interviewer's intro saves the name and hands over; the gate injects the product page (/app) and its scripts,
and Live.handoff opens the guide with the handoff note and the name; then End demo has the guide hand back and
opens the interviewer's debrief, whose end shows the library. GPT-Live is the stub from
tests/demo/test_gate_page.py (nothing reaches OpenAI); create_webrtc is replaced; no Claude call is made.
"""

import json
import socket
import threading
import time
import types

import pytest

import server
from fakes import FakeAnthropic, Responder
from stages import llm, memory
from voice.gpt_live import session as live_session

sync_api = pytest.importorskip("playwright.sync_api")

from test_gate_page import PRODUCT, STUB  # noqa: E402  (the same GPT-Live stub and product-word list)


@pytest.fixture
def site(cfg, own_listeners, monkeypatch):
    import uvicorn

    from demo import interviewer as IV
    from demo import testers

    cfg["live"]["provider"] = "gpt_live"
    cfg["demo"]["enabled"] = True
    cfg["demo"]["interviewer"]["page"] = {**(cfg["demo"]["interviewer"].get("page") or {}), "line_quiet_ms": 200,
                                          "close_quiet_ms": 300, "close_min_ms": 100, "close_max_ms": 4000,
                                          "act_grace_ms": 400, "greet_fallback_ms": 100000}
    cfg["live"]["page"].update(line_quiet_ms=300, greet_fallback_ms=100000, talk_rest_secs=1000, hello_silence_secs=1000,
                               pitch_nudge_secs=[1000, 1000])
    monkeypatch.setattr(llm, "_client", FakeAnthropic(Responder()))
    monkeypatch.setattr(memory, "after_session", lambda *a, **k: False)
    monkeypatch.setattr(IV, "_after_end", lambda *a: None)
    offers = []

    def webrtc(cfg_, session, sdp, client=None):
        offers.append(session)
        return {"id": f"live_{len(offers)}", "sdp": "ANSWER"}
    monkeypatch.setattr(live_session, "create_webrtc", webrtc)
    t = testers.create(cfg, "Sam Tester")
    pipe = server.Pipeline()
    app = server.create_app(pipe, cfg, remember_on_start=False)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    deadline = time.monotonic() + 10
    while not srv.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield types.SimpleNamespace(base=f"http://127.0.0.1:{port}", tester=t, offers=offers,
                                folder=own_listeners / t["lid"])
    srv.should_exit = True


class Page:
    def __init__(self):
        self.pw = sync_api.sync_playwright().start()
        self.browser = self.pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        self.ctx = self.browser.new_context()
        self.page = self.ctx.new_page()
        self.posts = []
        self.page.on("request", lambda r: r.method == "POST" and self.posts.append(
            (r.url.split("://", 1)[1].split("/", 1)[1].split("?")[0], json.loads(r.post_data or "{}"))))
        self.page.add_init_script(STUB)

    def close(self):
        self.browser.close()
        self.pw.stop()

    def js(self, code, arg=None):
        return self.page.evaluate(code, arg)

    def wait(self, cond, timeout=8000):
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

    def posted(self, path):
        return [b for p, b in self.posts if p == path]


@pytest.fixture
def page():
    p = Page()
    yield p
    p.close()


def test_a_tester_goes_from_the_link_through_the_intro_to_the_guide_and_the_debrief(site, page):
    # The link: a cookie and a redirect, and the token is gone from the address bar (acceptance check 1).
    page.page.goto(site.base + "/t/" + site.tester["token"])
    assert page.page.url == site.base + "/"
    assert any(c["name"] == "wl_tester" for c in page.ctx.cookies())
    # The gate, with nothing of the product in its source (acceptance check 3).
    html = page.page.content()
    assert "Tap to begin" in html and not PRODUCT.search(html.replace("Tap to begin", ""))
    page.wait("window.Interview")
    page.page.click("#w-gate")
    page.wait("window.Interview.I.started && !window.Interview.I.connecting")
    assert page.posted("api/demo/consent") and json.loads((site.folder / "tester.json").read_text()).get("consent_at")
    intro = site.offers[-1]
    assert intro["audio"]["output"]["voice"] == "cedar"
    # The intro: the name saved, then the handover.
    page.say("Thanks so much for making the time. What's your name?")
    page.page.wait_for_timeout(400)
    page.tool("set_listener_name", {"name": "Sam"})
    page.wait("window.__sent.filter((e) => e.type === 'response.item.create').length >= 1")
    assert "Their name is Sam" in (site.folder / "profile.md").read_text()
    page.say("Lovely to meet you, Sam. I'll hand you over now.")
    page.tool("handoff_to_guide")
    # The product page arrives in the same document and the guide opens with the handoff and the name.
    page.wait("window.Live && window.Live.L && window.Live.L.started", timeout=10000)
    assert page.js("document.body.classList.contains('tester')")
    assert "handoff" in page.posted("api/live/start")[-1]
    guide = site.offers[-1]
    assert guide["audio"]["output"]["voice"] == "gleam" and guide["input"] == []
    page.wait("window.Live.L.phase === 'talk'")
    first = [e["content"] for e in page.js("window.__sent") if e["type"] == "session.commentary.append"][-1]
    assert "Sam" in first and "name" in first  # said by the page at the handover, as speakable context
    assert len(page.posted("api/live/start")) == 1
    # one screen grows into the next: the dot ends exactly where the circle is, then the first screen goes
    page.wait("""(() => { const d = document.getElementById('w-dot'); if (!d) return false;
      const r = d.getBoundingClientRect(), s = window.Live.spot();
      return Math.abs(r.left - s.x) < 2 && Math.abs(r.top - s.y) < 2 && Math.abs(r.width - s.w) < 2; })()""", timeout=5000)
    assert page.js("getComputedStyle(document.body).backgroundColor") == "rgb(245, 242, 237)"  # the same ground beneath
    page.wait("!document.getElementById('w-gate')", timeout=6000)
    assert json.loads((site.folder / "tester.json").read_text()).get("intro_done")
    # Nothing of the interviewer went into the guide's memory (it would think it had met them before).
    assert not list((site.folder / "sessions").glob("*.jsonl")) or all(
        "interviewer" not in p.read_text() for p in (site.folder / "sessions").glob("*.jsonl"))
    assert list((site.folder / "study").glob("intro-*.jsonl"))
    # End demo (upper right, there from the reveal): the guide closes and hands back, the debrief, then the library.
    assert not page.page.is_visible("#end-demo")  # only the circle until the guide speaks
    page.say("Hi Sam! I'm Wren.")
    page.page.wait_for_selector("#end-demo", state="visible", timeout=5000)
    page.wait("!document.body.classList.contains('handoff')", timeout=6000)
    page.page.wait_for_timeout(500)
    page.page.click("#end-demo")
    page.wait("window.Live.L.phase === 'handback'")
    assert not page.page.is_visible("#end-demo")
    page.page.wait_for_timeout(400)
    page.say("Good to meet you, Sam. I'm handing you back to the person who welcomed you.")
    page.wait("window.Interview.I.mode === 'debrief' && window.Interview.I.started", timeout=10000)
    assert page.posted("api/interview/start")[-1]["mode"] == "debrief"
    assert len([b for b in page.posted("api/interview/start") if b["mode"] == "debrief"]) == 1
    assert not page.page.is_visible("#end-demo")  # not during the debrief
    # what they pressed, for the debrief's account of the session (study/, never sessions/)
    recs = [json.loads(x) for x in (site.folder / "study" / "events.jsonl").read_text().splitlines()]
    pressed = [e for e in recs if e["event"] != "audio_route"]
    assert [(e["event"], e["phase"]) for e in pressed] == [("end_demo", "talk")]
    # ... beside page/speaker.js's audio route records (the gate's mic open, the voices playing), never presses
    routes = [e["data"]["reason"] for e in recs if e["event"] == "audio_route"]
    assert "mic" in routes and set(routes) <= {"mic", "playing", "devicechange", "speakerphone"}
    # The debrief ends itself (Ian's note, 2026-11-18: "there was no clear exit"): its farewell is the end, with
    # no closing question and no tool call; the page acts on the words.
    # (A debrief the tester said nothing in is never done: they answer first.)
    page.emit({"type": "session.input_transcript.delta", "delta": "The wait felt long, and the voice was warm."})
    page.page.wait_for_timeout(500)
    page.say("Thank you, Sam. That really helps Ian. That's everything from me.")
    page.wait("!window.Interview.busy()", timeout=10000)
    page.wait("!document.body.classList.contains('debrief')", timeout=10000)
    debrief = json.loads((site.folder / "study" / "debrief.json").read_text())
    assert debrief.get("session") and debrief["done"] and debrief["farewell"] and debrief["closing"] is False
    ended = [b for b in page.posted("api/interview/tool") if b["name"] == "end_debrief"]
    assert len(ended) == 1 and ended[0]["by"] == "page"
    page.wait("window.Live.L.debriefDone", timeout=5000)
    page.page.wait_for_timeout(600)
    assert not page.page.is_visible("#end-demo")  # the debrief done: gone
    assert page.js("document.getElementById('lib-debrief')") is None  # and no card in the library


# What a person could read on the screen right now: [text, opacity, "gate" | "app"] for every piece of text
# that is displayed, in the viewport and not fully transparent; the app's text doesn't count while the first
# screen still covers it.
ON_SCREEN = r"""() => {
  const out = [];
  const gate = document.getElementById('w-gate');
  const gateOp = gate ? parseFloat(getComputedStyle(gate).opacity) : 0;
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walk.nextNode())) {
    const text = n.textContent.trim(), e = n.parentElement;
    if (!text || ['SCRIPT', 'STYLE'].includes(e.tagName)) continue;
    const r = e.getBoundingClientRect();
    if (!r.width || !r.height || r.bottom < 0 || r.top > innerHeight) continue;
    const inGate = !!(gate && gate.contains(e));
    let op = 1, shown = true;
    for (let x = e; x && x !== document.documentElement; x = x.parentElement) {
      const cs = getComputedStyle(x);
      if (cs.display === 'none' || cs.visibility === 'hidden' || x.hidden) { shown = false; break; }
      op *= parseFloat(cs.opacity);
    }
    if (!shown || op < 0.05 || (!inGate && gateOp > 0.95)) continue;
    out.push([text, +op.toFixed(2), inGate ? 'gate' : 'app']);
  }
  return out;
}"""


def test_nothing_invites_a_tap_while_the_tester_is_handed_over(site, page):
    """Ian's second run (2026-11-18): "The transition was better this time, but there was that tap to listen
    button that was visible." It was the first screen's own "Tap to begin", back over the growing dot: the next
    screen's script rewrote <body>'s classes as it drew, which was where the first screen kept its state, so the
    three words faded back in (and the dot jumped to full size instead of growing). From the moment the next
    screen is brought in until the guide speaks, the screen shows the circle and no words but the recording
    notice fading out; nothing that reads as a button."""
    page.page.set_viewport_size({"width": 420, "height": 800})
    page.page.goto(site.base + "/t/" + site.tester["token"])
    page.wait("window.Interview")
    page.page.click("#w-gate")
    page.wait("window.Interview.I.started && !window.Interview.I.connecting")
    page.say("Hi, is this Sam?")
    page.page.wait_for_timeout(400)
    page.emit({"type": "session.input_transcript.delta", "delta": "Yes."})
    page.tool("set_listener_name", {"name": "Sam"})
    page.wait("getComputedStyle(document.getElementById('w-tap')).opacity === '0'")
    page.say("Lovely to meet you, Sam. I'll hand you over now.")
    page.tool("handoff_to_guide")
    seen, sizes, t0 = [], [], time.monotonic()
    spoke = None
    while time.monotonic() - t0 < 8:
        now = time.monotonic() - t0
        live = page.js("!!(window.Live && window.Live.L)")
        texts = page.js(ON_SCREEN)
        seen.append((round(now, 2), spoke is not None, live, texts))
        dot = page.js("(() => { const d = document.getElementById('w-dot'); return d ? d.getBoundingClientRect().width : null; })()")
        if live and dot:
            sizes.append(dot)
        if spoke is None and live and page.js("window.Live.L.phase === 'talk' && !document.getElementById('w-gate')"):
            assert texts == [], texts  # the first screen gone, the guide not yet heard: the circle and nothing else
            assert not page.page.is_visible("#end-demo")
            page.say("Hi Sam! I'm Wren.")
            spoke = now
        if spoke is not None and now - spoke > 2.5:
            break
        page.page.wait_for_timeout(60)
    assert spoke is not None, "the handover never finished"
    before = [x for x in seen if x[2] and not x[1]]  # the next screen brought in, the guide not yet speaking
    assert len(before) >= 10
    for at, _, _, texts in before:
        words = " ".join(t for t, _, _ in texts)
        assert "tap" not in words.lower(), (at, texts)
        assert all(t.startswith("Your conversations are recorded") for t, _, _ in texts), (at, texts)
    # ... and the dot grows into the circle (it jumped to full size when its class was wiped)
    assert any(40 < w < 120 for w in sizes), sizes[:12]
    # once the guide speaks the screen fades in: still nothing asking for a tap
    for at, _, _, texts in [x for x in seen if x[1]]:
        assert "tap" not in " ".join(t for t, _, _ in texts).lower(), (at, texts)
    assert page.page.is_visible("#end-demo")


def test_a_second_browser_without_the_link_sees_only_the_dot(site, page):
    page.page.goto(site.base + "/")
    assert page.page.inner_text("body").strip() == ""
    assert page.page.evaluate("fetch('/api/status').then((r) => r.status)") == 404
