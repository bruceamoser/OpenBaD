"""Deterministic task DAG planner for Phase 9 task orchestration.

:func:`plan_task` generates a set of :class:`~openbad.tasks.models.NodeModel`
objects and a list of directed edges that together form a linear-chain DAG for
a given task.  The shape of the DAG is determined by the task's
:class:`~openbad.tasks.models.TaskKind` and a mapping of *templates*.

The planner is **deterministic**: every call with the same *task* produces the
same logical node sequence, using freshly generated UUIDs only for node IDs.

Planner output is persisted immediately via :class:`~openbad.tasks.store.TaskStore`.
"""

from __future__ import annotations

import dataclasses

from openbad.tasks.models import NodeModel, TaskKind, TaskModel
from openbad.tasks.store import TaskStore

# ---------------------------------------------------------------------------
# Node templates
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class NodeTemplate:
    """Blueprint for a single DAG node."""

    title: str
    node_type: str
    max_retries: int = 1
    reward_program_id: str | None = None


# Built-in templates keyed by TaskKind.  Subclasses / callers may supply their
# own template registry via the ``templates`` parameter of :func:`plan_task`.
DEFAULT_TEMPLATES: dict[str, list[NodeTemplate]] = {
    TaskKind.USER_REQUESTED: [
        NodeTemplate("Clarify", "clarify", max_retries=0),
        NodeTemplate("Plan", "plan", max_retries=1),
        NodeTemplate("Execute", "execute", max_retries=2),
        NodeTemplate("Review", "review", max_retries=0),
    ],
    TaskKind.RESEARCH: [
        NodeTemplate("Gather", "gather", max_retries=2),
        NodeTemplate("Analyse", "analyse", max_retries=1),
        NodeTemplate("Summarise", "summarise", max_retries=0),
    ],
    TaskKind.SYSTEM: [
        NodeTemplate("Execute", "execute", max_retries=1),
    ],
    TaskKind.SCHEDULED: [
        NodeTemplate("Execute", "execute", max_retries=1),
    ],
}


# ---------------------------------------------------------------------------
# Planner output
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class PlanResult:
    """The output of :func:`plan_task`."""

    nodes: list[NodeModel]
    edges: list[tuple[str, str]]  # (from_node_id, to_node_id)


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------


def plan_task(
    task: TaskModel,
    store: TaskStore,
    *,
    templates: dict[str, list[NodeTemplate]] | None = None,
) -> PlanResult:
    """Generate a DAG for *task* and persist it via *store*.

    Parameters
    ----------
    task:
        The parent task.  Its ``task_id`` and ``kind`` are used to look up
        the correct template and set node ownership.
    store:
        A :class:`~openbad.tasks.store.TaskStore` backed by an open
        ``sqlite3.Connection``.  All nodes and edges are written here.
    templates:
        Optional override for :data:`DEFAULT_TEMPLATES`.

    Returns
    -------
    PlanResult
        Immutable container of the created :class:`~openbad.tasks.models.NodeModel`
        objects and their edges.

    Raises
    ------
    ValueError
        If no template is registered for ``task.kind``.
    """
    registry = templates if templates is not None else DEFAULT_TEMPLATES
    node_templates = registry.get(task.kind)
    if node_templates is None:
        raise ValueError(f"No DAG template registered for task kind {task.kind!r}")

    # Build nodes
    nodes: list[NodeModel] = []
    for tmpl in node_templates:
        node = NodeModel.new(
            task_id=task.task_id,
            title=tmpl.title,
            node_type=tmpl.node_type,
            max_retries=tmpl.max_retries,
        )
        node = dataclasses.replace(node, reward_program_id=tmpl.reward_program_id)
        store.create_node(node)
        nodes.append(node)

    # Build linear-chain edges: node[i] → node[i+1]
    edges: list[tuple[str, str]] = []
    for i in range(len(nodes) - 1):
        from_id = nodes[i].node_id
        to_id = nodes[i + 1].node_id
        store.create_edge(task.task_id, from_id, to_id)
        edges.append((from_id, to_id))

    return PlanResult(nodes=nodes, edges=edges)
