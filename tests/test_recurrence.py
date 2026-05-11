"""Tests for recurrence rule parsing and next-due computation."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from openbad.tasks.recurrence import (
    RecurrenceError,
    compute_next_due,
    validate_recurrence_rule,
)


class TestValidateRecurrenceRule:
    def test_valid_daily(self) -> None:
        validate_recurrence_rule("daily|18:00|America/New_York")

    def test_valid_daily_alias(self) -> None:
        validate_recurrence_rule("daily|06:30|US/Eastern")

    def test_valid_weekly(self) -> None:
        validate_recurrence_rule("weekly|mon|09:00|America/Chicago")

    def test_valid_interval(self) -> None:
        validate_recurrence_rule("interval|3600s")

    def test_invalid_empty(self) -> None:
        with pytest.raises(RecurrenceError, match="Unknown recurrence type"):
            validate_recurrence_rule("")

    def test_invalid_type(self) -> None:
        with pytest.raises(RecurrenceError, match="Unknown recurrence type"):
            validate_recurrence_rule("monthly|1|09:00|UTC")

    def test_daily_wrong_parts(self) -> None:
        with pytest.raises(RecurrenceError, match="3 parts"):
            validate_recurrence_rule("daily|18:00")

    def test_daily_bad_time(self) -> None:
        with pytest.raises(RecurrenceError, match="out of range"):
            validate_recurrence_rule("daily|25:00|UTC")

    def test_daily_bad_tz(self) -> None:
        with pytest.raises(RecurrenceError, match="Unknown timezone"):
            validate_recurrence_rule("daily|18:00|Mars/Olympus")

    def test_weekly_wrong_parts(self) -> None:
        with pytest.raises(RecurrenceError, match="4 parts"):
            validate_recurrence_rule("weekly|mon|09:00")

    def test_weekly_bad_dow(self) -> None:
        with pytest.raises(RecurrenceError, match="Invalid day of week"):
            validate_recurrence_rule("weekly|foo|09:00|UTC")

    def test_interval_bad_format(self) -> None:
        with pytest.raises(RecurrenceError, match="Invalid interval"):
            validate_recurrence_rule("interval|abc")

    def test_interval_zero(self) -> None:
        with pytest.raises(RecurrenceError, match="at least 1"):
            validate_recurrence_rule("interval|0s")

    def test_time_out_of_range(self) -> None:
        with pytest.raises(RecurrenceError, match="out of range"):
            validate_recurrence_rule("daily|12:60|UTC")


class TestComputeNextDue:
    def test_daily_future_today(self) -> None:
        # Set "now" to 10am UTC, rule is daily at 18:00 UTC → same day 18:00
        after = datetime(2026, 5, 11, 10, 0, 0, tzinfo=UTC)
        ts = compute_next_due("daily|18:00|UTC", after)
        result = datetime.fromtimestamp(ts, tz=UTC)
        assert result.hour == 18
        assert result.minute == 0
        assert result.day == 11

    def test_daily_past_today_rolls_to_tomorrow(self) -> None:
        after = datetime(2026, 5, 11, 20, 0, 0, tzinfo=UTC)
        ts = compute_next_due("daily|18:00|UTC", after)
        result = datetime.fromtimestamp(ts, tz=UTC)
        assert result.day == 12
        assert result.hour == 18

    def test_daily_with_timezone(self) -> None:
        # 2pm ET = 18:00 UTC. If after is 1pm ET, next is today 2pm ET.
        tz = ZoneInfo("America/New_York")
        after = datetime(2026, 5, 11, 13, 0, 0, tzinfo=tz)
        ts = compute_next_due("daily|14:00|America/New_York", after)
        result = datetime.fromtimestamp(ts, tz=tz)
        assert result.hour == 14
        assert result.day == 11

    def test_daily_alias_timezone(self) -> None:
        tz = ZoneInfo("America/New_York")
        after = datetime(2026, 5, 11, 13, 0, 0, tzinfo=tz)
        ts = compute_next_due("daily|14:00|US/Eastern", after)
        result = datetime.fromtimestamp(ts, tz=tz)
        assert result.hour == 14

    def test_weekly_same_day_future(self) -> None:
        # May 11, 2026 is a Monday
        after = datetime(2026, 5, 11, 8, 0, 0, tzinfo=UTC)
        ts = compute_next_due("weekly|mon|09:00|UTC", after)
        result = datetime.fromtimestamp(ts, tz=UTC)
        assert result.weekday() == 0
        assert result.day == 11
        assert result.hour == 9

    def test_weekly_same_day_past(self) -> None:
        after = datetime(2026, 5, 11, 10, 0, 0, tzinfo=UTC)
        ts = compute_next_due("weekly|mon|09:00|UTC", after)
        result = datetime.fromtimestamp(ts, tz=UTC)
        assert result.weekday() == 0
        assert result.day == 18  # next Monday

    def test_weekly_different_day(self) -> None:
        # Monday, want Friday
        after = datetime(2026, 5, 11, 10, 0, 0, tzinfo=UTC)
        ts = compute_next_due("weekly|fri|09:00|UTC", after)
        result = datetime.fromtimestamp(ts, tz=UTC)
        assert result.weekday() == 4
        assert result.day == 15

    def test_interval(self) -> None:
        after = datetime(2026, 5, 11, 10, 0, 0, tzinfo=UTC)
        ts = compute_next_due("interval|3600s", after)
        result = datetime.fromtimestamp(ts, tz=UTC)
        assert result == after + timedelta(seconds=3600)

    def test_interval_small(self) -> None:
        after = datetime(2026, 5, 11, 10, 0, 0, tzinfo=UTC)
        ts = compute_next_due("interval|60s", after)
        expected = (after + timedelta(seconds=60)).timestamp()
        assert abs(ts - expected) < 0.01

    def test_defaults_to_now(self) -> None:
        before = time.time()
        ts = compute_next_due("interval|10s")
        after = time.time()
        assert ts >= before + 10
        assert ts <= after + 10


class TestServiceRecurrence:
    """Test TaskService integration with recurrence rules."""

    @pytest.fixture()
    def svc(self, tmp_path):

        from openbad.state.db import initialize_state_db
        from openbad.tasks.service import TaskService

        db = tmp_path / "test.db"
        conn = initialize_state_db(db)
        return TaskService(conn)

    def test_create_task_with_recurrence(self, svc) -> None:
        task = svc.create_task(
            "Daily check",
            recurrence_rule="daily|18:00|UTC",
        )
        assert task.recurrence_rule == "daily|18:00|UTC"
        assert task.due_at is not None
        assert task.due_at > time.time() - 1

    def test_create_task_with_recurrence_and_explicit_due(self, svc) -> None:
        explicit = time.time() + 86400
        task = svc.create_task(
            "Test",
            recurrence_rule="daily|18:00|UTC",
            due_at=explicit,
        )
        assert task.due_at == explicit

    def test_create_task_invalid_recurrence_raises(self, svc) -> None:
        with pytest.raises(RecurrenceError):
            svc.create_task("Bad", recurrence_rule="bogus|rule")

    def test_complete_task_spawns_next(self, svc) -> None:
        from openbad.tasks.models import TaskStatus

        task = svc.create_task(
            "Recurring",
            description="test desc",
            owner="user",
            recurrence_rule="interval|60s",
        )
        original_due = task.due_at
        svc.transition_task(task.task_id, TaskStatus.RUNNING)
        completed = svc.complete_task(task.task_id)
        assert completed.status.value == "done"

        # Find the spawned task
        pending = svc.list_tasks()
        spawned = [
            t for t in pending
            if t.parent_task_id == task.task_id
        ]
        assert len(spawned) == 1
        assert spawned[0].title == "Recurring"
        assert spawned[0].description == "test desc"
        assert spawned[0].recurrence_rule == "interval|60s"
        assert spawned[0].due_at is not None
        assert spawned[0].due_at > original_due

    def test_complete_non_recurring_no_spawn(self, svc) -> None:
        from openbad.tasks.models import TaskStatus

        task = svc.create_task("One-off")
        svc.transition_task(task.task_id, TaskStatus.RUNNING)
        svc.complete_task(task.task_id)
        pending = svc.list_tasks()
        spawned = [t for t in pending if t.parent_task_id == task.task_id]
        assert len(spawned) == 0
