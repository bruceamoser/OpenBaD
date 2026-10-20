"""Tests for exploration action generator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openbad.active_inference.exploration_actions import ExplorationActionGenerator
from openbad.active_inference.insight_queue import InsightQueue


@pytest.mark.asyncio
async def test_process_high_surprise_basic():
    """Test basic exploration action generation."""
    queue = InsightQueue()
    generator = ExplorationActionGenerator(
        insight_queue=queue,
        submit_task_fn=None,
        episodic_memory=None,
    )

    action = await generator.process_high_surprise(
        source_id="email",
        surprise=0.85,
        errors={"unread_count": 12.5},
    )

    assert action is not None
    assert action.source_id == "email"
    assert action.priority == 0.85
    assert "High surprise" in action.trigger

    insights = await queue.get_pending(limit=10)
    assert len(insights) == 1
    assert insights[0].source == "email"


@pytest.mark.asyncio
async def test_surprise_clamped_to_one():
    """Test priority is clamped to 1.0 even with very high surprise."""
    queue = InsightQueue()
    generator = ExplorationActionGenerator(queue)

    action = await generator.process_high_surprise(
        source_id="test",
        surprise=2.5,
        errors={},
    )

    assert action.priority == 1.0


@pytest.mark.asyncio
async def test_submit_task_fn_called():
    """Test submit_task_fn is called when provided."""
    queue = InsightQueue()
    submit_mock = AsyncMock()
    generator = ExplorationActionGenerator(
        insight_queue=queue,
        submit_task_fn=submit_mock,
    )

    await generator.process_high_surprise(
        source_id="calendar",
        surprise=0.7,
        errors={"conflict_count": 3.0},
    )

    submit_mock.assert_called_once()
    call_args = submit_mock.call_args[0][0]
    assert call_args.source_id == "calendar"


@pytest.mark.asyncio
async def test_episodic_memory_storage():
    """Test discoveries are stored in episodic memory."""
    queue = InsightQueue()
    memory_mock = MagicMock()
    memory_mock.append = AsyncMock()

    generator = ExplorationActionGenerator(
        insight_queue=queue,
        episodic_memory=memory_mock,
    )

    await generator.process_high_surprise(
        source_id="browser",
        surprise=0.65,
        errors={"visit_frequency": 8.2},
    )

    memory_mock.append.assert_called_once()
    call_args = memory_mock.append.call_args
    assert call_args[0][0] == "discovery"
    assert "curiosity" in call_args[1]["tags"]


@pytest.mark.asyncio
async def test_summary_generation_with_errors():
    """Test summary includes top error metric."""
    queue = InsightQueue()
    generator = ExplorationActionGenerator(queue)

    await generator.process_high_surprise(
        source_id="system",
        surprise=0.9,
        errors={
            "cpu_usage": 5.2,
            "disk_usage": 15.8,
            "memory_usage": 3.1,
        },
    )

    insights = await queue.get_pending(limit=1)
    assert "disk_usage" in insights[0].summary


@pytest.mark.asyncio
async def test_summary_generation_no_errors():
    """Test summary when no error details available."""
    queue = InsightQueue()
    generator = ExplorationActionGenerator(queue)

    await generator.process_high_surprise(
        source_id="test",
        surprise=0.5,
        errors={},
    )

    insights = await queue.get_pending(limit=1)
    assert "Novel pattern" in insights[0].summary


@pytest.mark.asyncio
async def test_raw_data_included_in_context():
    """Test raw_data is included in action context."""
    queue = InsightQueue()
    generator = ExplorationActionGenerator(queue)

    raw_data = {"emails": ["email1@example.com", "email2@example.com"]}
    action = await generator.process_high_surprise(
        source_id="email",
        surprise=0.6,
        errors={},
        raw_data=raw_data,
    )

    assert action.context["raw_data"] == raw_data


@pytest.mark.asyncio
async def test_episodic_storage_error_handling():
    """Test episodic storage errors don't crash generator."""
    queue = InsightQueue()
    memory_mock = MagicMock()
    memory_mock.append = AsyncMock(side_effect=RuntimeError("Storage error"))

    generator = ExplorationActionGenerator(
        insight_queue=queue,
        episodic_memory=memory_mock,
    )

    action = await generator.process_high_surprise(
        source_id="test",
        surprise=0.5,
        errors={},
    )

    assert action is not None
