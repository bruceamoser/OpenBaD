from __future__ import annotations

from pathlib import Path

import pytest

from openbad.state.db import initialize_state_db
from openbad.tasks.gating import DependencyGate, RetryPolicy
from openbad.tasks.models import NodeModel, NodeStatus, TaskModel
from openbad.tasks.planner import plan_task
from openbad.tasks.store import TaskStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path):
    return initialize_state_db(tmp_path / "state.db")


@pytest.fixture()
def store(db):
    return TaskStore(db)


@pytest.fixture()
def gate(db):
    return DependencyGate(db)


@pytest.fixture()
def policy(db):
    return RetryPolicy(db)


def make_task(store: TaskStore) -> TaskModel:
    task = TaskModel.new("Test task")
    store.create_task(task)
    return task


def make_node(store: TaskStore, task_id: str, *, max_retries: int = 0) -> NodeModel:
    node = NodeModel.new(task_id, "Node", max_retries=max_retries)
    store.create_node(node)
    return node


# ---------------------------------------------------------------------------
# Dependency gating
# ---------------------------------------------------------------------------


def test_node_with_no_predecessors_is_ready(gate: DependencyGate, store: TaskStore) -> None:
    task = make_task(store)
    node = make_node(store, task.task_id)

    assert gate.is_ready(task.task_id, node.node_id) is True


def test_node_blocked_by_pending_predecessor(gate: DependencyGate, store: TaskStore) -> None:
    task = make_task(store)
    pred = make_node(store, task.task_id)  # status=PENDING
    succ = make_node(store, task.task_id)
    store.create_edge(task.task_id, pred.node_id, succ.node_id)

    assert gate.is_ready(task.task_id, succ.node_id) is False


def test_node_ready_when_predecessor_done(gate: DependencyGate, store: TaskStore) -> None:
    task = make_task(store)
    pred = make_node(store, task.task_id)
    succ = make_node(store, task.task_id)
    store.create_edge(task.task_id, pred.node_id, succ.node_id)

    # Complete predecessor
    store.update_node_status(pred.node_id, NodeStatus.RUNNING)
    store.update_node_status(pred.node_id, NodeStatus.DONE)

    assert gate.is_ready(task.task_id, succ.node_id) is True


def test_node_blocked_when_only_some_predecessors_done(
    gate: DependencyGate, store: TaskStore
) -> None:
    task = make_task(store)
    pred_a = make_node(store, task.task_id)
    pred_b = make_node(store, task.task_id)
    succ = make_node(store, task.task_id)
    store.create_edge(task.task_id, pred_a.node_id, succ.node_id)
    store.create_edge(task.task_id, pred_b.node_id, succ.node_id)

    store.update_node_status(pred_a.node_id, NodeStatus.RUNNING)
    store.update_node_status(pred_a.node_id, NodeStatus.DONE)
    # pred_b still PENDING

    assert gate.is_ready(task.task_id, succ.node_id) is False


def test_unmet_dependencies_returns_blocking_nodes(
    gate: DependencyGate, store: TaskStore
) -> None:
    task = make_task(store)
    pred = make_node(store, task.task_id)
    succ = make_node(store, task.task_id)
    store.create_edge(task.task_id, pred.node_id, succ.node_id)

    unmet = gate.unmet_dependencies(task.task_id, succ.node_id)

    assert unmet == [pred.node_id]


def test_linear_chain_ready_in_order(gate: DependencyGate, store: TaskStore) -> None:
    """Using plan_task: only the first node of a chain is initially ready."""
    from openbad.tasks.models import TaskKind

    task = TaskModel.new("Planned task", kind=TaskKind.RESEARCH)
    store.create_task(task)
    result = plan_task(task, store)

    first, second, third = result.nodes

    assert gate.is_ready(task.task_id, first.node_id) is True
    assert gate.is_ready(task.task_id, second.node_id) is False
    assert gate.is_ready(task.task_id, third.node_id) is False


# ---------------------------------------------------------------------------
# Retry increments
# ---------------------------------------------------------------------------


def test_retry_count_starts_at_zero(policy: RetryPolicy, store: TaskStore) -> None:
    task = make_task(store)
    node = make_node(store, task.task_id, max_retries=3)

    assert policy.retry_count(node.node_id) == 0


def test_record_attempt_increments_count(policy: RetryPolicy, store: TaskStore) -> None:
    task = make_task(store)
    node = make_node(store, task.task_id, max_retries=3)

    policy.record_attempt(node.node_id)

    assert policy.retry_count(node.node_id) == 1


def test_retry_permitted_within_limit(policy: RetryPolicy, store: TaskStore) -> None:
    task = make_task(store)
    node = make_node(store, task.task_id, max_retries=2)

    allowed_1 = policy.record_attempt(node.node_id)
    allowed_2 = policy.record_attempt(node.node_id)

    assert allowed_1 is True
    assert allowed_2 is True
    assert policy.retry_count(node.node_id) == 2


# ---------------------------------------------------------------------------
# Blocked transition at retry limit
# ---------------------------------------------------------------------------


def test_retry_exhaustion_blocks_node(policy: RetryPolicy, store: TaskStore) -> None:
    task = make_task(store)
    node = make_node(store, task.task_id, max_retries=1)

    policy.record_attempt(node.node_id)  # count=1 ≤ max=1 → still allowed
    allowed = policy.record_attempt(node.node_id)  # count=2 > max=1 → BLOCKED

    assert allowed is False
    updated = store.get_node(node.node_id)
    assert updated is not None
    assert updated.status == NodeStatus.BLOCKED


def test_zero_retries_exhausted_immediately(policy: RetryPolicy, store: TaskStore) -> None:
    task = make_task(store)
    node = make_node(store, task.task_id, max_retries=0)

    allowed = policy.record_attempt(node.node_id)  # count=1 > max=0 → BLOCKED

    assert allowed is False
    updated = store.get_node(node.node_id)
    assert updated is not None
    assert updated.status == NodeStatus.BLOCKED


def test_unknown_node_raises(policy: RetryPolicy) -> None:
    with pytest.raises(ValueError, match="No node found"):
        policy.record_attempt("no-such-node")
