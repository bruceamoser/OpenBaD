"""Round-trip serialization tests for sensory protobuf schemas — Issue #43."""

from __future__ import annotations

import json
import time

from openbad.nervous_system.schemas import (
    AttentionTrigger,
    FrameFormat,
    Header,
    ParsedScreen,
    ParseMethod,
    TranscriptionEvent,
    TTSComplete,
    TTSPriority,
    TTSRequest,
    VisionFrame,
    WakeWordEvent,
)


def _make_header(**overrides: object) -> Header:
    defaults = {
        "timestamp_unix": time.time(),
        "source_module": "sensory-test",
        "correlation_id": "test-corr-sensory",
        "schema_version": 1,
    }
    defaults.update(overrides)
    return Header(**defaults)


# ---------------------------------------------------------------------------
# Vision messages
# ---------------------------------------------------------------------------


class TestVisionFrameRoundTrip:
    def test_round_trip(self) -> None:
        frame_bytes = b"\x00\x01\x02" * 100
        original = VisionFrame(
            header=_make_header(source_module="vision.capture"),
            source_id="window-abc",
            window_title="Firefox — OpenBaD Docs",
            width=1920,
            height=1080,
            format=FrameFormat.JPEG,
            frame_data=frame_bytes,
            fps=5.0,
        )
        data = original.SerializeToString()
        restored = VisionFrame()
        restored.ParseFromString(data)
        assert restored.source_id == "window-abc"
        assert restored.window_title == "Firefox — OpenBaD Docs"
        assert restored.width == 1920
        assert restored.height == 1080
        assert restored.format == FrameFormat.JPEG
        assert restored.frame_data == frame_bytes
        assert abs(restored.fps - 5.0) < 0.01
        assert restored.header.source_module == "vision.capture"

    def test_raw_rgb_format(self) -> None:
        msg = VisionFrame(format=FrameFormat.RAW_RGB)
        assert msg.format == FrameFormat.RAW_RGB

    def test_png_format(self) -> None:
        msg = VisionFrame(format=FrameFormat.PNG)
        assert msg.format == FrameFormat.PNG

    def test_binary_is_compact_without_frame(self) -> None:
        msg = VisionFrame(
            header=_make_header(),
            source_id="test",
            width=800,
            height=600,
        )
        data = msg.SerializeToString()
        assert len(data) < 120


class TestParsedScreenRoundTrip:
    def test_round_trip(self) -> None:
        tree = [
            {"role": "button", "name": "OK", "bounds": [10, 20, 100, 40]},
            {"role": "text", "name": "Hello", "bounds": [10, 50, 200, 70]},
        ]
        original = ParsedScreen(
            header=_make_header(source_module="vision.a11y"),
            source_id="window-abc",
            method=ParseMethod.AT_SPI2,
            tree_json=json.dumps(tree),
            node_count=2,
            extraction_ms=42.5,
        )
        data = original.SerializeToString()
        restored = ParsedScreen()
        restored.ParseFromString(data)
        assert restored.source_id == "window-abc"
        assert restored.method == ParseMethod.AT_SPI2
        assert restored.node_count == 2
        assert abs(restored.extraction_ms - 42.5) < 0.01
        parsed = json.loads(restored.tree_json)
        assert len(parsed) == 2
        assert parsed[0]["role"] == "button"

    def test_cdp_dom_method(self) -> None:
        msg = ParsedScreen(method=ParseMethod.CDP_DOM)
        assert msg.method == ParseMethod.CDP_DOM

    def test_ocr_method(self) -> None:
        msg = ParsedScreen(method=ParseMethod.OCR)
        assert msg.method == ParseMethod.OCR


class TestAttentionTriggerRoundTrip:
    def test_round_trip(self) -> None:
        original = AttentionTrigger(
            header=_make_header(source_module="vision.attention"),
            source_id="window-abc",
            ssim_delta=0.15,
            region_description="Dialog popup detected in center of screen",
            changed_pixels=45_000,
        )
        data = original.SerializeToString()
        restored = AttentionTrigger()
        restored.ParseFromString(data)
        assert restored.source_id == "window-abc"
        assert abs(restored.ssim_delta - 0.15) < 0.001
        assert "Dialog popup" in restored.region_description
        assert restored.changed_pixels == 45_000


# ---------------------------------------------------------------------------
# Audio messages
# ---------------------------------------------------------------------------


class TestTranscriptionEventRoundTrip:
    def test_round_trip_vosk(self) -> None:
        original = TranscriptionEvent(
            header=_make_header(source_module="audio.asr"),
            source_id="mic-default",
            text="hello world",
            confidence=0.82,
            speaker_id="",
            is_final=False,
            engine="vosk",
            latency_ms=150.0,
        )
        data = original.SerializeToString()
        restored = TranscriptionEvent()
        restored.ParseFromString(data)
        assert restored.text == "hello world"
        assert abs(restored.confidence - 0.82) < 0.01
        assert restored.is_final is False
        assert restored.engine == "vosk"
        assert abs(restored.latency_ms - 150.0) < 0.1

    def test_round_trip_whisper(self) -> None:
        original = TranscriptionEvent(
            header=_make_header(source_module="audio.asr"),
            source_id="mic-default",
            text="The quick brown fox jumps over the lazy dog.",
            confidence=0.97,
            speaker_id="speaker-1",
            is_final=True,
            engine="whisper",
            latency_ms=2500.0,
        )
        data = original.SerializeToString()
        restored = TranscriptionEvent()
        restored.ParseFromString(data)
        assert restored.text == "The quick brown fox jumps over the lazy dog."
        assert restored.is_final is True
        assert restored.engine == "whisper"
        assert restored.speaker_id == "speaker-1"


class TestWakeWordEventRoundTrip:
    def test_round_trip(self) -> None:
        original = WakeWordEvent(
            header=_make_header(source_module="audio.wake_word"),
            keyword="hey agent",
            score=0.92,
            buffer_seconds=3.0,
        )
        data = original.SerializeToString()
        restored = WakeWordEvent()
        restored.ParseFromString(data)
        assert restored.keyword == "hey agent"
        assert abs(restored.score - 0.92) < 0.01
        assert abs(restored.buffer_seconds - 3.0) < 0.01


class TestTTSRequestRoundTrip:
    def test_round_trip(self) -> None:
        original = TTSRequest(
            header=_make_header(source_module="audio.tts"),
            text="Task completed successfully.",
            voice_model="en_US-lessac-medium",
            priority=TTSPriority.TTS_NORMAL,
            ssml="",
        )
        data = original.SerializeToString()
        restored = TTSRequest()
        restored.ParseFromString(data)
        assert restored.text == "Task completed successfully."
        assert restored.voice_model == "en_US-lessac-medium"
        assert restored.priority == TTSPriority.TTS_NORMAL

    def test_urgent_priority(self) -> None:
        msg = TTSRequest(priority=TTSPriority.TTS_URGENT)
        assert msg.priority == TTSPriority.TTS_URGENT

    def test_low_priority(self) -> None:
        msg = TTSRequest(priority=TTSPriority.TTS_LOW)
        assert msg.priority == TTSPriority.TTS_LOW

    def test_ssml_overrides_text(self) -> None:
        msg = TTSRequest(
            text="fallback",
            ssml='<speak>Hello <emphasis level="strong">world</emphasis></speak>',
        )
        data = msg.SerializeToString()
        restored = TTSRequest()
        restored.ParseFromString(data)
        assert "<emphasis" in restored.ssml


class TestTTSCompleteRoundTrip:
    def test_success(self) -> None:
        original = TTSComplete(
            header=_make_header(source_module="audio.tts"),
            request_id="req-001",
            duration_ms=1250.0,
            success=True,
            error="",
        )
        data = original.SerializeToString()
        restored = TTSComplete()
        restored.ParseFromString(data)
        assert restored.request_id == "req-001"
        assert abs(restored.duration_ms - 1250.0) < 0.1
        assert restored.success is True
        assert restored.error == ""

    def test_failure(self) -> None:
        original = TTSComplete(
            header=_make_header(),
            request_id="req-002",
            duration_ms=0.0,
            success=False,
            error="Voice model not found",
        )
        data = original.SerializeToString()
        restored = TTSComplete()
        restored.ParseFromString(data)
        assert restored.success is False
        assert "Voice model not found" in restored.error
