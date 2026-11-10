import re

import pytest

from stages.common import (PROMPTS_DIR, StageError, dossier_entries, join_script_segments, load_prompt,
                           parse_range, spoken_text, split_script_segments, word_count)


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
    s = "<!-- seg01 -->\n\nPlato wrote it. [C1] Then more [C2, C3].\n\n## Stray\n**Bold** words."
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
    d = "## Claims\n[C1] One. Source: x\n**[C2]** Two.\n  Quote: q\n\n### Next\n[C3] Three."
    e = dossier_entries(d)
    assert e[1] == "[C1] One. Source: x"
    assert e[2].startswith("**[C2]** Two.") and "Quote: q" in e[2]
    assert e[3] == "[C3] Three."
