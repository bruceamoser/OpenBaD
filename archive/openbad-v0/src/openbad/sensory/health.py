"""Sensory health integration — registers modalities with proprioception.

Each enabled sensory modality (vision, hearing, speech) registers as a tool
in the :class:`ToolRegistry` with a health-check callback.  When a health
check fails, the tool transitions to DEGRADED and a telemetry event is
published on ``agent/telemetry/sensory_health``.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from openbad.nervous_system.topics import TELEMETRY_SENSORY_HEALTH
from openbad.proprioception.registry import HealthStatus, ToolRegistry, ToolRole

logger = logging.getLogger(__name__)

SENSORY_TOOLS = ("sensory.vision", "sensory.hearing", "sensory.speech")


@dataclass
class SensoryHealthEvent:
    """Published when a sensory modality health status changes."""

    modality: str
    status: str
    reason: str = ""
    timestamp: float = 0.0

    def to_bytes(self) -> bytes:
        if self.timestamp == 0.0:
            self.timestamp = time.time()
        return json.dumps({
            "modality": self.modality,
            "status": self.status,
            "reason": self.reason,
            "timestamp": self.timestamp,
        }).encode()


def register_sensory_tools(
    registry: ToolRegistry,
    *,
    vision_check: Callable[[], bool] | None = None,
    hearing_check: Callable[[], bool] | None = None,
    speech_check: Callable[[], bool] | None = None,
) -> None:
    """Register vision, hearing, and speech as proprioception tools."""
    registry.register(
        "sensory.vision",
        metadata={"modality": "vision"},
        health_check=vision_check,
        role=ToolRole.MEDIA,
    )
    registry.register(
        "sensory.hearing",
        metadata={"modality": "hearing"},
        health_check=hearing_check,
        role=ToolRole.MEDIA,
    )
    registry.register(
        "sensory.speech",
        metadata={"modality": "speech"},
        health_check=speech_check,
        role=ToolRole.MEDIA,
    )


def check_sensory_health(
    registry: ToolRegistry,
    publish_fn: Callable[[str, bytes], None] | None = None,
    cortisol_hook: Callable[[str, str], float] | None = None,
) -> list[str]:
    """Run health checks on sensory tools, publish events for degraded ones.

    Returns list of modality names that transitioned to DEGRADED.
    """
    degraded = registry.run_health_checks()
    sensory_degraded = [n for n in degraded if n in SENSORY_TOOLS]

    for name in sensory_degraded:
        modality = name.split(".")[-1]
        tool = next(
            (t for t in registry.get_all_tools() if t.name == name),
            None,
        )
        reason = (tool.metadata.get("degraded_reason", "") if tool else "")

        event = SensoryHealthEvent(
            modality=modality,
            status=HealthStatus.DEGRADED.value,
            reason=reason,
        )
        if publish_fn is not None:
            try:
                publish_fn(TELEMETRY_SENSORY_HEALTH, event.to_bytes())
            except Exception:
                logger.exception("Failed to publish sensory health event")

        if cortisol_hook is not None:
            try:
                cortisol_hook(name, reason)
            except Exception:
                logger.exception("Cortisol hook failed for %s", name)

    return sensory_degraded
