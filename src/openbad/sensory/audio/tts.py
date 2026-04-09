"""Text-to-Speech output using Piper TTS.

Converts text or SSML markup into WAV audio via the ``piper-tts`` engine
(MIT-licensed, runs fully offline).  Synthesised audio is written to
PipeWire for playback and a ``TTSComplete`` event is published on the
event bus when finished.

Protobuf messages consumed:
    ``TTSRequest`` on ``agent/sensory/audio/tts/request``
Protobuf messages produced:
    ``TTSComplete`` on ``agent/sensory/audio/tts/complete``
"""

from __future__ import annotations

import io
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from openbad.nervous_system.schemas import (
    Header,
    TTSComplete,
    TTSRequest,
)
from openbad.nervous_system.topics import SENSORY_AUDIO_TTS_COMPLETE
from openbad.sensory.audio.config import TTSConfig

logger = logging.getLogger(__name__)


@dataclass
class SynthResult:
    """Result of a speech synthesis operation.

    Attributes
    ----------
    request_id : str
        Correlation ID linking back to the original request.
    audio_bytes : bytes
        Synthesised audio in WAV format.
    duration_ms : float
        Duration of the audio in milliseconds.
    processing_ms : float
        Wall-clock time to synthesise.
    success : bool
        Whether synthesis completed without error.
    error : str
        Non-empty on failure.
    """

    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    audio_bytes: bytes = b""
    duration_ms: float = 0.0
    processing_ms: float = 0.0
    success: bool = True
    error: str = ""

    def to_proto(self) -> TTSComplete:
        return TTSComplete(
            header=Header(
                timestamp_unix=time.time(),
                source_module="sensory.audio.tts",
                schema_version=1,
            ),
            request_id=self.request_id,
            duration_ms=self.duration_ms,
            success=self.success,
            error=self.error,
        )


class TTSEngine:
    """Piper TTS synthesis engine.

    Parameters
    ----------
    config : TTSConfig | None
        Engine configuration.  Defaults used if omitted.
    publish_fn : callable | None
        Optional async callback ``(topic, payload) -> None``.
    """

    def __init__(
        self,
        config: TTSConfig | None = None,
        publish_fn: Any | None = None,
    ) -> None:
        self._config = config or TTSConfig()
        self._publish = publish_fn
        self._voice: Any | None = None
        self._synth_count: int = 0

    @property
    def config(self) -> TTSConfig:
        return self._config

    @property
    def is_loaded(self) -> bool:
        return self._voice is not None

    @property
    def synth_count(self) -> int:
        return self._synth_count

    def load_voice(self) -> None:
        """Load the Piper voice model.

        Raises RuntimeError if piper-tts is not installed.
        """
        try:
            from piper import PiperVoice  # type: ignore[import-untyped]
        except ImportError:
            msg = (
                "piper-tts is required for TTS output. "
                "Install with: pip install piper-tts"
            )
            raise RuntimeError(msg) from None

        if not self._config.model_path:
            msg = "model_path must be set in TTSConfig"
            raise ValueError(msg)

        self._voice = PiperVoice.load(self._config.model_path)

    def synthesize(self, text: str) -> SynthResult:
        """Synthesise text to WAV audio bytes.

        Parameters
        ----------
        text : str
            Text to speak.

        Returns :class:`SynthResult` with audio data and metadata.
        """
        if self._voice is None:
            msg = "Voice not loaded — call load_voice() first"
            raise RuntimeError(msg)

        if not text.strip():
            return SynthResult(success=True, audio_bytes=b"", duration_ms=0.0)

        request_id = uuid.uuid4().hex[:12]
        start = time.perf_counter()

        try:
            wav_io = io.BytesIO()
            self._voice.synthesize(text, wav_io)
            audio = wav_io.getvalue()
            elapsed_ms = (time.perf_counter() - start) * 1000

            # Estimate duration from WAV size:
            # 16kHz, 16-bit mono → 32000 bytes/sec
            data_size = max(0, len(audio) - 44)  # strip WAV header
            duration_ms = (data_size / 32000) * 1000

            self._synth_count += 1

            return SynthResult(
                request_id=request_id,
                audio_bytes=audio,
                duration_ms=duration_ms,
                processing_ms=elapsed_ms,
                success=True,
            )
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (time.perf_counter() - start) * 1000
            return SynthResult(
                request_id=request_id,
                processing_ms=elapsed_ms,
                success=False,
                error=str(exc),
            )

    async def synthesize_and_publish(self, text: str) -> SynthResult:
        """Synthesise text and publish a TTSComplete event."""
        result = self.synthesize(text)

        if self._publish is not None:
            proto = result.to_proto()
            await self._publish(
                SENSORY_AUDIO_TTS_COMPLETE,
                proto.SerializeToString(),
            )

        return result

    def handle_request(self, request: TTSRequest) -> SynthResult:
        """Synchronous handler for a ``TTSRequest`` protobuf message.

        Uses SSML field if present, otherwise falls back to text.
        """
        content = request.ssml if request.ssml else request.text
        return self.synthesize(content)
