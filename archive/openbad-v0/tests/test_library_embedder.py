"""Tests for the text chunker."""

from __future__ import annotations

from openbad.library.embedder import chunk_text


class TestChunkText:
    def test_empty_string_returns_empty(self) -> None:
        assert chunk_text("") == []

    def test_short_text_returns_single_chunk(self) -> None:
        text = "Hello world."
        result = chunk_text(text)
        assert len(result) == 1
        assert result[0] == (text, 0)

    def test_text_at_exact_limit_returns_single_chunk(self) -> None:
        # Default chunk_tokens=500, chars_per_token=4 → 2000 chars
        text = "a" * 2000
        result = chunk_text(text)
        assert len(result) == 1

    def test_long_text_produces_multiple_chunks(self) -> None:
        # Create text well beyond 2000 chars
        text = "word " * 1000  # 5000 chars
        result = chunk_text(text)
        assert len(result) > 1
        # Each chunk index should be sequential
        for i, (_, idx) in enumerate(result):
            assert idx == i

    def test_chunk_indices_are_sequential(self) -> None:
        text = ("paragraph one. " * 200) + "\n\n" + ("paragraph two. " * 200)
        result = chunk_text(text)
        indices = [idx for _, idx in result]
        assert indices == list(range(len(result)))

    def test_custom_chunk_size(self) -> None:
        text = "word " * 100  # 500 chars
        result = chunk_text(text, chunk_tokens=25)  # 100 chars per chunk
        assert len(result) > 1

    def test_splits_on_paragraph_boundaries(self) -> None:
        para_a = "A" * 1500
        para_b = "B" * 1500
        text = para_a + "\n\n" + para_b
        result = chunk_text(text)
        assert len(result) >= 2
        # First chunk should start with A, last with B
        assert result[0][0].startswith("A")
        assert result[-1][0].startswith("B") or "B" in result[-1][0]

    def test_overlap_produces_shared_content(self) -> None:
        # With overlap, consecutive chunks should share some text
        sentences = [f"Sentence number {i}. " for i in range(100)]
        text = " ".join(sentences)
        result = chunk_text(text, chunk_tokens=50, overlap_tokens=10)
        if len(result) >= 2:
            # Last words of chunk 0 should appear near start of chunk 1
            words_end_0 = set(result[0][0].split()[-5:])
            words_start_1 = set(result[1][0].split()[:20])
            assert words_end_0 & words_start_1, "Expected overlap between chunks"

    def test_no_empty_chunks(self) -> None:
        text = "hello " * 500
        result = chunk_text(text)
        for chunk, _ in result:
            assert chunk.strip() != ""
