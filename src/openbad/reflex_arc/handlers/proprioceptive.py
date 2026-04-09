"""Proprioceptive block reflex handler.

Fires when a tool/subsystem becomes UNAVAILABLE during an active task.
Mission-critical tool loss transitions the FSM to THROTTLED; non-critical
losses log a warning.  Graceful degradation suggests alternative tools
via the event bus.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable

from openbad.nervous_system.schemas.common_pb2 import Header
from openbad.nervous_system.schemas.reflex_pb2 import ReflexResult

logger = logging.getLogger(__name__)

STATE_TOPIC = "agent/proprioception/state"
RESULT_TOPIC = "agent/reflex/proprioceptive/result"
DEGRADATION_TOPIC = "agent/reflex/proprioceptive/degradation"


class ProprioceptiveHandler:
    """Reacts to proprioceptive state changes (tool availability).

    Parameters
    ----------
    critical_tools:
        Set of tool names whose loss triggers FSM throttle.
    alternatives:
        Mapping of ``tool_name → [fallback_tool, ...]`` for graceful
        degradation.
    fsm:
        Object with ``fire(trigger_name)`` method for FSM integration.
    publish_fn:
        Callable ``(topic, data)`` for event publishing.
    """

    def __init__(
        self,
        critical_tools: set[str] | None = None,
        alternatives: dict[str, list[str]] | None = None,
        fsm: object | None = None,
        publish_fn: Callable[[str, bytes], None] | None = None,
    ) -> None:
        self._critical_tools: set[str] = critical_tools or set()
        self._alternatives: dict[str, list[str]] = alternatives or {}
        self._fsm = fsm
        self._publish_fn = publish_fn
        self._lock = threading.Lock()
        self._aborted: set[str] = set()

    # -- public API ---------------------------------------------------------

    def handle_state_change(self, payload: bytes) -> bool:
        """Process a proprioceptive state snapshot.

        Returns ``True`` if a reflex action was taken, ``False`` otherwise.
        """
        try:
            snapshot = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid proprioceptive state payload")
            return False

        acted = False
        for tool_name, info in snapshot.items():
            status = info if isinstance(info, str) else info.get("status", "")
            if status != "UNAVAILABLE":
                continue

            is_critical = tool_name in self._critical_tools

            with self._lock:
                self._aborted.add(tool_name)

            if is_critical:
                logger.warning(
                    "Mission-critical tool %s lost — throttling",
                    tool_name,
                )
                if self._fsm is not None:
                    try:
                        self._fsm.fire("throttle")
                    except Exception:
                        logger.exception("FSM throttle failed")
            else:
                logger.warning(
                    "Non-critical tool %s unavailable",
                    tool_name,
                )

            self._publish_result(tool_name, is_critical)

            # Graceful degradation
            alts = self._alternatives.get(tool_name, [])
            if alts:
                self._publish_degradation(tool_name, alts)

            acted = True

        return acted

    @property
    def aborted_tools(self) -> frozenset[str]:
        """Tools whose in-flight actions have been aborted."""
        with self._lock:
            return frozenset(self._aborted)

    def clear_aborted(self, tool_name: str) -> None:
        """Clear abort status when a tool recovers."""
        with self._lock:
            self._aborted.discard(tool_name)

    # -- MQTT integration ---------------------------------------------------

    def subscribe(self, client: object) -> None:
        """Subscribe to proprioceptive state changes."""
        client.subscribe(STATE_TOPIC, self.handle_state_change)  # type: ignore[attr-defined]

    # -- internal -----------------------------------------------------------

    def _publish_result(self, tool_name: str, is_critical: bool) -> None:
        if self._publish_fn is None:
            return
        action = (
            f"ABORT in-flight for {tool_name}; FSM → THROTTLED"
            if is_critical
            else f"ABORT in-flight for {tool_name}; logged warning"
        )
        msg = ReflexResult(
            header=Header(timestamp_unix=time.time()),
            reflex_id="proprioceptive_block",
            handled=True,
            action_taken=action,
        )
        try:
            self._publish_fn(RESULT_TOPIC, msg.SerializeToString())
        except Exception:
            logger.exception("Failed to publish reflex result")

    def _publish_degradation(
        self,
        lost_tool: str,
        alternatives: list[str],
    ) -> None:
        if self._publish_fn is None:
            return
        payload = json.dumps(
            {"lost_tool": lost_tool, "alternatives": alternatives},
        ).encode()
        try:
            self._publish_fn(DEGRADATION_TOPIC, payload)
        except Exception:
            logger.exception("Failed to publish degradation notice")
