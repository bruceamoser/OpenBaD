"""PipeWire ScreenCast portal integration for window capture.

This module connects to the ``org.freedesktop.portal.ScreenCast`` D-Bus
interface on Linux/Wayland to capture individual application windows.
On unsupported platforms the module logs a clear error and exposes a
no-op fallback so the rest of the stack can load without crashing.

Runtime requirements (Linux only):
  - Wayland compositor (Sway, Hyprland, GNOME Wayland)
  - PipeWire running as multimedia daemon
  - ``xdg-desktop-portal`` with an appropriate backend
  - ``dbus-next`` (MIT) Python package
"""

from __future__ import annotations

import asyncio
import logging
import platform
import time
from dataclasses import dataclass, field
from typing import Any

from openbad.nervous_system.schemas import Header, VisionFrame
from openbad.nervous_system.schemas.sensory_pb2 import FrameFormat
from openbad.nervous_system.topics import SENSORY_VISION, topic_for
from openbad.sensory.vision.config import CaptureRegion, VisionConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Platform guard
# ---------------------------------------------------------------------------
_IS_LINUX = platform.system() == "Linux"

PORTAL_BUS_NAME = "org.freedesktop.portal.Desktop"
PORTAL_OBJECT_PATH = "/org/freedesktop/portal/desktop"
PORTAL_SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
PORTAL_REQUEST_IFACE = "org.freedesktop.portal.Request"

# ScreenCast source type flags
SOURCE_TYPE_MONITOR = 1
SOURCE_TYPE_WINDOW = 2

_FORMAT_MAP = {
    "raw_rgb": FrameFormat.RAW_RGB,
    "jpeg": FrameFormat.JPEG,
    "png": FrameFormat.PNG,
}


@dataclass
class CapturedFrame:
    """A single captured frame with metadata."""

    source_id: str
    window_title: str
    width: int
    height: int
    data: bytes
    format: FrameFormat
    fps: float
    timestamp: float = field(default_factory=time.time)

    def to_proto(self) -> VisionFrame:
        """Serialise to a ``VisionFrame`` protobuf message."""
        return VisionFrame(
            header=Header(
                timestamp_unix=self.timestamp,
                source_module="sensory.vision.capture",
                schema_version=1,
            ),
            source_id=self.source_id,
            window_title=self.window_title,
            width=self.width,
            height=self.height,
            format=self.format,
            frame_data=self.data,
            fps=self.fps,
        )

    def mqtt_topic(self) -> str:
        """Return the MQTT topic for this frame."""
        return topic_for(SENSORY_VISION, source_id=self.source_id)


# ---------------------------------------------------------------------------
# Portal session management (Linux / Wayland only)
# ---------------------------------------------------------------------------


class ScreenCastPortal:
    """Async wrapper around the xdg-desktop-portal ScreenCast API.

    Parameters
    ----------
    config : VisionConfig
        Capture configuration (FPS, format, resolution).
    publish_fn : callable | None
        Optional async callback ``(topic, payload) -> None`` to publish
        frames to the event bus.  When *None*, captured frames are
        silently discarded (useful for testing).
    """

    def __init__(
        self,
        config: VisionConfig | None = None,
        publish_fn: Any | None = None,
    ) -> None:
        self._config = config or VisionConfig()
        self._publish = publish_fn
        self._running = False
        self._session_handle: str | None = None
        self._bus: Any | None = None  # dbus_next.aio.MessageBus
        self._portal: Any | None = None  # dbus_next proxy interface
        self._active_fps: float = self._config.fps_idle
        self._frame_format = _FORMAT_MAP.get(
            self._config.output_format, FrameFormat.JPEG
        )
        self._capture_region = CaptureRegion(self._config.capture_region)
        self._capture_interval_s = self._config.capture_interval_s
        self._max_resolution = self._config.max_resolution
        self._compression_format = self._config.compression.format
        self._compression_quality = self._config.compression.quality

    @property
    def running(self) -> bool:
        return self._running

    @property
    def current_fps(self) -> float:
        return self._active_fps

    @property
    def capture_region(self) -> CaptureRegion:
        return self._capture_region

    @property
    def capture_interval_s(self) -> float:
        return self._capture_interval_s

    @property
    def max_resolution(self) -> tuple[int, int]:
        return self._max_resolution

    def reload_config(self, config: VisionConfig) -> None:
        """Hot-reload configuration without restarting the capture session."""
        self._config = config
        was_active = self._active_fps == self._config.fps_active
        self._active_fps = config.fps_active if was_active else config.fps_idle
        self._frame_format = _FORMAT_MAP.get(config.output_format, FrameFormat.JPEG)
        self._capture_region = CaptureRegion(config.capture_region)
        self._capture_interval_s = config.capture_interval_s
        self._max_resolution = config.max_resolution
        self._compression_format = config.compression.format
        self._compression_quality = config.compression.quality
        logger.info(
            "Vision config reloaded: region=%s interval=%ss",
            self._capture_region.value,
            self._capture_interval_s,
        )

    def set_active(self, active: bool) -> None:
        """Switch between idle and active FPS."""
        self._active_fps = (
            self._config.fps_active if active else self._config.fps_idle
        )

    # -- Portal negotiation --------------------------------------------------

    async def _connect_bus(self) -> Any:
        """Connect to the session D-Bus."""
        if not _IS_LINUX:
            msg = "ScreenCast portal requires a Linux Wayland session"
            raise RuntimeError(msg)

        try:
            from dbus_next.aio import MessageBus  # type: ignore[import-untyped]
        except ImportError:
            msg = (
                "dbus-next is required for PipeWire screen capture. "
                "Install with: pip install dbus-next"
            )
            raise RuntimeError(msg) from None

        bus = await MessageBus().connect()
        return bus

    async def _get_portal_interface(self, bus: Any) -> Any:
        """Obtain the ScreenCast portal proxy."""
        introspection = await bus.introspect(PORTAL_BUS_NAME, PORTAL_OBJECT_PATH)
        proxy = bus.get_proxy_object(
            PORTAL_BUS_NAME, PORTAL_OBJECT_PATH, introspection
        )
        return proxy.get_interface(PORTAL_SCREENCAST_IFACE)

    async def create_session(self) -> str:
        """Negotiate a ScreenCast session (D-Bus → PipeWire stream).

        Returns the PipeWire node ID as a string.

        Raises
        ------
        RuntimeError
            If the platform is unsupported or any D-Bus call fails.
        """
        self._bus = await self._connect_bus()
        self._portal = await self._get_portal_interface(self._bus)

        # CreateSession
        session_result = await self._portal.call_create_session(
            {"session_handle_token": ("s", "openbad_vision")}
        )
        self._session_handle = session_result

        # SelectSources — request based on capture_region
        source_type = (
            SOURCE_TYPE_MONITOR
            if self._capture_region == CaptureRegion.FULL_SCREEN
            else SOURCE_TYPE_WINDOW
        )
        await self._portal.call_select_sources(
            self._session_handle,
            {
                "types": ("u", source_type),
                "multiple": ("b", False),
            },
        )

        # Start — user will see the window picker
        start_result = await self._portal.call_start(
            self._session_handle, ""
        )
        return str(start_result)

    # -- Frame consumption ---------------------------------------------------

    @staticmethod
    def _parse_raw_frame(
        raw: bytes, width: int, height: int
    ) -> bytes:
        """Validate and return raw RGB frame data."""
        expected = width * height * 4  # BGRA
        if len(raw) < expected:
            msg = f"Frame too small: got {len(raw)}, expected {expected}"
            raise ValueError(msg)
        return raw[:expected]

    @staticmethod
    def _encode_jpeg(raw_bgra: bytes, width: int, height: int, quality: int) -> bytes:
        """Encode raw BGRA bytes to JPEG. Requires *Pillow* at runtime."""
        try:
            from PIL import Image  # type: ignore[import-untyped]
        except ImportError:
            msg = "Pillow is required for JPEG encoding: pip install Pillow"
            raise RuntimeError(msg) from None

        img = Image.frombytes("RGBA", (width, height), raw_bgra)
        rgb = img.convert("RGB")
        import io

        buf = io.BytesIO()
        rgb.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()

    @staticmethod
    def _encode_png(raw_bgra: bytes, width: int, height: int) -> bytes:
        """Encode raw BGRA bytes to PNG."""
        try:
            from PIL import Image  # type: ignore[import-untyped]
        except ImportError:
            msg = "Pillow is required for PNG encoding: pip install Pillow"
            raise RuntimeError(msg) from None

        img = Image.frombytes("RGBA", (width, height), raw_bgra)
        import io

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def encode_frame(
        self,
        raw_bgra: bytes,
        width: int,
        height: int,
    ) -> tuple[bytes, FrameFormat]:
        """Encode raw BGRA frame to the configured format."""
        if self._frame_format == FrameFormat.JPEG:
            return (
                self._encode_jpeg(raw_bgra, width, height, self._config.jpeg_quality),
                FrameFormat.JPEG,
            )
        if self._frame_format == FrameFormat.PNG:
            return self._encode_png(raw_bgra, width, height), FrameFormat.PNG
        # RAW_RGB — strip alpha channel
        rgb = bytearray()
        for i in range(0, len(raw_bgra), 4):
            rgb.extend(raw_bgra[i + 2 : i + 3])  # R
            rgb.extend(raw_bgra[i + 1 : i + 2])  # G
            rgb.extend(raw_bgra[i : i + 1])  # B
        return bytes(rgb), FrameFormat.RAW_RGB

    async def capture_loop(
        self,
        source_id: str,
        window_title: str,
        frame_provider: Any,
    ) -> None:
        """Run the capture loop, encoding and publishing frames.

        Parameters
        ----------
        source_id : str
            Identifier for the capture source.
        window_title : str
            Title of the captured window.
        frame_provider : async iterable
            Yields ``(raw_bytes, width, height)`` tuples.
        """
        self._running = True
        try:
            async for raw, width, height in frame_provider:
                if not self._running:
                    break

                encoded, fmt = self.encode_frame(raw, width, height)
                frame = CapturedFrame(
                    source_id=source_id,
                    window_title=window_title,
                    width=width,
                    height=height,
                    data=encoded,
                    format=fmt,
                    fps=self._active_fps,
                )

                if self._publish is not None:
                    proto = frame.to_proto()
                    topic = frame.mqtt_topic()
                    await self._publish(topic, proto.SerializeToString())

                # Throttle to configured interval / FPS
                interval = self._capture_interval_s
                if self._active_fps > 0:
                    interval = min(interval, 1.0 / self._active_fps)
                await asyncio.sleep(interval)
        finally:
            self._running = False

    async def stop(self) -> None:
        """Stop the capture loop and disconnect the D-Bus session."""
        self._running = False
        if self._bus is not None:
            self._bus.disconnect()
            self._bus = None
        self._session_handle = None

    async def __aenter__(self) -> ScreenCastPortal:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()
