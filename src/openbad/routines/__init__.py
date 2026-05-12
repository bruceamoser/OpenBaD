"""Routine model and status definitions."""

from __future__ import annotations

import dataclasses
import time
import uuid
from enum import StrEnum


class RunStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclasses.dataclass
class Routine:
    routine_id: str
    name: str
    description: str
    body_md: str
    recurrence_rule: str | None
    next_run_at: float | None
    enabled: bool
    created_at: float
    updated_at: float

    @classmethod
    def create(
        cls,
        name: str,
        body_md: str,
        *,
        description: str = "",
        recurrence_rule: str | None = None,
        next_run_at: float | None = None,
        enabled: bool = True,
    ) -> Routine:
        now = time.time()
        return cls(
            routine_id=uuid.uuid4().hex[:12],
            name=name,
            description=description,
            body_md=body_md,
            recurrence_rule=recurrence_rule,
            next_run_at=next_run_at,
            enabled=enabled,
            created_at=now,
            updated_at=now,
        )


@dataclasses.dataclass
class RoutineRun:
    run_id: str
    routine_id: str
    started_at: float
    finished_at: float | None
    status: RunStatus
    output: str
    error: str
    tokens_used: int

    @classmethod
    def start(cls, routine_id: str) -> RoutineRun:
        return cls(
            run_id=uuid.uuid4().hex[:12],
            routine_id=routine_id,
            started_at=time.time(),
            finished_at=None,
            status=RunStatus.RUNNING,
            output="",
            error="",
            tokens_used=0,
        )
