"""Task and node status enums with legal transition maps and validation helpers."""

from __future__ import annotations

import dataclasses
import time
import uuid
from enum import Enum, StrEnum

# ---------------------------------------------------------------------------
# Status enums
# ---------------------------------------------------------------------------


class TaskStatus(StrEnum):
    """Lifecycle states for a top-level task."""

    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    BLOCKED_ON_USER = "blocked_on_user"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NodeStatus(StrEnum):
    """Lifecycle states for a task DAG node."""

    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    DEFERRED_RESOURCES = "deferred_resources"
    QUARANTINED = "quarantined"
    BLOCKED_ON_USER = "blocked_on_user"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunStatus(StrEnum):
    """Lifecycle states for a task run record."""

    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class ResearchStatus(StrEnum):
    """Lifecycle states for a research node."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskKind(StrEnum):
    """How a task was originated."""

    USER_REQUESTED = "user_requested"
    SYSTEM = "system"
    RESEARCH = "research"
    SCHEDULED = "scheduled"


class TaskPriority(int, Enum):
    """Numeric priority constants (higher = more important)."""

    LOW = 0
    NORMAL = 5
    HIGH = 10
    CRITICAL = 20


# ---------------------------------------------------------------------------
# Transition maps
# ---------------------------------------------------------------------------

#: Legal task status transitions.  Key → set of allowed next states.
TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.DONE,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.BLOCKED_ON_USER,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.BLOCKED: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
    TaskStatus.BLOCKED_ON_USER: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
    TaskStatus.DONE: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}

#: Legal node status transitions.
NODE_TRANSITIONS: dict[NodeStatus, frozenset[NodeStatus]] = {
    NodeStatus.PENDING: frozenset(
        {NodeStatus.RUNNING, NodeStatus.BLOCKED, NodeStatus.CANCELLED}
    ),
    NodeStatus.RUNNING: frozenset(
        {
            NodeStatus.DONE,
            NodeStatus.FAILED,
            NodeStatus.BLOCKED,
            NodeStatus.DEFERRED_RESOURCES,
            NodeStatus.QUARANTINED,
            NodeStatus.BLOCKED_ON_USER,
            NodeStatus.CANCELLED,
        }
    ),
    NodeStatus.BLOCKED: frozenset({NodeStatus.RUNNING, NodeStatus.CANCELLED}),
    NodeStatus.DEFERRED_RESOURCES: frozenset({NodeStatus.RUNNING, NodeStatus.CANCELLED}),
    NodeStatus.QUARANTINED: frozenset(),
    NodeStatus.BLOCKED_ON_USER: frozenset({NodeStatus.RUNNING, NodeStatus.CANCELLED}),
    NodeStatus.DONE: frozenset(),
    NodeStatus.FAILED: frozenset(),
    NodeStatus.CANCELLED: frozenset(),
}


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def is_valid_task_transition(current: TaskStatus, next_status: TaskStatus) -> bool:
    """Return True if transitioning from *current* to *next_status* is legal."""
    return next_status in TASK_TRANSITIONS.get(current, frozenset())


def assert_valid_task_transition(current: TaskStatus, next_status: TaskStatus) -> None:
    """Raise :class:`ValueError` if the task transition is illegal."""
    if not is_valid_task_transition(current, next_status):
        raise ValueError(
            f"Illegal task transition: {current.value!r} → {next_status.value!r}"
        )


def is_valid_node_transition(current: NodeStatus, next_status: NodeStatus) -> bool:
    """Return True if transitioning from *current* to *next_status* is legal."""
    return next_status in NODE_TRANSITIONS.get(current, frozenset())


def assert_valid_node_transition(current: NodeStatus, next_status: NodeStatus) -> None:
    """Raise :class:`ValueError` if the node transition is illegal."""
    if not is_valid_node_transition(current, next_status):
        raise ValueError(
            f"Illegal node transition: {current.value!r} → {next_status.value!r}"
        )


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class TaskModel:
    """In-memory representation of a task record."""

    task_id: str
    title: str
    description: str
    kind: TaskKind
    horizon: str
    priority: int
    status: TaskStatus
    owner: str
    root_task_id: str
    due_at: float | None = None
    parent_task_id: str | None = None
    lease_owner: str | None = None
    recurrence_rule: str | None = None
    requires_context: bool = False
    isolated_execution: bool = False
    notes_path: str | None = None
    created_at: float = dataclasses.field(default_factory=time.time)
    updated_at: float = dataclasses.field(default_factory=time.time)

    @classmethod
    def new(
        cls,
        title: str,
        *,
        description: str = "",
        kind: TaskKind = TaskKind.USER_REQUESTED,
        horizon: str = "short",
        priority: int = TaskPriority.NORMAL,
        owner: str = "system",
        due_at: float | None = None,
        parent_task_id: str | None = None,
    ) -> TaskModel:
        """Create a new task with a generated UUID."""
        now = time.time()
        task_id = str(uuid.uuid4())
        return cls(
            task_id=task_id,
            title=title,
            description=description,
            kind=kind,
            horizon=horizon,
            priority=int(priority),
            status=TaskStatus.PENDING,
            owner=owner,
            root_task_id=task_id,
            due_at=due_at,
            parent_task_id=parent_task_id,
            created_at=now,
            updated_at=now,
        )

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["kind"] = self.kind.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> TaskModel:
        d = dict(data)
        d["kind"] = TaskKind(d["kind"])
        d["status"] = TaskStatus(d["status"])
        return cls(**d)


@dataclasses.dataclass
class NodeModel:
    """In-memory representation of a task DAG node."""

    node_id: str
    task_id: str
    title: str
    node_type: str
    status: NodeStatus
    capability_requirements: list
    model_requirements: list
    expected_info_gain: float
    blockage_score: float
    retry_count: int
    max_retries: int
    reward_program_id: str | None = None
    created_at: float = dataclasses.field(default_factory=time.time)
    updated_at: float = dataclasses.field(default_factory=time.time)

    @classmethod
    def new(
        cls,
        task_id: str,
        title: str,
        *,
        node_type: str = "reason",
        max_retries: int = 0,
    ) -> NodeModel:
        """Create a new node with a generated UUID."""
        now = time.time()
        return cls(
            node_id=str(uuid.uuid4()),
            task_id=task_id,
            title=title,
            node_type=node_type,
            status=NodeStatus.PENDING,
            capability_requirements=[],
            model_requirements=[],
            expected_info_gain=0.0,
            blockage_score=0.0,
            retry_count=0,
            max_retries=max_retries,
            created_at=now,
            updated_at=now,
        )

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> NodeModel:
        d = dict(data)
        d["status"] = NodeStatus(d["status"])
        return cls(**d)
