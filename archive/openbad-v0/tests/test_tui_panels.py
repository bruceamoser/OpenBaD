"""Tests for TUI hormone gauge and FSM state panels."""

from __future__ import annotations

from openbad.tui.panels import (
    FSM_STATES,
    HORMONE_COLOURS,
    HORMONES,
    STATE_COLOURS,
    FSMPanel,
    HormoneGauge,
    HormonePanel,
    _bar,
)

# ── Bar helper ───────────────────────────────────────────────────────


class TestBarHelper:
    def test_zero(self):
        result = _bar(0.0, "green")
        assert "0%" in result

    def test_full(self):
        result = _bar(1.0, "green")
        assert "100%" in result
        assert "█" in result

    def test_half(self):
        result = _bar(0.5, "red")
        assert "50%" in result

    def test_clamps_above_one(self):
        result = _bar(1.5, "green")
        assert "100%" in result

    def test_clamps_below_zero(self):
        result = _bar(-0.5, "green")
        assert "0%" in result


# ── HormoneGauge ─────────────────────────────────────────────────────


class TestHormoneGauge:
    def test_creation(self):
        g = HormoneGauge("dopamine")
        assert g.hormone == "dopamine"
        assert g.level == 0.0

    def test_render_contains_name(self):
        g = HormoneGauge("cortisol")
        text = g.render()
        assert "cortisol" in text

    def test_level_reactive(self):
        g = HormoneGauge("adrenaline")
        g.level = 0.75
        assert g.level == 0.75


# ── HormonePanel ─────────────────────────────────────────────────────


class TestHormonePanel:
    def test_creation(self):
        panel = HormonePanel()
        assert isinstance(panel, HormonePanel)


# ── FSMPanel ─────────────────────────────────────────────────────────


class TestFSMPanel:
    def test_creation(self):
        panel = FSMPanel()
        assert panel.state == "UNKNOWN"

    def test_render_shows_state(self):
        panel = FSMPanel()
        panel.state = "ACTIVE"
        text = panel.render()
        assert "ACTIVE" in text
        assert "FSM State" in text

    def test_render_shows_all_states(self):
        panel = FSMPanel()
        text = panel.render()
        for s in FSM_STATES:
            assert s in text

    def test_current_marker(self):
        panel = FSMPanel()
        panel.state = "THROTTLED"
        text = panel.render()
        # The current state should have marker ▸
        for line in text.split("\n"):
            if "THROTTLED" in line and "Current" not in line:
                assert "▸" in line


# ── Constants ────────────────────────────────────────────────────────


class TestConstants:
    def test_hormones_tuple(self):
        assert len(HORMONES) == 4
        assert "dopamine" in HORMONES
        assert "adrenaline" in HORMONES
        assert "cortisol" in HORMONES
        assert "endorphin" in HORMONES

    def test_hormone_colours_complete(self):
        for h in HORMONES:
            assert h in HORMONE_COLOURS

    def test_fsm_states(self):
        assert len(FSM_STATES) == 5
        assert "IDLE" in FSM_STATES
        assert "EMERGENCY" in FSM_STATES

    def test_state_colours_complete(self):
        for s in FSM_STATES:
            assert s in STATE_COLOURS
