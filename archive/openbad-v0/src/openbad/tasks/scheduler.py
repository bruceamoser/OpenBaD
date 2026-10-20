"""Lease-aware scheduler dispatch loop for Phase 9 task orchestration.

:class:`TaskScheduler` polls due tasks, acquires leases atomically, and
dispatches them to a caller-supplied callback.  It respects configured
quiet-hour windows so that non-urgent work is suppressed during maintenance
or low-activity periods.
"""

from __future__ import annotations

import dataclasses
import sqlite3
import time
from typing import Protocol

from openbad.tasks.lease import LeaseStore
from openbad.tasks.models import TaskModel, TaskStatus
from openbad.tasks.store import TaskStore

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class QuietHoursWindow:
    """A time-of-day window (24-hour clock) during which non-urgent tasks are
    suppressed.

    ``start_hour`` and ``end_hour`` are both in [0, 23].  An ``end_hour``
    less than ``start_hour`` denotes an overnight window (e.g. 23–06).
    """

    start_hour: int
    end_hour: int

    def is_active(self, hour: int | None = None) -> bool:
        """Return True if *hour* (local time, 0–23) falls inside this window."""
        h = hour if hour is not None else time.localtime().tm_hour
        if self.start_hour <= self.end_hour:
            return self.start_hour <= h <= self.end_hour
        # Overnight window
        return h >= self.start_hour or h <= self.end_hour


@dataclasses.dataclass
class SchedulerConfig:
    """Runtime configuration for :class:`TaskScheduler`."""

    lease_ttl_seconds: float = 300.0
    poll_limit: int = 10
    quiet_hours: list[QuietHoursWindow] = dataclasses.field(default_factory=list)

    def is_quiet_hour(self) -> bool:
        """Return True if any configured quiet-hours window is currently active."""
        return any(w.is_active() for w in self.quiet_hours)


# ---------------------------------------------------------------------------
# Dispatch protocol
# ---------------------------------------------------------------------------


class DispatchCallback(Protocol):
    """Protocol for the callable that handles a dispatched task."""

    def __call__(self, task: TaskModel, lease_id: str) -> None: ...


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class TaskScheduler:
    """Polls due tasks, acquires leases, and dispatches to a callback.

    Only tasks in ``PENDING`` status with a ``due_at <= now`` (or no ``due_at``)
    are considered.  Non-urgent dispatch is skipped during quiet hours.

    The scheduler is designed to be called from a heartbeat loop rather than
    run as a continuous background thread, giving the caller full control over
    tick frequency and error handling.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        worker_id: str,
        callback: DispatchCallback,
        config: SchedulerConfig | None = None,
    ) -> None:
        self._store = TaskStore(conn)
        self._leases = LeaseStore(conn)
        self._worker_id = worker_id
        self._callback = callback
        self._config = config or SchedulerConfig()

    # ------------------------------------------------------------------

    def tick(self, *, urgent: bool = False) -> list[str]:
        """Run one dispatch iteration.

        Parameters
        ----------
        urgent:
            When ``True``, quiet-hours suppression is bypassed.

        Returns
        -------
        list[str]
            The task IDs that were successfully dispatched in this tick.
        """
        if not urgent and self._config.is_quiet_hour():
            return []

        now = time.time()
        dispatched: list[str] = []

        candidates = self._store.list_tasks(
            status=TaskStatus.PENDING,
            limit=self._config.poll_limit,
        )

        for task in candidates:
            # Skip tasks that are not yet due
            if task.due_at is not None and task.due_at > now:
                continue

            lease = self._leases.acquire(
                "task",
                task.task_id,
                self._worker_id,
                self._config.lease_ttl_seconds,
            )
            if lease is None:
                # Another worker already holds this task
                continue

            self._callback(task, lease.lease_id)
            dispatched.append(task.task_id)

        return dispatched
