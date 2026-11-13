import pytest

from stages.common import StageError
from stages.tts import apply_pronunciations, chunk_paragraphs


def test_chunks_split_at_paragraphs_then_sentences():
    p1 = "Short paragraph."
    long_para = " ".join(f"Sentence number {i} is here." for i in range(40))
    chunks = chunk_paragraphs(f"{p1}\n\n{long_para}\n\nEnd.", 200)
    assert all(len(c) <= 200 for c in chunks)
    assert chunks[0].startswith("Short paragraph.")
    rejoined = " ".join(c.replace("\n\n", " ") for c in chunks)
    assert rejoined.split() == f"{p1} {long_para} End.".split()


def test_oversized_sentence_is_an_error():
    with pytest.raises(StageError):
        chunk_paragraphs("x" * 300, 200)


def test_pronunciations_whole_words():
    table = {"Weil": "Vay"}
    assert apply_pronunciations("Simone Weil said. Weill is different. Weil's view.", table) == \
        "Simone Vay said. Weill is different. Vay's view."


def test_the_chapter_mp3_is_written_whole_or_not_at_all(tmp_path, monkeypatch):
    """A stop mid-encode (SIGINT to the run's process group) leaves ffmpeg's partial output: it must not
    become chapter.mp3, which marks narration done."""
    import wave

    from stages import tts
    wav = tmp_path / "a.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000)
        w.writeframes(b"\x00\x00" * 2400)
    out = tmp_path / "10_audio" / "chapter.mp3"
    real = tts.ffmpeg

    def interrupted(args):
        if args[-1].endswith(".mp3"):
            open(args[-1], "wb").write(b"partial")  # what ffmpeg leaves when stopped
            raise StageError("ffmpeg failed: interrupted")
        return real(args)
    monkeypatch.setattr(tts, "ffmpeg", interrupted)
    with pytest.raises(StageError):
        tts.join_to_mp3([wav, 200, wav], out, tmp_path, "64k")
    assert not out.exists()
    monkeypatch.setattr(tts, "ffmpeg", real)
    tts.join_to_mp3([wav, 200, wav], out, tmp_path, "64k")
    assert out.stat().st_size > 0 and not list(out.parent.glob("*.part.mp3"))
