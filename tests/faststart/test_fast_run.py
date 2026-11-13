"""The fast run's driver (faststart/run.py), end to end and offline: every area's fake replies
chained into one model, a fake voice with real audio and timings, real ffmpeg, real threads, and
a simulated listener. Nothing here may reach a live API (the `offline` fixture), and no worker
thread may outlive its test (`no_leaks`): once a test's fake client is gone, a stray thread
would make real, paid calls."""

import json
import math
import random
import re
import shutil
import threading
import time
from types import SimpleNamespace as NS

import pytest

import fast_outline_fakes as A
import fast_research_fakes as B
import fast_sections_fakes as C
from fakes import all_text
from fast_run_fakes import (Balance, Patient, fast_clock, fast_threads, happened, install, no_leaks,  # noqa: F401
                            offline, settings, slow_section_3, wait_for, world)

import cli
from faststart import base, checks, e2e, narrate, notes, research, sections, status
from faststart import run as fast_run
from faststart import timings as T
from faststart.e2e import ScriptedListener
from faststart.player import SimPlayer
from faststart.scheduler import MEDIANS
from stages import brief, common, new, tts
from stages.common import MARKER, Run, StageError, entry_numbers, split_script_segments

REMARKS = [{"kind": "landed", "text": "The barge going down into the water, I could see that."},
           {"kind": "unclear", "text": "I lost track of where the ash came from."},
           {"kind": "connection", "text": "My grandfather was a mason in Naples."}]


def events(r, name=None, **match):
    return [e for e in base.read_timeline(r) if (name is None or e["event"] == name)
            and all(e.get(k) == v for k, v in match.items())]


def final_status(r) -> dict:
    return json.loads(r.path("status").read_text(encoding="utf-8"))


def locked(r) -> list[tuple[int, str]]:
    n = len(json.loads(base.path(r, "outline_json").read_text(encoding="utf-8"))["sections"])
    return [(k, sections.locked_text(r, k)) for k in range(1, n + 1)]


def earlier_sent(kwargs) -> dict[int, tuple[str, str]]:
    """The earlier sections a draft or lock request carries: {section: (state, full text)}."""
    out = {}
    for block in kwargs["messages"][-1]["content"]:
        if m := re.match(r'<section n="(\d+)" state="(locked|draft)">\n(.*)\n</section>$', block["text"], re.S):
            out[int(m.group(1))] = (m.group(2), m.group(3))
    return out


def versions(r, k: int) -> set[str]:
    return {p.read_text(encoding="utf-8").strip() for p in base.path(r, "drafts").glob(f"seg{k:02d}.d*.md")}


def assert_check_2(w) -> None:
    """Acceptance check 2 at the run level: every draft request holds the full text of every
    earlier section, the locked text for a locked one and a draft of it otherwise."""
    total = len(locked(w.r))
    for n in range(2, total + 1):
        calls = w.chain.of("draft", n)
        assert calls, f"section {n} was never drafted"
        for kw in calls:
            sent = earlier_sent(kw)
            assert sorted(sent) == list(range(1, n)), (n, sorted(sent))
            for k, (state, text) in sent.items():
                if state == "locked":
                    assert text == sections.locked_text(w.r, k), (n, k)
                else:
                    assert text in versions(w.r, k), (n, k)
                assert len(text.split()) > 30  # the whole section, not a summary of it


# --- the whole run ---------------------------------------------------------------------------

def test_a_fast_run_of_four_sections_end_to_end(tmp_path, cfg, monkeypatch):
    w = world(tmp_path, cfg, monkeypatch)
    r = w.r
    seen, real = [], new.write_status
    monkeypatch.setattr(new, "write_status", lambda r, cfg, state, **f: seen.append(real(r, cfg, state, **f)) or seen[-1])

    out = fast_run.run(r, w.cfg, player=SimPlayer())

    assert out["run"] == r.name and out["sections"] == 4 and out["episode"] and out["solo"] == []
    assert out["cost_usd"] == round(r.spent(), 2) > 0

    # every file of the layout in faststart/base.py
    for rel in ["fast.json", "01_scout.md", "03_angle.md", "04_research/L00.md", "04_research/L01.md", "04_dossier.md",
                "05_outline.json", "05_outline.md", "05_brief.md", "ledger.json", "06_segments/seg01.md",
                "06_segments/seg04.md", "06_segments/drafts/seg02.d1.md", "fast_timeline.jsonl", "fast/state.json",
                "fast/locks/seg03.json", "fast/archive.json", "10_audio/sec01/p00.mp3", "10_audio/sec04/section.mp3",
                "11_episode/sections/seg02.json", "09_script_final.md", "10_audio/chapter.mp3",
                "11_episode/timing.json", "11_episode/episode.json", "decisions.md", "status.json"]:
        assert (r.root / rel).exists(), rel
    assert [n for n, _ in split_script_segments(r.read("script_final"))] == [1, 2, 3, 4]
    assert len(brief.parse_segments(r.read("brief"))) == 4  # ask-by-voice and the episode page read it as a brief

    # acceptance check 8, on the chapter as locked
    assert checks.chapter_problems(locked(r), r.read("dossier")) == []

    # one paid call per step, in the scheduler's order
    whats = w.chain.whats()
    assert whats[:3] == [("scout", None), ("outline", None), ("outline_edit", None)]
    assert sorted(k for k in whats if k[0] == "row") == [("row", f"L0{i}") for i in range(1, 5)]
    assert [k for k in whats if k[0] in ("opening", "opening_edit", "draft", "lock")] == [
        ("opening", 1), ("opening_edit", 1), ("draft", 2), ("draft", 3), ("lock", 2), ("draft", 4), ("lock", 3),
        ("lock", 4)]

    # acceptance check 2: here the order is fixed, so the exact versions are known
    assert_check_2(w)
    assert {n: {k: s for k, (s, _) in earlier_sent(w.chain.of("draft", n)[0]).items()} for n in (2, 3, 4)} == {
        2: {1: "locked"}, 3: {1: "locked", 2: "draft"}, 4: {1: "locked", 2: "locked", 3: "draft"}}
    # the final lock reads the whole chapter; a paired lock has the next draft beside it
    assert sorted(earlier_sent(w.chain.of("lock", 4)[0])) == [1, 2, 3]
    assert '<draft n="3">' in all_text(w.chain.of("lock", 2)[0]) and '<draft n="4">' not in all_text(w.chain.of("lock", 2)[0])

    # the timeline: every event a plain run has, and nothing narrated before it was locked
    names = {e["event"] for e in events(r)}
    assert names >= {T.SEED_SET, T.SCOUT_DONE, T.OUTLINE_DONE, T.OPENING_START, T.OPENING_DONE, T.RESEARCH_START,
                     T.RESEARCH_DONE, T.DRAFT_START, T.DRAFT_DONE, T.LOCK_START, T.LOCK_DONE, T.NARRATE_START,
                     T.PIECE_READY, T.NARRATE_DONE, T.FIRST_AUDIO, T.PLAYBACK_START, T.SECTION_NEEDED,
                     T.ARCHIVE_START, T.ARCHIVE_DONE, T.PLAYBACK_DONE, T.READY}
    order = [(e["event"], e.get("section")) for e in events(r)]
    for n in (2, 3, 4):
        assert order.index((T.LOCK_DONE, n)) < order.index((T.NARRATE_START, n))
        assert order.index((T.NARRATE_DONE, n - 1)) < order.index((T.NARRATE_START, n))
    assert order.index((T.OPENING_DONE, 1)) < order.index((T.NARRATE_START, 1))
    assert [e["mode"] for e in events(r, T.LOCK_DONE)] == ["paired", "paired", "final"]
    assert len(events(r, T.FIRST_AUDIO)) == 1 and len(events(r, T.ARCHIVE_DONE)) == 1
    assert [e["section"] for e in events(r, T.SECTION_NEEDED)] == [1, 2, 3, 4]  # the rest of the listening, played out
    pieces = sum(len(narrate.pieces(r, n)) for n in range(1, 5))
    assert len(events(r, T.PIECE_READY)) == pieces == len(w.voice.calls)

    # status.json over the run: the overnight shape the page and server know, plus the fast fields
    assert all(s["mode"] == "fast" and s["pid"] for s in seen)
    assert [s["state"] for s in seen][-1] == "ready" and {s["state"] for s in seen[:-1]} == {"making"}
    labels = [s["label"] for s in seen if s["label"]]
    assert labels[0] == "Looking into it" and "Planning the chapter" in labels and "Writing the opening" in labels
    assert "Finishing" in labels and any(re.fullmatch(r"(Researching|Writing|Editing|Recording) part \d of 4", x) for x in labels)
    assert seen[0]["sections"] == [] and seen[0]["first_audio"] is False and seen[0]["minutes_left"] is None
    states = [[x["state"] for x in s["sections"]] for s in seen if s["sections"]]
    assert states[0] == ["being_written"] * 4 and all(len(x) == 4 for x in states)
    assert any(x[0] in ("ready", "playing") and x[3] == "being_written" for x in states)  # the opening first
    assert [s["first_audio"] for s in seen] == sorted(s["first_audio"] for s in seen)  # false, then true for good
    done = [s["stages_done"] for s in seen]
    assert done == sorted(done) and seen[-1]["stages_done"] == seen[-1]["stages_total"] == 11
    last = final_status(r)
    assert last["audio"] == "10_audio/chapter.mp3" and last["minutes_left"] == 0 and last["title"] == "The harbor that heals itself"
    assert last["waiting_for"] is None and last["check_in"] is None
    for sec in last["sections"]:
        assert sec["ready"] and sec["complete"] and sec["state"] in ("ready", "playing", "played")
        assert (r.root / sec["text"]).exists() and (r.root / sec["timing"]).exists()
        assert all((r.root / p["src"]).exists() and p["seconds"] > 0 for p in sec["pieces"])
        assert sec["seconds"] == pytest.approx(sum(p.seconds for p in narrate.pieces(r, sec["n"])), abs=0.01)
    assert sum(s["seconds"] for s in last["sections"]) == pytest.approx(out["seconds"], abs=0.25)
    assert status.plain(last)["sections"][0] == {"title": "Forms in the sea", "state": last["sections"][0]["state"]}
    assert status.plain(last)["can_start_listening"] is True and status.plain({"state": "making"}) == {}
    state = json.loads(base.path(r, "state").read_text(encoding="utf-8"))  # the scheduler's snapshot, for milestone 5
    assert status.sections(r, json.loads(base.path(r, "outline_json").read_text(encoding="utf-8")), state) == last["sections"]

    # after the chapter: the decisions, the listener's history, the medians of this run
    decisions = r.read("decisions")
    assert "## start" in decisions and "## angle" in decisions and "## done" in decisions and "4 sections" in decisions
    assert f"({r.name}," in (tmp_path / "listener" / "history.md").read_text(encoding="utf-8")
    medians = T.load(w.cfg)
    assert set(medians) >= {*MEDIANS, "scout", "outline", "first_audio"} and medians["lock_final"] is not None
    assert all(medians[k] is not None for k in (*MEDIANS, "scout", "outline", "first_audio"))
    assert T.estimate(w.cfg) == events(r, T.FIRST_AUDIO)[0]["t"]


def test_status_moves_through_the_opening_steps_and_sections_carry_their_steps(tmp_path, cfg, monkeypatch):
    """Ian, 2026-11-13: the steps instead of a timer. Before the first audio the status names the four
    steps in order with a countdown from the medians; after it, each section says what is happening to it
    and the next one not ready carries its estimate."""
    w = world(tmp_path, cfg, monkeypatch)
    medians = {k: 1000.0 for k in (*MEDIANS, "scout", "outline", "first_audio")}  # no fake step runs past these
    base.write_json(T.path(w.cfg), {"medians": medians, "runs": []})
    seen, real = [], new.write_status
    monkeypatch.setattr(new, "write_status", lambda r, cfg, state, **f: seen.append(real(r, cfg, state, **f)) or seen[-1])

    fast_run.run(w.r, w.cfg, player=SimPlayer(), episode=False)

    early = [s for s in seen if not s["first_audio"]]
    doing = [next(x["key"] for x in s["steps"] if x["state"] == "doing") for s in early]
    assert [k for i, k in enumerate(doing) if i == 0 or doing[i - 1] != k] == ["scout", "outline", "opening", "first_piece"]
    for s in early:
        keys = [x["key"] for x in s["steps"]]
        at = keys.index(next(x["key"] for x in s["steps"] if x["state"] == "doing"))
        assert [x["state"] for x in s["steps"]] == ["done"] * at + ["doing"] + ["todo"] * (3 - at)
        assert s["first_audio_in"] is not None and s["minutes_left"] == math.ceil(s["first_audio_in"] / 60)
        # the current step's median and every later one's, less what has run of it
        assert 1000 * (4 - at) - 30 < s["first_audio_in"] <= 1000 * (4 - at)
        # the end of the step under way: the page and the guide stop counting there with no new status
        assert 0 < s["step_ends_in"] <= s["first_audio_in"] and 1000 - 30 < s["step_ends_in"] <= 1000
    assert early[0]["first_audio_in"] > 3900 and early[-1]["first_audio_in"] <= 1000
    assert all(s["first_audio_in"] is None and s["step_ends_in"] is None and {x["state"] for x in s["steps"]} == {"done"}
               for s in seen if s["first_audio"])

    listed = [s["sections"] for s in seen if s["sections"]]
    assert all(x["step"] in status.STEPS for secs in listed for x in secs)
    assert any(x["step"] == "researching" for secs in listed for x in secs)
    for step in ("writing", "editing", "recording"):
        assert any(x["step"] == step for secs in listed for x in secs if x["n"] > 1), step
    for secs in listed:
        for x in secs:
            assert (x["step"] == "ready") == x["ready"]
    after = [s for s in seen if s["first_audio"] and s["state"] == "making" and not all(x["ready"] for x in s["sections"])]
    assert after and all(sum(x["ready_in"] is not None for x in s["sections"]) == 1 for s in after)
    for s in after:  # on the first section not ready, and only there
        (x,) = [x for x in s["sections"] if x["ready_in"] is not None]
        assert x["n"] == next(y["n"] for y in s["sections"] if not y["ready"]) and x["ready_in"] > 0
    assert all(x["ready_in"] is None for x in final_status(w.r)["sections"])


def test_status_gives_no_countdown_without_medians(tmp_path, cfg, monkeypatch):
    w = world(tmp_path, cfg, monkeypatch)
    seen, real = [], new.write_status
    monkeypatch.setattr(new, "write_status", lambda r, cfg, state, **f: seen.append(real(r, cfg, state, **f)) or seen[-1])
    fast_run.run(w.r, w.cfg, player=SimPlayer(), episode=False)
    assert seen[0]["steps"][0] == {"key": "scout", "label": "Looking into it", "state": "doing"}
    assert all(s["first_audio_in"] is None and s["step_ends_in"] is None and s["minutes_left"] in (None, 0) for s in seen)
    assert all(x["ready_in"] is None for s in seen for x in s["sections"])


@pytest.mark.parametrize("total", [3, 5])
def test_three_and_five_sections(tmp_path, cfg, monkeypatch, total):
    w = world(tmp_path, cfg, monkeypatch, sections=total)
    out = fast_run.run(w.r, w.cfg, player=SimPlayer(), episode=False)
    assert out["sections"] == total and not out["episode"] and final_status(w.r)["state"] == "ready"
    assert not (w.r.root / "11_episode" / "episode.json").exists() and w.r.path("chapter_mp3").exists()
    assert_check_2(w)
    assert checks.chapter_problems(locked(w.r), w.r.read("dossier")) == []
    modes = [e["mode"] for e in events(w.r, T.LOCK_DONE)]
    assert modes == ["paired"] * (total - 2) + ["final"]
    assert [x["n"] for x in final_status(w.r)["sections"]] == list(range(1, total + 1))


def test_a_run_is_unattended_and_takes_the_fast_cap(tmp_path, cfg, monkeypatch):
    w = world(tmp_path, cfg, monkeypatch)
    w.r.unattended = False
    with pytest.raises(StageError, match="unattended"):
        fast_run.run(w.r, w.cfg)
    assert w.chain.calls == []
    w.r.unattended, w.r.max_usd = True, None
    monkeypatch.setattr(fast_run._Driver, "run", lambda self: {"cap": self.r.max_usd})
    assert fast_run.run(w.r, w.cfg) == {"cap": w.cfg["fast"]["max_run_usd"]}


# --- listener notes (acceptance check 5b, at the run level) -----------------------------------

def test_a_note_added_mid_run_reaches_every_later_draft_and_lock_and_nothing_locked(tmp_path, cfg, monkeypatch):
    w = world(tmp_path, cfg, monkeypatch)
    r, said, before = w.r, "The barge going down into the water stays with me.", {}

    def at_lock_2(kwargs):  # the lock of section 2 has been asked for: its request is already written
        before.update({1: sections.locked_text(r, 1), "at": len(w.chain.calls)})
        notes.add(r.root, said, "landed", section=1)

    w.chain.on("lock", 2, at_lock_2)
    fast_run.run(r, w.cfg, player=SimPlayer(), episode=False)

    writing = [(k, kw) for k, kw in w.chain.calls if k[0] in ("opening", "opening_edit", "draft", "lock")]
    cut = next(i for i, (k, _) in enumerate(writing) if k == ("lock", 2))
    earlier, later = writing[:cut + 1], writing[cut + 1:]
    assert [k for k, _ in later] == [("draft", 4), ("lock", 3), ("lock", 4)]
    assert all(said in all_text(kw) for _, kw in later)
    assert not any(said in all_text(kw) for _, kw in earlier)
    assert sections.locked_text(r, 1) == before[1]  # what was locked when it was said is as it was
    assert said not in "\n".join(text for _, text in locked(r))  # the fake writer does not echo it: nothing was patched in
    note = events(r, T.LISTENER_NOTE)
    assert [(e["kind"], e["section"], e["text"]) for e in note] == [("landed", 1, said)]
    assert w.chain.of("note") == []  # a remark that landed is not put to the research judge


def test_a_note_the_dossier_cannot_serve_starts_one_more_research_agent(tmp_path, cfg, monkeypatch):
    question = "Did the Romans know why the ash worked?"
    w = world(tmp_path, cfg, monkeypatch, note=B.note_reply(
        research=True, section=4, idea="The Romans knew the recipe and not the chemistry.",
        question="What did Roman writers say about why the ash set under water?", reason="No entry says."))
    r = w.r
    researching = threading.Event()
    w.chain.on("row", "L05", lambda kw: researching.set())
    judged, real = [], research.note_row
    monkeypatch.setattr(research, "note_row", lambda *a, **k: judged.append(k.get("budget")) or real(*a, **k))

    def at_lock_2(kwargs):  # the draft of 4 has not begun, so a note may still start research for it
        notes.add(r.root, question, "question", section=1)
        assert researching.wait(8), "the note's research agent never started"

    w.chain.on("lock", 2, at_lock_2)
    fast_run.run(r, w.cfg, player=SimPlayer(), episode=False)

    judge = w.chain.of("note")
    assert len(judge) == 1 and question in all_text(judge[0]) and "Section 4" in all_text(judge[0])
    # the judge is handed the run's budget, whose notes' counter knows what a running agent has reserved
    assert len(judged) == 1 and judged[0].total == 30 and judged[0].notes.total == 6 and judged[0].notes.left == 3
    led = json.loads(r.path("ledger").read_text(encoding="utf-8"))
    row = next(x for x in led["rows"] if x["id"] == "L05")
    assert row["from_note"] and row["section"] == 4 and row["listener_note"] == question and row["dossier_refs"]
    agent = w.chain.of("row", "L05")[0]
    assert [t["max_uses"] for t in agent["tools"]] == [3, 1]  # the note agent's smaller budget (area B)
    assert [(e["row"], e["section"], e["researched"]) for e in events(r, T.NOTE_ROW)] == [("L05", 4, True)]
    assert events(r, T.RESEARCH_DONE, row="L05")[0]["entries"] > 0
    # the draft of section 4 waited for it, and was written with its entries in hand
    order = [(e["event"], e.get("row") or e.get("section")) for e in events(r)]
    assert order.index((T.RESEARCH_DONE, "L05")) < order.index((T.DRAFT_START, 4))
    offered = C.offered(C.last_block(w.chain.of("draft", 4)[0]))
    assert set(row["dossier_refs"]) <= {f"D{d:02d}" for d in offered}
    assert "listener note" in r.read("decisions")


# --- holds, the solo fallback, check-ins ------------------------------------------------------

def test_a_hold_past_max_hold_locks_the_section_solo_and_the_listener_is_checked_in_on(tmp_path, cfg, monkeypatch):
    w = world(tmp_path, cfg, monkeypatch)
    r = w.r
    clock = slow_section_3(w, max_hold=3)
    listener = ScriptedListener(REMARKS, gap=0)

    out = fast_run.run(r, w.cfg, player=Patient(), listener=listener, clock=clock, episode=False)

    # the solo fallback: section 2, after max_hold of waiting, still a full edit
    assert out["solo"][0] == 2 and sections.saved_lock(r, 2).mode == "solo"
    solo = events(r, T.SOLO_FALLBACK, section=2)
    assert len(solo) == 1 and solo[0]["waited"] >= 3
    lock_2 = all_text(w.chain.of("lock", 2)[0])
    assert "locking section 2 on its own" in lock_2 and '<draft n="3">' not in lock_2
    modes = [e["mode"] for e in events(r, T.LOCK_DONE)]
    assert modes[0] == "solo" and modes[-1] == "final" and len(modes) == 3
    assert "## solo edit" in r.read("decisions")
    order = [(e["event"], e.get("section")) for e in events(r)]
    assert order.index((T.SOLO_FALLBACK, 2)) < order.index((T.DRAFT_START, 3))  # section 3's research was still out
    assert order.index((T.LOCK_DONE, 2)) < order.index((T.NARRATE_START, 2))  # held for its edit all the same

    # the hold, in the timeline and in the result
    hold = out["holds"][0]  # (a busy machine may add a wait for a later section; this one is by design)
    assert hold["section"] == 2 and hold["reason"] == "end_of_audio" and hold["seconds"] >= 3 and hold["solo"]
    start, end = events(r, T.HOLD_START, section=2)[0], events(r, T.HOLD_END, section=2)[0]
    assert end["seconds"] == hold["seconds"] and end["solo"] and end["decision"] == "check_in" and end["t"] > start["t"]
    assert events(r, T.SECTION_NEEDED, section=2)[0]["state"] == "waiting"
    assert events(r, T.SECTION_NEEDED, section=2)[0]["t"] < events(r, T.PIECE_READY, section=2, index=0)[0]["t"]

    # no medians yet, so no estimate: the wait is a check-in, and the listener's hook fired once, for it
    first = events(r, T.CHECK_IN)[0]
    assert (first["section"], first["decision"], first["eta"], first["late"]) == (2, "check_in", None, False)
    assert listener.at["waiting_for"] == 2 and len(listener.said) == 3
    said = notes.load(r.root)
    assert [(n["kind"], n["section"], n["text"]) for n in said] == [(x["kind"], 1, x["text"]) for x in REMARKS]
    assert [e["text"] for e in events(r, T.LISTENER_NOTE)] == [x["text"] for x in REMARKS]
    # said while section 2 was not yet locked: its lock and everything after had them (check 5b)
    for kw in [w.chain.of("lock", 2)[0], w.chain.of("draft", 3)[0], w.chain.of("lock", 4)[0]]:
        assert all(x["text"] in all_text(kw) for x in REMARKS)
    assert not any(x["text"] in all_text(w.chain.of("draft", 2)[0]) for x in REMARKS)
    assert len(w.chain.of("note")) == 1  # only the "unclear" remark is a research question; the judge said no
    assert all(x["text"] in all_text(w.chain.of("note")[0]) for x in REMARKS[1:2])


def test_a_wait_expected_to_be_short_is_a_pause_and_brings_no_check_in(tmp_path, cfg, monkeypatch):
    w = world(tmp_path, cfg, monkeypatch)
    r = w.r
    base.write_json(T.path(w.cfg), {"medians": {k: 1.0 for k in (*MEDIANS, "scout", "outline", "first_audio")}, "runs": []})
    w.cfg["fast"]["short_wait_seconds"] = 100000
    # section 2's research does not come back until the listener has run out of audio and is waiting for it
    w.chain.rows["L01"] = lambda kw: (wait_for(lambda: happened(r, T.HOLD_START)), B.row_message(kw, "L01"))[1]
    seen, real = [], new.write_status
    monkeypatch.setattr(new, "write_status", lambda r, cfg, state, **f: seen.append(real(r, cfg, state, **f)) or seen[-1])
    listener = ScriptedListener(REMARKS, gap=0)

    out = fast_run.run(r, w.cfg, player=Patient(), listener=listener, clock=fast_clock(20), episode=False)

    assert out["holds"][0]["section"] == 2 and out["solo"] == []
    decided = events(r, T.CHECK_IN)
    assert {e["decision"] for e in decided} == {"pause"} and decided[0]["section"] == 2 and decided[0]["eta"] is not None
    assert listener.at is None and notes.load(r.root) == []
    assert events(r, T.HOLD_END, section=2)[0]["decision"] == "pause"
    waiting = [s for s in seen if s["waiting_for"] == 2]
    assert waiting and all(s["check_in"] == "pause" and s["eta_seconds"] is not None for s in waiting)
    assert status.plain(waiting[0])["waiting_for"] == "Ash and lime" and "expected_in_seconds" in status.plain(waiting[0])
    assert seen[0]["minutes_left"] == 1  # an estimate exists now: a minute at least until the first audio
    assert final_status(r)["waiting_for"] is None and final_status(r)["check_in"] is None


def test_a_pause_that_outlasts_its_estimate_becomes_a_check_in(tmp_path, cfg, monkeypatch):
    w = world(tmp_path, cfg, monkeypatch)
    r = w.r
    base.write_json(T.path(w.cfg), {"medians": {k: 0.5 for k in (*MEDIANS, "scout", "outline", "first_audio")}, "runs": []})
    w.cfg["fast"]["short_wait_seconds"] = 4  # on a clock twenty times the wall's
    w.chain.rows["L01"] = lambda kw: (wait_for(lambda: happened(r, T.CHECK_IN, late=True)), B.row_message(kw, "L01"))[1]
    listener = ScriptedListener(REMARKS[:1], gap=0)
    fast_run.run(r, w.cfg, player=Patient(), listener=listener, clock=fast_clock(20), episode=False)
    decided = [(e["decision"], e["late"]) for e in events(r, T.CHECK_IN, section=2)]
    assert decided == [("pause", False), ("check_in", True)] and listener.at["waiting_for"] == 2
    end = events(r, T.HOLD_END, section=2)[0]
    assert end["decision"] == "check_in" and end["seconds"] > 4


def test_a_research_row_that_does_not_come_back_is_written_without(tmp_path, cfg, monkeypatch):
    w = world(tmp_path, cfg, monkeypatch)
    r = w.r
    w.cfg["fast"]["research"]["row_timeout_seconds"] = 5  # on a clock twenty times the wall's
    drafting = threading.Event()
    w.chain.rows["L03"] = lambda kw: (drafting.wait(8), B.row_message(kw, "L03"))[1]  # back only once 3 is being drafted
    w.chain.on("draft", 3, lambda kw: drafting.set())

    out = fast_run.run(r, w.cfg, clock=fast_clock(20), episode=False)

    assert out["sections"] == 4 and final_status(r)["state"] == "ready"
    assert [e["section"] for e in events(r, T.RESEARCH_TIMEOUT, row="L03")] == [3]
    order = [(e["event"], e.get("row") or e.get("section")) for e in events(r)]
    assert order.index((T.RESEARCH_TIMEOUT, "L03")) < order.index((T.DRAFT_START, 3)) < order.index((T.RESEARCH_DONE, "L03"))
    rows = C.tag(C.last_block(w.chain.of("draft", 3)[0]), "rows")
    assert "L03" in rows and "came back thin (no findings); no entries" in rows  # the writer is told, and works with what there is
    # the agent's late findings are still filed (ask-by-voice can use them), and nothing waited for them
    assert "### L03" in r.read("dossier") and len(w.chain.of("row", "L03")) == 1


@pytest.mark.parametrize("seed", [5, 7, 13, 19, 21])
def test_whatever_the_timing_the_rules_hold(tmp_path, cfg, monkeypatch, seed):
    """The real threaded driver under made-up conditions: every reply late by a random amount, a
    clock of random speed, a skip, short and long max_hold, rows that time out. (These seeds cover
    three, four and five sections, no hold and several, and the solo fallback.)"""
    rng = random.Random(seed)
    total = rng.choice([3, 4, 5])
    w = world(tmp_path, cfg, monkeypatch, sections=total)
    r = w.r
    w.cfg["fast"]["max_hold_seconds"] = rng.choice([2, 5, 180])
    w.cfg["fast"]["research"]["row_timeout_seconds"] = rng.choice([4, 600])
    slow, dice = rng.choice([0.02, 0.08, 0.2]), threading.Lock()

    def late(kwargs):
        with dice:
            wait = rng.random() * slow
        time.sleep(wait)

    for what in ("opening", "opening_edit", "draft", "lock", "row", "note"):
        w.chain.on(what, None, late)
    skips = [rng.random() * 3] if rng.random() < 0.5 else []
    player = SimPlayer(skips=skips) if rng.random() < 0.5 else Patient(skips=skips)

    out = fast_run.run(r, w.cfg, player=player, clock=fast_clock(rng.choice([1, 20, 60])), episode=False)

    assert out["sections"] == total and final_status(r)["state"] == "ready"
    assert checks.chapter_problems(locked(r), r.read("dossier")) == []
    assert_check_2(w)
    order = [(e["event"], e.get("section")) for e in events(r)]
    for n in range(2, total + 1):
        assert order.index((T.DRAFT_DONE, n)) < order.index((T.LOCK_START, n))        # edited after it was written
        assert order.index((T.LOCK_DONE, n)) < order.index((T.NARRATE_START, n))      # narrated after it was edited
        assert order.index((T.NARRATE_DONE, n - 1)) < order.index((T.NARRATE_START, n))  # and in order
    assert order.count((T.ARCHIVE_DONE, None)) == 1
    modes = {e["section"]: e["mode"] for e in events(r, T.LOCK_DONE)}
    assert modes[total] == "final" and set(out["solo"]) == {n for n, mode in modes.items() if mode == "solo"}
    for e in events(r, T.SOLO_FALLBACK):
        assert e["waited"] >= w.cfg["fast"]["max_hold_seconds"]  # never before max_hold of waiting
    assert len(events(r, T.HOLD_START)) == len(events(r, T.HOLD_END)) == len(out["holds"])
    assert len(w.chain.whats()) == len(set(w.chain.whats()))  # no step was paid for twice


# --- the page's position reports --------------------------------------------------------------

def test_the_page_position_file_is_read_and_a_stale_one_is_not(tmp_path, cfg, monkeypatch):
    w = world(tmp_path, cfg, monkeypatch, name="listening")
    report = {"state": "playing", "section": 1, "piece": 0, "t": 0.4, "chapter_t": 0.4, "waiting_for": None,
              "since": None, "reason": None}
    base.write_json(base.path(w.r, "player"), {"at": time.time(), **report})
    fast_run.run(w.r, w.cfg, episode=False)
    assert [s["state"] for s in final_status(w.r)["sections"]] == ["playing", "ready", "ready", "ready"]
    assert events(w.r, T.PLAYBACK_START) and [e["section"] for e in events(w.r, T.SECTION_NEEDED)] == [1]

    v = world(tmp_path / "second", cfg, monkeypatch, name="gone")
    base.write_json(base.path(v.r, "player"), {"at": time.time() - 100, **report})  # the page was closed long ago
    fast_run.run(v.r, v.cfg, episode=False)
    assert [s["state"] for s in final_status(v.r)["sections"]] == ["ready"] * 4 and not events(v.r, T.PLAYBACK_START)
    assert final_status(v.r)["state"] == "ready"  # nobody listening: the run goes straight through all the same


def test_page_report_moves_wall_clock_times_into_the_loops_clock():
    raw = {"at": 1000.0, "state": "waiting", "section": 3, "piece": 0, "t": 0, "chapter_t": 300.0, "waiting_for": 3,
           "since": 990.0, "reason": "skip", "extra": "dropped"}
    report, age = fast_run.page_report(raw, now=50.0, wall=1004.0)
    assert age == 4.0 and report["since"] == 36.0  # the wait began 14 s ago, by either clock
    assert report["waiting_for"] == 3 and report["reason"] == "skip" and "at" not in report and "extra" not in report
    assert fast_run.page_report({"state": "idle"}, 50.0, 1004.0) == ({"state": "idle"}, 0.0)


# --- stopping and starting again --------------------------------------------------------------

def test_a_second_run_after_a_kill_pays_for_nothing_twice_and_reaches_the_archive(tmp_path, cfg, monkeypatch):
    w = world(tmp_path, cfg, monkeypatch)
    killed = tmp_path / "killed" / "runs" / w.r.name

    def kill(kwargs):  # the folder as a kill at this moment would leave it: mid-narration, section 4 not drafted
        try:
            shutil.copytree(w.r.root, killed, ignore=shutil.ignore_patterns("*.tmp*", "*.play.wav"))
        except shutil.Error:
            pass  # a file renamed while it was being copied, which a kill would not have left either

    w.chain.on("draft", 4, kill)
    fast_run.run(w.r, w.cfg, player=SimPlayer(), episode=False)

    r2 = Run.open(w.r.name, runs_dir=killed.parent)
    r2.unattended, r2.max_usd = True, 8.0
    had = {"drafts": [n for n in range(1, 5) if sections.latest_draft(r2, n) is not None],
           "locked": [n for n in range(1, 5) if sections.locked_text(r2, n) is not None],
           "rows": [x for x in ("L01", "L02", "L03", "L04") if (r2.path("research_dir") / f"{x}.md").exists()],
           "pieces": [p.text for n in range(1, 5) for p in narrate.pieces(r2, n)]}
    assert had["locked"][:2] == [1, 2] and 4 not in had["drafts"] and had["drafts"][:3] == [1, 2, 3] and had["rows"]
    assert not r2.path("chapter_mp3").exists() and final_status(r2)["state"] == "making"
    chain2, voice2 = install(monkeypatch)
    seen, real = [], new.write_status
    monkeypatch.setattr(new, "write_status", lambda r, cfg, state, **f: seen.append(real(r, cfg, state, **f)) or seen[-1])

    out = fast_run.run(r2, w.cfg, player=SimPlayer(), episode=False)
    monkeypatch.setattr(new, "write_status", real)

    # regression: a continued run first wrote "making", "Looking into it", with no sections and no audio
    assert all(len(s["sections"]) == 4 for s in seen) and seen[0]["first_audio"] == bool(had["pieces"])
    assert seen[0]["sections"][1]["text"] == "06_segments/seg02.md"  # what was locked is there from the first status on
    assert not {s["label"] for s in seen} & {"Looking into it", "Planning the chapter"}
    whats = chain2.whats()
    assert not {k[0] for k in whats} & {"scout", "scout_writeup", "outline", "outline_edit", "opening", "opening_edit"}
    assert not [k for k in whats if k[0] == "draft" and k[1] in had["drafts"]]
    assert not [k for k in whats if k[0] == "lock" and k[1] in had["locked"]]
    assert not [k for k in whats if k[0] == "row" and k[1] in had["rows"]]
    assert ("draft", 4) in whats and ("lock", 4) in whats and len(whats) == len(set(whats))  # each step left, once
    assert not set(voice2.texts) & set(had["pieces"])  # no piece narrated twice
    assert out["sections"] == 4 and final_status(r2)["state"] == "ready" and r2.path("chapter_mp3").exists()
    assert checks.chapter_problems(locked(r2), r2.read("dossier")) == []
    assert events(r2, T.CONTINUED) and len(events(r2, T.SEED_SET)) == 1 and len(events(r2, T.SCOUT_DONE)) == 1
    assert "## continue" in r2.read("decisions")
    claude = lambda r: sum(x.get("cost_usd", 0) for x in r.read_log() if x.get("provider") == "anthropic")
    assert claude(r2) == pytest.approx(claude(w.r))  # the model was paid what one uninterrupted run pays, and no more
    # a third start finds the chapter finished: no call at all, ready again, and never "making" on the way
    chain3, voice3 = install(monkeypatch)
    seen.clear()
    monkeypatch.setattr(new, "write_status", lambda r, cfg, state, **f: seen.append(real(r, cfg, state, **f)) or seen[-1])
    fast_run.run(r2, w.cfg, episode=False)
    assert chain3.calls == [] and voice3.calls == [] and final_status(r2)["state"] == "ready"
    assert [(s["state"], s["audio"], len(s["sections"])) for s in seen] == [("ready", "10_audio/chapter.mp3", 4)]
    assert len(events(r2, T.READY)) == 1 and r2.read("decisions").count("## done") == 1  # ready once, not twice
    assert voice3.balance.reads == 0  # nothing is left to narrate, so the account is not asked


def test_done_from_files_is_what_the_scheduler_restores_from(tmp_path, cfg, monkeypatch):
    w = world(tmp_path, cfg, monkeypatch)
    fast_run.run(w.r, w.cfg, episode=False)
    from faststart.dossier import Dossier

    outline = json.loads(base.path(w.r, "outline_json").read_text(encoding="utf-8"))
    led = json.loads(w.r.path("ledger").read_text(encoding="utf-8"))
    done = fast_run.done_from_files(w.r, outline, led, Dossier(w.r, w.cfg), episode=False)
    assert done["rows"] == ["L01", "L02", "L03", "L04"] and done["drafts"] == done["locked"] == done["narrated"] == [1, 2, 3, 4]
    assert done["archived"] and done["solo"] == [] and [len(done["pieces"][n]) for n in (1, 2, 3, 4)] == [3, 1, 1, 1]
    # with the episode wanted and not built, the archive is still to do
    assert not fast_run.done_from_files(w.r, outline, led, Dossier(w.r, w.cfg), episode=True)["archived"]


def test_a_step_that_fails_once_is_tried_again_by_the_driver(tmp_path, cfg, monkeypatch):
    w = world(tmp_path, cfg, monkeypatch)
    tries = []

    def flaky(kwargs):
        tries.append(1)
        if len(tries) == 1:
            raise RuntimeError("the connection dropped")

    w.chain.on("draft", 3, flaky)
    out = fast_run.run(w.r, w.cfg, player=SimPlayer(), episode=False)
    assert out["sections"] == 4 and len(w.chain.of("draft", 3)) == 2
    retry = events(w.r, T.RETRY)
    assert [(e["action"], e["error"]) for e in retry] == [("draft 3", "RuntimeError: the connection dropped")]
    assert not events(w.r, T.ACTION_FAILED) and final_status(w.r)["state"] == "ready"


def test_a_step_that_keeps_failing_stops_the_run_with_a_problem_and_the_same_command_continues_it(tmp_path, cfg, monkeypatch):
    w = world(tmp_path, cfg, monkeypatch)
    r = w.r

    def broken(kwargs):
        raise RuntimeError("the editor is out")

    w.chain.on("lock", 3, broken)
    with pytest.raises(StageError) as e:
        fast_run.run(r, w.cfg, player=SimPlayer(), episode=False)
    assert "lock 3 (paired) failed 3 times" in str(e.value) and "the editor is out" in str(e.value)
    assert f"python cli.py new {r.name} --unattended" in str(e.value)
    assert len(w.chain.of("lock", 3)) == 3  # the driver's retry, then the scheduler's one more issue
    assert [x["action"] for x in events(r, T.RETRY)] == ["lock 3 (paired)"] and len(events(r, T.ACTION_FAILED)) == 2
    st = final_status(r)
    assert st["state"] == "problem" and st["retryable"] and st["mode"] == "fast"
    assert st["message"] == "Something went wrong while editing part 3 of 4." and st["stage"] == "lock 3"
    assert "the editor is out" in st["detail"] and st["minutes_left"] is None
    assert st["sections"][1]["text"] == "06_segments/seg02.md"  # locked before the lock of 3 was tried
    assert st["sections"][2]["text"] is None and not r.path("chapter_mp3").exists()
    assert "## stopped" in r.read("decisions") and events(r, T.PROBLEM)

    # the editor is back: the same run continues from its files
    chain2, _ = install(monkeypatch)
    out = fast_run.run(r, w.cfg, player=SimPlayer(), episode=False)
    assert [k for k in chain2.whats() if k[0] in ("draft", "lock")] == [("lock", 3), ("lock", 4)]
    assert out["sections"] == 4 and final_status(r)["state"] == "ready" and final_status(r)["message"] is None


@pytest.mark.parametrize("cap, stage", [(0.01, "outline"), (0.15, None)])
def test_the_cap_stops_the_run_with_a_problem_that_trying_again_cannot_fix(tmp_path, cfg, monkeypatch, cap, stage):
    w = world(tmp_path, cfg, monkeypatch)
    w.r.max_usd = cap
    with pytest.raises(StageError, match="cap"):
        fast_run.run(w.r, w.cfg, player=SimPlayer(), episode=False)
    st = final_status(w.r)
    assert st["state"] == "problem" and st["retryable"] is False and "spending limit" in st["message"]
    assert st["mode"] == "fast" and (stage is None or st["stage"] == stage)
    assert w.r.spent() < cap + 0.25  # the calls in flight when it was reached, and no more
    assert not w.r.path("chapter_mp3").exists() and "## stopped" in w.r.read("decisions")
    if stage is None:  # reached in the loop: a writing step raised it, and it was not tried again
        assert not events(w.r, T.RETRY) and events(w.r, T.OPENING_START)


def test_a_step_with_no_result_counts_as_failed_and_its_late_result_is_not_paid_for_twice(tmp_path, cfg, monkeypatch):
    w = world(tmp_path, cfg, monkeypatch)
    r = w.r
    monkeypatch.setattr(fast_run, "GRACE", 0.2)
    w.cfg["fast"]["step_timeout_seconds"] = 20  # on a clock a hundred times the wall's; the opening gets twice that
    gate = threading.Event()
    w.chain.on("opening_edit", 1, lambda kw: gate.wait(8) and None)
    try:
        with pytest.raises(StageError, match="opening failed 2 times.*no result after 40 s"):
            fast_run.run(r, w.cfg, clock=fast_clock(100), episode=False)
        assert [e["action"] for e in events(r, T.STEP_TIMEOUT)] == ["opening", "opening"]
        st = final_status(r)
        assert st["state"] == "problem" and st["retryable"] and st["message"] == "Something went wrong while writing the opening."
        assert not events(r, T.NARRATE_START)  # nothing was narrated without its edit
    finally:
        gate.set()
    # the first try comes back after all; the second, which waited its turn, takes its files and asks nothing
    assert wait_for(lambda: not [t for t in threading.enumerate() if t.name.startswith("fast-")])
    assert len(w.chain.of("opening")) == 1 and len(w.chain.of("opening_edit")) == 1
    assert sections.locked_text(r, 1) and len(list(base.path(r, "drafts").glob("seg01.d*.md"))) == 1


def test_a_failed_episode_build_is_recorded_and_the_chapter_is_still_ready(tmp_path, cfg, monkeypatch):
    from episode import references

    w = world(tmp_path, cfg, monkeypatch)

    def no_cards(*a, **k):
        raise ValueError("the references reply was not JSON")

    monkeypatch.setattr(references, "ask", no_cards)
    out = fast_run.run(w.r, w.cfg, player=SimPlayer())
    st = final_status(w.r)
    assert st["state"] == "ready" and st["audio"] == "10_audio/chapter.mp3" and not out["episode"]
    assert w.r.path("chapter_mp3").exists() and (w.r.root / "11_episode" / "timing.json").exists()
    assert not (w.r.root / "11_episode" / "episode.json").exists()
    assert "references reply was not JSON" in events(w.r, T.EPISODE_FAILED)[0]["error"]
    assert "## episode" in w.r.read("decisions") and not events(w.r, T.PROBLEM)
    # the same command again builds only the episode
    chain2, voice2 = install(monkeypatch)
    again = fast_run.run(w.r, w.cfg)
    assert again["episode"] and (w.r.root / "11_episode" / "episode.json").exists()
    assert chain2.calls == [] and voice2.calls == []


def test_a_paired_lock_that_revises_the_next_draft_is_what_later_calls_read(tmp_path, cfg, monkeypatch):
    """Check 2's "latest draft", held: the stock fake editor never revised the next draft, so a
    driver that kept the text draft() returned instead of reading the files would have passed."""
    mark = "The harbour wall, seen again from the water, is the same wall."
    w = world(tmp_path, cfg, monkeypatch)
    r = w.r
    w.chain.on("lock", 2, lambda kw: C.lock_reply(kw, next_text=C.tag(C.last_block(kw), "draft", 'n="3"') + "\n\n" + mark))
    fast_run.run(r, w.cfg, player=SimPlayer(), episode=False)
    assert sorted(p.name for p in base.path(r, "drafts").glob("seg03.d*.md")) == ["seg03.d1.md", "seg03.d2.md"]
    assert mark not in (base.path(r, "drafts") / "seg03.d1.md").read_text(encoding="utf-8")
    sent = earlier_sent(w.chain.of("draft", 4)[0])[3]
    assert sent == ("draft", sections.latest_draft(r, 3)) and mark in sent[1]  # the revised draft, in full
    assert mark in C.tag(C.last_block(w.chain.of("lock", 3)[0]), "draft", 'n="3"')
    assert mark in sections.locked_text(r, 3) and "\n".join(t for _, t in locked(r)).count(mark) == 1


def test_a_wait_reported_by_the_page_goes_solo_after_max_hold(tmp_path, cfg, monkeypatch):
    """The production path of the solo fallback: fast/player.json, as the server will write it
    from the page's reports (here every 50 ms, and torn on purpose: half, a pause, the rest)."""
    w = world(tmp_path, cfg, monkeypatch)
    r = w.r
    w.cfg["fast"]["max_hold_seconds"] = 1.0
    gate, stop, began = threading.Event(), threading.Event(), {}
    w.chain.rows["L03"] = lambda kw: (gate.wait(10), B.row_message(kw, "L03"))[1]
    w.chain.on("lock", 2, lambda kw: gate.set())
    path = base.path(r, "player")

    def page() -> None:  # the listener finished the opening and waits for section 2
        wait_for(lambda: happened(r, T.NARRATE_DONE, section=1), 20)
        began["since"] = time.time()
        while not stop.is_set():
            there = happened(r, T.PIECE_READY, section=2)
            text = json.dumps({"at": time.time(), "state": "playing" if there else "waiting", "section": 2, "piece": 0,
                               "t": 0.1 if there else 0, "chapter_t": 5, "waiting_for": None if there else 2,
                               "since": None if there else began["since"], "reason": None if there else "end_of_audio"})
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text[:len(text) // 2])
                f.flush()
                time.sleep(0.003)
                f.write(text[len(text) // 2:])
            time.sleep(0.05)

    t = threading.Thread(target=page, name="page-sim", daemon=True)
    t.start()
    try:
        out = fast_run.run(r, w.cfg, episode=False)
    finally:
        stop.set()
        t.join()
    assert out["solo"] == [2] and out["holds"][0]["section"] == 2 and out["holds"][0]["seconds"] >= 1.0
    (fell,) = events(r, T.SOLO_FALLBACK)
    assert fell["section"] == 2 and fell["waited"] >= 1.0 and events(r, T.HOLD_END, section=2)[0]["solo"]
    assert events(r, T.SECTION_NEEDED, section=2)[0]["state"] == "waiting" and final_status(r)["state"] == "ready"


def test_a_solo_lock_issued_again_is_one_fallback_in_the_timeline_and_the_report(tmp_path, cfg, monkeypatch):
    """Regression: the solo fallback and its decision were logged at every issue of the lock, so a
    solo lock that failed and was issued again counted as two sections that fell back."""
    w = world(tmp_path, cfg, monkeypatch)
    r = w.r
    clock = slow_section_3(w, max_hold=3)
    release, tries = w.chain.hooks[("lock", 2)], []

    def flaky(kw):  # section 3's research stays out until the third try, so the lock can only be solo again
        tries.append(1)
        if len(tries) <= 2:
            raise RuntimeError("overloaded")
        release(kw)

    w.chain.on("lock", 2, flaky)
    out = fast_run.run(r, w.cfg, player=Patient(), clock=clock, episode=False)
    assert len(tries) == 3 and out["solo"] == [2] and sections.saved_lock(r, 2).mode == "solo"
    assert [e["mode"] for e in events(r, T.LOCK_START, section=2)] == ["solo", "solo"]  # issued twice (and retried once)
    assert [(e["section"], e["waited"] >= 3) for e in events(r, T.SOLO_FALLBACK)] == [(2, True)]
    assert r.read("decisions").count("## solo edit") == 1
    rep = e2e.report(r, w.cfg)
    assert [(x["section"], x["waited"] >= 3) for x in rep["solo"]] == [(2, True)]
    assert "Solo-edit fallbacks: 1 (section 2)." in e2e.render(rep)


# --- leaving ----------------------------------------------------------------------------------

def test_a_run_that_stops_waits_for_the_calls_in_flight_so_they_are_logged_and_kept(tmp_path, cfg, monkeypatch):
    """Regression: every exit left its worker threads after 5 s. A research agent still searching
    had written no findings and no line in the log; under cli.py the process then ended and killed
    it: its spend was missing from the run's total and cap, and the next start paid for the row again."""
    w = world(tmp_path, cfg, monkeypatch)
    r = w.r
    monkeypatch.setattr(fast_run, "GRACE", 0.2)  # what a hung thread gets; a call that is only slow gets longer
    started, gate, order = threading.Event(), threading.Event(), {}
    findings = r.path("research_dir") / "L04.md"

    def slow_row(kw):  # an agent mid-search when the run stops
        started.set()
        gate.wait(15)
        return B.row_message(kw, "L04")

    def broken(kw):
        started.wait(5)
        raise RuntimeError("the editor is out")

    def later() -> None:  # the agent comes back a moment after the run has said what went wrong
        order["problem_first"] = wait_for(lambda: (base.read_json(r.path("status")) or {}).get("state") == "problem", 15) \
            and not findings.exists()
        time.sleep(0.8)
        gate.set()

    w.chain.rows["L04"] = slow_row
    w.chain.on("lock", 2, broken)
    t = threading.Thread(target=later, name="test-later", daemon=True)
    t.start()
    try:
        with pytest.raises(StageError, match="lock 2 .paired. failed 3 times"):
            fast_run.run(r, w.cfg, player=SimPlayer(), episode=False)
        left = fast_threads(), findings.exists()  # as the run returned: under cli.py the process ends here
    finally:
        gate.set()
        t.join()
    assert order["problem_first"]  # the page was told at once; the wait came after
    assert left == ([], True)
    assert [x["cost_usd"] > 0 for x in r.read_log() if x.get("row") == "L04"] == [True]  # its spend is in the log
    spent = r.spent()
    chain2, _ = install(monkeypatch)
    fast_run.run(r, w.cfg, player=SimPlayer(), episode=False)
    assert not [k for k in chain2.whats() if k[0] == "row"]  # the continue pays for no row again
    assert final_status(r)["state"] == "ready" and r.spent() > spent


def test_a_run_that_stops_ends_narration_with_the_piece_in_flight(tmp_path, cfg, monkeypatch):
    w = world(tmp_path, cfg, monkeypatch)
    r = w.r

    def held(text, **kw):  # the first piece is with ElevenLabs when the run stops
        if not w.voice.calls:
            wait_for(lambda: happened(r, T.PROBLEM))
        return w.voice(text, **kw)

    monkeypatch.setattr(tts, "synthesize", held)
    w.chain.on("draft", 2, lambda kw: (_ for _ in ()).throw(RuntimeError("the writer is out")))
    with pytest.raises(StageError, match="draft 2 failed 3 times"):
        fast_run.run(r, w.cfg, player=SimPlayer(), episode=False)
    # that piece was paid for, so it is filed and logged; no further piece is asked for once the run is leaving
    assert len(w.voice.calls) == 1 and [p.text for p in narrate.pieces(r, 1)] == w.voice.texts
    assert len([x for x in r.read_log() if x.get("provider") == "elevenlabs"]) == 1 and not narrate.narrated(r, 1)
    chain2, voice2 = install(monkeypatch)
    fast_run.run(r, w.cfg, player=SimPlayer(), episode=False)
    assert w.voice.texts[0] not in voice2.texts and len(narrate.pieces(r, 1)) == 3 and final_status(r)["state"] == "ready"


def test_an_interrupted_run_and_a_hung_call_are_not_waited_for(tmp_path, cfg, monkeypatch):
    w = world(tmp_path, cfg, monkeypatch)
    monkeypatch.setattr(fast_run, "GRACE", 0.2)
    w.cfg["fast"]["exit_wait_seconds"] = 30
    gate = threading.Event()
    w.chain.rows["L04"] = lambda kw: (gate.wait(4), B.row_message(kw, "L04"))[1]
    w.chain.on("lock", 2, lambda kw: (_ for _ in ()).throw(KeyboardInterrupt()))
    t0 = time.monotonic()
    try:
        with pytest.raises(KeyboardInterrupt):
            fast_run.run(w.r, w.cfg, player=SimPlayer(), episode=False)
        assert time.monotonic() - t0 < 3 and final_status(w.r)["message"].startswith("The chapter was interrupted while")
    finally:
        gate.set()
    # a row past its own timeout is taken for hung: the stop does not wait for it either
    v = world(tmp_path / "hung", cfg, monkeypatch, name="hung")
    v.cfg["fast"].update(exit_wait_seconds=30)
    v.cfg["fast"]["research"]["row_timeout_seconds"] = 5  # on a clock a hundred times the wall's
    gate2 = threading.Event()
    v.chain.rows["L04"] = lambda kw: (gate2.wait(4), B.row_message(kw, "L04"))[1]
    v.chain.on("lock", 4, lambda kw: (_ for _ in ()).throw(RuntimeError("the editor is out")))
    t0 = time.monotonic()
    try:
        with pytest.raises(StageError, match="lock 4 .final. failed 3 times"):
            fast_run.run(v.r, v.cfg, clock=fast_clock(100), episode=False)
        assert events(v.r, T.RESEARCH_TIMEOUT, row="L04") and time.monotonic() - t0 < 3
    finally:
        gate2.set()


def test_an_archive_issued_again_after_its_timeout_waits_for_the_first_try(tmp_path, cfg, monkeypatch):
    """Regression: the archive was the one step with no mutex, so the try issued after its timeout
    ran beside the first: two episode builds in one folder, and a references call paid twice."""
    from episode import references

    w = world(tmp_path, cfg, monkeypatch)
    r = w.r
    w.cfg["fast"]["step_timeout_seconds"] = 200  # on a clock a hundred times the wall's; the archive gets twice that
    asked, live, peak, lock, stock = [], [0], [0], threading.Lock(), references.ask

    def ask(rr, c, seg, prompt):
        with lock:
            asked.append(seg.id)
            live[0] += 1
            peak[0] = max(peak[0], live[0])
            first = len(asked) == 1
        try:
            if first:  # the first call does not come back until the archive has been given up on
                wait_for(lambda: happened(r, T.STEP_TIMEOUT, action="archive"), 20)
                time.sleep(0.3)
            return stock(rr, c, seg, prompt)
        finally:
            with lock:
                live[0] -= 1

    monkeypatch.setattr(references, "ask", ask)
    out = fast_run.run(r, w.cfg, player=SimPlayer(), clock=fast_clock(100))
    assert [e["action"] for e in events(r, T.STEP_TIMEOUT)] == ["archive"]
    assert asked == ["seg01", "seg02", "seg03", "seg04"] and peak[0] == 1  # each card once, one build at a time
    assert out["episode"] and final_status(r)["state"] == "ready" and len(events(r, T.ARCHIVE_DONE)) == 1


def test_a_row_given_up_on_is_not_researched_again_after_a_restart(tmp_path, cfg, monkeypatch):
    w = world(tmp_path, cfg, monkeypatch)
    r = w.r
    w.cfg["fast"]["research"]["row_timeout_seconds"] = 5  # on a clock twenty times the wall's
    killed = tmp_path / "killed" / "runs" / r.name
    back = threading.Event()
    w.chain.rows["L03"] = lambda kw: (back.wait(8), B.row_message(kw, "L03"))[1]

    def kill(kwargs):  # section 3 is being drafted without L03: the folder as a kill at this moment would leave it
        try:
            shutil.copytree(r.root, killed, ignore=shutil.ignore_patterns("*.tmp*", "*.play.wav"))
        except shutil.Error:
            pass
        back.set()

    w.chain.on("draft", 3, kill)
    fast_run.run(r, w.cfg, clock=fast_clock(20), episode=False)

    from faststart.dossier import Dossier

    r2 = Run.open(r.name, runs_dir=killed.parent)
    r2.unattended, r2.max_usd = True, 8.0
    assert not (r2.path("research_dir") / "L03.md").exists() and len(events(r2, T.RESEARCH_TIMEOUT, row="L03")) == 1
    outline = json.loads(base.path(r2, "outline_json").read_text(encoding="utf-8"))
    led = json.loads(r2.path("ledger").read_text(encoding="utf-8"))
    done = fast_run.done_from_files(r2, outline, led, Dossier(r2, w.cfg), episode=False)
    assert done["timed_out"] == ["L03"] and "L03" not in done["rows"]
    chain2, _ = install(monkeypatch)
    out = fast_run.run(r2, w.cfg, episode=False)
    assert ("row", "L03") not in chain2.whats() and ("draft", 3) in chain2.whats()
    assert len(events(r2, T.RESEARCH_TIMEOUT)) == 1  # known from before the restart: not logged a second time
    assert out["sections"] == 4 and final_status(r2)["state"] == "ready"


# --- the ElevenLabs balance --------------------------------------------------------------------

def test_a_chapter_the_elevenlabs_balance_cannot_cover_is_not_started(tmp_path, cfg, monkeypatch):
    """Regression: nothing read the balance, so a real run could pay for its research and writing
    and stop mid-listen when the month's characters ran out."""
    w = world(tmp_path, cfg, monkeypatch)
    r = w.r
    need = narrate.needed_chars(w.cfg)
    assert need == 21600 and fast_run.chars_ahead(w.cfg) == need == fast_run.chars_ahead(w.cfg, r)
    w.voice.balance.used, w.voice.balance.limit = 35_000, 40_000
    with pytest.raises(StageError, match="Not enough narration characters this month") as e:
        fast_run.run(r, w.cfg, episode=False)
    assert "up to 21,600" in str(e.value) and "5,000 left (35,000 of 40,000 used)" in str(e.value)
    assert w.chain.calls == [] and w.voice.calls == [] and w.voice.balance.reads == 1 and r.spent() == 0
    st = final_status(r)
    assert st["state"] == "problem" and st["retryable"] is False and st["mode"] == "fast"
    assert st["message"] == "There are not enough narration characters this month to make this chapter."
    assert events(r, T.PROBLEM) and "## stopped" in r.read("decisions")

    # the month turns: the same command makes the chapter
    w.voice.balance.used = 0
    fast_run.run(r, w.cfg, episode=False)
    assert final_status(r)["state"] == "ready" and final_status(r)["message"] is None
    real = sum(p.chars for n in range(1, 5) for p in narrate.pieces(r, n))
    assert fast_run.chars_ahead(w.cfg, r) == 0  # a finished chapter needs nothing
    r.path("chapter_mp3").unlink()
    assert fast_run.chars_ahead(w.cfg, r) == 0  # nor one whose every section is narrated
    (base.section_dir(r, 4) / "section.json").unlink()
    assert fast_run.chars_ahead(w.cfg, r) == need - real  # a continued run: less what the voice has already said


@pytest.mark.parametrize("balance", [Balance(limit=None), Balance(error=RuntimeError("no route to host"))])
def test_a_balance_that_cannot_be_read_never_stops_a_run(tmp_path, cfg, monkeypatch, balance):
    w = world(tmp_path, cfg, monkeypatch)
    monkeypatch.setattr(narrate, "balance", balance)
    out = fast_run.run(w.r, w.cfg, episode=False)
    assert out["sections"] == 4 and final_status(w.r)["state"] == "ready" and balance.reads == 1
    (line,) = [x for x in w.r.read_log() if x.get("label") == "ElevenLabs balance"]
    assert "could not be read" in line["error"] and "going on" in line["error"]


def test_simulated_narration_asks_the_account_for_nothing_and_a_real_first_piece_must_fit(tmp_path, cfg, monkeypatch):
    w = world(tmp_path, cfg, monkeypatch)
    w.cfg["fast"]["narration"].update(simulate_after_chars=0, synth_chars_per_second=1e6, speech_chars_per_second=100)
    w.voice.balance.used = w.voice.balance.limit = 40_000  # not a character left
    assert fast_run.chars_ahead(w.cfg, w.r) == 0
    fast_run.run(w.r, w.cfg, episode=False)
    assert final_status(w.r)["state"] == "ready" and w.voice.balance.reads == 0 and w.voice.calls == []

    v = world(tmp_path / "first", cfg, monkeypatch, name="first")
    v.cfg["fast"]["narration"]["simulate_after_chars"] = 450
    v.voice.balance.used, v.voice.balance.limit = 39_600, 40_000
    assert fast_run.chars_ahead(v.cfg, v.r) == 450
    with pytest.raises(StageError, match="may still need up to 450, and the ElevenLabs account has 400 left"):
        fast_run.run(v.r, v.cfg, episode=False)
    assert v.chain.calls == []


# --- the brief while the chapter is being made --------------------------------------------------

def test_the_brief_lists_each_sections_entries_while_the_chapter_is_being_made(tmp_path, cfg, monkeypatch):
    """Regression: 05_brief.md was written at the outline and again only at the archive. Ask-by-voice
    gives the guide an entry in full only when the brief lists it for the section being heard or
    the next, so mid-chapter it saw those sections' research as stubs, with no verbatim flag or source."""
    w = world(tmp_path, cfg, monkeypatch)
    r, mid = w.r, {}

    def at_lock_4(kwargs):  # section 2 has been narrated (the brief is written again then); 4 is not locked yet
        assert wait_for(lambda: happened(r, T.NARRATE_DONE, section=2))
        mid["brief"] = [s["entries"] for s in brief.parse_segments(r.read("brief"))]
        mid["locked_4"] = sections.locked_text(r, 4)

    w.chain.on("lock", 4, at_lock_4)
    first = []
    w.chain.on("opening", 1, lambda kw: first.append([s["entries"] for s in brief.parse_segments(r.read("brief"))]))
    fast_run.run(r, w.cfg, player=SimPlayer(), episode=False)

    cited = lambda n: entry_numbers(" ".join(m.group(0) for m in MARKER.finditer(sections.locked_text(r, n))))
    led = json.loads(r.path("ledger").read_text(encoding="utf-8"))
    refs = lambda n: entry_numbers(" ".join(f"[{x}]" for row in led["rows"] if row["section"] == n
                                            for x in row["dossier_refs"]))
    assert first[0][1:] != [cited(2), cited(3)] and mid["locked_4"] is None
    assert mid["brief"][0] == cited(1) and mid["brief"][1] == cited(2) and set(cited(2)) & set(refs(2))
    assert set(refs(4)) <= set(mid["brief"][3]) and refs(4)  # not locked: the entries its rows have brought in
    assert [s["entries"] for s in brief.parse_segments(r.read("brief"))] == [cited(n) for n in (1, 2, 3, 4)]


# --- one folder, one mode ----------------------------------------------------------------------

def test_a_fast_run_refuses_a_folder_the_overnight_pipeline_has_worked_in(tmp_path, cfg, monkeypatch):
    """Regression: `--mode fast` on an unfinished overnight folder paid for a scout and an outline,
    crashed on the overnight ledger, and on the next try finished "ready" with no research at all."""
    w = world(tmp_path, cfg, monkeypatch)
    r = w.r
    (r.root / "01_map.md").write_text("# Map\n\nThe overnight scout's map.\n", encoding="utf-8")
    r.path("ledger").write_text(json.dumps({"rows": [{"id": "L01", "rank": 1, "idea": "an overnight idea",
                                                      "type": "deep-dive", "episode_has": "",
                                                      "research_question": "an overnight question?",
                                                      "tier_needed": 2}], "parked": [], "connections": []}))
    before = sorted(str(p.relative_to(r.root)) for p in r.root.rglob("*"))
    with pytest.raises(StageError, match="is not a fast run: it already has 01_map.md, ledger.json"):
        fast_run.run(r, w.cfg, episode=False)
    assert w.chain.calls == [] and w.voice.balance.reads == 0
    assert sorted(str(p.relative_to(r.root)) for p in r.root.rglob("*")) == before  # nothing written: no marker, no status


# --- cli.py ------------------------------------------------------------------------------------

@pytest.fixture
def shell(tmp_path, cfg, monkeypatch):
    """cli.main against a temp runs folder, as tests/test_seed_run.py sets it up."""
    import builtins

    from stages import seed

    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(common, "RUNS_DIR", runs)
    monkeypatch.setattr(seed, "RUNS_DIR", runs)
    settings(cfg, tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(builtins, "input", lambda prompt: pytest.fail(f"an unattended run asked: {prompt}"))
    chain, voice = install(monkeypatch)
    return NS(runs=runs, cfg=cfg, chain=chain, voice=voice)


def test_cli_mode_fast_runs_a_fast_run_and_a_run_marked_fast_continues_fast(shell, monkeypatch):
    assert cli.main(["new", "--seed", A.TOPIC, "--why", "It is my trade.", "--unattended", "--mode", "fast"]) == 0
    (root,) = [p for p in shell.runs.iterdir() if p.is_dir()]
    r = Run.open(root.name, runs_dir=shell.runs)
    assert base.is_fast(r) and final_status(r)["state"] == "ready" and final_status(r)["mode"] == "fast"
    assert (root / "11_episode" / "episode.json").exists() and not (root / "01_map.md").exists()
    assert "Cap $8.00" in r.read("decisions")  # fast.max_run_usd, not the overnight cap

    # the server's retry and continue command names no mode: the folder's marker decides
    called = []
    monkeypatch.setattr(fast_run, "run", lambda r, cfg, **kw: called.append(("fast", r.name, r.unattended, r.max_usd)))
    monkeypatch.setattr(new, "run", lambda r, cfg: called.append(("overnight", r.name)))
    assert cli.main(["new", root.name, "--unattended", "--max-usd", "3"]) == 0
    assert called == [("fast", root.name, True, 3.0)]


def test_cli_without_mode_runs_the_overnight_pipeline_as_before(shell, monkeypatch):
    called = []
    monkeypatch.setattr(fast_run, "run", lambda r, cfg, **kw: called.append(("fast", r.name)))
    monkeypatch.setattr(new, "run", lambda r, cfg: called.append(("overnight", r.name, r.unattended, r.max_usd)))
    assert cli.main(["new", "--seed", A.TOPIC, "--unattended"]) == 0
    (root,) = [p for p in shell.runs.iterdir() if p.is_dir()]
    assert called == [("overnight", root.name, True, shell.cfg["unattended"]["max_run_usd"])]
    assert not (root / "fast.json").exists() and shell.chain.calls == []
    assert cli.main(["new", root.name, "--unattended", "--mode", "overnight", "--max-usd", "2"]) == 0
    assert called[-1] == ("overnight", root.name, True, 2.0)


def test_cli_mode_fast_without_unattended_only_writes_the_seed(shell, capsys):
    assert cli.main(["new", "--seed", A.TOPIC, "--mode", "fast"]) == 0
    (root,) = [p for p in shell.runs.iterdir() if p.is_dir()]
    assert (root / "00_source" / "seed.md").exists() and shell.chain.calls == []
    assert f"python cli.py new {root.name} --unattended --mode fast" in capsys.readouterr().out


def test_a_seed_lookup_never_hands_a_run_to_the_other_mode(shell, monkeypatch):
    """Regression: with no run named, `new --seed` took the newest unfinished run of that seed
    whatever had made it. Check 11's overnight command joined the fast run's folder (and ran it
    fast), and --mode fast continued an overnight folder."""
    called = []
    monkeypatch.setattr(fast_run, "run", lambda r, cfg, **kw: called.append(("fast", r.name)))
    monkeypatch.setattr(new, "run", lambda r, cfg: called.append(("overnight", r.name)))
    seed_args = ["new", "--seed", A.TOPIC, "--why", "It is my trade."]
    folders = lambda: sorted(p.name for p in shell.runs.iterdir() if p.is_dir())

    # an overnight seed run that stopped after its scout
    assert cli.main(seed_args) == 0
    (night,) = folders()
    (shell.runs / night / "status.json").write_text(json.dumps({"state": "problem"}), encoding="utf-8")
    (shell.runs / night / "01_map.md").write_text("# Map\n", encoding="utf-8")
    kept = sorted(p.name for p in (shell.runs / night).iterdir())
    assert cli.main(seed_args + ["--unattended", "--mode", "fast"]) == 0
    fast_name = called[-1][1]
    assert called[-1][0] == "fast" and fast_name != night and len(folders()) == 2
    assert sorted(p.name for p in (shell.runs / night).iterdir()) == kept  # the overnight run is as it was
    base.mark_fast(Run.open(fast_name, runs_dir=shell.runs))  # as the fast run does at once

    # from here each command continues its own run: the newer one of the other mode does not hide it
    assert cli.main(seed_args + ["--unattended"]) == 0 and called[-1] == ("overnight", night)
    assert cli.main(seed_args + ["--unattended", "--mode", "fast"]) == 0 and called[-1] == ("fast", fast_name)
    assert cli.main(seed_args + ["--unattended", "--mode", "overnight"]) == 0 and called[-1] == ("overnight", night)
    assert len(folders()) == 2

    # with only the fast run unfinished, the plain command starts a run of its own beside it
    shutil.rmtree(shell.runs / night)
    assert cli.main(seed_args + ["--unattended"]) == 0
    other = called[-1][1]
    assert called[-1][0] == "overnight" and other != fast_name and len(folders()) == 2
    assert not (shell.runs / other / "fast.json").exists()
    # a folder that holds only its seed belongs to neither yet: either mode may take it
    shutil.rmtree(shell.runs / fast_name)
    assert cli.main(seed_args + ["--unattended", "--mode", "fast"]) == 0 and called[-1] == ("fast", other)
    assert folders() == [other]
