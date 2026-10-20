"""Tests for PipeWire audio capture — Issue #49."""

from __future__ import annotations

import struct
import sys
from unittest.mock import patch

import pytest

from openbad.sensory.audio.capture import AudioChunk, PipeWireAudioStream
from openbad.sensory.audio.config import AudioCaptureConfig

# ---------------------------------------------------------------------------
# AudioChunk
# ---------------------------------------------------------------------------


class TestAudioChunk:
    def test_basic_creation(self) -> None:
        chunk = AudioChunk(source_id="mic", pcm_data=b"\x00" * 3200)
        assert chunk.source_id == "mic"
        assert len(chunk.pcm_data) == 3200

    def test_duration_ms_s16le(self) -> None:
        # 1600 samples * 2 bytes = 3200 bytes at 16kHz = 100ms
        chunk = AudioChunk(
            source_id="mic", pcm_data=b"\x00" * 3200,
            sample_rate=16000, channels=1, sample_format="s16le",
        )
        assert abs(chunk.duration_ms - 100.0) < 0.01

    def test_duration_ms_f32le(self) -> None:
        # 1600 samples * 4 bytes = 6400 bytes at 16kHz = 100ms
        chunk = AudioChunk(
            source_id="mic", pcm_data=b"\x00" * 6400,
            sample_rate=16000, channels=1, sample_format="f32le",
        )
        assert abs(chunk.duration_ms - 100.0) < 0.01

    def test_duration_ms_empty(self) -> None:
        chunk = AudioChunk(source_id="mic", pcm_data=b"")
        assert chunk.duration_ms == 0.0

    def test_mqtt_topic(self) -> None:
        chunk = AudioChunk(source_id="mic", pcm_data=b"")
        assert chunk.mqtt_topic() == "agent/sensory/audio/mic"

    def test_mqtt_topic_app(self) -> None:
        chunk = AudioChunk(source_id="app-firefox", pcm_data=b"")
        assert chunk.mqtt_topic() == "agent/sensory/audio/app-firefox"

    def test_rms_amplitude_silence(self) -> None:
        chunk = AudioChunk(source_id="mic", pcm_data=b"\x00" * 200)
        assert chunk.rms_amplitude == 0.0

    def test_rms_amplitude_signal(self) -> None:
        # Create 4 samples of value 1000 (s16le)
        samples = [1000, -1000, 1000, -1000]
        pcm = b"".join(struct.pack("<h", s) for s in samples)
        chunk = AudioChunk(source_id="mic", pcm_data=pcm)
        assert abs(chunk.rms_amplitude - 1000.0) < 0.1

    def test_rms_unsupported_format(self) -> None:
        chunk = AudioChunk(
            source_id="mic", pcm_data=b"\x00" * 100,
            sample_format="f32le",
        )
        assert chunk.rms_amplitude == 0.0

    def test_sequence_numbering(self) -> None:
        chunk = AudioChunk(source_id="mic", pcm_data=b"", sequence=42)
        assert chunk.sequence == 42


# ---------------------------------------------------------------------------
# PipeWireAudioStream — platform guard
# ---------------------------------------------------------------------------


class TestPipeWireAudioStreamPlatform:
    def test_linux_only(self) -> None:
        if sys.platform == "linux":
            stream = PipeWireAudioStream(source_id="mic")
            assert stream.source_id == "mic"
        else:
            with pytest.raises(RuntimeError, match="requires Linux"):
                PipeWireAudioStream(source_id="mic")


# ---------------------------------------------------------------------------
# PipeWireAudioStream — with mocked platform
# ---------------------------------------------------------------------------


class TestPipeWireAudioStreamMocked:
    @pytest.fixture(autouse=True)
    def _patch_linux(self) -> None:
        """Pretend we are on Linux for stream tests."""
        with patch.object(sys, "platform", "linux"):
            yield

    def test_default_config(self) -> None:
        stream = PipeWireAudioStream(source_id="mic")
        assert stream.config.sample_rate == 16000
        assert stream.is_running is False

    def test_custom_config(self) -> None:
        cfg = AudioCaptureConfig(sample_rate=44100, channels=2)
        stream = PipeWireAudioStream(source_id="app", config=cfg)
        assert stream.config.sample_rate == 44100
        assert stream.config.channels == 2

    def test_make_chunk(self) -> None:
        stream = PipeWireAudioStream(source_id="mic")
        chunk = stream._make_chunk(b"\x00" * 3200)
        assert chunk.source_id == "mic"
        assert chunk.sequence == 1
        chunk2 = stream._make_chunk(b"\x00" * 3200)
        assert chunk2.sequence == 2

    async def test_capture_loop_with_provider(self) -> None:
        chunks_received: list[bytes] = []
        call_count = 0

        async def provider() -> bytes | None:
            nonlocal call_count
            call_count += 1
            if call_count > 3:
                return None
            return b"\x00" * 3200

        async def mock_publish(topic: str, payload: bytes) -> None:
            chunks_received.append(payload)

        stream = PipeWireAudioStream(source_id="mic", publish_fn=mock_publish)
        await stream.capture_loop(audio_provider=provider)

        assert len(chunks_received) == 3
        assert stream.is_running is False

    async def test_capture_loop_stop(self) -> None:
        call_count = 0

        async def provider() -> bytes | None:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                # Simulate stop from outside
                stream.stop()
            return b"\x00" * 100

        stream = PipeWireAudioStream(source_id="mic")
        await stream.capture_loop(audio_provider=provider)
        assert stream.is_running is False

    async def test_context_manager(self) -> None:
        async with PipeWireAudioStream(source_id="mic") as stream:
            assert isinstance(stream, PipeWireAudioStream)
        assert stream.is_running is False

    async def test_capture_topics(self) -> None:
        topics: list[str] = []

        async def mock_publish(topic: str, payload: bytes) -> None:
            topics.append(topic)

        call_count = 0

        async def provider() -> bytes | None:
            nonlocal call_count
            call_count += 1
            return b"\x00" * 100 if call_count <= 2 else None

        stream = PipeWireAudioStream(source_id="meeting", publish_fn=mock_publish)
        await stream.capture_loop(audio_provider=provider)

        assert all(t == "agent/sensory/audio/meeting" for t in topics)
        assert len(topics) == 2
