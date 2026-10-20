"""Wake-word / activation phrase detector.

Runs a lightweight keyword-spotting model (openWakeWord) ahead of full
ASR to detect configurable activation phrases.  When a wake word is
detected the module:

1. Publishes a ``WakeWordEvent`` protobuf on ``agent/sensory/audio/{source_id}``.
2. Publishes an ``AttentionTrigger`` on ``agent/reflex/attention/trigger``
   so the reflex arc can switch from Vosk ambient mode to Whisper high-
   accuracy mode.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from openbad.nervous_system.schemas import AttentionTrigger, Header, WakeWordEvent
from openbad.nervous_system.topics import (
    SENSORY_ATTENTION_TRIGGER,
    SENSORY_AUDIO,
    topic_for,
)
from openbad.sensory.audio.config import WakeWordConfig

logger = logging.getLogger(__name__)


@dataclass
class Detection:
    """A single wake-word detection event.

    Attributes
    ----------
    phrase : str
        The detected activation phrase.
    confidence : float
        Detection confidence 0.0–1.0.
    timestamp : float
        Unix timestamp of the detection.
    """

    phrase: str
    confidence: float
    timestamp: float = field(default_factory=time.time)


class WakeWordDetector:
    """Lightweight keyword-spotting detector using openWakeWord.

    Parameters
    ----------
    config : WakeWordConfig | None
        Phrases and threshold.  Uses defaults if omitted.
    publish_fn : callable | None
        Optional async callback ``(topic, payload) -> None``.
    """

    def __init__(
        self,
        config: WakeWordConfig | None = None,
        publish_fn: Any | None = None,
    ) -> None:
        self._config = config or WakeWordConfig()
        self._publish = publish_fn
        self._model: Any | None = None
        self._detection_count: int = 0

    @property
    def config(self) -> WakeWordConfig:
        return self._config

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def detection_count(self) -> int:
        return self._detection_count

    def load_model(self) -> None:
        """Load the openWakeWord model.

        Raises RuntimeError if openwakeword is not installed.
        """
        try:
            import openwakeword  # type: ignore[import-untyped]
        except ImportError:
            msg = (
                "openwakeword is required for wake-word detection. "
                "Install with: pip install openwakeword"
            )
            raise RuntimeError(msg) from None

        self._model = openwakeword.Model()

    def process_audio(self, pcm_data: bytes) -> list[Detection]:
        """Feed PCM audio and return any detected wake words.

        Parameters
        ----------
        pcm_data : bytes
            Raw 16-bit signed LE mono PCM audio at 16kHz.

        Returns a list of :class:`Detection` objects for phrases that
        exceeded the confidence threshold.
        """
        if self._model is None:
            msg = "Model not loaded — call load_model() first"
            raise RuntimeError(msg)

        prediction = self._model.predict(pcm_data)

        detections: list[Detection] = []
        for phrase in self._config.phrases:
            # openWakeWord returns a dict keyed by model name
            score = prediction.get(phrase, 0.0)
            if score >= self._config.threshold:
                detections.append(Detection(
                    phrase=phrase,
                    confidence=float(score),
                ))
                self._detection_count += 1

        return detections

    async def process_and_publish(
        self,
        source_id: str,
        pcm_data: bytes,
    ) -> list[Detection]:
        """Process audio, publish events for any detections.

        Publishes both a ``WakeWordEvent`` on the audio channel and an
        ``AttentionTrigger`` on the reflex channel for each detection.
        """
        detections = self.process_audio(pcm_data)

        for det in detections:
            ww_proto = WakeWordEvent(
                header=Header(
                    timestamp_unix=det.timestamp,
                    source_module="sensory.audio.wake_word",
                    schema_version=1,
                ),
                keyword=det.phrase,
                score=det.confidence,
            )

            attn_proto = AttentionTrigger(
                header=Header(
                    timestamp_unix=det.timestamp,
                    source_module="sensory.audio.wake_word",
                    schema_version=1,
                ),
                source_id=source_id,
                ssim_delta=0.0,
                region_description=f"Wake word detected: {det.phrase}",
            )

            if self._publish is not None:
                audio_topic = topic_for(SENSORY_AUDIO, source_id=source_id)
                await self._publish(audio_topic, ww_proto.SerializeToString())
                await self._publish(
                    SENSORY_ATTENTION_TRIGGER,
                    attn_proto.SerializeToString(),
                )

        return detections
