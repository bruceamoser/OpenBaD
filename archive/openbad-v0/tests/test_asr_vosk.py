"""Tests for Vosk streaming ASR — Issue #50."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from openbad.nervous_system.schemas import TranscriptionEvent
from openbad.sensory.audio.asr_vosk import TranscriptionResult, VoskRecogniser

# ---------------------------------------------------------------------------
# TranscriptionResult
# ---------------------------------------------------------------------------


class TestTranscriptionResult:
    def test_basic(self) -> None:
        r = TranscriptionResult(text="hello world", confidence=0.95)
        assert r.text == "hello world"
        assert r.confidence == 0.95
        assert r.is_final is True

    def test_partial(self) -> None:
        r = TranscriptionResult(text="hello", confidence=0.0, is_final=False)
        assert r.is_final is False

    def test_to_proto(self) -> None:
        r = TranscriptionResult(
            text="test transcription",
            confidence=0.88,
            is_final=True,
            source_id="mic",
        )
        proto = r.to_proto()
        assert isinstance(proto, TranscriptionEvent)
        assert proto.text == "test transcription"
        assert abs(proto.confidence - 0.88) < 0.001
        assert proto.is_final is True
        assert proto.source_id == "mic"
        assert proto.header.source_module == "sensory.audio.asr_vosk"

    def test_proto_roundtrip(self) -> None:
        r = TranscriptionResult(text="hello", confidence=0.9, source_id="app")
        proto = r.to_proto()
        data = proto.SerializeToString()
        restored = TranscriptionEvent()
        restored.ParseFromString(data)
        assert restored.text == "hello"


# ---------------------------------------------------------------------------
# VoskRecogniser — config
# ---------------------------------------------------------------------------


class TestVoskRecogniserConfig:
    def test_defaults(self) -> None:
        rec = VoskRecogniser()
        assert rec.is_loaded is False

    def test_not_loaded_raises(self) -> None:
        rec = VoskRecogniser()
        with pytest.raises(RuntimeError, match="Model not loaded"):
            rec.accept_waveform(b"\x00" * 100)


class TestVoskRecogniserImportError:
    def test_import_error(self) -> None:
        with patch.dict("sys.modules", {"vosk": None}):
            rec = VoskRecogniser(model_path="/some/model")
            with pytest.raises(RuntimeError, match="vosk is required"):
                rec.load_model()

    def test_empty_model_path(self) -> None:
        mock_vosk = MagicMock()
        with patch.dict("sys.modules", {"vosk": mock_vosk}):
            rec = VoskRecogniser(model_path="")
            with pytest.raises(ValueError, match="model path must be specified"):
                rec.load_model()


# ---------------------------------------------------------------------------
# VoskRecogniser — with mocked vosk
# ---------------------------------------------------------------------------


class TestVoskRecogniserMocked:
    @pytest.fixture()
    def recogniser(self) -> VoskRecogniser:
        """Create a VoskRecogniser with mocked internals."""
        rec = VoskRecogniser(model_path="/test/model", sample_rate=16000)
        mock_rec = MagicMock()
        rec._model = MagicMock()
        rec._recogniser = mock_rec
        return rec

    def test_final_result_from_accept(self, recogniser: VoskRecogniser) -> None:
        mock_rec = recogniser._recogniser
        mock_rec.AcceptWaveform.return_value = True
        mock_rec.Result.return_value = json.dumps({
            "text": "hello world",
            "result": [
                {"conf": 0.98, "word": "hello"},
                {"conf": 0.95, "word": "world"},
            ],
        })

        result = recogniser.accept_waveform(b"\x00" * 3200)
        assert result is not None
        assert result.text == "hello world"
        assert result.is_final is True
        assert abs(result.confidence - 0.965) < 0.01

    def test_partial_result(self, recogniser: VoskRecogniser) -> None:
        mock_rec = recogniser._recogniser
        mock_rec.AcceptWaveform.return_value = False
        mock_rec.PartialResult.return_value = json.dumps({"partial": "hello"})

        result = recogniser.accept_waveform(b"\x00" * 1600)
        assert result is not None
        assert result.text == "hello"
        assert result.is_final is False
        assert result.confidence == 0.0

    def test_silence_returns_none(self, recogniser: VoskRecogniser) -> None:
        mock_rec = recogniser._recogniser
        mock_rec.AcceptWaveform.return_value = False
        mock_rec.PartialResult.return_value = json.dumps({"partial": ""})

        result = recogniser.accept_waveform(b"\x00" * 1600)
        assert result is None

    def test_final_empty_text(self, recogniser: VoskRecogniser) -> None:
        mock_rec = recogniser._recogniser
        mock_rec.AcceptWaveform.return_value = True
        mock_rec.Result.return_value = json.dumps({"text": ""})

        result = recogniser.accept_waveform(b"\x00" * 3200)
        assert result is None

    def test_flush_final_result(self, recogniser: VoskRecogniser) -> None:
        mock_rec = recogniser._recogniser
        mock_rec.FinalResult.return_value = json.dumps({
            "text": "goodbye",
            "result": [{"conf": 0.9, "word": "goodbye"}],
        })

        result = recogniser.final_result()
        assert result is not None
        assert result.text == "goodbye"
        assert result.is_final is True

    def test_flush_empty(self, recogniser: VoskRecogniser) -> None:
        mock_rec = recogniser._recogniser
        mock_rec.FinalResult.return_value = json.dumps({"text": ""})
        assert recogniser.final_result() is None

    def test_final_result_no_model(self) -> None:
        rec = VoskRecogniser()
        assert rec.final_result() is None

    def test_confidence_no_words(self) -> None:
        assert VoskRecogniser._extract_confidence({"text": "hi"}) == 0.0

    def test_confidence_with_words(self) -> None:
        raw = {"result": [{"conf": 0.8}, {"conf": 0.9}, {"conf": 1.0}]}
        assert abs(VoskRecogniser._extract_confidence(raw) - 0.9) < 0.001


# ---------------------------------------------------------------------------
# VoskRecogniser — async publish
# ---------------------------------------------------------------------------


class TestVoskRecogniserPublish:
    async def test_publish_on_result(self) -> None:
        published: list[tuple[str, bytes]] = []

        async def mock_publish(topic: str, payload: bytes) -> None:
            published.append((topic, payload))

        rec = VoskRecogniser(
            model_path="/test/model",
            publish_fn=mock_publish,
        )
        mock_rec = MagicMock()
        rec._model = MagicMock()
        rec._recogniser = mock_rec

        mock_rec.AcceptWaveform.return_value = True
        mock_rec.Result.return_value = json.dumps({"text": "check calendar"})

        result = await rec.process_and_publish("mic", b"\x00" * 3200)
        assert result is not None
        assert result.source_id == "mic"

        assert len(published) == 1
        topic, payload = published[0]
        assert topic == "agent/sensory/audio/mic"

        restored = TranscriptionEvent()
        restored.ParseFromString(payload)
        assert restored.text == "check calendar"

    async def test_no_publish_on_silence(self) -> None:
        published: list[tuple[str, bytes]] = []

        async def mock_publish(topic: str, payload: bytes) -> None:
            published.append((topic, payload))

        rec = VoskRecogniser(publish_fn=mock_publish)
        mock_rec = MagicMock()
        rec._model = MagicMock()
        rec._recogniser = mock_rec

        mock_rec.AcceptWaveform.return_value = False
        mock_rec.PartialResult.return_value = json.dumps({"partial": ""})

        result = await rec.process_and_publish("mic", b"\x00" * 1600)
        assert result is None
        assert len(published) == 0

    async def test_no_publish_fn(self) -> None:
        rec = VoskRecogniser()
        mock_rec = MagicMock()
        rec._model = MagicMock()
        rec._recogniser = mock_rec

        mock_rec.AcceptWaveform.return_value = True
        mock_rec.Result.return_value = json.dumps({"text": "test"})

        result = await rec.process_and_publish("mic", b"\x00" * 3200)
        assert result is not None
        assert result.text == "test"
