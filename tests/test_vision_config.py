"""Tests for vision configuration — Issue #44, #227."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from openbad.sensory.vision.config import (
    AttentionConfig,
    CaptureRegion,
    CompressionConfig,
    VisionConfig,
    load_vision_config,
)


class TestVisionConfigDefaults:
    def test_default_fps_idle(self) -> None:
        cfg = VisionConfig()
        assert cfg.fps_idle == 1.0

    def test_default_fps_active(self) -> None:
        cfg = VisionConfig()
        assert cfg.fps_active == 5.0

    def test_default_output_format(self) -> None:
        cfg = VisionConfig()
        assert cfg.output_format == "jpeg"

    def test_default_jpeg_quality(self) -> None:
        cfg = VisionConfig()
        assert cfg.jpeg_quality == 85

    def test_default_match_source_resolution(self) -> None:
        cfg = VisionConfig()
        assert cfg.match_source_resolution is True

    def test_default_max_dimensions(self) -> None:
        cfg = VisionConfig()
        assert cfg.max_width == 1920
        assert cfg.max_height == 1080

    def test_default_portal_backend_empty(self) -> None:
        cfg = VisionConfig()
        assert cfg.portal_backend == ""

    def test_default_attention_config(self) -> None:
        cfg = VisionConfig()
        assert isinstance(cfg.attention, AttentionConfig)
        assert cfg.attention.ssim_threshold == 0.05
        assert cfg.attention.cooldown_ms == 500
        assert cfg.attention.roi_enabled is False


class TestAttentionConfigDefaults:
    def test_defaults(self) -> None:
        cfg = AttentionConfig()
        assert cfg.ssim_threshold == 0.05
        assert cfg.cooldown_ms == 500
        assert cfg.roi_enabled is False

    def test_custom_threshold(self) -> None:
        cfg = AttentionConfig(ssim_threshold=0.1, cooldown_ms=1000, roi_enabled=True)
        assert cfg.ssim_threshold == 0.1
        assert cfg.cooldown_ms == 1000
        assert cfg.roi_enabled is True


class TestLoadVisionConfig:
    def test_none_path_returns_defaults(self) -> None:
        cfg = load_vision_config(None)
        assert cfg.fps_idle == 1.0
        assert cfg.output_format == "jpeg"

    def test_missing_file_returns_defaults(self) -> None:
        cfg = load_vision_config("/nonexistent/path.yaml")
        assert cfg.fps_idle == 1.0

    def test_load_from_yaml(self, tmp_path: Path) -> None:
        yaml_content = dedent("""\
            vision:
              fps_idle: 2.0
              fps_active: 10.0
              output_format: png
              jpeg_quality: 90
              match_source_resolution: false
              max_width: 1280
              max_height: 720
              portal_backend: gnome
              attention:
                ssim_threshold: 0.1
                cooldown_ms: 1000
                roi_enabled: true
        """)
        cfg_file = tmp_path / "vision.yaml"
        cfg_file.write_text(yaml_content)
        cfg = load_vision_config(cfg_file)

        assert cfg.fps_idle == 2.0
        assert cfg.fps_active == 10.0
        assert cfg.output_format == "png"
        assert cfg.jpeg_quality == 90
        assert cfg.match_source_resolution is False
        assert cfg.max_width == 1280
        assert cfg.max_height == 720
        assert cfg.portal_backend == "gnome"
        assert cfg.attention.ssim_threshold == 0.1
        assert cfg.attention.cooldown_ms == 1000
        assert cfg.attention.roi_enabled is True

    def test_partial_yaml(self, tmp_path: Path) -> None:
        yaml_content = dedent("""\
            vision:
              fps_idle: 3.0
        """)
        cfg_file = tmp_path / "partial.yaml"
        cfg_file.write_text(yaml_content)
        cfg = load_vision_config(cfg_file)
        assert cfg.fps_idle == 3.0
        assert cfg.fps_active == 5.0  # default
        assert cfg.attention.ssim_threshold == 0.05  # default

    def test_empty_yaml(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "empty.yaml"
        cfg_file.write_text("")
        cfg = load_vision_config(cfg_file)
        assert cfg.fps_idle == 1.0

    def test_ignores_unknown_keys(self, tmp_path: Path) -> None:
        yaml_content = dedent("""\
            vision:
              fps_idle: 2.0
              unknown_key: should_be_ignored
        """)
        cfg_file = tmp_path / "unknown.yaml"
        cfg_file.write_text(yaml_content)
        cfg = load_vision_config(cfg_file)
        assert cfg.fps_idle == 2.0

    def test_load_from_project_config(self) -> None:
        """Load the actual config/sensory_vision.yaml from the repo."""
        cfg_path = Path(__file__).resolve().parents[1] / "config" / "sensory_vision.yaml"
        if cfg_path.exists():
            cfg = load_vision_config(cfg_path)
            assert cfg.fps_idle == 1.0
            assert cfg.attention.ssim_threshold == 0.05


# ── Capture scope fields (#227) ──────────────────────────────────── #


class TestCaptureRegionEnum:
    def test_valid_values(self) -> None:
        assert CaptureRegion("full-screen") == CaptureRegion.FULL_SCREEN
        assert CaptureRegion("active-window") == CaptureRegion.ACTIVE_WINDOW
        assert CaptureRegion("custom-rect") == CaptureRegion.CUSTOM_RECT

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            CaptureRegion("bad-value")


class TestCompressionConfig:
    def test_defaults(self) -> None:
        c = CompressionConfig()
        assert c.format == "jpeg"
        assert c.quality == 85

    def test_png_format(self) -> None:
        c = CompressionConfig(format="png", quality=100)
        assert c.format == "png"

    def test_invalid_format_raises(self) -> None:
        with pytest.raises(ValueError, match="compression.format"):
            CompressionConfig(format="bmp")

    def test_quality_too_low_raises(self) -> None:
        with pytest.raises(ValueError, match="compression.quality"):
            CompressionConfig(quality=0)

    def test_quality_too_high_raises(self) -> None:
        with pytest.raises(ValueError, match="compression.quality"):
            CompressionConfig(quality=101)


class TestVisionCaptureScope:
    def test_default_capture_region(self) -> None:
        cfg = VisionConfig()
        assert cfg.capture_region == "active-window"

    def test_default_capture_interval(self) -> None:
        cfg = VisionConfig()
        assert cfg.capture_interval_s == 1.0

    def test_default_max_resolution(self) -> None:
        cfg = VisionConfig()
        assert cfg.max_resolution == (1920, 1080)

    def test_default_compression(self) -> None:
        cfg = VisionConfig()
        assert isinstance(cfg.compression, CompressionConfig)
        assert cfg.compression.format == "jpeg"

    def test_invalid_capture_region_raises(self) -> None:
        with pytest.raises(ValueError, match="capture_region"):
            VisionConfig(capture_region="quadrant")

    def test_invalid_capture_interval_raises(self) -> None:
        with pytest.raises(ValueError, match="capture_interval_s"):
            VisionConfig(capture_interval_s=0)

    def test_full_screen_region(self) -> None:
        cfg = VisionConfig(capture_region="full-screen")
        assert cfg.capture_region == "full-screen"

    def test_custom_rect_region(self) -> None:
        cfg = VisionConfig(capture_region="custom-rect")
        assert cfg.capture_region == "custom-rect"

    def test_load_capture_scope_from_yaml(self, tmp_path: Path) -> None:
        yaml_content = dedent("""\
            vision:
              capture_region: full-screen
              capture_interval_s: 0.5
              max_resolution: [1280, 720]
              compression:
                format: png
                quality: 100
        """)
        cfg_file = tmp_path / "scope.yaml"
        cfg_file.write_text(yaml_content)
        cfg = load_vision_config(cfg_file)
        assert cfg.capture_region == "full-screen"
        assert cfg.capture_interval_s == 0.5
        assert cfg.max_resolution == (1280, 720)
        assert cfg.compression.format == "png"
        assert cfg.compression.quality == 100

    def test_load_project_config_has_scope(self) -> None:
        cfg_path = Path(__file__).resolve().parents[1] / "config" / "sensory_vision.yaml"
        if not cfg_path.exists():
            pytest.skip("sensory_vision.yaml not found")
        cfg = load_vision_config(cfg_path)
        assert cfg.capture_region == "active-window"
        assert cfg.capture_interval_s == 1.0
        assert cfg.compression.format == "jpeg"
