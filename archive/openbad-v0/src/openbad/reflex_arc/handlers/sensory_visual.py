"""Visual attention reflex handler.

Processes :class:`AttentionTrigger` events from the sensory dispatcher
and decides whether to escalate to the cognitive engine (System 2).

Examples of escalation-worthy visual triggers:
- Error dialog detected on screen
- Critical UI element changed
- Rapid successive attention spikes
"""

from __future__ import annotations

import logging
from typing import Any

from openbad.nervous_system.schemas import AttentionTrigger

logger = logging.getLogger(__name__)

# SSIM delta above which an event is considered critical
DEFAULT_CRITICAL_DELTA = 0.5


class VisualReflexHandler:
    """Handle visual attention triggers from the sensory dispatcher.

    Parameters
    ----------
    escalation_gw : object | None
        Optional :class:`EscalationGateway` for System 1→2 escalation.
    critical_delta : float
        SSIM delta above which the event triggers escalation.
    """

    def __init__(
        self,
        escalation_gw: Any | None = None,
        critical_delta: float = DEFAULT_CRITICAL_DELTA,
    ) -> None:
        self._escalation = escalation_gw
        self._critical_delta = critical_delta
        self._trigger_count: int = 0
        self._escalation_count: int = 0

    @property
    def trigger_count(self) -> int:
        return self._trigger_count

    @property
    def escalation_count(self) -> int:
        return self._escalation_count

    def handle(self, trigger: AttentionTrigger) -> bool:
        """Evaluate an attention trigger and optionally escalate.

        Returns True if the event was handled (always True for valid
        triggers — escalation is an additional action).
        """
        self._trigger_count += 1

        if trigger.ssim_delta >= self._critical_delta:
            self._escalation_count += 1
            if self._escalation is not None:
                self._escalation.escalate(
                    event_topic="agent/reflex/attention/trigger",
                    event_payload=trigger.SerializeToString(),
                    reason=f"High visual change: ssim_delta={trigger.ssim_delta:.3f}",
                    reflex_id="sensory/visual",
                )

        return True
