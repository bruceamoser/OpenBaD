from __future__ import annotations

import pytest

from openbad.tasks.models import (
    NodeModel,
    NodeStatus,
    TaskKind,
    TaskModel,
    TaskPriority,
    TaskStatus,
    assert_valid_node_transition,
    assert_valid_task_transition,
    is_valid_node_transition,
    is_valid_task_transition,
)

# ---------------------------------------------------------------------------
# Model round-trip serialization
# ---------------------------------------------------------------------------


def test_task_model_round_trip() -> None:
    task = TaskModel.new(
        "Test task",
        description="A description",
        kind=TaskKind.USER_REQUESTED,
        priority=TaskPriority.HIGH,
    )
    d = task.to_dict()
    restored = TaskModel.from_dict(d)

    assert restored.task_id == task.task_id
    assert restored.title == task.title
    assert restored.status == TaskStatus.PENDING
    assert restored.kind == TaskKind.USER_REQUESTED
    assert restored.priority == int(TaskPriority.HIGH)


def test_node_model_round_trip() -> None:
    task = TaskModel.new("Parent task")
    node = NodeModel.new(task.task_id, "Reason step", max_retries=3)
    d = node.to_dict()
    restored = NodeModel.from_dict(d)

    assert restored.node_id == node.node_id
    assert restored.task_id == task.task_id
    assert restored.status == NodeStatus.PENDING
    assert restored.max_retries == 3


# ---------------------------------------------------------------------------
# Valid transitions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "current, next_status",
    [
        (TaskStatus.PENDING, TaskStatus.RUNNING),
        (TaskStatus.PENDING, TaskStatus.CANCELLED),
        (TaskStatus.RUNNING, TaskStatus.DONE),
        (TaskStatus.RUNNING, TaskStatus.FAILED),
        (TaskStatus.RUNNING, TaskStatus.BLOCKED),
        (TaskStatus.RUNNING, TaskStatus.CANCELLED),
        (TaskStatus.BLOCKED, TaskStatus.RUNNING),
        (TaskStatus.BLOCKED, TaskStatus.CANCELLED),
    ],
)
def test_valid_task_transitions(current: TaskStatus, next_status: TaskStatus) -> None:
    assert is_valid_task_transition(current, next_status)
    assert_valid_task_transition(current, next_status)  # must not raise


@pytest.mark.parametrize(
    "current, next_status",
    [
        (NodeStatus.PENDING, NodeStatus.RUNNING),
        (NodeStatus.PENDING, NodeStatus.BLOCKED),
        (NodeStatus.PENDING, NodeStatus.CANCELLED),
        (NodeStatus.RUNNING, NodeStatus.DONE),
        (NodeStatus.RUNNING, NodeStatus.FAILED),
        (NodeStatus.RUNNING, NodeStatus.BLOCKED),
        (NodeStatus.BLOCKED, NodeStatus.RUNNING),
    ],
)
def test_valid_node_transitions(current: NodeStatus, next_status: NodeStatus) -> None:
    assert is_valid_node_transition(current, next_status)
    assert_valid_node_transition(current, next_status)  # must not raise


# ---------------------------------------------------------------------------
# Invalid transitions rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "current, next_status",
    [
        (TaskStatus.DONE, TaskStatus.PENDING),
        (TaskStatus.DONE, TaskStatus.RUNNING),
        (TaskStatus.FAILED, TaskStatus.PENDING),
        (TaskStatus.CANCELLED, TaskStatus.RUNNING),
        (TaskStatus.PENDING, TaskStatus.DONE),
        (TaskStatus.PENDING, TaskStatus.BLOCKED),
    ],
)
def test_invalid_task_transitions_rejected(
    current: TaskStatus, next_status: TaskStatus
) -> None:
    assert not is_valid_task_transition(current, next_status)
    with pytest.raises(ValueError, match="Illegal task transition"):
        assert_valid_task_transition(current, next_status)


@pytest.mark.parametrize(
    "current, next_status",
    [
        (NodeStatus.DONE, NodeStatus.PENDING),
        (NodeStatus.DONE, NodeStatus.RUNNING),
        (NodeStatus.FAILED, NodeStatus.PENDING),
        (NodeStatus.CANCELLED, NodeStatus.RUNNING),
        (NodeStatus.PENDING, NodeStatus.DONE),
        (NodeStatus.PENDING, NodeStatus.FAILED),
    ],
)
def test_invalid_node_transitions_rejected(
    current: NodeStatus, next_status: NodeStatus
) -> None:
    assert not is_valid_node_transition(current, next_status)
    with pytest.raises(ValueError, match="Illegal node transition"):
        assert_valid_node_transition(current, next_status)


# ---------------------------------------------------------------------------
# Terminal states have no outgoing transitions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "terminal", [TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED]
)
def test_task_terminal_states_have_no_transitions(terminal: TaskStatus) -> None:
    for other in TaskStatus:
        assert not is_valid_task_transition(terminal, other)


@pytest.mark.parametrize(
    "terminal", [NodeStatus.DONE, NodeStatus.FAILED, NodeStatus.CANCELLED]
)
def test_node_terminal_states_have_no_transitions(terminal: NodeStatus) -> None:
    for other in NodeStatus:
        assert not is_valid_node_transition(terminal, other)


# ---------------------------------------------------------------------------
# Phase 10: new status enum values (#406)
# ---------------------------------------------------------------------------


class TestPhase10StatusEnums:
    def test_task_blocked_on_user_exists(self) -> None:
        assert TaskStatus.BLOCKED_ON_USER == "blocked_on_user"

    def test_node_deferred_resources_exists(self) -> None:
        assert NodeStatus.DEFERRED_RESOURCES == "deferred_resources"

    def test_node_quarantined_exists(self) -> None:
        assert NodeStatus.QUARANTINED == "quarantined"

    def test_node_blocked_on_user_exists(self) -> None:
        assert NodeStatus.BLOCKED_ON_USER == "blocked_on_user"

    # New valid task transitions
    @pytest.mark.parametrize(
        "current, next_status",
        [
            (TaskStatus.RUNNING, TaskStatus.BLOCKED_ON_USER),
            (TaskStatus.BLOCKED_ON_USER, TaskStatus.RUNNING),
            (TaskStatus.BLOCKED_ON_USER, TaskStatus.CANCELLED),
        ],
    )
    def test_valid_task_transitions_phase10(
        self, current: TaskStatus, next_status: TaskStatus
    ) -> None:
        assert is_valid_task_transition(current, next_status)
        assert_valid_task_transition(current, next_status)

    # New valid node transitions
    @pytest.mark.parametrize(
        "current, next_status",
        [
            (NodeStatus.RUNNING, NodeStatus.DEFERRED_RESOURCES),
            (NodeStatus.DEFERRED_RESOURCES, NodeStatus.RUNNING),
            (NodeStatus.DEFERRED_RESOURCES, NodeStatus.CANCELLED),
            (NodeStatus.RUNNING, NodeStatus.QUARANTINED),
            (NodeStatus.RUNNING, NodeStatus.BLOCKED_ON_USER),
            (NodeStatus.BLOCKED_ON_USER, NodeStatus.RUNNING),
            (NodeStatus.BLOCKED_ON_USER, NodeStatus.CANCELLED),
        ],
    )
    def test_valid_node_transitions_phase10(
        self, current: NodeStatus, next_status: NodeStatus
    ) -> None:
        assert is_valid_node_transition(current, next_status)
        assert_valid_node_transition(current, next_status)

    def test_quarantined_is_terminal(self) -> None:
        for other in NodeStatus:
            assert not is_valid_node_transition(NodeStatus.QUARANTINED, other)

    def test_new_values_round_trip_to_string(self) -> None:
        assert str(TaskStatus.BLOCKED_ON_USER) == "blocked_on_user"
        assert str(NodeStatus.DEFERRED_RESOURCES) == "deferred_resources"
        assert str(NodeStatus.QUARANTINED) == "quarantined"
        assert str(NodeStatus.BLOCKED_ON_USER) == "blocked_on_user"

    def test_blocked_on_user_invalid_task_transitions(self) -> None:
        # Can't jump straight from BLOCKED_ON_USER to terminal without RUNNING
        assert not is_valid_task_transition(TaskStatus.BLOCKED_ON_USER, TaskStatus.DONE)
        assert not is_valid_task_transition(
            TaskStatus.BLOCKED_ON_USER, TaskStatus.FAILED
        )
