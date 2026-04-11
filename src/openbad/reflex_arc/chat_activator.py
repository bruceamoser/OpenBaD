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
