"""Tests for unified sensory config — Issue #226."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from openbad.sensory.audio.config import AudioConfig
from openbad.sensory.config import (
    SensoryConfig,
    load_sensory_config,
)

# ── Defaults ──────────────────────────────────────────────────────── #


class TestSensoryConfigDefaults:
    def test_hearing_defaults(self) -> None:
        cfg = SensoryConfig()
        assert cfg.hearing.capture.sample_rate == 16000
        assert cfg.hearing.asr.default_engine == "vosk"
        assert cfg.hearing.wake_word.phrases == ["hey agent"]

    def test_vision_defaults(self) -> None:
        cfg = SensoryConfig()
        assert cfg.vision.fps_idle == 1.0
        assert cfg.vision.attention.ssim_threshold == 0.05

    def test_speech_defaults(self) -> None:
        cfg = SensoryConfig()
        assert cfg.speech.tts.engine == "piper"

    def test_to_audio_config(self) -> None:
        cfg = SensoryConfig()
        audio = cfg.to_audio_config()
        assert isinstance(audio, AudioConfig)
        assert audio.capture is cfg.hearing.capture
        assert audio.tts is cfg.speech.tts


# ── Loading ───────────────────────────────────────────────────────── #


class TestLoadSensoryConfig:
    def test_none_returns_defaults(self) -> None:
        cfg = load_sensory_config(Path("/nonexistent/senses.yaml"))
        assert cfg.hearing.capture.sample_rate == 16000
        assert cfg.vision.fps_idle == 1.0

    def test_loads_senses_yaml(self, tmp_path: Path) -> None:
        senses = tmp_path / "senses.yaml"
        senses.write_text(
            "hearing:\n"
            "  capture:\n"
            "    sample_rate: 44100\n"
            "vision:\n"
            "  fps_idle: 2.0\n"
            "speech:\n"
            "  tts:\n"
            "    engine: espeak\n"
        )
        cfg = load_sensory_config(senses)
        assert cfg.hearing.capture.sample_rate == 44100
        assert cfg.vision.fps_idle == 2.0
        assert cfg.speech.tts.engine == "espeak"

    def test_partial_config_preserves_defaults(self, tmp_path: Path) -> None:
        senses = tmp_path / "senses.yaml"
        senses.write_text("hearing:\n  capture:\n    sample_rate: 8000\n")
        cfg = load_sensory_config(senses)
        assert cfg.hearing.capture.sample_rate == 8000
        assert cfg.hearing.capture.channels == 1  # default preserved
        assert cfg.vision.fps_idle == 1.0  # default preserved

    def test_unknown_keys_ignored(self, tmp_path: Path) -> None:
        senses = tmp_path / "senses.yaml"
        senses.write_text("hearing:\n  capture:\n    sample_rate: 8000\n    bogus: 42\n")
        cfg = load_sensory_config(senses)
        assert cfg.hearing.capture.sample_rate == 8000

    def test_loads_project_senses_yaml(self) -> None:
        cfg_path = Path(__file__).resolve().parents[1] / "config" / "senses.yaml"
        if not cfg_path.exists():
            pytest.skip("config/senses.yaml not found")
        cfg = load_sensory_config(cfg_path)
        assert cfg.hearing.capture.sample_rate == 16000
        assert cfg.vision.fps_idle == 1.0
        assert cfg.speech.tts.engine == "piper"

    def test_vision_attention_parsed(self, tmp_path: Path) -> None:
        senses = tmp_path / "senses.yaml"
        senses.write_text(
            "vision:\n"
            "  fps_idle: 3.0\n"
            "  attention:\n"
            "    ssim_threshold: 0.1\n"
            "    cooldown_ms: 250\n"
        )
        cfg = load_sensory_config(senses)
        assert cfg.vision.fps_idle == 3.0
        assert cfg.vision.attention.ssim_threshold == 0.1
        assert cfg.vision.attention.cooldown_ms == 250


# ── Backward-compat ───────────────────────────────────────────────── #


class TestBackwardCompat:
    def test_merges_legacy_audio(self, tmp_path: Path) -> None:
        # No senses.yaml exists — only legacy file
        legacy = tmp_path / "sensory_audio.yaml"
        legacy.write_text(
            "audio:\n"
            "  capture:\n"
            "    sample_rate: 22050\n"
            "  tts:\n"
            "    engine: festival\n"
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            cfg = load_sensory_config(tmp_path / "senses.yaml")
        assert cfg.hearing.capture.sample_rate == 22050
        assert cfg.speech.tts.engine == "festival"
        assert any("sensory_audio.yaml is deprecated" in str(x.message) for x in w)

    def test_merges_legacy_vision(self, tmp_path: Path) -> None:
        legacy = tmp_path / "sensory_vision.yaml"
        legacy.write_text(
            "vision:\n"
            "  fps_idle: 10.0\n"
            "  attention:\n"
            "    ssim_threshold: 0.2\n"
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            cfg = load_sensory_config(tmp_path / "senses.yaml")
        assert cfg.vision.fps_idle == 10.0
        assert cfg.vision.attention.ssim_threshold == 0.2
        assert any("sensory_vision.yaml is deprecated" in str(x.message) for x in w)

    def test_senses_yaml_takes_precedence_over_legacy(self, tmp_path: Path) -> None:
        senses = tmp_path / "senses.yaml"
        senses.write_text("hearing:\n  capture:\n    sample_rate: 48000\n")
        legacy = tmp_path / "sensory_audio.yaml"
        legacy.write_text("audio:\n  capture:\n    sample_rate: 8000\n")
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            cfg = load_sensory_config(senses)
        # senses.yaml takes precedence
        assert cfg.hearing.capture.sample_rate == 48000

    def test_merges_both_legacy_files(self, tmp_path: Path) -> None:
        (tmp_path / "sensory_audio.yaml").write_text(
            "audio:\n  capture:\n    sample_rate: 22050\n"
        )
        (tmp_path / "sensory_vision.yaml").write_text(
            "vision:\n  fps_idle: 7.5\n"
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            cfg = load_sensory_config(tmp_path / "senses.yaml")
        assert cfg.hearing.capture.sample_rate == 22050
        assert cfg.vision.fps_idle == 7.5
        assert len([x for x in w if issubclass(x.category, DeprecationWarning)]) == 2


# ── Validation ────────────────────────────────────────────────────── #


class TestValidation:
    def test_empty_yaml_returns_defaults(self, tmp_path: Path) -> None:
        senses = tmp_path / "senses.yaml"
        senses.write_text("")
        cfg = load_sensory_config(senses)
        assert cfg.hearing.capture.sample_rate == 16000
        assert cfg.vision.fps_idle == 1.0
