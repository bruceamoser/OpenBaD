"""Vision configuration — FPS, resolution, format settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


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

    # Attention filter settings (co-located for convenience)
    attention: AttentionConfig = field(default_factory=lambda: AttentionConfig())


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
    attention = AttentionConfig(**{
        k: v for k, v in attention_raw.items() if k in AttentionConfig.__dataclass_fields__
    })

    fields = {
        k: v
        for k, v in vision_raw.items()
        if k in VisionConfig.__dataclass_fields__ and k != "attention"
    }
    return VisionConfig(**fields, attention=attention)
