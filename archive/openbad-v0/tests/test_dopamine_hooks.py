"""Tests for dopamine hooks."""

from __future__ import annotations

import pytest

from openbad.endocrine.controller import EndocrineController
from openbad.endocrine.hooks.dopamine import DopamineEvent, DopamineHooks


@pytest.fixture
def controller() -> EndocrineController:
    return EndocrineController()


@pytest.fixture
def hooks(controller: EndocrineController) -> DopamineHooks:
    return DopamineHooks(controller)


class TestDopamineHooks:
    def test_on_task_complete(
        self, hooks: DopamineHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_task_complete("task-1")
        assert level > 0.0
        assert controller.level("dopamine") == level

    def test_on_exploration_success_base(
        self, hooks: DopamineHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_exploration_success(surprise_delta=0.0)
        assert level == pytest.approx(
            controller._config.dopamine.increment,  # noqa: SLF001
        )

    def test_on_exploration_success_scaled(
        self, hooks: DopamineHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_exploration_success(surprise_delta=0.5)
        base = controller._config.dopamine.increment  # noqa: SLF001
        # 0.15 * 1.5 = 0.225
        assert level == pytest.approx(base * 1.5)

    def test_on_positive_feedback(
        self, hooks: DopamineHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_positive_feedback()
        base = controller._config.dopamine.increment  # noqa: SLF001
        assert level == pytest.approx(base * 1.5)

    def test_on_learning(
        self, hooks: DopamineHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_learning(improvement=0.3)
        base = controller._config.dopamine.increment  # noqa: SLF001
        assert level == pytest.approx(base * 1.3)

    def test_fire_generic(
        self, hooks: DopamineHooks, controller: EndocrineController,
    ) -> None:
        event = DopamineEvent(source="test", reason="manual", intensity=2.0)
        level = hooks.fire(event)
        base = controller._config.dopamine.increment  # noqa: SLF001
        assert level == pytest.approx(base * 2.0)

    def test_multiple_triggers_additive(
        self, hooks: DopamineHooks, controller: EndocrineController,
    ) -> None:
        hooks.on_task_complete()
        hooks.on_task_complete()
        base = controller._config.dopamine.increment  # noqa: SLF001
        assert controller.level("dopamine") == pytest.approx(base * 2)

    def test_level_clamped_at_one(
        self, hooks: DopamineHooks, controller: EndocrineController,
    ) -> None:
        for _ in range(20):
            hooks.on_task_complete()
        assert controller.level("dopamine") <= 1.0
