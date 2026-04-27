"""Tests for LibraryStore CRUD, tree retrieval, edges, and chunk storage."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from openbad.library.store import LibraryStore, serialize_float32
from openbad.state.db import initialize_state_db


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    """In-memory-like temp DB with all migrations applied."""
    db_path = tmp_path / "test.db"
    connection = initialize_state_db(db_path)
    yield connection
    connection.close()


@pytest.fixture()
def store(conn: sqlite3.Connection) -> LibraryStore:
    return LibraryStore(conn)


# ------------------------------------------------------------------
# Library / Shelf / Section CRUD
# ------------------------------------------------------------------


class TestLibraryCRUD:
    def test_create_library(self, store: LibraryStore) -> None:
        lib_id = store.create_library("Main", "Primary library")
        assert lib_id  # non-empty UUID

    def test_create_shelf(self, store: LibraryStore) -> None:
        lib_id = store.create_library("Lib")
        shelf_id = store.create_shelf(lib_id, "Reference", "Ref books")
        assert shelf_id

    def test_create_section(self, store: LibraryStore) -> None:
        lib_id = store.create_library("Lib")
        shelf_id = store.create_shelf(lib_id, "Shelf")
        section_id = store.create_section(shelf_id, "Chapter 1")
        assert section_id


# ------------------------------------------------------------------
# Book CRUD
# ------------------------------------------------------------------


class TestBookCRUD:
    def test_create_and_get_book(self, store: LibraryStore) -> None:
        lib_id = store.create_library("Lib")
        shelf_id = store.create_shelf(lib_id, "Shelf")
        sec_id = store.create_section(shelf_id, "Sec")
        book_id = store.create_book(
            sec_id, "My Book", "Some content here.", author="user", summary="A book"
        )
        book = store.get_book(book_id)
        assert book is not None
        assert book.title == "My Book"
        assert book.content == "Some content here."
        assert book.author == "user"
        assert book.summary == "A book"

    def test_get_nonexistent_book(self, store: LibraryStore) -> None:
        assert store.get_book("nonexistent") is None

    def test_update_book(self, store: LibraryStore) -> None:
        lib_id = store.create_library("Lib")
        shelf_id = store.create_shelf(lib_id, "Shelf")
        sec_id = store.create_section(shelf_id, "Sec")
        book_id = store.create_book(sec_id, "Title", "Old content")
        store.update_book(book_id, "New content", summary="Updated")
        book = store.get_book(book_id)
        assert book is not None
        assert book.content == "New content"
        assert book.summary == "Updated"

    def test_create_book_stores_chunks(
        self, store: LibraryStore, conn: sqlite3.Connection
    ) -> None:
        lib_id = store.create_library("Lib")
        shelf_id = store.create_shelf(lib_id, "Shelf")
        sec_id = store.create_section(shelf_id, "Sec")
        book_id = store.create_book(sec_id, "Title", "Some text content")
        rows = conn.execute(
            "SELECT * FROM book_chunks WHERE book_id = ?", (book_id,)
        ).fetchall()
        assert len(rows) >= 1
        assert rows[0]["text_content"] == "Some text content"

    def test_update_book_replaces_chunks(
        self, store: LibraryStore, conn: sqlite3.Connection
    ) -> None:
        lib_id = store.create_library("Lib")
        shelf_id = store.create_shelf(lib_id, "Shelf")
        sec_id = store.create_section(shelf_id, "Sec")
        book_id = store.create_book(sec_id, "Title", "Original text")
        store.update_book(book_id, "Replacement text")
        rows = conn.execute(
            "SELECT text_content FROM book_chunks WHERE book_id = ?", (book_id,)
        ).fetchall()
        assert len(rows) >= 1
        assert rows[0]["text_content"] == "Replacement text"


# ------------------------------------------------------------------
# Tree
# ------------------------------------------------------------------


class TestGetTree:
    def test_empty_tree(self, store: LibraryStore) -> None:
        assert store.get_tree() == []

    def test_full_tree(self, store: LibraryStore) -> None:
        lib_id = store.create_library("Lib")
        shelf_id = store.create_shelf(lib_id, "Shelf")
        sec_id = store.create_section(shelf_id, "Sec")
        store.create_book(sec_id, "Book A", "content")

        tree = store.get_tree()
        assert len(tree) == 1
        assert tree[0]["name"] == "Lib"
        assert len(tree[0]["shelves"]) == 1
        assert tree[0]["shelves"][0]["name"] == "Shelf"
        sections = tree[0]["shelves"][0]["sections"]
        assert len(sections) == 1
        assert sections[0]["name"] == "Sec"
        assert len(sections[0]["books"]) == 1
        assert sections[0]["books"][0]["title"] == "Book A"


# ------------------------------------------------------------------
# Edges
# ------------------------------------------------------------------


class TestBookEdges:
    def test_link_books(self, store: LibraryStore) -> None:
        lib_id = store.create_library("Lib")
        shelf_id = store.create_shelf(lib_id, "Shelf")
        sec_id = store.create_section(shelf_id, "Sec")
        book_a = store.create_book(sec_id, "A", "content a")
        book_b = store.create_book(sec_id, "B", "content b")
        store.link_books(book_a, book_b, "references")

        book = store.get_book(book_a)
        assert book is not None
        assert len(book.edges) == 1
        assert book.edges[0].relation_type == "references"
        assert book.edges[0].target_book_id == book_b

    def test_duplicate_link_ignored(self, store: LibraryStore) -> None:
        lib_id = store.create_library("Lib")
        shelf_id = store.create_shelf(lib_id, "Shelf")
        sec_id = store.create_section(shelf_id, "Sec")
        book_a = store.create_book(sec_id, "A", "content a")
        book_b = store.create_book(sec_id, "B", "content b")
        store.link_books(book_a, book_b, "references")
        store.link_books(book_a, book_b, "references")  # should not raise

        book = store.get_book(book_a)
        assert book is not None
        assert len(book.edges) == 1


# ------------------------------------------------------------------
# Vector storage and search
# ------------------------------------------------------------------


class TestVectorSearch:
    def test_store_and_search_vectors(self, store: LibraryStore) -> None:
        lib_id = store.create_library("Lib")
        shelf_id = store.create_shelf(lib_id, "Shelf")
        sec_id = store.create_section(shelf_id, "Sec")
        book_id = store.create_book(sec_id, "VecBook", "Some vector-searchable text")

        # Grab the chunk IDs that were created
        rows = store._conn.execute(
            "SELECT chunk_id FROM book_chunks WHERE book_id = ?", (book_id,)
        ).fetchall()
        chunk_ids = [r["chunk_id"] for r in rows]
        assert len(chunk_ids) >= 1

        # Create a 768-dim embedding for each chunk
        embeddings = [[0.1] * 768 for _ in chunk_ids]
        store.store_chunk_vectors(chunk_ids, embeddings)

        # Search with the same embedding — should match
        query_emb = [0.1] * 768
        results = store.search_chunks(query_emb, top_k=5)
        assert len(results) >= 1
        assert results[0].book_title == "VecBook"
        assert results[0].book_id == book_id


class TestSerializeFloat32:
    def test_round_trip(self) -> None:
        import struct

        vec = [1.0, 2.0, 3.0]
        data = serialize_float32(vec)
        assert len(data) == 12  # 3 * 4 bytes
        unpacked = list(struct.unpack("3f", data))
        assert unpacked == pytest.approx(vec)
