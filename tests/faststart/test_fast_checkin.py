"""Fast Start milestone 5, the check-in (docs/fast-start.md, "When a section isn't ready"): the third
voice agent, what the guide is told, its tools, the ask routes' check-in branch (check 5, server
half), the notes a mid-chapter question leaves, and the hand-off to the profile. Offline: the
signed URL and the memory pass are patched, and every folder is a temp one."""

import json
import re
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import ask_fixture as F
from fast_run_fakes import offline  # noqa: F401  (no live client for the whole session)
from ask import aside as A
from ask import routes as ask_routes
from faststart import checkin, notes
from stages import memory, voice
from stages.common import load_config

TITLES = {1: "A wall that heals", 2: "The recipe nobody wrote down", 3: "What the sea does"}
LOCKED = {
    1: "<!-- seg01 -->\n\nRoman harbours still stand in the sea. [D01]\n\n[pause]\n\n"
       "Nobody **expected** a wall to get stronger with age. [D02, D03]",
    2: "<!-- seg02 -->\n\nThe recipe was lime and ash. [D04]\n\nPliny wrote that the waves made it one stone. [D05]",
    3: "<!-- seg03 -->\n\nSeawater grows new crystals inside it. [D06]",
}
HEARD_1 = ["Roman harbours still stand in the sea.", "Nobody expected a wall to get stronger with age."]


def write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")


def add_piece(root, n, i, text, seconds=20.0):
    stem = root / "10_audio" / f"sec{n:02d}" / f"p{i:02d}"
    write(stem.with_suffix(".json"), {"text": text, "seconds": seconds, "pause_ms": 0})
    stem.with_suffix(".wav").write_bytes(b"")
    stem.with_suffix(".mp3").write_bytes(b"")


def set_status(root, pieces, waiting_for=None, **more):
    """status.json as a fast run writes it: `pieces` is {section: how many pieces are playable}."""
    listed = [{"n": n, "title": TITLES[n], "state": "ready" if pieces.get(n) else "being_written",
               "pieces": [{"src": f"10_audio/sec{n:02d}/p{i:02d}.mp3", "seconds": 20.0} for i in range(pieces.get(n, 0))]}
              for n in TITLES]
    write(root / "status.json", {"run": root.name, "state": "making", "mode": "fast", "sections": listed,
                                 "first_audio": True, "waiting_for": waiting_for, "eta_seconds": 40,
                                 "check_in": "check_in" if waiting_for else None, **more})


def join_passes():
    """Wait for the memory hand-off's threads: one that outlived its test would find the real pass."""
    for t in [t for t in threading.enumerate() if t.name == "checkin-memory"]:
        t.join(5)
        assert not t.is_alive()


def fast_run(runs, name="fast-run", locked=(1,), waiting_for=2):
    """A fast run's folder part-way: the outline, the sections locked so far, and the page waiting."""
    root = runs / name
    write(root / "fast.json", {"mode": "fast", "seed_set": 0})
    write(root / "05_outline.json", {
        "title": "Concrete That Heals", "through_line": "A material that was meant to change.",
        "sections": [{"n": n, "title": TITLES[n], "lands": f"Lands {n}: the secret of part {n}."} for n in TITLES]})
    for n in locked:
        write(root / "06_segments" / f"seg{n:02d}.md", LOCKED[n])
    set_status(root, {n: 2 for n in locked}, waiting_for)
    return root


@pytest.fixture
def world(tmp_path, cfg, monkeypatch):
    cfg["listener_dir"] = str(tmp_path / "listener")
    urls = []
    monkeypatch.setattr(voice, "signed_url", lambda cfg, client=None, kind="guide": urls.append(kind) or f"wss://example/{kind}")
    monkeypatch.setattr(ask_routes, "_save_cost", lambda *a, **k: None)
    passed, done = [], threading.Event()
    monkeypatch.setattr(memory, "after_session", lambda cfg, path: (passed.append(path), done.set()))
    runs = tmp_path / "runs"
    root = fast_run(runs)
    app = FastAPI()
    ask_routes.mount(app, cfg, runs)

    class W:
        pass
    w = W()
    w.c, w.root, w.runs, w.cfg, w.urls, w.passed, w.done, w.tmp = TestClient(app), root, runs, cfg, urls, passed, done, tmp_path
    w.start = lambda **body: w.c.post("/api/ask/start", json={"run": root.name, "t": 120.0, "kind": "check_in", **body})
    w.tool = lambda aid, name, **args: w.c.post("/api/ask/tool", json={"aside": aid, "name": name, "args": args}).json()
    w.line = lambda aid, who, text: w.c.post("/api/ask/line", json={"aside": aid, "who": who, "text": text})
    w.end = lambda aid, how="guide": w.c.post("/api/ask/end", json={"aside": aid, "how": how}).json()
    yield w
    join_passes()  # before the patches are undone


# --- the third agent --------------------------------------------------------------------------

def head_digest(cfg, kind):
    """voice._digest for the two agents that existed before the check-in, written out as the code
    stood then (stages/voice.py at 4d13961, plus the quiet guide's turn_timeout from the default branch,
    15d0413), so a branch added for the check-in cannot leak into them."""
    v = cfg["voice"]
    conv = {"agent": {"first_message": "", "language": "en",
                      "dynamic_variables": {"dynamic_variable_placeholders": {
                          k: f"({k})" for k in voice.KINDS[kind]["placeholders"]}},
                      "prompt": {"prompt": voice.guide_prompt(kind), "llm": v["llm"],
                                 "reasoning_effort": v["reasoning_effort"], "tool_ids": []}},
            "tts": {"model_id": v["tts_model"], "voice_id": v.get("voice_id") or cfg["narration"]["voice_id"]},
            "turn": {"turn_eagerness": v["turn_eagerness"], "turn_timeout": v["turn_timeout"]},
            "conversation": {"max_duration_seconds": v["max_duration_seconds"]}}
    if kind == "aside":
        conv["turn"]["turn_timeout"] = cfg["ask"]["turn_timeout_secs"]
        conv["agent"]["prompt"]["reasoning_effort"] = cfg["ask"].get("reasoning_effort") or v["reasoning_effort"]
    name = v["agent_name"] if kind == "guide" else cfg["ask"]["agent_name"]
    blob = json.dumps({"tools": voice.client_tools(cfg, kind), "conversation": conv,
                       "platform": voice.platform_settings(), "name": name}, sort_keys=True)
    return voice.hashlib.sha256(blob.encode()).hexdigest()[:16]


def test_three_agents_with_their_own_names_and_state_and_the_old_two_unchanged(cfg, tmp_path):
    cfg["listener_dir"] = str(tmp_path / "listener")
    kinds = ("guide", "aside", "checkin")
    assert tuple(voice.KINDS) == kinds
    assert len({voice.agent_name(cfg, k) for k in kinds}) == 3 and len({voice.state_path(cfg, k) for k in kinds}) == 3
    assert voice.agent_name(cfg, "checkin") == cfg["fast"]["checkin"]["agent_name"]
    assert voice.state_path(cfg, "checkin").name == "voice_checkin.json"
    for kind in ("guide", "aside"):  # adding the check-in changed neither agent: no re-sync on Ian's account
        assert voice._digest(cfg, kind) == head_digest(cfg, kind)
    # The aside agent's digest with the real config, as it was before the check-in was added (4d13961).
    # A change to prompts/guide_aside.md, its tools or config.yaml's ask and voice sections changes it on
    # purpose: update the value then.
    assert voice._digest(load_config(), "aside") == "0dba9c8336fdcf68"
    assert len({voice._digest(cfg, k) for k in kinds}) == 3


def test_the_check_in_agent_is_the_prompt_its_tools_and_fast_checkin(cfg):
    prompt = voice.guide_prompt("checkin")
    assert prompt.startswith("You are a guide. You make chapters")  # the same guide as the other two
    assert set(re.findall(r"\{\{\s*(\w+)\s*\}\}", prompt)) == set(voice.CHECKIN_PLACEHOLDERS)
    assert voice.KINDS["checkin"]["placeholders"] is voice.CHECKIN_PLACEHOLDERS
    assert "—" not in prompt and "–" not in prompt  # no dashes for a voice to trip on
    tools = voice.client_tools(cfg, "checkin")
    assert [t["name"] for t in tools] == ["add_note", "get_status", "resume_chapter"]
    assert all(t["type"] == "client" and t["expects_response"] for t in tools)
    note = tools[0]["parameters"]
    assert note["required"] == ["note", "kind"] and note["properties"]["kind"]["enum"] == list(notes.KINDS)
    assert tools[2]["parameters"]["required"] == ["reason"]
    assert "additionalProperties" not in json.dumps(tools)
    conv = voice.conversation_config(cfg, ["a", "b", "c"], "checkin")
    assert conv["turn"]["turn_timeout"] == cfg["fast"]["checkin"]["turn_timeout_secs"]
    assert conv["agent"]["prompt"]["reasoning_effort"] == cfg["fast"]["checkin"]["reasoning_effort"]
    assert conv["agent"]["first_message"] == ""  # the page's note makes it speak first, in its own words
    assert set(conv["agent"]["dynamic_variables"]["dynamic_variable_placeholders"]) == set(voice.CHECKIN_PLACEHOLDERS)


def test_page_notes_and_settings(cfg):
    n = checkin.page_notes()
    assert set(n) == {"opened", "reopened", "silence", "ready", "tap"}
    assert all(v.startswith("[") and v.endswith("]") for v in n.values())
    assert "resume_chapter" in n["ready"]
    s = checkin.page_settings(cfg)
    assert s == {"silence_seconds": cfg["fast"]["silence_seconds"], "gap_mic": cfg["fast"]["gap_mic"],
                 "tap_wait_seconds": cfg["fast"]["page"]["tap_wait_seconds"],
                 "quiet_seconds": cfg["fast"]["page"]["quiet_seconds"]}


# --- what the guide is told -------------------------------------------------------------------

def test_heard_is_the_locked_text_before_the_awaited_section_and_nothing_to_come(world, cfg):
    root = world.root
    write(root / "06_segments" / "seg02.md", LOCKED[2])  # locked and being narrated, but not heard
    write(root / "06_segments" / "seg03.md", LOCKED[3])
    c = checkin.context(root, cfg, 2, 130.0)
    assert set(c) == set(voice.CHECKIN_PLACEHOLDERS) and all(isinstance(v, str) for v in c.values())
    assert c["heard"] == f"(Part 1: {TITLES[1]})\n\n" + "\n\n".join(HEARD_1)  # markers, [Dn], [pause], ** all gone
    for coming in ("lime and ash", "Pliny", "crystals"):
        assert coming not in c["heard"]
    # what is coming: a title and a line each, the awaited one first
    assert c["coming"].splitlines() == [f"Part 2, being finished now: {TITLES[2]}. Lands 2: the secret of part 2.",
                                        f"Part 3: {TITLES[3]}. Lands 3: the secret of part 3."]
    assert "Lands 1" not in c["coming"]
    assert c["chapter"].splitlines() == ["Title: Concrete That Heals", "Through-line: A material that was meant to change.",
                                         "Where they are: waiting for part 2 of 3, after about 2 minutes of listening."]
    assert c["notes"] == "(none yet)" and c["earlier_asides"] == "(none)"
    assert c["earlier_openings"].startswith("(none") and c["mic"] == "open"
    assert c["identity"] == checkin.NO_NAME and "no profile yet" in c["profile"]
    # two sections heard: both, in order
    both = checkin.context(root, cfg, 3)["heard"]
    assert both.index("Roman harbours") < both.index(f"(Part 2: {TITLES[2]})") < both.index("Pliny")
    assert "crystals" not in both and checkin.context(root, cfg, 3)["coming"].startswith("Part 3, being finished now")


def test_context_takes_the_lock_s_title_the_notes_the_profile_and_the_mic(world, cfg):
    root = world.root
    write(root / "fast" / "locks" / "seg01.json", {"title": "The wall that *mends* itself"})
    notes.add(root, "The wall getting stronger surprised me.", "landed", section=1)
    write(world.tmp / "listener" / "profile.md", "Pours concrete for a living.\n")
    write(world.tmp / "listener" / "guide.md", "You are Wren.\n")
    cfg["fast"]["gap_mic"] = "tap"
    c = checkin.context(root, cfg, 2)
    assert c["heard"].startswith("(Part 1: The wall that mends itself)")  # the asterisks a voice would read are gone
    assert c["notes"] == "- (landed, during section 1) The wall getting stronger surprised me."
    assert c["profile"] == "Pours concrete for a living." and c["identity"] == "You are Wren."
    assert c["mic"] == "closed until the listener taps the circle"
    assert "minutes of listening" not in c["chapter"]  # no time given: none said


def test_a_skip_two_minutes_in_needs_no_timing_and_says_they_skipped(world, cfg):
    root = world.root
    assert not (root / "11_episode").exists() and not (root / "09_script_final.md").exists()
    write(root / "fast" / "player.json", {"at": 1.0, "state": "waiting", "section": 2, "piece": 0, "t": 0,
                                          "chapter_t": 120.0, "waiting_for": 2, "since": 1.0, "reason": "skip"})
    c = checkin.context(root, cfg, 2, 120.0)
    assert c["heard"].startswith("(The listener skipped ahead to get here")
    assert all(line in c["heard"] for line in HEARD_1)
    assert checkin.listener_at(root, 2) == {"section": 1, "pieces": 0, "skipped": True}
    # and a run with nothing locked at all still answers
    bare = fast_run(world.runs, "bare", locked=())
    assert checkin.context(bare, cfg, 1)["heard"].startswith("(nothing yet")


def test_running_dry_inside_a_section_counts_the_pieces_already_heard(world, cfg):
    root = world.root
    write(root / "06_segments" / "seg02.md", LOCKED[2])
    add_piece(root, 2, 0, "The recipe was lime and ash.")
    add_piece(root, 2, 1, "Pliny wrote that the waves made it one stone.")  # narrated, not yet heard
    write(root / "fast" / "player.json", {"at": 1.0, "state": "waiting", "section": 2, "piece": 1, "t": 20.0,
                                          "chapter_t": 60.0, "waiting_for": 2, "since": 1.0, "reason": "end_of_audio"})
    at = checkin.listener_at(root, 2)
    assert at == {"section": 2, "pieces": 1, "skipped": False}
    c = checkin.context(root, cfg, 2)
    assert c["heard"].endswith(f"(Part 2: {TITLES[2]}, as far as it has played)\n\nThe recipe was lime and ash.")
    assert "Pliny" not in c["heard"] and c["coming"].startswith("Part 2, the rest of it, being finished now")
    assert checkin.anchor(root, 2, 1) == "seg02-s002" and checkin.anchor(root, 2) == "seg02-s001"
    # ready means audio they have not heard: a second piece, not the one they ran out of
    set_status(root, {1: 2, 2: 1}, waiting_for=2)
    assert not checkin.ready(root, 2, have=1)
    set_status(root, {1: 2, 2: 2}, waiting_for=2)
    assert checkin.ready(root, 2, have=1)


def test_a_runaway_heard_is_cut_from_the_start_and_says_so(world, cfg, monkeypatch):
    monkeypatch.setattr(checkin, "HEARD_MAX_CHARS", 60)
    heard = checkin.context(world.root, cfg, 2)["heard"]
    assert heard.startswith("(The start of the chapter is left out here for length.")
    assert heard.endswith(HEARD_1[1]) and "Roman harbours" not in heard


# --- the routes (check 5, the server's half) --------------------------------------------------

def test_a_check_in_starts_with_the_context_and_the_notes_and_needs_no_timing(world, cfg):
    res = world.start(section=2)
    assert res.status_code == 200
    res = res.json()
    assert world.urls == ["checkin"] and res["signed_url"] == "wss://example/checkin"
    assert set(res) == {"aside", "kind", "section", "t", "sentence_id", "signed_url", "dynamic_variables", "notes",
                        "settings"}
    assert (res["kind"], res["section"], res["t"], res["sentence_id"]) == ("check_in", 2, 120.0, "seg02-s001")
    assert res["dynamic_variables"] == checkin.context(world.root, cfg, 2, 120.0)
    assert res["notes"] == checkin.page_notes() and res["settings"] == checkin.page_settings(cfg)
    # the awaited section defaults to the one the run says the listener is waiting for
    assert world.start().json()["section"] == 2
    # nothing to wait for, a run that is not fast, an unknown run, no voice
    assert world.start(section=9).status_code == 400
    F.build(world.runs / "overnight")
    assert world.c.post("/api/ask/start", json={"run": "overnight", "t": 1, "kind": "check_in", "section": 2}).status_code == 400
    assert world.c.post("/api/ask/start", json={"run": "nope", "t": 1, "kind": "check_in"}).status_code == 404


def test_voice_unavailable_at_a_check_in_says_so(world, monkeypatch):
    def down(cfg, client=None, kind="guide"):
        raise RuntimeError("no key")
    monkeypatch.setattr(voice, "signed_url", down)
    res = world.start(section=2)
    assert res.status_code == 503 and "Voice isn't available" in res.json()["detail"]


def test_add_note_writes_listener_notes_with_kind_and_section(world):
    aid = world.start(section=2).json()["aside"]
    out = world.tool(aid, "add_note", note="The wall  getting stronger\nsurprised me.", kind="landed")
    assert out["added"] is True and out["kind"] == "landed"
    world.tool(aid, "add_note", note="My dad poured our driveway.", kind="connection")
    assert world.tool(aid, "add_note", note="  ", kind="landed") == {"added": False, "reason": "The note was empty."}
    got = notes.load(world.root)
    assert [(n["section"], n["kind"], n["text"]) for n in got] == [
        (1, "landed", "The wall getting stronger surprised me."), (1, "connection", "My dad poured our driveway.")]
    assert "(section 1, landed)" in (world.root / "listener_notes.md").read_text()
    assert world.tool(aid, "save_seed", topic="x", why="y") == {"error": "No tool named save_seed."}


def test_resume_chapter_is_refused_until_the_awaited_section_has_audio(world):
    aid = world.start(section=2).json()["aside"]
    assert world.tool(aid, "resume_chapter", reason="listener") == checkin.NOT_READY
    status = world.tool(aid, "get_status")
    assert status["next_part_ready"] is False and status["waiting_for"] == TITLES[2]
    assert status["expected_in_seconds"] == 40 and status["sections"][1] == {"title": TITLES[2], "state": "being written"}
    # a refused call ends nothing: the check-in closed by the page now is not logged as resumed
    assert world.end(aid, "guide") == {"restart": None, "ended": "other", "seconds": pytest.approx(0, abs=5)}

    aid = world.start(section=2).json()["aside"]
    set_status(world.root, {1: 2, 2: 1}, waiting_for=2)  # the first piece of section 2 lands
    assert world.tool(aid, "get_status")["next_part_ready"] is True
    out = world.tool(aid, "resume_chapter", reason="ready")
    assert out == checkin.RESUMED and out["resuming"] is True and "lead-in" in out["now"]
    end = world.end(aid, "guide")
    assert end["restart"] is None and end["ended"] == "resumed"
    assert world.c.post("/api/ask/end", json={"aside": aid, "how": "guide"}).status_code == 409  # once


def test_a_check_in_is_logged_with_its_kind_and_section_where_the_next_section_starts(world):
    aid = world.start(section=2, reopened=True).json()["aside"]
    world.line(aid, "guide", "The next part's still coming together. What do you make of it so far?")
    world.line(aid, "listener", "...")
    world.line(aid, "page", checkin.page_notes()["silence"])
    world.line(aid, "guide", "No rush.")
    assert world.end(aid, "silence")["ended"] == "silence"
    log = A.load_asides(world.root)
    assert len(log) == 1
    rec = log[0]
    assert (rec["kind"], rec["section"], rec["segment"], rec["sentence_id"]) == ("check_in", 2, 2, "seg02-s001")
    assert rec["restart"] is None and rec["reopened"] is True and rec["t"] == 120.0 and rec["ended"] == "silence"
    assert [x["who"] for x in rec["transcript"]] == ["guide", "listener", "page", "guide"]
    assert world.passed == [] and not (world.tmp / "listener" / "sessions").exists()  # they said nothing: no pass
    # an unknown ending from the page, and one left open when the next begins
    aid = world.start(section=2).json()["aside"]
    world.end(aid, "nonsense")
    first = world.start(section=2).json()["aside"]
    world.start(section=2)
    assert [(x["id"], x["ended"]) for x in A.load_asides(world.root)][1:] == [(aid, "other"), (first, "dropped")]


def test_the_next_check_in_knows_how_the_last_one_opened(world, cfg):
    opening = "The next part's still coming together. What do you make of it so far?"
    aid = world.start(section=2).json()["aside"]
    world.line(aid, "guide", opening)
    world.line(aid, "listener", "I liked the harbour.")
    world.line(aid, "guide", "It is a good one.")
    world.end(aid, "button")
    A.append_aside(world.root, {"id": "q1", "kind": "ask", "t": 30.0, "sentence_id": "seg01-s002", "ended": "closing",
                                "transcript": [{"who": "guide", "text": "Yes?"}, {"who": "listener", "text": "Who built it?"}]})
    c = world.start(section=2).json()["dynamic_variables"]
    assert c["earlier_openings"] == f'- "{opening}"'  # the check-in's first line; not the aside's "Yes?"
    lines = c["earlier_asides"].splitlines()
    assert lines[0].startswith("Check-in. At 2:00 (seg02-s001): you opened with") and "I liked the harbour." in lines[0]
    assert lines[1].startswith("Question. At 0:30") and "Who built it?" in lines[1]


def test_what_was_said_at_a_check_in_goes_to_the_memory_pass_once(world):
    aid = world.start(section=2).json()["aside"]
    world.line(aid, "guide", "What do you make of it so far?")
    world.line(aid, "listener", "My dad poured our driveway, so this is close to home.")
    world.tool(aid, "add_note", note="My dad poured our driveway.", kind="connection")
    world.end(aid, "button")
    assert world.done.wait(5)
    path = world.tmp / "listener" / "sessions" / f"{aid}-checkin.jsonl"
    assert world.passed == [path]
    assert memory.had_listener(path) and memory.session_date(path) == f"{aid[:4]}-{aid[4:6]}-{aid[6:8]}"
    text = memory.transcript_text(path)
    assert text.splitlines()[0].startswith('page: [A check-in while the listener heard the chapter "Concrete That Heals"')
    assert "listener: My dad poured our driveway, so this is close to home." in text
    assert 'tool add_note: {"note": "My dad poured our driveway.", "kind": "connection"}' in text
    assert world.c.post("/api/ask/end", json={"aside": aid, "how": "button"}).status_code == 409
    assert world.passed == [path]  # once


def test_a_check_in_never_goes_ahead_of_an_earlier_conversation_still_waiting(world, cfg, monkeypatch):
    """stages.memory skips a conversation older than one already remembered: a check-in's pass that
    ran first would cost an onboarding conversation still open (a typed chat) its place in the profile."""
    sessions = world.tmp / "listener" / "sessions"
    write(sessions / "20261112-090000.jsonl", json.dumps({"who": "listener", "text": "I pour concrete."}) + "\n")
    ran = []
    monkeypatch.setattr(memory, "after_session", lambda cfg, path: ran.append(path))
    # The hand-off now waits for the earlier conversation (the test below); shortened here to its give-up.
    monkeypatch.setattr(checkin, "WAIT_POLL_SECONDS", 0.01)
    monkeypatch.setattr(checkin, "WAIT_MAX_SECONDS", 0.05)
    a = {"id": "20261113-101500-ab12", "run": world.root, "section": 2, "started": "2026-11-13T10:15:00",
         "lines": [{"s": 1.0, "who": "listener", "text": "It surprised me."}]}
    path = checkin.remember(cfg, a, "Concrete That Heals")
    join_passes()  # its own thread found the earlier conversation waiting, waited, and left it
    assert ran == []
    assert path.name == "20261113-101500-ab12-checkin.jsonl" and path.stem > "20261113-101500"  # sorts by when it began
    checkin._pass(cfg, path)
    assert ran == []                                       # left for the catch-up, which takes both in order
    assert [p.name for p in memory.pending_sessions(cfg)] == ["20261112-090000.jsonl", path.name]
    (sessions / "20261112-090000.done").write_text("done\n")
    checkin._pass(cfg, path)
    assert ran == [path]
    assert checkin.remember(cfg, {**a, "lines": [{"who": "listener", "text": "..."}]}, "x") is None
    join_passes()


# --- asking mid-chapter while a fast run is still being made --------------------------------------

@pytest.fixture
def asked(world):
    """The synthetic timed chapter (three sections narrated and timed), marked as a fast run still
    being made, with a fourth section's first two pieces playable."""
    root = F.build(world.runs / "synthetic")
    write(root / "fast.json", {"mode": "fast", "seed_set": 0})
    return root


def ask(world, t, *said, how="button"):
    res = world.c.post("/api/ask/start", json={"run": "synthetic", "t": t}).json()
    world.line(res["aside"], "guide", "Yes?")
    for text in said:
        world.line(res["aside"], "listener", text)
    return res, world.end(res["aside"], how)


def test_a_question_asked_mid_chapter_becomes_a_question_note(world, asked, cfg):
    ch = A.Chapter(asked, cfg)
    t = ch.segments[1]["start"] + 3.0  # in section 2
    res, end = ask(world, t, "Who wrote this?", "Oh, OK.", "And when was that?")
    assert world.urls == ["aside"] and end["restart"]["rule"] == "sentence"
    got = notes.load(asked)
    assert [(n["section"], n["kind"], n["text"]) for n in got] == [(2, "question", "Who wrote this?"),
                                                                   (2, "question", "And when was that?")]
    log = A.load_asides(asked)
    assert log[0]["kind"] == "ask" and log[0]["segment"] == 2 and "section" not in log[0]
    join_passes()  # a question goes to the profile too (review fix; it was the check-in's alone)
    assert [p.name for p in world.passed] == [f"{res['aside']}-ask.jsonl"]
    # once the chapter is archived, or on a run that was never fast, nothing is written
    write(asked / "fast" / "archive.json", {})
    ask(world, t, "One more?")
    assert len(notes.load(asked)) == 2
    plain = F.build(world.runs / "overnight")
    res = world.c.post("/api/ask/start", json={"run": "overnight", "t": 5}).json()
    world.line(res["aside"], "listener", "Who wrote this?")
    world.end(res["aside"], "button")
    assert not (plain / "listener_notes.md").exists() and A.load_asides(plain)[0]["kind"] == "ask"


def test_an_ask_past_the_rolling_timing_is_answered_from_the_pieces_and_picks_up_in_place(world, asked, cfg):
    """Regression: the listener is in a section whose narration is still running, so the timed
    transcript ends before them. Clamped, the aside restarted at the end of the section before."""
    ch = A.Chapter(asked, cfg)
    write(asked / "06_segments" / "seg04.md", "<!-- seg04 -->\n\nOne two three. Four five six.\n\nSeven eight nine. Ten.")
    add_piece(asked, 4, 0, "One two three.", 10.0)
    add_piece(asked, 4, 1, "Four five six.", 10.0)
    add_piece(asked, 4, 2, "Seven eight nine.", 10.0)
    t = ch.duration + 15.0  # in the second piece of section 4
    res, end = ask(world, t, "What was that?")
    assert res["t"] == t and res["sentence_id"] == "seg04-s003"  # shown after the two pieces heard
    v = res["dynamic_variables"]
    assert set(v) == set(voice.ASIDE_PLACEHOLDERS)
    assert v["just_said"] == "One two three.\n\nFour five six." and v["coming"] == "Seven eight nine."
    assert v["where"].startswith('Segment 4, "part 4"') and "still being recorded" in v["where"]
    assert "chapter has ended" not in v["coming"] and v["later"].startswith("(nothing after that")
    assert end["restart"] == {"t": round(t, 3), "sentence_id": "seg04-s003", "rule": "position"}
    rec = A.load_asides(asked)[0]
    assert rec["segment"] == 4 and rec["t"] == round(t, 3)
    assert [(n["section"], n["text"]) for n in notes.load(asked)] == [(4, "What was that?")]
    # inside the timing nothing changes, and a finished chapter clamps as it always did
    inside = world.c.post("/api/ask/start", json={"run": "synthetic", "t": 20.0}).json()
    assert inside["t"] == 20.0 and inside["dynamic_variables"] == ch.context(20.0, A.load_asides(asked))
    write(asked / "fast" / "archive.json", {})
    late = world.c.post("/api/ask/start", json={"run": "synthetic", "t": t}).json()
    assert late["t"] == ch.duration and "chapter has ended" in late["dynamic_variables"]["coming"]


def test_a_fast_run_being_made_can_be_asked_about_once_it_is_timed(world, asked):
    making = {"run": "synthetic", "state": "making", "mode": "fast"}
    assert ask_routes.with_extras(making, world.runs) == {**making, "asks": True}
    assert ask_routes.with_extras({**making, "run": "fast-run"}, world.runs)["asks"] is False  # no timing yet
    overnight = {"run": "synthetic", "state": "making"}
    assert ask_routes.with_extras(overnight, world.runs) == overnight  # an overnight run being made: as before
    ready = ask_routes.with_extras({"run": "synthetic", "state": "ready", "mode": "fast"}, world.runs)
    assert ready["asks"] is True and ready["episode"] is False
    assert ask_routes.with_extras({"state": "idle"}, world.runs) == {"state": "idle"}


# --- review fixes -------------------------------------------------------------------------------

def cut_timing(root, keep=1):
    """The synthetic chapter as a fast run part-way: only the first `keep` sections narrated and
    timed (11_episode/timing.json), while 05_brief.md lists every section, as archive.write_brief does."""
    p = root / "11_episode" / "timing.json"
    t = json.loads(p.read_text())
    t["segments"] = t["segments"][:keep]
    t["duration"] = round(t["segments"][-1]["end"] + 1.0, 3)
    p.write_text(json.dumps(t))


def test_an_ask_inside_the_timing_of_an_unfinished_fast_run_is_told_more_is_coming(world, asked, cfg):
    """Regression: the timing holds only the sections narrated so far, so the aside guide was told
    this was the last segment of a short chapter, with nothing after it."""
    cut_timing(asked)
    ch = A.Chapter(asked, cfg)
    for t in (20.0, ch.duration - 2.0):  # early in section 1, and near the end of what is timed
        v = world.c.post("/api/ask/start", json={"run": "synthetic", "t": t}).json()["dynamic_variables"]
        assert v["where"].startswith('Segment 1 of 3, "Part 1"') and "last segment" not in v["where"]
        assert "still being made" in v["where"] and f"{ch.duration / 60:.1f}-minute" not in v["where"]
        assert "segments 2 to 3 are still to come" in v["where"]
        assert v["later"].splitlines()[-2:] == ["Segment 2: Part 2. Say segment 2's words.",
                                                "Segment 3: Part 3. Say segment 3's words."]
        assert "chapter ends" not in v["later"] and "chapter has ended" not in v["coming"]
        assert v["chapter"].splitlines()[-1] == "Segment 3: Part 3. Say segment 3's words."
    # a finished chapter, or one whose brief has nothing beyond the timing, is told as before
    whole = A.Chapter(asked, cfg)
    write(asked / "fast" / "archive.json", {})
    v = world.c.post("/api/ask/start", json={"run": "synthetic", "t": 20.0}).json()["dynamic_variables"]
    assert v == whole.context(20.0, A.load_asides(asked)) and "last segment" in v["where"]


def test_never_mind_and_a_few_words_that_are_not_a_question_leave_no_note(world, asked, cfg):
    """Regression: the first thing said in an aside was always saved as a question, even 'Never mind.'"""
    ch = A.Chapter(asked, cfg)
    t = ch.segments[1]["start"] + 3.0
    ask(world, t, "Never mind.")
    ask(world, t, "I love this part")
    res = world.c.post("/api/ask/start", json={"run": "synthetic", "t": t}).json()
    world.line(res["aside"], "listener", "Who built the harbour at Caesarea?")
    world.tool(res["aside"], "resume_chapter", reason="never_mind")
    world.end(res["aside"], "guide")  # the guide went back because they said never mind
    assert notes.load(asked) == []
    ask(world, t, "Tell me more about how the Romans mixed it")  # a question without a question mark
    assert [n["text"] for n in notes.load(asked)] == ["Tell me more about how the Romans mixed it"]


def test_a_question_asked_mid_chapter_on_a_fast_run_also_goes_to_the_profile(world, asked, cfg):
    """Spec: notes also go to the profile. A mid-chapter question is written as a session transcript
    and passed to the memory pass, as a check-in is; one that left no note is not."""
    ch = A.Chapter(asked, cfg)
    t = ch.segments[1]["start"] + 3.0
    ask(world, t, "Never mind.")
    join_passes()
    assert world.passed == []
    res, _ = ask(world, t, "I've poured concrete for thirty years, why did nobody tell us this?")
    assert world.done.wait(5)
    join_passes()
    path = world.tmp / "listener" / "sessions" / f"{res['aside']}-ask.jsonl"
    assert world.passed == [path]
    text = memory.transcript_text(path)
    assert text.splitlines()[0].startswith('page: [A question the listener asked mid-chapter, in "A Synthetic Chapter"')
    assert "listener: I've poured concrete for thirty years" in text


def test_a_deferred_check_in_is_passed_once_the_earlier_conversation_is_remembered(world, cfg, monkeypatch):
    """Regression: a check-in whose pass found an earlier conversation still open (a typed chat) was
    left for the next server start, and was skipped for good if a newer conversation was remembered
    first. Its thread now waits, and passes it as soon as nothing earlier is pending."""
    monkeypatch.setattr(checkin, "WAIT_POLL_SECONDS", 0.05)
    sessions = world.tmp / "listener" / "sessions"
    write(sessions / "20261112-090000.jsonl", json.dumps({"who": "listener", "text": "I pour concrete."}) + "\n")
    ran = []
    monkeypatch.setattr(memory, "after_session", lambda cfg, path: ran.append(path))
    a = {"id": "20261113-101500-ab12", "run": world.root, "section": 2, "started": "2026-11-13T10:15:00",
         "lines": [{"s": 1.0, "who": "listener", "text": "My dad poured our driveway."}]}
    path = checkin.remember(cfg, a, "Concrete That Heals")
    threading.Event().wait(0.3)
    assert ran == []  # the open chat goes first
    (sessions / "20261112-090000.done").write_text("done\n")  # the chat ends and is remembered
    join_passes()
    assert ran == [path]
    # it gives up after WAIT_MAX_SECONDS (the catch-up at the next start takes it then), and never
    # passes a transcript something else has passed meanwhile
    monkeypatch.setattr(checkin, "WAIT_MAX_SECONDS", 0.1)
    write(sessions / "20261112-080000.jsonl", json.dumps({"who": "listener", "text": "Hi."}) + "\n")
    checkin._pass(cfg, path)
    assert ran == [path]


def test_the_check_in_prompt_gives_no_time_of_its_own():
    """Regression: it told the guide to say the next part was 'nearly there' and framed the wait as a
    minute or so, times no tool had given."""
    prompt = voice.guide_prompt("checkin")
    for said in ("nearly there", "minute or so", "almost ready", "any moment"):
        assert said not in prompt
    assert "say the next part isn't ready yet" in prompt and "call get_status" in prompt
