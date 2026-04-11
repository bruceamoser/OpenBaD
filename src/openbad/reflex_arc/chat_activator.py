"""Chat activity FSM activator.

Subscribes to COGNITIVE_INPUT events from the nervous system and triggers
FSM activation when user chat activity is detected. Manages idle timeout
to transition back to IDLE when user stops interacting.

Design
------
- On COGNITIVE_INPUT → trigger activate if FSM is in IDLE or SLEEP
- On idle timeout (configurable) → trigger deactivate (ACTIVE → IDLE)
- Debounce rapid messages to prevent spam activations
- Respect EMERGENCY state (do not override)
- THROTTLED → ACTIVE transition allowed (user takes priority)

Integration
-----------
- Subscribe to topics.COGNITIVE_INPUT
- Read current FSM state from topics.REFLEX_STATE
- Call fsm.fire("activate") or fsm.fire("deactivate") directly
- Not a daemon subprocess — instantiated by main daemon or tests
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from openbad.nervous_system.client import NervousSystemClient
    from openbad.reflex_arc.fsm import AgentFSM
    from openbad.tasks.models import NodeModel
    from openbad.tasks.store import TaskStore

logger = logging.getLogger(__name__)

DEFAULT_IDLE_TIMEOUT = 300  # 5 minutes
DEFAULT_DEBOUNCE_WINDOW = 2  # 2 seconds


def load_config(config_path: Path | None = None) -> dict[str, int]:
    """Load FSM config from yaml, return dict with idle_timeout and debounce_window."""
    if config_path is None:
        # Try system path first, then fallback to local config
        config_path = Path("/etc/openbad/fsm.yaml")
        try:
            if not config_path.exists():
                config_path = Path(__file__).parents[3] / "config" / "fsm.yaml"
        except (PermissionError, OSError):
            # Can't access /etc/openbad, use local config
            config_path = Path(__file__).parents[3] / "config" / "fsm.yaml"

    try:
        if not config_path.exists():
            logger.warning("FSM config not found at %s, using defaults", config_path)
            return {
                "idle_timeout_seconds": DEFAULT_IDLE_TIMEOUT,
                "debounce_window_seconds": DEFAULT_DEBOUNCE_WINDOW,
            }
    except (PermissionError, OSError):
        logger.warning("Cannot access FSM config at %s, using defaults", config_path)
        return {
            "idle_timeout_seconds": DEFAULT_IDLE_TIMEOUT,
            "debounce_window_seconds": DEFAULT_DEBOUNCE_WINDOW,
        }

    with config_path.open("r") as f:
        data = yaml.safe_load(f) or {}

    return {
        "idle_timeout_seconds": data.get("idle_timeout_seconds", DEFAULT_IDLE_TIMEOUT),
        "debounce_window_seconds": data.get(
            "debounce_window_seconds", DEFAULT_DEBOUNCE_WINDOW
        ),
    }


class ChatActivator:
    """Monitors COGNITIVE_INPUT events and activates FSM on chat activity."""

    def __init__(
        self,
        fsm: AgentFSM,
        client: NervousSystemClient,
        config_path: Path | None = None,
    ) -> None:
        self.fsm = fsm
        self.client = client
        self._config = load_config(config_path)
        self._idle_timeout = self._config["idle_timeout_seconds"]
        self._debounce_window = self._config["debounce_window_seconds"]

        self._last_activity: float = 0.0
        self._last_activate: float = 0.0
        self._idle_timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._running = False

    def start(self) -> None:
        """Start subscribing to COGNITIVE_INPUT events."""
        from openbad.nervous_system import topics

        self._running = True
        self.client.subscribe(topics.COGNITIVE_INPUT, self._on_cognitive_input)
        logger.info("ChatActivator started (idle_timeout=%ds)", self._idle_timeout)

    def stop(self) -> None:
        """Stop monitoring and cancel any pending timers."""
        self._running = False
        with self._lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
        logger.info("ChatActivator stopped")

    def _on_cognitive_input(self, topic: str, payload: bytes) -> None:
        """Handle COGNITIVE_INPUT event: activate FSM and reset idle timer."""
        if not self._running:
            return

        # Parse JSON payload for timestamp (optional)
        try:
            data = json.loads(payload.decode())
            logger.debug("COGNITIVE_INPUT: %s", data.get("source", "unknown"))
        except Exception:
            logger.debug("COGNITIVE_INPUT: received raw payload")

        now = time.time()
        with self._lock:
            self._last_activity = now

            # Debounce: skip activation if within debounce window
            if (now - self._last_activate) < self._debounce_window:
                logger.debug("ChatActivator: debounce skip")
                self._reset_idle_timer()
                return

            # Check FSM state before activating
            current_state = getattr(self.fsm, "state", "UNKNOWN")

            # EMERGENCY: never override
            if current_state == "EMERGENCY":
                logger.debug("ChatActivator: FSM in EMERGENCY, skip activate")
                return

            # ACTIVE: already active, just reset timer
            if current_state == "ACTIVE":
                logger.debug("ChatActivator: already ACTIVE, reset timer")
                self._reset_idle_timer()
                return

            # IDLE, SLEEP, THROTTLED → activate
            if current_state in ("IDLE", "SLEEP", "THROTTLED"):
                success = self.fsm.fire("activate")
                if success:
                    logger.info("ChatActivator: activated FSM from %s", current_state)
                    self._last_activate = now
                else:
                    logger.warning(
                        "ChatActivator: failed to activate from %s", current_state
                    )

            # Reset idle timer regardless
            self._reset_idle_timer()

    def _reset_idle_timer(self) -> None:
        """Reset the idle timeout timer (cancel old, start new)."""
        # Cancel existing timer
        if self._idle_timer is not None:
            self._idle_timer.cancel()

        # Start new timer
        self._idle_timer = threading.Timer(self._idle_timeout, self._on_idle_timeout)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def _on_idle_timeout(self) -> None:
        """Called when idle timeout expires: deactivate FSM if ACTIVE."""
        if not self._running:
            return

        with self._lock:
            current_state = getattr(self.fsm, "state", "UNKNOWN")

            # Only deactivate if currently ACTIVE
            if current_state == "ACTIVE":
                success = self.fsm.fire("deactivate")
                if success:
                    logger.info("ChatActivator: idle timeout, deactivated FSM")
                else:
                    logger.warning("ChatActivator: failed to deactivate from ACTIVE")
            else:
                logger.debug(
                    "ChatActivator: idle timeout but FSM not ACTIVE (state=%s)",
                    current_state,
                )


# ---------------------------------------------------------------------------
# Re-engagement hook (Phase 10, Issue #416)
# ---------------------------------------------------------------------------


class ReEngagementHook:
    """Surfaces BLOCKED_ON_USER questions when the WUI reconnects.

    On a WUI presence flip to ``active=True``, queries the ``TaskStore`` for
    all nodes in the ``BLOCKED_ON_USER`` state and publishes their pending
    questions to ``agent/chat/response`` *before* the standard greeting.

    When the user answers, the reply published to ``agent/chat/inbound``
    transitions the first pending node from ``BLOCKED_ON_USER`` to
    ``RUNNING``.

    Parameters
    ----------
    client:
        Live :class:`~openbad.nervous_system.client.NervousSystemClient`.
    store:
        :class:`~openbad.tasks.store.TaskStore` backed by the task database.
    greeting:
        Optional greeting message sent after pending questions.
    """

    def __init__(
        self,
        client: NervousSystemClient,
        store: TaskStore,
        *,
        greeting: str = "Hello — I'm back. What would you like to work on?",
    ) -> None:
        self._client = client
        self._store = store
        self._greeting = greeting
        self._pending: list[NodeModel] = []
        self._lock = threading.Lock()
        self._running = False

    def start(self) -> None:
        """Subscribe to presence events and inbound chat replies."""
        from openbad.nervous_system import topics

        self._running = True
        self._client.subscribe(topics.WUI_PRESENCE, bytes, self._on_presence)
        self._client.subscribe(topics.AGENT_CHAT_INBOUND, bytes, self._on_inbound)
        logger.info("ReEngagementHook started")

    def stop(self) -> None:
        """Unsubscribe and clear pending queue."""
        from openbad.nervous_system import topics

        self._running = False
        self._client.unsubscribe(topics.WUI_PRESENCE)
        self._client.unsubscribe(topics.AGENT_CHAT_INBOUND)
        with self._lock:
            self._pending.clear()
        logger.info("ReEngagementHook stopped")

    # ------------------------------------------------------------------
    # Internal handlers
    # ------------------------------------------------------------------

    def _on_presence(self, _topic: str, payload: bytes) -> None:
        """Handle WUI_PRESENCE events; re-engage on active=True."""
        if not self._running:
            return
        try:
            body = json.loads(payload)
            if not body.get("active", False):
                return
        except Exception:
            logger.debug("ReEngagementHook: could not parse presence payload")
            return

        self._load_pending()
        self._surface_pending()

    def _on_inbound(self, _topic: str, payload: bytes) -> None:
        """Handle user reply; unblock the first pending node."""
        if not self._running:
            return

        with self._lock:
            if not self._pending:
                return
            node = self._pending.pop(0)

        try:
            self._store.update_node_status(node.node_id, _RUNNING)
            logger.info(
                "ReEngagementHook: node %s unblocked (→ RUNNING)", node.node_id
            )
        except Exception:
            logger.exception(
                "ReEngagementHook: could not update node %s to RUNNING", node.node_id
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_pending(self) -> None:
        """Query the store for BLOCKED_ON_USER nodes, ordered by priority."""
        from openbad.tasks.models import NodeStatus

        try:
            nodes = self._store.list_nodes_by_status(NodeStatus.BLOCKED_ON_USER)
        except Exception:
            logger.exception("ReEngagementHook: could not query pending nodes")
            return

        with self._lock:
            # Sort by task priority proxy (node_id is UUID — use created_at ordering
            # already returned DESC from the store; reverse to oldest-first so we
            # surface the earliest blocked question first).
            self._pending = list(reversed(nodes))

    def _surface_pending(self) -> None:
        """Publish pending questions to the chat feed, then send the greeting."""
        from openbad.nervous_system import topics

        with self._lock:
            pending_snapshot = list(self._pending)

        for node in pending_snapshot:
            payload = json.dumps(
                {
                    "question": node.title,
                    "task_id": node.task_id,
                    "node_id": node.node_id,
                    "type": "pending_question",
                }
            ).encode()
            try:
                self._client.publish_bytes(topics.AGENT_CHAT_RESPONSE, payload)
            except Exception:
                logger.exception(
                    "ReEngagementHook: could not publish question for node %s",
                    node.node_id,
                )

        if not pending_snapshot:
            # No blocked questions — send standard greeting directly.
            self._send_greeting()
        else:
            # Greeting follows after questions.
            self._send_greeting()

    def _send_greeting(self) -> None:
        from openbad.nervous_system import topics

        payload = json.dumps({"text": self._greeting, "type": "greeting"}).encode()
        try:
            self._client.publish_bytes(topics.AGENT_CHAT_RESPONSE, payload)
        except Exception:
            logger.debug("ReEngagementHook: could not send greeting")


# Shorthand used within the hook — resolved at import time.
try:
    from openbad.tasks.models import NodeStatus as _NodeStatus

    _RUNNING = _NodeStatus.RUNNING
except ImportError:  # pragma: no cover
    _RUNNING = "running"  # type: ignore[assignment]
