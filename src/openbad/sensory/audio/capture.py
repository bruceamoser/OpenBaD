"""PipeWire audio capture node management.

Creates virtual audio capture streams via the PipeWire D-Bus interface
to passively monitor microphone input and optional application audio
without disrupting the user's normal audio experience.

Audio chunks are published as ``AudioChunk`` protobuf messages on
``agent/sensory/audio/{source_id}``.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from openbad.nervous_system.topics import SENSORY_AUDIO, topic_for
from openbad.sensory.audio.config import AudioCaptureConfig

logger = logging.getLogger(__name__)


@dataclass
class AudioChunk:
    """A single chunk of captured audio data.

    Attributes
    ----------
    source_id : str
        Identifier for the audio source (e.g. ``"mic"``, ``"app-firefox"``).
    pcm_data : bytes
        Raw PCM audio bytes.
    sample_rate : int
        Sample rate in Hz.
    channels : int
        Number of audio channels.
    sample_format : str
        PCM format string (e.g. ``"s16le"``, ``"f32le"``).
    timestamp : float
        Unix timestamp when the chunk was captured.
    sequence : int
        Monotonic sequence number for ordering.
    """

    source_id: str
    pcm_data: bytes
    sample_rate: int = 16000
    channels: int = 1
    sample_format: str = "s16le"
    timestamp: float = field(default_factory=time.time)
    sequence: int = 0

    @property
    def duration_ms(self) -> float:
        """Duration of this chunk in milliseconds."""
        bytes_per_sample = 4 if self.sample_format == "f32le" else 2
        frame_size = self.channels * bytes_per_sample
        if frame_size == 0 or self.sample_rate == 0:
            return 0.0
        num_frames = len(self.pcm_data) / frame_size
        return (num_frames / self.sample_rate) * 1000

    @property
    def rms_amplitude(self) -> float:
        """Root mean square amplitude of PCM data (s16le only).

        Returns 0.0 for unsupported formats or empty data.
        """
        if self.sample_format != "s16le" or len(self.pcm_data) < 2:
            return 0.0
        n = len(self.pcm_data) // 2
        total = 0.0
        for i in range(n):
            sample = struct.unpack_from("<h", self.pcm_data, i * 2)[0]
            total += sample * sample
        return (total / n) ** 0.5

    def mqtt_topic(self) -> str:
        return topic_for(SENSORY_AUDIO, source_id=self.source_id)


# ---------------------------------------------------------------------------
# PipeWire audio stream
# ---------------------------------------------------------------------------


class PipeWireAudioStream:
    """Async audio capture stream using PipeWire.

    On Linux, this creates a PipeWire stream node that passively
    captures audio from the specified source.  Audio chunks are emitted
    via an async generator or published directly to the event bus.

    Parameters
    ----------
    source_id : str
        Human-readable name for this audio source.
    config : AudioCaptureConfig | None
        Capture settings.  Uses defaults if omitted.
    publish_fn : callable | None
        Optional async callback ``(topic, payload) -> None``.
    """

    def __init__(
        self,
        source_id: str = "mic",
        config: AudioCaptureConfig | None = None,
        publish_fn: Any | None = None,
    ) -> None:
        if sys.platform != "linux":
            msg = (
                "PipeWireAudioStream requires Linux with PipeWire. "
                f"Current platform: {sys.platform}"
            )
            raise RuntimeError(msg)

        self._source_id = source_id
        self._config = config or AudioCaptureConfig()
        self._publish = publish_fn
        self._running = False
        self._sequence = 0

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def config(self) -> AudioCaptureConfig:
        return self._config

    @property
    def is_running(self) -> bool:
        return self._running

    def _make_chunk(self, pcm_data: bytes) -> AudioChunk:
        self._sequence += 1
        return AudioChunk(
            source_id=self._source_id,
            pcm_data=pcm_data,
            sample_rate=self._config.sample_rate,
            channels=self._config.channels,
            sample_format=self._config.sample_format,
            sequence=self._sequence,
        )

    async def _read_audio(self) -> bytes:
        """Read audio from PipeWire (placeholder for real implementation).

        In production this would use pw-cat subprocess or PipeWire's
        native Python bindings to read PCM data.  For now, it raises
        NotImplementedError to indicate the integration point.
        """
        msg = (
            "PipeWire audio read not yet connected. "
            "Production implementation will use pw-cat or libpipewire."
        )
        raise NotImplementedError(msg)

    async def capture_loop(
        self,
        audio_provider: Any | None = None,
    ) -> None:
        """Run the audio capture loop.

        Parameters
        ----------
        audio_provider : async callable | None
            Override for testing — an async callable returning ``bytes``
            or ``None`` to stop the loop.  If omitted, uses the real
            PipeWire reader.
        """
        reader = audio_provider or self._read_audio
        self._running = True
        chunk_interval = self._config.chunk_duration_ms / 1000

        try:
            while self._running:
                pcm_data = await reader()
                if pcm_data is None:
                    break

                chunk = self._make_chunk(pcm_data)

                if self._publish is not None:
                    topic = chunk.mqtt_topic()
                    await self._publish(topic, chunk.pcm_data)

                await asyncio.sleep(chunk_interval)
        finally:
            self._running = False

    def stop(self) -> None:
        """Signal the capture loop to stop."""
        self._running = False

    async def __aenter__(self) -> PipeWireAudioStream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.stop()
