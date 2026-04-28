"""Library tool — CRUD helpers for FastMCP skill registration."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid as _uuid

from openbad.library.store import LibraryStore
from openbad.state.db import initialize_state_db

log = logging.getLogger(__name__)

# Singleton database connection and store
_conn: sqlite3.Connection | None = None
_store: LibraryStore | None = None


def _get_store() -> LibraryStore:
    global _conn, _store  # noqa: PLW0603
    if _store is None:
        _conn = initialize_state_db()
        _store = LibraryStore(_conn)
    return _store


async def _embed_and_store_vectors(
    store: LibraryStore,
    book_id: str,
) -> None:
    """Background task: embed chunks and store vectors."""
    from openbad.memory.controller import make_ollama_embed_fn

    try:
        embed_fn = make_ollama_embed_fn()
        rows = store._conn.execute(
            "SELECT chunk_id, text_content FROM book_chunks WHERE book_id = ?",
            (book_id,),
        ).fetchall()
        if not rows:
            return
        chunk_ids = [r["chunk_id"] for r in rows]
        texts = [r["text_content"] for r in rows]
        embeddings = [embed_fn(t) for t in texts]
        store.store_chunk_vectors(chunk_ids, embeddings)
        log.info("Embedded %d chunks for book %s", len(chunk_ids), book_id)
    except Exception:
        log.exception("Background embedding failed for book %s", book_id)


def search_library(query: str, top_k: int = 5) -> str:
    """Search the Library by vector similarity.

    Returns top matching chunks with book title and ID.
    Requires embeddings to be stored for the books.
    """
    from openbad.memory.controller import make_ollama_embed_fn

    store = _get_store()
    try:
        embed_fn = make_ollama_embed_fn()
        query_embedding = embed_fn(query)
        results = store.search_chunks(query_embedding, top_k=top_k)
    except Exception:
        log.exception("Library search failed")
        return "Library search failed — embeddings may not be available."

    if not results:
        return "No matching library content found."

    lines = [f"Found {len(results)} matches:\n"]
    for r in results:
        preview = r.chunk_text[:200]
        lines.append(
            f"- [{r.book_title}] (book_id={r.book_id}, "
            f"score={r.score:.4f}): {preview}"
        )
    return "\n".join(lines)


def read_book(book_id: str) -> str:
    """Read a Library book by ID.

    Returns the full content, summary, author, and edges.
    """
    store = _get_store()
    book = store.get_book(book_id)
    if book is None:
        return f"Book not found: {book_id}"

    edges_text = ""
    if book.edges:
        edge_lines = [
            f"  - {e.relation_type}: {e.target_book_id}"
            for e in book.edges
        ]
        edges_text = "\nEdges:\n" + "\n".join(edge_lines)

    return (
        f"Title: {book.title}\n"
        f"Author: {book.author}\n"
        f"Summary: {book.summary}\n"
        f"Content:\n{book.content}"
        f"{edges_text}"
    )


def draft_book(section_id: str, title: str, content: str) -> str:
    """Create a new book in the Library.

    Auto-chunks the content synchronously and enqueues background
    embedding so the cognitive router is not blocked.
    Also creates a semantic memory "card catalog" entry with a
    ``library_refs`` pointer so recall can annotate results.
    """
    store = _get_store()
    book_id = store.create_book(section_id, title, content, author="system")

    # Create semantic card catalog entry
    _create_card_catalog_entry(store._conn, book_id, title, content)

    # Enqueue background embedding
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_embed_and_store_vectors(store, book_id))
    except RuntimeError:
        log.debug("No running loop — skipping background embedding")

    return json.dumps({"book_id": book_id, "title": title, "status": "created"})


_VALID_RELATIONS = {"supersedes", "relies_on", "contradicts", "references"}


def link_books(
    source_id: str, target_id: str, relation_type: str
) -> str:
    """Create a citation edge between two books.

    relation_type must be one of: supersedes, relies_on, contradicts,
    references.
    """
    if relation_type not in _VALID_RELATIONS:
        return (
            f"Invalid relation_type: {relation_type!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_RELATIONS))}"
        )
    store = _get_store()
    store.link_books(source_id, target_id, relation_type)
    return json.dumps({
        "source_id": source_id,
        "target_id": target_id,
        "relation_type": relation_type,
        "status": "linked",
    })


# ------------------------------------------------------------------
# Card catalog: semantic memory ↔ library book pointer
# ------------------------------------------------------------------

_SUMMARY_MAX_CHARS = 500


def _create_card_catalog_entry(
    conn: sqlite3.Connection,
    book_id: str,
    title: str,
    content: str,
) -> None:
    """Create or update a semantic engram that points at a Library book.

    The engram key is ``library/{book_id}`` so it is deterministic and
    idempotent.  The ``library_refs`` metadata field enables
    ``recall()`` to annotate results with book availability.
    """
    now = time.time()
    engram_id = _uuid.uuid4().hex[:16]
    key = f"library/{book_id}"
    summary = content[:_SUMMARY_MAX_CHARS]
    metadata = json.dumps({"library_refs": [book_id]})

    try:
        conn.execute(
            """INSERT INTO engrams
               (engram_id, tier, key, concept, content, confidence,
                access_count, last_access_at, created_at, updated_at,
                context, metadata, state)
               VALUES (?, 'semantic', ?, ?, ?, 0.7, 0, ?, ?, ?, ?,
                       ?, 'active')
               ON CONFLICT(engram_id) DO UPDATE SET
                 content  = excluded.content,
                 concept  = excluded.concept,
                 metadata = excluded.metadata,
                 updated_at = excluded.updated_at""",
            (
                engram_id,
                key,
                title,
                summary,
                now,
                now,
                now,
                f"Library book: {title}",
                metadata,
            ),
        )
        conn.commit()
        log.debug("Card catalog entry for book %s (%s)", book_id, title)
    except Exception:
        log.warning(
            "Could not create card catalog entry for book %s",
            book_id,
            exc_info=True,
        )
