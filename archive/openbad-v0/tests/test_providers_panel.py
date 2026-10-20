"""Tests for SvelteKit Providers panel (#249)."""

from __future__ import annotations

from pathlib import Path

_WUI = Path(__file__).resolve().parent.parent / "wui-svelte"
PAGE = _WUI / "src" / "routes" / "providers" / "+page.svelte"


def _text() -> str:
    return PAGE.read_text(encoding="utf-8")


# -- File exists --

def test_providers_page_exists():
    assert PAGE.exists()


# -- Provider list with health indicators --

def test_provider_list_section():
    t = _text()
    assert "Registered Providers" in t


def test_health_dot():
    t = _text()
    assert "health-dot" in t
    assert "healthColor" in t


def test_verified_tag():
    t = _text()
    assert "verified" in t
    assert "unverified" in t


# -- System assignments --

def test_system_assignments():
    t = _text()
    assert "System Assignments" in t
    for sys in ("chat", "reasoning", "reactions", "sleep"):
        assert sys in t.lower()


def test_system_provider_model_inputs():
    t = _text()
    assert 'placeholder="provider"' in t
    assert 'placeholder="model"' in t


def test_provider_models_are_assigned_per_system():
    t = _text()
    assert 'Providers do not define default models.' in t
    assert 'Choose models under System Assignments.' in t


# -- Fallback chain --

def test_fallback_chain_section():
    t = _text()
    assert "Fallback Chain" in t


def test_drag_to_reorder():
    t = _text()
    assert "draggable" in t
    assert "ondragstart" in t
    assert "ondrop" in t


# -- Cortisol indicator --

def test_cortisol_indicator():
    t = _text()
    assert "Cortisol Level" in t
    assert "cortisol-bar" in t


def test_cortisol_color_function():
    t = _text()
    assert "cortisolColor" in t


# -- Save button --

def test_save_button():
    t = _text()
    assert "save" in t.lower()
    assert "dirty" in t


# -- REST API integration --

def test_loads_from_api():
    t = _text()
    assert "/api/providers" in t
    assert "/api/systems" in t


def test_saves_via_put():
    t = _text()
    assert "apiPut" in t


# -- Responsive layout --

def test_responsive_flex_wrap():
    t = _text()
    assert "flex-wrap" in t


# -- WebSocket integration for cortisol --

def test_endocrine_subscription():
    t = _text()
    assert "endocrineLevels" in t
