import re

import pytest

from stages.common import (PROMPTS_DIR, StageError, dossier_entries, entry_tags, format_tags, join_script_segments,
                           load_prompt, parse_range, parse_tags, spoken_text, split_script_segments, word_count)


def test_load_prompt_drops_notes_and_fills():
    text = load_prompt("draft_earlier", earlier="SEGMENTS HERE")
    assert "Inputs:" not in text
    assert "SEGMENTS HERE" in text
    assert "{{" not in text


def test_load_prompt_missing_value_is_an_error():
    with pytest.raises(StageError, match="earlier"):
        load_prompt("draft_earlier")


def test_every_prompt_has_a_notes_header():
    for p in PROMPTS_DIR.glob("*.md"):
        if p.name == "style_guide.md":
            continue
        assert re.search(r"^---[ \t]*$", p.read_text(), re.M), p.name


def test_spoken_text_strips_markers():
    s = "<!-- seg01 -->\n\nPlato wrote it. [D01] Then more [D02, D03].\n\n## Stray\n**Bold** words."
    assert spoken_text(s) == "Plato wrote it. Then more.\n\nBold words."
    assert word_count(s) == 7


def test_segments_round_trip():
    script = join_script_segments([(1, "One."), (2, "Two.\n\nTwo b.")])
    assert split_script_segments(script) == [(1, "One."), (2, "Two.\n\nTwo b.")]


def test_parse_range():
    assert parse_range("1-2", [1, 2, 3]) == [1, 2]
    assert parse_range("1,3", [1, 2, 3]) == [1, 3]
    assert parse_range(None, [1, 2]) == [1, 2]
    with pytest.raises(StageError):
        parse_range("4", [1, 2, 3])


def test_dossier_entries():
    d = ("# Dossier\n- [D09] in the intro is not an entry\n## Entries\n[D01] (L01, claim, tier 2) One. Source: x\n"
         "**[D02]** (L01, quote) Two.\n  Quote: q\n\n### L02: Next\n[D10] Three.\n## Source check\n[D11] no")
    e = dossier_entries(d)
    assert sorted(e) == [1, 2, 10]
    assert e[1] == "[D01] (L01, claim, tier 2) One. Source: x"
    assert e[2].startswith("**[D02]** (L01, quote) Two.") and "Quote: q" in e[2]
    assert e[10] == "[D10] Three."


def test_tags_round_trip():
    t = parse_tags("L01, L04, quote, tier 1, verbatim: yes")
    assert t["rows"] == ["L01", "L04"] and t["kind"] == "quote" and t["tier"] == 1 and t["verbatim"] == "yes"
    assert format_tags(t) == "(L01, L04, quote, tier 1, verbatim: yes)"
    c = parse_tags("L3, connection, L5")
    assert c["rows"] == ["L03"] and c["links"] == ["L05"]
    v = entry_tags("**[D07]** (L02, claim, tier: episode, corrected) The episode says...")
    assert v["tier"] == "episode" and v["verdict"] == "corrected"
    assert entry_tags("[D07] no tags here")["kind"] is None
