"""Fast Start step 2: the outline validator (acceptance check 1) and the outline stage (faststart/outline.py)."""

import json
import re

import pytest
from fakes import FakeAnthropic, Responder, all_text
from fast_outline_fakes import (EDIT_PHRASE, OUTLINE_PHRASE, REPAIR_PHRASE, SAID, TOPIC, AreaA, clone,
                                dossiers, file_scout, make_dossier, offline, outline as an_outline, seed_run)  # noqa: F401

from faststart import base, notes, outline
from stages import ledger, llm, research
from stages.brief import parse_segments
from stages.common import StageError, load_prompt

SCOUT = {1, 2, 3, 4, 5, 6}


def sized(words: list[int]) -> dict:
    """A valid outline with these word targets (one research question in each later section)."""
    o = an_outline(5 if len(words) == 5 else 4 if len(words) == 4 else 3)
    for s, w in zip(o["sections"], words):
        s["target_words"] = w
    return o


def errors(o, cfg, scout=SCOUT) -> str:
    return "\n".join(outline.validate(o, scout, cfg))


# --- the validator: acceptance check 1 --------------------------------------------------------

@pytest.mark.parametrize("n", [3, 4, 5])
def test_a_sound_outline_of_three_to_five_sections_is_valid(cfg, n):
    assert outline.validate(an_outline(n), SCOUT, cfg) == []


def test_two_sections_or_six_are_refused(cfg):
    two = an_outline(3)
    two["sections"] = two["sections"][:2]
    two["sections"][1]["target_words"] = 2100
    assert errors(two, cfg) == "The outline has 2 sections; it needs 3 to 5."
    six = an_outline(5)
    extra = clone(six["sections"][-1])
    extra["n"] = 6
    six["sections"].append(extra)
    for s in six["sections"][1:]:
        s["target_words"] = 450
    assert errors(six, cfg) == "The outline has 6 sections; it needs 3 to 5."


def test_the_opening_is_at_least_six_minutes(cfg):
    assert outline.validate(sized([900, 700, 700, 700]), SCOUT, cfg) == []  # exactly 6 minutes at 150 a minute
    assert errors(sized([899, 701, 700, 700]), cfg) == (
        "Section 1 (the opening) is 899 words, 5.99 minutes; the opening needs at least 900 words (6 minutes).")
    assert "the opening needs at least 900 words" in errors(sized([450, 850, 850, 850]), cfg)
    assert outline.validate(sized([1500, 500, 500, 500]), SCOUT, cfg) == []


def test_no_section_is_under_three_minutes(cfg):
    assert outline.validate(sized([1200, 450, 675, 675]), SCOUT, cfg) == []  # exactly 3 minutes
    assert errors(sized([1200, 449, 676, 675]), cfg) == (
        "Section 2 is 449 words, 2.99 minutes; no section may be under 450 words (3 minutes).")
    assert outline.validate(sized([900, 450, 450, 450, 450]), SCOUT, cfg) == []  # five sections, all at the floor
    assert "Section 5 is 300 words, 2 minutes" in errors(sized([900, 600, 600, 600, 300]), cfg)


def test_the_total_is_between_18_and_24_minutes(cfg):
    assert outline.validate(sized([900, 600, 600, 600]), SCOUT, cfg) == []  # exactly 18 minutes
    assert errors(sized([900, 600, 600, 599]), cfg) == (
        "The sections add up to 2699 words, 17.99 minutes; the chapter must be between 2700 and 3600 words "
        "(18 to 24 minutes).")
    assert outline.validate(sized([900, 900, 900, 900]), SCOUT, cfg) == []  # exactly 24 minutes
    assert "add up to 3601 words, 24.01 minutes" in errors(sized([900, 900, 900, 901]), cfg)
    assert "add up to 5000 words, 33.33 minutes" in errors(sized([2000, 1000, 1000, 1000]), cfg)


def test_the_limits_come_from_config(cfg):
    cfg["fast"].update(opening_min_minutes=4, section_min_minutes=2, total_minutes=[10, 12], sections=[2, 3])
    o = an_outline(3)
    for s, w in zip(o["sections"], [600, 600, 300]):
        s["target_words"] = w
    assert outline.validate(o, SCOUT, cfg) == []
    o["sections"][2]["target_words"] = 299
    assert "no section may be under 300 words (2 minutes)" in errors(o, cfg)
    assert outline.limits(cfg)["min_words"] == 1500 and outline.limits(cfg)["max_words"] == 1800


@pytest.mark.parametrize("field,what", [("lands", "what it lands"), ("sets_up", "what it sets up"),
                                         ("hand_off", "how it hands off")])
@pytest.mark.parametrize("n", [1, 3, 4])
@pytest.mark.parametrize("empty", ["", "   ", None])
def test_every_section_has_its_lands_sets_up_and_hand_off(cfg, field, what, n, empty):
    o = an_outline(4)
    o["sections"][n - 1][field] = empty
    got = outline.validate(o, SCOUT, cfg)
    assert len(got) == 1 and got[0].startswith(f"Section {n}") and f"does not say {what} ({field} is empty)" in got[0]


def test_the_opening_cites_only_scout_entries(cfg):
    o = an_outline(4)
    o["sections"][0]["scout_entries"] = [1, 6]
    assert outline.validate(o, SCOUT, cfg) == []
    o["sections"][0]["scout_entries"] = [1, 7, 12]
    assert errors(o, cfg) == ("Section 1 (the opening) lists D07, D12, which are not scout entries; "
                              "the opening may cite only scout entries.")
    o["sections"][0]["scout_entries"] = []
    assert errors(o, cfg) == "Section 1 (the opening) cites no scout entry; every fact in it must come from one."
    o["sections"][0]["scout_entries"] = [2]
    assert "D02, which is not a scout entry" in errors(o, cfg, scout={1})  # the scout's numbers are what count
    o["sections"][0]["scout_entries"] = "D01"
    assert "scout_entries must be a list of entry numbers" in errors(o, cfg)


def test_a_later_section_may_list_scout_entries_but_no_others(cfg):
    o = an_outline(4)
    o["sections"][2]["scout_entries"] = [4, 5]
    assert outline.validate(o, SCOUT, cfg) == []
    o["sections"][2]["scout_entries"] = [9]
    assert errors(o, cfg) == ("Section 3 lists D09, which is not a scout entry; no other entries exist until the "
                              "research is back.")


def test_the_opening_has_no_research_questions(cfg):
    o = an_outline(4)
    o["sections"][0]["research_questions"] = [clone(o["sections"][1]["research_questions"][0])]
    assert errors(o, cfg) == ("Section 1 (the opening) has 1 research question(s); it is written from the scout "
                              "alone and may have none.")


def test_later_sections_have_one_or_two_research_questions(cfg):
    o = an_outline(4)
    assert [len(s["research_questions"]) for s in o["sections"]] == [0, 2, 1, 1]
    q = o["sections"][1]["research_questions"][0]
    o["sections"][2]["research_questions"] = []
    assert errors(o, cfg) == "Section 3 has 0 research questions; sections after the opening need 1 or 2."
    o["sections"][2]["research_questions"] = [clone(q), clone(q), clone(q)]
    assert errors(o, cfg) == "Section 3 has 3 research questions; sections after the opening need 1 or 2."
    o["sections"][2]["research_questions"] = [clone(q), clone(q)]
    assert outline.validate(o, SCOUT, cfg) == []


def test_research_questions_fit_the_search_budget(cfg):
    assert outline.limits(cfg)["max_questions"] == 6  # 30 searches for the run, 5 a row
    o = an_outline(5)
    q = o["sections"][1]["research_questions"][0]
    for s in o["sections"][1:3]:
        s["research_questions"] = [clone(q), clone(q)]
    assert sum(len(s["research_questions"]) for s in o["sections"]) == 6 and outline.validate(o, SCOUT, cfg) == []
    o["sections"][3]["research_questions"] = [clone(q), clone(q)]
    assert errors(o, cfg) == "The outline has 7 research questions; the search budget serves at most 6."
    cfg["fast"]["research"]["max_searches"] = 19
    assert "the search budget serves at most 3" in errors(an_outline(4), cfg)


def test_a_research_question_is_a_whole_ledger_row(cfg):
    o = an_outline(4)
    q = o["sections"][1]["research_questions"][0]
    q.update(idea=" ", question="", type="wide", tier_needed=5)
    assert outline.validate(o, SCOUT, cfg) == [
        "Section 2, research question 1: idea is empty.", "Section 2, research question 1: question is empty.",
        "Section 2, research question 1: type must be one of deep-dive, missing, covered.",
        "Section 2, research question 1: tier_needed must be 1, 2, 3 or 4."]


def test_sections_are_numbered_in_order(cfg):
    o = an_outline(4)
    o["sections"][2]["n"], o["sections"][3]["n"] = 4, 3
    assert errors(o, cfg) == "The sections must be numbered 1 to 4 in order; they are numbered [1, 2, 4, 3]."
    o = an_outline(3)
    for s in o["sections"]:
        s["n"] -= 1
    assert "numbered [0, 1, 2]" in errors(o, cfg)


def test_titles_are_short(cfg):
    o = an_outline(4)
    o["sections"][1]["title"] = "One two three four five six"
    assert outline.validate(o, SCOUT, cfg) == []
    o["sections"][1]["title"] = "One two three four five six seven"
    assert errors(o, cfg) == ("Section 2's title is 7 words (\"One two three four five six seven\"); a marker title "
                              "is at most 6.")
    o["sections"][1]["title"] = " "
    assert errors(o, cfg) == "Section 2 has no title."


def test_the_outline_names_its_angle_and_arc(cfg):
    for field in ("title", "angle", "through_line", "closing_idea"):
        o = an_outline(4)
        o[field] = ""
        assert errors(o, cfg) == f"The outline's {field} is empty."
    assert outline.validate([], SCOUT, cfg) == ["The outline is not a JSON object."]
    assert "The outline has no list of sections." in outline.validate({"title": "x"}, SCOUT, cfg)
    o = an_outline(4)
    o["sections"][1]["target_words"] = "700"
    assert errors(o, cfg) == "Section 2's target_words must be a whole number of words."


def test_every_broken_rule_is_reported_at_once(cfg):
    o = an_outline(4)
    o["sections"][0].update(target_words=600, scout_entries=[9], hand_off="")
    o["sections"][3]["research_questions"] = []
    got = outline.validate(o, SCOUT, cfg)
    assert len(got) == 4 and all(isinstance(e, str) for e in got)


def test_the_schema_is_one_structured_outputs_accept():
    def walk(node):
        if isinstance(node, dict):
            assert not {"minimum", "maximum", "minItems", "maxItems", "minLength", "maxLength"} & set(node)
            if node.get("type") == "object":
                assert node["additionalProperties"] is False and set(node["required"]) == set(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(outline.SCHEMA)
    walk(outline.EDIT_SCHEMA)
    assert outline.EDIT_SCHEMA["properties"]["outline"] is outline.SCHEMA
    assert set(an_outline(4)) == set(outline.SCHEMA["properties"])
    section = outline.SCHEMA["properties"]["sections"]["items"]
    assert set(an_outline(4)["sections"][1]) == set(section["properties"])
    assert set(an_outline(4)["sections"][1]["research_questions"][0]) == set(
        section["properties"]["research_questions"]["items"]["properties"])


# --- word targets mended by code --------------------------------------------------------------

@pytest.mark.parametrize("given,want", [
    ([600, 300, 300, 300], [1200, 600, 600, 600]),          # half the length: brought to 20 minutes, in proportion
    ([2000, 2000, 2000], [1000, 1000, 1000]),               # twice the length
    ([800, 760, 720, 720], [900, 720, 680, 680]),           # the opening lifted, the rest give in proportion
    ([500, 500, 500, 500, 500], [900, 520, 520, 520, 520]),
    ([905, 455, 455, 885], [900, 450, 450, 900]),           # whole tens, without slipping under 18 minutes
    ([0, 0, 0, 0], [1200, 600, 600, 600]),                  # no numbers at all: in proportion to the floors
    ([900, 700, 700, 700], [900, 700, 700, 700]),           # already sound: untouched
])
def test_repair_targets_scales_the_words_into_range(cfg, given, want):
    o = sized(given)
    fixed = outline.repair_targets(o, cfg)
    assert [s["target_words"] for s in fixed["sections"]] == want
    assert outline.validate(fixed, SCOUT, cfg) == []
    assert [s["target_words"] for s in o["sections"]] == given  # a copy: the outline it was given is as it was
    for a, b in zip(o["sections"], fixed["sections"]):
        assert {k: v for k, v in a.items() if k != "target_words"} == {k: v for k, v in b.items() if k != "target_words"}


def test_repair_targets_mends_nothing_but_the_words(cfg):
    o = sized([600, 300, 300, 300])
    o["sections"][0]["hand_off"] = ""
    o["sections"][1]["target_words"] = None
    fixed = outline.repair_targets(o, cfg)
    assert outline.validate(fixed, SCOUT, cfg) == ["Section 1 (the opening) does not say how it hands off "
                                                   "(hand_off is empty)."]
    assert outline.repair_targets({"sections": []}, cfg) == {"sections": []}


# --- the stage, end to end against the fake model ----------------------------------------------

@pytest.fixture
def fake(monkeypatch):
    a = AreaA()
    monkeypatch.setattr(llm, "_client", FakeAnthropic(a))
    return a


@pytest.fixture(params=dossiers())
def scouted(request, tmp_path, cfg):
    """A seed run whose scout is filed: (run, dossier)."""
    r = seed_run(tmp_path, cfg)
    d = make_dossier(request.param, r, cfg)
    assert file_scout(r, cfg, d) == [1, 2, 3, 4, 5, 6]
    return r, d


def test_outline_run_writes_edits_and_files_the_outline(scouted, cfg, fake):
    r, d = scouted
    first = an_outline(4)
    edited = clone(first)
    edited["sections"][1]["title"] = "Ash, lime and seawater"
    edited["sections"][1]["lands"] = "  The lime lumps were the method,\n not a flaw. "
    fake.script.first, fake.script.edited = first, edited
    notes.add(r.root, "My grandfather poured the sea wall at Whitby.", "connection")

    got = outline.run(r, cfg, d)
    want = clone(edited)
    want["sections"][1]["lands"] = "The lime lumps were the method, not a flaw."  # stray whitespace is all code touches
    assert got == want and outline.load(r) == want

    # two calls, the second in a fresh context
    write, edit = fake.calls
    assert [rec["stage"] for rec in r.read_log()] == ["fast_outline", "fast_outline_edit"]
    assert all(rec["model"] == "claude-opus-5-5" for rec in r.read_log())
    assert write["output_config"] == {"effort": "medium", "format": {"type": "json_schema", "schema": outline.SCHEMA}}
    assert edit["output_config"]["format"] == {"type": "json_schema", "schema": outline.EDIT_SCHEMA}
    asked = all_text(write)
    assert OUTLINE_PHRASE in asked and "{{" not in asked
    assert TOPIC in asked and SAID in asked  # the seed, with the listener's own words
    assert "My grandfather poured the sea wall at Whitby." in asked  # what they have said since
    assert "[D03] (L00, quote, tier 1, verbatim: yes)" in asked and "Entries: D01, D02, D03" in asked  # the scout
    assert "The entries are D01, D02, D03, D04, D05, D06; no others exist yet." in asked
    assert "3 to 5 sections" in asked and "never less than 2700 or more than 3600" in asked
    assert "at least 900 words (6 minutes)" in asked and "under 450 words (3 minutes)" in asked
    assert "at most 6 in all" in asked and "5 searches and 2 page reads" in asked
    assert "episode" not in asked and "podcast" not in asked

    assert len(edit["messages"]) == 1 and edit["messages"][0]["role"] == "user"  # no earlier turns
    reviewed = all_text(edit)
    assert EDIT_PHRASE in reviewed and "{{" not in reviewed
    assert OUTLINE_PHRASE not in reviewed and asked not in reviewed  # none of the first call's conversation
    assert json.dumps(first, ensure_ascii=False, indent=1) in reviewed  # only its outline
    assert "<problems>\nNone.\n</problems>" in reviewed
    assert TOPIC in reviewed and "[D03] (L00, quote" in reviewed and "My grandfather" in reviewed

    # the files
    assert json.loads((r.root / "05_outline.json").read_text()) == want
    md = (r.root / "05_outline.md").read_text()
    assert md.startswith("# Outline: The harbor that heals itself\n")
    assert "### 2. Ash, lime and seawater (700 words)" in md and "- Hands off: The question section 2 leaves open." in md
    assert "- Scout entries: D01, D02, D03" in md and "- ours cracks in thirty years" in md
    assert "- Research (missing, tier 1): Pliny saw the same thing and said so." in md
    angle = r.path("angle").read_text()
    assert angle.splitlines()[0] == "# Angle: The harbor that heals itself"
    assert angle.splitlines()[2] == want["angle"] and want["why"] in angle and want["through_line"] in angle

    led = json.loads(r.path("ledger").read_text())
    assert ledger.validate(led) == [f"L0{k} has fields the pipeline does not use: section" for k in (1, 2, 3, 4)]
    rows = ledger.active(ledger.normalize(led))
    assert [(x["id"], x["rank"], x["section"]) for x in rows] == [("L01", 1, 2), ("L02", 2, 2), ("L03", 3, 3),
                                                                  ("L04", 4, 4)]
    q = want["sections"][1]["research_questions"][1]
    assert {k: rows[1][k] for k in ledger.PLAN_FIELDS} == {
        "id": "L02", "rank": 2, "idea": q["idea"], "type": "missing", "episode_has": "",
        "research_question": q["question"], "tier_needed": 1}
    assert rows[0]["episode_has"] == "D05 names the lime lumps." and rows[0]["status"] is None
    assert led["source"] == "seed" and led["run"] == r.name and led["parked"] == [] and led["connections"] == []
    assert "| L03 | 3 | deep-dive |" in r.path("ledger_md").read_text()
    assert [x["id"] for x in outline.rows_for(led, 2)] == ["L01", "L02"] and outline.rows_for(led, 1) == []
    assert outline.section(want, 4)["title"] == "What we lost"
    with pytest.raises(StageError, match="no section 9"):
        outline.section(want, 9)

    brief = r.path("brief").read_text()
    assert brief.splitlines()[0] == "# Brief: The harbor that heals itself"
    segs = parse_segments(brief)
    assert [(s["n"], s["title"], s["target_words"], s["rows"], s["entries"]) for s in segs] == [
        (1, "Forms in the sea", 900, [], [1, 2, 3]), (2, "Ash, lime and seawater", 700, ["L01", "L02"], [5]),
        (3, "The lumps that heal", 700, ["L03"], []), (4, "What we lost", 700, ["L04"], [])]
    assert re.search(r"^## Through-line\s*\n\s*\nWhat would it mean to build something that mends\?$", brief, re.M)

    decisions = r.path("decisions").read_text()
    assert re.findall(r"^## (.+?) \(", decisions, re.M) == ["angle", "outline", "outline edit", "ledger"]
    assert 'Chose "The harbor that heals itself".' in decisions and "Why: It answers the listener's own question" in decisions
    assert "The outline editor's changes:\n\n- Moved the lime lumps before the loss" in decisions
    assert "- L02 (section 2, missing, tier 1): Pliny saw the same thing and said so." in decisions


def test_a_ledger_row_from_the_outline_is_one_a_research_agent_can_take(scouted, cfg, fake):
    r, d = scouted
    outline.run(r, cfg, d)
    led = ledger.load(r, render=False)
    prompt = research.agent_prompt(r, base.agent_cfg(cfg), led["rows"][1], led, r.path("angle").read_text(),
                                   (r.root / "01_scout.md").read_text())
    assert "Your idea is row L02" in prompt and "Natural History 35.166" in prompt
    assert "5 searches and 2 page fetches" in prompt and "- L01: Idea 2" in prompt


def test_the_editor_is_told_what_the_code_check_found(scouted, cfg, fake):
    r, d = scouted
    first = an_outline(4)
    first["sections"][0]["target_words"] = 600
    fake.script.first, fake.script.edited, fake.script.changes = first, an_outline(4), []
    got = outline.run(r, cfg, d)
    assert got == an_outline(4) and len(fake.calls) == 2  # the editor mended it: no repair call
    reviewed = all_text(fake.with_phrase(EDIT_PHRASE)[0])
    assert "<problems>\n- Section 1 (the opening) is 600 words, 4 minutes; the opening needs at least 900 words" in reviewed
    assert "- The sections add up to 2700 words" not in reviewed and "add up to 2700" not in reviewed
    assert "The outline editor changed nothing." in r.path("decisions").read_text()


def test_an_invalid_edit_gets_one_repair_call_told_the_errors(scouted, cfg, fake):
    r, d = scouted
    bad = an_outline(4)
    bad["sections"][0]["research_questions"] = [clone(bad["sections"][2]["research_questions"][0])]
    bad["sections"][0]["scout_entries"] = [2, 11]
    bad["sections"][3]["sets_up"] = ""
    mended = an_outline(4)
    mended["title"] = "Mended"
    fake.script.edited, fake.script.repaired = bad, mended
    assert outline.run(r, cfg, d) == mended

    assert [rec["stage"] for rec in r.read_log()] == ["fast_outline", "fast_outline_edit", "fast_outline_repair"]
    repair, = fake.with_phrase(REPAIR_PHRASE)
    assert repair["output_config"] == {"effort": "medium", "format": {"type": "json_schema", "schema": outline.SCHEMA}}
    assert repair["max_tokens"] == cfg["stages"]["fast_outline_edit"]["max_tokens"]
    assert len(repair["messages"]) == 1
    sent = all_text(repair)
    assert "{{" not in sent and EDIT_PHRASE not in sent and OUTLINE_PHRASE not in sent
    assert ("<problems>\n- Section 1 (the opening) lists D11, which is not a scout entry; the opening may cite only "
            "scout entries.\n- Section 1 (the opening) has 1 research question(s); it is written from the scout "
            "alone and may have none.\n- Section 4 does not say what it sets up (sets_up is empty).\n</problems>") in sent
    assert json.dumps(bad, ensure_ascii=False, indent=1) in sent and "[D03] (L00, quote" in sent
    assert r.path("angle").read_text().startswith("# Angle: Mended\n")
    decisions = r.path("decisions").read_text()
    assert "## outline repair" in decisions and "one repair call mended them" in decisions
    assert "- Section 4 does not say what it sets up (sets_up is empty)." in decisions
    assert "scaled into range by code" not in decisions


def test_an_edit_wrong_only_in_its_word_targets_is_scaled_by_code_without_a_repair_call(scouted, cfg, fake):
    """Regression: the arithmetic went to a third Opus call first, with the listener waiting for first audio."""
    r, d = scouted
    edited = sized([850, 700, 700, 700])
    assert outline.validate(edited, SCOUT, cfg) == [
        "Section 1 (the opening) is 850 words, 5.67 minutes; the opening needs at least 900 words (6 minutes)."]
    fake.script.edited = edited
    fake.script.repaired = {"title": "the repair call must not be asked"}
    got = outline.run(r, cfg, d)
    assert [rec["stage"] for rec in r.read_log()] == ["fast_outline", "fast_outline_edit"]  # write, edit: no third call
    assert not fake.with_phrase(REPAIR_PHRASE) and not (r.root / "fast" / "outline_repair.json").exists()
    assert [s["target_words"] for s in got["sections"]] == [900, 680, 680, 680]
    assert outline.validate(got, set(d.numbers_of("L00")), cfg) == []
    for a, b in zip(edited["sections"], got["sections"]):  # nothing but the numbers moved
        assert {k: v for k, v in a.items() if k != "target_words"} == {k: v for k, v in b.items() if k != "target_words"}
    assert [s["target_words"] for s in parse_segments(r.path("brief").read_text())] == [900, 680, 680, 680]
    decisions = r.path("decisions").read_text()
    assert "Word targets scaled into range by code: 850, 700, 700, 700 became 900, 680, 680, 680." in decisions
    assert "one repair call mended them" not in decisions


def test_word_targets_still_off_after_the_repair_call_are_scaled_by_code(scouted, cfg, fake):
    r, d = scouted
    short = sized([600, 300, 300, 300])
    edited = clone(short)
    edited["sections"][3]["sets_up"] = ""  # the arithmetic and something code cannot mend: the repair call is made
    fake.script.edited, fake.script.repaired = edited, short
    got = outline.run(r, cfg, d)
    assert [s["target_words"] for s in got["sections"]] == [1200, 600, 600, 600]
    assert outline.validate(got, set(d.numbers_of("L00")), cfg) == []
    assert {k: v for k, v in got.items() if k != "sections"} == {k: v for k, v in short.items() if k != "sections"}
    assert len(fake.calls) == 3  # write, edit, one repair: no fourth call
    sent = all_text(fake.with_phrase(REPAIR_PHRASE)[0])  # told every rule the edit broke, the arithmetic included
    assert "- Section 4 does not say what it sets up (sets_up is empty)." in sent
    assert "- Section 1 (the opening) is 600 words" in sent and json.dumps(edited, ensure_ascii=False, indent=1) in sent
    assert [s["target_words"] for s in parse_segments(r.path("brief").read_text())] == [1200, 600, 600, 600]
    assert ("Word targets scaled into range by code: 600, 300, 300, 300 became 1200, 600, 600, 600."
            in r.path("decisions").read_text())


def test_anything_else_still_wrong_after_the_repair_call_stops_the_run(scouted, cfg, fake):
    r, d = scouted
    bad = sized([600, 300, 300, 300])
    bad["sections"][2]["research_questions"] = []
    fake.script.first = fake.script.edited = fake.script.repaired = bad  # no sound outline anywhere to fall back on
    with pytest.raises(StageError, match=r"(?s)still invalid after the repair call:\n  - .*Section 3 has 0 research questions"):
        outline.run(r, cfg, d)
    assert len(fake.calls) == 3
    assert json.loads((r.root / "_partial_fast_outline.json").read_text()) == bad
    for name in ("05_outline.json", "05_outline.md", "03_angle.md", "ledger.json", "05_brief.md", "decisions.md"):
        assert not (r.root / name).exists(), name


def test_a_sound_first_outline_stands_when_the_edit_and_the_repair_both_fail(scouted, cfg, fake):
    """Regression: a valid first outline was thrown away and the listener got a problem state."""
    r, d = scouted
    first = an_outline(4)
    bad = clone(first)
    bad["title"] = "The editor's version"
    bad["sections"][2]["research_questions"] = []
    fake.script.first, fake.script.edited, fake.script.repaired = first, bad, bad
    assert outline.run(r, cfg, d) == first and outline.load(r) == first
    assert len(fake.calls) == 3  # write, edit, repair: the fall-back costs nothing
    assert r.path("angle").read_text().startswith("# Angle: The harbor that heals itself\n")
    assert len(parse_segments(r.path("brief").read_text())) == 4 and r.path("ledger").exists()
    decisions = r.path("decisions").read_text()
    assert "the first outline stands unedited" in decisions
    assert "- Section 3 has 0 research questions; sections after the opening need 1 or 2." in decisions
    assert "The outline editor's changes" not in decisions and "one repair call mended them" not in decisions
    assert outline.run(r, cfg, d) == first and len(fake.calls) == 3  # resumed: nothing is asked again


def test_a_first_outline_wrong_only_in_its_word_targets_is_the_fall_back_once_scaled(scouted, cfg, fake):
    r, d = scouted
    first = sized([850, 700, 700, 700])
    bad = an_outline(4)
    bad["sections"][2]["research_questions"] = []
    fake.script.first, fake.script.edited, fake.script.repaired = first, bad, bad
    got = outline.run(r, cfg, d)
    assert [s["target_words"] for s in got["sections"]] == [900, 680, 680, 680]
    decisions = r.path("decisions").read_text()
    assert "the first outline stands unedited" in decisions
    assert "Word targets scaled into range by code: 850, 700, 700, 700 became 900, 680, 680, 680." in decisions


def test_outline_resumes_without_a_paid_call_and_keeps_the_ledger(scouted, cfg, fake):
    r, d = scouted
    first = outline.run(r, cfg, d)
    assert len(fake.calls) == 2
    with base.LEDGER_LOCK:  # what research and a lock will have added by the time a run is resumed
        led = ledger.load(r, render=False)
        led["rows"][0].update(status="partial", gap="thin", coverage="thin", segments=[2])
        ledger.save(r, led)
    kept = r.path("ledger").read_text()
    r.path("brief").unlink()
    (r.root / "05_outline.md").unlink()
    decisions = r.path("decisions").read_text()

    assert outline.run(r, cfg, d) == first and len(fake.calls) == 2
    assert r.path("ledger").read_text() == kept
    assert parse_segments(r.path("brief").read_text())[1]["rows"] == ["L01", "L02"]
    assert (r.root / "05_outline.md").exists() and r.path("decisions").read_text() == decisions


def test_a_run_stopped_between_the_two_calls_pays_only_for_the_second(scouted, cfg, fake, monkeypatch):
    r, d = scouted

    def refuse_the_edit(kwargs):
        if EDIT_PHRASE in all_text(kwargs):
            return Responder.msg(kwargs, "", stop="refusal")
        return fake(kwargs)

    monkeypatch.setattr(llm, "_client", FakeAnthropic(refuse_the_edit))
    with pytest.raises(StageError, match="declined"):
        outline.run(r, cfg, d)
    assert (r.root / "fast" / "outline_first.json").exists() and not (r.root / "fast" / "outline_edit.json").exists()
    assert not (r.root / "05_outline.json").exists()

    monkeypatch.setattr(llm, "_client", FakeAnthropic(fake))
    assert outline.run(r, cfg, d) == an_outline(4)
    assert len(fake.with_phrase(OUTLINE_PHRASE)) == 1 and len(fake.with_phrase(EDIT_PHRASE)) == 1


def test_a_failed_outline_is_asked_for_again_not_replayed(scouted, cfg, fake):
    r, d = scouted
    first = an_outline(4)
    first["sections"][2]["research_questions"] = []  # unsound too, so there is nothing to fall back on
    fake.script.first = first
    fake.script.edited = fake.script.repaired = {"title": "not an outline"}
    with pytest.raises(StageError, match="still invalid"):
        outline.run(r, cfg, d)
    assert len(fake.calls) == 3
    assert (r.root / "fast" / "outline_first.json").exists()  # the first call's answer is kept
    assert not (r.root / "fast" / "outline_edit.json").exists() and not (r.root / "fast" / "outline_repair.json").exists()

    fake.script.edited, fake.script.repaired = an_outline(4), None
    assert outline.run(r, cfg, d) == an_outline(4)
    assert len(fake.calls) == 4 and len(fake.with_phrase(OUTLINE_PHRASE)) == 1 and len(fake.with_phrase(EDIT_PHRASE)) == 2

    (r.root / "05_outline.json").unlink()  # an edit reply with no outline in it is not replayed either
    (r.root / "fast" / "outline_edit.json").write_text(json.dumps({"changes": [], "outline": "none"}))
    with pytest.raises(StageError, match="has no outline"):
        outline.run(r, cfg, d)
    assert not (r.root / "fast" / "outline_edit.json").exists()


def test_outline_needs_the_scout(tmp_path, cfg, fake):
    r = seed_run(tmp_path, cfg)
    with pytest.raises(StageError, match="01_scout.md"):
        outline.run(r, cfg, make_dossier("stub", r, cfg))
    base.write_atomic(r.root / "01_scout.md", "# Scout\n")
    with pytest.raises(StageError, match="no scout entries"):
        outline.run(r, cfg, make_dossier("stub", r, cfg))
    assert fake.calls == []


def test_render_brief_lists_what_each_locked_section_cites(cfg):
    o = an_outline(4)
    led = ledger.normalize({"rows": [
        {"id": "L01", "rank": 1, "idea": "a", "type": "missing", "research_question": "q", "tier_needed": 2, "section": 2},
        {"id": "L05", "rank": 2, "idea": "b", "type": "missing", "research_question": "q", "tier_needed": 2,
         "section": 4, "from_note": True}]})
    segs = parse_segments(outline.render_brief(o, led, cited={1: [3, 1], 2: [9, 8, 9], 4: [14]}))
    assert [(s["n"], s["rows"], s["entries"]) for s in segs] == [(1, [], [1, 3]), (2, ["L01"], [8, 9]), (3, [], []),
                                                                 (4, ["L05"], [14])]
    before = parse_segments(outline.render_brief(o, led))
    # Before the research: the scout entries each section plans to use; the rest do not exist yet.
    assert [s["entries"] for s in before] == [[1, 2, 3], [5], [], []]
    assert "- Dossier: none yet" in before[2]["text"]
    assert all("Sets up:" in s["text"] and "Hands off to:" in s["text"] for s in before)


def test_the_brief_gives_ask_by_voice_what_it_reads(cfg):
    """Regression: ask/aside.py reads a bold Purpose line from each segment (Chapter._purpose) and the
    Dossier lines of the playing segment and the next (dossier_text). A fast brief had no Purpose, so
    the guide knew what was coming by title alone."""
    from types import SimpleNamespace

    from ask.aside import Chapter

    o = an_outline(4)
    brief = outline.render_brief(o, ledger.normalize({"rows": []}))
    chapter = SimpleNamespace(brief_segments=parse_segments(brief))
    assert [Chapter._purpose(chapter, k) for k in range(4)] == [s["lands"] for s in o["sections"]]
    assert all(s["text"].count(s_o["lands"]) == 1 for s, s_o in zip(chapter.brief_segments, o["sections"]))  # said once
    # the lines parse_segments reads are still read with the Purpose line there
    assert [(s["target_words"], s["entries"]) for s in chapter.brief_segments] == [(900, [1, 2, 3]), (700, [5]),
                                                                                  (700, []), (700, [])]


def test_render_brief_mid_chapter_and_with_titles_changed_at_a_lock(cfg):
    """A brief re-rendered while the chapter is still being made: sections not yet locked are left out
    of `cited` and keep what the outline planned; `titles` carries a title a lock changed (the outline
    itself is never rewritten)."""
    o = an_outline(4)
    led = ledger.normalize({"rows": []})
    segs = parse_segments(outline.render_brief(o, led, cited={1: [1, 3], 2: [8, 5, 9]}, titles={2: "Lime that looks wrong"}))
    assert [(s["title"], s["entries"]) for s in segs] == [
        ("Forms in the sea", [1, 3]), ("Lime that looks wrong", [5, 8, 9]), ("The lumps that heal", []),
        ("What we lost", [])]
    assert o["sections"][1]["title"] == "Ash and lime"  # the outline it was given is untouched
    assert [s["title"] for s in parse_segments(outline.render_brief(o, led, {1: [1]}, titles={2: " ", 9: "x"}))] == [
        s["title"] for s in o["sections"]]  # an empty title changes nothing


def test_the_outline_prompts_carry_the_house_rules(cfg):
    values = {**outline.limits(cfg), "seed": "S", "profile": "P", "notes": "N", "scout": "SC", "scout_numbers": "D01",
              "source_tiers": "T", "outline": "{}", "problems": "None."}
    write, edit, repair = (load_prompt(name, **values) for name in ("fast_outline", "fast_outline_edit",
                                                                    "fast_outline_repair"))
    for text in (write, edit, repair):
        assert "{{" not in text and "episode" not in text.lower() and "podcast" not in text.lower()
    for text in (write, edit):
        assert "verbatim: yes" in text and "tier 1 or 2" in text  # the quote rule and the tier rule
        assert "story-led" in text or "strongest scene" in text
        assert "stands alone" in text  # the ending never previews another chapter
        assert "a place to rest" in text  # a section's end may be where the listener waits
    assert "written from the scout alone" in write and "few facts" in write
    # Regressions (prompt review): the scene comes first and the tie to the listener's words within the
    # first minute, as fast_opening.md has it; the long opening is carried by moves, not padding or guesses
    # about the listener; and a plan written before the research is never a source of facts.
    assert "for the first lines to echo" not in write and "how the first lines tie back" not in write
    assert "Its first lines are the scene." in write and "after the scene has begun" in write
    assert "first lines the scene" in edit
    assert "Fill it" not in write and "told slowly" not in write and "at stake for this listener" not in write
    for text in (write, edit):
        assert "without padding" in text and "guess about" in text and "life, mood or feelings" in text
        assert "never a source" in text and "what the section settles" in text
    assert "Change what needs changing and leave the rest exactly as it is" in edit
