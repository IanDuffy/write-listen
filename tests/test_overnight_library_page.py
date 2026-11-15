"""The library page in headless Chromium (page/library.html, page/library.js), against the real server
with GPT-Live's session opening faked and WebRTC stubbed in the page: the test plays GPT-Live's part by
pushing events into the data channel. Covers the morning flow (the intro, then the recording, by the
tool, by the lead-in line and by the 90-second cap), an aside on the recording with the rewind, and Change."""

import json
import socket
import threading
import time
from types import SimpleNamespace as NS

import pytest
from overnight_plan_fakes import offline  # noqa: F401

import server
from overnight import live, planner, profiles, schedule
from stages import memory

sync_api = pytest.importorskip("playwright.sync_api")

STUB = """
window.__rtc = { sent: [], channels: [], closed: 0 };
class FakeDC { constructor() { this.readyState = 'open'; window.__rtc.channels.push(this); }
  send(x) { window.__rtc.sent.push(JSON.parse(x)); } close() {} }
class FakePC { constructor() { this.iceGatheringState = 'complete'; this.connectionState = 'new'; }
  addTrack() {} createDataChannel() { this.dc = new FakeDC(); return this.dc; }
  async createOffer() { return { type: 'offer', sdp: 'offer-sdp' }; }
  async setLocalDescription(d) { this.localDescription = d; }
  async setRemoteDescription(d) { this.remote = d; }
  addEventListener() {} close() { window.__rtc.closed++; } }
window.RTCPeerConnection = FakePC;
navigator.mediaDevices.getUserMedia = async () => { const t = { enabled: true, stop() {} };
  return { getAudioTracks: () => [t], getTracks: () => [t] }; };
window.__emit = (m) => { const ch = window.__rtc.channels[window.__rtc.channels.length - 1]; ch.onmessage({ data: JSON.stringify(m) }); };
window.__types = () => window.__rtc.sent.map((m) => m.type);
"""


@pytest.fixture
def site(cfg, tmp_path, monkeypatch, own_listeners):
    import uvicorn

    import ask_fixture
    cfg.pop("listener_dir", None)
    cfg["overnight"]["live"]["page"].update(line_quiet_ms=300, intro_quiet_ms=600, intro_min_secs=0,
                                            resume_quiet_ms=300, change_close_ms=3000)
    listener = profiles.create("ian")
    profiles.set_active("ian")
    root = listener / "chapters" / "2026-11-17-why-herons-wait"
    ask_fixture.build(root, audio=True)
    profiles.write_json(root / "00_source" / "up_next.json", {"title": "Why Herons Wait", "overview": "Patience."})
    profiles.write_json(root / "11_episode" / "episode.json", {"title": "Why Herons Wait", "segments": []})
    (root / "intro_brief.md").write_text("## Title\n\nWhy Herons Wait\n\n## Findings you may mention\n\n- One.\n")
    profiles.write_json(listener / "up_next.json", {
        "id": "plan-1", "for_morning": "2026-11-18", "title": "How Owls Hear", "overview": "Ears, not eyes.",
        "summary": "A summary.", "status": "planned", "chapter": None})
    posts = []
    monkeypatch.setattr(live, "create_webrtc", lambda cfg, session, sdp, client=None:
                        posts.append(session) or {"id": f"live_{len(posts)}", "sdp": "answer-sdp"})
    monkeypatch.setattr(memory, "after_session", lambda *a, **k: False)
    monkeypatch.setattr(schedule, "can_change", lambda card, settings, now: (True, ""))

    def replan(cfg, listener, request=None, now=None):
        card = profiles.read_json(listener / "up_next.json")
        profiles.write_json(listener / "up_next.json", {**card, "id": "plan-2", "title": "Octopus Eyes"})
    monkeypatch.setattr(planner, "plan", replan)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(server.create_app(server.Pipeline(), cfg, remember_on_start=False),
                                        host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 10
    while not srv.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield NS(url=f"http://127.0.0.1:{port}", listener=listener, root=root, cfg=cfg, posts=posts)
    srv.should_exit = True
    t.join(5)


@pytest.fixture
def page(site):
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        except Exception as e:
            pytest.skip(f"Chromium is not installed for Playwright: {e}")
        pg = browser.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.add_init_script(STUB)
        ends = []
        pg.on("request", lambda r: ends.append(json.loads(r.post_data)) if r.url.endswith("/api/overnight/live/end") else None)
        pg.goto(site.url + "/library")
        pg.wait_for_function("document.getElementById('latest-title').textContent.length > 0")
        yield NS(p=pg, errors=errors, ends=ends)
        browser.close()


def say(pg, text, start=0):
    for i, w in enumerate(text.split()):
        pg.evaluate(f"window.__emit({json.dumps({'type': 'session.output_transcript.delta', 'delta': ' ' + w, 'start_ms': start + i * 200})})")


def tool(pg, name, call_id, args=None):
    item = {"type": "function_call", "name": name, "call_id": call_id, "arguments": json.dumps(args or {})}
    pg.evaluate(f"window.__emit({json.dumps({'type': 'response.event', 'event': {'type': 'response.output_item.done', 'item': item}})})")


def start_session(pg):
    pg.wait_for_function("window.__rtc.channels.length > 0")
    pg.evaluate("window.__emit({type: 'session.started', session: {id: 'live_x'}})")


def test_the_cards(site, page):
    pg = page.p
    assert pg.inner_text("#up-title") == "How Owls Hear" and pg.is_visible("#change")
    assert pg.inner_text("#latest-title") == "Why Herons Wait" and pg.is_visible("#episode")
    assert "min" in pg.inner_text("#latest-meta")
    pg.click("#settings-link")
    pg.uncheck("#s-enabled")
    pg.click("#s-save")
    pg.wait_for_function("document.getElementById('up-title').textContent === 'Overnight chapters are off'")
    assert profiles.settings(site.cfg, "ian")["overnight"]["enabled"] is False and not pg.is_visible("#change")
    assert page.errors == []


def test_play_opens_the_intro_and_the_tool_starts_the_recording(site, page):
    pg = page.p
    pg.click("#play")
    start_session(pg)
    pg.wait_for_function("window.__types().includes('session.commentary.append')")
    first = [m for m in pg.evaluate("window.__rtc.sent") if m["type"] == "session.commentary.append"][0]
    assert "Why Herons Wait" in first["content"]
    assert site.posts[-1]["delegation"]["responses"]["tools"][0]["name"] == "start_chapter"
    assert pg.evaluate("document.getElementById('audio').paused")  # nothing plays until the intro hands over
    say(pg, "Morning! I made you one about herons. OK, let's listen.")
    tool(pg, "start_chapter", "c1")
    pg.wait_for_function("window.__types().includes('response.create')")
    pg.wait_for_function("!document.getElementById('audio').paused", timeout=8000)
    assert pg.evaluate("document.getElementById('audio').currentTime") < 2  # from the beginning
    assert page.ends[-1]["how"] == "tool" and "session.close" in pg.evaluate("window.__types()")
    assert pg.inner_text("#play") == "Ask a question" and page.errors == []


def test_without_the_tool_the_lead_in_line_and_a_silence_start_it(site, page):
    pg = page.p
    pg.click("#play")
    start_session(pg)
    say(pg, "I found something odd about herons. Here it is.")
    pg.wait_for_function("!document.getElementById('audio').paused", timeout=8000)
    assert page.ends[-1]["how"] == "lead-in"


def test_at_ninety_seconds_at_the_latest(site, page, monkeypatch):
    pg = page.p
    site.cfg["overnight"]["live"]["page"]["intro_max_secs"] = 2
    pg.click("#play")
    start_session(pg)
    say(pg, "Good morning, this one took a while to find.")
    pg.wait_for_function("!document.getElementById('audio').paused", timeout=8000)
    assert page.ends[-1]["how"] == "timeout"


def test_a_tap_during_the_recording_asks_and_resumes_with_the_rewind(site, page):
    pg = page.p
    pg.click("#play")
    start_session(pg)
    tool(pg, "start_chapter", "c1")
    say(pg, "Here it is.")
    pg.wait_for_function("!document.getElementById('audio').paused", timeout=8000)
    pg.evaluate("document.getElementById('audio').currentTime = 20")
    pg.wait_for_timeout(300)
    pg.click("#play")  # "Ask a question"
    pg.wait_for_function("document.getElementById('audio').paused")
    pg.wait_for_function("window.__rtc.channels.length > 1")
    start_session(pg)
    assert pg.inner_text("#play") == "Back to the chapter"
    assert [t["name"] for t in site.posts[-1]["delegation"]["responses"]["tools"]] == ["resume_chapter", "save_seed"]
    pg.wait_for_function("window.__types().includes('session.instructions.append')")
    say(pg, "Because the fish can't see it. Anyway, where were we.")
    tool(pg, "resume_chapter", "c2", {"reason": "closing"})
    pg.wait_for_function("!document.getElementById('audio').paused", timeout=8000)
    t = pg.evaluate("document.getElementById('audio').currentTime")
    from ask import aside as A
    restart = A.load_asides(site.root)[-1]["restart"]
    assert restart["t"] <= 20.5 and abs(t - restart["t"]) < 1.5  # from the restart point, not where it paused
    assert page.errors == []


def test_change_talks_it_over_and_the_card_refreshes(site, page):
    pg = page.p
    pg.click("#change")
    start_session(pg)
    pg.wait_for_function("window.__types().includes('session.commentary.append')")
    first = [m for m in pg.evaluate("window.__rtc.sent") if m["type"] == "session.commentary.append"][0]
    assert "How Owls Hear" in first["content"]
    say(pg, "Octopus eyes it is: I'll make you one on how they see.")
    tool(pg, "set_next_chapter", "c9", {"mode": "topic", "topic": "Octopus eyes"})
    pg.wait_for_function("document.getElementById('up-title').textContent === 'Octopus Eyes'", timeout=15000)
    assert page.ends[-1]["how"] == "tool" and page.errors == []


def test_read_live_reads_the_chapter_in_the_same_session(site, page):
    """playback.read_live: start_chapter switches the session into reading mode instead of closing it."""
    pg = page.p
    profiles.save_settings(site.cfg, {"playback": {"read_live": True}}, "ian")
    pg.click("#play")
    start_session(pg)
    tool(pg, "start_chapter", "c1")
    say(pg, "Here it is.")
    pg.wait_for_function("window.__rtc.sent.some((m) => m.type === 'session.commentary.append' && m.content.startsWith('Read this passage'))", timeout=8000)
    sent = pg.evaluate("window.__rtc.sent")
    assert "session.input_audio.mute" in [m["type"] for m in sent] and "session.close" not in [m["type"] for m in sent]
    reads = [m["content"] for m in sent if m["type"] == "session.commentary.append" and m["content"].startswith("Read this")]
    first = reads[0].split(":", 1)[1].strip()
    say(pg, first)
    pg.wait_for_function("window.__rtc.sent.filter((m) => m.type === 'session.commentary.append' && m.content.startsWith('Read this')).length === 2", timeout=8000)
    assert pg.evaluate("document.getElementById('audio').paused")  # the guide reads; the recording stays off
    pg.click("#play")  # "Stop"
    pg.wait_for_function("window.__types().includes('session.close')")
    assert page.ends[-1]["how"] == "read-live-stopped" and page.errors == []
