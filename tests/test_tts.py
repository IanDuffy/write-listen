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
