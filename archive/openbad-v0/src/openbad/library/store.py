"""CRUD operations for the Library hierarchy backed by SQLite."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass, field

from openbad.library.embedder import chunk_text  # noqa: I001

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BookEdge:
    source_book_id: str
    target_book_id: str
    relation_type: str


@dataclass(frozen=True)
class Book:
    book_id: str
    section_id: str
    title: str
    summary: str
    content: str
    author: str
    created_at: float
    updated_at: float
    edges: list[BookEdge] = field(default_factory=list)


@dataclass(frozen=True)
class ChunkMatch:
    chunk_text: str
    book_id: str
    book_title: str
    score: float


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class LibraryStore:
    """Provides CRUD operations for libraries, shelves, sections, and books."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Library
    # ------------------------------------------------------------------

    def create_library(self, name: str, description: str = "") -> str:
        library_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO libraries (library_id, name, description) VALUES (?, ?, ?)",
            (library_id, name, description),
        )
        self._conn.commit()
        return library_id

    # ------------------------------------------------------------------
    # Shelf
    # ------------------------------------------------------------------

    def create_shelf(
        self, library_id: str, name: str, description: str = ""
    ) -> str:
        shelf_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO shelves (shelf_id, library_id, name, description)"
            " VALUES (?, ?, ?, ?)",
            (shelf_id, library_id, name, description),
        )
        self._conn.commit()
        return shelf_id

    # ------------------------------------------------------------------
    # Section
    # ------------------------------------------------------------------

    def create_section(self, shelf_id: str, name: str) -> str:
        section_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO sections (section_id, shelf_id, name) VALUES (?, ?, ?)",
            (section_id, shelf_id, name),
        )
        self._conn.commit()
        return section_id

    # ------------------------------------------------------------------
    # Book
    # ------------------------------------------------------------------

    def create_book(
        self,
        section_id: str,
        title: str,
        content: str,
        author: str = "user",
        summary: str = "",
    ) -> str:
        book_id = str(uuid.uuid4())
        now = time.time()
        self._conn.execute(
            "INSERT INTO books (book_id, section_id, title, summary, content,"
            " author, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (book_id, section_id, title, summary, content, author, now, now),
        )
        self._store_chunks(book_id, content)
        self._conn.commit()
        return book_id

    def update_book(
        self,
        book_id: str,
        content: str,
        summary: str = "",
    ) -> None:
        now = time.time()
        self._conn.execute(
            "UPDATE books SET content = ?, summary = ?, updated_at = ?"
            " WHERE book_id = ?",
            (content, summary, now, book_id),
        )
        # Remove old chunks and vectors, then re-chunk
        old_chunk_ids = [
            row["chunk_id"]
            for row in self._conn.execute(
                "SELECT chunk_id FROM book_chunks WHERE book_id = ?", (book_id,)
            ).fetchall()
        ]
        if old_chunk_ids:
            placeholders = ",".join("?" for _ in old_chunk_ids)
            self._conn.execute(
                f"DELETE FROM book_chunk_vectors WHERE chunk_id IN ({placeholders})",  # noqa: S608
                old_chunk_ids,
            )
        self._conn.execute(
            "DELETE FROM book_chunks WHERE book_id = ?", (book_id,)
        )
        self._store_chunks(book_id, content)
        self._conn.commit()

    def get_book(self, book_id: str) -> Book | None:
        row = self._conn.execute(
            "SELECT * FROM books WHERE book_id = ?", (book_id,)
        ).fetchone()
        if not row:
            return None

        edge_rows = self._conn.execute(
            "SELECT source_book_id, target_book_id, relation_type"
            " FROM book_edges"
            " WHERE source_book_id = ? OR target_book_id = ?",
            (book_id, book_id),
        ).fetchall()
        edges = [
            BookEdge(
                source_book_id=e["source_book_id"],
                target_book_id=e["target_book_id"],
                relation_type=e["relation_type"],
            )
            for e in edge_rows
        ]

        return Book(
            book_id=row["book_id"],
            section_id=row["section_id"],
            title=row["title"],
            summary=row["summary"],
            content=row["content"],
            author=row["author"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            edges=edges,
        )

    # ------------------------------------------------------------------
    # Tree
    # ------------------------------------------------------------------

    def get_tree(self) -> list[dict]:
        """Return nested hierarchy: libraries → shelves → sections → books."""
        libraries = self._conn.execute(
            "SELECT library_id, name FROM libraries ORDER BY name"
        ).fetchall()

        result = []
        for lib in libraries:
            lib_dict: dict = {
                "library_id": lib["library_id"],
                "name": lib["name"],
                "shelves": [],
            }
            shelves = self._conn.execute(
                "SELECT shelf_id, name FROM shelves WHERE library_id = ? ORDER BY name",
                (lib["library_id"],),
            ).fetchall()
            for shelf in shelves:
                shelf_dict: dict = {
                    "shelf_id": shelf["shelf_id"],
                    "name": shelf["name"],
                    "sections": [],
                }
                sections = self._conn.execute(
                    "SELECT section_id, name FROM sections"
                    " WHERE shelf_id = ? ORDER BY name",
                    (shelf["shelf_id"],),
                ).fetchall()
                for sec in sections:
                    books = self._conn.execute(
                        "SELECT book_id, title FROM books"
                        " WHERE section_id = ? ORDER BY title",
                        (sec["section_id"],),
                    ).fetchall()
                    shelf_dict["sections"].append(
                        {
                            "section_id": sec["section_id"],
                            "name": sec["name"],
                            "books": [
                                {"book_id": b["book_id"], "title": b["title"]}
                                for b in books
                            ],
                        }
                    )
                lib_dict["shelves"].append(shelf_dict)
            result.append(lib_dict)
        return result

    # ------------------------------------------------------------------
    # Edges
    # ------------------------------------------------------------------

    def link_books(
        self, source_id: str, target_id: str, relation_type: str
    ) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO book_edges"
            " (source_book_id, target_book_id, relation_type)"
            " VALUES (?, ?, ?)",
            (source_id, target_id, relation_type),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Vector search
    # ------------------------------------------------------------------

    def search_chunks(
        self, embedding: list[float], top_k: int = 5
    ) -> list[ChunkMatch]:
        rows = self._conn.execute(
            "SELECT v.chunk_id, v.distance, c.text_content, c.book_id"
            " FROM book_chunk_vectors AS v"
            " JOIN book_chunks AS c ON c.chunk_id = v.chunk_id"
            " WHERE v.embedding MATCH ? AND k = ?",
            (serialize_float32(embedding), top_k),
        ).fetchall()

        results: list[ChunkMatch] = []
        for row in rows:
            title_row = self._conn.execute(
                "SELECT title FROM books WHERE book_id = ?", (row["book_id"],)
            ).fetchone()
            results.append(
                ChunkMatch(
                    chunk_text=row["text_content"],
                    book_id=row["book_id"],
                    book_title=title_row["title"] if title_row else "",
                    score=row["distance"],
                )
            )
        return results

    def store_chunk_vectors(
        self, chunk_ids: list[str], embeddings: list[list[float]]
    ) -> None:
        for chunk_id, emb in zip(chunk_ids, embeddings, strict=True):
            self._conn.execute(
                "INSERT INTO book_chunk_vectors (chunk_id, embedding)"
                " VALUES (?, ?)",
                (chunk_id, serialize_float32(emb)),
            )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _store_chunks(self, book_id: str, content: str) -> None:
        """Chunk *content* and insert rows into ``book_chunks``."""
        chunks = chunk_text(content)
        for text, idx in chunks:
            chunk_id = str(uuid.uuid4())
            self._conn.execute(
                "INSERT INTO book_chunks (chunk_id, book_id, chunk_index,"
                " text_content) VALUES (?, ?, ?, ?)",
                (chunk_id, book_id, idx, text),
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def serialize_float32(vec: list[float]) -> bytes:
    """Serialize a float list to the binary format expected by sqlite-vec."""
    import struct

    return struct.pack(f"{len(vec)}f", *vec)
