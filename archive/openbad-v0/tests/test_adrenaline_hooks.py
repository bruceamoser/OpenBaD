"""Tests for adrenaline hooks."""

from __future__ import annotations

import pytest

from openbad.endocrine.controller import EndocrineController
from openbad.endocrine.hooks.adrenaline import AdrenalineEvent, AdrenalineHooks


@pytest.fixture
def controller() -> EndocrineController:
    return EndocrineController()


@pytest.fixture
def hooks(controller: EndocrineController) -> AdrenalineHooks:
    return AdrenalineHooks(controller)


class TestAdrenalineHooks:
    def test_on_high_surprise(
        self, hooks: AdrenalineHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_high_surprise(surprise_level=0.8)
        base = controller._config.adrenaline.increment  # noqa: SLF001
        assert level == pytest.approx(base * 1.8)

    def test_on_high_surprise_zero(
        self, hooks: AdrenalineHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_high_surprise(surprise_level=0.0)
        base = controller._config.adrenaline.increment  # noqa: SLF001
        assert level == pytest.approx(base)

    def test_on_security_threat(
        self, hooks: AdrenalineHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_security_threat(severity=2.0)
        base = controller._config.adrenaline.increment  # noqa: SLF001
        assert level == pytest.approx(base * 2.0)

    def test_on_security_threat_capped(
        self, hooks: AdrenalineHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_security_threat(severity=10.0)
        base = controller._config.adrenaline.increment  # noqa: SLF001
        # capped at 3.0
        assert level == pytest.approx(base * 3.0)

    def test_on_resource_critical_high(
        self, hooks: AdrenalineHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_resource_critical(utilization=0.95)
        base = controller._config.adrenaline.increment  # noqa: SLF001
        assert level == pytest.approx(base * 1.5)

    def test_on_resource_critical_normal(
        self, hooks: AdrenalineHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_resource_critical(utilization=0.5)
        base = controller._config.adrenaline.increment  # noqa: SLF001
        assert level == pytest.approx(base)

    def test_on_deadline_pressure(
        self, hooks: AdrenalineHooks, controller: EndocrineController,
    ) -> None:
        level = hooks.on_deadline_pressure()
        assert level > 0.0

    def test_fire_generic(
        self, hooks: AdrenalineHooks, controller: EndocrineController,
    ) -> None:
        event = AdrenalineEvent(source="test", reason="manual", intensity=2.0)
        level = hooks.fire(event)
        base = controller._config.adrenaline.increment  # noqa: SLF001
        assert level == pytest.approx(base * 2.0)

    def test_level_clamped(
        self, hooks: AdrenalineHooks, controller: EndocrineController,
    ) -> None:
        for _ in range(20):
            hooks.on_security_threat(severity=3.0)
        assert controller.level("adrenaline") <= 1.0
