from __future__ import annotations

from pathlib import Path

import pytest

from openbad.state.db import initialize_state_db
from openbad.tasks.models import TaskKind, TaskModel
from openbad.tasks.planner import DEFAULT_TEMPLATES, NodeTemplate, PlanResult, plan_task
from openbad.tasks.store import TaskStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> TaskStore:
    conn = initialize_state_db(tmp_path / "state.db")
    return TaskStore(conn)


def make_task(kind: TaskKind = TaskKind.USER_REQUESTED) -> TaskModel:
    return TaskModel.new(f"{kind} task", kind=kind)


# ---------------------------------------------------------------------------
# DAG shape for representative task kind
# ---------------------------------------------------------------------------


def test_user_requested_dag_shape(store: TaskStore) -> None:
    task = make_task(TaskKind.USER_REQUESTED)
    store.create_task(task)

    result = plan_task(task, store)

    node_types = [n.node_type for n in result.nodes]
    assert node_types == ["clarify", "plan", "execute", "review"]


def test_research_dag_shape(store: TaskStore) -> None:
    task = make_task(TaskKind.RESEARCH)
    store.create_task(task)

    result = plan_task(task, store)

    node_types = [n.node_type for n in result.nodes]
    assert node_types == ["gather", "analyse", "summarise"]


def test_system_dag_shape(store: TaskStore) -> None:
    task = make_task(TaskKind.SYSTEM)
    store.create_task(task)

    result = plan_task(task, store)

    assert len(result.nodes) == 1
    assert result.nodes[0].node_type == "execute"
    assert result.edges == []


def test_scheduled_dag_shape(store: TaskStore) -> None:
    task = make_task(TaskKind.SCHEDULED)
    store.create_task(task)

    result = plan_task(task, store)

    assert len(result.nodes) == 1
    assert result.nodes[0].node_type == "execute"


# ---------------------------------------------------------------------------
# Edge dependency validation
# ---------------------------------------------------------------------------


def test_edges_form_linear_chain(store: TaskStore) -> None:
    task = make_task(TaskKind.USER_REQUESTED)
    store.create_task(task)

    result = plan_task(task, store)

    # 4 nodes → 3 edges
    assert len(result.edges) == len(result.nodes) - 1
    for i, (from_id, to_id) in enumerate(result.edges):
        assert from_id == result.nodes[i].node_id
        assert to_id == result.nodes[i + 1].node_id


def test_edges_reference_valid_nodes(store: TaskStore) -> None:
    task = make_task(TaskKind.RESEARCH)
    store.create_task(task)

    result = plan_task(task, store)
    node_ids = {n.node_id for n in result.nodes}

    for from_id, to_id in result.edges:
        assert from_id in node_ids
        assert to_id in node_ids


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_nodes_persisted(store: TaskStore) -> None:
    task = make_task(TaskKind.USER_REQUESTED)
    store.create_task(task)

    result = plan_task(task, store)

    persisted = store.list_nodes(task.task_id)
    assert len(persisted) == len(result.nodes)
    assert {n.node_id for n in persisted} == {n.node_id for n in result.nodes}


def test_edges_persisted(store: TaskStore) -> None:
    task = make_task(TaskKind.USER_REQUESTED)
    store.create_task(task)

    result = plan_task(task, store)

    persisted_edges = store.list_edges(task.task_id)
    assert set(persisted_edges) == set(result.edges)


def test_plan_result_is_frozen(store: TaskStore) -> None:
    task = make_task(TaskKind.SYSTEM)
    store.create_task(task)

    result = plan_task(task, store)

    assert isinstance(result, PlanResult)
    with pytest.raises((TypeError, AttributeError)):
        result.nodes = []  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Retry and reward fields
# ---------------------------------------------------------------------------


def test_max_retries_from_template(store: TaskStore) -> None:
    task = make_task(TaskKind.USER_REQUESTED)
    store.create_task(task)

    result = plan_task(task, store)

    # Matches DEFAULT_TEMPLATES[USER_REQUESTED]
    expected_retries = [t.max_retries for t in DEFAULT_TEMPLATES[TaskKind.USER_REQUESTED]]
    actual_retries = [n.max_retries for n in result.nodes]
    assert actual_retries == expected_retries


def test_reward_program_id_from_template(store: TaskStore) -> None:
    custom = {
        TaskKind.SYSTEM: [
            NodeTemplate("Execute", "execute", max_retries=1, reward_program_id="prog-1"),
        ]
    }
    task = make_task(TaskKind.SYSTEM)
    store.create_task(task)

    result = plan_task(task, store, templates=custom)

    assert result.nodes[0].reward_program_id == "prog-1"


# ---------------------------------------------------------------------------
# Unknown kind raises
# ---------------------------------------------------------------------------


def test_unknown_kind_raises(store: TaskStore) -> None:
    task = make_task(TaskKind.USER_REQUESTED)
    store.create_task(task)

    with pytest.raises(ValueError, match="No DAG template"):
        plan_task(task, store, templates={})


# ---------------------------------------------------------------------------
# Custom templates
# ---------------------------------------------------------------------------


def test_custom_template_overrides_default(store: TaskStore) -> None:
    custom = {
        TaskKind.USER_REQUESTED: [
            NodeTemplate("A", "step-a"),
            NodeTemplate("B", "step-b"),
        ]
    }
    task = make_task(TaskKind.USER_REQUESTED)
    store.create_task(task)

    result = plan_task(task, store, templates=custom)

    assert [n.node_type for n in result.nodes] == ["step-a", "step-b"]
    assert len(result.edges) == 1
