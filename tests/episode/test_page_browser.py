"""The page in a real browser (Playwright, headless Chromium): the highlight moves during playback,
and a click on a word seeks the audio (acceptance check 7). Served by a plain static server, as
`python -m http.server` would, so it also proves the page needs nothing else."""

import functools
import json
import shutil
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from episode import bundle, references

PAGE = Path(__file__).resolve().parents[2] / "episode" / "page"
sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def served(replay_built, tmp_path):
    root = tmp_path / "site"
    shutil.copytree(PAGE, root / "page")
    shutil.copytree(replay_built, root / "run")

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(root)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/page/index.html?run=/run"
    httpd.shutdown()


@pytest.fixture
def replay_built(timed, cfg, monkeypatch):
    run, _ = timed
    monkeypatch.setattr(references, "ask", lambda r, c, seg, p: json.loads(
        (run / "references_recorded" / f"{seg.id}.json").read_text()))
    references.write(run, references.build(run, cfg)[0])
    bundle.write(run)
    return run


@pytest.fixture
def page(served):
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        except Exception as e:  # browsers not installed: python -m playwright install chromium
            pytest.skip(f"Chromium for Playwright is not available: {e}")
        pg = browser.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(served)
        pg.wait_for_function("document.getElementById('audio').readyState >= 1 && document.querySelectorAll('.w').length > 0")
        yield pg, errors
        browser.close()


def test_highlight_moves_and_a_click_seeks(page):  # check 7
    pg, errors = page
    pg.evaluate("document.getElementById('audio').muted = true")
    pg.keyboard.press("Space")  # the page's own play key
    pg.wait_for_function("!document.getElementById('audio').paused && document.getElementById('audio').currentTime > 0.5")
    first = pg.evaluate("document.body.dataset.word")
    pg.wait_for_function(f"document.body.dataset.word && document.body.dataset.word !== {json.dumps(first)}", timeout=8000)
    assert pg.locator(".w.current").count() == 1 and pg.locator(".s.current").count() == 1

    target = pg.locator("#seg02-s002 .w").nth(2)  # a word in the second segment
    target.click()
    t = pg.evaluate("document.getElementById('audio').currentTime")
    run_ep = pg.evaluate("fetch('/run/11_episode/episode.json').then(r => r.json())")
    word = run_ep["segments"][1]["sentences"][1]["words"][2]
    assert abs(t - word["start"]) < 0.35  # jumped there (and kept playing from it)
    pg.wait_for_function("document.body.dataset.word.startsWith('seg02-s002')", timeout=4000)
    assert pg.locator("#cardlist .card").count() >= 1  # cards spoken so far are listed
    assert errors == []


def test_keys_work_from_the_seek_bar_and_cards_track_seeks(page):
    pg, errors = page
    pg.click("#seek")  # the seek bar has focus now
    pg.evaluate("document.getElementById('audio').currentTime = 0")
    pg.keyboard.press("ArrowRight")
    starts = pg.evaluate("fetch('/run/11_episode/episode.json').then(r => r.json()).then(e => e.segments.flatMap(s => s.sentences.map(x => x.start)))")
    t = pg.evaluate("document.getElementById('audio').currentTime")
    assert abs(t - starts[1]) < 0.05  # the next sentence, not the slider's own step
    pg.evaluate(f"document.getElementById('audio').currentTime = {starts[-1] + 0.5}")
    pg.wait_for_timeout(200)
    n_end = pg.locator("#cardlist .card").count()
    pg.evaluate("document.getElementById('audio').currentTime = 0.1")
    pg.wait_for_timeout(200)
    assert pg.locator("#cardlist .card").count() < n_end  # seeking back removes cards not yet spoken
    assert errors == []
