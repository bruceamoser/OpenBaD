"""Tests for visual and audio sensory reflex handlers — Issue #54."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from openbad.nervous_system.schemas import (
    AttentionTrigger,
    Header,
    TranscriptionEvent,
    WakeWordEvent,
)
from openbad.reflex_arc.handlers.sensory_audio import AudioReflexHandler
from openbad.reflex_arc.handlers.sensory_visual import VisualReflexHandler

# ---------------------------------------------------------------------------
# VisualReflexHandler
# ---------------------------------------------------------------------------


class TestVisualReflexHandler:
    def _trigger(self, ssim_delta: float = 0.6) -> AttentionTrigger:
        return AttentionTrigger(
            header=Header(timestamp_unix=time.time(), source_module="test"),
            source_id="screen0",
            ssim_delta=ssim_delta,
        )

    def test_handle_always_returns_true(self) -> None:
        h = VisualReflexHandler()
        assert h.handle(self._trigger(0.1)) is True
        assert h.trigger_count == 1

    def test_no_escalation_below_threshold(self) -> None:
        esc = MagicMock()
        h = VisualReflexHandler(escalation_gw=esc, critical_delta=0.5)
        h.handle(self._trigger(0.3))
        esc.escalate.assert_not_called()
        assert h.escalation_count == 0

    def test_escalation_above_threshold(self) -> None:
        esc = MagicMock()
        h = VisualReflexHandler(escalation_gw=esc, critical_delta=0.5)
        h.handle(self._trigger(0.7))
        esc.escalate.assert_called_once()
        assert h.escalation_count == 1

    def test_escalation_at_threshold(self) -> None:
        esc = MagicMock()
        h = VisualReflexHandler(escalation_gw=esc, critical_delta=0.5)
        h.handle(self._trigger(0.5))
        esc.escalate.assert_called_once()

    def test_no_escalation_gw(self) -> None:
        h = VisualReflexHandler(escalation_gw=None, critical_delta=0.5)
        # Should not raise even with high delta
        assert h.handle(self._trigger(0.9)) is True
        assert h.escalation_count == 1

    def test_count_accumulates(self) -> None:
        h = VisualReflexHandler()
        h.handle(self._trigger(0.1))
        h.handle(self._trigger(0.2))
        assert h.trigger_count == 2


# ---------------------------------------------------------------------------
# AudioReflexHandler
# ---------------------------------------------------------------------------


class TestAudioReflexHandler:
    def _wake_word(self, keyword: str = "hey agent") -> WakeWordEvent:
        return WakeWordEvent(
            header=Header(timestamp_unix=time.time(), source_module="test"),
            keyword=keyword,
            score=0.95,
        )

    def _transcription(
        self, text: str = "open the file", confidence: float = 0.9,
    ) -> TranscriptionEvent:
        return TranscriptionEvent(
            header=Header(timestamp_unix=time.time(), source_module="test"),
            source_id="mic",
            text=text,
            confidence=confidence,
            is_final=True,
        )

    def test_wake_word_activates_fsm(self) -> None:
        fsm = MagicMock()
        h = AudioReflexHandler(fsm=fsm)
        assert h.handle_wake_word(self._wake_word()) is True
        fsm.fire.assert_called_once_with("activate")
        assert h.wake_count == 1

    def test_wake_word_escalates(self) -> None:
        esc = MagicMock()
        h = AudioReflexHandler(escalation_gw=esc)
        h.handle_wake_word(self._wake_word())
        esc.escalate.assert_called_once()
        assert h.escalation_count == 1

    def test_wake_word_fsm_error_tolerated(self) -> None:
        fsm = MagicMock()
        fsm.fire.side_effect = RuntimeError("already active")
        h = AudioReflexHandler(fsm=fsm)
        assert h.handle_wake_word(self._wake_word()) is True

    def test_transcription_high_conf_escalates(self) -> None:
        esc = MagicMock()
        h = AudioReflexHandler(escalation_gw=esc, escalation_confidence=0.7)
        h.handle_transcription(self._transcription(confidence=0.9))
        esc.escalate.assert_called_once()
        assert h.escalation_count == 1
        assert h.transcription_count == 1

    def test_transcription_low_conf_no_escalation(self) -> None:
        esc = MagicMock()
        h = AudioReflexHandler(escalation_gw=esc, escalation_confidence=0.7)
        h.handle_transcription(self._transcription(confidence=0.5))
        esc.escalate.assert_not_called()
        assert h.transcription_count == 1
        assert h.escalation_count == 0

    def test_no_esc_gw(self) -> None:
        h = AudioReflexHandler(escalation_gw=None)
        assert h.handle_transcription(self._transcription(confidence=0.9)) is True

    def test_counts_accumulate(self) -> None:
        h = AudioReflexHandler()
        h.handle_wake_word(self._wake_word())
        h.handle_wake_word(self._wake_word())
        h.handle_transcription(self._transcription())
        assert h.wake_count == 2
        assert h.transcription_count == 1
