"""ASR engine factory — selects the right backend from config."""

from __future__ import annotations

from typing import Any

from openbad.sensory.audio.config import AudioConfig


def create_asr_engine(
    config: AudioConfig,
    publish_fn: Any | None = None,
) -> Any:
    """Instantiate the ASR engine specified by *config.asr.default_engine*.

    Returns a :class:`VoskRecogniser` or :class:`WhisperTranscriber`
    ready for :meth:`load_model`.
    """
    engine = config.asr.default_engine
    if engine == "vosk":
        from openbad.sensory.audio.asr_vosk import VoskRecogniser

        return VoskRecogniser(
            model_path=config.asr.vosk_model_path,
            sample_rate=config.capture.sample_rate,
            publish_fn=publish_fn,
        )
    if engine == "whisper":
        from openbad.sensory.audio.asr_whisper import WhisperTranscriber

        return WhisperTranscriber(
            model_size=config.asr.whisper_model,
            publish_fn=publish_fn,
        )
    msg = f"Unknown ASR engine: {engine!r}"
    raise ValueError(msg)
