"""Tests for SvelteKit Health dashboard (#254)."""

from __future__ import annotations

from pathlib import Path

_WUI = Path(__file__).resolve().parent.parent / "wui-svelte"
PAGE = _WUI / "src" / "routes" / "health" / "+page.svelte"


def _text() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_health_page_exists():
    assert PAGE.exists()


# -- FSM state --

def test_fsm_states():
    t = _text()
    for state in ("IDLE", "ACTIVE", "THROTTLED", "SLEEP", "EMERGENCY"):
        assert state in t


def test_fsm_badge():
    t = _text()
    assert "fsm-badge" in t
    assert "fsmColor" in t


def test_fsm_transition_log():
    t = _text()
    assert "transitions" in t
    assert "transition-log" in t


def test_transition_limit_10():
    t = _text()
    assert "slice(0, 10)" in t


# -- Endocrine gauges --

def test_endocrine_gauges():
    t = _text()
    assert "Endocrine Levels" in t
    for h in ("Dopamine", "Adrenaline", "Cortisol", "Endorphin"):
        assert h in t


def test_gauge_bars():
    t = _text()
    assert "gauge-bar-fill" in t
    assert "hormoneColor" in t


# -- CPU/Memory sparklines --

def test_cpu_sparkline():
    t = _text()
    assert "CPU" in t
    assert "cpuHistory" in t
    assert "sparklinePath" in t


def test_memory_sparkline():
    t = _text()
    assert "Memory" in t
    assert "memHistory" in t


def test_sparkline_5_min_window():
    t = _text()
    assert "SPARKLINE_MAX" in t
    assert "300" in t


# -- Disk/Network --

def test_disk_network():
    t = _text()
    assert "Disk" in t
    assert "Net TX" in t
    assert "Net RX" in t


# -- Sleep schedule --

def test_sleep_schedule():
    t = _text()
    assert "Sleep Schedule" in t


def test_sleep_wake_buttons():
    t = _text()
    assert "Sleep Now" in t
    assert "Wake" in t
    assert "triggerSleep" in t
    assert "triggerWake" in t


# -- WebSocket sourced --

def test_websocket_stores():
    t = _text()
    assert "cpuTelemetry" in t
    assert "endocrineLevels" in t
    assert "fsmState" in t


# -- Responsive grid --

def test_responsive_grid():
    t = _text()
    assert "dashboard-grid" in t
    assert "grid-template-columns" in t
