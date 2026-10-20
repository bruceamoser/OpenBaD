"""Tests for openbad.interoception.token_budget — persistent token budget tracker."""

from __future__ import annotations

import time

import pytest

from openbad.interoception.token_budget import TokenBudget


@pytest.fixture
def budget(tmp_path):
    """Create a TokenBudget with an in-temp-dir SQLite DB."""
    db = tmp_path / "test_budget.db"
    b = TokenBudget(db_path=db, daily_ceiling=10_000, hourly_ceiling=1_000)
    yield b
    b.close()


# ── Recording & cumulative tracking ───────────────────────────────


class TestRecord:
    def test_single_record(self, budget: TokenBudget):
        budget.record(provider="openai", model="gpt-4o", task_id="t-1", tokens=500)
        assert budget.daily_used() == 500

    def test_multiple_records_accumulate(self, budget: TokenBudget):
        budget.record(provider="openai", model="gpt-4o", task_id="t-1", tokens=200)
        budget.record(provider="openai", model="gpt-4o", task_id="t-2", tokens=300)
        assert budget.daily_used() == 500

    def test_different_providers(self, budget: TokenBudget):
        budget.record(provider="openai", model="gpt-4o", task_id="t-1", tokens=100)
        budget.record(provider="anthropic", model="claude", task_id="t-2", tokens=200)
        assert budget.daily_used() == 300


# ── Budget ceilings & status ──────────────────────────────────────


class TestBudgetStatus:
    def test_remaining_100_when_empty(self, budget: TokenBudget):
        status = budget.status()
        assert status.daily_remaining_pct == 100.0
        assert status.hourly_remaining_pct == 100.0

    def test_remaining_decreases(self, budget: TokenBudget):
        budget.record(provider="openai", model="gpt-4o", task_id="t-1", tokens=5_000)
        status = budget.status()
        assert status.daily_remaining_pct == 50.0
        assert status.daily_used == 5_000
        assert status.daily_ceiling == 10_000

    def test_remaining_zero_when_over(self, budget: TokenBudget):
        budget.record(provider="openai", model="gpt-4o", task_id="t-1", tokens=15_000)
        status = budget.status()
        assert status.daily_remaining_pct == 0.0

    def test_hourly_ceiling(self, budget: TokenBudget):
        budget.record(provider="openai", model="gpt-4o", task_id="t-1", tokens=500)
        status = budget.status()
        assert status.hourly_remaining_pct == 50.0
        assert status.hourly_used == 500
        assert status.hourly_ceiling == 1_000


# ── Cost per action ───────────────────────────────────────────────


class TestCostPerAction:
    def test_no_actions(self, budget: TokenBudget):
        assert budget.cost_per_action_avg() == 0.0

    def test_average(self, budget: TokenBudget):
        budget.record(provider="openai", model="gpt-4o", task_id="t-1", tokens=100)
        budget.record(provider="openai", model="gpt-4o", task_id="t-2", tokens=300)
        assert budget.cost_per_action_avg() == 200.0


# ── Per-model and per-task grouping ───────────────────────────────


class TestGrouping:
    def test_usage_by_model(self, budget: TokenBudget):
        budget.record(provider="openai", model="gpt-4o", task_id="t-1", tokens=100)
        budget.record(provider="openai", model="gpt-4o-mini", task_id="t-2", tokens=50)
        budget.record(provider="anthropic", model="claude", task_id="t-3", tokens=200)
        by_model = budget.usage_by_model()
        assert by_model["openai/gpt-4o"] == 100
        assert by_model["openai/gpt-4o-mini"] == 50
        assert by_model["anthropic/claude"] == 200

    def test_usage_by_task(self, budget: TokenBudget):
        budget.record(provider="openai", model="gpt-4o", task_id="task-a", tokens=100)
        budget.record(provider="openai", model="gpt-4o", task_id="task-a", tokens=200)
        budget.record(provider="openai", model="gpt-4o", task_id="task-b", tokens=50)
        by_task = budget.usage_by_task()
        assert by_task["task-a"] == 300
        assert by_task["task-b"] == 50


# ── Persistence across "restarts" ─────────────────────────────────


class TestPersistence:
    def test_survives_restart(self, tmp_path):
        db = tmp_path / "persist.db"

        # First "process"
        b1 = TokenBudget(db_path=db, daily_ceiling=10_000, hourly_ceiling=1_000)
        b1.record(provider="openai", model="gpt-4o", task_id="t-1", tokens=750)
        b1.close()

        # Second "process" — re-open same DB
        b2 = TokenBudget(db_path=db, daily_ceiling=10_000, hourly_ceiling=1_000)
        assert b2.daily_used() == 750
        status = b2.status()
        assert status.total_used == 750
        assert status.cost_per_action_avg == 750.0
        b2.close()


# ── Old records roll off windowed queries ─────────────────────────


class TestWindowedQueries:
    def test_old_records_excluded_from_daily(self, budget: TokenBudget):
        # Record as if 25 hours ago
        old_ts = time.time() - 90_000  # 25 hours
        budget.record(
            provider="openai", model="gpt-4o", task_id="t-old", tokens=999, timestamp=old_ts
        )
        budget.record(provider="openai", model="gpt-4o", task_id="t-new", tokens=100)
        assert budget.daily_used() == 100

    def test_old_records_excluded_from_hourly(self, budget: TokenBudget):
        old_ts = time.time() - 3700  # ~1h 1min ago
        budget.record(
            provider="openai", model="gpt-4o", task_id="t-old", tokens=999, timestamp=old_ts
        )
        budget.record(provider="openai", model="gpt-4o", task_id="t-new", tokens=50)
        assert budget.hourly_used() == 50

    def test_total_includes_all(self, budget: TokenBudget):
        old_ts = time.time() - 90_000
        budget.record(
            provider="openai", model="gpt-4o", task_id="t-old", tokens=500, timestamp=old_ts
        )
        budget.record(provider="openai", model="gpt-4o", task_id="t-new", tokens=100)
        status = budget.status()
        assert status.total_used == 600
