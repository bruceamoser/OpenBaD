"""Thermal throttle reflex handler.

Subscribes to ``agent/endocrine/cortisol`` and reacts to **critical**
cortisol events whose metric is CPU or thermal related.  On trigger the
handler:

1. Publishes a suspend directive to ``agent/reflex/thermal/suspend``.
2. Publishes a cognitive downgrade directive (SLM-only) to
   ``agent/cognitive/routing/directive``.
3. Publishes a :class:`ReflexResult` to ``agent/reflex/thermal/result``.

Pure Python — no LLM calls, no external service dependencies.
"""

from __future__ import annotations

import logging
import time

from openbad.nervous_system.schemas.common_pb2 import Header
from openbad.nervous_system.schemas.endocrine_pb2 import EndocrineEvent
from openbad.nervous_system.schemas.reflex_pb2 import ReflexResult

logger = logging.getLogger(__name__)

# Topics
CORTISOL_TOPIC = "agent/endocrine/cortisol"
SUSPEND_TOPIC = "agent/reflex/thermal/suspend"
COGNITIVE_DIRECTIVE_TOPIC = "agent/cognitive/routing/directive"
RESULT_TOPIC = "agent/reflex/thermal/result"

# Metrics this handler cares about
_THERMAL_METRICS = frozenset({"cpu_percent", "thermal_celsius"})

# Severity threshold — only react to CRITICAL (3)
_CRITICAL = 3


def handle_cortisol(payload: bytes, publish_fn: callable) -> bool:  # type: ignore[valid-type]
    """Evaluate a cortisol event and fire thermal throttle if warranted.

    Parameters
    ----------
    payload:
        Serialised :class:`EndocrineEvent` protobuf bytes.
    publish_fn:
        Callable ``(topic: str, data: bytes) -> None`` used to publish
        outgoing directives.

    Returns
    -------
    bool
        ``True`` if the handler fired (critical thermal event), else ``False``.
    """
    event = EndocrineEvent()
    event.ParseFromString(payload)

    if event.severity != _CRITICAL:
        return False

    if event.metric_name not in _THERMAL_METRICS:
        return False

    _fire(event, publish_fn)
    return True


def _fire(event: EndocrineEvent, publish_fn: callable) -> None:  # type: ignore[valid-type]
    """Execute the thermal throttle response."""
    now = time.time()

    # 1. Suspend background tasks
    publish_fn(
        SUSPEND_TOPIC,
        b'{"action": "suspend", "reason": "thermal_throttle"}',
    )

    # 2. Downgrade cognitive routing to SLM-only
    publish_fn(
        COGNITIVE_DIRECTIVE_TOPIC,
        b'{"mode": "slm_only", "reason": "thermal_throttle"}',
    )

    # 3. Publish reflex result
    result = ReflexResult(
        header=Header(timestamp_unix=now),
        reflex_id="thermal_throttle",
        handled=True,
        action_taken=(
            f"Suspended background tasks; routed to SLM-only "
            f"(metric={event.metric_name}, value={event.metric_value})"
        ),
    )
    publish_fn(RESULT_TOPIC, result.SerializeToString())

    logger.info(
        "Thermal throttle fired: %s=%.2f",
        event.metric_name,
        event.metric_value,
    )


def subscribe(client: object) -> None:
    """Subscribe the thermal throttle handler to the cortisol topic.

    Parameters
    ----------
    client:
        MQTT client with ``subscribe(topic, callback)`` and
        ``publish(topic, data)`` methods.
    """

    def _callback(topic: str, payload: bytes) -> None:
        handle_cortisol(payload, client.publish)  # type: ignore[union-attr]

    client.subscribe(CORTISOL_TOPIC, _callback)  # type: ignore[union-attr]
