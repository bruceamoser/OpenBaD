"""Dual-mode user communication tool — Phase 10, Issue #415.

**Mode A (Active):** When a WUI session is live (``is_active = True``), publish
the question to :data:`~openbad.nervous_system.topics.AGENT_CHAT_RESPONSE` and
block up to *timeout* seconds waiting for a reply from
:data:`~openbad.nervous_system.topics.AGENT_CHAT_INBOUND`.

**Mode B (Inactive / Timeout):** When no session is active, or when Mode A
times out, mark the task node ``BLOCKED_ON_USER`` via the :class:`TaskStore`,
then publish the full question payload to
:data:`~openbad.nervous_system.topics.AGENT_ESCALATION` so a re-engagement
hook can resurface it later.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from openbad.nervous_system import topics
from openbad.tasks.models import NodeStatus

if TYPE_CHECKING:
    from openbad.nervous_system.client import NervousSystemClient
    from openbad.tasks.store import TaskStore

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public sentinel for deferred (Mode B) returns
# ---------------------------------------------------------------------------

DEFERRED: str = "__deferred__"
"""Sentinel returned when the question is deferred to escalation."""

# ---------------------------------------------------------------------------
# Payload schemas
# ---------------------------------------------------------------------------


@dataclass
class QuestionPayload:
    """The message published to ``agent/chat/response`` for active users.

    Also used as the body of the ``agent/escalation`` message when the task
    is blocked.
    """

    question: str
    task_id: str | None
    node_id: str | None
    timeout: float
    extra: dict = field(default_factory=dict)

    def to_json(self) -> bytes:
        return json.dumps(
            {
                "question": self.question,
                "task_id": self.task_id,
                "node_id": self.node_id,
                "timeout": self.timeout,
                **self.extra,
            }
        ).encode()


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------


def ask_user(
    question: str,
    *,
    mqtt: NervousSystemClient | None = None,
    wui_is_active: bool = False,
    task_id: str | None = None,
    node_id: str | None = None,
    store: TaskStore | None = None,
    timeout: float = 30.0,
) -> str:
    """Ask the user a question and return the answer or :data:`DEFERRED`.

    Parameters
    ----------
    question:
        The question text to present to the user.
    mqtt:
        Live :class:`~openbad.nervous_system.client.NervousSystemClient`.
        When ``None`` the function runs in headless/test mode (no MQTT
        messages are sent).
    wui_is_active:
        Whether the WUI session is currently live (from
        :attr:`~openbad.wui.bridge.UserSession.is_active`).
    task_id:
        ID of the task that triggered this question.
    node_id:
        ID of the task node that triggered this question.
    store:
        Optional :class:`~openbad.tasks.store.TaskStore`.  When provided,
        the node status is updated to
        :attr:`~openbad.tasks.models.NodeStatus.BLOCKED_ON_USER` in Mode B.
    timeout:
        Seconds to wait for a reply in Mode A before falling back to Mode B.

    Returns
    -------
    str
        The user's reply text (Mode A), or :data:`DEFERRED` (Mode B / timeout).
    """
    payload = QuestionPayload(
        question=question,
        task_id=task_id,
        node_id=node_id,
        timeout=timeout,
    )

    if wui_is_active:
        answer = _active_mode(payload, mqtt, timeout)
        if answer is not None:
            return answer
        # Timed out → fall through to Mode B
        log.info("ask_user: Mode A timed out, falling back to Mode B")

    return _inactive_mode(payload, mqtt, store, node_id)


# ---------------------------------------------------------------------------
# Mode helpers
# ---------------------------------------------------------------------------


def _active_mode(
    payload: QuestionPayload,
    mqtt: NervousSystemClient | None,
    timeout: float,
) -> str | None:
    """Publish the question and wait for a reply.  Returns ``None`` on timeout."""
    event = threading.Event()
    container: list[str] = []

    def _on_inbound(topic: str, raw: bytes) -> None:  # noqa: ARG001
        try:
            body = json.loads(raw)
            answer = body.get("answer") or body.get("text") or str(body)
        except Exception:
            answer = raw.decode(errors="replace")
        container.append(answer)
        event.set()

    if mqtt is not None:
        # Subscribe to raw bytes (not protobuf) on the inbound topic.
        # We use a raw bytes callback: NervousSystemClient.subscribe expects a
        # message_type, so we work around it with publish_bytes / a wrapper.
        # The bridge sends plain JSON on AGENT_CHAT_INBOUND. We temporarily
        # register a side-channel via a threading.Event.
        mqtt.subscribe(topics.AGENT_CHAT_INBOUND, bytes, _on_inbound)
        try:
            mqtt.publish_bytes(topics.AGENT_CHAT_RESPONSE, payload.to_json())
            event.wait(timeout=timeout)
        finally:
            mqtt.unsubscribe(topics.AGENT_CHAT_INBOUND)
    else:
        log.debug("ask_user: no MQTT client; skipping active-mode publish")

    return container[0] if container else None


def _inactive_mode(
    payload: QuestionPayload,
    mqtt: NervousSystemClient | None,
    store: TaskStore | None,
    node_id: str | None,
) -> str:
    """Block the node and publish escalation.  Returns :data:`DEFERRED`."""
    if store is not None and node_id is not None:
        try:
            store.update_node_status(node_id, NodeStatus.BLOCKED_ON_USER)
        except Exception:
            log.exception("ask_user: could not update node status to BLOCKED_ON_USER")

    if mqtt is not None:
        try:
            mqtt.publish_bytes(topics.AGENT_ESCALATION, payload.to_json())
        except Exception:
            log.exception("ask_user: could not publish escalation message")

    log.info(
        "ask_user: node %s deferred (BLOCKED_ON_USER), question=%r",
        node_id or "?",
        payload.question[:80],
    )
    return DEFERRED
