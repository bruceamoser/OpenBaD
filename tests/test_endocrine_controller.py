"""Tests for the endocrine controller."""

from __future__ import annotations

import time

import pytest

from openbad.endocrine.config import EndocrineConfig, HormoneConfig
from openbad.endocrine.controller import (
    HORMONES,
    EndocrineController,
    HormoneState,
)

# ------------------------------------------------------------------ #
# HormoneState
# ------------------------------------------------------------------ #


class TestHormoneState:
    def test_defaults_zero(self) -> None:
        s = HormoneState()
        for h in HORMONES:
            assert getattr(s, h) == 0.0

    def test_to_dict(self) -> None:
        s = HormoneState(dopamine=0.3, cortisol=0.7)
        d = s.to_dict()
        assert d["dopamine"] == 0.3
        assert d["cortisol"] == 0.7
        assert d["adrenaline"] == 0.0


# ------------------------------------------------------------------ #
# Trigger / clamping
# ------------------------------------------------------------------ #


class TestTrigger:
    def test_default_increment(self) -> None:
        c = EndocrineController()
        c.trigger("dopamine")
        assert c.level("dopamine") == pytest.approx(0.15)

    def test_custom_amount(self) -> None:
        c = EndocrineController()
        c.trigger("dopamine", 0.4)
        assert c.level("dopamine") == pytest.approx(0.4)

    def test_clamp_upper(self) -> None:
        c = EndocrineController()
        c.trigger("dopamine", 1.5)
        assert c.level("dopamine") == 1.0

    def test_clamp_lower(self) -> None:
        c = EndocrineController()
        c.trigger("dopamine", -0.5)
        assert c.level("dopamine") == 0.0

    def test_additive(self) -> None:
        c = EndocrineController()
        c.trigger("adrenaline", 0.3)
        c.trigger("adrenaline", 0.3)
        assert c.level("adrenaline") == pytest.approx(0.6)

    def test_unknown_hormone_raises(self) -> None:
        c = EndocrineController()
        with pytest.raises(ValueError, match="Unknown hormone"):
            c.trigger("serotonin")


# ------------------------------------------------------------------ #
# Decay
# ------------------------------------------------------------------ #


class TestDecay:
    def test_exact_half_life(self) -> None:
        cfg = EndocrineConfig(
            dopamine=HormoneConfig(half_life_seconds=10.0),
        )
        c = EndocrineController(cfg)
        c.trigger("dopamine", 1.0)
        c.decay(dt=10.0)
        assert c.level("dopamine") == pytest.approx(0.5, abs=1e-4)

    def test_two_half_lives(self) -> None:
        cfg = EndocrineConfig(
            dopamine=HormoneConfig(half_life_seconds=10.0),
        )
        c = EndocrineController(cfg)
        c.trigger("dopamine", 1.0)
        c.decay(dt=20.0)
        assert c.level("dopamine") == pytest.approx(0.25, abs=1e-4)

    def test_snap_to_zero(self) -> None:
        cfg = EndocrineConfig(
            dopamine=HormoneConfig(half_life_seconds=1.0),
        )
        c = EndocrineController(cfg)
        c.trigger("dopamine", 0.01)
        c.decay(dt=100.0)
        assert c.level("dopamine") == 0.0

    def test_zero_dt_no_change(self) -> None:
        c = EndocrineController()
        c.trigger("cortisol", 0.5)
        c.decay(dt=0.0)
        assert c.level("cortisol") == pytest.approx(0.5)

    def test_negative_dt_no_change(self) -> None:
        c = EndocrineController()
        c.trigger("cortisol", 0.5)
        c.decay(dt=-1.0)
        assert c.level("cortisol") == pytest.approx(0.5)

    def test_decay_all_hormones(self) -> None:
        cfg = EndocrineConfig(
            dopamine=HormoneConfig(half_life_seconds=10.0),
            adrenaline=HormoneConfig(half_life_seconds=10.0),
            cortisol=HormoneConfig(half_life_seconds=10.0),
            endorphin=HormoneConfig(half_life_seconds=10.0),
        )
        c = EndocrineController(cfg)
        for h in HORMONES:
            c.trigger(h, 1.0)
        c.decay(dt=10.0)
        for h in HORMONES:
            assert c.level(h) == pytest.approx(0.5, abs=1e-4)


# ------------------------------------------------------------------ #
# Activation / escalation
# ------------------------------------------------------------------ #


class TestThresholds:
    def test_not_active_below_threshold(self) -> None:
        c = EndocrineController()
        c.trigger("dopamine", 0.30)
        assert not c.is_active("dopamine")

    def test_active_above_threshold(self) -> None:
        c = EndocrineController()
        c.trigger("dopamine", 0.55)
        assert c.is_active("dopamine")

    def test_not_escalated_without_config(self) -> None:
        c = EndocrineController()
        c.trigger("dopamine", 1.0)
        assert not c.is_escalated("dopamine")

    def test_escalated(self) -> None:
        c = EndocrineController()
        c.trigger("adrenaline", 0.90)
        assert c.is_escalated("adrenaline")

    def test_cortisol_two_tiers(self) -> None:
        c = EndocrineController()
        c.trigger("cortisol", 0.55)
        assert c.is_active("cortisol")
        assert not c.is_escalated("cortisol")

        c.trigger("cortisol", 0.30)
        assert c.is_escalated("cortisol")


# ------------------------------------------------------------------ #
# Reset
# ------------------------------------------------------------------ #


class TestReset:
    def test_reset_zeroes(self) -> None:
        c = EndocrineController()
        for h in HORMONES:
            c.trigger(h, 0.8)
        c.reset()
        for h in HORMONES:
            assert c.level(h) == 0.0


# ------------------------------------------------------------------ #
# Publishing helpers
# ------------------------------------------------------------------ #


class TestPublish:
    def test_should_publish_on_interval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = EndocrineConfig(publish_interval_seconds=1.0)
        c = EndocrineController(cfg)
        c.mark_published()

        # Advance monotonic clock past publish interval.
        _base = time.monotonic()
        monkeypatch.setattr(time, "monotonic", lambda: _base + 2.0)
        assert c.should_publish()

    def test_should_publish_on_significant_change(self) -> None:
        cfg = EndocrineConfig(
            publish_interval_seconds=999.0,
            significant_change_delta=0.1,
        )
        c = EndocrineController(cfg)
        c.mark_published()
        c.trigger("dopamine", 0.15)
        assert c.should_publish()

    def test_no_publish_when_quiet(self) -> None:
        cfg = EndocrineConfig(
            publish_interval_seconds=999.0,
            significant_change_delta=0.5,
        )
        c = EndocrineController(cfg)
        c.mark_published()
        c.trigger("dopamine", 0.05)
        assert not c.should_publish()


# ------------------------------------------------------------------ #
# get_state
# ------------------------------------------------------------------ #


class TestGetState:
    def test_returns_snapshot(self) -> None:
        c = EndocrineController()
        c.trigger("adrenaline", 0.4)
        s = c.get_state()
        assert isinstance(s, HormoneState)
        assert s.adrenaline == pytest.approx(0.4)
        assert s.dopamine == 0.0


# ------------------------------------------------------------------ #
# Config from YAML
# ------------------------------------------------------------------ #


class TestEndocrineConfigYaml:
    def test_round_trip(self, tmp_path) -> None:
        import yaml

        cfg_data = {
            "endocrine": {
                "publish_interval_seconds": 5.0,
                "significant_change_delta": 0.05,
                "dopamine": {
                    "increment": 0.20,
                    "activation_threshold": 0.55,
                    "half_life_seconds": 200.0,
                },
                "adrenaline": {
                    "increment": 0.30,
                    "activation_threshold": 0.65,
                    "escalation_threshold": 0.90,
                    "half_life_seconds": 30.0,
                },
            },
        }
        path = tmp_path / "endo.yaml"
        path.write_text(yaml.dump(cfg_data), encoding="utf-8")

        loaded = EndocrineConfig.from_yaml(path)
        assert loaded.dopamine.increment == pytest.approx(0.20)
        assert loaded.adrenaline.escalation_threshold == pytest.approx(0.90)
        assert loaded.publish_interval_seconds == pytest.approx(5.0)
        # Unmentioned hormones keep defaults.
        assert loaded.cortisol.increment == pytest.approx(0.15)

    def test_defaults(self) -> None:
        cfg = EndocrineConfig()
        assert cfg.dopamine.half_life_seconds == 300.0
        assert cfg.adrenaline.half_life_seconds == 60.0
        assert cfg.cortisol.escalation_threshold == 0.80
        assert cfg.endorphin.activation_threshold == 0.40
