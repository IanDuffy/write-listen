"""The overnight writing chain: outline after research, write and edit with the paired edit, the
promise check (overnight/outline.py, write.py, promise.py). Offline: a fake Claude keyed by prompt."""

from __future__ import annotations

import copy
import json

import pytest
from overnight_write_fakes import ADDED, CARD, Killed, chapter, install, offline, outline, promise_reply  # noqa: F401

from faststart import sections
from overnight import outline as ov_outline
from overnight import promise, write
from stages import ledger
from stages.brief import parse_segments
from stages.common import StageError, dossier_entries, split_script_segments


def build(r, cfg):
    ov_outline.run(r, cfg)
    write.run(r, cfg)
    return promise.run(r, cfg)


def paired_order(n: int) -> list[tuple]:
    """outline, edit, draft 1, then draft k and lock k-1 for each k, the final lock, the promise check."""
    want = [("outline", None), ("outline_edit", None), ("draft", 1)]
    for k in range(2, n + 1):
        want += [("draft", k), ("lock", k - 1)]
    return want + [("lock", n), ("promise", None)]


# --- end to end -----------------------------------------------------------------------------

@pytest.mark.parametrize("n", [4, 5, 6])
def test_research_folder_to_final_script(tmp_path, cfg, monkeypatch, n):
    r, cfg = chapter(tmp_path, cfg)
    chain = install(monkeypatch, sections=n)
    final = build(r, cfg)

    assert chain.whats() == paired_order(n)
    assert final == r.path("script_final") and final.read_text() == r.path("script_draft").read_text()
    assert [k for k, _ in split_script_segments(final.read_text())] == list(range(1, n + 1))
    assert all(r.segment_path(k).exists() for k in range(1, n + 1))
    assert not (r.root / "fast.json").exists() and not (r.root / "fast_timeline.jsonl").exists()

    got = json.loads((r.root / "05_outline.json").read_text())
    assert got["title"] == CARD["title"] and got["promise"] == CARD["summary"]
    assert len(got["sections"]) == n
    segs = parse_segments(r.path("brief").read_text())
    assert [s["n"] for s in segs] == list(range(1, n + 1))
    assert all("**Purpose:**" in s["text"] for s in segs)
    led = json.loads(r.path("ledger").read_text())
    home = {rid: s["n"] for s in got["sections"] for rid in s["rows"]}
    assert {row["id"]: row["section"] for row in led["rows"]} == home
    assert all(s["entries"] for s in segs)

    record = json.loads((r.root / "08_promise.json").read_text())
    assert record["verdict"] == "kept" and record["revised"] == []
    # After the promise check the brief lists what each final section actually cites.
    final_segs = dict(split_script_segments(final.read_text()))
    for s in parse_segments(r.path("brief").read_text()):
        assert s["entries"] == sorted(set(dossier_entries_cited(final_segs[s["n"]])))
    assert "overnight write" in r.path("decisions").read_text()
    stages_logged = {rec["stage"] for rec in r.read_log()}
    want = {"overnight_outline", "overnight_outline_edit", "fast_draft", "fast_lock", "overnight_promise"}
    assert want <= stages_logged


def test_writing_calls_use_the_overnight_prompts(tmp_path, cfg, monkeypatch):
    r, cfg = chapter(tmp_path, cfg)
    chain = install(monkeypatch, sections=5)
    build(r, cfg)
    system = chain.of("draft", 2)[0]["system"][0]["text"]
    assert "made overnight and recorded" in system and "already listening" not in system
    assert "What the listener was promised: " + CARD["summary"].split(" The")[0] in system
    first = chain.of("draft", 1)[0]["messages"][-1]["content"][-1]["text"]
    assert "This is the first section" in first and "The listener may have just come back" not in first
    lock1 = chain.of("lock", 1)[0]["messages"][-1]["content"][-1]["text"]
    assert '<draft n="2">' in lock1  # section 1 is locked paired with draft 2, not edited alone
    assert "narrated the moment you return it" not in lock1
    # The editor sees the whole dossier; a draft is offered the entries the outline gives other sections too.
    assert set(dossier_entries(sections_tag(lock1, "entries"))) == set(range(1, 9))
    draft2 = chain.of("draft", 2)[0]["messages"][-1]["content"][-1]["text"]
    assert set(dossier_entries(sections_tag(draft2, "entries"))) == {1, 2, 3}  # its row L01's, and D03
    assert set(dossier_entries(sections_tag(draft2, "other_entries"))) == set(range(4, 9))
    # Draft 3 reads locked section 1 and the edited draft of section 2.
    blocks = [b["text"] for b in chain.of("draft", 3)[0]["messages"][-1]["content"][:-1]]
    assert blocks[0].startswith("The chapter so far") and 'state="locked"' in blocks[1] and 'state="draft"' in blocks[2]


def dossier_entries_cited(text: str) -> list[int]:
    from faststart.checks import cited
    return sorted(cited(text))


def sections_tag(text: str, name: str) -> str:
    import fast_sections_fakes as C
    return C.tag(text, name)


def test_a_second_run_pays_nothing(tmp_path, cfg, monkeypatch):
    r, cfg = chapter(tmp_path, cfg)
    install(monkeypatch)
    build(r, cfg)
    before = {p: p.read_bytes() for p in r.root.rglob("*") if p.is_file() and p.name != "log.jsonl"}
    chain = install(monkeypatch)
    build(r, cfg)
    assert chain.calls == []
    assert {p: p.read_bytes() for p in r.root.rglob("*") if p.is_file() and p.name != "log.jsonl"} == before


def test_a_kill_mid_write_resumes_without_repeating_a_call(tmp_path, cfg, monkeypatch):
    r, cfg = chapter(tmp_path, cfg)
    chain = install(monkeypatch)

    def kill(kw):
        raise Killed()

    chain.on("lock", 3, kill)
    ov_outline.run(r, cfg)
    with pytest.raises(Killed):
        write.run(r, cfg)
    assert r.segment_path(2).exists() and not r.segment_path(3).exists()
    assert not r.path("script_draft").exists()

    again = install(monkeypatch)
    write.run(r, cfg)
    promise.run(r, cfg)
    # The killed lock is asked again; nothing that had finished is.
    assert again.whats() == [("lock", 3), ("draft", 5), ("lock", 4), ("lock", 5), ("promise", None)]


def test_a_kill_after_the_outline_calls_pays_nothing_for_them(tmp_path, cfg, monkeypatch):
    r, cfg = chapter(tmp_path, cfg)
    install(monkeypatch)
    real = ov_outline._write_files
    monkeypatch.setattr(ov_outline, "_write_files", lambda *a: (_ for _ in ()).throw(Killed()))
    with pytest.raises(Killed):
        ov_outline.run(r, cfg)
    monkeypatch.setattr(ov_outline, "_write_files", real)
    chain = install(monkeypatch)
    ov_outline.run(r, cfg)
    assert chain.calls == [] and (r.root / "05_outline.json").exists()


# --- the outline ----------------------------------------------------------------------------

def check(o, r, cfg):
    led = ledger.normalize(json.loads(r.path("ledger").read_text()))
    return ov_outline.validate(o, led, set(range(1, 9)), cfg)


def full(o: dict) -> dict:
    return {"title": CARD["title"], "promise": CARD["summary"], **copy.deepcopy(o)}


def test_the_stock_outline_is_valid(tmp_path, cfg):
    r, cfg = chapter(tmp_path, cfg)
    for n in (4, 5, 6):
        assert check(full(outline(n)), r, cfg) == []


def test_every_row_is_landed_by_exactly_one_section(tmp_path, cfg):
    r, cfg = chapter(tmp_path, cfg)
    o = full(outline(5))
    o["sections"][3]["rows"] = []
    assert any("L03 is landed by no section" in e for e in check(o, r, cfg))
    o["sections"][3]["rows"] = ["L03", "L01"]
    assert any("L01 is listed by sections 2, 4" in e for e in check(o, r, cfg))
    o["sections"][3]["rows"] = ["L03", "L09"]
    assert any("L09, which is not a ledger row" in e for e in check(o, r, cfg))


def test_entries_must_exist_and_every_section_cites_one(tmp_path, cfg):
    r, cfg = chapter(tmp_path, cfg)
    o = full(outline(5))
    o["sections"][1]["entries"] = [3, 12]
    assert any("D12, which is not an entry in the dossier" in e for e in check(o, r, cfg))
    o["sections"][1]["entries"] = []
    assert any("Section 2 lists no dossier entries" in e for e in check(o, r, cfg))


def test_shape_rules(tmp_path, cfg):
    r, cfg = chapter(tmp_path, cfg)
    o = full(outline(5))
    o["sections"][2]["title"] = "A title that runs to far too many words"
    o["sections"][4]["hand_off"] = ""
    o["through_line"] = " "
    errors = check(o, r, cfg)
    assert any("Section 3's title is 9 words" in e for e in errors)
    assert any("Section 5 does not say how it hands off" in e for e in errors)
    assert any("through_line is empty" in e for e in errors)
    three = full(outline(4))
    three["sections"] = three["sections"][:3]
    assert any("3 sections; it needs 4 to 6" in e for e in check(three, r, cfg))


def test_word_targets_are_checked_and_mended_by_code(tmp_path, cfg):
    r, cfg = chapter(tmp_path, cfg)
    o = full(outline(5))
    o["sections"][0]["target_words"] = 300
    o["sections"][1]["target_words"] = 200
    errors = check(o, r, cfg)
    assert any("the opening needs at least 450 words" in e for e in errors)
    assert any("no section may be under 300 words" in e for e in errors)
    assert any("add up to" in e for e in errors)
    mended = ov_outline.repair_targets(o, cfg)
    assert check(mended, r, cfg) == []
    assert 2700 <= sum(s["target_words"] for s in mended["sections"]) <= 3300


def test_an_edit_wrong_only_in_its_arithmetic_is_scaled_without_a_call(tmp_path, cfg, monkeypatch):
    r, cfg = chapter(tmp_path, cfg)
    chain = install(monkeypatch)
    long = outline(5, words=5000)
    chain.on("outline_edit", None, lambda kw: json.dumps({"changes": ["Longer."], "outline": long}))
    got = ov_outline.run(r, cfg)
    assert [k for k, _ in chain.whats()] == ["outline", "outline_edit"]
    assert 2700 <= sum(s["target_words"] for s in got["sections"]) <= 3300
    assert "scaled into range by code" in r.path("decisions").read_text()


def test_an_edit_that_breaks_a_rule_gets_one_repair_call(tmp_path, cfg, monkeypatch):
    r, cfg = chapter(tmp_path, cfg)
    chain = install(monkeypatch)
    bad = outline(5)
    bad["sections"][4]["rows"] = []  # L04 landed nowhere
    chain.on("outline_edit", None, lambda kw: json.dumps({"changes": ["Dropped L04."], "outline": bad}))
    got = ov_outline.run(r, cfg)
    assert [k for k, _ in chain.whats()] == ["outline", "outline_edit", "outline_repair"]
    assert "Ledger row L04 is landed by no section" in chain.of("outline_repair")[0]["messages"][0]["content"]
    assert got["sections"][4]["rows"] == ["L04"]
    assert json.loads(r.path("ledger").read_text())["rows"][3]["section"] == 5


def test_when_edit_and_repair_fail_the_sound_first_outline_stands(tmp_path, cfg, monkeypatch):
    r, cfg = chapter(tmp_path, cfg)
    chain = install(monkeypatch)
    bad = outline(5)
    bad["sections"][1]["entries"] = [99]
    chain.on("outline_edit", None, lambda kw: json.dumps({"changes": ["x"], "outline": bad}))
    chain.on("outline_repair", None, lambda kw: json.dumps(bad))
    got = ov_outline.run(r, cfg)
    assert got["sections"][1]["entries"] == [3]
    assert "the first outline stands unedited" in r.path("decisions").read_text()


def test_an_outline_that_cannot_be_mended_stops_and_is_asked_again(tmp_path, cfg, monkeypatch):
    r, cfg = chapter(tmp_path, cfg)
    chain = install(monkeypatch)
    bad = outline(5)
    bad["sections"][1]["entries"] = [99]
    chain.outline = bad  # first, edit and repair all bad
    with pytest.raises(StageError, match="still invalid"):
        ov_outline.run(r, cfg)
    assert (r.root / "_partial_overnight_outline.json").exists()
    assert not (r.root / "work" / "outline_edit.json").exists() and (r.root / "work" / "outline_first.json").exists()


def test_the_outline_prompt_carries_the_promise_and_the_research(tmp_path, cfg, monkeypatch):
    r, cfg = chapter(tmp_path, cfg)
    chain = install(monkeypatch)
    ov_outline.run(r, cfg)
    text = chain.of("outline")[0]["messages"][0]["content"]
    assert CARD["summary"] in text and CARD["source"]["words"] in text
    assert "[D08] (L04, claim, tier 2)" in text
    assert "L03 (rank 3" in text and "came back partial" in text and "no tier 1 source" in text
    assert "Parked" in text and "Roman roads" in text


# --- the dossier adapter --------------------------------------------------------------------

def test_the_dossier_adapter(tmp_path, cfg):
    r, cfg = chapter(tmp_path, cfg)
    d = write.Dossier(r)
    assert d.numbers_of("L02") == [4, 5] and d.numbers_of("L00") == list(range(1, 9))
    assert set(d.for_rows(["L03"])) == {6, 7} and set(d.for_rows(["L00", "L03"])) == set(range(1, 9))
    led = {row["id"]: row for row in json.loads(r.path("ledger").read_text())["rows"]}
    assert d.thin(led["L01"]) is None
    assert d.thin(led["L03"]) == "no tier 1 source for the healing rate"
    assert d.thin({**led["L04"], "gap": ""}) == "its research came back open"
    assert d.thin({"id": "L09", "status": "filled"}) == "no findings"


# --- the promise ----------------------------------------------------------------------------

def test_a_drifted_chapter_gets_one_revision_per_named_section(tmp_path, cfg, monkeypatch):
    r, cfg = chapter(tmp_path, cfg)
    chain = install(monkeypatch)
    chain.promise = promise_reply(keeps=False, fixes={1: "Tie the opening to the listener's pours.",
                                                      5: "End on their own wall."})
    build(r, cfg)
    assert [k for k in chain.whats() if k[0] in ("promise", "revise")] == [("promise", None), ("revise", 1),
                                                                          ("revise", 5)]
    final = dict(split_script_segments(r.path("script_final").read_text()))
    draft = dict(split_script_segments(r.path("script_draft").read_text()))
    assert ADDED in final[1] and ADDED in final[5]
    assert final[2] == draft[2] and final[3] == draft[3] and final[4] == draft[4]
    record = json.loads((r.root / "08_promise.json").read_text())
    assert record["verdict"] == "drifted" and record["revised"] == [1, 5]
    assert record["drift"] and record["fixes"]["1"].startswith("Tie the opening")
    rev = chain.of("revise", 1)[0]
    assert "made overnight and recorded" in rev["system"][0]["text"]  # the writing rules hold for a revision
    assert CARD["summary"] in rev["messages"][0]["content"]
    assert "Not checked again" in r.path("decisions").read_text()


def test_a_revision_that_lost_the_section_is_dropped(tmp_path, cfg, monkeypatch):
    r, cfg = chapter(tmp_path, cfg)
    chain = install(monkeypatch)
    chain.promise = promise_reply(keeps=False, fixes={2: "Say what the recipe has to do with the listener."})
    chain.on("revise", 2, lambda kw: "Too short.")
    build(r, cfg)
    assert r.path("script_final").read_text() == r.path("script_draft").read_text()
    record = json.loads((r.root / "08_promise.json").read_text())
    assert record["revised"] == [] and "stands as it was" in record["problems"]["2"][0]


def test_a_revision_with_a_bad_marker_is_repaired_by_code(tmp_path, cfg, monkeypatch):
    r, cfg = chapter(tmp_path, cfg)
    chain = install(monkeypatch)
    chain.promise = promise_reply(keeps=False, fixes={3: "Bring the listener in."})

    def bad(kw):
        old = sections_tag(kw["messages"][-1]["content"], "section")
        return old + "\n\nAnd a sentence that cites an entry nobody gathered. [D42]"

    chain.on("revise", 3, bad)
    build(r, cfg)
    final = dict(split_script_segments(r.path("script_final").read_text()))
    assert "[D42]" not in final[3] and "nobody gathered" in final[3]
    record = json.loads((r.root / "08_promise.json").read_text())
    assert record["revised"] == [3] and any("repaired by code" in p for p in record["problems"]["3"])


def test_drift_notes_are_ignored_when_the_chapter_keeps_its_promise(tmp_path, cfg, monkeypatch):
    r, cfg = chapter(tmp_path, cfg)
    chain = install(monkeypatch)
    chain.promise = promise_reply(keeps=True, fixes={2: "Could be warmer."})
    build(r, cfg)
    assert not chain.of("revise")
    assert r.path("script_final").read_text() == r.path("script_draft").read_text()


def test_a_kill_after_the_promise_check_does_not_pay_for_it_again(tmp_path, cfg, monkeypatch):
    r, cfg = chapter(tmp_path, cfg)
    chain = install(monkeypatch)
    chain.promise = promise_reply(keeps=False, fixes={1: "Tie it to them.", 4: "And here."})

    def kill(kw):
        raise Killed()

    chain.on("revise", 4, kill)
    ov_outline.run(r, cfg)
    write.run(r, cfg)
    with pytest.raises(Killed):
        promise.run(r, cfg)
    assert not r.path("script_final").exists() and not (r.root / "08_promise.json").exists()
    again = install(monkeypatch)
    again.promise = chain.promise
    promise.run(r, cfg)
    assert again.whats() == [("revise", 4)]
    assert json.loads((r.root / "08_promise.json").read_text())["revised"] == [1, 4]


def test_the_promise_stage_needs_the_card(tmp_path, cfg, monkeypatch):
    r, cfg = chapter(tmp_path, cfg)
    install(monkeypatch)
    ov_outline.run(r, cfg)
    write.run(r, cfg)
    (r.root / "00_source" / "up_next.json").write_text(json.dumps({**CARD, "summary": ""}))
    with pytest.raises(StageError, match="no summary"):
        promise.run(r, cfg)


def test_fast_section_one_still_drafts_with_the_middle_note(tmp_path, cfg, monkeypatch):
    """Backward compatibility: a Ctx without prompt overrides drafts as before."""
    r, cfg = chapter(tmp_path, cfg)
    chain = install(monkeypatch)
    ov_outline.run(r, cfg)
    ctx = write.context(r, cfg)
    ctx.prompts = {}
    sections.draft(ctx, 1, [], [])
    text = chain.of("draft", 1)[0]["messages"][-1]["content"][-1]["text"]
    assert "Pick up the thread from where the previous section ended" in text
    assert "The scout's other entries" in text


def test_the_brief_from_the_outline_and_again_from_the_final_script(tmp_path, cfg, monkeypatch):
    r, cfg = chapter(tmp_path, cfg)
    chain = install(monkeypatch)
    got = ov_outline.run(r, cfg)
    segs = parse_segments(r.path("brief").read_text())
    # Before writing: the entries the outline plans for a section and its rows' own (section 3 lands L02).
    assert segs[0]["entries"] == [1, 2] and segs[2]["entries"] == [4, 5] and segs[2]["rows"] == ["L02"]
    assert segs[2]["title"] == got["sections"][2]["title"]
    import fast_sections_fakes as C
    chain.on("lock", 3, lambda kw: C.lock_reply(kw, title="Stone that keeps growing"))
    write.run(r, cfg)
    promise.run(r, cfg)
    assert parse_segments(r.path("brief").read_text())[2]["title"] == "Stone that keeps growing"


def test_notes_added_while_research_ran_reach_every_draft_and_lock(tmp_path, cfg, monkeypatch):
    """R11: a 'tonight' note (overnight/live.add_note -> 00_source/notes.md) reached only the outline; the
    writer and the editor got NOTES = []. Now each draft and lock gets it, as faststart's listener notes."""
    r, cfg = chapter(tmp_path, cfg)
    r.path("notes").write_text("## Added during the conversation, after the chapter was started\n\n"
                               "- Don't spend long on the history\n- Mention the harbour at Caesarea\n")
    install(monkeypatch, sections=4)
    seen = []
    real_draft, real_lock = sections.draft, sections.lock
    monkeypatch.setattr(sections, "draft", lambda ctx, n, earlier, notes:
                        seen.append(("draft", n, notes)) or real_draft(ctx, n, earlier, notes))
    monkeypatch.setattr(sections, "lock", lambda ctx, n, text, nxt, locked, notes, mode:
                        seen.append(("lock", n, notes)) or real_lock(ctx, n, text, nxt, locked, notes, mode))
    ov_outline.run(r, cfg)
    write.run(r, cfg)
    assert len(seen) == 8
    for _, _, notes in seen:
        assert [(x["section"], x["text"]) for x in notes] == [(0, "Don't spend long on the history"),
                                                             (0, "Mention the harbour at Caesarea")]
    assert "- (note, before the chapter began) Don't spend long on the history" in \
        sections.listener_notes.as_prompt(seen[0][2])
    assert "Mention the harbour at Caesarea" in write.said(r, {})
    assert write.listener_notes(r.__class__(name="x", root=tmp_path / "none")) == []
