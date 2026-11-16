"""Following the guide's reading word by word (page/align.js): pure functions, run here under Node.
What they must survive is what the live runs showed (2026-11-13): deltas that split a word, quotes and
hyphens the transcript writes differently, and a voice that skips, joins or garbles words."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ALIGN_JS = Path(__file__).resolve().parents[2] / "page" / "align.js"

HARNESS = r"""
const A = require(process.argv[1]);
const out = {};
const push = (deltas) => { const t = new A.Tokens(); const got = deltas.map((d) => t.push(d)); return { got, rest: t.flush() }; };
out.split = push([" list", "ening ", "to"]);
out.sud = push([" Sud", "denly", " it", " rained."]);
out.spaced = push([" a ", "b ", " c"]);
out.flush_empty = push([" done "]);
out.norm = {
  hyphen: A.norm("Re-listening to well-known songs"),
  quotes: A.norm("\"Hello,\" she said. 'Fine,' he said; ‘quoted’ “double”"),
  apostrophe: A.norm("Don’t stop: it's Meadows' list"),
  punct: A.norm("Wait — what? Yes... (no) 3.5%"),
};
const words = A.norm("the cat sat on the mat and then the dog came in to see what the noise was about today");
out.match = {
  same: A.match(words, 0, "the"),
  near: A.match(words, 0, "on"),             // index 3: within NEAR
  near_edge: A.match(words, 0, "mat"),       // index 5: from + NEAR + 1 = 5, so not near; short, so not far
  far_long: A.match(words, 0, "what"),       // index 14: within FAR and 4+ letters
  far_short: A.match(words, 0, "see"),       // index 13: too short to look for far
  beyond_far: A.match(words, 0, "noise"),    // index 16: beyond FAR
  missing: A.match(words, 0, "elephant"),
  from_later: A.match(words, 8, "dog"),
  joined: A.match(A.norm("check in now please"), 0, "checkin"),
  joined_hyphen: A.match(A.norm("we re-listen"), 0, "relisten"),
  at_end: A.match(words, words.length, "about"),
};
out.opens = {
  first: A.opens(words, "cat"),
  sixth: A.opens(words, "mat"),
  seventh: A.opens(words, "and"),
  k3: A.opens(words, "on", 3),
};
out.consts = { NEAR: A.NEAR, FAR: A.FAR };
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out():
    node = shutil.which("node") or ("/opt/homebrew/bin/node" if Path("/opt/homebrew/bin/node").exists() else None)
    if not node:
        pytest.skip("node is not installed")
    res = subprocess.run([node, "-e", HARNESS, str(ALIGN_JS)], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_tokens_join_words_split_across_deltas(out):
    """Tokens: a delta that doesn't end in a space leaves its last word to join with the next."""
    assert out["split"] == {"got": [[], ["listening"], []], "rest": ["to"]}
    assert out["sud"] == {"got": [[], [], ["suddenly"], ["it"]], "rest": ["rained"]}
    assert out["spaced"] == {"got": [["a"], ["b"], []], "rest": ["c"]}
    assert out["flush_empty"] == {"got": [["done"]], "rest": []}


def test_norm_joins_hyphens_and_drops_quotes_but_keeps_apostrophes(out):
    """norm: lower case, hyphenated words joined (the transcript writes "relistening"), quotes and
    other punctuation dropped, apostrophes inside a word kept."""
    n = out["norm"]
    assert n["hyphen"] == ["relistening", "to", "wellknown", "songs"]
    assert n["quotes"] == ["hello", "she", "said", "fine", "he", "said", "quoted", "double"]
    assert n["apostrophe"] == ["don't", "stop", "it's", "meadows", "list"]
    assert n["punct"] == ["wait", "what", "yes", "no", "3", "5"]


def test_match_finds_near_joined_and_far_words(out):
    """match: the index just after the word, looked for NEAR words ahead; two of the text's words heard
    as one; a word of four letters or more up to FAR ahead (the reading ran on past a garbled stretch);
    -1 otherwise."""
    m = out["match"]
    assert out["consts"] == {"NEAR": 4, "FAR": 15}
    assert m["same"] == 1 and m["near"] == 4
    assert m["near_edge"] == -1
    assert m["far_long"] == 15
    assert m["far_short"] == -1 and m["beyond_far"] == -1 and m["missing"] == -1
    assert m["from_later"] == 10
    assert m["joined"] == 2 and m["joined_hyphen"] == 2
    assert m["at_end"] == -1


def test_opens_looks_at_a_paragraphs_first_words(out):
    """opens: whether a word is among a paragraph's first six (or k) words: how a lead-in is told from
    the reading's start."""
    assert out["opens"] == {"first": True, "sixth": True, "seventh": False, "k3": False}
