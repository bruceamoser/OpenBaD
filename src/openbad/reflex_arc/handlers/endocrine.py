"""Endocrine reflex handlers — deterministic responses to hormone thresholds.

Handles four scenarios without LLM involvement:

1. **High cortisol**: Throttle non-essential systems, create monitoring task
2. **Adrenaline spike**: Emergency mode, alert doctor
3. **Endorphin release**: Trigger sleep/maintenance cycle
4. **Immune alert**: Quarantine source + escalate to Internal Crew
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

# Thresholds (severity 3 = CRITICAL in FSM)
CORTISOL_THROTTLE: float = 0.70
ADRENALINE_EMERGENCY: float = 0.80
ENDORPHIN_SLEEP: float = 0.60


@dataclass
class ReflexAction:
    """Result of a deterministic reflex handler."""

    action: str  # "task", "research", "mqtt", "escalate"
    title: str
    description: str
    metadata: dict[str, Any]


def handle_high_cortisol(
    level: float,
    *,
    task_store: Any | None = None,
    publish_fn: Any | None = None,
) -> list[ReflexAction]:
    """React to critical cortisol levels."""
    actions: list[ReflexAction] = []
    if level < CORTISOL_THROTTLE:
        return actions

    # Create monitoring task
    if task_store is not None:
        from openbad.tasks.models import TaskKind, TaskModel, TaskPriority

        task = TaskModel.new(
            title="Cortisol recovery: monitor system load",
            description=(
                f"Cortisol level at {level:.2f} exceeded threshold "
                f"{CORTISOL_THROTTLE:.2f}. Monitor CPU/memory and "
                "verify non-essential systems are throttled."
            ),
            kind=TaskKind.SYSTEM,
            priority=int(TaskPriority.HIGH),
            owner="reflex-arc",
        )
        task_store.create_task(task)
        actions.append(
            ReflexAction(
                action="task",
                title=task.title,
                description=task.description,
                metadata={"cortisol": level},
            )
        )

    # Publish throttle event
    if publish_fn is not None:
        publish_fn(
            "agent/reflex/throttle",
            f'{{"source":"cortisol","level":{level:.2f}}}'.encode(),
        )
        actions.append(
            ReflexAction(
                action="mqtt",
                title="Throttle notification",
                description=f"Published throttle event (cortisol={level:.2f})",
                metadata={"topic": "agent/reflex/throttle"},
            )
        )

    log.info("Cortisol reflex: level=%.2f, actions=%d", level, len(actions))
    return actions


def handle_adrenaline_spike(
    level: float,
    *,
    task_store: Any | None = None,
    publish_fn: Any | None = None,
) -> list[ReflexAction]:
    """React to critical adrenaline levels."""
    actions: list[ReflexAction] = []
    if level < ADRENALINE_EMERGENCY:
        return actions

    # Create doctor call task
    if task_store is not None:
        from openbad.tasks.models import TaskKind, TaskModel, TaskPriority

        task = TaskModel.new(
            title="Emergency: adrenaline spike detected",
            description=(
                f"Adrenaline at {level:.2f} — potential threat or "
                "critical error. Doctor evaluation required."
            ),
            kind=TaskKind.SYSTEM,
            priority=int(TaskPriority.CRITICAL),
            owner="reflex-arc",
        )
        task_store.create_task(task)
        actions.append(
            ReflexAction(
                action="task",
                title=task.title,
                description=task.description,
                metadata={"adrenaline": level},
            )
        )

    # Alert doctor via MQTT
    if publish_fn is not None:
        publish_fn(
            "agent/doctor/call",
            (
                f'{{"source":"reflex-arc",'
                f'"reason":"Adrenaline spike ({level:.2f})"}}'
            ).encode(),
        )
        actions.append(
            ReflexAction(
                action="mqtt",
                title="Doctor alert",
                description=f"Published doctor call (adrenaline={level:.2f})",
                metadata={"topic": "agent/doctor/call"},
            )
        )

    log.info(
        "Adrenaline reflex: level=%.2f, actions=%d", level, len(actions)
    )
    return actions


def handle_endorphin_release(
    level: float,
    *,
    research_store: Any | None = None,
    publish_fn: Any | None = None,
) -> list[ReflexAction]:
    """React to endorphin release — trigger sleep/maintenance."""
    actions: list[ReflexAction] = []
    if level < ENDORPHIN_SLEEP:
        return actions

    # Create maintenance research entry
    if research_store is not None:
        research_store.enqueue(
            "Sleep cycle: memory consolidation",
            description=(
                "Endorphin-triggered maintenance cycle. "
                "Consolidate recent memories, prune stale entries, "
                "and update semantic indices."
            ),
        )
        actions.append(
            ReflexAction(
                action="research",
                title="Sleep cycle: memory consolidation",
                description="Endorphin-triggered maintenance",
                metadata={"endorphin": level},
            )
        )

    # Publish sleep trigger
    if publish_fn is not None:
        publish_fn(
            "agent/endocrine/endorphin",
            f'{{"level":{level:.2f},"severity":3}}'.encode(),
        )
        actions.append(
            ReflexAction(
                action="mqtt",
                title="Sleep trigger",
                description=f"Published endorphin event (level={level:.2f})",
                metadata={"topic": "agent/endocrine/endorphin"},
            )
        )

    log.info(
        "Endorphin reflex: level=%.2f, actions=%d", level, len(actions)
    )
    return actions


def handle_immune_alert(
    source_id: str,
    threat_type: str,
    *,
    task_store: Any | None = None,
    escalation_gw: Any | None = None,
    publish_fn: Any | None = None,
) -> list[ReflexAction]:
    """React to immune alert — quarantine + escalate."""
    actions: list[ReflexAction] = []

    # Create quarantine task
    if task_store is not None:
        from openbad.tasks.models import TaskKind, TaskModel, TaskPriority

        task = TaskModel.new(
            title=f"Quarantine: {threat_type} from {source_id}",
            description=(
                f"Immune alert: {threat_type} detected from {source_id}. "
                "Isolate the source and assess impact."
            ),
            kind=TaskKind.SYSTEM,
            priority=int(TaskPriority.CRITICAL),
            owner="reflex-arc",
        )
        task_store.create_task(task)
        actions.append(
            ReflexAction(
                action="task",
                title=task.title,
                description=task.description,
                metadata={
                    "source_id": source_id,
                    "threat_type": threat_type,
                },
            )
        )

    # Escalate to Internal Crew
    if escalation_gw is not None:
        escalation_gw.escalate(
            event_topic="agent/immune/alert",
            event_payload=f'{{"source_id":"{source_id}","threat_type":"{threat_type}"}}'.encode(),
            reason=f"Immune alert: {threat_type} from {source_id}",
            reflex_id="immune_handler",
            current_state="EMERGENCY",
        )
        actions.append(
            ReflexAction(
                action="escalate",
                title="Escalated to cognitive engine",
                description=f"{threat_type} from {source_id}",
                metadata={"source_id": source_id},
            )
        )

    log.info(
        "Immune reflex: source=%s, type=%s, actions=%d",
        source_id, threat_type, len(actions),
    )
    return actions
