"""The library timeline's two screens in headless Chromium (docs/library-timeline.md, checks 2-8, 10, 11):
the chapter strip with its scrubber and From this chapter (sketch B), and the chapter page (sketch D), against
the real server. GPT-Live's session opening is faked and WebRTC stubbed (test_overnight_library_page.py)."""

import json
import socket
import threading
import time
from types import SimpleNamespace as NS

import pytest
from overnight_plan_fakes import offline  # noqa: F401
from test_overnight_library_page import STUB

import server
from overnight import live, planner, profiles, schedule, timeline
from stages import memory

sync_api = pytest.importorskip("playwright.sync_api")

LATEST = "2026-11-14-herons"
CARDS = [  # folder, title, topic, minutes (no audio but the latest)
    ("2026-11-03-same-heron", "Is It the Same Heron?", "Birds", 19),
    ("2026-11-04-heronries", "Why Herons Nest Together", "Birds", 21),
    ("2026-11-05-still-pond", "Why a Still Pond Feels Good", "Attention", 20),
    ("2026-11-11-beauty", "What Beauty Asks of Us", "Beauty", 38),
    ("2026-11-12-quiet", "A Chapter With Nothing Saved", "Beauty", 20),
]


@pytest.fixture
def site(cfg, tmp_path, monkeypatch, own_listeners):
    import ask_fixture
    import uvicorn
    cfg.pop("listener_dir", None)
    lis = profiles.create("ian")
    profiles.set_active("ian")
    (lis / "guide.md").write_text("# Your guide\n\n- Name: Wren.\n")
    profiles.append_jsonl(lis / "library.jsonl", {"id": "c-1", "kind": "conversation", "date": "2026-10-24",
                                                  "title": "the heron by the pond"})
    for folder, title, topic, minutes in CARDS:
        root = lis / "chapters" / folder
        profiles.write_json(root / "00_source" / "up_next.json", {"id": f"plan-{folder[:10]}", "title": title,
                            "summary": f"{title}: summary.", "topics": [topic], "minutes": minutes, "demo": True})
        profiles.write_json(root / "finished.json", {"t": f"{folder[:10]}T06:00:00", "demo": True})
    timeline.record_playback(lis / "chapters" / CARDS[0][0], 1140, 1140)
    timeline.record_playback(lis / "chapters" / CARDS[2][0], 731, 1200)
    timeline.add_moments(lis / "chapters" / CARDS[0][0], [  # four moments: From this chapter shows three
        {"kind": "question", "position_s": 100, "text": "How long do herons live?", "session": "a", "outcome": "answered",
         "reply": "Well over a decade."},
        {"kind": "question", "position_s": 300, "text": "Do they migrate?", "session": "a", "outcome": "answered"},
        {"kind": "mention", "position_s": 500, "text": "The Peregrine, J. A. Baker", "title": "The Peregrine",
         "creator": "J. A. Baker", "session": "a", "outcome": "in_library"},
        {"kind": "question", "position_s": 700, "text": "Why do they nest together?", "session": "b",
         "outcome": "became_chapter", "outcome_ref": "plan-2026-11-04"}])
    root = lis / "chapters" / LATEST
    ask_fixture.build(root, audio=True)
    dur = json.loads((root / "11_episode" / "timing.json").read_text())["duration"]
    profiles.write_json(root / "00_source" / "up_next.json", {"id": "plan-2026-11-14", "title": "Herons: Hunting",
                        "summary": "How a bird that barely moves hunts.", "topics": ["Birds"]})
    profiles.write_json(root / "11_episode" / "episode.json", {"title": "Herons", "duration": dur, "segments": [],
                                                               "references": []})
    profiles.write_json(root / "finished.json", {"t": "2026-11-14T06:00:00"})
    s = library_seed(lis, root)
    timeline.add_moments(root, [
        {"kind": "question", "position_s": round(dur * 0.25, 1), "text": "Why do they stand on one leg?",
         "session": "c", "outcome": "answered", "reply": "Most likely to keep warm."},
        {"kind": "question", "position_s": round(dur * 0.6, 1), "text": "Do gardens make us notice like this?",
         "session": "c", "outcome": "became_chapter", "outcome_ref": "plan-up", "seed": s},
        {"kind": "mention", "position_s": round(dur * 0.9, 1), "text": "Upstream, Mary Oliver", "title": "Upstream",
         "creator": "Mary Oliver", "session": "c", "outcome": "in_library"}])
    card = {"id": "plan-up", "for_morning": "2026-11-17", "title": "Borrowed Scenery: Why Japanese Gardens Hide the View",
            "overview": "Framed views.", "summary": "A summary.", "status": "planned", "chapter": None,
            "source": {"kind": "seed", "ref": s}}
    profiles.write_json(lis / "up_next.json", card)
    monkeypatch.setattr(live, "create_webrtc", lambda cfg, session, sdp, client=None: {"id": "live_1", "sdp": "answer"})
    monkeypatch.setattr(memory, "after_session", lambda *a, **k: False)
    monkeypatch.setattr(schedule, "can_change", lambda card, settings, now: (True, ""))
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(server.create_app(server.Pipeline(), cfg, remember_on_start=False),
                                        host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 10
    while not srv.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield NS(url=f"http://127.0.0.1:{port}", listener=lis, root=root, duration=dur, card=card, cfg=cfg)
    srv.should_exit = True
    t.join(5)


def library_seed(lis, root):
    from overnight import library
    return library.add_seeds(lis, [{"kind": "question", "text": "Do gardens make us notice like this?",
                                    "source": {"chapter": root.name, "session": "c"}}])[0]["id"]


@pytest.fixture
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        except Exception as e:
            pytest.skip(f"Chromium is not installed for Playwright: {e}")
        yield b
        b.close()


def open_page(site, browser, path="/library", width=390, height=844, touch=False):
    ctx = browser.new_context(viewport={"width": width, "height": height}, has_touch=touch, is_mobile=touch)
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.add_init_script(STUB)
    pg.goto(site.url + path)
    pg.wait_for_function("document.querySelectorAll('#strip .lt-ch').length > 0 && document.getElementById('library').dataset.card")
    pg.wait_for_timeout(150)
    return NS(p=pg, errors=errors)


def card(pg):
    return int(pg.evaluate("document.getElementById('library').dataset.card"))


def settled(pg, i, timeout=4000):
    """The strip has come to rest on card i, and the scrubber's handle on its tick."""
    pg.wait_for_function(f"""(() => {{ const s = document.getElementById('strip'), cards = s.querySelectorAll('.lt-card');
      return document.getElementById('library').dataset.card === '{i}'
        && Math.abs(s.scrollLeft - (cards[{i}].offsetLeft - cards[0].offsetLeft)) < 2; }})()""", timeout=timeout)


def handle_x(pg):
    return pg.evaluate("parseFloat(document.getElementById('scrub-handle').style.left)")


def tick_xs(pg, kind):
    return pg.eval_on_selector_all(f"#scrub-marks .lt-tick.{kind}", "els => els.map((e) => parseFloat(e.style.left))")


def test_opens_on_the_latest_and_up_next_is_the_rightmost_card(site, browser):  # check 2
    page = open_page(site, browser)
    pg = page.p
    n = pg.eval_on_selector_all("#strip .lt-card", "els => els.length")
    assert n == len(CARDS) + 2
    assert pg.evaluate("[...document.querySelectorAll('#strip .lt-card')].pop().id") == "up"
    settled(pg, n - 2)
    assert pg.inner_text("#latest-title") == "Herons: Hunting"
    # the next card (Up next) peeks at the right edge
    peek = pg.evaluate("(() => { const r = document.getElementById('up').getBoundingClientRect(); return [r.left, innerWidth]; })()")
    assert 0 < peek[1] - peek[0] < 120
    titles = pg.eval_on_selector_all("#strip .lt-ch a.lt-ctitle", "els => els.map((e) => e.textContent)")
    assert titles == [c[1] for c in CARDS] + ["Herons: Hunting"]  # oldest on the left
    st = pg.eval_on_selector_all("#strip .lt-ch .lt-cstatus", "els => els.map((e) => e.textContent)")
    assert st[0] == "Finished · 3 questions · 1 mention" and st[2] == "12 of 20 min" and st[1] == "Not started"
    colours = pg.eval_on_selector_all("#strip .lt-ch", "els => els.map((e) => ['teal', 'coral', 'amber'].find((c) => e.classList.contains(c)))")
    assert colours == ["teal", "teal", "coral", "amber", "amber", "teal"]
    assert page.errors == []


def test_arrow_keys_buttons_and_a_swipe_each_move_one_card(site, browser):  # check 3
    page = open_page(site, browser, width=1000, height=800)  # a laptop: the arrow buttons show
    pg = page.p
    last = len(CARDS)
    settled(pg, last)
    assert pg.is_visible("#strip-prev") and pg.is_visible("#strip-next")
    pg.focus("#latest-title")
    pg.keyboard.press("ArrowLeft")
    settled(pg, last - 1)
    assert pg.evaluate("document.activeElement.textContent") == CARDS[-1][1]  # the keyboard stays on the card
    xs = tick_xs(pg, "ch")
    assert abs(handle_x(pg) - xs[last - 1]) < 0.1  # the handle follows
    pg.keyboard.press("ArrowRight")
    settled(pg, last)
    pg.click("#strip-prev")
    settled(pg, last - 1)
    pg.click("#strip-next")
    pg.click("#strip-next")
    settled(pg, last + 1)  # Up next
    assert pg.is_disabled("#strip-next") and abs(handle_x(pg) - pg.evaluate(
        "parseFloat(document.querySelector('#scrub-marks .lt-upring').style.left)")) < 0.1
    assert pg.text_content("#from-kicker") == "Where it came from" and "Do gardens" in pg.inner_text("#from-list")
    assert page.errors == []


def test_a_touch_swipe_moves_one_card(site, browser):  # check 3, by touch
    page = open_page(site, browser, touch=True)
    pg = page.p
    last = len(CARDS)
    settled(pg, last)
    cdp = pg.context.new_cdp_session(pg)
    cdp.send("Input.synthesizeScrollGesture", {"x": 200, "y": 300, "xDistance": 180, "yDistance": 0,
                                               "gestureSourceType": "touch", "speed": 800})
    settled(pg, last - 1, timeout=6000)
    assert abs(handle_x(pg) - tick_xs(pg, "ch")[last - 1]) < 0.1 and page.errors == []


def test_dragging_the_handle_and_tapping_a_tick_move_the_strip(site, browser):  # check 4
    page = open_page(site, browser)
    pg = page.p
    settled(pg, len(CARDS))
    box = pg.locator("#scrubber").bounding_box()
    px = lambda pct: box["x"] + box["width"] * pct / 100  # noqa: E731
    xs = tick_xs(pg, "ch")
    y = box["y"] + box["height"] / 2
    pg.mouse.move(px(handle_x(pg)), y)
    pg.mouse.down()
    pg.mouse.move(px(xs[1]) + 20, y, steps=8)
    pg.mouse.move(px(xs[1]), y, steps=4)
    pg.mouse.up()
    settled(pg, 1)
    assert abs(handle_x(pg) - xs[1]) < 0.1
    mo = tick_xs(pg, "mo")  # the first chapter's moments are its first four short ticks
    pg.mouse.click(px(mo[3]), y)  # a moment's tick: its chapter
    settled(pg, 0)
    pg.mouse.click(px(xs[3]), y)  # a chapter's tick
    settled(pg, 3)
    pg.focus("#scrubber")
    pg.keyboard.press("End")
    settled(pg, len(CARDS) + 1)
    pg.keyboard.press("ArrowLeft")
    settled(pg, len(CARDS))
    assert pg.get_attribute("#scrubber", "aria-valuetext").startswith("Herons: Hunting") and page.errors == []


def test_a_gap_over_a_week_is_squeezed_with_a_break_mark(site, browser):  # check 5
    page = open_page(site, browser)
    pg = page.p
    conv = tick_xs(pg, "conv")[0]
    xs = tick_xs(pg, "ch")
    breaks = pg.eval_on_selector_all("#scrub-marks .lt-break", "els => els.map((e) => parseFloat(e.style.left))")
    assert len(breaks) == 1 and conv < breaks[0] < xs[0]  # Oct 24 to Nov 3: ten days, squeezed
    one_day = xs[1] - xs[0]  # Nov 3 to Nov 4
    assert xs[0] - conv <= 3.01 * one_day  # ten days drawn as three
    assert xs[3] - xs[2] == pytest.approx(6 * one_day, rel=0.01)  # Nov 5 to Nov 11: six days, true to the dates
    labels = pg.eval_on_selector_all("#scrub-labels span", "els => els.map((e) => e.textContent)")
    assert labels[-1] == "Tonight" and 1 <= len(labels) <= 5 and page.errors == []


def test_from_this_chapter_shows_up_to_three_and_the_empty_line(site, browser):  # check 6
    page = open_page(site, browser)
    pg = page.p
    pg.focus("#scrubber")
    pg.keyboard.press("Home")
    settled(pg, 0)
    rows = pg.eval_on_selector_all("#from-list .lt-row", "els => els.map((e) => e.innerText.replace(/\\s+/g, ' '))")
    assert len(rows) == 3  # four moments: the one that became a chapter is never left out
    assert rows[0].startswith("1:40 You asked") and rows[1].startswith("5:00 You asked")  # in time order
    assert rows[2] == "11:40 You asked: “Why do they nest together?” → Why Herons Nest Together"
    assert pg.get_attribute("#from-list a.lt-to", "href") == f"#/chapter/{CARDS[1][0]}"  # where it led, linked
    assert pg.get_attribute("#from-all", "href") == f"#/chapter/{CARDS[0][0]}"
    pg.keyboard.press("ArrowRight")
    settled(pg, 1)
    assert pg.is_visible("#from-empty") and pg.inner_text("#from-empty") == "Nothing saved from this one yet."
    pg.keyboard.press("End")
    pg.keyboard.press("ArrowLeft")
    settled(pg, len(CARDS))
    assert "→ Tonight" in pg.inner_text("#from-list") and page.errors == []


def test_the_chapter_page_markers_sit_where_the_moments_are(site, browser):  # check 7
    page = open_page(site, browser, path=f"/library#/chapter/{LATEST}")
    pg = page.p
    pg.wait_for_function("document.querySelectorAll('#pg-marks .lt-mk').length === 3")
    assert pg.inner_text("#pg-meta").endswith("· with Wren") and pg.inner_text("#pg-title") == "Herons: Hunting"
    bar = pg.locator("#pg-bar").bounding_box()
    centres = pg.eval_on_selector_all("#pg-marks .lt-mk", "els => els.map((e) => { const r = e.getBoundingClientRect(); return r.left + r.width / 2; })")
    for c, want in zip(centres, (0.25, 0.6, 0.9)):
        assert abs((c - bar["x"]) / bar["width"] - want) < 0.02
    kinds = pg.eval_on_selector_all("#pg-marks .lt-mk", "els => els.map((e) => e.className)")
    assert kinds == ["lt-mk question", "lt-mk became", "lt-mk mention"]
    rows = pg.eval_on_selector_all("#pg-list .lt-mrow", "els => els.map((e) => e.innerText.replace(/\\s+/g, ' '))")
    assert "Answered" in rows[0] and "Wren: Most likely to keep warm." in rows[0]
    assert "Became tonight's chapter" in rows[1] and "Up next: Borrowed Scenery." in rows[1]
    assert "In your library" in rows[2] and "Upstream, Mary Oliver" in rows[2]
    pg.evaluate("scrollTo(0, 0)")
    pg.click("#pg-marks .lt-mk.mention")
    pg.wait_for_function("document.querySelector('#pg-list .lt-mrow:nth-child(3)').classList.contains('flash')")
    offer = pg.locator("#pg-list .lt-mrow:nth-child(3) .lt-offer")
    assert offer.is_visible() and pg.evaluate("document.activeElement.classList.contains('lt-offer')")
    at = round(site.duration * 0.9, 1) - 10
    assert offer.inner_text() == f"Play from {int(at // 60)}:{int(at % 60):02d}"
    offer.click()  # the playable chapter: from ten seconds before the moment
    pg.wait_for_function(f"!document.getElementById('chapter-audio').paused && document.getElementById('chapter-audio').currentTime > {at - 1.5}")
    assert abs(pg.evaluate("document.getElementById('chapter-audio').currentTime") - at) < 1.5
    assert page.errors == []


def test_play_appears_only_on_the_latest_chapter(site, browser):  # check 8
    page = open_page(site, browser)
    pg = page.p
    plays = pg.eval_on_selector_all("#strip .lt-ch", "els => els.map((e) => !!e.querySelector('.lt-play'))")
    assert plays == [False] * len(CARDS) + [True]
    pg.evaluate(f"location.hash = '#/chapter/{CARDS[0][0]}'")
    pg.wait_for_function(f"document.getElementById('pg-title').textContent === '{CARDS[0][1]}'")
    assert not pg.is_visible("#pg-play") and pg.inner_text("#pg-heard") == "Finished"
    assert pg.is_visible("#pg-demo") and pg.inner_text("#pg-demo") == "Demo chapter" and not pg.is_visible("#pg-transcript")
    pg.click("#pg-marks .lt-mk.question")
    assert pg.eval_on_selector_all("#pg-list .lt-offer", "els => els.length") == 0  # nothing to play from
    pg.evaluate(f"location.hash = '#/chapter/{LATEST}'")
    pg.wait_for_function("document.getElementById('pg-title').textContent === 'Herons: Hunting'")
    assert pg.is_visible("#pg-play") and pg.is_visible("#pg-transcript")
    pg.evaluate(f"location.hash = '#/chapter/{CARDS[3][0]}'")
    pg.wait_for_function(f"document.getElementById('pg-title').textContent === '{CARDS[3][1]}'")
    assert not pg.is_visible("#pg-play") and pg.inner_text("#pg-empty") == "Ask anything while you listen and it’ll show up here."
    assert page.errors == []


def test_changing_up_next_away_puts_the_moment_back_to_saved(site, browser):  # check 10, the screens
    page = open_page(site, browser, path=f"/library#/chapter/{LATEST}")
    pg = page.p
    pg.wait_for_function("document.querySelectorAll('#pg-list .lt-mrow').length === 3")
    assert "Became tonight's chapter" in pg.inner_text("#pg-list .lt-mrow:nth-child(2)")
    new = {**site.card, "id": "plan-other", "title": "Octopus Eyes", "source": {"kind": "request", "ref": "x"}}
    profiles.write_json(site.listener / "up_next.json", new)
    planner.moments_follow(site.listener, site.card, new)
    pg.evaluate("window.Library.reload()")
    pg.wait_for_function("document.querySelector('#pg-list .lt-mrow:nth-child(2) .lt-mhead').innerText.includes('Saved for later')")
    assert "Became" not in pg.inner_text("#pg-list .lt-mrow:nth-child(2)")
    assert pg.get_attribute("#pg-marks .lt-mk:nth-child(2)", "class") == "lt-mk question"
    pg.evaluate("location.hash = '#/'")
    pg.wait_for_function("document.getElementById('up-title').textContent === 'Octopus Eyes'")
    assert "→ Tonight" not in pg.inner_text("#from-list") and page.errors == []


def test_the_player_writes_playback_and_the_card_follows_after_a_reload(site, browser):  # check 11
    page = open_page(site, browser)
    pg = page.p
    pg.click("#lib-play")  # no intro brief: Play plays the recording
    pg.wait_for_function("!document.getElementById('chapter-audio').paused && document.getElementById('chapter-audio').currentTime > 0")
    pb = site.root / "playback.json"
    deadline = time.monotonic() + 5
    while not pb.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert json.loads(pb.read_text())["plays"] == 1  # the start of a play is reported at once
    t = round(site.duration * 0.5)
    pg.evaluate(f"document.getElementById('chapter-audio').currentTime = {t}")
    pg.wait_for_timeout(300)
    pg.click("#player-toggle")  # pause: reported
    deadline = time.monotonic() + 5
    while json.loads(pb.read_text())["furthest_s"] < t - 1 and time.monotonic() < deadline:
        time.sleep(0.05)
    got = json.loads(pb.read_text())
    assert got["furthest_s"] >= t - 1 and not got["finished"] and got["last_played"]
    want = timeline.status_of(timeline.playback(site.root), site.duration)[1]
    pg.reload()
    pg.wait_for_function("document.getElementById('latest-title')")
    assert pg.inner_text(".lt-ch:last-of-type .lt-cstatus").startswith(want)
    pg.evaluate(f"location.hash = '#/chapter/{LATEST}'")
    pg.wait_for_function("document.getElementById('pg-heard').textContent.length > 0")
    fill = pg.evaluate("parseFloat(document.getElementById('pg-fill').style.width)")
    assert abs(fill - 100 * got["furthest_s"] / site.duration) < 2
    assert page.errors == []


def test_the_player_reports_every_ten_seconds_while_it_plays(site, browser, monkeypatch):
    page = open_page(site, browser)
    pg = page.p
    posts = []
    pg.on("request", lambda r: posts.append(time.monotonic()) if r.url.endswith("/playback") else None)
    pg.click("#lib-play")
    pg.wait_for_function("!document.getElementById('chapter-audio').paused")
    pg.wait_for_timeout(11500)
    assert len(posts) >= 2 and page.errors == []  # the start, then one ten seconds in
