"""Audio configuration — device selection, sample format, ASR settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class AudioCaptureConfig:
    """PipeWire audio capture settings.

    Attributes
    ----------
    sample_rate : int
        Sample rate in Hz (default 16000, standard for ASR).
    channels : int
        Number of audio channels (default 1 — mono for ASR).
    sample_format : str
        PCM sample format: ``"s16le"`` (signed 16-bit LE) or ``"f32le"`` (default ``"s16le"``).
    chunk_duration_ms : int
        Duration of each audio chunk in milliseconds (default 100).
    device : str
        PipeWire source node name/serial. Empty string = default mic.
    passive : bool
        Monitor mode — tap existing stream without disrupting it (default True).
    """

    sample_rate: int = 16000
    channels: int = 1
    sample_format: str = "s16le"
    chunk_duration_ms: int = 100
    device: str = ""
    passive: bool = True

    @property
    def chunk_bytes(self) -> int:
        """Number of bytes per chunk."""
        bytes_per_sample = 4 if self.sample_format == "f32le" else 2
        samples = int(self.sample_rate * self.chunk_duration_ms / 1000)
        return samples * self.channels * bytes_per_sample


@dataclass
class ASRConfig:
    """Automatic speech recognition settings.

    Attributes
    ----------
    vosk_model_path : str
        Path to the Vosk language model directory.
    whisper_model : str
        Whisper model size (default ``"base"``).
    default_engine : str
        Default ASR engine: ``"vosk"`` or ``"whisper"`` (default ``"vosk"``).
    """

    vosk_model_path: str = ""
    whisper_model: str = "base"
    default_engine: str = "vosk"


@dataclass
class WakeWordConfig:
    """Wake-word detector settings.

    Attributes
    ----------
    phrases : list[str]
        Activation phrases to listen for.
    threshold : float
        Detection confidence threshold 0.0–1.0 (default 0.5).
    """

    phrases: list[str] = field(default_factory=lambda: ["hey agent"])
    threshold: float = 0.5


@dataclass
class TTSConfig:
    """Text-to-speech output settings.

    Attributes
    ----------
    engine : str
        TTS backend: ``"piper"`` (default).
    model_path : str
        Path to TTS voice model.
    output_device : str
        PipeWire sink node for TTS output. Empty = default.
    """

    engine: str = "piper"
    model_path: str = ""
    output_device: str = ""


@dataclass
class AudioConfig:
    """Top-level audio configuration."""

    capture: AudioCaptureConfig = field(default_factory=AudioCaptureConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    wake_word: WakeWordConfig = field(default_factory=WakeWordConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)


def load_audio_config(path: Path | str | None = None) -> AudioConfig:
    """Load :class:`AudioConfig` from a YAML file.

    Falls back to defaults when *path* is ``None`` or the file does not exist.
    """
    if path is None:
        return AudioConfig()

    p = Path(path)
    if not p.exists():
        return AudioConfig()

    raw: dict[str, Any] = yaml.safe_load(p.read_text()) or {}
    audio_raw = raw.get("audio", raw)

    capture_raw = audio_raw.get("capture", {})
    capture = AudioCaptureConfig(**{
        k: v for k, v in capture_raw.items() if k in AudioCaptureConfig.__dataclass_fields__
    })

    asr_raw = audio_raw.get("asr", {})
    asr = ASRConfig(**{
        k: v for k, v in asr_raw.items() if k in ASRConfig.__dataclass_fields__
    })

    ww_raw = audio_raw.get("wake_word", {})
    wake_word = WakeWordConfig(**{
        k: v for k, v in ww_raw.items() if k in WakeWordConfig.__dataclass_fields__
    })

    tts_raw = audio_raw.get("tts", {})
    tts = TTSConfig(**{
        k: v for k, v in tts_raw.items() if k in TTSConfig.__dataclass_fields__
    })

    return AudioConfig(capture=capture, asr=asr, wake_word=wake_word, tts=tts)
