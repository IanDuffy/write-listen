"""signals/record.py: text measures, the norm, the change, the trend, the note (package 2)."""
from signals import record as R

S = R.settings({})


def rec(turn, words_text, *, source="page", gap_ms=600, dur_ms=None, speak_ms=None, rms_db=-30.0,
        pitch=None, t=None, cut_in=None):
    if speak_ms is None:                                        # a steady 2.5 words a second unless given
        speak_ms = max(400, R.words(words_text) * 400)
    if dur_ms is None:
        dur_ms = speak_ms + 200
    raw = {"gap_ms": gap_ms, "dur_ms": dur_ms, "speak_ms": speak_ms, "rms_db": rms_db, "pitch_sd_st": pitch,
           "pauses": 0, "longest_pause_ms": 0, "cut_in": cut_in}
    return R.build(raw, words_text, stem="20261121-101500", part="guide", source=source, turn=turn,
                   live_id="sess_1", t=t or f"2026-11-21T10:15:{turn:02d}.000", S=S)


def ten(n):
    return " ".join(["word"] * n) + "."


# --- text -------------------------------------------------------------------------

def test_tags_in_pieces_join_before_reading():
    assert R.join(["[inhale", "]Yeah, it was good."]) == "[inhale]Yeah, it was good."
    assert R.join(["I was", "going to say"]) == "I was going to say"
    assert R.join(["Well [tongue", "click ]so"]) == "Well [tongue click ]so"
    assert R.join(["", "  ", "Hi"]) == "Hi"


def test_words_match_the_page_rule():
    # live.js words(): tags are not words; an unclosed tag swallows to the end; punctuation alone is not a word
    assert R.words("[inhale]Yeah, it was good") == 4
    assert R.words("[inhale and then I") == 0
    assert R.words("Tch .Um... No, but uh") == 5
    assert R.words("let's-let's end it") == 3
    assert R.words("") == 0
    assert R.words("— ... !") == 0


def test_tags_are_listed_lower_case():
    assert R.tags("[inhale]Yeah [Laughs] fine [tongue click ]ok") == ["inhale", "laughs", "tongue click"]
    assert R.tags("Yeah, um [inhale") == ["inhale"]
    assert R.tags("No tags here.") == []


def test_hedges_and_fillers():
    t = "Um... I guess it was, uh, kind of fine. Uh- sort of. I think so."
    m = R.text_measures(t, S)
    assert m["hedges"] == 3
    assert m["fillers"] == 3
    assert R.count_phrases("I don’t know", ["i don't know"]) == 1


def test_trailing_off():
    assert R.trailed("It was good.", S) is False
    assert R.trailed("It was good, but", S) is True
    assert R.trailed("I went there and...", S) is True
    assert R.trailed("I mean I don't know.", S) is True
    assert R.trailed("Yeah, this is Ian", S) is True                         # no sentence end
    assert R.trailed("Yeah, this is Ian", {**S, "trail_unpunctuated": False}) is False
    assert R.trailed("Was it?\"", S) is False
    assert R.trailed("[inhale]", S) is None


# --- one record ---------------------------------------------------------------------

def test_build_takes_audio_from_the_measurer_and_text_from_here():
    r = rec(0, "[sigh]Um, I guess it was okay, but", gap_ms=1200, dur_ms=3000, speak_ms=2000)
    assert r["words"] == 7 and r["wps"] == 3.5 and r["tags"] == ["sigh"]
    assert r["hedges"] == 1 and r["fillers"] == 1 and r["trailed"] is True
    assert r["cut_in"] is False
    assert r["pitch_sd_st"] is None                            # not taken: null, never a guess
    assert set(R.MEASURES) <= set(r)


def test_cut_in_is_a_negative_gap_and_wps_needs_time():
    r = rec(0, "Wait, no.", gap_ms=-400)
    assert r["cut_in"] is True
    r = rec(0, "Yes.", dur_ms=200, speak_ms=200)
    assert r["wps"] is None
    r = R.build({}, "Hello there.", stem="20261121-101500", part="guide", source="page", turn=0,
                live_id=None, t=None, S=S)
    assert r["gap_ms"] is None and r["cut_in"] is None and r["wps"] is None


# --- the norm ------------------------------------------------------------------------

def test_no_norm_until_three_turns_of_three_words():
    turns = [rec(0, "Yes."), rec(1, ten(12)), rec(2, "Okay sure."), rec(3, ten(10))]
    nm, n = R.norm(turns, S)
    assert nm is None and n == 2
    placed = R.place(rec(4, ten(4)), turns, S)
    assert placed["norm"] is None and placed["change"] is None
    assert placed["note"] == "Their last turn: no norm yet (2 of 3 turns)."


def test_norm_is_the_median_and_change_a_ratio_or_db():
    turns = [rec(i, ten(n), gap_ms=g, rms_db=db) for i, (n, g, db) in
             enumerate([(10, 500, -30), (20, 700, -28), (12, 600, -32)])]
    nm, n = R.norm(turns, S)
    assert n == 3 and nm["words"] == 12 and nm["gap_ms"] == 600 and nm["rms_db"] == -30
    assert nm["pitch_sd_st"] is None                           # no values: no norm for it
    p = R.place(rec(3, ten(4), gap_ms=2400, rms_db=-36), turns, S)
    assert p["change"]["words"] == round(4 / 12, 2)
    assert p["change"]["gap_ms"] == 4.0
    assert p["change"]["rms_db"] == -6.0
    assert p["mention"] == ["words", "gap_ms", "rms_db"]
    assert p["note"] == ("Their last turn: 4 words (norm 12); 2.4 s before answering (norm 0.6 s); "
                         "6 dB quieter than their norm; three turns running: shorter, quieter.")
    assert len(p["note"].split()) <= 30


def test_gap_needs_both_the_ratio_and_800_ms():
    turns = [rec(i, ten(10), gap_ms=200) for i in range(3)]
    p = R.place(rec(3, ten(10), gap_ms=900), turns, S)          # 4.5x but only 700 ms longer
    assert "gap_ms" not in p["mention"]
    p = R.place(rec(3, ten(10), gap_ms=1000), turns, S)
    assert "gap_ms" in p["mention"]


def test_norm_counts_only_the_same_source_and_the_latest_turns():
    rec_turns = [rec(i, ten(30), source="recording") for i in range(5)]
    page_turns = [rec(i, ten(10)) for i in range(3)]
    p = R.place(rec(9, ten(10)), rec_turns + page_turns, S)
    assert p["norm"]["words"] == 10 and p["norm_turns"] == 3
    many = [rec(i, ten(100)) for i in range(5)] + [rec(10 + i, ten(10)) for i in range(40)]
    assert R.norm(many, S)[0]["words"] == 10


def test_tags_cut_ins_and_trailing_are_always_mentioned_even_without_a_norm():
    p = R.place(rec(0, "[laughs]Well, so", gap_ms=-300), [], S)
    assert p["mention"] == ["tags", "cut_in", "trailed"]
    assert p["note"] == "Their last turn: no norm yet (0 of 3 turns); [laughs]; cut in; trailed off."


def test_nothing_outside_the_norm():
    turns = [rec(i, ten(10)) for i in range(3)]
    p = R.place(rec(3, ten(11)), turns, S)
    assert p["mention"] == [] and p["note"] == "Their last turn: nothing outside their norm."


def test_trend_over_the_last_three_turns():
    turns = [rec(0, ten(30), gap_ms=300), rec(1, ten(20), gap_ms=700)]
    p = R.place(rec(2, ten(10), gap_ms=1200), turns, S)
    assert p["trend"] == ["shorter", "slower to answer"]
    assert p["note"].endswith("three turns running: shorter, slower to answer.")
    flat = [rec(0, ten(10)), rec(1, ten(9))]
    assert R.place(rec(2, ten(8)), flat, S)["trend"] == []       # steps under the 0.8 ratio


def test_the_note_drops_whole_items_to_stay_under_the_limit():
    turns = [rec(i, ten(10), pitch=4.0) for i in range(3)]
    r = rec(3, "[sigh] [laughs] [inhale] [tongue click] [cough] " + ten(2) + " but", gap_ms=-200, pitch=1.0)
    p = R.place(r, turns, {**S, "note_words": 20})
    assert len(p["note"].split()) <= 20
    assert p["note"].startswith("Their last turn: 3 words (norm 10)")
    assert "trailed off" not in p["note"]                       # the last items went first


def test_annotate_places_each_against_the_ones_before():
    turns = [rec(i, ten(10)) for i in range(3)] + [rec(3, ten(3))]
    got = R.annotate(turns, S)
    assert [g["norm"] is None for g in got] == [True, True, True, False]
    assert got[3]["mention"] == ["words"]


def test_settings_merge_over_defaults():
    s = R.settings({"signals": {"min_turns": 5, "thresholds": {"rms_db": 6}}})
    assert s["min_turns"] == 5 and s["thresholds"]["rms_db"] == 6 and s["thresholds"]["wps"] == [0.7, 1.3]
    assert R.live_on({}) is False
    assert R.live_on({"signals": {"live": True}}) is True
    assert R.live_on({"live": {"director": {"signals": True}}}) is True
