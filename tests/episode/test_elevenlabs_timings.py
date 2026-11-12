"""Timings from generation: the ElevenLabs alignment narration keeps beside each chunk, read by
episode.timing.ElevenLabsTimings and fed to the same aligner. No API call: the alignments here are
built from the fixture's recorded timings, in the shape ElevenLabs returns."""

import json
import subprocess

import pytest

from episode import timing
from stages.common import StageError


def test_alignment_words_split_at_spaces_and_end_on_the_last_letter():
    #        H    i    _    t    h    e    r    e    .    _    G    o
    starts = [0.0, 0.1, 0.2, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 1.2, 1.3, 1.4]
    ends = [0.1, 0.2, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 1.2, 1.3, 1.4, 1.5]
    # ElevenLabs gives the pause after "." to the "." itself (0.55 -> 1.2).
    a = {"characters": list("Hi there. Go"), "character_start_times_seconds": starts,
         "character_end_times_seconds": ends}
    words = timing.alignment_words(a)
    assert [w["w"] for w in words] == ["Hi", "there.", "Go"]
    assert words[1]["start"] == 0.3 and words[1]["end"] == 0.55  # the pause is not part of "there."
    assert words[2]["start"] == 1.3


def _alignment(words, offset):
    """An ElevenLabs-shaped alignment for timed words: letters spread over each word, a word's
    trailing punctuation holding the pause after it, one space between words."""
    chars, st, en = [], [], []
    for k, w in enumerate(words):
        text, a, b = w["spoken"], w["start"] - offset, w["end"] - offset
        core = text.rstrip(".,;:!?\"”’)")
        n = max(1, len(core))
        for i, c in enumerate(core):
            chars.append(c), st.append(a + (b - a) * i / n), en.append(a + (b - a) * (i + 1) / n)
        nxt = words[k + 1]["start"] - offset if k + 1 < len(words) else b + 0.3
        for c in text[len(core):]:
            chars.append(c), st.append(b), en.append(nxt)
        if k + 1 < len(words):
            chars.append(" "), st.append(nxt), en.append(nxt)
    return {"characters": chars, "character_start_times_seconds": st, "character_end_times_seconds": en}


@pytest.fixture
def narrated(timed):
    """The fixture run as narration leaves it with timings kept: two chunk WAVs (one per segment),
    each with its alignment in the chunk's .json, joined in concat.txt."""
    run, t = timed
    audio = run / "10_audio"
    chunks = audio / "chunks"
    chunks.mkdir()
    cut = t["segments"][1]["start"] - 0.05
    dur = timing.audio_duration(audio / "chapter.mp3")
    lines = []
    for i, (seg, a, b) in enumerate([(t["segments"][0], 0.0, cut), (t["segments"][1], cut, dur)]):
        wav = chunks / f"c{i}.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(audio / "chapter.mp3"), "-ss", f"{a:.3f}",
                        "-t", f"{b - a:.3f}", "-ac", "1", "-ar", "24000", str(wav)], check=True)
        words = [{**w, "spoken": w.get("spoken", w["w"])} for x in seg["sentences"] for w in x["words"]]
        offset = timing.audio_pieces(run, audio / "chapter.mp3")[0]["offset"] if i == 0 else a
        (chunks / f"c{i}.json").write_text(json.dumps({"request_id": f"r{i}", "segment": i + 1,
                                                       "text": " ".join(w["spoken"] for w in words),
                                                       "alignment": _alignment(words, offset)}))
        lines.append(f"file '{wav}'")
    (audio / "concat.txt").write_text("\n".join(lines) + "\n")
    return run, t


def test_generation_timings_reproduce_the_times_they_were_given(narrated, cfg):
    run, whisper = narrated
    src = timing.pick_timings(run, "auto", lambda: pytest.fail("the recognizer should not be needed"))
    assert isinstance(src, timing.ElevenLabsTimings)
    t = timing.build(run, src, cfg["narration"]["pronunciations"])
    assert t["aligned_by"] == "elevenlabs" and t["pieces"] == 2
    assert t["stats"]["estimated"] == 0 and t["stats"]["gaps_reheard"] == 0
    got = [w for s in t["segments"] for x in s["sentences"] for w in x["words"]]
    want = [w for s in whisper["segments"] for x in s["sentences"] for w in x["words"]]
    assert [w["w"] for w in got] == [w["w"] for w in want]
    # the second chunk is placed at its offset; the respelled name (spoken form) still matches
    assert max(abs(g["start"] - w["start"]) for g, w in zip(got, want)) < 0.06
    assert max(abs(g["end"] - w["end"]) for g, w in zip(got, want)) < 0.06
    assert not (run / "11_episode" / "recognizer.json").read_text().count("elevenlabs")


def test_auto_falls_back_to_the_recognizer_without_timings(timed):
    run, _ = timed
    marker = object()
    assert timing.pick_timings(run, "auto", lambda: marker) is marker  # chapter.mp3 alone, no chunks


def test_a_chunk_without_timings_means_the_recognizer(narrated):
    run, _ = narrated
    meta = run / "10_audio" / "chunks" / "c1.json"
    m = json.loads(meta.read_text())
    del m["alignment"]
    meta.write_text(json.dumps(m))
    marker = object()
    assert timing.pick_timings(run, "auto", lambda: marker) is marker
    with pytest.raises(StageError, match="--timings whisper"):
        timing.pick_timings(run, "elevenlabs", lambda: marker)
    assert timing.pick_timings(run, "whisper", lambda: marker) is marker
