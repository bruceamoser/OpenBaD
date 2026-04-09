"""Tests for openbad.reflex_arc.handlers.proprioceptive."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

from openbad.nervous_system.schemas.reflex_pb2 import ReflexResult
from openbad.reflex_arc.handlers.proprioceptive import (
    DEGRADATION_TOPIC,
    RESULT_TOPIC,
    STATE_TOPIC,
    ProprioceptiveHandler,
)

# ── helpers ───────────────────────────────────────────────────────


def _state_payload(tools: dict[str, str]) -> bytes:
    """Build a proprioceptive state snapshot payload."""
    return json.dumps({name: {"status": status} for name, status in tools.items()}).encode()


def _handler_with_publish(
    critical: set[str] | None = None,
    alternatives: dict[str, list[str]] | None = None,
    fsm: object | None = None,
):
    messages: list[tuple[str, bytes]] = []
    h = ProprioceptiveHandler(
        critical_tools=critical,
        alternatives=alternatives,
        fsm=fsm,
        publish_fn=lambda t, d: messages.append((t, d)),
    )
    return h, messages


# ── fires on UNAVAILABLE ─────────────────────────────────────────


class TestFiresOnUnavailable:
    def test_fires_when_tool_unavailable(self):
        h, _ = _handler_with_publish()
        result = h.handle_state_change(
            _state_payload({"my_tool": "UNAVAILABLE"}),
        )
        assert result is True

    def test_does_not_fire_for_available(self):
        h, _ = _handler_with_publish()
        result = h.handle_state_change(
            _state_payload({"my_tool": "AVAILABLE"}),
        )
        assert result is False


# ── non-critical tool changes ─────────────────────────────────────


class TestNonCritical:
    def test_no_fsm_transition(self):
        fsm = MagicMock()
        h, _ = _handler_with_publish(
            critical={"important_tool"},
            fsm=fsm,
        )
        h.handle_state_change(
            _state_payload({"unimportant": "UNAVAILABLE"}),
        )
        fsm.fire.assert_not_called()

    def test_publishes_result(self):
        h, messages = _handler_with_publish()
        h.handle_state_change(
            _state_payload({"tool_a": "UNAVAILABLE"}),
        )
        assert any(t == RESULT_TOPIC for t, _ in messages)


# ── mission-critical tool loss → FSM THROTTLED ───────────────────


class TestCriticalToolLoss:
    def test_fsm_throttle(self):
        fsm = MagicMock()
        h, _ = _handler_with_publish(critical={"llm_api"}, fsm=fsm)
        h.handle_state_change(_state_payload({"llm_api": "UNAVAILABLE"}))
        fsm.fire.assert_called_once_with("throttle")

    def test_result_mentions_throttle(self):
        h, messages = _handler_with_publish(critical={"llm_api"})
        h.handle_state_change(_state_payload({"llm_api": "UNAVAILABLE"}))
        result_msgs = [(t, d) for t, d in messages if t == RESULT_TOPIC]
        assert len(result_msgs) == 1
        msg = ReflexResult()
        msg.ParseFromString(result_msgs[0][1])
        assert "THROTTLED" in msg.action_taken


# ── graceful degradation ─────────────────────────────────────────


class TestGracefulDegradation:
    def test_publishes_alternatives(self):
        h, messages = _handler_with_publish(
            alternatives={"tool_a": ["tool_b", "tool_c"]},
        )
        h.handle_state_change(_state_payload({"tool_a": "UNAVAILABLE"}))
        deg = [(t, d) for t, d in messages if t == DEGRADATION_TOPIC]
        assert len(deg) == 1
        payload = json.loads(deg[0][1])
        assert payload["lost_tool"] == "tool_a"
        assert payload["alternatives"] == ["tool_b", "tool_c"]

    def test_no_alternatives_no_degradation(self):
        h, messages = _handler_with_publish()
        h.handle_state_change(_state_payload({"tool_a": "UNAVAILABLE"}))
        assert not any(t == DEGRADATION_TOPIC for t, _ in messages)


# ── abort tracking ────────────────────────────────────────────────


class TestAbortTracking:
    def test_aborted_tools_tracked(self):
        h, _ = _handler_with_publish()
        h.handle_state_change(_state_payload({"tool_x": "UNAVAILABLE"}))
        assert "tool_x" in h.aborted_tools

    def test_clear_aborted(self):
        h, _ = _handler_with_publish()
        h.handle_state_change(_state_payload({"tool_x": "UNAVAILABLE"}))
        h.clear_aborted("tool_x")
        assert "tool_x" not in h.aborted_tools


# ── MQTT subscribe ────────────────────────────────────────────────


class TestSubscribe:
    def test_subscribe_registers_callback(self):
        h, _ = _handler_with_publish()
        client = MagicMock()
        h.subscribe(client)
        client.subscribe.assert_called_once()
        assert client.subscribe.call_args.args[0] == STATE_TOPIC


# ── invalid payload ───────────────────────────────────────────────


class TestInvalidPayload:
    def test_bad_json(self):
        h, _ = _handler_with_publish()
        result = h.handle_state_change(b"not json")
        assert result is False


# ── performance ───────────────────────────────────────────────────


class TestPerformance:
    def test_handle_under_100ms(self):
        h, _ = _handler_with_publish(critical={"tool"})
        payload = _state_payload({"tool": "UNAVAILABLE"})
        start = time.perf_counter_ns()
        h.handle_state_change(payload)
        elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
        assert elapsed_ms < 100.0, f"Handler took {elapsed_ms:.3f}ms"
