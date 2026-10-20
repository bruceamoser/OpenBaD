"""Tests for cognitive scoring math (ACT-R, Hebbian, Ebbinghaus, composite)."""

from __future__ import annotations

import math

from openbad.memory.cognitive import (
    act_r_activation,
    composite_score,
    ebbinghaus_retention,
    hebbian_decay,
    hebbian_update,
)


class TestActRActivation:
    """ACT-R base-level activation scoring."""

    def test_recent_frequent_beats_old_rare(self):
        """14 accesses 2 hours ago > 1 access 30 days ago."""
        recent_frequent = act_r_activation(14, 0.083)
        old_rare = act_r_activation(1, 30.0)
        assert recent_frequent > old_rare

    def test_more_accesses_increases_score(self):
        """Holding age constant, more accesses → higher activation."""
        s1 = act_r_activation(1, 1.0)
        s5 = act_r_activation(5, 1.0)
        s20 = act_r_activation(20, 1.0)
        assert s1 < s5 < s20

    def test_older_decreases_score(self):
        """Holding access count constant, older → lower activation."""
        s_new = act_r_activation(5, 0.1)
        s_mid = act_r_activation(5, 5.0)
        s_old = act_r_activation(5, 30.0)
        assert s_new > s_mid > s_old

    def test_zero_accesses(self):
        """Zero accesses should still produce a finite value."""
        score = act_r_activation(0, 1.0)
        assert math.isfinite(score)

    def test_very_small_age_floors(self):
        """Age near zero floors to 0.001 to avoid log(0)."""
        score = act_r_activation(3, 0.0)
        assert math.isfinite(score)

    def test_custom_decay(self):
        """Higher decay exponent penalizes age more."""
        low_decay = act_r_activation(5, 10.0, decay=0.3)
        high_decay = act_r_activation(5, 10.0, decay=0.8)
        assert low_decay > high_decay

    def test_deterministic(self):
        """Same inputs always produce same output."""
        a = act_r_activation(10, 2.5)
        b = act_r_activation(10, 2.5)
        assert a == b


class TestHebbianUpdate:
    """Hebbian weight update in log-space."""

    def test_weight_increases(self):
        """A single update should increase the weight."""
        w = 0.3
        w_new = hebbian_update(w)
        assert w_new > w

    def test_monotonic_increase(self):
        """Repeated co-activations → monotonic weight increase."""
        w = 0.1
        for _ in range(20):
            w_prev = w
            w = hebbian_update(w)
            assert w >= w_prev

    def test_clamped_to_one(self):
        """Weight never exceeds 1.0."""
        w = 0.99
        for _ in range(100):
            w = hebbian_update(w)
        assert w <= 1.0

    def test_near_zero_weight(self):
        """Very small weight still increases."""
        w = hebbian_update(1e-8)
        assert w > 1e-8

    def test_custom_learning_rate(self):
        """Higher learning rate → larger increase."""
        w_low = hebbian_update(0.3, learning_rate=0.05)
        w_high = hebbian_update(0.3, learning_rate=0.2)
        assert w_high > w_low


class TestHebbianDecay:
    """Exponential decay of association weights."""

    def test_half_life_halves_weight(self):
        """At exactly the half-life, weight ≈ 50%."""
        decayed = hebbian_decay(1.0, hours_since_last=168.0, half_life_hours=168.0)
        assert abs(decayed - 0.5) < 0.01

    def test_no_time_no_decay(self):
        """Zero hours elapsed → no decay."""
        assert hebbian_decay(0.8, 0.0) == 0.8

    def test_long_time_approaches_zero(self):
        """After many half-lives, weight approaches zero."""
        decayed = hebbian_decay(1.0, hours_since_last=10000.0, half_life_hours=168.0)
        assert decayed < 0.01

    def test_proportional_to_initial(self):
        """Decay is proportional: 2× weight → 2× decayed value."""
        d1 = hebbian_decay(0.5, 100.0)
        d2 = hebbian_decay(1.0, 100.0)
        assert abs(d2 / d1 - 2.0) < 0.001

    def test_zero_half_life(self):
        """Zero half-life → instant decay to 0."""
        assert hebbian_decay(0.5, 1.0, half_life_hours=0.0) == 0.0


class TestEbbinghausRetention:
    """Ebbinghaus forgetting curve."""

    def test_fresh_memory_high_retention(self):
        """Just-accessed memory has near-perfect retention."""
        r = ebbinghaus_retention(0.0, access_count=3)
        assert r > 0.99

    def test_old_memory_low_retention(self):
        """Very old, rarely accessed memory has low retention."""
        r = ebbinghaus_retention(5000.0, access_count=0)
        assert r < 0.1

    def test_more_accesses_slower_decay(self):
        """More accesses reinforce memory, slowing decay."""
        r_few = ebbinghaus_retention(500.0, access_count=1)
        r_many = ebbinghaus_retention(500.0, access_count=50)
        assert r_many > r_few

    def test_importance_boosts_retention(self):
        """High importance increases retention."""
        r_low = ebbinghaus_retention(500.0, access_count=3, importance=0.0)
        r_high = ebbinghaus_retention(500.0, access_count=3, importance=1.0)
        assert r_high > r_low

    def test_importance_none_is_neutral(self):
        """None importance uses factor 1.0 (same as importance=0.5)."""
        r_none = ebbinghaus_retention(200.0, access_count=5, importance=None)
        r_mid = ebbinghaus_retention(200.0, access_count=5, importance=0.5)
        assert r_none == r_mid

    def test_return_range(self):
        """Retention is always in [0, 1]."""
        for hours in (0, 1, 100, 10000):
            for n in (0, 1, 10):
                r = ebbinghaus_retention(hours, access_count=n)
                assert 0.0 <= r <= 1.0


class TestCompositeScore:
    """Composite retrieval score."""

    def test_deterministic(self):
        """Same inputs → same output."""
        a = composite_score(5.0, 2.0, 0.3, 0.9)
        b = composite_score(5.0, 2.0, 0.3, 0.9)
        assert a == b

    def test_higher_bm25_higher_score(self):
        """Better content match → higher score."""
        s_low = composite_score(1.0, 2.0, 0.3, 0.9)
        s_high = composite_score(10.0, 2.0, 0.3, 0.9)
        assert s_high > s_low

    def test_higher_act_r_higher_score(self):
        """Higher cognitive activation → higher score."""
        s_low = composite_score(5.0, 0.5, 0.3, 0.9)
        s_high = composite_score(5.0, 5.0, 0.3, 0.9)
        assert s_high > s_low

    def test_higher_hebbian_higher_score(self):
        """Stronger associations → higher score."""
        s_low = composite_score(5.0, 2.0, 0.0, 0.9)
        s_high = composite_score(5.0, 2.0, 1.0, 0.9)
        assert s_high > s_low

    def test_higher_confidence_higher_score(self):
        """Higher confidence → higher score."""
        s_low = composite_score(5.0, 2.0, 0.3, 0.3)
        s_high = composite_score(5.0, 2.0, 0.3, 1.0)
        assert s_high > s_low

    def test_zero_confidence_zeroes_score(self):
        """Zero confidence → zero score."""
        assert composite_score(5.0, 2.0, 0.3, 0.0) == 0.0

    def test_zero_bm25_zeroes_score(self):
        """Zero content match → zero score."""
        assert composite_score(0.0, 2.0, 0.3, 0.9) == 0.0

    def test_positive_for_typical_inputs(self):
        """Typical inputs produce a positive score."""
        s = composite_score(3.0, 1.5, 0.2, 0.8)
        assert s > 0
