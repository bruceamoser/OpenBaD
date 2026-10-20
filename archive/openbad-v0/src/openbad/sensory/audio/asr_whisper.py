"""Whisper high-accuracy automatic speech recognition.

Provides on-demand, high-fidelity transcription using OpenAI Whisper
via the ``faster-whisper`` CTranslate2 backend.  Triggered by the reflex
arc when the wake-word detector or attention filter escalates from Vosk
ambient mode.

Transcription events are published as ``TranscriptionEvent`` protobuf
messages on ``agent/sensory/audio/{source_id}``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from openbad.nervous_system.schemas import Header, TranscriptionEvent
from openbad.nervous_system.topics import SENSORY_AUDIO, topic_for

logger = logging.getLogger(__name__)


@dataclass
class WhisperSegment:
    """A single transcribed segment from Whisper.

    Attributes
    ----------
    text : str
        Transcribed text.
    start : float
        Start time in seconds within the audio.
    end : float
        End time in seconds within the audio.
    avg_logprob : float
        Average log-probability (lower = less confident).
    no_speech_prob : float
        Probability that this segment is silence/noise.
    """

    text: str
    start: float = 0.0
    end: float = 0.0
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0

    @property
    def confidence(self) -> float:
        """Rough confidence estimate derived from avg_logprob.

        Maps log-probabilities to 0.0–1.0 range.  Values below -1.0
        are treated as very low confidence.
        """
        import math

        return max(0.0, min(1.0, math.exp(self.avg_logprob)))


@dataclass
class WhisperResult:
    """Complete Whisper transcription result for an audio buffer.

    Attributes
    ----------
    segments : list[WhisperSegment]
        Ordered list of transcribed segments.
    language : str
        Detected or specified language code.
    language_probability : float
        Confidence of the language detection.
    duration_s : float
        Total audio duration in seconds.
    processing_ms : float
        Wall-clock processing time in milliseconds.
    """

    segments: list[WhisperSegment] = field(default_factory=list)
    language: str = "en"
    language_probability: float = 0.0
    duration_s: float = 0.0
    processing_ms: float = 0.0

    @property
    def full_text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text.strip())

    @property
    def avg_confidence(self) -> float:
        if not self.segments:
            return 0.0
        return sum(s.confidence for s in self.segments) / len(self.segments)

    def to_proto(self, source_id: str = "") -> TranscriptionEvent:
        return TranscriptionEvent(
            header=Header(
                timestamp_unix=time.time(),
                source_module="sensory.audio.asr_whisper",
                schema_version=1,
            ),
            source_id=source_id,
            text=self.full_text,
            confidence=self.avg_confidence,
            is_final=True,
        )


class WhisperTranscriber:
    """On-demand high-accuracy transcription using faster-whisper.

    Parameters
    ----------
    model_size : str
        Whisper model: ``"tiny"``, ``"base"``, ``"small"``, ``"medium"``,
        ``"large-v3"`` (default ``"base"``).
    device : str
        Compute device: ``"cpu"`` or ``"cuda"`` (default ``"cpu"``).
    compute_type : str
        CTranslate2 compute type (default ``"int8"``).
    publish_fn : callable | None
        Optional async callback ``(topic, payload) -> None``.
    """

    VALID_MODELS = ("tiny", "base", "small", "medium", "large-v3")

    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
        publish_fn: Any | None = None,
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._publish = publish_fn
        self._model: Any | None = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def model_size(self) -> str:
        return self._model_size

    def load_model(self) -> None:
        """Load the faster-whisper model.

        Raises RuntimeError if faster-whisper is not installed.
        """
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-untyped]
        except ImportError:
            msg = (
                "faster-whisper is required for Whisper transcription. "
                "Install with: pip install faster-whisper"
            )
            raise RuntimeError(msg) from None

        if self._model_size not in self.VALID_MODELS:
            msg = (
                f"Invalid model size '{self._model_size}'. "
                f"Valid options: {self.VALID_MODELS}"
            )
            raise ValueError(msg)

        self._model = WhisperModel(
            self._model_size,
            device=self._device,
            compute_type=self._compute_type,
        )

    def transcribe(
        self,
        audio: Any,
        language: str | None = None,
    ) -> WhisperResult:
        """Transcribe audio data.

        Parameters
        ----------
        audio : str | numpy.ndarray | bytes
            Audio input — a file path, numpy array (float32, mono, 16kHz),
            or raw bytes.
        language : str | None
            Force language code (e.g. ``"en"``).  ``None`` for auto-detect.

        Returns :class:`WhisperResult` with all segments.
        """
        if self._model is None:
            msg = "Model not loaded — call load_model() first"
            raise RuntimeError(msg)

        start = time.perf_counter()

        kwargs: dict[str, Any] = {}
        if language is not None:
            kwargs["language"] = language

        segments_gen, info = self._model.transcribe(audio, **kwargs)

        segments: list[WhisperSegment] = []
        for seg in segments_gen:
            segments.append(WhisperSegment(
                text=seg.text,
                start=seg.start,
                end=seg.end,
                avg_logprob=seg.avg_logprob,
                no_speech_prob=seg.no_speech_prob,
            ))

        elapsed_ms = (time.perf_counter() - start) * 1000

        return WhisperResult(
            segments=segments,
            language=info.language,
            language_probability=info.language_probability,
            duration_s=info.duration,
            processing_ms=elapsed_ms,
        )

    async def transcribe_and_publish(
        self,
        source_id: str,
        audio: Any,
        language: str | None = None,
    ) -> WhisperResult:
        """Transcribe audio and optionally publish the result."""
        result = self.transcribe(audio, language=language)

        if self._publish is not None and result.full_text:
            proto = result.to_proto(source_id=source_id)
            topic = topic_for(SENSORY_AUDIO, source_id=source_id)
            await self._publish(topic, proto.SerializeToString())

        return result
