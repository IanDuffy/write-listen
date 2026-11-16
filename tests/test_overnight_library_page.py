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
window.__rtc = { sent: [], channels: [], pcs: [], closed: 0, mics: 0, stopped: 0 };
class FakeDC { constructor() { this.readyState = 'open'; window.__rtc.channels.push(this); }
  send(x) { window.__rtc.sent.push(JSON.parse(x)); } close() {} }
class FakePC { constructor() { this.iceGatheringState = 'complete'; this.connectionState = 'new'; window.__rtc.pcs.push(this); }
  addTrack() {} createDataChannel() { this.dc = new FakeDC(); return this.dc; }
  async createOffer() { return { type: 'offer', sdp: 'offer-sdp' }; }
  async setLocalDescription(d) { this.localDescription = d; }
  async setRemoteDescription(d) { if (window.__failRemote) throw new Error('bad answer'); this.remote = d; }
  addEventListener() {} close() { window.__rtc.closed++; } }
window.RTCPeerConnection = FakePC;
navigator.mediaDevices.getUserMedia = async () => {
  if (window.__micDelay) await new Promise((r) => setTimeout(r, window.__micDelay));
  if (window.__realMic) return window.__realMic();
  window.__rtc.mics++;
  const t = { enabled: true, stop() { window.__rtc.stopped++; } };
  return { getAudioTracks: () => [t], getTracks: () => [t] }; };
window.__emit = (m) => { const ch = window.__rtc.channels[window.__rtc.channels.length - 1]; ch.onmessage({ data: JSON.stringify(m) }); };
window.__emitTo = (i, m) => window.__rtc.channels[i].onmessage({ data: JSON.stringify(m) });
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
        pg.on("request", lambda r: ends.append(json.loads(r.post_data)) if r.url.endswith("/api/overnight/live/end")
              and r.post_data else None)  # a beacon's body is not shown: the server's log has it
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
    pg.wait_for_function("window.__library.session")  # connected: tool calls now reach the server


def test_the_cards(site, page):
    pg = page.p
    assert pg.inner_text("#up-title") == "How Owls Hear" and pg.is_visible("#change")
    assert pg.inner_text("#latest-title") == "Why Herons Wait"  # the latest card in the strip opens its chapter page
    assert pg.get_attribute("#latest-title", "href") == "#/chapter/2026-11-17-why-herons-wait"
    assert "min" in pg.inner_text("#latest-meta").lower()
    pg.click("#settings-link")
    pg.uncheck("#s-enabled")
    pg.click("#s-save")
    pg.wait_for_function("document.getElementById('up-title').textContent === 'Overnight chapters are off'")
    assert profiles.settings(site.cfg, "ian")["overnight"]["enabled"] is False and not pg.is_visible("#change")
    assert page.errors == []


def test_play_opens_the_intro_and_the_tool_starts_the_recording(site, page):
    pg = page.p
    pg.click("#lib-play")
    start_session(pg)
    pg.wait_for_function("window.__types().includes('session.commentary.append')")
    first = [m for m in pg.evaluate("window.__rtc.sent") if m["type"] == "session.commentary.append"][0]
    assert "Why Herons Wait" in first["content"]
    assert site.posts[-1]["delegation"]["responses"]["tools"][0]["name"] == "start_chapter"
    assert pg.evaluate("document.getElementById('chapter-audio').paused")  # nothing plays until the intro hands over
    say(pg, "Morning! I made you one about herons. OK, let's listen.")
    tool(pg, "start_chapter", "c1")
    pg.wait_for_function("window.__types().includes('response.create')")
    pg.wait_for_function("!document.getElementById('chapter-audio').paused", timeout=8000)
    assert pg.evaluate("document.getElementById('chapter-audio').currentTime") < 2  # from the beginning
    assert page.ends[-1]["how"] == "tool" and "session.close" in pg.evaluate("window.__types()")
    assert pg.inner_text("#lib-play") == "Ask a question" and page.errors == []


def test_without_the_tool_the_lead_in_line_and_a_silence_start_it(site, page):
    pg = page.p
    pg.click("#lib-play")
    start_session(pg)
    say(pg, "I found something odd about herons. Here it is.")
    pg.wait_for_function("!document.getElementById('chapter-audio').paused", timeout=8000)
    assert page.ends[-1]["how"] == "lead-in"


def test_at_ninety_seconds_at_the_latest(site, page, monkeypatch):
    pg = page.p
    site.cfg["overnight"]["live"]["page"]["intro_max_secs"] = 2
    pg.click("#lib-play")
    start_session(pg)
    say(pg, "Good morning, this one took a while to find.")
    pg.wait_for_function("!document.getElementById('chapter-audio').paused", timeout=8000)
    assert page.ends[-1]["how"] == "timeout"


def test_a_tap_during_the_recording_asks_and_resumes_with_the_rewind(site, page):
    pg = page.p
    pg.click("#lib-play")
    start_session(pg)
    tool(pg, "start_chapter", "c1")
    say(pg, "Here it is.")
    pg.wait_for_function("!document.getElementById('chapter-audio').paused", timeout=8000)
    pg.evaluate("document.getElementById('chapter-audio').currentTime = 20")
    pg.wait_for_timeout(300)
    pg.click("#lib-play")  # "Ask a question"
    pg.wait_for_function("document.getElementById('chapter-audio').paused")
    pg.wait_for_function("window.__rtc.channels.length > 1")
    start_session(pg)
    assert pg.inner_text("#lib-play") == "Back to the chapter"
    assert [t["name"] for t in site.posts[-1]["delegation"]["responses"]["tools"]] == ["resume_chapter", "save_seed"]
    pg.wait_for_function("window.__types().includes('session.instructions.append')")
    say(pg, "Because the fish can't see it. Anyway, where were we.")
    tool(pg, "resume_chapter", "c2", {"reason": "closing"})
    pg.wait_for_function("!document.getElementById('chapter-audio').paused", timeout=8000)
    t = pg.evaluate("document.getElementById('chapter-audio').currentTime")
    from ask import aside as A
    restart = A.load_asides(site.root)[-1]["restart"]
    assert restart["t"] <= 20.5 and abs(t - restart["t"]) < 1.5  # from the restart point, not where it paused
    assert page.errors == []


def test_change_talks_it_over_and_the_card_refreshes(site, page):
    pg = page.p
    pg.click("#change")
    start_session(pg)
    pg.wait_for_function("window.__types().includes('session.commentary.append')")
    assert "How Owls Hear" in site.posts[-1]["instructions"]  # the plan, in the session's context
    say(pg, "Octopus eyes it is: I'll make you one on how they see.")
    tool(pg, "set_next_chapter", "c9", {"mode": "topic", "topic": "Octopus eyes"})
    pg.wait_for_function("document.getElementById('up-title').textContent === 'Octopus Eyes'", timeout=15000)
    assert page.ends[-1]["how"] == "tool" and page.errors == []


def test_read_live_reads_the_chapter_in_the_same_session(site, page):
    """playback.read_live: start_chapter switches the session into reading mode instead of closing it."""
    pg = page.p
    profiles.save_settings(site.cfg, {"playback": {"read_live": True}}, "ian")
    pg.click("#lib-play")
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
    assert pg.evaluate("document.getElementById('chapter-audio').paused")  # the guide reads; the recording stays off
    pg.click("#lib-play")  # "Stop"
    pg.wait_for_function("window.__types().includes('session.close')")
    assert page.ends[-1]["how"] == "read-live-stopped" and page.errors == []


# --- the session lifecycle (review findings C9, C10, C12, C13, C14, C16, C29) ----------------------------

def server_log(site):
    p = site.listener / "sessions" / "log.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def wait_for(fn, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = fn()
        if got:
            return got
        time.sleep(0.05)
    raise AssertionError("timed out waiting")


def playing(pg):
    pg.wait_for_function("!document.getElementById('chapter-audio').paused", timeout=8000)


def into_the_recording(pg):
    pg.click("#lib-play")
    start_session(pg)
    tool(pg, "start_chapter", "c1")
    say(pg, "Here it is.")
    playing(pg)


def test_a_tap_while_the_intro_connects_abandons_it(site, page):
    """C9: the server had opened GPT-Live; the tap goes straight to the chapter and ends that session."""
    pg = page.p
    pg.click("#lib-play")
    pg.wait_for_function("window.__library.opening")  # /start returned; not started yet
    pg.click("#lib-play")  # "Skip to the chapter"
    playing(pg)
    pg.evaluate("window.__emitTo(0, {type: 'session.started'})")  # the abandoned session starting late
    pg.wait_for_timeout(700)
    assert "session.commentary.append" not in pg.evaluate("window.__types()")  # its intro never speaks
    assert pg.evaluate("[window.__rtc.closed, window.__rtc.mics, window.__rtc.stopped]") == [1, 1, 1]
    ended = wait_for(lambda: server_log(site))
    assert ended[-1]["ended"] == "abandoned" and ended[-1]["mode"] == "intro"
    assert pg.inner_text("#lib-play") == "Ask a question" and page.errors == []


def test_a_tap_before_the_server_opened_anything_opens_nothing(site, page):
    pg = page.p
    pg.evaluate("window.__micDelay = 800")
    pg.click("#lib-play")
    pg.click("#lib-play")
    playing(pg)
    pg.wait_for_timeout(1300)
    assert site.posts == [] and pg.evaluate("[window.__rtc.mics, window.__rtc.stopped]") == [1, 1]
    assert pg.evaluate("window.__library.pc === null && window.__library.stream === null") and page.errors == []


def test_back_to_the_chapter_while_the_aside_connects(site, page):
    from ask import aside as A
    pg = page.p
    into_the_recording(pg)
    pg.click("#lib-play")  # "Ask a question"
    pg.wait_for_function("window.__library.opening")
    pg.click("#lib-play")  # "Back to the chapter"
    playing(pg)
    pg.evaluate("window.__emitTo(1, {type: 'session.started'})")
    pg.wait_for_timeout(500)
    assert "session.instructions.append" not in pg.evaluate("window.__types()")
    ended = wait_for(lambda: [x for x in server_log(site) if x["mode"] == "ask"])
    assert ended[-1]["ended"] == "abandoned" and A.load_asides(site.root) == []
    assert pg.inner_text("#lib-play") == "Ask a question" and page.errors == []


def test_one_conversation_at_a_time(site, page):
    """C10: Change waits while the intro is on; Change pauses the recording; Play ends Change first."""
    pg = page.p
    pg.click("#lib-play")
    start_session(pg)
    pg.click("#change")
    pg.wait_for_timeout(500)
    assert len(site.posts) == 1  # no second session over the intro
    tool(pg, "start_chapter", "c1")
    say(pg, "Here it is.")
    playing(pg)
    pg.click("#change")
    pg.wait_for_function("document.getElementById('chapter-audio').paused")  # the recording waits
    pg.wait_for_function("window.__rtc.channels.length > 1")
    start_session(pg)
    pg.wait_for_function("document.getElementById('change').textContent === 'Done'")
    assert len(site.posts) == 2
    pg.click("#lib-play")  # Play: Change ends first, then the recording goes on
    playing(pg)
    ended = wait_for(lambda: [x for x in server_log(site) if x["mode"] == "change"])
    assert ended[-1]["ended"] == "button" and pg.inner_text("#change") == "Change"
    assert pg.evaluate("window.__library.pc === null && window.__library.session === null")
    assert pg.evaluate("window.__rtc.closed") == 2 and page.errors == []


def test_a_failed_start_releases_the_microphone(site, page, monkeypatch):
    """C14: GPT-Live refuses (no key): the chapter plays and nothing stays captured."""
    from stages.common import StageError

    def refuse(*a, **k):
        raise StageError("No OpenAI key: set OPENAI_API_KEY in .env.")
    monkeypatch.setattr(live, "create_webrtc", refuse)
    pg = page.p
    pg.click("#lib-play")
    playing(pg)
    assert pg.evaluate("[window.__rtc.closed, window.__rtc.mics, window.__rtc.stopped]") == [1, 1, 1]
    assert pg.evaluate("window.__library.pc === null && window.__library.stream === null") and page.errors == []


def test_a_start_that_fails_after_the_server_opened_it_is_ended(site, page):
    """C13: the server's side of a session that never connected is ended (its cost logged)."""
    pg = page.p
    pg.evaluate("window.__failRemote = true")
    pg.click("#lib-play")
    playing(pg)
    ended = wait_for(lambda: server_log(site))
    assert ended[-1]["ended"] == "failed" and pg.evaluate("window.__rtc.stopped") == 1 and page.errors == []


def test_closing_the_page_ends_the_session(site, page):
    """C13: pagehide sends the end as a beacon."""
    pg = page.p
    pg.click("#lib-play")
    start_session(pg)
    pg.wait_for_function("window.__types().includes('session.commentary.append')")
    pg.goto("about:blank")
    ended = wait_for(lambda: server_log(site))
    assert ended[-1]["ended"] == "pagehide" and ended[-1]["mode"] == "intro"


def test_a_lead_in_right_after_they_ask_to_skip_starts_the_chapter(site, page):
    """C12: 'just play it' and 'Here it is.' at a few seconds: honoured inside intro_min_secs."""
    pg = page.p
    site.cfg["overnight"]["live"]["page"]["intro_min_secs"] = 20
    pg.click("#lib-play")
    start_session(pg)
    pg.evaluate("window.__emit({type: 'session.input_transcript.delta', delta: 'Just play it.'})")
    pg.wait_for_timeout(500)
    say(pg, "Sure. Here it is.")
    playing(pg)
    assert page.ends[-1]["how"] == "lead-in"


def test_a_lead_in_needs_them_to_ask_before_the_minimum(site, page):
    pg = page.p
    site.cfg["overnight"]["live"]["page"]["intro_min_secs"] = 20
    pg.click("#lib-play")
    start_session(pg)
    say(pg, "Morning. Here it is.")
    pg.wait_for_timeout(2000)
    assert pg.evaluate("document.getElementById('chapter-audio').paused")


def test_a_stray_enjoy_or_a_lead_in_mid_line_does_not_cut_the_guide_off(site, page):
    pg = page.p
    assert "enjoy" not in site.cfg["overnight"]["live"]["page"]["lead_in"]
    pg.click("#lib-play")
    start_session(pg)
    say(pg, "I think you'll enjoy this one. Have you ever watched a heron fish?")
    pg.wait_for_timeout(1800)
    say(pg, "Here it is, the odd part: herons wait because the water bends the light they hunt by.")
    pg.wait_for_timeout(2200)
    assert pg.evaluate("document.getElementById('chapter-audio').paused") and page.errors == []


def test_a_second_change_ended_by_done_is_not_taken_for_the_first(site, page):
    """C16: the first Change's set_next_chapter result no longer makes Done poll for a new card."""
    pg = page.p
    pg.click("#change")
    start_session(pg)
    tool(pg, "set_next_chapter", "c9", {"mode": "topic", "topic": "Octopus eyes"})
    pg.wait_for_function("document.getElementById('up-title').textContent === 'Octopus Eyes'", timeout=15000)
    pg.wait_for_function("document.getElementById('change').textContent === 'Change'")
    pg.click("#change")
    pg.wait_for_function("window.__rtc.channels.length > 1")
    start_session(pg)
    pg.wait_for_function("document.getElementById('change').textContent === 'Done'")
    pg.click("#change")  # Done, before any tool
    wait_for(lambda: len(page.ends) == 2)
    assert page.ends[-1]["how"] == "button"
    assert pg.inner_text("#up-line") != "Rewriting the card…" and page.errors == []


def test_a_conversation_is_recorded_and_kept(site, page):
    """C29: the mic and the guide's voice, mixed, recorded and uploaded to sessions/<id>.webm."""
    pg = page.p
    pg.evaluate("""window.__realMic = async () => {
      const ac = new AudioContext();
      const tone = (hz) => { const o = ac.createOscillator(), d = ac.createMediaStreamDestination();
                             o.frequency.value = hz; o.connect(d); o.start(); return d.stream; };
      window.__guideVoice = () => tone(660);
      return tone(440); }""")
    pg.click("#lib-play")
    start_session(pg)
    pg.evaluate("window.__rtc.pcs[0].ontrack({ streams: [window.__guideVoice()] })")
    pg.wait_for_function("window.__library.rec && window.__library.rec.mr && window.__library.rec.mr.state === 'recording'")
    assert pg.evaluate("window.__library.rec.nodes.length") == 2  # both sides
    pg.wait_for_timeout(1500)
    tool(pg, "start_chapter", "c1")
    say(pg, "Here it is.")
    playing(pg)
    sid = page.ends[-1]["session"]
    path = site.listener / "sessions" / f"{sid}.webm"
    assert wait_for(lambda: path.exists() and path.stat().st_size > 0) and page.errors == []
