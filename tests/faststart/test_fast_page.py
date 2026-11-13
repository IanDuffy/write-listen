"""Fast Start milestone 5, the page (docs/fast-start.md, "On the page" and "When a section isn't
ready"; acceptance checks 5 and 6, the page's half), in headless Chromium when Playwright is installed.

The real server serves the page over a fast run's folder built here: status.json as the run writes
it (sections and their pieces), and each piece a second or two of real, silent MP3. The test plays
the run's part by rewriting status.json, and reads what the page told the server from
fast/player.json. The ElevenLabs browser client is a stub the test drives, as in test_ask_page.py.
No network, no model, no key; every folder is a temp one.
"""

import json
import os
import socket
import subprocess
import threading
import time

import pytest

import ask_fixture as AF
import server
from fast_run_fakes import offline  # noqa: F401  (no live client for the whole session)
from ask import aside as A
from ask import routes as ask_routes
from faststart import run as fast_run
from stages import memory, voice

sync_api = pytest.importorskip("playwright.sync_api")

STUB = """
window.__el = { delay: 100, sessions: [], inVol: 0, outVol: 0.4 };
window.ElevenLabsClient = { Conversation: { startSession: (opts) => new Promise((resolve) => {
  setTimeout(() => {
    const conv = { opts, sent: [], ctx: [], muted: [], ended: false,
      sendUserMessage(t) { this.sent.push(t); }, sendContextualUpdate(t) { this.ctx.push(t); },
      setMicMuted(b) { this.muted.push(b); },
      async endSession() { this.ended = true; },
      getInputVolume() { return window.__el.inVol; }, getOutputVolume() { return window.__el.outVol; },
      mode(m) { opts.onModeChange({ mode: m }); },
      say(text) { this.mode("speaking"); opts.onMessage({ role: "agent", message: text }); this.mode("listening"); },
      hear(text) { opts.onMessage({ role: "user", message: text }); },
      tool(name, p) { return opts.clientTools[name](p); } };
    window.__el.sessions.push(conv);
    opts.onConnect({ conversationId: "conv_" + window.__el.sessions.length });
    resolve(conv);
  }, window.__el.delay);
}) } };
"""
TITLES = {1: "A wall that heals", 2: "The recipe nobody wrote down", 3: "What the sea does", 4: "Why we forgot"}
LOCKED_1 = "<!-- seg01 -->\n\nRoman harbours still stand in the sea. [D01]\n\nNobody expected a wall to get stronger. [D02]"
WORDS = {"played": "played", "playing": "playing", "ready": "ready", "being_written": "being written"}


def write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
    os.replace(tmp, p)  # the server may be reading it


class FastRun:
    """A fast run's folder, and the run's side of the page: pieces appear, status.json is rewritten."""

    def __init__(self, runs, name="fast-run", sections=3):
        self.root, self.name = runs / name, name
        self.numbers = list(range(1, sections + 1))
        self.pieces = {n: [] for n in self.numbers}
        self.complete, self.states, self.titles, self.more = set(), {}, dict(TITLES), {}
        write(self.root / "fast.json", {"mode": "fast", "seed_set": 0})
        write(self.root / "05_outline.json", {
            "title": "Concrete That Heals", "through_line": "A material that was meant to change.",
            "sections": [{"n": n, "title": TITLES[n], "lands": f"Lands {n}."} for n in self.numbers]})
        write(self.root / "06_segments" / "seg01.md", LOCKED_1)

    def add(self, n, seconds=1.0):
        """A narrated piece: real audio of that length (silence), under a name that is never reused."""
        i = len(self.pieces[n])
        stem = self.root / "10_audio" / f"sec{n:02d}" / f"p{i:02d}"
        stem.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", str(seconds),
                        "-b:a", "8k", str(stem.with_suffix(".mp3"))], check=True)
        stem.with_suffix(".wav").write_bytes(b"")
        write(stem.with_suffix(".json"), {"text": f"Piece {i} of part {n}.", "seconds": seconds, "pause_ms": 0})
        self.pieces[n].append(seconds)
        return self

    def status(self, **more):
        """status.json as faststart/run.py writes it (faststart/status.py), with this process's pid so the
        server takes the run for alive."""
        self.more.update(more)
        listed = [{"n": n, "title": self.titles[n],
                   "state": self.states.get(n) or ("ready" if self.pieces[n] else "being_written"),
                   "ready": bool(self.pieces[n]), "complete": n in self.complete,
                   "pieces": [{"src": f"10_audio/sec{n:02d}/p{i:02d}.mp3", "seconds": s} for i, s in enumerate(self.pieces[n])],
                   "seconds": sum(self.pieces[n]), "text": None, "timing": None} for n in self.numbers]
        write(self.root / "status.json", {
            "run": self.name, "state": "making", "mode": "fast", "pid": os.getpid(), "stage": "section 2",
            "label": "Writing part 2 of 3", "stages_done": 3, "stages_total": 9, "minutes_left": None,
            "title": "Concrete That Heals", "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "sections": listed,
            "first_audio": any(self.pieces.values()), "waiting_for": None, "eta_seconds": None, "check_in": None,
            **self.more})
        return self

    def player(self):
        try:
            return json.loads((self.root / "fast" / "player.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def reported(self, timeout=6, **want):
        """Wait until the page's last position report has these values."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            got = self.player()
            if got and all(got.get(k) == v for k, v in want.items()):
                return got
            time.sleep(0.05)
        raise AssertionError(f"the page never reported {want}; the last report was {self.player()}")


@pytest.fixture
def site(cfg, tmp_path, monkeypatch):
    """The real server on a free port over a temp runs folder, with no ElevenLabs and no memory pass."""
    import uvicorn

    cfg["listener_dir"] = str(tmp_path / "listener")
    cfg["voice"]["client_js"] = "/stub-el.js"
    cfg["ask"].update(fade_ms=50, resume_delay_ms=50, slow_line_secs=4, give_up_secs=8)
    cfg["fast"].update(silence_seconds=1, position_seconds=0.3, gap_mic="auto")
    cfg["fast"]["page"].update(section_gap_ms=150, ready_beat_ms=150, decision_wait_seconds=30, tap_wait_seconds=1.2,
                               quiet_seconds=0.4)
    monkeypatch.setattr(voice, "signed_url", lambda cfg, client=None, kind="guide": f"wss://example/{kind}")
    monkeypatch.setattr(voice, "save_conversation", lambda *a, **k: None)
    monkeypatch.setattr(ask_routes, "_save_cost", lambda *a, **k: None)
    monkeypatch.setattr(memory, "after_session", lambda cfg, path: False)
    runs = tmp_path / "runs"
    runs.mkdir()
    pipe = server.Pipeline(runs_dir=runs)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(server.create_app(pipe, cfg, remember_on_start=False), host="127.0.0.1",
                                        port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    deadline = time.monotonic() + 10
    while not srv.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", runs, cfg
    srv.should_exit = True
    for t in [t for t in threading.enumerate() if t.name == "checkin-memory"]:
        t.join(5)  # before the patches are undone


class Browser:
    """One page in headless Chromium, with what the page posted and what its player loaded."""

    def __init__(self, base, path="/"):
        self.pw = sync_api.sync_playwright().start()
        self.browser = self.pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required",
                                                     "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
        self.page = self.browser.new_context().new_page()
        self.posts = []  # (path, body) of every POST, in the order the page sent them
        self.page.on("request", lambda r: r.method == "POST" and self.posts.append(
            (r.url.split("://", 1)[1].split("/", 1)[1], json.loads(r.post_data or "{}"))))
        self.page.route(base + "/stub-el.js", lambda r: r.fulfill(status=200, content_type="text/javascript", body=STUB))
        self.page.add_init_script("""
          window.__a = { plays: [], loads: 0 };
          document.addEventListener("play", (e) => { if (e.target.id === "audio") window.__a.plays.push(new URL(e.target.src).pathname); }, true);
          document.addEventListener("loadstart", (e) => { if (e.target.id === "audio") window.__a.loads++; }, true);
        """)
        self.page.goto(base + path)
        self.page.wait_for_function("window.Fast && window.Ask && window.ElevenLabsClient")

    def close(self):
        self.browser.close()
        self.pw.stop()

    def wait(self, js, timeout=8000):
        return self.page.wait_for_function(js, timeout=timeout)

    def js(self, code):
        return self.page.evaluate(code)

    def state(self):
        return self.js("Fast.debug.state()")

    def plays(self):
        return self.js("window.__a.plays")

    def markers(self):
        return self.js("[...document.querySelectorAll('#sections button')].map((b) => "
                       "[b.dataset.state, b.querySelector('.title').textContent, b.querySelector('.state').textContent, "
                       "b.getAttribute('aria-label')])")

    def starts(self):
        return [body for path, body in self.posts if path == "api/ask/start"]


@pytest.fixture
def world(site):
    """A fast run with its first audio ready, and a way to open the page on it."""
    base, runs, cfg = site
    opened = []

    class W:
        pass
    w = W()
    w.base, w.runs, w.cfg = base, runs, cfg
    w.run = FastRun(runs)

    def open_page(path="/", fast=True):
        b = Browser(base, path)
        opened.append(b)
        if fast:
            b.wait("Fast.active()")
        return b
    w.open = open_page
    yield w
    for b in opened:
        b.close()


def piece(n, i):
    return f"/runs/fast-run/10_audio/sec{n:02d}/p{i:02d}.mp3"


def waiting_for(b, n, piece_index=0):
    b.wait(f"(() => {{ const s = Fast.debug.state(); return s.mode === 'waiting' && s.n === {n} && s.i === {piece_index}; }})()")


def check_in(w, b, section=2):
    """Play part 1 to its end, wait for part `section`, and have the run call the check-in."""
    b.page.click("#orb")
    waiting_for(b, section)
    w.run.reported(state="waiting", waiting_for=section)
    w.run.status(waiting_for=section, check_in="check_in", eta_seconds=60)
    b.wait("window.__el.sessions.length === 1 && Ask.state() && Ask.state().phase === 'live'")


# --- the markers ---------------------------------------------------------------------------------

def test_markers_show_the_four_states_and_follow_a_new_title(world):
    """A page opened mid-chapter: the markers say what the run's status says, and the first tap starts
    where the listener was, not at the top and not with an error."""
    r = FastRun(world.runs, sections=4)
    world.run = r
    r.add(1).add(2, 3.0).add(3)
    r.complete.update({1, 2})
    r.states = {1: "played", 2: "playing"}
    r.status()
    b = world.open()
    b.wait("document.querySelectorAll('#sections button').length === 4")
    assert b.markers() == [[s, TITLES[n], WORDS[s], f"{TITLES[n]}: {WORDS[s]}"]
                           for n, s in ((1, "played"), (2, "playing"), (3, "ready"), (4, "being_written"))]
    assert b.js("getComputedStyle(document.getElementById('sections')).display") != "none"
    r.titles[4] = "Why the recipe was lost"  # its lock gave it a new title
    r.status()
    b.wait("document.querySelectorAll('#sections button')[3].querySelector('.title').textContent === 'Why the recipe was lost'")
    assert b.markers()[3][3] == "Why the recipe was lost: being written"
    assert b.js("document.body.classList.contains('ready')") and b.page.inner_text("#line") == "Concrete That Heals"
    b.page.click("#orb")
    b.wait("window.__a.plays.length === 1 && !document.getElementById('audio').paused")
    assert b.plays() == [piece(2, 0)]
    assert [m[0] for m in b.markers()] == ["played", "playing", "ready", "being_written"]


# --- playing -------------------------------------------------------------------------------------

def test_pieces_play_in_order_and_a_new_piece_is_picked_up(world):
    r = world.run
    r.add(1, 2.0).add(1, 1.0).status()
    b = world.open()
    assert b.js("document.getElementById('audio').paused") and b.state()["mode"] == "idle"  # nothing plays by itself
    b.page.click("#orb")
    b.wait("!document.getElementById('audio').paused")
    got = r.reported(state="playing", section=1, piece=0)
    assert got["waiting_for"] is None and got["since"] is None
    # A status that arrives while a piece plays (a later part's title changed) restarts nothing.
    r.titles[3] = "What the sea adds"
    r.status()
    b.wait("document.querySelectorAll('#sections button')[2].querySelector('.title').textContent === 'What the sea adds'")
    assert b.js("window.__a.loads") == 1 and b.plays() == [piece(1, 0)]
    b.wait("window.__a.plays.length === 2")
    assert b.plays() == [piece(1, 0), piece(1, 1)]
    # The end of what is narrated, inside part 1: the same wait on the same part, at its next piece.
    waiting_for(b, 1, 2)
    got = r.reported(state="waiting", section=1, piece=2, waiting_for=1, reason="end_of_audio")
    assert abs(got["since"] - time.time()) < 5 and 2.5 < got["chapter_t"] < 3.6 and 2.5 < got["t"] < 3.6
    report, age = fast_run.page_report(got, 100.0, time.time())  # as the run reads it
    assert age < 5 and report["state"] == "waiting" and report["waiting_for"] == 1 and 95 < report["since"] <= 100
    assert b.js("document.body.classList.contains('making')") and b.js("document.getElementById('audio').paused")
    r.add(1, 1.0).add(2, 1.0)
    r.complete.update({1, 2})
    r.status()
    b.wait("window.__a.plays.length === 4")  # the new piece after a beat, then part 2 after the pause between parts
    assert b.plays() == [piece(1, 0), piece(1, 1), piece(1, 2), piece(2, 0)]
    assert b.js("window.__a.loads") == 4  # each piece loaded once, at its own address
    waiting_for(b, 3)
    r.reported(state="waiting", section=3, piece=0, waiting_for=3)
    assert [m[0] for m in b.markers()] == ["played", "played", "being_written"]


def test_the_run_finishing_mid_listen_keeps_the_pieces_playing(world):
    r = world.run = FastRun(world.runs, sections=2)
    r.add(1, 2.0).add(2, 1.0)
    r.complete.update({1, 2})
    r.status()
    b = world.open()
    b.page.click("#orb")
    b.wait("!document.getElementById('audio').paused")
    r.status(state="ready", audio="10_audio/chapter.mp3", label="Ready")
    b.wait("status.state === 'ready'")
    assert b.js("Fast.active()") and b.js("new URL(document.getElementById('audio').src).pathname") == piece(1, 0)
    assert b.js("window.__a.loads") == 1 and not b.js("document.getElementById('audio').paused")
    b.wait("window.__a.plays.length === 2")
    assert b.plays() == [piece(1, 0), piece(2, 0)]
    b.wait("!Fast.active()")  # heard to the end: from here it is an ordinary ready chapter
    assert r.reported(state="done")["section"] == 2
    b.wait("new URL(document.getElementById('audio').src).pathname === '/api/chapters/fast-run/audio'")
    assert b.js("document.body.classList.contains('ready')") and b.js("document.getElementById('audio').paused")
    assert b.js("getComputedStyle(document.getElementById('sections')).display") == "none"
    assert "Ready:" not in b.page.inner_text("#transcript") and "Ready:" not in b.js("document.getElementById('transcript').textContent")


# --- check 6: skipping goes to the next marker only ----------------------------------------------------

def test_tapping_a_marker_being_written_goes_to_the_next_marker_only(world):
    r = world.run = FastRun(world.runs, sections=4)
    r.add(1, 6.0).add(2, 6.0).status()
    b = world.open()
    b.page.click("#orb")
    b.wait("!document.getElementById('audio').paused")
    b.page.click("#sections li:nth-child(4) button")  # two ahead of what is ready, three ahead of the listener
    b.wait("window.__a.plays.length === 2")
    assert b.plays() == [piece(1, 0), piece(2, 0)] and b.state()["n"] == 2  # the next marker, which is ready: it plays
    r.reported(state="playing", section=2, piece=0)
    b.page.click("#sections li:nth-child(4) button")
    waiting_for(b, 3)  # the next marker again, which is not ready: the wait is for it
    got = r.reported(state="waiting", section=3, waiting_for=3, reason="skip")
    assert got["piece"] == 0 and b.js("document.getElementById('audio').paused")
    b.page.click("#sections li:nth-child(4) button")
    b.page.wait_for_timeout(300)
    assert b.state()["n"] == 3 and b.state()["mode"] == "waiting"  # already at the next marker: nowhere further
    assert [m[0] for m in b.markers()] == ["played", "played", "being_written", "being_written"]
    b.page.click("#sections li:nth-child(1) button")  # a played marker: back to its start
    b.wait("window.__a.plays.length === 3 && !document.getElementById('audio').paused")
    assert b.plays()[-1] == piece(1, 0) and b.js("document.getElementById('audio').currentTime") < 1.5
    r.reported(state="playing", section=1, piece=0, waiting_for=None)


# --- check 5: the wait, and the check-in ---------------------------------------------------------------

def test_a_short_wait_is_just_a_pause(world):
    world.cfg["fast"]["page"]["decision_wait_seconds"] = 1
    r = world.run
    r.add(1, 1.0).status()
    b = world.open()
    b.page.click("#orb")
    waiting_for(b, 1, 1)
    r.status(waiting_for=1, check_in="pause", eta_seconds=8)
    b.wait("status.check_in === 'pause'")
    b.page.wait_for_timeout(1600)  # past the time after which an unanswered wait would bring the guide in
    assert b.js("window.__el.sessions.length") == 0 and b.starts() == [] and b.js("Ask.state()") is None
    assert b.js("document.body.classList.contains('making')") and b.page.inner_text("#line") == "Writing part 2 of 3"
    r.add(1, 1.0).status(waiting_for=None, check_in=None)
    b.wait("window.__a.plays.length === 2")  # it arrives: a beat, and the chapter goes on
    assert b.plays() == [piece(1, 0), piece(1, 1)] and b.starts() == []


def test_a_wait_the_run_says_nothing_about_becomes_a_check_in(world):
    world.cfg["fast"]["page"]["decision_wait_seconds"] = 0.6
    r = world.run
    r.add(1, 1.0)
    r.complete.add(1)
    r.status()
    b = world.open()
    b.page.click("#orb")
    waiting_for(b, 2)
    b.wait("window.__el.sessions.length === 1")
    assert b.starts()[0]["kind"] == "check_in" and b.starts()[0]["section"] == 2


def test_a_longer_wait_opens_the_check_in_with_the_guide_speaking_first(world):
    r = world.run
    r.add(1, 1.0)
    r.complete.add(1)
    r.status()
    b = world.open()
    check_in(world, b)
    start = b.starts()
    assert len(start) == 1 and start[0]["run"] == "fast-run" and start[0]["kind"] == "check_in"
    assert start[0]["section"] == 2 and start[0]["reopened"] is False and 0.5 < start[0]["t"] < 1.6
    # The position went first: the server reads it to know what the listener has heard.
    k = next(i for i, (path, _) in enumerate(b.posts) if path == "api/ask/start")
    before = [body for path, body in b.posts[:k] if path == "api/fast/position"][-1]
    assert before["state"] == "waiting" and before["waiting_for"] == 2
    s = b.js("(() => { const c = window.__el.sessions[0]; return { sent: c.sent, tools: Object.keys(c.opts.clientTools).sort(),"
             " heard: c.opts.dynamicVariables.heard, muted: c.muted }; })()")
    assert len(s["sent"]) == 1 and s["sent"][0].startswith("[The listener has reached a part")  # the guide speaks first
    assert s["tools"] == ["add_note", "get_status", "resume_chapter"]
    assert "Roman harbours still stand in the sea." in s["heard"] and s["muted"] == []  # the mic is open
    assert b.js("document.body.classList.contains('aside')") and b.js("document.getElementById('audio').paused")
    assert b.page.inner_text("#line") == ""
    b.page.click("#orb")  # a tap ends it; the part is still not ready, so the wait goes on
    b.wait("Ask.state() === null && document.getElementById('line').textContent === 'Talk while you wait'")
    log = A.load_asides(r.root)
    assert [(x["kind"], x["section"], x["ended"], x["restart"]) for x in log] == [("check_in", 2, "button", None)]


def test_silence_closes_the_check_in_and_a_tap_reopens_it(world):
    r = world.run
    r.add(1, 1.0)
    r.complete.add(1)
    r.status()
    b = world.open()
    check_in(world, b)
    b.js("window.__el.inVol = 0.5; window.__el.sessions[0].say('The next part is still coming together. What do you make of it?')")
    b.page.wait_for_timeout(1500)  # past the silence time, but the listener is talking: nothing closes over them
    assert b.js("window.__el.sessions[0].sent.length") == 1 and b.js("Ask.state().phase") == "live"
    b.js("window.__el.inVol = 0")
    b.wait("window.__el.sessions[0].sent.length === 2", timeout=4000)
    assert b.js("window.__el.sessions[0].sent[1]").startswith("[Silence")
    assert b.js("Ask.state().phase") == "live"  # the guide's word or two first
    b.js("window.__el.sessions[0].say('No rush. I am here.')")
    b.wait("Ask.state() === null && document.getElementById('line').textContent === 'Talk while you wait'")
    assert b.js("window.__el.sessions[0].ended")  # the session, and with it the mic, is closed
    assert b.js("document.body.classList.contains('making')") and not b.js("document.body.classList.contains('aside')")
    assert [(x["kind"], x["ended"]) for x in A.load_asides(r.root)] == [("check_in", "silence")]
    r.status(label="Recording part 2 of 3")  # a new status that still says check_in: not opened again by itself
    b.wait("status.label === 'Recording part 2 of 3'")
    b.page.wait_for_timeout(500)
    assert b.js("window.__el.sessions.length") == 1 and b.state()["mode"] == "waiting"
    assert b.page.inner_text("#line") == "Talk while you wait"
    b.page.click("#orb")
    b.wait("window.__el.sessions.length === 2 && Ask.state() && Ask.state().phase === 'live'")
    assert b.starts()[1]["reopened"] is True and b.starts()[1]["section"] == 2
    assert b.js("window.__el.sessions[1].sent[0]").startswith("[The listener tapped to talk")


def test_the_ready_note_waits_for_quiet_and_resume_plays_the_awaited_part(world):
    r = world.run
    r.add(1, 1.0)
    r.complete.add(1)
    r.status()
    b = world.open()
    check_in(world, b)
    b.js("""(() => { const c = window.__el.sessions[0]; c.say('What do you make of it so far?');
        c.hear('I liked the harbour.'); c.say('It is quite a picture.'); })()""")
    early = json.loads(b.js("window.__el.sessions[0].tool('resume_chapter', {reason: 'ready'})"))
    assert early["resuming"] is False  # not ready: refused, and nothing changes
    b.js("window.__el.inVol = 0.5")  # the listener starts talking
    b.page.wait_for_timeout(700)
    assert b.js("Ask.state().phase") == "live" and b.js("window.__el.sessions[0].ctx.length") == 0
    assert b.js("window.__el.sessions[0].sent.length") == 1  # no ready note before the part is playable
    r.add(2, 2.0).status(waiting_for=None, check_in=None)
    b.wait("window.__el.sessions[0].ctx.length === 1")  # told at once, without making it speak
    assert b.js("window.__el.sessions[0].ctx[0]").startswith("[The next part is ready")
    b.page.wait_for_timeout(1000)  # well past the quiet time and the beat: but the listener is speaking
    assert b.js("window.__el.sessions[0].sent.length") == 1
    assert b.js("document.getElementById('audio').paused") and b.plays() == [piece(1, 0)]  # the part waits for the conversation
    b.js("window.__el.inVol = 0")
    b.wait("window.__el.sessions[0].sent.length === 2", timeout=4000)
    assert b.js("window.__el.sessions[0].sent[1]").startswith("[The next part is ready")
    # The guide answers something first and does not go back: the note comes again once they are quiet.
    b.js("window.__el.sessions[0].say('One thing first: yes, it is Roman.')")
    b.wait("window.__el.sessions[0].sent.length === 3", timeout=4000)
    assert b.js("window.__el.sessions[0].sent[2]").startswith("[The next part is ready")
    b.js("""(async () => { const c = window.__el.sessions[0];
        window.__out = await c.tool('resume_chapter', {reason: 'ready'});
        c.mode('speaking'); c.opts.onMessage({ role: 'agent', message: 'You liked the harbour. Here is what holds it up.' }); })()""")
    b.wait("window.__out !== undefined")
    assert json.loads(b.js("window.__out"))["resuming"] is True
    b.page.wait_for_timeout(600)
    assert b.js("Ask.state() !== null") and b.js("document.getElementById('audio').paused")  # still saying its lead-in
    b.js("window.__el.sessions[0].mode('listening')")
    b.wait("Ask.state() === null && !document.getElementById('audio').paused")
    assert b.js("window.__el.sessions[0].ended") and b.plays() == [piece(1, 0), piece(2, 0)]
    assert b.js("document.getElementById('audio').currentTime") < 1.5 and b.state()["mode"] == "playing"
    log = A.load_asides(r.root)
    assert [(x["kind"], x["section"], x["ended"]) for x in log] == [("check_in", 2, "resumed")]
    r.reported(state="playing", section=2, piece=0, waiting_for=None)


def test_a_part_that_arrives_while_the_check_in_connects_just_plays(world):
    r = world.run
    r.add(1, 1.0)
    r.complete.add(1)
    r.status()
    b = world.open()
    b.js("window.__el.delay = 2500")
    b.page.click("#orb")
    waiting_for(b, 2)
    r.status(waiting_for=2, check_in="check_in")
    b.wait("Ask.state() && Ask.state().phase === 'connecting'")
    r.add(2, 2.0).status(waiting_for=None, check_in=None)
    b.wait("window.__a.plays.length === 2")
    assert b.plays() == [piece(1, 0), piece(2, 0)] and b.js("Ask.state()") is None
    b.wait("window.__el.sessions.length === 1 && window.__el.sessions[0].ended", timeout=6000)  # the late connection is closed
    assert b.js("window.__el.sessions[0].sent") == [] and not b.js("document.getElementById('audio').paused")


def test_gap_mic_tap_keeps_the_mic_closed_until_the_tap(world):
    world.cfg["fast"]["gap_mic"] = "tap"
    r = world.run
    r.add(1, 1.0)
    r.complete.add(1)
    r.status()
    b = world.open()
    check_in(world, b)
    s = b.js("(() => { const c = window.__el.sessions[0]; return { sent: c.sent, muted: c.muted }; })()")
    assert s["muted"] == [True] and len(s["sent"]) == 1
    assert s["sent"][0].startswith("[The listener has reached a part") and "[The microphone is closed" in s["sent"][0]
    b.js("window.__el.sessions[0].say('What do you make of it so far?')")
    b.wait("document.getElementById('line').textContent === 'Tap to answer'")
    b.page.wait_for_timeout(600)
    assert b.js("window.__el.sessions[0].muted") == [True] and b.js("Ask.state().phase") == "live"  # closed until the tap
    b.page.click("#orb")
    b.wait("window.__el.sessions[0].muted.length === 2")
    assert b.js("window.__el.sessions[0].muted") == [True, False]
    assert b.js("Ask.state().phase") == "live" and not b.js("window.__el.sessions[0].ended")  # the tap opened the mic, no more
    assert b.page.inner_text("#line") == ""


def test_gap_mic_tap_with_no_tap_closes_quietly(world):
    world.cfg["fast"]["gap_mic"] = "tap"
    r = world.run
    r.add(1, 1.0)
    r.complete.add(1)
    r.status()
    b = world.open()
    check_in(world, b)
    b.js("window.__el.sessions[0].say('What do you make of it so far?')")
    b.wait("Ask.state() === null && document.getElementById('line').textContent === 'Talk while you wait'", timeout=6000)
    s = b.js("(() => { const c = window.__el.sessions[0]; return { sent: c.sent.length, muted: c.muted, ended: c.ended }; })()")
    assert s == {"sent": 1, "muted": [True], "ended": True}  # never opened, and nothing more was asked of the guide
    assert [x["ended"] for x in A.load_asides(r.root)] == ["silence"]


# --- first audio, with the onboarding guide still talking -------------------------------------------

def test_first_audio_tells_the_live_guide_and_the_tap_ends_the_conversation_and_plays(world):
    r = world.run
    r.status()  # the outline is there, nothing narrated yet
    b = world.open()
    assert [m[0] for m in b.markers()] == ["being_written"] * 3 and b.js("document.body.classList.contains('making')")
    b.page.click("#orb")  # nothing to play yet: the tap is the page's own, a conversation with the guide
    b.wait("window.__el.sessions.length === 1 && voiceConv !== null")
    assert "scout_topic" in b.js("Object.keys(window.__el.sessions[0].opts.clientTools)")
    b.js("window.__el.sessions[0].mode('listening')")
    assert b.starts() == [] and b.js("window.__el.sessions[0].sent.length") == 1  # the opening page note
    r.add(1, 2.0).status()
    b.wait("window.__el.sessions[0].sent.length === 2")
    assert b.js("window.__el.sessions[0].sent[1]").startswith('[The chapter is ready: "Concrete That Heals"')
    assert b.js("document.body.classList.contains('ready')") and b.page.inner_text("#line") == "Concrete That Heals"
    assert "Ready to play: Concrete That Heals" in b.js("document.getElementById('transcript').textContent")
    b.page.wait_for_timeout(400)
    assert b.js("document.getElementById('audio').paused") and b.plays() == []  # never over the conversation
    b.page.click("#orb")
    b.wait("window.__a.plays.length === 1 && !document.getElementById('audio').paused")
    assert b.js("window.__el.sessions[0].ended") and b.js("voiceConv === null")  # the conversation ended first
    assert b.plays() == [piece(1, 0)]
    r.reported(state="playing", section=1, piece=0)


def test_an_awaited_part_never_starts_over_a_conversation_begun_meanwhile(world):
    """The run stops while the listener waits, they tap and talk with the guide, the run is continued and the
    part arrives: the chapter and the guide are never heard at once. The place is kept for the next tap."""
    r = world.run
    r.add(1, 1.0)
    r.complete.add(1)
    r.status()
    b = world.open()
    b.page.click("#orb")
    waiting_for(b, 2)
    r.status(state="problem", message="The chapter stopped unexpectedly.", retryable=True)
    b.wait("document.body.classList.contains('problem')")
    assert b.js("Fast.active()") is False
    b.page.click("#orb")  # the page's own tap: a conversation with the guide
    b.wait("window.__el.sessions.length === 1 && voiceConv !== null")
    r.add(2, 2.0).status(state="making", message=None)
    b.wait("Fast.debug.state().mode === 'paused' && Fast.debug.state().n === 2")
    b.page.wait_for_timeout(500)
    assert b.js("document.getElementById('audio').paused") and b.plays() == [piece(1, 0)]
    assert not b.js("window.__el.sessions[0].ended") and b.starts() == []
    b.page.click("#orb")
    b.wait("window.__a.plays.length === 2 && !document.getElementById('audio').paused")
    assert b.js("window.__el.sessions[0].ended") and b.plays() == [piece(1, 0), piece(2, 0)]


# --- asking mid-chapter while the run is still being made ---------------------------------------------

def test_asking_mid_chapter_on_a_fast_run_uses_chapter_time_and_comes_back_through_fast(world):
    r = world.run
    AF.build(r.root)  # the rolling timing and script of what is narrated so far (sentences of 5.5 s)
    r.add(1, 2.0).add(1, 8.0).status()
    b = world.open()
    b.wait("status.asks === true")
    b.page.click("#orb")
    b.wait("Fast.debug.state().i === 1 && !document.getElementById('audio').paused && document.getElementById('audio').currentTime > 0.2")
    b.js("document.getElementById('audio').currentTime = 4.6")  # 6.6 s into the chapter: its second sentence
    b.wait("document.getElementById('audio').currentTime >= 4.6 && document.getElementById('line').textContent === 'Ask a question'")
    b.page.click("#orb")
    b.wait("Ask.state() && Ask.state().phase === 'live'")
    start = b.starts()[0]
    assert "kind" not in start and 6.5 < start["t"] < 7.6  # the chapter's time, not the piece's
    assert b.js("document.getElementById('audio').paused")
    r.reported(state="paused", section=1, piece=1)  # an aside has the floor
    b.page.click("#orb")  # back
    b.wait("Ask.debug.asides.length === 1 && !document.getElementById('audio').paused")
    restart = b.js("Ask.debug.asides[0].restart")
    assert restart["sentence_id"] == "seg01-s002" and restart["t"] == 5.5
    assert b.js("new URL(document.getElementById('audio').src).pathname") == piece(1, 1)
    assert 3.4 <= b.js("document.getElementById('audio').currentTime") < 4.6  # 5.5 s of chapter is 3.5 s into the piece
    assert b.js("window.__a.loads") == 2  # the piece was not loaded again
    log = A.load_asides(r.root)
    assert [(x["kind"], x["ended"]) for x in log] == [("ask", "button")]


# --- what Fast leaves alone ------------------------------------------------------------------------

def test_an_overnight_run_being_made_is_untouched(world):
    write(world.runs / "night" / "status.json", {
        "run": "night", "state": "making", "pid": os.getpid(), "label": "Drafting", "stages_done": 5, "stages_total": 14,
        "minutes_left": None, "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    b = world.open(fast=False)
    b.wait("document.body.classList.contains('making')")
    assert b.js("Fast.active()") is False and b.js("document.body.className") == "making"
    assert b.page.inner_text("#line") == "Drafting"
    assert b.js("getComputedStyle(document.getElementById('sections')).display") == "none"
    assert b.js("Fast.onTap()") is False and not (world.runs / "night" / "fast").exists()
    write(world.runs / "night" / "status.json", {"run": "night", "state": "ready", "title": "A Night Chapter",
                                                 "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    b.wait("document.body.classList.contains('ready')")  # the page's own ready, as before
    assert b.js("new URL(document.getElementById('audio').src).pathname") == "/api/chapters/night/audio"
    assert "Ready: A Night Chapter" in b.js("document.getElementById('transcript').textContent")


def test_a_finished_chapter_is_an_ordinary_chapter(world):
    AF.build(world.runs / "synthetic", audio=True)
    write(world.runs / "synthetic" / "fast.json", {"mode": "fast", "seed_set": 0})  # even one that was made fast
    b = world.open("/?chapter=synthetic", fast=False)
    b.wait("document.body.classList.contains('ready')")
    assert b.js("Fast.active()") is False
    assert b.js("new URL(document.getElementById('audio').src).pathname") == "/api/chapters/synthetic/audio"
    b.page.click("#orb")
    b.wait("!document.getElementById('audio').paused && document.getElementById('line').textContent === 'Ask a question'")
    assert b.plays() == ["/api/chapters/synthetic/audio"]
    assert b.js("getComputedStyle(document.getElementById('sections')).display") == "none"
    assert not (world.runs / "synthetic" / "fast" / "player.json").exists()  # no position reports
