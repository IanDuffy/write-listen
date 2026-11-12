"""End-to-end checks on a built run (acceptance checks 8-12): `python -m episode check <run>`.

Writes <run>/11_episode/checks.json and, for check 11, screenshots in <run>/11_episode/screenshots/
(kept with the run, which is not committed: they show the chapter's text).
"""

from __future__ import annotations

import functools
import json
import re
import shutil
import subprocess
import tempfile
import threading
from collections import Counter
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .align import norm_tokens
from .timing import EPISODE_DIR

PAGE = Path(__file__).parent / "page"


def words(t: dict) -> list[tuple[str, dict]]:
    return [(x["id"], w) for s in t["segments"] for x in s["sentences"] for w in x["words"]]


SQUEEZED = 0.08  # seconds a word: an estimated run tighter than this was squeezed into a gap


def check_match(t: dict) -> dict:  # 8
    ws = words(t)
    runs, squeezed, cur = [], [], []
    for sid, w in ws + [("", {"w": "", "start": 0, "end": 0})]:
        if w.get("estimated"):
            cur.append((sid, w))
        else:
            item = {"sentence": cur[0][0], "start": cur[0][1]["start"], "words": " ".join(x["w"] for _, x in cur)} if cur else None
            if len(cur) > 5:
                runs.append(item)
            if cur and (cur[-1][1]["end"] - cur[0][1]["start"]) / len(cur) < SQUEEZED:
                squeezed.append(item)
            cur = []
    st = t["stats"]
    return {"words": st["words"], "exact": st["exact"], "fuzzy": st["fuzzy"], "estimated": st["estimated"],
            "matched_pct": round(100 * (st["exact"] + st["fuzzy"]) / st["words"], 2), "estimated_runs_over_5": runs,
            "squeezed_runs": squeezed, "gaps_reheard": st.get("gaps_reheard"),
            "pass": (st["exact"] + st["fuzzy"]) / st["words"] >= 0.98 and not squeezed}


def check_duration(t: dict) -> dict:  # 9
    end = t["segments"][-1]["end"]
    return {"audio_duration": t["duration"], "aligned_end": end, "difference": round(t["duration"] - end, 3),
            "pass": abs(t["duration"] - end) <= 1.0}


def check_spot(run: Path, t: dict, recognizer, n: int = 20) -> dict:  # 10
    """n words spread evenly across the chapter (four letters or more, matched, not estimated):
    cut one second around each, recognize the clip, and look for the word."""
    pool = [(sid, w) for sid, w in words(t) if not w.get("estimated") and len(re.sub(r"\W", "", w["w"])) >= 4]
    picks = [pool[round(i * (len(pool) - 1) / (n - 1))] for i in range(n)]
    audio = run / t["audio"]
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for k, (sid, w) in enumerate(picks):
            mid = (w["start"] + w["end"]) / 2
            clip = Path(tmp) / f"clip{k}.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, mid - 0.5):.3f}", "-t", "1.0", "-i",
                            str(audio), "-ac", "1", "-ar", "16000", str(clip)], check=True)
            heard = " ".join(x["w"] for x in recognizer(clip))
            want = norm_tokens(w.get("spoken", w["w"])) + norm_tokens(w["w"])
            got = norm_tokens(heard)
            ok = any(tok in got for tok in want) or any(_close(tok, g) for tok in want for g in got)
            results.append({"word": w["w"], "sentence": sid, "time": round(mid, 2), "heard": heard, "pass": ok})
    passed = sum(r["pass"] for r in results)
    return {"passed": passed, "of": len(results), "clips": results}


def _close(a: str, b: str) -> bool:
    """One edit apart (a clipped first or last sound in a one-second cut)."""
    from difflib import SequenceMatcher
    return len(a) >= 4 and SequenceMatcher(None, a, b).ratio() >= 0.8


def check_screens(run: Path, points: list[float] | None = None) -> dict:  # 11
    from playwright.sync_api import sync_playwright

    ep = json.loads((run / EPISODE_DIR / "episode.json").read_text(encoding="utf-8"))
    points = points or [round(ep["duration"] * f, 1) for f in (0.05, 0.27, 0.5, 0.72, 0.94)]
    out_dir = run / EPISODE_DIR / "screenshots"
    out_dir.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        shutil.copytree(PAGE, root / "page")
        (root / "run").symlink_to(run)

        class Quiet(SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(root)))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        shots = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                pg = browser.new_page(viewport={"width": 1280, "height": 860})
                pg.goto(f"http://127.0.0.1:{httpd.server_address[1]}/page/index.html?run=/run")
                pg.wait_for_function("document.getElementById('audio').readyState >= 1", timeout=60000)
                for k, t in enumerate(points):
                    pg.evaluate(f"document.getElementById('audio').currentTime = {t}")
                    pg.wait_for_timeout(1300)  # the smooth scroll to the sentence
                    state = pg.evaluate("""() => ({word: document.querySelector('.w.current')?.textContent || null,
                        sentence: document.querySelector('.s.current')?.id || null,
                        card: document.querySelector('.card.latest .title')?.textContent || null,
                        cards: document.querySelectorAll('.card').length})""")
                    path = out_dir / f"seek{k + 1}_{int(t)}s.png"
                    pg.screenshot(path=str(path))
                    shots.append({"time": t, "file": str(path.relative_to(run)), **state})
                browser.close()
        finally:
            httpd.shutdown()
    return {"shots": shots, "pass": all(s["word"] for s in shots)}


def check_references(run: Path) -> dict:  # 12
    from stages.brief import parse_segments

    ep = json.loads((run / EPISODE_DIR / "episode.json").read_text(encoding="utf-8"))
    refs = ep["references"]
    cards = {r["dossier_ref"] for r in refs if r["dossier_ref"]}
    att_path = run / EPISODE_DIR / "attachments.json"
    attached = {c["dossier_ref"] for cs in json.loads(att_path.read_text("utf-8")).values() for c in cs
                if c["dossier_ref"]} if att_path.exists() else cards
    brief = run / "05_brief.md"
    assigned = [(s["n"], f"D{k:02d}") for s in parse_segments(brief.read_text("utf-8")) for k in s["entries"]] \
        if brief.exists() else []
    from stages.common import dossier_entries, entry_tags
    entries = dossier_entries((run / "04_dossier.md").read_text("utf-8"))
    kind = {f"D{k:02d}": entry_tags(v)["kind"] or "claim" for k, v in entries.items()}
    script = (run / "09_script_final.md").read_text("utf-8")
    cited = {f"D{int(m):02d}" for m in re.findall(r"D0*(\d+)", " ".join(re.findall(r"\[D[^\]]*\]", script)))}
    never = [(n, d) for n, d in assigned if d not in attached]
    reasoning = [f"seg{n:02d} {d}" for n, d in never if kind.get(d) in ("connection", "tension")]
    uncited = [f"seg{n:02d} {d}" for n, d in never if kind.get(d) not in ("connection", "tension") and d not in cited]
    missed = [f"seg{n:02d} {d}" for n, d in never if kind.get(d) not in ("connection", "tension") and d in cited]
    return {"by_kind": dict(Counter(r["kind"] for r in refs)), "total": len(refs),
            "linked": sum(1 for r in refs if r["url"]),
            "brief_assigned": len(assigned),
            "assigned_without_a_card": [f"seg{n:02d} {d}" for n, d in assigned if d not in cards],
            "assigned_never_attached": [f"seg{n:02d} {d}" for n, d in never],
            # why: reasoning entries are never shown as sources (by design); uncited ones the script
            # never used; missed ones the script cites but no card or attachment was made for
            "never_attached_reasoning": reasoning, "never_attached_uncited": uncited, "never_attached_missed": missed}


def run_all(run: Path, recognizer) -> dict:
    t = json.loads((run / EPISODE_DIR / "timing.json").read_text(encoding="utf-8"))
    out = {"match": check_match(t), "duration": check_duration(t), "spot": check_spot(run, t, recognizer),
           "screens": check_screens(run), "references": check_references(run)}
    (run / EPISODE_DIR / "checks.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
