"""Live timing (docs/ask-voice.md, check 20): real asides against the real aside agent, from the real
page in headless Chromium, over a copy of a chapter. Chromium's fake microphone plays silence, so
the guide is never interrupted; each aside ends with a tap once the guide has spoken for a moment.
Measures tap to animation start, tap to session ready and tap to the guide's first sound, and records
a short video of the circle (Connecting, then Speaking) for Ian.

    python -m ask.live <run> [--n 10] [--listener DIR] [--video]   -> ask/live.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import statistics
import subprocess
import threading
import time
from pathlib import Path

from stages.common import ROOT, load_config

OUT = ROOT / "ask"
COPY = ("03_angle.md", "04_dossier.md", "05_brief.md", "09_script_final.md", "ledger.json",
        "11_episode/timing.json", "11_episode/episode.json", "10_audio/chapter.mp3")


def copy_run(src: Path, dest_runs: Path) -> Path:
    dest = dest_runs / f"{src.name}-live"
    for rel in COPY:
        if (src / rel).exists() and not (dest / rel).exists():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / rel, dest / rel)
    return dest


def serve(cfg: dict, runs: Path) -> tuple[str, object]:
    import uvicorn

    import server
    pipe = server.Pipeline(runs_dir=runs)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(server.create_app(pipe, cfg, remember_on_start=False), host="127.0.0.1",
                                        port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}", srv


def silence_wav(path: Path) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "60", str(path)],
                   check=True)
    return path


def main(argv: list[str] | None = None) -> int:
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser(prog="python -m ask.live")
    ap.add_argument("run", type=Path)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--listener", default=None)
    ap.add_argument("--work", type=Path, default=OUT / "_runs" / "live")
    ap.add_argument("--video", action="store_true")
    args = ap.parse_args(argv)
    cfg = load_config()
    if args.listener:
        cfg["listener_dir"] = args.listener
    runs = args.work / "runs"
    run = copy_run(args.run, runs)
    base, srv = serve(cfg, runs)
    wav = silence_wav(args.work / "silence.wav")
    duration = json.loads((run / "11_episode" / "timing.json").read_text())["duration"]
    points = [duration * (k + 0.5) / args.n for k in range(args.n)]
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required",
                                           "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
                                           f"--use-file-for-fake-audio-capture={wav}"])
        for k, t in enumerate(points):
            video = args.video and k == 0
            ctx = browser.new_context(viewport={"width": 420, "height": 420} if video else None,
                                      record_video_dir=str(OUT / "capture") if video else None,
                                      record_video_size={"width": 420, "height": 420} if video else None)
            page = ctx.new_page()
            page.goto(f"{base}/?chapter={run.name}")
            page.wait_for_function("window.Ask && window.ElevenLabsClient && document.body.classList.contains('ready')",
                                   timeout=30000)
            page.evaluate(f"document.getElementById('audio').currentTime = {t}")
            page.click("#orb")  # play
            page.wait_for_function("!document.getElementById('audio').paused && document.getElementById('line').textContent === 'Ask a question'")
            page.wait_for_timeout(1500)
            page.click("#orb")  # ask
            try:
                page.wait_for_function("window.Ask.debug.asides.length > 0 || (window.Ask.state() && window.Ask.state().mode === 'speaking')",
                                       timeout=20000)
                page.wait_for_timeout(2500 if not video else 4000)  # let the guide speak, for the video too
                speaking = page.evaluate("window.Ask.state() && window.Ask.state().mode")
                page.click("#orb")  # back to the chapter
                page.wait_for_function("window.Ask.debug.asides.length > 0", timeout=15000)
            except Exception as e:  # noqa: BLE001 -- record and go on
                speaking = f"error: {e}"
            d = page.evaluate("window.Ask.debug.asides[0] || null")
            lines = page.evaluate("Array.from(document.querySelectorAll('#transcript details.aside summary')).map(e => e.textContent)")
            ctx.close()
            row = {"k": k, "t": round(t, 1), "how": d and d["how"], "mode_before_tap": speaking,
                   "tap_to_animation_ms": d and d["blobAt"] and round(d["blobAt"] - d["tap"], 1),
                   "tap_to_paused_ms": d and d["pausedAt"] and round(d["pausedAt"] - d["tap"], 1),
                   "tap_to_ready_ms": d and d["ready"] and round(d["ready"] - d["tap"]),
                   "tap_to_first_sound_ms": d and d["firstSound"] and round(d["firstSound"] - d["tap"]),
                   "opening": lines[0] if lines else None}
            results.append(row)
            print(f"  {k}: {row}", flush=True)
            time.sleep(1)
        browser.close()
    srv.should_exit = True

    def stats(key):
        xs = [r[key] for r in results if isinstance(r.get(key), (int, float))]
        return {"n": len(xs), "median": statistics.median(xs) if xs else None, "worst": max(xs) if xs else None}

    summary = {k: stats(k) for k in ("tap_to_animation_ms", "tap_to_paused_ms", "tap_to_ready_ms", "tap_to_first_sound_ms")}
    asides = [json.loads(ln) for ln in (run / "12_asks" / "asides.jsonl").read_text().splitlines()] \
        if (run / "12_asks" / "asides.jsonl").exists() else []
    from stages import voice

    from .scenarios import conversation_cost, log_cost
    client, costs = voice._client(), []
    from .scenarios import COSTS
    logged = {json.loads(ln).get("conversation_id") for ln in COSTS.read_text().splitlines()} if COSTS.exists() else set()
    for a in asides:  # ElevenLabs' cost of each, once processed (each logged once)
        if a.get("conversation_id") and a["conversation_id"] not in logged:
            c = conversation_cost(client, a["conversation_id"])
            costs.append(c)
            log_cost({"what": "live timing aside", "provider": "elevenlabs", "conversation_id": a["conversation_id"], **c})
    (OUT / "live.json").write_text(json.dumps({"summary": summary, "runs": results, "asides": asides,
                                               "costs": costs}, indent=1, ensure_ascii=False))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
