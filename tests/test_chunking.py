import pytest
from ensemble.chunking import Chunker


def test_splits_long_text_and_keeps_nonempty_chunks():
    chunks = Chunker().split("alpha " * 100, target_characters=120, overlap_characters=20)
    assert len(chunks) > 1
    assert all(chunk.strip() for chunk in chunks)


def test_rejects_invalid_overlap():
    with pytest.raises(ValueError):
        Chunker().split("text", target_characters=10, overlap_characters=10)


def test_blank_text_has_no_chunks():
    assert Chunker().split(" \n ") == []
