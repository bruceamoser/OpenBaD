"""Voice Activity Detection using WebRTC VAD."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbad.sensory.audio.capture import AudioChunk

logger = logging.getLogger(__name__)


class VoiceActivityDetector:
    """Voice activity detector using WebRTC VAD.

    Filters audio chunks to include only segments with detected speech,
    reducing unnecessary ASR processing.

    Parameters
    ----------
    sensitivity : float
        Detection sensitivity 0.0-1.0 (default 0.5).
        Higher values make the detector less aggressive.
    sample_rate : int
        Audio sample rate in Hz (default 16000).
    """

    def __init__(self, sensitivity: float = 0.5, sample_rate: int = 16000) -> None:
        if not 0.0 <= sensitivity <= 1.0:
            msg = f"sensitivity must be 0.0-1.0, got {sensitivity}"
            raise ValueError(msg)

        self._sensitivity = sensitivity
        self._sample_rate = sample_rate
        self._vad = None
        self._aggressiveness = self._sensitivity_to_aggressiveness(sensitivity)

        try:
            import webrtcvad  # noqa: PLC0415

            if sample_rate not in {8000, 16000, 32000, 48000}:
                msg = (
                    f"WebRTC VAD requires sample rate in {{8000, 16000, 32000, 48000}}, "
                    f"got {sample_rate}. Audio will not be filtered."
                )
                logger.warning(msg)
            else:
                self._vad = webrtcvad.Vad(self._aggressiveness)
        except ImportError:
            logger.warning(
                "webrtcvad not installed. VAD filtering disabled. "
                "Install with: pip install webrtcvad"
            )

    @staticmethod
    def _sensitivity_to_aggressiveness(sensitivity: float) -> int:
        """Convert sensitivity (0-1) to WebRTC aggressiveness (0-3).

        Higher sensitivity → lower aggressiveness (more permissive).
        """
        if sensitivity >= 0.75:
            return 0
        if sensitivity >= 0.5:
            return 1
        if sensitivity >= 0.25:
            return 2
        return 3

    def is_speech(self, chunk: AudioChunk) -> bool:
        """Determine if an audio chunk contains speech.

        Parameters
        ----------
        chunk : AudioChunk
            Audio chunk to analyze.

        Returns
        -------
        bool
            True if speech detected, False if silence or VAD unavailable.
        """
        if self._vad is None:
            return True

        if chunk.sample_format != "s16le":
            logger.debug("VAD only supports s16le format, passing through")
            return True

        if self._sample_rate not in {8000, 16000, 32000, 48000}:
            return True

        frame_duration_ms = int(chunk.duration_ms)
        if frame_duration_ms not in {10, 20, 30}:
            return True

        try:
            return self._vad.is_speech(chunk.pcm_data, self._sample_rate)
        except Exception:
            logger.exception("VAD processing error, passing chunk through")
            return True

    def filter_chunks(self, chunks: list[AudioChunk]) -> list[AudioChunk]:
        """Filter a list of chunks to include only those with speech.

        Parameters
        ----------
        chunks : list[AudioChunk]
            Audio chunks to filter.

        Returns
        -------
        list[AudioChunk]
            Chunks containing speech.
        """
        if self._vad is None:
            return chunks
        return [chunk for chunk in chunks if self.is_speech(chunk)]
