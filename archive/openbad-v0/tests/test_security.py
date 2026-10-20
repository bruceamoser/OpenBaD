"""Tests for openbad.reflex_arc.handlers.security — security lockdown reflex."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from openbad.nervous_system.schemas.common_pb2 import Header
from openbad.nervous_system.schemas.immune_pb2 import ImmuneAlert
from openbad.nervous_system.schemas.reflex_pb2 import ReflexResult
from openbad.reflex_arc.handlers.security import (
    IMMUNE_ALERT_TOPIC,
    RESULT_TOPIC,
    SecurityGuard,
)

# ── helpers ───────────────────────────────────────────────────────


def _make_alert(
    severity: int = 3,
    source_id: str = "rogue_tool",
    threat_type: str = "prompt-injection",
) -> bytes:
    return ImmuneAlert(
        header=Header(timestamp_unix=time.time()),
        severity=severity,
        threat_type=threat_type,
        source_id=source_id,
        detail="test alert",
    ).SerializeToString()


# ── handler fires on critical immune alert ────────────────────────


class TestHandlerFires:
    def test_fires_on_critical(self):
        guard = SecurityGuard()
        result = guard.handle_alert(_make_alert(severity=3))
        assert result is True
        assert guard.locked_down is True

    def test_blocks_tool_invocations(self):
        guard = SecurityGuard()
        assert guard.is_tool_allowed() is True
        guard.handle_alert(_make_alert(severity=3))
        assert guard.is_tool_allowed() is False


# ── handler ignores non-critical ──────────────────────────────────


class TestHandlerIgnores:
    def test_ignores_warning(self):
        guard = SecurityGuard()
        result = guard.handle_alert(_make_alert(severity=2))
        assert result is False
        assert guard.locked_down is False

    def test_ignores_info(self):
        guard = SecurityGuard()
        result = guard.handle_alert(_make_alert(severity=1))
        assert result is False


# ── subsystem isolation ───────────────────────────────────────────


class TestIsolation:
    def test_isolates_source(self):
        guard = SecurityGuard()
        guard.handle_alert(_make_alert(source_id="bad_tool"))
        assert "bad_tool" in guard.isolated_sources

    def test_tool_not_allowed_when_isolated(self):
        guard = SecurityGuard()
        guard.handle_alert(_make_alert(source_id="bad_tool"))
        assert guard.is_tool_allowed("bad_tool") is False

    def test_other_tool_also_blocked_during_lockdown(self):
        guard = SecurityGuard()
        guard.handle_alert(_make_alert(source_id="bad_tool"))
        # Lockdown blocks ALL tools
        assert guard.is_tool_allowed("good_tool") is False

    def test_multiple_alerts_accumulate_sources(self):
        guard = SecurityGuard()
        guard.handle_alert(_make_alert(source_id="tool_a"))
        guard.handle_alert(_make_alert(source_id="tool_b"))
        assert "tool_a" in guard.isolated_sources
        assert "tool_b" in guard.isolated_sources


# ── FSM integration ───────────────────────────────────────────────


class TestFSMIntegration:
    def test_triggers_emergency(self):
        fsm = MagicMock()
        guard = SecurityGuard(fsm=fsm)
        guard.handle_alert(_make_alert(severity=3))
        fsm.fire.assert_called_once_with("emergency")


# ── recovery requires explicit clearance ──────────────────────────


class TestRecovery:
    def test_clear_lifts_lockdown(self):
        guard = SecurityGuard()
        guard.handle_alert(_make_alert(severity=3))
        result = guard.clear(operator="admin")
        assert result is True
        assert guard.locked_down is False
        assert guard.isolated_sources == frozenset()

    def test_clear_noop_when_not_locked(self):
        guard = SecurityGuard()
        assert guard.clear() is False

    def test_no_auto_recovery(self):
        """Lockdown persists until explicit clear — no automatic reset."""
        guard = SecurityGuard()
        guard.handle_alert(_make_alert(severity=3))
        # No recover/timeout mechanism exists
        assert guard.locked_down is True
        assert guard.is_tool_allowed() is False


# ── publishing ────────────────────────────────────────────────────


class TestPublishing:
    def test_publishes_result(self):
        published: list[tuple[str, bytes]] = []
        guard = SecurityGuard(
            publish_fn=lambda t, d: published.append((t, d)),
        )
        guard.handle_alert(_make_alert(severity=3))
        assert len(published) == 1
        assert published[0][0] == RESULT_TOPIC
        msg = ReflexResult()
        msg.ParseFromString(published[0][1])
        assert msg.reflex_id == "security_lockdown"
        assert msg.handled is True


# ── MQTT subscribe ────────────────────────────────────────────────


class TestSubscribe:
    def test_subscribe_registers_callback(self):
        guard = SecurityGuard()
        client = MagicMock()
        guard.subscribe(client)
        client.subscribe.assert_called_once()
        assert client.subscribe.call_args.args[0] == IMMUNE_ALERT_TOPIC


# ── performance ───────────────────────────────────────────────────


class TestPerformance:
    def test_handle_under_1ms(self):
        guard = SecurityGuard()
        payload = _make_alert(severity=3)
        # warm up
        guard.handle_alert(payload)
        guard.clear()
        start = time.perf_counter_ns()
        guard.handle_alert(payload)
        elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
        assert elapsed_ms < 1.0, f"Handler took {elapsed_ms:.3f}ms"
