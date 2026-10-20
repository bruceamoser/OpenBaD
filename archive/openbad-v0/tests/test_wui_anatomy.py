"""Tests for the SvelteKit WUI assets (replaced legacy static/ tests)."""

from __future__ import annotations

from pathlib import Path

_WUI = Path(__file__).resolve().parent.parent / "wui-svelte"
LAYOUT = _WUI / "src" / "routes" / "+layout.svelte"


def test_layout_exists():
    assert LAYOUT.exists()


def test_layout_contains_nav():
    html = LAYOUT.read_text(encoding="utf-8")
    assert "side-nav" in html
    assert "Health" in html
    assert "Chat" in html
    assert "Providers" in html


def test_layout_contains_hormone_refs():
    """The health dashboard (not layout) now owns hormone display."""
    health = _WUI / "src" / "routes" / "health" / "+page.svelte"
    html = health.read_text(encoding="utf-8")
    assert "Dopamine" in html
    assert "Adrenaline" in html
    assert "Cortisol" in html
    assert "Endorphin" in html
