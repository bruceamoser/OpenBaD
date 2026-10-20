"""Tests for openbad.interoception.thresholds — threshold policies + cortisol."""

from __future__ import annotations

from unittest.mock import MagicMock

from openbad.interoception.thresholds import (
    Breach,
    ThresholdSpec,
    breach_to_proto,
    evaluate,
    load_thresholds,
    publish_breaches,
)
from openbad.nervous_system.schemas.endocrine_pb2 import EndocrineEvent

# ── YAML loading ──────────────────────────────────────────────────


class TestLoadThresholds:
    def test_loads_default_config(self):
        specs = load_thresholds()
        names = {s.metric for s in specs}
        assert "cpu_percent" in names
        assert "memory_percent" in names
        assert "token_budget_remaining_pct" in names

    def test_correct_values(self):
        specs = load_thresholds()
        cpu = next(s for s in specs if s.metric == "cpu_percent")
        assert cpu.warning == 75
        assert cpu.critical == 90

    def test_custom_config(self, tmp_path):
        cfg = tmp_path / "custom.yaml"
        cfg.write_text("thresholds:\n  my_metric:\n    warning: 10\n    critical: 20\n")
        specs = load_thresholds(cfg)
        assert len(specs) == 1
        assert specs[0].metric == "my_metric"
        assert specs[0].warning == 10
        assert specs[0].critical == 20


# ── Evaluation ────────────────────────────────────────────────────

_SPECS = [
    ThresholdSpec("cpu_percent", warning=75, critical=90),
    ThresholdSpec("memory_percent", warning=80, critical=95),
    ThresholdSpec("token_budget_remaining_pct", warning=20, critical=5),
]


class TestEvaluate:
    def test_no_breaches_within_bounds(self):
        values = {"cpu_percent": 50.0, "memory_percent": 60.0, "token_budget_remaining_pct": 50.0}
        assert evaluate(_SPECS, values) == []

    def test_cpu_warning(self):
        values = {"cpu_percent": 80.0}
        breaches = evaluate(_SPECS, values)
        assert len(breaches) == 1
        assert breaches[0].severity == 2  # WARNING
        assert breaches[0].metric == "cpu_percent"

    def test_cpu_critical(self):
        values = {"cpu_percent": 95.0}
        breaches = evaluate(_SPECS, values)
        assert len(breaches) == 1
        assert breaches[0].severity == 3  # CRITICAL

    def test_memory_critical(self):
        values = {"memory_percent": 97.0}
        breaches = evaluate(_SPECS, values)
        assert len(breaches) == 1
        assert breaches[0].severity == 3

    def test_token_budget_inverted_warning(self):
        # token_budget_remaining_pct is inverted: low value = bad
        values = {"token_budget_remaining_pct": 15.0}
        breaches = evaluate(_SPECS, values)
        assert len(breaches) == 1
        assert breaches[0].severity == 2

    def test_token_budget_inverted_critical(self):
        values = {"token_budget_remaining_pct": 3.0}
        breaches = evaluate(_SPECS, values)
        assert len(breaches) == 1
        assert breaches[0].severity == 3

    def test_multiple_breaches(self):
        values = {"cpu_percent": 92.0, "memory_percent": 82.0}
        breaches = evaluate(_SPECS, values)
        assert len(breaches) == 2

    def test_missing_metric_ignored(self):
        values = {"unknown_metric": 100.0}
        assert evaluate(_SPECS, values) == []

    def test_exact_warning_boundary(self):
        values = {"cpu_percent": 75.0}
        breaches = evaluate(_SPECS, values)
        assert len(breaches) == 1
        assert breaches[0].severity == 2

    def test_exact_critical_boundary(self):
        values = {"cpu_percent": 90.0}
        breaches = evaluate(_SPECS, values)
        assert len(breaches) == 1
        assert breaches[0].severity == 3


# ── Proto conversion ──────────────────────────────────────────────


class TestBreachToProto:
    def test_warning_event(self):
        breach = Breach("cpu_percent", 80.0, 75.0, 2)
        msg = breach_to_proto(breach)
        assert isinstance(msg, EndocrineEvent)
        assert msg.hormone == "cortisol"
        assert msg.severity == 2
        assert msg.metric_name == "cpu_percent"
        assert msg.metric_value == 80.0
        assert msg.level == 0.6

    def test_critical_event(self):
        breach = Breach("cpu_percent", 95.0, 90.0, 3)
        msg = breach_to_proto(breach)
        assert msg.severity == 3
        assert msg.level == 1.0

    def test_serialization_round_trip(self):
        breach = Breach("memory_percent", 96.0, 95.0, 3)
        data = breach_to_proto(breach).SerializeToString()
        parsed = EndocrineEvent()
        parsed.ParseFromString(data)
        assert parsed.hormone == "cortisol"
        assert parsed.metric_name == "memory_percent"


# ── Publishing ────────────────────────────────────────────────────


class TestPublishBreaches:
    def test_publishes_each_breach(self):
        client = MagicMock()
        breaches = [
            Breach("cpu_percent", 92.0, 90.0, 3),
            Breach("memory_percent", 82.0, 80.0, 2),
        ]
        count = publish_breaches(client, breaches)
        assert count == 2
        assert client.publish.call_count == 2

        # All to same topic
        for call_obj in client.publish.call_args_list:
            assert call_obj.args[0] == "agent/endocrine/cortisol"

    def test_no_breaches_no_publish(self):
        client = MagicMock()
        count = publish_breaches(client, [])
        assert count == 0
        client.publish.assert_not_called()

    def test_payload_is_valid_protobuf(self):
        client = MagicMock()
        breaches = [Breach("cpu_percent", 80.0, 75.0, 2)]
        publish_breaches(client, breaches)
        payload = client.publish.call_args.args[1]
        # publish_breaches passes an EndocrineEvent object (not serialized bytes)
        assert isinstance(payload, EndocrineEvent)
        assert payload.hormone == "cortisol"
