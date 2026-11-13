"""Ask mid-chapter, in a real browser (headless Chromium, when Playwright is installed): the tap
(check 1), going back from every state (check 4), a dropped or never-completed connection (check 5),
reduced motion (check 5b), and the guide's own return.

The real server serves the page and the aside routes, over a synthetic chapter (ask_fixture) as
silent audio. The ElevenLabs browser client is a stub the test drives: when it connects, which
mode it is in, tool calls, a dropped connection. No network, no model, no key.
"""

import json
import socket
import threading
import time

import pytest

import ask_fixture as F
import server
from ask import aside as A
from stages import voice

sync_api = pytest.importorskip("playwright.sync_api")

STUB = """
window.__el = { delay: 300, never: false, sessions: [], inVol: 0, outVol: 0.4 };
window.ElevenLabsClient = { Conversation: { startSession: (opts) => new Promise((resolve) => {
  if (window.__el.never) return;  // never connects
  setTimeout(() => {
    const conv = { opts, sent: [], ended: false,
      sendUserMessage(t) { this.sent.push(t); },
      async endSession() { this.ended = true; },
      getInputVolume() { return window.__el.inVol; }, getOutputVolume() { return window.__el.outVol; },
      mode(m) { opts.onModeChange({ mode: m }); },
      say(text) { opts.onMessage({ role: "agent", message: text }); },
      hear(text) { opts.onMessage({ role: "user", message: text }); },
      tool(name, p) { return opts.clientTools[name](p); },
      drop() { opts.onDisconnect(); } };
    window.__el.sessions.push(conv);
    opts.onConnect({ conversationId: "conv_" + window.__el.sessions.length });
    resolve(conv);
  }, window.__el.delay);
}) } };
"""


@pytest.fixture
def site(cfg, tmp_path, monkeypatch):
    """The real server on a free port, over the synthetic chapter, with no ElevenLabs."""
    import uvicorn

    F.build(tmp_path / "runs" / "synthetic", audio=True)
    cfg["listener_dir"] = str(tmp_path / "listener")
    cfg["voice"]["client_js"] = "/stub-el.js"
    cfg["ask"].update(slow_line_secs=1, give_up_secs=2.5, silence_after_greeting_secs=1, silence_after_answer_secs=0.6,
                      silence_after_question_secs=1.5)
    monkeypatch.setattr(voice, "signed_url", lambda cfg, client=None, kind="guide": "wss://example/signed")
    pipe = server.Pipeline(runs_dir=tmp_path / "runs")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(server.create_app(pipe, cfg, remember_on_start=False), host="127.0.0.1",
                                        port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    deadline = time.monotonic() + 10
    while not srv.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", tmp_path / "runs" / "synthetic", cfg
    srv.should_exit = True


def open_page(site, reduced=False):
    base, root, cfg = site
    pw = sync_api.sync_playwright().start()
    browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required",
                                       "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
    ctx = browser.new_context(reduced_motion="reduce" if reduced else "no-preference")
    page = ctx.new_page()
    page.route(base + "/stub-el.js", lambda r: r.fulfill(status=200, content_type="text/javascript", body=STUB))
    page.add_init_script("""
      window.__t = {};
      document.addEventListener("pause", (e) => { if (e.target.id === "audio") window.__t.paused = performance.now(); }, true);
      document.addEventListener("click", (e) => { if (e.target.closest && e.target.closest("#orb")) window.__t.click = performance.now(); }, true);
    """)
    page.goto(base + "/?chapter=synthetic")
    page.wait_for_function("window.Ask && window.ElevenLabsClient && document.body.classList.contains('ready')")
    return pw, browser, page


def play_at(page, t):
    page.evaluate(f"(() => {{ const a = document.getElementById('audio'); a.currentTime = {t}; }})()")
    page.click("#orb")  # paused, ready: the tap plays
    page.wait_for_function(f"!document.getElementById('audio').paused && document.getElementById('audio').currentTime >= {t}")
    page.wait_for_function("document.getElementById('line').textContent === 'Ask a question'")


def audio_state(page):
    return page.evaluate("(() => { const a = document.getElementById('audio'); return {paused: a.paused, t: a.currentTime}; })()")


def wait_back(page, n):
    page.wait_for_function(f"window.Ask.debug.asides.length >= {n} && !document.getElementById('audio').paused", timeout=15000)
    return page.evaluate(f"window.Ask.debug.asides[{n - 1}]")


@pytest.fixture
def browser_page(site):
    pw, browser, page = open_page(site)
    yield page, site
    browser.close()
    pw.stop()


# --- check 1: the tap ----------------------------------------------------------------------------

def test_tap_pauses_starts_connecting_and_records_the_sentence(browser_page):
    page, (base, root, cfg) = browser_page
    play_at(page, 52.0)
    page.click("#orb")
    page.wait_for_function("window.__t.paused !== undefined")
    t = page.evaluate("({click: window.__t.click, paused: window.__t.paused, d: window.Ask.debug, st: window.Ask.state()})")
    assert t["paused"] - t["click"] < 200          # faded out and paused within 200 ms
    assert page.evaluate("document.body.classList.contains('connecting')")
    page.wait_for_function("window.__el.sessions.length === 1")
    first = page.evaluate("window.__el.sessions[0].sent[0]")
    assert first.startswith("[The listener tapped")  # the guide speaks first
    page.wait_for_function("window.Ask.state() && window.Ask.state().phase === 'live'")
    page.click("#orb")  # back
    d = wait_back(page, 1)
    assert d["blobAt"] - d["tap"] < 100            # the membrane moved within 100 ms of the tap
    assert d["pausedAt"] - d["tap"] < 200
    log = A.load_asides(root)
    ch = A.Chapter(root, cfg)
    assert log[0]["sentence_id"] == ch.sents[ch.locate(log[0]["t"])].id == "seg01-s010"
    assert 52.0 <= log[0]["t"] < 53.5


def test_blob_moves_while_connecting(browser_page):
    page, _ = browser_page
    page.evaluate("window.__el.delay = 1500")
    play_at(page, 20.0)
    page.click("#orb")
    d1 = page.evaluate("document.querySelector('#orb .blob').getAttribute('d')")
    page.wait_for_timeout(300)
    d2 = page.evaluate("document.querySelector('#orb .blob').getAttribute('d')")
    assert d1 and d2 and d1 != d2                  # the outline deforms continuously
    assert page.evaluate("getComputedStyle(document.querySelector('#orb .blob')).opacity") == "1"
    page.wait_for_timeout(700)  # the plain circle fades out underneath (its own transition)
    assert page.evaluate("getComputedStyle(document.querySelector('#orb .core')).opacity") == "0"


# --- check 4: going back from every state -----------------------------------------------------------

@pytest.mark.parametrize("state", ["connecting", "guide speaking", "listener speaking"])
def test_tap_goes_back_from_every_state(browser_page, state):
    page, (base, root, cfg) = browser_page
    if state == "connecting":
        page.evaluate("window.__el.delay = 4000")
    play_at(page, 30.0)
    page.click("#orb")
    if state == "connecting":
        page.wait_for_timeout(300)
        assert page.evaluate("window.Ask.state().phase") == "connecting"
    else:
        page.wait_for_function("window.__el.sessions.length === 1")
        page.wait_for_function("window.Ask.state() && window.Ask.state().phase === 'live'")
        if state == "guide speaking":
            page.evaluate("window.__el.sessions[0].mode('speaking')")
        else:
            page.evaluate("window.__el.inVol = 0.5; window.__el.sessions[0].mode('listening')")
    assert page.inner_text("#line") == "Back to the chapter"
    page.click("#orb")
    d = wait_back(page, 1)
    assert d["how"] == "button"
    if state != "connecting":
        assert page.evaluate("window.__el.sessions[0].ended")  # the voice session (and the mic) closed
    a = audio_state(page)
    assert not a["paused"] and abs(a["t"] - d["restart"]["t"]) < 1.0
    page.wait_for_function("document.getElementById('line').textContent === 'Ask a question'")
    if state == "connecting":  # the connection that arrives late is closed, not used
        page.wait_for_function("window.__el.sessions.length === 1", timeout=8000)
        assert page.evaluate("window.__el.sessions[0].ended")
    page.wait_for_timeout(300)
    assert [x["ended"] for x in A.load_asides(root)] == ["button"]


# --- check 5: a dropped session, or one that never connects ----------------------------------------

def test_a_dropped_connection_resumes_the_chapter(browser_page):
    page, (base, root, cfg) = browser_page
    play_at(page, 40.0)
    page.click("#orb")
    page.wait_for_function("window.Ask.state() && window.Ask.state().phase === 'live'")
    page.evaluate("window.__el.sessions[0].drop()")
    d = wait_back(page, 1)
    assert d["how"] == "dropped"
    assert page.inner_text("#line") == "The connection dropped, so back to the chapter."
    assert A.load_asides(root)[0]["ended"] == "dropped"


def test_a_connection_that_never_completes_gives_up_and_resumes(browser_page):
    page, (base, root, cfg) = browser_page
    page.evaluate("window.__el.never = true")
    play_at(page, 40.0)
    page.click("#orb")
    page.wait_for_function("document.getElementById('line').textContent === 'Still waking up'", timeout=3000)
    d = wait_back(page, 1)
    assert d["how"] == "timeout" and 2400 <= page.evaluate("performance.now()") - d["tap"]
    assert A.load_asides(root)[0]["ended"] == "timeout"
    assert page.inner_text("#line") == "Ask a question"  # quietly: no message


# --- check 5b: reduced motion --------------------------------------------------------------------

def test_reduced_motion_pulses_instead(site):
    pw, browser, page = open_page(site, reduced=True)
    try:
        page.evaluate("window.__el.delay = 2000")
        play_at(page, 20.0)
        page.click("#orb")
        page.wait_for_timeout(200)
        assert page.evaluate("document.body.classList.contains('calm')")
        assert page.evaluate("getComputedStyle(document.querySelector('#orb .blob')).animationName") == "breathe"
        d1 = page.evaluate("document.querySelector('#orb .blob').getAttribute('d')")
        page.wait_for_timeout(300)
        d2 = page.evaluate("document.querySelector('#orb .blob').getAttribute('d')")
        assert d1 == d2  # a still circle that pulses, no deforming outline
    finally:
        browser.close()
        pw.stop()


# --- the guide's return, and the silences the page times -------------------------------------------

def test_resume_chapter_waits_for_the_lead_in_then_restarts(browser_page):
    page, (base, root, cfg) = browser_page
    play_at(page, 52.0)
    page.click("#orb")
    page.wait_for_function("window.Ask.state() && window.Ask.state().phase === 'live'")
    page.evaluate("""(async () => { const c = window.__el.sessions[0];
        c.mode('speaking'); c.say('Yes?'); c.mode('listening'); c.hear('Who wrote this?');
        c.mode('speaking'); c.say('A synthetic author.'); c.mode('listening'); c.hear('Oh, OK.');
        window.__toolOut = await c.tool('resume_chapter', {reason: 'closing'});
        c.mode('speaking'); c.say('OK, where were we...'); })()""")
    page.wait_for_timeout(1200)
    assert page.evaluate("window.Ask.state() !== null")  # still speaking its lead-in: not yet
    page.evaluate("window.__el.sessions[0].mode('listening')")
    d = wait_back(page, 1)
    assert d["how"] == "guide" and json.loads(page.evaluate("window.__toolOut"))["resuming"] is True
    log = A.load_asides(root)[0]
    assert log["ended"] == "closing" and log["restart"]["sentence_id"] == "seg01-s010"
    assert [x["who"] for x in log["transcript"]] == ["guide", "listener", "guide", "listener", "tool", "guide"]
    assert audio_state(page)["t"] >= log["restart"]["t"] - 0.1
    page.click("#toggle")
    assert page.inner_text("#transcript details.aside summary") == "Yes?"  # collapsed to its first line


def test_silence_notes(browser_page):
    page, _ = browser_page
    play_at(page, 30.0)
    page.click("#orb")
    page.wait_for_function("window.Ask.state() && window.Ask.state().phase === 'live'")
    page.evaluate("const c = window.__el.sessions[0]; c.mode('speaking'); c.say('Mm?'); c.mode('listening');")
    page.wait_for_function("window.__el.sessions[0].sent.length === 2", timeout=4000)  # after the opening
    assert "since your opening" in page.evaluate("window.__el.sessions[0].sent[1]")
    page.evaluate("""const c = window.__el.sessions[0]; c.hear('Who?'); c.mode('speaking');
        c.say('An author. Did you want more?'); c.mode('listening');""")
    page.wait_for_timeout(900)
    assert page.evaluate("window.__el.sessions[0].sent.length") == 2  # it asked something: longer wait
    page.wait_for_function("window.__el.sessions[0].sent.length === 3", timeout=4000)
    assert "few seconds have passed" in page.evaluate("window.__el.sessions[0].sent[2]")


# --- the episode page shows each aside where it interrupted ----------------------------------------

def test_episode_page_shows_asides_where_they_interrupted(site):
    from episode import bundle
    base, root, cfg = site
    (root / "11_episode" / "references.json").write_text("[]")
    bundle.write(root)
    A.append_aside(root, {"id": "x1", "t": 52.3, "sentence_id": "seg01-s010", "ended": "closing", "transcript": [
        {"who": "guide", "text": "Yes?"}, {"who": "listener", "text": "Who wrote this?"},
        {"who": "tool", "name": "resume_chapter"}, {"who": "guide", "text": "A synthetic author."}]})
    pw = sync_api.sync_playwright().start()
    browser = pw.chromium.launch()
    try:
        page = browser.new_page()
        page.goto(base + "/episode/index.html?run=/runs/synthetic")
        page.wait_for_selector("details.aside")
        assert page.inner_text("details.aside summary") == "Yes?"
        assert not page.is_visible("details.aside p")  # collapsed to its first line
        assert page.evaluate("document.querySelector('details.aside').nextElementSibling.id") == "seg01-s010"
        page.click("details.aside summary")
        assert page.inner_text("details.aside").count("\n") >= 2 and "You: Who wrote this?" in page.inner_text("details.aside")
    finally:
        browser.close()
        pw.stop()


def test_resume_without_a_lead_in_goes_back_after_a_moment(browser_page):
    page, (base, root, cfg) = browser_page
    play_at(page, 30.0)
    page.click("#orb")
    page.wait_for_function("window.Ask.state() && window.Ask.state().phase === 'live'")
    page.evaluate("const c = window.__el.sessions[0]; c.mode('speaking'); c.say('Mm?'); c.mode('listening'); c.hear('Never mind.'); c.tool('resume_chapter', {reason: 'never_mind'});")
    page.wait_for_timeout(1500)
    assert page.evaluate("window.Ask.state() !== null")  # waiting for a lead-in
    d = wait_back(page, 1)
    assert d["how"] == "guide" and A.load_asides(root)[0]["ended"] == "never_mind"


def test_start_at_a_time_from_the_address(site):
    base, root, cfg = site
    pw = sync_api.sync_playwright().start()
    browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    try:
        page = browser.new_page()
        page.route(base + "/stub-el.js", lambda r: r.fulfill(status=200, content_type="text/javascript", body=STUB))
        page.goto(base + "/?chapter=synthetic&t=120")
        page.wait_for_function("window.Ask && document.body.classList.contains('ready')")
        page.click("#orb")
        page.wait_for_function("document.getElementById('audio').currentTime >= 120")
    finally:
        browser.close()
        pw.stop()
