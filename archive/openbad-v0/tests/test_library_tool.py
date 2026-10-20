"""Tests for library tool skill functions."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from openbad.library.store import LibraryStore
from openbad.skills import library_tool
from openbad.state.db import initialize_state_db


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "test.db"
    connection = initialize_state_db(db_path)
    yield connection
    connection.close()


@pytest.fixture()
def store(conn: sqlite3.Connection) -> LibraryStore:
    return LibraryStore(conn)


@pytest.fixture(autouse=True)
def _patch_store(store: LibraryStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject test store into the library_tool module."""
    monkeypatch.setattr(library_tool, "_store", store)
    monkeypatch.setattr(library_tool, "_conn", store._conn)


def _scaffold(store: LibraryStore) -> tuple[str, str, str]:
    """Create library → shelf → section and return (lib_id, shelf_id, sec_id)."""
    lib_id = store.create_library("TestLib")
    shelf_id = store.create_shelf(lib_id, "TestShelf")
    sec_id = store.create_section(shelf_id, "TestSection")
    return lib_id, shelf_id, sec_id


class TestReadBook:
    def test_read_existing_book(self, store: LibraryStore) -> None:
        _, _, sec_id = _scaffold(store)
        book_id = store.create_book(sec_id, "My Book", "Book content here")
        result = library_tool.read_book(book_id)
        assert "My Book" in result
        assert "Book content here" in result

    def test_read_nonexistent_book(self) -> None:
        result = library_tool.read_book("nonexistent-id")
        assert "not found" in result.lower()

    def test_read_book_with_edges(self, store: LibraryStore) -> None:
        _, _, sec_id = _scaffold(store)
        book_a = store.create_book(sec_id, "A", "Content A")
        book_b = store.create_book(sec_id, "B", "Content B")
        store.link_books(book_a, book_b, "references")
        result = library_tool.read_book(book_a)
        assert "references" in result
        assert book_b in result


class TestDraftBook:
    def test_draft_creates_book(self, store: LibraryStore) -> None:
        _, _, sec_id = _scaffold(store)
        result = library_tool.draft_book(sec_id, "New Book", "Some content")
        data = json.loads(result)
        assert data["status"] == "created"
        assert data["title"] == "New Book"
        assert data["book_id"]

        # Verify book exists in store
        book = store.get_book(data["book_id"])
        assert book is not None
        assert book.content == "Some content"
        assert book.author == "system"


class TestLinkBooks:
    def test_valid_relation(self, store: LibraryStore) -> None:
        _, _, sec_id = _scaffold(store)
        a = store.create_book(sec_id, "A", "a")
        b = store.create_book(sec_id, "B", "b")
        result = library_tool.link_books(a, b, "supersedes")
        data = json.loads(result)
        assert data["status"] == "linked"
        assert data["relation_type"] == "supersedes"

    def test_invalid_relation(self, store: LibraryStore) -> None:
        _, _, sec_id = _scaffold(store)
        a = store.create_book(sec_id, "A", "a")
        b = store.create_book(sec_id, "B", "b")
        result = library_tool.link_books(a, b, "invalid_type")
        assert "Invalid" in result

    def test_all_valid_relations(self, store: LibraryStore) -> None:
        _, _, sec_id = _scaffold(store)
        for rel in ("supersedes", "relies_on", "contradicts", "references"):
            a = store.create_book(sec_id, f"A-{rel}", "a")
            b = store.create_book(sec_id, f"B-{rel}", "b")
            result = library_tool.link_books(a, b, rel)
            data = json.loads(result)
            assert data["status"] == "linked"


class TestSearchLibrary:
    def test_search_no_embeddings_returns_message(
        self, store: LibraryStore
    ) -> None:
        # Without embeddings stored, search should return a "no matches" message
        result = library_tool.search_library("test query")
        assert "no matching" in result.lower() or "failed" in result.lower()

    def test_search_with_embeddings(self, store: LibraryStore) -> None:
        _, _, sec_id = _scaffold(store)
        book_id = store.create_book(sec_id, "Embedded Book", "Content to search")

        # Store vectors manually
        rows = store._conn.execute(
            "SELECT chunk_id FROM book_chunks WHERE book_id = ?", (book_id,)
        ).fetchall()
        chunk_ids = [r["chunk_id"] for r in rows]
        embeddings = [[0.1] * 768 for _ in chunk_ids]
        store.store_chunk_vectors(chunk_ids, embeddings)

        # Monkey-patch embed_fn to return matching vector
        from unittest.mock import patch

        with patch(
            "openbad.memory.controller.make_ollama_embed_fn",
            return_value=lambda text: [0.1] * 768,
        ):
            result = library_tool.search_library("content")
            assert "Embedded Book" in result
