"""Tests for Library REST API endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient

from openbad.library.store import LibraryStore
from openbad.state.db import initialize_state_db
from openbad.wui.library_api import setup_library_routes

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path):
    conn = initialize_state_db(tmp_path / "test_library.db")
    return conn


@pytest.fixture()
def store(db) -> LibraryStore:
    return LibraryStore(db)


@pytest.fixture()
def app(db) -> web.Application:
    a = web.Application()
    setup_library_routes(a, db)
    return a


@pytest.fixture()
async def client(app, aiohttp_client) -> TestClient:
    return await aiohttp_client(app)


# ---------------------------------------------------------------------------
# Helper to seed a full hierarchy
# ---------------------------------------------------------------------------


def _seed(store: LibraryStore) -> dict[str, str]:
    lib_id = store.create_library("Test Lib", description="desc")
    shelf_id = store.create_shelf(lib_id, "Shelf A")
    section_id = store.create_section(shelf_id, "Section 1")
    book_id = store.create_book(section_id, "My Book", "Hello world content")
    return {
        "library_id": lib_id,
        "shelf_id": shelf_id,
        "section_id": section_id,
        "book_id": book_id,
    }


# ---------------------------------------------------------------------------
# GET /api/library/tree
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tree_empty(client: TestClient) -> None:
    resp = await client.get("/api/library/tree")
    assert resp.status == 200
    data = await resp.json()
    assert data["tree"] == []


@pytest.mark.asyncio
async def test_get_tree_with_data(client: TestClient, store: LibraryStore) -> None:
    _seed(store)
    resp = await client.get("/api/library/tree")
    assert resp.status == 200
    data = await resp.json()
    assert len(data["tree"]) == 1
    lib = data["tree"][0]
    assert lib["name"] == "Test Lib"
    assert len(lib["shelves"]) == 1
    assert len(lib["shelves"][0]["sections"]) == 1
    assert len(lib["shelves"][0]["sections"][0]["books"]) == 1


# ---------------------------------------------------------------------------
# GET /api/library/book/{book_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_book(client: TestClient, store: LibraryStore) -> None:
    ids = _seed(store)
    resp = await client.get(f"/api/library/book/{ids['book_id']}")
    assert resp.status == 200
    data = await resp.json()
    assert data["title"] == "My Book"
    assert data["content"] == "Hello world content"
    assert data["edges"] == []


@pytest.mark.asyncio
async def test_get_book_not_found(client: TestClient) -> None:
    resp = await client.get("/api/library/book/nonexistent")
    assert resp.status == 404


# ---------------------------------------------------------------------------
# POST /api/library/book
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_book(client: TestClient, store: LibraryStore) -> None:
    ids = _seed(store)
    resp = await client.post(
        "/api/library/book",
        json={
            "section_id": ids["section_id"],
            "title": "New Book",
            "content": "Some content",
        },
    )
    assert resp.status == 201
    data = await resp.json()
    assert "book_id" in data

    book = store.get_book(data["book_id"])
    assert book is not None
    assert book.title == "New Book"


@pytest.mark.asyncio
async def test_create_book_missing_title(client: TestClient, store: LibraryStore) -> None:
    ids = _seed(store)
    resp = await client.post(
        "/api/library/book",
        json={"section_id": ids["section_id"], "content": "x"},
    )
    assert resp.status == 400


@pytest.mark.asyncio
async def test_create_book_missing_section(client: TestClient) -> None:
    resp = await client.post(
        "/api/library/book",
        json={"title": "X", "content": "x"},
    )
    assert resp.status == 400


# ---------------------------------------------------------------------------
# PUT /api/library/book/{book_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_book(client: TestClient, store: LibraryStore) -> None:
    ids = _seed(store)
    resp = await client.put(
        f"/api/library/book/{ids['book_id']}",
        json={"content": "Updated content", "summary": "Updated"},
    )
    assert resp.status == 200
    book = store.get_book(ids["book_id"])
    assert book is not None
    assert book.content == "Updated content"
    assert book.summary == "Updated"


@pytest.mark.asyncio
async def test_update_book_not_found(client: TestClient) -> None:
    resp = await client.put(
        "/api/library/book/nonexistent",
        json={"content": "x"},
    )
    assert resp.status == 404


# ---------------------------------------------------------------------------
# POST /api/library/link
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_link(client: TestClient, store: LibraryStore) -> None:
    ids = _seed(store)
    book2_id = store.create_book(ids["section_id"], "Book 2", "Content 2")
    resp = await client.post(
        "/api/library/link",
        json={
            "source_id": ids["book_id"],
            "target_id": book2_id,
            "relation_type": "references",
        },
    )
    assert resp.status == 201
    data = await resp.json()
    assert data["ok"] is True

    book = store.get_book(ids["book_id"])
    assert book is not None
    assert len(book.edges) == 1


@pytest.mark.asyncio
async def test_create_link_missing_fields(client: TestClient) -> None:
    resp = await client.post(
        "/api/library/link",
        json={"source_id": "a", "target_id": "b"},
    )
    assert resp.status == 400


# ---------------------------------------------------------------------------
# POST /api/library/library
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_library(client: TestClient) -> None:
    resp = await client.post(
        "/api/library/library",
        json={"name": "New Lib", "description": "A library"},
    )
    assert resp.status == 201
    data = await resp.json()
    assert "library_id" in data


@pytest.mark.asyncio
async def test_create_library_missing_name(client: TestClient) -> None:
    resp = await client.post("/api/library/library", json={})
    assert resp.status == 400


# ---------------------------------------------------------------------------
# POST /api/library/shelf
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_shelf(client: TestClient, store: LibraryStore) -> None:
    ids = _seed(store)
    resp = await client.post(
        "/api/library/shelf",
        json={"library_id": ids["library_id"], "name": "New Shelf"},
    )
    assert resp.status == 201
    data = await resp.json()
    assert "shelf_id" in data


@pytest.mark.asyncio
async def test_create_shelf_missing_library_id(client: TestClient) -> None:
    resp = await client.post(
        "/api/library/shelf",
        json={"name": "X"},
    )
    assert resp.status == 400


# ---------------------------------------------------------------------------
# POST /api/library/section
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_section(client: TestClient, store: LibraryStore) -> None:
    ids = _seed(store)
    resp = await client.post(
        "/api/library/section",
        json={"shelf_id": ids["shelf_id"], "name": "New Section"},
    )
    assert resp.status == 201
    data = await resp.json()
    assert "section_id" in data


@pytest.mark.asyncio
async def test_create_section_missing_shelf_id(client: TestClient) -> None:
    resp = await client.post(
        "/api/library/section",
        json={"name": "X"},
    )
    assert resp.status == 400


# ---------------------------------------------------------------------------
# POST /api/library/search (no embed_fn → 400)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_no_embed_fn(client: TestClient) -> None:
    resp = await client.post(
        "/api/library/search",
        json={"query": "hello"},
    )
    assert resp.status == 400
