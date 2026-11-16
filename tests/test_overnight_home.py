"""The library mounted on the main page (overnight/DESIGN.md "Round 2"; page/index.html, page/library.js), in
headless Chromium against the real server, GPT-Live's session opening faked and WebRTC stubbed in the page (the
pattern of test_overnight_library_page.py). Covers: the orb on top and the library (the chapter strip,
docs/library-timeline.md) below it; one player that keeps playing while the listener moves between the home, a
chapter page and its transcript; a sentence click seeking (only on the latest chapter, the one that plays); Skip the intro playing with no session; the typed note; the lines still being heard saved before a
session ends; the orb's audio and the chapter player pausing each other; the orb's voice guide and the library's
player and sessions never running together (the ElevenLabs client stubbed as in test_page_voice.py); every play
and seek closing a live session first; the chapter view taking over the orb's audio; Ask only where it can work."""

import json
import socket
import threading
import time
from types import SimpleNamespace as NS

import pytest
from overnight_plan_fakes import offline  # noqa: F401
from test_overnight_library_page import STUB, say, start_session, tool  # noqa: F401
from test_page_voice import STUB as EL_STUB

import server
from overnight import live, profiles, schedule
from stages import memory, voice

sync_api = pytest.importorskip("playwright.sync_api")

LATEST, OLDER = "2026-11-17-why-herons-wait", "2026-11-16-octopus-eyes"
REFS = [{"id": "r1", "kind": "work", "title": "The Heron Book", "creator": "A. Writer", "start": 3.0, "url": None},
        {"id": "r2", "kind": "source", "title": "A study of herons", "start": 80.0, "url": "https://example.org/herons"}]


def chapter(listener, name, title, finished, refs=(), intro=False):
    import ask_fixture
    root = listener / "chapters" / name
    ask_fixture.build(root, audio=True)
    profiles.write_json(root / "00_source" / "up_next.json", {"title": title, "overview": f"About {title.lower()}."})
    t = json.loads((root / "11_episode" / "timing.json").read_text())
    profiles.write_json(root / "11_episode" / "episode.json", {
        "title": title, "angle": f"About {title.lower()}.", "audio": "10_audio/chapter.mp3", "duration": t["duration"],
        "segments": t["segments"], "references": list(refs)})
    profiles.write_json(root / "finished.json", {"t": finished})
    if intro:
        (root / "intro_brief.md").write_text(f"## Title\n\n{title}\n\n## Findings you may mention\n\n- One.\n")
    return root


@pytest.fixture
def site(cfg, tmp_path, monkeypatch, own_listeners):
    import uvicorn
    cfg.pop("listener_dir", None)
    cfg["voice"]["client_js"] = "/stub-el.js"
    cfg["overnight"]["live"]["page"].update(line_quiet_ms=300, intro_quiet_ms=600, intro_min_secs=0,
                                            resume_quiet_ms=300, change_close_ms=3000)
    listener = profiles.create("ian")
    profiles.set_active("ian")
    older = chapter(listener, OLDER, "Octopus Eyes", "2026-11-16T04:00:00")
    root = chapter(listener, LATEST, "Why Herons Wait", "2026-11-17T04:00:00", refs=REFS, intro=True)
    profiles.write_json(listener / "up_next.json", {
        "id": "plan-1", "for_morning": "2026-11-18", "title": "How Owls Hear", "overview": "Ears, not eyes.",
        "summary": "A summary.", "status": "planned", "chapter": None})
    posts = []
    monkeypatch.setattr(live, "create_webrtc", lambda cfg, session, sdp, client=None:
                        posts.append(session) or {"id": f"live_{len(posts)}", "sdp": "answer-sdp"})
    monkeypatch.setattr(memory, "after_session", lambda *a, **k: False)
    monkeypatch.setattr(schedule, "can_change", lambda card, settings, now: (True, ""))
    monkeypatch.setattr(voice, "signed_url", lambda *a, **k: "wss://example/signed")  # the orb's guide, stubbed
    monkeypatch.setattr(server, "_save_voice", lambda *a, **k: None)
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
    yield NS(url=f"http://127.0.0.1:{port}", listener=listener, root=root, older=older, cfg=cfg, posts=posts)
    srv.should_exit = True
    t.join(5)


@pytest.fixture
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        except Exception as e:
            pytest.skip(f"Chromium is not installed for Playwright: {e}")
        yield b
        b.close()


def open_page(site, browser, path="/", voice=False):
    """voice: the orb's ElevenLabs client stubbed (window.__el), so a tap on the orb starts a voice conversation."""
    pg = browser.new_page(viewport={"width": 1000, "height": 760})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.add_init_script(STUB)
    pg.route(site.url + "/stub-el.js", lambda r: r.fulfill(status=200, content_type="text/javascript",
                                                           body=EL_STUB if voice else ""))
    ends = []
    pg.on("request", lambda r: ends.append(json.loads(r.post_data)) if r.url.endswith("/api/overnight/live/end")
          and r.post_data else None)
    pg.goto(site.url + path)
    pg.wait_for_function("document.getElementById('latest-title') && document.getElementById('latest-title').textContent.length > 0")
    return NS(p=pg, errors=errors, ends=ends)


AUDIO = "document.getElementById('chapter-audio')"


def playing(pg):
    pg.wait_for_function(f"!{AUDIO}.paused && {AUDIO}.currentTime > 0", timeout=8000)


def audio_state(pg):
    return pg.evaluate(f"({{src: {AUDIO}.src, t: {AUDIO}.currentTime, paused: {AUDIO}.paused}})")


def test_the_home_shows_the_orb_and_the_library_below_it(site, browser):
    page = open_page(site, browser)
    pg = page.p
    assert pg.is_visible("#orb") and pg.inner_text("#line")  # the orb's screen, as it was
    height = pg.evaluate("innerHeight")
    assert pg.evaluate("document.getElementById('library').getBoundingClientRect().top") >= height - 1  # below it
    assert pg.evaluate("document.getElementById('orb').getBoundingClientRect().bottom") <= height
    pg.locator("#strip").scroll_into_view_if_needed()
    assert pg.inner_text("#up-title") == "How Owls Hear" and pg.is_visible("#change") and pg.is_visible("#note-open")
    assert pg.inner_text("#latest-title") == "Why Herons Wait"
    titles = pg.eval_on_selector_all("#strip .lt-ch a.lt-ctitle", "els => els.map((e) => e.textContent)")
    assert titles == ["Octopus Eyes", "Why Herons Wait"]  # every chapter, oldest first (the strip)
    hrefs = pg.eval_on_selector_all("#strip .lt-ch a.lt-ctitle", "els => els.map((e) => e.getAttribute('href'))")
    assert hrefs == [f"#/chapter/{OLDER}", f"#/chapter/{LATEST}"]  # each card opens its chapter page
    assert pg.eval_on_selector_all("#strip .lt-ch", "els => els.map((e) => !!e.querySelector('.lt-play'))") == [False, True]
    assert not pg.is_visible(".lt-orb")  # the page's own orb is the guide circle here
    assert not pg.is_visible("#player")  # nothing loaded yet
    assert page.errors == []


def test_moving_between_the_home_and_a_chapter_keeps_the_same_audio_playing(site, browser):
    page = open_page(site, browser)
    pg = page.p
    pg.click("#lib-skip")  # the recording at once: no intro, no mic
    playing(pg)
    assert site.posts == [] and pg.evaluate("window.__rtc.mics") == 0
    assert pg.is_visible("#player") and pg.inner_text("#player-title") == "Why Herons Wait"
    first = audio_state(pg)
    assert first["src"].endswith(f"/api/chapters/{LATEST}/audio")
    pg.click("#latest-title")  # its chapter page
    pg.wait_for_function(f"location.hash === '#/chapter/{LATEST}'")
    pg.wait_for_function("document.documentElement.classList.contains('lib-in-chapter')")
    assert not pg.is_visible("#orb") and not pg.is_visible("#lib-home") and pg.is_visible("#lib-page")
    assert pg.is_visible("#player") and pg.get_attribute("#pg-play", "aria-label") == "Pause chapter"
    pg.click("#pg-transcript")  # its transcript: read along with the player
    pg.wait_for_function("document.querySelectorAll('#ch-text .lib-s').length > 0")
    assert pg.evaluate("location.hash") == f"#/chapter/{LATEST}/transcript"
    pg.wait_for_function("document.querySelector('#ch-text .lib-s.current')")
    pg.wait_for_timeout(600)
    mid = audio_state(pg)
    assert mid["src"] == first["src"] and not mid["paused"] and mid["t"] > first["t"]
    pg.click("#lib-back")  # to the chapter page, then to the library
    pg.wait_for_function(f"location.hash === '#/chapter/{LATEST}'")
    pg.click("#pg-back")
    pg.wait_for_function("location.hash === '#/' && !document.documentElement.classList.contains('lib-in-chapter')")
    assert pg.is_visible("#orb") and pg.is_visible("#lib-home")
    pg.wait_for_timeout(600)
    end = audio_state(pg)
    assert end["src"] == first["src"] and not end["paused"] and end["t"] > mid["t"]
    pg.go_back()  # the browser's Back: into the chapter view again, still the same audio
    pg.wait_for_function("document.documentElement.classList.contains('lib-in-chapter')")
    back = audio_state(pg)
    assert back["src"] == first["src"] and not back["paused"] and back["t"] >= end["t"]
    assert page.errors == []


def test_a_sentence_click_seeks_and_plays(site, browser):
    page = open_page(site, browser, f"/#/chapter/{LATEST}/transcript")
    pg = page.p
    pg.wait_for_function("document.querySelectorAll('#ch-text .lib-s').length > 20")
    assert pg.inner_text("#ch-title") == "Why Herons Wait"
    assert pg.eval_on_selector_all("#ch-cardlist .lib-ref .title", "els => els.map((e) => e.textContent)") == [
        "The Heron Book", "A study of herons"]
    import ask_fixture
    starts = ask_fixture.sentence_starts(site.root)
    target = "seg02-s004"
    pg.click(f"#ch-text .lib-s[data-id='{target}']")
    playing(pg)
    pg.wait_for_function(f"document.getElementById('library').dataset.sentence === '{target}'", timeout=5000)
    assert abs(audio_state(pg)["t"] - starts[target]) < 1.5
    assert pg.is_visible("#player") and pg.inner_text("#ch-play") == "Pause"
    assert pg.evaluate("document.querySelector('#ch-cardlist .lib-ref.latest .title').textContent") == "A study of herons"
    earlier = "seg01-s002"
    pg.click(f"#ch-text .lib-s[data-id='{earlier}']")
    pg.wait_for_function(f"document.getElementById('library').dataset.sentence === '{earlier}'", timeout=5000)
    assert abs(audio_state(pg)["t"] - starts[earlier]) < 1.5 and not audio_state(pg)["paused"]
    assert site.posts == [] and page.errors == []


def test_an_older_chapters_transcript_reads_but_does_not_play(site, browser):
    page = open_page(site, browser, f"/#/chapter/{OLDER}/transcript")
    pg = page.p
    pg.wait_for_function("document.querySelectorAll('#ch-text .lib-s').length > 20")
    assert pg.inner_text("#ch-title") == "Octopus Eyes" and not pg.is_visible("#ch-play")
    pg.click("#ch-text .lib-s[data-id='seg02-s004']")
    pg.wait_for_timeout(500)
    assert pg.evaluate(f"{AUDIO}.paused") and not pg.is_visible("#player") and page.errors == []


def test_skip_the_intro_plays_at_once_and_ask_runs_from_the_player(site, browser):
    page = open_page(site, browser, "/library")  # the standalone shell
    pg = page.p
    assert pg.is_visible("#lib-skip") and pg.is_visible("#lib-hint") and "microphone" in pg.inner_text("#lib-hint")
    pg.click("#lib-skip")
    playing(pg)
    assert site.posts == [] and pg.evaluate("window.__rtc.mics") == 0  # no session, no microphone
    assert audio_state(pg)["src"].endswith(f"/api/chapters/{LATEST}/audio")
    assert pg.inner_text("#lib-play") == "Ask a question" and not pg.is_visible("#lib-skip")
    pg.click("#player-ask")  # Ask, from the mini player
    pg.wait_for_function(f"{AUDIO}.paused")
    start_session(pg)
    assert [t["name"] for t in site.posts[-1]["delegation"]["responses"]["tools"]] == ["resume_chapter", "save_seed"]
    assert pg.inner_text("#player-ask") == "Back to the chapter"
    pg.click("#player-ask")
    pg.wait_for_function(f"!{AUDIO}.paused", timeout=8000)
    assert page.ends[-1]["how"] == "button" and pg.inner_text("#player-ask") == "Ask" and page.errors == []


def test_play_on_the_latest_says_the_guide_introduces_it_live(site, browser):
    page = open_page(site, browser, "/library")
    pg = page.p
    pg.click("#lib-play")
    pg.wait_for_function("document.getElementById('live').textContent.includes('live')")
    assert "introduces" in pg.inner_text("#live") and pg.inner_text("#lib-play") == "Skip to the chapter"
    start_session(pg)
    assert pg.inner_text("#live") == "" and site.posts[-1]["delegation"]["responses"]["tools"][0]["name"] == "start_chapter"
    assert page.errors == []


def test_a_typed_note_is_posted_and_shown_on_the_card(site, browser):
    page = open_page(site, browser)
    pg = page.p
    got = []

    def note(route):
        body = json.loads(route.request.post_data)
        got.append(body)
        card = profiles.read_json(site.listener / "up_next.json")
        n = {"t": "2026-11-17T19:00:00", "text": body["text"], "via": "typed"}
        profiles.write_json(site.listener / "up_next.json", {**card, "notes_added": [n]})
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"where": "card", "note": n, "message": "Added to tonight's plan."}))
    pg.route("**/api/overnight/note", note)
    assert not pg.is_visible("#note-box") and not pg.is_visible("#up-notes")
    pg.click("#note-open")
    assert pg.is_visible("#note-text")
    pg.fill("#note-text", "Leave out the owl myths; more on the ear shape.")
    pg.click("#note-send")
    pg.wait_for_function("document.getElementById('note-msg').textContent.length > 0")
    assert got == [{"text": "Leave out the owl myths; more on the ear shape."}]
    assert pg.inner_text("#note-msg") == "Added to tonight's plan." and not pg.is_visible("#note-box")
    pg.wait_for_function("document.querySelectorAll('#up-notes li').length === 1")
    assert pg.inner_text("#up-notes li") == "Leave out the owl myths; more on the ear shape."
    assert site.posts == [] and page.errors == []  # typed: no session, no microphone


def test_a_failed_note_says_so_and_keeps_the_text(site, browser):
    page = open_page(site, browser)
    pg = page.p
    pg.route("**/api/overnight/note", lambda r: r.fulfill(status=400, content_type="application/json",
                                                           body=json.dumps({"detail": "The note is empty."})))
    pg.click("#note-open")
    pg.fill("#note-text", "Something")
    pg.click("#note-send")
    pg.wait_for_function("document.getElementById('note-msg').textContent.includes('The note is empty.')")
    assert pg.input_value("#note-text") == "Something" and pg.is_visible("#note-box") and page.errors == []


def test_the_lines_still_being_heard_are_saved_before_the_end(site, browser):
    """A short session: the guide's and the listener's last lines were still being collected (line_quiet_ms not
    yet passed) when it ended; they reach the transcript before /end."""
    site.cfg["overnight"]["live"]["page"]["line_quiet_ms"] = 20000
    page = open_page(site, browser)
    pg = page.p
    pg.click("#change")
    start_session(pg)
    pg.wait_for_function("document.getElementById('change').textContent === 'Done'")
    pg.evaluate("window.__emit({type: 'session.input_transcript.delta', delta: 'Something on tides.'})")
    say(pg, "Tides it is.")
    pg.click("#change")  # Done, at once
    deadline = time.monotonic() + 8
    while not page.ends and time.monotonic() < deadline:
        pg.wait_for_timeout(50)  # Playwright's events arrive while the page is being driven
    sid = page.ends[-1]["session"]
    path = site.listener / "sessions" / f"{sid}.jsonl"
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        lines = [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
        if len([x for x in lines if x.get("who") == "session"]) >= 2:
            break
        time.sleep(0.05)
    said = [(x["who"], x.get("text")) for x in lines if x["who"] in ("listener", "guide")]
    assert said == [("listener", "Something on tides."), ("guide", "Tides it is.")]
    last_session = max(i for i, x in enumerate(lines) if x["who"] == "session")
    assert all(i < last_session for i, x in enumerate(lines) if x["who"] in ("listener", "guide"))  # before /end
    assert page.errors == []


def test_the_orb_audio_and_the_chapter_player_pause_each_other(site, browser):
    page = open_page(site, browser)
    pg = page.p
    pg.click("#lib-skip")
    playing(pg)
    pg.evaluate(f"(() => {{ const a = document.getElementById('audio'); a.src = '/api/chapters/{LATEST}/audio'; a.play(); }})()")
    pg.wait_for_function("!document.getElementById('audio').paused")
    pg.wait_for_function(f"{AUDIO}.paused")  # the orb's chapter started: the player paused
    pg.click("#player-toggle")
    playing(pg)
    pg.wait_for_function("document.getElementById('audio').paused")  # and the other way round
    assert page.errors == []


ORB_AUDIO = "document.getElementById('audio')"


def orb_plays(pg, run, at=0):
    """The orb's own chapter (#audio) playing `run` from `at` seconds."""
    pg.evaluate(f"(() => {{ const a = {ORB_AUDIO}; a.src = '/api/chapters/{run}/audio'; a.play().catch(() => {{}}); }})()")
    pg.wait_for_function(f"!{ORB_AUDIO}.paused")
    if at:
        pg.evaluate(f"{ORB_AUDIO}.currentTime = {at}")
        pg.wait_for_function(f"{ORB_AUDIO}.currentTime >= {at}")


def orb_talking(pg):
    pg.click("#orb")
    pg.wait_for_function("window.__el.sessions.length === 1 && window.Orb.busy()")


def watch_mic(pg):
    """Each microphone request records whether the orb's guide was still on and whether #audio was playing."""
    pg.evaluate("""(() => { const g = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices); window.__gum = [];
      navigator.mediaDevices.getUserMedia = (c) => { window.__gum.push({ orb: window.Orb.busy(),
        orbAudio: !document.getElementById('audio').paused }); return g(c); }; })()""")


def wait_end(page, n=1):
    deadline = time.monotonic() + 8
    while len(page.ends) < n and time.monotonic() < deadline:
        page.p.wait_for_timeout(50)
    return page.ends[-1]


# R1: the orb's guide and the library never talk or listen together.
def test_a_chapter_played_below_ends_the_orb_guide(site, browser):
    page = open_page(site, browser, voice=True)
    pg = page.p
    orb_talking(pg)
    pg.click("#lib-skip")
    playing(pg)
    pg.wait_for_function("window.__el.sessions[0].ended && !window.Orb.busy()")
    pg.wait_for_timeout(400)
    assert pg.evaluate("window.__el.sessions.length") == 1 and not pg.evaluate("resting")  # nothing woke it
    # Resting or coming back is refused while the player plays: the local listener would hear the chapter.
    assert pg.evaluate("(async () => { resting = true; const ok = await wake('spoke'); resting = false; return ok; })()") is False
    assert pg.evaluate("(() => { sessionId = 'gone'; rested('test'); return resting; })()") is False
    assert pg.evaluate("window.__el.sessions.length") == 1 and page.errors == []


def test_the_orb_pauses_the_chapter_player_before_it_listens(site, browser):
    page = open_page(site, browser, voice=True)
    pg = page.p
    pg.evaluate("""(() => { const C = window.ElevenLabsClient.Conversation, s = C.startSession; window.__heard = [];
      C.startSession = (o) => { window.__heard.push(document.getElementById('chapter-audio').paused); return s(o); }; })()""")
    pg.click("#lib-skip")
    playing(pg)
    pg.click("#orb")
    pg.wait_for_function("window.__el.sessions.length === 1")
    assert pg.evaluate("window.__heard") == [True] and pg.evaluate(f"{AUDIO}.paused")
    assert page.errors == []


def test_a_session_below_ends_the_orb_guide_before_the_mic_opens(site, browser):
    page = open_page(site, browser, voice=True)
    pg = page.p
    orb_talking(pg)
    watch_mic(pg)
    pg.click("#change")
    start_session(pg)
    assert pg.evaluate("window.__gum") == [{"orb": False, "orbAudio": False}]
    assert pg.evaluate("window.__el.sessions[0].ended") and page.errors == []


def test_an_orb_tap_ends_a_session_below_first(site, browser):
    page = open_page(site, browser, voice=True)
    pg = page.p
    pg.click("#change")
    start_session(pg)
    pg.click("#orb")
    pg.wait_for_function("window.__el.sessions.length === 1")
    assert wait_end(page)["how"] == "button" and not pg.evaluate("window.__library.session")
    assert pg.evaluate("window.__library.mode") is None and page.errors == []


# R2: a session below never opens the mic over the orb's chapter.
def test_change_pauses_the_orbs_chapter_before_the_mic_opens(site, browser):
    page = open_page(site, browser)
    pg = page.p
    orb_plays(pg, LATEST)
    watch_mic(pg)
    pg.click("#change")
    start_session(pg)
    assert pg.evaluate("window.__gum") == [{"orb": False, "orbAudio": False}] and pg.evaluate(f"{ORB_AUDIO}.paused")
    assert page.errors == []


# R3: every play and seek closes a live session first.
def test_the_player_toggle_closes_a_change_conversation_first(site, browser):
    page = open_page(site, browser)
    pg = page.p
    pg.click("#lib-skip")
    playing(pg)
    pg.click("#change")
    start_session(pg)
    pg.wait_for_function(f"{AUDIO}.paused")  # the recording waits while they talk it over
    pg.click("#player-toggle")
    playing(pg)
    assert wait_end(page)["how"] == "button"
    assert pg.evaluate("window.__library.session") is None and pg.evaluate("window.__library.mode") is None
    assert pg.evaluate("window.__rtc.closed") >= 1 and page.errors == []


def test_a_sentence_clicked_during_the_intro_plays_from_that_sentence(site, browser):
    import ask_fixture
    page = open_page(site, browser)
    pg = page.p
    pg.click("#lib-play")  # the intro, live
    start_session(pg)
    pg.evaluate(f"location.hash = '#/chapter/{LATEST}/transcript'")
    pg.wait_for_function("document.querySelectorAll('#ch-text .lib-s').length > 20")
    target = "seg02-s004"
    pg.click(f"#ch-text .lib-s[data-id='{target}']")
    playing(pg)
    assert abs(audio_state(pg)["t"] - ask_fixture.sentence_starts(site.root)[target]) < 1.5
    assert wait_end(page)["how"] == "skip" and pg.evaluate("window.__library.session") is None
    assert page.errors == []


# R4: the chapter view follows and controls the orb's chapter when it is the same one.
def test_the_chapter_view_takes_over_the_orbs_audio(site, browser):
    page = open_page(site, browser)
    pg = page.p
    orb_plays(pg, LATEST, at=40)
    pg.locator("#latest-title").scroll_into_view_if_needed()
    pg.click("#latest-title")  # the latest card: its chapter page
    pg.wait_for_function(f"location.hash === '#/chapter/{LATEST}'")
    playing(pg)
    pg.wait_for_function(f"{ORB_AUDIO}.paused")
    assert audio_state(pg)["t"] >= 40  # where the orb had got to, not the start
    assert pg.is_visible("#player") and pg.inner_text("#player-title") == "Why Herons Wait"
    assert pg.get_attribute("#pg-play", "aria-label") == "Pause chapter"
    pg.click("#pg-play")
    pg.wait_for_function(f"{AUDIO}.paused")
    t = audio_state(pg)["t"]
    assert t >= 40
    pg.click("#pg-play")
    playing(pg)
    pg.click("#pg-transcript")  # and the read-along follows it
    pg.wait_for_function("document.querySelector('#ch-text .lib-s.current')")
    assert pg.inner_text("#ch-play") == "Pause"
    assert audio_state(pg)["t"] >= t and site.posts == [] and page.errors == []


def test_play_on_the_latest_while_the_orb_plays_it_carries_on_without_an_intro(site, browser):
    page = open_page(site, browser)
    pg = page.p
    orb_plays(pg, LATEST, at=40)
    pg.locator("#lib-play").scroll_into_view_if_needed()
    pg.click("#lib-play")
    playing(pg)
    pg.wait_for_function(f"{ORB_AUDIO}.paused")
    assert audio_state(pg)["t"] >= 40 and site.posts == [] and pg.evaluate("window.__rtc.mics") == 0
    assert pg.inner_text("#lib-play") == "Ask a question" and page.errors == []


# R6: Ask only on a chapter with a timed transcript; when it can't start, the listener is told why.
def test_ask_is_not_offered_on_a_chapter_without_a_timed_transcript(site, browser):
    for name in ("timing.json", "episode.json"):
        (site.root / "11_episode" / name).unlink()
    page = open_page(site, browser)
    pg = page.p
    pg.click("#lib-skip")
    playing(pg)
    pg.wait_for_timeout(300)
    assert pg.is_visible("#player") and not pg.is_visible("#player-ask")
    assert pg.evaluate("window.__rtc.mics") == 0 and site.posts == [] and page.errors == []


def test_an_ask_that_cannot_start_says_why_and_the_chapter_goes_on(site, browser):
    page = open_page(site, browser)
    pg = page.p
    why = "This chapter has no timed transcript, so it can't be asked about yet."
    pg.route("**/api/overnight/live/start", lambda r: r.fulfill(status=400, content_type="application/json",
                                                                body=json.dumps({"detail": why})))
    pg.locator("#lib-skip").scroll_into_view_if_needed()
    pg.click("#lib-skip")
    playing(pg)
    pg.click("#player-ask")
    pg.wait_for_function(f"document.getElementById('player-line').textContent === {json.dumps(why)}")
    playing(pg)
    assert pg.evaluate("window.__library.mode") is None and page.errors == []


def test_the_orb_leaves_past_chapters_to_the_library(site, browser):
    """Demo (2026-11-15): the orb offered 'Play the last chapter: <the run touched last>' while the library
    below named another chapter as the latest. With the library mounted, the orb shows neither link for a
    past chapter."""
    pg = open_page(site, browser).p
    pg.wait_for_function("document.documentElement.classList.contains('has-library')")
    pg.evaluate("document.body.classList.add('past'); document.getElementById('last').textContent = 'Play the last chapter: x'")
    assert not pg.is_visible("#last")
