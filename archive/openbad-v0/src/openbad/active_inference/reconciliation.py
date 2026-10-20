"""Library reconciliation — surprise-triggered book updates.

When the Active Inference engine detects high surprise on a semantic entry
that contains ``library_refs``, a reconciliation task is created so the
scheduler can update the referenced Library book with new information.
"""

from __future__ import annotations

import logging
from typing import Any

from openbad.memory.base import MemoryEntry
from openbad.tasks.models import TaskKind, TaskModel, TaskPriority

logger = logging.getLogger(__name__)

#: Surprise threshold above which reconciliation is triggered.
RECONCILIATION_SURPRISE_THRESHOLD = 0.7


def check_library_reconciliation(
    semantic_entry: MemoryEntry,
    surprise_score: float,
) -> list[str]:
    """Check if *semantic_entry* needs library reconciliation.

    Returns the list of book IDs that need reconciliation, or an empty
    list if no action is needed.
    """
    if surprise_score < RECONCILIATION_SURPRISE_THRESHOLD:
        return []
    refs = semantic_entry.metadata.get("library_refs", [])
    if not refs:
        return []
    logger.info(
        "Reconciliation triggered for %s (surprise=%.2f, refs=%s)",
        semantic_entry.key,
        surprise_score,
        refs,
    )
    return list(refs)


def create_reconciliation_task(
    task_store: Any,
    book_id: str,
    new_fact: str,
    reason: str = "",
) -> str:
    """Create a system task to reconcile a Library book with a new fact.

    Returns the created task_id.
    """
    task = TaskModel.new(
        title=f"Library Reconciliation: {book_id[:8]}",
        description=(
            f"Reconcile Library book {book_id} with new information.\n\n"
            f"New fact: {new_fact}\n"
            f"Reason: {reason}\n\n"
            f"[reconcile] book_id={book_id}"
        ),
        kind=TaskKind.SYSTEM,
        priority=TaskPriority.NORMAL,
    )
    task_id = task.task_id
    task_store.create_task(task)
    logger.info("Created reconciliation task %s for book %s", task_id, book_id)
    return task_id


def is_reconcile_task(task: TaskModel) -> bool:
    """Return True if *task* is a library reconciliation task."""
    return task.title.startswith("Library Reconciliation:")


def parse_reconcile_metadata(task: TaskModel) -> dict[str, str]:
    """Extract book_id and new_fact from a reconciliation task description."""
    desc = task.description or ""
    result: dict[str, str] = {}

    for line in desc.split("\n"):
        if line.startswith("New fact: "):
            result["new_fact"] = line[len("New fact: "):]
        if "[reconcile] book_id=" in line:
            result["book_id"] = line.split("book_id=", 1)[1].strip()

    return result
