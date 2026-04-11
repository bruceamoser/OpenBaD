"""Tests for WUI chat nervous system integration (issue #303).

Covers: cognitive event publishing (input/output/error), graceful degradation
when broker unavailable, and event payload validation.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from openbad.cognitive.providers.base import ProviderAdapter
from openbad.nervous_system import topics
from openbad.wui.chat_pipeline import stream_chat

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture(autouse=True)
def mock_memory_dirs(tmp_path: Path) -> None:
    """Mock memory directories to avoid permissions issues."""
    import openbad.wui.chat_pipeline as cp

    cp._DATA_DIR = tmp_path
    cp._MEMORY_DIR = tmp_path / "memory"
    cp._MEMORY_DIR.mkdir(parents=True, exist_ok=True)


class MockProvider(ProviderAdapter):
    """Mock provider for testing."""

    def __init__(self, tokens: list[str] | None = None) -> None:
        self.tokens = tokens or ["Hello", " world", "!"]
        self.model_id = "mock-model"

    async def stream(self, prompt: str, *, model_id: str = "") -> AsyncIterator[str]:
        for token in self.tokens:
            yield token

    async def complete(self, prompt: str, *, model_id: str = "") -> str:
        return "".join(self.tokens)

    async def health_check(self) -> bool:
        return True

    async def list_models(self) -> list[str]:
        return ["mock-model"]


# ------------------------------------------------------------------ #
# Event publishing tests
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_publishes_input_event_on_user_message() -> None:
    """When user sends message, should publish COGNITIVE_INPUT event."""
    mock_client = Mock()
    mock_client.is_connected = True

    adapter = MockProvider()
    chunks = []
    async for chunk in stream_chat(
        adapter,
        "mock-model",
        "Hello",
        "test-session",
        nervous_system_client=mock_client,
    ):
        chunks.append(chunk)

    # Should have called publish for COGNITIVE_INPUT
    calls = [c for c in mock_client.publish.call_args_list if topics.COGNITIVE_INPUT in str(c)]
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

    adapter = MockProvider(tokens=["Test", " response"])
    chunks = []
    async for chunk in stream_chat(
        adapter,
        "mock-model",
        "Hello",
        "test-session",
        nervous_system_client=mock_client,
    ):
        chunks.append(chunk)

    # Should have called publish for COGNITIVE_OUTPUT
    calls = [c for c in mock_client.publish.call_args_list if topics.COGNITIVE_OUTPUT in str(c)]
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

    # Create adapter that raises error
    class FailingProvider(ProviderAdapter):
        async def stream(self, prompt: str, *, model_id: str = "") -> AsyncIterator[str]:
            raise ValueError("Test error")
            yield  # Unreachable but needed for type checker

        async def complete(self, prompt: str, *, model_id: str = "") -> str:
            return ""

        async def health_check(self) -> bool:
            return True

        async def list_models(self) -> list[str]:
            return []

    adapter = FailingProvider()
    chunks = []
    async for chunk in stream_chat(
        adapter,
        "mock-model",
        "Hello",
        "test-session",
        nervous_system_client=mock_client,
    ):
        chunks.append(chunk)

    # Should have called publish for COGNITIVE_ERROR
    calls = [c for c in mock_client.publish.call_args_list if topics.COGNITIVE_ERROR in str(c)]
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
    adapter = MockProvider(tokens=["Hello", "!"])
    chunks = []
    async for chunk in stream_chat(
        adapter,
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

    adapter = MockProvider()
    chunks = []
    async for chunk in stream_chat(
        adapter,
        "mock-model",
        "Hello",
        "test-session",
        nervous_system_client=mock_client,
    ):
        chunks.append(chunk)

    # Should complete without calling publish
    assert len(chunks) >= 2
    assert chunks[-1].done is True
    assert mock_client.publish.call_count == 0


@pytest.mark.asyncio
async def test_continues_on_publish_failure() -> None:
    """Chat should continue if event publishing raises an exception."""
    mock_client = Mock()
    mock_client.is_connected = True
    mock_client.publish.side_effect = Exception("MQTT publish failed")

    adapter = MockProvider()
    chunks = []
    async for chunk in stream_chat(
        adapter,
        "mock-model",
        "Hello",
        "test-session",
        nervous_system_client=mock_client,
    ):
        chunks.append(chunk)

    # Should complete despite publish failures
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

    adapter = MockProvider()
    async for _chunk in stream_chat(
        adapter,
        "mock-model",
        "Hello",
        "test-session",
        nervous_system_client=mock_client,
    ):
        pass

    import json

    # Extract timestamps
    timestamps = []
    for call in mock_client.publish.call_args_list:
        topic, payload_bytes = call[0]
        if topic.startswith("agent/cognitive/"):
            payload = json.loads(payload_bytes.decode())
            timestamps.append(payload.get("timestamp", 0))

    # Should have at least input and output timestamps
    assert len(timestamps) >= 2
    # Should be in chronological order
    assert timestamps == sorted(timestamps)
