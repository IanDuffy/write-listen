"""Stage 1: timing (acceptance checks 1-4 and 6, Python side), on the small fixture."""

import json
import re

import pytest

from episode import align, script, timing
from stages.common import spoken_text, split_script_segments


def words_of(t):
    return [w for s in t["segments"] for x in s["sentences"] for w in x["words"]]


def test_every_script_word_once_in_order(timed, cfg):  # check 1
    run, t = timed
    parsed = script.parse((run / "09_script_final.md").read_text(), cfg["narration"]["pronunciations"])
    assert [w.w for s in parsed for x in s.sentences for w in x.words] == [w["w"] for w in words_of(t)]
    assert [x.id for s in parsed for x in s.sentences] == [x["id"] for s in t["segments"] for x in s["sentences"]]
    assert [s["id"] for s in t["segments"]] == ["seg01", "seg02"]


def test_times_are_sane(timed):  # check 2
    _, t = timed
    ws = words_of(t)
    assert all(b["start"] >= a["end"] for a, b in zip(ws, ws[1:]))  # never backwards
    assert all(0 < w["end"] - w["start"] <= align.MAX_WORD for w in ws)
    for s in t["segments"]:
        assert s["start"] <= s["sentences"][0]["start"] and s["end"] >= s["sentences"][-1]["end"]
        for x in s["sentences"]:
            assert x["start"] == x["words"][0]["start"] and x["end"] == x["words"][-1]["end"]
            assert all(x["start"] <= w["start"] and w["end"] <= x["end"] for w in x["words"])
    assert t["segments"][-1]["end"] <= t["duration"] and abs(t["duration"] - 25.59) < 0.1
    assert t["stats"]["estimated"] == 0 and t["stats"]["fuzzy"] >= 1  # "houseful" was heard as "house full"


def test_displayed_text_is_the_script(timed):  # check 3
    run, t = timed
    script_text = (run / "09_script_final.md").read_text()
    for (n, body), seg in zip(split_script_segments(script_text), t["segments"]):
        expected = " ".join(spoken_text(body).replace("[pause]", " ").split())  # the pipeline's own stripping
        assert " ".join(x["text"] for x in seg["sentences"]) == expected
        assert all(x["text"] == " ".join(w["w"] for w in x["words"]) for x in seg["sentences"])
    diotima = [w for w in words_of(t) if w["w"].startswith("Diotima")]
    assert len(diotima) == 2 and all(w["spoken"] == "Dee-oh-TEE-mah" for w in diotima)  # spoken kept alongside
    assert "house full" not in json.dumps(t) and any(w["w"] == "houseful" for w in words_of(t))


def test_nearest_sentence_boundary(timed):  # check 4
    _, t = timed
    nb = timing.nearest_sentence_boundary
    s1, s2 = t["segments"]
    assert nb(t, 0.0) == 0.0 and nb(t, -5) == 0.0
    assert nb(t, t["duration"]) == t["duration"] and nb(t, 999) == t["duration"]
    gap_mid = (s1["end"] + s2["start"]) / 2  # the pause between segments
    assert nb(t, gap_mid, "before") == s1["end"] and nb(t, gap_mid, "after") == s2["start"]
    assert nb(t, s1["end"] + 0.01) == s1["end"] and nb(t, s2["start"] - 0.01) == s2["start"]
    x = s1["sentences"][1]
    mid = x["start"] + 0.3
    assert nb(t, mid, "before") == x["start"]
    assert nb(t, x["end"] - 0.05, "after") == x["end"]
    with pytest.raises(ValueError):
        nb(t, 1.0, "sideways")


def test_current_word_python(timed):  # check 6 (the page's JavaScript twin is tested in test_page_timing.py)
    _, t = timed
    ws = words_of(t)
    assert timing.current_word(t, ws[0]["start"] - 0.01) is None
    assert timing.current_word(t, ws[0]["start"]) == (0, 0, 0)
    assert timing.current_word(t, ws[3]["start"] + 0.001) == (0, 0, 3)
    s1, s2 = t["segments"]
    assert timing.current_word(t, (s1["end"] + s2["start"]) / 2) == (0, 2, len(s1["sentences"][2]["words"]) - 1)
    assert timing.current_word(t, t["duration"]) == (1, 2, len(s2["sentences"][2]["words"]) - 1)


def test_markers_attach_to_the_sentence_they_follow(small_run, cfg):
    segs = script.parse((small_run / "09_script_final.md").read_text(), cfg["narration"]["pronunciations"])
    assert [x.dossier for x in segs[0].sentences] == [[], [], ["D01"]]
    assert [x.dossier for x in segs[1].sentences] == [["D02"], [], ["D02", "D03"]]
    assert all(not re.search(r"\[D|\[pause", x.text) for s in segs for x in s.sentences)


# --- the aligner on its own ---------------------------------------------------------------

def W(text):
    return [{"w": w, "spoken": w} for w in text.split()]


def R(text, t0=0.0, step=0.4):
    return [{"w": w, "start": t0 + i * step, "end": t0 + i * step + 0.3} for i, w in enumerate(text.split())]


def test_numbers_and_split_words_are_matched():
    times = align.align(W("In two thousand eight the houseful left."), R("In 2008 the house full left."))
    assert all(x["match"] != "estimated" for x in times)  # digits and a split word, all heard
    assert [x["match"] for x in times[:4]] == ["exact"] * 4  # "2008" normalizes to the words the script has
    assert times[5]["match"] == "fuzzy"  # "houseful" heard as "house full"
    assert all(b["start"] >= a["end"] for a, b in zip(times, times[1:]))


def test_unheard_words_are_estimated_between_neighbours():
    words = W("one two three four five six seven")
    rec = R("one two") + R("six seven", t0=5.0)
    times = align.align(words, rec)
    est = [x for x in times if x["match"] == "estimated"]
    assert len(est) == 3 and est[0]["start"] >= times[1]["end"] and est[-1]["end"] <= times[5]["start"]


def test_a_word_stretched_into_a_pause_is_trimmed():
    times = align.align(W("Long pause"), [{"w": "Long", "start": 0.0, "end": 4.0}, {"w": "pause", "start": 4.1, "end": 4.5}])
    assert times[0]["end"] - times[0]["start"] <= align.MAX_WORD


def test_number_words():
    assert align.number_words(2008) == "two thousand eight" and align.number_words(416) == "four hundred sixteen"
    assert align.norm_tokens("Plato's one-to-one, 1,200—") == ["platos", "one", "to", "one", "one", "thousand", "two", "hundred"]


def test_initials_and_abbreviations_do_not_end_sentences():
    segs = script.parse("<!-- seg01 -->\n\nThe scholar C. D. C. Reeve states it. Mr. Smith agreed, e.g. Dr. Jones. Then\nanother.\n")
    assert [x.text for x in segs[0].sentences] == ["The scholar C. D. C. Reeve states it.",
                                                    "Mr. Smith agreed, e.g. Dr. Jones.", "Then another."]


def test_a_skipped_gap_is_heard_again(tmp_path):
    from episode import timing as T
    audio = tmp_path / "a.wav"
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "6", str(audio)], check=True)
    words = W("one two three four five six seven eight")
    first = R("one two three") + R("seven eight", t0=4.0)  # the long pass skipped four, five, six
    times = align.align(words, first, limit=6.0)
    assert sum(t["match"] == "estimated" for t in times) == 3

    def clip_recognizer(path):  # the short clip hears everything (times from the clip's start)
        return R("one two three four five six seven eight", t0=1.0, step=0.5)
    clip_recognizer.name = "fake"
    assert T.repair_gaps(audio, words, times, clip_recognizer, None, 6.0) == 1
    assert all(t["match"] != "estimated" for t in times)
    assert all(b["start"] >= a["end"] for a, b in zip(times, times[1:])) and times[-1]["end"] <= 6.0
