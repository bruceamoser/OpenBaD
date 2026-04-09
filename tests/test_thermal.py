"""Tests for openbad.reflex_arc.handlers.thermal — thermal throttle reflex."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from openbad.nervous_system.schemas.common_pb2 import Header
from openbad.nervous_system.schemas.endocrine_pb2 import EndocrineEvent
from openbad.nervous_system.schemas.reflex_pb2 import ReflexResult
from openbad.reflex_arc.handlers.thermal import (
    COGNITIVE_DIRECTIVE_TOPIC,
    CORTISOL_TOPIC,
    RESULT_TOPIC,
    SUSPEND_TOPIC,
    handle_cortisol,
    subscribe,
)

# ── helpers ───────────────────────────────────────────────────────


def _make_event(
    metric: str = "cpu_percent",
    value: float = 95.0,
    severity: int = 3,
) -> bytes:
    return EndocrineEvent(
        header=Header(timestamp_unix=time.time()),
        hormone="cortisol",
        level=1.0,
        severity=severity,
        metric_name=metric,
        metric_value=value,
        recommended_action="test",
    ).SerializeToString()


# ── handler fires on critical CPU/thermal ─────────────────────────


class TestHandlerFires:
    def test_fires_on_critical_cpu(self):
        published: list[tuple[str, bytes]] = []
        payload = _make_event("cpu_percent", 95.0, severity=3)
        result = handle_cortisol(payload, lambda t, d: published.append((t, d)))
        assert result is True
        assert len(published) == 3

    def test_fires_on_critical_thermal(self):
        published: list[tuple[str, bytes]] = []
        payload = _make_event("thermal_celsius", 100.0, severity=3)
        result = handle_cortisol(payload, lambda t, d: published.append((t, d)))
        assert result is True


# ── handler ignores non-critical ──────────────────────────────────


class TestHandlerIgnores:
    def test_ignores_warning_severity(self):
        published: list[tuple[str, bytes]] = []
        payload = _make_event("cpu_percent", 80.0, severity=2)
        result = handle_cortisol(payload, lambda t, d: published.append((t, d)))
        assert result is False
        assert published == []

    def test_ignores_info_severity(self):
        published: list[tuple[str, bytes]] = []
        payload = _make_event("cpu_percent", 50.0, severity=1)
        result = handle_cortisol(payload, lambda t, d: published.append((t, d)))
        assert result is False

    def test_ignores_non_thermal_metric(self):
        published: list[tuple[str, bytes]] = []
        payload = _make_event("memory_percent", 97.0, severity=3)
        result = handle_cortisol(payload, lambda t, d: published.append((t, d)))
        assert result is False


# ── directives published correctly ────────────────────────────────


class TestDirectives:
    def test_suspend_directive(self):
        published: list[tuple[str, bytes]] = []
        handle_cortisol(
            _make_event("cpu_percent", 95.0, severity=3),
            lambda t, d: published.append((t, d)),
        )
        topics = [t for t, _ in published]
        assert SUSPEND_TOPIC in topics

    def test_cognitive_downgrade_directive(self):
        published: list[tuple[str, bytes]] = []
        handle_cortisol(
            _make_event("cpu_percent", 95.0, severity=3),
            lambda t, d: published.append((t, d)),
        )
        topics = [t for t, _ in published]
        assert COGNITIVE_DIRECTIVE_TOPIC in topics

    def test_reflex_result_published(self):
        published: list[tuple[str, bytes]] = []
        handle_cortisol(
            _make_event("cpu_percent", 95.0, severity=3),
            lambda t, d: published.append((t, d)),
        )
        result_msgs = [(t, d) for t, d in published if t == RESULT_TOPIC]
        assert len(result_msgs) == 1
        msg = ReflexResult()
        msg.ParseFromString(result_msgs[0][1])
        assert msg.reflex_id == "thermal_throttle"
        assert msg.handled is True

    def test_suspend_payload_json(self):
        published: list[tuple[str, bytes]] = []
        handle_cortisol(
            _make_event("cpu_percent", 95.0, severity=3),
            lambda t, d: published.append((t, d)),
        )
        suspend_msgs = [(t, d) for t, d in published if t == SUSPEND_TOPIC]
        assert b"suspend" in suspend_msgs[0][1]
        assert b"thermal_throttle" in suspend_msgs[0][1]

    def test_cognitive_payload_json(self):
        published: list[tuple[str, bytes]] = []
        handle_cortisol(
            _make_event("cpu_percent", 95.0, severity=3),
            lambda t, d: published.append((t, d)),
        )
        cog_msgs = [(t, d) for t, d in published if t == COGNITIVE_DIRECTIVE_TOPIC]
        assert b"slm_only" in cog_msgs[0][1]


# ── performance ───────────────────────────────────────────────────


class TestPerformance:
    def test_executes_under_1ms(self):
        payload = _make_event("cpu_percent", 99.0, severity=3)
        noop = lambda t, d: None  # noqa: E731
        # warm up
        handle_cortisol(payload, noop)
        start = time.perf_counter_ns()
        handle_cortisol(payload, noop)
        elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
        assert elapsed_ms < 1.0, f"Handler took {elapsed_ms:.3f}ms"


# ── MQTT subscribe integration ────────────────────────────────────


class TestSubscribe:
    def test_subscribe_registers_callback(self):
        client = MagicMock()
        subscribe(client)
        client.subscribe.assert_called_once()
        assert client.subscribe.call_args.args[0] == CORTISOL_TOPIC
