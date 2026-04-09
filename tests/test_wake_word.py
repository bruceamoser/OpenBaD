"""Tests for wake-word detector — Issue #52."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from openbad.nervous_system.schemas import AttentionTrigger, WakeWordEvent
from openbad.sensory.audio.config import WakeWordConfig
from openbad.sensory.audio.wake_word import Detection, WakeWordDetector

# ---------------------------------------------------------------------------
# Detection dataclass
# ---------------------------------------------------------------------------


class TestDetection:
    def test_basic(self) -> None:
        d = Detection(phrase="hey agent", confidence=0.95, timestamp=1000.0)
        assert d.phrase == "hey agent"
        assert d.confidence == 0.95
        assert d.timestamp == 1000.0

    def test_auto_timestamp(self) -> None:
        d = Detection(phrase="test", confidence=0.8)
        assert d.timestamp > 0


# ---------------------------------------------------------------------------
# WakeWordDetector — config
# ---------------------------------------------------------------------------


class TestWakeWordDetectorConfig:
    def test_defaults(self) -> None:
        det = WakeWordDetector()
        assert det.is_loaded is False
        assert det.detection_count == 0
        assert "hey agent" in det.config.phrases

    def test_custom_config(self) -> None:
        cfg = WakeWordConfig(phrases=["ok computer"], threshold=0.8)
        det = WakeWordDetector(config=cfg)
        assert det.config.threshold == 0.8
        assert det.config.phrases == ["ok computer"]


class TestWakeWordDetectorNotLoaded:
    def test_raises_not_loaded(self) -> None:
        det = WakeWordDetector()
        with pytest.raises(RuntimeError, match="Model not loaded"):
            det.process_audio(b"\x00" * 3200)


# ---------------------------------------------------------------------------
# WakeWordDetector — import error handling
# ---------------------------------------------------------------------------


class TestWakeWordDetectorImportError:
    def test_import_error(self) -> None:
        with patch.dict("sys.modules", {"openwakeword": None}):
            det = WakeWordDetector()
            with pytest.raises(RuntimeError, match="openwakeword is required"):
                det.load_model()


# ---------------------------------------------------------------------------
# WakeWordDetector — mocked model
# ---------------------------------------------------------------------------


class TestWakeWordDetectorMocked:
    @pytest.fixture()
    def detector(self) -> WakeWordDetector:
        cfg = WakeWordConfig(phrases=["hey agent", "ok computer"], threshold=0.5)
        det = WakeWordDetector(config=cfg)
        det._model = MagicMock()
        return det

    def test_single_detection(self, detector: WakeWordDetector) -> None:
        detector._model.predict.return_value = {
            "hey agent": 0.9,
            "ok computer": 0.2,
        }
        results = detector.process_audio(b"\x00" * 3200)
        assert len(results) == 1
        assert results[0].phrase == "hey agent"
        assert results[0].confidence == 0.9
        assert detector.detection_count == 1

    def test_multi_detection(self, detector: WakeWordDetector) -> None:
        detector._model.predict.return_value = {
            "hey agent": 0.8,
            "ok computer": 0.7,
        }
        results = detector.process_audio(b"\x00" * 3200)
        assert len(results) == 2
        assert detector.detection_count == 2

    def test_no_detection(self, detector: WakeWordDetector) -> None:
        detector._model.predict.return_value = {
            "hey agent": 0.1,
            "ok computer": 0.05,
        }
        results = detector.process_audio(b"\x00" * 3200)
        assert len(results) == 0
        assert detector.detection_count == 0

    def test_missing_phrase_in_prediction(self, detector: WakeWordDetector) -> None:
        # Model may not return all configured phrases
        detector._model.predict.return_value = {"hey agent": 0.8}
        results = detector.process_audio(b"\x00" * 3200)
        assert len(results) == 1

    def test_detection_count_accumulates(self, detector: WakeWordDetector) -> None:
        detector._model.predict.return_value = {"hey agent": 0.9, "ok computer": 0.1}
        detector.process_audio(b"\x00" * 3200)
        detector.process_audio(b"\x00" * 3200)
        assert detector.detection_count == 2

    def test_threshold_boundary(self, detector: WakeWordDetector) -> None:
        # Exactly at threshold → should detect
        detector._model.predict.return_value = {"hey agent": 0.5}
        results = detector.process_audio(b"\x00" * 3200)
        assert len(results) == 1


# ---------------------------------------------------------------------------
# WakeWordDetector — async publish
# ---------------------------------------------------------------------------


class TestWakeWordDetectorPublish:
    async def test_publishes_both_events(self) -> None:
        published: list[tuple[str, bytes]] = []

        async def mock_pub(topic: str, payload: bytes) -> None:
            published.append((topic, payload))

        cfg = WakeWordConfig(phrases=["hey agent"], threshold=0.5)
        det = WakeWordDetector(config=cfg, publish_fn=mock_pub)
        det._model = MagicMock()
        det._model.predict.return_value = {"hey agent": 0.95}

        results = await det.process_and_publish("mic", b"\x00" * 3200)
        assert len(results) == 1

        # Two messages published: WakeWordEvent + AttentionTrigger
        assert len(published) == 2

        ww_topic, ww_data = published[0]
        assert ww_topic == "agent/sensory/audio/mic"
        ww = WakeWordEvent()
        ww.ParseFromString(ww_data)
        assert ww.keyword == "hey agent"
        assert ww.score == pytest.approx(0.95)

        attn_topic, attn_data = published[1]
        assert attn_topic == "agent/reflex/attention/trigger"
        attn = AttentionTrigger()
        attn.ParseFromString(attn_data)
        assert "hey agent" in attn.region_description

    async def test_no_publish_without_detection(self) -> None:
        published: list[tuple[str, bytes]] = []

        async def mock_pub(topic: str, payload: bytes) -> None:
            published.append((topic, payload))

        cfg = WakeWordConfig(phrases=["hey agent"], threshold=0.9)
        det = WakeWordDetector(config=cfg, publish_fn=mock_pub)
        det._model = MagicMock()
        det._model.predict.return_value = {"hey agent": 0.1}

        await det.process_and_publish("mic", b"\x00" * 3200)
        assert len(published) == 0

    async def test_no_publish_fn(self) -> None:
        det = WakeWordDetector()
        det._model = MagicMock()
        det._model.predict.return_value = {"hey agent": 0.9}

        # Should not raise
        results = await det.process_and_publish("mic", b"\x00" * 3200)
        assert len(results) == 1
