"""Tests for sensory → reflex arc dispatcher — Issue #54."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from openbad.nervous_system.schemas import (
    AttentionTrigger,
    Header,
    TranscriptionEvent,
    WakeWordEvent,
)
from openbad.sensory.dispatcher import (
    DispatcherConfig,
    SensoryDispatcher,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_attention_payload(ssim_delta: float = 0.6) -> bytes:
    return AttentionTrigger(
        header=Header(timestamp_unix=time.time(), source_module="test"),
        source_id="screen0",
        ssim_delta=ssim_delta,
        region_description="test region",
    ).SerializeToString()


def _make_transcription_payload(
    text: str = "hello world", confidence: float = 0.9,
) -> bytes:
    return TranscriptionEvent(
        header=Header(timestamp_unix=time.time(), source_module="test"),
        source_id="mic",
        text=text,
        confidence=confidence,
        is_final=True,
    ).SerializeToString()


def _make_wake_word_payload(keyword: str = "hey agent") -> bytes:
    return WakeWordEvent(
        header=Header(timestamp_unix=time.time(), source_module="test"),
        keyword=keyword,
        score=0.95,
    ).SerializeToString()


def _mock_fsm(state: str = "ACTIVE") -> SimpleNamespace:
    return SimpleNamespace(state=state)


# ---------------------------------------------------------------------------
# DispatcherConfig
# ---------------------------------------------------------------------------


class TestDispatcherConfig:
    def test_defaults(self) -> None:
        cfg = DispatcherConfig()
        assert cfg.audio_noise_floor == 0.3
        assert cfg.vision_change_rate_max == 30

    def test_custom(self) -> None:
        cfg = DispatcherConfig(audio_noise_floor=0.5, vision_change_rate_max=10)
        assert cfg.audio_noise_floor == 0.5


# ---------------------------------------------------------------------------
# SensoryDispatcher — basic routing
# ---------------------------------------------------------------------------


class TestDispatcherRouting:
    def test_visual_trigger(self) -> None:
        d = SensoryDispatcher()
        result = d.dispatch("agent/reflex/attention/trigger", _make_attention_payload())
        assert result is True
        assert d.stats.vision_events == 1
        assert d.stats.total_dispatched == 1

    def test_audio_transcription(self) -> None:
        d = SensoryDispatcher()
        result = d.dispatch("agent/sensory/audio/mic", _make_transcription_payload())
        assert result is True
        assert d.stats.audio_events == 1

    def test_tts_ignored(self) -> None:
        d = SensoryDispatcher()
        result = d.dispatch("agent/sensory/audio/tts/complete", b"\x00")
        assert result is False

    def test_unknown_topic(self) -> None:
        d = SensoryDispatcher()
        result = d.dispatch("agent/sensory/video/unknown", b"\x00")
        assert result is False


# ---------------------------------------------------------------------------
# FSM suppression
# ---------------------------------------------------------------------------


class TestDispatcherSuppression:
    def test_throttled_suppresses(self) -> None:
        fsm = _mock_fsm("THROTTLED")
        d = SensoryDispatcher(fsm=fsm)
        result = d.dispatch("agent/reflex/attention/trigger", _make_attention_payload())
        assert result is False
        assert d.stats.total_suppressed == 1

    def test_emergency_suppresses(self) -> None:
        fsm = _mock_fsm("EMERGENCY")
        d = SensoryDispatcher(fsm=fsm)
        result = d.dispatch("agent/sensory/audio/mic", _make_transcription_payload())
        assert result is False

    def test_active_allows(self) -> None:
        fsm = _mock_fsm("ACTIVE")
        d = SensoryDispatcher(fsm=fsm)
        result = d.dispatch("agent/reflex/attention/trigger", _make_attention_payload())
        assert result is True

    def test_idle_allows(self) -> None:
        fsm = _mock_fsm("IDLE")
        d = SensoryDispatcher(fsm=fsm)
        result = d.dispatch("agent/reflex/attention/trigger", _make_attention_payload())
        assert result is True

    def test_no_fsm_allows(self) -> None:
        d = SensoryDispatcher(fsm=None)
        result = d.dispatch("agent/reflex/attention/trigger", _make_attention_payload())
        assert result is True


# ---------------------------------------------------------------------------
# Confidence gating
# ---------------------------------------------------------------------------


class TestDispatcherConfidenceGating:
    def test_low_confidence_filtered(self) -> None:
        d = SensoryDispatcher(config=DispatcherConfig(audio_noise_floor=0.5))
        payload = _make_transcription_payload(confidence=0.2)
        result = d.dispatch("agent/sensory/audio/mic", payload)
        assert result is False
        assert d.stats.below_threshold == 1

    def test_at_threshold_passes(self) -> None:
        d = SensoryDispatcher(config=DispatcherConfig(audio_noise_floor=0.5))
        payload = _make_transcription_payload(confidence=0.5)
        result = d.dispatch("agent/sensory/audio/mic", payload)
        assert result is True


# ---------------------------------------------------------------------------
# Vision rate limiting
# ---------------------------------------------------------------------------


class TestDispatcherVisionRateLimit:
    def test_rate_limited(self) -> None:
        cfg = DispatcherConfig(vision_change_rate_max=3)
        d = SensoryDispatcher(config=cfg)
        payload = _make_attention_payload()

        # First 3 should pass
        for _ in range(3):
            assert d.dispatch("agent/reflex/attention/trigger", payload) is True

        # 4th should be rate limited
        assert d.dispatch("agent/reflex/attention/trigger", payload) is False
        assert d.stats.rate_limited == 1


# ---------------------------------------------------------------------------
# Custom handlers
# ---------------------------------------------------------------------------


class TestDispatcherHandlers:
    def test_visual_handler_called(self) -> None:
        handler = MagicMock(return_value=True)
        d = SensoryDispatcher(visual_handler=handler)
        d.dispatch("agent/reflex/attention/trigger", _make_attention_payload())
        handler.assert_called_once()
        assert d.stats.total_dispatched == 1

    def test_audio_handler_called(self) -> None:
        handler = MagicMock(return_value=True)
        d = SensoryDispatcher(audio_handler=handler)
        d.dispatch("agent/sensory/audio/mic", _make_transcription_payload())
        handler.assert_called_once()

    def test_wake_word_handler_called(self) -> None:
        handler = MagicMock(return_value=True)
        d = SensoryDispatcher(wake_word_handler=handler)
        d.dispatch("agent/sensory/audio/mic", _make_wake_word_payload())
        handler.assert_called_once()
        assert d.stats.wake_word_events == 1

    def test_handler_returning_false(self) -> None:
        handler = MagicMock(return_value=False)
        d = SensoryDispatcher(visual_handler=handler)
        result = d.dispatch("agent/reflex/attention/trigger", _make_attention_payload())
        assert result is False
        assert d.stats.total_dispatched == 0


# ---------------------------------------------------------------------------
# Publish result
# ---------------------------------------------------------------------------


class TestDispatcherPublish:
    def test_publishes_result_on_visual(self) -> None:
        published: list[tuple[str, bytes]] = []

        def pub(topic: str, data: bytes) -> None:
            published.append((topic, data))

        handler = MagicMock(return_value=True)
        d = SensoryDispatcher(publish_fn=pub, visual_handler=handler)
        d.dispatch("agent/reflex/attention/trigger", _make_attention_payload())

        assert len(published) == 1
        assert published[0][0] == "agent/reflex/sensory/visual/result"


# ---------------------------------------------------------------------------
# Subscribe
# ---------------------------------------------------------------------------


class TestDispatcherSubscribe:
    def test_subscribe(self) -> None:
        client = MagicMock()
        d = SensoryDispatcher()
        d.subscribe(client)
        client.subscribe.assert_called_once()
        args = client.subscribe.call_args[0]
        assert args[0] == "agent/sensory/#"
        assert callable(args[1])
