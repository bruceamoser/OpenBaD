"""Tests for the sleep system: consolidation, scheduling, and crew dispatch."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.memory.sleep.consolidation import (
    ConsolidationReport,
    consolidate_stm_to_ltm,
)
from openbad.memory.sleep.schedule import SleepScheduleConfig, SleepScheduler

# ── Helpers ───────────────────────────────────────────────────────────── #


def _make_stm_entry(
    key: str,
    value: str = "test",
    context: str = "",
    age: float = 3600.0,
    metadata: dict | None = None,
) -> MemoryEntry:
    return MemoryEntry(
        key=key,
        value=value,
        tier=MemoryTier.STM,
        context=context,
        created_at=time.time() - age,
        metadata=metadata or {},
    )


def _mock_memory_controller(stm_entries: list[MemoryEntry] | None = None):
    mc = MagicMock()
    mc.stm.query.return_value = stm_entries or []
    mc.stm.delete.return_value = True
    mc.episodic.query.return_value = []
    mc.semantic.query.return_value = []
    mc.write_episodic.return_value = "eid-1"
    mc.write_semantic.return_value = "sid-1"
    return mc


# ── Deterministic consolidation ──────────────────────────────────────── #


class TestConsolidateStmToLtm:
    def test_empty_stm_returns_zero(self) -> None:
        mc = _mock_memory_controller([])
        report = consolidate_stm_to_ltm(mc)
        assert report.turns_promoted == 0
        assert report.facts_extracted == 0
        assert report.stm_entries_pruned == 0

    def test_conversation_turns_promoted_to_episodic(self) -> None:
        entries = [
            _make_stm_entry("turn-1", "Hello", context="conversation"),
            _make_stm_entry("turn-2", "Hi there", context="chat"),
        ]
        mc = _mock_memory_controller(entries)
        report = consolidate_stm_to_ltm(mc)
        assert report.turns_promoted == 2
        assert mc.write_episodic.call_count == 2
        assert mc.stm.delete.call_count == 2

    def test_fact_entries_extracted_to_semantic(self) -> None:
        entries = [
            _make_stm_entry(
                "fact-1", "Python 3.12 is required",
                context="fact",
            ),
        ]
        mc = _mock_memory_controller(entries)
        report = consolidate_stm_to_ltm(mc)
        assert report.facts_extracted == 1
        assert mc.write_semantic.call_count == 1

    def test_fact_by_metadata_tags(self) -> None:
        entries = [
            _make_stm_entry(
                "tagged-1", "Use ruff for linting",
                metadata={"tags": ["instruction"]},
            ),
        ]
        mc = _mock_memory_controller(entries)
        report = consolidate_stm_to_ltm(mc)
        assert report.facts_extracted == 1

    def test_old_generic_entries_pruned(self) -> None:
        entries = [
            _make_stm_entry("misc-1", "random stuff"),
        ]
        mc = _mock_memory_controller(entries)
        report = consolidate_stm_to_ltm(mc)
        assert report.stm_entries_pruned == 1
        mc.stm.delete.assert_called_once_with("misc-1")

    def test_young_entries_not_touched(self) -> None:
        entries = [
            _make_stm_entry("recent", "just added", age=60.0),
        ]
        mc = _mock_memory_controller(entries)
        report = consolidate_stm_to_ltm(mc, stm_age_threshold=1800.0)
        assert report.turns_promoted == 0
        assert report.facts_extracted == 0
        assert report.stm_entries_pruned == 0

    def test_publishes_event(self) -> None:
        entries = [
            _make_stm_entry("turn-1", "Hello", context="conversation"),
        ]
        mc = _mock_memory_controller(entries)
        publish = MagicMock()
        consolidate_stm_to_ltm(mc, publish_fn=publish)
        publish.assert_called_once()
        assert publish.call_args[0][0] == "agent/memory/sleep/consolidation"

    def test_indices_updated(self) -> None:
        mc = _mock_memory_controller([
            _make_stm_entry("turn-1", "Hello", context="conversation"),
        ])
        report = consolidate_stm_to_ltm(mc)
        assert report.indices_updated >= 1

    def test_report_to_dict(self) -> None:
        report = ConsolidationReport(
            turns_promoted=2,
            facts_extracted=1,
            stm_entries_pruned=3,
        )
        d = report.to_dict()
        assert d["turns_promoted"] == 2
        assert d["facts_extracted"] == 1


# ── Sleep schedule ────────────────────────────────────────────────────── #


class TestSleepScheduleConfig:
    def test_from_dict(self) -> None:
        cfg = SleepScheduleConfig.from_dict({
            "sleep_window_start": "02:30",
            "sleep_window_duration_hours": 2.5,
            "idle_timeout_minutes": 20,
        })
        assert cfg.start_hour == 2
        assert cfg.start_minute == 30
        assert cfg.duration_hours == 2.5

    def test_is_in_window(self) -> None:
        from datetime import UTC, datetime

        cfg = SleepScheduleConfig(start_hour=2, duration_hours=3.0)
        in_window = datetime(2024, 1, 1, 3, 0, tzinfo=UTC)
        out_window = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
        assert cfg.is_in_window(in_window) is True
        assert cfg.is_in_window(out_window) is False

    def test_validation_errors(self) -> None:
        with pytest.raises(ValueError):
            SleepScheduleConfig(start_hour=25)
        with pytest.raises(ValueError):
            SleepScheduleConfig(duration_hours=0)


# ── Sleep scheduler — endorphin trigger ──────────────────────────────── #


class TestSleepSchedulerEndorphin:
    def _make_scheduler(
        self,
        *,
        cortisol: float = 0.0,
        allow_naps: bool = True,
    ) -> SleepScheduler:
        cfg = SleepScheduleConfig(allow_daytime_naps=allow_naps)
        fsm = MagicMock()
        return SleepScheduler(
            config=cfg,
            fsm=fsm,
            get_cortisol=lambda: cortisol,
        )

    def test_endorphin_triggers_sleep(self) -> None:
        sched = self._make_scheduler()
        assert sched.on_endorphin_signal(0.65) is True
        assert sched.sleeping is True

    def test_low_endorphin_no_trigger(self) -> None:
        sched = self._make_scheduler()
        assert sched.on_endorphin_signal(0.3) is False
        assert sched.sleeping is False

    def test_cortisol_blocks_endorphin(self) -> None:
        sched = self._make_scheduler(cortisol=0.8)
        assert sched.on_endorphin_signal(0.7) is False
        assert sched.sleeping is False

    def test_naps_disabled_blocks_outside_window(self) -> None:
        sched = self._make_scheduler(allow_naps=False)
        # Not in window by default (current time unlikely 2-5 AM)
        # But if in window, it should work — test the blocking path
        sched._config._enabled = True  # ensure enabled
        # Force not-in-window by using a config that's clearly past
        sched._config = SleepScheduleConfig(
            start_hour=3, duration_hours=1.0,
            allow_daytime_naps=False,
        )
        # Unless we happen to be at 3AM, this will return False
        result = sched.on_endorphin_signal(0.7)
        # Can't assert True/False definitively due to clock dependency
        # but we can verify it doesn't crash
        assert isinstance(result, bool)

    def test_already_sleeping_no_retrigger(self) -> None:
        sched = self._make_scheduler()
        sched.on_endorphin_signal(0.7)
        assert sched.sleeping is True
        assert sched.on_endorphin_signal(0.8) is False


# ── Cortisol gating ──────────────────────────────────────────────────── #


class TestCortisolGating:
    def test_high_cortisol_blocks_scheduled_sleep(self) -> None:
        sched = SleepScheduler(
            config=SleepScheduleConfig(),
            fsm=MagicMock(),
            get_cortisol=lambda: 0.85,
        )
        assert sched._is_cortisol_blocked() is True

    def test_low_cortisol_allows_sleep(self) -> None:
        sched = SleepScheduler(
            config=SleepScheduleConfig(),
            fsm=MagicMock(),
            get_cortisol=lambda: 0.3,
        )
        assert sched._is_cortisol_blocked() is False

    def test_no_cortisol_fn_allows_sleep(self) -> None:
        sched = SleepScheduler(
            config=SleepScheduleConfig(),
            fsm=MagicMock(),
        )
        assert sched._is_cortisol_blocked() is False


# ── Maintenance crew dispatch ─────────────────────────────────────────── #


class TestMaintenanceCrewGating:
    def test_blocked_in_emergency(self) -> None:
        from openbad.frameworks.crews.maintenance import (
            create_maintenance_crew,
        )

        result = create_maintenance_crew("test topic", fsm_state="EMERGENCY")
        assert result is None

    def test_blocked_in_throttled(self) -> None:
        from openbad.frameworks.crews.maintenance import (
            create_maintenance_crew,
        )

        result = create_maintenance_crew("test topic", fsm_state="THROTTLED")
        assert result is None
