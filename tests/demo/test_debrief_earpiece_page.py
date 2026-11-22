"""The debrief's earpiece on the page (page/interview.js; docs/debrief-earpiece-design.md section 3), in headless
Chromium against the real server in tester mode: looks at the start, while they answer and at a turn's end nothing
covered; a note sent at once as an instruction, or dropped once its turn was taken; nothing after the farewell or the
clock; two failures take it down; off, it never asks or posts anything; and the panel shows the debrief for a tester
with the earpiece view on. GPT-Live is the stub from tests/demo/test_gate_page.py; /api/interview/direct is answered by
Playwright routes (no model is reached); fates reach the real route and land in the debrief's transcript."""

import json
import socket
import threading
import time
import types

import pytest

import server
from fakes import FakeAnthropic, Responder
from stages import llm, memory
from voice.gpt_live import session as live_session

sync_api = pytest.importorskip("playwright.sync_api")

from test_demo_flow import Page  # noqa: E402  (the stub-driven page: posts, say, emit, tool, wait)

KNOBS = {"enabled": True, "hear_words": 4, "hear_every_ms": 1500, "turn_merge_ms": 600, "timeout_ms": 2000}
NO_CHANGE = {"read": "r", "plan": [{"item": "Hear the opening answer", "state": "open"}], "next": None,
             "changed": False, "kind": None, "about": None, "note": None, "ms": 5, "cost_usd": 0.0}
DEBRIEF_JS = """async () => { window.__looks = []; const s = await navigator.mediaDevices.getUserMedia({ audio: true });
  await Interview.start("debrief", { stream: s, onDone: (x) => { window.__done = x; },
                                      onLook: (e) => { window.__looks.push(e); } }); }"""


def whisper(note, about="follow_up"):
    return {**NO_CHANGE, "changed": True, "kind": "next_turn", "about": about, "note": note}


@pytest.fixture
def site(cfg, own_listeners, monkeypatch):
    import uvicorn

    from demo import interviewer as IV
    from demo import testers

    cfg["live"]["provider"] = "gpt_live"
    cfg["demo"]["enabled"] = True
    cfg["demo"]["interviewer"]["page"] = {**(cfg["demo"]["interviewer"].get("page") or {}), "line_quiet_ms": 200,
                                          "close_quiet_ms": 300, "close_min_ms": 100, "close_max_ms": 4000,
                                          "act_grace_ms": 400, "greet_fallback_ms": 100000, "reply_wait_ms": 60000,
                                          "hanging_ms": 60000}
    cfg["demo"]["interviewer"]["director"] = dict(KNOBS)
    cfg["live"]["page"].update(line_quiet_ms=300, greet_fallback_ms=100000, talk_rest_secs=1000, hello_silence_secs=1000,
                               pitch_nudge_secs=[1000, 1000])
    monkeypatch.setattr(llm, "_client", FakeAnthropic(Responder()))
    monkeypatch.setattr(memory, "after_session", lambda *a, **k: False)
    monkeypatch.setattr(IV, "_after_end", lambda *a: None)
    offers = []

    def webrtc(cfg_, session, sdp, client=None):
        offers.append(session)
        return {"id": f"live_{len(offers)}", "sdp": "ANSWER"}
    monkeypatch.setattr(live_session, "create_webrtc", webrtc)
    t = testers.create(cfg, "Sam Tester", earpiece_view=True)
    app = server.create_app(server.Pipeline(), cfg, remember_on_start=False)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    deadline = time.monotonic() + 10
    while not srv.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield types.SimpleNamespace(base=f"http://127.0.0.1:{port}", tester=t, cfg=cfg, folder=own_listeners / t["lid"])
    srv.should_exit = True


@pytest.fixture
def page():
    p = Page()
    yield p
    p.close()


def answering(p, answers=()):
    """POST /api/interview/direct answered by the next of `answers` (a look, or "error" for a 500); then no change.
    Returns the requests' bodies."""
    asked, it = [], iter(answers)

    def handle(route):
        body = json.loads(route.request.post_data or "{}")
        asked.append(body)
        a = next(it, NO_CHANGE)
        if a == "error":
            route.fulfill(status=500, content_type="application/json", body="{}")
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"id": body["id"], **a}))
    p.page.route("**/api/interview/direct", handle)
    return asked


def debrief(site, p):
    """The tester's gate page (intro not done), then a debrief started by hand, as live.js would."""
    p.page.goto(site.base + "/t/" + site.tester["token"])
    p.wait("window.Interview")
    p.js(DEBRIEF_JS)
    p.wait("window.Interview.I.started && window.Interview.I.sid")


def instructions(p):
    return [e["content"] for e in p.js("window.__sent") if e["type"] == "session.instructions.append"]


def fates(site):
    rows = []
    for f in sorted((site.folder / "study").glob("debrief-*.jsonl")):
        rows += [json.loads(x) for x in f.read_text().splitlines()]
    return [r for r in rows if r.get("who") == "director_fate"]


def wait_until(p, cond, timeout=6):
    """Poll `cond` while letting Playwright run (its route handlers only run inside its calls)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        p.page.wait_for_timeout(50)
    raise AssertionError("timed out")


def hear(p, text):
    p.emit({"type": "session.input_transcript.delta", "delta": " " + text})


def test_off_it_never_looks_or_posts(site, page):
    site.cfg["demo"]["interviewer"]["director"]["enabled"] = False
    asked = answering(page)
    debrief(site, page)
    assert page.js("window.Interview.I.S.direct.enabled") is False
    page.say("Welcome back, Sam. What's one thing that stood out to you?")
    page.page.wait_for_timeout(500)
    hear(page, "The voice was warm and it picked up on my hiking trip right away.")
    page.page.wait_for_timeout(1800)
    page.say("The hiking trip, nice. What made that land for you?")
    page.page.wait_for_timeout(600)
    assert asked == [] and page.posted("api/interview/direct/fate") == [] and page.js("window.__looks") == []
    assert not any("Earpiece note" in x for x in instructions(page))


def test_it_looks_at_the_start_while_they_answer_and_its_note_goes_at_once(site, page):
    asked = answering(page, [NO_CHANGE, whisper("Ask which moment of the hiking story felt most like theirs.")])
    debrief(site, page)
    wait_until(page, lambda: len(asked) == 1)
    assert asked[0]["trigger"] == "opened" and asked[0]["sid"] == page.js("window.Interview.I.sid")
    page.say("Welcome back, Sam. What's one thing that stood out to you?")
    page.page.wait_for_timeout(500)
    hear(page, "The voice was warm and it picked up")  # 7 words, still going: a look while they answer
    wait_until(page, lambda: len(asked) == 2)
    assert asked[1]["trigger"] == "hearing" and "picked up" in asked[1]["hearing"]
    # The opening as the voice's last lines (under load the stub's words can arrive more than line_quiet_ms apart, and
    # the page then ends a line in the middle: the full suite once split it after "What's one thing that").
    said = " ".join(x["text"] for x in asked[1]["recent"] if x["who"] == "out")
    assert said == "Welcome back, Sam. What's one thing that stood out to you?" and asked[1]["recent"][-1]["who"] == "out"
    assert asked[1]["page"]["questions"] >= 1 and asked[1]["page"]["time_up"] is False
    note = "Ask which moment of the hiking story felt most like theirs."
    want = page.js("window.Interview.I.notes.direct").replace("{note}", note)
    page.wait(f"window.__sent.some((e) => e.content === {json.dumps(want)})")
    assert page.js("window.Interview.I.noteKind") == "direct"
    wait_until(page, lambda: any(f["fate"] == "sent" and f["id"] == 2 for f in fates(site)))
    assert not any("text" in f or "note" in f for f in fates(site))  # never read as speech
    page.page.wait_for_timeout(1500)  # their turn ends: the look while they spoke covered it (no new words since)
    assert [a["trigger"] for a in asked] == ["opened", "hearing"]
    kinds = [e["type"] for e in page.js("window.__looks")]
    assert kinds[0] == "conversation" and "ask" in kinds and "look" in kinds and "fate" in kinds and "note" in kinds
    assert page.js("window.__looks[0].view") is True  # this tester has the earpiece view on


def test_a_short_answer_gets_a_look_at_its_end(site, page):
    asked = answering(page)
    debrief(site, page)
    wait_until(page, lambda: len(asked) == 1)
    page.say("Welcome back, Sam. What's one thing that stood out to you?")
    page.page.wait_for_timeout(500)
    hear(page, "It was fine.")  # three words: no look while they spoke
    wait_until(page, lambda: len(asked) == 2)
    assert asked[1]["trigger"] == "turn_end" and asked[1]["hearing"] == ""
    assert asked[1]["recent"][-1] == {"who": "in", "text": "It was fine."}


def test_a_note_for_a_turn_already_taken_is_dropped(site, page):
    """The look was asked while they answered; the voice replied before its note came back: used now it would ask
    the old thing a turn late, so it is dropped (moved_on) and nothing is sent."""
    answering(page)
    debrief(site, page)
    page.page.wait_for_timeout(300)
    page.page.unroute("**/api/interview/direct")
    page.js("""() => { window.__held = []; const f = window.fetch;
        window.fetch = (u, o) => {
          if (!/\\/api\\/interview\\/direct$/.test(String(u))) return f(u, o);
          const id = JSON.parse(o.body).id;
          return new Promise((res) => window.__held.push(() => res(new Response(JSON.stringify({id, read: 'r', plan: [],
            next: null, changed: true, kind: 'next_turn', about: 'follow_up', note: 'Ask about the hike.'}),
            {status: 200, headers: {'Content-Type': 'application/json'}}))));
        }; }""")
    page.say("Welcome back, Sam. What's one thing that stood out to you?")
    page.page.wait_for_timeout(500)
    hear(page, "The voice was warm and it picked up on my hike")
    page.wait("window.__held.length === 1")
    page.page.wait_for_timeout(1500)
    page.say("A hike, lovely. What made the voice feel warm to you?")  # the voice takes its turn
    page.page.wait_for_timeout(500)
    page.js("() => window.__held[0]()")
    wait_until(page, lambda: any(f["fate"] == "dropped" for f in fates(site)))
    got = [f for f in fates(site) if f["fate"] == "dropped"][0]
    assert got["reason"] == "moved_on"
    assert not any("Ask about the hike." in x for x in instructions(page))


def test_nothing_after_the_farewell(site, page):
    asked = answering(page)
    debrief(site, page)
    wait_until(page, lambda: len(asked) == 1)
    page.say("Welcome back, Sam. What's one thing that stood out to you?")
    page.page.wait_for_timeout(500)
    hear(page, "Nothing much really.")
    wait_until(page, lambda: len(asked) == 2)
    page.page.wait_for_timeout(300)
    page.say("Thank you, Sam. That really helps Ian. That's everything from me.")
    page.page.wait_for_timeout(400)
    n = len(asked)
    hear(page, "Bye then, it was really lovely talking with you today")  # past the farewell: never looked at
    page.page.wait_for_timeout(1500)
    assert len(asked) == n


def test_two_failures_take_it_down(site, page):
    asked = answering(page, ["error", "error"])
    debrief(site, page)
    wait_until(page, lambda: len(asked) == 1)
    page.say("Welcome back, Sam. What's one thing that stood out to you?")
    page.page.wait_for_timeout(500)
    hear(page, "It was fine.")
    wait_until(page, lambda: len(asked) == 2)
    page.wait("window.Interview.I.lookDown")
    page.page.wait_for_timeout(300)
    page.say("Fine, okay. What part of it felt fine?")
    page.page.wait_for_timeout(400)
    hear(page, "The part about the river was good I guess")
    page.page.wait_for_timeout(1500)
    assert len(asked) == 2
    kinds = [e["type"] for e in page.js("window.__looks")]
    assert kinds.count("fail") == 2 and "down" in kinds


def test_the_panel_shows_the_debrief_for_a_tester_with_the_view(site, page):
    """The whole flow to the debrief (as tests/demo/test_demo_flow.py), the earpiece view on: the panel stays up
    under the cooled screen while it shows the debrief, labelled so, with the note sent to the interviewer."""
    asked = answering(page, [whisper("Ask what made the voice feel warm, in their words.")])
    page.page.goto(site.base + "/t/" + site.tester["token"])
    page.wait("window.Interview")
    page.page.click("#w-gate")
    page.wait("window.Interview.I.started && !window.Interview.I.connecting")
    page.say("Thanks so much for making the time. I'll hand you over now.")
    page.tool("handoff_to_guide")
    page.wait("window.Live && window.Live.L && window.Live.L.started", timeout=10000)
    page.wait("window.Live.L.phase === 'talk'")
    page.say("Hi Sam! I'm Wren.")
    page.page.wait_for_selector("#end-demo", state="visible", timeout=5000)
    page.wait("!document.body.classList.contains('handoff')", timeout=6000)
    page.page.wait_for_timeout(500)
    page.page.click("#end-demo")
    page.wait("window.Live.L.phase === 'handback'")
    page.page.wait_for_timeout(400)
    page.say("Good to meet you, Sam. I'm handing you back to the person who welcomed you.")
    page.wait("window.Interview.I.mode === 'debrief' && window.Interview.I.started", timeout=10000)
    wait_until(page, lambda: len(asked) >= 1, timeout=10)
    page.wait("window.Earpiece && document.getElementById('earpiece')", timeout=6000)
    page.wait("document.body.classList.contains('debrief')")
    page.wait("document.querySelector('#earpiece .fate[data-fate=sent]')", timeout=6000)
    assert page.page.is_visible("#earpiece")
    head = page.js("document.getElementById('earpiece-head').innerText")
    assert "Debrief" in head and "1 sent" in head
    text = page.js("document.getElementById('earpiece-list').innerText")
    assert "Sent to the interviewer" in text and "Ask what made the voice feel warm" in text and "Debrief" in text
    assert page.js("window.Live.L.dirEvents.some((e) => e.session && e.session.startsWith('debrief-'))")


# --- after the review (2026-11-22) -------------------------------------------------------------------------

HOLD_JS = """(note) => { window.__held = []; const f = window.fetch;
    window.fetch = (u, o) => {
      if (!/\\/api\\/interview\\/direct$/.test(String(u))) return f(u, o);
      const id = JSON.parse(o.body).id;
      return new Promise((res) => window.__held.push(() => res(new Response(JSON.stringify({id, read: 'r', plan: [],
        next: null, changed: true, kind: 'next_turn', about: 'follow_up', note}),
        {status: 200, headers: {'Content-Type': 'application/json'}}))));
    }; }"""


def held_look(site, page, note):
    """A debrief whose looks are held until the test releases them; the opening said and a look asked while they
    answer."""
    answering(page)
    debrief(site, page)
    page.page.wait_for_timeout(300)
    page.page.unroute("**/api/interview/direct")
    page.js(HOLD_JS, note)
    page.say("Welcome back, Sam. What's one thing that stood out to you?")
    page.page.wait_for_timeout(500)
    hear(page, "The voice was warm and it picked up on my hike")
    page.wait("window.__held.length === 1")
    page.page.wait_for_timeout(700)


def test_an_invitation_to_go_on_leaves_the_turn_theirs(site, page):
    """ "Sorry, go on." cuts into their pause, and the turn is still theirs: the note in flight is not stale."""
    held_look(site, page, "Ask about the hike.")
    page.say("Sorry, go on.")
    page.page.wait_for_timeout(500)
    page.js("() => window.__held[0]()")
    wait_until(page, lambda: any(f["fate"] == "sent" for f in fates(site)))
    assert any("Ask about the hike." in x for x in instructions(page))


def test_a_note_that_comes_while_the_voice_answers_waits_for_its_line(site, page):
    """The voice was already answering them when the note came back: that answer may be the turn the note was for.
    It waits for the line: a question takes the turn (moved_on); a stall leaves it, and the note goes then."""
    held_look(site, page, "Ask about the hike.")
    page.emit({"type": "session.output_transcript.delta", "delta": " A hike,"})  # mid-line
    page.js("() => window.__held[0]()")
    page.page.wait_for_timeout(100)
    assert page.js("!!window.Interview.I.heldNote") and not any("Ask about the hike." in x for x in instructions(page))
    page.say("lovely. What made it feel warm?")
    wait_until(page, lambda: any(f["fate"] == "dropped" for f in fates(site)))
    assert [f for f in fates(site) if f["fate"] == "dropped"][0]["reason"] == "moved_on"
    assert not any("Ask about the hike." in x for x in instructions(page))


def test_a_note_held_through_a_stall_goes_when_it_ends(site, page):
    held_look(site, page, "Ask about the hike.")
    page.emit({"type": "session.output_transcript.delta", "delta": " Just a"})
    page.js("() => window.__held[0]()")
    page.page.wait_for_timeout(100)
    page.say("second.")
    wait_until(page, lambda: any(f["fate"] == "sent" for f in fates(site)))
    assert any("Ask about the hike." in x for x in instructions(page))


def test_a_look_in_flight_when_it_closes_is_let_go_and_the_panel_told(site, page):
    held_look(site, page, "Ask about the hike.")
    page.js("() => window.Interview.end()")
    page.wait("!window.Interview.I.sid")
    page.js("() => window.__held[0]()")
    page.page.wait_for_timeout(300)
    assert any(e["type"] == "forget" for e in page.js("window.__looks"))
    assert not any("Ask about the hike." in x for x in instructions(page))


def test_the_panel_keeps_the_guides_late_look_out_of_the_debriefs_plan(page):
    """A guide look still in flight at End demo is answered after the debrief began: its plan must not become the one
    the debrief's first plan is marked against, and its earpiece going down is not the debrief's."""
    from stages.common import ROOT
    p = page.page
    p.set_content("<html><body></body></html>")
    p.evaluate("""() => { window.Live = { L: { dirEvents: [], earpieceLoaded: true }, S: { director: { enabled: true } } }; }""")
    p.add_script_tag(content=(ROOT / "page" / "earpiece.js").read_text(encoding="utf-8"))
    p.wait_for_function("window.Earpiece")
    p.evaluate("""() => {
      const t = Date.now(), ev = (e) => window.Earpiece.event({ at: t, secs: 1, ...e });
      ev({ type: "conversation", session: "s1" });
      ev({ type: "ask", session: "s1", id: 1, trigger: "cadence", speaking: "none" });
      ev({ type: "look", session: "s1", id: 1, got: { read: "g", plan: [{ item: "Meet them", state: "open" }] } });
      ev({ type: "ask", session: "s1", id: 2, trigger: "cadence", speaking: "none" });
      ev({ type: "conversation", session: "debrief-x", debrief: true });
      ev({ type: "look", session: "s1", id: 2, got: { read: "g", plan: [{ item: "Meet them", state: "done" }] } });
      ev({ type: "down", session: null });
      ev({ type: "ask", session: "debrief-x", id: 1, trigger: "opened", speaking: "none" });
      ev({ type: "look", session: "debrief-x", id: 1, got: { read: "d", plan: [{ item: "Opening: what stood out", state: "open" }] } });
    }""")
    marks = p.evaluate("""() => [...document.querySelectorAll('#earpiece-list li.look[data-session="debrief-x"] li[data-change]')]
                          .map((x) => x.dataset.change)""")
    assert marks == [""], marks   # the debrief's first plan: nothing marked new against the guide's
    assert "down" not in p.evaluate("document.getElementById('earpiece-head').innerText")
    guide = p.evaluate("""() => [...document.querySelectorAll('#earpiece-list li.look[data-session="s1"] li[data-change]')]
                          .map((x) => x.dataset.change)""")
    assert "done" in guide   # the guide's own late look is still marked against the guide's plan
