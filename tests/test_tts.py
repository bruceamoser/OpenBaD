"""Tests for TTS output — Issue #53."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest

from openbad.nervous_system.schemas import TTSComplete, TTSRequest
from openbad.sensory.audio.config import TTSConfig
from openbad.sensory.audio.tts import SynthResult, TTSEngine

# ---------------------------------------------------------------------------
# SynthResult
# ---------------------------------------------------------------------------


class TestSynthResult:
    def test_defaults(self) -> None:
        r = SynthResult()
        assert r.success is True
        assert r.error == ""
        assert r.audio_bytes == b""
        assert len(r.request_id) == 12

    def test_to_proto(self) -> None:
        r = SynthResult(request_id="abc123", duration_ms=500.0, success=True)
        proto = r.to_proto()
        assert isinstance(proto, TTSComplete)
        assert proto.request_id == "abc123"
        assert proto.duration_ms == pytest.approx(500.0)
        assert proto.success is True
        assert proto.header.source_module == "sensory.audio.tts"

    def test_failure_proto(self) -> None:
        r = SynthResult(success=False, error="model crashed")
        proto = r.to_proto()
        assert proto.success is False
        assert proto.error == "model crashed"

    def test_proto_roundtrip(self) -> None:
        r = SynthResult(request_id="x", duration_ms=100.0, success=True)
        data = r.to_proto().SerializeToString()
        restored = TTSComplete()
        restored.ParseFromString(data)
        assert restored.request_id == "x"


# ---------------------------------------------------------------------------
# TTSEngine — config
# ---------------------------------------------------------------------------


class TestTTSEngineConfig:
    def test_defaults(self) -> None:
        eng = TTSEngine()
        assert eng.is_loaded is False
        assert eng.synth_count == 0
        assert eng.config.engine == "piper"

    def test_custom_config(self) -> None:
        cfg = TTSConfig(engine="piper", model_path="/models/voice.onnx")
        eng = TTSEngine(config=cfg)
        assert eng.config.model_path == "/models/voice.onnx"


class TestTTSEngineNotLoaded:
    def test_raises(self) -> None:
        eng = TTSEngine()
        with pytest.raises(RuntimeError, match="Voice not loaded"):
            eng.synthesize("hello")


class TestTTSEngineImportError:
    def test_import_error(self) -> None:
        with patch.dict("sys.modules", {"piper": None}):
            cfg = TTSConfig(model_path="/models/v.onnx")
            eng = TTSEngine(config=cfg)
            with pytest.raises(RuntimeError, match="piper-tts is required"):
                eng.load_voice()

    def test_no_model_path(self) -> None:
        mock_piper = MagicMock()
        with patch.dict("sys.modules", {"piper": mock_piper}):
            eng = TTSEngine(config=TTSConfig(model_path=""))
            with pytest.raises(ValueError, match="model_path must be set"):
                eng.load_voice()


# ---------------------------------------------------------------------------
# TTSEngine — mocked voice
# ---------------------------------------------------------------------------

def _make_fake_wav(size: int = 3244) -> bytes:
    """Create a minimal fake WAV with a 44-byte header + data."""
    return b"RIFF" + b"\x00" * 40 + b"\x01" * (size - 44)


class TestTTSEngineMocked:
    @pytest.fixture()
    def engine(self) -> TTSEngine:
        cfg = TTSConfig(model_path="/models/voice.onnx")
        eng = TTSEngine(config=cfg)
        mock_voice = MagicMock()

        def fake_synth(text: str, wav_io: io.BytesIO) -> None:
            wav_io.write(_make_fake_wav())

        mock_voice.synthesize.side_effect = fake_synth
        eng._voice = mock_voice
        return eng

    def test_synthesize_text(self, engine: TTSEngine) -> None:
        result = engine.synthesize("hello world")
        assert result.success is True
        assert len(result.audio_bytes) > 0
        assert result.duration_ms > 0
        assert result.processing_ms >= 0
        assert engine.synth_count == 1

    def test_synthesize_empty(self, engine: TTSEngine) -> None:
        result = engine.synthesize("   ")
        assert result.success is True
        assert result.audio_bytes == b""
        assert result.duration_ms == 0.0

    def test_synthesize_error(self, engine: TTSEngine) -> None:
        engine._voice.synthesize.side_effect = RuntimeError("boom")
        result = engine.synthesize("hello")
        assert result.success is False
        assert "boom" in result.error

    def test_synth_count_accumulates(self, engine: TTSEngine) -> None:
        engine.synthesize("a")
        engine.synthesize("b")
        assert engine.synth_count == 2

    def test_handle_request_text(self, engine: TTSEngine) -> None:
        req = TTSRequest(text="say this")
        result = engine.handle_request(req)
        assert result.success is True
        assert len(result.audio_bytes) > 0

    def test_handle_request_ssml(self, engine: TTSEngine) -> None:
        req = TTSRequest(text="fallback", ssml="<speak>hello</speak>")
        engine.handle_request(req)
        assert engine._voice.synthesize.call_count == 1
        call_text = engine._voice.synthesize.call_args[0][0]
        assert call_text == "<speak>hello</speak>"


# ---------------------------------------------------------------------------
# TTSEngine — async publish
# ---------------------------------------------------------------------------


class TestTTSEnginePublish:
    async def test_publishes_complete(self) -> None:
        published: list[tuple[str, bytes]] = []

        async def mock_pub(topic: str, payload: bytes) -> None:
            published.append((topic, payload))

        cfg = TTSConfig(model_path="/models/voice.onnx")
        eng = TTSEngine(config=cfg, publish_fn=mock_pub)
        mock_voice = MagicMock()

        def fake_synth(text: str, wav_io: io.BytesIO) -> None:
            wav_io.write(_make_fake_wav())

        mock_voice.synthesize.side_effect = fake_synth
        eng._voice = mock_voice

        result = await eng.synthesize_and_publish("hello")
        assert result.success is True

        assert len(published) == 1
        topic, payload = published[0]
        assert topic == "agent/sensory/audio/tts/complete"

        restored = TTSComplete()
        restored.ParseFromString(payload)
        assert restored.success is True

    async def test_no_publish_fn(self) -> None:
        cfg = TTSConfig(model_path="/models/v.onnx")
        eng = TTSEngine(config=cfg)
        mock_voice = MagicMock()

        def fake_synth(text: str, wav_io: io.BytesIO) -> None:
            wav_io.write(_make_fake_wav())

        mock_voice.synthesize.side_effect = fake_synth
        eng._voice = mock_voice

        result = await eng.synthesize_and_publish("hello")
        assert result.success is True
