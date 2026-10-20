"""Tests for PipeWire audio capture integration."""

from __future__ import annotations

import asyncio

import pytest

from openbad.sensory.audio.capture import AudioChunk, PipeWireAudioStream
from openbad.sensory.audio.config import AudioCaptureConfig


@pytest.fixture
def audio_config():
    """Provide standard audio configuration."""
    return AudioCaptureConfig(
        sample_rate=16000,
        channels=1,
        sample_format="s16le",
        chunk_duration_ms=100,
    )


def test_audio_chunk_duration_calculation():
    """Test audio chunk duration is calculated correctly."""
    chunk = AudioChunk(
        source_id="test",
        pcm_data=b"\x00" * 3200,
        sample_rate=16000,
        channels=1,
        sample_format="s16le",
    )
    expected_duration = (3200 / 2) / 16000 * 1000
    assert abs(chunk.duration_ms - expected_duration) < 1.0


def test_audio_chunk_mqtt_topic():
    """Test MQTT topic generation."""
    chunk = AudioChunk(
        source_id="mic",
        pcm_data=b"",
        sample_rate=16000,
    )
    topic = chunk.mqtt_topic()
    assert "sensory/audio" in topic
    assert "mic" in topic


def test_pipewire_stream_requires_linux():
    """Test PipeWireAudioStream raises on non-Linux platforms."""
    from unittest.mock import patch

    with patch("sys.platform", "win32"), pytest.raises(RuntimeError, match="requires Linux"):
        PipeWireAudioStream(source_id="test")


@pytest.mark.skipif(
    __import__("sys").platform != "linux",
    reason="PipeWire only on Linux",
)
def test_pipewire_stream_initialization(audio_config):
    """Test PipeWireAudioStream initializes correctly."""
    stream = PipeWireAudioStream(
        source_id="mic",
        config=audio_config,
    )
    assert stream.source_id == "mic"
    assert stream.config.sample_rate == 16000
    assert not stream.is_running


@pytest.mark.skipif(
    __import__("sys").platform != "linux",
    reason="PipeWire only on Linux",
)
@pytest.mark.asyncio
async def test_capture_loop_with_mock_audio(audio_config):
    """Test capture loop processes audio chunks."""
    published_chunks = []

    async def mock_publish(topic: str, payload: bytes) -> None:
        published_chunks.append((topic, payload))

    stream = PipeWireAudioStream(
        source_id="mic",
        config=audio_config,
        publish_fn=mock_publish,
    )

    chunk_count = 0

    async def mock_audio_provider() -> bytes | None:
        nonlocal chunk_count
        if chunk_count >= 3:
            return None
        chunk_count += 1
        return b"\x00" * audio_config.chunk_bytes

    task = asyncio.create_task(stream.capture_loop(audio_provider=mock_audio_provider))
    await task

    assert len(published_chunks) == 3
    assert all("sensory/audio" in topic for topic, _ in published_chunks)


@pytest.mark.skipif(
    __import__("sys").platform != "linux",
    reason="PipeWire only on Linux",
)
@pytest.mark.asyncio
async def test_context_manager():
    """Test PipeWireAudioStream as async context manager."""
    async with PipeWireAudioStream(source_id="test") as stream:
        assert stream.source_id == "test"


@pytest.mark.skipif(
    __import__("sys").platform != "linux",
    reason="PipeWire only on Linux",
)
def test_audio_config_chunk_bytes_calculation():
    """Test chunk bytes calculation for different formats."""
    config_s16 = AudioCaptureConfig(
        sample_rate=16000,
        channels=1,
        sample_format="s16le",
        chunk_duration_ms=100,
    )
    assert config_s16.chunk_bytes == 3200

    config_f32 = AudioCaptureConfig(
        sample_rate=16000,
        channels=2,
        sample_format="f32le",
        chunk_duration_ms=100,
    )
    assert config_f32.chunk_bytes == 12800
