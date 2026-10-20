"""Tests for Voice Activity Detection."""

from __future__ import annotations

import pytest

from openbad.sensory.audio.capture import AudioChunk
from openbad.sensory.audio.vad import VoiceActivityDetector


def test_vad_initialization():
    """Test VAD initializes with valid parameters."""
    vad = VoiceActivityDetector(sensitivity=0.5, sample_rate=16000)
    assert vad._sensitivity == 0.5
    assert vad._sample_rate == 16000


def test_vad_sensitivity_validation():
    """Test VAD rejects invalid sensitivity values."""
    with pytest.raises(ValueError, match="sensitivity must be 0.0-1.0"):
        VoiceActivityDetector(sensitivity=1.5)


def test_sensitivity_to_aggressiveness_mapping():
    """Test sensitivity maps correctly to WebRTC aggressiveness."""
    assert VoiceActivityDetector._sensitivity_to_aggressiveness(0.9) == 0
    assert VoiceActivityDetector._sensitivity_to_aggressiveness(0.1) == 3


def test_vad_fallback_without_webrtcvad():
    """Test VAD falls back gracefully when webrtcvad unavailable."""
    vad = VoiceActivityDetector(sensitivity=0.5, sample_rate=44100)
    chunk = AudioChunk(
        source_id="test",
        pcm_data=b"\x00" * 320,
        sample_rate=44100,
        channels=1,
        sample_format="s16le",
    )
    assert vad.is_speech(chunk) is True


def test_vad_unsupported_format():
    """Test VAD passes through unsupported audio formats."""
    vad = VoiceActivityDetector(sensitivity=0.5, sample_rate=16000)
    chunk = AudioChunk(
        source_id="test",
        pcm_data=b"\x00" * 320,
        sample_rate=16000,
        channels=1,
        sample_format="f32le",
    )
    assert vad.is_speech(chunk) is True
