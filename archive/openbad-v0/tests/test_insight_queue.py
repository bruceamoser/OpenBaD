"""Tests for proactive insight queue."""

from __future__ import annotations

import asyncio

import pytest

from openbad.active_inference.insight_queue import InsightQueue


@pytest.mark.asyncio
async def test_add_insight():
    """Test adding insights to the queue."""
    queue = InsightQueue(max_size=5)
    insight_id = await queue.add(
        source="email",
        summary="Test insight",
        details={"key": "value"},
        priority=0.8,
    )
    assert insight_id.startswith("insight_")
    pending = await queue.get_pending(limit=10)
    assert len(pending) == 1
    assert pending[0].source == "email"
    assert pending[0].summary == "Test insight"


@pytest.mark.asyncio
async def test_priority_sorting():
    """Test insights are sorted by priority."""
    queue = InsightQueue(max_size=10)
    await queue.add("src1", "Low priority", {}, 0.3)
    await queue.add("src2", "High priority", {}, 0.9)
    await queue.add("src3", "Medium priority", {}, 0.6)

    pending = await queue.get_pending(limit=10)
    assert len(pending) == 3
    assert pending[0].summary == "High priority"
    assert pending[1].summary == "Medium priority"
    assert pending[2].summary == "Low priority"


@pytest.mark.asyncio
async def test_max_size_enforcement():
    """Test queue respects max_size limit."""
    queue = InsightQueue(max_size=3)
    for i in range(5):
        await queue.add(f"src{i}", f"Insight {i}", {}, priority=float(i))

    pending = await queue.get_pending(limit=10)
    assert len(pending) == 3
    assert all(int(p.summary.split()[1]) >= 2 for p in pending)


@pytest.mark.asyncio
async def test_dismiss_insight():
    """Test dismissing an insight."""
    queue = InsightQueue()
    insight_id = await queue.add("email", "Test", {}, 0.5)

    dismissed = await queue.dismiss(insight_id)
    assert dismissed is True

    pending = await queue.get_pending(limit=10)
    assert len(pending) == 0


@pytest.mark.asyncio
async def test_dismiss_nonexistent():
    """Test dismissing nonexistent insight returns False."""
    queue = InsightQueue()
    dismissed = await queue.dismiss("nonexistent_id")
    assert dismissed is False


@pytest.mark.asyncio
async def test_get_pending_limit():
    """Test get_pending respects limit parameter."""
    queue = InsightQueue()
    for i in range(10):
        await queue.add(f"src{i}", f"Insight {i}", {}, priority=0.5)

    pending = await queue.get_pending(limit=5)
    assert len(pending) == 5


@pytest.mark.asyncio
async def test_get_pending_excludes_dismissed():
    """Test get_pending excludes dismissed insights."""
    queue = InsightQueue()
    await queue.add("src1", "Keep", {}, 0.5)
    id2 = await queue.add("src2", "Dismiss", {}, 0.6)
    await queue.add("src3", "Keep", {}, 0.4)

    await queue.dismiss(id2)
    pending = await queue.get_pending(limit=10)
    assert len(pending) == 2
    assert all(p.summary != "Dismiss" for p in pending)


@pytest.mark.asyncio
async def test_clear_dismissed():
    """Test clearing dismissed insights."""
    queue = InsightQueue()
    id1 = await queue.add("src1", "One", {}, 0.5)
    await queue.add("src2", "Two", {}, 0.6)
    id3 = await queue.add("src3", "Three", {}, 0.4)

    await queue.dismiss(id1)
    await queue.dismiss(id3)

    removed = await queue.clear_dismissed()
    assert removed == 2

    pending = await queue.get_pending(limit=10)
    assert len(pending) == 1
    assert pending[0].summary == "Two"


@pytest.mark.asyncio
async def test_count_pending():
    """Test counting pending insights."""
    queue = InsightQueue()
    assert await queue.count_pending() == 0

    await queue.add("src1", "One", {}, 0.5)
    await queue.add("src2", "Two", {}, 0.6)
    assert await queue.count_pending() == 2

    id1 = queue._insights[0].id
    await queue.dismiss(id1)
    assert await queue.count_pending() == 1


@pytest.mark.asyncio
async def test_concurrent_access():
    """Test thread-safe concurrent access."""
    queue = InsightQueue()

    async def add_many(offset: int) -> None:
        for i in range(10):
            await queue.add(
                f"src{offset}_{i}",
                f"Insight {offset}_{i}",
                {},
                0.5,
            )

    await asyncio.gather(add_many(0), add_many(1), add_many(2))
    pending = await queue.get_pending(limit=100)
    assert len(pending) == 30
