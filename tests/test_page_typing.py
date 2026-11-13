"""Voice first: the page hides the text box until voice can't start (headless Chromium, when
Playwright is installed). The page is the one the server renders; every request it makes is
answered here, so there is no network, model or key."""

import json

import pytest
from fastapi.testclient import TestClient

import server

sync_api = pytest.importorskip("playwright.sync_api")

ORIGIN = "http://localhost:9"  # a secure context, so the page sees navigator.mediaDevices
STUB = """window.ElevenLabsClient = { Conversation: { startSession: async (opts) => ({
  sendUserMessage() {}, async endSession() {}, getInputVolume() { return 0; }, getOutputVolume() { return 0; } }) } };"""


def _open(cfg, tmp_path, mic: bool):
    """The page in Chromium; mic=True gives it a fake microphone it may use, False none at all."""
    cfg["voice"]["client_js"] = ORIGIN + "/stub-el.js"
    html = TestClient(server.create_app(server.Pipeline(runs_dir=tmp_path), cfg, remember_on_start=False)).get("/").text
    answers = {
        "/": ("text/html", html),
        "/stub-el.js": ("text/javascript", STUB),
        "/api/events": ("text/event-stream", 'retry: 600000\ndata: {"state": "idle"}\n\n'),
        "/api/chat/start": ("application/json", json.dumps({"session": "s1", "reply": "Hello, I'm Wren."})),
        "/api/voice/start": ("application/json", json.dumps({"session": "s1", "signed_url": "wss://example/signed",
                                                             "dynamic_variables": {}, "opened": "[opened]"})),
    }

    def answer(route):
        path = route.request.url[len(ORIGIN):].split("?")[0] or "/"
        kind, body = answers.get(path, ("application/json", "{}"))
        route.fulfill(status=200, content_type=kind, body=body)

    pw = sync_api.sync_playwright().start()
    args = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"] if mic else []
    browser = pw.chromium.launch(args=args)
    page = browser.new_page()
    if not mic:  # no microphone at all: getUserMedia fails, as on a machine without one
        page.add_init_script("navigator.mediaDevices.getUserMedia = async () => { throw new Error('no mic'); };")
    page.route(ORIGIN + "/**", answer)
    page.goto(ORIGIN + "/")
    page.wait_for_function("window.ElevenLabsClient !== undefined")
    return pw, browser, page


def test_text_box_hidden_while_voice_works(cfg, tmp_path):
    pw, browser, page = _open(cfg, tmp_path, mic=True)
    try:
        assert not page.is_visible("#say")  # nothing to type into before the tap
        page.click("#orb")
        page.wait_for_selector("body.voice")
        assert not page.is_visible("#say")  # talking: still no text box
        assert page.is_visible("#toggle")   # the transcript is still there
    finally:
        browser.close(), pw.stop()


def test_text_box_appears_when_voice_cannot_start(cfg, tmp_path):
    pw, browser, page = _open(cfg, tmp_path, mic=False)
    try:
        assert not page.is_visible("#say")
        page.click("#orb")
        page.wait_for_selector("#say", state="visible")
        page.wait_for_function("document.activeElement && document.activeElement.id === 'say'")
        assert "No microphone" in page.inner_text("#transcript")
        assert "Hello, I'm Wren." in page.inner_text("#transcript")
    finally:
        browser.close(), pw.stop()


def test_keyboard_button_shows_and_hides_the_text_box(cfg, tmp_path):
    pw, browser, page = _open(cfg, tmp_path, mic=True)
    try:
        assert page.is_visible("#type") and not page.is_visible("#say")
        page.click("#type")
        page.wait_for_selector("#say", state="visible")
        assert page.evaluate("document.activeElement.id") == "say"
        assert page.get_attribute("#type", "aria-pressed") == "true"
        page.click("#orb")  # a voice conversation keeps the box they asked for
        page.wait_for_selector("body.voice")
        assert page.is_visible("#say")
        page.click("#type")
        page.wait_for_selector("#say", state="hidden")
        assert page.get_attribute("#type", "aria-pressed") == "false"
    finally:
        browser.close(), pw.stop()
