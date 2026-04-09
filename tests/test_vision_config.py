"""Tests for vision configuration — Issue #44."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from openbad.sensory.vision.config import (
    AttentionConfig,
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
