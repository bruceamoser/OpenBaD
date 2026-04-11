"""Tests for hearing and wake-word config — Issue #228."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from openbad.sensory.audio.asr_factory import create_asr_engine
from openbad.sensory.audio.config import (
    ASRConfig,
    AudioCaptureConfig,
    AudioConfig,
    WakeWordConfig,
)

# ── ASRConfig validation ─────────────────────────────────────────── #


class TestASRConfig:
    def test_defaults(self) -> None:
        cfg = ASRConfig()
        assert cfg.default_engine == "vosk"
        assert cfg.vad_sensitivity == 0.5

    def test_vosk_valid(self) -> None:
        cfg = ASRConfig(default_engine="vosk")
        assert cfg.default_engine == "vosk"

    def test_whisper_valid(self) -> None:
        cfg = ASRConfig(default_engine="whisper")
        assert cfg.default_engine == "whisper"

    def test_invalid_engine_raises(self) -> None:
        with pytest.raises(ValueError, match="default_engine must be one of"):
            ASRConfig(default_engine="deepspeech")

    def test_vad_sensitivity_range(self) -> None:
        ASRConfig(vad_sensitivity=0.0)
        ASRConfig(vad_sensitivity=1.0)

    def test_vad_sensitivity_too_low(self) -> None:
        with pytest.raises(ValueError, match="vad_sensitivity must be 0.0-1.0"):
            ASRConfig(vad_sensitivity=-0.1)

    def test_vad_sensitivity_too_high(self) -> None:
        with pytest.raises(ValueError, match="vad_sensitivity must be 0.0-1.0"):
            ASRConfig(vad_sensitivity=1.5)


# ── WakeWordConfig validation ────────────────────────────────────── #


class TestWakeWordConfig:
    def test_defaults(self) -> None:
        cfg = WakeWordConfig()
        assert cfg.phrases == ["hey agent"]
        assert cfg.threshold == 0.5

    def test_empty_phrases_raises(self) -> None:
        with pytest.raises(ValueError, match="phrases must not be empty"):
            WakeWordConfig(phrases=[])

    def test_threshold_bounds(self) -> None:
        WakeWordConfig(threshold=0.0)
        WakeWordConfig(threshold=1.0)

    def test_threshold_too_low(self) -> None:
        with pytest.raises(ValueError, match="threshold must be 0.0-1.0"):
            WakeWordConfig(threshold=-0.1)

    def test_threshold_too_high(self) -> None:
        with pytest.raises(ValueError, match="threshold must be 0.0-1.0"):
            WakeWordConfig(threshold=1.5)


# ── ASR engine factory ───────────────────────────────────────────── #


class TestCreateASREngine:
    def test_vosk_engine(self) -> None:
        cfg = AudioConfig(
            asr=ASRConfig(
                default_engine="vosk",
                vosk_model_path="/models/vosk",
            ),
            capture=AudioCaptureConfig(sample_rate=16000),
        )
        with patch(
            "openbad.sensory.audio.asr_vosk.VoskRecogniser",
        ) as mock_cls:
            engine = create_asr_engine(cfg, publish_fn="pub")
            mock_cls.assert_called_once_with(
                model_path="/models/vosk",
                sample_rate=16000,
                publish_fn="pub",
            )
            assert engine is mock_cls.return_value

    def test_whisper_engine(self) -> None:
        cfg = AudioConfig(
            asr=ASRConfig(
                default_engine="whisper",
                whisper_model="small",
            ),
        )
        with patch(
            "openbad.sensory.audio.asr_whisper.WhisperTranscriber",
        ) as mock_cls:
            engine = create_asr_engine(cfg)
            mock_cls.assert_called_once_with(
                model_size="small",
                publish_fn=None,
            )
            assert engine is mock_cls.return_value

    def test_unknown_engine_raises(self) -> None:
        cfg = AudioConfig(asr=ASRConfig.__new__(ASRConfig))
        cfg.asr.default_engine = "unknown"
        with pytest.raises(ValueError, match="Unknown ASR engine"):
            create_asr_engine(cfg)


# ── Config YAML round-trip ────────────────────────────────────────── #


class TestHearingYAML:
    def test_load_vad_sensitivity(self, tmp_path: object) -> None:
        from pathlib import Path

        import yaml

        from openbad.sensory.audio.config import load_audio_config

        p = Path(str(tmp_path)) / "audio.yaml"
        p.write_text(yaml.dump({"audio": {
            "asr": {"default_engine": "vosk", "vad_sensitivity": 0.8},
            "wake_word": {"phrases": ["computer"], "threshold": 0.7},
        }}))
        cfg = load_audio_config(p)
        assert cfg.asr.vad_sensitivity == 0.8
        assert cfg.asr.default_engine == "vosk"
        assert cfg.wake_word.phrases == ["computer"]
        assert cfg.wake_word.threshold == 0.7

    def test_load_invalid_engine_from_yaml(self, tmp_path: object) -> None:
        from pathlib import Path

        import yaml

        from openbad.sensory.audio.config import load_audio_config

        p = Path(str(tmp_path)) / "audio.yaml"
        p.write_text(yaml.dump({"audio": {
            "asr": {"default_engine": "invalid"},
        }}))
        with pytest.raises(ValueError, match="default_engine must be one of"):
            load_audio_config(p)

    def test_unified_hearing_config(self, tmp_path: object) -> None:
        from pathlib import Path

        import yaml

        from openbad.sensory.config import load_sensory_config

        p = Path(str(tmp_path)) / "senses.yaml"
        p.write_text(yaml.dump({
            "hearing": {
                "asr": {
                    "default_engine": "whisper",
                    "whisper_model": "small",
                    "vad_sensitivity": 0.3,
                },
                "wake_word": {
                    "phrases": ["jarvis", "friday"],
                    "threshold": 0.6,
                },
            },
        }))
        cfg = load_sensory_config(p)
        assert cfg.hearing.asr.default_engine == "whisper"
        assert cfg.hearing.asr.whisper_model == "small"
        assert cfg.hearing.asr.vad_sensitivity == 0.3
        assert cfg.hearing.wake_word.phrases == ["jarvis", "friday"]
        assert cfg.hearing.wake_word.threshold == 0.6

    def test_to_audio_config_preserves_vad(self) -> None:
        from openbad.sensory.config import HearingConfig, SensoryConfig, SpeechConfig

        sensory = SensoryConfig(
            hearing=HearingConfig(
                asr=ASRConfig(vad_sensitivity=0.9),
            ),
            speech=SpeechConfig(),
        )
        audio = sensory.to_audio_config()
        assert audio.asr.vad_sensitivity == 0.9
