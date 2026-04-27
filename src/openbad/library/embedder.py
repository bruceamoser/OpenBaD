"""Recursive character text splitter for library book content."""

from __future__ import annotations

_CHARS_PER_TOKEN = 4
_DEFAULT_CHUNK_TOKENS = 500
_DEFAULT_OVERLAP_TOKENS = 50


def chunk_text(
    text: str,
    *,
    chunk_tokens: int = _DEFAULT_CHUNK_TOKENS,
    overlap_tokens: int = _DEFAULT_OVERLAP_TOKENS,
) -> list[tuple[str, int]]:
    """Split *text* into overlapping chunks.

    Returns a list of ``(chunk_text, chunk_index)`` tuples.  Token counts
    are estimated using the ``chars / 4`` heuristic consistent with
    ``context_manager.py``.
    """
    if not text:
        return []

    chunk_chars = chunk_tokens * _CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * _CHARS_PER_TOKEN

    separators = ["\n\n", "\n", ". ", " "]
    return _split_recursive(text, chunk_chars, overlap_chars, separators)


def _split_recursive(
    text: str,
    chunk_chars: int,
    overlap_chars: int,
    separators: list[str],
) -> list[tuple[str, int]]:
    """Recursively split text using the first separator that produces chunks."""
    if len(text) <= chunk_chars:
        return [(text, 0)]

    sep = separators[0] if separators else ""
    remaining_seps = separators[1:] if separators else []

    parts = text.split(sep) if sep else list(text)

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for part in parts:
        part_len = len(part) + (len(sep) if current else 0)
        if current_len + part_len > chunk_chars and current:
            chunk = sep.join(current)
            if len(chunk) > chunk_chars and remaining_seps:
                sub_chunks = _split_recursive(
                    chunk, chunk_chars, overlap_chars, remaining_seps
                )
                chunks.extend(t for t, _ in sub_chunks)
            else:
                chunks.append(chunk)

            # Keep overlap from end of current chunk
            overlap_parts: list[str] = []
            overlap_len = 0
            for p in reversed(current):
                p_len = len(p) + len(sep)
                if overlap_len + p_len > overlap_chars:
                    break
                overlap_parts.insert(0, p)
                overlap_len += p_len

            current = overlap_parts + [part]
            current_len = sum(len(p) for p in current) + len(sep) * (
                len(current) - 1
            )
        else:
            current.append(part)
            current_len += part_len

    if current:
        final = sep.join(current)
        if len(final) > chunk_chars and remaining_seps:
            sub_chunks = _split_recursive(
                final, chunk_chars, overlap_chars, remaining_seps
            )
            chunks.extend(t for t, _ in sub_chunks)
        else:
            chunks.append(final)

    return [(chunk, idx) for idx, chunk in enumerate(chunks)]
