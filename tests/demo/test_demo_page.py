"""Tester Demo, part D, the product page in headless Chromium: the handoff from the interviewer into the guide
(the handoff note, no greeting of the page's own, the reveal), no chapter card while the chapter is made (the
library shows it on the finished chapter), tester mode, the End demo button (upper right; it replaced I'm done
and the library's debrief card: Ian, 2026-11-18) in every phase, the handback to the interviewer (End demo, and
the chapter's end), and the button presses reported for the debrief (/api/demo/event).

The live page's own harness (tests/live/test_live_page.py) runs the real server over a scratch run with GPT-Live
stubbed; these tests reuse it. The interviewer (page/interview.js, part C) is a stub: window.Interview records
what it was asked to start. A tester's page is the live page with class "tester" on its body, as the server
serves it (part B); the tests add the class to the response the same way."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "live"))
from test_live_page import (AUDIO_SESSION, RUN, STUB, Page, offered, read_through, say_yes, site,  # noqa: E402,F401
                            start_chapter, world)

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

    def __init__(self, base_url, tester=True, before=None, touch=False):
        from test_live_page import sync_api
        self.pw = sync_api.sync_playwright().start()
        self.browser = self.pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        self.page = self.browser.new_context(has_touch=touch).new_page()
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
        if before:
            before(self.page)  # routes that must be there for the page's first requests
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


def as_tester(cfg, **fields):
    """The tester's record in their folder, as the server keeps one for every tester (Ian's End demo setting
    for them is in it: debrief_card auto | on | off)."""
    lis = Path(cfg["listener_dir"])
    lis.mkdir(parents=True, exist_ok=True)
    (lis / "tester.json").write_text(json.dumps({"label": "Pilot 1", "lid": "t-pilot-1", "debrief_card": "auto", **fields}))
    return lis


def events(p, name=None):
    """The button presses the page reported (POST /api/demo/event)."""
    return [b for b in p.post_bodies("api/demo/event") if name is None or b["event"] == name]


def end_demo(p):
    """Where the End demo button is and whether a tap would reach it."""
    return p.js("""() => { const b = document.getElementById('end-demo'), r = b.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return { shown: getComputedStyle(b).display !== 'none', text: b.textContent.trim(),
               fixed: getComputedStyle(b).position === 'fixed', top: r.top, from_right: innerWidth - r.right, hit: hit === b }; }""")


def debriefs(p):
    return p.js("window.__interview.length")


def patient(p):
    """The page waits for the scripted goodbye however slow the driver is: the fixture's lead_in_max_ms of 1500
    gives it 3 s from the tap, which a loaded machine spends on round trips (a full run failed on it)."""
    p.js("() => { Live.S.lead_in_max_ms = 10000; Live.S.handback_again_ms = 20000; Live.S.handback_silent_ms = 20000; }")


@pytest.fixture
def demo(world, cfg):
    as_tester(cfg)
    world.cfg = cfg
    pages = []

    def open_page(tester=True, before=None, touch=False):
        p = DemoPage(world.base, tester, before, touch)
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
    # Said by the page as speakable context the moment the session starts (acted on at once); as the session's
    # startup message alone the guide waited out greet_fallback_ms in silence (Ian's run, 2026-11-16).
    assert session["input"] == []
    assert [e["content"] for e in p.sent("session.commentary.append")] == [note]
    p.page.wait_for_timeout(200)
    assert note in [x["text"] for x in p.post_bodies("api/live/line") if x["who"] == "page"]
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


def held(w):
    """The handover is near: the gate calls Live.handoff with hold (the interviewer is saying its last words)."""
    p = w.open_demo(tester=False)
    p.js("""async () => {
      const add = RTCPeerConnection.prototype.addTrack;
      window.__replaced = [];
      RTCPeerConnection.prototype.addTrack = function (t, s) {
        add.call(this, t, s);
        return { replaceTrack: async (x) => { window.__replaced.push(x ? "mic" : null); } };
      };
      const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      window.__handStream = s;
      Live.handoff({ stream: s, hold: true });
    }""")
    p.wait("L.started && L.log.some((x) => x[1] === 'held')")
    return p


def test_a_session_opened_ahead_of_the_handover_is_silent_and_deaf_until_it_is_let_go(demo):
    """Ian's notes (2026-11-16): about 20 s passed between the interviewer's last words and the guide's first.
    The guide's session now opens while the interviewer says its last words; at the handover only the note goes."""
    w = demo
    p = held(w)
    [start] = p.post_bodies("api/live/start")
    assert start["handoff"] == {"name": ""}
    assert p.js("window.__replaced") == [None]  # the mic is still the interviewer's: nothing of it is sent here
    assert p.sent("session.input_audio.mute") and not p.sent("session.input_audio.unmute")
    assert not p.sent("session.commentary.append") and not p.sent("session.instructions.append")
    assert p.L("L.phase") == "connecting" and "handoff" in p.body()
    p.page.wait_for_timeout(600)
    assert "revealed" not in p.body() and not p.sent("session.commentary.append")
    assert p.js("(() => { const s = Live.spot(); return s.w > 100 && Math.abs(s.w - s.h) < 1 && !!s.color; })()")
    # the handover: the name as known now (it may have been corrected during the intro), sent at once
    p.js("() => { window.__t0 = Date.now(); Live.handoff({ name: ' Sam [x] ', stream: window.__handStream }); }")
    p.wait("L.phase === 'talk'")
    note = w.notes["handoff"].replace("{name}", "Sam x")
    assert [e["content"] for e in p.sent("session.commentary.append")] == [note]
    assert p.js("window.__replaced") == [None, "mic"] and p.sent("session.input_audio.unmute")
    assert len(p.post_bodies("api/live/start")) == 1 and p.js("window.__mics") == 1
    sent_at = next(x[0] for x in p.L("L.log") if x[1] == "handoff-note")
    assert sent_at - p.js("window.__t0") < 300, "the note waited on something at the handover"
    p.say("Hi Sam, I'm Wren.")
    p.wait("!document.body.classList.contains('handoff')", timeout=5000)


def test_a_held_session_never_let_go_is_closed_and_its_conversation_ended(demo):
    w = demo
    p = w.open_demo(tester=False)
    p.js("() => { Live.S.hold_max_ms = 400; }")
    p.js("""async () => { const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      window.__handStream = s; Live.handoff({ stream: s, hold: true }); }""")
    p.wait("L.started")
    p.wait("L.phase === 'idle' && !L.pc && !L.hold")
    assert p.sent("session.close") and not p.sent("session.commentary.append")
    p.page.wait_for_timeout(200)
    assert p.post_bodies("api/chat/end")
    assert p.js("window.__handStream.getAudioTracks()[0].readyState") == "live"  # the mic is not this page's to stop
    # a handover after all starts again, from the start
    p.js("() => { Live.handoff({ name: 'Sam', stream: window.__handStream }); }")
    p.wait("L.started && L.phase === 'talk'")
    assert len(p.post_bodies("api/live/start")) == 2
    assert [e["content"] for e in p.sent("session.commentary.append")] == [w.notes["handoff"].replace("{name}", "Sam")]


def failing_start(p):
    """/api/live/start hangs until window.__fail() answers it with a 500."""
    p.js("""() => {
      const f = window.fetch;
      window.fetch = (u, o) => !String(u).includes("/api/live/start") ? f(u, o) : new Promise((done) => {
        window.__fail = () => done(new Response(JSON.stringify({ detail: "Voice couldn't start." }),
                                                { status: 500, headers: { "Content-Type": "application/json" } }));
      });
    }""")


def test_a_session_opened_ahead_that_fails_after_the_handover_shows_the_screen_and_why(demo):
    """Review of the handover fix: opened ahead (hold), let go of at the handover, and then the connection
    failed: nothing revealed the screen (a held connect never did), the gate faded regardless, and the tester
    was left with a bare circle. Now the screen comes up, with the page's line saying why."""
    w = demo
    p = w.open_demo(tester=False)
    failing_start(p)
    p.js("""async () => { const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      window.__handStream = s; Live.handoff({ stream: s, hold: true }); }""")
    p.page.wait_for_function("!!window.__fail")
    assert "handoff" in p.body() and "revealed" not in p.body()
    p.js("() => { Live.handoff({ name: 'Sam', stream: window.__handStream }); }")  # the handover: let go of
    p.page.wait_for_timeout(300)
    assert "revealed" not in p.body() and not p.marks("revealed")
    p.js("() => window.__fail()")
    p.wait("L.log.some((x) => x[1] === 'revealed')", timeout=4000)
    p.wait("L.phase === 'idle' && !L.pc")
    assert p.page.is_visible("#transcript") and "Voice couldn't start." in p.page.inner_text("#transcript")
    p.wait("!document.body.classList.contains('handoff')", timeout=5000)  # the screen is the page's again
    assert p.page.is_visible("#line") and p.js("getComputedStyle(document.querySelector('#line')).pointerEvents") != "none"


def test_a_session_opened_ahead_that_fails_before_the_handover_waits_for_it(demo):
    """Only opened ahead, and it failed: the gate still covers the screen, so nothing is revealed yet; the
    handover's own call starts again, and if that fails too the screen comes up."""
    w = demo
    p = w.open_demo(tester=False)
    failing_start(p)
    p.js("""async () => { const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      window.__handStream = s; Live.handoff({ stream: s, hold: true }); }""")
    p.page.wait_for_function("!!window.__fail")
    p.js("() => { window.__fail(); window.__fail = null; }")
    p.wait("L.phase === 'idle' && !L.hold")
    p.page.wait_for_timeout(300)
    assert not p.marks("revealed") and "handoff" in p.body()
    p.js("""async () => { const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      Live.handoff({ name: 'Sam', stream: s }); }""")
    p.page.wait_for_function("!!window.__fail")
    p.js("() => window.__fail()")
    p.wait("L.log.some((x) => x[1] === 'revealed')", timeout=4000)


def test_the_mic_is_on_before_the_debrief_starts(demo):
    """The guide's page may have the mic disabled (while it reads); the debrief now starts before the guide's
    session has finished closing, so the mic is enabled first: the interviewer hears them from its first word."""
    w = demo
    p = start_tester_reading(w)
    p.js("""() => { const start = window.Interview.start;
      window.Interview.start = (mode, opts) => { window.__micAtDebrief = opts.stream.getAudioTracks()[0].enabled; return start(mode, opts); }; }""")
    p.page.click("#end-demo")
    p.wait("L.phase === 'handback'")
    p.page.wait_for_timeout(400)
    p.js("() => { window.Live.L.mic.enabled = false; }")
    p.say("Let's leave it there. I'm handing you back to the person who welcomed you now.")
    p.page.wait_for_function("window.__interview.length === 1", timeout=8000)
    assert p.js("window.__micAtDebrief") is True


def test_the_debrief_starts_while_the_guides_session_closes(demo):
    """Ian's notes: the handback waited for the guide's session to close (up to 3 s), its cost and its end
    before the interviewer's session even began to connect."""
    w = demo
    p = start_tester_reading(w)
    p.say(" ".join(w.paragraphs()["s1-p01"]["text"].split()[:4]))
    p.wait("L.feed.cur && L.feed.cur.matched >= 3")
    p.page.click("#end-demo")
    p.wait("L.phase === 'handback'")
    p.page.wait_for_timeout(400)
    p.say("Let's leave it there. I'm handing you back to the person who welcomed you now.")
    p.page.wait_for_function("window.__interview.length === 1", timeout=8000)
    assert not p.post_bodies("api/chat/end"), "the debrief waited for the guide's conversation to end"
    p.wait("!L.handingBack && L.phase === 'idle'", timeout=6000)
    assert p.post_bodies("api/chat/end") and p.L("L.debriefing")


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


def test_the_finished_chapter_in_the_library_carries_the_cards_overview_only(demo):
    """A chapter made in conversation has no Up next card; its own card's overview (faststart/card.py) is what
    the library shows for it once it is written, under the outline's title. Not the card's longer summary: Ian
    "didn't like seeing so much info" (the first fix had added that paragraph to the Latest card)."""
    from overnight import routes as library_routes
    w = demo
    assert "summary" not in library_routes._chapter_info(w.root)
    base.write_json(w.root / "fast" / "card.json", {**CARD, "run": RUN})
    info = library_routes._chapter_info(w.root)
    assert info["title"] == "The List"  # the outline's
    assert info["overview"] == CARD["overview"] and "summary" not in info
    def with_latest(route):  # the library's state with this chapter as its latest (it has no recording here)
        res = route.fetch()
        route.fulfill(response=res, body=json.dumps({**res.json(), "latest": info}))
    p = w.open_demo(tester=False)
    p.page.route("**/api/library", with_latest)
    p.js("() => window.Library.reload()")
    p.page.wait_for_function("document.getElementById('latest-title').textContent === 'The List'", timeout=5000)
    assert p.page.text_content("#latest-overview") == CARD["overview"]
    assert p.js("document.getElementById('latest-summary')") is None and CARD["summary"] not in p.page.text_content("#library")


def test_end_demo_is_in_the_upper_right_clear_of_the_player_bar_and_over_the_library(demo):
    """Ian (2026-11-18): "a more prominent button, maybe in the upper right that says end demo". Fixed to the
    screen's corner, so it is there over the chapter's player bar and when they scroll down to the library."""
    p = start_tester_reading(demo)
    p.page.wait_for_selector("#player:not([hidden])")
    at = end_demo(p)
    assert at["shown"] and at["text"] == "End demo" and at["fixed"] and at["hit"]
    assert at["top"] < 40 and at["from_right"] < 40
    assert p.js("""() => { const d = document.getElementById('end-demo').getBoundingClientRect(), b = document.getElementById('player').getBoundingClientRect();
                          return d.bottom <= b.top; }""")
    p.js("() => document.getElementById('library').scrollIntoView()")
    p.page.wait_for_timeout(200)
    at = end_demo(p)
    assert at["shown"] and at["hit"] and at["top"] < 40 and at["from_right"] < 40
    p.page.click("#end-demo")
    p.wait("L.handingBack === 'done'")


# --- 4. tester mode ----------------------------------------------------------------------------------

def test_tester_mode_hides_the_controls(demo):
    w = demo
    p = w.open_demo()
    p.page.wait_for_selector("#library #up", state="attached")
    for sel in ("#reset", "#halt", "#type", "#form", "#library #up", "#settings-link", "#settings"):
        assert not p.shown(sel), sel
    assert p.js("document.getElementById('done')") is None  # I'm done is gone: End demo replaced it
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    w.status()  # a chapter being made: #halt would show for anyone else
    p.page.wait_for_timeout(600)
    assert not p.shown("#halt")


def test_without_tester_mode_the_page_is_as_before_with_no_end_demo_and_nothing_reported(demo):
    w = demo
    p = w.open_demo(tester=False)  # Ian's own page: everything there, no End demo, no presses reported
    p.page.wait_for_selector("#settings-link", state="attached")
    assert p.shown("#reset") and p.shown("#type") and p.shown("#settings-link") and not p.shown("#end-demo")
    assert "tester" not in p.body()
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    p.say("Hi there.")
    start_chapter(p)
    offered(p, w)
    say_yes(p)
    assert not p.shown("#end-demo") and "end-demo" not in p.body()
    p.page.click("#play")  # pause
    p.wait("L.phase === 'paused'")
    p.page.click("#play")
    p.wait("L.phase === 'reading'", timeout=6000)
    p.page.click("#orb")  # a question
    p.wait("L.phase === 'aside'")
    assert not p.shown("#end-demo") and not events(p)
    p.js("() => Live.endDemo()")  # nothing to end
    p.page.wait_for_timeout(300)
    assert p.L("L.phase") == "aside" and not debriefs(p) and not events(p)
    assert not [u for u in p.js("performance.getEntriesByType('resource').map((e) => e.name)") if "/api/live/demo" in u]


# --- 4b. End demo: where it shows ---------------------------------------------------------------------
# Ian (2026-11-18): "there should be a more prominent button, maybe in the upper right that says end demo and
# visible while the onboarding and chapter is being read, not only after."

def test_end_demo_shows_through_the_conversation_the_making_a_rest_the_reading_a_pause_and_a_question(demo):
    w = demo
    p = w.open_demo()
    p.wait("L.demo")
    assert not p.shown("#end-demo")  # back on their page with nothing begun (Ian's setting: auto)
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    p.wait("document.body.classList.contains('end-demo')")  # the conversation
    at = end_demo(p)
    assert at["shown"] and at["text"] == "End demo" and at["hit"] and at["top"] < 40 and at["from_right"] < 40
    p.say("Hi there.")
    start_chapter(p)
    w.status()  # the chapter being made, talking
    p.wait("L.status.run === 'live-run'")
    assert end_demo(p)["shown"]
    p.page.click("#orb")  # the mic off: resting while it is made
    p.wait("L.phase === 'resting' && !L.pc && !L.closing")
    p.page.wait_for_timeout(600)
    assert end_demo(p)["shown"] and end_demo(p)["hit"]
    p.page.click("#orb")  # back to talk
    p.wait("L.phase === 'talk' && L.liveId === 'live_2'", timeout=6000)
    p.say("I'm here.")
    offered(p, w)
    assert end_demo(p)["shown"]
    say_yes(p)
    p.wait("document.body.classList.contains('reading')")
    assert end_demo(p)["shown"] and end_demo(p)["hit"]  # the reading
    p.page.click("#play")
    p.wait("L.phase === 'paused' && !L.pc")
    p.page.wait_for_timeout(600)
    assert end_demo(p)["shown"]  # paused
    p.page.click("#orb")  # a question from the pause
    p.wait("L.phase === 'aside'", timeout=6000)
    p.page.wait_for_timeout(600)
    assert end_demo(p)["shown"] and end_demo(p)["hit"]  # a question


@pytest.mark.parametrize("setting, chapter, shown", [
    ("auto", False, False),   # nothing begun: nothing to debrief yet
    ("auto", True, True),     # they have a chapter
    ("on", False, True),      # Ian turned it on
    ("off", True, False),     # Ian turned it off
])
def test_a_tester_back_on_their_page_sees_end_demo_as_ians_setting_says(demo, setting, chapter, shown):
    """A tester who left and comes back, the debrief not done, sees the same button where the library's card
    was; the admin page's control (the stored field is still debrief_card) governs it."""
    w = demo
    lis = as_tester(w.cfg, debrief_card=setting)
    if chapter:
        (lis / "chapters" / "roman-concrete-20261116").mkdir(parents=True)
    p = w.open_demo()
    p.wait("L.demo")
    p.page.wait_for_timeout(600)
    assert end_demo(p)["shown"] is shown
    assert p.js("document.getElementById('lib-debrief')") is None  # no card in the library
    if setting == "off":  # not even in a conversation
        p.page.click("#orb")
        p.wait("L.started && L.phase === 'talk'")
        p.page.wait_for_timeout(600)
        assert not end_demo(p)["shown"]


@pytest.mark.parametrize("tester, debrief, chapter, want", [
    (None, None, True, {"tester": False, "show": False, "done": False, "setting": "off"}),          # not a tester: never
    ({"debrief_card": "auto"}, None, False, {"tester": True, "show": False, "done": False, "setting": "auto"}),
    ({"debrief_card": "auto"}, None, True, {"tester": True, "show": True, "done": False, "setting": "auto"}),
    ({}, None, True, {"tester": True, "show": True, "done": False, "setting": "auto"}),             # auto is the default
    ({"debrief_card": "auto"}, {"done": True}, True, {"tester": True, "show": False, "done": True, "setting": "auto"}),
    ({"debrief_card": "off"}, None, True, {"tester": True, "show": False, "done": False, "setting": "off"}),
    ({"debrief_card": "on"}, None, False, {"tester": True, "show": True, "done": False, "setting": "on"}),
    ({"debrief_card": "on"}, {"done": True}, False, {"tester": True, "show": False, "done": True, "setting": "on"}),
])
def test_the_end_demo_state_the_server_gives(cfg, tmp_path, tester, debrief, chapter, want):
    """voice/gpt_live/routes.end_demo (GET /api/live/demo): what the library's debrief card was decided by."""
    from overnight import routes as library_routes
    from voice.gpt_live import routes as live_routes
    lis = tmp_path / "listeners" / "t-pilot-1"
    lis.mkdir(parents=True)
    cfg["listener_dir"] = str(lis)
    if tester is not None:
        (lis / "tester.json").write_text(json.dumps({"label": "Pilot 1", "lid": "t-pilot-1", **tester}))
    if debrief is not None:
        (lis / "study").mkdir()
        (lis / "study" / "debrief.json").write_text(json.dumps(debrief))
    if chapter:
        (lis / "chapters" / "roman-concrete-20261116").mkdir(parents=True)
    assert live_routes.end_demo(cfg) == want
    assert "debrief" not in library_routes.library_state(cfg)  # the library carries no card
    assert not hasattr(library_routes, "debrief_card") and not hasattr(library_routes, "DEBRIEF_LINE")


def test_end_demo_shows_once_the_handed_over_screen_is_revealed_never_before(demo):
    """Hidden during the interviewer's intro (the gate has nothing but the dot) and while the guide's session is
    held; there from the moment the guide's screen is revealed, whatever Ian's setting for returners."""
    w = demo
    as_tester(w.cfg, debrief_card="off")
    p = held(w)
    p.page.wait_for_timeout(600)
    assert not end_demo(p)["shown"]
    p.js("() => { Live.handoff({ name: 'Sam', stream: window.__handStream }); }")
    p.wait("L.phase === 'talk'")
    p.page.wait_for_timeout(400)
    assert not end_demo(p)["shown"] and "revealed" not in p.body()  # the guide hasn't spoken: still only the circle
    p.say("Hi Sam, I'm Wren.")
    p.wait("document.body.classList.contains('revealed') || !document.body.classList.contains('handoff')")
    p.wait("document.body.classList.contains('end-demo')")
    p.wait("!document.body.classList.contains('handoff')", timeout=5000)
    at = end_demo(p)
    assert at["shown"] and at["hit"] and at["top"] < 40 and at["from_right"] < 40


# --- 5. the handback ---------------------------------------------------------------------------------

def test_end_demo_stops_the_reading_has_the_guide_hand_back_then_starts_the_debrief(demo):
    w = demo
    p = start_tester_reading(w)
    p.say(" ".join(w.paragraphs()["s1-p01"]["text"].split()[:4]))
    p.wait("L.feed.cur && L.feed.cur.matched >= 3")
    patient(p)
    p.js("() => { const b = document.getElementById('end-demo'); b.click(); b.click(); }")  # a double tap is one
    p.wait("L.phase === 'handback'")
    assert p.instructions()[-1] == w.notes["handback_done"] and "End demo" in w.notes["handback_done"]
    assert [x for x in p.instructions() if x.startswith("[The listener tapped End demo")] == [w.notes["handback_done"]]
    assert not end_demo(p)["shown"]  # tapped: gone while the guide says goodbye
    w.reported(state="done")  # never offered again by itself (routes.continuing skips "done")
    reads = [b for b in p.post_bodies("api/live/read") if b["id"] == "s1-p01"]
    assert reads and reads[-1]["cut"] is True
    assert not debriefs(p)
    p.page.wait_for_timeout(400)
    p.js("() => Live.endDemo()")  # a tap during the handback does nothing
    assert not debriefs(p)
    p.say("Let's leave it there for today. I'm handing you back to the person who welcomed you now.")
    p.page.wait_for_function("window.__interview.length === 1", timeout=8000)
    assert p.js("window.__interview[0]") == {"mode": "debrief", "stream": True, "live": True}  # the mic handed on, not stopped
    assert "debrief" in p.body() and p.L("L.debriefing")
    assert not end_demo(p)["shown"]  # nor during the debrief
    p.wait("!L.handingBack", timeout=6000)  # the guide's session closes on the side (the debrief doesn't wait for it)
    assert p.post_bodies("api/chat/end") and p.post_bodies("api/live/usage")
    assert not p.post_bodies("api/live/archive")  # not heard to the end
    p.page.click("#orb", force=True)
    p.js("() => Live.endDemo()")
    p.page.wait_for_timeout(300)
    assert len(p.post_bodies("api/live/start")) == 1  # the orb is not the guide's during the debrief
    [ev] = events(p, "end_demo")
    assert ev["phase"] == "reading" and ev["chapter_t"] is not None and ev["chapter_t"] < 3
    lis = Path(w.cfg["listener_dir"])
    (lis / "study").mkdir(exist_ok=True)
    (lis / "study" / "debrief.json").write_text(json.dumps({"done": True}))
    p.js("window.__interviewDone()")
    p.wait("!L.debriefing && !document.body.classList.contains('debrief')")
    p.wait("L.debriefDone")
    p.page.wait_for_timeout(600)
    assert debriefs(p) == 1 and not end_demo(p)["shown"]  # the debrief done: gone for good


def test_end_demo_in_conversation_has_the_guide_close_and_hand_back(demo):
    """The button is there through the onboarding conversation too: the guide closes in a sentence (its own
    note: handback_done speaks of the chapter) and hands back, then the debrief, with nothing to confirm."""
    w = demo
    p = w.open_demo()
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    p.say("Hi there. What have you been up to?")
    p.hear("I've been building cabinets all week.")
    p.page.wait_for_timeout(1000)
    said = len(p.instructions())
    patient(p)
    p.js("() => { const b = document.getElementById('end-demo'); b.click(); b.click(); }")
    p.wait("L.phase === 'handback'")
    assert p.L("L.handingBack") == "talk"
    # As speakable context, which GPT-Live acts on at once. As an instruction it was let pass (live, 2026-11-18:
    # the guide had just asked a question and went on waiting for the answer; 12 s of silence, then the debrief).
    note = w.notes["handback_talk"]
    assert [e["content"] for e in p.sent("session.commentary.append")][-1] == note and not note.startswith("[")
    assert len(p.instructions()) == said and p.js("document.getElementById('remote').volume") == 1
    assert not end_demo(p)["shown"]
    p.page.wait_for_timeout(400)
    assert not debriefs(p)
    p.say("Lovely talking with you. I'm handing you back to the person who welcomed you.")
    p.page.wait_for_function("window.__interview.length === 1", timeout=8000)
    assert p.js("window.__interview[0]") == {"mode": "debrief", "stream": True, "live": True}
    assert len(p.instructions()) == said  # it spoke: not asked a second time
    p.wait("!L.handingBack && L.phase === 'idle'", timeout=6000)
    assert p.post_bodies("api/chat/end")
    assert note in [x["text"] for x in p.post_bodies("api/live/line") if x["who"] == "page"]  # in the transcript
    assert events(p) == [{"event": "end_demo", "phase": "talk", "chapter_t": None, "detail": None}]
    assert debriefs(p) == 1


def test_end_demo_in_conversation_with_a_silent_guide_asks_once_more_then_goes_on(demo):
    w = demo
    p = w.open_demo()
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    p.say("Hi there.")
    p.page.wait_for_timeout(1000)
    p.js("() => { Live.S.handback_again_ms = 500; Live.S.handback_silent_ms = 1500; }")
    said = len(p.instructions())
    p.page.click("#end-demo")
    p.wait(f"window.__sent.filter((e) => e.type === 'session.instructions.append').length === {said + 1}", timeout=3000)
    assert p.instructions()[-1] == "[" + w.notes["handback_talk"] + "]"
    assert not debriefs(p)
    p.page.wait_for_function("window.__interview.length === 1", timeout=4000)  # no second wait in silence
    assert len(p.instructions()) == said + 1 and p.marks("handed-back")


def test_end_demo_while_the_guide_is_mid_reply_is_silent_at_once_and_does_not_wait_the_reply_out(demo):
    """Live (2026-11-18): tapped as the guide began a reply, GPT-Live (which can't be cut off) finished the reply
    first; its goodbye began 10 s after the tap. The reply is silenced at the tap; if it stops within a moment
    the goodbye is heard, else the debrief starts without it."""
    w = demo
    p = w.open_demo()
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    p.say("Hi there.")
    p.hear("Just back from a walk.")
    p.page.wait_for_timeout(600)
    p.js("""() => { let k = 0; window.__talking = setInterval(() => window.__emit(
      { type: "session.output_transcript.delta", delta: " word" + (k++) }), 100); }""")  # a long reply, still going
    p.page.wait_for_timeout(300)
    p.page.click("#end-demo")
    p.wait("L.phase === 'handback'")
    assert p.js("document.getElementById('remote').volume") == 0
    assert [e["content"] for e in p.sent("session.commentary.append")][-1] == w.notes["handback_talk"]
    p.page.wait_for_timeout(1500)
    assert not debriefs(p)  # a moment for the reply to stop
    p.page.wait_for_function("window.__interview.length === 1", timeout=3000)  # ... then on, at about 2.5 s
    took = p.marks("handed-back")[-1][0] - p.marks("handback")[-1][0]
    assert 2300 < took < 3600, took
    p.js("() => clearInterval(window.__talking)")
    p.wait("!L.handingBack && L.phase === 'idle'", timeout=6000)
    assert p.js("document.getElementById('remote').volume") == 1  # the next voice here is heard


def test_end_demo_as_a_reply_ends_lets_the_goodbye_be_heard(demo):
    w = demo
    p = w.open_demo()
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    patient(p)
    p.say("Hi there, good to see you.")  # its last words just said
    p.page.click("#end-demo")
    p.wait("L.phase === 'handback'")
    assert p.js("document.getElementById('remote').volume") == 0
    p.page.wait_for_function("document.getElementById('remote').volume === 1", timeout=2000)  # it went quiet: heard again
    assert not debriefs(p)
    p.say("Lovely to meet you. I'll hand you back to the person who welcomed you.")
    p.page.wait_for_function("window.__interview.length === 1", timeout=8000)


def test_end_demo_while_the_chapter_is_made_closes_without_promising_it(demo):
    """Ian's third tester run (2026-11-19): "We definately need to stop the run of the chapter once the post
    inverview starts, if the user clicks stop demo." The server stops it as the debrief starts (not the page:
    it never posts the stop itself), so the guide's goodbye no longer says it will be there."""
    w = demo
    p = w.open_demo()
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    p.say("Hi there.")
    start_chapter(p)
    w.status()
    p.wait("L.status.run === 'live-run' && L.status.state === 'making'")
    p.page.wait_for_timeout(1000)
    patient(p)
    p.page.click("#end-demo")
    p.wait("L.phase === 'handback'")
    assert [e["content"] for e in p.sent("session.commentary.append")][-1] == w.notes["handback_making"]
    assert "isn't stopped" not in w.notes["handback_making"] and "stops" in w.notes["handback_making"]
    p.page.wait_for_timeout(400)
    assert not debriefs(p)
    p.say("Thanks for the chat. I'm handing you back to the person who welcomed you.")
    p.page.wait_for_function("window.__interview.length === 1", timeout=8000)
    p.wait("!L.handingBack", timeout=6000)
    assert events(p)[-1]["event"] == "end_demo" and events(p)[-1]["phase"] == "making"
    assert not [path for path, _ in p.posts if path.endswith("/stop")]  # the server's to stop, not the page's


def test_end_demo_while_the_guide_rests_starts_the_debrief_at_once(demo):
    """No session is open (the guide stepped back, GPT-Live closed): nothing is said and none is opened to say
    it; the debrief starts on the mic the page kept."""
    w = demo
    p = w.open_demo()
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    p.say("Hi there.")
    start_chapter(p)
    p.page.click("#orb")  # the mic off
    p.wait("L.phase === 'resting' && !L.pc && !L.closing")
    assert p.page.text_content("#line") == "Tap to talk"
    p.js("""() => { const b = document.getElementById('end-demo'), line = document.getElementById('line');
      b.click(); b.click();
      window.__lines = [];  // every text the line shows from the tap on
      new MutationObserver(() => window.__lines.push(line.textContent)).observe(line, { childList: true, characterData: true, subtree: true });
    }""")
    p.page.wait_for_function("window.__interview.length === 1", timeout=4000)
    assert p.js("window.__interview[0]") == {"mode": "debrief", "stream": True, "live": True}
    assert "debrief" in p.body() and not end_demo(p)["shown"]
    p.wait("!L.handingBack && L.phase === 'idle'", timeout=6000)
    # No line as the screen cools: endConversation sets idle under the debrief, and "Tap to begin" (or the making
    # label) showed and faded just after the tap (review, 2026-11-18; Ian's note 2 was a stray "tap" at the handover).
    assert p.L("L.debriefing") and p.page.text_content("#line") == ""
    assert not p.js("window.__lines.filter((x) => x).length"), p.js("window.__lines")
    p.page.wait_for_timeout(300)
    assert not p.post_bodies("api/live/resume") and len(p.post_bodies("api/live/start")) == 1
    assert not [x for x in p.instructions() if "End demo" in x]
    assert events(p)[-1] == {"event": "end_demo", "phase": "resting", "chapter_t": None, "detail": None}
    assert debriefs(p) == 1 and p.post_bodies("api/chat/end")


def test_end_demo_while_paused_starts_the_debrief_at_once(demo):
    """Paused, GPT-Live is closed and the mic released: a session used to be opened just for the goodbye. Now
    the debrief starts at once, on a mic asked for in the tap."""
    w = demo
    p = start_tester_reading(w)
    p.page.click("#play")  # pause: GPT-Live closes
    p.wait("L.phase === 'paused' && !L.pc && !L.closing")
    mics = p.js("window.__mics")
    p.page.click("#end-demo")
    p.page.wait_for_function("window.__interview.length === 1", timeout=4000)
    assert p.js("window.__interview[0]") == {"mode": "debrief", "stream": True, "live": True}
    assert p.js("window.__mics") == mics + 1
    p.wait("!L.handingBack && L.phase === 'idle'", timeout=6000)
    assert p.L("L.debriefing") and p.page.text_content("#line") == ""  # not "Writing your chapter", fading
    assert not p.post_bodies("api/live/resume")
    assert not [x for x in p.instructions() if "End demo" in x]
    w.reported(state="done")  # heard as far as they wanted
    assert events(p, "end_demo")[-1]["phase"] == "paused" and debriefs(p) == 1


def test_end_demo_during_a_question_hands_back(demo):
    """The question in progress is logged at the tap (review, 2026-11-18): the debrief's account of what they
    asked reads 12_asks/asides.jsonl, and without the log it said "They never stopped it to ask anything" of a
    tester who pressed End demo while asking."""
    w = demo
    p = start_tester_reading(w)
    p.page.click("#orb")  # a question
    p.wait("L.phase === 'aside'")
    p.hear("Wait, who was she again?")
    p.page.wait_for_timeout(400)
    patient(p)
    p.page.click("#end-demo")
    p.wait("L.phase === 'handback'")
    assert p.instructions()[-1] == w.notes["handback_done"]
    p.page.wait_for_timeout(400)
    assert not debriefs(p)
    p.say("We'll stop there. I'm handing you back to the person who welcomed you.")
    p.page.wait_for_function("window.__interview.length === 1", timeout=8000)
    assert events(p, "end_demo")[-1]["phase"] == "aside" and debriefs(p) == 1
    [a] = p.post_bodies("api/live/aside")
    assert a["kind"] == "ask" and a["how"] == "end demo"
    assert {"who": "listener", "text": "Wait, who was she again?"} in a["lines"]
    [rec] = w.logged("12_asks/asides.jsonl")
    assert rec["kind"] == "ask" and rec["ended"] == "end demo"


def test_end_demo_during_a_check_in_is_reported_as_the_reading_not_a_question(demo):
    """Review (2026-11-18): a check-in (the guide filling time because the next part isn't written) was sent as
    phase "aside", which the debrief's account words as "while asking it a question": they asked nothing."""
    w = demo
    p = start_tester_reading(w)
    for pid in ("s1-p01", "s1-p02", "s1-p03"):
        p.wait(f"L.feed.cur && L.feed.cur.id === '{pid}'")
        read_through(p, w, pid)
    p.wait("L.phase === 'waiting'")
    w.status(waiting_for=2, check_in="check_in", eta_seconds=180)
    p.wait("L.phase === 'checkin'")
    p.page.click("#end-demo")
    p.wait("L.phase === 'handback'")
    assert events(p, "end_demo")[-1]["phase"] == "reading"


def test_end_demo_on_a_page_they_came_back_to_starts_the_debrief_from_the_tap(demo):
    """A tester who left and comes back (the page loaded fresh, the debrief not done): the button is where the
    library's card was, and the tap is the gesture the mic and the voice need. A debrief that drops part-way
    leaves the button there to go back to it; once done it is gone."""
    w = demo
    lis = as_tester(w.cfg, debrief_card="on")
    p = w.open_demo()
    p.wait("document.body.classList.contains('end-demo')")
    assert p.L("L.phase") == "idle" and p.js("window.__mics") == 0
    p.js("() => { const b = document.getElementById('end-demo'); b.click(); b.click(); }")
    p.page.wait_for_function("window.__interview.length === 1", timeout=4000)
    assert p.js("window.__interview[0]") == {"mode": "debrief", "stream": True, "live": True}
    assert p.js("window.__mics") == 1 and "debrief" in p.body() and not end_demo(p)["shown"]
    assert events(p) == [{"event": "end_demo", "phase": None, "chapter_t": None, "detail": None}]
    assert not p.post_bodies("api/live/start")
    p.js("window.__interviewDone()")  # it dropped: not done
    p.wait("!L.debriefing && document.body.classList.contains('end-demo')")
    p.page.click("#end-demo")
    p.page.wait_for_function("window.__interview.length === 2", timeout=4000)
    (lis / "study").mkdir(exist_ok=True)
    (lis / "study" / "debrief.json").write_text(json.dumps({"done": True}))
    p.js("window.__interviewDone()")
    p.wait("!L.debriefing && L.debriefDone")
    p.page.wait_for_timeout(600)
    assert not end_demo(p)["shown"] and debriefs(p) == 2


def test_end_demo_while_still_connecting_drops_the_connection_and_starts_the_debrief(demo):
    w = demo
    as_tester(w.cfg, debrief_card="on")
    p = w.open_demo()
    p.wait("document.body.classList.contains('end-demo')")
    p.js("() => { window.__autoStart = false; }")  # the guide's session never starts
    p.page.click("#orb")
    p.wait("L.phase === 'connecting' && L.pc")
    p.page.click("#end-demo")
    p.page.wait_for_function("window.__interview.length === 1", timeout=4000)
    assert p.js("window.__interview[0]") == {"mode": "debrief", "stream": True, "live": True}
    p.wait("!L.handingBack && L.phase === 'idle' && !L.pc", timeout=8000)
    p.page.wait_for_timeout(400)
    assert p.L("L.phase") == "idle" and debriefs(p) == 1
    assert not [x for x in p.instructions() if "End demo" in x]


def test_a_mic_refused_for_the_debrief_asks_for_it_and_try_again_goes_on(demo):
    w = demo
    as_tester(w.cfg, debrief_card="on")
    p = w.open_demo()
    p.wait("document.body.classList.contains('end-demo')")
    p.js("() => { window.__denied = navigator.mediaDevices.getUserMedia; "
         "navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('denied', 'NotAllowedError'); }; }")
    p.page.click("#end-demo")
    p.wait("L.micRefused === 'debrief' && !L.handingBack")
    assert p.page.text_content("#line") == "I need your microphone to talk with you"
    assert p.shown("#mic-again") and not debriefs(p) and end_demo(p)["shown"]
    p.js("() => { navigator.mediaDevices.getUserMedia = window.__denied; }")
    p.page.click("#mic-again")
    p.page.wait_for_function("window.__interview.length === 1", timeout=4000)
    assert not p.post_bodies("api/live/start")  # Try again went on to the debrief, not to the guide


def test_the_chapters_end_hands_back_instead_of_the_end_hold(demo):
    w = demo
    p = start_tester_reading(w, 1, 2)
    patient(p)
    for pid in w.paragraphs():
        p.wait(f"L.feed.cur && L.feed.cur.id === '{pid}' && L.feed.cur.sentAt")
        read_through(p, w, pid)
    p.wait("L.phase === 'handback'", timeout=4000)
    # As speakable context (what the reading itself is), which GPT-Live acts on at once: as an instruction, live
    # (2026-11-18), the guide said nothing at the chapter's end and the debrief followed 8 s of silence.
    said = len(p.instructions())
    assert [e["content"] for e in p.sent("session.commentary.append")][-1] == w.notes["handback_end"]
    assert not w.notes["handback_end"].startswith("[") and w.notes["handback_end"] not in p.instructions()
    p.page.wait_for_timeout(300)
    assert not end_demo(p)["shown"]  # the handback already runs: nothing left to tap
    p.js("() => Live.endDemo()")
    assert not debriefs(p)
    p.say("That's the story of the list. I'm passing you back to the person who welcomed you.")
    p.page.wait_for_function("window.__interview.length === 1", timeout=8000)
    assert p.js("window.__interview[0].mode") == "debrief"
    p.wait("!L.handingBack", timeout=6000)
    assert p.post_bodies("api/live/archive")  # heard to the end: archived as ever
    assert "debrief" in p.body()
    assert debriefs(p) == 1 and not events(p, "end_demo")
    assert len(p.instructions()) == said  # it spoke: not asked a second time


def test_a_guide_silent_at_the_chapters_end_is_asked_once_more_then_the_debrief_comes(demo):
    w = demo
    p = start_tester_reading(w, 1, 2)
    p.js("() => { Live.S.handback_again_ms = 500; Live.S.handback_silent_ms = 1500; }")
    for pid in w.paragraphs():
        p.wait(f"L.feed.cur && L.feed.cur.id === '{pid}' && L.feed.cur.sentAt")
        read_through(p, w, pid)
    p.wait("L.phase === 'handback'", timeout=4000)
    p.wait("window.__sent.some((e) => e.type === 'session.instructions.append' && e.content.startsWith('[The chapter has ended'))", timeout=3000)
    assert p.instructions()[-1] == "[" + w.notes["handback_end"] + "]"
    p.page.wait_for_function("window.__interview.length === 1", timeout=6000)
    took = p.marks("handed-back")[-1][0] - p.marks("handback")[-1][0]
    assert took < 5000, took  # no second wait in silence (it was 12 s)


def test_the_chapters_end_reports_the_whole_chapter_heard(demo):
    """Review: End demo (I'm done, then) and the end both report state "done"; the end reports the whole chapter
    as chapter_t, End demo where it stopped (demo/debrief.py reads chapter_t)."""
    w = demo
    p = start_tester_reading(w, 1, 2)
    for pid in w.paragraphs():
        p.wait(f"L.feed.cur && L.feed.cur.id === '{pid}' && L.feed.cur.sentAt")
        read_through(p, w, pid)
    words = sum(len(x["text"].split()) for x in w.paragraphs().values())
    got = w.reported(state="done")
    assert abs(got["chapter_t"] - words / 2.5) < 0.01


def test_end_demo_part_way_reports_where_it_stopped(demo):
    w = demo
    p = start_tester_reading(w, 1, 2)
    p.say(" ".join(w.paragraphs()["s1-p01"]["text"].split()[:4]))
    p.wait("L.feed.cur && L.feed.cur.matched >= 3")
    p.page.click("#end-demo")
    got = w.reported(state="done")
    assert got["chapter_t"] < 3  # four words heard, not the chapter


def test_a_done_debrief_is_never_handed_back_to_again(demo, cfg):
    """Review: a second chapter's end (or I'm done, now End demo) started a second full debrief whose summary
    overwrote the first. Once study/debrief.json says done, the chapter just ends and End demo isn't shown."""
    w = demo
    lis = Path(cfg["listener_dir"])
    (lis / "study").mkdir(parents=True, exist_ok=True)
    (lis / "study" / "debrief.json").write_text(json.dumps({"done": True, "session": "s1"}))
    p = start_tester_reading(w, 1, 2)
    assert p.L("L.debriefDone")
    p.wait("document.body.classList.contains('reading')")
    assert not end_demo(p)["shown"] and "end-demo" not in p.body()
    p.js("() => Live.endDemo()")
    p.page.wait_for_timeout(300)
    assert p.L("L.phase") == "reading" and not events(p)
    for pid in w.paragraphs():
        p.wait(f"L.feed.cur && L.feed.cur.id === '{pid}' && L.feed.cur.sentAt")
        read_through(p, w, pid)
    p.wait("L.phase === 'ended'", timeout=4000)
    p.page.wait_for_timeout(500)
    assert not p.js("window.__interview.length") and not p.L("L.handingBack")
    assert w.notes["handback_end"] not in p.instructions()
    assert w.notes["handback_end"] not in [e["content"] for e in p.sent("session.commentary.append")]


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
    p.page.click("#end-demo")
    p.page.wait_for_function("window.__interview.length === 1", timeout=12000)


def test_the_tail_of_the_passage_after_the_tap_is_not_taken_for_the_goodbye(demo):
    """Review (2026-11-18): the page asked "did the guide speak after the tap?", and mid-reading it always has
    (GPT-Live finishes a word or two). A guide that then ignored the note was waited for a second time: about
    14 s of silence before the debrief. The wait is timed from when the page begins to wait for the goodbye."""
    w = demo
    p = start_tester_reading(w)
    words = w.paragraphs()["s1-p01"]["text"].split()
    p.say(" ".join(words[:4]))
    p.wait("L.feed.cur && L.feed.cur.matched >= 3")
    p.js("""(tail) => { document.getElementById('end-demo').click();
      setTimeout(() => tail.forEach((w) => window.__emit({ type: "session.output_transcript.delta", delta: " " + w })), 60); }""",
         words[4:6])
    p.page.wait_for_function("window.__interview.length === 1", timeout=8000)  # the guide never says goodbye
    assert p.L("L.handbackTries") == 0
    assert p.L("L.spokeAt") > p.marks("handback")[-1][0]  # the tail did come after the tap


def test_a_reply_cut_by_the_tap_then_silence_is_still_asked_once_more(demo):
    """The same in conversation: the tail of the reply the tap cut also turned off the one retry as an instruction."""
    w = demo
    p = w.open_demo()
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    p.say("Hi there.")
    p.hear("Just back from a walk.")
    p.page.wait_for_timeout(1000)
    said = len(p.instructions())
    p.js("""() => { Live.S.handback_again_ms = 900;
      const word = (w) => window.__emit({ type: "session.output_transcript.delta", delta: " " + w });
      word("Oh"); word("lovely");  // mid-reply at the tap
      document.getElementById('end-demo').click();
      setTimeout(() => { word("where"); word("to"); }, 60); }""")
    p.wait(f"window.__sent.filter((e) => e.type === 'session.instructions.append').length === {said + 1}", timeout=4000)
    assert p.instructions()[-1] == "[" + w.notes["handback_talk"] + "]"
    p.page.wait_for_function("window.__interview.length === 1", timeout=8000)
    assert p.L("L.handbackTries") == 0 and len(p.instructions()) == said + 1


def test_a_start_chapter_result_landing_after_end_demo_says_nothing_more_to_the_guide(demo):
    """Tool results come through delegation, seconds after the guide's words: one landing mid-goodbye must not
    send the guide "carry on the conversation" on top of the handback note."""
    w = demo
    p = w.open_demo()
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    p.say("Hi there. A chapter on the list, then?")
    p.hear("Yes, let's do that.")
    p.page.wait_for_timeout(1000)
    patient(p)
    p.page.click("#end-demo")
    p.wait("L.phase === 'handback'")
    start_chapter(p)
    p.page.wait_for_timeout(300)
    assert not p.marks("after-start") and not [x for x in p.instructions() if "under way" in x]
    assert not debriefs(p)
    p.say("Lovely talking with you. I'm handing you back to the person who welcomed you.")
    p.page.wait_for_function("window.__interview.length === 1", timeout=5000)


def test_a_start_reading_result_landing_after_end_demo_does_not_take_the_handbacks_wait(demo):
    """Without the guard the tool's own wait replaced the handback's, and only the fallback timer (12.5 s here,
    35 s with the real settings) brought the debrief."""
    w = demo
    p = w.open_demo()
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    p.say("Hi there.")
    start_chapter(p)
    offered(p, w)
    p.page.wait_for_timeout(1000)
    patient(p)
    p.page.click("#end-demo")
    p.wait("L.phase === 'handback'")
    p.tool("start_reading", {})
    p.page.wait_for_timeout(400)
    assert not debriefs(p)
    p.say("It'll be here for you. I'm handing you back to the person who welcomed you.")
    p.page.wait_for_function("window.__interview.length === 1", timeout=5000)
    assert p.marks("handed-back")[-1][0] - p.marks("handback")[-1][0] < 5000
    assert not p.L("L.readStarted")


def test_the_debrief_comes_even_if_the_handbacks_wait_is_lost(demo):
    """The guarantee: whatever takes the page's one waiting step during the handback, a timer brings the debrief."""
    w = demo
    p = w.open_demo()
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    p.say("Hi there.")
    p.page.wait_for_timeout(1000)  # the guide quiet: the handback isn't ducked, its wait is set in the tap
    p.js("() => { Live.S.lead_in_max_ms = 100; Live.S.handback_again_ms = 20000; "
         "document.getElementById('end-demo').click(); Live.L.pending = null; }")  # the wait, lost
    assert p.L("L.phase") == "handback" and not p.L("L.pending")
    p.page.wait_for_timeout(1500)
    assert not debriefs(p)
    p.page.wait_for_function("window.__interview.length === 1", timeout=14000)  # the timer: 8.3 s after the tap


def test_a_failed_ask_for_the_end_demo_state_is_asked_again_when_a_conversation_starts(demo):
    """Review (2026-11-18): the one fetch of /api/live/demo at page load failing left a returning tester with no
    End demo for the whole visit, even through a conversation and a chapter."""
    w = demo
    as_tester(w.cfg, debrief_card="on")
    calls = []

    def flaky(page):
        def once(route):
            calls.append(1)
            if len(calls) == 1:
                return route.fulfill(status=500, body="no")
            route.continue_()
        page.route("**/api/live/demo", once)
    p = w.open_demo(before=flaky)
    p.page.wait_for_timeout(600)
    assert calls and not end_demo(p)["shown"] and p.L("L.demo") is None
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    p.wait("document.body.classList.contains('end-demo')")
    assert end_demo(p)["shown"] and len(calls) == 2


@pytest.mark.parametrize("where", ["chapter view", "scrolled to the library"])
def test_the_dot_is_on_screen_for_a_debrief_started_from_the_library(demo, where):
    """Review (2026-11-18): End demo is fixed over the library and its chapter view. Tapped from the chapter view
    (which hides the circle's screen) or scrolled down the library, the debrief ran on a blank page: the card it
    replaced scrolled to the top first."""
    w = demo
    as_tester(w.cfg, debrief_card="on")
    p = w.open_demo()
    p.wait("document.body.classList.contains('end-demo')")
    p.page.wait_for_selector("#library #lib-home", state="attached")
    if where == "chapter view":
        p.js(f"() => {{ location.hash = '#/chapter/{RUN}'; }}")
        p.page.wait_for_function("document.documentElement.classList.contains('lib-in-chapter')")
        assert p.js("getComputedStyle(document.getElementById('screen')).display") == "none"
    else:
        p.js("() => { const lib = document.getElementById('library'); lib.style.minHeight = '3000px'; "
             "lib.scrollIntoView({ block: 'start' }); }")
        p.page.wait_for_function("scrollY > 200")
    assert end_demo(p)["hit"]
    p.page.click("#end-demo")
    p.page.wait_for_function("window.__interview.length === 1", timeout=4000)
    orb = p.js("""() => { const o = document.getElementById('orb'), r = o.getBoundingClientRect();
      let shown = true;
      for (let e = o; e && e !== document.documentElement; e = e.parentElement) shown = shown && getComputedStyle(e).display !== 'none';
      return { shown, top: r.top, bottom: r.bottom, height: innerHeight, scrollY }; }""")
    assert orb["shown"] and orb["top"] >= 0 and orb["bottom"] <= orb["height"] and orb["scrollY"] == 0, orb
    if where == "chapter view":
        p.js("window.__interviewDone()")
        p.wait("!L.debriefing && !document.body.classList.contains('debrief')")
        assert p.js("location.hash") == f"#/chapter/{RUN}"
        assert p.js("getComputedStyle(document.getElementById('screen')).display") == "none"  # the chapter view, as it was


# --- 6. what the tester did, for the debrief ----------------------------------------------------------
# Ian (2026-11-18): "we should pass a bit of context about the things that it did or buttons it pressed, like if
# they asked a question". The page reports each press (POST /api/demo/event) before acting on it.

def test_button_presses_are_reported_with_the_phase_they_were_pressed_in(demo):
    w = demo
    p = w.open_demo()
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    p.say("Hi there.")
    start_chapter(p)
    p.page.click("#orb")  # the mic off: not a press the debrief asks about
    p.wait("L.phase === 'resting' && !L.pc && !L.closing")
    assert not events(p)
    p.page.click("#orb")  # a tap brings the guide back
    p.wait("L.phase === 'talk' && L.liveId === 'live_2'", timeout=6000)
    assert events(p) == [{"event": "tap_talk", "phase": "resting", "chapter_t": None, "detail": None}]
    p.say("I'm here.")
    offered(p, w)
    p.page.wait_for_selector("#player:not([hidden])")
    p.page.click("#play")  # the bar's play starts the ready chapter
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.sentAt", timeout=4000)
    assert events(p)[-1] == {"event": "play", "phase": "making", "chapter_t": None, "detail": None}
    p.say(" ".join(w.paragraphs()["s1-p01"]["text"].split()[:6]))
    p.wait("L.feed.cur && L.feed.cur.matched >= 5")
    p.page.click("#orb")  # a question, tapped during the reading
    p.wait("L.phase === 'aside'")
    ask = events(p)[-1]
    assert (ask["event"], ask["phase"], ask["detail"]) == ("ask", "reading", None) and 1 <= ask["chapter_t"] <= 4
    p.page.click("#orb")  # back to the chapter: not reported
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.sentAt", timeout=4000)
    assert len(events(p)) == 3
    p.page.click("#play")  # pause
    p.wait("L.phase === 'paused' && !L.pc && !L.closing")
    assert (events(p)[-1]["event"], events(p)[-1]["phase"]) == ("pause", "reading")
    p.js("() => { const s = document.getElementById('seek'); s.value = '400'; s.dispatchEvent(new Event('change')); }")
    p.wait("L.log.some((x) => x[1] === 'seek')")
    seek = events(p)[-1]
    assert (seek["event"], seek["phase"]) == ("seek", "paused") and seek["detail"].startswith("to 0:")
    p.page.click("#play")  # and on
    p.wait("L.phase === 'reading'", timeout=6000)
    assert (events(p)[-1]["event"], events(p)[-1]["phase"]) == ("play", "paused")
    p.page.click("#play")
    p.wait("L.phase === 'paused' && !L.pc && !L.closing")
    p.page.click("#orb")  # a question from the pause
    p.wait("L.phase === 'aside'", timeout=6000)
    assert (events(p)[-1]["event"], events(p)[-1]["phase"]) == ("ask", "paused")
    assert [e["event"] for e in events(p)] == ["tap_talk", "play", "ask", "pause", "seek", "play", "pause", "ask"]
    assert all(set(e) == {"event", "phase", "chapter_t", "detail"} for e in events(p))


def test_reporting_a_press_never_holds_the_page_up(demo):
    """The helper never throws and nothing waits on it: with the route failing, or fetch itself throwing, the
    press is acted on all the same."""
    w = demo
    p = start_tester_reading(w)
    p.page.route("**/api/demo/event", lambda route: route.abort())
    p.page.click("#play")
    p.wait("L.phase === 'paused' && !L.pc && !L.closing")
    p.js("""() => { const f = window.fetch;
      window.fetch = (u, o) => { if (String(u).includes('/api/demo/event')) throw new Error('no'); return f(u, o); }; }""")
    p.page.click("#play")
    p.wait("L.phase === 'reading'", timeout=6000)
    p.page.click("#end-demo")
    p.wait("L.phase === 'handback'")


def test_the_library_has_no_debrief_card(demo):
    """The card ("Got five minutes to tell Ian how it went?") is gone, whatever Ian's setting: End demo is the
    way on to the debrief."""
    w = demo
    as_tester(w.cfg, debrief_card="on")
    p = w.open_demo()
    p.page.wait_for_selector("#library #lib-home", state="attached")
    p.page.wait_for_timeout(1200)
    assert p.js("document.getElementById('lib-debrief')") is None and p.js("document.getElementById('debrief-start')") is None
    assert "Got five minutes" not in p.page.text_content("#library")
    state = p.js("fetch('/api/library').then((r) => r.json())")
    assert "debrief" not in state


# --- the phone's loudspeaker (page/speaker.js; Ian, 2026-11-21) ---------------------------------------------------

def test_a_mic_handed_over_on_a_phone_is_kept_on_the_loudspeaker(demo):
    """The gate's mic, handed to the guide: counted as live, so "play-and-record" holds while it is open, even
    for a stream asked for some other way."""
    w = demo
    p = w.open_demo(tester=False, touch=True, before=lambda page: page.add_init_script(AUDIO_SESSION))
    p.js("""async () => {
      const s = await navigator.mediaDevices.getUserMedia({ audio: true });  // not through Speaker.mic
      Live.handoff({ name: "Sam", stream: s });
    }""")
    p.wait("L.started && L.phase === 'talk'")
    assert p.js("window.__typeAtMic") == ["auto"] and p.js("navigator.audioSession.type") == "play-and-record"
    assert p.js("Speaker.live()")


def test_the_debriefs_mic_on_a_phone_asks_for_the_loudspeaker_first_and_lets_it_go_after(demo):
    """End demo on a page they came back to: the mic asked for in the tap comes after "play-and-record"; the
    debrief over and its mic stopped, the type goes back to "auto"."""
    w = demo
    as_tester(w.cfg, debrief_card="on")
    p = w.open_demo(touch=True, before=lambda page: page.add_init_script(AUDIO_SESSION))
    p.wait("document.body.classList.contains('end-demo')")
    p.js("document.getElementById('end-demo').click()")
    p.page.wait_for_function("window.__interview.length === 1", timeout=4000)
    assert p.js("window.__typeAtMic") == ["play-and-record"] and p.js("navigator.audioSession.type") == "play-and-record"
    p.js("window.__interviewDone()")
    p.wait("!L.debriefing")
    assert p.js("navigator.audioSession.type") == "auto" and not p.js("Speaker.live()")
