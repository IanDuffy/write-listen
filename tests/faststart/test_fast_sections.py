"""faststart/sections.py: the opening, the drafts and the rolling edit, against fake replies.
Acceptance checks 2 (every draft has the full text of all earlier sections), 4 (the lock moves
material without losing or duplicating it), 5b (listener notes reach every later draft and lock)
and 8 (what is locked passes the code checks, by a fix call or by repair)."""

import copy
import json
from pathlib import Path

import fast_sections_fakes as F
import pytest
from fakes import all_text

from faststart import base, checks, sections
from faststart.sections import Ctx, Lock
from stages.common import StageError, load_prompt

CACHE = {"type": "ephemeral"}
NOTES = [{"time": "t1", "section": 0, "kind": "connection", "text": "My grandfather built sea walls in Cork."},
         {"time": "t2", "section": 1, "kind": "unclear", "text": "I lost the thread at the volcanic ash."}]
MINE = ["fast_context", "fast_earlier", "fast_opening", "fast_opening_edit", "fast_draft", "fast_draft_middle",
        "fast_draft_last", "fast_lock", "fast_lock_paired", "fast_lock_solo", "fast_lock_final", "fast_fix"]


@pytest.fixture
def fake(monkeypatch):
    return F.install(monkeypatch)


@pytest.fixture
def ctx(run, cfg):
    run.unattended, run.max_usd = True, 5.0
    run.path("source_dir").mkdir()
    run.path("seed").write_text(F.SEED)
    base.write_atomic(base.path(run, "scout"), "## Territory\n\nHarbours, ash and seawater.\n\n## Entries\n\n"
                      "[L00.1] (material, tier 2) UNCHECKED SCOUT LINE. Source: https://example.org/x\n\n"
                      "## Scenes\n\n1. The barges at Caesarea (D01).\n")
    led = F.ledger_rows()
    run.path("ledger").write_text(json.dumps(led))
    return Ctx(r=run, cfg=cfg, outline=copy.deepcopy(F.OUTLINE), led=led, dossier=F.FakeDossier())


def drafts(n_from=1, n_to=5):
    return {n: F.section_text(n, [1, 3]) for n in range(n_from, n_to + 1)}


def decisions(run) -> str:
    p = run.path("decisions")
    return p.read_text() if p.exists() else ""


# --- the prompts ----------------------------------------------------------------------------

def test_prompts_have_headers_and_never_mention_an_episode():
    for name in MINE:
        raw = (Path(sections.__file__).parent.parent / "prompts" / f"{name}.md").read_text()
        head, _, body = raw.partition("\n---\n")
        assert head.startswith("# Prompt:") and body.strip(), name
        assert "episode" not in body.lower() and "podcast" not in body.lower(), name


def test_the_rules_say_what_listener_notes_may_and_may_not_change(ctx):
    system = sections._system(ctx)
    for allowed in ("They may change:", "what a section dwells on", "which example it chooses", "a callback",
                    "clears up what was unclear", "an answer to a question when an entry has it"):
        assert allowed in system
    for barred in ("They may not change:", "the section list or its order", "the through-line", "anything already narrated"):
        assert barred in system
    assert "When I looked into this further, I found" in system  # the correction, in the guide's own voice
    assert "verbatim: yes" in system and "[D03, D07]" in system and "[pause]" in system
    assert F.SEED.strip() in system and "Through-line: What we call decay" in system
    assert "Section 4: Cracks that close (about 550 words)" in system


# --- the opening ----------------------------------------------------------------------------

def test_the_opening_is_written_from_the_scout_alone(ctx, fake):
    text = sections.write_opening(ctx, NOTES[:1])
    call, = fake.of("opening")
    block = F.last_block(call)
    assert call["system"][0]["cache_control"] == CACHE and len(F.blocks(call)) == 1
    # The scout's entries come from the dossier, where they were checked: all of L00 and nothing else.
    assert F.offered(block) == [1, 2, 3, 4]
    assert "UNCHECKED SCOUT LINE" not in block and "The barges at Caesarea (D01)" in block and "Harbours, ash" in block
    assert "- I pour concrete for a living" in block and "My grandfather built sea walls in Cork." in block
    assert "Open on the barges at Caesarea" in block and "about 950 words" in block and "60 words at most" in block
    assert text == sections.latest_draft(ctx.r, 1) == F.opening_text([1, 2, 3, 4])
    assert (ctx.r.root / "06_segments/drafts/seg01.d1.md").exists()
    rec = ctx.r.read_log()[-1]
    assert rec["stage"] == "fast_opening" and rec["section"] == 1
    assert sections.write_opening(ctx, NOTES) == text and len(fake.calls) == 1  # resumed from its file


def test_the_opening_edit_locks_section_one(ctx, fake):
    draft = sections.write_opening(ctx, [])
    final = sections.edit_opening(ctx, draft, NOTES)
    call, = fake.of("opening_edit")
    block = F.last_block(call)
    assert F.tag(block, "draft") == draft and "My grandfather built sea walls in Cork." in block
    assert call["system"] == fake.of("opening")[0]["system"]  # one system prompt, cached once
    assert F.FILLER in draft and F.FILLER not in final
    assert sections.locked_text(ctx.r, 1) == final
    assert sections.saved_lock(ctx.r, 1) == Lock(n=1, mode="opening", text=final)
    assert checks.section_problems(final, ctx.dossier.text(), {1, 2, 3, 4}) == []
    assert not fake.of("fix") and decisions(ctx.r) == ""
    assert sections.edit_opening(ctx, draft, NOTES) == final and len(fake.calls) == 2  # resumed


def test_an_opening_that_cites_past_the_scout_gets_one_fix_call(ctx, fake):
    bad = "The ash came from the bay near Pozzuoli. [D05]\n\nThe mix was lime, ash and seawater. [D03]"
    good = "The ash came from somewhere across the water.\n\nThe mix was lime, ash and seawater. [D03]"
    fake.on("opening_edit", lambda kw: bad).on("fix", lambda kw: good)
    assert sections.edit_opening(ctx, bad, []) == good
    fix, = fake.of("fix")
    block = F.last_block(fix)
    assert F.tag(block, "returned") == bad
    assert "cites D05, which is not a scout entry" in F.tag(block, "problems")
    assert F.blocks(fix)[:-1] == F.blocks(fake.of("opening_edit")[0])  # the same request, one block more
    assert ctx.r.read_log()[-1]["fix"] is True and ctx.r.read_log()[-1]["stage"] == "fast_opening_edit"
    assert decisions(ctx.r) == ""


def test_what_the_fix_leaves_in_the_opening_is_repaired_by_code_and_recorded(ctx, fake):
    bad = ('The ash came from the bay. [D05] The wall is old. [D99] One engineer said, "nothing we pour today will '
           'outlive us by much." The mix was lime, ash and seawater. [D03]')
    fake.on("opening_edit", lambda kw: bad)  # and the stock fix changes nothing
    final = sections.edit_opening(ctx, bad, [])
    assert final == ("The ash came from the bay. The wall is old. One engineer said, nothing we pour today will "
                     "outlive us by much. The mix was lime, ash and seawater. [D03]")
    assert len(fake.of("fix")) == 1
    said = decisions(ctx.r)
    assert "## fast opening" in said
    assert "repaired by code: dropped the marker for D05 (not a scout entry)" in said
    assert "repaired by code: dropped the marker for D99 (not in the dossier)" in said
    assert "repaired by code: took the quotation marks off" in said and "not repaired" not in said
    assert sections.saved_lock(ctx.r, 1).problems and sections.locked_text(ctx.r, 1) == final


def test_an_opening_edit_that_comes_back_as_a_fragment_is_caught(ctx, fake):
    draft = F.opening_text([1, 2])
    fake.on("opening_edit", lambda kw: "A barge rides low. [D01]").on("fix", lambda kw: F.without_filler(draft))
    assert sections.edit_opening(ctx, draft, []) == F.without_filler(draft)
    assert "text was lost" in F.tag(F.last_block(fake.of("fix")[0]), "problems")


# --- acceptance check 2: drafting -----------------------------------------------------------

def test_every_draft_carries_the_full_text_of_every_earlier_section(ctx, fake):
    """The writer lane: when section n is drafted, sections up to n-2 are locked and n-1 is a draft."""
    locked = {1: "LOCKED ONE. " + F.without_filler(F.opening_text([1, 2]))}
    latest = {}
    for n in (2, 3, 4, 5):
        earlier = [(k, locked.get(k) or latest[k], k in locked) for k in range(1, n)]
        latest[n] = sections.draft(ctx, n, earlier, [])
        call = fake.of("draft", n)[0]
        content = F.blocks(call)
        assert len(content) == n + 1 and content[0]["text"] == load_prompt("fast_earlier")
        for (k, text, is_locked), block in zip(earlier, content[1:n]):
            state = "locked" if is_locked else "draft"
            assert block["text"] == f'<section n="{k}" state="{state}">\n{text}\n</section>', (n, k)
        assert content[-1]["text"].startswith(f"Write section {n} of 5: {F.OUTLINE['sections'][n - 1]['title']}.")
        # Cache: the system prompt and the last locked section. A draft's block is never a breakpoint.
        n_locked = sum(1 for _, _, is_locked in earlier if is_locked)
        marked = [i for i, b in enumerate(content) if b.get("cache_control") == CACHE]
        assert marked == [n_locked] and call["system"][0]["cache_control"] == CACHE
        if n >= 3:  # the lane locks section n-1 (with this draft beside it) before the next draft
            locked[n - 1] = f"LOCKED {n - 1}. " + F.without_filler(latest[n - 1])
    calls = [fake.of("draft", n)[0] for n in (2, 3, 4, 5)]
    assert len({json.dumps(c["system"]) for c in calls}) == 1
    # Locked sections are stable blocks: each call's locked prefix is the next call's, byte for byte.
    texts = [[b["text"] for b in F.blocks(c)] for c in calls]
    assert texts[1][:2] == texts[0][:2] and texts[2][:2] == texts[1][:2]   # the preamble and section 1
    assert texts[3][:3] == texts[2][:3]                                    # and section 2, once it is locked
    assert "LOCKED 4" not in all_text(calls[3]) and "LOCKED 3. " in all_text(calls[3])
    assert [r["section"] for r in ctx.r.read_log() if r["stage"] == "fast_draft"] == [2, 3, 4, 5]


def test_a_draft_gets_its_rows_entries_and_thin_flags(ctx, fake):
    ctx.dossier.thin_rows["L01"] = "no tellable material and no verbatim quote"
    sections.draft(ctx, 2, [(1, "One.", True)], [])
    block = F.last_block(fake.of("draft", 2)[0])
    assert F.offered(block) == [3, 5, 6]                    # L01's entries and the scout entry the outline gives it
    assert F.offered(block, "other_entries") == [1, 2, 4]   # the rest of the scout
    rows = F.tag(block, "rows")
    assert rows.startswith("- L01: The recipe\n  Research question: What went into the mix?")
    assert "  Research: came back thin (no tellable material and no verbatim quote); entries D05, D06" in rows
    assert "Start at the quarry." in F.tag(block, "plan") and "about 500 words" in block
    assert "Do not wrap up the chapter" in block and "This is the last section" not in block


def test_a_row_that_never_landed_is_flagged_and_the_last_section_is_told_it_is_last(ctx, fake):
    ctx.dossier = F.FakeDossier(F.DOSSIER.replace("[D11]", "[D11x]"))  # L04's research did not come back
    sections.draft(ctx, 5, [(k, f"Text {k}.", True) for k in range(1, 5)], [])
    block = F.last_block(fake.of("draft", 5)[0])
    assert "- L04: Modern concrete" in block and "Research: came back thin (no findings); no entries" in block
    assert F.offered(block) == [2] and "This is the last section." in block and "Land the through-line" in block


def test_a_draft_needs_every_earlier_section_and_is_kept_once_written(ctx, fake):
    with pytest.raises(StageError, match="sections 1 to 2"):
        sections.draft(ctx, 3, [(1, "One.", True)], [])
    assert fake.calls == []
    first = sections.draft(ctx, 2, [(1, "One.", True)], [])
    assert sections.draft(ctx, 2, [(1, "One, changed.", True)], NOTES) == first and len(fake.calls) == 1
    assert sections.save_draft(ctx.r, 2, "Revised.").name == "seg02.d2.md" and sections.latest_draft(ctx.r, 2) == "Revised."
    assert sections.latest_draft(ctx.r, 3) is None and sections.locked_text(ctx.r, 2) is None


# --- acceptance check 5b: listener notes ----------------------------------------------------

def test_notes_reach_every_draft_and_lock_made_after_them_and_none_made_before(ctx, fake):
    one = [(1, "One.", True)]
    d2 = sections.draft(ctx, 2, one, [])
    d3 = sections.draft(ctx, 3, one + [(2, d2, False)], NOTES[:1])
    lock2 = sections.lock(ctx, 2, d2, d3, [(1, "One.")], NOTES, "paired")
    sections.apply(ctx, lock2)
    sections.draft(ctx, 4, one + [(2, lock2.text, True), (3, d3, False)], NOTES)
    first, second = NOTES[0]["text"], NOTES[1]["text"]
    said = {(k, n): F.tag(F.last_block(c), "listener_notes") for (k, n), c in
            {("draft", 2): fake.of("draft", 2)[0], ("draft", 3): fake.of("draft", 3)[0],
             ("lock", 2): fake.of("lock", 2)[0], ("draft", 4): fake.of("draft", 4)[0]}.items()}
    assert said[("draft", 2)] == "(none yet)"
    assert first in said[("draft", 3)] and second not in said[("draft", 3)]
    for later in (("lock", 2), ("draft", 4)):
        assert "- (connection, before the chapter began) " + first in said[later]
        assert "- (unclear, during section 1) " + second in said[later]
    # Notes travel in the request's own block, never in the cached prefix or the locked text.
    for c in fake.calls:
        assert first not in json.dumps(c["system"]) and all(first not in b["text"] for b in F.blocks(c)[:-1])
    assert first not in sections.locked_text(ctx.r, 2)


# --- acceptance check 4: the paired lock ----------------------------------------------------

MOVED = "A core drilled from the harbour wall still shows the lumps of ash sitting in it. [D06]"


def pair():
    d2 = F.without_filler(F.section_text(2, [5, 6]))
    d3 = F.without_filler(F.section_text(3, [7, 8])) + "\n\n" + MOVED
    return d2, d3


def test_the_paired_lock_request(ctx, fake):
    d2, d3 = pair()
    got = sections.lock(ctx, 2, d2, d3, [(1, "LOCKED ONE.")], NOTES, "paired")
    call, = fake.of("lock", 2)
    content, block = F.blocks(call), F.last_block(call)
    assert [b["text"] for b in content[:2]] == [load_prompt("fast_earlier"), '<section n="1" state="locked">\nLOCKED ONE.\n</section>']
    assert content[1]["cache_control"] == CACHE and "cache_control" not in content[0]
    assert F.tag(block, "draft", 'n="2"') == d2 and F.tag(block, "draft", 'n="3"') == d3
    assert "Tell it through one drilled core." in F.tag(block, "plan", 'n="3"') and "Start at the quarry." in block
    assert F.tag(block, "rows").startswith("- L01: The recipe") and "L02" not in F.tag(block, "rows")
    assert F.offered(block) == [1, 2, 3, 4, 5, 6, 7, 8]  # the scout's, both sections' rows', whatever the drafts cite
    fmt = call["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"] == sections.SCHEMA
    assert fmt["schema"]["required"] == ["text", "next_text", "moved", "rows", "cut", "title", "corrections"]
    assert got == Lock(n=2, mode="paired", text=d2, next_text=d3,
                       rows=[{"id": "L01", "coverage": "covered", "note": "Lands with its material."}])
    rec = ctx.r.read_log()[-1]
    assert (rec["stage"], rec["section"], rec["mode"]) == ("fast_lock", 2, "paired")
    assert not (ctx.r.root / "06_segments").exists()  # lock() writes nothing; apply() does


def test_each_lock_reads_the_prefix_the_lock_before_it_wrote(ctx, fake):
    """Same model, effort, format and system prompt, and the locked sections as the same blocks."""
    d2, d3 = pair()
    d4 = F.section_text(4, [9, 10])
    two = sections.lock(ctx, 2, d2, d3, [(1, "LOCKED ONE.")], [], "paired")
    sections.lock(ctx, 3, d3, d4, [(1, "LOCKED ONE."), (2, two.text)], NOTES, "paired")
    sections.lock(ctx, 4, d4, None, [(1, "LOCKED ONE."), (2, two.text), (3, "Three.")], NOTES, "solo")
    a, b, c = (fake.of("lock", n)[0] for n in (2, 3, 4))
    assert a["system"] == b["system"] == c["system"] and a["model"] == b["model"] == c["model"]
    assert a["output_config"] == b["output_config"] == c["output_config"]
    assert [x["text"] for x in F.blocks(a)[:2]] == [x["text"] for x in F.blocks(b)[:2]]
    assert [x["text"] for x in F.blocks(b)[:3]] == [x["text"] for x in F.blocks(c)[:3]]
    assert [i for i, x in enumerate(F.blocks(a)) if x.get("cache_control")] == [1]
    assert [i for i, x in enumerate(F.blocks(b)) if x.get("cache_control")] == [2]
    # A locked section is sent as it was locked, in drafts too.
    sections.draft(ctx, 5, [(1, "LOCKED ONE.", True), (2, two.text, True), (3, "Three.", True), (4, d4, False)], [])
    assert [x["text"] for x in F.blocks(fake.of("draft", 5)[0])[:3]] == [x["text"] for x in F.blocks(b)[:3]]


def test_the_lock_moves_a_paragraph_from_the_next_section_into_this_one(ctx, fake):
    d2, d3 = pair()
    new2 = d2.replace("\n\nAnd section 2", "\n\n" + MOVED + "\n\nAnd section 2")
    new3 = d3.replace("\n\n" + MOVED, "")
    fake.on("lock", lambda kw: F.lock_reply(kw, text=new2, next_text=new3,
                                            moved=[{"text": MOVED, "from": 3, "to": 2}]))
    got = sections.lock(ctx, 2, d2, d3, [(1, "One.")], [], "paired")
    assert (got.text, got.next_text, got.problems) == (new2, new3, [])
    assert (got.text + got.next_text).count(MOVED) == 1 and MOVED in got.text
    assert got.moved == [{"text": MOVED, "from": 3, "to": 2}]
    assert checks.cited(d2) | checks.cited(d3) == checks.cited(got.text) | checks.cited(got.next_text)
    assert len(fake.calls) == 1  # nothing to fix


def test_a_move_left_in_both_sections_gets_the_fix_round(ctx, fake):
    d2, d3 = pair()
    new2 = d2.replace("\n\nAnd section 2", "\n\n" + MOVED + "\n\nAnd section 2")
    new3 = d3.replace("\n\n" + MOVED, "")
    move = [{"text": MOVED, "from": 3, "to": 2}]
    fake.on("lock", lambda kw: F.lock_reply(kw, text=new2, next_text="", moved=move))  # the next draft untouched
    fake.on("fix", lambda kw: F.lock_reply({"messages": [{"role": "user", "content": F.blocks(kw)[:-1]}]},
                                           text=new2, next_text=new3, moved=move))
    got = sections.lock(ctx, 2, d2, d3, [(1, "One.")], [], "paired")
    fix, = fake.of("fix")
    told = F.tag(F.last_block(fix), "problems")
    assert "appears in both sections; keep it in one place only" in told and "core drilled from the harbour wall" in told
    assert json.loads(F.tag(F.last_block(fix), "returned"))["text"] == new2
    assert F.blocks(fix)[:-1] == F.blocks(fake.of("lock")[0]) and fix["output_config"]["format"]["schema"] == sections.SCHEMA
    assert (got.text + "\n" + got.next_text).count(MOVED) == 1 and got.next_text == new3 and got.problems == []
    assert [r.get("fix") for r in ctx.r.read_log()] == [None, True]


def test_a_duplicate_the_fix_does_not_remove_is_reported_not_hidden(ctx, fake):
    d2, d3 = pair()
    fake.on("lock", lambda kw: F.lock_reply(kw, text=d2 + "\n\n" + MOVED, next_text=""))  # and the stock fix changes nothing
    got = sections.lock(ctx, 2, d2, d3, [(1, "One.")], [], "paired")
    assert len(fake.of("fix")) == 1 and len(got.problems) == 1 and "appears in both sections" in got.problems[0]
    sections.apply(ctx, got)
    assert "- Code check: a sentence appears in both sections" in decisions(ctx.r)


def test_a_fix_call_that_fails_leaves_the_first_edit_standing(ctx, fake):
    d2, d3 = pair()
    fake.on("lock", lambda kw: F.lock_reply(kw, text=d2 + " [D99]", next_text="")).on("fix", lambda kw: "not json")
    got = sections.lock(ctx, 2, d2, d3, [(1, "One.")], [], "paired")
    assert got.text == d2 and "repaired by code: dropped the marker for D99 (not in the dossier)" in got.problems


# --- code repairs on a lock -----------------------------------------------------------------

def test_what_the_fix_leaves_in_a_lock_is_repaired_by_code_and_recorded(ctx, fake):
    d2, d3 = pair()
    bad = d2 + ' [D99] One engineer said, "nothing we pour today will outlive us by much." [laughs]'
    fake.on("lock", lambda kw: F.lock_reply(kw, text=bad))
    got = sections.lock(ctx, 2, d2, d3, [(1, "One.")], [], "paired")
    assert got.text == d2 + " One engineer said, nothing we pour today will outlive us by much."
    assert checks.section_problems(got.text, ctx.dossier.text()) == []
    record = "\n".join(got.problems)
    assert "cites D99, which is not in the dossier" in record and "a quotation that matches the quoted words of no" in record
    assert "repaired by code: dropped the marker for D99" in record and "repaired by code: took the quotation marks off" in record
    assert "repaired by code: removed a bracketed tag the narrator would read: '[laughs]'" in record
    assert "not repaired" not in record and len(fake.of("fix")) == 1
    sections.apply(ctx, got)
    assert "- Code check: repaired by code: dropped the marker for D99 (not in the dossier)" in decisions(ctx.r)
    assert sections.locked_text(ctx.r, 2) == got.text


def test_the_editors_verdicts_are_checked_by_code(ctx, fake):
    d2, d3 = pair()
    uncited = "Section 2 says something about the recipe and cites only the scout. [D03]\n\n" + "It goes on. " * 60
    fake.on("lock", lambda kw: F.lock_reply(kw, text=uncited, cut=[{"text": d2, "reason": "rewritten"}]))
    got = sections.lock(ctx, 2, d2, d3, [(1, "One.")], [], "paired")
    assert got.rows == [{"id": "L01", "coverage": "thin", "note": "Lands with its material. (By code: the section cites "
                                                                  "none of this row's entries.)"}]
    assert got.cut == [f"{' '.join(d2.split())} (rewritten)"]


def test_a_row_the_editor_skipped_and_a_title(ctx, fake):
    d2, d3 = pair()
    fake.on("lock", lambda kw: F.lock_reply(kw, rows=[], title="Ash shipped across the bay"), 2)
    got = sections.lock(ctx, 2, d2, d3, [(1, "One.")], [], "paired")
    assert got.rows == [{"id": "L01", "coverage": "thin", "note": "No verdict from the editor; judged by code from the markers."}]
    assert got.title == "Ash shipped across the bay"
    fake.on("lock", lambda kw: F.lock_reply(kw, title="A title that runs on for far too many words"), 3)
    assert sections.lock(ctx, 3, d3, F.section_text(4, [9]), [(1, "One."), (2, d2)], [], "paired").title is None
    fake.on("lock", lambda kw: F.lock_reply(kw, title="Cracks that close"), 4)
    assert sections.lock(ctx, 4, F.section_text(4, [9]), "Five. [D11]", [(1, "One."), (2, d2), (3, d3)], [], "paired").title is None


# --- the solo and the final lock ------------------------------------------------------------

def test_the_solo_lock_is_a_full_edit_without_the_look_ahead(ctx, fake):
    d3 = F.section_text(3, [7, 8])
    got = sections.lock(ctx, 3, d3, "A draft of four the solo lock must not read.", [(1, "One."), (2, "Two.")], NOTES, "solo")
    call, = fake.of("lock", 3)
    block = F.last_block(call)
    assert "You are locking section 3 on its own" in block and "Edit the pair" not in block
    assert '<draft n="4">' not in block and "must not read" not in all_text(call)
    assert F.tag(block, "draft", 'n="3"') == d3 and NOTES[1]["text"] in block
    assert [b["text"][:30] for b in F.blocks(call)[1:3]] == ['<section n="1" state="locked">', '<section n="2" state="locked">']
    assert call["output_config"]["format"]["schema"] == sections.SCHEMA  # one format for every mode
    assert '"next_text" is an empty string and "moved" an empty list' in block
    assert (got.mode, got.next_text, got.moved) == ("solo", None, [])
    assert got.text == F.without_filler(d3) and got.cut == [f"{F.FILLER} (throat-clearing)"] and got.problems == []
    assert F.offered(block) == [1, 2, 3, 4, 7, 8]


def test_a_solo_lock_that_loses_text_gets_the_fix_round(ctx, fake):
    d3 = F.section_text(3, [7, 8])
    fake.on("lock", lambda kw: F.lock_reply(kw, text="Section 3 in one line. [D07]", cut=[]))
    got = sections.lock(ctx, 3, d3, None, [(1, "One."), (2, "Two.")], [], "solo")
    assert "text was lost" in F.tag(F.last_block(fake.of("fix")[0]), "problems")
    assert got.problems and got.problems[0].startswith("text was lost")


def test_the_final_lock_reads_the_whole_chapter(ctx, fake):
    earlier = [(k, f"LOCKED SECTION {k}. " + F.without_filler(F.section_text(k, [1])), ) for k in range(1, 5)]
    d5 = F.section_text(5, [11, 2])
    got = sections.lock(ctx, 5, d5, None, earlier, NOTES, "final")
    call, = fake.of("lock", 5)
    content, block = F.blocks(call), F.last_block(call)
    assert len(content) == 6
    for (k, text), b in zip(earlier, content[1:5]):
        assert b["text"] == f'<section n="{k}" state="locked">\n{text}\n</section>'
    assert [i for i, b in enumerate(content) if b.get("cache_control")] == [4]
    assert "Section 5 is the last, and the whole chapter is above" in block and "It lands the through-line" in block
    assert "call back once to the opening's scene" in block and "Edit the pair" not in block
    assert call["output_config"]["format"]["schema"] == sections.SCHEMA
    assert (got.mode, got.next_text) == ("final", None) and got.rows[0]["id"] == "L04"
    assert ctx.r.read_log()[-1]["mode"] == "final"


def test_the_last_section_is_always_locked_as_the_ending(ctx, fake):
    earlier = [(k, f"Section {k}.") for k in range(1, 5)]
    assert sections.lock(ctx, 5, F.section_text(5, [11]), None, earlier, [], "solo").mode == "final"
    assert "Section 5 is the last" in F.last_block(fake.of("lock", 5)[0])
    with pytest.raises(StageError, match="not the last"):
        sections.lock(ctx, 3, "Three.", None, earlier[:2], [], "final")
    with pytest.raises(StageError, match="needs the draft of section 4"):
        sections.lock(ctx, 3, "Three.", None, earlier[:2], [], "paired")
    with pytest.raises(StageError, match="sections 1 to 2"):
        sections.lock(ctx, 3, "Three.", None, earlier[:1], [], "solo")
    with pytest.raises(StageError, match="Unknown lock mode"):
        sections.lock(ctx, 3, "Three.", None, earlier[:2], [], "rolling")


# --- apply ----------------------------------------------------------------------------------

def a_lock() -> Lock:
    return Lock(n=2, mode="paired", text="Two, final. [D05]", next_text="Three, revised. [D07]",
                rows=[{"id": "L01", "coverage": "thin", "note": "The quarry is there; the recipe is not."}],
                moved=[{"text": MOVED, "from": 3, "to": 2}], cut=["The harbour again. (repeats section 1)"],
                title="Ash across the bay",
                corrections=["Section 1 said the wall was poured in a year; D05 says it took longer. Now: When I "
                             "looked into this further, I found it took longer."],
                problems=[])


def test_apply_persists_a_lock(ctx):
    r = ctx.r
    sections.save_draft(r, 3, "Three, as first drafted. [D07]")
    lock = a_lock()
    sections.apply(ctx, lock)
    assert r.segment_path(2).read_text() == "Two, final. [D05]\n" and sections.locked_text(r, 2) == "Two, final. [D05]"
    assert json.loads((r.root / "fast/locks/seg02.json").read_text())["moved"] == lock.moved
    assert sections.saved_lock(r, 2) == lock
    assert (r.root / "06_segments/drafts/seg03.d2.md").read_text() == "Three, revised. [D07]\n"
    assert sections.latest_draft(r, 3) == "Three, revised. [D07]"
    led = json.loads(r.path("ledger").read_text())
    row = led["rows"][0]
    assert (row["id"], row["coverage"], row["segments"], row["note"]) == ("L01", "thin", [2], "The quarry is there; the recipe is not.")
    assert row["section"] == 2 and led["rows"][1]["coverage"] is None  # the others untouched, the tag kept
    assert ctx.led["rows"][0]["coverage"] == "thin"                     # the ledger the driver holds, in step
    assert "| L01 | 1 | deep-dive | The recipe | 2 | - | - | - | thin | 2 |" in r.path("ledger_md").read_text()
    said = decisions(r)
    assert "## fast lock seg02" in said and "Section 2 locked (paired edit, 2 words)." in said
    assert '- Moved from section 3 to section 2: "A core drilled from the harbour wall' in said
    assert "- Cut: The harbour again. (repeats section 1)" in said
    assert "- L01 thin: The quarry is there; the recipe is not." in said
    assert "- Correction: Section 1 said the wall was poured in a year" in said and "When I looked into this further" in said
    assert "- Marker title changed to: Ash across the bay" in said


def test_apply_twice_changes_nothing_and_a_locked_section_is_not_edited_again(ctx, fake):
    sections.save_draft(ctx.r, 3, "Three, as first drafted. [D07]")
    sections.apply(ctx, a_lock())
    before = {p: p.read_text() for p in ctx.r.root.rglob("*") if p.is_file()}
    sections.apply(ctx, a_lock())
    assert {p: p.read_text() for p in ctx.r.root.rglob("*") if p.is_file()} == before
    assert sections.lock(ctx, 2, "Two.", "Three.", [(1, "One.")], [], "paired") == a_lock() and fake.calls == []


def test_a_clean_lock_records_no_decision_and_an_unchanged_next_draft_no_version(ctx, fake):
    d2, d3 = pair()
    sections.save_draft(ctx.r, 3, d3)
    sections.apply(ctx, sections.lock(ctx, 2, d2, d3, [(1, "One.")], [], "paired"))
    assert decisions(ctx.r) == "" and sections.latest_draft(ctx.r, 3) == d3
    assert [p.name for p in base.path(ctx.r, "drafts").iterdir()] == ["seg03.d1.md"]
    assert json.loads(ctx.r.path("ledger").read_text())["rows"][0]["coverage"] == "covered"
    with pytest.raises(StageError, match="no text to lock"):
        sections.apply(ctx, Lock(n=3, mode="solo", text=" "))


def test_a_row_an_earlier_section_missed_is_carried_to_later_sections(ctx, fake):
    r = ctx.r
    sections.apply(ctx, Lock(n=2, mode="solo", text="Two.", rows=[
        {"id": "L01", "coverage": "missing", "note": "Its research had not landed; section 4 could take it up."}]))
    assert "- L01 missing: Its research had not landed" in decisions(r)
    earlier = [(1, "One.", True), (2, "Two.", True), (3, "Three.", False)]
    d4 = sections.draft(ctx, 4, earlier, [])
    block = F.last_block(fake.of("draft", 4)[0])
    rows = F.tag(block, "rows")
    assert "- L03: Self-healing" in rows
    assert "- L01: The recipe (section 2's row, left missing there: Its research had not landed; section 4 could take it up.)" in rows
    assert "  Research: landed; entries D05, D06" in rows
    assert F.offered(block) == [5, 6, 9, 10]  # the carried row's entries come with it
    # The lock of section 4 is asked about it too, and lands it.
    landed = d4 + "\n\nThe ash for it was quarried on the bay and shipped across the sea. [D05]"
    fake.on("lock", lambda kw: F.lock_reply(kw, text=landed))
    got = sections.lock(ctx, 4, d4, "Five. [D11]", [(1, "One."), (2, "Two."), (3, "Three.")], [], "paired")
    assert [v["id"] for v in got.rows] == ["L03", "L01"] and got.rows[1]["coverage"] == "covered"
    sections.apply(ctx, got)
    rows = {row["id"]: row for row in json.loads(r.path("ledger").read_text())["rows"]}
    assert (rows["L01"]["coverage"], rows["L01"]["segments"]) == ("covered", [4])
    # A carried row only ever improves: a later "missing" or "thin" does not undo it or claim the section.
    sections.apply(ctx, Lock(n=5, mode="final", text="Five.", rows=[{"id": "L01", "coverage": "thin", "note": "Not here."}]))
    row = json.loads(r.path("ledger").read_text())["rows"][0]
    assert (row["coverage"], row["segments"], row["note"]) == ("covered", [4], "Lands with its material.")


def test_the_ledger_on_disk_wins_over_the_one_in_memory(ctx, fake):
    """A listener's note can add a row while sections are being written (faststart/research.py note_row)."""
    led = json.loads(ctx.r.path("ledger").read_text())
    led["rows"].append({"id": "L05", "rank": 5, "idea": "Sea walls in Cork", "type": "missing", "episode_has": "",
                        "research_question": "Did Irish sea walls use lime?", "tier_needed": 2, "section": 3,
                        "from_note": True})
    ctx.r.path("ledger").write_text(json.dumps(led))
    sections.draft(ctx, 3, [(1, "One.", True), (2, "Two.", False)], [])
    rows = F.tag(F.last_block(fake.of("draft", 3)[0]), "rows")
    assert "- L02: Crystals" in rows and "- L05: Sea walls in Cork" in rows and "(no findings)" in rows


# --- regressions from the review --------------------------------------------------------------

def long_section(n=3):
    return (F.without_filler(F.section_text(n, [7, 8])) + "\n\n" + " ".join(
        f"Sentence {i} of the third section carries the story a little further along the harbour wall."
        for i in range(30)) + " [D07]")


def as_fix(kw, **changes):
    """A fix reply built like a lock reply, from the request without its fix block."""
    return F.lock_reply({"messages": [{"role": "user", "content": F.blocks(kw)[:-1]}]}, **changes)


def test_a_fix_that_comes_back_as_a_fragment_never_replaces_a_full_section(ctx, fake):
    """The fix round compared the two results by how many problems each had: a full section with
    two problems code repairs lost to a six-word reply with one, 'text was lost', and was narrated."""
    d3 = long_section()
    fake.on("lock", lambda kw: F.lock_reply(kw, text=d3 + " [D99] [laughs]", cut=[]))
    fake.on("fix", lambda kw: as_fix(kw, text="Section 3 in one line. [D07]", cut=[]))
    got = sections.lock(ctx, 3, d3, None, [(1, "One."), (2, "Two.")], [], "solo")
    assert len(fake.of("fix")) == 1 and got.text == d3
    assert "repaired by code: dropped the marker for D99 (not in the dossier)" in got.problems
    assert not any(checks.is_loss(p) for p in got.problems)


def test_the_opening_fix_as_a_fragment_never_replaces_the_edit(ctx, fake):
    draft = F.opening_text([1, 2])
    edited = F.without_filler(draft) + " [D99] [laughs]"
    fake.on("opening_edit", lambda kw: edited).on("fix", lambda kw: "A barge rides low. [D01]")
    assert sections.edit_opening(ctx, draft, []) == F.without_filler(draft)
    assert len(fake.of("fix")) == 1


def test_of_two_results_that_lost_text_the_one_that_kept_more_stands(ctx, fake):
    d3 = long_section()
    half = d3.split("Sentence 15")[0].strip() + " [D07]"
    fake.on("lock", lambda kw: F.lock_reply(kw, text="Section 3 in one line. [D07]", cut=[]))
    fake.on("fix", lambda kw: as_fix(kw, text=half + " [D99]", cut=[]))
    got = sections.lock(ctx, 3, d3, None, [(1, "One."), (2, "Two.")], [], "solo")
    assert got.text == half and any("text was lost" in p for p in got.problems)


def test_a_fix_that_leaves_only_what_code_repairs_is_kept(ctx, fake):
    """On a tie by count the fix used to be thrown away: the moved passage stayed in both sections."""
    d2, d3 = pair()
    new2, new3 = d2 + "\n\n" + MOVED, d3.replace("\n\n" + MOVED, "")
    move = [{"text": MOVED, "from": 3, "to": 2}]
    fake.on("lock", lambda kw: F.lock_reply(kw, text=new2, next_text="", moved=move))
    fake.on("fix", lambda kw: as_fix(kw, text=new2 + " [D99]", next_text=new3, moved=move))
    got = sections.lock(ctx, 2, d2, d3, [(1, "One.")], [], "paired")
    assert (got.text, got.next_text) == (new2, new3) and (got.text + got.next_text).count(MOVED) == 1
    assert got.problems == ["cites D99, which is not in the dossier",
                            "repaired by code: dropped the marker for D99 (not in the dossier)"]


def test_a_stop_inside_apply_neither_loses_nor_duplicates_a_moved_passage(ctx, fake, monkeypatch):
    """apply() saved the revised draft of n+1 before the lock record. Stopped in between, the next
    try paid for a second lock that paired the original draft of n with the revised draft of n+1:
    a passage moved from n+1 into n was then in neither."""
    r = ctx.r
    d2, d3 = pair()
    sections.save_draft(r, 2, d2), sections.save_draft(r, 3, d3)
    new2, new3 = d2 + "\n\n" + MOVED, d3.replace("\n\n" + MOVED, "")
    fake.on("lock", lambda kw: F.lock_reply(kw, text=new2, next_text=new3, moved=[{"text": MOVED, "from": 3, "to": 2}]))
    got = sections.lock(ctx, 2, d2, d3, [(1, "One.")], [], "paired")

    def stopped(*a, **k):
        raise KeyboardInterrupt("killed")
    with monkeypatch.context() as m:
        m.setattr(sections, "_write_coverage", stopped)
        with pytest.raises(KeyboardInterrupt):
            sections.apply(ctx, got)
    assert sections.locked_text(r, 2) is None            # not locked: the locked text is still written last
    fake.script.clear()                                  # a second editor would leave both drafts alone
    again = sections.lock(ctx, 2, sections.latest_draft(r, 2), sections.latest_draft(r, 3), [(1, "One.")], [], "paired")
    assert again == got and len(fake.of("lock")) == 1    # the same lock, not a second paid one
    sections.apply(ctx, again)
    assert sections.locked_text(r, 2) == new2 and sections.latest_draft(r, 3) == new3
    assert (sections.locked_text(r, 2) + sections.latest_draft(r, 3)).count(MOVED) == 1
    assert decisions(r).count("## fast lock seg02") == 1 and "- Moved from section 3 to section 2" in decisions(r)
    assert json.loads(r.path("ledger").read_text())["rows"][0]["coverage"] == "covered"
    assert [p.name for p in sorted(base.path(r, "drafts").iterdir())] == ["seg02.d1.md", "seg03.d1.md", "seg03.d2.md"]


def test_the_listeners_own_words_in_quotation_marks_cost_no_fix_call(ctx, fake):
    """Their words are in no dossier entry, so the quote check sent the whole opening round again."""
    draft = F.opening_text([1, 2])
    echo = F.without_filler(draft).replace("You pour concrete for a living,", 'You told me, "I pour concrete for a living,"')
    assert echo != F.without_filler(draft)
    fake.on("opening_edit", lambda kw: echo)
    assert sections.edit_opening(ctx, draft, []) == echo
    assert not fake.of("fix") and decisions(ctx.r) == "" and sections.saved_lock(ctx.r, 1).problems == []
    # In a lock too, from the seed and from a note; words nobody said still get the fix round.
    d2, d3 = pair()
    said = d2 + ' You said yours "cracks in thirty years," and that your "grandfather built sea walls in Cork."'
    fake.on("lock", lambda kw: F.lock_reply(kw, text=said), 2)
    got = sections.lock(ctx, 2, d2, d3, [(1, echo)], NOTES, "paired")
    assert got.text == said and got.problems == [] and not fake.of("fix")
    fake.on("lock", lambda kw: F.lock_reply(kw, text=d3 + ' You said, "nothing we pour today will outlive us."'), 3)
    got = sections.lock(ctx, 3, d3, None, [(1, echo), (2, said)], NOTES, "solo")
    assert len(fake.of("fix")) == 1 and '"' not in got.text


def test_an_over_long_opening_trimmed_to_its_target_is_not_a_loss(ctx, fake):
    """The fragment check compared the edit with the draft alone, so trimming a draft that ran far
    over cost a fix call whose only remedies were to put the trim back or 'declare it in cut'."""
    target = F.OUTLINE["sections"][0]["target_words"]
    body = lambda k: " ".join(f"Line {i} tells one more plain thing about the harbour and the men." for i in range(k))
    draft = F.opening_text([1, 2]) + "\n\n" + body(190)       # far over the target
    edited = F.without_filler(F.opening_text([1, 2])) + "\n\n" + body(70)
    assert sections.word_count(draft) > 2000 and sections.word_count(edited) < 0.6 * sections.word_count(draft)
    assert sections.word_count(edited) >= 0.6 * target
    fake.on("opening_edit", lambda kw: edited)
    assert sections.edit_opening(ctx, draft, []) == edited and not fake.of("fix")
    assert sections.saved_lock(ctx.r, 1).problems == []


def test_entries_that_answer_a_listeners_note_reach_the_draft_and_the_lock(ctx, fake, monkeypatch):
    """A question the dossier can answer gets no research, and the entry that answers it sat under
    another section's row, where no later writer or editor was offered it."""
    note = {"time": "t3", "section": 2, "kind": "question", "text": "Can you still see the ash in the wall today?"}
    answers = [{"time": "t3", "text": note["text"], "kind": "question", "entries": [6, 99]},
               {"time": "t4", "text": "A note nothing answers.", "kind": "unclear", "entries": []}]
    monkeypatch.setattr(sections.fast_research, "note_answers", lambda r: answers, raising=False)
    earlier = [(1, "One.", True), (2, "Two.", True), (3, "Three.", False)]
    d4 = sections.draft(ctx, 4, earlier, NOTES + [note])
    block = F.last_block(fake.of("draft", 4)[0])
    assert F.offered(block) == [9, 10] and F.offered(block, "other_entries") == [1, 2, 3, 4]
    told = F.tag(block, "note_entries")
    assert told.startswith("The listener (question): Can you still see the ash in the wall today?")
    assert F.offered(block, "note_entries") == [6] and "A note nothing answers." not in told
    got = sections.lock(ctx, 4, d4, "Five. [D11]", [(1, "One."), (2, "Two."), (3, "Three.")], NOTES + [note], "paired")
    block = F.last_block(fake.of("lock", 4)[0])
    assert F.offered(block, "note_entries") == [6] and note["text"] in F.tag(block, "note_entries")
    assert 6 not in F.offered(block) and got.problems == []
    # Nothing in the cached prefix, and nothing at all when no note has an answer on file.
    for c in fake.calls:
        assert "<note_entries>" not in json.dumps(c["system"]) and all("note_entries" not in b["text"] for b in F.blocks(c)[:-1])
    monkeypatch.setattr(sections.fast_research, "note_answers", lambda r: [], raising=False)
    sections.draft(ctx, 5, earlier[:2] + [(3, "Three.", True), (4, d4, False)], NOTES)
    assert F.tag(F.last_block(fake.of("draft", 5)[0]), "note_entries") == "(none)"
    monkeypatch.delattr(sections.fast_research, "note_answers")  # an older research module: no answers, no error
    assert sections._note_entries(ctx) == "(none)"


def test_the_prompts_carry_the_rules_the_review_found_missing(ctx):
    system = sections._system(ctx)
    assert "never a source of facts" in system and "Never assert what the outline expected" in system
    assert "A note is served once in the chapter" in system
    assert "without quotation marks" in system.split("10. Listener notes.")[1].split("11. Corrections.")[0]
    body = lambda name: (Path(sections.__file__).parent.parent / "prompts" / f"{name}.md").read_text().partition("\n---\n")[2]
    assert "without quotation marks" in body("fast_opening") and "take the marks off" in body("fast_opening_edit")
    assert "Where the research came back thin, write the section short" in body("fast_draft")
    lock = body("fast_lock")
    assert "as far as the entries bear it out" in lock and "may stay short" in lock
    assert "no section above has already" in lock and "from a draft above" in lock
    assert "removed from the draft of section {{next_n}}" in body("fast_lock_paired")
    fix = body("fast_fix")
    assert "there is no cut list" in fix and "took the marker off" in fix
    assert fix.index("removed the passage on purpose") < fix.index("Put it back")
