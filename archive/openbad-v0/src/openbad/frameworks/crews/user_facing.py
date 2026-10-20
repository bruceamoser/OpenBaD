"""User-Facing Crew — Chat + Task agents for interactive user requests.

The crew operates in sequential mode:

1. **Chat Agent** receives the user message, decides whether it can
   answer directly or needs to delegate to the Task Agent.
2. **Task Agent** (when invoked) breaks the request into steps and
   drives them to completion.

Public API
----------
``create_user_facing_crew(user_message, *, llm_factory, tools_factory)``
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


# ── Task templates ───────────────────────────────────────────────────── #

_CHAT_TASK_DESCRIPTION = (
    "You received the following user message:\n\n"
    "---\n{user_message}\n---\n\n"
    "Determine whether you can answer directly or whether the request "
    "requires multi-step planning and execution.  If it is a simple "
    "conversational query, answer it yourself.  If it requires "
    "planning, research, tool use, or multiple steps, summarize "
    "what the Task Agent should do and pass it along."
)

_CHAT_TASK_EXPECTED = (
    "Either a direct conversational response to the user, or a clear "
    "task brief for the Task Agent to execute."
)

_EXEC_TASK_DESCRIPTION = (
    "Execute the task described by the Chat Agent:\n\n"
    "---\n{chat_result}\n---\n\n"
    "Break the work into concrete steps, execute each one, and "
    "return a final result to the user."
)

_EXEC_TASK_EXPECTED = (
    "A complete result addressing the user's original request, "
    "including any outputs from tool calls or research."
)


# ── Crew factory ─────────────────────────────────────────────────────── #


def create_user_facing_crew(
    user_message: str,
    *,
    llm_factory: Any | None = None,
    tools_factory: Any | None = None,
) -> Crew:
    """Build the User-Facing crew for a single user request.

    Parameters
    ----------
    user_message:
        The raw message from the user.
    llm_factory:
        ``(priority: str) -> llm`` or ``None`` for CrewAI defaults.
    tools_factory:
        ``(tool_role: str) -> list[tool]`` or ``None``.

    Returns
    -------
    Crew
        A configured crew ready for ``crew.kickoff()``.
    """
    # ── agents ──
    chat_spec = AGENT_SPECS["chat"]
    task_spec = AGENT_SPECS["task"]

    chat_llm = llm_factory(chat_spec.priority) if llm_factory else None
    task_llm = llm_factory(task_spec.priority) if llm_factory else None
    chat_tools = tools_factory(chat_spec.tool_role) if tools_factory else None
    task_tools = tools_factory(task_spec.tool_role) if tools_factory else None

    chat_agent = create_agent("chat", llm=chat_llm, tools=chat_tools)
    task_agent = create_agent("task", llm=task_llm, tools=task_tools)

    # ── tasks ──
    chat_task = Task(
        description=_CHAT_TASK_DESCRIPTION.format(user_message=user_message),
        expected_output=_CHAT_TASK_EXPECTED,
        agent=chat_agent,
    )

    exec_task = Task(
        description=_EXEC_TASK_DESCRIPTION.format(chat_result="{chat_result}"),
        expected_output=_EXEC_TASK_EXPECTED,
        agent=task_agent,
        context=[chat_task],
    )

    # ── crew ──
    crew = Crew(
        agents=[chat_agent, task_agent],
        tasks=[chat_task, exec_task],
        process=Process.sequential,
        verbose=False,
    )

    return crew
