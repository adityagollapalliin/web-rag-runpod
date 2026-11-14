import pytest

from app.services.chunking import chunk_pages


def test_short_text_is_one_chunk() -> None:
    chunks = chunk_pages(["Hello world. This is a test."])
    assert len(chunks) == 1
    assert chunks[0].text == "Hello world. This is a test."
    assert (chunks[0].page_start, chunks[0].page_end) == (1, 1)


def test_empty_and_whitespace_pages_give_no_chunks() -> None:
    assert chunk_pages([]) == []
    assert chunk_pages(["", "  \n\n  "]) == []


def test_chunks_respect_size_overlap_and_track_pages() -> None:
    pages = [" ".join(f"Page {p} sentence {i} has words." for i in range(40)) for p in (1, 2, 3)]
    chunks = chunk_pages(pages, max_chars=300, overlap_chars=80)

    assert len(chunks) > 3
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
    assert all(len(chunk.text) <= 300 for chunk in chunks)
    assert chunks[0].page_start == 1 and chunks[-1].page_end == 3
    assert all(chunk.page_start <= chunk.page_end for chunk in chunks)
    # Each chunk opens with the tail of the previous one (whole sentences, at most 80 chars).
    for previous, following in zip(chunks, chunks[1:], strict=False):
        first_sentence = following.text.split(". ")[0] + "."
        shared = previous.text[previous.text.rindex(first_sentence) :]
        assert following.text.startswith(shared)
        assert previous.text.endswith(shared) and 0 < len(shared) <= 80
    # Nothing is lost: every sentence appears somewhere.
    joined = " ".join(chunk.text for chunk in chunks)
    assert all(f"Page {p} sentence {i} has words." in joined for p in (1, 2, 3) for i in range(40))


def test_sentence_longer_than_a_chunk_is_split() -> None:
    chunks = chunk_pages(["x" * 1000], max_chars=300, overlap_chars=50)
    assert [len(chunk.text) for chunk in chunks] == [300, 300, 300, 100]


def test_overlap_must_be_smaller_than_chunk() -> None:
    with pytest.raises(ValueError):
        chunk_pages(["a"], max_chars=100, overlap_chars=100)
