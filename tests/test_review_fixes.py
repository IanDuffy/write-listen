"""Regression tests for defects found in review."""

import pytest

from stages import baseline, ingest, revise
from stages.brief import parse_segments
from stages.common import StageError, claim_numbers, dossier_entries, spoken_text, split_script_segments
from stages.report import claim_sentences
from stages.research import extract_dossier, source_check
from stages.revise import parse_notes
from stages.tts import join_to_mp3, write_wav


def test_short_speaker_turns_are_not_headers():
    pages = [f"Brooks: Question {i}?\nScarry: Yes.\nBrooks: And then {i}.\nScarry: Yes.\nMore talk {i}."
             for i in range(6)]
    text, removed = ingest.clean(pages)
    assert text.count("Scarry: Yes.") == 12
    assert not any("Scarry" in r for r in removed)


def test_wrapped_dialogue_is_not_a_related_block():
    pages = ["Brooks: Opening.\n"] * 3 + [
        "Scarry: The thing about beauty is that it is\nrelated to fairness, and that is the thing I most want\n"
        "to say.\nBrooks: Thank you, Elaine."]
    text, removed = ingest.clean(pages)
    assert "Thank you, Elaine." in text
    assert not any("trailing" in r for r in removed)


def test_explicit_source_wins(run, cfg, tmp_path):
    run.path("source_dir").mkdir()
    run.path("transcript_pdf").write_bytes(b"not used")
    txt = tmp_path / "cleaned.txt"
    txt.write_text("Brooks: From the text file.\n")
    ingest.run(run, cfg, source=str(txt))
    assert "From the text file." in run.path("transcript").read_text()


def test_claim_ranges_and_shapes():
    assert claim_numbers("C3-C5, C9") == [3, 4, 5, 9]
    assert claim_numbers("[C3], [C7]; C2–4") == [2, 3, 4, 7]
    brief = ("## Segment 1: One\n- **Claims:** C3-C5, C9\n- **Target words:** about 780\n- Beats: page 12.\n"
             "## Segment 2: Two\n- Claims:\n- Target words: 700")
    segs = parse_segments(brief)
    assert segs[0]["claims"] == [3, 4, 5, 9] and segs[0]["target_words"] == 780
    assert segs[1]["claims"] == []  # an empty Claims line does not swallow the next line


def test_marker_shapes_are_stripped_and_sampled():
    s = "Weil said so. [C3-C5] And Plato too. [C12; C13][C14]"
    assert "[C" not in spoken_text(s)
    sents = claim_sentences(s)
    assert sents[0][1] == [3, 4, 5] and sents[1][1] == [12, 13, 14]


def test_text_above_first_marker_is_kept():
    segs = split_script_segments("An opening Ian added.\n\n<!-- seg01 -->\n\nOne.\n\n<!-- seg02 -->\n\nTwo.")
    assert segs[0] == (1, "An opening Ian added.\n\nOne.")


def test_notes_headings_variants():
    g, per = parse_notes("## Global\nx\n## Segment 1: The ladder\na\n## **seg02**\nb\n## Claim audit\nz")
    assert per == {1: "a", 2: "b"} and g == "x"


def test_revise_stops_when_a_segment_has_no_notes(run, cfg, responder):
    run.path("script_draft").write_text("<!-- seg01 -->\n\nOne.\n\n<!-- seg02 -->\n\nTwo.\n")
    run.path("edit_notes").write_text("## Global\nx\n\n## seg01\n1. [CUT] y\n")
    run.path("brief").write_text("b")
    run.path("dossier").write_text("[C1] d. Source: episode")
    with pytest.raises(StageError, match="seg02"):
        revise.run(run, cfg)


def test_dossier_bullets_and_tags():
    d = "- [C1] One. Source: https://a.org\n1. **[C2]** Two. Source: episode\n"
    assert set(dossier_entries(d)) == {1, 2}
    assert "episode" in source_check(d, {"https://a.org"})
    text = "I'll write it in <dossier> tags.\n<dossier>\n# Dossier: x\n[C1] y\n</dossier>\nDone with <dossier>."
    assert extract_dossier(text).startswith("# Dossier: x")
    with pytest.raises(StageError):
        extract_dossier("<dossier>\nunfinished")


def test_baseline_without_script_tags_stops(run, cfg, monkeypatch):
    from types import SimpleNamespace as NS

    from stages import llm
    run.path("source_dir").mkdir()
    run.path("transcript").write_text("t")
    run.path("angle").write_text("a")
    monkeypatch.setattr(llm, "call", lambda *a, **k: NS(text="Let me search... and here is a script."))
    with pytest.raises(StageError, match="_partial_baseline.md"):
        baseline.run(run, cfg)
    assert not run.path("baseline_script").exists()


def test_concat_handles_quotes_in_paths(tmp_path):
    work = tmp_path / "Ian's run"
    work.mkdir()
    a, b = work / "a.wav", work / "b.wav"
    write_wav(a, b"\x00\x01" * 2400, "pcm_24000")
    write_wav(b, b"\x00\x01" * 2400, "pcm_24000")
    out = work / "out.mp3"
    join_to_mp3([a, 300, b], out, work, "64k")
    assert out.stat().st_size > 0
