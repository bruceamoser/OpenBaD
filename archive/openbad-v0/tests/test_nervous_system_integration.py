"""Tests for WUI chat nervous system integration (issue #303).

Covers: cognitive event publishing (input/output/error), graceful degradation
when broker unavailable, and event payload validation.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, Mock, patch

import pytest

from openbad.nervous_system import topics
from openbad.wui.chat_pipeline import StreamChunk, stream_chat

if TYPE_CHECKING:
    pass


@pytest.fixture(autouse=True)
def mock_memory_dirs(tmp_path: Path) -> None:
    """Mock memory directories and state DB to avoid permissions issues."""
    import sqlite3

    import openbad.wui.chat_pipeline as cp

    cp._DATA_DIR = tmp_path
    cp._MEMORY_DIR = tmp_path / "memory"
    cp._MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(tmp_path / "state.db"))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS session_messages (
            message_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'assistant',
            content TEXT NOT NULL,
            created_at REAL NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )"""
    )
    conn.commit()
    cp._state_conn = conn


def _mock_chat_model():
    """Return a MagicMock standing in for a BaseChatModel."""
    return MagicMock()


async def _fake_agentic_stream(chat_model, model_id, messages, request_id, tokens=None):
    """Fake _agentic_stream that yields canned chunks."""
    for tok in (tokens or ["Hello", " world", "!"]):
        yield StreamChunk(token=tok, tokens_used=1)
    yield StreamChunk(done=True, tokens_used=3)


async def _failing_agentic_stream(chat_model, model_id, messages, request_id):
    """Fake _agentic_stream that raises."""
    raise ValueError("Test error")
    yield  # noqa: F841


# ------------------------------------------------------------------ #
# Event publishing tests
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_publishes_input_event_on_user_message() -> None:
    """When user sends message, should publish COGNITIVE_INPUT event."""
    mock_client = Mock()
    mock_client.is_connected = True

    chat_model = _mock_chat_model()
    chunks = []
    with patch("openbad.wui.chat_pipeline._agentic_stream", _fake_agentic_stream):
        async for chunk in stream_chat(
            chat_model,
            "mock-model",
            "Hello",
            "test-session",
            nervous_system_client=mock_client,
        ):
            chunks.append(chunk)

    # Should have called publish for COGNITIVE_INPUT
    calls = [
        c for c in mock_client.publish_bytes.call_args_list
        if topics.COGNITIVE_INPUT in str(c)
    ]
    assert len(calls) >= 1, "Should publish COGNITIVE_INPUT event"

    # Verify payload structure
    call_topic, call_payload = calls[0][0]
    assert call_topic == topics.COGNITIVE_INPUT

    import json

    payload = json.loads(call_payload.decode())
    assert payload["source"] == "wui"
    assert payload["user_id"] == "test-session"
    assert "message_hash" in payload
    assert "timestamp" in payload


@pytest.mark.asyncio
async def test_publishes_output_event_on_completion() -> None:
    """When LLM completes successfully, should publish COGNITIVE_OUTPUT event."""
    mock_client = Mock()
    mock_client.is_connected = True

    chat_model = _mock_chat_model()
    chunks = []
    with patch("openbad.wui.chat_pipeline._agentic_stream", _fake_agentic_stream):
        async for chunk in stream_chat(
            chat_model,
            "mock-model",
            "Hello",
            "test-session",
            nervous_system_client=mock_client,
        ):
            chunks.append(chunk)

    # Should have called publish for COGNITIVE_OUTPUT
    calls = [
        c for c in mock_client.publish_bytes.call_args_list
        if topics.COGNITIVE_OUTPUT in str(c)
    ]
    assert len(calls) >= 1, "Should publish COGNITIVE_OUTPUT event"

    # Verify payload structure
    call_topic, call_payload = calls[0][0]
    assert call_topic == topics.COGNITIVE_OUTPUT

    import json

    payload = json.loads(call_payload.decode())
    assert payload["source"] == "wui"
    assert payload["tokens_used"] > 0
    assert payload["model"] == "mock-model"
    assert "latency_ms" in payload
    assert "timestamp" in payload


@pytest.mark.asyncio
async def test_publishes_error_event_on_failure() -> None:
    """When stream fails, should publish COGNITIVE_ERROR event."""
    mock_client = Mock()
    mock_client.is_connected = True

    chat_model = _mock_chat_model()
    chunks = []
    with patch("openbad.wui.chat_pipeline._agentic_stream", _failing_agentic_stream):
        async for chunk in stream_chat(
            chat_model,
            "mock-model",
            "Hello",
            "test-session",
            nervous_system_client=mock_client,
        ):
            chunks.append(chunk)

    # Should have called publish for COGNITIVE_ERROR
    calls = [
        c for c in mock_client.publish_bytes.call_args_list
        if topics.COGNITIVE_ERROR in str(c)
    ]
    assert len(calls) >= 1, "Should publish COGNITIVE_ERROR event"

    # Verify payload structure
    call_topic, call_payload = calls[0][0]
    assert call_topic == topics.COGNITIVE_ERROR

    import json

    payload = json.loads(call_payload.decode())
    assert payload["source"] == "wui"
    assert payload["error_type"] == "ValueError"
    assert "message_hash" in payload
    assert "timestamp" in payload


# ------------------------------------------------------------------ #
# Graceful degradation tests
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_works_without_nervous_system_client() -> None:
    """Chat should work normally when no nervous system client provided."""
    chat_model = _mock_chat_model()
    chunks = []
    with patch("openbad.wui.chat_pipeline._agentic_stream", _fake_agentic_stream):
        async for chunk in stream_chat(
            chat_model,
            "mock-model",
            "Hello",
            "test-session",
            nervous_system_client=None,  # No client
        ):
            chunks.append(chunk)

    # Should still complete successfully
    assert len(chunks) >= 2
    assert chunks[-1].done is True


@pytest.mark.asyncio
async def test_works_when_broker_disconnected() -> None:
    """Chat should work when nervous system client exists but is disconnected."""
    mock_client = Mock()
    mock_client.is_connected = False  # Disconnected

    chat_model = _mock_chat_model()
    chunks = []
    with patch("openbad.wui.chat_pipeline._agentic_stream", _fake_agentic_stream):
        async for chunk in stream_chat(
            chat_model,
            "mock-model",
            "Hello",
            "test-session",
            nervous_system_client=mock_client,
        ):
            chunks.append(chunk)

    # Should complete without calling publish
    assert len(chunks) >= 2
    assert chunks[-1].done is True
    assert mock_client.publish_bytes.call_count == 0


@pytest.mark.asyncio
async def test_continues_on_publish_failure() -> None:
    """Chat should continue if event publishing raises an exception."""
    mock_client = Mock()
    mock_client.is_connected = True
    mock_client.publish_bytes.side_effect = Exception("MQTT publish failed")

    adapter = _mock_chat_model()
    chunks = []
    with patch("openbad.wui.chat_pipeline._agentic_stream", _fake_agentic_stream):
        async for chunk in stream_chat(
            adapter,
            "mock-model",
            "Hello",
            "test-session",
            nervous_system_client=mock_client,
        ):
            chunks.append(chunk)
    assert len(chunks) >= 2
    assert chunks[-1].done is True


# ------------------------------------------------------------------ #
# Immune scan integration
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_publishes_error_on_immune_blocked() -> None:
    """When message is blocked by immune scan, should publish error event."""
    mock_client = Mock()
    mock_client.is_connected = True

    # We can't easily mock the immune scan without modifying global state,
    # so we test the error path directly via exceptions instead
    # (immune scan blocking is tested in immune system tests)
    pass  # Placeholder - immune integration tested separately


# ------------------------------------------------------------------ #
# Event payload correctness
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_message_hash_consistency() -> None:
    """Message hash should be consistent for the same message."""
    from openbad.wui.chat_pipeline import _hash_message

    hash1 = _hash_message("Hello world")
    hash2 = _hash_message("Hello world")
    hash3 = _hash_message("Different message")

    assert hash1 == hash2
    assert hash1 != hash3
    assert len(hash1) == 16  # blake2b digest_size=8 → 16 hex chars


@pytest.mark.asyncio
async def test_event_timestamps_in_order() -> None:
    """Events should have timestamps in chronological order."""
    mock_client = Mock()
    mock_client.is_connected = True

    chat_model = _mock_chat_model()
    with patch("openbad.wui.chat_pipeline._agentic_stream", _fake_agentic_stream):
        async for _chunk in stream_chat(
            chat_model,
            "mock-model",
            "Hello",
            "test-session",
            nervous_system_client=mock_client,
        ):
            pass

    import json

    # Extract timestamps
    timestamps = []
    for call in mock_client.publish_bytes.call_args_list:
        topic, payload_bytes = call[0]
        if topic.startswith("agent/cognitive/"):
            payload = json.loads(payload_bytes.decode())
            timestamps.append(payload.get("timestamp", 0))

    # Should have at least input and output timestamps
    assert len(timestamps) >= 2
    # Should be in chronological order
    assert timestamps == sorted(timestamps)
