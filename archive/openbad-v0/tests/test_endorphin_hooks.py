"""Tests for endorphin hooks."""

from __future__ import annotations

import pytest

from openbad.endocrine.controller import EndocrineController
from openbad.endocrine.hooks.endorphin import EndorphinEvent, EndorphinHooks


@pytest.fixture
def controller() -> EndocrineController:
    return EndocrineController()


@pytest.fixture
def hooks(controller: EndocrineController) -> EndorphinHooks:
    return EndorphinHooks(controller)


class TestEndorphinHooks:
    def test_on_recovery_base(
        self, hooks: EndorphinHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_recovery(stress_level_before=0.0)
        base = controller._config.endorphin.increment  # noqa: SLF001
        assert level == pytest.approx(base)

    def test_on_recovery_scaled(
        self, hooks: EndorphinHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_recovery(stress_level_before=0.8)
        base = controller._config.endorphin.increment  # noqa: SLF001
        assert level == pytest.approx(base * 1.8)

    def test_on_self_heal(
        self, hooks: EndorphinHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_self_heal()
        base = controller._config.endorphin.increment  # noqa: SLF001
        assert level == pytest.approx(base * 1.5)

    def test_on_stability_short(
        self, hooks: EndorphinHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_stability(stable_duration_seconds=30)
        base = controller._config.endorphin.increment  # noqa: SLF001
        assert level == pytest.approx(base)

    def test_on_stability_long(
        self, hooks: EndorphinHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_stability(stable_duration_seconds=300)
        base = controller._config.endorphin.increment  # noqa: SLF001
        assert level == pytest.approx(base * (1.0 + 300.0 / 600.0))

    def test_on_maintenance_complete(
        self, hooks: EndorphinHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_maintenance_complete()
        assert level > 0.0

    def test_fire_generic(
        self, hooks: EndorphinHooks, controller: EndocrineController,
    ) -> None:
        event = EndorphinEvent(source="test", reason="heal", intensity=2.0)
        level = hooks.fire(event)
        base = controller._config.endorphin.increment  # noqa: SLF001
        assert level == pytest.approx(base * 2.0)

    def test_level_clamped(
        self, hooks: EndorphinHooks, controller: EndocrineController,
    ) -> None:
        for _ in range(20):
            hooks.on_self_heal()
        assert controller.level("endorphin") <= 1.0
