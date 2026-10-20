"""Budget exhaustion reflex handler.

Subscribes to ``agent/endocrine/cortisol`` and reacts to **critical**
cortisol events whose metric is ``token_budget_remaining_pct``.

On trigger the handler:

1. Sets a thread-safe *blocked* flag that prevents new LLM API calls.
2. Fires the ``throttle`` trigger on the FSM to enter THROTTLED state.
3. Publishes a :class:`ReflexResult` to ``agent/reflex/budget/result``.

A recovery path clears the block when the budget is available again.

Pure Python — no LLM calls, no external service dependencies.
"""

from __future__ import annotations

import logging
import threading
import time

from openbad.nervous_system.schemas.common_pb2 import Header
from openbad.nervous_system.schemas.endocrine_pb2 import EndocrineEvent
from openbad.nervous_system.schemas.reflex_pb2 import ReflexResult

logger = logging.getLogger(__name__)

# Topics
CORTISOL_TOPIC = "agent/endocrine/cortisol"
RESULT_TOPIC = "agent/reflex/budget/result"

# Only react to this metric
_BUDGET_METRIC = "token_budget_remaining_pct"

# Severity
_CRITICAL = 3


class BudgetGuard:
    """Thread-safe gate that blocks new API calls when the budget is exhausted.

    Parameters
    ----------
    fsm:
        Optional :class:`AgentFSM` instance.  If provided, the guard will
        fire the ``throttle`` trigger on exhaustion and ``recover`` on
        recovery.
    publish_fn:
        Optional callable ``(topic, data)`` for publishing results.
    """

    def __init__(
        self,
        fsm: object | None = None,
        publish_fn: callable | None = None,  # type: ignore[valid-type]
    ) -> None:
        self._lock = threading.Lock()
        self._blocked = False
        self._fsm = fsm
        self._publish_fn = publish_fn

    # -- public API ---------------------------------------------------------

    @property
    def blocked(self) -> bool:
        """Return ``True`` if new API calls are currently blocked."""
        return self._blocked

    def is_call_allowed(self) -> bool:
        """Check whether a new LLM API call is permitted."""
        return not self._blocked

    def handle_cortisol(self, payload: bytes) -> bool:
        """Evaluate a cortisol event and block if budget is exhausted.

        Returns ``True`` if the handler fired.
        """
        event = EndocrineEvent()
        event.ParseFromString(payload)

        if event.severity != _CRITICAL:
            return False
        if event.metric_name != _BUDGET_METRIC:
            return False

        self._exhaust(event)
        return True

    def recover(self) -> bool:
        """Clear the block and resume API calls.

        Returns ``True`` if state actually changed (was blocked).
        """
        with self._lock:
            if not self._blocked:
                return False
            self._blocked = False

        if self._fsm is not None:
            try:
                self._fsm.fire("recover")  # type: ignore[union-attr]
            except Exception:
                logger.exception("FSM recover trigger failed")

        logger.info("Budget guard: API calls resumed")
        return True

    # -- MQTT integration ---------------------------------------------------

    def subscribe(self, client: object) -> None:
        """Subscribe to cortisol events via an MQTT *client*."""

        def _callback(_topic: str, payload: bytes) -> None:
            self.handle_cortisol(payload)

        client.subscribe(CORTISOL_TOPIC, _callback)  # type: ignore[union-attr]

    # -- internals ----------------------------------------------------------

    def _exhaust(self, event: EndocrineEvent) -> None:
        with self._lock:
            if self._blocked:
                return  # already in exhaustion mode
            self._blocked = True

        # Transition FSM to THROTTLED
        if self._fsm is not None:
            try:
                self._fsm.fire("throttle")  # type: ignore[union-attr]
            except Exception:
                logger.exception("FSM throttle trigger failed")

        # Publish result
        if self._publish_fn is not None:
            result = ReflexResult(
                header=Header(timestamp_unix=time.time()),
                reflex_id="budget_exhaustion",
                handled=True,
                action_taken=(f"Blocked new API calls (budget_remaining={event.metric_value}%)"),
            )
            try:
                self._publish_fn(RESULT_TOPIC, result.SerializeToString())
            except Exception:
                logger.exception("Failed to publish budget result")

        logger.info(
            "Budget exhaustion handler fired: %s=%.2f",
            event.metric_name,
            event.metric_value,
        )
