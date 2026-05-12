"""Tests for immune interceptor and plugin feedback loop."""

from __future__ import annotations

import time

import pytest

from openbad.active_inference.immune_interceptor import (
    _HABITUATION_THRESHOLD,
    _VIGILANCE_DECAY_SECONDS,
    ImmuneInterceptor,
)
from openbad.endocrine.controller import EndocrineController


@pytest.fixture()
def endocrine() -> EndocrineController:
    return EndocrineController()


@pytest.fixture()
def interceptor(endocrine: EndocrineController) -> ImmuneInterceptor:
    return ImmuneInterceptor(endocrine=endocrine)


class TestThreatDetection:
    """Tests for immune threat scanning."""

    def test_clean_observation_not_quarantined(
        self, interceptor: ImmuneInterceptor
    ) -> None:
        result = interceptor.intercept(
            source_id="test", surprise=0.3, errors={"metric": 0.2}
        )
        assert result.quarantined is False

    def test_threat_pattern_triggers_quarantine(
        self, interceptor: ImmuneInterceptor
    ) -> None:
        result = interceptor.intercept(
            source_id="test",
            surprise=0.5,
            errors={},
            raw_data={"message": "authentication failure detected"},
        )
        assert result.quarantined is True
        assert "authentication failure" in result.threat_indicators

    def test_multiple_threat_patterns(
        self, interceptor: ImmuneInterceptor
    ) -> None:
        result = interceptor.intercept(
            source_id="test",
            surprise=0.5,
            errors={},
            raw_data="unauthorized access with brute force",
        )
        assert result.quarantined is True
        assert len(result.threat_indicators) == 2

    def test_threat_triggers_adrenaline(
        self,
        interceptor: ImmuneInterceptor,
        endocrine: EndocrineController,
    ) -> None:
        interceptor.intercept(
            source_id="test",
            surprise=0.5,
            errors={},
            raw_data="kernel panic detected",
        )
        assert endocrine.level("adrenaline") > 0.0


class TestNoveltyDopamine:
    """Tests for dopamine signaling on novel observations."""

    def test_high_surprise_triggers_dopamine(
        self,
        interceptor: ImmuneInterceptor,
        endocrine: EndocrineController,
    ) -> None:
        interceptor.intercept(
            source_id="test", surprise=0.6, errors={"metric": 0.4}
        )
        assert endocrine.level("dopamine") > 0.0

    def test_low_surprise_no_dopamine(
        self,
        interceptor: ImmuneInterceptor,
        endocrine: EndocrineController,
    ) -> None:
        interceptor.intercept(
            source_id="test", surprise=0.1, errors={"metric": 0.05}
        )
        assert endocrine.level("dopamine") == 0.0

    def test_zero_surprise_no_dopamine(
        self,
        interceptor: ImmuneInterceptor,
        endocrine: EndocrineController,
    ) -> None:
        interceptor.intercept(
            source_id="test", surprise=0.0, errors={}
        )
        assert endocrine.level("dopamine") == 0.0


class TestThreatMemory:
    """Tests for immune memory and vigilance."""

    def test_threat_creates_memory(self, interceptor: ImmuneInterceptor) -> None:
        interceptor.intercept(
            source_id="bad_source",
            surprise=0.5,
            errors={},
            raw_data="segfault occurred",
        )
        assert interceptor.is_quarantined_source("bad_source") is True

    def test_clean_source_not_quarantined(
        self, interceptor: ImmuneInterceptor
    ) -> None:
        assert interceptor.is_quarantined_source("good_source") is False

    def test_vigilant_source_poll_factor_faster(
        self, interceptor: ImmuneInterceptor
    ) -> None:
        interceptor.intercept(
            source_id="flagged",
            surprise=0.5,
            errors={},
            raw_data="out of memory",
        )
        factor = interceptor.get_poll_factor("flagged")
        assert factor < 1.0  # Poll faster

    def test_vigilance_decays_over_time(
        self, interceptor: ImmuneInterceptor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        interceptor.intercept(
            source_id="old_threat",
            surprise=0.5,
            errors={},
            raw_data="disk full event",
        )
        # Simulate time passing beyond decay
        memory = interceptor._threat_memory["old_threat"]
        monkeypatch.setattr(
            memory, "last_threat_ts", time.monotonic() - _VIGILANCE_DECAY_SECONDS - 1
        )
        assert memory.is_vigilant is False
        assert interceptor.get_poll_factor("old_threat") == 1.0


class TestHabituation:
    """Tests for boring observation habituation."""

    def test_boring_streak_builds_habituation(
        self, interceptor: ImmuneInterceptor
    ) -> None:
        for _ in range(_HABITUATION_THRESHOLD):
            interceptor.intercept(
                source_id="boring", surprise=0.0, errors={}
            )
        factor = interceptor.get_poll_factor("boring")
        assert factor > 1.0  # Poll slower

    def test_novel_observation_resets_habituation(
        self, interceptor: ImmuneInterceptor
    ) -> None:
        # Build up boringness
        for _ in range(_HABITUATION_THRESHOLD - 1):
            interceptor.intercept(
                source_id="mixed", surprise=0.0, errors={}
            )
        # Novel observation resets streak
        interceptor.intercept(
            source_id="mixed", surprise=0.5, errors={"x": 0.3}
        )
        factor = interceptor.get_poll_factor("mixed")
        assert factor == 1.0  # Normal (not habituated)


class TestVigilantSourceHeightenedSensitivity:
    """Tests for heightened sensitivity on flagged sources."""

    def test_flagged_source_high_error_triggers_quarantine(
        self, interceptor: ImmuneInterceptor
    ) -> None:
        # First: flag the source
        interceptor.intercept(
            source_id="suspect",
            surprise=0.5,
            errors={},
            raw_data="connection refused repeatedly",
        )
        # Now high error on flagged source → threat
        result = interceptor.intercept(
            source_id="suspect",
            surprise=0.3,
            errors={"error_rate": 0.9},
        )
        assert result.quarantined is True
        assert any("high_error" in i for i in result.threat_indicators)

    def test_unflagged_source_high_error_not_quarantined(
        self, interceptor: ImmuneInterceptor
    ) -> None:
        # Same high error but source isn't flagged → no quarantine
        result = interceptor.intercept(
            source_id="clean",
            surprise=0.3,
            errors={"error_rate": 0.9},
        )
        assert result.quarantined is False
