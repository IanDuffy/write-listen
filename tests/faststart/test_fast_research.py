"""Research in a fast run (faststart/research.py): the shared search counter, one agent per row filed
as it lands, thin rows flagged, the extra agent a listener's note may start (with its own searches), the
entries that answer a note, and the fast row prompt."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fakes import PAGE_TEXT, FakeAnthropic, all_text
from fast_research_fakes import (NOTE_PHRASE, PAGE_URL, ResearchFake, file_row, findings, ledger_for, note_reply,
                                 outline, row_message, scout_row)

from faststart import base, notes
from faststart import research as fast_research
from faststart.dossier import Dossier
from faststart.research import Budget, note_row, run_row
from stages import common, ledger, llm
from stages import research as stage_research
from stages.common import dossier_entries, entry_tags

SCOUT_TEXT = "## Territory\nThe Symposium.\n\n## Scenes\n1. The dinner party at Agathon's house (D02, D05)."


@pytest.fixture
def fast(run, cfg, monkeypatch, tmp_path):
    """A fast run as the driver leaves it when research starts: a seed, unattended with a cap, the
    outline's ledger on disk, the scout's row in the dossier, and a fake model that answers only
    this area's prompts. Returns a namespace of the pieces."""
    import anthropic

    def live(*a, **k):
        raise AssertionError("a test reached for a live Anthropic client")

    monkeypatch.setattr(anthropic, "Anthropic", live)
    run.path("source_dir").mkdir()
    run.path("seed").write_text("# Seed\n\n## Topic\nCan beauty make us good?\n", encoding="utf-8")
    cfg["listener_dir"] = str(tmp_path / "listener")
    cfg = common.run_config(run, cfg)
    run.unattended, run.max_usd = True, 8.0
    base.mark_fast(run)
    o = outline()
    led = ledger_for(o)
    ledger.save(run, led)
    scout_row(run)
    d = Dossier(run, cfg)
    d.add_row("L00")
    fake = ResearchFake()
    monkeypatch.setattr(llm, "_client", FakeAnthropic(fake))

    class Fast:
        pass
    f = Fast()
    f.r, f.cfg, f.outline, f.led, f.dossier, f.fake = run, cfg, o, led, d, fake
    f.budget = Budget.for_run(run, cfg)
    f.row = lambda rid: ledger.by_id(f.led)[rid]
    f.go = lambda rid, **kw: run_row(run, cfg, kw.get("row") or f.row(rid), f.led, o, SCOUT_TEXT,
                                     kw.get("dossier") or d, kw.get("budget") or f.budget)
    f.disk = lambda: json.loads(run.path("ledger").read_text(encoding="utf-8"))
    return f


# --- the shared search counter ---------------------------------------------------------------------

def test_budget_is_thirty_in_all_five_a_row_and_the_seventh_row_gets_nothing(cfg):
    per = cfg["fast"]["research"]["per_row"]["searches"]
    b = Budget(cfg["fast"]["research"]["max_searches"])
    assert (b.total, per) == (30, 5)
    assert [b.reserve(per) for _ in range(6)] == [5] * 6
    assert b.reserve(per) == 0 and b.left == 0            # the seventh row
    assert b.settle(5, 3) == 2 and b.left == 2            # a row that used 3 of its 5 hands 2 back
    assert b.reserve(per) == 2 and b.reserve(1) == 0      # and a later row gets those, no more
    assert b.settle(2, 2) == 0 and b.settle(2, 9) == 0 and b.left == 0   # nothing invented on the way back
    assert b.settle(5, 0) == 5 and b.settle(5, 0) == 5 and b.left == 10
    assert Budget(30, used=28).reserve(5) == 2 and Budget(30, used=40).reserve(5) == 0


def test_budget_holds_under_threads():
    b = Budget(30)
    with ThreadPoolExecutor(max_workers=16) as pool:
        granted = list(pool.map(lambda _: b.reserve(1), range(200)))
    assert sum(granted) == 30 and b.left == 0
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda g: b.settle(g, 0), granted))
    assert b.left == 30


def test_a_continued_run_does_not_get_the_searches_again(fast):
    fast.r.log({"stage": "research_row", "row": "L01", "web_searches": 4})
    fast.r.log({"stage": "fast_scout", "web_searches": 6})          # the scout has its own budget
    assert Budget.for_run(fast.r, fast.cfg).left == 26


# --- one row ---------------------------------------------------------------------------------------

def test_run_row_researches_files_and_reports(fast):
    out = fast.go("L01")
    assert out["row"] == "L01" and out["error"] is None and out["thin"] is None
    assert out["numbers"] == [6, 7, 8, 9, 10, 11] == fast.dossier.numbers_of("L01")   # after the scout's five
    assert out["searches"] == 3 and out["cost"] > 0 and out["seconds"] >= 0
    assert set(out) == {"row", "numbers", "thin", "error", "searches", "seconds", "cost"}
    # The budget went to the web tools as max_uses, and what was not used came back.
    (call,) = fast.fake.agent_calls("L01")
    tools = {t["name"]: t["max_uses"] for t in call["tools"]}
    assert tools == {"web_search": 5, "web_fetch": 2}
    assert fast.budget.left == 27
    # The agent is the overnight one, told about this chapter: angle, through-line, its section, the scout.
    sent = all_text(call)
    assert "You are a research agent for one idea" in sent and "Your budget is 5 searches and 2 page fetches" in sent
    assert "Beauty is a ladder you climb away from yourself." in sent
    assert "Through-line: Can looking at beauty change who you are?" in sent
    assert 'This idea belongs to section 2 of 4, "Rung 2".' in sent and "Tell rung 2 through one scene." in sent
    assert "The dinner party at Agathon's house (D02, D05)" in sent and "podcast" not in sent.lower()
    assert "- L02: Rung 3 turns the lover" in sent
    rec = [x for x in fast.r.read_log() if x.get("row") == "L01"]
    assert [x["stage"] for x in rec] == ["research_row"] and rec[0]["web_searches"] == 3
    # Filed at once, checked, in the dossier file ask-by-voice reads.
    e = dossier_entries(fast.r.path("dossier").read_text(encoding="utf-8"))
    assert entry_tags(e[8])["verbatim"] == "yes" and entry_tags(e[9])["verbatim"] == "no"
    assert "compare D06 and D08" in e[10]
    # The ledger shows where the row stands, on disk and in the caller's copy.
    for row in (ledger.by_id(fast.disk())["L01"], fast.row("L01")):
        assert row["status"] == "filled" and row["gap"] == "" and row["best_tier"] == 1
        assert row["dossier_refs"] == ["D06", "D07", "D08", "D09", "D10", "D11"] and row["section"] == 2
    assert ledger.by_id(fast.disk())["L02"]["status"] is None
    assert "L01" in fast.r.path("ledger_md").read_text(encoding="utf-8")
    assert not fast.r.path("decisions").exists()            # nothing to decide about a row that landed well


def test_a_row_gets_only_the_searches_that_are_left(fast):
    b = Budget(30, used=28)
    out = fast.go("L02", budget=b)
    (call,) = fast.fake.agent_calls("L02")
    assert {t["name"]: t["max_uses"] for t in call["tools"]} == {"web_search": 2, "web_fetch": 2}
    assert "Your budget is 2 searches" in all_text(call)
    assert out["error"] is None and b.left == 0             # the fake used 3; nothing is handed back


def test_the_seventh_row_is_not_researched(fast):
    b = Budget(30)
    assert [b.reserve(5) for _ in range(6)] == [5] * 6
    out = fast.go("L03", budget=b)
    assert fast.fake.calls == []                            # no paid call
    assert out["numbers"] == [] and out["error"] is None and out["searches"] == 0 and out["cost"] == 0
    assert out["thin"] == "not researched: the run's 30 searches were spent before this row's turn"
    row = ledger.by_id(fast.disk())["L03"]
    assert row["status"] == "open" and row["gap"] == out["thin"] and row["dossier_refs"] == []
    assert not fast.dossier.has("L03") and not stage_research.row_paths(fast.r, "L03")[0].exists()
    assert "L03 came back thin" in fast.r.path("decisions").read_text(encoding="utf-8")


def test_a_thin_row_is_flagged_in_the_ledger(fast):
    fast.fake.rows["L02"] = "thin"       # one claim, from a page no search or fetch returned
    out = fast.go("L02")
    assert out["error"] is None and out["numbers"] == [6]
    assert out["thin"] == ("best source tier is none, and the row needs 2; "
                           "no tellable material and no verbatim quote")
    for row in (ledger.by_id(fast.disk())["L02"], fast.row("L02")):
        assert row["status"] == "partial" and row["gap"] == out["thin"]
        assert row["dossier_refs"] == ["D06"] and row["best_tier"] is None and row["followup"] is False
    decided = fast.r.path("decisions").read_text(encoding="utf-8")
    assert "L02 came back thin" in decided and "section 3 is written with what there is" in decided
    assert len(fast.fake.agent_calls()) == 1                # no follow-up round
    # A resumed run files the same verdict and does not report it twice.
    assert fast.go("L02")["thin"] == out["thin"]
    assert fast.r.path("decisions").read_text(encoding="utf-8") == decided
    # A row with no entries at all is open, and still counts as landed.
    fast.fake.rows["L03"] = "empty"
    out = fast.go("L03")
    assert out["thin"] == "no entries" and out["numbers"] == [] and fast.dossier.has("L03")
    assert ledger.by_id(fast.disk())["L03"]["status"] == "open"


@pytest.mark.parametrize("how, searches_back", [
    (RuntimeError("the connection fell over"), 30),        # before anything was spent
    ("none", 27),                                           # three searches, then no <research> block
])
def test_a_failed_agent_returns_an_error_and_never_raises(fast, how, searches_back):
    fast.fake.rows["L01"] = how
    out = fast.go("L01")
    assert out["numbers"] == [] and out["thin"] == "no findings"
    assert out["error"] and ("fell over" in out["error"] or "no <research> block" in out["error"])
    assert fast.budget.left == searches_back
    row = ledger.by_id(fast.disk())["L01"]
    assert row["status"] == "open" and row["gap"].startswith("The research agent failed: ")
    assert not fast.dossier.has("L01")
    assert "The agent failed" in fast.r.path("decisions").read_text(encoding="utf-8")
    # The other rows are untouched, and the next row still runs.
    assert fast.go("L02")["numbers"] == [6, 7, 8, 9, 10, 11]


def test_resume_skips_the_paid_call(fast):
    first = fast.go("L01")
    spent, calls = fast.r.spent(), len(fast.fake.calls)
    again = fast.go("L01")                                   # the same process
    restarted = Dossier(fast.r, fast.cfg)                    # a new process: a new dossier and budget
    b = Budget.for_run(fast.r, fast.cfg)
    third = fast.go("L01", dossier=restarted, budget=b)
    assert len(fast.fake.calls) == calls and fast.r.spent() == spent
    assert again["numbers"] == third["numbers"] == first["numbers"]
    assert (again["cost"], again["searches"], again["error"]) == (0, 0, None) and b.left == 27
    # Findings an earlier run paid for but never filed (it stopped in between) are filed, not redone.
    file_row(fast.r, "L02", findings("L02").split("## Entries\n")[1].split("\n\n## Not found")[0].splitlines(),
             pages={PAGE_URL: PAGE_TEXT})
    out = fast.go("L02", dossier=restarted, budget=b)
    assert out["numbers"] == [12, 13, 14, 15, 16, 17] and len(fast.fake.calls) == calls and b.left == 27
    assert ledger.by_id(fast.disk())["L02"]["status"] == "filled"


def test_a_paused_loop_is_written_up_and_a_failed_write_up_is_retried_alone(fast):
    fast.fake.rows["L01"] = "pause"                          # the loop pauses with its five searches used
    out = fast.go("L01")
    assert out["error"] is None and out["numbers"] == [6, 7, 8, 9, 10, 11] and out["searches"] == 5
    assert fast.budget.left == 25
    assert [bool(c.get("tools")) for c in fast.fake.calls] == [True, False]     # the loop, then its write-up
    # A write-up that fails leaves the paid loop's material; the next run writes it up without searching.
    fast.fake.rows["L02"] = "pause"
    real = fast.fake.__call__

    def no_block(kwargs):
        msg = real(kwargs)
        if "your search and fetch budget is now spent" in all_text(kwargs):
            msg.content[0].text = "No block here."
        return msg

    llm._client = FakeAnthropic(no_block)
    failed = fast.go("L02")
    assert "no <research> block" in failed["error"] and stage_research.pending_path(fast.r, "L02").exists()
    assert fast.budget.left == 20
    llm._client = FakeAnthropic(fast.fake)
    before = len(fast.fake.calls)
    out = fast.go("L02")
    assert out["error"] is None and out["numbers"] == [12, 13, 14, 15, 16, 17] and out["searches"] == 0
    assert [bool(c.get("tools")) for c in fast.fake.calls[before:]] == [False] and fast.budget.left == 20
    assert "Your budget is 5 searches" in all_text(fast.fake.calls[-1])       # the loop's own instructions


def test_rows_run_in_parallel_share_the_budget_the_dossier_and_the_ledger(fast):
    o = outline(5)
    for s in o["sections"][1:]:                              # two rows a section: eight agents, six budgets
        s["research_questions"] = s["research_questions"] * 2
    fast.outline, fast.led = o, ledger_for(o)
    ledger.save(fast.r, fast.led)
    started = threading.Barrier(4, timeout=10)
    slow = lambda rid: (lambda kwargs: (started.wait() if rid <= "L04" else None, row_message(kwargs, rid))[1])
    fast.fake.rows.update({rid: slow(rid) for rid in ledger.by_id(fast.led)})
    with ThreadPoolExecutor(max_workers=4) as pool:          # the first four agents are in flight together
        out = list(pool.map(lambda rid: run_row(fast.r, fast.cfg, fast.row(rid), fast.led, o, SCOUT_TEXT,
                                                fast.dossier, fast.budget), sorted(ledger.by_id(fast.led))))
    assert [x["error"] for x in out] == [None] * 8
    numbers = sorted(n for x in out for n in x["numbers"])
    assert numbers == list(range(6, 6 + 6 * 8))              # unique and with no gaps, after the scout's five
    assert all(x["numbers"] == list(range(x["numbers"][0], x["numbers"][0] + 6)) for x in out)
    assert dossier_entries(fast.r.path("dossier").read_text(encoding="utf-8")) == fast.dossier.entries()
    disk = ledger.by_id(fast.disk())                         # no verdict lost between threads
    assert all(disk[x["row"]]["dossier_refs"] == [f"D{n:02d}" for n in x["numbers"]] for x in out)
    assert all(disk[x["row"]]["status"] == "filled" for x in out)
    assert fast.budget.left == 30 - 3 * 8
    granted = sorted(c["tools"][0]["max_uses"] for c in fast.fake.agent_calls())
    assert sum(granted) <= 30 + 2 * 7 and max(granted) == 5  # never more than is left when an agent starts


# --- a listener's note ------------------------------------------------------------------------------

def note(kind="question", text="Did Alcibiades really turn up drunk?", section=1):
    return {"time": "2026-11-12T22:01:03+00:00", "section": section, "kind": kind, "text": text}


YES = dict(research=True, section=3, idea="Alcibiades' entrance is the dialogue's own answer to the ladder.",
           question="What does Alcibiades say on arriving, Symposium 212c-215a?", tier_needed=1,
           reason="No entry has his arrival; section 3 gains the scene the listener asked about.")


def test_only_some_kinds_of_note_are_considered(fast):
    fast.fake.note = note_reply(**YES)
    for kind in notes.KINDS:
        if kind not in ("wants more", "question", "unclear"):
            assert note_row(fast.r, fast.cfg, note(kind), fast.outline, fast.led, fast.dossier, [3, 4]) is None
    assert fast.fake.calls == []                             # decided by code: no call for these
    assert fast_research.NOTE_KINDS == ("wants more", "question", "unclear")
    for k, kind in enumerate(fast_research.NOTE_KINDS):
        fast.cfg["fast"]["note_research"]["max_agents"] = 9
        note_row(fast.r, fast.cfg, note(kind, text=f"note {k}"), fast.outline, fast.led, fast.dossier, [3, 4])
    assert len(fast.fake.note_calls()) == 3


def test_a_note_the_dossier_cannot_serve_adds_a_row_for_an_open_section(fast):
    fast.go("L01")
    fast.fake.note = note_reply(**YES)
    n = note()
    notes.add(fast.r.root, n["text"], n["kind"], n["section"], when=n["time"])
    row = note_row(fast.r, fast.cfg, notes.load(fast.r.root)[0], fast.outline, fast.led, fast.dossier, [3, 4])
    assert row["id"] == "L04" and row["section"] == 3 and row["from_note"] is True and row["type"] == "missing"
    assert row["research_question"] == YES["question"] and row["idea"] == YES["idea"] and row["tier_needed"] == 1
    assert row["listener_note"] == n["text"] and row["rank"] == 4 and row["status"] is None
    assert ledger.by_id(fast.disk())["L04"] == row and fast.row("L04") is row     # on disk and in the caller's copy
    assert ledger.validate(fast.disk()) is not None
    decided = fast.r.path("decisions").read_text(encoding="utf-8")
    assert "started one extra research agent, L04, for section 3" in decided and n["text"] in decided
    # What the model was asked: the note, the open and closed sections, the research so far, the small budget.
    (call,) = fast.fake.note_calls()
    sent = all_text(call)
    assert NOTE_PHRASE in sent and "(question, said during section 1) Did Alcibiades really turn up drunk?" in sent
    assert 'Section 2, "Rung 2": closed.' in sent and 'Section 3, "Rung 3": open.' in sent
    assert "Lands: What rung 3 of the ladder asks of the lover." in sent
    assert "- L01, section 2 (landed: D06-D11)" in sent and "- L02, section 3 (still out)" in sent
    assert "[D07] (L01, material, tier 1) Agathon's dinner party" in sent
    assert "3 searches and 1 page fetches" in sent and "may start 2 more" in sent and "| Tier | Sources |" in sent
    assert "{{" not in sent and "podcast" not in sent.lower() and "episode" not in sent.lower()
    assert call["output_config"]["format"] == {"type": "json_schema", "schema": fast_research.NOTE_SCHEMA}
    assert call["model"] == "claude-sonnet-5-5"
    assert [x["stage"] for x in fast.r.read_log()][-1] == "fast_note"
    # Its agent gets the note budget from the notes' own counter, is told what the listener said, and is filed.
    out = fast.go("L04")
    (agent,) = fast.fake.agent_calls("L04")
    assert {t["name"]: t["max_uses"] for t in agent["tools"]} == {"web_search": 3, "web_fetch": 1}
    assert 'The listener, part-way through the chapter, said: "Did Alcibiades really turn up drunk?"' in all_text(agent)
    assert 'This idea belongs to section 3 of 4, "Rung 3".' in all_text(agent)
    assert out["error"] is None and out["numbers"] == [12, 13, 14, 15, 16, 17]
    assert (fast.budget.left, fast.budget.notes.left) == (30 - 3, 6 - 3)
    assert "added to answer something the listener said" in fast.dossier.text()
    # The same note is never decided twice: the row comes back without a call, also after a restart.
    assert note_row(fast.r, fast.cfg, note(), fast.outline, fast.led, fast.dossier, []) is row
    reloaded = ledger.load(fast.r, render=False)
    again = note_row(fast.r, fast.cfg, note(), fast.outline, reloaded, Dossier(fast.r, fast.cfg), [4])
    assert again["id"] == "L04" and len(fast.fake.note_calls()) == 1


def test_at_most_two_extra_agents_a_chapter(fast):
    fast.fake.note = note_reply(**YES)
    rows = [note_row(fast.r, fast.cfg, note(text=f"question {k}"), fast.outline, fast.led, fast.dossier, [3, 4])
            for k in range(3)]
    assert [x and x["id"] for x in rows] == ["L04", "L05", None]
    assert len(fast.fake.note_calls()) == 2                  # the third note costs nothing
    assert [x["id"] for x in fast.disk()["rows"] if x.get("from_note")] == ["L04", "L05"]
    assert "may start 1 more" in all_text(fast.fake.note_calls()[1])


def test_a_note_only_reaches_sections_whose_drafting_has_not_begun(fast):
    fast.fake.note = note_reply(**YES)                       # the model names section 3
    assert note_row(fast.r, fast.cfg, note(), fast.outline, fast.led, fast.dossier, []) is None
    assert fast.fake.calls == []                             # every section is under way: no call
    assert note_row(fast.r, fast.cfg, note(), fast.outline, fast.led, fast.dossier, [4]) is None
    sent = all_text(fast.fake.note_calls()[0])
    assert 'Section 3, "Rung 3": closed.' in sent and 'Section 4, "Rung 4": open.' in sent
    assert len(fast.disk()["rows"]) == 3 and len(fast.led["rows"]) == 3      # section 3 has begun: no row
    assert note_row(fast.r, fast.cfg, note(), fast.outline, fast.led, fast.dossier, [3, 4]) is None  # decided once
    assert len(fast.fake.note_calls()) == 1


def test_a_note_the_dossier_serves_starts_nothing(fast):
    fast.fake.note = note_reply(research=False)
    assert note_row(fast.r, fast.cfg, note("wants more", "More about the dinner."), fast.outline, fast.led,
                    fast.dossier, [2, 3, 4]) is None
    assert len(fast.disk()["rows"]) == 3 and not fast.r.path("decisions").exists()
    saved = json.loads((base.path(fast.r, "work") / "note_rows.json").read_text(encoding="utf-8"))
    assert [(x["row"], x["research"], x["reason"]) for x in saved.values()] == [
        (None, False, "D02 already tells the dinner party.")]
    # A yes that names nothing to research is no row either.
    fast.fake.note = note_reply(**{**YES, "question": " "})
    assert note_row(fast.r, fast.cfg, note(text="another"), fast.outline, fast.led, fast.dossier, [3]) is None
    assert len(fast.disk()["rows"]) == 3


# --- review fixes: the notes' own searches, the entries that answer a note, the fast row prompt ----

def _max_uses(call) -> dict:
    return {t["name"]: t["max_uses"] for t in call["tools"]}


def test_note_agents_have_their_own_searches_and_the_outlines_rows_keep_the_thirty(fast):
    """Six outline rows may hold all 30 searches; a note's agent still gets its 3, and takes none of theirs."""
    b = Budget.for_run(fast.r, fast.cfg)
    assert (b.total, b.left) == (30, 30) and (b.notes.total, b.notes.left) == (6, 6)
    assert Budget.for_notes(fast.r, fast.cfg).total == 6     # max_agents x searches, for the driver to build
    fast.fake.note = note_reply(**YES)
    first = note_row(fast.r, fast.cfg, note(), fast.outline, fast.led, fast.dossier, [3, 4], budget=b)
    second = note_row(fast.r, fast.cfg, note(text="And then?"), fast.outline, fast.led, fast.dossier, [3, 4], budget=b)
    assert (first["id"], second["id"]) == ("L04", "L05")
    assert [b.reserve(5) for _ in range(6)] == [5] * 6 and b.left == 0       # the outline's rows hold every search
    out = fast.go("L04", budget=b)                           # the driver passes the run's budget, as for any row
    assert _max_uses(fast.fake.agent_calls("L04")[0]) == {"web_search": 3, "web_fetch": 1}
    assert out["error"] is None and out["thin"] is None and out["numbers"] == [6, 7, 8, 9, 10, 11]
    assert (b.left, b.notes.left) == (0, 3)
    assert "the outline's rows share 30" in fast.dossier.text()
    # The other way round: a note's agent that starts first leaves an outline row its five.
    b = Budget(30, used=25, notes=Budget(6, used=3))
    fast.go("L05", budget=b)
    assert (b.left, b.notes.left) == (5, 0)
    fast.go("L03", budget=b)
    assert _max_uses(fast.fake.agent_calls("L03")[0]) == {"web_search": 5, "web_fetch": 2}
    # A continued run counts each agent's searches against its own budget.
    again = Budget.for_run(fast.r, fast.cfg)
    assert (again.left, again.notes.left) == (30 - 3, 0)
    # A note's row whose turn comes with the notes' searches gone is not researched, and says which budget.
    fast.led["rows"].append({**first, "id": "L06"})
    out = fast.go("L06", budget=again)
    assert out["thin"] == "not researched: the 6 searches kept for listeners' notes were spent before this row's turn"
    assert fast.fake.agent_calls("L06") == []
    # A budget built by hand with no notes of its own keeps the old behaviour: one counter.
    lone = Budget(30, used=28)
    assert lone.notes is None and fast_research._budget_for(lone, first) is lone


def test_a_note_is_not_judged_or_given_a_row_when_no_searches_are_left(fast):
    fast.fake.note = note_reply(**YES)
    b = Budget(30, notes=Budget(6, used=6))
    args = (fast.r, fast.cfg, note(), fast.outline, fast.led, fast.dossier, [3, 4])
    assert note_row(*args, budget=b) is None
    assert fast.fake.calls == []                             # not paid for
    assert not [x for x in fast.disk()["rows"] if x.get("from_note")] and len(fast.led["rows"]) == 3
    assert not fast.r.path("decisions").exists()
    assert not (base.path(fast.r, "work") / "note_rows.json").exists()       # and not decided: it may be asked again
    assert note_row(*args, budget=Budget(30, used=30)) is None and fast.fake.calls == []   # one counter, spent
    b.notes.settle(3, 1)                                     # an agent hands two back
    row = note_row(*args, budget=b)
    assert row["id"] == "L04" and len(fast.fake.note_calls()) == 1
    assert "2 searches and 1 page fetches" in all_text(fast.fake.note_calls()[0])   # judged against what is left
    # Called without a budget (the driver's call today), the run's log decides.
    fast.r.log({"stage": "research_row", "row": "L04", "web_searches": 6})
    assert note_row(fast.r, fast.cfg, note(text="One more?"), fast.outline, fast.led, fast.dossier, [3, 4]) is None
    assert len(fast.fake.note_calls()) == 1


def test_the_judge_names_the_entries_that_answer_a_note_and_note_answers_hands_them_on(fast):
    assert fast_research.note_answers(fast.r) == []
    fast.go("L01")
    fast.fake.note = note_reply(research=False, entries=[7, 2, 7, 99], reason="D02 and D07 tell the dinner party.")
    asked = note("question", "Who gave the dinner?", section=2)
    assert note_row(fast.r, fast.cfg, asked, fast.outline, fast.led, fast.dossier, [4]) is None
    # D07 belongs to L01 (section 2); section 4's writer is not offered it unless it is named here.
    assert fast_research.note_answers(fast.r) == [
        {"time": asked["time"], "text": asked["text"], "kind": "question", "entries": [2, 7]}]   # D99 does not exist
    fast.fake.note = note_reply(**YES, entries=[1])
    more = note("wants more", "More about Alcibiades.", section=2)
    assert note_row(fast.r, fast.cfg, more, fast.outline, fast.led, fast.dossier, [3, 4])["id"] == "L04"
    assert fast_research.note_answers(fast.r)[1] == {"time": more["time"], "text": more["text"], "kind": "wants more",
                                                     "entries": [1]}
    # The model is asked for them, and told they are what the writers get.
    sent = all_text(fast.fake.note_calls()[0])
    assert "**entries**" in sent and "{{" not in sent
    schema = fast_research.NOTE_SCHEMA
    assert "entries" in schema["required"] and schema["properties"]["entries"] == {
        "type": "array", "items": {"type": "integer"}}
    # A reply without the field, and a decision an earlier version saved, read as no entries.
    fast.fake.note = json.dumps({k: v for k, v in json.loads(note_reply()).items() if k != "entries"})
    fast.cfg["fast"]["note_research"]["max_agents"] = 9
    note_row(fast.r, fast.cfg, note(text="third"), fast.outline, fast.led, fast.dossier, [4])
    assert fast_research.note_answers(fast.r)[2]["entries"] == []
    path = base.path(fast.r, "work") / "note_rows.json"
    base.write_json(path, {"abc": {"note": "an old one", "row": None, "research": False, "reason": ""}})
    assert fast_research.note_answers(fast.r) == [{"time": "", "text": "an old one", "kind": "", "entries": []}]


def test_a_fast_rows_agent_is_told_the_scouts_entries_are_already_filed(fast):
    fast.go("L01")
    sent = all_text(fast.fake.agent_calls("L01")[0])
    assert "never sees the scout" not in sent and "return as your own sourced entries the basic facts" not in sent
    assert "are already filed" in sent and "what the scout does not have" in sent
    assert "{{" not in sent and "podcast" not in sent.lower() and "episode" not in sent.lower()
    assert "<scout_has>\nThe ladder, in passing.\n</scout_has>" in sent
    # The reply format is the overnight seed run's fragment, whole: research.parse_entries reads both.
    assert common.load_prompt("research_format_seed", row_id="L01", start=1).strip() in sent
    assert len([ln for ln in sent.splitlines() if ln.startswith("[L01.")]) == 5
    # A paused loop's write-up carries the same instructions.
    fast.fake.rows["L02"] = "pause"
    fast.go("L02")
    writeup = all_text(fast.fake.calls[-1])
    assert "your search and fetch budget is now spent" in writeup and "are already filed" in writeup
    assert "never sees the scout" not in writeup
    # A covered row keeps the overnight verify prompt, word for word.
    row = fast.row("L03")
    row["type"] = "covered"
    fast.go("L03")
    assert fast.fake.agent_calls("L03")[0]["messages"][0]["content"] == stage_research.agent_prompt(
        fast.r, fast_research._row_cfg(fast.cfg, row, 5), row, fast.led,
        fast_research.angle_text(fast.outline, row), SCOUT_TEXT)


def test_overnight_agents_are_unchanged_by_run_agents_prompt_argument(fast):
    """stages/research.py run_agent gained `prompt`; left out, the agent gets agent_prompt's text as before."""
    import inspect
    assert inspect.signature(stage_research.run_agent).parameters["prompt"].default is None
    c, row = base.agent_cfg(fast.cfg), fast.row("L01")
    stage_research.run_agent(fast.r, c, row, fast.led, "the angle", "the map")
    sent = fast.fake.agent_calls("L01")[0]["messages"][0]["content"]
    assert sent == stage_research.agent_prompt(fast.r, c, row, fast.led, "the angle", "the map")
    assert "The writer never sees the scout's map" in sent   # the seed prompt itself is untouched
