"""Tests for PipeWire ScreenCast capture service — Issue #44."""

from __future__ import annotations

import asyncio
import time

import pytest

from openbad.nervous_system.schemas import VisionFrame
from openbad.nervous_system.schemas.sensory_pb2 import FrameFormat
from openbad.sensory.vision.capture import (
    _IS_LINUX,
    CapturedFrame,
    ScreenCastPortal,
)
from openbad.sensory.vision.config import CaptureRegion, CompressionConfig, VisionConfig

# ---------------------------------------------------------------------------
# CapturedFrame tests
# ---------------------------------------------------------------------------


class TestCapturedFrame:
    def test_to_proto(self) -> None:
        frame = CapturedFrame(
            source_id="win-001",
            window_title="Test Window",
            width=800,
            height=600,
            data=b"\xff" * 100,
            format=FrameFormat.JPEG,
            fps=5.0,
            timestamp=1234567890.0,
        )
        proto = frame.to_proto()
        assert isinstance(proto, VisionFrame)
        assert proto.source_id == "win-001"
        assert proto.window_title == "Test Window"
        assert proto.width == 800
        assert proto.height == 600
        assert proto.format == FrameFormat.JPEG
        assert proto.frame_data == b"\xff" * 100
        assert abs(proto.fps - 5.0) < 0.01
        assert proto.header.source_module == "sensory.vision.capture"

    def test_to_proto_serializes(self) -> None:
        frame = CapturedFrame(
            source_id="win-002",
            window_title="Another Window",
            width=1920,
            height=1080,
            data=b"\x00" * 50,
            format=FrameFormat.RAW_RGB,
            fps=1.0,
        )
        data = frame.to_proto().SerializeToString()
        restored = VisionFrame()
        restored.ParseFromString(data)
        assert restored.source_id == "win-002"
        assert restored.width == 1920

    def test_mqtt_topic(self) -> None:
        frame = CapturedFrame(
            source_id="firefox-main",
            window_title="Firefox",
            width=800,
            height=600,
            data=b"",
            format=FrameFormat.JPEG,
            fps=1.0,
        )
        assert frame.mqtt_topic() == "agent/sensory/vision/firefox-main"

    def test_timestamp_auto_set(self) -> None:
        before = time.time()
        frame = CapturedFrame(
            source_id="test",
            window_title="Test",
            width=100,
            height=100,
            data=b"",
            format=FrameFormat.JPEG,
            fps=1.0,
        )
        after = time.time()
        assert before <= frame.timestamp <= after


# ---------------------------------------------------------------------------
# ScreenCastPortal unit tests (with mocked D-Bus)
# ---------------------------------------------------------------------------


class TestScreenCastPortalConfig:
    def test_default_config(self) -> None:
        portal = ScreenCastPortal()
        assert portal.current_fps == 1.0
        assert portal.running is False

    def test_custom_config(self) -> None:
        cfg = VisionConfig(fps_idle=2.0, fps_active=10.0)
        portal = ScreenCastPortal(config=cfg)
        assert portal.current_fps == 2.0

    def test_set_active_true(self) -> None:
        cfg = VisionConfig(fps_idle=1.0, fps_active=10.0)
        portal = ScreenCastPortal(config=cfg)
        portal.set_active(True)
        assert portal.current_fps == 10.0

    def test_set_active_false(self) -> None:
        cfg = VisionConfig(fps_idle=1.0, fps_active=10.0)
        portal = ScreenCastPortal(config=cfg)
        portal.set_active(True)
        portal.set_active(False)
        assert portal.current_fps == 1.0

    def test_format_map_jpeg(self) -> None:
        cfg = VisionConfig(output_format="jpeg")
        portal = ScreenCastPortal(config=cfg)
        assert portal._frame_format == FrameFormat.JPEG

    def test_format_map_raw_rgb(self) -> None:
        cfg = VisionConfig(output_format="raw_rgb")
        portal = ScreenCastPortal(config=cfg)
        assert portal._frame_format == FrameFormat.RAW_RGB

    def test_format_map_png(self) -> None:
        cfg = VisionConfig(output_format="png")
        portal = ScreenCastPortal(config=cfg)
        assert portal._frame_format == FrameFormat.PNG


class TestScreenCastPortalPlatformGuard:
    @pytest.mark.skipif(_IS_LINUX, reason="Test only on non-Linux")
    async def test_connect_bus_fails_on_non_linux(self) -> None:
        portal = ScreenCastPortal()
        with pytest.raises(RuntimeError, match="Linux Wayland session"):
            await portal._connect_bus()


class TestScreenCastPortalFrameEncoding:
    def _make_bgra_frame(self, width: int, height: int) -> bytes:
        """Create a fake BGRA frame (blue channel = 0xFF, others = 0x00)."""
        pixel = b"\xff\x00\x00\xff"  # BGRA: B=255, G=0, R=0, A=255
        return pixel * (width * height)

    def test_encode_raw_rgb(self) -> None:
        cfg = VisionConfig(output_format="raw_rgb")
        portal = ScreenCastPortal(config=cfg)
        raw = self._make_bgra_frame(2, 2)
        encoded, fmt = portal.encode_frame(raw, 2, 2)
        assert fmt == FrameFormat.RAW_RGB
        # BGRA (B=255,G=0,R=0,A=255) → RGB (R=0,G=0,B=255)
        assert len(encoded) == 2 * 2 * 3  # RGB
        # First pixel: R=0, G=0, B=255
        assert encoded[0:3] == b"\x00\x00\xff"

    def test_encode_jpeg_requires_pillow(self) -> None:
        cfg = VisionConfig(output_format="jpeg")
        portal = ScreenCastPortal(config=cfg)
        raw = self._make_bgra_frame(2, 2)
        # This will either succeed (Pillow installed) or raise RuntimeError
        try:
            encoded, fmt = portal.encode_frame(raw, 2, 2)
            assert fmt == FrameFormat.JPEG
            # JPEG magic bytes
            assert encoded[:2] == b"\xff\xd8"
        except RuntimeError:
            pytest.skip("Pillow not installed")

    def test_encode_png_requires_pillow(self) -> None:
        cfg = VisionConfig(output_format="png")
        portal = ScreenCastPortal(config=cfg)
        raw = self._make_bgra_frame(2, 2)
        try:
            encoded, fmt = portal.encode_frame(raw, 2, 2)
            assert fmt == FrameFormat.PNG
            # PNG magic bytes
            assert encoded[:4] == b"\x89PNG"
        except RuntimeError:
            pytest.skip("Pillow not installed")


class TestScreenCastPortalCaptureLoop:
    async def test_capture_publishes_frames(self) -> None:
        """Mock frame provider delivers 3 frames; all are published."""
        published: list[tuple[str, bytes]] = []

        async def mock_publish(topic: str, payload: bytes) -> None:
            published.append((topic, payload))

        cfg = VisionConfig(output_format="raw_rgb", fps_idle=1000)  # fast for test
        portal = ScreenCastPortal(config=cfg, publish_fn=mock_publish)

        # Create a mock async iterable that yields 3 frames then stops
        frames = [
            (b"\xff\x00\x00\xff" * 4, 2, 2),
            (b"\x00\xff\x00\xff" * 4, 2, 2),
            (b"\x00\x00\xff\xff" * 4, 2, 2),
        ]

        async def frame_gen():
            for f in frames:
                yield f

        await portal.capture_loop("test-win", "Test", frame_gen())

        assert len(published) == 3
        assert all(t == "agent/sensory/vision/test-win" for t, _ in published)
        # Each payload should deserialize to VisionFrame
        for _, payload in published:
            vf = VisionFrame()
            vf.ParseFromString(payload)
            assert vf.source_id == "test-win"
            assert vf.width == 2
            assert vf.height == 2

    async def test_capture_loop_sets_running_flag(self) -> None:
        cfg = VisionConfig(output_format="raw_rgb", fps_idle=1000)
        portal = ScreenCastPortal(config=cfg)

        assert portal.running is False

        async def empty_gen():
            return
            yield  # make it an async generator  # noqa: E501

        await portal.capture_loop("x", "X", empty_gen())
        assert portal.running is False  # cleaned up after completion

    async def test_stop_terminates_loop(self) -> None:
        published: list[tuple[str, bytes]] = []

        async def mock_publish(topic: str, payload: bytes) -> None:
            published.append((topic, payload))

        cfg = VisionConfig(output_format="raw_rgb", fps_idle=100)
        portal = ScreenCastPortal(config=cfg, publish_fn=mock_publish)

        async def infinite_gen():
            while True:
                yield (b"\xff\x00\x00\xff" * 4, 2, 2)

        async def stop_soon():
            await asyncio.sleep(0.05)
            await portal.stop()

        # Run capture loop and stop concurrently
        await asyncio.gather(
            portal.capture_loop("w", "W", infinite_gen()),
            stop_soon(),
        )
        assert portal.running is False
        assert len(published) >= 1  # At least one frame before stop


class TestScreenCastPortalContextManager:
    async def test_async_context_manager(self) -> None:
        async with ScreenCastPortal() as portal:
            assert isinstance(portal, ScreenCastPortal)
        assert portal.running is False

    async def test_stop_is_safe_to_call_multiple_times(self) -> None:
        portal = ScreenCastPortal()
        await portal.stop()
        await portal.stop()  # Should not raise


# ── Capture scope properties (#227) ──────────────────────────────── #


class TestCaptureScope:
    def test_capture_region_property(self) -> None:
        cfg = VisionConfig(capture_region="full-screen")
        portal = ScreenCastPortal(config=cfg)
        assert portal.capture_region == CaptureRegion.FULL_SCREEN

    def test_capture_interval_property(self) -> None:
        cfg = VisionConfig(capture_interval_s=0.5)
        portal = ScreenCastPortal(config=cfg)
        assert portal.capture_interval_s == 0.5

    def test_max_resolution_property(self) -> None:
        cfg = VisionConfig(max_resolution=(1280, 720))
        portal = ScreenCastPortal(config=cfg)
        assert portal.max_resolution == (1280, 720)


class TestReloadConfig:
    def test_reload_updates_capture_region(self) -> None:
        portal = ScreenCastPortal(config=VisionConfig())
        assert portal.capture_region == CaptureRegion.ACTIVE_WINDOW
        portal.reload_config(VisionConfig(capture_region="full-screen"))
        assert portal.capture_region == CaptureRegion.FULL_SCREEN

    def test_reload_updates_interval(self) -> None:
        portal = ScreenCastPortal(config=VisionConfig())
        assert portal.capture_interval_s == 1.0
        portal.reload_config(VisionConfig(capture_interval_s=0.25))
        assert portal.capture_interval_s == 0.25

    def test_reload_updates_max_resolution(self) -> None:
        portal = ScreenCastPortal(config=VisionConfig())
        portal.reload_config(VisionConfig(max_resolution=(640, 480)))
        assert portal.max_resolution == (640, 480)

    def test_reload_updates_compression(self) -> None:
        portal = ScreenCastPortal(config=VisionConfig())
        new_cfg = VisionConfig(
            compression=CompressionConfig(format="png", quality=100),
        )
        portal.reload_config(new_cfg)
        assert portal._compression_format == "png"
        assert portal._compression_quality == 100
