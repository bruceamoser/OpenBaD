from __future__ import annotations

from pathlib import Path

import pytest

from openbad.state.db import initialize_state_db
from openbad.tasks.executor import NodeExecutor, RunRecord
from openbad.tasks.models import NodeModel, NodeStatus, RunStatus, TaskModel, TaskStatus
from openbad.tasks.store import TaskStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path):
    return initialize_state_db(tmp_path / "state.db")


@pytest.fixture()
def executor(db):
    return NodeExecutor(db)


@pytest.fixture()
def store(db):
    return TaskStore(db)


def make_task_and_node(store: TaskStore) -> tuple[TaskModel, NodeModel]:
    task = TaskModel.new("Test task")
    store.create_task(task)
    node = NodeModel.new(task.task_id, "Test node")
    store.create_node(node)
    return task, node


# ---------------------------------------------------------------------------
# Run lifecycle transitions
# ---------------------------------------------------------------------------


def test_start_run_creates_record(executor: NodeExecutor, store: TaskStore) -> None:
    task, node = make_task_and_node(store)

    run = executor.start_run(task.task_id, node_id=node.node_id)

    assert run.run_id is not None
    assert run.task_id == task.task_id
    assert run.node_id == node.node_id
    assert run.status == RunStatus.RUNNING
    assert run.started_at > 0


def test_start_run_transitions_task_to_running(executor: NodeExecutor, store: TaskStore) -> None:
    task, node = make_task_and_node(store)

    executor.start_run(task.task_id, node_id=node.node_id)

    updated_task = store.get_task(task.task_id)
    assert updated_task is not None
    assert updated_task.status == TaskStatus.RUNNING


def test_start_run_transitions_node_to_running(executor: NodeExecutor, store: TaskStore) -> None:
    task, node = make_task_and_node(store)

    executor.start_run(task.task_id, node_id=node.node_id)

    updated_node = store.get_node(node.node_id)
    assert updated_node is not None
    assert updated_node.status == NodeStatus.RUNNING


def test_finish_run_success(executor: NodeExecutor, store: TaskStore) -> None:
    task, node = make_task_and_node(store)
    run = executor.start_run(task.task_id, node_id=node.node_id)

    result = executor.finish_run(run.run_id, success=True)

    assert result.status == RunStatus.DONE
    assert result.finished_at is not None


def test_finish_run_success_transitions_node_done(
    executor: NodeExecutor, store: TaskStore
) -> None:
    task, node = make_task_and_node(store)
    run = executor.start_run(task.task_id, node_id=node.node_id)

    executor.finish_run(run.run_id, success=True)

    updated = store.get_node(node.node_id)
    assert updated is not None
    assert updated.status == NodeStatus.DONE


def test_finish_run_failure(executor: NodeExecutor, store: TaskStore) -> None:
    task, node = make_task_and_node(store)
    run = executor.start_run(task.task_id, node_id=node.node_id)

    result = executor.finish_run(run.run_id, success=False, failure_summary="timed out")

    assert result.status == RunStatus.FAILED
    assert result.finished_at is not None


def test_finish_run_failure_transitions_node_failed(
    executor: NodeExecutor, store: TaskStore
) -> None:
    task, node = make_task_and_node(store)
    run = executor.start_run(task.task_id, node_id=node.node_id)

    executor.finish_run(run.run_id, success=False)

    updated = store.get_node(node.node_id)
    assert updated is not None
    assert updated.status == NodeStatus.FAILED


def test_finish_run_unknown_id_raises(executor: NodeExecutor) -> None:
    with pytest.raises(ValueError, match="No run found"):
        executor.finish_run("no-such-run")


# ---------------------------------------------------------------------------
# Failure summary persistence
# ---------------------------------------------------------------------------


def test_failure_summary_written_as_event(executor: NodeExecutor, store: TaskStore) -> None:
    task, node = make_task_and_node(store)
    run = executor.start_run(task.task_id, node_id=node.node_id)

    executor.finish_run(run.run_id, success=False, failure_summary="quota exceeded")

    events = store.list_events(task.task_id)
    failed_events = [e for e in events if e["event_type"] == "node_failed"]
    assert len(failed_events) == 1
    assert failed_events[0]["payload"]["summary"] == "quota exceeded"


def test_failure_details_in_event_payload(executor: NodeExecutor, store: TaskStore) -> None:
    task, node = make_task_and_node(store)
    run = executor.start_run(task.task_id, node_id=node.node_id)

    executor.finish_run(
        run.run_id,
        success=False,
        failure_summary="error",
        failure_details={"code": 500, "retries": 3},
    )

    events = store.list_events(task.task_id)
    payload = events[-1]["payload"]
    assert payload["code"] == 500
    assert payload["retries"] == 3


def test_no_failure_event_on_success(executor: NodeExecutor, store: TaskStore) -> None:
    task, node = make_task_and_node(store)
    run = executor.start_run(task.task_id, node_id=node.node_id)

    executor.finish_run(run.run_id, success=True)

    events = store.list_events(task.task_id)
    failed_events = [e for e in events if e["event_type"] == "node_failed"]
    assert failed_events == []


# ---------------------------------------------------------------------------
# Run record retrieval
# ---------------------------------------------------------------------------


def test_get_run_returns_record(executor: NodeExecutor, store: TaskStore) -> None:
    task, node = make_task_and_node(store)
    run = executor.start_run(task.task_id, node_id=node.node_id)

    fetched = executor.get_run(run.run_id)

    assert fetched is not None
    assert fetched.run_id == run.run_id
    assert fetched.status == RunStatus.RUNNING


def test_get_run_unknown_returns_none(executor: NodeExecutor) -> None:
    assert executor.get_run("missing") is None


def test_list_runs_ordered_by_start(executor: NodeExecutor, store: TaskStore) -> None:
    task, _ = make_task_and_node(store)

    r1 = executor.start_run(task.task_id)
    r2 = executor.start_run(task.task_id)

    runs = executor.list_runs(task.task_id)
    assert len(runs) == 2
    assert runs[0].run_id == r1.run_id
    assert runs[1].run_id == r2.run_id


def test_run_record_to_dict_round_trip() -> None:
    r = RunRecord(
        run_id="r1",
        task_id="t1",
        node_id="n1",
        status=RunStatus.DONE,
        actor="system",
        routing_provider=None,
        routing_model=None,
        started_at=1.0,
        finished_at=2.0,
    )
    assert RunRecord.from_dict(r.to_dict()) == r


def test_start_run_without_node(executor: NodeExecutor, store: TaskStore) -> None:
    task = TaskModel.new("Task only")
    store.create_task(task)

    run = executor.start_run(task.task_id, actor="cron")

    assert run.node_id is None
    assert run.actor == "cron"
    assert run.status == RunStatus.RUNNING
