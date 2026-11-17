"""Tester Demo, part D, the product page in headless Chromium: the handoff from the interviewer into the guide
(the handoff note, no greeting of the page's own, the reveal), no chapter card while the chapter is made (the
library shows it on the finished chapter), tester mode, the
handback to the interviewer (I'm done, and the chapter's end), and the library's debrief card.

The live page's own harness (tests/live/test_live_page.py) runs the real server over a scratch run with GPT-Live
stubbed; these tests reuse it. The interviewer (page/interview.js, part C) is a stub: window.Interview records
what it was asked to start. A tester's page is the live page with class "tester" on its body, as the server
serves it (part B); the tests add the class to the response the same way."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "live"))
from test_live_page import (RUN, STUB, Page, offered, read_through, say_yes, site, start_chapter,  # noqa: E402,F401
                            world)

from faststart import base  # noqa: E402

INTERVIEW = r"""
(() => {
  window.__interview = [];
  window.Interview = {
    start(mode, opts) {
      const s = opts && opts.stream;
      window.__interview.push({ mode, stream: !!s, live: !!(s && s.getTracks().every((t) => t.readyState === "live")) });
      window.__interviewDone = opts && opts.onDone;
      return Promise.resolve();
    },
    busy: () => false,
    end() {},
  };
})();
"""
CARD = {"title": "Why Roman harbors heal", "overview": "A concrete that got stronger in the sea.",
        "summary": "You asked why your slabs crack. This chapter asks why Rome's did not."}


class DemoPage(Page):
    """The live page, a tester's when `tester` (body class "tester", as the server serves it), with the
    interviewer stubbed."""

    def __init__(self, base_url, tester=True):
        from test_live_page import sync_api
        self.pw = sync_api.sync_playwright().start()
        self.browser = self.pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        self.page = self.browser.new_context().new_page()
        self.posts = []
        self.page.on("request", lambda r: r.method == "POST" and self.posts.append(
            (r.url.split("://", 1)[1].split("/", 1)[1], json.loads(r.post_data or "{}"))))
        self.page.add_init_script(STUB)
        self.page.add_init_script(INTERVIEW)
        if tester:
            def as_tester(route):
                res = route.fetch()
                route.fulfill(response=res, body=res.text().replace('<body class="idle">', '<body class="idle tester">'))
            self.page.route(base_url + "/", as_tester)
        self.page.goto(base_url + "/")
        self.page.wait_for_function("window.Live && window.Align")
        self.ms = 0

    def body(self):
        return self.js("document.body.className").split()

    def shown(self, sel):
        return self.js("(sel) => { const e = document.querySelector(sel); "
                       "return !!e && getComputedStyle(e).display !== 'none' && !e.hidden; }", sel)

    def instructions(self):
        return [e["content"] for e in self.sent("session.instructions.append")]


@pytest.fixture
def demo(world):
    pages = []

    def open_page(tester=True):
        p = DemoPage(world.base, tester)
        pages.append(p)
        return p
    world.open_demo = open_page
    yield world
    for p in pages:
        p.close()


def start_tester_reading(w, *lock):
    p = w.open_demo()
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    p.say("Hi there.")
    start_chapter(p)
    offered(p, w, *lock)
    return say_yes(p)


# --- 3. the handoff ----------------------------------------------------------------------------------

def handed_over(w, name="Purnima"):
    p = w.open_demo(tester=False)  # the gate injects the page; the body gets "tester" from the handoff
    p.js("""async (name) => {
      const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      window.__handStream = s;
      Live.handoff({ name, stream: s });
    }""", name)
    p.wait("L.started && L.phase === 'talk'")
    return p


def test_the_handoff_opens_with_the_handoff_note_and_no_greeting_of_the_pages_own(demo):
    w = demo
    p = handed_over(w)
    [start] = p.post_bodies("api/live/start")
    assert start["handoff"] == {"name": "Purnima"}
    note = w.notes["handoff"].replace("{name}", "Purnima")
    session = w.offers[-1][0]
    assert session["input"][-1]["content"][0]["text"] == note  # the session's first message
    assert p.js("window.__mics") == 1, "the gate's mic was asked for again"
    assert p.L("L.stream === window.__handStream")
    assert not [e for e in p.sent("session.commentary.append") if e["content"] == w.notes["opened"]]
    assert "tester" in p.body() and "handoff" in p.body()
    # before the guide's first word only the dot: the rest of the screen hidden
    assert p.js("getComputedStyle(document.querySelector('footer')).pointerEvents") == "none"
    assert p.js("getComputedStyle(document.querySelector('#line')).pointerEvents") == "none"
    p.wait("document.body.classList.contains('warm')")
    p.say("Hi Purnima, I'm Wren.")
    p.wait("document.body.classList.contains('revealed') || !document.body.classList.contains('handoff')")
    p.wait("!document.body.classList.contains('handoff')", timeout=5000)
    assert p.js("getComputedStyle(document.querySelector('footer')).pointerEvents") != "none"
    assert "tester" in p.body()
    p.page.wait_for_timeout(1700)  # past greet_fallback_ms: the guide spoke, so no second ask
    assert not [x for x in p.instructions() if "Purnima" in x]


def test_a_silent_guide_is_asked_once_more_with_the_handoff_note_never_the_greeting(demo):
    w = demo
    p = handed_over(w, "Sam")
    p.page.wait_for_timeout(2000)  # greet_fallback_ms is 1.5 s in the test config
    assert [x for x in p.instructions() if x == "[" + w.notes["handoff"].replace("{name}", "Sam") + "]"]
    assert w.notes["opened"] not in p.instructions()
    assert not [e for e in p.sent("session.commentary.append") if e["content"] == w.notes["opened"]]


# --- 2. the chapter card: never while the chapter is made; on the finished chapter in the library ----------
# (Ian, after his tester run, 2026-11-16: "I didn't like seeing so much info about what would be built. That
# card ... only is shown after the chapter is written.")

def test_no_card_shows_while_the_chapter_is_made_or_read_only_the_steps_and_the_parts(demo):
    w = demo
    base.write_json(w.root / "fast" / "card.json", {**CARD, "run": RUN})
    p = w.open_demo()
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    p.say("Hi there.")
    start_chapter(p)
    w.status(steps=[{"key": "scout", "label": "Looking into it", "state": "doing"},
                    {"key": "outline", "label": "Planning the chapter", "state": "todo"}])
    p.page.wait_for_selector("#steps:not([hidden])", timeout=5000)
    assert p.page.text_content("#steps li[data-state='doing']") == "Looking into it"
    p.page.wait_for_timeout(1000)
    shown = p.page.text_content("#screen")
    assert p.js("document.getElementById('card')") is None
    assert CARD["overview"] not in shown and CARD["summary"] not in shown and CARD["title"] not in shown
    offered(p, w, 1)
    say_yes(p)
    p.page.wait_for_selector("#sections button", timeout=5000)
    shown = p.page.text_content("#screen") + p.page.text_content("#player")
    assert CARD["overview"] not in shown and CARD["summary"] not in shown
    assert not [u for u in p.js("performance.getEntriesByType('resource').map((e) => e.name)") if "/api/live/card" in u]


def test_the_finished_chapter_in_the_library_carries_the_cards_overview_and_summary(demo):
    """A chapter made in conversation has no Up next card; its own card (faststart/card.py) is what the library
    shows for it once it is written, under the outline's title."""
    from overnight import routes as library_routes
    w = demo
    assert library_routes._chapter_info(w.root)["summary"] == ""  # no card: as before
    base.write_json(w.root / "fast" / "card.json", {**CARD, "run": RUN})
    info = library_routes._chapter_info(w.root)
    assert info["title"] == "The List"  # the outline's
    assert info["overview"] == CARD["overview"] and info["summary"] == CARD["summary"]
    def with_latest(route):  # the library's state with this chapter as its latest (it has no recording here)
        res = route.fetch()
        route.fulfill(response=res, body=json.dumps({**res.json(), "latest": info}))
    p = w.open_demo(tester=False)
    p.page.route("**/api/library", with_latest)
    p.js("() => window.Library.reload()")
    p.page.wait_for_selector("#latest-summary:not([hidden])", timeout=5000)
    assert p.page.text_content("#latest-summary") == CARD["summary"]
    assert p.page.text_content("#latest-overview") == CARD["overview"] and p.page.text_content("#latest-title") == "The List"


def test_im_done_stays_reachable_above_the_fixed_player_bar(demo):
    """The chapter's play, slider and time are a bar fixed to the bottom; I'm done is never under it."""
    p = start_tester_reading(demo)
    p.page.wait_for_selector("#player:not([hidden])")
    assert p.js("getComputedStyle(document.getElementById('player')).position") == "fixed"
    p.page.locator("#done").scroll_into_view_if_needed()
    assert p.js("""() => { const d = document.getElementById('done').getBoundingClientRect(), b = document.getElementById('player').getBoundingClientRect();
                          const hit = document.elementFromPoint(d.left + d.width / 2, d.top + d.height / 2);
                          return d.bottom <= b.top && hit === document.getElementById('done'); }""")
    p.page.click("#done")
    p.wait("L.handingBack === 'done'")


# --- 4. tester mode ----------------------------------------------------------------------------------

def test_tester_mode_hides_the_controls_and_shows_im_done_only_in_the_chapter(demo):
    w = demo
    p = w.open_demo()
    p.page.wait_for_selector("#library #up", state="attached")
    for sel in ("#reset", "#halt", "#type", "#form", "#done", "#library #up", "#settings-link", "#settings"):
        assert not p.shown(sel), sel
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    w.status()  # a chapter being made: #halt would show for anyone else
    p.page.wait_for_timeout(600)
    assert not p.shown("#halt") and not p.shown("#done")


def test_without_tester_mode_the_page_is_as_before(demo):
    p = demo.open_demo(tester=False)  # Ian's own page: everything there, no I'm done
    p.page.wait_for_selector("#settings-link", state="attached")
    assert p.shown("#reset") and p.shown("#type") and p.shown("#settings-link") and not p.shown("#done")
    assert "tester" not in p.body()


def test_im_done_shows_during_the_reading(demo):
    w = demo
    p = start_tester_reading(w)
    p.wait("document.body.classList.contains('reading')")
    assert p.shown("#done") and "tester" in p.body()


# --- 5. the handback ---------------------------------------------------------------------------------

def test_im_done_stops_the_reading_has_the_guide_hand_back_then_starts_the_debrief(demo):
    w = demo
    p = start_tester_reading(w)
    p.say(" ".join(w.paragraphs()["s1-p01"]["text"].split()[:4]))
    p.wait("L.feed.cur && L.feed.cur.matched >= 3")
    p.page.click("#done")
    p.wait("L.phase === 'handback'")
    assert p.instructions()[-1] == w.notes["handback_done"]
    w.reported(state="done")  # never offered again by itself (routes.continuing skips "done")
    reads = [b for b in p.post_bodies("api/live/read") if b["id"] == "s1-p01"]
    assert reads and reads[-1]["cut"] is True
    assert not p.js("window.__interview.length")
    p.page.wait_for_timeout(400)
    p.say("Let's leave it there for today. I'm handing you back to the person who welcomed you now.")
    p.page.wait_for_function("window.__interview.length === 1", timeout=8000)
    assert p.js("window.__interview[0]") == {"mode": "debrief", "stream": True, "live": True}  # the mic handed on, not stopped
    assert "debrief" in p.body() and p.L("L.debriefing")
    assert p.post_bodies("api/chat/end") and p.post_bodies("api/live/usage")
    assert not p.post_bodies("api/live/archive")  # not heard to the end
    p.page.click("#orb", force=True)
    p.page.wait_for_timeout(300)
    assert len(p.post_bodies("api/live/start")) == 1  # the orb is not the guide's during the debrief
    p.js("window.__interviewDone()")
    p.wait("!L.debriefing && !document.body.classList.contains('debrief')")


def test_im_done_while_paused_opens_a_session_to_say_it(demo):
    w = demo
    p = start_tester_reading(w)
    p.page.click("#play")  # pause: GPT-Live closes
    p.wait("L.phase === 'paused' && !L.pc")
    p.page.click("#done")
    p.wait("L.phase === 'handback' && L.pc && L.started", timeout=6000)
    starts = [b for b in p.posts if b[0] == "api/live/resume"]
    assert starts and starts[-1][1]["reason"] == "paused"
    assert p.instructions()[-1] == w.notes["handback_done"]
    p.say("Thanks for listening. I'll hand you back now.")
    p.page.wait_for_function("window.__interview.length === 1", timeout=8000)


def test_the_chapters_end_hands_back_instead_of_the_end_hold(demo):
    w = demo
    p = start_tester_reading(w, 1, 2)
    for pid in w.paragraphs():
        p.wait(f"L.feed.cur && L.feed.cur.id === '{pid}' && L.feed.cur.sentAt")
        read_through(p, w, pid)
    p.wait("L.phase === 'handback'", timeout=4000)
    assert p.instructions()[-1] == w.notes["handback_end"]
    p.page.wait_for_timeout(300)
    p.say("That's the story of the list. I'm passing you back to the person who welcomed you.")
    p.page.wait_for_function("window.__interview.length === 1", timeout=8000)
    assert p.js("window.__interview[0].mode") == "debrief"
    assert p.post_bodies("api/live/archive")  # heard to the end: archived as ever
    assert "debrief" in p.body()


def test_the_chapters_end_reports_the_whole_chapter_heard(demo):
    """Review: I'm done and the end both report state "done"; the end reports the whole chapter as chapter_t,
    I'm done where it stopped (demo/debrief.py reads chapter_t)."""
    w = demo
    p = start_tester_reading(w, 1, 2)
    for pid in w.paragraphs():
        p.wait(f"L.feed.cur && L.feed.cur.id === '{pid}' && L.feed.cur.sentAt")
        read_through(p, w, pid)
    words = sum(len(x["text"].split()) for x in w.paragraphs().values())
    got = w.reported(state="done")
    assert abs(got["chapter_t"] - words / 2.5) < 0.01


def test_im_done_part_way_reports_where_it_stopped(demo):
    w = demo
    p = start_tester_reading(w, 1, 2)
    p.say(" ".join(w.paragraphs()["s1-p01"]["text"].split()[:4]))
    p.wait("L.feed.cur && L.feed.cur.matched >= 3")
    p.page.click("#done")
    got = w.reported(state="done")
    assert got["chapter_t"] < 3  # four words heard, not the chapter


def test_a_done_debrief_is_never_handed_back_to_again(demo, cfg):
    """Review: a second chapter's end (or I'm done) started a second full debrief whose summary overwrote the
    first. Once study/debrief.json says done, the chapter just ends and I'm done isn't shown."""
    w = demo
    lis = Path(cfg["listener_dir"])
    (lis / "study").mkdir(parents=True, exist_ok=True)
    (lis / "study" / "debrief.json").write_text(json.dumps({"done": True, "session": "s1"}))
    p = start_tester_reading(w, 1, 2)
    assert p.L("L.debriefDone")
    p.wait("document.body.classList.contains('reading')")
    assert not p.shown("#done") and "in-chapter" not in p.body()
    for pid in w.paragraphs():
        p.wait(f"L.feed.cur && L.feed.cur.id === '{pid}' && L.feed.cur.sentAt")
        read_through(p, w, pid)
    p.wait("L.phase === 'ended'", timeout=4000)
    p.page.wait_for_timeout(500)
    assert not p.js("window.__interview.length") and not p.L("L.handingBack")
    assert w.notes["handback_end"] not in p.instructions()


def test_a_testers_refused_mic_asks_for_it_never_typing(demo):
    """Review: a tester's mic refused fell back to the typed chat, which testers can't use."""
    w = demo
    p = w.open_demo()
    p.js("() => { window.__denied = navigator.mediaDevices.getUserMedia; "
         "navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('denied', 'NotAllowedError'); }; }")
    p.page.click("#orb")
    p.wait("L.micRefused && L.phase === 'idle'")
    assert p.page.text_content("#line") == "I need your microphone to talk with you"
    assert p.shown("#mic-again") and not p.shown("#form") and not p.L("L.typing")
    assert not p.post_bodies("api/chat/start") and "No microphone" not in p.page.text_content("#transcript")
    p.js("() => { navigator.mediaDevices.getUserMedia = window.__denied; }")
    p.page.click("#mic-again")
    p.wait("L.started && L.phase === 'talk'")
    assert not p.L("L.micRefused") and not p.shown("#mic-again")


def test_without_tester_mode_a_refused_mic_still_types(demo):
    p = demo.open_demo(tester=False)
    p.js("() => { navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('denied', 'NotAllowedError'); }; }")
    p.page.click("#orb")
    p.wait("L.typing")
    assert not p.L("L.micRefused") and not p.shown("#mic-again")


def test_a_guide_that_stays_silent_still_hands_back(demo):
    w = demo
    p = start_tester_reading(w)
    p.js("() => { Live.S.lead_in_max_ms = 500; }")
    p.page.click("#done")
    p.page.wait_for_function("window.__interview.length === 1", timeout=12000)


# --- 6. the library's debrief card -------------------------------------------------------------------

def test_the_debrief_card_starts_the_debrief(demo, cfg):
    w = demo
    lis = Path(cfg["listener_dir"])
    lis.mkdir(parents=True, exist_ok=True)
    (lis / "tester.json").write_text(json.dumps({"label": "Pilot 1", "debrief_card": "on"}))
    p = w.open_demo()
    p.page.wait_for_selector("#lib-debrief:not([hidden])", timeout=5000)
    assert p.page.text_content("#debrief-line") == "Got five minutes to tell Ian how it went?"
    assert p.js("document.querySelector('#lib-home').firstElementChild.id") == "lib-debrief"
    p.page.click("#debrief-start")
    p.page.wait_for_function("window.__interview.length === 1", timeout=5000)
    assert p.js("window.__interview[0]") == {"mode": "debrief", "stream": True, "live": True}
    assert "debrief" in p.body()
    p.page.wait_for_timeout(1200)
    assert not p.shown("#lib-debrief")  # not while it runs
    (lis / "study").mkdir()
    (lis / "study" / "debrief.json").write_text(json.dumps({"done": True}))
    p.js("window.__interviewDone()")
    p.wait("!document.body.classList.contains('debrief')")
    p.page.wait_for_timeout(1500)
    assert not p.shown("#lib-debrief")  # done: gone


def test_the_debrief_card_waits_for_a_conversation_to_end(demo, cfg):
    w = demo
    lis = Path(cfg["listener_dir"])
    lis.mkdir(parents=True, exist_ok=True)
    (lis / "tester.json").write_text(json.dumps({"debrief_card": "on"}))
    p = w.open_demo()
    p.page.wait_for_selector("#lib-debrief:not([hidden])", timeout=5000)
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    p.page.wait_for_timeout(1200)
    assert not p.shown("#lib-debrief")
