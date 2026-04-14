"""Tests for SvelteKit Toolbelt panel (#251)."""

from __future__ import annotations

from pathlib import Path

_WUI = Path(__file__).resolve().parent.parent / "wui-svelte"
PAGE = _WUI / "src" / "routes" / "toolbelt" / "+page.svelte"


def _text() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_toolbelt_page_exists():
    assert PAGE.exists()


# -- Cabinet grouped by role --

def test_tool_roles_defined():
    t = _text()
    for role in ("CLI", "WEB_SEARCH", "MEMORY", "MEDIA",
                 "CODE", "FILE_SYSTEM", "COMMUNICATION"):
        assert role in t


def test_cabinet_grouped():
    t = _text()
    assert "grouped" in t
    assert "TOOL_ROLES" in t


# -- Belt equipped tools --

def test_belt_section():
    t = _text()
    assert "Equipped Belt" in t
    assert "belt" in t


# -- Equip/Unequip --

def test_equip_button():
    t = _text()
    assert "Equip" in t
    assert "equip(" in t


def test_unequip_button():
    t = _text()
    assert "Unequip" in t
    assert "unequip(" in t


def test_optimistic_ui_update():
    t = _text()
    # Optimistic update modifies cabinet before API call
    assert "cabinet.map" in t or "cabinet =" in t


# -- Health indicators --

def test_health_dots():
    t = _text()
    assert "health-dot" in t
    assert "healthColor" in t


def test_health_statuses():
    t = _text()
    assert "AVAILABLE" in t
    assert "DEGRADED" in t


# -- Auto-swap event log --

def test_swap_log():
    t = _text()
    assert "Auto-Swap Log" in t
    assert "swapLog" in t


def test_swap_log_max_20():
    t = _text()
    assert "slice(-20)" in t


# -- REST API integration --

def test_api_toolbelt():
    t = _text()
    assert "/api/toolbelt" in t


def test_tool_surfaces_copy_present():
    t = _text()
    assert "Runtime Toolbelt" in t
    assert "Chat-Callable Embedded Tools" in t
    assert "embedded tools" in t.lower()


# -- Responsive card-based layout --

def test_responsive_layout():
    t = _text()
    assert "flex-wrap" in t


# -- WebSocket integration --

def test_websocket_health():
    t = _text()
    assert "toolbeltHealth" in t
