"""Research planner — decomposes broad research nodes into focused sub-tasks.

Instead of executing a complex research topic in a single LLM call (big-bang),
the planner:

1. **Plans** — uses the LLM to decompose the topic into 3–6 focused questions.
2. **Enqueues** — creates child research nodes for each question.
3. **Synthesises** — after all children complete, enqueues a synthesis node
   that combines findings into a cohesive report.

The planner is invoked by :func:`openbad.autonomy.scheduler_worker._process_research`
when a top-level (non-child) research node is processed.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openbad.tasks.research_queue import ResearchNode
    from openbad.tasks.research_service import ResearchService

log = logging.getLogger(__name__)

# Maximum sub-questions the planner may produce.
_MAX_SUBTASKS = 6
_MIN_SUBTASKS = 2

_PLAN_SYSTEM_PROMPT = """\
You are a research planner. Your job is to decompose a broad research topic
into focused, independent sub-questions that can each be investigated
separately by a research worker.

Rules:
- Output ONLY valid JSON — no markdown fences, no commentary.
- Return a JSON array of objects, each with "title" and "description" keys.
- Each sub-question should be narrow enough for a single web-search + read cycle.
- Produce between {min} and {max} sub-questions.
- The sub-questions should collectively cover the full scope of the original topic.
- Do NOT include a synthesis/summary step — that will be added automatically.
- Order sub-questions from foundational to advanced.

Example output:
[
  {{"title": "What is X?", "description": "Define X and its core concepts."}},
  {{"title": "How is X used in Y?", "description": "Practical applications of X in Y."}}
]
"""


def build_plan_prompt(node: ResearchNode) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the planning LLM call."""
    system = _PLAN_SYSTEM_PROMPT.format(min=_MIN_SUBTASKS, max=_MAX_SUBTASKS)
    user = (
        f"Research topic: {node.title}\n"
        f"Description: {node.description or '(none)'}\n\n"
        f"Decompose this into {_MIN_SUBTASKS}–{_MAX_SUBTASKS} focused sub-questions."
    )
    return system, user


def parse_plan(raw: str) -> list[dict[str, str]]:
    """Parse the LLM's JSON plan into a list of sub-task dicts.

    Tolerant of markdown fences and minor formatting issues.
    Returns an empty list if parsing fails.
    """
    text = raw.strip()
    # Strip markdown code fences if present.
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning("Research planner: failed to parse JSON plan")
        return []

    if not isinstance(data, list):
        log.warning("Research planner: expected JSON array, got %s", type(data).__name__)
        return []

    # Validate each item has title + description.
    valid: list[dict[str, str]] = []
    for item in data:
        if isinstance(item, dict) and "title" in item:
            valid.append({
                "title": str(item["title"]),
                "description": str(item.get("description", "")),
            })

    # Enforce bounds.
    if len(valid) < _MIN_SUBTASKS:
        log.warning("Research planner: only %d sub-tasks (min %d)", len(valid), _MIN_SUBTASKS)
        return []
    return valid[:_MAX_SUBTASKS]


def enqueue_plan(
    research_svc: ResearchService,
    parent: ResearchNode,
    subtasks: list[dict[str, str]],
) -> list[ResearchNode]:
    """Enqueue child research nodes for each sub-task in the plan.

    Returns the list of created child nodes.
    """
    children: list[ResearchNode] = []
    for i, task in enumerate(subtasks):
        child = research_svc.enqueue(
            title=task["title"],
            description=(
                f"{task['description']}\n\n"
                f"(Sub-question {i + 1}/{len(subtasks)} of parent research: "
                f"{parent.title})"
            ),
            priority=parent.priority,
            source_task_id=parent.source_task_id,
            parent_node_id=parent.node_id,
        )
        children.append(child)
        log.info(
            "Research planner: enqueued child %s (%d/%d) for parent %s",
            child.node_id, i + 1, len(subtasks), parent.node_id,
        )
    return children


def build_synthesis_description(
    parent: ResearchNode,
    children: list[ResearchNode],
    child_summaries: dict[str, str],
) -> str:
    """Build the description for the synthesis node."""
    lines = [
        f"Synthesise the findings from the following sub-research into a "
        f"cohesive report on: {parent.title}\n",
        f"Original description: {parent.description or '(none)'}\n",
        "## Sub-research findings\n",
    ]
    for i, child in enumerate(children):
        summary = child_summaries.get(child.node_id, "(no summary available)")
        lines.append(f"### {i + 1}. {child.title}\n{summary}\n")
    return "\n".join(lines)


def should_plan(node: ResearchNode) -> bool:
    """Return True if this node should go through the planning phase.

    Only top-level (non-child) nodes get planned. Child nodes are already
    focused and execute directly.
    """
    return not node.is_child


def all_children_complete(
    research_svc: ResearchService,
    parent_node_id: str,
) -> bool:
    """Return True if all children of the parent are completed."""
    pending = research_svc.list_pending_children(parent_node_id)
    return len(pending) == 0


def get_child_summaries(
    research_svc: ResearchService,
    parent_node_id: str,
    session_messages_fn: Any,
) -> dict[str, str]:
    """Collect completed child summaries from session messages.

    Parameters
    ----------
    session_messages_fn:
        Callable(session_id) that returns recent messages for extracting
        summaries.  Falls back to child descriptions if unavailable.
    """
    children = research_svc.list_children(parent_node_id)
    summaries: dict[str, str] = {}
    for child in children:
        if child.dequeued_at is not None:
            # Use description as fallback — the actual summary will be
            # injected by the scheduler_worker when completing each child.
            summaries[child.node_id] = child.description
    return summaries
