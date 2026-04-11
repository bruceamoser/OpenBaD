"""Tests for sleep schedule configuration and scheduler."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from openbad.cognitive.config import CognitiveSystem
from openbad.cognitive.event_loop import CognitiveRequest
from openbad.memory.sleep.schedule import (
    SleepScheduleConfig,
    SleepScheduler,
    sleep_gate,
)

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


class FakeFSM:
    """Minimal FSM stub that records transitions."""

    def __init__(self) -> None:
        self.state = "IDLE"
        self.history: list[str] = []

    def fire(self, trigger: str) -> bool:
        self.history.append(trigger)
        if trigger == "sleep":
            self.state = "SLEEP"
        elif trigger == "wake":
            self.state = "IDLE"
        return True


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2025, 6, 15, hour, minute, tzinfo=UTC)


# ------------------------------------------------------------------ #
# SleepScheduleConfig
# ------------------------------------------------------------------ #


class TestSleepScheduleConfig:
    def test_defaults(self) -> None:
        cfg = SleepScheduleConfig()
        assert cfg.start_hour == 2
        assert cfg.duration_hours == 3.0
        assert cfg.enabled is True

    def test_from_dict(self) -> None:
        cfg = SleepScheduleConfig.from_dict({
            "start_hour": 23,
            "duration_hours": 4.0,
            "enabled": False,
        })
        assert cfg.start_hour == 23
        assert cfg.duration_hours == 4.0
        assert cfg.enabled is False

    def test_invalid_start_hour(self) -> None:
        with pytest.raises(ValueError, match="start_hour"):
            SleepScheduleConfig(start_hour=25)

    def test_invalid_duration(self) -> None:
        with pytest.raises(ValueError, match="duration_hours"):
            SleepScheduleConfig(duration_hours=0)

    def test_is_in_window_inside(self) -> None:
        cfg = SleepScheduleConfig(start_hour=2, duration_hours=3.0)
        assert cfg.is_in_window(_dt(3, 0)) is True

    def test_is_in_window_outside(self) -> None:
        cfg = SleepScheduleConfig(start_hour=2, duration_hours=3.0)
        assert cfg.is_in_window(_dt(6, 0)) is False

    def test_is_in_window_at_boundary_start(self) -> None:
        cfg = SleepScheduleConfig(start_hour=2, duration_hours=3.0)
        assert cfg.is_in_window(_dt(2, 0)) is True

    def test_is_in_window_at_boundary_end(self) -> None:
        cfg = SleepScheduleConfig(start_hour=2, duration_hours=3.0)
        # 05:00 is NOT in the window (end exclusive)
        assert cfg.is_in_window(_dt(5, 0)) is False

    def test_is_in_window_wrap_midnight(self) -> None:
        cfg = SleepScheduleConfig(start_hour=23, duration_hours=4.0)
        assert cfg.is_in_window(_dt(23, 30)) is True
        assert cfg.is_in_window(_dt(1, 0)) is True
        assert cfg.is_in_window(_dt(4, 0)) is False

    def test_disabled_never_in_window(self) -> None:
        cfg = SleepScheduleConfig(start_hour=2, enabled=False)
        assert cfg.is_in_window(_dt(3, 0)) is False

    def test_seconds_until_start(self) -> None:
        cfg = SleepScheduleConfig(start_hour=2)
        # At 01:00, 1 hour until 02:00
        assert cfg.seconds_until_start(_dt(1, 0)) == 3600.0

    def test_seconds_until_start_wrap(self) -> None:
        cfg = SleepScheduleConfig(start_hour=2)
        # At 03:00, ~23 hours until next 02:00
        assert cfg.seconds_until_start(_dt(3, 0)) == 23 * 3600.0


# ------------------------------------------------------------------ #
# SleepScheduler FSM transitions
# ------------------------------------------------------------------ #


class TestSleepSchedulerTransitions:
    def test_entering_sleep_fires_fsm(self) -> None:
        fsm = FakeFSM()
        cfg = SleepScheduleConfig(start_hour=2, duration_hours=3.0)
        scheduler = SleepScheduler(config=cfg, fsm=fsm)
        scheduler._enter_sleep()
        assert fsm.state == "SLEEP"
        assert "sleep" in fsm.history
        assert scheduler.sleeping is True

    def test_exiting_sleep_fires_wake(self) -> None:
        fsm = FakeFSM()
        cfg = SleepScheduleConfig(start_hour=2, duration_hours=3.0)
        scheduler = SleepScheduler(config=cfg, fsm=fsm)
        scheduler._enter_sleep()
        scheduler._exit_sleep()
        assert fsm.state == "IDLE"
        assert "wake" in fsm.history
        assert scheduler.sleeping is False

    def test_manual_wake_override(self) -> None:
        fsm = FakeFSM()
        cfg = SleepScheduleConfig(start_hour=2, duration_hours=3.0)
        scheduler = SleepScheduler(config=cfg, fsm=fsm)
        scheduler._enter_sleep()
        assert scheduler.manual_wake() is True
        assert fsm.state == "IDLE"
        assert scheduler.sleeping is False

    def test_manual_wake_when_not_sleeping(self) -> None:
        fsm = FakeFSM()
        cfg = SleepScheduleConfig(start_hour=2, duration_hours=3.0)
        scheduler = SleepScheduler(config=cfg, fsm=fsm)
        assert scheduler.manual_wake() is False


# ------------------------------------------------------------------ #
# sleep_gate
# ------------------------------------------------------------------ #


class TestSleepGate:
    def _req(self, system: CognitiveSystem) -> CognitiveRequest:
        return CognitiveRequest(
            request_id="test",
            prompt="hello",
            system=system,
        )

    def test_allows_when_not_sleeping(self) -> None:
        fsm = FakeFSM()
        cfg = SleepScheduleConfig()
        scheduler = SleepScheduler(config=cfg, fsm=fsm)
        assert sleep_gate(self._req(CognitiveSystem.CHAT), scheduler) is None

    def test_blocks_chat_during_sleep(self) -> None:
        fsm = FakeFSM()
        cfg = SleepScheduleConfig()
        scheduler = SleepScheduler(config=cfg, fsm=fsm)
        scheduler._enter_sleep()
        resp = sleep_gate(self._req(CognitiveSystem.CHAT), scheduler)
        assert resp is not None
        assert "sleep" in resp.error.lower()

    def test_blocks_reasoning_during_sleep(self) -> None:
        fsm = FakeFSM()
        cfg = SleepScheduleConfig()
        scheduler = SleepScheduler(config=cfg, fsm=fsm)
        scheduler._enter_sleep()
        resp = sleep_gate(self._req(CognitiveSystem.REASONING), scheduler)
        assert resp is not None

    def test_blocks_reactions_during_sleep(self) -> None:
        fsm = FakeFSM()
        cfg = SleepScheduleConfig()
        scheduler = SleepScheduler(config=cfg, fsm=fsm)
        scheduler._enter_sleep()
        resp = sleep_gate(self._req(CognitiveSystem.REACTIONS), scheduler)
        assert resp is not None

    def test_allows_sleep_system_during_sleep(self) -> None:
        fsm = FakeFSM()
        cfg = SleepScheduleConfig()
        scheduler = SleepScheduler(config=cfg, fsm=fsm)
        scheduler._enter_sleep()
        assert sleep_gate(self._req(CognitiveSystem.SLEEP), scheduler) is None

    def test_allows_after_manual_wake(self) -> None:
        fsm = FakeFSM()
        cfg = SleepScheduleConfig()
        scheduler = SleepScheduler(config=cfg, fsm=fsm)
        scheduler._enter_sleep()
        scheduler.manual_wake()
        assert sleep_gate(self._req(CognitiveSystem.CHAT), scheduler) is None
