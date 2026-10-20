"""Sensory → reflex arc dispatcher.

Subscribes to **all** sensory events (``agent/sensory/#``) and routes
them to the appropriate reflex handlers.  The dispatcher respects the
FSM state — during THROTTLED or EMERGENCY, non-critical sensory events
are suppressed.

The dispatcher sits between the sensory layer and the reflex arc,
acting as a bridge that transforms raw perception events into reflex
triggers.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from openbad.nervous_system.schemas import (
    AttentionTrigger,
    Header,
    TranscriptionEvent,
    WakeWordEvent,
)
from openbad.nervous_system.schemas.reflex_pb2 import ReflexResult
from openbad.nervous_system.topics import (
    SENSORY_ATTENTION_TRIGGER,
)

logger = logging.getLogger(__name__)

# Topics this dispatcher subscribes to
SENSORY_WILDCARD = "agent/sensory/#"

# FSM states where sensory events are suppressed
_SUPPRESSED_STATES = frozenset({"THROTTLED", "EMERGENCY"})

# Minimum confidence for transcription events to be routed
DEFAULT_AUDIO_NOISE_FLOOR = 0.3

# Maximum visual change events per minute before throttling
DEFAULT_VISION_CHANGE_RATE_MAX = 30


@dataclass
class DispatcherConfig:
    """Configuration for the sensory dispatcher.

    Attributes
    ----------
    audio_noise_floor : float
        Minimum transcription confidence to route.
    vision_change_rate_max : int
        Max visual attention events per minute.
    """

    audio_noise_floor: float = DEFAULT_AUDIO_NOISE_FLOOR
    vision_change_rate_max: int = DEFAULT_VISION_CHANGE_RATE_MAX


@dataclass
class DispatchStats:
    """Tracked statistics for the dispatcher."""

    total_received: int = 0
    total_dispatched: int = 0
    total_suppressed: int = 0
    vision_events: int = 0
    audio_events: int = 0
    wake_word_events: int = 0
    below_threshold: int = 0
    rate_limited: int = 0


class SensoryDispatcher:
    """Routes sensory events to reflex arc handlers.

    Parameters
    ----------
    fsm : object | None
        Optional :class:`AgentFSM` — checked for suppression state.
    publish_fn : callable | None
        Optional ``(topic, data) -> None``.
    config : DispatcherConfig | None
        Thresholds.  Defaults used if omitted.
    visual_handler : callable | None
        ``(AttentionTrigger) -> bool`` handler for vision events.
    audio_handler : callable | None
        ``(TranscriptionEvent) -> bool`` handler for audio events.
    wake_word_handler : callable | None
        ``(WakeWordEvent) -> bool`` handler for wake-word events.
    """

    def __init__(
        self,
        fsm: Any | None = None,
        publish_fn: Any | None = None,
        config: DispatcherConfig | None = None,
        visual_handler: Any | None = None,
        audio_handler: Any | None = None,
        wake_word_handler: Any | None = None,
    ) -> None:
        self._fsm = fsm
        self._publish = publish_fn
        self._config = config or DispatcherConfig()
        self._visual_handler = visual_handler
        self._audio_handler = audio_handler
        self._wake_word_handler = wake_word_handler
        self._stats = DispatchStats()
        self._vision_timestamps: list[float] = []

    @property
    def stats(self) -> DispatchStats:
        return self._stats

    def _is_suppressed(self) -> bool:
        """Check if the FSM is in a state that suppresses sensory events."""
        if self._fsm is None:
            return False
        state = getattr(self._fsm, "state", "IDLE")
        return str(state).upper() in _SUPPRESSED_STATES

    def _is_vision_rate_limited(self) -> bool:
        """Check if visual events exceed the per-minute rate limit."""
        now = time.monotonic()
        # Prune timestamps older than 60s
        self._vision_timestamps = [
            t for t in self._vision_timestamps if now - t < 60
        ]
        return len(self._vision_timestamps) >= self._config.vision_change_rate_max

    def dispatch(self, topic: str, payload: bytes) -> bool:
        """Route a sensory event to the appropriate handler.

        Returns True if the event was dispatched, False if suppressed
        or filtered.
        """
        self._stats.total_received += 1

        # Suppress in restricted FSM states
        if self._is_suppressed():
            self._stats.total_suppressed += 1
            return False

        # Route by topic
        if topic == SENSORY_ATTENTION_TRIGGER:
            return self._dispatch_visual(payload)

        if topic.startswith("agent/sensory/audio/") and "tts" not in topic:
            return self._dispatch_audio(payload)

        # TTS complete and other topics — no routing needed
        return False

    def _dispatch_visual(self, payload: bytes) -> bool:
        """Route a visual attention trigger."""
        # Rate limit check
        if self._is_vision_rate_limited():
            self._stats.rate_limited += 1
            return False

        try:
            trigger = AttentionTrigger()
            trigger.ParseFromString(payload)
        except Exception:
            logger.warning("Failed to parse AttentionTrigger")
            return False

        self._vision_timestamps.append(time.monotonic())
        self._stats.vision_events += 1

        if self._visual_handler is not None:
            handled = self._visual_handler(trigger)
            if handled:
                self._stats.total_dispatched += 1
                self._publish_result(
                    "sensory/visual",
                    handled=True,
                    action="visual_attention_routed",
                )
            return handled

        self._stats.total_dispatched += 1
        return True

    def _dispatch_audio(self, payload: bytes) -> bool:
        """Route an audio event (transcription or wake-word)."""
        # Try wake word first — discriminate by score > 0
        try:
            ww = WakeWordEvent()
            ww.ParseFromString(payload)
            if ww.keyword and ww.score > 0:
                self._stats.wake_word_events += 1
                if self._wake_word_handler is not None:
                    handled = self._wake_word_handler(ww)
                    if handled:
                        self._stats.total_dispatched += 1
                        self._publish_result(
                            "sensory/audio",
                            handled=True,
                            action=f"wake_word_detected: {ww.keyword}",
                        )
                    return handled
                self._stats.total_dispatched += 1
                return True
        except Exception:
            logger.debug("Payload is not a WakeWordEvent, trying TranscriptionEvent")

        # Try transcription event
        try:
            te = TranscriptionEvent()
            te.ParseFromString(payload)
            if te.text:
                self._stats.audio_events += 1

                # Confidence gate
                if te.confidence < self._config.audio_noise_floor:
                    self._stats.below_threshold += 1
                    return False

                if self._audio_handler is not None:
                    handled = self._audio_handler(te)
                    if handled:
                        self._stats.total_dispatched += 1
                        self._publish_result(
                            "sensory/audio",
                            handled=True,
                            action="transcription_routed",
                        )
                    return handled
                self._stats.total_dispatched += 1
                return True
        except Exception:
            logger.debug("Failed to parse audio payload as TranscriptionEvent")

        return False

    def _publish_result(
        self,
        reflex_id: str,
        *,
        handled: bool,
        action: str,
    ) -> None:
        if self._publish is None:
            return

        result = ReflexResult(
            header=Header(
                timestamp_unix=time.time(),
                source_module="sensory.dispatcher",
                schema_version=1,
            ),
            reflex_id=reflex_id,
            handled=handled,
            action_taken=action,
        )
        topic = f"agent/reflex/{reflex_id}/result"
        self._publish(topic, result.SerializeToString())

    def subscribe(self, client: Any) -> None:
        """Subscribe to all sensory topics on the given MQTT client."""

        def _on_message(topic: str, payload: bytes) -> None:
            self.dispatch(topic, payload)

        client.subscribe(SENSORY_WILDCARD, _on_message)
