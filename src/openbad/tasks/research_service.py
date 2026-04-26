"""High-level research service layer.

:class:`ResearchService` wraps :class:`~openbad.tasks.research_queue.ResearchQueue`
into a singleton API that abstracts DB access for all research operations.

Use :meth:`ResearchService.get_instance` to obtain the process-wide singleton.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

from openbad.tasks.research_queue import (
    ResearchNode,
    ResearchQueue,
    initialize_research_db,
)

log = logging.getLogger(__name__)

_lock = threading.Lock()
_instance: ResearchService | None = None


class ResearchService:
    """Unified research service wrapping the priority queue."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        initialize_research_db(conn)
        self._queue = ResearchQueue(conn)

    # ------------------------------------------------------------------
    # Singleton access
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls, db_path: str | Path | None = None) -> ResearchService:
        """Return the process-wide singleton, creating it on first call.

        Parameters
        ----------
        db_path:
            Optional path override; used only when the singleton is first
            created.  Subsequent calls ignore it.
        """
        global _instance  # noqa: PLW0603
        if _instance is not None:
            return _instance
        with _lock:
            if _instance is not None:
                return _instance
            from openbad.state.db import DEFAULT_STATE_DB_PATH, initialize_state_db

            effective = Path(db_path) if db_path else DEFAULT_STATE_DB_PATH
            conn = initialize_state_db(effective)
            _instance = cls(conn)
            log.info("ResearchService singleton initialised (db=%s)", effective)
            return _instance

    @classmethod
    def reset_instance(cls) -> None:
        """Tear down the singleton — for tests only."""
        global _instance  # noqa: PLW0603
        with _lock:
            _instance = None

    # ------------------------------------------------------------------
    # Queue operations
    # ------------------------------------------------------------------

    def enqueue(
        self,
        title: str,
        *,
        priority: int = 0,
        description: str = "",
        source_task_id: str | None = None,
        node_id: str | None = None,
    ) -> ResearchNode:
        """Add a research node to the queue."""
        return self._queue.enqueue(
            title,
            priority=priority,
            description=description,
            source_task_id=source_task_id,
            node_id=node_id,
        )

    def dequeue(self) -> ResearchNode | None:
        """Pop the highest-priority pending node, or *None*."""
        return self._queue.dequeue()

    def peek(self) -> ResearchNode | None:
        """Return the next node without removing it, or *None*."""
        return self._queue.peek()

    def get(self, node_id: str) -> ResearchNode | None:
        """Return a specific node by ID, or *None*."""
        return self._queue.get(node_id)

    def list_pending(self) -> list[ResearchNode]:
        """Return all pending nodes in priority order."""
        return self._queue.list_pending()

    def list_completed(self, *, limit: int = 50) -> list[ResearchNode]:
        """Return completed nodes, most recent first."""
        return self._queue.list_completed(limit=limit)

    def update(
        self,
        node_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        priority: int | None = None,
        source_task_id: str | None = None,
    ) -> ResearchNode | None:
        """Update mutable fields on a pending node."""
        return self._queue.update(
            node_id,
            title=title,
            description=description,
            priority=priority,
            source_task_id=source_task_id,
        )

    def complete(self, node_id: str) -> ResearchNode | None:
        """Mark a node as completed (dequeued)."""
        return self._queue.complete(node_id)

    def enqueue_or_append_pending(
        self,
        title: str,
        *,
        priority: int = 0,
        description: str = "",
        source_task_id: str | None = None,
        observation: str | None = None,
        max_observations: int = 20,
    ) -> ResearchNode:
        """Reuse a pending node with the same title, appending observation."""
        return self._queue.enqueue_or_append_pending(
            title,
            priority=priority,
            description=description,
            source_task_id=source_task_id,
            observation=observation,
            max_observations=max_observations,
        )
