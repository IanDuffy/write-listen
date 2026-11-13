"""Ask mid-chapter (docs/ask-voice.md): the context windows (check 2), restart points (check 3), and
the server's aside routes and log. The page's own checks (1, 4, 5, 5b) are in test_ask_page.py."""

import json
import re

import pytest
from fastapi.testclient import TestClient

import ask_fixture as F
import server
from ask import aside as A
from stages import voice

WORD_RE = re.compile(r"S\dP\dX\dW\d+")


@pytest.fixture
def chapter(tmp_path, cfg):
    root = F.build(tmp_path / "runs" / "synthetic")
    cfg["listener_dir"] = str(tmp_path / "listener")
    return A.Chapter(root, cfg)


def all_words(ch):
    return [(w["w"].rstrip("."), w["start"]) for s in ch.sents for w in s.words]


def expected_window(ch, a, b):
    return [w for w, t in all_words(ch) if a <= t < b]


# --- check 2: the 90-second and 3-minute windows -------------------------------------------------

@pytest.mark.parametrize("where", ["start", "middle", "segment edge", "gap before a segment", "end"])
def test_windows(chapter, where):
    ch = chapter
    seg2 = ch.segments[1]["start"]
    t = {"start": 0.0, "middle": 150.25, "segment edge": seg2, "gap before a segment": seg2 - 1.0,
         "end": ch.duration}[where]
    c = ch.context(t)
    just, coming = WORD_RE.findall(c["just_said"]), WORD_RE.findall(c["coming"])
    assert just == expected_window(ch, t - 90, t)
    assert coming == expected_window(ch, t, t + 180)
    if where == "start":
        assert not just and "nothing yet" in c["just_said"]
        assert c["coming"].startswith("(Segment 1: Part 1)")
    if where == "middle":  # paused mid-sentence: the sentence is split between the two windows
        assert just[-1].split("W")[0] == coming[0].split("W")[0]  # same sentence, either side of the tap
    if where in ("segment edge", "gap before a segment"):
        assert "(Segment 2: Part 2)" in c["coming"] and "(Segment 2" not in c["just_said"]
        assert "Segment 2 of 3" in c["where"] and "sentence 1 of 18" in c["where"]
        assert "heard segment 1 and this segment up to here; segment 3 is still to come." in c["where"]
    if where == "end":
        assert "nothing: the chapter has ended" in c["coming"] and just[-1] == "S3P3X6W10"
    assert ("minutes into a" in c["where"]) and c["chapter"].startswith("Title: A Synthetic Chapter")


def test_later_outline_and_dossier(chapter):
    c = chapter.context(5.0)
    assert c["later"].startswith("The rest of segment 2, then:") and "Segment 3: Part 3" in c["later"]
    # current (1) and next (2) segments' entries in full; segment 3's in a line
    assert "Entry 1 says" in c["dossier"] and "Source: https://example.org/6" in c["dossier"]
    assert "Source: https://example.org/7" not in c["dossier"] and "[D07] Entry 7 says" in c["dossier"]
    assert "L01 (rank 1, claim): Idea 1." in c["ledger"] and "A parked idea." in c["ledger"]
    assert c["earlier_asides"] == "(none)"
    assert set(c) == set(voice.ASIDE_PLACEHOLDERS)  # every placeholder the agent's prompt has


def test_locate(chapter):
    ch = chapter
    s1, s2 = ch.sents[0], ch.sents[1]
    assert ch.locate(0) == 0 and ch.locate(s1.end - 0.01) == 0
    assert ch.locate(s1.end + 0.1) == 1  # in the pause after a sentence: the next one
    assert ch.locate(s2.start + 0.1) == 1
    assert ch.locate(ch.duration) == len(ch.sents) - 1


# --- check 3: restart points ---------------------------------------------------------------------

def sid(ch, seg, n):
    return f"seg{seg:02d}-s{n:03d}"


@pytest.mark.parametrize("seconds,rule,expect", [
    (10, "sentence", 9), (29.9, "sentence", 9), (30, "previous", 8), (60, "previous", 8), (120, "previous", 8),
    (120.1, "paragraph", 7), (600, "paragraph", 7)])
def test_restart_bands(chapter, seconds, rule, expect):
    ch = chapter
    s = next(x for x in ch.sents if x.id == sid(ch, 2, 9))  # paragraph 2, its third sentence
    r = ch.restart_point(s.start + 2.0, seconds)
    assert r["rule"] == rule and r["sentence_id"] == sid(ch, 2, expect)
    assert r["t"] == F.sentence_starts(ch.run)[r["sentence_id"]]


@pytest.mark.parametrize("rewind,expect", [("sentence", 9), ("previous", 8), ("paragraph", 7)])
@pytest.mark.parametrize("seconds", [5, 60, 300])
def test_rewind_overrides(chapter, rewind, expect, seconds):
    ch = chapter
    s = next(x for x in ch.sents if x.id == sid(ch, 2, 9))
    r = ch.restart_point(s.start + 2.0, seconds, rewind)
    assert r["rule"] == rewind and r["sentence_id"] == sid(ch, 2, expect)


@pytest.mark.parametrize("rewind", [None, "previous", "paragraph"])
def test_restart_never_crosses_a_segment_start(chapter, rewind):
    ch = chapter
    first = next(x for x in ch.sents if x.id == sid(ch, 2, 1))
    for t in (first.start + 1.0, first.start - 1.0):  # in it, and in the gap before it
        r = ch.restart_point(t, 300 if rewind is None else 5, rewind)
        assert r["sentence_id"] == sid(ch, 2, 1)


def test_restart_at_the_very_start_and_end(chapter):
    ch = chapter
    assert ch.restart_point(0.2, 60)["t"] == 0.0
    last = ch.sents[-1]
    r = ch.restart_point(ch.duration, 60)
    assert r["sentence_id"] == ch.sents[-2].id and r["rule"] == "previous"
    assert ch.restart_point(ch.duration, 300)["sentence_id"] == sid(ch, 3, 13)  # its paragraph's first
    assert last.id == sid(ch, 3, 18)


# --- the routes and the log ----------------------------------------------------------------------

@pytest.fixture
def app(tmp_path, cfg, monkeypatch):
    F.build(tmp_path / "runs" / "synthetic", audio=False)
    cfg["listener_dir"] = str(tmp_path / "listener")
    monkeypatch.setattr(voice, "signed_url", lambda cfg, client=None, kind="guide": f"wss://example/{kind}")
    pipe = server.Pipeline(runs_dir=tmp_path / "runs")
    return TestClient(server.create_app(pipe, cfg, remember_on_start=False)), tmp_path / "runs" / "synthetic"


def test_an_aside_from_start_to_log(app, cfg):
    c, root = app
    res = c.post("/api/ask/start", json={"run": "synthetic", "t": 52.3}).json()
    assert res["signed_url"] == "wss://example/aside"
    ch = A.Chapter(root, cfg)
    assert res["sentence_id"] == ch.sents[ch.locate(52.3)].id == "seg01-s010"
    assert set(res["dynamic_variables"]) == set(voice.ASIDE_PLACEHOLDERS)
    assert res["notes"]["opened"].startswith("[") and res["settings"]["give_up_secs"] > 0
    aid = res["aside"]
    assert c.post("/api/ask/line", json={"aside": aid, "who": "guide", "text": "Yes?"}).json() == {"ok": True}
    c.post("/api/ask/line", json={"aside": aid, "who": "listener", "text": "Who wrote this?"})
    out = c.post("/api/ask/tool", json={"aside": aid, "name": "save_seed",
                                        "args": {"topic": "Numbers in poetry", "why": "They asked"}}).json()
    assert out["saved"]
    assert c.post("/api/ask/tool", json={"aside": aid, "name": "resume_chapter",
                                         "args": {"reason": "closing"}}).json()["resuming"] is True
    end = c.post("/api/ask/end", json={"aside": aid, "how": "guide", "tap_to_ready_ms": 900,
                                       "tap_to_first_sound_ms": 1800}).json()
    assert end["ended"] == "closing" and end["restart"]["rule"] == "sentence"
    log = A.load_asides(root)
    assert len(log) == 1 and log[0]["ended"] == "closing" and log[0]["sentence_id"] == res["sentence_id"]
    assert [x["who"] for x in log[0]["transcript"]] == ["guide", "listener", "tool", "tool"]
    assert log[0]["seeds"] == [{"topic": "Numbers in poetry", "why": "They asked"}]
    assert log[0]["tap_to_first_sound_ms"] == 1800 and log[0]["t"] == 52.3
    assert "## Numbers in poetry" in (root / "12_asks" / "seeds.md").read_text()
    assert c.post("/api/ask/end", json={"aside": aid, "how": "button"}).status_code == 409  # once
    # the next aside hears about this one
    nxt = c.post("/api/ask/start", json={"run": "synthetic", "t": 100}).json()
    assert 'you opened with "Yes?"; they said "Who wrote this?"' in nxt["dynamic_variables"]["earlier_asides"]
    # and the episode page can read the log
    assert c.get("/runs/synthetic/12_asks/asides.jsonl").status_code == 200


def test_the_sentence_recorded_is_the_one_being_spoken(app, cfg):
    c, root = app
    ch = A.Chapter(root, cfg)
    for s in (ch.sents[0], ch.sents[25], ch.sents[-1]):
        res = c.post("/api/ask/start", json={"run": "synthetic", "t": s.start + 1.0}).json()
        assert res["sentence_id"] == s.id


def test_a_new_aside_ends_one_left_open(app):
    c, root = app
    first = c.post("/api/ask/start", json={"run": "synthetic", "t": 10}).json()["aside"]
    c.post("/api/ask/start", json={"run": "synthetic", "t": 20})
    assert [(x["id"], x["ended"]) for x in A.load_asides(root)] == [(first, "dropped")]
    assert c.post("/api/ask/line", json={"aside": first, "who": "guide", "text": "x"}).status_code == 409


def test_endings_from_the_page(app):
    c, root = app
    for how in ("button", "dropped", "timeout", "nonsense"):
        aid = c.post("/api/ask/start", json={"run": "synthetic", "t": 10}).json()["aside"]
        c.post("/api/ask/end", json={"aside": aid, "how": how})
    assert [x["ended"] for x in A.load_asides(root)] == ["button", "dropped", "timeout", "other"]


def test_lost_listener_rewinds_to_the_paragraph(app):
    c, root = app
    aid = c.post("/api/ask/start", json={"run": "synthetic", "t": 52.3}).json()["aside"]
    c.post("/api/ask/tool", json={"aside": aid, "name": "resume_chapter", "args": {"reason": "other", "rewind": "paragraph"}})
    end = c.post("/api/ask/end", json={"aside": aid, "how": "guide"}).json()
    assert end["restart"]["rule"] == "paragraph"


def test_chapter_status_and_page(app, tmp_path):
    c, root = app
    s = c.get("/api/status", params={"chapter": "synthetic"}).json()
    assert s["state"] == "problem"  # no audio in this fixture
    (root / "10_audio").mkdir()
    (root / "10_audio" / "chapter.mp3").write_bytes(b"ID3")
    s = c.get("/api/status", params={"chapter": "synthetic"}).json()
    assert s == {"run": "synthetic", "state": "ready", "title": "A Synthetic Chapter", "asks": True, "episode": False}
    assert c.get("/api/status", params={"chapter": "nope"}).json()["state"] == "problem"
    js = c.get("/ask.js").text
    assert "__ASK_SETTINGS__" not in js and '"give_up_secs"' in js
    assert '<script src="/ask.js"></script>' in c.get("/").text


def test_a_chapter_without_timing_cannot_be_asked_about(app):
    c, root = app
    (root / "11_episode" / "timing.json").unlink()
    assert c.post("/api/ask/start", json={"run": "synthetic", "t": 1}).status_code == 400
