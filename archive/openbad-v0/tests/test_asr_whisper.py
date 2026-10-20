"""Tests for Whisper high-accuracy transcription — Issue #51."""

from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from openbad.nervous_system.schemas import TranscriptionEvent
from openbad.sensory.audio.asr_whisper import (
    WhisperResult,
    WhisperSegment,
    WhisperTranscriber,
)

# ---------------------------------------------------------------------------
# WhisperSegment
# ---------------------------------------------------------------------------


class TestWhisperSegment:
    def test_basic(self) -> None:
        s = WhisperSegment(text="hello", start=0.0, end=1.5)
        assert s.text == "hello"
        assert s.start == 0.0
        assert s.end == 1.5

    def test_confidence_from_logprob(self) -> None:
        # avg_logprob=0 → confidence=1.0 (exp(0)=1)
        s = WhisperSegment(text="x", avg_logprob=0.0)
        assert abs(s.confidence - 1.0) < 0.001

    def test_confidence_low(self) -> None:
        # avg_logprob=-2.0 → exp(-2)≈0.135
        s = WhisperSegment(text="x", avg_logprob=-2.0)
        assert abs(s.confidence - math.exp(-2.0)) < 0.001

    def test_confidence_clamped(self) -> None:
        # Very negative → effectively zero
        s = WhisperSegment(text="x", avg_logprob=-100.0)
        assert s.confidence < 0.001


# ---------------------------------------------------------------------------
# WhisperResult
# ---------------------------------------------------------------------------


class TestWhisperResult:
    def test_empty(self) -> None:
        r = WhisperResult()
        assert r.full_text == ""
        assert r.avg_confidence == 0.0

    def test_full_text(self) -> None:
        r = WhisperResult(segments=[
            WhisperSegment(text=" Hello "),
            WhisperSegment(text=" world "),
        ])
        assert r.full_text == "Hello world"

    def test_avg_confidence(self) -> None:
        r = WhisperResult(segments=[
            WhisperSegment(text="a", avg_logprob=0.0),  # conf=1.0
            WhisperSegment(text="b", avg_logprob=-0.693),  # conf≈0.5
        ])
        assert 0.7 < r.avg_confidence < 0.8

    def test_to_proto(self) -> None:
        r = WhisperResult(segments=[
            WhisperSegment(text="test", avg_logprob=-0.1),
        ])
        proto = r.to_proto(source_id="mic")
        assert isinstance(proto, TranscriptionEvent)
        assert proto.text == "test"
        assert proto.source_id == "mic"
        assert proto.is_final is True
        assert proto.header.source_module == "sensory.audio.asr_whisper"

    def test_proto_roundtrip(self) -> None:
        r = WhisperResult(segments=[WhisperSegment(text="hello")])
        data = r.to_proto("app").SerializeToString()
        restored = TranscriptionEvent()
        restored.ParseFromString(data)
        assert restored.text == "hello"


# ---------------------------------------------------------------------------
# WhisperTranscriber — config
# ---------------------------------------------------------------------------


class TestWhisperTranscriberConfig:
    def test_defaults(self) -> None:
        t = WhisperTranscriber()
        assert t.model_size == "base"
        assert t.is_loaded is False

    def test_not_loaded_raises(self) -> None:
        t = WhisperTranscriber()
        with pytest.raises(RuntimeError, match="Model not loaded"):
            t.transcribe("audio.wav")


class TestWhisperTranscriberImportError:
    def test_import_error(self) -> None:
        with patch.dict("sys.modules", {"faster_whisper": None}):
            t = WhisperTranscriber(model_size="base")
            with pytest.raises(RuntimeError, match="faster-whisper is required"):
                t.load_model()

    def test_invalid_model_size(self) -> None:
        mock_fw = MagicMock()
        with patch.dict("sys.modules", {"faster_whisper": mock_fw}):
            t = WhisperTranscriber(model_size="invalid")
            with pytest.raises(ValueError, match="Invalid model size"):
                t.load_model()


# ---------------------------------------------------------------------------
# WhisperTranscriber — with mocked model
# ---------------------------------------------------------------------------


def _make_mock_segment(text: str, start: float = 0.0, end: float = 1.0) -> SimpleNamespace:
    return SimpleNamespace(
        text=text, start=start, end=end,
        avg_logprob=-0.2, no_speech_prob=0.05,
    )


def _make_mock_info(lang: str = "en") -> SimpleNamespace:
    return SimpleNamespace(
        language=lang,
        language_probability=0.98,
        duration=5.0,
    )


class TestWhisperTranscriberMocked:
    @pytest.fixture()
    def transcriber(self) -> WhisperTranscriber:
        t = WhisperTranscriber(model_size="base")
        mock_model = MagicMock()
        t._model = mock_model
        return t

    def test_transcribe_segments(self, transcriber: WhisperTranscriber) -> None:
        segs = [
            _make_mock_segment("Hello world", 0.0, 2.0),
            _make_mock_segment("testing now", 2.0, 4.0),
        ]
        transcriber._model.transcribe.return_value = (segs, _make_mock_info())

        result = transcriber.transcribe("audio.wav")
        assert len(result.segments) == 2
        assert result.full_text == "Hello world testing now"
        assert result.language == "en"
        assert result.processing_ms >= 0

    def test_transcribe_empty(self, transcriber: WhisperTranscriber) -> None:
        transcriber._model.transcribe.return_value = ([], _make_mock_info())
        result = transcriber.transcribe("silence.wav")
        assert result.full_text == ""
        assert result.avg_confidence == 0.0

    def test_transcribe_with_language(self, transcriber: WhisperTranscriber) -> None:
        segs = [_make_mock_segment("Bonjour")]
        transcriber._model.transcribe.return_value = (segs, _make_mock_info("fr"))
        result = transcriber.transcribe("audio.wav", language="fr")
        transcriber._model.transcribe.assert_called_once_with("audio.wav", language="fr")
        assert result.language == "fr"


# ---------------------------------------------------------------------------
# WhisperTranscriber — async publish
# ---------------------------------------------------------------------------


class TestWhisperTranscriberPublish:
    async def test_publish_on_result(self) -> None:
        published: list[tuple[str, bytes]] = []

        async def mock_publish(topic: str, payload: bytes) -> None:
            published.append((topic, payload))

        t = WhisperTranscriber(publish_fn=mock_publish)
        t._model = MagicMock()

        segs = [_make_mock_segment("check calendar")]
        t._model.transcribe.return_value = (segs, _make_mock_info())

        result = await t.transcribe_and_publish("mic", "audio.wav")
        assert result.full_text == "check calendar"

        assert len(published) == 1
        topic, payload = published[0]
        assert topic == "agent/sensory/audio/mic"

        restored = TranscriptionEvent()
        restored.ParseFromString(payload)
        assert restored.text == "check calendar"

    async def test_no_publish_on_empty(self) -> None:
        published: list[tuple[str, bytes]] = []

        async def mock_publish(topic: str, payload: bytes) -> None:
            published.append((topic, payload))

        t = WhisperTranscriber(publish_fn=mock_publish)
        t._model = MagicMock()
        t._model.transcribe.return_value = ([], _make_mock_info())

        await t.transcribe_and_publish("mic", "silence.wav")
        assert len(published) == 0

    async def test_no_publish_fn(self) -> None:
        t = WhisperTranscriber()
        t._model = MagicMock()
        segs = [_make_mock_segment("hello")]
        t._model.transcribe.return_value = (segs, _make_mock_info())

        result = await t.transcribe_and_publish("mic", "audio.wav")
        assert result.full_text == "hello"
