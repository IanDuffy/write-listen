"""Listener Signals, package 6: the earpiece's note (voice/gpt_live/director.py behind live.director.signals; Ian's
build plan, 2026-11-21: "a flat 'yeah, it was good' reads the same as a warm one"). Off, the look and its row are as
they were and nothing waits. On, the look carries the listener's latest turn's note (signals.record.note, from the
conversation's signals file, which signals/live.py writes as the line route does) in a <listener_signals> block, and
the director row records it as "heard"; a turn's record that lands after the look is waited for (outside the
conversation lock, which the line post needs), and one from an earlier turn gives no note. Also the persona mirror,
the earpiece history and the prompts. Offline: llm's client is a fake that answers with the JSON a test gives it."""

import asyncio
import json
import shutil
import threading
import time

import pytest
from fastapi.testclient import TestClient

import server
from fakes import FakeAnthropic, Responder
from signals import live as sig_live
from signals import store
from stages import common, guide, llm
from voice.gpt_live import director as D
from voice.gpt_live import earpiece_history as EH
from voice.gpt_live import persona as P
from voice.gpt_live import session as S

LOOK = {"read": "Dinner talk.", "plan": [{"item": "Meet them a little", "state": "open"}], "next": None,
        "changed": False, "kind": None, "about": None, "note": None}


def answering(monkeypatch, *answers, delay=0.0):
    """llm's client answers each director call with the next of `answers`; returns the calls it was given."""
    calls, it = [], iter(answers)

    def respond(kwargs):
        calls.append(kwargs)
        if delay:
            time.sleep(delay)
        return Responder.msg(kwargs, json.dumps(next(it)))
    monkeypatch.setattr(llm, "_client", FakeAnthropic(respond))
    return calls


@pytest.fixture
def conv(cfg, tmp_path, monkeypatch):
    """A live conversation (a real guide.Session) with the earpiece on; signals as the test sets them."""
    cfg["listener_dir"] = str(tmp_path / "listener")
    cfg["live"]["provider"] = "gpt_live"
    cfg["live"]["director"] = {**cfg["live"]["director"], "enabled": True}
    monkeypatch.setattr(llm, "_client", FakeAnthropic(Responder()))
    s = guide.Session(cfg, start=lambda *a, **k: None, status=lambda: {}, runs_dir=tmp_path / "runs")
    s.record("session", live_id="live_1")
    s.record("guide", text="Hi! How was your day?", mode="live")
    return s


def listener_of(s):
    return s.transcript.parent.parent


def measured(cfg, s, turn, text, gap_ms=600, dur_ms=4000):
    """The page's record of one listener turn, written as the line route writes it."""
    sig = {"turn": turn, "gap_ms": gap_ms, "dur_ms": dur_ms, "speak_ms": dur_ms, "pauses": 0, "longest_pause_ms": 0,
           "rms_db": -30.0, "start_s": 10.0 + 10 * turn, "end_s": 10.0 + 10 * turn + dur_ms / 1000, "ago_ms": 900}
    return sig_live.record_line(listener_of(s), s.id, part="guide", live_id="live_1", text=text, sig=sig, cfg=cfg)


def fuller_turns(cfg, s):
    """Three fuller turns: the norm. The fourth, short and slow to come, is the one the earpiece hears about."""
    for i, text in enumerate(["I made a salmon dinner for my wife and me tonight.",
                              "We cooked it on a cedar plank out on the porch grill.",
                              "My father taught me that one when I was a little kid."]):
        measured(cfg, s, i, text)
    return measured(cfg, s, 3, "Yeah, fine.", gap_ms=3200, dur_ms=900)


def directed(s, cfg, lock=None, **req):
    return D.ask(cfg, s, {"id": 1, "trigger": "turn_end", "speaking": "none", "page": {"seconds": 40}, **req},
                 lock=lock)


def rows(s, who="director"):
    return [r for r in (json.loads(x) for x in s.transcript.read_text().splitlines()) if r["who"] == who]


def block(note):
    return f"</transcript>\n\n<listener_signals>\n{note}\n</listener_signals>\n\nUpdate your plan"


# --- off: as it was --------------------------------------------------------------------------------------------

def test_off_by_default_with_its_wait():
    from stages.common import load_config
    assert D.settings({"live": {}})["signals"] is False and D.settings({"live": {}})["signals_wait_ms"] == 400
    DC = load_config()["live"]["director"]
    assert DC["signals"] is False and DC["signals_wait_ms"] == 400


def test_off_the_look_and_its_row_are_as_they_were_and_nothing_waits(conv, cfg, monkeypatch):
    fuller_turns(cfg, conv)
    cfg["live"]["director"]["signals_wait_ms"] = 3000          # would show as a 3 s wait if it read the file
    calls = answering(monkeypatch, LOOK)
    t = time.monotonic()
    out = directed(conv, cfg, lock=threading.Lock(), sig_turn=9)
    assert time.monotonic() - t < 2.0 and "error" not in out and "heard" not in out
    user = calls[0]["messages"][0]["content"]
    assert "listener_signals" not in user and "</transcript>\n\nUpdate your plan" in user
    (row,) = rows(conv)
    assert "heard" not in row and set(row) == {"t", "who", "id", "trigger", "speaking", *D.FIELDS, "ms", "cost_usd"}


def test_an_empty_signals_line_renders_the_look_byte_for_byte_as_before(tmp_path, monkeypatch):
    """The {{signals}} line rendered empty adds nothing: the moment prompt without that line gives the same look."""
    lines = [{"secs": 5, "who": "guide", "text": "Ian. Hi!"},
             {"secs": 30, "who": "listener", "text": "I saw a peregrine on a lamp post today"}]
    kw = dict(lines=lines, trigger="turn_end", speaking="listener", last_who=None, silence_secs=0.4, seconds=66,
              explained=True, started=False, profile="Name: Ian.", plan=None, last_read="")
    now = D.look(D.settings({"live": {}}), **kw)
    assert now == D.look(D.settings({"live": {}}), signals="", **kw)
    old = tmp_path / "prompts"
    shutil.copytree(common.PROMPTS_DIR, old)
    moment = old / "live_director_moment.md"
    text = moment.read_text()
    assert text.count("</transcript>\n{{signals}}\nUpdate your plan") == 1
    moment.write_text(text.replace("</transcript>\n{{signals}}\n", "</transcript>\n\n"))
    monkeypatch.setattr(common, "PROMPTS_DIR", old)
    assert D.look(D.settings({"live": {}}), **kw) == now


# --- on: the note in the look and the row --------------------------------------------------------------------

def test_on_the_look_carries_the_turns_note_and_the_row_records_it_as_heard(conv, cfg, monkeypatch):
    cfg["live"]["director"]["signals"] = True
    rec = fuller_turns(cfg, conv)
    note = rec["note"]
    assert "2 words (norm" in note and "before answering" in note        # the record passed its thresholds
    calls = answering(monkeypatch, {**LOOK, "changed": True, "about": "interest",
                                    "note": "Ask what made the cedar plank his father's way."})
    out = directed(conv, cfg, lock=threading.Lock(), sig_turn=3)
    assert block(note) in calls[0]["messages"][0]["content"]
    assert "heard" not in out and out["note"] == "Ask what made the cedar plank his father's way."
    (row,) = rows(conv)
    assert row["heard"] == note and row["note"] == out["note"] and "text" not in row
    # nothing that reads the conversation reads it
    assert note not in guide.earlier_text(conv.transcript) and note not in json.dumps(D.lines_from_transcript(conv.transcript))


def test_without_a_turn_number_it_reads_the_newest_record(conv, cfg, monkeypatch):
    cfg["live"]["director"]["signals"] = True
    rec = fuller_turns(cfg, conv)
    calls = answering(monkeypatch, LOOK)
    directed(conv, cfg)
    assert block(rec["note"]) in calls[0]["messages"][0]["content"] and rows(conv)[0]["heard"] == rec["note"]


def test_a_record_that_lands_after_the_look_is_waited_for(conv, cfg, monkeypatch):
    cfg["live"]["director"]["signals"] = True
    cfg["live"]["director"]["signals_wait_ms"] = 3000
    measured(cfg, conv, 0, "I made a salmon dinner for my wife and me tonight.")
    landed = {}

    def post():
        time.sleep(0.1)
        landed["rec"] = measured(cfg, conv, 1, "Yeah, it was good.")
    calls = answering(monkeypatch, LOOK)
    th = threading.Thread(target=post)
    t = time.monotonic()
    th.start()
    directed(conv, cfg, sig_turn=1)
    took = time.monotonic() - t
    th.join()
    assert 0.1 <= took < 2.5
    assert landed["rec"]["note"] in calls[0]["messages"][0]["content"] and rows(conv)[0]["heard"] == landed["rec"]["note"]


def test_the_conversation_lock_is_free_while_it_waits(conv, cfg, monkeypatch):
    """The line post that carries the turn's measurements takes the conversation lock: held through the wait, the
    record could never land in time. And the transcript is read after the wait, so the look has that line too."""
    cfg["live"]["director"]["signals"] = True
    cfg["live"]["director"]["signals_wait_ms"] = 3000
    lock, got = threading.Lock(), {}

    def line_post():
        time.sleep(0.1)
        got["lock"] = lock.acquire(timeout=1.0)
        if got["lock"]:
            try:
                conv.record("listener", text="Yeah, it was good.", mode="live")
                got["rec"] = measured(cfg, conv, 0, "Yeah, it was good.")
            finally:
                lock.release()
    calls = answering(monkeypatch, LOOK)
    th = threading.Thread(target=line_post)
    th.start()
    directed(conv, cfg, lock=lock, sig_turn=0)
    th.join()
    assert got["lock"] is True
    user = calls[0]["messages"][0]["content"]
    assert got["rec"]["note"] in user and "listener: Yeah, it was good." in user


def test_a_record_from_an_earlier_turn_gives_no_note(conv, cfg, monkeypatch):
    cfg["live"]["director"]["signals"] = True
    cfg["live"]["director"]["signals_wait_ms"] = 150
    measured(cfg, conv, 0, "I made a salmon dinner for my wife and me tonight.")
    calls = answering(monkeypatch, LOOK)
    directed(conv, cfg, sig_turn=1)
    assert "listener_signals" not in calls[0]["messages"][0]["content"]
    (row,) = rows(conv)
    assert "heard" in row and row["heard"] is None


def test_no_record_gives_no_note(conv, cfg, monkeypatch):
    cfg["live"]["director"]["signals"] = True
    cfg["live"]["director"]["signals_wait_ms"] = 100
    calls = answering(monkeypatch, LOOK, LOOK)
    directed(conv, cfg)
    directed(conv, cfg, sig_turn=0)
    assert all("listener_signals" not in c["messages"][0]["content"] for c in calls)
    assert [r["heard"] for r in rows(conv)] == [None, None]
    assert not store.folder(listener_of(conv)).exists()          # a look never writes the signals folder


def test_a_slow_look_still_records_what_it_heard(conv, cfg, monkeypatch):
    cfg["live"]["director"]["signals"] = True
    cfg["live"]["director"]["timeout_ms"] = 200
    rec = fuller_turns(cfg, conv)
    answering(monkeypatch, LOOK, delay=1.0)
    out = directed(conv, cfg, sig_turn=3)
    assert out["error"] == "timeout" and "heard" not in out
    (row,) = rows(conv)
    assert row["error"] == "timeout" and row["heard"] == rec["note"]


def test_the_folder_is_the_transcripts_not_the_routes_listener(conv, cfg, monkeypatch, tmp_path):
    """The route's cfg is global (a tester's folder comes from the request, and the look's thread doesn't inherit
    it): the signals are read beside the conversation's own transcript."""
    cfg["live"]["director"]["signals"] = True
    rec = fuller_turns(cfg, conv)
    cfg["listener_dir"] = str(tmp_path / "someone-else")
    calls = answering(monkeypatch, LOOK)
    directed(conv, cfg, sig_turn=3)
    assert rec["note"] in calls[0]["messages"][0]["content"]


def test_through_the_route(cfg, tmp_path, monkeypatch):
    """POST /api/live/direct with the flag on: the note of the conversation's newest measured turn."""
    cfg["listener_dir"] = str(tmp_path / "listener")
    cfg["live"]["provider"] = "gpt_live"
    cfg["live"]["director"] = {**cfg["live"]["director"], "enabled": True, "signals": True}
    monkeypatch.setattr(llm, "_client", FakeAnthropic(Responder()))
    monkeypatch.setattr(server.memory, "after_session", lambda *a, **k: False)
    monkeypatch.setattr(S, "create_webrtc", lambda cfg, session, sdp, client=None: {"id": "live_1", "sdp": "ANSWER"})
    (tmp_path / "runs").mkdir()
    c = TestClient(server.create_app(server.Pipeline(runs_dir=tmp_path / "runs"), cfg, remember_on_start=False))
    sid = c.post("/api/live/start", json={"sdp": "OFFER"}).json()["session"]
    c.post("/api/live/line", json={"session": sid, "who": "guide", "text": "How was your day?"})
    sig = {"turn": 0, "gap_ms": 700, "dur_ms": 1500, "speak_ms": 1500, "ago_ms": 900}
    rec = sig_live.record_line(guide.listener_dir(cfg), sid, part="guide", live_id="live_1", text="Long. [sigh]",
                               sig=sig, cfg=cfg)
    assert "[sigh]" in rec["note"]
    calls = answering(monkeypatch, LOOK)
    out = c.post("/api/live/direct", json={"session": sid, "id": 2, "sig_turn": 0}).json()
    assert "heard" not in out and block(rec["note"]) in calls[0]["messages"][0]["content"]
    d = [json.loads(x) for x in (guide.listener_dir(cfg) / "sessions" / f"{sid}.jsonl").read_text().splitlines()]
    assert [r["heard"] for r in d if r["who"] == "director"] == [rec["note"]]


# --- the persona mirror, the earpiece history, the prompts --------------------------------------------------------

class EarSim:
    """Just what the persona's Earpiece reads of a Sim (as tests/live/test_sim.py's)."""

    def __init__(self, timeline=(), t=0.0):
        self.timeline, self.turns, self.calls, self.t = list(timeline), [], [], t
        self.speech, self.speaking, self.audio_last, self.notes = b"", False, 0.0, []
        self.closed = type("Closed", (), {"is_set": lambda self: False})()

    def now(self):
        return self.t

    async def note(self, kind, text):
        self.notes.append((kind, text))


def test_the_persona_mirror_still_builds_its_looks(cfg):
    """persona.Earpiece calls look() without signals and copies FIELDS into its records: neither changes."""
    assert "heard" not in D.FIELDS
    cfg["live"]["director"] = {**cfg["live"]["director"], "enabled": True, "signals": True}
    users = []

    def fn(user):
        users.append(user)
        return dict(LOOK)
    sim = EarSim([{"t": 0.0, "who": "guide", "text": "Hi there!"},
                  {"t": 2.0, "end": 3.0, "who": "listener", "text": "Hey, long day at the shop."}], t=5.0)
    e = P.Earpiece(cfg, sim, profile="(new)", notes=S.notes(), decide_fn=fn)

    async def go():
        e.ask("turn_end", 5.0)
        await e.inflight

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(1) as pool:      # its own loop, whatever a browser test left in the main thread
        pool.submit(asyncio.run, go()).result()
    assert len(users) == 1 and "listener: Hey, long day at the shop." in users[0]
    assert "listener_signals" not in users[0] and e.looks and "heard" not in e.looks[0]


def test_the_earpiece_history_carries_what_each_look_heard():
    rows_ = [{"t": "2026-11-21T10:00:00", "who": "session", "names": []},
             {"t": "2026-11-21T10:00:05", "who": "listener", "text": "Yeah, fine."},
             {"t": "2026-11-21T10:00:07", "who": "director", "id": 1, "trigger": "turn_end", "speaking": "none",
              "heard": "Their last turn: 2 words (norm 11).", "read": "r", "plan": [], "changed": False,
              "note": None, "ms": 1800, "cost_usd": 0.003},
             {"t": "2026-11-21T10:00:30", "who": "director", "id": 2, "trigger": "cadence", "speaking": "none",
              "heard": None, "read": "r", "plan": [], "changed": False, "note": None, "ms": 1500, "cost_usd": 0.003},
             {"t": "2026-11-21T10:01:00", "who": "director", "id": 3, "trigger": "cadence", "speaking": "none",
              "read": "r", "plan": [], "changed": False, "note": None, "ms": 1500, "cost_usd": 0.003}]
    got = EH.timeline_of(rows_, "t-ian", "20261121-100000")
    assert [x["heard"] for x in got["items"] if x["kind"] == "look"] == ["Their last turn: 2 words (norm 11).", None, None]
    assert got["director"]["notes"] == 0                   # "heard" is never taken for a whisper


def test_the_prompts_explain_the_measurements_and_keep_the_look_as_it_was():
    from stages.common import ROOT
    head, body = (ROOT / "prompts" / "live_director.md").read_text().split("\n---\n", 1)
    assert "2026-11-21" in head and "live.director.signals" in head
    flat = " ".join(body.split())
    for rule in ("<listener_signals>", "These are measurements, not feelings",
                 "Weigh them with what they said, never instead of it", "A short, flat or slow answer after fuller ones",
                 "Never have the guide name a feeling or mention the measurements",
                 "the tags in them are still the transcript's, not their meaning",
                 'Sound tags ("[inhale]") and fragments are the transcript\'s, not their meaning'):
        assert rule in flat, rule
    mhead, mbody = (ROOT / "prompts" / "live_director_moment.md").read_text().split("\n---\n", 1)
    assert "{{signals}}" in mhead and "</transcript>\n{{signals}}\nUpdate your plan" in mbody
