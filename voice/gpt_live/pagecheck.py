"""Live checks of the real page (page/live.html) against the real GPT-Live, in headless Chromium.

    python -m voice.gpt_live.pagecheck smoke   [--source <run folder>] [--paragraphs N]
    python -m voice.gpt_live.pagecheck read    --source <run folder>           # check 7/8: a chapter read live
    python -m voice.gpt_live.pagecheck asks    --source <run folder>           # check 8/9: interruptions

The server runs in this process (server.create_app) on a scratch runs folder; the chapter is a copy
of a run's locked sections, written in step as a fast run would write them (status.json with the
sections, voice "live"). The listener is simulated: their lines are spoken by OpenAI's TTS into a fake
microphone (an AudioContext stream that replaces getUserMedia), so the page's own WebRTC session
carries them to GPT-Live. Nothing reaches the listener's memory (the memory pass is switched off)
or the real runs/ folder. Results: voice/gpt_live/_runs/<time>-<scenario>/ (gitignored): the page's
log, the transcript, the reading check, and a summary. Costs go to voice/gpt_live/build_costs.jsonl.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import statistics
import threading
import time
from pathlib import Path

import httpx

from stages.common import ROOT, load_config
from voice.gpt_live import session as live_session

HERE = Path(__file__).parent
RUNS_OUT = HERE / "_runs"
CACHE = HERE / "_cache"
COSTS = HERE / "build_costs.jsonl"
TTS_PER_MIN = 0.015  # gpt-4o-mini-tts, about $0.015 a minute of audio (OpenAI pricing page, 2026-11-13)

FAKE_MIC = """
(() => {
  const ctx = new AudioContext({ sampleRate: 24000 });
  const dest = ctx.createMediaStreamDestination();
  const hum = ctx.createConstantSource(); hum.offset.value = 0; hum.connect(dest); hum.start();
  window.__said = 0;
  window.__say = (b64) => new Promise((done) => {
    const bin = atob(b64), n = bin.length >> 1;
    const buf = ctx.createBuffer(1, n, 24000), d = buf.getChannelData(0);
    for (let i = 0; i < n; i++) { let v = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8); if (v >= 32768) v -= 65536; d[i] = v / 32768; }
    const src = ctx.createBufferSource(); src.buffer = buf; src.connect(dest);
    src.onended = () => { window.__said++; done(true); }; src.start();
  });
  // A fresh track each time, as a real browser gives (the page stops its tracks when a pause closes the session)
  navigator.mediaDevices.getUserMedia = async () => { await ctx.resume(); return new MediaStream([dest.stream.getAudioTracks()[0].clone()]); };
})();
"""


def spend() -> float:
    if not COSTS.exists():
        return 0.0
    return sum(json.loads(x).get("usd", 0) for x in COSTS.read_text().splitlines() if x.strip())


def log_cost(what: str, usd: float, **extra) -> None:
    with open(COSTS, "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "what": what, "usd": round(usd, 4), **extra}) + "\n")


def tts(text: str, voice: str = "ash") -> bytes:
    CACHE.mkdir(exist_ok=True)
    import hashlib
    f = CACHE / (hashlib.sha1(f"{voice}|{text}".encode()).hexdigest()[:20] + ".pcm")
    if f.exists():
        return f.read_bytes()
    res = httpx.post("https://api.openai.com/v1/audio/speech", headers={"Authorization": f"Bearer {live_session.api_key()}"},
                     json={"model": "gpt-4o-mini-tts", "voice": voice, "input": text, "response_format": "pcm",
                           "instructions": "Speak casually, like a person talking to a friend."}, timeout=60)
    res.raise_for_status()
    f.write_bytes(res.content)
    log_cost("tts", len(res.content) / 48000 / 60 * TTS_PER_MIN, chars=len(text))
    return res.content


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class World:
    """A scratch runs folder with one live fast run, its status written as the run would write it.
    The run has no status.json until `adopt()`: the page reads only the chapter its conversation started
    (start_chapter's run), so a scenario adopts it as start_chapter would, then locks sections."""

    def __init__(self, out: Path, source: Path, name: str = "live-check", paragraphs: int | None = None,
                 sections: int | None = None):
        self.runs = out / "runs"
        self.root = self.runs / name
        self.name = name
        (self.root / "06_segments").mkdir(parents=True, exist_ok=True)
        if (source / "05_outline.json").exists():
            outline = json.loads((source / "05_outline.json").read_text(encoding="utf-8"))
        else:  # an overnight chapter: its final script, split at the segment markers
            from stages.common import split_script_segments
            segs = split_script_segments((source / "09_script_final.md").read_text(encoding="utf-8"))
            outline = {"title": source.name, "sections": [{"n": n, "title": f"Part {n}", "lands": ""} for n, _ in segs]}
            source = self.root / "_source"
            (source / "06_segments").mkdir(parents=True, exist_ok=True)
            for n, text in segs:
                (source / "06_segments" / f"seg{n:02d}.md").write_text(text, encoding="utf-8")
        if sections:
            outline["sections"] = outline["sections"][:sections]
        self.outline = outline
        self.texts = {}
        for s in outline["sections"]:
            p = source / "06_segments" / f"seg{s['n']:02d}.md"
            if p.exists():
                text = p.read_text(encoding="utf-8").strip()
                if paragraphs:
                    text = "\n\n".join([x for x in text.split("\n\n") if x.strip()][:paragraphs])
                self.texts[s["n"]] = text
        for f in ("05_outline.json",):
            (self.root / f).write_text(json.dumps(outline, indent=1), encoding="utf-8")
        (self.root / "fast.json").write_text(json.dumps({"mode": "fast", "voice": "live", "seed_set": time.time()}))
        self.locked: list[int] = []

    def lock(self, n: int, text: str | None = None) -> None:
        (self.root / "06_segments" / f"seg{n:02d}.md").write_text(text or self.texts[n], encoding="utf-8")
        self.locked.append(n)
        self.status()

    def status(self, waiting_for=None, check_in=None, eta=None, state="making") -> None:
        secs = [{"n": s["n"], "title": s["title"], "state": "ready" if s["n"] in self.locked else "being_written",
                 "text": f"06_segments/seg{s['n']:02d}.md" if s["n"] in self.locked else None,
                 "pieces": [], "ready_in": None}
                for s in self.outline["sections"]]
        st = {"run": self.name, "state": state, "stage": "live", "label": "Writing your chapter", "stages_done": len(self.locked),
              "stages_total": len(secs), "minutes_left": None, "title": self.outline["title"], "audio": None,
              "message": None, "detail": None, "started": "", "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "history": [], "pid": os.getpid(), "retryable": True, "mode": "fast", "voice": "live",
              "sections": secs, "first_audio": bool(self.locked), "waiting_for": waiting_for,
              "eta_seconds": eta, "check_in": check_in}
        (self.root / "status.json").write_text(json.dumps(st), encoding="utf-8")


class Harness:
    def __init__(self, scenario: str, source: Path, paragraphs: int | None, sections: int | None, cap: float):
        self.cfg = load_config()
        self.cfg["listener_dir"] = str((RUNS_OUT / "_listener").relative_to(ROOT))
        if spend() >= cap:
            raise SystemExit(f"The build has spent ${spend():.2f} of its ${cap:.2f} cap.")
        self.out = RUNS_OUT / f"{time.strftime('%Y%m%d-%H%M%S')}-{scenario}"
        self.out.mkdir(parents=True)
        self.world = World(self.out, source, paragraphs=paragraphs, sections=sections) if source else None
        self.t0 = time.time()
        self.events: list[dict] = []
        self.run = None

    def serve(self):
        import uvicorn

        import server
        from stages import memory
        memory.after_session = lambda *a, **k: False  # nothing reaches the listener's memory
        memory.run_pending = lambda *a, **k: None
        # A real fast run (check 12) is made in the worktree's own runs/ (gitignored), where cli.py writes it.
        pipe = server.Pipeline(runs_dir=self.world.runs if self.world else ROOT / "runs")
        self.pipe = pipe
        app = server.create_app(pipe, self.cfg, remember_on_start=False)
        self.port = free_port()
        self.srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error"))
        threading.Thread(target=self.srv.run, daemon=True).start()
        while not self.srv.started:
            time.sleep(0.05)

    def open(self):
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required",
                                                     "--use-fake-ui-for-media-stream"])
        self.page = self.browser.new_page()
        self.page.add_init_script(FAKE_MIC)
        self.page.on("console", lambda m: self.events.append({"t": self.now(), "console": m.text}) if m.type in ("error", "warning") else None)
        self.page.goto(f"http://127.0.0.1:{self.port}/")
        self.page.wait_for_function("window.Live")

    def live_ids(self) -> set[str]:
        return {m[2] for m in self.page.evaluate("window.Live.L.log") if m[1] == "started" and m[2]}

    def now(self) -> float:
        return round(time.time() - self.t0, 2)

    def L(self, expr: str):
        return self.page.evaluate(f"(() => {{ const L = window.Live.L; return {expr}; }})()")

    def phase(self) -> str:
        return self.page.evaluate("window.Live.state()")

    def wait(self, cond: str, timeout: float, what: str) -> bool:
        # The check's own cap (config live.checks.run_cap_usd, at $0.05 a connected minute): stop there.
        cap = float((self.cfg["live"].get("checks") or {}).get("run_cap_usd") or 0)
        if cap and time.time() - self.t0 > cap / float(self.cfg["live"]["price_per_minute"]) * 60:
            raise RuntimeError(f"stopped at the check's ${cap:.2f} cap (live.checks.run_cap_usd)")
        end = time.time() + timeout
        while time.time() < end:
            if self.page.evaluate(f"(() => {{ const L = window.Live.L; return !!({cond}); }})()"):
                return True
            self.page.wait_for_timeout(200)
        self.events.append({"t": self.now(), "timeout": what})
        print(f"  [{self.now()}] timed out: {what}", flush=True)
        return False

    def quiet(self, secs: float = 1.5, timeout: float = 30) -> None:
        """Until the guide has said nothing for `secs`."""
        self.wait(f"Date.now() - L.spokeAt > {int(secs * 1000)} && !L.out", timeout, "the guide to go quiet")

    def say(self, text: str) -> None:
        pcm = tts(text)
        print(f"  [{self.now()}] listener: {text}", flush=True)
        self.events.append({"t": self.now(), "listener": text})
        self.page.evaluate("(b) => window.__say(b)", base64.b64encode(pcm).decode())

    def tap(self) -> None:
        self.events.append({"t": self.now(), "tap": self.phase()})
        print(f"  [{self.now()}] tap ({self.phase()})", flush=True)
        self.page.click("#orb")

    def transcript(self) -> list[str]:
        return self.page.evaluate("[...document.querySelectorAll('#transcript p')].map(p => p.className + ': ' + p.textContent)")

    def finish(self) -> dict:
        try:
            if self.phase() not in ("idle", "done"):
                self.page.evaluate("window.Live.endConversation()")
                self.page.wait_for_timeout(3500)
        except Exception as e:
            print("  end failed:", e)
        L = self.page.evaluate("(() => { const L = window.Live.L; return {log: L.log, trace: L.trace, gaps: L.gaps, asides: L.asides, seconds: L.seconds, ratio: L.ratio, backend: L.backend, liveId: L.liveId}; })()")
        reading = []
        root = self.world.root if self.world else (ROOT / "runs" / (self.run or "none"))
        rp = root / "live" / "reading.jsonl"
        if rp.exists():
            reading = [json.loads(x) for x in rp.read_text().splitlines() if x.strip()]
        # Every session's cost as the server logged it (/api/live/usage: the page resets its counters per
        # session, and a long chapter may take over in a new session at a section boundary).
        logp = ROOT / self.cfg["listener_dir"] / "sessions" / "log.jsonl"
        mine = [json.loads(x) for x in (logp.read_text().splitlines() if logp.exists() else []) if x.strip()]
        mine = [x for x in mine if x.get("provider") == "openai" and x.get("live_id") in self.live_ids()]
        usd = round(sum(x.get("cost_usd") or 0 for x in mine), 4)
        L["seconds"] = round(sum(x.get("seconds") or 0 for x in mine), 1)
        log_cost(f"pagecheck:{self.out.name}", usd, seconds=L["seconds"], sessions=len(mine))
        whole = [r for r in reading if not r["cut"]]
        gaps = [g / 1000 for g in L["gaps"] if g is not None]
        summary = {
            "seconds": L["seconds"], "usd": usd, "context_ratio": L["ratio"], "live_id": L["liveId"],
            "paragraphs_read": len(whole), "word_for_word": sum(r["same"] for r in whole),
            "deviating": [{"id": r["id"], "diffs": r["diffs"]} for r in whole if not r["same"]],
            "gaps_s": {"median": round(statistics.median(gaps), 2) if gaps else None,
                       "worst": round(max(gaps), 2) if gaps else None, "all": [round(g, 2) for g in gaps]},
            "asides": L["asides"],
            "stop_ms": [a.get("stop_ms") for a in L["asides"]],
            "first_word_ms": [a.get("first_word_ms") for a in L["asides"]],
        }
        (self.out / "page_log.json").write_text(json.dumps(L, indent=1))
        (self.out / "events.json").write_text(json.dumps(self.events, indent=1))
        (self.out / "transcript.txt").write_text("\n".join(self.transcript()))
        (self.out / "reading.json").write_text(json.dumps(reading, indent=1))
        (self.out / "summary.json").write_text(json.dumps(summary, indent=1))
        self.browser.close(); self.pw.stop()
        self.srv.should_exit = True
        return summary


def cfg_nudge_secs(h: Harness) -> float:
    return h.cfg["live"]["page"]["start_nudge_ms"] / 1000


def adopt(h: Harness) -> None:
    """What start_chapter does for the page: this conversation's chapter is the World's run."""
    h.world.status()
    h.page.evaluate(f"window.Live.L.own = {json.dumps(h.world.name)}; window.Live.L.startCalled = true;")
    h.events.append({"t": h.now(), "adopted": h.world.name})


def accept(h: Harness, timeout: float = 60) -> bool:
    """The opening is written mid-conversation: the guide offers it at a pause (Ian's second test,
    2026-11-15) and the listener says yes; if it never asks, the circle's tap plays it."""
    if not h.wait("L.offer || L.phase === 'reading' || L.phase === 'leadin'", timeout, "the offer"):
        return False
    if h.phase() == "talk":
        h.wait("L.offer && L.offer.askedAt", 25, "the guide to ask if they'd like it now")
        h.quiet(1.2)
        if h.phase() == "talk":
            h.say("Yes, let's hear it.")
    if not h.wait("L.phase === 'reading'", 25, "the reading to start after the yes"):
        h.tap()
        return h.wait("L.phase === 'reading'", 10, "the reading after the tap")
    return True


def returning(h: Harness) -> None:
    """Ian's first live test (2026-11-14): an earlier conversation's chapter, finished and heard, is the
    newest run. A new conversation must greet and talk, and never read that chapter by itself."""
    for n in h.world.texts:
        h.world.lock(n)
    h.world.status(state="ready")
    (h.world.root / "fast").mkdir(exist_ok=True)
    (h.world.root / "fast" / "player.json").write_text(json.dumps({"at": time.time() - 3600, "state": "done",
                                                                    "section": len(h.world.texts), "piece": 0}))
    h.tap()
    h.wait("L.phase === 'talk'", 20, "the session to start")
    h.wait("L.outFirst", 10, "the greeting")
    h.quiet(1.2)
    for line in ["Hey, I'm Sam.", "Pretty good, thanks. I spent the afternoon fixing my bike.",
                 "The chain kept slipping, so I finally replaced the whole cassette."]:
        h.say(line)
        h.page.wait_for_timeout(1500)
        h.quiet(2.0, 30)
    h.page.wait_for_timeout(4000)
    read = [m for m in h.page.evaluate("window.Live.L.log") if m[1] in ("read",) or (m[1] == "instruct" and "chapter" in (m[2] or ""))]
    h.events.append({"t": h.now(), "check": "no old chapter read", "phase": h.phase(), "marks": read,
                     "own": h.L("L.own"), "readStarted": h.L("L.readStarted")})
    print(f"  [{h.now()}] phase {h.phase()}, readStarted {h.L('L.readStarted')}, chapter marks {read}", flush=True)


def smoke(h: Harness) -> None:
    """Greeting, two exchanges, the opening read, one question, back, the end of the written part,
    a check-in, the next part arriving, and the reading going on."""
    h.tap()
    h.wait("L.phase === 'talk'", 20, "the session to start")
    h.wait("L.outFirst", 10, "the greeting")
    h.quiet(1.2)
    h.say("Hi! I'm Sam. I've been re-reading Thinking in Systems by Donella Meadows.")
    h.page.wait_for_timeout(1500)
    h.quiet(1.5)
    h.say("Yeah, I like how it shows everything is a system. I plan things very precisely at work.")
    h.page.wait_for_timeout(1500)
    h.quiet(1.5)
    adopt(h)
    h.world.lock(1)  # the opening is written
    accept(h)
    h.wait("L.feed.cur && L.feed.cur.i >= 1 && L.feed.cur.matched > 8", 120, "the second paragraph")
    h.tap()
    h.wait("L.phase === 'aside'", 5, "the aside")
    h.page.wait_for_timeout(1500)
    h.say("Wait, who was Donella Meadows exactly?")
    h.page.wait_for_timeout(1500)
    h.quiet(2.0)
    h.say("Oh okay, got it. Go on.")
    h.wait("L.phase === 'reading'", 30, "back to the reading")
    h.wait("L.phase === 'waiting' || L.phase === 'checkin'", 300, "the end of the written part")
    h.world.status(waiting_for=2, check_in="check_in", eta=120)
    h.wait("L.phase === 'checkin'", 30, "the check-in")
    h.page.wait_for_timeout(1500)
    h.quiet(2.0)
    h.say("It's making me think about my own planning, honestly. I tweak numbers all the time.")
    h.page.wait_for_timeout(1500)
    h.quiet(2.0)
    h.world.lock(2, "Here is the second part. It is short, and it is only here to test the reading after a check-in.\n\nThat is the end of the test chapter.")
    h.wait("L.phase === 'reading'", 40, "the reading after the check-in")
    h.wait("L.finished", 120, "the end of the chapter")


def read(h: Harness) -> None:
    """Check 7: the whole chapter read live, every section written before it starts (no waits, no
    questions): how much is read word for word, and the gaps between paragraphs."""
    h.tap()
    h.wait("L.phase === 'talk'", 20, "the session to start")
    h.wait("L.outFirst", 10, "the greeting")
    h.quiet(1.2)
    h.say("Hi, I'm Sam. I'm ready for my chapter.")
    h.page.wait_for_timeout(1500)
    h.quiet(1.5)
    adopt(h)
    for n in h.world.texts:
        h.world.lock(n)
        h.page.wait_for_timeout(300)
    accept(h)
    total = sum(len(t.split()) for t in h.world.texts.values()) / 2.2 + 120
    h.wait("L.finished", total, "the end of the chapter")


def asks(h: Harness) -> None:
    """Check 8: ten interruptions spread through the reading, each a short question answered and
    then "go on": how long the reading takes to stop after the tap, and to start again after the
    guide goes back (handover)."""
    questions = ["Wait, what does that mean?", "Sorry, who was that again?", "Hang on, is that true?",
                 "What was that word?", "Can you say more about that?", "Why does that matter?",
                 "Where did that happen?", "When was that?", "Wait, how so?", "Hmm, what's the point there?"]
    h.tap()
    h.wait("L.phase === 'talk'", 20, "the session to start")
    h.wait("L.outFirst", 10, "the greeting")
    h.quiet(1.2)
    h.say("Hi, I'm Sam. Ready when you are.")
    h.page.wait_for_timeout(1500)
    h.quiet(1.5)
    adopt(h)
    for n in h.world.texts:
        h.world.lock(n)
    accept(h)
    for k, q in enumerate(questions):
        h.page.wait_for_timeout(9000 + 2000 * (k % 3))
        if not h.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.startedAt", 60, f"reading before tap {k + 1}"):
            break
        h.tap()
        h.wait("L.phase === 'aside'", 5, "the aside")
        h.page.wait_for_timeout(1200)
        h.say(q)
        h.page.wait_for_timeout(1500)
        h.quiet(2.0, 30)
        h.say("Okay, got it. Go on.")
        if not h.wait("L.phase === 'reading'", 30, f"back to the reading after question {k + 1}"):
            h.tap()  # Back to the chapter
            h.wait("L.phase === 'reading'", 10, "back by the button")
        if h.page.evaluate("window.Live.L.finished"):
            break


def fast(h: Harness) -> None:
    """Check 12: a real fast-start chapter from the conversation, read live: the listener asks for a
    chapter, the guide pitches and starts it (or, if it never does, the seed goes in typed), they talk
    while it is made, the opening is read as soon as it is written, one question mid-chapter, and every
    check-in answered once. The run is made by cli.py in runs/ (its cost is the run's own, capped at
    fast.max_run_usd)."""
    h.run = None
    h.tap()
    h.wait("L.phase === 'talk'", 20, "the session to start")
    h.wait("L.outFirst", 10, "the greeting")
    h.quiet(1.2)
    lines = ["Hi, I'm Sam. I just got back from a walk by the river.",
             "Yeah, I kept watching the herons. They stand completely still for ages and then strike.",
             "Honestly I'd love a chapter about how herons hunt like that. Let's do that.",
             "Yes, that sounds great, go for it."]
    for line in lines:
        h.say(line)
        h.page.wait_for_timeout(1500)
        h.quiet(2.0, 40)
        if h.pipe.status().get("run") and h.pipe.status().get("state") == "making":
            break
    if not h.L("L.own"):  # one more go-ahead (the page's own start reminder may also come); a chapter typed in
        # through /api/chapters would not be this conversation's, so the page would never read it
        h.say("Please go ahead and start that chapter now.")
        h.wait("L.own", cfg_nudge_secs(h) + 15, "start_chapter after the go-ahead")
    if not h.L("L.own"):
        print("  the guide never called start_chapter", flush=True)
        h.events.append({"t": h.now(), "check": "the guide never called start_chapter"})
        return
    h.run = h.L("L.own")
    print(f"  [{h.now()}] run {h.run}", flush=True)
    h.events.append({"t": h.now(), "run": h.run})
    h.page.wait_for_timeout(4000)
    h.say("Nice. While I wait: I've only ever seen grey herons here, is that the usual one?")
    h.page.wait_for_timeout(1500)
    h.quiet(2.0, 30)
    h.wait("L.phase === 'reading'", 900, "the opening to be written and the reading to start")
    asked = False
    answered = set()
    end = time.time() + 60 * 45
    while time.time() < end and not h.page.evaluate("window.Live.L.finished"):
        ph = h.phase()
        if ph == "reading" and not asked and h.L("L.feed.cur && L.feed.cur.n === 1 && L.feed.cur.i >= 2 && L.feed.cur.matched > 10"):
            asked = True
            h.tap()
            h.wait("L.phase === 'aside'", 5, "the aside")
            h.page.wait_for_timeout(1200)
            h.say("Wait, how long can a heron actually stand still like that?")
            h.page.wait_for_timeout(1500)
            h.quiet(2.0, 30)
            h.say("Huh, okay. Go on.")
            h.wait("L.phase === 'reading'", 30, "back to the reading")
        elif ph == "checkin":
            n = h.L("L.wait && L.wait.n")
            if n not in answered and h.L("L.wait && L.wait.lastGuide"):
                answered.add(n)
                h.quiet(1.5, 20)
                h.say("It's good. The bit about patience landed for me, I'm terrible at waiting.")
        h.page.wait_for_timeout(1000)
    h.wait("L.finished", 60, "the end of the chapter")


def player(h: Harness) -> None:
    """Ian's second test: the offer and a yes; pause (how soon the guide stops, and that it says nothing),
    play (from the paused sentence), the slider back to the start, a tap nobody speaks after (a pause, the
    mic off), and one question answered and back."""
    h.tap()
    h.wait("L.phase === 'talk'", 20, "the session to start")
    h.wait("L.outFirst", 10, "the greeting")
    h.quiet(1.2)
    h.say("Hi! I'm Sam.")
    h.page.wait_for_timeout(1500)
    h.quiet(1.5)
    h.say("Pretty good. I've been reading about Donella Meadows lately.")
    h.page.wait_for_timeout(1500)
    h.quiet(1.5)
    adopt(h)
    for n in h.world.texts:
        h.world.lock(n)
    if not accept(h):
        return
    h.wait("L.feed.cur && L.feed.cur.startedAt && L.feed.cur.matched > 12", 60, "a little of the reading")

    def words_after(t0: float) -> str:
        return h.page.evaluate(f"(() => {{ const L = window.Live.L; return [...document.querySelectorAll('#transcript p.guide')].slice(-3).map(p => p.textContent).join(' | '); }})()")

    t_pause = time.time()
    h.events.append({"t": h.now(), "pause": h.L("JSON.stringify(window.Live.where() && {id: window.Live.where().id, sentence: window.Live.where().sentence})")})
    h.page.click("#play")
    h.wait("L.phase === 'paused'", 6, "the pause")
    stop_ms = h.L(f"L.spokeAt - {int(t_pause * 1000)}")
    h.page.wait_for_timeout(6000)  # it should say nothing at all
    spoke = h.L(f"L.spokeAt > {int(t_pause * 1000) + 2500}")
    h.events.append({"t": h.now(), "check": "pause", "stop_ms": stop_ms, "spoke_while_paused": spoke,
                     "paused_at": h.L("L.pausedAt"), "muted": h.L("L.muted"), "lines": words_after(t_pause)})
    print(f"  [{h.now()}] paused at {h.L('JSON.stringify(L.pausedAt)')}, last word {stop_ms} ms after, spoke while paused: {spoke}", flush=True)
    h.page.click("#play")
    h.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.startedAt", 20, "the reading after play")
    h.events.append({"t": h.now(), "check": "play", "from": h.L("L.feed.cur && {id: L.feed.cur.id, sentence: L.feed.cur.sentence}")})
    h.page.wait_for_timeout(8000)
    t_seek = time.time()
    h.page.evaluate("window.Live.seek(0)")
    h.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.id === 's1-p01' && L.feed.cur.startedAt", 20, "the reading from the start")
    h.events.append({"t": h.now(), "check": "seek", "first_word_ms": h.L(f"L.feed.cur.startedAt - {int(t_seek * 1000)}")})
    h.page.wait_for_timeout(6000)
    h.tap()
    h.wait("L.phase === 'aside'", 5, "the aside")
    h.page.evaluate("window.Live.S.aside_quiet_secs = 12")
    h.wait("L.phase === 'paused'", 30, "a silent tap to become a pause")
    h.events.append({"t": h.now(), "check": "silent tap", "phase": h.phase(), "muted": h.L("L.muted")})
    h.tap()  # a question from the paused place
    h.wait("L.phase === 'aside'", 5, "the aside from the pause")
    h.page.wait_for_timeout(1200)
    h.say("Wait, who was Donella Meadows exactly?")
    h.page.wait_for_timeout(1500)
    h.quiet(2.0, 30)
    h.say("Oh okay, got it. Go on.")
    h.wait("L.phase === 'reading'", 30, "back to the reading")
    h.page.wait_for_timeout(8000)
    # Ian's third test: a question it couldn't answer from the chapter ("is this a true story?") got "let me
    # give that a thought" and nothing. Does it answer now?
    h.wait("L.phase === 'reading' && L.feed.cur && L.feed.cur.startedAt", 30, "reading before the second tap")
    h.tap()
    h.wait("L.phase === 'aside'", 5, "the second aside")
    h.page.wait_for_timeout(1200)
    h.say("Hang on, is that actually true? Has anyone checked it?")
    h.page.wait_for_timeout(1500)
    h.quiet(3.0, 40)
    h.events.append({"t": h.now(), "check": "hard question", "stalls": h.L("L.log.filter((m) => m[1] === 'stall').map((m) => m[2])"),
                     "notes": h.L("L.log.filter((m) => m[1] === 'instruct').slice(-3).map((m) => m[2])"),
                     "lines": h.transcript()[-4:]})
    h.say("Okay, thanks. Go on.")
    h.wait("L.phase === 'reading'", 30, "back to the reading after the second question")
    h.page.wait_for_timeout(4000)


def handoff(h: Harness) -> None:
    """Tester Demo, Ian's notes (2026-11-16): the guide's session opened ahead of the handover (Live.handoff
    with hold), held a few seconds as the interviewer's last words would hold it, then let go with the name.
    Timed: how long it took to open, whether it stayed silent while held, and from the handover to the guide's
    first sound and first word. About 20 connected seconds."""
    p = h.page
    p.evaluate("""async () => {
      const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      window.__s = s;
      window.__t = { open: Date.now(), loud: [] };
      const ctx = new AudioContext(); let a = null;
      setInterval(() => {  // the guide's voice as it is heard: an analyser on the page's own audio element
        const src = document.getElementById("remote").srcObject;
        if (src && !a) { a = ctx.createAnalyser(); a.fftSize = 512; ctx.createMediaStreamSource(src).connect(a); }
        if (!a) return;
        const d = new Float32Array(a.fftSize); a.getFloatTimeDomainData(d);
        if (Math.sqrt(d.reduce((x, v) => x + v * v, 0) / d.length) > 0.01) window.__t.loud.push(Date.now());
      }, 40);
      Live.handoff({ stream: s, hold: true });
    }""")
    h.wait("L.log.some((x) => x[1] === 'held')", 15, "the held session to open")
    p.wait_for_timeout(4000)
    p.evaluate("() => { window.__t.go = Date.now(); Live.handoff({ name: 'Rolando', stream: window.__s }); }")
    h.wait("L.sessionWord", 15, "the guide's first word")
    h.quiet(1.5, 15)
    h.say("Hi! Good to meet you.")  # the mic, kept from the held session until the handover, is heard after it
    heard = h.wait("L.heardAt > window.__t.go", 15, "the listener to be heard after the handover")
    h.quiet(1.5, 15)
    t = p.evaluate("""(() => { const L = Live.L, t = window.__t, at = (k) => (L.log.find((x) => x[1] === k) || [0])[0];
      const after = t.loud.filter((x) => x >= t.go);
      return { open_ms: at("held") - t.open, sound_while_held: t.loud.filter((x) => x < t.go).length,
               word_while_held: L.log.some((x) => x[1] === "revealed" && x[0] < t.go),
               note_ms: at("handoff-note") - t.go, first_sound_ms: after.length ? after[0] - t.go : null,
               first_word_ms: L.sessionWord ? L.sessionWord - t.go : null, said: L.lastGuideLine,
               asked_again: L.log.filter((x) => x[1] === "instruct").length }; })()""")
    t["heard_after"] = heard
    h.events.append({"t": h.now(), "handoff": t})
    print("  handoff:", json.dumps(t), flush=True)


SCENARIOS = {"smoke": smoke, "read": read, "asks": asks, "fast": fast, "returning": returning, "player": player,
             "handoff": handoff}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", choices=sorted(SCENARIOS))
    ap.add_argument("--source", default="/Users/ian/Desktop/write-listen/runs/ideas-behind-systems-stocks-20261113")
    ap.add_argument("--paragraphs", type=int, default=None)
    ap.add_argument("--sections", type=int, default=None)
    a = ap.parse_args(argv)
    cfg = load_config()
    if a.scenario in ("smoke", "returning", "player"):
        a.paragraphs, a.sections = a.paragraphs or 3, a.sections or 2
    h = Harness(a.scenario, None if a.scenario in ("fast", "handoff") else Path(a.source), a.paragraphs, a.sections,
                cfg["live"]["build_cap_usd"])
    h.serve()
    h.open()
    try:
        SCENARIOS[a.scenario](h)
    finally:
        s = h.finish()
        print(json.dumps(s, indent=1))
        print("out:", h.out)


if __name__ == "__main__":
    main()
