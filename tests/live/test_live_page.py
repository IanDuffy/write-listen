"""The live page (page/live.html, page/live.js, page/align.js) in headless Chromium: the page's side of
the one GPT-Live session, against the spec's automated checks as built (voice/gpt_live/REPORT.md).

The real server runs on a free port over a scratch runs folder with one live fast run (fast.json
"voice": "live", the outline, locked sections, status.json as the run writes it), so /api/live/* and
/api/fast/position run for real. GPT-Live is a stub: RTCPeerConnection is replaced by an init script
whose data channel records every client event the page sends (window.__sent) and lets the test play
GPT-Live's side (window.__emit). The WebRTC call to OpenAI is patched out (create_webrtc), the memory
pass is off, and no model is reached. The page's timings are shortened in the test config.
"""

import json
import os
import re
import socket
import threading
import time
import urllib.request

import pytest

import server
from fakes import FakeAnthropic, Responder
from stages import llm, memory
from stages.common import ROOT
from voice.gpt_live import session as S

sync_api = pytest.importorskip("playwright.sync_api")

RUN = "live-run"
TEXTS = {
    1: ("Picture a woman in a meeting. [D01] Her name is Donella Meadows. [D01, D14]\n\n"
        "The meeting is about trade. It angers her.\n\n[pause]\n\n"
        "So she gets up. She writes a list, and the list has nine items."),
    2: ("Years later engineers still argue about that list. [D02]\n\n"
        "Some call it obvious. Others call it radical."),
}
TITLES = {1: "One", 2: "Two"}

STUB = r"""
(() => {
  window.__sent = [];       // every client event: {ch, ...event}
  window.__channels = [];   // one per session
  window.__autoStart = true;
  window.__offers = 0;
  const emitTo = (ch, ev) => { if (ch.onmessage) ch.onmessage({ data: JSON.stringify(ev) }); };
  window.__emit = (ev) => emitTo(window.__channels[window.__channels.length - 1], ev);
  class FakeChannel {
    constructor(label, k) { this.label = label; this.k = k; this.readyState = "open"; this.onmessage = null; }
    send(s) { window.__sent.push({ ch: this.k, ...JSON.parse(s) }); }
    close() { this.readyState = "closed"; }
  }
  class FakePC {
    constructor() { this.iceGatheringState = "complete"; this.connectionState = "new"; this.localDescription = null;
                    this.tracks = []; this.channel = null; window.__pcs = (window.__pcs || []).concat([this]); }
    addTrack(t, s) { this.tracks.push(t); }
    addTransceiver(kind, init) { this.transceivers = (this.transceivers || []).concat([kind]); }
    createDataChannel(label) {
      this.channel = new FakeChannel(label, window.__channels.length);
      window.__channels.push(this.channel);
      return this.channel;
    }
    async createOffer() { window.__offers++; return { type: "offer", sdp: "OFFER" + window.__offers }; }
    async setLocalDescription(d) { this.localDescription = d; }
    async setRemoteDescription(d) {
      this.remote = d; this.connectionState = "connected";
      const ch = this.channel;
      if (window.__autoStart) setTimeout(() => emitTo(ch, { type: "session.started",
        session: { id: "sess_" + ch.k, expires_at: Date.now() / 1000 + 7200 } }), 30);
    }
    addEventListener() {}
    close() { this.connectionState = "closed"; if (this.channel) this.channel.readyState = "closed"; }
  }
  window.RTCPeerConnection = FakePC;
  const ctx = new AudioContext();
  const dest = ctx.createMediaStreamDestination();
  window.__track = dest.stream.getAudioTracks()[0];
  window.__mic = { ctx, dest };  // a test can play a sound into the mic (wake on sound)
  window.__mics = 0;  // how often the page asked for the mic
  navigator.mediaDevices.getUserMedia = async () => { window.__mics++; const s = new MediaStream([dest.stream.getAudioTracks()[0].clone()]);
    window.__track = s.getAudioTracks()[0]; return s; };  // a fresh track each time, as a real browser gives
})();
"""


def write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
    os.replace(tmp, p)


class World:
    """The live fast run's folder, and the run's side: sections lock, status.json is rewritten."""

    def __init__(self, runs, base):
        self.root, self.base = runs / RUN, base
        self.locked: list[int] = []
        self.more: dict = {}
        write(self.root / "fast.json", {"mode": "fast", "voice": "live", "seed_set": 0})
        write(self.root / "05_outline.json", {"title": "The List", "sections": [
            {"n": n, "title": TITLES[n], "lands": f"Lands {n}."} for n in TEXTS]})
        (self.root / "06_segments").mkdir(parents=True, exist_ok=True)
        # No status.json yet: the run is started by the conversation (start_chapter, see connect()). A
        # status written before the tap is a run already going, which a new conversation takes over.

    def lock(self, *ns, **more):
        for n in ns:
            write(self.root / "06_segments" / f"seg{n:02d}.md", TEXTS[n])
            self.locked.append(n)
        return self.status(**more)

    def status(self, **more):
        self.more.update(more)
        secs = [{"n": n, "title": TITLES[n], "state": "ready" if n in self.locked else "being_written",
                 "text": f"06_segments/seg{n:02d}.md" if n in self.locked else None, "pieces": [], "ready_in": None}
                for n in TEXTS]
        write(self.root / "status.json", {
            "run": RUN, "state": "making", "stage": "live", "label": "Writing your chapter",
            "stages_done": len(self.locked), "stages_total": len(TEXTS), "minutes_left": None, "title": "The List",
            "updated": time.strftime("%Y-%m-%dT%H:%M:%S") + f".{time.time_ns() % 10**9}", "pid": os.getpid(),
            "mode": "fast", "voice": "live", "sections": secs, "first_audio": bool(self.locked),
            "waiting_for": None, "eta_seconds": None, "check_in": None, **self.more})
        return self

    def player(self):
        try:
            return json.loads((self.root / "fast" / "player.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def reported(self, timeout=6, **want):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            got = self.player()
            if got and all(got.get(k) == v for k, v in want.items()):
                return got
            time.sleep(0.05)
        raise AssertionError(f"the page never reported {want}; the last report was {self.player()}")

    def paragraphs(self) -> dict:
        with urllib.request.urlopen(f"{self.base}/api/live/chapter/{RUN}") as res:
            ch = json.loads(res.read())
        return {p["id"]: p for s in ch["sections"] for p in s["paragraphs"]}

    def lines(self, name):
        p = self.root / name
        return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []

    def logged(self, name, timeout=5, where=lambda rec: True):
        """The records of a log the page writes through the server, once one matching `where` is there."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(where(r) for r in self.lines(name)):
                return self.lines(name)
            time.sleep(0.05)
        raise AssertionError(f"nothing matching in {name}: {self.lines(name)}")


@pytest.fixture
def site(cfg, tmp_path, monkeypatch):
    import uvicorn

    cfg["listener_dir"] = str(tmp_path / "listener")
    cfg["live"]["provider"] = "gpt_live"
    cfg["live"]["page"].update(line_quiet_ms=300, resend_ms=800, lead_in_max_ms=1500, handback_quiet_ms=600,
                               stop_quiet_ms=150, silence_after_answer_secs=1, aside_give_up_secs=1,
                               checkin_silence_secs=1, ready_beat_ms=100, position_seconds=0.3,
                               greet_fallback_ms=1500, answer_wait_ms=2500, start_nudge_ms=1500,
                               # the clock-driven cues are off unless a test turns them on (Live.S)
                               hello_silence_secs=1000, pitch_nudge_secs=[1000, 1000], ready_remind_secs=1000,
                               pitch_hold={"explained_secs": 0, "question_secs": 0, "max_secs": 0}, offer_nudge=None,
                               aside_quiet_secs=1000, end_hold_secs=1000, talk_rest_secs=1000, wake_grace_secs=0,
                               # every listener counts as engaged (the guide keeps them company after the start)
                               # unless a test says otherwise
                               engaged_turns=0, engaged_words=0)
    # The director (live.director) is off here, as the clocks are: the tests that are about it turn it on (Live.S).
    cfg["live"]["director"] = {**(cfg["live"].get("director") or {}), "enabled": False}
    monkeypatch.setattr(llm, "_client", FakeAnthropic(Responder()))
    monkeypatch.setattr(memory, "after_session", lambda *a, **k: False)
    offers = []

    def webrtc(cfg, session, sdp, client=None):
        offers.append((session, sdp))
        return {"id": f"live_{len(offers)}", "sdp": "ANSWER"}
    monkeypatch.setattr(S, "create_webrtc", webrtc)
    started = []

    def start_seed(self, topic, why="", notes="", said="", mode="overnight", live=False):  # the run is the World's
        started.append({"topic": topic, "mode": mode, "live": live})
        return RUN
    monkeypatch.setattr(server.Pipeline, "start_seed", start_seed)
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
    base = f"http://127.0.0.1:{port}"
    w = World(runs, base)
    w.cfg = cfg  # the server's own: a test can turn live.director.view on (director.view_on reads it per request)
    yield base, w, offers
    srv.should_exit = True


class Page:
    """One page in headless Chromium; GPT-Live's side is played through window.__emit."""

    def __init__(self, base):
        self.pw = sync_api.sync_playwright().start()
        self.browser = self.pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        self.page = self.browser.new_context().new_page()
        self.posts = []
        self.page.on("request", lambda r: r.method == "POST" and self.posts.append(
            (r.url.split("://", 1)[1].split("/", 1)[1], json.loads(r.post_data or "{}"))))
        self.page.add_init_script(STUB)
        self.page.goto(base + "/")
        self.page.wait_for_function("window.Live && window.Align")
        self.ms = 0  # session-relative time for the transcript's start_ms/end_ms

    def close(self):
        self.browser.close()
        self.pw.stop()

    def js(self, code, arg=None):
        return self.page.evaluate(code, arg)

    def L(self, expr):
        return self.js(f"(() => {{ const L = window.Live.L; return {expr}; }})()")

    def wait(self, cond, timeout=8000):
        self.page.wait_for_function(f"(() => {{ const L = window.Live.L; return !!({cond}); }})()", timeout=timeout)

    def phase(self):
        return self.js("window.Live.state()")

    def emit(self, ev):
        self.js("(e) => window.__emit(e)", ev)

    def say(self, text):
        """The guide's words, as output transcript deltas: one word a delta, each starting with a space."""
        for w in text.split():
            self.emit({"type": "session.output_transcript.delta", "delta": " " + w,
                       "start_ms": self.ms, "end_ms": self.ms + 200})
            self.ms += 200

    def hear(self, text):
        self.emit({"type": "session.input_transcript.delta", "delta": " " + text})

    def tool(self, name, args=None, call_id=None):
        self.emit({"type": "response.event", "event": {"type": "response.output_item.done", "item": {
            "type": "function_call", "call_id": call_id or f"call_{name}_{time.time_ns()}", "name": name,
            "arguments": json.dumps(args or {})}}})

    def sent(self, type_=None):
        out = self.js("window.__sent")
        return [e for e in out if type_ is None or e["type"] == type_]

    def reads(self, notes):
        """The paragraphs sent to be read, in order: the text after notes.read."""
        return [e["content"][len(notes["read"]) + 1:] for e in self.sent("session.commentary.append")
                if e["content"].startswith(notes["read"])]

    def marks(self, kind=None):
        return [m for m in self.L("L.log") if kind is None or m[1] == kind]

    def post_bodies(self, path):
        return [b for p, b in self.posts if p == path]


@pytest.fixture
def world(site):
    base, w, offers = site
    pages = []

    def open_page():
        p = Page(base)
        pages.append(p)
        return p
    w.open, w.offers, w.notes = open_page, offers, S.notes()
    yield w
    for p in pages:
        p.close()


def connect(w, greet=True, start=True):
    """The page open, the orb tapped, the session started; the guide says hello, and (`start`) starts
    the chapter: start_chapter through the server, whose run (the World's) the page may then read."""
    p = w.open()
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    if greet:
        p.say("Hi there.")
    if start:
        start_chapter(p)
    return p


def start_chapter(p):
    p.tool("start_chapter", {"topic": "The List", "why": "they asked", "notes": ""})
    p.wait(f"L.own === {json.dumps(RUN)}")


def offered(p, w, *lock):
    """The chapter's opening written mid-conversation: the page offers it (first_ready) and nothing is
    read until the listener says yes (Ian's second test, 2026-11-15)."""
    p.wait("Date.now() - L.spokeAt > 900")
    w.lock(*(lock or (1,)))
    p.wait("L.offer")
    return p


def say_yes(p, words="Yes please."):
    """The guide offers it, the listener says yes, the guide leads in: the first paragraph goes."""
    before = p.L("L.offer && L.offer.askedAt") or 0
    p.say("Your chapter's ready. Want to hear it now?")
    p.wait(f"L.offer && L.offer.askedAt > {before}")
    p.hear(words)
    p.wait("L.pending")
    p.say("Okay, here we go.")
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.sentAt")
    return p


def start_reading(w, *lock):
    """Connected, the chapter's opening written and offered, the listener's yes, the guide's lead-in,
    then the first paragraph sent."""
    p = connect(w)
    offered(p, w, *lock)
    return say_yes(p)


def read(p, w, pid, upto=None, fragments=None):
    """The guide reads paragraph `pid` (its first `upto` words); `fragments` replaces the words."""
    words = w.paragraphs()[pid]["text"].split()
    if fragments is not None:
        for f in fragments:
            p.emit({"type": "session.output_transcript.delta", "delta": f, "start_ms": p.ms, "end_ms": p.ms + 200})
            p.ms += 200
        return
    p.say(" ".join(words[:upto]))


def read_through(p, w, pid):
    read(p, w, pid)
    p.wait(f"L.feed.items.find((x) => x.id === '{pid}' && x.done)")


def mic_rules(p, notes):
    """The mic rules over everything sent: a reading commentary only while muted; every unmute is
    straight followed by an instruction; every instruction but `back` with the mic open."""
    muted, ch = False, None
    sent = p.sent()
    for k, e in enumerate(sent):
        t = e["type"]
        if e["ch"] != ch:  # a new session starts with the mic open
            muted, ch = False, e["ch"]
        if t == "session.input_audio.mute":
            muted = True
        elif t == "session.input_audio.unmute":
            muted = False
            assert sent[k + 1]["type"] == "session.instructions.append", f"unmuted for {sent[k + 1]}"
        elif t == "session.commentary.append" and e["content"].startswith(notes["read"]):
            assert muted, f"a paragraph went with the mic open: {e['content'][:60]}"
        elif t == "session.instructions.append" and e["content"] != notes["back"]:
            assert not muted, f"an instruction went while muted: {e['content'][:60]}"


# --- 1. the feed loop --------------------------------------------------------------------------------

def test_feed_loop_sends_one_paragraph_at_a_time_and_waits_for_the_next_section(world):
    """Check 1, the feed loop: paragraphs go in order as commentary headed notes.read; the next is sent
    only once the current one has been heard to its end (queue_words_left 0), never more than one
    outstanding; a new section only once it is locked; with the queue empty and the next section not
    locked the page reports waiting (fast/player.json state waiting, waiting_for)."""
    w = world
    p = start_reading(w)
    paras = w.paragraphs()
    assert p.reads(w.notes) == [paras["s1-p01"]["text"]]
    words = paras["s1-p01"]["text"].split()
    read(p, w, "s1-p01", upto=len(words) - 1)
    p.page.wait_for_timeout(300)
    assert len(p.reads(w.notes)) == 1, "the next paragraph went before the current one was heard to its end"
    p.say(words[-1])  # the last word: completed by the flush once the deltas stop
    p.wait("L.feed.cur && L.feed.cur.id === 's1-p02'")
    assert p.reads(w.notes) == [paras["s1-p01"]["text"], paras["s1-p02"]["text"]]
    read_through(p, w, "s1-p02")
    p.wait("L.feed.cur && L.feed.cur.id === 's1-p03' && L.feed.cur.sentAt")
    assert len(p.reads(w.notes)) == 3
    read_through(p, w, "s1-p03")
    p.wait("L.phase === 'waiting'")
    w.reported(state="waiting", waiting_for=2)
    p.page.wait_for_timeout(500)
    assert len(p.reads(w.notes)) == 3, "a paragraph of a section not yet written was sent"
    w.lock(2)
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.id === 's2-p01'")
    assert p.reads(w.notes)[-1] == w.paragraphs()["s2-p01"]["text"]
    reads = [e for e in p.sent("session.commentary.append") if e["content"] != w.notes["opened"]
             and not e["content"].startswith(w.notes["opening_words"].split("{")[0])]  # but the greeting and the offer
    assert all(e["content"].startswith(w.notes["read"] + " ") for e in reads)
    mic_rules(p, w.notes)


# --- 2. the playing-paragraph tracker -----------------------------------------------------------------

def test_tracker_follows_the_transcript_split_words_sound_tags_and_lead_ins(world):
    """Check 2, the tracker: Live.where() moves through paragraphs and sentences as deltas arrive; a
    word split across deltas ("Don" + "ella") still matches; a sound tag "[laugh]" is ignored; a
    lead-in before the paragraph's words is not counted as reading; position reports reach
    fast/player.json with the right section and piece."""
    w = world
    p = start_reading(w)
    # a lead-in in the reading phase, before the paragraph's own words: not reading
    p.say("Right, so.")
    p.page.wait_for_timeout(700)  # the flush
    assert p.L("L.feed.cur.startedAt") == 0 and p.L("L.feed.cur.heard") == ""
    read(p, w, "s1-p01", fragments=[" Picture", " a", " woman", " [laugh] ", " in", " a"])
    p.wait("L.feed.cur.matched === 5")
    assert p.js("Live.where()")["sentence"] == 0
    read(p, w, "s1-p01", fragments=[" meeting.", " Her", " name", " is", " Don", "ella", " Meadows."])
    p.wait("L.feed.cur && L.feed.cur.id === 's1-p02'")  # the split word matched: the paragraph was heard to its end
    rec = [r for r in w.logged("live/reading.jsonl", where=lambda r: r["id"] == "s1-p01") if r["id"] == "s1-p01"][0]
    assert rec["same"] is True and "laugh" not in rec["heard"] and "donella" in rec["heard"].lower()
    assert "right" not in rec["heard"].lower()
    at = p.js("(() => { const a = Live.where(); return a && {id: a.id, sentence: a.sentence, n: a.n, i: a.i}; })()")
    assert at == {"id": "s1-p02", "sentence": 0, "n": 1, "i": 1}
    w.reported(state="playing", section=1, piece=1)
    read(p, w, "s1-p02", upto=6)  # "The meeting is about trade. It"
    p.wait("L.feed.cur.matched === 6")
    assert p.js("Live.where().sentence") == 1
    # the lead-in went to the transcript as the guide's words, not to the paragraph (with line_quiet_ms
    # shortened below the 500 ms word flush, its last word may close a line of its own)
    p.wait("[...document.querySelectorAll('#transcript p.guide')].map((x) => x.textContent).join(' ').includes('right')")


# --- 3. the mic ---------------------------------------------------------------------------------------

def test_mic_is_muted_for_every_reading_and_opened_only_for_instructions(world):
    """Check 3, the mic: muted (session.input_audio.mute and the track disabled) before every reading
    commentary; unmuted only for a tap, a check-in or a note the guide must act on, and every
    instruction goes with the mic open (unmute first)."""
    w = world
    p = start_reading(w)
    assert p.js("window.__track.enabled") is False and p.L("L.muted") is True
    sent = p.sent()
    first_read = next(k for k, e in enumerate(sent) if e["type"] == "session.commentary.append"
                      and e["content"].startswith(w.notes["read"]))
    assert [e["type"] for e in sent[:first_read] if e["type"].startswith("session.input_audio")][-1] == "session.input_audio.mute"
    read_through(p, w, "s1-p01")
    assert p.js("window.__track.enabled") is False
    # a tap opens it
    p.page.click("#orb")
    p.wait("L.phase === 'aside'")
    assert p.js("window.__track.enabled") is True
    unmutes = [k for k, e in enumerate(p.sent()) if e["type"] == "session.input_audio.unmute"]
    instr = [e["content"] for e in p.sent("session.instructions.append") if not e["content"].startswith("[Their chapter is under way")]
    assert len(unmutes) == 1 and instr == [w.notes["tapped"]]  # the offer is said (speakable), not a note
    assert p.sent()[unmutes[0] + 1]["content"] == w.notes["tapped"]
    p.tool("resume_chapter")
    p.say("Back to it.")
    p.wait("L.phase === 'reading' && L.feed.cur.sentAt > L.asides[0].resume_at")
    assert p.js("window.__track.enabled") is False
    mic_rules(p, w.notes)


# --- 4. a tap during the reading ----------------------------------------------------------------------

def test_a_tap_mid_paragraph_asks_and_the_reading_restarts_at_the_sentence(world):
    """Check 4, a tap during the reading: the guide's audio goes to volume 0 at once; notes.tapped as an
    instruction after unmute; notes.context and where it stopped as silent context (from
    /api/live/context) once the listener starts asking; the interrupted paragraph and sentence recorded (L.aside.at); the cut reading
    posted to /api/live/read with cut true; the backend's resume_chapter answered with a
    function_call_output and response.create; after the guide's lead-in, muted and the paragraph
    re-sent from the restart sentence (/api/live/restart: under 30 s the interrupted sentence); the
    aside logged to 12_asks/asides.jsonl."""
    w = world
    p = start_reading(w)
    read(p, w, "s1-p01", upto=8)  # "Picture a woman in a meeting. Her name"
    p.wait("L.feed.cur.matched === 7")  # "name" waits for the next delta
    vol = p.js("() => { document.getElementById('orb').click(); return document.getElementById('remote').volume; }")
    assert vol == 0
    p.wait("L.phase === 'aside'")
    assert p.L("L.aside.at && [L.aside.at.id, L.aside.at.sentence]") == ["s1-p01", 1]
    p.wait("L.log.some((m) => m[1] === 'ask')")
    sent = p.sent()
    types = [e["type"] for e in sent]
    k = max(i for i, e in enumerate(sent) if e["type"] == "session.instructions.append")
    assert sent[k]["content"] == w.notes["tapped"] and types[k - 1] == "session.input_audio.unmute"
    p.page.wait_for_timeout(600)
    assert not p.sent("session.thinking.append")  # not before they ask: sent early, it was talked about unasked
    deadline = time.monotonic() + 3
    while not [b for b in p.post_bodies("api/live/read") if b["cut"]] and time.monotonic() < deadline:
        time.sleep(0.05)
    cut = [b for b in p.post_bodies("api/live/read") if b["cut"]]
    assert cut and cut[0]["id"] == "s1-p01" and cut[0]["heard"].lower().startswith("picture a woman")
    p.wait("document.getElementById('remote').volume === 1")  # unducked once the reading has stopped
    # the listener asks (the silent context goes now), the guide answers, the backend goes back
    p.hear("Who was she?")
    p.wait("window.__sent.some((e) => e.type === 'session.thinking.append')")
    thinks = p.sent("session.thinking.append")
    assert thinks[0]["content"].startswith(w.notes["context"] + "\n")
    assert 'while you were reading: "Her name is Donella Meadows."' in thinks[0]["content"]
    p.say("A systems thinker.")
    p.page.wait_for_timeout(400)
    p.tool("resume_chapter", {"reason": "listener"}, call_id="call_r1")
    p.wait("window.__sent.some((e) => e.type === 'response.create')")
    out = [e for e in p.sent("response.item.create") if e["item"]["call_id"] == "call_r1"]
    assert out and out[0]["item"]["type"] == "function_call_output"
    assert json.loads(out[0]["item"]["output"]) == {"resuming": True}
    after = p.sent()
    j = next(i for i, e in enumerate(after) if e["type"] == "response.item.create")
    assert after[j + 1]["type"] == "response.create"
    p.say("Where were we? Right.")
    p.wait("L.phase === 'reading'")
    reads = p.reads(w.notes)
    assert reads[-1] == "Her name is Donella Meadows."
    assert p.L("L.asides[0].from") == {"id": "s1-p01", "sentence": 1, "rule": "sentence"}
    sent = p.sent()
    last_read = max(i for i, e in enumerate(sent) if e["type"] == "session.commentary.append")
    assert [e["type"] for e in sent[:last_read] if e["type"].startswith("session.input_audio")][-1] == "session.input_audio.mute"
    log = w.logged("12_asks/asides.jsonl")
    assert len(log) == 1 and log[0]["kind"] == "ask" and log[0]["paragraph"] == "s1-p01" and log[0]["sentence"] == 1
    assert log[0]["ended"] == "guide" and log[0]["restart"]["rule"] == "sentence"
    # the answer (and the lead-in, when it follows within a line's quiet) is in the aside's transcript
    assert any(x["who"] == "guide" and x["text"].startswith("A systems thinker.") for x in log[0]["transcript"])
    # the restarted paragraph is followed from its new start, and the next one comes after it
    read(p, w, "s1-p01", fragments=[" Her", " name", " is", " Donella", " Meadows. "])
    p.wait("L.feed.cur && L.feed.cur.id === 's1-p02'")
    mic_rules(p, w.notes)


# --- 5. context: a new session at a section boundary ----------------------------------------------------

def test_a_full_context_opens_a_new_session_at_the_next_section_boundary(world):
    """Check 5, context: GPT-Live can't delete items, so when session.usage.updated reports
    context_window.usage_ratio above live.page.reopen_ratio a new session takes over at the next section
    boundary (POST /api/live/resume, reason 'section boundary'), not mid-section, and the reading goes
    on in the new session (muted, the next section's first paragraph sent on the new channel)."""
    w = world
    p = start_reading(w, 1, 2)  # both sections written: the reading runs straight across the boundary
    p.emit({"type": "session.usage.updated", "usage": {"seconds": 300},
            "context_window": {"usage_ratio": 0.85}})
    read_through(p, w, "s1-p01")
    read_through(p, w, "s1-p02")
    p.wait("L.feed.cur && L.feed.cur.id === 's1-p03'")
    assert not p.post_bodies("api/live/resume"), "a new session mid-section"
    read_through(p, w, "s1-p03")
    p.wait("L.liveId === 'live_2' && L.phase === 'reading' && L.feed.cur && L.feed.cur.id === 's2-p01' && L.feed.cur.sentAt")
    res = p.post_bodies("api/live/resume")
    assert len(res) == 1 and res[0]["reason"] == "section boundary" and res[0]["sdp"] == "OFFER2"
    sent = p.sent()
    old = [e["type"] for e in sent if e["ch"] == 0]
    new = [e for e in sent if e["ch"] == 1]
    assert "session.close" in old
    assert not any(e["type"] == "session.commentary.append" and "Years later" in e["content"] for e in sent if e["ch"] == 0)
    reads = [e for e in new if e["type"] == "session.commentary.append"]
    assert len(reads) == 1 and reads[0]["content"] == f"{w.notes['read']} {w.paragraphs()['s2-p01']['text']}"
    assert new.index(next(e for e in new if e["type"] == "session.input_audio.mute")) < new.index(reads[0])
    assert not any(e["type"] == "session.instructions.append" for e in new)  # no wake note: it reads straight on
    assert p.js("window.__track.enabled") is False
    # the new session is followed like the first
    read_through(p, w, "s2-p01")
    p.wait("L.feed.cur && L.feed.cur.id === 's2-p02'")


# --- the rest of what was built --------------------------------------------------------------------------

def test_check_in_while_waiting_then_the_next_part_at_the_first_pause(world):
    """The check-in: the queue empty and status check_in 'check_in' -> the mic opens and notes.check_in
    goes as an instruction; the section arriving mid-conversation -> notes.next_ready at the first pause
    (handback_quiet_ms), then the reading resumes after the guide's line; logged as a check-in."""
    w = world
    p = start_reading(w)
    for pid in ("s1-p01", "s1-p02", "s1-p03"):
        p.wait(f"L.feed.cur && L.feed.cur.id === '{pid}'")
        read_through(p, w, pid)
    p.wait("L.phase === 'waiting'")
    w.status(waiting_for=2, check_in="check_in", eta_seconds=180)
    p.wait("L.phase === 'checkin'")
    instr = p.sent("session.instructions.append")[-1]["content"]
    assert instr.startswith("[The reading has reached a part that is still being written, about 3 minutes away.")
    assert p.js("window.__track.enabled") is True
    w.reported(state="paused")
    p.say("While we wait, what stood out?")
    p.page.wait_for_timeout(400)
    p.hear("The list, I think.")
    w.lock(2, check_in=None, waiting_for=None)
    p.wait("L.wait && L.wait.arrived")
    p.say("Same here.")
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['next_ready'])})")
    assert p.phase() == "checkin"
    p.say("And that list is where we pick up.")
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.id === 's2-p01' && L.feed.cur.sentAt")
    log = w.logged("12_asks/asides.jsonl")
    assert log[0]["kind"] == "check_in" and log[0]["paragraph"] == "s2-p01" and log[0]["ended"] == "ready"
    mic_rules(p, w.notes)


def test_an_aside_the_guide_never_ends_goes_back_by_itself(world):
    """An aside where the guide never calls resume_chapter: after its answer and a silence the page
    sends notes.silence, then (still nothing) the reading goes on by itself, marked 'resume-unasked',
    from the interrupted sentence."""
    w = world
    p = start_reading(w)
    read(p, w, "s1-p01", upto=3)
    p.wait("L.feed.cur.matched === 2")
    p.page.click("#orb")
    p.wait("L.phase === 'aside'")
    p.page.wait_for_timeout(300)
    p.say("Yes?")
    p.page.wait_for_timeout(400)
    p.hear("What list was that?")  # silence only counts after an answer to something they said
    p.page.wait_for_timeout(400)
    p.say("A list of nine places to step in.")
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['silence'])})", timeout=8000)
    p.wait("L.phase === 'reading'", timeout=12000)
    assert p.marks("resume-unasked")
    assert p.reads(w.notes)[-1] == w.paragraphs()["s1-p01"]["text"]  # sentence 0: the paragraph again
    assert w.logged("12_asks/asides.jsonl")[0]["ended"] == "unasked"
    mic_rules(p, w.notes)


def test_a_lead_back_without_resume_chapter_goes_back(world):
    """The guide leads back in its own words ("Anyway, where was I... right.") but never calls
    resume_chapter (about half the live asides): the lead-back is the cue, and the reading goes on
    from the interrupted sentence, logged as ended 'lead-back'."""
    w = world
    p = start_reading(w)
    read(p, w, "s1-p01", upto=3)
    p.wait("L.feed.cur.matched === 2")
    p.page.click("#orb")
    p.wait("L.phase === 'aside'")
    p.page.wait_for_timeout(2200)
    p.hear("Who was she?")
    p.page.wait_for_timeout(500)
    p.say("A systems thinker.")
    p.page.wait_for_timeout(600)
    p.hear("Oh, okay.")
    p.page.wait_for_timeout(500)
    p.say("Anyway, where was I... right.")
    p.wait("L.phase === 'reading'", timeout=6000)
    assert p.marks("lead-back") and not p.marks("resume-unasked")
    assert w.logged("12_asks/asides.jsonl")[0]["ended"] == "lead-back"
    mic_rules(p, w.notes)


def test_a_lead_back_the_listener_answers_with_a_question_stays_in_the_aside(world):
    """A lead-back said in the same turn as an answer, and the listener follows up: the page stays in
    the conversation rather than start reading over them."""
    w = world
    p = start_reading(w)
    read(p, w, "s1-p01", upto=3)
    p.wait("L.feed.cur.matched === 2")
    p.page.click("#orb")
    p.wait("L.phase === 'aside'")
    p.page.wait_for_timeout(2200)
    p.hear("What was GATT?")
    p.page.wait_for_timeout(500)
    p.say("A postwar trade treaty. Anyway, where was I? Right.")
    p.page.wait_for_timeout(1000)  # the line has ended; the listener comes straight back with a question
    p.hear("And was she against trade?")
    p.page.wait_for_timeout(2500)
    assert p.L("L.phase") == "aside" and p.marks("lead-back-cancelled")


def test_a_paragraph_never_started_is_sent_again_once_then_skipped(world):
    """A paragraph the guide never starts is sent again once (after resend_ms), then skipped, and the
    next one goes."""
    w = world
    p = start_reading(w)
    p.wait("L.feed.cur && L.feed.cur.id === 's1-p02'", timeout=12000)
    assert [m[2] for m in p.marks("resend")] == ["s1-p01"]
    assert [m[2] for m in p.marks("skipped")] == ["s1-p01"]
    reads = p.reads(w.notes)
    paras = w.paragraphs()
    assert reads == [paras["s1-p01"]["text"]] * 2 + [paras["s1-p02"]["text"]]
    mic_rules(p, w.notes)


def test_step_back_mutes_and_rests_after_the_guides_words(world):
    """The step_back tool: answered to the backend, then once the guide's parting words are said the mic
    is muted (and the track off) and the page rests; a tap brings it back with notes.wake."""
    w = world
    p = connect(w)
    p.tool("step_back", call_id="call_sb")
    p.wait("window.__sent.some((e) => e.type === 'response.item.create' && e.item.call_id === 'call_sb')")
    out = [e for e in p.sent("response.item.create") if e["item"]["call_id"] == "call_sb"][0]
    assert json.loads(out["item"]["output"])["quiet"] is True
    p.say("I'll be right here.")
    p.wait("L.phase === 'resting' && !L.pc && !L.closing")
    assert p.L("L.muted") is True and p.js("window.__track.enabled") is False
    sent = [e["type"] for e in p.sent()]
    assert sent[-2:] == ["session.input_audio.mute", "session.close"]  # then GPT-Live closed (Ian's fifth note)
    p.page.click("#orb")
    p.wait("L.phase === 'talk' && L.liveId === 'live_2'")
    session, _ = w.offers[-1]
    assert session["input"][-1]["content"][0]["text"] == w.notes["back_from_rest"]


def test_typed_text_while_live_goes_as_an_instruction(world):
    """Typed text while the session is live: an instruction with notes.typed (after unmute), and the
    line in the transcript."""
    w = world
    p = connect(w)
    p.page.click("#type")
    p.page.fill("#say", "Tell me about octopuses")
    p.page.click("#send")
    want = w.notes["typed"].replace("{text}", "Tell me about octopuses")
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(want)})")
    sent = p.sent()
    k = next(i for i, e in enumerate(sent) if e.get("content") == want)
    assert p.L("L.muted") is False
    deadline = time.monotonic() + 3
    while not p.post_bodies("api/live/line") and time.monotonic() < deadline:
        time.sleep(0.05)
    assert {"session": p.L("L.session"), "who": "listener", "text": "Tell me about octopuses"} in p.post_bodies("api/live/line")


def test_greeting_goes_as_speakable_context_when_the_session_starts(world):
    """The guide speaks first: notes.opened as speakable context the moment the session starts (live,
    2026-11-14: a developer message in the startup input alone did not reliably make it speak), and the
    session's startup input asks for no greeting of its own (never two)."""
    w = world
    p = connect(w, greet=False, start=False)
    p.wait(f"window.__sent.some((e) => e.type === 'session.commentary.append' && e.content === {json.dumps(w.notes['opened'])})",
           timeout=1000)
    session, _ = w.offers[-1]
    assert session["input"] == []
    # no word from the guide by greet_fallback_ms: asked once more, as an instruction (the mic open)
    p.wait(f"window.__sent.some((e) => e.type === 'session.instructions.append' && e.content === {json.dumps(w.notes['opened'])})",
           timeout=4000)
    assert len([e for e in p.sent("session.commentary.append") if e["content"] == w.notes["opened"]]) == 1


def test_no_greeting_fallback_when_the_guide_speaks_first(world):
    """The fallback's other side: a guide that greets in time is not asked again."""
    w = world
    p = connect(w, greet=True, start=False)
    p.page.wait_for_timeout(2500)
    assert not [e for e in p.sent("session.instructions.append") if e["content"] == w.notes["opened"]]
    assert len([e for e in p.sent("session.commentary.append") if e["content"] == w.notes["opened"]]) == 1


def test_an_opening_set_in_config_is_said_word_for_word(world, cfg):
    """config.yaml live.opening (the Playground's "Opening" box, not an API field): those words, asked
    for word for word, instead of the greeting in its own words."""
    cfg["live"]["opening"] = "Evening! How's the day been?"
    w = world
    p = connect(w, greet=False, start=False)
    want = w.notes["opening_words"].replace("{words}", "Evening! How's the day been?")
    p.wait(f"window.__sent.some((e) => e.type === 'session.commentary.append' && e.content === {json.dumps(want)})",
           timeout=1000)
    assert not [e for e in p.sent("session.commentary.append") if e["content"] == w.notes["opened"]]


def test_a_chapter_being_made_when_the_page_opens_is_taken_over(world):
    """A page reopened while this listener's chapter is being made (its reading not begun): the new
    conversation takes it over (one chapter at a time), opens with notes.reopened instead of a greeting,
    and begins it after the guide's line, as the opening is already written."""
    w = world
    w.lock(1)
    p = w.open()
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    session, _ = w.offers[-1]
    assert session["input"][0]["content"][0]["text"] == w.notes["reopened"].replace("{title}", "The List")
    assert p.L("L.own") == RUN
    assert not [e for e in p.sent("session.commentary.append") if e["content"] == w.notes["opened"]]
    p.say("Hi again.")
    p.wait("L.offer", timeout=5000)  # offered, not begun: they may be mid-conversation
    assert [e for e in p.sent("session.commentary.append") if e["content"] == w.notes["opening_words"].replace("{words}", w.notes["offer_line"].replace("{title}", "The List"))]
    say_yes(p)
    assert p.L("L.feed.cur.id") == "s1-p01"


# --- regressions from the review of the GPT-Live change ----------------------------------------------------

def slow(p, path, ms):
    """Hold the page's POSTs to `path` back by `ms`: a reply that comes back after something else happened."""
    p.js("""([path, ms]) => { const f = window.fetch;
      window.fetch = (u, o) => String(u).includes(path) ? new Promise((r) => setTimeout(r, ms)).then(() => f(u, o)) : f(u, o); }""",
         [path, ms])


def dropped(p):
    """The connection drops (GPT-Live's session.closed for a lost connection): the page calls dropped()."""
    p.emit({"type": "session.closed", "reason": "connection_lost"})


def sent_reads(p, pid, after=0):
    """How many times paragraph `pid` was sent to be read after time `after`, resends of it left out."""
    reads = [m for m in p.marks("read") if m[0] >= after and m[2].split("#")[0] == pid]
    resends = [m for m in p.marks("resend") if m[0] >= after and m[2] == pid]
    return len(reads) - len(resends)


def stop(p):
    """Stop listening: what the Stop button calls (shown while resting), and the end after a long rest."""
    p.js("() => { window.Live.endConversation(); }")


def posted_playing(p):
    return [b for b in p.post_bodies("api/fast/position") if b.get("state") == "playing"]


def tap_mid_paragraph(p, w):
    read(p, w, "s1-p01", upto=3)
    p.wait("L.feed.cur.matched === 2")
    p.page.click("#orb")
    p.wait("L.phase === 'aside'")
    return p.L("Date.now()")


def test_the_button_during_an_aside_reads_once_when_resume_chapter_is_in_flight(world):
    """Regression: "Back to the chapter" read the paragraph, then the restart the guide's resume_chapter
    had asked for came back and read it a second time (the phase was not checked after the await).
    Now the restart gives up once the aside is over: the paragraph goes once."""
    w = world
    p = start_reading(w)
    t0 = tap_mid_paragraph(p, w)
    slow(p, "/api/live/restart", 900)
    p.tool("resume_chapter")
    p.wait("L.aside && L.aside.resuming")
    p.page.click("#orb")  # "Back to the chapter" while the restart is still on its way
    p.wait("L.phase === 'reading'")
    read_through(p, w, "s1-p01")
    p.page.wait_for_timeout(2500)  # the restart's reply, and then its lead-in step, would have come by now
    assert sent_reads(p, "s1-p01", t0) == 1
    assert p.L("L.asides.length") == 0  # the late restart left no record
    log = w.logged("12_asks/asides.jsonl")
    assert len(log) == 1 and log[0]["ended"] == "button"
    mic_rules(p, w.notes)


def test_a_lead_back_and_the_tool_together_start_one_restart(world):
    """Regression: a lead-back in words, resume_chapter, and the silence fallback could each start a
    restart; with the restart's reply slow, two went (two records, the paragraph sent twice)."""
    w = world
    p = start_reading(w)
    t0 = tap_mid_paragraph(p, w)
    slow(p, "/api/live/restart", 700)
    p.page.wait_for_timeout(2200)
    p.hear("Who was she?")
    p.page.wait_for_timeout(500)
    p.say("A systems thinker. Anyway, where was I... right.")
    p.wait("L.aside && L.aside.resuming")  # the lead-back's restart is on its way
    p.tool("resume_chapter")
    p.wait("L.phase === 'reading'", timeout=6000)
    p.page.wait_for_timeout(2500)
    assert p.L("L.asides.length") == 1
    assert len(w.logged("12_asks/asides.jsonl")) == 1
    assert len(p.post_bodies("api/live/restart")) == 1
    assert sent_reads(p, "s1-p01", t0) == 1
    mic_rules(p, w.notes)


def test_a_chapter_ready_while_resting_is_announced_and_waits_for_play(world):
    """Ian's third test: the play button only when the mic is off. Resting, the guide says it's ready in one
    line, the mic stays off, nothing is read, and the play button starts it."""
    w = world
    p = connect(w)
    p.wait("Date.now() - L.spokeAt > 900")
    p.page.click("#orb")  # talk -> resting: the mic off
    p.wait("L.phase === 'resting'")
    w.lock(1)
    p.wait("L.offer")
    line = w.notes["opening_words"].replace("{words}", w.notes["ready_line_resting"])
    assert p.sent("session.commentary.append")[-1]["content"] == line
    p.page.wait_for_timeout(1500)
    assert p.phase() == "resting" and p.L("L.muted") and not p.reads(w.notes)
    assert p.js("document.getElementById('player').hidden") is False and "offer" in p.js("document.body.className")
    p.page.click("#play")
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.sentAt", timeout=4000)
    mic_rules(p, w.notes)


def test_stop_during_the_lead_in_reads_nothing(world):
    w = world
    p = connect(w)
    offered(p, w)
    p.say("It's ready. Want to hear it now?")
    p.wait("L.offer && L.offer.askedAt")
    p.hear("Yes.")
    p.wait("L.pending")  # the start waits for the guide's lead-in
    stop(p)
    p.wait("L.phase === 'idle'", timeout=6000)
    p.page.wait_for_timeout(1500)
    assert p.phase() == "idle"
    assert not p.reads(w.notes) and not p.post_bodies("api/live/read") and not posted_playing(p)
    assert not p.post_bodies("api/live/archive")


def test_a_drop_while_the_resume_is_pending_reads_nothing(world):
    """Regression: resume_chapter's step (read on once the guide has led back in) survived a drop."""
    w = world
    p = start_reading(w)
    t0 = tap_mid_paragraph(p, w)
    p.tool("resume_chapter")
    p.wait("L.pending")
    playing = len(posted_playing(p))
    dropped(p)
    p.wait("L.phase === 'resting'")
    p.page.wait_for_timeout(2500)
    assert p.phase() == "resting" and sent_reads(p, "s1-p01", t0) == 0
    assert len(posted_playing(p)) == playing
    assert p.L("L.dropAt && [L.dropAt.id, L.dropAt.sentence]") == ["s1-p01", 0]


def test_stop_during_a_section_boundary_reopen_does_not_read_on(world):
    """Regression: Stop pressed while the new session of a section-boundary reopen was starting: connect()
    returned true (its epoch had moved on), and reopen() set the page reading on the new channel."""
    w = world
    p = start_reading(w, 1, 2)
    p.emit({"type": "session.usage.updated", "usage": {"seconds": 300}, "context_window": {"usage_ratio": 0.85}})
    for pid in ("s1-p01", "s1-p02"):
        read_through(p, w, pid)
    p.wait("L.feed.cur && L.feed.cur.id === 's1-p03'")
    p.js("window.__autoStart = false")  # the new session never says it started
    read_through(p, w, "s1-p03")
    # the new session is set up (its answer is back) and waiting for session.started
    p.wait("L.reopening && L.phase === 'connecting' && L.liveId === 'live_2'")
    stop(p)
    p.wait("L.phase === 'idle'", timeout=8000)
    p.page.wait_for_timeout(500)
    assert p.phase() == "idle"
    assert not [e for e in p.sent("session.commentary.append") if e["ch"] == 1]
    assert "Years later" not in " ".join(p.reads(w.notes))
    assert not [m for m in p.marks("dropped")]  # a Stop, not a drop


def test_a_chapter_ready_while_the_guide_is_away_is_announced_then_play_plays_it(world):
    """Resting closes GPT-Live (Ian's fifth note). The opening written meanwhile: a session opens just to say it's
    ready (the mic off), then closes; a tap opens one more, told to say nothing, and reads with the mic off.
    (Earlier regressions kept: nothing is read into a closed connection, and the mic track comes back on in a
    new session.)"""
    w = world
    p = connect(w)
    p.page.click("#orb")  # talk -> resting: muted, the track off, the session closed
    p.wait("L.phase === 'resting' && L.muted && !L.pc && !L.closing")
    assert p.js("window.__track.enabled") is False
    w.lock(1)
    p.wait("L.offer && L.liveId === 'live_2'", timeout=6000)
    assert p.post_bodies("api/live/resume")[-1]["reason"] == "ready"
    line = w.notes["opening_words"].replace("{words}", w.notes["ready_line_resting"])
    assert p.sent("session.commentary.append")[-1]["content"] == line and p.L("L.muted") is True
    p.say("Your chapter's ready whenever you are. Tap play when you'd like it.")
    p.wait("L.phase === 'resting' && !L.pc && !L.closing", timeout=8000)  # closed again
    assert not p.reads(w.notes) and p.page.text_content("#line") == "The List is ready"
    p.page.click("#play")
    p.wait("L.phase === 'reading' && L.liveId === 'live_3' && L.feed.cur && L.feed.cur.sentAt", timeout=8000)
    assert p.post_bodies("api/live/resume")[-1]["reason"] == "play"
    assert all(e["ch"] == 2 for e in p.sent("session.commentary.append") if e["content"].startswith(w.notes["read"]))
    mic_rules(p, w.notes)


def test_a_second_drop_after_the_reading_went_on_does_not_go_back_to_the_first(world):
    """Regression: the first drop's place (L.dropAt) was kept after the reading went on, so a second drop
    where nothing was being read (waiting for the next part) resumed at the first drop's paragraph."""
    w = world
    p = start_reading(w)
    read(p, w, "s1-p01", upto=3)
    p.wait("L.feed.cur.matched === 2")
    dropped(p)
    p.wait("L.phase === 'resting'")
    assert p.L("L.dropAt.id") == "s1-p01"
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'aside' && L.aside && L.aside.at && L.aside.at.id === 's1-p01'")
    p.tool("resume_chapter")
    p.say("Where were we? Right.")
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.id === 's1-p01' && L.feed.cur.sentAt")
    assert p.L("L.dropAt") is None
    for pid in ("s1-p01", "s1-p02", "s1-p03"):
        p.wait(f"L.feed.cur && L.feed.cur.id === '{pid}'")
        read_through(p, w, pid)
    p.wait("L.phase === 'waiting'")
    t = p.L("Date.now()")
    dropped(p)
    p.wait("L.phase === 'resting'")
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'aside' && L.liveId === 'live_3'")
    assert p.L("L.aside.at") is None
    p.tool("resume_chapter")
    p.say("Back to it.")
    p.wait("L.phase === 'waiting'", timeout=6000)
    assert sent_reads(p, "s1-p01", t) == 0


def test_stop_mid_paragraph_then_a_new_start_resumes_at_the_interrupted_sentence(world):
    """Regression: Stop kept no place, so tapping "Tap to begin" again went on from the paragraph after
    the interrupted one (or a stale drop's place). Now Stop records where the reading was."""
    w = world
    p = start_reading(w)
    read(p, w, "s1-p01", upto=8)  # "Picture a woman in a meeting. Her name"
    p.wait("L.feed.cur.matched === 7")
    stop(p)
    p.wait("L.phase === 'idle'", timeout=6000)
    assert p.L("L.dropAt && [L.dropAt.id, L.dropAt.sentence]") == ["s1-p01", 1]
    p.page.click("#orb")  # a new conversation, which picks the chapter back up (its reading was under way)
    p.wait("L.started && L.phase === 'talk'")
    session, _ = w.offers[-1]
    assert session["input"][0]["content"][0]["text"] == w.notes["reloaded"].replace("{title}", "The List")
    assert p.L("[L.cont.id, L.cont.sentence]") == ["s1-p01", 1]
    p.say("I'm back.")
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.sentAt")
    assert p.reads(w.notes)[-1] == "Her name is Donella Meadows."


def test_back_to_the_chapter_in_a_check_in_reads_the_next_part_once(world):
    """Regression: the check-in's lead-in step was still pending when the listener tapped "Back to the
    chapter"; it then fired and started the reading again from f.next, skipping the part's first paragraph."""
    w = world
    p = start_reading(w)
    for pid in ("s1-p01", "s1-p02", "s1-p03"):
        p.wait(f"L.feed.cur && L.feed.cur.id === '{pid}'")
        read_through(p, w, pid)
    p.wait("L.phase === 'waiting'")
    w.status(waiting_for=2, check_in="check_in", eta_seconds=180)
    p.wait("L.phase === 'checkin'")
    p.say("While we wait, what stood out?")
    w.lock(2, check_in=None, waiting_for=None)
    p.wait("L.wait && L.wait.arrived")
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['next_ready'])})")
    assert p.L("!!L.pending")
    p.page.click("#orb")  # "Back to the chapter"
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.id === 's2-p01'")
    assert p.L("L.pending") is None
    p.say("And here's the next part.")  # the lead-in it was asked for, said anyway
    p.page.wait_for_timeout(2000)
    assert not [m for m in p.marks("read") if m[2].startswith("s2-p02")]
    assert p.L("L.feed.cur && L.feed.cur.id") == "s2-p01"
    assert sent_reads(p, "s2-p01") == 1


def test_a_question_typed_mid_chapter_gets_the_silent_context(world):
    """Regression: typed text set neither a.lastListener nor L.inp, so the question's silent context
    (where the reading stopped, which parts exist) waited for the listener to speak, or 120 s."""
    w = world
    p = start_reading(w)
    read(p, w, "s1-p01", upto=8)
    p.wait("L.feed.cur.matched === 7")
    p.page.click("#type")
    p.page.fill("#say", "Who was she?")
    p.page.click("#send")
    p.wait("window.__sent.some((e) => e.type === 'session.thinking.append')", timeout=4000)
    thinks = p.sent("session.thinking.append")
    assert thinks[0]["content"].startswith(w.notes["context"] + "\n")
    assert 'while you were reading: "Her name is Donella Meadows."' in thinks[0]["content"]
    assert p.L("L.aside.lines").count({"who": "listener", "text": "Who was she?"}) == 1
    typed = w.notes["typed"].replace("{text}", "Who was she?")
    assert any(e.get("content") == typed for e in p.sent("session.instructions.append"))


def test_tapping_the_next_unwritten_part_waits_for_it_and_brings_in_the_check_in(world):
    """Regression: a tap on the next part's marker while it said "being written" did nothing, though
    REPORT.md's script asks Ian to force a check-in that way. Now the reading stops and waits for it."""
    w = world
    p = start_reading(w)
    p.wait("document.querySelector('#sections button[data-n=\"2\"]')")
    assert p.js("document.querySelector('#sections button[data-n=\"2\"]').dataset.state") == "being_written"
    p.page.click('#sections button[data-n="2"]')
    p.wait("L.phase === 'waiting' && L.wait && L.wait.n === 2")
    assert p.L("L.feed.cur") is None
    assert w.notes["moved"] in [e["content"] for e in p.sent("session.instructions.append")]
    w.reported(state="waiting", waiting_for=2)
    w.status(waiting_for=2, check_in="check_in", eta_seconds=120)
    p.wait("L.phase === 'checkin'")
    assert p.sent("session.instructions.append")[-1]["content"].startswith("[The reading has reached a part")


def test_typing_after_a_drop_goes_on_in_the_same_conversation(world):
    """Regression: a line typed after the voice dropped started a new typed conversation (/api/chat/start),
    which ended the live one on the server, so the tap to bring voice back got 409 ("That conversation
    has ended."). Now the typed line goes to /api/chat with the live session's id, and voice comes back."""
    w = world
    p = connect(w)
    sid = p.L("L.session")
    dropped(p)
    p.wait("L.phase === 'resting' && !L.pc")
    p.page.click("#type")
    p.page.fill("#say", "Still there?")
    p.page.click("#send")
    # Since the guide steps back with GPT-Live closed (Ian's fifth note), typing brings the live guide back in
    # the same conversation, as a tap does (the typed guide only when voice can't come back).
    p.wait("L.started && L.phase === 'talk' && L.liveId === 'live_2'", timeout=6000)
    assert p.post_bodies("api/live/resume")[0]["session"] == sid and p.L("L.session") == sid
    assert p.post_bodies("api/live/resume")[0]["reason"] == "dropped"
    assert instructions(p)[-1] == w.notes["typed"].replace("{text}", "Still there?") and not p.post_bodies("api/chat")


def test_reset_reports_the_sessions_cost_before_the_server_forgets_it(world, cfg):
    """Regression: Reset posted /api/reset, cleared L.session, and only then ended the conversation, so
    reportUsage returned early and the session's cost was never logged."""
    w = world
    w.status(pid=None)  # no chapter process to stop (status.json's pid is this test's own)
    p = connect(w)
    p.emit({"type": "session.usage.updated", "usage": {"seconds": 90}})
    p.page.on("dialog", lambda d: d.accept())
    p.page.click("#reset")
    p.wait("L.phase === 'idle' && !L.session", timeout=8000)
    order = [path for path, _ in p.posts if path in ("api/live/usage", "api/reset")]
    assert order[:2] == ["api/live/usage", "api/reset"] and order.count("api/live/usage") == 1
    # logged before the reset, so it moved with the sessions to listener/_cleared_<time>/
    logs = list((w.root.parent.parent / "listener").glob("**/sessions/log.jsonl"))
    recs = [json.loads(x) for log in logs for x in log.read_text().splitlines() if '"openai"' in x]
    assert len(recs) == 1 and recs[0]["live_id"] == "live_1" and recs[0]["seconds"] == 90


# --- whose chapter (Ian's first live test, 2026-11-14) -----------------------------------------------------
# An earlier conversation's finished chapter was the newest run: "Hey! I'm Juno. What's your name?" ...
# "Hey, I'm Ian" ... "Here's your chapter, starting now." No start_chapter, no research.

@pytest.mark.parametrize("state, heard", [("ready", "done"), ("ready", None), ("ready", "idle"), ("problem", "playing"),
                                          ("stopped", "playing")])
def test_an_earlier_conversations_chapter_is_never_read_by_itself(world, state, heard):
    """("ready", None): written days ago and never begun; ("ready", "idle"): let go by Reset."""
    w = world
    w.lock(1, 2, state=state, **({"updated": "2026-11-01T10:00:00"} if heard is None else {}))
    if heard:
        write(w.root / "fast" / "player.json", {"at": time.time() - (7200 if heard == "done" else 30), "state": heard,
                                                "section": 1, "piece": 0})
    p = connect(w, greet=False, start=False)
    assert p.L("L.own") is None and p.L("L.cont") is None
    label = f"Old {time.time_ns()}"
    w.status(label=label)  # the old run's status arrives again inside the conversation (onStatus, not just the page load)
    p.wait(f"L.status.label === {json.dumps(label)}")
    p.say("Hey! I'm Juno. What's your name?")
    p.page.wait_for_timeout(700)
    p.hear("Hey, I'm Ian.")
    p.page.wait_for_timeout(3500)
    assert p.phase() == "talk"
    assert not p.marks("read") and not p.L("L.readStarted")
    assert not [e for e in p.sent("session.instructions.append") if "chapter" in e["content"].lower()]
    assert "Tap to begin" not in p.js("document.getElementById('line').textContent")  # mid-conversation
    assert "problem" not in p.js("document.body.className")  # an old run's failure isn't this conversation's
    # once this conversation starts its own chapter (here the World's run again, now being made), it is read
    start_chapter(p)
    w.status(state="making", label="Writing your chapter", message=None)
    p.wait("L.offer", timeout=8000)


def test_start_chapters_run_is_kept_by_the_page_and_not_passed_to_the_backend(world):
    w = world
    p = connect(w, start=False)
    start_chapter(p)
    outs = [json.loads(e["item"]["output"]) for e in p.sent("response.item.create")]
    assert outs and outs[-1]["started"] is True and "run" not in outs[-1]


def test_the_lead_in_waits_for_the_answer_to_the_guides_question(world):
    """The opening became ready right after the guide asked something: the lead-in waited only for the
    listener to stop, so it cut in before they had answered. Now it waits for the answer (answer_wait_ms
    at most), then for both to be quiet."""
    w = world
    p = connect(w)
    p.page.wait_for_timeout(1000)
    p.say("So what did you make today?")
    w.lock(1)
    p.page.wait_for_timeout(1800)
    assert p.phase() == "talk", "it cut in before they answered"
    assert not p.L("L.offer")
    p.hear("A bookshelf, finally.")
    p.wait("L.offer", timeout=5000)
    t_lead = p.L("L.log.find((m) => m[1] === 'say')[0]")
    t_heard = p.L("L.heardAt")
    assert t_lead - t_heard >= 600 - 50  # handback_quiet_ms after their last word
    assert any(b.get("who") == "page" and b["text"] == w.notes["opening_words"].replace("{words}", w.notes["offer_line"].replace("{title}", "The List"))
               for b in p.post_bodies("api/live/line"))  # the page's notes are in the transcript


def test_the_lead_in_goes_ahead_if_the_question_gets_no_answer(world):
    w = world
    p = connect(w)
    p.page.wait_for_timeout(1000)
    p.say("What are you up to tonight?")
    w.lock(1)
    p.wait("L.offer", timeout=6000)  # answer_wait_ms (2.5 s here), then the pause


def test_a_promised_start_without_start_chapter_gets_one_reminder(world):
    """Delegation can miss the tool: "I'll get to work on it" and no start_chapter -> one reminder."""
    w = world
    p = connect(w, start=False)
    p.page.wait_for_timeout(1000)
    p.say("Great, I'll get to work on it.")
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['start_nudge'])})", timeout=5000)
    p.page.wait_for_timeout(2500)
    assert len([e for e in p.sent("session.instructions.append") if e["content"] == w.notes["start_nudge"]]) == 1


def test_no_reminder_when_start_chapter_comes(world):
    w = world
    p = connect(w, start=False)
    p.page.wait_for_timeout(1000)
    p.say("Great, I'll get to work on it.")
    start_chapter(p)
    p.page.wait_for_timeout(3000)
    assert not [e for e in p.sent("session.instructions.append") if e["content"] == w.notes["start_nudge"]]


def read_section_1(p, w):
    for pid in ("s1-p01", "s1-p02", "s1-p03"):
        p.wait(f"L.feed.cur && L.feed.cur.id === '{pid}'")
        read_through(p, w, pid)
    p.wait("L.phase === 'waiting'")


@pytest.mark.parametrize("state, note", [("problem", "chapter_stopped"), ("stopped", "chapter_halted")])
def test_a_chapter_that_stops_mid_reading_is_said_plainly_not_waited_for(world, state, note):
    """A failure is said as one; Stop pressed by the listener is not called a failure."""
    w = world
    p = start_reading(w)
    read_section_1(p, w)
    w.status(state=state)
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes[note])})", timeout=4000)
    assert p.phase() == "talk"
    p.page.wait_for_timeout(1500)
    assert not [e for e in p.sent("session.instructions.append") if e["content"].startswith("[The reading has reached")]


def test_a_tap_after_the_chapter_has_finished_starts_a_new_conversation(world):
    """The circle was dead once a chapter had been heard; now a tap is a new conversation, and the
    finished chapter is not read again."""
    w = world
    p = start_reading(w)
    p.js("window.Live.endConversation()")
    p.wait("L.phase === 'idle'", timeout=6000)
    p.page.wait_for_timeout(500)  # its last position report is in
    w.lock(2, state="ready")
    write(w.root / "fast" / "player.json", {"at": time.time(), "state": "done", "section": 2, "piece": 0})
    p.js("window.Live.L.finished = true; window.Live.L.phase = 'done'")
    n = len(w.offers)
    p.page.click("#orb")
    p.wait(f"L.started && L.phase === 'talk' && L.liveId === 'live_{n + 1}'")
    assert p.L("L.own") is None and p.L("L.readStarted") is False and p.L("L.feed.items.length") == 0
    p.say("Hi again!")
    p.page.wait_for_timeout(2500)
    assert p.phase() == "talk"



# --- regressions from the review of the whose-chapter fix ---------------------------------------------------

def test_a_reload_during_a_question_picks_up_at_that_paragraph(world):
    """A tap reported section 1, piece 0 (no paragraph current once the feed was cleared), so a page
    reopened during the question read the chapter again from its opening."""
    w = world
    p = start_reading(w)
    read_through(p, w, "s1-p01")
    p.wait("L.feed.cur && L.feed.cur.id === 's1-p02' && L.feed.cur.sentAt")
    read(p, w, "s1-p02", upto=3)
    p.wait("L.feed.cur.matched >= 2")
    p.page.click("#orb")  # a question
    p.wait("L.phase === 'aside'")
    w.reported(state="paused", section=1, piece=1)
    p.page.wait_for_timeout(500)
    assert w.player()["piece"] == 1, "a later report moved it back to the opening"
    p.page.reload()
    p.page.wait_for_function("window.Live && window.Align")
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    assert p.L("L.cont && [L.cont.id, L.cont.begun]") == ["s1-p02", True]


def test_a_chapter_that_fails_before_its_opening_is_told(world):
    w = world
    p = connect(w)
    w.status(state="problem", message="Something went wrong.")
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['chapter_failed'])})", timeout=4000)
    assert p.phase() == "talk"
    assert "Something went wrong" in p.js("document.getElementById('line').textContent")


def test_try_again_after_the_chapter_stopped_reads_the_rest(world):
    """After chapter_stopped, Try again wrote the next part but nothing read it (the wait was gone)."""
    w = world
    p = start_reading(w)
    read_section_1(p, w)
    w.status(state="problem")
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['chapter_stopped'])})", timeout=4000)
    p.say("Oh no, the rest couldn't be written.")
    w.status(state="making")
    p.wait("L.phase === 'checkin'")
    w.lock(2)
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['next_ready'])})", timeout=6000)
    p.say("Good news, here's the rest.")
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.id === 's2-p01'", timeout=6000)


@pytest.mark.parametrize("line", ["Here's a thought: I'll get started on a chapter about why glue holds. Want that?",
                                  "Did you get started on the cabinets yet?"])
def test_a_pitch_or_a_question_never_arms_the_reminder(world, line):
    w = world
    p = connect(w, start=False)
    p.page.wait_for_timeout(800)
    p.say(line)
    p.page.wait_for_timeout(3500)
    assert not [e for e in p.sent("session.instructions.append") if e["content"] == w.notes["start_nudge"]]


def test_the_reminder_from_resting_shows_talk_then_rests_again(world):
    """The guide promised, stepped back, and never called start_chapter: the reminder opened the mic
    while the page still said 'Tap to talk'."""
    w = world
    p = connect(w, start=False)
    p.page.wait_for_timeout(800)
    p.say("Lovely, I'll get started on it now.")
    p.tool("step_back")
    p.say("Enjoy the quiet.")
    # the reminder goes before the session closes for the rest (a rest closes GPT-Live)
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['start_nudge'])})", timeout=8000)
    assert p.phase() == "talk" and p.L("L.muted") is False
    p.say("Right.")
    p.wait("L.phase === 'resting' && L.muted && !L.pc", timeout=8000)


def test_no_greeting_fallback_while_the_listener_speaks_first(world):
    w = world
    p = connect(w, greet=False, start=False)
    p.hear("Hi, I'm Ian, I've been testing this all evening and")
    p.page.wait_for_timeout(2500)
    assert not [e for e in p.sent("session.instructions.append") if e["content"] == w.notes["opened"]]


def test_a_taken_over_chapters_session_is_prompted_if_it_says_nothing(world):
    w = world
    w.lock(1)
    p = w.open()
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    want = "[" + w.notes["reopened"].replace("{title}", "The List") + "]"
    p.wait(f"window.__sent.some((e) => e.type === 'session.instructions.append' && e.content === {json.dumps(want)})", timeout=4000)


def test_the_answer_wait_counts_from_the_question(world):
    """The opening was ready well before the guide asked: the wait ran from then, so it cut in on the answer."""
    w = world
    p = connect(w)
    p.page.wait_for_timeout(800)
    w.lock(1)
    for k in range(5):  # chatting on past answer_wait_ms (no pause long enough to lead in)
        p.hear(f"and then another thing {k}")
        p.page.wait_for_timeout(300)
        p.say(f"Oh nice, tell me more {k}.")
        p.page.wait_for_timeout(300)
    assert p.phase() == "talk"
    p.say("Which clamp has been with you longest?")
    p.page.wait_for_timeout(1800)
    assert p.phase() == "talk" and not p.L("L.offer"), "it offered over an unanswered question"
    p.hear("The old blue one.")
    p.wait("L.offer", timeout=5000)


# --- review of the third run's build (2026-11-19) -----------------------------------------------------------

def sent_note(w, key):
    return f"window.__sent.some((e) => e.content === {json.dumps(w.notes[key])})"


def test_a_director_gone_down_brings_the_clocks_back_in_a_lull_only(world):
    """Two failures put the director down; by then the offer cue is long overdue. It must not go into the listener's
    sentence on the next tick (Ian's run-3 complaint), only at the next lull."""
    w = world
    p = connect(w, greet=False, start=False)
    director_on(p, ["error", "error"])
    cue(p, "offer_nudge", {"after_secs": 0.2, "max_secs": 0.5, "offered": OFFERED})
    p.say("Hi there!")
    p.hear("Hi.")
    p.wait(logged(p, "direct-fail"), timeout=6000)
    p.hear("Hello?")
    p.wait(logged(p, "director-down"), timeout=6000)
    for _ in range(8):  # mid-story, well past max_secs: the old clock would cut in now
        p.hear("and then the salmon, and the omega threes")
        p.page.wait_for_timeout(400)
    assert w.notes["offer_nudge"] not in instructions(p)
    p.wait(sent_note(w, "offer_nudge"), timeout=8000)  # the lull
    assert p.L("Date.now() - L.heardAt") >= 4000


def inject(p, about, kind="when_it_fits", note="Tease them about the shed.", asked_ms_ago=50):
    """A whisper as if a look had just come back (L.dir.next), asked for `asked_ms_ago`."""
    p.js("([about, kind, note, ago]) => { window.Live.L.dir.next = {kind, about, note, askedAt: Date.now() - ago, "
         "at: Date.now()}; }", [about, kind, note, asked_ms_ago])


def test_other_page_notes_never_gate_a_whisper_but_the_last_whisper_does(world):
    """The min gap was counted from any page note, so a started note or a clock's kept the director's whisper out
    (review, 2026-11-19). Now only the last whisper counts: a whisper right after the started note goes; a second
    within min_gap_secs is dropped; a "now" whisper is exempt and goes as `direct_now`."""
    w = world
    p = connect(w, greet=False, start=False)
    director_on(p, [], min_gap_secs=10)
    p.say("Hi there!")
    p.wait("Date.now() - L.spokeAt > 900 && !L.out")
    start_chapter(p)
    p.wait(logged(p, "after-start", "company"))
    inject(p, "habit", note="Drop the stacked questions.")
    p.wait(logged(p, "direct-sent", "habit"), timeout=4000)
    assert instructions(p)[-1] == w.notes["direct"].replace("{note}", "Drop the stacked questions.")
    assert p.L("L.dir.sentAt > 0") and not p.L("'noteAt' in L")
    inject(p, "habit", note="Fewer questions.")
    p.wait(logged(p, "direct-drop", "habit"), timeout=4000)
    inject(p, "unanswered", kind="now", note="They asked what it is about: answer that.")
    p.wait(logged(p, "direct-sent", "unanswered"), timeout=4000)
    assert instructions(p)[-1] == w.notes["direct_now"].replace("{note}", "They asked what it is about: answer that.")


@pytest.mark.parametrize("line", ["Okay, sure, let's chat!", "Great, tell me about you.", "Thanks, tell me about falcons.",
                                  "Yes.", "Sure!", "Yeah, let's chat.", "No, I'd love to chat actually"])
def test_taking_up_the_offer_to_chat_is_never_read_as_a_no(world, line):
    w = world
    p = w.open()
    assert p.js("(s) => window.Live.declinesChat(s)", line) is False, line


@pytest.mark.parametrize("line", ["No, go ahead.", "Okay, thanks!", "Nope.", "No thanks, I'm good.", "Perfect, thank you",
                                  "Um, no, I'm good.", "Sure, go ahead.", "Yeah, go ahead.", "Oh, I'm good.",
                                  "Not right now.", "Yep, all good.", "I think I'm good.", "Nothing else, go for it"])
def test_the_ways_of_declining_the_offer_to_chat_are_a_no(world, line):
    w = world
    p = w.open()
    assert p.js("(s) => window.Live.declinesChat(s)", line) is True, line


def test_okay_lets_chat_keeps_the_company_going(world):
    """Review (2026-11-19): "Okay, sure, let's chat!" matched the start-anchored no and rested the page."""
    w = world
    p = step_back_offered(w, reply_secs=1.5)
    p.hear("Okay, sure, let's chat!")
    p.wait(logged(p, "step-back-cancelled", "chat"), timeout=4000)
    p.say("What's on your mind?")
    p.page.wait_for_timeout(3000)
    assert p.phase() == "talk"


def test_sure_go_ahead_to_the_offer_to_chat_rests(world):
    w = world
    p = step_back_offered(w, reply_secs=20)
    p.hear("Sure, go ahead.")
    p.wait(logged(p, "step-back-answer", "no"), timeout=4000)
    p.say("Great, talk soon.")
    p.wait("L.phase === 'resting' && !L.pc", timeout=6000)


def test_a_paraphrased_step_back_line_that_asks_still_waits_for_the_answer(world):
    """Run 3's guide said it with none of the step-back words; ending on a question, it opened no reply wait, and the
    12 s fallback rested before the listener could answer. Any line after the note is the step-back line."""
    w = world
    p = connect(w, start=False)
    not_engaged(p)
    cue(p, "step_back_wait_secs", 2)
    cue(p, "step_back_reply_secs", 6)
    p.wait("Date.now() - L.spokeAt > 900 && !L.out")
    start_chapter(p)
    p.wait("L.stepBackAt")
    p.say("Perfect, I'll go dig into that now, about four minutes. Anything else on your mind first?")
    p.wait(logged(p, "step-back-offer"), timeout=5000)
    assert p.L("!!L.replyWait")
    p.page.wait_for_timeout(3000)
    assert p.phase() == "talk"
    p.wait("L.phase === 'resting' && !L.pc", timeout=8000)


def test_with_the_director_up_the_first_fallback_is_the_offer_if_nothing_was_explained(world):
    """The director has no offer clock: a guide that never said what it does got the pitch nudge first (review,
    2026-11-19). Then the first fallback is the offer cue, and the pitch nudge comes at the next one."""
    w = world
    p = connect(w, greet=False, start=False)
    director_on(p, fallback={"pitch_nudge_secs": [0.5, 1000]})
    cue(p, "offer_nudge", {"after_secs": 1000, "max_secs": 1000, "offered": OFFERED})
    p.say("Hi there!")
    p.wait(sent_note(w, "offer_nudge"), timeout=9000)
    assert p.marks("offer-nudge") and w.notes["pitch_nudge"] not in instructions(p)


def test_with_the_director_up_and_it_explained_the_first_fallback_is_the_pitch(world):
    w = world
    p = connect(w, greet=False, start=False)
    director_on(p, fallback={"pitch_nudge_secs": [0.5, 1000]})
    cue(p, "offer_nudge", {"after_secs": 1000, "max_secs": 1000, "offered": OFFERED})
    p.say("I make short audio chapters, just for you.")
    p.wait(sent_note(w, "pitch_nudge"), timeout=9000)
    assert w.notes["offer_nudge"] not in instructions(p)


def test_with_the_director_up_the_pitch_fallback_goes_late_only_in_a_lull_and_not_near_its_note(world):
    """Decision 6: with the director up, the pitch nudges are late fallbacks (director.fallback.pitch_nudge_secs) that
    never go while the listener talks, and not within min_gap_secs of the earpiece's last whisper."""
    w = world
    p = connect(w, greet=False, start=False)
    director_on(p, min_gap_secs=7, fallback={"pitch_nudge_secs": [0.5, 1000]})
    p.say("I make short audio chapters, just for you. What have you been up to?")
    p.wait("L.lastGuideAt")
    # A look is asked after the line it follows: an interest note asked before the guide's question is dropped.
    p.page.wait_for_timeout(150)
    inject(p, "interest", note="When the shed story winds down, ask what they read.")
    p.wait(logged(p, "direct-sent", "interest"), timeout=6000)
    for _ in range(6):  # they talk past the due time: nothing (the old branch would go at once)
        p.hear("and another thing about the shed")
        p.page.wait_for_timeout(400)
    assert w.notes["pitch_nudge"] not in instructions(p)
    p.wait(sent_note(w, "pitch_nudge"), timeout=12000)
    assert p.L("Date.now() - L.heardAt") >= 4000
    assert p.js("() => { const L = window.Live.L; const s = L.log.find((m) => m[1] === 'direct-sent'); "
                "const n = L.log.find((m) => m[1] === 'pitch-nudge'); return n[0] - s[0]; }") >= 6900  # marks a tick late


# --- after Ian's second test (2026-11-15) ------------------------------------------------------------------

def instructions(p):
    return [e["content"] for e in p.sent("session.instructions.append")]


def cue(p, key, value):
    """Turn a clock-driven cue on (the test config has them off)."""
    p.js(f"() => {{ window.Live.S.{key} = {json.dumps(value)}; }}")


def test_a_hello_nobody_answers_gets_one_light_check_in(world):
    w = world
    p = connect(w, start=False)
    cue(p, "hello_silence_secs", 1)
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['hello_silence'])})", timeout=5000)
    p.page.wait_for_timeout(2000)
    assert instructions(p).count(w.notes["hello_silence"]) == 1


def test_a_hello_that_was_answered_gets_no_check_in(world):
    w = world
    p = connect(w, start=False)
    p.hear("Oh hi!")
    cue(p, "hello_silence_secs", 1)
    p.page.wait_for_timeout(2500)
    assert w.notes["hello_silence"] not in instructions(p)


def test_a_long_conversation_without_a_chapter_is_nudged_toward_one_twice_at_most(world):
    """pitch_nudge_secs into the conversation, with no chapter: the guide is asked to move toward one,
    sent while the listener is speaking (its reply takes it in); a second, firmer one later; no more."""
    w = world
    p = connect(w, start=False)
    cue(p, "pitch_nudge_secs", [1, 2])
    p.page.wait_for_timeout(1700)  # never on top of the guide: its hello was 1.5 s ago at least
    p.hear("so I was building a shelf")
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['pitch_nudge'])})", timeout=3000)
    p.page.wait_for_timeout(1200)
    p.hear("and then the glue")
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['pitch_nudge_again'])})", timeout=3000)
    p.page.wait_for_timeout(1200)
    p.hear("anyway")
    p.page.wait_for_timeout(1000)
    got = instructions(p)
    assert got.count(w.notes["pitch_nudge"]) == 1 and got.count(w.notes["pitch_nudge_again"]) == 1


def test_the_pitch_nudge_waits_after_the_guide_says_what_it_does(world):
    """Ian's tester run (2026-11-16): the nudge came 31 s after the guide explained itself and 12 s after its
    first open question, mid-answer, and it pitched the first thing he said. Held for explained_secs."""
    w = world
    p = connect(w, start=False)
    cue(p, "pitch_hold", {"explained_secs": 3, "question_secs": 0, "max_secs": 0})
    p.say("I make short audio chapters for one person at a time.")
    p.wait("L.explainedAt")
    cue(p, "pitch_nudge_secs", [0.5, 1000])
    p.page.wait_for_timeout(1700)
    p.hear("Um, boy. So much")
    p.page.wait_for_timeout(600)
    assert w.notes["pitch_nudge"] not in instructions(p)
    p.page.wait_for_timeout(1500)  # past the hold
    p.hear("mostly the garden, lately")
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['pitch_nudge'])})", timeout=3000)


def test_the_pitch_nudge_waits_for_the_answer_to_the_guides_question_but_not_forever(world):
    """Not while its latest question is being answered; a guide that keeps asking is still nudged, max_secs late."""
    w = world
    p = connect(w, start=False)
    cue(p, "pitch_hold", {"explained_secs": 0, "question_secs": 1000, "max_secs": 3})
    p.say("What have you been tinkering with lately?")
    p.wait("L.askedAt")
    cue(p, "pitch_nudge_secs", [0.5, 1000])
    p.page.wait_for_timeout(1700)
    p.hear("Um, boy. So much")
    p.page.wait_for_timeout(600)
    assert w.notes["pitch_nudge"] not in instructions(p)
    for _ in range(12):  # they go on talking: the nudge goes once it is max_secs overdue
        p.hear("and another thing")
        p.page.wait_for_timeout(500)
        if w.notes["pitch_nudge"] in instructions(p):
            break
    assert w.notes["pitch_nudge"] in instructions(p)


def test_the_pitch_nudge_waits_for_a_lull_rather_than_going_while_they_talk(world):
    """Review: the question hold is timed from the guide's question, so an answer longer than it was still
    nudged mid-answer. Within max_secs of due the nudge goes only in a lull; past that, as before."""
    w = world
    p = connect(w, start=False)
    cue(p, "pitch_hold", {"explained_secs": 0, "question_secs": 0, "max_secs": 1000})
    cue(p, "pitch_nudge_secs", [0.5, 1000])
    p.page.wait_for_timeout(1700)
    for _ in range(5):  # a long answer: they are talking, and the nudge is due
        p.hear("and another thing about the shed")
        p.page.wait_for_timeout(400)
    assert w.notes["pitch_nudge"] not in instructions(p)
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['pitch_nudge'])})", timeout=8000)  # the lull
    assert p.L("Date.now() - L.heardAt") >= 4000


def test_the_second_pitch_nudge_keeps_its_gap_after_a_late_first(world):
    w = world
    p = connect(w, start=False)
    cue(p, "pitch_nudge_secs", [0.2, 2.7])  # the first is long overdue by the time anyone speaks
    p.page.wait_for_timeout(3000)
    p.hear("so I was building a shelf")
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['pitch_nudge'])})", timeout=3000)
    p.page.wait_for_timeout(1000)
    p.hear("and then the glue")
    p.page.wait_for_timeout(500)
    assert w.notes["pitch_nudge_again"] not in instructions(p)
    p.page.wait_for_timeout(1500)
    p.hear("anyway")
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['pitch_nudge_again'])})", timeout=3000)


def test_no_nudge_once_the_chapter_is_started(world):
    w = world
    p = connect(w)  # start_chapter called
    cue(p, "pitch_nudge_secs", [0.2, 0.4])
    p.page.wait_for_timeout(600)
    p.hear("still chatting")
    p.page.wait_for_timeout(1500)
    assert w.notes["pitch_nudge"] not in instructions(p)


OFFERED = "\\bwhat i do\\b"  # config live.page.offer_nudge.offered


def test_the_first_lull_is_the_guides_cue_to_say_what_it_does_once(world):
    """Ian's second tester run (2026-11-18): "Want to hear what I do?" came two minutes in, after four questions
    about his day ("jump quicker into ... what they do"; "it did a good job at trying to find the silence"). Once
    the listener has spoken and the conversation is after_secs old, the first lull sends offer_nudge; never twice."""
    w = world
    p = connect(w, start=False)
    cue(p, "offer_nudge", {"after_secs": 0.5, "max_secs": 1000, "offered": OFFERED})
    p.page.wait_for_timeout(4600)  # a lull, but the listener hasn't said a word yet: that is the hello's check-in
    assert w.notes["offer_nudge"] not in instructions(p)
    p.hear("oh hi, just winding down")
    p.page.wait_for_timeout(1000)
    p.say("Ooh, winding down from what, I wonder.")
    p.page.wait_for_timeout(2500)
    assert w.notes["offer_nudge"] not in instructions(p)  # not yet a lull (4 s of quiet from both)
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['offer_nudge'])})", timeout=4000)
    assert p.L("Date.now() - L.spokeAt") >= 4000 and p.marks("offer-nudge")
    p.say("Want to hear what I do?")
    p.hear("no, tell me about your day")
    p.page.wait_for_timeout(1000)
    p.say("Ha, mine's been all talk.")
    p.page.wait_for_timeout(5000)  # another lull
    assert instructions(p).count(w.notes["offer_nudge"]) == 1


def test_no_offer_cue_once_the_guide_has_said_what_it_does_nor_while_its_offer_is_being_answered(world):
    w = world
    p = connect(w, start=False)
    cue(p, "offer_nudge", {"after_secs": 0.5, "max_secs": 1000, "offered": OFFERED})
    p.hear("hey")
    p.page.wait_for_timeout(600)
    p.say("So, want to hear what I do?")
    p.page.wait_for_timeout(5200)  # they're thinking about it: a lull, but its own offer is the line waiting
    assert w.notes["offer_nudge"] not in instructions(p)
    p.hear("sure")
    p.page.wait_for_timeout(600)
    p.say("I make short audio chapters, researched properly and written just for you.")
    p.wait("L.explainedAt")
    p.page.wait_for_timeout(5200)
    assert w.notes["offer_nudge"] not in instructions(p) and not p.marks("offer-nudge")


def test_by_max_secs_the_offer_cue_goes_even_while_they_talk(world):
    """A listener who never pauses (or a guide that answers every line at once) leaves no lull: by max_secs the cue
    goes as they speak, so the guide's reply takes it in."""
    w = world
    p = connect(w, start=False)
    p.page.wait_for_timeout(1600)  # never on top of the guide's hello
    age = p.L("(Date.now() - L.convAt) / 1000")  # max_secs counts from the conversation's start
    cue(p, "offer_nudge", {"after_secs": 0.2, "max_secs": age + 2.5, "offered": OFFERED})
    for _ in range(3):
        p.hear("and another thing about the shed")
        p.page.wait_for_timeout(400)
    assert w.notes["offer_nudge"] not in instructions(p)  # before max_secs, only a lull would do
    for _ in range(6):
        p.hear("and the roof, too")
        p.page.wait_for_timeout(400)
        if w.notes["offer_nudge"] in instructions(p):
            break
    assert instructions(p).count(w.notes["offer_nudge"]) == 1
    assert p.L("Date.now() - L.heardAt") < 4000  # no lull was needed


def test_no_offer_cue_once_a_chapter_is_started(world):
    w = world
    p = connect(w)  # start_chapter called
    cue(p, "offer_nudge", {"after_secs": 0.2, "max_secs": 1, "offered": OFFERED})
    p.hear("still chatting")
    p.page.wait_for_timeout(5200)
    assert w.notes["offer_nudge"] not in instructions(p)


@pytest.mark.parametrize("asked, reply", [("Let's just have some quiet time in the meantime.", "Sure, I'll give you space."),
                                          ("Maybe let's just have some quiet time.", "Of course."),
                                          ("I'll just wait for it.", "Sounds good.")])
def test_asking_for_quiet_turns_the_mic_off_without_step_back(world, asked, reply):
    """Ian's second test: "let's just have some quiet time" ... "Sure, I'll give you space." and no
    step_back, so the mic stayed open. The page rests on the ask and the guide's reply."""
    w = world
    p = connect(w)
    p.hear(asked)
    p.wait("L.quietAt")
    p.say(reply)
    p.wait("L.phase === 'resting' && L.muted", timeout=5000)
    assert p.js("window.__track.enabled") is False
    assert p.marks("rest-said") and not p.marks("tool")[1:]  # no step_back was needed
    assert p.page.text_content("#line") == "Tap to talk"


def test_quiet_words_answered_with_a_question_keep_talking(world):
    w = world
    p = connect(w)
    p.hear("It's quiet time at the library where I work.")
    p.page.wait_for_timeout(500)
    p.say("Oh, which library is it?")
    p.page.wait_for_timeout(2000)
    assert p.phase() == "talk" and not p.L("L.muted")


def test_a_ready_chapter_is_offered_and_waits_for_a_yes(world):
    """The opening is written mid-conversation: offered (first_ready), never begun on top of the talk;
    "not yet" leaves it; a yes to a later offer starts it after the guide's lead-in."""
    w = world
    p = connect(w)
    offered(p, w)
    assert p.sent("session.commentary.append")[-1]["content"] == w.notes["opening_words"].replace("{words}", w.notes["offer_line"].replace("{title}", "The List"))  # said, not a note
    p.say("It's ready. Want to hear it now?")
    p.wait("L.offer.askedAt")
    p.hear("Not yet, let me finish this thought.")
    p.page.wait_for_timeout(2500)
    assert p.phase() == "talk" and not p.reads(w.notes) and not p.L("L.readStarted")
    p.say("Sure, go on.")
    p.hear("So anyway, that's the shelf.")
    p.page.wait_for_timeout(400)
    say_yes(p, "Yeah, let's do it.")
    assert p.marks("ready-yes") and p.reads(w.notes)[0] == w.paragraphs()["s1-p01"]["text"]
    mic_rules(p, w.notes)


def test_a_yes_to_another_question_does_not_start_the_chapter(world):
    w = world
    p = connect(w)
    offered(p, w)
    p.say("What did you have for breakfast?")
    p.page.wait_for_timeout(500)
    p.hear("Yeah, toast and chutney.")
    p.page.wait_for_timeout(2500)
    assert p.phase() == "talk" and not p.reads(w.notes)


def test_start_reading_starts_the_offered_chapter_after_the_lead_in(world):
    w = world
    p = connect(w)
    offered(p, w)
    p.tool("start_reading", {"listener_said": "go for it"})
    p.wait("L.pending")
    p.say("Alright.")
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.sentAt")
    assert p.marks("start-reading")[-1][2] == "tool"
    out = [b for b in p.post_bodies("api/live/tool") if b["name"] == "start_reading"]
    assert out and not any(e["type"] == "response.item.create" and "run" in e["item"]["output"] for e in p.sent())


def test_start_reading_before_the_opening_is_written_does_nothing(world):
    w = world
    p = connect(w)
    p.tool("start_reading")
    p.wait("window.__sent.some((e) => e.type === 'response.item.create' && e.item.output.includes('isn'))", timeout=4000)
    p.page.wait_for_timeout(1500)
    assert p.phase() == "talk" and not p.L("L.readStarted") and p.L("L.pending") is None


def test_the_guides_lead_in_starts_the_offered_chapter(world):
    """An answer that isn't a plain yes but isn't a no ("Mm, go on then"), then the guide's lead-in."""
    w = world
    p = connect(w)
    offered(p, w)
    p.say("Your chapter's ready. Want to hear it now?")
    p.wait("L.offer.askedAt")
    p.hear("Mm, go on then.")
    p.wait("L.offer.agreed")
    assert not p.L("L.pending")
    p.say("Okay, here we go.")
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.sentAt", timeout=5000)
    assert p.marks("lead-in")


def test_an_offer_in_conversation_is_spoken_and_the_bars_play_button_shows_in_talk_and_resting(world):
    """In conversation the ready chapter is said by the guide. The bar with its play button shows whenever this
    conversation's chapter is ready and not yet started, in talk as well as with the mic off: since the circle
    no longer plays (Ian, 2026-11-16) the bar is the one way to start it by hand, so it must not go away when
    they tap the circle to talk. (Ian's third test had no play button in talk, when the circle still played.)"""
    w = world
    p = connect(w)
    assert p.js("document.getElementById('player').hidden") is True  # nothing ready yet
    offered(p, w)
    p.page.wait_for_selector("#player:not([hidden])")
    assert p.phase() == "talk" and "offer" in p.js("document.body.className")
    assert p.page.is_visible("#play") and p.page.is_enabled("#play")
    p.page.click("#orb")  # the mic off
    p.wait("L.phase === 'resting'")
    assert p.js("document.getElementById('player').hidden") is False and "offer" in p.js("document.body.className")
    p.page.click("#orb")  # and back to talk: the bar stays
    p.wait("L.phase === 'talk' && L.liveId === 'live_2'", timeout=6000)
    p.page.wait_for_timeout(400)
    assert p.js("document.getElementById('player').hidden") is False and p.page.is_enabled("#play")
    p.page.click("#play")
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.sentAt", timeout=4000)
    mic_rules(p, w.notes)


def test_an_offer_not_taken_up_is_made_once_more(world):
    w = world
    p = connect(w)
    offered(p, w)
    cue(p, "ready_remind_secs", 1)
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['ready_again'])})", timeout=9000)
    p.page.wait_for_timeout(5000)
    assert instructions(p).count(w.notes["ready_again"]) == 1


def test_pause_closes_gpt_live_and_play_opens_a_new_session_from_the_sentence(world):
    """Ian's third test: GPT-Live is not running while paused. Pause closes the session (and releases the
    mic); play opens a new one, told to say nothing, and reads on from the paused sentence."""
    w = world
    p = start_reading(w)
    read_through(p, w, "s1-p01")
    p.wait("L.feed.cur && L.feed.cur.id === 's1-p02' && L.feed.cur.sentAt")
    read(p, w, "s1-p02", upto=6)  # "The meeting is about trade. It"
    p.wait("L.feed.cur.matched === 6")
    assert p.js("document.getElementById('play').dataset.state") == "pause"
    p.page.click("#play")
    p.wait("L.phase === 'paused' && !L.pc", timeout=4000)
    assert any(e["type"] == "session.close" and e["ch"] == 0 for e in p.sent()) and p.L("L.stream") is None
    assert p.L("L.pausedAt") == {"id": "s1-p02", "sentence": 1}
    p.page.wait_for_timeout(1800)
    assert p.post_bodies("api/live/usage")  # its cost reported once it has closed
    w.reported(state="paused", section=1, piece=1)
    p.page.wait_for_timeout(600)
    assert p.L("!L.pc") and p.js("document.getElementById('play').dataset.state") == "play"
    p.page.click("#play")
    p.wait("L.phase === 'reading' && L.liveId === 'live_2' && L.feed.cur && L.feed.cur.sentAt", timeout=6000)
    assert p.post_bodies("api/live/resume")[-1]["reason"] == "paused"
    session, _ = w.offers[-1]
    assert session["input"][-1]["content"][0]["text"] == w.notes["continue"]
    last = [e for e in p.sent("session.commentary.append") if e["content"].startswith(w.notes["read"])][-1]
    assert last["ch"] == 1 and last["content"].endswith("It angers her.")
    mic_rules(p, w.notes)


def test_a_tap_while_paused_asks_from_the_paused_place(world):
    w = world
    p = start_reading(w)
    read(p, w, "s1-p01", upto=3)
    p.wait("L.feed.cur.matched === 2")
    p.page.click("#play")
    p.wait("L.phase === 'paused'", timeout=4000)
    p.page.click("#orb")
    p.wait("L.phase === 'aside'")
    assert p.L("L.aside.at") == {"id": "s1-p01", "sentence": 0}
    assert instructions(p)[-1] == w.notes["tapped"] and p.L("L.muted") is False


def test_a_tap_nobody_speaks_after_stays_paused_with_the_mic_off(world):
    """Ian's second test: a tap, then the reading's tail counted as an answer and the silence after it sent
    the reading back unasked, seven seconds later. Now a tap nobody speaks after becomes a pause."""
    w = world
    p = start_reading(w)
    tap_mid_paragraph(p, w)
    cue(p, "aside_quiet_secs", 1)
    p.say("Yes?")
    p.wait("L.phase === 'paused'", timeout=6000)
    assert p.L("L.muted") is True and w.notes["silence"] not in instructions(p)
    assert w.logged("12_asks/asides.jsonl")[-1]["ended"] == "quiet"
    n = len(p.reads(w.notes))
    p.page.click("#play")
    p.wait(f"window.Live.L.log.filter((m) => m[1] === 'read').length > 0 && L.phase === 'reading' && L.feed.cur && L.feed.cur.sentAt")
    assert len(p.reads(w.notes)) == n + 1 and p.reads(w.notes)[-1] == w.paragraphs()["s1-p01"]["text"]


def test_the_readings_tail_after_a_tap_is_not_an_answer(world):
    w = world
    p = start_reading(w)
    read(p, w, "s1-p01", upto=3)  # "Picture a woman"
    p.wait("L.feed.cur.matched === 2")
    p.page.click("#orb")
    p.wait("L.phase === 'aside'")
    p.say("in a")  # the passage's next words, said before it stopped
    p.wait("window.Live.L.log.some((m) => m[1] === 'tail')", timeout=3000)
    p.page.wait_for_timeout(2500)  # past silence_after_answer_secs
    assert p.L("L.aside.lastGuide") == 0 and w.notes["silence"] not in instructions(p) and p.phase() == "aside"


def tl_frac(p, pid, sentence=0):
    return p.js(f"""(() => {{ const tl = Live.timeline(); const x = tl.paras.find((q) => q.id === '{pid}');
      const before = x.sents.slice(0, {sentence}).reduce((a, b) => a + b, 0);
      return (x.start + before / {p.js("Live.S.words_per_second")} + 0.05) / tl.total; }})()""")


def test_the_slider_moves_the_reading_to_that_sentence(world):
    w = world
    p = start_reading(w)
    read(p, w, "s1-p01", upto=3)
    p.wait("L.feed.cur.matched === 2")
    frac = tl_frac(p, "s1-p03", 1)
    p.js(f"""() => {{ const s = document.getElementById('seek'); s.value = String(Math.round({frac} * 1000));
                      s.dispatchEvent(new Event('input')); s.dispatchEvent(new Event('change')); }}""")
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.id === 's1-p03' && L.feed.cur.sentAt", timeout=4000)
    assert w.notes["moved"] in instructions(p)
    assert p.L("L.feed.cur.sentence") == 1 and p.reads(w.notes)[-1] == "She writes a list, and the list has nine items."
    assert any(b["id"] == "s1-p01" and b["cut"] for b in p.post_bodies("api/live/read"))
    mic_rules(p, w.notes)


def test_the_slider_while_paused_moves_where_play_reads_from(world):
    w = world
    p = start_reading(w)
    read(p, w, "s1-p01", upto=3)
    p.wait("L.feed.cur.matched === 2")
    p.page.click("#play")
    p.wait("L.phase === 'paused'", timeout=4000)
    n = len(p.reads(w.notes))
    p.js(f"() => Live.seek({tl_frac(p, 's1-p02')})")
    assert p.L("L.pausedAt") == {"id": "s1-p02", "sentence": 0} and len(p.reads(w.notes)) == n and p.phase() == "paused"
    p.page.click("#play")
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.id === 's1-p02' && L.feed.cur.sentAt")


def test_the_slider_past_what_is_written_stops_at_its_last_paragraph(world):
    w = world
    p = start_reading(w)
    assert "~" in p.page.text_content("#time")  # section 2 isn't written: the length is a guess
    written = p.js("parseFloat(document.getElementById('seek').style.getPropertyValue('--written'))")
    assert 0 < written < 100
    p.js("() => Live.seek(0.99)")
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.id === 's1-p03' && L.feed.cur.sentAt", timeout=4000)


def test_the_ring_moves_from_the_start_of_the_chapter_and_never_goes_back(world):
    """Ian's second test: no progress until the first section was done. The ring now fills toward the
    opening from the run's start and its countdown."""
    w = world
    p = connect(w)
    started = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 60))
    w.status(started=started, first_audio_in=120, updated=time.strftime("%Y-%m-%dT%H:%M:%S"))
    p.wait("document.body.classList.contains('ring')")
    circ = 2 * 3.141592653589793 * 88
    off = lambda: float(p.js("parseFloat(document.querySelector('#orb .ring').style.strokeDashoffset)"))
    frac = 1 - off() / circ
    assert 0.25 < frac < 0.45, frac  # about 60 of 180 seconds
    w.status(first_audio_in=None)  # past its median: the estimate changes, the ring doesn't go back
    p.page.wait_for_timeout(1500)
    assert 1 - off() / circ >= frac - 0.01
    w.lock(1)
    p.wait("L.offer")
    p.page.wait_for_timeout(1000)
    assert off() < 1  # full: the opening is ready


def test_the_session_stays_after_the_end_for_the_slider_then_closes(world):
    w = world
    p = start_reading(w, 1, 2)
    for pid in w.paragraphs():
        p.wait(f"L.feed.cur && L.feed.cur.id === '{pid}' && L.feed.cur.sentAt")
        read_through(p, w, pid)
    p.wait("L.phase === 'ended'", timeout=4000)
    assert p.L("!!L.pc") and p.L("L.finished") and "finished" in p.page.text_content("#line")
    p.js(f"() => Live.seek({tl_frac(p, 's1-p02')})")
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.id === 's1-p02' && L.feed.cur.sentAt", timeout=4000)
    p.page.wait_for_timeout(300)
    assert sent_reads(p, "s1-p03", after=p.marks("seek")[-1][0]) == 0  # regression: paragraphs heard before raced ahead
    for pid in list(w.paragraphs())[1:]:  # heard to the end again: ended again
        p.wait(f"L.feed.cur && L.feed.cur.id === '{pid}' && L.feed.cur.sentAt")
        read_through(p, w, pid)
    p.wait("L.phase === 'ended'", timeout=4000)
    cue(p, "end_hold_secs", 0.5)
    p.wait("L.phase === 'done'", timeout=4000)
    assert p.post_bodies("api/live/archive")


def test_asking_for_quiet_in_a_check_in_turns_the_mic_off_until_the_next_part(world):
    w = world
    p = start_reading(w)
    for pid in ("s1-p01", "s1-p02", "s1-p03"):
        p.wait(f"L.feed.cur && L.feed.cur.id === '{pid}' && L.feed.cur.sentAt")
        read_through(p, w, pid)
    p.wait("L.phase === 'waiting'")
    w.status(waiting_for=2, check_in="check_in", eta_seconds=120)
    p.wait("L.phase === 'checkin'")
    p.say("So what do you make of it so far?")
    p.hear("I'll just wait for the next part.")
    p.page.wait_for_timeout(400)
    p.say("Sure thing.")
    p.wait("L.phase === 'waiting' && L.muted", timeout=5000)
    w.lock(2)
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.id === 's2-p01'", timeout=5000)


# --- regressions from the review of the second-test changes -------------------------------------------------

@pytest.mark.parametrize("said, reply", [("Hmm, let me think.", "Take your time."),
                                         ("Sure, I'll listen.", "So I make audio chapters, just for you."),
                                         ("I'll let you be the judge of that.", None)])
def test_ordinary_words_never_turn_the_mic_off(world, said, reply):
    w = world
    p = connect(w)
    if reply is None:  # the guide's own words: "I'll let you be..." is not a parting line
        p.say(said)
    else:
        p.hear(said)
        p.page.wait_for_timeout(500)
        p.say(reply)
    p.page.wait_for_timeout(2500)
    assert p.phase() == "talk" and not p.L("L.muted") and not p.marks("rest-said")


def test_a_quiet_ask_answered_with_a_question_is_used_up(world):
    w = world
    p = connect(w)
    p.hear("Let's have some quiet time.")
    p.page.wait_for_timeout(500)
    p.say("Before that, want to hear what I do?")
    p.page.wait_for_timeout(500)
    p.hear("Okay.")
    p.page.wait_for_timeout(500)
    p.say("I make audio chapters.")  # a plain line, but not the answer to the ask any more
    p.page.wait_for_timeout(2500)
    assert p.phase() == "talk" and not p.L("L.muted")


def test_a_rest_is_cancelled_when_the_listener_speaks_first(world):
    w = world
    p = connect(w)
    p.hear("I'll just wait for it.")
    p.page.wait_for_timeout(500)
    p.say("Sure, I'll give you space.")
    p.wait("L.pending")
    p.hear("oh, actually one more thing")
    p.page.wait_for_timeout(2500)
    assert p.phase() == "talk" and not p.L("L.muted") and p.marks("rest-cancelled")


@pytest.mark.parametrize("question, answer", [("Do you play an instrument?", "Yeah, guitar."),
                                              ("Are you ready for the weekend?", "Oh yeah, totally.")])
def test_a_yes_to_an_ordinary_question_never_starts_the_chapter(world, question, answer):
    w = world
    p = connect(w)
    offered(p, w)
    p.say(question)
    p.page.wait_for_timeout(500)
    p.hear(answer)
    p.page.wait_for_timeout(2500)
    assert p.phase() == "talk" and not p.reads(w.notes) and not p.marks("ready-yes")


def test_after_a_not_yet_a_later_question_takes_over_the_offer(world):
    w = world
    p = connect(w)
    offered(p, w)
    p.say("Your chapter's ready. Want to hear it now?")
    p.page.wait_for_timeout(500)
    p.hear("Not yet, I'm making dinner.")
    p.page.wait_for_timeout(500)
    p.say("Are you making the pasta from scratch?")
    p.page.wait_for_timeout(500)
    p.hear("Yeah, always.")
    p.page.wait_for_timeout(2500)
    assert p.phase() == "talk" and not p.reads(w.notes)


@pytest.mark.parametrize("answer", ["Okay, one sec.", "Ok hang on.", "Yeah, not right now.", "Sure, in five minutes.",
                                    "Yeah, let's keep chatting."])
def test_a_deferral_to_the_offer_never_starts_the_chapter(world, answer):
    w = world
    p = connect(w)
    offered(p, w)
    p.say("Your chapter's ready. Want to hear it now?")
    p.page.wait_for_timeout(500)
    p.hear(answer)
    p.page.wait_for_timeout(500)
    p.say("Okay, here we go.")  # not a lead-in after a no
    p.page.wait_for_timeout(2500)
    assert p.phase() == "talk" and not p.reads(w.notes)


def test_a_lead_in_phrase_mid_conversation_never_starts_the_chapter(world):
    w = world
    p = connect(w)
    offered(p, w)
    p.say("Okay, here it is, the thing I love about walnut.")
    p.page.wait_for_timeout(2500)
    assert p.phase() == "talk" and not p.reads(w.notes)


def to_checkin(p, w):
    for pid in ("s1-p01", "s1-p02", "s1-p03"):
        p.wait(f"L.feed.cur && L.feed.cur.id === '{pid}' && L.feed.cur.sentAt")
        read_through(p, w, pid)
    p.wait("L.phase === 'waiting'")
    w.status(waiting_for=2, check_in="check_in", eta_seconds=120)
    p.wait("L.phase === 'checkin'")


def test_step_back_in_a_check_in_turns_the_mic_off_until_the_next_part(world):
    w = world
    p = start_reading(w)
    to_checkin(p, w)
    p.say("Want me to go quiet till it's ready?")
    p.tool("step_back")
    p.wait("L.phase === 'waiting' && L.muted", timeout=5000)
    w.lock(2)
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.id === 's2-p01'", timeout=5000)


def test_silence_in_a_check_in_gets_the_check_in_note_not_go_back(world):
    w = world
    p = start_reading(w)
    to_checkin(p, w)
    p.say("So what do you make of it so far?")
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['checkin_silence'])})", timeout=5000)
    assert w.notes["silence"] not in instructions(p)


def pause_at(p, w, upto=3):
    read(p, w, "s1-p01", upto=upto)
    p.wait(f"L.feed.cur.matched === {upto - 1}")
    p.page.click("#play")
    p.wait("L.phase === 'paused'", timeout=4000)


def test_a_tap_while_paused_reconnects_quietly_and_asks_with_the_context(world):
    w = world
    p = start_reading(w)
    pause_at(p, w)
    p.wait("!L.pc", timeout=4000)
    assert p.L("L.stream") is None  # the mic device released too
    p.page.click("#orb")
    p.wait("L.phase === 'aside' && L.liveId === 'live_2'", timeout=6000)
    assert p.post_bodies("api/live/resume")[-1]["reason"] == "paused-ask"
    session, _ = w.offers[-1]
    assert session["input"][-1]["content"][0]["text"] == w.notes["asking"]
    assert instructions(p)[-1] == w.notes["tapped"] and p.L("L.aside.at") == {"id": "s1-p01", "sentence": 0}
    p.hear("Who was she?")
    p.wait("window.__sent.some((e) => e.type === 'session.thinking.append')", timeout=5000)


def test_a_move_while_paused_is_reported_where_it_now_is(world):
    w = world
    p = start_reading(w)
    pause_at(p, w)
    p.js(f"() => Live.seek({tl_frac(p, 's1-p03')})")
    w.reported(state="paused", section=1, piece=2)


def test_after_the_end_a_word_with_the_guide_then_the_slider_reads_again(world):
    w = world
    p = start_reading(w, 1, 2)
    for pid in w.paragraphs():
        p.wait(f"L.feed.cur && L.feed.cur.id === '{pid}' && L.feed.cur.sentAt")
        read_through(p, w, pid)
    p.wait("L.phase === 'ended'", timeout=4000)
    p.page.click("#orb")  # talk about it
    p.wait("L.phase === 'talk'")
    assert p.js("document.getElementById('seek').disabled") is False and p.js("document.getElementById('play').disabled") is False
    p.js(f"() => Live.seek({tl_frac(p, 's1-p02')})")
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.id === 's1-p02' && L.feed.cur.sentAt", timeout=4000)
    assert p.L("L.replaying") is True and p.L("L.pausedAt") is None


def test_the_end_hold_ends_the_conversation_once(world):
    w = world
    p = start_reading(w, 1, 2)
    for pid in w.paragraphs():
        p.wait(f"L.feed.cur && L.feed.cur.id === '{pid}' && L.feed.cur.sentAt")
        read_through(p, w, pid)
    p.wait("L.phase === 'ended'", timeout=4000)
    p.js("() => { window.__closeSlow = true; }")
    cue(p, "end_hold_secs", 0.2)
    p.wait("L.phase === 'done'", timeout=8000)
    p.page.wait_for_timeout(500)
    assert len(p.post_bodies("api/chat/end")) == 1 and len([e for e in p.sent() if e["type"] == "session.close"]) == 1


def test_a_drop_after_the_end_ends_and_archives(world):
    w = world
    p = start_reading(w, 1, 2)
    for pid in w.paragraphs():
        p.wait(f"L.feed.cur && L.feed.cur.id === '{pid}' && L.feed.cur.sentAt")
        read_through(p, w, pid)
    p.wait("L.phase === 'ended'", timeout=4000)
    dropped(p)
    p.wait("L.phase === 'done'", timeout=6000)
    assert p.post_bodies("api/live/archive")


def test_a_typed_hello_counts_as_an_answer(world):
    w = world
    p = connect(w, start=False)
    p.page.click("#type")
    p.page.fill("#say", "hi there")
    p.page.press("#say", "Enter")
    cue(p, "hello_silence_secs", 1)
    p.page.wait_for_timeout(2500)
    assert w.notes["hello_silence"] not in instructions(p)


def test_a_chapter_that_fails_while_they_rest_says_so_then_rests_again(world):
    w = world
    p = connect(w)
    p.page.click("#orb")  # talk -> resting
    p.wait("L.phase === 'resting' && L.muted")
    # Resting closes GPT-Live (Ian's fifth note): the page shows why, and Try again; the mic stays off.
    w.status(state="problem", message="It stopped.")
    p.wait("L.runEnded", timeout=5000)
    p.page.wait_for_timeout(800)
    assert p.phase() == "resting" and p.L("L.muted") and p.page.text_content("#line") == "It stopped."
    assert "problem" in p.js("document.body.className")


def test_a_long_silence_in_conversation_turns_the_mic_off(world):
    w = world
    p = connect(w)
    cue(p, "talk_rest_secs", 1)
    p.wait("L.phase === 'resting' && L.muted", timeout=5000)
    assert p.marks("silence-rest")


# --- the live player check (2026-11-15): "Yes, let's hear it" after the guide's "it's ready", no question -----

def test_asking_for_the_offered_chapter_outright_starts_it(world):
    w = world
    p = connect(w)
    offered(p, w)
    p.say("Always nice when someone reads her properly.")  # not an offer
    p.page.wait_for_timeout(400)
    p.hear("Yes, let's hear it.")
    p.wait("L.pending")
    p.say("Here we go.")
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.sentAt", timeout=5000)


def test_a_yes_to_a_ready_statement_starts_it(world):
    w = world
    p = connect(w)
    offered(p, w)
    p.say("Your chapter's ready whenever you are.")
    p.wait("L.offer.askedAt")
    p.hear("Sure.")
    p.wait("L.pending")
    p.say("Okay.")
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.sentAt", timeout=5000)


def test_im_ready_for_the_weekend_is_not_asking_for_the_chapter(world):
    w = world
    p = connect(w)
    offered(p, w)
    p.say("Big plans?")
    p.page.wait_for_timeout(400)
    p.hear("I'm ready for the weekend, honestly.")
    p.page.wait_for_timeout(2500)
    assert p.phase() == "talk" and not p.reads(w.notes)


# --- Ian's third test (2026-11-15) -----------------------------------------------------------------------------

@pytest.mark.parametrize("said, reply", [("When it's ready, we'll get back to ya, if you don't mind.", "Of course. Take your time."),
                                         ("I'll do something, you call me.", "Sounds good."),
                                         ("I'll pick up the conversation later.", "Sure thing.")])
def test_saying_youll_be_away_a_minute_turns_the_mic_off(world, said, reply):
    w = world
    p = connect(w)
    p.hear(said)
    p.wait("L.quietAt")
    p.say(reply)
    p.wait("L.phase === 'resting' && L.muted", timeout=5000)


def test_a_stall_in_a_question_is_not_an_answer_and_the_guide_is_asked_to_answer(world):
    """Ian's third test: "Let me just give that a thought", then silence, then the page's silence note sent it
    back to the chapter unanswered. A stall now isn't an answer: the note asks it to answer."""
    w = world
    p = start_reading(w)
    tap_mid_paragraph(p, w)
    assert p.js("getComputedStyle(document.querySelector('#orb .core')).opacity") == "1"  # the circle stays
    p.say("Sure, go on.")
    p.page.wait_for_timeout(400)
    p.hear("Is it true saints meditate in the snow for a hundred years?")
    p.page.wait_for_timeout(400)
    p.say("Huh, that's a good one. Let me just give that a thought.")
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['unanswered'])})", timeout=6000)
    assert p.marks("stall") and w.notes["silence"] not in instructions(p) and p.phase() == "aside"
    p.say("There are stories like that, but no one has documented it; the body can't go that long without food.")
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['silence'])})", timeout=6000)


def tone(p, on=True):
    """A voice-band sound into the fake mic, over a faint steady one (a real room is never digital silence:
    the detector learns the room's level from it)."""
    p.js("""(on) => { const m = window.__mic;
      if (!m.osc) { m.osc = m.ctx.createOscillator(); m.osc.frequency.value = 440; m.g = m.ctx.createGain();
                    m.g.gain.value = 0.002; m.osc.connect(m.g).connect(m.dest); m.osc.start(); }
      m.g.gain.value = on ? 0.4 : 0.002; }""", on)


def room(p):
    tone(p, False)  # the faint steady sound only


def test_a_sound_while_resting_opens_the_mic_and_rests_again_if_nobody_speaks(world):
    """The local listener (page/wake.js) on the live page: resting, a sustained sound in the voice band opens
    the mic with no note to the guide; no words within wake_listen_secs and it rests again; two unanswered
    wakes and only a tap wakes it."""
    w = world
    p = connect(w)
    cue(p, "wake_listen_secs", 1)
    room(p)
    p.page.click("#orb")  # rest
    p.wait("window.Live.L.log.some((m) => m[1] === 'wake-listening')", timeout=5000)
    assert "Say something" in p.page.text_content("#how")
    p.page.wait_for_timeout(1200)  # the detector's warm-up, and the room's level learnt
    n = len(instructions(p))
    tone(p)
    p.wait("L.phase === 'talk' && !L.muted", timeout=5000)
    tone(p, False)
    assert p.marks("sound-wake") and len(instructions(p)) == n  # no note: it may not have been the listener
    p.wait("L.phase === 'resting' && L.muted", timeout=6000)  # nobody spoke
    p.wait("window.Live.L.log.filter((m) => m[1] === 'wake-listening').length >= 2", timeout=5000)
    p.page.wait_for_timeout(1200)
    tone(p)
    p.wait("L.phase === 'talk'", timeout=5000)
    tone(p, False)
    p.wait("L.phase === 'resting'", timeout=6000)
    p.page.wait_for_timeout(2500)
    tone(p)
    p.page.wait_for_timeout(1500)
    tone(p, False)
    assert p.phase() == "resting" and len(p.marks("sound-wake")) == 2  # tap only now
    assert p.page.text_content("#how") == "Tap to talk"


def test_a_sound_wake_answered_keeps_talking(world):
    w = world
    p = connect(w)
    cue(p, "wake_listen_secs", 1)
    room(p)
    p.page.click("#orb")
    p.wait("window.Live.L.log.some((m) => m[1] === 'wake-listening')", timeout=5000)
    p.page.wait_for_timeout(1200)
    tone(p)
    p.wait("L.phase === 'talk'", timeout=5000)
    tone(p, False)
    p.hear("Hey, how's it going?")
    p.page.wait_for_timeout(2500)
    assert p.phase() == "talk" and not p.L("L.muted")


# --- regressions from the review of the third-test changes -----------------------------------------------------

def fail(p, path, status=503):
    p.js("""([path, status]) => { const f = window.fetch;
      window.fetch = (u, o) => String(u).includes(path) ? Promise.resolve(new Response('{"detail": "no"}', {status})) : f(u, o); }""",
         [path, status])


def test_a_failed_reconnect_from_pause_stays_paused_and_releases_the_mic(world):
    w = world
    p = start_reading(w)
    pause_at(p, w)
    p.wait("!L.pc && !L.closing")
    fail(p, "api/live/resume")
    p.page.click("#play")
    p.page.wait_for_timeout(1500)
    assert p.phase() == "paused" and p.L("L.stream") is None and p.L("L.pc") is None
    assert p.L("L.pausedAt") == {"id": "s1-p01", "sentence": 0}


def test_text_typed_while_paused_asks_the_live_guide_from_there(world):
    w = world
    p = start_reading(w)
    pause_at(p, w)
    p.wait("!L.pc && !L.closing")
    p.page.click("#type")
    p.page.fill("#say", "who was she?")
    p.page.press("#say", "Enter")
    p.wait("L.phase === 'aside' && L.liveId === 'live_2'", timeout=6000)
    assert p.post_bodies("api/live/resume")[-1]["reason"] == "paused-ask"
    assert instructions(p)[-1] == w.notes["typed"].replace("{text}", "who was she?")
    assert not p.post_bodies("api/chat")  # not the typed (Claude) guide


def test_a_ready_chapter_after_a_drop_plays_on_one_tap_without_a_second_offer(world):
    w = world
    p = connect(w)
    p.wait("Date.now() - L.spokeAt > 900")
    p.page.click("#orb")  # rest
    p.wait("L.phase === 'resting'")
    w.lock(1)
    p.wait("L.offer")
    dropped(p)
    p.wait("!L.pc && L.phase === 'resting'")
    says, before = len(p.marks("say")), p.L("L.liveId")
    p.page.click("#play")  # reconnect and read
    p.wait(f"L.phase === 'reading' && L.liveId !== {json.dumps(before)} && L.feed.cur && L.feed.cur.sentAt", timeout=8000)
    assert len(p.marks("say")) == says  # no second "want to hear it?"


def test_a_ready_unheard_chapter_picked_up_counts_the_yes_to_its_first_question(world):
    w = world
    w.lock(1, state="ready")
    p = w.open()
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    session, _ = w.offers[-1]
    assert session["input"][0]["content"][0]["text"] == w.notes["ready_unheard"].replace("{title}", "The List")
    assert p.L("!!L.offer")
    p.say("Hey, I'm back. Your chapter's ready. Want to hear it now?")
    p.page.wait_for_timeout(400)
    p.hear("Yes please.")
    p.wait("L.pending")
    p.say("Here we go.")
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.sentAt", timeout=6000)


def test_a_sound_wake_with_a_ready_chapter_keeps_the_play_button(world):
    w = world
    p = connect(w)
    cue(p, "wake_listen_secs", 5)
    room(p)
    p.page.click("#orb")
    p.wait("L.phase === 'resting'")
    w.lock(1)
    p.wait("L.offer")
    p.wait("window.Live.L.log.filter((m) => m[1] === 'wake-listening').length >= 1", timeout=6000)
    p.page.wait_for_timeout(1500)
    tone(p)
    p.wait("L.phase === 'talk' && L.wokeAt", timeout=5000)
    tone(p, False)
    assert p.js("document.getElementById('player').hidden") is False and "offer" in p.js("document.body.className")
    p.page.click("#play")
    p.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.sentAt", timeout=5000)


def test_an_offered_chapter_that_stops_later_is_not_said_to_have_no_opening(world):
    w = world
    p = connect(w)
    offered(p, w)
    w.status(state="problem", message="It stopped.")
    p.wait("L.runEnded", timeout=5000)
    p.page.wait_for_timeout(500)
    assert w.notes["chapter_failed"] not in instructions(p) and instructions(p)[-1] == w.notes["chapter_stopped"]
    assert p.L("!!L.offer")


def test_a_bare_okay_is_not_an_answer(world):
    w = world
    p = start_reading(w)
    tap_mid_paragraph(p, w)
    p.hear("Can you explain some of this in Gujarati?")
    p.page.wait_for_timeout(400)
    p.say("Okay.")
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['unanswered'])})", timeout=6000)


def test_an_aside_that_only_stalls_ends_paused_after_a_long_quiet(world):
    w = world
    p = start_reading(w)
    tap_mid_paragraph(p, w)
    cue(p, "aside_quiet_secs", 4)
    p.hear("Is that true?")
    p.page.wait_for_timeout(400)
    p.say("Hmm, let me think about that.")
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['unanswered'])})", timeout=6000)
    p.say("On it.")
    p.wait("L.phase === 'paused' && !L.pc", timeout=10000)
    assert w.logged("12_asks/asides.jsonl")[-1]["ended"] == "quiet"


def test_a_resume_the_listener_talks_over_is_cancelled(world):
    w = world
    p = start_reading(w)
    tap_mid_paragraph(p, w)
    p.hear("Who was she?")
    p.page.wait_for_timeout(400)
    p.say("Donella Meadows was a systems scientist.")
    p.page.wait_for_timeout(400)
    p.tool("resume_chapter")
    p.wait("L.pending")
    p.hear("oh wait, one more thing")
    p.page.wait_for_timeout(3500)
    assert p.phase() == "aside" and p.marks("lead-back-cancelled")


def test_a_sound_wake_does_not_mute_a_later_conversation(world):
    w = world
    p = connect(w, start=False)
    cue(p, "wake_listen_secs", 1)
    room(p)
    p.page.click("#orb")
    p.wait("window.Live.L.log.some((m) => m[1] === 'wake-listening')", timeout=5000)
    p.page.wait_for_timeout(1200)
    tone(p)
    p.wait("L.phase === 'talk' && L.wokeAt", timeout=5000)
    tone(p, False)
    stop(p)
    p.wait("L.phase === 'idle'", timeout=6000)
    before = p.L("L.liveId")
    p.page.click("#orb")  # a new conversation
    p.wait(f"L.started && L.phase === 'talk' && L.liveId && L.liveId !== {json.dumps(before)}", timeout=6000)
    p.page.wait_for_timeout(2500)
    assert p.phase() == "talk" and not p.L("L.muted")


def test_resting_while_the_guide_still_speaks_waits_to_listen(world):
    w = world
    p = connect(w, start=False)
    p.page.click("#orb")  # rest, mid-word
    p.say("Sure, I'll be right here whenever you want me back, just tap.")
    p.page.wait_for_timeout(1200)
    assert not p.marks("wake-listening")  # not while it is still speaking
    p.wait("window.Live.L.log.some((m) => m[1] === 'wake-listening')", timeout=6000)



def test_a_closed_sessions_last_events_never_end_the_new_one(world):
    """Live check after Ian's third test: after pause, the closed session's errors and its session.closed
    (reason not close_requested) reached the page and ended the session play had just opened."""
    w = world
    p = start_reading(w)
    pause_at(p, w)
    p.wait("!L.pc && !L.closing")
    p.page.click("#play")
    p.wait("L.phase === 'reading' && L.liveId === 'live_2' && L.feed.cur && L.feed.cur.sentAt", timeout=6000)
    old = """(ev) => { const ch = window.__channels[0]; if (ch.onmessage) ch.onmessage({ data: JSON.stringify(ev) }); }"""
    p.js(old, {"type": "error", "error": {"message": "The session closed before the estimated context injection completed."}})
    p.js(old, {"type": "session.closed", "reason": "error"})
    p.page.wait_for_timeout(800)
    assert p.phase() == "reading" and p.L("!!L.pc") and not p.marks("dropped")
    assert "context injection" not in p.page.text_content("#transcript")


def test_a_tap_while_the_pause_is_still_closing_asks_in_the_new_session(world):
    """Live check after Ian's third test: a tap right after a pause asked the session still closing ("The session
    is closing and cannot accept new client events") and the question was lost."""
    w = world
    p = start_reading(w)
    read(p, w, "s1-p01", upto=3)
    p.wait("L.feed.cur.matched === 2")
    p.page.click("#play")
    p.wait("L.phase === 'paused' && L.closing", timeout=3000)
    p.page.click("#orb")  # at once, while it closes
    p.wait("L.phase === 'aside' && L.liveId === 'live_2'", timeout=8000)
    tapped = [e for e in p.sent("session.instructions.append") if e["content"] == w.notes["tapped"]]
    assert tapped and all(e["ch"] == 1 for e in tapped)



# --- Ian's fourth test (2026-11-15) ----------------------------------------------------------------------------

def test_an_outright_mic_off_is_woken_only_by_a_tap(world):
    """Ian's fourth test: "can you turn off the microphone" and it came back on while he talked to his son. Asked
    outright, the mic stays off until a tap: no waking on a sound."""
    w = world
    p = connect(w)
    room(p)
    p.hear("Can you turn off the microphone for a moment, please?")
    p.page.wait_for_timeout(400)
    p.say("Sure, I'll be right here.")
    p.wait("L.phase === 'resting' && L.muted", timeout=5000)
    assert p.marks("tap-only")
    p.page.wait_for_timeout(1500)
    assert not p.marks("wake-listening") and p.page.text_content("#how") == "Tap to talk"
    tone(p)
    p.page.wait_for_timeout(2000)
    tone(p, False)
    assert p.phase() == "resting" and not p.marks("sound-wake")
    p.page.click("#orb")
    p.wait("L.phase === 'talk' && !L.muted")


def test_a_rest_waits_its_grace_period_before_listening_for_a_voice(world):
    w = world
    p = connect(w, start=False)
    cue(p, "wake_grace_secs", 2.5)
    room(p)
    p.page.click("#orb")  # rest (not an outright mic-off)
    p.wait("L.phase === 'resting'")
    p.page.wait_for_timeout(1500)
    assert not p.marks("wake-listening")
    tone(p)
    p.page.wait_for_timeout(500)
    assert p.phase() == "resting"  # a sound in the grace period doesn't wake it
    tone(p, False)
    p.wait("window.Live.L.log.some((m) => m[1] === 'wake-listening')", timeout=5000)



# --- Ian's fifth note (2026-11-15): step back while it researches, unless they're chatting ---------------------

def not_engaged(p):
    cue(p, "engaged_turns", 1000)


def test_after_the_start_the_guide_steps_back_and_gpt_live_closes(world):
    w = world
    p = connect(w, start=False)
    not_engaged(p)
    p.wait("Date.now() - L.spokeAt > 900 && !L.out")  # the hello said and done
    start_chapter(p)
    p.wait("window.Live.L.log.some((m) => m[1] === 'after-start' && m[2] === 'step back')")
    note = [x for x in instructions(p) if x.startswith("[Their chapter is under way")]
    assert note and "step back" in note[-1]
    cue(p, "step_back_reply_secs", 1)  # it waits this long for an answer to its offer to chat (8 s in config)
    p.say("It's under way. I'll step back while I research it; speak up or tap my circle any time.")
    p.wait("L.phase === 'resting' && !L.pc && !L.closing", timeout=6000)
    assert any(e["type"] == "session.close" for e in p.sent()) and p.post_bodies("api/live/usage")
    assert p.L("!!L.stream") and p.js("window.__track.enabled") is False  # kept here for the wake listener, sending nothing


def test_after_the_start_an_engaged_listener_is_offered_company(world):
    w = world
    p = connect(w)  # the test config counts everyone as engaged
    p.wait("window.Live.L.log.some((m) => m[1] === 'after-start' && m[2] === 'company')")
    assert instructions(p)[-1].startswith("[Their chapter is under way") and "keep talking" in instructions(p)[-1]
    p.say("It's under way, and I'm happy to keep chatting while I research.")
    p.page.wait_for_timeout(2500)
    assert p.phase() == "talk" and p.L("!!L.pc")


def test_talking_on_after_the_start_keeps_the_guide(world):
    w = world
    p = connect(w, start=False)
    not_engaged(p)
    start_chapter(p)
    p.wait("L.stepBackAt")
    p.hear("Actually, can I ask you something about falcons first?")
    p.page.wait_for_timeout(600)
    p.say("Of course, go ahead.")
    p.page.wait_for_timeout(2500)
    assert p.phase() == "talk" and p.marks("step-back-cancelled")


def test_if_the_guide_never_mentions_stepping_back_it_steps_back_anyway(world):
    w = world
    p = connect(w, start=False)
    not_engaged(p)
    cue(p, "step_back_wait_secs", 1)
    start_chapter(p)
    p.wait("L.phase === 'resting' && !L.pc", timeout=8000)


def test_a_voice_while_resting_opens_a_new_session_that_only_listens(world):
    w = world
    p = connect(w, start=False)
    cue(p, "wake_listen_secs", 1)
    room(p)
    p.page.click("#orb")  # rest: the session closes
    p.wait("L.phase === 'resting' && !L.pc && window.Live.L.log.some((m) => m[1] === 'wake-listening')", timeout=6000)
    p.page.wait_for_timeout(1200)
    tone(p)
    p.wait("L.phase === 'talk' && L.liveId === 'live_2' && L.wokeAt", timeout=6000)
    tone(p, False)
    assert p.post_bodies("api/live/resume")[-1]["reason"] == "wake"
    p.wait("L.phase === 'resting' && !L.pc", timeout=6000)  # nobody spoke: closed again


def test_a_tap_while_resting_opens_a_new_session(world):
    w = world
    p = connect(w, start=False)
    p.page.click("#orb")
    p.wait("L.phase === 'resting' && !L.pc && !L.closing", timeout=6000)
    p.page.click("#orb")
    p.wait("L.phase === 'talk' && L.liveId === 'live_2'", timeout=6000)
    assert p.post_bodies("api/live/resume")[-1]["reason"] == "tap"



def test_a_line_already_under_way_at_the_start_is_not_the_step_back(world):
    """"Alright, I'm on it" said as start_chapter returns isn't the guide stepping back: the mic stays on until
    it has said so (here, the fallback after step_back_wait_secs)."""
    w = world
    p = connect(w, greet=False, start=False)
    not_engaged(p)
    p.say("Alright, I'm")  # mid-line when the tool returns
    start_chapter(p)
    p.say("on it.")
    p.page.wait_for_timeout(1500)
    assert p.phase() == "talk"



# --- regressions from the review of the fifth-note changes -------------------------------------------------------

def test_a_tap_right_after_a_rest_opens_a_new_session_and_never_uses_the_closing_one(world):
    w = world
    p = connect(w, start=False)
    p.page.click("#orb")  # rest: the session is let go of at once
    p.wait("L.phase === 'resting' && !L.pc")
    p.page.click("#orb")  # straight away, while the old one is still closing
    p.wait("L.phase === 'talk' && L.liveId === 'live_2'", timeout=6000)
    late = [e for e in p.sent() if e["ch"] == 0 and e["type"] in ("session.instructions.append", "session.input_audio.unmute")]
    assert not [e for e in late if e["type"] == "session.input_audio.unmute"]
    p.page.wait_for_timeout(1800)  # the old session's close waited for on the side, then its cost reported
    assert any(b["live_id"] == "live_1" for b in p.post_bodies("api/live/usage"))


def test_okay_thanks_after_the_step_back_line_still_steps_back(world):
    w = world
    p = connect(w, start=False)
    not_engaged(p)
    p.wait("Date.now() - L.spokeAt > 900 && !L.out")
    start_chapter(p)
    p.wait("L.stepBackAt")
    p.page.wait_for_timeout(100)
    p.say("I'll step back while I research, unless there's anything else you'd like to chat about?")
    p.wait("L.replyWait", timeout=4000)  # a question, but the step-back line: the page waits for the answer
    p.hear("Okay, thanks.")  # a plain no to chatting
    p.wait("L.phase === 'resting' && !L.pc", timeout=8000)


def test_typed_text_while_resting_brings_the_live_guide_back(world):
    w = world
    p = connect(w, start=False)
    p.page.click("#orb")
    p.wait("L.phase === 'resting' && !L.pc && !L.closing")
    p.page.click("#type")
    p.page.fill("#say", "how's it going?")
    p.page.press("#say", "Enter")
    p.wait("L.phase === 'talk' && L.liveId === 'live_2'", timeout=6000)
    assert instructions(p)[-1] == w.notes["typed"].replace("{text}", "how's it going?") and not p.post_bodies("api/chat")


def test_a_tap_after_a_dropped_connection_says_it_is_back(world):
    w = world
    p = connect(w, start=False)
    dropped(p)
    p.wait("L.phase === 'resting' && !L.pc")
    p.page.click("#orb")
    p.wait("L.phase === 'talk' && L.liveId === 'live_2'", timeout=6000)
    assert p.post_bodies("api/live/resume")[-1]["reason"] == "dropped"


def test_after_an_outright_mic_off_the_ready_announcement_never_opens_a_mic(world):
    w = world
    p = connect(w)
    p.hear("Can you turn off the microphone, please?")
    p.page.wait_for_timeout(400)
    p.say("Sure.")
    p.wait("L.phase === 'resting' && !L.pc && !L.stream", timeout=6000)  # tap only: the mic released
    mics = p.js("window.__mics")
    w.lock(1)
    p.wait("L.offer && L.liveId === 'live_2'", timeout=6000)  # also: ready while the rest's session was still closing
    assert p.js("window.__mics") == mics and p.L("L.mic") is None  # no mic asked for, none sent
    assert p.js("window.__pcs[window.__pcs.length - 1].tracks.length") == 0


def test_a_rest_waits_for_a_tool_call_in_flight(world):
    w = world
    p = connect(w, start=False)
    p.emit({"type": "session.delegation.created"})
    p.wait("L.thinking")
    p.page.click("#orb")  # rest while the backend works
    p.page.wait_for_timeout(1200)
    assert p.phase() == "talk" and p.L("!!L.pc")
    p.emit({"type": "response.event", "event": {"type": "response.completed", "response": {"usage": None}}})
    p.wait("L.phase === 'resting' && !L.pc", timeout=4000)


def test_typed_lines_count_toward_engagement(world):
    w = world
    p = connect(w, start=False)
    cue(p, "engaged_turns", 2)
    cue(p, "engaged_words", 4)
    p.page.click("#type")
    for line in ("I've been building cabinets all week long", "the birch plywood is lovely to work with honestly"):
        p.page.fill("#say", line)
        p.page.press("#say", "Enter")
        p.page.wait_for_timeout(200)
    # each typed line is its own turn, however close together (review, 2026-11-19: they merged into one)
    assert p.js("() => window.Live.L.turnWords.filter((n) => n > 0)") == [7, 9]
    start_chapter(p)
    p.wait("window.Live.L.log.some((m) => m[1] === 'after-start' && m[2] === 'company')")



def test_a_sound_while_a_library_chapter_plays_never_wakes_the_guide_and_listening_goes_on(world):
    """The library below has its own player: its sound isn't the listener. The wake is skipped and the listener
    is armed again (it fires only once), so a real voice after the chapter stops still wakes it."""
    w = world
    p = connect(w, start=False)
    room(p)
    p.js("""() => { let a = document.getElementById('chapter-audio');  // the library's own player (page/library.js)
                    if (!a) { a = document.createElement('audio'); a.id = 'chapter-audio'; document.body.appendChild(a); }
                    Object.defineProperty(a, 'paused', { get: () => !window.__libPlaying }); window.__libPlaying = true; }""")
    p.page.click("#orb")
    p.wait("L.phase === 'resting' && window.Live.L.log.some((m) => m[1] === 'wake-listening')", timeout=6000)
    p.page.wait_for_timeout(1200)
    tone(p)
    p.page.wait_for_timeout(2000)
    tone(p, False)
    assert p.phase() == "resting" and not p.marks("sound-wake")
    p.js("() => { window.__libPlaying = false; }")
    p.wait("window.Live.L.log.filter((m) => m[1] === 'wake-listening').length >= 2", timeout=10000)
    p.page.wait_for_timeout(1200)
    tone(p)
    p.wait("L.phase === 'talk'", timeout=6000)
    tone(p, False)


# --- Ian's notes after his tester run (2026-11-16) -------------------------------------------------------

def resting_with_a_ready_chapter(w):
    p = connect(w)
    p.wait("Date.now() - L.spokeAt > 900")
    p.page.click("#orb")  # talk -> resting
    p.wait("L.phase === 'resting' && !L.pc && !L.closing")
    w.lock(1)
    p.wait("L.offer && L.liveId === 'live_2'", timeout=6000)
    p.say("Your chapter's ready whenever you are. Tap play when you'd like it.")
    p.wait("L.phase === 'resting' && !L.pc && !L.closing", timeout=8000)
    return p


def test_the_circle_never_shows_play_and_never_plays_the_bars_button_does(world):
    """Ian: "The play button ... on the dot ... is a little odd, we should just use the play button near the
    playback slider." A ready chapter, the page resting: no play mark on the circle, the bar's play button is
    visible and enabled, and a tap on the circle talks (it never starts the reading)."""
    w = world
    p = resting_with_a_ready_chapter(w)
    assert p.js("getComputedStyle(document.querySelector('#orb .icon.play')).opacity") == "0"
    assert p.page.is_visible("#play") and p.page.is_enabled("#play")
    assert "play" not in p.page.text_content("#hint").lower() and "tap to play" not in p.page.text_content("#line")
    p.page.click("#orb")
    p.wait("L.phase === 'talk' && L.liveId === 'live_3'", timeout=6000)
    assert p.post_bodies("api/live/resume")[-1]["reason"] == "tap"
    p.page.wait_for_timeout(1200)
    assert p.phase() == "talk" and not p.reads(w.notes) and not p.L("L.readStarted")
    p.say("I'm here.")
    p.wait("L.offer && L.phase === 'talk' && window.Live.L.log.filter((m) => m[1] === 'offer').length >= 2", timeout=8000)
    assert not p.reads(w.notes)  # offered again by voice in the new session, still not read


def test_the_chapters_player_is_a_bar_fixed_to_the_bottom_and_the_librarys_gives_way(world):
    """Ian: "What happened to the fixed playbar?" The live chapter's play, slider and time are a bar fixed to the
    bottom of the screen (the library's #lib-player look); while it shows the library's bar doesn't, and the
    page ends above it."""
    w = world
    p = connect(w)
    assert not p.page.is_visible("#player") and "live-bar" not in p.js("document.body.className")
    p.js("() => { const b = document.getElementById('lib-player'); if (b) b.hidden = false; }")  # a library chapter loaded
    offered(p, w)
    say_yes(p)
    p.page.wait_for_selector("#player:not([hidden])")
    box = p.js("""() => { const el = document.getElementById('player'), r = el.getBoundingClientRect(), lib = document.getElementById('lib-player');
                         return { pos: getComputedStyle(el).position, bottom: Math.round(r.bottom), left: Math.round(r.left),
                                  width: Math.round(r.width), h: el.offsetHeight, vh: innerHeight, vw: document.documentElement.clientWidth,
                                  bar: getComputedStyle(document.documentElement).getPropertyValue('--live-bar-h').trim(),
                                  lib: lib ? getComputedStyle(lib).display : 'none',
                                  pad: parseFloat(getComputedStyle(document.getElementById('library')).paddingBottom) }; }""")
    assert box["pos"] == "fixed" and box["bottom"] == box["vh"] and box["left"] == 0 and box["width"] == box["vw"]
    assert box["h"] > 40 and box["bar"] == f"{box['h']}px" and box["pad"] == 48 + box["h"] and box["lib"] == "none"
    assert "live-bar" in p.js("document.body.className")
    assert p.page.text_content("#play-title") == "The List"
    for sel in ("#play", "#seek", "#time"):
        assert p.js("(sel) => !!document.querySelector('#player ' + sel)", sel) and p.page.is_visible(sel)
    p.js("() => window.scrollTo(0, document.documentElement.scrollHeight)")  # the page's end is above the bar
    assert p.js("document.getElementById('library').getBoundingClientRect().bottom - parseFloat(getComputedStyle(document.getElementById('library')).paddingBottom)"
                " <= document.getElementById('player').getBoundingClientRect().top + 1")
    p.js("() => { window.Live.endConversation(); }")
    p.wait("L.phase === 'idle'")
    p.page.wait_for_timeout(700)
    assert not p.page.is_visible("#player") and "live-bar" not in p.js("document.body.className")
    assert p.js("getComputedStyle(document.documentElement).getPropertyValue('--live-bar-h').trim()") == "0px"


def test_a_tap_back_that_the_guide_meets_with_silence_is_asked_once_to_speak(world):
    """Review of the tap fix: the session now stays open after a tap, but its "they tapped to talk" note is only
    the new session's startup message, which alone may not make GPT-Live speak (a first connect has a fallback
    for exactly that). Nothing said by either side after greet_fallback_ms: once more, as an instruction."""
    w = world
    p = connect(w)
    p.wait("Date.now() - L.spokeAt > 900")
    p.page.click("#orb")
    p.wait("L.phase === 'resting' && !L.pc && !L.closing")
    p.page.click("#orb")
    p.wait("L.phase === 'talk' && L.liveId === 'live_2'", timeout=6000)
    assert p.post_bodies("api/live/resume")[-1]["reason"] == "tap"
    p.page.wait_for_timeout(700)
    assert w.notes["wake"] not in instructions(p)  # not before greet_fallback_ms (1.5 s here)
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(w.notes['wake'])})", timeout=3000)
    p.page.wait_for_timeout(2000)
    assert instructions(p).count(w.notes["wake"]) == 1 and p.phase() == "talk"
    mic_rules(p, w.notes)


def test_a_tap_back_the_guide_answers_gets_no_second_ask(world):
    w = world
    p = connect(w)
    p.wait("Date.now() - L.spokeAt > 900")
    p.page.click("#orb")
    p.wait("L.phase === 'resting' && !L.pc && !L.closing")
    p.page.click("#orb")
    p.wait("L.phase === 'talk' && L.liveId === 'live_2'", timeout=6000)
    p.say("I'm here.")
    p.page.wait_for_timeout(2200)
    assert w.notes["wake"] not in instructions(p)
    # ... nor when the listener has begun to speak first
    p.page.click("#orb")
    p.wait("L.phase === 'resting' && !L.pc && !L.closing")
    p.page.click("#orb")
    p.wait("L.phase === 'talk' && L.liveId === 'live_3'", timeout=6000)
    p.hear("So I was wondering")
    p.page.wait_for_timeout(2200)
    assert w.notes["wake"] not in instructions(p)


def test_a_tap_after_a_drop_that_the_guide_meets_with_silence_is_asked_once_to_speak(world):
    w = world
    p = connect(w)
    p.wait("Date.now() - L.spokeAt > 900")
    dropped(p)
    p.wait("L.phase === 'resting' && !L.pc")
    p.page.click("#orb")
    p.wait("L.phase === 'talk' && L.liveId === 'live_2'", timeout=6000)
    assert p.post_bodies("api/live/resume")[-1]["reason"] == "dropped"
    back = "[" + w.notes["returned"] + "]"
    p.wait(f"window.__sent.some((e) => e.content === {json.dumps(back)})", timeout=4000)
    p.page.wait_for_timeout(1800)
    assert instructions(p).count(back) == 1


def test_the_parts_show_once_the_opening_is_written_before_the_offer_is_made(world):
    """Review: between the opening being written and the offer (which waits for a pause, or for an answer to the
    guide's question) neither the steps nor the parts showed. The parts show from the run's first audio."""
    w = world
    p = connect(w)
    w.status(steps=STEPS)
    p.page.wait_for_selector("#steps:not([hidden])", timeout=5000)
    p.say("So what's the garden like this time of year?")  # a question: the offer waits for its answer
    p.wait("/\\?\\s*$/.test(L.lastGuideLine || '')")
    w.lock(1)
    p.page.wait_for_selector("#sections button", timeout=2000)
    assert not p.L("L.offer") and not p.page.is_visible("#steps") and p.phase() == "talk"
    assert p.js("[...document.querySelectorAll('#sections button')].map((b) => b.dataset.state)") == [
        "ready" if n == 1 else "being_written" for n in TEXTS]
    p.page.wait_for_selector("#player:not([hidden])")  # and the bar: it is ready, and play starts it by hand
    assert p.page.is_enabled("#play")
    p.wait("L.offer", timeout=9000)
    assert p.js("document.getElementById('player').hidden") is False


def test_the_live_bar_leaves_no_room_for_the_librarys_and_clears_the_home_indicator(world):
    """Review: with a library chapter loaded first, #screen's height subtracted both bars though only the live
    one shows; and the bar had no safe-area padding (a phone's home indicator sits over its bottom edge)."""
    w = world
    p = connect(w)
    offered(p, w)
    p.page.wait_for_selector("#player:not([hidden])")
    p.js("() => document.documentElement.style.setProperty('--lib-player-h', '64px')")  # as library.js sets it
    got = p.js("""() => ({ lib: getComputedStyle(document.body).getPropertyValue('--lib-player-h').trim(),
                           bar: document.getElementById('player').offsetHeight,
                           min: parseFloat(getComputedStyle(document.getElementById('screen')).minHeight), vh: innerHeight })""")
    assert got["lib"] == "0px" and abs(got["min"] - (got["vh"] - got["bar"])) < 1
    css = (ROOT / "page" / "live.html").read_text(encoding="utf-8")
    pin = css[css.index("#player .pin {"):]
    assert "env(safe-area-inset-bottom" in pin[:pin.index("}")]
    p.js("() => { window.Live.endConversation(); }")
    p.wait("L.phase === 'idle'")
    p.page.wait_for_timeout(500)
    assert p.js("getComputedStyle(document.body).getPropertyValue('--lib-player-h').trim()") == "64px"  # the library's own again


STEPS = [{"key": "scout", "label": "Looking into it", "state": "done"},
         {"key": "outline", "label": "Planning the chapter", "state": "doing"},
         {"key": "opening", "label": "Writing the opening", "state": "todo"}]


def test_while_the_chapter_is_made_the_steps_show_then_its_parts_and_never_a_card(world):
    """Ian: "we had just a simple progress indicator which was an overview of the different sections ... I didn't
    like seeing so much info about what would be built." While the opening is made: the steps to it (as the
    fast-start page showed them); once it is written: the chapter's parts and where each stands. No card with a
    title, overview and summary, and the page never asks for one."""
    w = world
    write(w.root / "fast" / "card.json", {"run": RUN, "title": "A card", "overview": "An overview.", "summary": "A summary."})
    p = connect(w)
    assert not p.page.is_visible("#steps") and not p.page.is_visible("#sections")
    w.status(steps=STEPS)
    p.page.wait_for_selector("#steps:not([hidden])", timeout=5000)
    assert p.js("[...document.querySelectorAll('#steps li')].map((li) => [li.textContent, li.dataset.state])") == [
        [x["label"], x["state"]] for x in STEPS]
    assert not p.page.is_visible("#sections")
    p.page.wait_for_timeout(1200)
    assert p.js("document.getElementById('card')") is None
    assert "An overview." not in p.page.text_content("main") and "A summary." not in p.page.text_content("main")
    offered(p, w, 1)
    p.page.wait_for_selector("#sections button", timeout=5000)
    assert not p.page.is_visible("#steps")
    assert p.js("[...document.querySelectorAll('#sections button')].map((b) => [b.querySelector('.title').textContent, b.dataset.state])") == [
        [TITLES[n], "ready" if n == 1 else "being_written"] for n in TEXTS]
    say_yes(p)
    p.say(" ".join(TEXTS[1].split()[:4]))
    p.page.wait_for_selector("#sections button[data-state='playing']", timeout=5000)
    assert p.page.text_content("#sections button[data-state='playing'] .state") == "playing"
    assert not [u for u in p.js("performance.getEntriesByType('resource').map((e) => e.name)") if "/api/live/card" in u]


def long_rest(p, secs=2):
    """Resting (the session closed) for longer than talk_rest_secs, as Ian's 2.5 minutes against its 90 s."""
    cue(p, "talk_rest_secs", secs)
    p.page.click("#orb")
    p.wait("L.phase === 'resting' && !L.pc && !L.closing")
    p.page.wait_for_timeout(secs * 1000 + 600)


def test_a_tap_after_a_long_rest_brings_the_guide_back_and_it_stays(world):
    """Ian: "Tap to talk doesn't work once turned off." His transcript: stepped back at 19:56:10, "[A new voice
    session: tap.]" at 19:58:46 and "[The guide stepped back.]" one second later. The long-silence rest
    (talk_rest_secs) measured from the last words before the rest, 156 s old, so the new session rested at once.
    Now it counts from when the guide came back: the guide stays, and rests only after that long in silence."""
    w = world
    p = connect(w)  # the chapter started, not yet ready: Ian's case
    p.wait("Date.now() - L.spokeAt > 900")
    long_rest(p)
    rests = len(p.marks("silence-rest"))
    p.page.click("#orb")
    p.wait("L.phase === 'talk' && L.liveId === 'live_2'", timeout=6000)
    assert p.post_bodies("api/live/resume")[-1]["reason"] == "tap"
    p.page.wait_for_timeout(1200)
    assert p.phase() == "talk" and p.L("!!L.pc") and len(p.marks("silence-rest")) == rests
    p.say("I'm here.")
    p.wait("L.phase === 'resting'", timeout=8000)  # the normal rest, after talk_rest_secs of silence
    assert len(p.marks("silence-rest")) == rests + 1


def test_a_tap_after_a_long_rest_with_the_chapter_ready_brings_the_guide_back_too(world):
    w = world
    p = resting_with_a_ready_chapter(w)
    cue(p, "talk_rest_secs", 3)
    p.page.wait_for_timeout(3600)
    p.page.click("#orb")
    p.wait("L.phase === 'talk' && L.liveId === 'live_3'", timeout=6000)
    p.page.wait_for_timeout(2400)  # past the wait for the guide's line, which holds every cue back
    assert p.phase() == "talk" and p.L("!!L.pc") and not p.marks("silence-rest")


def test_a_sound_after_a_long_rest_wakes_the_guide_for_the_listening_time(world):
    """The same for a wake on sound: it listens for wake_listen_secs, not for no time at all."""
    w = world
    p = connect(w, start=False)
    cue(p, "wake_listen_secs", 3)
    cue(p, "talk_rest_secs", 2)
    room(p)
    p.page.click("#orb")
    p.wait("L.phase === 'resting' && window.Live.L.log.some((m) => m[1] === 'wake-listening')", timeout=6000)
    p.page.wait_for_timeout(2600)
    tone(p)
    p.wait("L.phase === 'talk' && L.wokeAt", timeout=6000)
    tone(p, False)
    p.page.wait_for_timeout(1200)
    assert p.phase() == "talk" and not p.marks("silence-rest")


# --- Ian's third tester run (2026-11-19): the director, merged turns, a real offer to chat, the topic ---------------

# The earpiece's knobs for these tests: it looks when a listener turn ends (every_lines 1), not on time, not on
# silence, with no gap between whispers, unless a test says otherwise.
DIRECTOR = {"enabled": True, "every_secs": 1000, "company_every_secs": 1000, "every_words": 1000, "every_lines": 1,
            "silence_secs": [], "timeout_ms": 1500, "min_gap_secs": 0, "fallback": {"pitch_nudge_secs": [1000, 1000]}}
NO_CHANGE = {"read": "", "plan": [], "next": "", "changed": False, "kind": None, "about": None, "note": None}


def whisper(note, about="pitch", kind="when_it_fits"):
    return {**NO_CHANGE, "changed": True, "kind": kind, "about": about, "note": note}


def director_on(p, answers=(), **knobs):
    """The director on for this page (Live.S.director), POST /api/live/direct answered by the next of `answers`
    (a look's answer, or "error" for a 500); once they run out, no change. Returns the requests' bodies."""
    asked, it = [], iter(answers)

    def handle(route):
        body = json.loads(route.request.post_data or "{}")
        asked.append(body)
        a = next(it, NO_CHANGE)
        if a == "error":
            route.fulfill(status=500, content_type="application/json", body="{}")
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"id": body["id"], **NO_CHANGE, **a}))
    p.page.route("**/api/live/direct", handle)
    cue(p, "director", {**DIRECTOR, **knobs})
    return asked


def logged(p, kind, detail=None):
    return f"window.Live.L.log.some((m) => m[1] === {json.dumps(kind)}" + (f" && m[2] === {json.dumps(detail)})" if detail else ")")


def test_the_earpiece_looks_when_a_listener_turn_ends_and_its_whisper_goes_at_once(world):
    """A listener turn ends (no line of theirs for engaged_merge_ms): the page looks, with its last lines, who is
    speaking and the page's facts; a changed whisper goes at once as the `direct` note."""
    w = world
    p = connect(w, greet=False, start=False)
    asked = director_on(p, [whisper("When the salmon story winds down, say what you do.", about="offer")])
    p.say("Hi there!")
    p.wait("L.lastGuideAt")
    p.hear("Hi, I'm making salmon tonight.")
    p.wait(logged(p, "direct-sent", "offer"), timeout=6000)
    assert instructions(p)[-1] == w.notes["direct"].replace("{note}", "When the salmon story winds down, say what you do.")
    body = asked[0]
    assert body["trigger"] == "turn_end" and body["last_who"] == "listener" and body["session"] == p.L("L.session")
    assert body["speaking"] in ("listener", "guide", "none") and body["silence_ms"] >= 0
    assert body["recent"][-1] == {"who": "listener", "text": "Hi, I'm making salmon tonight."}
    assert body["page"]["explained"] is False and body["page"]["started"] is False and body["page"]["turns"] == 1
    assert p.L("L.dir.offered")


def test_a_whisper_goes_while_the_listener_is_talking_and_after_they_spoke(world):
    """The lull hold and drop-if-spoke killed delivery live (12 decisions, all wait): a when_it_fits whisper goes at
    once even mid-sentence (it is worded for its moment); a "now" one is dropped once they have spoken since the look."""
    w = world
    p = connect(w, greet=False, start=False)
    director_on(p)
    p.say("Hi there!")
    p.wait("L.lastGuideAt && !L.out")
    p.js("() => { window.__emit({type: 'session.input_transcript.delta', delta: ' and then the omega threes'}); "
         "window.Live.L.dir.next = {kind: 'when_it_fits', about: 'pitch', note: 'Pitch the peregrine when it fits.', "
         "askedAt: Date.now() - 2000, at: Date.now()}; }")
    p.wait(logged(p, "direct-sent", "pitch"), timeout=4000)
    assert w.notes["direct"].replace("{note}", "Pitch the peregrine when it fits.") in instructions(p)
    p.page.wait_for_timeout(600)
    inject(p, "start", kind="now", note="They said yes: start it.", asked_ms_ago=3000)
    p.wait(logged(p, "direct-drop", "start"), timeout=4000)
    assert not any("They said yes" in x for x in instructions(p))


def test_the_earpiece_looks_on_its_cadence_only_with_a_new_line(world):
    """cadence: every every_secs with at least one new line since the last look; nothing new, no look."""
    w = world
    p = connect(w, greet=False, start=False)
    asked = director_on(p, every_secs=1.5, every_lines=1000)
    p.say("Hi there!")
    p.wait("window.Live.L.log.some((m) => m[1] === 'direct-ask')", timeout=5000)
    assert asked[0]["trigger"] == "cadence"
    p.page.wait_for_timeout(3000)
    assert len(asked) == 1  # no new line: no look
    p.say("Still there?")
    p.wait("window.Live.L.log.filter((m) => m[1] === 'direct-ask').length >= 2", timeout=5000)
    assert asked[1]["trigger"] == "cadence"


def test_a_turn_end_looks_after_enough_words_or_enough_new_lines(world):
    """turn_end: a listener turn of every_words words, or every_lines new lines since the last look."""
    w = world
    p = connect(w, greet=False, start=False)
    asked = director_on(p, every_words=6, every_lines=1000)
    p.say("Hi there!")
    p.wait("L.lastGuideAt")
    p.hear("Yes.")
    p.page.wait_for_timeout(2500)  # past engaged_merge_ms: one word, two lines
    assert not asked
    p.say("Oh?")
    p.wait("L.lastGuideAt > L.turnEnd")
    p.hear("I was making salmon for the boys tonight")
    p.wait(logged(p, "direct-ask", "turn_end"), timeout=5000)
    cue(p, "director", {**DIRECTOR, "every_words": 1000, "every_lines": 3})
    p.say("Nice.")
    p.wait("L.lastGuideAt > L.turnEnd")
    p.hear("Yeah.")
    p.page.wait_for_timeout(2500)  # two new lines
    assert len(asked) == 1
    p.say("And then?")
    p.wait("L.lastGuideAt > L.turnEnd")
    p.hear("Dessert.")  # four new lines
    p.wait("window.Live.L.log.filter((m) => m[1] === 'direct-ask').length >= 2", timeout=5000)
    assert [a["trigger"] for a in asked] == ["turn_end", "turn_end"]


def test_one_look_in_flight_a_newer_trigger_waits_for_it(world):
    """A look is never aborted for a newer one (it threw the analysis away): the newer trigger waits."""
    w = world
    p = connect(w, greet=False, start=False)
    routes = []

    def handle(route):
        routes.append(route)
    p.page.route("**/api/live/direct", handle)
    cue(p, "director", DIRECTOR)
    p.say("Hi there!")
    p.wait("L.lastGuideAt")
    p.hear("Hi.")
    p.wait("L.dir.ctl", timeout=5000)
    p.say("How's your evening?")
    p.wait("L.lastGuideAt > L.turnEnd")
    p.hear("Busy, the boys are in bed.")
    p.wait("L.dir.queued === 'turn_end'", timeout=5000)
    p.page.wait_for_timeout(400)
    assert len(routes) == 1 and len(p.post_bodies("api/live/direct")) == 1
    body = json.loads(routes[0].request.post_data)
    routes[0].fulfill(status=200, content_type="application/json", body=json.dumps({"id": body["id"], **NO_CHANGE}))
    p.wait("window.Live.L.log.filter((m) => m[1] === 'direct-ask').length >= 2", timeout=5000)
    assert p.marks("direct") and not p.marks("direct-fail")  # the first was answered, not aborted


def test_the_earpiece_looks_once_after_the_started_note(world):
    w = world
    p = connect(w, greet=False, start=False)
    asked = director_on(p)
    p.say("Hi there!")
    p.wait("Date.now() - L.spokeAt > 900 && !L.out")
    start_chapter(p)
    p.wait(logged(p, "direct-ask", "started"), timeout=5000)
    assert p.marks("after-start")[0][0] <= p.marks("direct-ask")[0][0]
    assert asked[0]["trigger"] == "started" and asked[0]["page"]["started"] is True
    p.page.wait_for_timeout(1000)
    assert len(asked) == 1


def test_with_the_director_up_the_hello_clock_is_on_the_offer_clock_off_and_two_failures_bring_it_back(world):
    w = world
    p = connect(w, greet=False, start=False)
    director_on(p, ["error", "error"])
    cue(p, "offer_nudge", {"after_secs": 0.2, "max_secs": 1000, "offered": OFFERED})
    cue(p, "hello_silence_secs", 1)
    p.say("Hi there!")
    p.wait(sent_note(w, "hello_silence"), timeout=5000)  # the page's own clock, director or not
    p.hear("Hi.")
    p.wait(logged(p, "direct-fail"), timeout=6000)
    p.page.wait_for_timeout(500)
    assert w.notes["offer_nudge"] not in instructions(p)  # up: no offer clock
    p.hear("Hello?")
    p.wait(logged(p, "director-down"), timeout=6000)
    # Down: the clocks as they were, for the rest of the conversation (the offer cue at the first lull).
    p.wait(sent_note(w, "offer_nudge"), timeout=8000)


def test_a_healthy_director_that_sends_nothing_never_lets_the_offer_cue_go(world):
    w = world
    p = connect(w, greet=False, start=False)
    asked = director_on(p)
    cue(p, "offer_nudge", {"after_secs": 0.2, "max_secs": 0.5, "offered": OFFERED})
    p.say("Hi there!")
    p.page.wait_for_timeout(500)
    p.hear("Hi, I'm making salmon and I was just thinking about omega threes")
    p.wait(logged(p, "direct-ask"), timeout=5000)
    p.say("Oh, nice.")
    p.wait("L.lastGuideAt > L.turnEnd")
    p.hear("Yes, and the boys are asleep.")
    p.page.wait_for_timeout(3500)
    assert w.notes["offer_nudge"] not in instructions(p) and not p.marks("offer-nudge")
    assert len(asked) >= 2 and not p.marks("director-down")


def test_a_silence_looks_once_and_only_while_nobody_speaks(world):
    w = world
    p = connect(w, greet=False, start=False)
    asked = director_on(p, silence_secs=[1.5], every_lines=1000)
    p.say("Hi there!")
    p.wait("L.lastGuideAt && !L.out")
    for _ in range(12):  # they keep talking for 2.4 s: never a silence
        p.hear("and")
        p.page.wait_for_timeout(200)
    assert not asked
    p.wait(logged(p, "direct-ask", "silence"), timeout=6000)
    assert asked[0]["trigger"] == "silence" and asked[0]["speaking"] == "none" and asked[0]["silence_ms"] >= 1500
    p.page.wait_for_timeout(2500)
    assert len(asked) == 1  # once a stretch


def test_the_director_is_never_asked_while_the_chapter_is_read(world):
    w = world
    p = start_reading(w, 1)
    asked = director_on(p, [whisper("Should never go.")], every_secs=0.5, silence_secs=[0.5])
    read_through(p, w, "s1-p01")
    p.page.wait_for_timeout(1500)
    assert not asked and not p.marks("direct-ask")


def test_the_directors_quiet_whisper_goes_as_direct_now_and_never_rests_the_page_itself(world):
    """They asked for quiet in words the page's own rule misses: a "now" whisper about quiet goes as `direct_now`.
    It used to arm restSoon too, which rested after whatever line the guide was on, a question left hanging (review,
    2026-11-20; DESIGN: the earpiece does not drive restSoon). The guide's own parting words still rest it."""
    w = world
    p = connect(w, greet=False, start=False)
    director_on(p, [whisper("They need quiet for the kids: a few words, then quiet.", about="quiet", kind="now")])
    p.say("Hi there!")
    p.page.wait_for_timeout(700)
    p.hear("I have to deal with the kids for a bit")  # not the quiet rule's words: the director catches it
    p.wait(logged(p, "direct-sent", "quiet"), timeout=6000)
    assert instructions(p)[-1] == w.notes["direct_now"].replace("{note}", "They need quiet for the kids: a few words, then quiet.")
    p.say("Oh sure, so have you ever seen one hunt?")
    p.page.wait_for_timeout(2500)
    assert p.phase() == "talk" and p.L("!!L.pc") and not p.marks("rest-said")
    p.say("Sure, I'll give you some space.")  # its own parting words: the page's reading rests it
    p.wait("L.phase === 'resting' && !L.pc", timeout=6000)


def test_a_started_look_left_over_from_a_step_back_never_fires_after_a_rest_and_a_tap(world):
    """Review, 2026-11-20: afterStart arms the started look, but on the step-back path the page isn't "here" (it waits
    for the answer to the offer to chat), and a rest with nobody answering left it armed: the first tick after a tap,
    minutes later, asked a "started" look ("the guide just called start_chapter"). A rest now forgets it."""
    w = world
    p = connect(w, greet=False, start=False)
    asked = director_on(p)
    not_engaged(p)
    cue(p, "step_back_reply_secs", 1)
    p.say("Hi there!")
    p.wait("Date.now() - L.spokeAt > 900 && !L.out")
    start_chapter(p)
    p.wait(logged(p, "after-start", "step back"))
    p.say("I'll get to work on it, unless you'd like to chat a bit first?")
    p.wait("L.phase === 'resting' && !L.pc && !L.closing", timeout=8000)
    assert p.L("L.dir.event") is None and p.L("L.dir.queued") is None
    p.page.click("#orb")
    p.wait("L.phase === 'talk' && L.liveId === 'live_2'", timeout=6000)
    p.say("Hey, you're back.")
    p.page.wait_for_timeout(2500)
    assert not [a for a in asked if a["trigger"] == "started"]


def test_a_look_says_how_long_ago_the_last_line_ended_while_the_guide_talks_on(world):
    """silence_ms is the time since the last line ended, as last_who is read: it was the time since the last word,
    so a look while the guide talked on said "the listener's line, 0 seconds ago" (review, 2026-11-20)."""
    w = world
    p = connect(w, greet=False, start=False)
    asked = director_on(p, every_words=3, every_lines=1000)
    cue(p, "engaged_merge_ms", 4000)  # the turn_end look comes 4 s after their line, while the guide talks
    p.say("Hi there!")
    p.wait("L.lastGuideAt && !L.out")
    p.hear("I made salmon for the boys tonight")
    p.wait("L.lastListenerAt", timeout=3000)
    for _ in range(30):
        p.say("and")
        p.page.wait_for_timeout(150)
        if asked:
            break
    assert asked and asked[0]["trigger"] == "turn_end" and asked[0]["speaking"] == "guide"
    assert asked[0]["last_who"] == "listener" and asked[0]["silence_ms"] >= 2000


def test_the_earpiece_hears_the_mic_not_only_the_transcript(world):
    """The input transcript runs seconds behind the voice: while the mic still carries their voice, with the
    transcript paused, no turn end or silence look goes and a "now" whisper is held; once the mic is quiet the turn
    end looks (nobody speaking) and the whisper goes (review, 2026-11-20: speaking came from the transcript alone)."""
    w = world
    p = connect(w, greet=False, start=False)
    asked = director_on(p, every_words=3, every_lines=1000, silence_secs=[1.5])
    room(p)
    p.say("Hi there!")
    p.wait("L.lastGuideAt && !L.out")
    p.page.wait_for_timeout(600)  # the room's level learnt
    tone(p)  # still talking, though the transcript has paused
    p.hear("I was making salmon tonight")
    p.page.wait_for_timeout(3000)
    assert not asked
    inject(p, "unanswered", kind="now", note="Answer what he asked about the salmon.")
    p.page.wait_for_timeout(800)
    assert not p.marks("direct-sent") and not p.marks("direct-drop") and p.L("!!L.dir.next")
    tone(p, False)
    p.wait(logged(p, "direct-ask", "turn_end"), timeout=6000)
    for _ in range(20):  # the route's handler runs a moment after the page logs the ask
        if asked:
            break
        p.page.wait_for_timeout(100)
    assert asked[0]["speaking"] == "none"
    p.wait(logged(p, "direct-sent", "unanswered"), timeout=4000)


def test_no_whisper_goes_while_a_tool_call_is_in_flight(world):
    """start_chapter's delegation takes seconds before the page knows the chapter started: a "start" whisper in that
    window (direct_start: "call start_chapter") invited a second call (review, 2026-11-20). Not here while thinking."""
    w = world
    p = connect(w, greet=False, start=False)
    director_on(p)
    p.say("Shall I make it about the peregrine?")
    p.wait("L.lastGuideAt && !L.out")
    p.emit({"type": "session.delegation.created"})
    p.wait("L.thinking")
    inject(p, "start", kind="now", note="They said yes to the peregrine.")
    p.wait(logged(p, "direct-drop", "start"), timeout=4000)
    assert not any("They said yes" in x for x in instructions(p))


def test_a_start_whisper_goes_as_direct_start(world):
    w = world
    p = connect(w, greet=False, start=False)
    director_on(p)
    p.say("Shall I make it about the peregrine?")
    p.wait("L.lastGuideAt && !L.out")
    inject(p, "start", kind="now", note="They said yes to the peregrine.")
    p.wait(logged(p, "direct-sent", "start"), timeout=4000)
    assert instructions(p)[-1] == w.notes["direct_start"].replace("{note}", "They said yes to the peregrine.")


def test_the_directors_verdict(world):
    """directorVerdict, pure: the rules for delivery with any state."""
    w = world
    p = w.open()
    C = {"min_gap_secs": 20}
    base = {"here": True, "heardAt": 100, "listenerSpeaking": False, "sentAt": 0, "explained": False,
            "started": False, "offered": False}

    def verdict(about, kind="when_it_fits", t=60000, **st):
        return p.js("([n, t, st, C]) => window.Live.directorVerdict(n, t, st, C)",
                    [{"kind": kind, "about": about, "note": "x", "askedAt": 1000, "at": 1500}, t, {**base, **st}, C])
    assert verdict("pitch") == "send"
    assert verdict("pitch", here=False) == "drop"  # left the conversation (reading, resting, muted, the handback)
    # No lull hold, no drop-if-spoke, no max age for an ordinary whisper.
    assert verdict("pitch", heardAt=50000, listenerSpeaking=True) == "send"
    assert verdict("habit", t=10 ** 9) == "send"
    # The gap counts from the last whisper only; "now" whispers are exempt.
    assert verdict("habit", sentAt=50000) == "drop" and verdict("habit", sentAt=30000) == "send"
    assert verdict("unanswered", kind="now", sentAt=50000) == "send"
    # Stale after the start: the offer, the pitch, an interest question, a start. Company whispers still go.
    for about in ("offer", "pitch", "interest", "start"):
        assert verdict(about, started=True) == "drop"
        assert verdict(about, kind="now", started=True) == "drop"
    assert verdict("habit", started=True) == "send" and verdict("wrap", started=True) == "send"
    assert verdict("offer", explained=True) == "drop" and verdict("offer", offered=True) == "drop"
    # "now": dropped once the listener has spoken since the look was asked, held while they are mid-utterance.
    assert verdict("start", kind="now", heardAt=1200) == "drop"
    assert verdict("quiet", kind="now", listenerSpeaking=True) == "hold"
    assert verdict("quiet", kind="now") == "send"
    # An interest question is stale once the guide's last line asked something after the look was asked (Pooja).
    assert verdict("interest", guideAskedAt=1200) == "drop" and verdict("interest", guideAskedAt=900) == "send"
    # The offer line that fires the offered look ends in a question at the look's own time: equal is not "since".
    assert verdict("interest", guideAskedAt=1000) == "send"
    assert verdict("pitch", guideAskedAt=1200) == "send"


# --- Pooja's run (2026-11-20): one question before a thin pitch -------------------------------------------------

def asks(asked, trigger):
    return [a for a in asked if a["trigger"] == trigger]


def test_the_earpiece_looks_once_when_the_guide_first_offers(world):
    """Her "Sure" to "Want to hear what I do?" was under every_words and every_lines: no look ran between the offer and
    the guide's own pitch. The guide's first line that offers to say what it does (or says it), after a listener line,
    asks a look of its own, once a conversation; its "interest" question is sent, though the offer line itself ended
    in a question at the very moment the look was asked (review, 2026-11-20: equal times are not "asked since")."""
    w = world
    p = connect(w, greet=False, start=False)
    note = "Once you've explained what you make, ask which bit of the table talk stuck with her."
    asked = director_on(p, [whisper(note, about="interest")], every_lines=1000)
    cue(p, "offer_nudge", {"after_secs": 1000, "max_secs": 1000, "offered": OFFERED})
    p.say("Hi there!")
    p.wait("L.lastGuideAt && !L.out")
    p.hear("Hi. Mostly listening tonight.")
    p.wait("L.lastListenerAt")
    p.say("Want to hear what I do?")
    p.wait(logged(p, "direct-ask", "offered"), timeout=5000)
    for _ in range(20):  # the route's handler runs a moment after the page logs the ask
        if asked:
            break
        p.page.wait_for_timeout(100)
    assert [a["trigger"] for a in asked] == ["offered"] and asked[0]["page"]["started"] is False
    p.wait(logged(p, "direct-sent", "interest"), timeout=4000)
    assert any(note in x for x in instructions(p))
    assert p.js("window.Live.L.dir.askedAt") == p.js("window.Live.L.lastGuideAt")  # the case the strict > protects
    p.say("I make short audio chapters, just for you.")
    p.page.wait_for_timeout(1500)
    assert len(asks(asked, "offered")) == 1


def test_the_offered_question_goes_despite_the_gap_after_an_offer_whisper(world):
    """The earpiece's own offer whisper sets the gap's clock; the guide acts on it within min_gap_secs, and the offered
    look's one question would be dropped as "gap", with the guide pitching before a later look (review, 2026-11-20).
    It is exempt; another whisper inside the gap is still dropped."""
    w = world
    p = connect(w, greet=False, start=False)
    note = "Once you've explained what you make, ask what she'd have said if she had joined in."
    asked = director_on(p, [whisper(note, about="interest")], every_lines=1000, min_gap_secs=20)
    cue(p, "offer_nudge", {"after_secs": 1000, "max_secs": 1000, "offered": OFFERED})
    p.say("Hi there!")
    p.wait("L.lastGuideAt && !L.out")
    p.hear("Politics and culture and history.")
    p.wait("L.lastListenerAt")
    inject(p, "offer", note="Offer to say what you make.")
    p.wait(logged(p, "direct-sent", "offer"), timeout=4000)
    p.say("Want to hear what I do?")
    p.wait(logged(p, "direct-ask", "offered"), timeout=5000)
    p.wait(logged(p, "direct-sent", "interest"), timeout=4000)
    assert any(note in x for x in instructions(p))
    assert p.js("Date.now() - window.Live.L.dir.sentAt") < 20000
    inject(p, "habit", note="Tease them about the shed.")
    p.wait(logged(p, "direct-drop", "habit"), timeout=4000)
    fate = p.js("() => window.Live.L.dirEvents.filter((e) => e.type === 'fate' && e.fate === 'dropped').pop()")
    assert fate["reason"] == "gap"


def test_no_offered_look_once_a_chapter_is_started(world):
    w = world
    p = connect(w, greet=False, start=False)
    asked = director_on(p, every_lines=1000)
    cue(p, "offer_nudge", {"after_secs": 1000, "max_secs": 1000, "offered": OFFERED})
    p.say("Hi there!")
    p.wait("L.lastGuideAt && !L.out")
    p.hear("Hi.")
    p.wait("L.lastListenerAt")
    p.js("() => { window.Live.L.startCalled = true; }")
    p.say("Want to hear what I do?")
    p.page.wait_for_timeout(1500)
    assert not asks(asked, "offered") and p.L("L.dir.offerLook") is False


def test_no_offered_look_before_the_listener_speaks(world):
    """An offer line before any listener line asks nothing; it waits for a later offer line after they speak."""
    w = world
    p = connect(w, greet=False, start=False)
    asked = director_on(p, every_lines=1000)
    cue(p, "offer_nudge", {"after_secs": 1000, "max_secs": 1000, "offered": OFFERED})
    p.say("Hi! I make short audio chapters, just for you.")
    p.page.wait_for_timeout(1500)
    assert not asks(asked, "offered") and p.L("L.dir.offerLook") is False
    p.hear("Oh, okay.")
    p.wait("L.lastListenerAt")
    p.say("Want to hear what I do?")
    p.wait(logged(p, "direct-ask", "offered"), timeout=5000)


def test_an_offered_look_is_kept_in_the_queue(world):
    """With a look in flight, the offered look waits: a turn end or the cadence coming due never replaces it, so the
    next look is the offered one; "started" still replaces it."""
    w = world
    p = connect(w, greet=False, start=False)
    routes = []
    p.page.route("**/api/live/direct", lambda route: routes.append(route))
    cue(p, "director", {**DIRECTOR, "timeout_ms": 20000})  # a look at every listener turn's end, held as long as needed
    cue(p, "offer_nudge", {"after_secs": 1000, "max_secs": 1000, "offered": OFFERED})
    p.say("Hi there!")
    p.wait("L.lastGuideAt")
    p.hear("Hi.")
    p.wait("L.dir.ctl", timeout=5000)  # the turn_end look, held
    p.say("Want to hear what I do?")
    p.wait("L.dir.queued === 'offered'", timeout=5000)
    p.hear("Sure.")
    p.wait("L.dir.turnOpen === false && L.lastListenerAt > L.lastGuideAt", timeout=5000)  # its turn_end came due
    cue(p, "director", {**DIRECTOR, "timeout_ms": 20000, "every_secs": 0.3})  # and the cadence
    p.page.wait_for_timeout(800)
    assert p.L("L.dir.queued") == "offered" and len(routes) == 1
    body = json.loads(routes[0].request.post_data)
    routes[0].fulfill(status=200, content_type="application/json", body=json.dumps({"id": body["id"], **NO_CHANGE}))
    p.wait("window.Live.L.log.filter((m) => m[1] === 'direct-ask').length >= 2", timeout=5000)
    assert p.marks("direct-ask")[1][2] == "offered"
    p.js("() => { window.Live.L.dir.queued = 'offered'; window.Live.L.dir.event = 'started'; }")  # a look in flight
    p.wait("L.dir.queued === 'started'", timeout=3000)


def test_an_interest_whisper_after_a_guide_question_is_dropped(world):
    """The look is asked, the guide asks something before its answer comes back: an "interest" question would land on
    top of the guide's own, so it is dropped ("moved_on"). With no guide question since, it is sent."""
    w = world
    p = connect(w, greet=False, start=False)
    routes = []
    p.page.route("**/api/live/direct", lambda route: routes.append(route))
    cue(p, "director", {**DIRECTOR, "timeout_ms": 20000})
    p.say("Hi there!")
    p.wait("L.lastGuideAt")
    p.hear("Politics and culture and history.")
    p.wait("L.dir.ctl", timeout=5000)
    p.say("Which part of it stuck with you?")
    p.wait("L.lastGuideAt > L.dir.askedAt && !L.out", timeout=5000)
    body = json.loads(routes[0].request.post_data)
    routes[0].fulfill(status=200, content_type="application/json", body=json.dumps(
        {"id": body["id"], **whisper("Ask which bit of the table talk stuck with her.", about="interest")}))
    p.wait(logged(p, "direct-drop", "interest"), timeout=4000)
    fate = p.js("() => window.Live.L.dirEvents.filter((e) => e.type === 'fate' && e.fate === 'dropped').pop()")
    assert fate["reason"] == "moved_on"
    assert not any("table talk" in x for x in instructions(p))
    p.say("That's lovely.")
    p.wait("L.lastGuideLine.startsWith('That') && !L.out", timeout=5000)
    p.page.wait_for_timeout(150)
    inject(p, "interest", note="Ask what she would have said if she had joined in.")
    p.wait(logged(p, "direct-sent", "interest"), timeout=4000)


def test_no_page_note_stamp_is_left_for_the_director():
    """The director's gap was counted from any page note (instruct's stamp, L.noteAt): it is whisper to whisper now."""
    src = (ROOT / "page" / "live.js").read_text(encoding="utf-8")
    assert "noteAt" not in src and "instruct(text, stamp" not in src


def test_the_listeners_fragments_are_one_turn_for_engaged(world):
    """Ian's speech came as "[inhale", "]Yeah ...", so he counted as four short lines and the guide stepped away.
    Lines heard within engaged_merge_ms of each other, with no guide line between, are one turn; sound tags are not
    words; a guide line starts the next turn."""
    w = world
    p = connect(w, start=False)
    p.wait("Date.now() - L.spokeAt > 400 && !L.out")
    p.hear("[inhale")
    p.wait("L.turnEnd")
    p.hear("]Yeah, I'd love that, it sounds great")
    p.wait("window.Live.turns().length && window.Live.turns()[0] === 7")
    p.say("Lovely.")
    p.wait("L.lastGuideAt > L.turnEnd")
    p.hear("Okay.")
    p.wait("window.Live.turns().length === 2")
    assert p.js("window.Live.turns()") == [7, 1]
    cue(p, "engaged_turns", 2)
    cue(p, "engaged_words", 4)
    assert p.js("window.Live.engaged()") is True


def step_back_offered(w, reply_secs=3):
    p = connect(w, start=False)
    not_engaged(p)
    cue(p, "step_back_reply_secs", reply_secs)
    p.wait("Date.now() - L.spokeAt > 900 && !L.out")
    start_chapter(p)
    p.wait("L.stepBackAt")
    p.say("It's under way, on the list. I'll step back while I research; is there anything else you'd like to chat about first?")
    p.wait(logged(p, "step-back-offer"), timeout=5000)
    return p


def test_the_offer_to_chat_before_stepping_back_waits_for_an_answer(world):
    """Ian's third run: the guide offered to chat and the page rested 6 s later. It waits step_back_reply_secs
    of silence for the answer, then rests."""
    w = world
    p = step_back_offered(w, reply_secs=3)
    p.page.wait_for_timeout(1800)
    assert p.phase() == "talk"
    p.wait("L.phase === 'resting' && !L.pc", timeout=6000)


def test_any_real_answer_to_the_offer_keeps_the_company_going(world):
    w = world
    p = step_back_offered(w, reply_secs=1.5)
    p.hear("[inhale")  # a sound is not an answer yet
    p.page.wait_for_timeout(600)
    p.hear("Um")
    p.page.wait_for_timeout(600)
    assert p.L("!!L.replyWait")
    p.hear("Sure, what got you into falcons?")
    p.wait(logged(p, "step-back-cancelled", "chat"), timeout=4000)
    p.say("Oh, their speed, honestly.")
    p.page.wait_for_timeout(3000)
    assert p.phase() == "talk"


def test_a_plain_no_to_the_offer_rests_after_the_guides_few_words(world):
    w = world
    p = step_back_offered(w, reply_secs=20)
    p.hear("No, go ahead.")
    p.wait(logged(p, "step-back-answer", "no"), timeout=4000)
    p.say("Okay, talk soon.")
    p.wait("L.phase === 'resting' && !L.pc", timeout=6000)


def test_the_started_note_names_the_topic(world):
    """Ian asked "What would it be about?" with his yes and never heard: the note after the start carries the
    topic start_chapter was called with, so the guide can say it in a sentence."""
    w = world
    p = connect(w, start=False)
    p.tool("start_chapter", {"topic": "Peregrine falcons and speed", "why": "they lit up", "notes": ""})
    p.wait(f"L.own === {json.dumps(RUN)}")
    p.wait(logged(p, "after-start"))
    note = [x for x in instructions(p) if x.startswith("[Their chapter is under way")][-1]
    assert "{topic}" not in note and "{when}" not in note
    if "{topic}" in w.notes["started_company"]:
        assert "Peregrine falcons and speed" in note


# --- the earpiece view (Ian, 2026-11-20): a floating panel of what the earpiece whispers, and each whisper's fate ----
# The panel (page/earpiece.js) loads only when /api/live/start says this listener has the view on (here, off demo
# mode: live.director.view); the fates are posted to /api/live/direct/fate (the real route) whether it shows or not.

PLAN1 = [{"item": "Say what it does", "state": "open"}, {"item": "Find the spark", "state": "open"}]
PLAN2 = [{"item": "Say what it does", "state": "done"}, {"item": "Find the spark", "state": "open"},
         {"item": "Pitch a chapter", "state": "open"}]
PLAN3 = [{"item": "Say what it does", "state": "done"}, {"item": "Pitch a chapter", "state": "open"}]


def viewing(w, on=True):
    w.cfg["live"]["director"]["view"] = on


def ep_open(w, on=True):
    """A page with the view on or off, its requests' URLs recorded, the session started and the guide's hello said."""
    viewing(w, on)
    p = w.open()
    p.urls = []
    p.page.on("request", lambda r: p.urls.append(r.url))
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    if on:
        p.page.wait_for_function("window.Earpiece && document.getElementById('earpiece')")
    return p


def fates(p):
    return p.post_bodies("api/live/direct/fate")


def wait_fates(p, n, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(fates(p)) >= n:
            return fates(p)
        p.page.wait_for_timeout(50)
    raise AssertionError(f"{len(fates(p))} fate posts, wanted {n}: {fates(p)}")


def deferred_director(p, **knobs):
    """The director on, its looks answered only when the test says (answer())."""
    routes = []
    p.page.route("**/api/live/direct", lambda route: routes.append(route))
    cue(p, "director", {**DIRECTOR, **knobs})
    return routes


def answer(p, routes, n, a):
    for _ in range(200):
        if len(routes) >= n:
            break
        p.page.wait_for_timeout(50)
    assert len(routes) >= n, f"look {n} was never asked"
    body = json.loads(routes[n - 1].request.post_data)
    routes[n - 1].fulfill(status=200, content_type="application/json", body=json.dumps({"id": body["id"], **NO_CHANGE, **a}))
    return body["id"]


def talking(p, ms):
    """The mic hears the listener for `ms` (no words yet): a "now" whisper is held meanwhile."""
    p.js(f"() => {{ window.Live.L.micVoiceAt = Date.now() + {ms}; }}")


def test_without_the_flag_the_earpiece_is_never_loaded(world):
    w = world
    p = ep_open(w, on=False)
    director_on(p, [whisper("When the salmon story winds down, ask what they read.", about="interest")])
    p.say("Hi there!")
    p.wait("L.lastGuideAt")
    p.hear("Hi, I'm making salmon tonight.")
    p.wait(logged(p, "direct-sent", "interest"), timeout=6000)
    assert w.notes["direct"].replace("{note}", "When the salmon story winds down, ask what they read.") in instructions(p)
    assert not any(u.endswith("/earpiece.js") for u in p.urls)
    assert p.js("document.getElementById('earpiece') === null && typeof window.Earpiece === 'undefined'")


def test_with_the_flag_the_panel_shows_each_look_newest_first(world):
    w = world
    p = ep_open(w)
    director_on(p, [
        {**whisper("When the salmon winds down, ask what they read.", about="interest"), "read": "They're cooking.",
         "plan": PLAN1, "next": "Say what it does"},
        {"read": "It told them what it does.", "plan": PLAN2, "next": "Find the spark"},
        {**whisper("Pitch a chapter on salmon runs.", about="pitch"), "read": "Salmon lit them up.", "plan": PLAN3,
         "next": "Pitch a chapter"},
    ])
    p.say("Hi there!")
    p.wait("L.lastGuideAt")
    for i, line in enumerate(["Hi, I'm making salmon tonight.", "Oh, nice.", "I love how they swim upstream."]):
        p.hear(line)
        p.wait(f"window.Live.L.log.filter((m) => m[1] === 'direct').length >= {i + 1}", timeout=6000)
        if i < 2:
            p.say("Go on.")
            p.wait("L.lastGuideAt > L.turnEnd")
    p.wait(logged(p, "direct-sent", "pitch"), timeout=4000)
    looks = p.js("""[...document.querySelectorAll('#earpiece-list > li.look')].map((li) => ({
        id: li.dataset.id, text: li.textContent,
        plan: [...li.querySelectorAll('.plan li')].map((x) => [x.firstChild.textContent, x.dataset.state, x.dataset.change]),
        removed: (li.querySelector('.removed') || {}).textContent || '',
        fate: (li.querySelector('.fate') || {}).textContent || '', fateKind: li.querySelector('.fate') ? li.querySelector('.fate').dataset.fate : null }))""")
    assert [x["id"] for x in looks] == ["3", "2", "1"]  # newest first
    first, second, third = looks[2], looks[1], looks[0]
    assert "turn_end" in first["text"] and "speaking" in first["text"] and "They're cooking." in first["text"]
    assert first["plan"] == [["Say what it does", "open", ""], ["Find the spark", "open", ""]]
    assert "Next: Say what it does" in first["text"]
    assert first["fateKind"] == "sent" and re.match(r"Sent to the guide at \d+:\d\d", first["fate"])
    assert second["plan"] == [["Say what it does", "done", "done"], ["Find the spark", "open", ""],
                              ["Pitch a chapter", "open", "new"]]
    assert second["fateKind"] is None  # no note
    assert third["removed"] == "removed: Find the spark"
    assert "Pitch a chapter on salmon runs." in third["text"] and third["fateKind"] == "sent"
    head = p.js("document.getElementById('earpiece-head').textContent")
    assert "3 looks" in head and "2 sent" in head and p.L("L.session") in head
    p.say("Have you ever heard about the salmon runs in Alaska?")
    assert "“Go on.”" in p.js("document.querySelector('#earpiece-list > li.look[data-id=\"1\"] .reply').textContent")
    p.page.wait_for_function("document.querySelector('#earpiece-list > li.look[data-id=\"3\"] .reply')", timeout=5000)
    reply = p.js("document.querySelector('#earpiece-list > li.look[data-id=\"3\"] .reply').textContent")
    assert re.match(r"Guide began \d+\.\d s after: “Have you ever heard about the salmon runs in Alaska\?”", reply)
    # the server has each fate, in the shape the history reads
    assert [(f["id"], f["fate"]) for f in wait_fates(p, 2)] == [(1, "sent"), (3, "sent")]


def test_held_dropped_replaced_and_forgotten_fates_are_shown_and_posted(world):
    w = world
    p = ep_open(w)
    routes = deferred_director(p, every_secs=1, every_lines=1000)
    p.say("Hi there!")
    # a "now" whisper while the listener is mid-line: held, then sent once they stop (one held post)
    p.wait("L.dir.ctl", timeout=5000)
    talking(p, 1500)
    a = answer(p, routes, 1, whisper("They asked what it is: answer that.", about="unanswered", kind="now"))
    p.wait(logged(p, "direct-sent", "unanswered"), timeout=6000)
    got = wait_fates(p, 2)
    assert [(f["id"], f["fate"]) for f in got] == [(a, "held"), (a, "sent")]
    assert got[1]["held_ms"] > 0 and got[0]["held_ms"] is None and got[1]["session"] == p.L("L.session")
    # within min_gap_secs of the last whisper: dropped, "gap", with how long after
    cue(p, "director", {**DIRECTOR, "every_secs": 1, "every_lines": 1000, "min_gap_secs": 30})
    p.say("Mm.")
    b = answer(p, routes, 2, whisper("Ask what they read.", about="interest"))
    gap = wait_fates(p, 3)[2]
    assert gap["id"] == b and gap["fate"] == "dropped" and gap["reason"] == "gap"
    assert re.fullmatch(r"\d+\.\d s after the last", gap["detail"])
    # a held note a newer look replaces
    cue(p, "director", {**DIRECTOR, "every_secs": 1, "every_lines": 1000, "min_gap_secs": 0})
    p.say("Right.")
    p.wait("L.dir.ctl", timeout=5000)
    talking(p, 8000)
    c = answer(p, routes, 3, whisper("They said yes: start it.", about="start", kind="now"))
    wait_fates(p, 4)
    p.say("Okay.")
    d = answer(p, routes, 4, whisper("Ask about the boys.", about="interest"))
    got = wait_fates(p, 6)
    replaced = next(f for f in got if f["id"] == c and f["fate"] == "dropped")
    assert replaced["reason"] == "replaced" and replaced["held_ms"] > 0
    assert any(f["id"] == d and f["fate"] == "sent" for f in got)
    # a rest with a note pending: forgotten, "rest"
    p.say("Hmm.")
    p.wait("L.dir.ctl", timeout=5000)
    talking(p, 8000)
    e = answer(p, routes, 5, whisper("Quiet now: they asked.", about="quiet", kind="now"))
    p.wait("L.dir.next && L.dir.next.heldAt", timeout=4000)
    p.page.click("#orb")  # a tap in talk rests
    got = wait_fates(p, 8)
    forgot = next(f for f in got if f["id"] == e and f["fate"] == "dropped")
    assert forgot["reason"] == "forgotten" and forgot["detail"] == "rest"
    assert sum(1 for f in got if f["fate"] == "held") == 3  # once per note, never every tick
    # the panel shows each
    shown = p.js("Object.fromEntries([...document.querySelectorAll('#earpiece-list > li.look .fate')]"
                 ".map((f) => [f.closest('li').dataset.id, [f.dataset.fate, f.dataset.reason, f.textContent]]))")
    assert shown[str(a)][0] == "sent" and shown[str(a)][2].startswith("Held ")
    assert shown[str(b)][:2] == ["dropped", "gap"] and "after the last" in shown[str(b)][2]
    assert shown[str(c)][:2] == ["dropped", "replaced"]
    assert shown[str(d)][0] == "sent"
    assert shown[str(e)][:2] == ["dropped", "forgotten"] and "(rest)" in shown[str(e)][2]
    assert p.js("[...document.querySelectorAll('#earpiece-list > li.forget')].some((x) => /Pending note forgotten \\(rest\\)/.test(x.textContent))")


def test_fates_are_posted_without_the_panel(world):
    w = world
    p = ep_open(w, on=False)
    director_on(p, [whisper("Ask what they read.", about="interest"), whisper("Pitch salmon runs.", about="pitch")],
                min_gap_secs=30)
    p.say("Hi there!")
    p.wait("L.lastGuideAt")
    p.hear("Hi, I'm making salmon tonight.")
    p.wait(logged(p, "direct-sent", "interest"), timeout=6000)
    p.say("Nice.")
    p.wait("L.lastGuideAt > L.turnEnd")
    p.hear("Yeah, with dill.")
    p.wait(logged(p, "direct-drop", "pitch"), timeout=6000)
    got = wait_fates(p, 2)
    assert [(f["id"], f["fate"], f["reason"]) for f in got] == [(1, "sent", None), (2, "dropped", "gap")]
    assert not any(u.endswith("/earpiece.js") for u in p.urls)


def test_errors_and_the_earpiece_going_down_show_plainly(world):
    w = world
    p = ep_open(w)
    director_on(p, ["error", "error"])
    p.say("Hi there!")
    p.wait("L.lastGuideAt")
    p.hear("Hi.")
    p.wait(logged(p, "direct-fail"), timeout=6000)
    p.say("How are you?")
    p.wait("L.lastGuideAt > L.turnEnd")
    p.hear("Fine.")
    p.wait(logged(p, "director-down"), timeout=6000)
    p.page.wait_for_function("document.querySelectorAll('#earpiece-list > li.fail').length === 2")
    assert p.js("[...document.querySelectorAll('#earpiece-list > li.fail')].every((x) => /Look failed: /.test(x.textContent))")
    assert p.js("/Earpiece down: the page's clocks have taken over/.test(document.querySelector('#earpiece .down').textContent)")
    assert "down" in p.js("document.getElementById('earpiece-head').textContent")


def overlaps(p, a, b):
    return p.js("([a, b]) => { const r = document.getElementById(a).getBoundingClientRect(), s = document.getElementById(b).getBoundingClientRect();"
                " return !(r.right <= s.left || s.right <= r.left || r.bottom <= s.top || s.bottom <= r.top); }", [a, b])


def test_the_panel_folds_and_never_covers_the_circle_end_demo_or_the_play_bar(world):
    w = world
    p = ep_open(w)
    p.js("""() => { for (let i = 1; i <= 30; i++) {
        const base = {session: window.Live.L.session, at: Date.now(), secs: i};
        Earpiece.event({...base, type: 'ask', id: 100 + i, trigger: 'cadence', speaking: 'none'});
        Earpiece.event({...base, type: 'look', id: 100 + i, got: {read: 'A fairly long read of the moment, number ' + i,
          plan: [{item: 'Say what it does', state: 'done'}, {item: 'Find the spark', state: 'open'}], next: 'Find the spark',
          changed: true, kind: 'when_it_fits', about: 'interest', note: 'Ask what they read when it fits.'}});
      }
      const pl = document.getElementById('player'); pl.hidden = false; pl.style.display = 'block';  // render() may re-hide
      document.getElementById('end-demo').style.display = 'inline-block'; }""")
    for size in ({"width": 375, "height": 812}, {"width": 1280, "height": 800}):
        p.page.set_viewport_size(size)
        p.js("() => window.dispatchEvent(new Event('resize'))")
        p.page.wait_for_timeout(100)
        for other in ("orb", "end-demo", "player"):
            assert p.js(f"!!document.getElementById('{other}').getBoundingClientRect().height"), other
            assert not overlaps(p, "earpiece", other), (size, other)
        r = p.js("(() => { const r = document.getElementById('earpiece').getBoundingClientRect(); return [r.left, r.right, innerWidth]; })()")
        assert r[0] >= 0 and r[1] <= r[2] - 11, r  # inside the screen, a gutter on each side
        widths = p.js("""(() => { const e = document.getElementById('earpiece'), d = document.documentElement;
            const w = d.scrollWidth; e.style.display = 'none'; const w0 = d.scrollWidth; e.style.display = ''; return [w, w0]; })()""")
        assert widths[0] == widths[1]  # it adds no horizontal scroll
        assert p.js("document.querySelectorAll('#earpiece-list > li.look').length") == 30
    head = "#earpiece-head"
    assert p.js(f"document.querySelector('{head}').getAttribute('aria-expanded')") == "true"
    p.page.click(head)
    assert p.js(f"document.querySelector('{head}').getAttribute('aria-expanded')") == "false"
    assert p.js("getComputedStyle(document.getElementById('earpiece-list')).display") == "none"
    p.page.click(head)
    assert p.js("getComputedStyle(document.getElementById('earpiece-list')).display") != "none"


def test_the_panel_refits_when_a_bar_appears_with_no_event(world):
    """Review (2026-11-20): the panel fitted only on its own events, a resize or a scroll, so a play bar shown by
    live.js's render tick (or the library's bar) could sit under it for minutes. It refits on a timer too."""
    w = world
    p = ep_open(w)
    p.page.set_viewport_size({"width": 375, "height": 812})
    p.js("""() => { for (let i = 1; i <= 30; i++) {
        const base = {session: window.Live.L.session, at: Date.now(), secs: i};
        Earpiece.event({...base, type: 'ask', id: 200 + i, trigger: 'cadence', speaking: 'none'});
        Earpiece.event({...base, type: 'look', id: 200 + i, got: {read: 'A fairly long read of the moment, number ' + i,
          plan: [{item: 'Find the spark', state: 'open'}], next: 'Find the spark', changed: false}});
      }
      document.getElementById('orb').style.display = 'none';      // out of the way: the panel reaches the bottom
      window.dispatchEvent(new Event('resize')); }""")
    p.page.wait_for_timeout(100)
    p.js("() => { const pl = document.getElementById('player'); pl.hidden = false; pl.style.display = 'block'; }")  # no event
    p.page.wait_for_timeout(1200)
    assert p.js("!!document.getElementById('player').getBoundingClientRect().height")
    assert not overlaps(p, "earpiece", "player")


def test_a_look_answered_after_the_conversation_ended_lands_on_its_own_card(world):
    """Review (2026-11-20): a look in flight when the conversation ended came back with L.session null, so the panel
    made a phantom "? · ? speaking" card and left the real one at "Looking…". Its events carry the session it was
    asked in; nothing is posted for it (the conversation is over), and the page decides as before."""
    w = world
    p = ep_open(w)
    director_on(p)
    p.js("""() => { window.__held = []; const f = window.fetch;
        window.fetch = (u, o) => {
          if (!/\\/api\\/live\\/direct$/.test(String(u))) return f(u, o);
          const id = JSON.parse(o.body).id;
          return new Promise((res) => window.__held.push(() => res(new Response(JSON.stringify({id, read: 'Late.',
            plan: [], next: null, changed: true, kind: 'when_it_fits', about: 'interest', note: 'Ask what they read.'}),
            {status: 200, headers: {'Content-Type': 'application/json'}}))));
        }; }""")
    sid = p.L("L.session")
    p.say("Hi there!")
    p.wait("L.lastGuideAt")
    p.hear("Hi, I'm making salmon tonight.")
    p.page.wait_for_function("window.__held.length === 1", timeout=6000)
    p.js("() => window.Live.endConversation()")
    p.wait("L.phase === 'idle' && !L.session")
    n = len(fates(p))
    p.js("() => window.__held[0]()")
    p.page.wait_for_function("[...document.querySelectorAll('#earpiece-list > li.look .fate')].some((e) => e.dataset.fate === 'dropped')",
                             timeout=4000)
    cards = p.js("[...document.querySelectorAll('#earpiece-list > li.look')].map((e) => [e.dataset.session, e.innerText])")
    assert len(cards) == 1 and cards[0][0] == sid, cards
    assert "Looking" not in cards[0][1] and "? speaking" not in cards[0][1] and "Ask what they read." in cards[0][1]
    p.page.wait_for_timeout(400)
    assert len(fates(p)) == n  # the conversation is over: nothing posted


def _scenario(p, w, on, throwing=False):
    """One conversation on this page (one browser per test: a second sync Playwright can't start), the view on or
    off; what it sent and marked, from its start."""
    viewing(w, on)
    if p.phase() != "idle":
        p.js("() => window.Live.endConversation()")
        p.wait("L.phase === 'idle'")
    k_log, k_sent, k_fates = len(p.L("L.log")), len(instructions(p)), len(fates(p))
    p.page.click("#orb")
    p.wait("L.started && L.phase === 'talk'")
    if on:
        p.page.wait_for_function("window.Earpiece && document.getElementById('earpiece')")
    else:
        p.page.wait_for_function("!window.Earpiece && !document.getElementById('earpiece')")
    if throwing:
        p.js("() => { window.Earpiece.event = () => { throw new Error('the panel broke'); }; }")
    p.page.unroute("**/api/live/direct")
    director_on(p, [whisper("Ask what they read.", about="interest"), {"read": "Fine.", "plan": PLAN1},
                    whisper("Pitch salmon runs.", about="pitch")], min_gap_secs=30)
    p.say("Hi there!")
    p.wait("L.lastGuideAt")
    for i, line in enumerate(["Hi, I'm making salmon tonight.", "With dill.", "And lemon."]):
        p.hear(line)
        p.wait(f"window.Live.L.log.slice({k_log}).filter((m) => m[1] === 'direct').length >= {i + 1}", timeout=6000)
        p.page.wait_for_timeout(400)
        if i < 2:
            p.say("Mm.")
            p.wait("L.lastGuideAt > L.turnEnd")
    marks = [(m[1], m[2]) for m in p.L("L.log")[k_log:] if str(m[1]).startswith("direct")]
    wait_fates(p, k_fates + 2)
    return instructions(p)[k_sent:], marks, [(f["id"], f["fate"], f["reason"]) for f in fates(p)[k_fates:]]


def test_the_panel_changes_nothing(world):
    w = world
    p = w.open()
    p.urls = []
    off = _scenario(p, w, False)
    on = _scenario(p, w, True)
    assert on == off
    assert ("direct-sent", "interest") in on[1] and ("direct-drop", "pitch") in on[1]
    assert on[2] == [(1, "sent", None), (3, "dropped", "gap")]
    assert _scenario(p, w, True, throwing=True) == off  # a throwing panel: the whisper still goes
    assert _scenario(p, w, False) == off  # and turned off again, the panel is gone


def test_directorWhy_names_each_drop_and_nothing_else(world):
    w = world
    p = connect(w, start=False)
    t = 100000
    base = {"here": True, "heardAt": 0, "listenerSpeaking": False, "sentAt": 0, "explained": False, "started": False,
            "offered": False}
    C = {"min_gap_secs": 20}
    table = [
        ({"about": "pitch", "kind": "when_it_fits", "askedAt": t}, {}, "send", None),
        ({"about": "pitch", "kind": "when_it_fits", "askedAt": t}, {"here": False}, "drop", "not_here"),
        ({"about": "pitch", "kind": "when_it_fits", "askedAt": t}, {"started": True}, "drop", "stale_after_start"),
        ({"about": "start", "kind": "now", "askedAt": t}, {"started": True}, "drop", "stale_after_start"),
        ({"about": "habit", "kind": "when_it_fits", "askedAt": t}, {"started": True}, "send", None),
        ({"about": "offer", "kind": "when_it_fits", "askedAt": t}, {"explained": True}, "drop", "explained"),
        ({"about": "offer", "kind": "when_it_fits", "askedAt": t}, {"offered": True}, "drop", "explained"),
        ({"about": "unanswered", "kind": "now", "askedAt": t - 10}, {"heardAt": t - 5}, "drop", "listener_spoke"),
        ({"about": "unanswered", "kind": "now", "askedAt": t}, {"listenerSpeaking": True}, "hold", None),
        ({"about": "unanswered", "kind": "now", "askedAt": t}, {"sentAt": t - 1000}, "send", None),
        ({"about": "interest", "kind": "when_it_fits", "askedAt": t}, {"sentAt": t - 1000}, "drop", "gap"),
        ({"about": "interest", "kind": "when_it_fits", "askedAt": t}, {"sentAt": t - 30000}, "send", None),
        # The offered look's question is exempt from the gap; another trigger's, or another about, is not.
        ({"about": "interest", "kind": "when_it_fits", "askedAt": t, "trigger": "offered"}, {"sentAt": t - 1000}, "send", None),
        ({"about": "pitch", "kind": "when_it_fits", "askedAt": t, "trigger": "offered"}, {"sentAt": t - 1000}, "drop", "gap"),
        ({"about": "interest", "kind": "when_it_fits", "askedAt": t, "trigger": "turn_end"}, {"sentAt": t - 1000}, "drop", "gap"),
        ({"about": "interest", "kind": "when_it_fits", "askedAt": t, "trigger": "offered"}, {"guideAskedAt": t + 5}, "drop", "moved_on"),
    ]
    for n, st, verdict, why in table:
        got = p.js("([n, t, st, C]) => [window.Live.directorVerdict(n, t, st, C), window.Live.directorWhy(n, t, st, C)]",
                   [n, t, {**base, **st}, C])
        assert got == [verdict, why], (n, st, got)
    cue(p, "director", DIRECTOR)
    p.wait("L.lastGuideAt && L.dc && L.dc.readyState === 'open'")
    assert p.js("[window.Live.dirHere(), window.Live.dirHereWhy()]") == [True, ""]
    p.js("() => { window.Live.L.muted = true; }")
    assert p.js("[window.Live.dirHere(), window.Live.dirHereWhy()]") == [False, "muted"]
    p.js("() => { window.Live.L.muted = false; window.Live.L.phase = 'resting'; }")
    assert p.js("[window.Live.dirHere(), window.Live.dirHereWhy()]") == [False, "phase resting"]
