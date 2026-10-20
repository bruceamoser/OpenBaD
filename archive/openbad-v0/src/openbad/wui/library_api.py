"""Library CRUD and search API endpoints.

Registers the following routes on a supplied :class:`aiohttp.web.Application`:

- ``GET  /api/library/tree``                — nested hierarchy (titles + IDs)
- ``GET  /api/library/book/{book_id}``      — full book content + edges
- ``POST /api/library/book``                — create a new book
- ``PUT  /api/library/book/{book_id}``      — update book content
- ``POST /api/library/search``              — vector similarity search
- ``POST /api/library/link``                — create citation edge
- ``POST /api/library/library``             — create a new library
- ``POST /api/library/shelf``               — create a new shelf
- ``POST /api/library/section``             — create a new section

Call :func:`setup_library_routes` to register all routes.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from aiohttp import web

from openbad.library.store import LibraryStore

logger = logging.getLogger(__name__)

_KEY_STORE = "library_api_store"
_KEY_EMBED_FN = "library_api_embed_fn"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _book_to_dict(book: Any) -> dict[str, Any]:
    """Serialise a :class:`Book` to a JSON-safe dict."""
    return {
        "book_id": book.book_id,
        "section_id": book.section_id,
        "title": book.title,
        "summary": book.summary,
        "content": book.content,
        "author": book.author,
        "created_at": book.created_at,
        "updated_at": book.updated_at,
        "edges": [asdict(e) for e in book.edges],
    }


async def _embed_and_store(
    store: LibraryStore,
    book_id: str,
    content: str,
    embed_fn: Callable[[str], list[float]] | None,
) -> None:
    """Chunk *content*, embed each chunk, and store vectors (background)."""
    if embed_fn is None:
        return
    from openbad.library.embedder import chunk_text  # noqa: PLC0415

    chunks = chunk_text(content)
    if not chunks:
        return

    chunk_ids: list[str] = []
    rows = store._conn.execute(  # noqa: SLF001
        "SELECT chunk_id, chunk_index FROM book_chunks WHERE book_id = ?",
        (book_id,),
    ).fetchall()
    idx_to_id = {r["chunk_index"]: r["chunk_id"] for r in rows}
    for _text, idx in chunks:
        cid = idx_to_id.get(idx)
        if cid:
            chunk_ids.append(cid)

    if not chunk_ids:
        return

    texts = [t for t, _ in chunks[: len(chunk_ids)]]
    loop = asyncio.get_running_loop()
    embeddings = await loop.run_in_executor(
        None, lambda: [embed_fn(t) for t in texts]
    )
    store.store_chunk_vectors(chunk_ids, embeddings)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _get_tree(request: web.Request) -> web.Response:
    store: LibraryStore = request.app[_KEY_STORE]
    tree = store.get_tree()
    return web.json_response({"tree": tree})


async def _get_book(request: web.Request) -> web.Response:
    store: LibraryStore = request.app[_KEY_STORE]
    book_id = request.match_info["book_id"]
    book = store.get_book(book_id)
    if book is None:
        raise web.HTTPNotFound(text=f"book {book_id!r} not found")
    return web.json_response(_book_to_dict(book))


async def _post_book(request: web.Request) -> web.Response:
    store: LibraryStore = request.app[_KEY_STORE]
    embed_fn = request.app.get(_KEY_EMBED_FN)
    body = await request.json()
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="request body must be an object")

    section_id = str(body.get("section_id", "")).strip()
    title = str(body.get("title", "")).strip()
    content = str(body.get("content", "")).strip()
    if not section_id:
        raise web.HTTPBadRequest(text="section_id is required")
    if not title:
        raise web.HTTPBadRequest(text="title is required")
    if not content:
        raise web.HTTPBadRequest(text="content is required")

    author = str(body.get("author", "user")).strip()
    summary = str(body.get("summary", "")).strip()

    book_id = store.create_book(
        section_id, title, content, author=author, summary=summary
    )
    asyncio.create_task(_embed_and_store(store, book_id, content, embed_fn))
    return web.json_response({"book_id": book_id}, status=201)


async def _put_book(request: web.Request) -> web.Response:
    store: LibraryStore = request.app[_KEY_STORE]
    embed_fn = request.app.get(_KEY_EMBED_FN)
    book_id = request.match_info["book_id"]

    existing = store.get_book(book_id)
    if existing is None:
        raise web.HTTPNotFound(text=f"book {book_id!r} not found")

    body = await request.json()
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="request body must be an object")

    content = str(body.get("content", "")).strip()
    if not content:
        raise web.HTTPBadRequest(text="content is required")
    summary = str(body.get("summary", "")).strip()

    store.update_book(book_id, content, summary=summary)
    asyncio.create_task(_embed_and_store(store, book_id, content, embed_fn))
    return web.json_response({"book_id": book_id})


async def _post_search(request: web.Request) -> web.Response:
    store: LibraryStore = request.app[_KEY_STORE]
    embed_fn = request.app.get(_KEY_EMBED_FN)
    body = await request.json()
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="request body must be an object")

    query = str(body.get("query", "")).strip()
    if not query:
        raise web.HTTPBadRequest(text="query is required")

    top_k = body.get("top_k", 5)
    try:
        top_k = int(top_k)
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(text="top_k must be an integer") from exc

    if embed_fn is None:
        raise web.HTTPBadRequest(
            text="embedding provider not configured"
        )

    loop = asyncio.get_running_loop()
    embedding = await loop.run_in_executor(None, embed_fn, query)
    matches = store.search_chunks(embedding, top_k=top_k)
    return web.json_response(
        {
            "results": [
                {
                    "chunk_text": m.chunk_text,
                    "book_id": m.book_id,
                    "book_title": m.book_title,
                    "score": m.score,
                }
                for m in matches
            ]
        }
    )


async def _post_link(request: web.Request) -> web.Response:
    store: LibraryStore = request.app[_KEY_STORE]
    body = await request.json()
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="request body must be an object")

    source_id = str(body.get("source_id", "")).strip()
    target_id = str(body.get("target_id", "")).strip()
    relation_type = str(body.get("relation_type", "")).strip()

    if not source_id:
        raise web.HTTPBadRequest(text="source_id is required")
    if not target_id:
        raise web.HTTPBadRequest(text="target_id is required")
    if not relation_type:
        raise web.HTTPBadRequest(text="relation_type is required")

    store.link_books(source_id, target_id, relation_type)
    return web.json_response({"ok": True}, status=201)


async def _post_library(request: web.Request) -> web.Response:
    store: LibraryStore = request.app[_KEY_STORE]
    body = await request.json()
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="request body must be an object")

    name = str(body.get("name", "")).strip()
    if not name:
        raise web.HTTPBadRequest(text="name is required")
    description = str(body.get("description", "")).strip()

    library_id = store.create_library(name, description=description)
    return web.json_response({"library_id": library_id}, status=201)


async def _post_shelf(request: web.Request) -> web.Response:
    store: LibraryStore = request.app[_KEY_STORE]
    body = await request.json()
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="request body must be an object")

    library_id = str(body.get("library_id", "")).strip()
    name = str(body.get("name", "")).strip()
    if not library_id:
        raise web.HTTPBadRequest(text="library_id is required")
    if not name:
        raise web.HTTPBadRequest(text="name is required")
    description = str(body.get("description", "")).strip()

    shelf_id = store.create_shelf(library_id, name, description=description)
    return web.json_response({"shelf_id": shelf_id}, status=201)


async def _post_section(request: web.Request) -> web.Response:
    store: LibraryStore = request.app[_KEY_STORE]
    body = await request.json()
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="request body must be an object")

    shelf_id = str(body.get("shelf_id", "")).strip()
    name = str(body.get("name", "")).strip()
    if not shelf_id:
        raise web.HTTPBadRequest(text="shelf_id is required")
    if not name:
        raise web.HTTPBadRequest(text="name is required")

    section_id = store.create_section(shelf_id, name)
    return web.json_response({"section_id": section_id}, status=201)


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def setup_library_routes(
    app: web.Application,
    conn: sqlite3.Connection,
    *,
    embed_fn: Callable[[str], list[float]] | None = None,
) -> None:
    """Register Library API routes on *app*.

    Parameters
    ----------
    app:
        The :class:`aiohttp.web.Application` to register routes on.
    conn:
        An open SQLite connection (must have Library tables from
        migration 0006).
    embed_fn:
        Optional synchronous embedding function ``text → vector``.
        When provided, book creation/update triggers background
        embedding, and ``/api/library/search`` is enabled.
    """
    app[_KEY_STORE] = LibraryStore(conn)
    if embed_fn is not None:
        app[_KEY_EMBED_FN] = embed_fn

    app.router.add_get("/api/library/tree", _get_tree)
    app.router.add_get("/api/library/book/{book_id}", _get_book)
    app.router.add_post("/api/library/book", _post_book)
    app.router.add_put("/api/library/book/{book_id}", _put_book)
    app.router.add_post("/api/library/search", _post_search)
    app.router.add_post("/api/library/link", _post_link)
    app.router.add_post("/api/library/library", _post_library)
    app.router.add_post("/api/library/shelf", _post_shelf)
    app.router.add_post("/api/library/section", _post_section)
