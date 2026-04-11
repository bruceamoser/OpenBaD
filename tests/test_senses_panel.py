"""Tests for SvelteKit Senses panel (#250)."""

from __future__ import annotations

from pathlib import Path

_WUI = Path(__file__).resolve().parent.parent / "wui-svelte"
PAGE = _WUI / "src" / "routes" / "senses" / "+page.svelte"


def _text() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_senses_page_exists():
    assert PAGE.exists()


# -- Vision section --

def test_vision_section():
    t = _text()
    assert "Vision" in t


def test_capture_region_control():
    t = _text()
    assert "capture_region" in t
    assert "full-screen" in t
    assert "active-window" in t


def test_capture_interval_control():
    t = _text()
    assert "capture_interval_s" in t


def test_resolution_controls():
    t = _text()
    assert "max_resolution" in t
    assert "Max Width" in t
    assert "Max Height" in t


def test_compression_controls():
    t = _text()
    assert "compression" in t
    assert "quality" in t.lower()


# -- Hearing section --

def test_hearing_section():
    t = _text()
    assert "Hearing" in t


def test_asr_engine_control():
    t = _text()
    assert "ASR Engine" in t
    assert "vosk" in t.lower()
    assert "whisper" in t.lower()


def test_vad_sensitivity():
    t = _text()
    assert "vad_sensitivity" in t


def test_wake_phrases():
    t = _text()
    assert "Wake Phrases" in t
    assert "addPhrase" in t


# -- Speech section --

def test_speech_section():
    t = _text()
    assert "Speech" in t


def test_tts_engine():
    t = _text()
    assert "TTS Engine" in t
    assert "piper" in t.lower()


def test_voice_speed_volume():
    t = _text()
    assert "speaking_rate" in t
    assert "volume" in t.lower()


def test_output_device():
    t = _text()
    assert "output_device" in t


# -- Test buttons --

def test_test_tts_button():
    t = _text()
    assert "Test TTS" in t
    assert "testTts" in t


def test_preview_capture_button():
    t = _text()
    assert "Preview Capture" in t
    assert "previewCapture" in t


# -- Save and REST --

def test_save_button():
    t = _text()
    assert "/api/senses" in t
    assert "apiPut" in t


# -- Collapsible sections --

def test_collapsible_sections():
    t = _text()
    assert "visionOpen" in t
    assert "hearingOpen" in t
    assert "speechOpen" in t
