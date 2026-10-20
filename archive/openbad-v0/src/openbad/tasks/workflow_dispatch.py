"""LangGraph-backed task dispatch adapter.

Provides a :class:`~openbad.tasks.scheduler.DispatchCallback`-compatible
callable that routes dispatched tasks to the correct LangGraph workflow graph,
runs the graph, and records the outcome via :class:`NodeExecutor`.
"""

from __future__ import annotations

import logging
import sqlite3

from openbad.frameworks.workflows.registry import get_workflow
from openbad.frameworks.workflows.state import AgentState
from openbad.tasks.executor import NodeExecutor
from openbad.tasks.models import TaskModel, TaskStatus
from openbad.tasks.service import TaskService
from openbad.tasks.store import TaskStore

log = logging.getLogger(__name__)


class WorkflowDispatcher:
    """Dispatch callback that executes tasks through LangGraph workflows.

    Satisfies the :class:`~openbad.tasks.scheduler.DispatchCallback` protocol
    so it can be passed directly to :class:`TaskScheduler`.

    Parameters
    ----------
    conn:
        SQLite connection shared with the task store / executor.
    checkpointer:
        Optional ``BaseCheckpointSaver`` for LangGraph state persistence.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        checkpointer: object | None = None,
    ) -> None:
        self._service = TaskService(conn)
        self._executor = NodeExecutor(conn)
        self._store = TaskStore(conn)
        self._checkpointer = checkpointer

    # -- DispatchCallback protocol ------------------------------------- #

    def __call__(self, task: TaskModel, lease_id: str) -> None:
        """Execute *task* through the matching LangGraph workflow.

        1. Look up the compiled graph for the task's ``kind``.
        2. Build initial ``AgentState`` from task metadata.
        3. Invoke the graph synchronously.
        4. Transition task to DONE or FAILED based on the result.
        5. Release the lease.
        """
        kind = task.kind.value

        # Transition PENDING → RUNNING before workflow execution.
        self._service.transition_task(task.task_id, TaskStatus.RUNNING)

        try:
            graph = get_workflow(kind, checkpointer=self._checkpointer)
        except ValueError:
            log.error("No workflow graph for task kind %r (task %s)", kind, task.task_id)
            self._service.transition_task(task.task_id, TaskStatus.FAILED)
            return

        initial_state: AgentState = {
            "messages": [],
            "context": task.description,
            "memory_refs": [],
            "task_metadata": {
                "task_id": task.task_id,
                "kind": kind,
                "title": task.title,
                "priority": task.priority,
                "owner": task.owner,
                "lease_id": lease_id,
            },
            "results": [],
            "retry_counts": {},
            "error": "",
            "status": "running",
        }

        log.info(
            "Dispatching task %s (kind=%s) to LangGraph workflow",
            task.task_id,
            kind,
        )

        try:
            config = {"configurable": {"thread_id": task.task_id}}
            result = graph.invoke(initial_state, config=config)

            status = result.get("status", "done")
            if status == "failed":
                error = result.get("error", "workflow returned failed status")
                log.warning("Task %s workflow failed: %s", task.task_id, error)
                self._service.transition_task(task.task_id, TaskStatus.FAILED)
            else:
                log.info("Task %s workflow completed successfully", task.task_id)
                self._service.transition_task(task.task_id, TaskStatus.DONE)
        except Exception:
            log.exception("Task %s workflow raised an exception", task.task_id)
            self._service.transition_task(task.task_id, TaskStatus.FAILED)
