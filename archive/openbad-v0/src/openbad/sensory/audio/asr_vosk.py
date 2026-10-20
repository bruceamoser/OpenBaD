"""Vosk streaming automatic speech recognition.

Provides always-on ambient speech recognition using the Vosk library.
Audio chunks from PipeWire are fed into the recogniser, producing partial
and final transcription results with confidence scores.

Transcription events are published as protobuf messages on
``agent/sensory/audio/{source_id}``.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from openbad.nervous_system.schemas import Header, TranscriptionEvent
from openbad.nervous_system.topics import SENSORY_AUDIO, topic_for

logger = logging.getLogger(__name__)


@dataclass
class TranscriptionResult:
    """A single transcription result from the Vosk recogniser.

    Attributes
    ----------
    text : str
        Recognised text.
    confidence : float
        Average confidence score 0.0–1.0.
    is_final : bool
        True for final results, False for partial/interim results.
    source_id : str
        Audio source identifier.
    timestamp : float
        Unix timestamp of the result.
    """

    text: str
    confidence: float
    is_final: bool = True
    source_id: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_proto(self) -> TranscriptionEvent:
        return TranscriptionEvent(
            header=Header(
                timestamp_unix=self.timestamp,
                source_module="sensory.audio.asr_vosk",
                schema_version=1,
            ),
            source_id=self.source_id,
            text=self.text,
            confidence=self.confidence,
            is_final=self.is_final,
        )


class VoskRecogniser:
    """Streaming speech recogniser backed by Vosk.

    Parameters
    ----------
    model_path : str
        Path to the Vosk language model directory.
    sample_rate : int
        Audio sample rate in Hz (must match the model, typically 16000).
    publish_fn : callable | None
        Optional async callback ``(topic, payload) -> None``.
    """

    def __init__(
        self,
        model_path: str = "",
        sample_rate: int = 16000,
        publish_fn: Any | None = None,
    ) -> None:
        self._model_path = model_path
        self._sample_rate = sample_rate
        self._publish = publish_fn
        self._model: Any | None = None
        self._recogniser: Any | None = None

    @property
    def is_loaded(self) -> bool:
        return self._recogniser is not None

    def load_model(self) -> None:
        """Load the Vosk model and create a recogniser.

        Raises RuntimeError if vosk is not installed.
        """
        try:
            import vosk  # type: ignore[import-untyped]
        except ImportError:
            msg = (
                "vosk is required for Vosk ASR. "
                "Install with: pip install vosk"
            )
            raise RuntimeError(msg) from None

        if not self._model_path:
            msg = "Vosk model path must be specified"
            raise ValueError(msg)

        vosk.SetLogLevel(-1)
        self._model = vosk.Model(self._model_path)
        self._recogniser = vosk.KaldiRecognizer(self._model, self._sample_rate)

    def accept_waveform(self, pcm_data: bytes) -> TranscriptionResult | None:
        """Feed PCM audio data and return a result if speech is detected.

        Returns a ``TranscriptionResult`` when Vosk produces a final or
        partial result with non-empty text.  Returns ``None`` if no
        speech is detected in this chunk.
        """
        if self._recogniser is None:
            msg = "Model not loaded — call load_model() first"
            raise RuntimeError(msg)

        if self._recogniser.AcceptWaveform(pcm_data):
            raw = json.loads(self._recogniser.Result())
            text = raw.get("text", "").strip()
            if text:
                return TranscriptionResult(
                    text=text,
                    confidence=self._extract_confidence(raw),
                    is_final=True,
                )
        else:
            raw = json.loads(self._recogniser.PartialResult())
            text = raw.get("partial", "").strip()
            if text:
                return TranscriptionResult(
                    text=text,
                    confidence=0.0,
                    is_final=False,
                )

        return None

    def final_result(self) -> TranscriptionResult | None:
        """Flush the recogniser and return any remaining text."""
        if self._recogniser is None:
            return None

        raw = json.loads(self._recogniser.FinalResult())
        text = raw.get("text", "").strip()
        if text:
            return TranscriptionResult(
                text=text,
                confidence=self._extract_confidence(raw),
                is_final=True,
            )
        return None

    async def process_and_publish(
        self,
        source_id: str,
        pcm_data: bytes,
    ) -> TranscriptionResult | None:
        """Process audio and optionally publish the result."""
        result = self.accept_waveform(pcm_data)

        if result is not None:
            result.source_id = source_id
            if self._publish is not None:
                proto = result.to_proto()
                topic = topic_for(SENSORY_AUDIO, source_id=source_id)
                await self._publish(topic, proto.SerializeToString())

        return result

    @staticmethod
    def _extract_confidence(raw: dict[str, Any]) -> float:
        """Extract average confidence from Vosk result dict.

        Vosk includes per-word confidence in the ``result`` array when
        the model supports it.  Falls back to 0.0 if unavailable.
        """
        words = raw.get("result", [])
        if not words:
            return 0.0
        total = sum(w.get("conf", 0.0) for w in words)
        return total / len(words)
