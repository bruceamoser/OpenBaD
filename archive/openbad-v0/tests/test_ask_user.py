"""Tests for dual-mode ask_user tool — Issue #415."""

from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock

from openbad.nervous_system import topics
from openbad.tasks.models import NodeStatus
from openbad.skills.ask_user import (
    DEFERRED,
    QuestionPayload,
    ask_user,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mqtt_stub(*, reply: str | None = None, reply_delay: float = 0.0) -> MagicMock:
    """Build an MQTT stub that optionally fires a reply on AGENT_CHAT_INBOUND."""
    mqtt = MagicMock()

    def _subscribe(topic: str, msg_type: type, callback, qos=None) -> None:  # noqa: ANN001
        if topic == topics.AGENT_CHAT_INBOUND and reply is not None:
            raw = json.dumps({"answer": reply}).encode()

            def _fire() -> None:
                import time

                time.sleep(reply_delay)
                callback(topic, raw)

            t = threading.Thread(target=_fire, daemon=True)
            t.start()

    mqtt.subscribe.side_effect = _subscribe
    return mqtt


# ---------------------------------------------------------------------------
# QuestionPayload
# ---------------------------------------------------------------------------


class TestQuestionPayload:
    def test_to_json_contains_all_fields(self) -> None:
        p = QuestionPayload(
            question="Are you sure?",
            task_id="t1",
            node_id="n1",
            timeout=15.0,
        )
        body = json.loads(p.to_json())
        assert body["question"] == "Are you sure?"
        assert body["task_id"] == "t1"
        assert body["node_id"] == "n1"
        assert body["timeout"] == 15.0

    def test_extra_fields_included(self) -> None:
        p = QuestionPayload(
            question="foo?",
            task_id=None,
            node_id=None,
            timeout=5.0,
            extra={"source": "test"},
        )
        body = json.loads(p.to_json())
        assert body["source"] == "test"


# ---------------------------------------------------------------------------
# Mode A: active session
# ---------------------------------------------------------------------------


class TestActiveModeReturnsAnswer:
    def test_returns_answer_when_reply_arrives(self) -> None:
        mqtt = _mqtt_stub(reply="yes")
        result = ask_user(
            "Continue?",
            mqtt=mqtt,
            wui_is_active=True,
            task_id="t1",
            node_id="n1",
            timeout=2.0,
        )
        assert result == "yes"
        topic_arg = mqtt.publish_bytes.call_args[0][0]
        assert topic_arg == topics.AGENT_CHAT_RESPONSE

    def test_publishes_question_payload(self) -> None:
        mqtt = _mqtt_stub(reply="ack")
        ask_user(
            "OK?",
            mqtt=mqtt,
            wui_is_active=True,
            task_id="task-x",
            node_id="node-y",
            timeout=2.0,
        )
        # publish_bytes was called on AGENT_CHAT_RESPONSE
        topic_arg = mqtt.publish_bytes.call_args[0][0]
        assert topic_arg == topics.AGENT_CHAT_RESPONSE
        # payload includes task ctx
        payload_bytes = mqtt.publish_bytes.call_args[0][1]
        body = json.loads(payload_bytes)
        assert body["task_id"] == "task-x"
        assert body["node_id"] == "node-y"

    def test_unsubscribes_after_reply(self) -> None:
        mqtt = _mqtt_stub(reply="done")
        ask_user("Q?", mqtt=mqtt, wui_is_active=True, timeout=2.0)
        mqtt.unsubscribe.assert_called_with(topics.AGENT_CHAT_INBOUND)


# ---------------------------------------------------------------------------
# Mode A → fallback on timeout
# ---------------------------------------------------------------------------


class TestActiveModeTimeout:
    def test_timeout_falls_back_to_deferred(self) -> None:
        # No reply scheduled → timeout → Mode B
        mqtt = _mqtt_stub(reply=None)
        store = MagicMock()
        result = ask_user(
            "ETA?",
            mqtt=mqtt,
            wui_is_active=True,
            task_id="t2",
            node_id="n2",
            store=store,
            timeout=0.05,  # very short
        )
        assert result == DEFERRED

    def test_timeout_updates_node_to_blocked_on_user(self) -> None:
        mqtt = _mqtt_stub(reply=None)
        store = MagicMock()
        ask_user(
            "Status?",
            mqtt=mqtt,
            wui_is_active=True,
            task_id="t3",
            node_id="n3",
            store=store,
            timeout=0.05,
        )
        store.update_node_status.assert_called_once_with("n3", NodeStatus.BLOCKED_ON_USER)

    def test_timeout_publishes_to_escalation(self) -> None:
        mqtt = _mqtt_stub(reply=None)
        ask_user(
            "Waiting…",
            mqtt=mqtt,
            wui_is_active=True,
            task_id="t4",
            node_id="n4",
            timeout=0.05,
        )
        escalation_calls = [
            c for c in mqtt.publish_bytes.call_args_list if c[0][0] == topics.AGENT_ESCALATION
        ]
        assert len(escalation_calls) == 1


# ---------------------------------------------------------------------------
# Mode B: inactive session
# ---------------------------------------------------------------------------


class TestInactiveMode:
    def test_inactive_returns_deferred(self) -> None:
        mqtt = _mqtt_stub()
        result = ask_user(
            "Confirm?",
            mqtt=mqtt,
            wui_is_active=False,
            task_id="t5",
            node_id="n5",
            timeout=5.0,
        )
        assert result == DEFERRED

    def test_inactive_updates_node_status(self) -> None:
        mqtt = _mqtt_stub()
        store = MagicMock()
        ask_user(
            "Are you there?",
            mqtt=mqtt,
            wui_is_active=False,
            task_id="t6",
            node_id="n6",
            store=store,
        )
        store.update_node_status.assert_called_once_with("n6", NodeStatus.BLOCKED_ON_USER)

    def test_inactive_publishes_escalation(self) -> None:
        mqtt = _mqtt_stub()
        ask_user(
            "Need input",
            mqtt=mqtt,
            wui_is_active=False,
            task_id="t7",
            node_id="n7",
        )
        mqtt.publish_bytes.assert_called_once()
        topic_arg = mqtt.publish_bytes.call_args[0][0]
        assert topic_arg == topics.AGENT_ESCALATION

    def test_inactive_payload_includes_task_and_node(self) -> None:
        mqtt = _mqtt_stub()
        ask_user("?", mqtt=mqtt, wui_is_active=False, task_id="tX", node_id="nX")
        payload_bytes = mqtt.publish_bytes.call_args[0][1]
        body = json.loads(payload_bytes)
        assert body["task_id"] == "tX"
        assert body["node_id"] == "nX"

    def test_no_mqtt_returns_deferred_without_error(self) -> None:
        result = ask_user("Q?", mqtt=None, wui_is_active=False)
        assert result == DEFERRED
