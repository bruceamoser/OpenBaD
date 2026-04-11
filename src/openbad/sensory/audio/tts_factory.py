"""TTS engine factory — selects the right backend from config."""

from __future__ import annotations

import logging
from typing import Any

from openbad.sensory.audio.config import TTSConfig
from openbad.sensory.audio.tts import SynthResult, TTSEngine

logger = logging.getLogger(__name__)


class DisabledTTSEngine(TTSEngine):
    """No-op TTS engine that suppresses all audio output."""

    def __init__(self) -> None:
        super().__init__(config=TTSConfig(engine="disabled"))

    @property
    def is_loaded(self) -> bool:
        return True

    def load_voice(self) -> None:
        pass

    def synthesize(self, text: str) -> SynthResult:
        return SynthResult(success=True, audio_bytes=b"", duration_ms=0.0)

    async def synthesize_and_publish(self, text: str) -> SynthResult:
        return self.synthesize(text)

    def handle_request(self, request: Any) -> SynthResult:
        return SynthResult(success=True, audio_bytes=b"", duration_ms=0.0)


def create_tts_engine(
    config: TTSConfig | None = None,
    publish_fn: Any | None = None,
) -> TTSEngine:
    """Instantiate the TTS engine specified by *config.engine*.

    Returns a :class:`DisabledTTSEngine` when engine is ``"disabled"``,
    otherwise a :class:`TTSEngine` for ``"piper"`` or ``"espeak"``.
    """
    cfg = config or TTSConfig()
    engine = cfg.engine

    if engine == "disabled":
        logger.info("TTS disabled by config — all speech output suppressed")
        return DisabledTTSEngine()

    return TTSEngine(config=cfg, publish_fn=publish_fn)
