"""Tests for the L2HR mapper."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from openbad.endocrine.l2hr import (
    HormoneAdjustment,
    L2HRConfig,
    L2HRMapper,
)


class TestHormoneAdjustment:
    def test_to_dict(self) -> None:
        adj = HormoneAdjustment(dopamine=0.1, cortisol=0.05)
        d = adj.to_dict()
        assert d["dopamine"] == pytest.approx(0.1)
        assert d["adrenaline"] == pytest.approx(0.0)

    def test_is_zero(self) -> None:
        assert HormoneAdjustment().is_zero()
        assert not HormoneAdjustment(dopamine=0.01).is_zero()


class TestKeywordClassification:
    def test_success_keywords(self) -> None:
        mapper = L2HRMapper()
        assert mapper.classify("Successfully resolved the user's question") == "success"
        assert mapper.classify("Task completed without errors") == "success"

    def test_failure_keywords(self) -> None:
        mapper = L2HRMapper()
        assert mapper.classify("Failed after 3 retries") == "failure"
        assert mapper.classify("Operation timed out with error") == "failure"

    def test_threat_keywords(self) -> None:
        mapper = L2HRMapper()
        assert mapper.classify("Detected and quarantined a prompt injection") == "threat"

    def test_urgency_keywords(self) -> None:
        mapper = L2HRMapper()
        assert mapper.classify("User escalated urgency") == "urgency"

    def test_recovery_keywords(self) -> None:
        mapper = L2HRMapper()
        assert mapper.classify("System recovered from overload") == "recovery"

    def test_no_match_returns_none(self) -> None:
        mapper = L2HRMapper()
        assert mapper.classify("The weather is nice today") is None


class TestAdjustmentMapping:
    def test_success_adjustment(self) -> None:
        mapper = L2HRMapper()
        adj = mapper.map("Successfully resolved the user's question")
        assert adj.dopamine == pytest.approx(0.10)
        assert adj.adrenaline == pytest.approx(0.0)

    def test_failure_adjustment(self) -> None:
        mapper = L2HRMapper()
        adj = mapper.map("Failed after 3 retries")
        assert adj.dopamine == pytest.approx(-0.05)
        assert adj.cortisol == pytest.approx(0.10)

    def test_threat_adjustment(self) -> None:
        mapper = L2HRMapper()
        adj = mapper.map("Detected and quarantined a prompt injection")
        assert adj.dopamine == pytest.approx(0.10)
        assert adj.endorphin == pytest.approx(0.10)

    def test_urgency_adjustment(self) -> None:
        mapper = L2HRMapper()
        adj = mapper.map("User escalated urgency")
        assert adj.adrenaline == pytest.approx(0.20)

    def test_no_match_returns_zero(self) -> None:
        mapper = L2HRMapper()
        adj = mapper.map("Nothing special here")
        assert adj.is_zero()

    def test_adjustments_smaller_than_direct_hooks(self) -> None:
        """L2HR adjustments should be smaller than direct hook increments (0.15+)."""
        mapper = L2HRMapper()
        for _cat, adj in mapper._config.adjustments.items():
            for h in ("dopamine", "adrenaline", "cortisol", "endorphin"):
                val = getattr(adj, h)
                # All values should be < 0.25 (max direct hook increment).
                assert abs(val) <= 0.20


class TestMapAll:
    def test_single_category(self) -> None:
        mapper = L2HRMapper()
        results = mapper.map_all("Task completed successfully")
        assert len(results) == 1
        assert results[0][0] == "success"

    def test_multi_category(self) -> None:
        mapper = L2HRMapper()
        text = "Detected injection attack and quarantined it successfully"
        results = mapper.map_all(text)
        categories = {r[0] for r in results}
        assert "threat" in categories
        assert "success" in categories


class TestCustomClassifyFn:
    def test_custom_fn_overrides_keywords(self) -> None:
        def custom_fn(text: str) -> str | None:
            if "special" in text.lower():
                return "success"
            return None

        mapper = L2HRMapper(classify_fn=custom_fn)
        assert mapper.classify("This is special") == "success"

    def test_custom_fn_fallback_to_keywords(self) -> None:
        def custom_fn(_text: str) -> str | None:
            return None

        mapper = L2HRMapper(classify_fn=custom_fn)
        assert mapper.classify("Failed after 3 retries") == "failure"


class TestYAMLConfig:
    def test_override_keywords(self, tmp_path: Path) -> None:
        yaml_content = textwrap.dedent("""\
            l2hr:
              keywords:
                success:
                  - nailed
                  - crushed
              adjustments:
                success:
                  dopamine: 0.20
        """)
        yml = tmp_path / "l2hr.yaml"
        yml.write_text(yaml_content, encoding="utf-8")

        config = L2HRConfig.from_yaml(yml)
        mapper = L2HRMapper(config=config)
        assert mapper.classify("We crushed it") == "success"
        adj = mapper.map("We nailed it")
        assert adj.dopamine == pytest.approx(0.20)

    def test_default_categories_preserved(self, tmp_path: Path) -> None:
        yaml_content = textwrap.dedent("""\
            l2hr:
              keywords:
                custom_category:
                  - wow
              adjustments:
                custom_category:
                  dopamine: 0.05
        """)
        yml = tmp_path / "l2hr.yaml"
        yml.write_text(yaml_content, encoding="utf-8")

        config = L2HRConfig.from_yaml(yml)
        mapper = L2HRMapper(config=config)
        # Default categories should still work.
        assert mapper.classify("Failed retries") == "failure"
        # Custom category should work too.
        assert mapper.classify("wow this is great") == "custom_category"
