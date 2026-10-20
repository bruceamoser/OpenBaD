"""Tests for the exploration budget."""

from __future__ import annotations

from openbad.active_inference.budget import ExplorationBudget


class TestExplorationBudget:
    def test_initial_remaining(self) -> None:
        b = ExplorationBudget(daily_limit=100)
        assert b.remaining == 100
        assert b.daily_limit == 100

    def test_spend_deducts(self) -> None:
        b = ExplorationBudget(daily_limit=10, cooldown_seconds=0)
        assert b.spend(cost=3, now=1.0)
        assert b.remaining == 7

    def test_spend_fails_insufficient(self) -> None:
        b = ExplorationBudget(daily_limit=2, cooldown_seconds=0)
        b.spend(cost=2, now=1.0)
        assert not b.can_spend(cost=1, now=2.0)

    def test_cooldown_blocks(self) -> None:
        b = ExplorationBudget(daily_limit=100, cooldown_seconds=10)
        b.spend(cost=1, now=0.0)
        assert not b.can_spend(cost=1, now=5.0)  # 5 < 10
        assert b.can_spend(cost=1, now=10.0)  # 10 >= 10

    def test_reset(self) -> None:
        b = ExplorationBudget(daily_limit=10, cooldown_seconds=0)
        b.spend(cost=10, now=1.0)
        assert b.remaining == 0
        b.reset()
        assert b.remaining == 10

    def test_reset_with_new_limit(self) -> None:
        b = ExplorationBudget(daily_limit=10, cooldown_seconds=0)
        b.spend(cost=5, now=1.0)
        b.reset(daily_limit=20)
        assert b.remaining == 20
        assert b.daily_limit == 20

    def test_can_spend_checks_cost_and_cooldown(self) -> None:
        b = ExplorationBudget(daily_limit=5, cooldown_seconds=1)
        assert b.can_spend(cost=5, now=0.0)
        b.spend(cost=1, now=0.0)
        assert not b.can_spend(cost=1, now=0.5)  # cooldown
        assert b.can_spend(cost=4, now=1.0)
        assert not b.can_spend(cost=5, now=1.0)  # over budget
