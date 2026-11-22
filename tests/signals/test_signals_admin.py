"""The admin's Listener Signals timeline (signals/routes.py, page/admin_signals.html): the guard, "own", the
conversation list, one conversation on its axis across two GPT-Live sessions, the whole visit with the norm carried
from the intro, marks, the recordings, and the page in headless Chromium. Fixture rows follow demo/visit.py's tests
(tests/demo/test_visit.py), with records written the way the replay and the page write them."""

import base64
import json
import socket
import threading
import time
import wave
from datetime import datetime, timedelta

import pytest

import server
from demo import testers
from signals import live, record, store
from stages import common

from tester_fakes import client, demo_cfg, jsonl, write
from test_visit import DEBRIEF, GUIDE, INTRO

GUIDE_STEM, INTRO_STEM, DEBRIEF_STEM = "20261120-212539", "intro-20261120-212519", "debrief-20261120-213257"
# Ian's guide conversation with one more line in its first GPT-Live session (live_g1; live_g2 opens at 21:29:50).
WATER = {"t": "2026-11-20T21:27:05", "who": "listener", "text": "I sat by the water for an hour and watched the birds.",
         "mode": "live"}
GUIDE2 = GUIDE[:8] + [WATER] + GUIDE[8:]
ANCHOR = {"live_i": datetime(2026, 11, 20, 21, 25, 19), "live_g1": datetime(2026, 11, 20, 21, 25, 40),
          "live_g2": datetime(2026, 11, 20, 21, 29, 50), "live_d": datetime(2026, 11, 20, 21, 32, 57)}
READING = [{"t": "2026-11-20T21:30:40", "id": "s1-p01", "sentence": 0, "cut": False, "heard": "x", "live_id": "live_g2",
            "times": {"start_ms": 25000, "end_ms": 48000}},
           {"t": "2026-11-20T21:31:45", "id": "s1-p02", "sentence": 0, "cut": False, "heard": "y", "live_id": "live_g2",
            "times": {"start_ms": 49000, "end_ms": 110000}}]


@pytest.fixture
def dcfg(cfg):
    return demo_cfg(cfg)


@pytest.fixture
def no_admin_env(monkeypatch):
    for k in ("ADMIN_USER", "ADMIN_PASSWORD", "WL_BASE_URL"):
        monkeypatch.delenv(k, raising=False)


def basic(user, pw):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def tiny_wav(p, seconds=1.0, rate=8000):
    p.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return p


def rec(stem, turn, live_id, text, start_s, dur_s, **raw):
    """A recording record as the replay writes one: its session's clock and the wall time of its first frame."""
    t = (ANCHOR[live_id] + timedelta(seconds=start_s)).isoformat(timespec="milliseconds")
    return record.build({"start_s": start_s, "end_s": start_s + dur_s, "dur_ms": dur_s * 1000,
                         "speak_ms": dur_s * 1000, "pauses": 0, "longest_pause_ms": 0, **raw}, text, stem=stem,
                        part=store.part_of(stem), source="recording", turn=turn, live_id=live_id, t=t,
                        S=record.settings({}))


def seed(dcfg, folder=None):
    """A tester's visit: the interviewer's intro, the guide's conversation over two GPT-Live sessions (an earpiece
    look whose whisper was sent), the chapter read aloud in the second, the debrief; recording records for six
    listener turns and one page record; one mark; recordings on disk for three of the four sessions."""
    if folder is None:
        lid = testers.create(dcfg, "Ian run 4", name="Ian")["lid"]
        d = testers.folder(lid)
    else:
        lid, d = None, folder
    jsonl(d / "study" / f"{INTRO_STEM}.jsonl", INTRO)
    jsonl(d / "study" / f"{DEBRIEF_STEM}.jsonl", DEBRIEF)
    jsonl(d / "study" / "events.jsonl", [])
    jsonl(d / "sessions" / f"{GUIDE_STEM}.jsonl", GUIDE2)
    jsonl(d / "sessions" / "log.jsonl", [{"stage": "voice", "provider": "openai", "session": GUIDE_STEM,
                                          "live_id": "live_g1", "seconds": 250.0, "cost_usd": 0.2}])
    ch = d / "chapters" / "places-20261120"
    write(ch / "status.json", {"state": "stopped", "started": "2026-11-20T21:28:32", "updated": "2026-11-20T21:32:57",
                               "title": "What the Heron Is Doing", "run": ch.name, "mode": "fast"})
    jsonl(ch / "live" / "reading.jsonl", READING)
    store.write_recordings(d, INTRO_STEM, [
        rec(INTRO_STEM, 0, "live_i", "Hey, yeah, this is Ian", 5.0, 1.6, gap_ms=800, rms_db=-30.0, pitch_sd_st=3.0),
        rec(INTRO_STEM, 1, "live_i", "Sure", 19.0, 0.4, gap_ms=600, rms_db=-31.0)])
    store.write_recordings(d, GUIDE_STEM, [
        rec(GUIDE_STEM, 0, "live_g1", "I was in Crandon Park today.", 30.0, 2.0, gap_ms=900, rms_db=-29.0,
            pitch_sd_st=3.2),
        rec(GUIDE_STEM, 1, "live_g1", WATER["text"], 80.0, 4.0, gap_ms=1000, rms_db=-31.0, pitch_sd_st=2.8),
        rec(GUIDE_STEM, 2, "live_g2", "Yeah, sure.", 19.5, 0.9, gap_ms=3000, rms_db=-38.0, pitch_sd_st=1.2)])
    live.record_line(d, GUIDE_STEM, part="guide", live_id="live_g2", text="[inhale", cfg={},
                     sig={"turn": 0, "gap_ms": 400, "dur_ms": 800, "rms_db": -25.0, "ago_ms": 3000},
                     now=datetime(2026, 11, 20, 21, 32, 1))      # no session clock: placed by its wall time
    live.record_line(d, GUIDE_STEM, part="guide", live_id="live_g2", text="]Wait, I didn't ask about herons", cfg={},
                     sig={"turn": 0, "gap_ms": 400, "dur_ms": 2400, "rms_db": -25.0, "ago_ms": 3000},
                     now=datetime(2026, 11, 20, 21, 32, 2))
    store.write_recordings(d, DEBRIEF_STEM, [
        rec(DEBRIEF_STEM, 0, "live_d", "The director was effective.", 13.0, 2.0, gap_ms=900, rms_db=-30.0,
            pitch_sd_st=2.9)])
    store.add_mark(d, GUIDE_STEM, 100.0, "lost them here", t="2026-11-21T09:00:00")
    tiny_wav(store.audio_path(d, "live_g1"), seconds=40)
    write(d / "study" / "live_i.mp3", "ID3 not really")
    tiny_wav(ch / "live" / "live_g2.wav")
    return lid


def others(d):
    """A library session (a mode, no recording) and a conversation typed with the guide (no GPT-Live session)."""
    jsonl(d / "sessions" / "20261120-200000.jsonl", [
        {"t": "2026-11-20T20:00:00", "who": "session", "live_id": "lib_1", "mode": "intro", "run": "x"},
        {"t": "2026-11-20T20:00:05", "who": "guide", "text": "Here is tonight's chapter."}])
    jsonl(d / "sessions" / "20261120-190000.jsonl", [
        {"t": "2026-11-20T19:00:00", "who": "session", "names": ["Hanne"], "returning": False},
        {"t": "2026-11-20T19:00:05", "who": "listener", "text": "hello"}])


# --- the guard -------------------------------------------------------------------------------------------

def paths(lid):
    return ("/admin/signals", f"/admin/signals?lid={lid}", f"/api/admin/signals/{lid}",
            f"/api/admin/signals/{lid}/visit", f"/api/admin/signals/{lid}/{GUIDE_STEM}",
            f"/api/admin/signals/{lid}/audio/live_g1", "/api/admin/signals/own")


def test_the_routes_are_admin_only(dcfg, tmp_path, no_admin_env, monkeypatch):
    lid = seed(dcfg)
    token = testers.record(lid)["token"]
    mark = f"/api/admin/signals/{lid}/{GUIDE_STEM}/mark"
    out = client(dcfg, tmp_path, host="203.0.113.5")
    for p in paths(lid):
        assert out.get(p).status_code == 403, p
    assert out.post(mark, json={"secs": 1, "text": "x"}).status_code == 403
    out.cookies.set("wl_tester", token)   # a tester's own cookie is not admin
    for p in paths(lid):
        assert out.get(p).status_code == 403, p
    proxied = client(dcfg, tmp_path)
    for p in paths(lid):
        assert proxied.get(p, headers={"X-Forwarded-For": "198.51.100.7"}).status_code == 403, p
    monkeypatch.setenv("ADMIN_USER", "ian")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3cret")
    for p in paths(lid):
        r = out.get(p)
        assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic"), p
    assert out.post(mark, json={"secs": 1, "text": "x"}).status_code == 401
    assert out.get(f"/api/admin/signals/{lid}", headers=basic("ian", "wrong")).status_code == 401
    assert out.get(f"/api/admin/signals/{lid}", headers=basic("ian", "s3cret")).json()["label"] == "Ian run 4"
    assert len(store.marks(testers.folder(lid), GUIDE_STEM)) == 1   # nothing refused was written


def test_the_page_and_the_admin_links(dcfg, tmp_path, no_admin_env):
    c = client(dcfg, tmp_path)
    page = c.get("/admin/signals?lid=t-x&session=visit")
    assert page.status_code == 200 and "/api/admin/signals/" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert "/admin/signals?lid=" in c.get("/admin").text and "/admin/signals?lid=own" in c.get("/admin").text


# --- the list, one conversation, the visit ---------------------------------------------------------------------

def test_the_conversation_list(dcfg, tmp_path, no_admin_env):
    lid = seed(dcfg)
    others(testers.folder(lid))   # left out of the list
    got = client(dcfg, tmp_path).get(f"/api/admin/signals/{lid}").json()
    assert got["label"] == "Ian run 4"
    cs = {c["stem"]: c for c in got["conversations"]}
    assert [c["stem"] for c in got["conversations"]] == [DEBRIEF_STEM, GUIDE_STEM, INTRO_STEM]  # newest first
    g = cs[GUIDE_STEM]
    assert g["part"] == "guide" and g["started"] == "2026-11-20T21:25:39" and g["turns"] == {"recording": 3, "page": 1}
    assert g["marks"] == 1 and g["audio"] == {"live_g1": True, "live_g2": True} and g["transcript"] is True
    assert cs[INTRO_STEM]["part"] == "intro" and cs[INTRO_STEM]["audio"] == {"live_i": True}
    assert cs[DEBRIEF_STEM]["audio"] == {"live_d": False} and cs[DEBRIEF_STEM]["turns"] == {"recording": 1, "page": 0}
    assert client(dcfg, tmp_path).get("/api/admin/signals/t-nobody").status_code == 404


def test_a_conversation_with_only_records_is_listed(dcfg, tmp_path, no_admin_env):
    lid = testers.create(dcfg, "Pilot 1")["lid"]
    d = testers.folder(lid)
    store.write_recordings(d, "20261121-101500", [rec("20261121-101500", 0, "live_g1", "Just this.", 3.0, 1.0)])
    c = client(dcfg, tmp_path)
    got = c.get(f"/api/admin/signals/{lid}").json()["conversations"]
    assert [(x["stem"], x["transcript"], x["turns"]["recording"]) for x in got] == [("20261121-101500", False, 1)]
    one = c.get(f"/api/admin/signals/{lid}/20261121-101500").json()
    assert one["records"][0]["secs"] == 0.0 and one["items"] == []   # no transcript: the axis starts at the record


def test_one_conversation_across_two_gpt_live_sessions(dcfg, tmp_path, no_admin_env):
    lid = seed(dcfg)
    got = client(dcfg, tmp_path).get(f"/api/admin/signals/{lid}/{GUIDE_STEM}").json()
    assert got["part"] == "guide" and got["started"] == "2026-11-20T21:25:39" and got["label"] == "Ian run 4"
    # Each GPT-Live session's zero on this axis (its session row), with the recording's length from the usage log
    assert got["sessions"] == [{"live_id": "live_g1", "secs": 1.0, "seconds": 250.0},
                               {"live_id": "live_g2", "secs": 251.0, "seconds": None}]
    recs = got["records"]
    assert [(r["source"], r["turn"], r["text"]) for r in recs] == [
        ("recording", 0, "I was in Crandon Park today."), ("recording", 1, WATER["text"]),
        ("recording", 2, "Yeah, sure."), ("page", 0, "[inhale]Wait, I didn't ask about herons")]
    # The session row's secs plus start_s; the page record (no session clock) by its wall time
    assert [r["secs"] for r in recs] == [31.0, 81.0, 270.5, 379.0]
    assert [r["end_secs"] for r in recs] == [33.0, 85.0, 271.4, 381.4]
    # Placed in the visit's context: the intro's turn counts, so the third turn here already has a norm
    assert recs[1]["norm"] is None and recs[1]["norm_turns"] == 2
    g2 = recs[2]
    assert g2["norm"]["words"] == 6 and g2["norm_turns"] == 3
    assert {"words", "gap_ms", "rms_db", "pitch_sd_st"} <= set(g2["mention"]) and g2["note"].startswith("Their last turn:")
    assert recs[3]["norm"] is None and recs[3]["tags"] == ["inhale"]   # the page is judged against the page
    # Each record's lines and each line's record
    lines = {x["text"]: x for x in got["items"] if x["kind"] == "line" and x["who"] == "listener"}
    assert lines["I was in Crandon Park today."]["rec"] == 0 and lines[WATER["text"]]["rec"] == 1
    assert lines["Yeah, sure."]["rec"] == 2 and lines["Wait, I didn't ask about herons"]["rec"] == 3
    assert [got["items"][i]["text"] for i in recs[2]["lines"]] == ["Yeah, sure."]
    assert all("rec" not in x for x in got["items"] if x["kind"] == "line" and x["who"] == "guide")
    # The earpiece's look on the same axis, with its fate; no look in the transcript items
    assert [(L["id"], L["secs"], L["asked_secs"], L["note"], L["fate"]["fate"], L["fate"]["secs"]) for L in got["looks"]] \
        == [(1, 21.0, 18.0, "Ask about the park.", "sent", 22.0)]
    assert not any(x["kind"] == "look" for x in got["items"])
    assert got["marks"] == [{"t": "2026-11-21T09:00:00", "secs": 100.0, "text": "lost them here"}]
    assert got["audio"] == {"live_g1": True, "live_g2": True}


def test_a_study_conversation_has_its_one_session_at_zero(dcfg, tmp_path, no_admin_env):
    lid = seed(dcfg)
    got = client(dcfg, tmp_path).get(f"/api/admin/signals/{lid}/{INTRO_STEM}").json()
    assert got["part"] == "intro" and got["sessions"] == [{"live_id": "live_i", "secs": 0.0, "seconds": None}]
    assert [(r["secs"], r["text"]) for r in got["records"]] == [(5.0, "Hey, yeah, this is Ian"), (19.0, "Sure")]
    who = [x["who"] for x in got["items"] if x["kind"] == "line"]
    assert "interviewer" in who and got["looks"] == []
    assert next(x for x in got["items"] if x.get("text") == "Sure")["rec"] == 1


def test_unknown_conversations_are_404(dcfg, tmp_path, no_admin_env):
    lid = seed(dcfg)
    c = client(dcfg, tmp_path)
    for stem in ("log", "20261101-000000", "..%2Ftester", "intro-x", "debrief-20261101-000000"):
        assert c.get(f"/api/admin/signals/{lid}/{stem}").status_code == 404, stem
    assert c.get(f"/api/admin/signals/t-nobody/{GUIDE_STEM}").status_code == 404


def test_the_whole_visit_on_one_axis(dcfg, tmp_path, no_admin_env):
    lid = seed(dcfg)
    got = client(dcfg, tmp_path).get(f"/api/admin/signals/{lid}/visit").json()
    assert got["started"] == "2026-11-20T21:25:19" and got["name"] == "Ian" and got["label"] == "Ian run 4"
    parts = [(p["part"], p["stem"], p["secs"], p["offset"]) for p in got["parts"]]
    assert parts == [("intro", INTRO_STEM, 0.0, 0.0), ("guide", GUIDE_STEM, 20.0, 20.0),
                     ("debrief", DEBRIEF_STEM, 458.0, 458.0)]
    assert all(p["end_secs"] >= p["secs"] for p in got["parts"])
    assert got["parts"][1]["end_secs"] >= 381.4
    # The chapter read aloud: its paragraphs on live_g2's clock, one stretch
    assert got["chapters"] == [{"run": "places-20261120", "title": "What the Heron Is Doing",
                                "spans": [{"secs": 296.0, "end_secs": 381.0}]}]
    recs = got["records"]
    assert [(r["stem"], r["secs"]) for r in recs] == [
        (INTRO_STEM, 5.0), (INTRO_STEM, 19.0), (GUIDE_STEM, 51.0), (GUIDE_STEM, 101.0), (GUIDE_STEM, 290.5),
        (GUIDE_STEM, 399.0), (DEBRIEF_STEM, 471.0)]
    # The norm carries: the debrief's one turn is judged against the intro's and the guide's
    d = recs[-1]
    assert d["norm"] is not None and d["norm_turns"] == 3 and d["norm"]["words"] == 6
    alone = record.annotate([r for r in store.visit(testers.folder(lid)) if r["stem"] == DEBRIEF_STEM],
                            record.settings({}))
    assert alone[0]["norm"] is None
    # Lines and records point at each other across parts
    for ri, r in enumerate(recs):
        assert r["lines"] and all(got["items"][i]["rec"] == ri and got["items"][i]["stem"] == r["stem"]
                                  for i in r["lines"]), r["text"]
    assert {got["items"][i]["text"] for i in recs[-1]["lines"]} == {"The director was effective."}
    # Marks of every conversation, on the visit's axis; the earpiece's look too
    assert [(m["stem"], m["secs"], m["conv_secs"], m["text"]) for m in got["marks"]] == [
        (GUIDE_STEM, 120.0, 100.0, "lost them here")]
    assert [(L["stem"], L["secs"], L["fate"]["fate"], L["fate"]["secs"]) for L in got["looks"]] == [
        (GUIDE_STEM, 41.0, "sent", 42.0)]
    assert {s["live_id"]: s["secs"] for s in got["sessions"]} == {"live_i": 0.0, "live_g1": 21.0, "live_g2": 271.0,
                                                                   "live_d": 458.0}
    assert got["audio"]["live_g1"] and got["audio"]["live_i"] and not got["audio"]["live_d"]
    # The parts, lines and events demo/visit.py gives, on the same axis
    texts = [x["text"] for x in got["items"]]
    assert "Hey, Ian! I'm Hanne." in texts and "Ask about the park." not in texts


def test_your_own_with_demo_off(cfg, tmp_path, no_admin_env):
    ocfg = demo_cfg(cfg, enabled=False)
    own = common.listener_dir(ocfg)
    seed(ocfg, folder=own)
    c = client(ocfg, tmp_path)
    got = c.get("/api/admin/signals/own").json()
    assert got["label"] == "Your own" and GUIDE_STEM in [x["stem"] for x in got["conversations"]]
    assert c.get(f"/api/admin/signals/own/{GUIDE_STEM}").json()["records"][0]["secs"] == 31.0
    v = c.get("/api/admin/signals/own/visit").json()
    assert v["name"] == "You" and len(v["records"]) == 7
    assert c.post(f"/api/admin/signals/own/{GUIDE_STEM}/mark", json={"secs": 5, "text": "mine"}).status_code == 200
    assert store.marks(own, GUIDE_STEM)[-1]["text"] == "mine"


# --- marks ------------------------------------------------------------------------------------------------

def test_marks_are_saved_and_checked(dcfg, tmp_path, no_admin_env):
    lid = seed(dcfg)
    d = testers.folder(lid)
    c = client(dcfg, tmp_path)
    url = f"/api/admin/signals/{lid}/{DEBRIEF_STEM}/mark"
    got = c.post(url, json={"secs": 12.345, "text": "  flat   answer  "})
    assert got.status_code == 200 and got.json()["mark"]["secs"] == 12.35 and got.json()["mark"]["text"] == "flat answer"
    assert store.marks(d, DEBRIEF_STEM)[0]["text"] == "flat answer"
    assert c.get(f"/api/admin/signals/{lid}/{DEBRIEF_STEM}").json()["marks"][0]["text"] == "flat answer"
    assert c.post(url, json={"secs": 1, "text": "   "}).status_code == 400
    assert c.post(url, json={"secs": 1e9, "text": "far"}).status_code == 400
    assert c.post(url, content='{"secs": NaN, "text": "x"}', headers={"content-type": "application/json"}).status_code == 400
    assert c.post(url, json={"secs": 1, "text": "x" * 2001}).status_code == 422
    assert c.post(url, json={"text": "no time"}).status_code == 422
    long = c.post(url, json={"secs": 2, "text": "y" * 900}).json()["mark"]
    assert len(long["text"]) == store.MARK_MAX
    for stem in ("log", "visit", "20261101-000000"):    # not a conversation, or none of theirs
        assert c.post(f"/api/admin/signals/{lid}/{stem}/mark", json={"secs": 1, "text": "x"}).status_code == 404, stem
    assert not (d / "signals" / "20261101-000000.marks.jsonl").exists()
    assert c.post(f"/api/admin/signals/t-nobody/{GUIDE_STEM}/mark", json={"secs": 1, "text": "x"}).status_code == 404


# --- recordings -------------------------------------------------------------------------------------------

def test_the_recordings_are_served_with_ranges(dcfg, tmp_path, no_admin_env):
    lid = seed(dcfg)
    d = testers.folder(lid)
    c = client(dcfg, tmp_path)
    whole = c.get(f"/api/admin/signals/{lid}/audio/live_g1")
    assert whole.status_code == 200 and whole.headers["content-type"] == "audio/wav"
    assert whole.content == store.audio_path(d, "live_g1").read_bytes() and whole.headers["accept-ranges"] == "bytes"
    part = c.get(f"/api/admin/signals/{lid}/audio/live_g1", headers={"Range": "bytes=0-9"})
    assert part.status_code == 206 and part.content == whole.content[:10]
    assert part.headers["content-range"] == f"bytes 0-9/{len(whole.content)}"
    mp3 = c.get(f"/api/admin/signals/{lid}/audio/live_i")       # the interviewer's, kept in study/
    assert mp3.status_code == 200 and mp3.headers["content-type"] == "audio/mpeg"
    ch = c.get(f"/api/admin/signals/{lid}/audio/live_g2")       # a chapter's archive
    assert ch.status_code == 200 and ch.content == (d / "chapters" / "places-20261120" / "live" / "live_g2.wav").read_bytes()
    write(d / "secret.wav", "no")
    for bad in ("live_d", "bad.id", "..%2Fsecret", "..%2F..%2Fsecret", "%2E%2E", "x" * 200):
        assert c.get(f"/api/admin/signals/{lid}/audio/{bad}").status_code == 404, bad
    assert c.get(f"/api/admin/signals/t-nobody/audio/live_g1").status_code == 404


# --- the page, in headless Chromium -----------------------------------------------------------------------

@pytest.fixture
def site(dcfg, tmp_path, no_admin_env):
    import uvicorn

    sync_api = pytest.importorskip("playwright.sync_api")

    runs = tmp_path / "runs"
    runs.mkdir()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(server.create_app(server.Pipeline(runs_dir=runs), dcfg,
                                                          remember_on_start=False),
                                        host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    deadline = time.monotonic() + 10
    while not srv.started and time.monotonic() < deadline:
        time.sleep(0.05)
    pw = sync_api.sync_playwright().start()
    browser = pw.chromium.launch()
    yield f"http://127.0.0.1:{port}", browser
    browser.close()
    pw.stop()
    srv.should_exit = True


def test_the_timeline_points_lines_and_marks(dcfg, site):
    base, browser = site
    lid = seed(dcfg)
    page = browser.new_page()
    page.goto(base + "/admin")
    link = page.wait_for_selector(f"tr[data-lid='{lid}'] a[href^='/admin/signals']")
    assert link.get_attribute("href") == f"/admin/signals?lid={lid}"
    link.click()
    page.wait_for_selector(f"tr[data-stem='{GUIDE_STEM}']")
    assert page.inner_text("#title") == "Signals: Ian run 4"
    assert page.get_attribute("#visit-link", "href") == f"?lid={lid}&session=visit"
    page.click(f"tr[data-stem='{GUIDE_STEM}'] a")
    page.wait_for_selector("#chart circle.pt")

    # A point per turn in each strip where it was measured; the turn before a norm hollow, on the line
    count = lambda sel: page.eval_on_selector_all(sel, "(es) => es.length")  # noqa: E731
    assert {m: count(f"#chart circle[data-m='{m}']") for m in ("gap_ms", "words", "wps", "pitch_sd_st", "rms_db")} == \
        {"gap_ms": 4, "words": 4, "wps": 4, "pitch_sd_st": 3, "rms_db": 4}
    assert "hollow" in page.get_attribute("#chart circle[data-rec='1'][data-m='words']", "class")
    assert "mention" in page.get_attribute("#chart circle[data-rec='2'][data-m='words']", "class")
    assert "[inhale]" in page.text_content("#chart")        # the page record's tag on the top edge
    assert count("#chart .wh[data-fate='sent']") == 1 and count("#chart .flag") == 1

    # A point selects its line and shows its numbers; a line selects its points
    page.click("#chart circle[data-rec='0'][data-m='words']")
    assert "Crandon Park" in page.inner_text("#transcript li.sel")
    det = page.inner_text("#detail")
    assert "Turn 0" in det and "Pause before answering" in det and "no norm yet" in det
    page.click("#transcript li:has-text('Yeah, sure.')")
    assert count("#chart circle.sel[data-rec='2']") == 5
    det = page.inner_text("#detail")
    assert "×0.33" in det and "Note for the earpiece: Their last turn:" in det
    assert page.get_attribute("#detail tr[data-m='words']", "class") == "hit"

    # A mark: an empty moment on the chart, typed inline, saved, shown as a flag and in the transcript
    box = page.query_selector("#chart").bounding_box()
    page.locator("#chart").click(position={"x": box["width"] * 0.5, "y": box["height"] - 4})
    page.wait_for_selector("#markbox:not([hidden])")
    page.fill("#mark-text", "flat after the question")
    page.click("#markbox button:not([type])")
    page.wait_for_function("document.querySelectorAll('#chart .flag').length === 2")
    assert "flat after the question" in page.inner_text("#transcript")
    saved = store.marks(testers.folder(lid), GUIDE_STEM)
    assert [m["text"] for m in saved] == ["lost them here", "flat after the question"] and saved[1]["secs"] > 0

    # Playback from a point when its recording is on disk (live_g1 for the first turn)
    page.click("#chart circle[data-rec='0'][data-m='words']")
    page.click("#play")
    page.wait_for_function("document.getElementById('player').src.endsWith('/audio/live_g1')")
    page.wait_for_function("document.getElementById('player').currentTime >= 28.5", timeout=10000)


def test_the_visit_view_fits_a_phone(dcfg, site):
    base, browser = site
    lid = seed(dcfg)
    phone = browser.new_page(viewport={"width": 375, "height": 812})
    phone.goto(base + f"/admin/signals?lid={lid}&session=visit")
    phone.wait_for_selector("#chart circle.pt")
    assert phone.inner_text("#title") == "Whole visit: Ian run 4"
    assert phone.eval_on_selector_all("#chart rect.band.chapter", "(es) => es.length") == 2   # band and shade
    assert phone.eval_on_selector_all("#chart circle[data-m='words']", "(es) => es.length") == 7
    assert phone.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    phone.click("#zoom-in")
    phone.click("#zoom-in")
    assert phone.evaluate("document.getElementById('wrap').scrollWidth > document.getElementById('wrap').clientWidth")
    assert phone.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    phone.click("#transcript li:has-text('The director was effective.')")
    assert "Debrief" in phone.inner_text("#detail")
    assert phone.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    phone.goto(base + f"/admin/signals?lid={lid}")
    phone.wait_for_selector(f"tr[data-stem='{GUIDE_STEM}']")
    assert phone.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_a_mark_in_the_visit_lands_on_its_conversations_axis(dcfg, site):
    base, browser = site
    lid = seed(dcfg)
    page = browser.new_page()
    page.goto(base + f"/admin/signals?lid={lid}&session=visit")
    page.wait_for_selector("#chart circle.pt")
    # Ask the page to mark visit second 471 (the debrief's turn): it posts on the debrief's own axis (471 - 458)
    page.evaluate("openMark(471)")
    assert "debrief" in page.inner_text("#mark-at")
    page.fill("#mark-text", "warmer here")
    page.click("#markbox button:not([type])")
    page.wait_for_function("[...document.querySelectorAll('#chart .flag')].length === 2")
    saved = store.marks(testers.folder(lid), DEBRIEF_STEM)
    assert [(m["secs"], m["text"]) for m in saved] == [(13.0, "warmer here")]


def test_records_find_their_lines():
    """A turn's lines are the rows the page posted for it: a tag split across two rows, two turns where the second's
    one word is also in the first, a typed line between with no record."""
    from signals.routes import link
    items = [{"i": i, "kind": "line", "who": who, "text": text, "secs": secs} for i, (who, text, secs) in enumerate([
        ("guide", "How was it?", 2), ("listener", "[inhale", 9), ("listener", "]Yeah, I think so", 11),
        ("guide", "Really?", 14), ("listener", "Yeah", 19), ("listener", "typed instead", 25),
        ("listener", "Then the rain came.", 40)])]
    recs = [{"text": "[inhale]Yeah, I think so", "secs": 6.0, "lines": []},
            {"text": "Yeah", "secs": 17.5, "lines": []},
            {"text": "Then the rain came.", "secs": 37.0, "lines": []}]
    link(recs, items)
    assert [r["lines"] for r in recs] == [[1, 2], [4], [6]]
    assert [x.get("rec") for x in items] == [None, 0, 0, None, 1, None, 2]


def test_older_chapter_records_are_a_part_of_their_own(dcfg, tmp_path, no_admin_env):
    """A folder spanning days (Ian's own): chapter records older than every transcript are a "records" part with no
    conversation, ending at its own last item, and the visit's axis starts there."""
    lid = seed(dcfg)
    old = testers.folder(lid) / "chapters" / "herons-20261114"
    write(old / "status.json", {"state": "ready", "started": "2026-11-14T00:11:46", "updated": "2026-11-14T01:00:05",
                                "title": "Herons", "run": old.name})
    jsonl(old / "fast_timeline.jsonl", [{"ts": "2026-11-14T00:23:17", "event": "ready"}])
    got = client(dcfg, tmp_path).get(f"/api/admin/signals/{lid}/visit").json()
    first = got["parts"][0]
    assert got["started"] == "2026-11-14T00:11:46"
    assert (first["part"], first["stem"], first["secs"], first["end_secs"], first["offset"]) == ("records", None, 0.0, 691.0, None)
    guide = next(p for p in got["parts"] if p["stem"] == GUIDE_STEM)
    assert guide["offset"] == guide["secs"] and guide["secs"] > 500000
    assert next(m for m in got["marks"])["secs"] == guide["offset"] + 100.0


def test_a_tester_with_nothing_yet(dcfg, tmp_path, no_admin_env):
    lid = testers.create(dcfg, "Pilot 1")["lid"]
    c = client(dcfg, tmp_path)
    assert c.get(f"/api/admin/signals/{lid}").json()["conversations"] == []
    v = c.get(f"/api/admin/signals/{lid}/visit").json()
    assert (v["started"], v["records"], v["parts"], v["chapters"], v["duration"]) == (None, [], [], [], 0.0)
