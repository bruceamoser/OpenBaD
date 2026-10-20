"""Tests for endocrine telemetry."""

from __future__ import annotations

import time

from openbad.endocrine.config import EndocrineConfig, HormoneConfig
from openbad.endocrine.controller import HORMONES, EndocrineController
from openbad.endocrine.telemetry import EndocrineTelemetry


def _make_controller(**overrides: HormoneConfig) -> EndocrineController:
    cfg = EndocrineConfig(**overrides)
    return EndocrineController(config=cfg)


class TestRecordTrigger:
    def test_logs_above_delta(self) -> None:
        ctrl = _make_controller()
        telemetry = EndocrineTelemetry(ctrl, min_change_delta=0.05)
        telemetry.record_trigger("dopamine", 0.0, 0.15, "task_complete")
        assert len(telemetry.change_log) == 1
        assert telemetry.change_log[0].hormone == "dopamine"
        assert telemetry.change_log[0].trigger_event == "task_complete"

    def test_ignores_below_delta(self) -> None:
        ctrl = _make_controller()
        telemetry = EndocrineTelemetry(ctrl, min_change_delta=0.05)
        telemetry.record_trigger("dopamine", 0.0, 0.02, "tiny")
        assert len(telemetry.change_log) == 0

    def test_history_capped(self) -> None:
        ctrl = _make_controller()
        telemetry = EndocrineTelemetry(ctrl, max_history=3)
        for i in range(5):
            telemetry.record_trigger("dopamine", 0.0, 0.2 + i * 0.01, f"evt-{i}")
        assert len(telemetry.change_log) == 3
        assert telemetry.change_log[0].trigger_event == "evt-2"

    def test_updates_last_trigger_time(self) -> None:
        ctrl = _make_controller()
        telemetry = EndocrineTelemetry(ctrl)
        telemetry.record_trigger("cortisol", 0.0, 0.15, "load")
        assert telemetry._stats["cortisol"].last_trigger_time is not None


class TestActivationStats:
    def test_counts_activation_crossing(self) -> None:
        ctrl = _make_controller(
            dopamine=HormoneConfig(
                activation_threshold=0.40,
                half_life_seconds=300.0,
            ),
        )
        telemetry = EndocrineTelemetry(ctrl)
        # Snapshot starts at 0. Trigger pushes dopamine above 0.40.
        ctrl.trigger("dopamine", 0.50)
        telemetry.update_activation_stats()
        assert telemetry._stats["dopamine"].activation_count == 1

    def test_no_double_count_when_still_active(self) -> None:
        ctrl = _make_controller(
            dopamine=HormoneConfig(
                activation_threshold=0.40,
                half_life_seconds=300.0,
            ),
        )
        telemetry = EndocrineTelemetry(ctrl)
        ctrl.trigger("dopamine", 0.50)
        telemetry.update_activation_stats()
        # Still above threshold — should not increment again.
        ctrl.trigger("dopamine", 0.10)
        telemetry.update_activation_stats()
        assert telemetry._stats["dopamine"].activation_count == 1

    def test_tracks_time_above_threshold(self) -> None:
        ctrl = _make_controller(
            dopamine=HormoneConfig(
                activation_threshold=0.30,
                half_life_seconds=600.0,
            ),
        )
        telemetry = EndocrineTelemetry(ctrl)
        ctrl.trigger("dopamine", 0.50)
        # Fake time advance by manipulating snapshot time.
        telemetry._last_snapshot_time = time.monotonic() - 2.0
        telemetry.update_activation_stats()
        assert telemetry._stats["dopamine"].total_time_above_threshold >= 1.5

    def test_counts_escalation_crossing(self) -> None:
        ctrl = _make_controller(
            adrenaline=HormoneConfig(
                activation_threshold=0.30,
                escalation_threshold=0.70,
                half_life_seconds=60.0,
            ),
        )
        telemetry = EndocrineTelemetry(ctrl)
        ctrl.trigger("adrenaline", 0.80)
        telemetry.update_activation_stats()
        assert telemetry._stats["adrenaline"].escalation_count == 1


class TestStatus:
    def test_status_contains_all_hormones(self) -> None:
        ctrl = _make_controller()
        telemetry = EndocrineTelemetry(ctrl)
        status = telemetry.status()
        assert "levels" in status
        for h in HORMONES:
            assert h in status["hormones"]
            assert "level" in status["hormones"][h]
            assert "active" in status["hormones"][h]
            assert "recent_changes" in status["hormones"][h]

    def test_status_shows_recent_changes(self) -> None:
        ctrl = _make_controller()
        telemetry = EndocrineTelemetry(ctrl)
        telemetry.record_trigger("dopamine", 0.0, 0.20, "test1")
        telemetry.record_trigger("dopamine", 0.20, 0.40, "test2")
        status = telemetry.status()
        changes = status["hormones"]["dopamine"]["recent_changes"]
        assert len(changes) == 2
        assert changes[0]["trigger"] == "test1"

    def test_seconds_since_last_trigger_none_when_never(self) -> None:
        ctrl = _make_controller()
        telemetry = EndocrineTelemetry(ctrl)
        status = telemetry.status()
        assert status["hormones"]["dopamine"]["seconds_since_last_trigger"] is None


class TestSummary:
    def test_summary_structure(self) -> None:
        ctrl = _make_controller()
        telemetry = EndocrineTelemetry(ctrl)
        s = telemetry.summary()
        assert "levels" in s
        assert "stats" in s
        for h in HORMONES:
            assert h in s["stats"]
            assert "activations" in s["stats"][h]
            assert "escalations" in s["stats"][h]


class TestReset:
    def test_reset_clears_all(self) -> None:
        ctrl = _make_controller()
        telemetry = EndocrineTelemetry(ctrl)
        telemetry.record_trigger("dopamine", 0.0, 0.20, "test")
        telemetry.reset()
        assert len(telemetry.change_log) == 0
        assert telemetry._stats["dopamine"].activation_count == 0
