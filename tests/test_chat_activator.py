"""Tests for chat activator FSM integration."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import Mock

from openbad.nervous_system import topics
from openbad.reflex_arc.chat_activator import ChatActivator, ReEngagementHook, load_config

if TYPE_CHECKING:
    pass


# ------------------------------------------------------------------ #
# Config loading
# ------------------------------------------------------------------ #


def test_load_config_defaults() -> None:
    """When config file doesn't exist, should return defaults."""
    cfg = load_config(Path("/nonexistent/fsm.yaml"))
    assert cfg["idle_timeout_seconds"] == 300  # 5 minutes
    assert cfg["debounce_window_seconds"] == 2  # 2 seconds


def test_load_config_from_file(tmp_path: Path) -> None:
    """When config file exists, should load values."""
    config_file = tmp_path / "fsm.yaml"
    config_file.write_text("idle_timeout_seconds: 60\ndebounce_window_seconds: 5\n")

    cfg = load_config(config_file)
    assert cfg["idle_timeout_seconds"] == 60
    assert cfg["debounce_window_seconds"] == 5


# ------------------------------------------------------------------ #
# FSM activation
# ------------------------------------------------------------------ #


def test_activates_from_idle_on_input() -> None:
    """When COGNITIVE_INPUT received and FSM is IDLE, should activate."""
    mock_fsm = Mock()
    mock_fsm.state = "IDLE"
    mock_fsm.fire.return_value = True

    mock_client = Mock()

    activator = ChatActivator(mock_fsm, mock_client)
    activator.start()

    # Simulate COGNITIVE_INPUT
    payload = json.dumps({"source": "wui", "timestamp": time.time()}).encode()
    activator._on_cognitive_input(topics.COGNITIVE_INPUT, payload)

    # Should call fire("activate")
    mock_fsm.fire.assert_called_with("activate")


def test_activates_from_sleep_on_input() -> None:
    """When COGNITIVE_INPUT received and FSM is SLEEP, should activate."""
    mock_fsm = Mock()
    mock_fsm.state = "SLEEP"
    mock_fsm.fire.return_value = True

    mock_client = Mock()

    activator = ChatActivator(mock_fsm, mock_client)
    activator.start()

    payload = json.dumps({"source": "wui"}).encode()
    activator._on_cognitive_input(topics.COGNITIVE_INPUT, payload)

    mock_fsm.fire.assert_called_with("activate")


def test_activates_from_throttled_on_input() -> None:
    """When COGNITIVE_INPUT received and FSM is THROTTLED, should activate."""
    mock_fsm = Mock()
    mock_fsm.state = "THROTTLED"
    mock_fsm.fire.return_value = True

    mock_client = Mock()

    activator = ChatActivator(mock_fsm, mock_client)
    activator.start()

    payload = json.dumps({"source": "wui"}).encode()
    activator._on_cognitive_input(topics.COGNITIVE_INPUT, payload)

    mock_fsm.fire.assert_called_with("activate")


def test_does_not_activate_when_already_active() -> None:
    """When COGNITIVE_INPUT received and FSM is already ACTIVE, should not call fire."""
    mock_fsm = Mock()
    mock_fsm.state = "ACTIVE"

    mock_client = Mock()

    activator = ChatActivator(mock_fsm, mock_client)
    activator.start()

    payload = json.dumps({"source": "wui"}).encode()
    activator._on_cognitive_input(topics.COGNITIVE_INPUT, payload)

    # Should NOT call fire when already active
    mock_fsm.fire.assert_not_called()


def test_does_not_activate_in_emergency() -> None:
    """When FSM is in EMERGENCY state, chat input should not override."""
    mock_fsm = Mock()
    mock_fsm.state = "EMERGENCY"

    mock_client = Mock()

    activator = ChatActivator(mock_fsm, mock_client)
    activator.start()

    payload = json.dumps({"source": "wui"}).encode()
    activator._on_cognitive_input(topics.COGNITIVE_INPUT, payload)

    # Should not call fire in emergency
    mock_fsm.fire.assert_not_called()


# ------------------------------------------------------------------ #
# Debouncing
# ------------------------------------------------------------------ #


def test_debounces_rapid_messages(tmp_path: Path) -> None:
    """When multiple messages arrive within debounce window, should only activate once."""
    # Use short debounce window for test
    config_file = tmp_path / "fsm.yaml"
    config_file.write_text("idle_timeout_seconds: 300\ndebounce_window_seconds: 1\n")

    mock_fsm = Mock()
    mock_fsm.state = "IDLE"
    mock_fsm.fire.return_value = True

    mock_client = Mock()

    activator = ChatActivator(mock_fsm, mock_client, config_path=config_file)
    activator.start()

    # Send 3 messages in quick succession
    payload = json.dumps({"source": "wui"}).encode()
    activator._on_cognitive_input(topics.COGNITIVE_INPUT, payload)
    activator._on_cognitive_input(topics.COGNITIVE_INPUT, payload)
    activator._on_cognitive_input(topics.COGNITIVE_INPUT, payload)

    # Should only activate once (first call)
    assert mock_fsm.fire.call_count == 1


def test_allows_activation_after_debounce_window(tmp_path: Path) -> None:
    """When messages arrive outside debounce window, both should trigger."""
    # Use very short debounce for test speed
    config_file = tmp_path / "fsm.yaml"
    config_file.write_text("idle_timeout_seconds: 300\ndebounce_window_seconds: 0.1\n")

    mock_fsm = Mock()
    mock_fsm.state = "IDLE"
    mock_fsm.fire.return_value = True

    mock_client = Mock()

    activator = ChatActivator(mock_fsm, mock_client, config_path=config_file)
    activator.start()

    # First message
    payload = json.dumps({"source": "wui"}).encode()
    activator._on_cognitive_input(topics.COGNITIVE_INPUT, payload)
    assert mock_fsm.fire.call_count == 1

    # Wait for debounce window to expire
    time.sleep(0.15)

    # Reset FSM to IDLE for second activation
    mock_fsm.state = "IDLE"

    # Second message (outside debounce window)
    activator._on_cognitive_input(topics.COGNITIVE_INPUT, payload)
    assert mock_fsm.fire.call_count == 2


# ------------------------------------------------------------------ #
# Idle timeout
# ------------------------------------------------------------------ #


def test_deactivates_after_idle_timeout(tmp_path: Path) -> None:
    """When no input for idle_timeout, should deactivate FSM from ACTIVE."""
    # Use very short timeout for test speed
    config_file = tmp_path / "fsm.yaml"
    config_file.write_text("idle_timeout_seconds: 0.2\ndebounce_window_seconds: 0.1\n")

    mock_fsm = Mock()
    mock_fsm.state = "IDLE"
    mock_fsm.fire.return_value = True

    mock_client = Mock()

    activator = ChatActivator(mock_fsm, mock_client, config_path=config_file)
    activator.start()

    # Activate via input
    payload = json.dumps({"source": "wui"}).encode()
    activator._on_cognitive_input(topics.COGNITIVE_INPUT, payload)

    # FSM transitions to ACTIVE
    mock_fsm.state = "ACTIVE"

    # Wait for idle timeout to expire
    time.sleep(0.3)

    # Should have called fire("deactivate")
    assert any(
        call[0][0] == "deactivate" for call in mock_fsm.fire.call_args_list
    ), "Should call deactivate after idle timeout"


def test_idle_timeout_resets_on_new_input(tmp_path: Path) -> None:
    """When new input arrives, idle timer should reset."""
    # Use short timeout
    config_file = tmp_path / "fsm.yaml"
    config_file.write_text("idle_timeout_seconds: 0.3\ndebounce_window_seconds: 0.1\n")

    mock_fsm = Mock()
    mock_fsm.state = "IDLE"
    mock_fsm.fire.return_value = True

    mock_client = Mock()

    activator = ChatActivator(mock_fsm, mock_client, config_path=config_file)
    activator.start()

    # First input
    payload = json.dumps({"source": "wui"}).encode()
    activator._on_cognitive_input(topics.COGNITIVE_INPUT, payload)
    mock_fsm.state = "ACTIVE"

    # Wait half the timeout
    time.sleep(0.15)

    # Second input (resets timer)
    # Need to be outside debounce window
    time.sleep(0.1)
    activator._on_cognitive_input(topics.COGNITIVE_INPUT, payload)

    # Wait another half timeout
    time.sleep(0.15)

    # Should NOT have deactivated yet (timer was reset)
    # Only the first "activate" call should have happened
    deactivate_calls = [
        call for call in mock_fsm.fire.call_args_list if call[0][0] == "deactivate"
    ]
    assert len(deactivate_calls) == 0, "Should not deactivate if timer was reset"


def test_does_not_deactivate_if_not_active(tmp_path: Path) -> None:
    """When idle timeout fires but FSM not ACTIVE, should not call deactivate."""
    config_file = tmp_path / "fsm.yaml"
    config_file.write_text("idle_timeout_seconds: 0.1\ndebounce_window_seconds: 0.05\n")

    mock_fsm = Mock()
    mock_fsm.state = "IDLE"
    mock_fsm.fire.return_value = True

    mock_client = Mock()

    activator = ChatActivator(mock_fsm, mock_client, config_path=config_file)
    activator.start()

    # Trigger input to start timer
    payload = json.dumps({"source": "wui"}).encode()
    activator._on_cognitive_input(topics.COGNITIVE_INPUT, payload)

    # But FSM stays in IDLE (activation failed or state changed)
    mock_fsm.state = "IDLE"

    # Wait for timeout
    time.sleep(0.2)

    # Should not call deactivate when not ACTIVE
    deactivate_calls = [
        call for call in mock_fsm.fire.call_args_list if call[0][0] == "deactivate"
    ]
    assert len(deactivate_calls) == 0


# ------------------------------------------------------------------ #
# Start/stop
# ------------------------------------------------------------------ #


def test_stop_cancels_idle_timer(tmp_path: Path) -> None:
    """When activator is stopped, should cancel pending idle timer."""
    config_file = tmp_path / "fsm.yaml"
    config_file.write_text("idle_timeout_seconds: 0.3\ndebounce_window_seconds: 0.1\n")

    mock_fsm = Mock()
    mock_fsm.state = "IDLE"
    mock_fsm.fire.return_value = True

    mock_client = Mock()

    activator = ChatActivator(mock_fsm, mock_client, config_path=config_file)
    activator.start()

    # Trigger input to start timer
    payload = json.dumps({"source": "wui"}).encode()
    activator._on_cognitive_input(topics.COGNITIVE_INPUT, payload)
    mock_fsm.state = "ACTIVE"

    # Stop activator before timeout
    activator.stop()

    # Wait for what would have been the timeout
    time.sleep(0.4)

    # Should not have called deactivate (timer was cancelled)
    deactivate_calls = [
        call for call in mock_fsm.fire.call_args_list if call[0][0] == "deactivate"
    ]
    assert len(deactivate_calls) == 0


def test_ignores_events_after_stop() -> None:
    """After stop, should not process COGNITIVE_INPUT events."""
    mock_fsm = Mock()
    mock_fsm.state = "IDLE"
    mock_fsm.fire.return_value = True

    mock_client = Mock()

    activator = ChatActivator(mock_fsm, mock_client)
    activator.start()
    activator.stop()

    # Send event after stop
    payload = json.dumps({"source": "wui"}).encode()
    activator._on_cognitive_input(topics.COGNITIVE_INPUT, payload)

    # Should not call fire after stopped
    mock_fsm.fire.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 10: ReEngagementHook tests (Issue #416)
# ---------------------------------------------------------------------------


def _make_node(node_id: str, task_id: str = "t1", title: str = "default question") -> Mock:
    """Build a minimal mock NodeModel."""
    node = Mock()
    node.node_id = node_id
    node.task_id = task_id
    node.title = title
    return node


def _make_hook(pending: list) -> tuple[ReEngagementHook, Mock, Mock]:
    """Return (hook, client_mock, store_mock)."""

    client = Mock()
    store = Mock()
    store.list_nodes_by_status.return_value = pending
    hook = ReEngagementHook(client, store)
    return hook, client, store


def _presence_payload(*, active: bool) -> bytes:
    return json.dumps({"active": active}).encode()


def _inbound_payload(answer: str = "yes") -> bytes:
    return json.dumps({"answer": answer}).encode()


class TestReEngagementHookReconnect:
    def test_surfaces_blocked_questions_on_reconnect(self) -> None:
        node = _make_node("n1", title="Are you sure?")
        hook, client, store = _make_hook([node])
        hook.start()
        hook._on_presence("system/wui/presence", _presence_payload(active=True))
        # Should publish the pending question + greeting
        assert client.publish_bytes.call_count >= 2
        chat_responses = [
            c for c in client.publish_bytes.call_args_list
            if c[0][0] == topics.AGENT_CHAT_RESPONSE
        ]
        assert len(chat_responses) >= 1
        first_payload = json.loads(chat_responses[0][0][1])
        assert first_payload["node_id"] == "n1"

    def test_no_pending_sends_greeting_only(self) -> None:
        hook, client, store = _make_hook([])
        hook.start()
        hook._on_presence("system/wui/presence", _presence_payload(active=True))
        assert client.publish_bytes.call_count == 1
        body = json.loads(client.publish_bytes.call_args[0][1])
        assert body["type"] == "greeting"

    def test_inactive_presence_does_not_trigger(self) -> None:
        hook, client, store = _make_hook([_make_node("n2")])
        hook.start()
        hook._on_presence("system/wui/presence", _presence_payload(active=False))
        client.publish_bytes.assert_not_called()

    def test_multiple_pending_ordered_oldest_first(self) -> None:
        nodes = [_make_node(f"n{i}", title=f"Q{i}") for i in range(3)]
        hook, client, store = _make_hook(nodes)
        hook.start()
        hook._on_presence("system/wui/presence", _presence_payload(active=True))
        chat_qs = [
            json.loads(c[0][1])
            for c in client.publish_bytes.call_args_list
            if json.loads(c[0][1]).get("type") == "pending_question"
        ]
        assert [q["node_id"] for q in chat_qs] == ["n2", "n1", "n0"]


class TestReEngagementHookUnblock:
    def test_answering_transitions_node_to_running(self) -> None:
        from openbad.tasks.models import NodeStatus

        node = _make_node("n10")
        hook, client, store = _make_hook([node])
        hook.start()
        # Simulate reconnect to load pending
        hook._on_presence("system/wui/presence", _presence_payload(active=True))
        # Simulate user reply
        hook._on_inbound(topics.AGENT_CHAT_INBOUND, _inbound_payload("ok"))
        store.update_node_status.assert_called_with("n10", NodeStatus.RUNNING)

    def test_inbound_with_no_pending_does_not_crash(self) -> None:
        hook, client, store = _make_hook([])
        hook.start()
        # No presence event → no pending loaded
        hook._on_inbound(topics.AGENT_CHAT_INBOUND, _inbound_payload("hello"))
        store.update_node_status.assert_not_called()

    def test_stop_prevents_processing(self) -> None:
        node = _make_node("nx")
        hook, client, store = _make_hook([node])
        hook.start()
        hook.stop()
        hook._on_presence("system/wui/presence", _presence_payload(active=True))
        client.publish_bytes.assert_not_called()
