"""Split page texts into overlapping chunks that respect sentence boundaries.

Sizes are in characters (roughly 4 per token), so the app needs no model tokenizer.
"""

import re
from dataclasses import dataclass

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n{2,}")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    page_start: int  # 1-based, inclusive
    page_end: int


def _units(pages: list[str], max_chars: int) -> list[tuple[int, str]]:
    """(page number, sentence) pairs; over-long sentences are hard-split."""
    units: list[tuple[int, str]] = []
    for page_number, page in enumerate(pages, start=1):
        for raw in _SENTENCE_END.split(page):
            sentence = _WHITESPACE.sub(" ", raw).strip()
            for start in range(0, len(sentence), max_chars):
                units.append((page_number, sentence[start : start + max_chars]))
    return units


def chunk_pages(pages: list[str], max_chars: int = 2000, overlap_chars: int = 200) -> list[Chunk]:
    if overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be smaller than max_chars")
    chunks: list[Chunk] = []
    current: list[tuple[int, str]] = []
    current_len = 0

    def flush() -> None:
        chunks.append(
            Chunk(
                index=len(chunks),
                text=" ".join(text for _, text in current),
                page_start=current[0][0],
                page_end=current[-1][0],
            )
        )

    for unit in _units(pages, max_chars):
        added = len(unit[1]) + (1 if current else 0)
        if current and current_len + added > max_chars:
            flush()
            # Carry trailing sentences into the next chunk so context spans the boundary.
            tail: list[tuple[int, str]] = []
            tail_len = 0
            for previous in reversed(current):
                if tail_len + len(previous[1]) + 1 > overlap_chars:
                    break
                tail.insert(0, previous)
                tail_len += len(previous[1]) + 1
            if tail_len + len(unit[1]) > max_chars:
                tail, tail_len = [], 0
            current, current_len = tail, max(tail_len - 1, 0)
            added = len(unit[1]) + (1 if current else 0)
        current.append(unit)
        current_len += added
    if current:
        flush()
    return chunks
