"""The page's pure timing functions (episode/page/timing.js) under Node, against the Python twins
(acceptance check 6, without a browser)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from episode import timing

TIMING_JS = Path(__file__).resolve().parents[2] / "episode" / "page" / "timing.js"
node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="Node is not installed")


def run_js(episode: dict, script: str):
    code = f"const T = require({json.dumps(str(TIMING_JS))}); const ep = {json.dumps(episode)};\n{script}"
    out = subprocess.run([node, "-e", code], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_current_word_matches_python_including_gaps(timed):
    _, t = timed
    ws = [w for s in t["segments"] for x in s["sentences"] for w in x["words"]]
    s1, s2 = t["segments"]
    g = next(i for i in range(len(ws) - 1) if ws[i + 1]["start"] - ws[i]["end"] > 0.05)  # a real gap
    samples = [0.0, ws[0]["start"], ws[0]["start"] - 0.01, ws[g]["start"] + 0.001,
               (ws[g]["end"] + ws[g + 1]["start"]) / 2,      # inside that gap between two words
               (s1["end"] + s2["start"]) / 2,                # the pause between segments
               ws[-1]["end"] + 0.2, t["duration"], 12.0, 17.3]
    got = run_js(t, f"const W = T.flattenWords(ep); console.log(JSON.stringify({json.dumps(samples)}"
                    ".map(x => { const i = T.currentWordIndex(W, x); return i < 0 ? null : [W[i].seg, W[i].sent, W[i].word]; })))")
    want = [list(c) if c else None for c in (timing.current_word(t, x) for x in samples)]
    assert got == want
    assert got[2] is None and got[4] == got[3]  # before the first word: none; in a gap: the word just said


def test_sentence_steps_and_cards(timed):
    _, t = timed
    sents = [x for s in t["segments"] for x in s["sentences"]]
    got = run_js({**t, "references": [{"start": sents[1]["start"]}, {"start": sents[4]["start"]}]}, f"""
      const S = T.flattenSentences(ep);
      console.log(JSON.stringify({{
        next0: T.sentenceStep(S, 0, 1), nextMid: T.sentenceStep(S, {sents[2]["start"] + 0.5}, 1),
        backEarly: T.sentenceStep(S, {sents[2]["start"] + 0.4}, -1), backLate: T.sentenceStep(S, {sents[2]["start"] + 2.5}, -1),
        lastNext: T.sentenceStep(S, {t["duration"]}, 1), firstBack: T.sentenceStep(S, 0, -1),
        cards: [0, {sents[1]["start"]}, {sents[3]["start"]}, {t["duration"]}].map(x => T.referencesUpTo(ep.references, x).length),
        clock: [T.formatTime(0), T.formatTime(61.9), T.formatTime(2570.5)] }}));""")
    assert got["next0"] == sents[1]["start"] and got["nextMid"] == sents[3]["start"]
    assert got["backEarly"] == sents[1]["start"] and got["backLate"] == sents[2]["start"]  # within a second: previous
    assert got["lastNext"] == t["duration"] and got["firstBack"] == sents[0]["start"]  # last sentence: stay put
    assert got["cards"] == [0, 1, 1, 2] and got["clock"] == ["0:00", "1:01", "42:50"]
