"""Tests for the media tool adapter (MediaToolAdapter)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from openbad.identity.permissions import (
    ActionTier,
    PermissionClassifier,
)
from openbad.proprioception.registry import ToolRegistry, ToolRole
from openbad.toolbelt.media_tool import (
    CaptureResult,
    MediaConfig,
    MediaToolAdapter,
)

# --------------- helpers ---------------

async def _fake_vision():
    return (b"\x89PNG-fake", "png")


async def _fake_audio(duration_s: float):
    return (b"PCM-fake-audio", "wav")


def _read_tier_classifier() -> PermissionClassifier:
    """Classifier that maps all media actions to READ tier."""
    return PermissionClassifier(
        action_mappings={
            "media.screenshot": ActionTier.READ,
            "media.audio_clip": ActionTier.READ,
            "media.read_file": ActionTier.READ,
            "media.write_file": ActionTier.READ,
        },
    )


def _write_tier_classifier() -> PermissionClassifier:
    """Classifier that maps all media actions to WRITE tier."""
    return PermissionClassifier(
        action_mappings={
            "media.screenshot": ActionTier.WRITE,
            "media.audio_clip": ActionTier.WRITE,
            "media.read_file": ActionTier.WRITE,
            "media.write_file": ActionTier.WRITE,
        },
    )


# --------------- screenshot ---------------

class TestScreenshot:
    def test_screenshot_returns_capture_result(self) -> None:
        adapter = MediaToolAdapter(vision_capture_fn=_fake_vision)
        result = asyncio.get_event_loop().run_until_complete(adapter.screenshot())
        assert result is not None
        assert isinstance(result, CaptureResult)
        assert result.data == b"\x89PNG-fake"
        assert result.format == "png"
        assert result.source == "vision"

    def test_screenshot_no_backend_returns_none(self) -> None:
        adapter = MediaToolAdapter()
        result = asyncio.get_event_loop().run_until_complete(adapter.screenshot())
        assert result is None

    def test_screenshot_permission_denied(self) -> None:
        adapter = MediaToolAdapter(
            permission_classifier=_write_tier_classifier(),
            vision_capture_fn=_fake_vision,
        )
        result = asyncio.get_event_loop().run_until_complete(adapter.screenshot())
        assert result is None

    def test_screenshot_permission_allowed(self) -> None:
        adapter = MediaToolAdapter(
            permission_classifier=_read_tier_classifier(),
            vision_capture_fn=_fake_vision,
        )
        result = asyncio.get_event_loop().run_until_complete(adapter.screenshot())
        assert result is not None


# --------------- audio_clip ---------------

class TestAudioClip:
    def test_audio_clip_returns_capture_result(self) -> None:
        adapter = MediaToolAdapter(audio_capture_fn=_fake_audio)
        result = asyncio.get_event_loop().run_until_complete(
            adapter.audio_clip(3.0),
        )
        assert result is not None
        assert result.data == b"PCM-fake-audio"
        assert result.format == "wav"
        assert result.metadata["duration_s"] == 3.0

    def test_audio_clip_clamps_duration(self) -> None:
        cfg = MediaConfig(audio_clip_max_seconds=5.0)
        adapter = MediaToolAdapter(config=cfg, audio_capture_fn=_fake_audio)
        result = asyncio.get_event_loop().run_until_complete(
            adapter.audio_clip(100.0),
        )
        assert result is not None
        assert result.metadata["duration_s"] == 5.0

    def test_audio_clip_no_backend(self) -> None:
        adapter = MediaToolAdapter()
        result = asyncio.get_event_loop().run_until_complete(
            adapter.audio_clip(1.0),
        )
        assert result is None

    def test_audio_clip_permission_denied(self) -> None:
        adapter = MediaToolAdapter(
            permission_classifier=_write_tier_classifier(),
            audio_capture_fn=_fake_audio,
        )
        result = asyncio.get_event_loop().run_until_complete(
            adapter.audio_clip(1.0),
        )
        assert result is None


# --------------- read_file ---------------

class TestReadFile:
    def test_read_file_success(self, tmp_path: Path) -> None:
        target = tmp_path / "data.txt"
        target.write_bytes(b"hello")
        cfg = MediaConfig(allowed_paths=[str(tmp_path)])
        adapter = MediaToolAdapter(config=cfg)
        data = adapter.read_file(str(target))
        assert data == b"hello"

    def test_read_file_outside_allowed(self, tmp_path: Path) -> None:
        target = tmp_path / "data.txt"
        target.write_bytes(b"secret")
        cfg = MediaConfig(allowed_paths=["/nonexistent"])
        adapter = MediaToolAdapter(config=cfg)
        assert adapter.read_file(str(target)) is None

    def test_read_file_too_large(self, tmp_path: Path) -> None:
        target = tmp_path / "big.bin"
        target.write_bytes(b"x" * 200)
        cfg = MediaConfig(allowed_paths=[str(tmp_path)], max_file_bytes=100)
        adapter = MediaToolAdapter(config=cfg)
        assert adapter.read_file(str(target)) is None

    def test_read_file_permission_denied(self, tmp_path: Path) -> None:
        target = tmp_path / "data.txt"
        target.write_bytes(b"hello")
        cfg = MediaConfig(allowed_paths=[str(tmp_path)])
        adapter = MediaToolAdapter(
            permission_classifier=_write_tier_classifier(),
            config=cfg,
        )
        assert adapter.read_file(str(target)) is None


# --------------- write_file ---------------

class TestWriteFile:
    def test_write_file_success(self, tmp_path: Path) -> None:
        target = tmp_path / "out.txt"
        cfg = MediaConfig(allowed_paths=[str(tmp_path)])
        adapter = MediaToolAdapter(config=cfg)
        assert adapter.write_file(str(target), b"world") is True
        assert target.read_bytes() == b"world"

    def test_write_file_outside_allowed(self, tmp_path: Path) -> None:
        target = tmp_path / "out.txt"
        cfg = MediaConfig(allowed_paths=["/nonexistent"])
        adapter = MediaToolAdapter(config=cfg)
        assert adapter.write_file(str(target), b"hack") is False
        assert not target.exists()

    def test_write_file_too_large(self, tmp_path: Path) -> None:
        target = tmp_path / "out.bin"
        cfg = MediaConfig(allowed_paths=[str(tmp_path)], max_file_bytes=10)
        adapter = MediaToolAdapter(config=cfg)
        assert adapter.write_file(str(target), b"x" * 100) is False

    def test_write_file_permission_denied(self, tmp_path: Path) -> None:
        target = tmp_path / "out.txt"
        cfg = MediaConfig(allowed_paths=[str(tmp_path)])
        adapter = MediaToolAdapter(
            permission_classifier=_write_tier_classifier(),
            config=cfg,
        )
        assert adapter.write_file(str(target), b"no") is False


# --------------- health_check ---------------

class TestHealthCheck:
    def test_healthy_with_vision(self) -> None:
        adapter = MediaToolAdapter(vision_capture_fn=_fake_vision)
        assert adapter.health_check() is True

    def test_healthy_with_audio(self) -> None:
        adapter = MediaToolAdapter(audio_capture_fn=_fake_audio)
        assert adapter.health_check() is True

    def test_unhealthy_no_backends(self) -> None:
        adapter = MediaToolAdapter()
        assert adapter.health_check() is False


# --------------- registration ---------------

class TestRegistration:
    def test_registers_under_media_role(self) -> None:
        adapter = MediaToolAdapter(vision_capture_fn=_fake_vision)
        registry = ToolRegistry(timeout=30.0)
        registry.register(
            "media",
            role=ToolRole.MEDIA,
            health_check=adapter.health_check,
        )
        cabinet = registry.cabinet
        assert ToolRole.MEDIA in cabinet
        assert any(t.name == "media" for t in cabinet[ToolRole.MEDIA])
