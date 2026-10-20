"""Audio sensory reflex handler.

Processes :class:`TranscriptionEvent` and :class:`WakeWordEvent` from
the sensory dispatcher and decides on reflex actions:

- **Wake word**: activates the FSM (IDLE → ACTIVE) and optionally
  escalates to System 2 for command processing.
- **High-confidence transcription**: routes to System 2 for NLU.
"""

from __future__ import annotations

import logging
from typing import Any

from openbad.nervous_system.schemas import TranscriptionEvent, WakeWordEvent

logger = logging.getLogger(__name__)

# Minimum confidence to escalate a transcription
DEFAULT_ESCALATION_CONFIDENCE = 0.7


class AudioReflexHandler:
    """Handle audio events from the sensory dispatcher.

    Parameters
    ----------
    fsm : object | None
        Optional :class:`AgentFSM` — fires ``activate`` on wake word.
    escalation_gw : object | None
        Optional :class:`EscalationGateway` for System 1→2 escalation.
    escalation_confidence : float
        Minimum confidence to escalate transcription events.
    """

    def __init__(
        self,
        fsm: Any | None = None,
        escalation_gw: Any | None = None,
        escalation_confidence: float = DEFAULT_ESCALATION_CONFIDENCE,
    ) -> None:
        self._fsm = fsm
        self._escalation = escalation_gw
        self._escalation_confidence = escalation_confidence
        self._wake_count: int = 0
        self._transcription_count: int = 0
        self._escalation_count: int = 0

    @property
    def wake_count(self) -> int:
        return self._wake_count

    @property
    def transcription_count(self) -> int:
        return self._transcription_count

    @property
    def escalation_count(self) -> int:
        return self._escalation_count

    def handle_wake_word(self, event: WakeWordEvent) -> bool:
        """Process a wake-word detection.

        Activates the FSM and escalates to System 2.
        """
        self._wake_count += 1

        # Activate the FSM
        if self._fsm is not None:
            try:
                self._fsm.fire("activate")
            except Exception:
                logger.debug("FSM activate failed (may already be ACTIVE)")

        # Escalate for command processing
        if self._escalation is not None:
            self._escalation_count += 1
            self._escalation.escalate(
                event_topic="agent/sensory/audio/wake_word",
                event_payload=event.SerializeToString(),
                reason=f"Wake word detected: {event.keyword}",
                reflex_id="sensory/audio",
            )

        return True

    def handle_transcription(self, event: TranscriptionEvent) -> bool:
        """Process a transcription event.

        High-confidence transcriptions are escalated to System 2 for
        natural language understanding.
        """
        self._transcription_count += 1

        if event.confidence >= self._escalation_confidence:
            self._escalation_count += 1
            if self._escalation is not None:
                self._escalation.escalate(
                    event_topic="agent/sensory/audio/transcription",
                    event_payload=event.SerializeToString(),
                    reason=f"Transcription: '{event.text[:50]}'",
                    reflex_id="sensory/audio",
                )

        return True
