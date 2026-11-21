"""The admin's Transcript view (Ian, 2026-11-21): a tester's whole visit as one timeline (demo/visit.py) and its
routes. Fixture rows follow the real shapes in listeners/t-ian-run-4 and t-pooja-test-20."""

import base64

import pytest

from demo import testers, visit

from tester_fakes import client, demo_cfg, jsonl, write


@pytest.fixture
def dcfg(cfg):
    return demo_cfg(cfg)


@pytest.fixture
def no_admin_env(monkeypatch):
    for k in ("ADMIN_USER", "ADMIN_PASSWORD", "WL_BASE_URL"):
        monkeypatch.delenv(k, raising=False)


def _whisper(note: str) -> str:
    from voice.gpt_live import session as live_session
    return live_session.notes()["direct"].replace("{note}", note)


INTRO = [
    {"t": "2026-11-20T21:25:19", "s": 0.0, "who": "session", "mode": "intro", "live_id": "live_i",
     "voice": "cedar", "instructions_sha256": "abc", "tools": [{"name": "handoff_to_guide"}], "name_given": "Ian"},
    {"t": "2026-11-20T21:25:20", "s": 1.3, "who": "page", "text": "Begin now: say hello warmly."},
    {"t": "2026-11-20T21:25:22", "s": 3.5, "who": "interviewer", "text": "Hi, is this Ian?"},
    {"t": "2026-11-20T21:25:26", "s": 7.1, "who": "listener", "text": "Hey, yeah, this is Ian"},
    {"t": "2026-11-20T21:25:39", "s": 20.5, "who": "tool", "name": "handoff_to_guide", "input": {}, "by": "page",
     "result": {"handed_over": True, "name": "Ian"}},
    {"t": "2026-11-20T21:25:40", "s": 21.2, "who": "listener", "text": "Sure"},
    {"t": "2026-11-20T21:25:40", "s": 21.5, "who": "interviewer", "text": "I'll hand you over now."},
    {"t": "2026-11-20T21:25:40", "s": 21.5, "who": "session", "ended": "handover"},
]

GUIDE = [
    {"t": "2026-11-20T21:25:39", "who": "session", "names": ["Hanne", "Gwen"], "returning": False},
    {"t": "2026-11-20T21:25:40", "who": "session", "live_id": "live_g1", "voice": "gleam",
     "instructions_sha256": "def", "tools": [{"name": "start_chapter"}], "startup": {"x": 1}},
    {"t": "2026-11-20T21:25:42", "who": "page", "text": "This session picks up from the person who welcomed them.",
     "mode": "live"},
    {"t": "2026-11-20T21:25:45", "who": "guide", "text": "Hey, Ian! I'm Hanne.", "mode": "live"},
    {"t": "2026-11-20T21:26:00", "who": "director", "id": 1, "trigger": "cadence", "speaking": "none",
     "read": "Early.", "plan": [{"item": "Meet them", "state": "open"}], "next": "Meet them", "changed": True,
     "kind": "when_it_fits", "about": "x", "note": "Ask about the park.", "ms": 3000, "cost_usd": 0.01},
    {"t": "2026-11-20T21:26:01", "who": "director_fate", "id": 1, "fate": "sent", "reason": None, "detail": None,
     "page_secs": 21.0, "held_ms": None},
    {"t": "2026-11-20T21:26:01", "who": "page", "text": _whisper("Ask about the park."), "mode": "live"},
    {"t": "2026-11-20T21:26:13", "who": "listener", "text": "I was in Crandon Park today.", "mode": "live"},
    {"t": "2026-11-20T21:28:32", "who": "tool", "name": "start_chapter",
     "input": {"topic": "How places lock in memories", "listener_said": "Crandon Park"},
     "result": {"started": True, "first_audio_minutes": 2, "when": "about 2 minutes"}},
    {"t": "2026-11-20T21:28:32", "who": "page", "text": "[Their chapter is under way: \"How places lock in memories\".]",
     "mode": "live"},
    {"t": "2026-11-20T21:29:10", "who": "guide", "text": "I'll step back while I make it.", "mode": "live"},
    {"t": "2026-11-20T21:29:10", "who": "page", "text": "[The guide stepped back.]", "mode": "live"},
    {"t": "2026-11-20T21:29:50", "who": "session", "live_id": "live_g2", "voice": "gleam"},
    {"t": "2026-11-20T21:29:50", "who": "page", "text": "[A new voice session: tap.]", "mode": "live"},
    {"t": "2026-11-20T21:29:55", "who": "tool", "name": "get_status", "input": {},
     "result": {"state": "being made", "stage": "Planning the chapter", "starts_playing_in_minutes": 1}},
    {"t": "2026-11-20T21:30:09", "who": "guide", "text": "Your chapter's ready. Want to hear it now?", "mode": "live"},
    {"t": "2026-11-20T21:30:12", "who": "listener", "text": "Yeah, sure.", "mode": "live"},
    {"t": "2026-11-20T21:30:13", "who": "tool", "name": "start_reading", "input": {"listener_said": "Yeah, sure."},
     "result": {"starting": True}},
    {"t": "2026-11-20T21:31:53", "who": "page", "text": "[The listener tapped to ask something: the chapter is paused.]",
     "mode": "live"},
    {"t": "2026-11-20T21:32:01", "who": "listener", "text": "Wait, I didn't ask about herons", "mode": "live"},
    {"t": "2026-11-20T21:32:10", "who": "guide", "text": "You're right. That was my leap.", "mode": "live"},
    {"t": "2026-11-20T21:32:19", "who": "tool", "name": "resume_chapter", "input": {"reason": "listener"},
     "result": {"resuming": True}},
]

DEBRIEF = [
    {"t": "2026-11-20T21:32:57", "s": 0.0, "who": "session", "mode": "debrief", "live_id": "live_d",
     "activity": {"asks": 1}, "did": "- asked one question"},
    {"t": "2026-11-20T21:33:03", "s": 6.0, "who": "interviewer", "text": "Welcome back, Ian. What stood out?"},
    {"t": "2026-11-20T21:33:14", "s": 16.5, "who": "listener", "text": "The director was effective."},
    {"t": "2026-11-20T21:35:04", "s": 126.4, "who": "tool", "name": "end_debrief", "input": {}, "by": "page",
     "result": {"ended": True}},
    {"t": "2026-11-20T21:35:05", "s": 127.9, "who": "session", "ended": "done"},
]

EVENTS = [
    {"t": "2026-11-20T21:31:53", "event": "ask", "phase": "reading", "chapter_t": 104.0, "detail": None},
    {"t": "2026-11-20T21:32:56", "event": "end_demo", "phase": "paused", "chapter_t": 134.0, "detail": None},
]

CHAPTER = {
    "run": "places-20261120", "title": "What the Heron Is Doing While It Waits", "rehearsal": True,
    "status": {"state": "stopped", "started": "2026-11-20T21:28:32", "updated": "2026-11-20T21:32:57",
               "message": "Stopped when the demo ended."},
    "timeline": [{"ts": "2026-11-20T21:28:51", "event": "scout_done"},
                 {"ts": "2026-11-20T21:30:02", "event": "first_audio", "section": 1}],
    "asides": [{"id": "20261120-213220-live", "kind": "ask", "voice": "live", "started": "2026-11-20T21:31:53",
                "paragraph": "s1-p05", "sentence": 3, "ended": "guide", "seconds": 27.8,
                "transcript": [{"who": "listener", "text": "Wait, I didn't ask about herons"}],
                "session": "20261120-212539"}],
    "reading": [{"t": f"2026-11-20T21:3{m}:{s}", "id": pid, "sentence": sen, "cut": cut, "heard": "words"}
                for m, s, pid, sen, cut in ((0, "32", "s1-p01", 0, False), (0, "56", "s1-p02", 0, False),
                                            (1, "21", "s1-p03", 0, False), (1, "53", "s1-p04", 0, True),
                                            (2, "34", "s1-p04", 2, False))],
}


def _visit(**over):
    args = {"name": "Ian", "intros": [("intro-20261120-212519", INTRO)],
            "conversations": [("20261120-212539", GUIDE)], "debriefs": [("debrief-20261120-213257", DEBRIEF)],
            "events": EVENTS, "chapters": [CHAPTER]}
    return visit.build(**{**args, **over})


def test_the_whole_visit_in_order_with_speakers_and_plain_events():
    v = _visit()
    items = v["items"]
    parts = [x["text"] for x in items if x["kind"] == "part"]
    assert parts == ["Intro with the interviewer", "Conversation with the guide", "Debrief with the interviewer"]
    assert v["started"] == "2026-11-20T21:25:19" and v["ended"] == "2026-11-20T21:35:05"
    # Speakers: Interviewer, Guide, the tester's name
    lines = [(x["speaker"], x["text"]) for x in items if x["kind"] == "line"]
    assert lines[:4] == [("Interviewer", "Hi, is this Ian?"), ("Ian", "Hey, yeah, this is Ian"), ("Ian", "Sure"),
                         ("Interviewer", "I'll hand you over now.")]
    assert ("Guide", "Hey, Ian! I'm Hanne.") in lines and lines[-1] == ("Ian", "The director was effective.")
    # Times: the clock, and seconds since the visit began; within a part never backwards
    first_guide = next(x for x in items if x["kind"] == "line" and x["who"] == "guide")
    assert first_guide["clock"] == "21:25:45" and first_guide["secs"] == 26
    texts = [x["text"] for x in items]
    order = ["Intro ended: handed over to the guide", "Conversation with the guide", "Hey, Ian! I'm Hanne.",
             "Chapter asked for: “How places lock in memories” (opening expected in about 2 min)",
             "Chapter begun: “What the Heron Is Doing While It Waits” (a rehearsal: a finished chapter replayed)",
             "The guide stepped back (resting)", "The guide came back: they tapped the circle",
             "Checked on the chapter: Planning the chapter, opening in about 1 min",
             "The chapter's opening was ready to hear", "Started reading the chapter",
             "Read aloud: part 1, paragraphs 1 to 4 (4 cut off)",
             "Tapped to ask a question while the chapter was read (at 1:44 into the chapter)",
             "Question asked mid-chapter at part 1, paragraph 5", "Wait, I didn't ask about herons",
             "Went back to the chapter", "Question over (28 s): the guide went back to the chapter",
             "Read aloud: part 1, paragraph 4 (from sentence 3)",
             "Pressed End demo while paused (at 2:14 into the chapter)", "Debrief with the interviewer",
             "Chapter stopped before it was finished: Stopped when the demo ended.",
             "Welcome back, Ian. What stood out?", "Ended the debrief", "Debrief ended"]
    at = [texts.index(t) for t in order]
    assert at == sorted(at), [t for t, i in zip(order, at)]
    # App notes are events with their words as the detail, never a speaker
    note = next(x for x in items if x.get("detail", "").startswith("This session picks up"))
    assert note["kind"] == "event" and note["type"] == "note" and note["text"] == "App note to the voice"
    assert not any(x["kind"] == "line" and x.get("who") == "page" for x in items)


def test_no_earpiece_rows_and_no_session_config():
    items = _visit()["items"]
    blob = repr(items)
    assert "Ask about the park" not in blob and "Earpiece note" not in blob   # the look and its whisper
    assert "director" not in {x.get("type") for x in items}
    assert not any(k in blob for k in ("instructions_sha256", "live_g1", "startup", "names", "Hanne', 'Gwen"))


def test_a_conversation_with_no_listener_lines_and_a_reload():
    reload = [{"t": "2026-11-20T21:40:00", "who": "session", "names": ["Hanne"], "returning": False},
              {"t": "2026-11-20T21:40:01", "who": "session", "live_id": "live_g3"},
              {"t": "2026-11-20T21:40:02", "who": "page", "text": "[Picked up the chapter places-20261120 at s1-p03.]",
               "mode": "live"}]
    v = _visit(conversations=[("20261120-212539", GUIDE), ("20261120-214000", reload)], debriefs=[], events=[])
    parts = [x for x in v["items"] if x["kind"] == "part"]
    assert [p["text"] for p in parts] == ["Intro with the interviewer", "Conversation with the guide",
                                          "Conversation with the guide"]
    assert parts[-1]["id"] == "20261120-214000"
    tail = v["items"][v["items"].index(parts[-1]) + 1:]
    assert [x["detail"] for x in tail] == ["[Picked up the chapter places-20261120 at s1-p03.]"]
    only = visit.build(name=None, conversations=[("20261120-214000", reload)])
    assert [x["kind"] for x in only["items"]] == ["part", "event"]


def test_an_aside_whose_conversation_is_missing_brings_its_own_lines_and_the_default_speaker():
    v = visit.build(name=None, chapters=[{**CHAPTER, "reading": [], "status": {}, "timeline": []}])
    lines = [(x["speaker"], x["text"]) for x in v["items"] if x["kind"] == "line"]
    assert lines == [("Tester", "Wait, I didn't ask about herons")]
    assert v["items"][0]["text"] == "Question asked mid-chapter at part 1, paragraph 5"


def test_an_empty_visit():
    assert visit.build() == {"name": None, "started": None, "ended": None, "items": []}
    assert "Nothing recorded yet." in visit.text(visit.build(), "Pilot 1")


def test_plain_text():
    out = visit.text(_visit(), "Ian run 4")
    assert out.startswith("Transcript: Ian run 4, Fri 20 Nov 2026, 21:25:19 to 21:35:05\n")
    assert "\n== Intro with the interviewer (21:25:19)\n" in out
    assert "[21:25:22] Interviewer: Hi, is this Ian?" in out
    assert "[21:28:32]   · Chapter asked for: “How places lock in memories”" in out
    assert "App note to the voice: This session picks up from the person who welcomed them." in out
    assert "{" not in out.replace("{note}", "")


# --- the routes -----------------------------------------------------------------------------------


def test_the_whole_chapter_was_written_when_the_timeline_says_not_when_the_archive_rewrote_status():
    """A live chapter heard to the end is archived later (status.json `updated` moves to the debrief): the
    marker is the timeline's `ready` event, as in Ian's heron chapter (ready 00:23:17, archived 01:00:05)."""
    ready = {**CHAPTER, "status": {**CHAPTER["status"], "state": "ready", "updated": "2026-11-20T21:34:00"},
             "timeline": CHAPTER["timeline"] + [{"ts": "2026-11-20T21:31:10-05:00", "event": "ready"}]}
    want = visit._local("2026-11-20T21:31:10-05:00")
    items = _visit(chapters=[ready])["items"]
    done = next(x for x in items if x.get("text") == "The whole chapter was written")
    assert done["t"] == want.isoformat(timespec="seconds")
    debrief = next(i for i, x in enumerate(items) if x.get("text") == "Debrief with the interviewer")
    assert items.index(done) < debrief   # in the conversation, not the debrief
    # A run with no timeline `ready` (an older one) still falls back to status.json
    old = {**ready, "timeline": CHAPTER["timeline"]}
    done = next(x for x in _visit(chapters=[old])["items"] if x.get("text") == "The whole chapter was written")
    assert done["clock"] == "21:34:00"


def test_chapter_records_older_than_every_transcript_get_their_own_parts_by_day():
    """Ian's own folder: days of chapters and asides, few sessions files. Never folded into the first part with
    negative times."""
    earlier = {**CHAPTER, "run": "herons-20261114",
               "status": {"state": "ready", "started": "2026-11-14T00:11:46", "updated": "2026-11-14T01:00:05"},
               "timeline": [{"ts": "2026-11-14T00:23:17", "event": "ready"}],
               "asides": [{**CHAPTER["asides"][0], "started": "2026-11-15T10:33:02", "session": "gone"}],
               "reading": []}
    v = _visit(chapters=[earlier, CHAPTER])
    items = v["items"]
    assert all(x["secs"] >= 0 for x in items)
    parts = [(x["text"], x["date"]) for x in items if x["kind"] == "part"]
    assert parts[:3] == [(visit.EARLIER, "Sat 14 Nov"), (visit.EARLIER, "Sun 15 Nov"),
                         ("Intro with the interviewer", "Fri 20 Nov")]
    first_intro = next(i for i, x in enumerate(items) if x.get("text") == "Intro with the interviewer")
    before = [x["text"] for x in items[:first_intro] if x["kind"] != "part"]
    assert before[:2] == ["Chapter begun: “What the Heron Is Doing While It Waits” (a rehearsal: a finished chapter "
                          "replayed)", "The whole chapter was written"]
    assert "Wait, I didn't ask about herons" in before   # the aside with no conversation, its own lines
    out = visit.text(v, "You")
    assert out.startswith("Transcript: You, Sat 14 Nov 2026, 00:11:46 to Fri 20 Nov 2026, 21:35:05\n")
    assert f"\n== {visit.EARLIER} (Sun 15 Nov, 10:33:02)\n" in out
    # One visit on one day keeps its plain headers
    assert "\n== Intro with the interviewer (21:25:19)\n" in visit.text(_visit(), "Ian run 4")

def _seed(dcfg):
    lid = testers.create(dcfg, "Ian run 4", name="Ian")["lid"]
    d = testers.folder(lid)
    jsonl(d / "study" / "intro-20261120-212519.jsonl", INTRO)
    jsonl(d / "study" / "debrief-20261120-213257.jsonl", DEBRIEF)
    jsonl(d / "study" / "events.jsonl", EVENTS)
    jsonl(d / "sessions" / "20261120-212539.jsonl", GUIDE)
    jsonl(d / "sessions" / "log.jsonl", [{"stage": "voice", "provider": "openai", "cost_usd": 0.3}])
    ch = d / "chapters" / CHAPTER["run"]
    write(ch / "status.json", {**CHAPTER["status"], "title": CHAPTER["title"], "run": CHAPTER["run"], "mode": "fast"})
    write(ch / "fast.json", {"mode": "fast", "voice": "live", "rehearsal": {"source_run": "x"}})
    jsonl(ch / "fast_timeline.jsonl", CHAPTER["timeline"])
    jsonl(ch / "12_asks" / "asides.jsonl", CHAPTER["asides"])
    jsonl(ch / "live" / "reading.jsonl", CHAPTER["reading"])
    return lid


def test_the_routes_read_the_testers_folder(dcfg, tmp_path, no_admin_env):
    lid = _seed(dcfg)
    c = client(dcfg, tmp_path)
    page = c.get(f"/admin/transcript?lid={lid}")
    assert page.status_code == 200 and "/api/admin/transcript/" in page.text
    got = c.get(f"/api/admin/transcript/{lid}").json()
    assert got["label"] == "Ian run 4" and got["name"] == "Ian"
    assert got["items"] == _visit()["items"]
    r = c.get(f"/api/admin/transcript/{lid}/text")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert r.headers["content-disposition"] == f'attachment; filename="{lid}-transcript.txt"'
    assert r.text.startswith("Transcript: Ian run 4 (Ian), Fri 20 Nov 2026")
    assert c.get("/api/admin/transcript/t-nobody").status_code == 404
    assert c.get("/api/admin/transcript/t-nobody/text").status_code == 404
    assert "/admin/transcript?lid=" in c.get("/admin").text


def test_the_routes_are_admin_only(dcfg, tmp_path, no_admin_env, monkeypatch):
    lid = _seed(dcfg)
    token = testers.record(lid)["token"]
    paths = ("/admin/transcript", f"/admin/transcript?lid={lid}", f"/api/admin/transcript/{lid}",
             f"/api/admin/transcript/{lid}/text", "/api/admin/transcript/own")
    out = client(dcfg, tmp_path, host="203.0.113.5")
    out.cookies.set("wl_tester", token)   # a tester's own cookie is not admin
    for p in paths:
        assert out.get(p).status_code == 403, p
    proxied = client(dcfg, tmp_path)
    for p in paths:
        assert proxied.get(p, headers={"X-Forwarded-For": "198.51.100.7"}).status_code == 403, p
    monkeypatch.setenv("ADMIN_USER", "ian")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3cret")
    for p in paths:
        r = out.get(p)
        assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic"), p
    ok = {"Authorization": "Basic " + base64.b64encode(b"ian:s3cret").decode()}
    assert out.get(f"/api/admin/transcript/{lid}", headers=ok).json()["name"] == "Ian"


def test_your_own_with_demo_off(cfg, tmp_path, no_admin_env):
    from stages import common
    ocfg = demo_cfg(cfg, enabled=False)
    jsonl(common.listener_dir(ocfg) / "sessions" / "20261120-212539.jsonl", GUIDE)
    got = client(ocfg, tmp_path).get("/api/admin/transcript/own").json()
    assert got["label"] == "Your own" and got["name"] == "You"
    assert ("You", "I was in Crandon Park today.") in [(x["speaker"], x["text"]) for x in got["items"]
                                                         if x["kind"] == "line"]


# --- the page's audio route records (page/speaker.js; Ian's Android phone, 2026-11-21) ------------------------

PIXEL_UA = ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 "
            "Mobile Safari/537.36")
AUDIO = {"t": "2026-11-20T21:25:40", "event": "audio_route", "data": {
    "reason": "speakerphone", "result": "switched", "ua": PIXEL_UA,
    "ua_hints": {"model": "Pixel 8", "platform": "Android", "android": "16.0.0", "chrome": "142.0.7444.52"},
    "inputs": ["Speakerphone", "Headset earpiece"], "outputs": ["Speakerphone"], "track_label": "Speakerphone",
    "settings": {"deviceId": "abc", "echoCancellation": True, "noiseSuppression": True, "autoGainControl": True,
                 "sampleRate": 48000}, "ec_modes": [True, False], "live_mic_tracks": 1}}


def test_an_audio_route_record_is_one_short_grey_line():
    assert visit.audio_words(AUDIO) == ("Audio (Speakerphone retry: switched): Pixel 8, Android 16, Chrome 142; "
                                        "inputs Speakerphone, Headset earpiece; outputs Speakerphone; mic Speakerphone")
    mic = {"t": AUDIO["t"], "event": "audio_route", "data": {
        "reason": "mic", "ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
                              "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1",
        "inputs": ["iPhone Microphone"], "track_label": "iPhone Microphone",
        "settings": {"echoCancellation": False}}}
    assert visit.audio_words(mic) == ("Audio (mic opened): iPhone, iOS 18.1, Safari 18.1; inputs iPhone Microphone; "
                                      "mic iPhone Microphone; echo cancellation off")
    assert visit.audio_words({"event": "audio_route", "data": {}}) == "Audio (?): unknown device"
    v = _visit(events=EVENTS + [AUDIO])
    got = [x for x in v["items"] if x.get("type") == "audio"]
    assert len(got) == 1 and got[0]["kind"] == "event" and got[0]["clock"] == "21:25:40"
    assert got[0]["text"].startswith("Audio (Speakerphone retry: switched): Pixel 8")
    # in the conversation's part (it began at 21:25:39), and in the download as an event line
    parts = [x for x in v["items"] if x["kind"] == "part"]
    i = v["items"].index(got[0])
    assert [p for p in parts if v["items"].index(p) < i][-1]["text"] == "Conversation with the guide"
    assert "[21:25:40]   · Audio (Speakerphone retry: switched): Pixel 8, Android 16" in visit.text(v)
    # nothing else changed
    assert [x["text"] for x in v["items"] if x.get("type") != "audio"] == [x["text"] for x in _visit()["items"]]


def test_android_is_shown_without_its_trailing_zero_parts():
    """Chrome's client hints give Android's version as "16.0.0" (page/speaker.js posts it as given): the line says
    Android 16, as Chrome is cut to 142; a real minor version is kept."""
    hints = lambda v: {"ua_hints": {"model": "Pixel 8", "platform": "Android", "android": v,  # noqa: E731
                                    "chrome": "142.0.7444.52"}}
    assert visit._device(hints("16.0.0")) == "Pixel 8, Android 16, Chrome 142"
    assert visit._device(hints("16")) == "Pixel 8, Android 16, Chrome 142"
    assert visit._device(hints("13.1.0")) == "Pixel 8, Android 13.1, Chrome 142"
    assert visit._device(hints("10.0")) == "Pixel 8, Android 10, Chrome 142"
    assert visit._device({"ua": PIXEL_UA.replace("Android 10;", "Android 14.0.0;")}).startswith("Android 14, Chrome 142")


def test_the_taps_mic_records_open_the_intro_rather_than_a_part_of_their_own():
    """The gate opens the mic in the tap, then posts consent and exchanges the offer; the intro's transcript writes
    its first row only after that, so the mic and Speakerphone records come a second or more before it. They open
    the intro (whose part now starts at the tap), never "Chapter records with no conversation recorded"."""
    mic = {"t": "2026-11-20T21:25:17", "event": "audio_route", "data": {**AUDIO["data"], "reason": "mic", "result": None,
                                                                       "track_label": "Headset earpiece"}}
    sp = {**AUDIO, "t": "2026-11-20T21:25:18"}
    v = _visit(events=EVENTS + [mic, sp])
    parts = [x for x in v["items"] if x["kind"] == "part"]
    assert [x["text"] for x in parts] == ["Intro with the interviewer", "Conversation with the guide",
                                         "Debrief with the interviewer"]
    assert visit.EARLIER not in visit.text(v)
    assert parts[0]["clock"] == "21:25:17" and v["started"] == "2026-11-20T21:25:17"
    got = v["items"][1:3]
    assert [x["text"][:22] for x in got] == ["Audio (mic opened): Pi", "Audio (Speakerphone re"]
    assert [x["secs"] for x in got] == [0, 1] and all(x["secs"] >= 0 for x in v["items"])
    assert v["items"][3].get("type") != "audio"   # then the intro's own rows
    assert "== Intro with the interviewer (21:25:17)\n[21:25:17]   · Audio (mic opened): Pixel 8, Android 16" \
        in visit.text(v)
    # a later part keeps its own start: End demo, pressed just before the debrief, stays in the conversation
    dmic = {**mic, "t": "2026-11-20T21:32:56"}
    v = _visit(events=EVENTS + [dmic])
    assert [x["clock"] for x in v["items"] if x["kind"] == "part"][-1] == "21:32:57"
    i = next(i for i, x in enumerate(v["items"]) if x.get("type") == "press" and "End demo" in x["text"])
    assert [x["text"] for x in v["items"][:i] if x["kind"] == "part"][-1] == "Conversation with the guide"
    # one long before any part (a page left open): still a part of its own
    old = {**mic, "t": "2026-11-20T21:20:00"}
    assert [x["text"] for x in _visit(events=[old])["items"] if x["kind"] == "part"][0] == visit.EARLIER
    # a device change just before a part is not the tap's: it stays where it was
    dev = {**mic, "t": "2026-11-20T21:25:18", "data": {**mic["data"], "reason": "devicechange"}}
    assert [x["text"] for x in _visit(events=[dev])["items"] if x["kind"] == "part"][0] == visit.EARLIER


def test_a_headset_move_reads_as_one_line():
    rec = {**AUDIO, "data": {**AUDIO["data"], "reason": "headset", "result": "moved",
                             "inputs": ["Speakerphone", "Headset earpiece", "Bluetooth headset"],
                             "track_label": "Bluetooth headset"}}
    assert visit.audio_words(rec).startswith("Audio (Headset connected: moved): Pixel 8, Android 16, Chrome 142; "
                                             "inputs Speakerphone, Headset earpiece, Bluetooth headset")


def test_the_audio_route_is_stored_by_the_event_route_and_shown_in_the_transcript(dcfg, tmp_path, no_admin_env):
    from demo import debrief
    lid = _seed(dcfg)
    token = testers.record(lid)["token"]
    c = client(dcfg, tmp_path)
    c.cookies.set("wl_tester", token)
    junk = {**AUDIO["data"], "inputs": ["x" * 200] * 15, "secret": "never kept", "ua_hints": {**AUDIO["data"]["ua_hints"], "evil": 1}}
    r = c.post("/api/demo/event", json={"event": "audio_route", "data": junk})
    assert r.status_code == 200 and r.json() == {"ok": True, "saved": True}
    assert c.post("/api/demo/event", json={"event": "audio_route", "data": {"ua": "x" * 9000}}).status_code == 413
    d = testers.folder(lid)
    rec = debrief.audio_routes(d)[-1]
    assert set(rec) == {"t", "event", "data"} and "secret" not in rec["data"] and "evil" not in rec["data"]["ua_hints"]
    assert len(rec["data"]["inputs"]) == 12 and all(len(x) == 60 for x in rec["data"]["inputs"])
    assert rec["data"]["settings"]["echoCancellation"] is True and rec["data"]["ec_modes"] == [True, False]
    # presses are unchanged: the audio record is no press, and the debrief's account doesn't see it
    assert [e["event"] for e in debrief.events(d)] == ["ask", "end_demo"]
    got = c.get(f"/api/admin/transcript/{lid}").json()
    assert [x["text"][:40] for x in got["items"] if x.get("type") == "audio"] == ["Audio (Speakerphone retry: switched): Pi"]
    assert c.get("/api/admin/testers").status_code == 200   # the testers' rows still build
