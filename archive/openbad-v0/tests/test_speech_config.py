"""Tests for speech/TTS config — Issue #229."""

from __future__ import annotations

import pytest

from openbad.sensory.audio.config import TTSConfig
from openbad.sensory.audio.tts_factory import DisabledTTSEngine, create_tts_engine

# ── TTSConfig validation ─────────────────────────────────────────── #


class TestTTSConfigValidation:
    def test_defaults(self) -> None:
        cfg = TTSConfig()
        assert cfg.engine == "piper"
        assert cfg.speaking_rate == 1.0
        assert cfg.volume == 1.0

    def test_piper_valid(self) -> None:
        TTSConfig(engine="piper")

    def test_espeak_valid(self) -> None:
        TTSConfig(engine="espeak")

    def test_disabled_valid(self) -> None:
        TTSConfig(engine="disabled")

    def test_invalid_engine_raises(self) -> None:
        with pytest.raises(ValueError, match="tts.engine must be one of"):
            TTSConfig(engine="festival")

    def test_speaking_rate_bounds(self) -> None:
        TTSConfig(speaking_rate=0.25)
        TTSConfig(speaking_rate=4.0)

    def test_speaking_rate_too_low(self) -> None:
        with pytest.raises(ValueError, match="tts.speaking_rate must be 0.25-4.0"):
            TTSConfig(speaking_rate=0.1)

    def test_speaking_rate_too_high(self) -> None:
        with pytest.raises(ValueError, match="tts.speaking_rate must be 0.25-4.0"):
            TTSConfig(speaking_rate=5.0)

    def test_volume_bounds(self) -> None:
        TTSConfig(volume=0.0)
        TTSConfig(volume=1.0)

    def test_volume_too_low(self) -> None:
        with pytest.raises(ValueError, match="tts.volume must be 0.0-1.0"):
            TTSConfig(volume=-0.1)

    def test_volume_too_high(self) -> None:
        with pytest.raises(ValueError, match="tts.volume must be 0.0-1.0"):
            TTSConfig(volume=1.5)

    def test_voice_model_field(self) -> None:
        cfg = TTSConfig(voice_model="en_US-lessac-medium")
        assert cfg.voice_model == "en_US-lessac-medium"


# ── TTS engine factory ───────────────────────────────────────────── #


class TestCreateTTSEngine:
    def test_piper_engine(self) -> None:
        cfg = TTSConfig(engine="piper", model_path="/voice.onnx")
        engine = create_tts_engine(cfg)
        assert not isinstance(engine, DisabledTTSEngine)
        assert engine.config.engine == "piper"

    def test_espeak_engine(self) -> None:
        cfg = TTSConfig(engine="espeak")
        engine = create_tts_engine(cfg)
        assert not isinstance(engine, DisabledTTSEngine)
        assert engine.config.engine == "espeak"

    def test_disabled_engine(self) -> None:
        cfg = TTSConfig(engine="disabled")
        engine = create_tts_engine(cfg)
        assert isinstance(engine, DisabledTTSEngine)

    def test_default_creates_piper(self) -> None:
        engine = create_tts_engine()
        assert engine.config.engine == "piper"


# ── Disabled TTS engine ──────────────────────────────────────────── #


class TestDisabledTTSEngine:
    def test_is_loaded(self) -> None:
        engine = DisabledTTSEngine()
        assert engine.is_loaded

    def test_load_voice_noop(self) -> None:
        engine = DisabledTTSEngine()
        engine.load_voice()  # should not raise

    def test_synthesize_returns_empty(self) -> None:
        engine = DisabledTTSEngine()
        result = engine.synthesize("hello world")
        assert result.success
        assert result.audio_bytes == b""
        assert result.duration_ms == 0.0

    @pytest.mark.asyncio
    async def test_synthesize_and_publish_suppressed(self) -> None:
        engine = DisabledTTSEngine()
        result = await engine.synthesize_and_publish("hello")
        assert result.success
        assert result.audio_bytes == b""

    def test_handle_request_suppressed(self) -> None:
        from types import SimpleNamespace

        engine = DisabledTTSEngine()
        req = SimpleNamespace(text="test", ssml="")
        result = engine.handle_request(req)
        assert result.success
        assert result.audio_bytes == b""


# ── Config YAML round-trip ────────────────────────────────────────── #


class TestSpeechYAML:
    def test_load_speech_from_senses(self, tmp_path: object) -> None:
        from pathlib import Path

        import yaml

        from openbad.sensory.config import load_sensory_config

        p = Path(str(tmp_path)) / "senses.yaml"
        p.write_text(yaml.dump({
            "speech": {
                "tts": {
                    "engine": "espeak",
                    "voice_model": "en-us",
                    "speaking_rate": 1.5,
                    "volume": 0.8,
                },
            },
        }))
        cfg = load_sensory_config(p)
        assert cfg.speech.tts.engine == "espeak"
        assert cfg.speech.tts.voice_model == "en-us"
        assert cfg.speech.tts.speaking_rate == 1.5
        assert cfg.speech.tts.volume == 0.8

    def test_disabled_from_yaml(self, tmp_path: object) -> None:
        from pathlib import Path

        import yaml

        from openbad.sensory.config import load_sensory_config

        p = Path(str(tmp_path)) / "senses.yaml"
        p.write_text(yaml.dump({
            "speech": {"tts": {"engine": "disabled"}},
        }))
        cfg = load_sensory_config(p)
        assert cfg.speech.tts.engine == "disabled"
        engine = create_tts_engine(cfg.speech.tts)
        assert isinstance(engine, DisabledTTSEngine)

    def test_invalid_engine_from_yaml(self, tmp_path: object) -> None:
        from pathlib import Path

        import yaml

        from openbad.sensory.config import load_sensory_config

        p = Path(str(tmp_path)) / "senses.yaml"
        p.write_text(yaml.dump({
            "speech": {"tts": {"engine": "festival"}},
        }))
        with pytest.raises(ValueError, match="tts.engine must be one of"):
            load_sensory_config(p)

    def test_to_audio_config_preserves_speech(self) -> None:
        from openbad.sensory.config import HearingConfig, SensoryConfig, SpeechConfig

        sensory = SensoryConfig(
            hearing=HearingConfig(),
            speech=SpeechConfig(
                tts=TTSConfig(
                    engine="espeak",
                    speaking_rate=2.0,
                    volume=0.5,
                ),
            ),
        )
        audio = sensory.to_audio_config()
        assert audio.tts.engine == "espeak"
        assert audio.tts.speaking_rate == 2.0
        assert audio.tts.volume == 0.5
