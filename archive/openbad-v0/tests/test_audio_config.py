"""Tests for audio configuration — Issue #49."""

from __future__ import annotations

from pathlib import Path

from openbad.sensory.audio.config import (
    ASRConfig,
    AudioCaptureConfig,
    AudioConfig,
    TTSConfig,
    WakeWordConfig,
    load_audio_config,
)

# ---------------------------------------------------------------------------
# AudioCaptureConfig
# ---------------------------------------------------------------------------


class TestAudioCaptureConfig:
    def test_defaults(self) -> None:
        c = AudioCaptureConfig()
        assert c.sample_rate == 16000
        assert c.channels == 1
        assert c.sample_format == "s16le"
        assert c.chunk_duration_ms == 100
        assert c.device == ""
        assert c.passive is True

    def test_chunk_bytes_s16le(self) -> None:
        c = AudioCaptureConfig(sample_rate=16000, channels=1, sample_format="s16le",
                               chunk_duration_ms=100)
        # 16000 * 0.1 = 1600 samples * 1 ch * 2 bytes = 3200
        assert c.chunk_bytes == 3200

    def test_chunk_bytes_f32le(self) -> None:
        c = AudioCaptureConfig(sample_rate=16000, channels=1, sample_format="f32le",
                               chunk_duration_ms=100)
        # 1600 samples * 1 ch * 4 bytes = 6400
        assert c.chunk_bytes == 6400

    def test_chunk_bytes_stereo(self) -> None:
        c = AudioCaptureConfig(sample_rate=48000, channels=2, sample_format="s16le",
                               chunk_duration_ms=50)
        # 48000 * 0.05 = 2400 samples * 2 ch * 2 bytes = 9600
        assert c.chunk_bytes == 9600


# ---------------------------------------------------------------------------
# ASRConfig
# ---------------------------------------------------------------------------


class TestASRConfig:
    def test_defaults(self) -> None:
        c = ASRConfig()
        assert c.vosk_model_path == ""
        assert c.whisper_model == "base"
        assert c.default_engine == "vosk"


# ---------------------------------------------------------------------------
# WakeWordConfig & TTSConfig
# ---------------------------------------------------------------------------


class TestWakeWordConfig:
    def test_defaults(self) -> None:
        c = WakeWordConfig()
        assert c.phrases == ["hey agent"]
        assert c.threshold == 0.5


class TestTTSConfig:
    def test_defaults(self) -> None:
        c = TTSConfig()
        assert c.engine == "piper"
        assert c.model_path == ""
        assert c.output_device == ""


# ---------------------------------------------------------------------------
# AudioConfig
# ---------------------------------------------------------------------------


class TestAudioConfig:
    def test_top_level_defaults(self) -> None:
        config = AudioConfig()
        assert isinstance(config.capture, AudioCaptureConfig)
        assert isinstance(config.asr, ASRConfig)
        assert isinstance(config.wake_word, WakeWordConfig)
        assert isinstance(config.tts, TTSConfig)


# ---------------------------------------------------------------------------
# load_audio_config
# ---------------------------------------------------------------------------


class TestLoadAudioConfig:
    def test_none_returns_defaults(self) -> None:
        config = load_audio_config(None)
        assert config.capture.sample_rate == 16000

    def test_missing_file_returns_defaults(self) -> None:
        config = load_audio_config("/nonexistent/audio.yaml")
        assert config.capture.sample_rate == 16000

    def test_loads_project_config(self) -> None:
        cfg_path = Path(__file__).resolve().parents[1] / "config" / "sensory_audio.yaml"
        if not cfg_path.exists():
            return
        config = load_audio_config(cfg_path)
        assert config.capture.sample_rate == 16000
        assert config.asr.default_engine == "vosk"
        assert config.wake_word.phrases == ["hey agent"]
        assert config.tts.engine == "piper"

    def test_partial_config(self, tmp_path: Path) -> None:
        cfg = tmp_path / "partial.yaml"
        cfg.write_text("audio:\n  capture:\n    sample_rate: 44100\n")
        config = load_audio_config(cfg)
        assert config.capture.sample_rate == 44100
        # Other defaults preserved
        assert config.capture.channels == 1
        assert config.asr.default_engine == "vosk"

    def test_unknown_keys_ignored(self, tmp_path: Path) -> None:
        cfg = tmp_path / "extra.yaml"
        cfg.write_text("audio:\n  capture:\n    sample_rate: 8000\n    unknown_field: 42\n")
        config = load_audio_config(cfg)
        assert config.capture.sample_rate == 8000
