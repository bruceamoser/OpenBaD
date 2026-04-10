"""Tests for cortisol hooks."""

from __future__ import annotations

import pytest

from openbad.endocrine.controller import EndocrineController
from openbad.endocrine.hooks.cortisol import CortisolEvent, CortisolHooks


@pytest.fixture
def controller() -> EndocrineController:
    return EndocrineController()


@pytest.fixture
def hooks(controller: EndocrineController) -> CortisolHooks:
    return CortisolHooks(controller)


class TestCortisolHooks:
    def test_on_sustained_load_short(
        self, hooks: CortisolHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_sustained_load(duration_seconds=30)
        base = controller._config.cortisol.increment  # noqa: SLF001
        assert level == pytest.approx(base)

    def test_on_sustained_load_long(
        self, hooks: CortisolHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_sustained_load(duration_seconds=150)
        base = controller._config.cortisol.increment  # noqa: SLF001
        assert level == pytest.approx(base * (1.0 + 150.0 / 300.0))

    def test_on_repeated_failure(
        self, hooks: CortisolHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_repeated_failure(failure_count=2)
        base = controller._config.cortisol.increment  # noqa: SLF001
        assert level == pytest.approx(base * 2.0)

    def test_on_repeated_failure_capped(
        self, hooks: CortisolHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_repeated_failure(failure_count=10)
        base = controller._config.cortisol.increment  # noqa: SLF001
        assert level == pytest.approx(base * 3.0)

    def test_on_persistent_surprise(
        self, hooks: CortisolHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_persistent_surprise(surprise_level=0.7)
        base = controller._config.cortisol.increment  # noqa: SLF001
        assert level == pytest.approx(base * 1.7)

    def test_on_error_accumulation(
        self, hooks: CortisolHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_error_accumulation(error_count=2)
        base = controller._config.cortisol.increment  # noqa: SLF001
        assert level == pytest.approx(base * 2.0)

    def test_fire_generic(
        self, hooks: CortisolHooks, controller: EndocrineController,
    ) -> None:
        event = CortisolEvent(source="test", reason="stress", intensity=2.0)
        level = hooks.fire(event)
        base = controller._config.cortisol.increment  # noqa: SLF001
        assert level == pytest.approx(base * 2.0)

    def test_level_clamped(
        self, hooks: CortisolHooks, controller: EndocrineController,
    ) -> None:
        for _ in range(20):
            hooks.on_repeated_failure(failure_count=3)
        assert controller.level("cortisol") <= 1.0
