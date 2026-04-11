"""Media tool adapter — screenshot, audio clip, file read/write.

Registers under ``ToolRole.MEDIA`` and wraps vision capture, audio
capture, and sandboxed file I/O behind permission checks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from openbad.identity.permissions import (
    ActionTier,
    PermissionClassifier,
    PermissionResult,
)

logger = logging.getLogger(__name__)


@dataclass
class MediaConfig:
    """Configuration for the media tool adapter."""

    allowed_paths: list[str] = field(default_factory=lambda: ["/tmp"])  # noqa: S108
    max_file_bytes: int = 10 * 1024 * 1024  # 10 MB
    audio_clip_max_seconds: float = 30.0


@dataclass
class CaptureResult:
    """Result of a screenshot or audio clip capture."""

    data: bytes
    format: str
    source: str
    metadata: dict = field(default_factory=dict)


class MediaToolAdapter:
    """Cognitive-callable media tool with permission checks.

    Parameters
    ----------
    permission_classifier:
        Used to check permissions before executing actions.
    config:
        Optional media configuration.
    vision_capture_fn:
        Async callable returning ``(image_bytes, format_str)`` or ``None``.
    audio_capture_fn:
        Async callable ``(duration_s) -> (audio_bytes, format_str)`` or ``None``.
    """

    def __init__(
        self,
        permission_classifier: PermissionClassifier | None = None,
        config: MediaConfig | None = None,
        vision_capture_fn=None,
        audio_capture_fn=None,
    ) -> None:
        self._perms = permission_classifier
        self._config = config or MediaConfig()
        self._vision_capture_fn = vision_capture_fn
        self._audio_capture_fn = audio_capture_fn

    def _check(self, action: str) -> PermissionResult | None:
        """Return a denial PermissionResult, or None if allowed."""
        if self._perms is None:
            return None
        result = self._perms.classify(action)
        if result.tier is ActionTier.READ:
            return None
        # For non-READ tiers without a session manager, deny
        return PermissionResult(
            allowed=False,
            action=action,
            tier=result.tier,
            reason="No active session for elevated action",
        )

    def _path_allowed(self, path: str) -> bool:
        """Check path is within allowed directories."""
        resolved = Path(path).resolve()
        for allowed in self._config.allowed_paths:
            if str(resolved).startswith(str(Path(allowed).resolve())):
                return True
        return False

    async def screenshot(self) -> CaptureResult | None:
        """Capture a screenshot via the vision subsystem."""
        denial = self._check("media.screenshot")
        if denial and not denial.allowed:
            logger.warning("Permission denied: %s", denial.reason)
            return None

        if self._vision_capture_fn is None:
            logger.warning("No vision capture backend configured")
            return None

        try:
            image_bytes, fmt = await self._vision_capture_fn()
            return CaptureResult(
                data=image_bytes,
                format=fmt,
                source="vision",
            )
        except Exception:
            logger.exception("Screenshot capture failed")
            return None

    async def audio_clip(self, duration_s: float = 5.0) -> CaptureResult | None:
        """Record a short audio clip."""
        denial = self._check("media.audio_clip")
        if denial and not denial.allowed:
            logger.warning("Permission denied: %s", denial.reason)
            return None

        if self._audio_capture_fn is None:
            logger.warning("No audio capture backend configured")
            return None

        clamped = min(duration_s, self._config.audio_clip_max_seconds)
        try:
            audio_bytes, fmt = await self._audio_capture_fn(clamped)
            return CaptureResult(
                data=audio_bytes,
                format=fmt,
                source="audio",
                metadata={"duration_s": clamped},
            )
        except Exception:
            logger.exception("Audio clip capture failed")
            return None

    def read_file(self, path: str) -> bytes | None:
        """Read a file within allowed paths."""
        denial = self._check("media.read_file")
        if denial and not denial.allowed:
            logger.warning("Permission denied: %s", denial.reason)
            return None

        if not self._path_allowed(path):
            logger.warning("Path not in allowed directories: %s", path)
            return None

        try:
            data = Path(path).read_bytes()
            if len(data) > self._config.max_file_bytes:
                logger.warning("File too large: %d bytes", len(data))
                return None
            return data
        except Exception:
            logger.exception("Failed to read file: %s", path)
            return None

    def write_file(self, path: str, content: bytes) -> bool:
        """Write content to a file within allowed paths."""
        denial = self._check("media.write_file")
        if denial and not denial.allowed:
            logger.warning("Permission denied: %s", denial.reason)
            return False

        if not self._path_allowed(path):
            logger.warning("Path not in allowed directories: %s", path)
            return False

        if len(content) > self._config.max_file_bytes:
            logger.warning("Content too large: %d bytes", len(content))
            return False

        try:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            return True
        except Exception:
            logger.exception("Failed to write file: %s", path)
            return False

    def health_check(self) -> bool:
        """Verify at least one media capability is available."""
        return (
            self._vision_capture_fn is not None
            or self._audio_capture_fn is not None
        )
