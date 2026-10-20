"""Internal Crew — Doctor + Immune agents for system health and security.

The crew operates in sequential mode:

1. **Immune Agent** scans for threats, anomalies, and policy violations.
2. **Doctor Agent** diagnoses issues flagged by the Immune Agent
   (or raised by endocrine signals) and recommends corrective actions.

Public API
----------
``create_internal_crew(alert_payload, *, adrenaline, llm_factory, tools_factory)``
    Build and return a ready-to-kickoff ``Crew``.
"""

from __future__ import annotations

import logging
from typing import Any

from crewai import Crew, Process, Task

from openbad.frameworks.agents.definitions import (
    AGENT_SPECS,
    create_agent,
)

log = logging.getLogger(__name__)

# Adrenaline threshold above which we expand the context budget.
ADRENALINE_THRESHOLD: float = 0.60

# ── Task templates ───────────────────────────────────────────────────── #

_IMMUNE_TASK_DESCRIPTION = (
    "Analyze the following system alert for threats, anomalies, "
    "or policy violations:\n\n"
    "---\n{alert_payload}\n---\n\n"
    "Determine whether the alert represents a genuine threat, "
    "a false positive, or an informational notice. If a threat "
    "is confirmed, describe its type, severity, and recommended "
    "quarantine actions."
)

_IMMUNE_TASK_EXPECTED = (
    "A threat assessment with: threat_detected (bool), "
    "threat_type, severity (low/medium/high/critical), "
    "recommended actions, and whether escalation to the "
    "Doctor Agent is needed."
)

_DOCTOR_TASK_DESCRIPTION = (
    "Review the Immune Agent's threat assessment and the "
    "current system health state:\n\n"
    "Adrenaline level: {adrenaline:.2f}\n"
    "Context budget: {context_budget}\n\n"
    "Diagnose the root cause, recommend corrective actions, "
    "and determine whether user intervention is required."
)

_DOCTOR_TASK_EXPECTED = (
    "A diagnostic report with: diagnosis, corrective actions, "
    "whether user escalation is needed, and updated system "
    "health recommendations."
)


# ── Crew factory ─────────────────────────────────────────────────────── #


def create_internal_crew(
    alert_payload: str,
    *,
    adrenaline: float = 0.0,
    llm_factory: Any | None = None,
    tools_factory: Any | None = None,
) -> Crew:
    """Build the Internal crew for a system health/security alert.

    Parameters
    ----------
    alert_payload:
        The raw alert content (from MQTT topic or endocrine signal).
    adrenaline:
        Current adrenaline level (0.0–1.0). Above ``ADRENALINE_THRESHOLD``
        the crew gets an expanded context budget.
    llm_factory:
        ``(priority: str) -> llm`` or ``None`` for CrewAI defaults.
    tools_factory:
        ``(tool_role: str) -> list[tool]`` or ``None``.

    Returns
    -------
    Crew
        A configured crew ready for ``crew.kickoff()``.
    """
    context_budget = "expanded" if adrenaline > ADRENALINE_THRESHOLD else "normal"

    # ── agents ──
    immune_spec = AGENT_SPECS["immune"]
    doctor_spec = AGENT_SPECS["doctor"]

    immune_llm = llm_factory(immune_spec.priority) if llm_factory else None
    doctor_llm = llm_factory(doctor_spec.priority) if llm_factory else None
    immune_tools = tools_factory(immune_spec.tool_role) if tools_factory else None
    doctor_tools = tools_factory(doctor_spec.tool_role) if tools_factory else None

    immune_agent = create_agent("immune", llm=immune_llm, tools=immune_tools)
    doctor_agent = create_agent("doctor", llm=doctor_llm, tools=doctor_tools)

    # ── tasks ──
    immune_task = Task(
        description=_IMMUNE_TASK_DESCRIPTION.format(
            alert_payload=alert_payload,
        ),
        expected_output=_IMMUNE_TASK_EXPECTED,
        agent=immune_agent,
    )

    doctor_task = Task(
        description=_DOCTOR_TASK_DESCRIPTION.format(
            adrenaline=adrenaline,
            context_budget=context_budget,
        ),
        expected_output=_DOCTOR_TASK_EXPECTED,
        agent=doctor_agent,
        context=[immune_task],
    )

    # ── crew ──
    crew = Crew(
        agents=[immune_agent, doctor_agent],
        tasks=[immune_task, doctor_task],
        process=Process.sequential,
        verbose=False,
    )

    return crew
