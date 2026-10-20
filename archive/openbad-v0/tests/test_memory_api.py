"""Tests for Memory Inspector API endpoints."""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient

from openbad.memory.episodic import EpisodicMemory
from openbad.memory.procedural import ProceduralMemory
from openbad.memory.semantic import SemanticMemory
from openbad.memory.stm import ShortTermMemory
from openbad.wui.memory_api import setup_memory_routes

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def stm() -> ShortTermMemory:
    return ShortTermMemory(max_tokens=32768, default_ttl=7200.0)


@pytest.fixture()
def episodic(tmp_path) -> EpisodicMemory:
    return EpisodicMemory(storage_path=tmp_path / "episodic.json")


@pytest.fixture()
def semantic(tmp_path) -> SemanticMemory:
    return SemanticMemory(storage_path=tmp_path / "semantic.json")


@pytest.fixture()
def procedural(tmp_path) -> ProceduralMemory:
    return ProceduralMemory(storage_path=tmp_path / "procedural.json")


@pytest.fixture()
def app(stm, episodic, semantic, procedural) -> web.Application:
    a = web.Application()
    setup_memory_routes(
        a,
        stm=stm,
        episodic=episodic,
        semantic=semantic,
        procedural=procedural,
    )
    return a


@pytest.fixture()
async def client(app, aiohttp_client) -> TestClient:
    return await aiohttp_client(app)


# ---------------------------------------------------------------------------
# GET /api/memory/stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_empty(client: TestClient) -> None:
    resp = await client.get("/api/memory/stats")
    assert resp.status == 200
    data = await resp.json()
    assert "stm" in data
    assert "episodic" in data
    assert "semantic" in data
    assert "procedural" in data
    assert data["stm"]["entry_count"] == 0
    assert data["episodic"]["entry_count"] == 0


@pytest.mark.asyncio
async def test_stats_with_entries(
    client: TestClient, stm: ShortTermMemory, episodic: EpisodicMemory,
) -> None:
    from openbad.memory.base import MemoryEntry, MemoryTier

    stm.write(MemoryEntry(key="s1", value="hello", tier=MemoryTier.STM))
    episodic.write(MemoryEntry(key="e1", value="event", tier=MemoryTier.EPISODIC))
    resp = await client.get("/api/memory/stats")
    data = await resp.json()
    assert data["stm"]["entry_count"] == 1
    assert data["episodic"]["entry_count"] == 1


# ---------------------------------------------------------------------------
# GET /api/memory/stm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stm_empty(client: TestClient) -> None:
    resp = await client.get("/api/memory/stm")
    assert resp.status == 200
    data = await resp.json()
    assert data["entries"] == []
    assert data["usage"]["entry_count"] == 0


@pytest.mark.asyncio
async def test_stm_with_entries(
    client: TestClient, stm: ShortTermMemory,
) -> None:
    from openbad.memory.base import MemoryEntry, MemoryTier

    stm.write(MemoryEntry(key="key1", value="value1", tier=MemoryTier.STM))
    stm.write(MemoryEntry(key="key2", value="value2", tier=MemoryTier.STM))
    resp = await client.get("/api/memory/stm")
    data = await resp.json()
    assert len(data["entries"]) == 2
    keys = {e["key"] for e in data["entries"]}
    assert keys == {"key1", "key2"}
    # Check computed fields
    for entry in data["entries"]:
        assert "age_seconds" in entry
        assert "ttl_remaining" in entry


# ---------------------------------------------------------------------------
# GET /api/memory/episodic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_episodic_empty(client: TestClient) -> None:
    resp = await client.get("/api/memory/episodic")
    assert resp.status == 200
    data = await resp.json()
    assert data["entries"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_episodic_with_entries(
    client: TestClient, episodic: EpisodicMemory,
) -> None:
    from openbad.memory.base import MemoryEntry, MemoryTier

    episodic.write(MemoryEntry(key="ep1", value="event one", tier=MemoryTier.EPISODIC))
    episodic.write(MemoryEntry(key="ep2", value="event two", tier=MemoryTier.EPISODIC))
    resp = await client.get("/api/memory/episodic")
    data = await resp.json()
    assert len(data["entries"]) == 2
    assert data["total"] == 2


# ---------------------------------------------------------------------------
# GET /api/memory/semantic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_semantic_empty(client: TestClient) -> None:
    resp = await client.get("/api/memory/semantic")
    assert resp.status == 200
    data = await resp.json()
    assert data["entries"] == []


@pytest.mark.asyncio
async def test_semantic_with_entries(
    client: TestClient, semantic: SemanticMemory,
) -> None:
    from openbad.memory.base import MemoryEntry, MemoryTier

    semantic.write(MemoryEntry(key="fact1", value="water is wet", tier=MemoryTier.SEMANTIC))
    resp = await client.get("/api/memory/semantic")
    data = await resp.json()
    assert len(data["entries"]) == 1
    assert data["entries"][0]["key"] == "fact1"
    assert "has_vector" in data["entries"][0]


# ---------------------------------------------------------------------------
# GET /api/memory/procedural
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_procedural_empty(client: TestClient) -> None:
    resp = await client.get("/api/memory/procedural")
    assert resp.status == 200
    data = await resp.json()
    assert data["entries"] == []


@pytest.mark.asyncio
async def test_procedural_with_skill(
    client: TestClient, procedural: ProceduralMemory,
) -> None:
    from openbad.memory.base import MemoryEntry, MemoryTier
    from openbad.memory.procedural import Skill

    skill = Skill(name="greet", description="Say hello", capabilities=["chat"])
    procedural.write(MemoryEntry(key="greet", value=skill, tier=MemoryTier.PROCEDURAL))
    resp = await client.get("/api/memory/procedural")
    data = await resp.json()
    assert len(data["entries"]) == 1
    entry = data["entries"][0]
    assert entry["skill"] is not None
    assert entry["skill"]["name"] == "greet"
    assert entry["skill"]["confidence"] == 0.5


# ---------------------------------------------------------------------------
# GET /api/memory/entry/{key}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_entry_found(
    client: TestClient, stm: ShortTermMemory,
) -> None:
    from openbad.memory.base import MemoryEntry, MemoryTier

    stm.write(MemoryEntry(key="lookup", value="found me", tier=MemoryTier.STM))
    resp = await client.get("/api/memory/entry/lookup")
    assert resp.status == 200
    data = await resp.json()
    assert data["key"] == "lookup"
    assert data["value"] == "found me"


@pytest.mark.asyncio
async def test_get_entry_not_found(client: TestClient) -> None:
    resp = await client.get("/api/memory/entry/ghost")
    assert resp.status == 404


# ---------------------------------------------------------------------------
# POST /api/memory/recall
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_empty(client: TestClient) -> None:
    resp = await client.post(
        "/api/memory/recall", json={"query": "anything"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["results"] == []


@pytest.mark.asyncio
async def test_recall_with_data(
    client: TestClient, semantic: SemanticMemory,
) -> None:
    from openbad.memory.base import MemoryEntry, MemoryTier

    semantic.write(MemoryEntry(key="water", value="water is H2O", tier=MemoryTier.SEMANTIC))
    resp = await client.post(
        "/api/memory/recall", json={"query": "water", "top_k": 5},
    )
    assert resp.status == 200
    data = await resp.json()
    assert len(data["results"]) >= 1
    assert any(r["key"] == "water" for r in data["results"])


@pytest.mark.asyncio
async def test_recall_missing_query(client: TestClient) -> None:
    resp = await client.post("/api/memory/recall", json={})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_recall_invalid_body(client: TestClient) -> None:
    resp = await client.post(
        "/api/memory/recall",
        json="not an object",
    )
    assert resp.status == 400
