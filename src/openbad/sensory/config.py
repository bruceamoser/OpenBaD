"""Unified sensory configuration — loads hearing, vision, speech from senses.yaml."""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from openbad.sensory.audio.config import (
    ASRConfig,
    AudioCaptureConfig,
    AudioConfig,
    TTSConfig,
    WakeWordConfig,
)
from openbad.sensory.vision.config import (
    AttentionConfig,
    CompressionConfig,
    VisionConfig,
)

# ── Speech section (wraps TTSConfig) ─────────────────────────────── #


@dataclass
class SpeechConfig:
    """Speech output section of the unified sensory config."""

    tts: TTSConfig = field(default_factory=TTSConfig)


# ── Hearing section (mirrors AudioConfig minus TTS) ──────────────── #


@dataclass
class HearingConfig:
    """Hearing input section of the unified sensory config."""

    capture: AudioCaptureConfig = field(default_factory=AudioCaptureConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    wake_word: WakeWordConfig = field(default_factory=WakeWordConfig)


# ── Top-level unified config ─────────────────────────────────────── #


@dataclass
class SensoryConfig:
    """Unified sensory configuration covering hearing, vision, and speech."""

    hearing: HearingConfig = field(default_factory=HearingConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    speech: SpeechConfig = field(default_factory=SpeechConfig)

    def to_audio_config(self) -> AudioConfig:
        """Return an :class:`AudioConfig` for backward-compat consumers."""
        return AudioConfig(
            capture=self.hearing.capture,
            asr=self.hearing.asr,
            wake_word=self.hearing.wake_word,
            tts=self.speech.tts,
        )


# ── Helpers ───────────────────────────────────────────────────────── #


def _filter_fields(cls: type, raw: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}


def _parse_hearing(raw: dict[str, Any]) -> HearingConfig:
    capture = AudioCaptureConfig(**_filter_fields(AudioCaptureConfig, raw.get("capture", {})))
    asr = ASRConfig(**_filter_fields(ASRConfig, raw.get("asr", {})))
    ww = WakeWordConfig(**_filter_fields(WakeWordConfig, raw.get("wake_word", {})))
    return HearingConfig(capture=capture, asr=asr, wake_word=ww)


def _parse_vision(raw: dict[str, Any]) -> VisionConfig:
    attention_raw = raw.pop("attention", {})
    attention = AttentionConfig(**_filter_fields(AttentionConfig, attention_raw))
    compression_raw = raw.pop("compression", {})
    compression = CompressionConfig(**_filter_fields(CompressionConfig, compression_raw))
    max_res = raw.pop("max_resolution", None)
    if isinstance(max_res, list) and len(max_res) == 2:
        max_res = tuple(max_res)
    skip = {"attention", "compression", "max_resolution"}
    fields = {
        k: v for k, v in raw.items()
        if k in VisionConfig.__dataclass_fields__ and k not in skip
    }
    kwargs: dict[str, Any] = {**fields, "attention": attention, "compression": compression}
    if max_res is not None:
        kwargs["max_resolution"] = max_res
    return VisionConfig(**kwargs)


def _parse_speech(raw: dict[str, Any]) -> SpeechConfig:
    tts = TTSConfig(**_filter_fields(TTSConfig, raw.get("tts", {})))
    return SpeechConfig(tts=tts)


def _merge_legacy(
    base: dict[str, Any],
    config_dir: Path,
) -> dict[str, Any]:
    """Merge deprecated per-file configs into *base* and emit warnings."""
    audio_path = config_dir / "sensory_audio.yaml"
    if audio_path.exists():
        warnings.warn(
            "sensory_audio.yaml is deprecated — migrate settings to senses.yaml",
            DeprecationWarning,
            stacklevel=3,
        )
        audio_raw: dict[str, Any] = yaml.safe_load(audio_path.read_text()) or {}
        audio_section = audio_raw.get("audio", audio_raw)
        # Map audio sections into hearing + speech
        hearing = base.setdefault("hearing", {})
        for key in ("capture", "asr", "wake_word"):
            if key in audio_section and key not in hearing:
                hearing[key] = audio_section[key]
        if "tts" in audio_section:
            speech = base.setdefault("speech", {})
            if "tts" not in speech:
                speech["tts"] = audio_section["tts"]

    vision_path = config_dir / "sensory_vision.yaml"
    if vision_path.exists():
        warnings.warn(
            "sensory_vision.yaml is deprecated — migrate settings to senses.yaml",
            DeprecationWarning,
            stacklevel=3,
        )
        vision_raw: dict[str, Any] = yaml.safe_load(vision_path.read_text()) or {}
        vision_section = vision_raw.get("vision", vision_raw)
        if "vision" not in base:
            base["vision"] = vision_section

    return base


# ── Public loader ─────────────────────────────────────────────────── #


def load_sensory_config(path: Path | str | None = None) -> SensoryConfig:
    """Load :class:`SensoryConfig` from a ``senses.yaml`` file.

    When *path* is ``None`` or the file does not exist the function still checks
    for the deprecated ``sensory_audio.yaml`` / ``sensory_vision.yaml`` in the
    same directory and merges them with a deprecation warning.  If none are found
    bare defaults are returned.
    """
    p = Path(path) if path is not None else Path("config/senses.yaml")

    if p.exists():
        raw: dict[str, Any] = yaml.safe_load(p.read_text()) or {}
    else:
        raw = {}

    # Backward-compat: merge old per-file configs
    config_dir = p.parent
    raw = _merge_legacy(raw, config_dir)

    hearing = _parse_hearing(raw.get("hearing", {}))
    vision = _parse_vision(dict(raw.get("vision", {})))  # copy — _parse_vision pops
    speech = _parse_speech(raw.get("speech", {}))

    return SensoryConfig(hearing=hearing, vision=vision, speech=speech)
