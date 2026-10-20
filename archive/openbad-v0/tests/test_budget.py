"""Tests for openbad.reflex_arc.handlers.budget — budget exhaustion reflex."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from openbad.nervous_system.schemas.common_pb2 import Header
from openbad.nervous_system.schemas.endocrine_pb2 import EndocrineEvent
from openbad.nervous_system.schemas.reflex_pb2 import ReflexResult
from openbad.reflex_arc.handlers.budget import (
    CORTISOL_TOPIC,
    RESULT_TOPIC,
    BudgetGuard,
)

# ── helpers ───────────────────────────────────────────────────────


def _make_event(
    metric: str = "token_budget_remaining_pct",
    value: float = 3.0,
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


# ── handler fires on critical budget ──────────────────────────────


class TestHandlerFires:
    def test_fires_on_critical_budget(self):
        guard = BudgetGuard()
        result = guard.handle_cortisol(_make_event(severity=3))
        assert result is True
        assert guard.blocked is True

    def test_blocks_api_calls(self):
        guard = BudgetGuard()
        assert guard.is_call_allowed() is True
        guard.handle_cortisol(_make_event(severity=3))
        assert guard.is_call_allowed() is False


# ── handler ignores non-matching events ───────────────────────────


class TestHandlerIgnores:
    def test_ignores_warning_severity(self):
        guard = BudgetGuard()
        result = guard.handle_cortisol(_make_event(severity=2))
        assert result is False
        assert guard.blocked is False

    def test_ignores_non_budget_metric(self):
        guard = BudgetGuard()
        result = guard.handle_cortisol(_make_event(metric="cpu_percent", severity=3))
        assert result is False
        assert guard.blocked is False


# ── FSM integration ───────────────────────────────────────────────


class TestFSMIntegration:
    def test_triggers_throttle_on_fsm(self):
        fsm = MagicMock()
        guard = BudgetGuard(fsm=fsm)
        guard.handle_cortisol(_make_event(severity=3))
        fsm.fire.assert_called_once_with("throttle")

    def test_triggers_recover_on_fsm(self):
        fsm = MagicMock()
        guard = BudgetGuard(fsm=fsm)
        guard.handle_cortisol(_make_event(severity=3))
        fsm.fire.reset_mock()
        guard.recover()
        fsm.fire.assert_called_once_with("recover")


# ── Publishing ────────────────────────────────────────────────────


class TestPublishing:
    def test_publishes_result(self):
        published: list[tuple[str, bytes]] = []
        guard = BudgetGuard(publish_fn=lambda t, d: published.append((t, d)))
        guard.handle_cortisol(_make_event(severity=3))
        assert len(published) == 1
        assert published[0][0] == RESULT_TOPIC
        msg = ReflexResult()
        msg.ParseFromString(published[0][1])
        assert msg.reflex_id == "budget_exhaustion"
        assert msg.handled is True

    def test_no_publish_when_no_fn(self):
        guard = BudgetGuard()
        guard.handle_cortisol(_make_event(severity=3))
        # should not raise


# ── Recovery ──────────────────────────────────────────────────────


class TestRecovery:
    def test_recover_clears_block(self):
        guard = BudgetGuard()
        guard.handle_cortisol(_make_event(severity=3))
        assert guard.blocked is True
        result = guard.recover()
        assert result is True
        assert guard.blocked is False
        assert guard.is_call_allowed() is True

    def test_recover_noop_when_not_blocked(self):
        guard = BudgetGuard()
        assert guard.recover() is False

    def test_idempotent_exhaustion(self):
        guard = BudgetGuard()
        guard.handle_cortisol(_make_event(severity=3))
        guard.handle_cortisol(_make_event(severity=3))
        assert guard.blocked is True
        guard.recover()
        assert guard.blocked is False


# ── MQTT subscribe ────────────────────────────────────────────────


class TestSubscribe:
    def test_subscribe_registers_callback(self):
        guard = BudgetGuard()
        client = MagicMock()
        guard.subscribe(client)
        client.subscribe.assert_called_once()
        assert client.subscribe.call_args.args[0] == CORTISOL_TOPIC


# ── Performance ───────────────────────────────────────────────────


class TestPerformance:
    def test_handle_under_1ms(self):
        guard = BudgetGuard()
        payload = _make_event(severity=3)
        # warm up
        guard.handle_cortisol(payload)
        guard.recover()
        start = time.perf_counter_ns()
        guard.handle_cortisol(payload)
        elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
        assert elapsed_ms < 1.0, f"Handler took {elapsed_ms:.3f}ms"
