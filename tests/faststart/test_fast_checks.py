"""faststart/checks.py: the code checks on what the fast writer and editor return.
Acceptance check 8 (quotes and dossier references) and check 4 (moves lose and duplicate nothing)."""

import random

import pytest
from fast_sections_fakes import DOSSIER

from faststart import checks
from stages import tts
from stages.common import StageError

SCOUT = {1, 2, 3, 4}

CLEAN = """A barge rides low off a coast with no harbour, and the men aboard pour a wall into the sea. [D01]

A Roman writer said of this concrete, "The sea itself would not dissolve it." [D02] The mix was lime, ash and seawater. [D03]

[pause]

So the question is what the sea did to it."""


def problems(text, allowed=None):
    return checks.section_problems(text, DOSSIER, allowed)


# --- check 8: one section -------------------------------------------------------------------

def test_a_clean_section_has_no_problems():
    assert problems(CLEAN) == []
    assert problems(CLEAN, allowed=SCOUT) == []
    assert checks.cited(CLEAN) == {1, 2, 3}


def test_an_unbacked_quotation():
    got = problems(CLEAN + '\n\nOne engineer put it this way: "nothing we pour today will outlive us by much."')
    assert len(got) == 1 and "no verbatim: yes entry" in got[0] and "nothing we pour today" in got[0]


def test_a_quotation_backed_only_by_a_verbatim_no_entry():
    got = problems(CLEAN + '\n\nA geologist said, "It gets stronger every single year it sits there." [D04]')
    assert len(got) == 1 and "D04" in got[0] and "not marked verbatim: yes" in got[0]


def test_a_quotation_must_be_the_quoted_words_not_the_attribution():
    # D02's attribution is not its quotation; the words in quotation marks are what count.
    got = problems('He called him "a Roman writer on harbour concrete" in passing. [D02]')
    assert len(got) == 1 and "a Roman writer on harbour concrete" in got[0]


def test_a_cut_inside_a_quotation_is_allowed_and_short_phrases_are_not_quotations():
    assert problems('The writer said that "as soon as the waves touch it ... a single mass of stone." [D08]') == []
    assert problems('They called it "the harbour mix" and moved on. [D03]') == []


def test_a_dossier_reference_that_does_not_exist():
    got = problems("The wall is two thousand years old. [D99] The mix was lime and ash. [D03, D42]")
    assert got == ["cites D42, which is not in the dossier", "cites D99, which is not in the dossier"]


def test_the_opening_may_cite_only_scout_entries():
    text = "The ash came from the bay near Pozzuoli. [D05] The mix was lime and ash. [D03]"
    assert problems(text) == []
    got = problems(text, allowed=SCOUT)
    assert len(got) == 1 and got[0].startswith("cites D05, which is not a scout entry")


def test_a_bracket_the_narrator_would_read():
    got = problems("The wall held. [D01] [laughs] And it still holds.")
    assert len(got) == 1 and "'[laughs]'" in got[0] and "narrator" in got[0]
    assert any("stray square bracket" in p for p in problems("The wall held [and here the writer left a long note to "
                                                             "self that runs on and on] for good. [D01]"))
    assert any("stray square bracket" in p for p in problems("The wall held ] for good. [D01]"))


def test_editorial_brackets_and_held_beats_are_fine():
    assert problems('The writer said that "[a]s soon as the waves touch it, it becomes a single mass of stone." [D08]'
                    "\n\n[pause]\n\nAnd so it did.") == []


def test_a_leftover_marker():
    got = problems("The mix was lime, ash and seawater. [D03 The wall held.")
    assert any("left unclosed" in p for p in got)


def test_a_segment_marker_or_no_text():
    assert any("<!-- seg" in p for p in problems("<!-- seg02 -->\n\n" + CLEAN))
    assert problems("  \n") == ["the section has no text"]
    assert problems("<!-- seg02 -->") == ["the section has no text"]


def test_the_checks_agree_with_narration(run, cfg, fake_tts):
    """What narrator_problems flags is what stages/tts.py stops on; what it passes, narrates."""
    bad = "The wall held. [D01] [laughs] And it still holds."
    assert checks.narrator_problems(bad)
    with pytest.raises(StageError, match="read aloud"):
        tts.narrate_segments(run, cfg, [(1, bad)], run.root / "bad.mp3", run.root / "bad", stage="narrate")
    assert fake_tts == []  # stopped before paying
    fixed, notes = checks.repair(bad, DOSSIER)
    assert checks.narrator_problems(fixed) == [] and notes
    tts.narrate_segments(run, cfg, [(1, fixed), (2, CLEAN)], run.root / "ok.mp3", run.root / "ok", stage="narrate")
    assert (run.root / "ok.mp3").exists()


# --- check 8: the whole script --------------------------------------------------------------

def test_chapter_problems_names_the_section():
    script = [(1, CLEAN), (2, "The wall is old. [D77]"), (3, 'She said, "this was never in any entry at all."')]
    got = checks.chapter_problems(script, DOSSIER)
    assert len(got) == 2
    assert got[0] == "section 2: cites D77, which is not in the dossier"
    assert got[1].startswith("section 3: a quotation") and "this was never in any entry" in got[1]
    assert checks.chapter_problems([(1, CLEAN)], DOSSIER) == []


# --- repairs by code ------------------------------------------------------------------------

def test_repair_drops_an_unknown_marker_and_keeps_the_known_ones():
    text, notes = checks.repair("The wall is old. [D99] The mix was lime and ash. [D03, D42] It held. [D01]", DOSSIER)
    assert text == "The wall is old. The mix was lime and ash. [D03] It held. [D01]"
    assert notes == ["dropped the marker for D99 (not in the dossier)", "dropped the marker for D42 (not in the dossier)"]


def test_repair_takes_the_marks_off_an_unbacked_quotation_only():
    text, notes = checks.repair(
        'An engineer said, "nothing we pour today will outlive us by much." A Roman writer said, '
        '"The sea itself would not dissolve it." [D02]', DOSSIER)
    assert text == ('An engineer said, nothing we pour today will outlive us by much. A Roman writer said, '
                    '"The sea itself would not dissolve it." [D02]')
    assert len(notes) == 1 and "took the quotation marks off" in notes[0] and "nothing we pour today" in notes[0]
    assert problems(text) == []


def test_repair_for_the_opening_drops_a_marker_that_is_not_the_scouts():
    text, notes = checks.repair("The ash came from the bay. [D05] The mix was lime and ash. [D03, D06]", DOSSIER, SCOUT)
    assert text == "The ash came from the bay. The mix was lime and ash. [D03]"
    assert all("not a scout entry" in n for n in notes) and len(notes) == 2


def test_repair_leaves_nothing_the_narrator_would_stop_on():
    messy = ('<!-- seg03 -->\n\nThe wall held. [D01] [laughs] And [T]he sea came in [sic] again. [D03\n\n[pause]\n\n'
             'It held ] still, [with a very long stage direction that nobody asked the writer to put here] they say. '
             '[D99] She said, "this was never in any entry at all." [D02]')
    assert len(problems(messy)) >= 6
    text, notes = checks.repair(messy, DOSSIER)
    assert problems(text) == []
    assert "[pause]" in text and "[T]he" in text and "[sic]" in text and "[D01]" in text and "[D02]" in text
    assert "laughs" not in text and "stage direction" not in text and "seg03" not in text and '"' not in text
    assert len(notes) >= 6


def test_whatever_the_text_repair_leaves_nothing_the_checks_or_the_narrator_object_to():
    """Narration stops on a bracket it cannot read (stages/tts.py), so this must hold for any text."""
    bits = ["The wall held.", "[D01]", "[D99]", "[D03, D42]", "[D3-D5]", "[D12", "[laughs]", "[pause]", "\n\n", "[", "]",
            '"', "“", "”", "[T]he sea", "want[ed]", "[sic]", "[...]", "<!-- seg02 -->", "<!-- seg", "said,", " ", "\n",
            "It gets stronger every single year it sits there.", "The sea itself would not dissolve it.", "[D05] [D06]",
            "[a very long stage direction that runs past forty characters easily]", "**bold**", "# Heading", "[Pause]",
            "[D07; D08]", "x[D01]y", "[]", "[[D01]]", "[D01]]", "[ D01 ]", "[D2,", "D3]", "__",
            "[D01 and a marker that runs on far too long to be a marker at all, really]"]
    rng = random.Random(7)
    for _ in range(1500):
        text = rng.choice(["", " "]).join(rng.choice(bits) for _ in range(rng.randint(1, 14)))
        for allowed in (None, SCOUT):
            fixed, notes = checks.repair(text, DOSSIER, allowed)
            if checks.spoken_text(fixed).strip():
                assert checks.section_problems(fixed, DOSSIER, allowed) == [], (text, fixed)
            assert notes or fixed == text, text


def test_repair_leaves_a_clean_section_alone():
    assert checks.repair(CLEAN, DOSSIER) == (CLEAN, [])
    odd = "He waited ... and then the wall  held. [D01]"  # spacing is the writer's unless something was removed
    assert checks.repair(odd, DOSSIER) == (odd, [])


# --- check 4: moving material between two sections ------------------------------------------

A1 = "Section two opens at the quarry on the bay where the ash was dug. [D05]"
A2 = "The ash went by ship across the open sea to the harbour works. [D05]"
A3 = "And still the mix alone does not explain why the wall stands."
B1 = "Section three begins with a core drilled out of the harbour wall. [D06]"
B2 = "Seawater moving through the wall grew a rare mineral in its pores. [D07]"
B3 = "A crack in this wall is the place where the growing starts again."
BEFORE, BEFORE_NEXT = "\n\n".join([A1, A2, A3]), "\n\n".join([B1, B2, B3])


def moves(after, after_next, moved=(), cut=()):
    return checks.move_problems(BEFORE, BEFORE_NEXT, after, after_next, list(moved), list(cut))


def test_no_change_and_a_clean_move_pass():
    assert moves(BEFORE, BEFORE_NEXT) == []
    # B1 (the drilled core) moves from section 3 into section 2, whole, with its marker.
    assert moves("\n\n".join([A1, A2, B1, A3]), "\n\n".join([B2, B3]), [{"text": B1, "from": 3, "to": 2}]) == []
    # And the other way: A2 moves on into section 3.
    assert moves("\n\n".join([A1, A3]), "\n\n".join([A2, B1, B2, B3]), [{"text": A2, "from": 2, "to": 3}]) == []


def test_a_moved_passage_left_in_both_sections_fails():
    got = moves("\n\n".join([A1, A2, B1, A3]), BEFORE_NEXT, [{"text": B1, "from": 3, "to": 2}])
    assert len(got) == 1 and "both sections" in got[0] and "core drilled" in got[0]


def test_a_move_reworded_on_arrival_but_left_behind_as_it_was_fails():
    arrived = "It begins with a core drilled out of the harbour wall. [D06]"
    got = moves("\n\n".join([A1, A2, arrived, A3]), BEFORE_NEXT, [{"text": arrived, "from": 3, "to": 2}])
    assert len(got) == 1 and "still in section 3" in got[0]
    assert moves("\n\n".join([A1, A2, arrived, A3]), "\n\n".join([B2, B3]), [{"text": arrived, "from": 3, "to": 2}]) == []


def test_a_declared_move_that_never_arrived_fails():
    got = moves(BEFORE, "\n\n".join([B2, B3]), [{"text": B1, "from": 3, "to": 2}], cut=[B1])
    assert len(got) == 1 and "declared moved to section 2 is not there" in got[0]
    assert any("two different sections" in p for p in moves(BEFORE, BEFORE_NEXT, [{"text": B1, "from": 2, "to": 2}]))


def test_a_passage_dropped_from_both_without_being_declared_cut_fails():
    got = moves(BEFORE, "\n\n".join([B1, B3]))  # B2 and its D07 are gone
    assert len(got) == 1 and got[0].startswith("D07 was cited in the drafts") and "rare mineral" in got[0]
    assert moves(BEFORE, "\n\n".join([B1, B3]), cut=[B2]) == []
    assert moves(BEFORE, "\n\n".join([B1, B3]), cut=[B2 + " (repeats section 1)"]) == []
    assert moves(BEFORE, "\n\n".join([B1, B3]), cut=[{"text": B2, "reason": "repeats section 1"}]) == []


def test_a_sentence_kept_with_a_different_marker_is_not_a_loss():
    recited = B2.replace("[D07]", "[D06]")
    assert moves(BEFORE, "\n\n".join([B1, recited, B3])) == []


def test_a_duplicated_sentence_fails():
    got = moves("\n\n".join([A1, A2, A3, A2]), BEFORE_NEXT)
    assert len(got) == 1 and "appears twice in the section" in got[0]
    got = moves(BEFORE, "\n\n".join([B1, B2, B3, B2]))
    assert len(got) == 1 and "appears twice in the next section's draft" in got[0]
    # A short refrain may recur: only sentences of eight words or more count.
    assert moves(BEFORE + "\n\nThe wall held.\n\nThe wall held.", BEFORE_NEXT + "\n\nThe wall held.") == []


def test_losing_a_fifth_of_the_words_fails_unless_cuts_account_for_it():
    got = moves(A1, BEFORE_NEXT)  # A2 and A3 gone: D05 is still cited by A1, but the words are lost
    assert len(got) == 1 and got[0].startswith("text was lost")
    assert moves(A1, BEFORE_NEXT, cut=[A2, A3]) == []
    assert any("came back empty" in p for p in moves(BEFORE, ""))


def test_loss_problems_for_an_edit_of_one_section():
    assert checks.loss_problems([BEFORE], [BEFORE], []) == []
    assert checks.loss_problems([BEFORE], [A1], [])[0].startswith("text was lost")
    assert checks.loss_problems([BEFORE], [A1], [A2, A3]) == []
    assert checks.loss_problems([BEFORE], ["\n\n".join([A1, A2])], [], keep=0.6) == []


# --- regressions from the review --------------------------------------------------------------

NINE = "[D01, D02, D03, D04, D05, D06, D07, D08, D09]"       # 40 characters inside: a valid marker
TEN = "[D01, D02, D03, D04, D05, D06, D07, D08, D09, D10]"   # too long to be one


def test_a_long_marker_is_never_cut_into_spoken_text():
    """The unclosed-marker pattern used to backtrack into a long marker's digits, and repair then
    left the rest of it ('1, D02, D03, ...') as prose for the narrator."""
    assert not checks.UNCLOSED.search(NINE) and not checks.UNCLOSED.search(TEN)
    assert checks.UNCLOSED.search("so it was. [D12 And then").group() == "[D12"
    assert checks.UNCLOSED.search("so it was. [D2, and").group() == "[D2"
    text = f"The whole story rests on this. {NINE} And one thing more. [D99]"
    fixed, notes = checks.repair(text, DOSSIER)
    assert fixed == f"The whole story rests on this. {NINE} And one thing more."   # only the unknown marker goes
    assert checks.cited(fixed) == set(range(1, 10))
    fixed, notes = checks.repair(f"The wall held for two thousand years. {TEN} And so on.", DOSSIER)
    assert problems(fixed) == [] and checks.spoken_text(fixed) == "The wall held for two thousand years. And so on."
    rng = random.Random(11)
    bits = ["The wall held.", NINE, TEN, "[D99]", "[laughs]", "[D12", '"', "\n\n", "And so on."]
    for _ in range(300):
        text = " ".join(rng.choice(bits) for _ in range(rng.randint(2, 8)))
        fixed, _ = checks.repair(text, DOSSIER)
        assert "D0" not in checks.spoken_text(fixed), (text, fixed)
        if checks.spoken_text(fixed).strip():
            assert problems(fixed) == [], (text, fixed)


def _para(i, d):
    return f"Paragraph {i} of the section says a plain thing about the wall and the sea and the ash in it. [D{d:02d}]"


def test_a_paired_edit_may_not_lose_a_large_part_of_either_section():
    """The 80% rule was applied only to the two sections pooled, so an unchanged neighbour hid a
    section that came back 40% short with nothing declared cut."""
    here = [_para(i, 5 if i % 2 else 6) for i in range(1, 11)]
    there = [_para(i + 20, 7 if i % 2 else 8) for i in range(1, 11)]
    before, before_next = "\n\n".join(here), "\n\n".join(there)
    got = checks.move_problems(before, before_next, "\n\n".join(here[:6]), before_next, [], [])
    assert len(got) == 1 and got[0].startswith("text was lost from the section") and checks.is_loss(got[0])
    got = checks.move_problems(before, before_next, before, "\n\n".join(there[:6]), [], [])
    assert len(got) == 1 and got[0].startswith("text was lost from the next section's draft")
    # Declared cuts account for it. A declared move is not a loss in the section it left
    # (test_no_change_and_a_clean_move_pass, both directions); the same move undeclared is.
    assert checks.move_problems(before, before_next, "\n\n".join(here[:6]), before_next, [], here[6:]) == []
    got = moves("\n\n".join([A1, A3]), "\n\n".join([A2, B1, B2, B3]))
    assert len(got) == 1 and got[0].startswith("text was lost from the section")
    # A passage already reported as gone by its marker is not reported a second time by its words.
    got = checks.move_problems(before, "\n\n".join([B1, B2, B3]), before, "\n\n".join([B1, B3]), [], [])
    assert len(got) == 1 and got[0].startswith("D07 was cited in the drafts")


def test_a_marker_taken_off_a_reworded_sentence_is_not_a_lost_passage():
    """The editor as fact-checker drops a marker the sentence does not support and rewords the
    sentence to what its other entry says: nothing was lost, so no fix round."""
    rest = "\n\n".join([A1, A2, A3, "The lime was burned in kilns above the shore and slaked with water from the sea.",
                        "Nobody who mixed it could have said why it worked, only that it did.",
                        "The barges carried it out past the breakers and the crews tipped it into wooden forms."])
    b = "The ash was quarried on the bay and a drilled core still shows lumps of it in the wall. [D05, D09]\n\n" + rest
    a = "The ash was quarried on the bay near Pozzuoli, then shipped across the sea. [D05]\n\n" + rest
    assert checks.move_problems(b, BEFORE_NEXT, a, BEFORE_NEXT, [], []) == []
    # Lightly reworded with the marker simply gone: still said.
    a = "The ash was quarried on the bay, and a drilled core still shows the lumps of it in the wall. [D05]\n\n" + rest
    assert checks.move_problems(b, BEFORE_NEXT, a, BEFORE_NEXT, [], []) == []
    # Dropped whole is still a loss, even though its other entry is cited elsewhere.
    got = checks.move_problems(b, BEFORE_NEXT, rest, BEFORE_NEXT, [], [])
    assert len(got) == 1 and got[0].startswith("D09 was cited in the drafts") and "drilled core" in got[0]


SAID = "# Seed\n\nWhy did Roman concrete last? I pour concrete for a living and ours cracks in thirty years.\n"


def test_the_listeners_own_words_are_not_a_quotation_from_a_source():
    """Their words, echoed in quotation marks, are in no dossier entry; that used to cost a fix call."""
    text = 'You told me, "I pour concrete for a living," and that yours "cracks in thirty years." The mix was lime. [D03]'
    assert len(problems(text)) == 2                         # the old signature still checks every quotation
    assert checks.section_problems(text, DOSSIER, None, SAID) == []
    assert checks.section_problems(text, DOSSIER, SCOUT, said=SAID) == []
    assert checks.repair(text, DOSSIER, None, SAID) == (text, [])
    assert checks.chapter_problems([(1, text)], DOSSIER, SAID) == [] and len(checks.chapter_problems([(1, text)], DOSSIER)) == 2
    # Words they never said are still a quotation nobody backs, and repair takes the marks off those alone.
    mixed = text + ' One engineer said, "nothing we pour today will outlive us by much."'
    got = checks.section_problems(mixed, DOSSIER, None, SAID)
    assert len(got) == 1 and "nothing we pour today" in got[0]
    fixed, notes = checks.repair(mixed, DOSSIER, None, SAID)
    assert fixed == text + " One engineer said, nothing we pour today will outlive us by much." and len(notes) == 1
    # A source's words are not theirs because the same words turn up apart in what they said.
    assert len(checks.section_problems('He wrote, "concrete for thirty years of living." [D03]', DOSSIER, None, SAID)) == 1


def test_which_problems_are_a_loss():
    assert checks.is_loss(checks.loss_problems([BEFORE], [A1], [])[0]) and checks.is_loss("the section has no text")
    assert checks.is_loss("the revised draft of the next section came back empty")
    assert not checks.is_loss("cites D99, which is not in the dossier")
    assert not checks.is_loss("a sentence appears in both sections; keep it in one place only: \"x\"")
