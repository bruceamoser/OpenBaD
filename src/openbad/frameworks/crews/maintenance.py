"""Maintenance Crew — Sleep + Research + Explorer for background work.

The crew operates in sequential mode during idle periods:

1. **Explorer Agent** forages for novel information and interesting leads.
2. **Research Agent** investigates findings from the Explorer.
3. **Sleep Agent** consolidates new knowledge into long-term memory.

Endocrine modulation
--------------------
- Cortisol > 0.50 → suppress Explorer (reduced scope)
- Cortisol > 0.80 → disable Explorer entirely
- Dopamine > 0.50 → boost Explorer's curiosity

FSM gating
----------
Blocked in THROTTLED and EMERGENCY states.

Public API
----------
``create_maintenance_crew(topic, *, cortisol, dopamine, fsm_state,
                          llm_factory, tools_factory)``
    Build and return a ready-to-kickoff ``Crew``, or ``None`` if FSM-gated.
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

# Endocrine thresholds
CORTISOL_SUPPRESS: float = 0.50
CORTISOL_DISABLE: float = 0.80
DOPAMINE_BOOST: float = 0.50

# FSM states where this crew is NOT allowed
_BLOCKED_STATES: frozenset[str] = frozenset({"THROTTLED", "EMERGENCY"})


# ── Task templates ───────────────────────────────────────────────────── #

_EXPLORE_TASK_DESCRIPTION = (
    "Explore and forage for novel information related to:\n\n"
    "---\n{topic}\n---\n\n"
    "Exploration scope: {exploration_scope}\n"
    "Dopamine level: {dopamine:.2f}\n\n"
    "Search for interesting leads, connections, and knowledge "
    "that may be useful in future tasks. Be creative but stay "
    "within the exploration budget."
)

_EXPLORE_TASK_EXPECTED = (
    "A list of interesting discoveries, leads, and connections "
    "found during exploration, with relevance scores."
)

_RESEARCH_TASK_DESCRIPTION = (
    "Investigate the Explorer's findings in depth:\n\n"
    "Analyze each discovery for accuracy, relevance, and "
    "potential utility. Filter out noise and consolidate "
    "the most valuable insights."
)

_RESEARCH_TASK_EXPECTED = (
    "Verified and analyzed findings with summaries, source "
    "assessments, and recommendations for memory storage."
)

_SLEEP_TASK_DESCRIPTION = (
    "Consolidate the Research Agent's verified findings into "
    "long-term memory:\n\n"
    "Organize insights by topic, create memory indices, "
    "prune redundant data, and strengthen connections "
    "between related memories."
)

_SLEEP_TASK_EXPECTED = (
    "A consolidation report listing: memories stored, "
    "indices updated, redundancies pruned, and connections "
    "strengthened."
)


# ── Crew factory ─────────────────────────────────────────────────────── #


def create_maintenance_crew(
    topic: str,
    *,
    cortisol: float = 0.0,
    dopamine: float = 0.0,
    fsm_state: str = "IDLE",
    llm_factory: Any | None = None,
    tools_factory: Any | None = None,
) -> Crew | None:
    """Build the Maintenance crew for background exploration and consolidation.

    Parameters
    ----------
    topic:
        The exploration topic or research query.
    cortisol:
        Current cortisol level (0.0–1.0). Suppresses/disables Explorer.
    dopamine:
        Current dopamine level (0.0–1.0). Boosts Explorer above threshold.
    fsm_state:
        Current FSM state. Crew is blocked in THROTTLED/EMERGENCY.
    llm_factory:
        ``(priority: str) -> llm`` or ``None`` for CrewAI defaults.
    tools_factory:
        ``(tool_role: str) -> list[tool]`` or ``None``.

    Returns
    -------
    Crew | None
        A configured crew ready for ``crew.kickoff()``, or ``None``
        if the FSM state blocks execution.
    """
    if fsm_state.upper() in _BLOCKED_STATES:
        log.info("Maintenance crew blocked by FSM state: %s", fsm_state)
        return None

    # ── exploration scope based on endocrine state ──
    if cortisol > CORTISOL_DISABLE:
        exploration_scope = "disabled"
    elif cortisol > CORTISOL_SUPPRESS:
        exploration_scope = "reduced"
    else:
        exploration_scope = "full"

    if dopamine > DOPAMINE_BOOST:
        exploration_scope = (
            f"{exploration_scope}+boosted" if exploration_scope != "disabled" else "disabled"
        )

    # ── agents ──
    explorer_spec = AGENT_SPECS["explorer"]
    research_spec = AGENT_SPECS["research"]
    sleep_spec = AGENT_SPECS["sleep"]

    explorer_llm = llm_factory(explorer_spec.priority) if llm_factory else None
    research_llm = llm_factory(research_spec.priority) if llm_factory else None
    sleep_llm = llm_factory(sleep_spec.priority) if llm_factory else None

    explorer_tools = tools_factory(explorer_spec.tool_role) if tools_factory else None
    research_tools = tools_factory(research_spec.tool_role) if tools_factory else None
    sleep_tools = tools_factory(sleep_spec.tool_role) if tools_factory else None

    explorer_agent = create_agent("explorer", llm=explorer_llm, tools=explorer_tools)
    research_agent = create_agent("research", llm=research_llm, tools=research_tools)
    sleep_agent = create_agent("sleep", llm=sleep_llm, tools=sleep_tools)

    # ── tasks ──
    agents = [explorer_agent, research_agent, sleep_agent]
    tasks: list[Task] = []

    explore_task = Task(
        description=_EXPLORE_TASK_DESCRIPTION.format(
            topic=topic,
            exploration_scope=exploration_scope,
            dopamine=dopamine,
        ),
        expected_output=_EXPLORE_TASK_EXPECTED,
        agent=explorer_agent,
    )
    tasks.append(explore_task)

    research_task = Task(
        description=_RESEARCH_TASK_DESCRIPTION,
        expected_output=_RESEARCH_TASK_EXPECTED,
        agent=research_agent,
        context=[explore_task],
    )
    tasks.append(research_task)

    sleep_task = Task(
        description=_SLEEP_TASK_DESCRIPTION,
        expected_output=_SLEEP_TASK_EXPECTED,
        agent=sleep_agent,
        context=[research_task],
    )
    tasks.append(sleep_task)

    # ── crew ──
    crew = Crew(
        agents=agents,
        tasks=tasks,
        process=Process.sequential,
        verbose=False,
    )

    return crew
