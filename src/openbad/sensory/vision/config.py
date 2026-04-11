"""Vision configuration — FPS, resolution, format settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml


class CaptureRegion(StrEnum):
    """Screen region to capture."""

    FULL_SCREEN = "full-screen"
    ACTIVE_WINDOW = "active-window"
    CUSTOM_RECT = "custom-rect"


@dataclass
class CompressionConfig:
    """Image compression settings.

    Attributes
    ----------
    format : str
        Output format — ``"jpeg"`` or ``"png"`` (default ``"jpeg"``).
    quality : int
        Compression quality 1-100 (default 85, used for JPEG only).
    """

    format: str = "jpeg"
    quality: int = 85

    def __post_init__(self) -> None:
        if self.format not in ("jpeg", "png"):
            msg = f"compression.format must be 'jpeg' or 'png', got '{self.format}'"
            raise ValueError(msg)
        if not 1 <= self.quality <= 100:
            msg = f"compression.quality must be 1-100, got {self.quality}"
            raise ValueError(msg)


@dataclass
class VisionConfig:
    """Configuration for the vision capture subsystem.

    Attributes
    ----------
    fps_idle : float
        Capture rate when the agent is passively monitoring (default 1.0).
    fps_active : float
        Capture rate during active GUI interaction (default 5.0).
    output_format : str
        Frame encoding — ``"raw_rgb"``, ``"jpeg"``, or ``"png"`` (default ``"jpeg"``).
    jpeg_quality : int
        JPEG compression quality 1-100 (default 85).
    match_source_resolution : bool
        When *True* (default) frames match the source window resolution.
    max_width : int
        Cap width if ``match_source_resolution`` is *False* (default 1920).
    max_height : int
        Cap height if ``match_source_resolution`` is *False* (default 1080).
    portal_backend : str
        xdg-desktop-portal backend hint (e.g. ``"wlr"``, ``"gnome"``).
    """

    fps_idle: float = 1.0
    fps_active: float = 5.0
    output_format: str = "jpeg"
    jpeg_quality: int = 85
    match_source_resolution: bool = True
    max_width: int = 1920
    max_height: int = 1080
    portal_backend: str = ""
    capture_region: str = "active-window"
    capture_interval_s: float = 1.0
    max_resolution: tuple[int, int] = (1920, 1080)
    compression: CompressionConfig = field(default_factory=CompressionConfig)

    # Attention filter settings (co-located for convenience)
    attention: AttentionConfig = field(default_factory=lambda: AttentionConfig())

    def __post_init__(self) -> None:
        # Validate capture_region
        try:
            CaptureRegion(self.capture_region)
        except ValueError:
            allowed = ", ".join(r.value for r in CaptureRegion)
            msg = f"capture_region must be one of ({allowed}), got '{self.capture_region}'"
            raise ValueError(msg) from None
        if self.capture_interval_s <= 0:
            msg = f"capture_interval_s must be > 0, got {self.capture_interval_s}"
            raise ValueError(msg)


@dataclass
class AttentionConfig:
    """Attention filter thresholds.

    Attributes
    ----------
    ssim_threshold : float
        Minimum SSIM delta to forward a frame (default 0.05).
    cooldown_ms : int
        Minimum interval between attention triggers in milliseconds (default 500).
    roi_enabled : bool
        Enable region-of-interest change tracking (default *False*).
    """

    ssim_threshold: float = 0.05
    cooldown_ms: int = 500
    roi_enabled: bool = False


def _filter_fields(cls: type, raw: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}


def load_vision_config(path: Path | str | None = None) -> VisionConfig:
    """Load :class:`VisionConfig` from a YAML file.

    Falls back to defaults when *path* is ``None`` or the file does not exist.
    """
    if path is None:
        return VisionConfig()

    p = Path(path)
    if not p.exists():
        return VisionConfig()

    raw: dict[str, Any] = yaml.safe_load(p.read_text()) or {}
    vision_raw = raw.get("vision", raw)

    attention_raw = vision_raw.pop("attention", {})
    attention = AttentionConfig(**_filter_fields(AttentionConfig, attention_raw))

    compression_raw = vision_raw.pop("compression", {})
    compression = CompressionConfig(**_filter_fields(CompressionConfig, compression_raw))

    # max_resolution may come as a list [w, h]
    max_res = vision_raw.pop("max_resolution", None)
    if isinstance(max_res, list) and len(max_res) == 2:
        max_res = tuple(max_res)

    skip = {"attention", "compression", "max_resolution"}
    fields = {
        k: v
        for k, v in vision_raw.items()
        if k in VisionConfig.__dataclass_fields__ and k not in skip
    }
    kwargs: dict[str, Any] = {
        **fields,
        "attention": attention,
        "compression": compression,
    }
    if max_res is not None:
        kwargs["max_resolution"] = max_res
    return VisionConfig(**kwargs)
