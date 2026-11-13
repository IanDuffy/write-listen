"""The page's resting state in a real browser (headless Chromium, when Playwright is installed):
the guide steps back, the connection ends, and a tap, typing, a voice at the microphone or the
chapter being ready brings it back into the same conversation.

The ElevenLabs browser client is replaced by a stub that records what the page does with it, and
Chromium's fake microphone plays a WAV made here (silence, or a voice-like sound), so the page's
own listener (page/wake.js, an AudioWorklet) is what hears it. No network, no model, no key.
"""

import json
import math
import os
import socket
import struct
import threading
import time
import wave
from types import SimpleNamespace as NS

import pytest
from fakes import FakeAnthropic, Responder

import server
from stages import guide, llm, voice

sync_api = pytest.importorskip("playwright.sync_api")

STUB = """
window.__el = { sessions: [], vol: 0.3, fail: false, delay: 0 };
window.ElevenLabsClient = { Conversation: { startSession: async (opts) => {
  if (window.__el.delay) await new Promise((r) => setTimeout(r, window.__el.delay));
  if (window.__el.fail) throw new Error("The AI agent you are trying to reach is not available.");
  const conv = { opts, sent: [], ended: false,
    sendUserMessage(t) { this.sent.push(t); },
    async endSession() { this.ended = true; },
    getInputVolume() { return window.__el.vol; }, getOutputVolume() { return 0; } };
  window.__el.sessions.push(conv);
  opts.onConnect({ conversationId: "conv_" + window.__el.sessions.length });
  return conv;
} } };
"""


def _wav(path, voiced: bool, secs: float = 4.0, rate: int = 48000):
    """Silence, or 1 s of quiet then a voice-like sound (harmonics of 140 Hz, four syllables a second)."""
    frames = bytearray()
    for i in range(int(secs * rate)):
        t = i / rate
        x = 0.0
        if voiced and t % secs > 1.0:
            syll = 0.55 + 0.45 * math.sin(2 * math.pi * 4 * t)
            x = 0.5 * syll * (math.sin(2 * math.pi * 140 * t) + 0.8 * math.sin(2 * math.pi * 420 * t)
                              + 0.6 * math.sin(2 * math.pi * 1120 * t) + 0.3 * math.sin(2 * math.pi * 2400 * t)) / 2
        frames += struct.pack("<h", int(max(-1, min(1, x)) * 32000))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


@pytest.fixture
def site(cfg, tmp_path, monkeypatch):
    """The real server on a free port, with a fake model and no ElevenLabs."""
    import uvicorn

    cfg["listener_dir"] = str(tmp_path / "listener")
    cfg["voice"].update(client_js="/stub-el.js", rest_after_silence_secs=2, rest_after_ready_secs=1)
    monkeypatch.setattr(llm, "_client", FakeAnthropic(Responder()))
    monkeypatch.setattr(voice, "signed_url", lambda cfg, client=None: "wss://example/signed")
    monkeypatch.setattr(server, "_save_voice", lambda *a, **k: None)
    monkeypatch.setattr(server.memory, "after_session", lambda *a, **k: False)  # its thread could outlive the fake model
    runs = tmp_path / "runs"
    runs.mkdir()
    pipe = server.Pipeline(runs_dir=runs)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(server.create_app(pipe, cfg, remember_on_start=False), host="127.0.0.1",
                                        port=port, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 10
    while not srv.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield NS(url=f"http://127.0.0.1:{port}", runs=runs, cfg=cfg, tmp=tmp_path)
    srv.should_exit = True
    t.join(5)


def _open(p, site, mic):
    try:
        browser = p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                                          f"--use-file-for-fake-audio-capture={mic}"])
    except Exception as e:  # Playwright without its browser
        pytest.skip(f"Chromium is not installed for Playwright: {e}")
    page = browser.new_page()
    page.route("**/stub-el.js", lambda route: route.fulfill(body=STUB, content_type="text/javascript"))
    page.goto(site.url)
    page.wait_for_function("window.ElevenLabsClient && window.Wake")
    return browser, page


def _sessions(page):
    return page.evaluate("window.__el.sessions.map((c) => ({sent: c.sent, ended: c.ended, "
                         "earlier: c.opts.dynamicVariables.earlier}))")


def _say(page, n, role, message):
    page.evaluate("([n, role, message]) => window.__el.sessions[n].opts.onMessage({ role, message })", [n, role, message])


def _mode(page, n, mode):
    page.evaluate("([n, mode]) => window.__el.sessions[n].opts.onModeChange({ mode })", [n, mode])


def _transcript(site):
    (path,) = [p for p in (guide.listener_dir(site.cfg) / "sessions").glob("*.jsonl")
               if p.name != "log.jsonl"]  # the memory pass logs its cost beside the transcripts
    return [json.loads(x) for x in path.read_text().splitlines()]


def _begin(page):
    """Tap, the guide says hello, the listener says they'll wait, the guide answers and steps back."""
    page.click("#orb")
    page.wait_for_function("window.__el.sessions.length === 1")
    _mode(page, 0, "speaking")
    _say(page, 0, "agent", "Hey, good to see you.")
    _mode(page, 0, "listening")
    _say(page, 0, "user", "Let me know when it's ready.")
    _mode(page, 0, "speaking")
    _say(page, 0, "agent", "Will do.")
    # The call is answered only once the page has rested, so the model is never asked to speak after it.
    page.evaluate("() => { window.__step = 'waiting'; "  # a block, so the pending call is not awaited here
                  "window.__el.sessions[0].opts.clientTools.step_back({}).then((r) => { window.__step = r; }); }")
    page.wait_for_timeout(1800)
    assert "resting" not in page.get_attribute("body", "class")  # not while it is still speaking
    assert page.evaluate("window.__step") == "waiting"
    _mode(page, 0, "listening")
    page.wait_for_function("document.body.classList.contains('resting')", timeout=6000)
    assert json.loads(page.evaluate("window.__step"))["quiet"] is True


def test_stepping_back_resting_and_coming_back(site):
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, site, _wav(site.tmp / "silence.wav", voiced=False))
        _begin(page)
        s = _sessions(page)
        assert s[0]["ended"] and s[0]["sent"] == ["[The listener has opened the page.]"]
        assert page.is_visible("#hint") and page.is_visible("#stop")
        page.wait_for_function("document.getElementById('how').textContent.startsWith('Say something')", timeout=5000)

        page.wait_for_timeout(2500)  # a silent room: it stays resting, and no new connection is made
        assert len(_sessions(page)) == 1 and "resting" in page.get_attribute("body", "class")

        page.click("#orb")  # a tap brings it back, told what came before
        page.wait_for_function("window.__el.sessions.length === 2")
        s = _sessions(page)
        assert s[1]["sent"][0].startswith("[The listener tapped to bring you back")
        assert "listener: Let me know when it's ready." in s[1]["earlier"] and "you: Will do." in s[1]["earlier"]
        assert "resting" not in page.get_attribute("body", "class") and not page.is_visible("#hint")

        # Nobody speaks (the room's level, steady): after rest_after_silence_secs it steps back unasked.
        _mode(page, 1, "listening")
        page.wait_for_function("document.body.classList.contains('resting')", timeout=6000)
        assert _sessions(page)[1]["ended"]

        page.fill("#say", "how is it going?")  # typing to a resting guide: what they typed is the turn
        page.click("#send")
        page.wait_for_function("window.__el.sessions.length === 3")
        page.wait_for_function("window.__el.sessions[2].sent.length === 1")
        assert _sessions(page)[2]["sent"] == ["how is it going?"]

        # While someone is speaking (the mic well above the room), it does not step back.
        _mode(page, 2, "listening")
        page.evaluate("window.__el.vol = 0.3")
        page.wait_for_timeout(600)
        page.evaluate("window.__talk = setInterval(() => { window.__el.vol = 0.3 + 0.3 * Math.random(); }, 100)")
        page.wait_for_timeout(3000)
        assert "resting" not in page.get_attribute("body", "class")
        page.evaluate("clearInterval(window.__talk); window.__el.vol = 0.3")
        page.wait_for_function("document.body.classList.contains('resting')", timeout=6000)

        page.click("#stop")  # resting no more: the conversation is over and the microphone released
        page.wait_for_function("!document.body.classList.contains('resting')")
        assert page.inner_text("#line") == "Tap to begin" and not page.is_visible("#hint")
        page.wait_for_timeout(300)
        lines = _transcript(site)
        pages = [x["text"] for x in lines if x["who"] == "page"]
        assert pages[1] == "[The guide stepped back: it was asked to.]"
        assert pages[2].startswith("[The listener tapped") and pages[3] == "[The guide stepped back: nobody had spoken for a while.]"
        assert [x["conversation_id"] for x in lines if x.get("conversation_id")] == ["conv_1", "conv_2", "conv_3"]
        assert any(x["who"] == "tool" and x["name"] == "step_back" for x in lines)
        browser.close()


def test_a_voice_at_the_microphone_brings_the_guide_back(site):
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, site, _wav(site.tmp / "voice.wav", voiced=True))
        _begin(page)
        page.wait_for_function("window.__el.sessions.length === 2", timeout=15000)  # nobody tapped
        s = _sessions(page)
        # A sound may not be the listener: the guide is given nothing to say, it listens (two soft notes play).
        assert s[1]["sent"] == [] and "listener: Let me know when it's ready." in s[1]["earlier"]
        assert "resting" not in page.get_attribute("body", "class")
        assert not page.is_disabled("#say")

        # The tool before any words: the call is answered at once, so the guide can say its few
        # words (the tool's note asks for them), and the page rests when they have been heard.
        _mode(page, 1, "speaking")
        _say(page, 1, "agent", "I'm here.")
        _mode(page, 1, "listening")
        _say(page, 1, "user", "Never mind, I'll wait.")
        out = json.loads(page.evaluate("window.__el.sessions[1].opts.clientTools.step_back({})"))
        assert out["quiet"] is True and "say them now" in out["note"]
        again = json.loads(page.evaluate("window.__el.sessions[1].opts.clientTools.step_back({})"))  # a repeat changes nothing
        assert again["quiet"] is True
        _mode(page, 1, "speaking")
        _say(page, 1, "agent", "Okay.")
        page.wait_for_timeout(700)
        assert "resting" not in page.get_attribute("body", "class")  # still speaking
        _mode(page, 1, "listening")
        page.wait_for_function("document.body.classList.contains('resting')", timeout=3000)
        browser.close()


def test_the_chapter_being_ready_brings_the_guide_back_to_say_so(site):
    status = site.runs / "night-office" / "status.json"
    status.parent.mkdir()
    making = {"run": "night-office", "state": "making", "label": "Looking into it", "stages_done": 1,
              "stages_total": 15, "minutes_left": 20, "pid": os.getpid()}
    status.write_text(json.dumps(making))
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, site, _wav(site.tmp / "silence.wav", voiced=False))
        page.wait_for_function("document.body.classList.contains('making')")
        _begin(page)
        assert "Looking into it" in page.inner_text("#line")  # the wait still shows on the page
        status.write_text(json.dumps({**making, "state": "ready", "title": "The Night Office", "pid": None}))
        page.wait_for_function("window.__el.sessions.length === 2", timeout=8000)
        s = _sessions(page)
        assert 'The chapter is ready: "The Night Office"' in s[1]["sent"][0]
        _mode(page, 1, "speaking")
        _say(page, 1, "agent", "Your chapter's ready. Tap the circle to play it.")
        _mode(page, 1, "listening")
        # It said so and nobody answered: it steps back after rest_after_ready_secs, the play control showing.
        page.wait_for_function("document.body.classList.contains('resting')", timeout=6000)
        assert "ready" in page.get_attribute("body", "class") and page.inner_text("#line") == "The Night Office"
        browser.close()


def test_sounds_nobody_follows_up_stop_waking_the_guide(site):
    """A television, music, someone else in the room: a sound wakes the guide, nobody then speaks to
    it, it rests; after two of those in a row only a tap brings it back."""
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, site, _wav(site.tmp / "voice.wav", voiced=True))
        _begin(page)
        for n in (2, 3):  # woken by the sound, twice; nobody says anything, so it rests after a second
            page.wait_for_function(f"window.__el.sessions.length === {n}", timeout=15000)
            _mode(page, n - 1, "listening")
            page.evaluate("window.__el.vol = 0.3")
            page.wait_for_function("document.body.classList.contains('resting')", timeout=8000)
        page.wait_for_timeout(7000)  # the sound goes on; the guide does not come back
        assert len(_sessions(page)) == 3 and page.inner_text("#how") == "Tap to talk"
        assert all(c["sent"] == [] for c in _sessions(page)[1:])  # and it never spoke into the room
        page.click("#orb")  # a tap still does
        page.wait_for_function("window.__el.sessions.length === 4")
        assert _sessions(page)[3]["sent"][0].startswith("[The listener tapped")
        browser.close()


def test_when_voice_cannot_come_back_it_says_so_and_typing_still_works(site):
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, site, _wav(site.tmp / "silence.wav", voiced=False))
        _begin(page)
        page.evaluate("window.__el.fail = true")  # the credits ran out, or the network is down
        page.click("#orb")
        page.wait_for_function("document.getElementById('how').textContent.startsWith(\"Voice couldn't start\")")
        assert "resting" in page.get_attribute("body", "class") and len(_sessions(page)) == 1
        page.fill("#say", "are you there?")
        page.click("#send")
        # What they typed goes to the typed chat (a new conversation), and the guide answers there.
        page.wait_for_function("document.querySelectorAll('#transcript p.guide').length >= 3", timeout=10000)
        assert "resting" not in page.get_attribute("body", "class") and page.input_value("#say") == ""
        assert "are you there?" in page.inner_text("#transcript")
        browser.close()


def test_pressing_play_while_the_guide_is_reconnecting_never_talks_over_the_chapter(site):
    status = site.runs / "night-office" / "status.json"
    status.parent.mkdir()
    making = {"run": "night-office", "state": "making", "label": "Writing", "stages_done": 9, "stages_total": 15,
              "minutes_left": 5, "pid": os.getpid()}
    status.write_text(json.dumps(making))
    (status.parent / "10_audio").mkdir()
    (status.parent / "10_audio" / "chapter.mp3").write_bytes(b"")
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, site, _wav(site.tmp / "silence.wav", voiced=False))
        page.wait_for_function("document.body.classList.contains('making')")
        _begin(page)
        page.evaluate("window.__el.delay = 1500")  # the connection takes a moment
        status.write_text(json.dumps({**making, "state": "ready", "title": "The Night Office", "pid": None}))
        page.wait_for_function("document.body.classList.contains('ready')", timeout=8000)
        page.click("#orb")  # play, while the guide is on its way back to say it is ready
        page.wait_for_timeout(2500)
        s = _sessions(page)
        assert len(s) == 2 and s[1]["ended"] and s[1]["sent"] == []  # it arrived, said nothing, and was sent away
        cls = page.get_attribute("body", "class")
        assert "voice" not in cls and "resting" not in cls
        browser.close()
