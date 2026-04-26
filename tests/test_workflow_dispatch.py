"""Tests for WorkflowDispatcher — LangGraph task execution adapter."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openbad.state.db import initialize_state_db
from openbad.tasks.models import (
    TaskKind,
    TaskModel,
    TaskPriority,
    TaskStatus,
)
from openbad.tasks.store import TaskStore
from openbad.tasks.workflow_dispatch import WorkflowDispatcher


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    return initialize_state_db(tmp_path / "state.db")


@pytest.fixture()
def store(db: sqlite3.Connection) -> TaskStore:
    return TaskStore(db)


@pytest.fixture()
def dispatcher(db: sqlite3.Connection) -> WorkflowDispatcher:
    return WorkflowDispatcher(db)


def _make_task(store: TaskStore, kind: str = "user_requested") -> TaskModel:
    import uuid

    task = TaskModel(
        task_id=str(uuid.uuid4()),
        title="Test task",
        description="A test description",
        kind=TaskKind(kind),
        horizon="immediate",
        priority=TaskPriority.NORMAL,
        status=TaskStatus.PENDING,
        owner="test-user",
        root_task_id="",
    )
    task.root_task_id = task.task_id
    return store.create_task(task)


class TestDispatchSuccess:
    def test_transitions_to_running_then_done(
        self, dispatcher: WorkflowDispatcher, store: TaskStore
    ) -> None:
        task = _make_task(store)

        with patch(
            "openbad.tasks.workflow_dispatch.get_workflow"
        ) as mock_get:
            mock_graph = MagicMock()
            mock_graph.invoke.return_value = {"status": "done", "results": []}
            mock_get.return_value = mock_graph

            dispatcher(task, "lease-1")

        updated = store.get_task(task.task_id)
        assert updated is not None
        assert updated.status == TaskStatus.DONE

    def test_passes_task_metadata_in_state(
        self, dispatcher: WorkflowDispatcher, store: TaskStore
    ) -> None:
        task = _make_task(store)

        with patch(
            "openbad.tasks.workflow_dispatch.get_workflow"
        ) as mock_get:
            mock_graph = MagicMock()
            mock_graph.invoke.return_value = {"status": "done"}
            mock_get.return_value = mock_graph

            dispatcher(task, "lease-42")

            call_args = mock_graph.invoke.call_args
            state = call_args[0][0]
            assert state["task_metadata"]["task_id"] == task.task_id
            assert state["task_metadata"]["lease_id"] == "lease-42"
            assert state["task_metadata"]["kind"] == "user_requested"

    def test_uses_correct_workflow_kind(
        self, dispatcher: WorkflowDispatcher, store: TaskStore
    ) -> None:
        task = _make_task(store, kind="research")

        with patch(
            "openbad.tasks.workflow_dispatch.get_workflow"
        ) as mock_get:
            mock_graph = MagicMock()
            mock_graph.invoke.return_value = {"status": "done"}
            mock_get.return_value = mock_graph

            dispatcher(task, "lease-1")
            mock_get.assert_called_once_with("research", checkpointer=None)


class TestDispatchFailure:
    def test_workflow_returns_failed(
        self, dispatcher: WorkflowDispatcher, store: TaskStore
    ) -> None:
        task = _make_task(store)

        with patch(
            "openbad.tasks.workflow_dispatch.get_workflow"
        ) as mock_get:
            mock_graph = MagicMock()
            mock_graph.invoke.return_value = {
                "status": "failed",
                "error": "something broke",
            }
            mock_get.return_value = mock_graph

            dispatcher(task, "lease-1")

        updated = store.get_task(task.task_id)
        assert updated is not None
        assert updated.status == TaskStatus.FAILED

    def test_workflow_raises_exception(
        self, dispatcher: WorkflowDispatcher, store: TaskStore
    ) -> None:
        task = _make_task(store)

        with patch(
            "openbad.tasks.workflow_dispatch.get_workflow"
        ) as mock_get:
            mock_graph = MagicMock()
            mock_graph.invoke.side_effect = RuntimeError("boom")
            mock_get.return_value = mock_graph

            dispatcher(task, "lease-1")

        updated = store.get_task(task.task_id)
        assert updated is not None
        assert updated.status == TaskStatus.FAILED

    def test_unknown_kind_fails_task(
        self, dispatcher: WorkflowDispatcher, store: TaskStore
    ) -> None:
        task = _make_task(store)
        # Override kind to something not in the registry
        task.kind = TaskKind("system")

        with patch(
            "openbad.tasks.workflow_dispatch.get_workflow",
            side_effect=ValueError("Unknown task kind: 'bogus'"),
        ):
            dispatcher(task, "lease-1")

        updated = store.get_task(task.task_id)
        assert updated is not None
        assert updated.status == TaskStatus.FAILED


class TestCheckpointerPropagation:
    def test_passes_checkpointer_to_registry(
        self, db: sqlite3.Connection, store: TaskStore
    ) -> None:
        mock_cp = MagicMock()
        dispatcher = WorkflowDispatcher(db, checkpointer=mock_cp)
        task = _make_task(store)

        with patch(
            "openbad.tasks.workflow_dispatch.get_workflow"
        ) as mock_get:
            mock_graph = MagicMock()
            mock_graph.invoke.return_value = {"status": "done"}
            mock_get.return_value = mock_graph

            dispatcher(task, "lease-1")
            mock_get.assert_called_once_with(
                "user_requested", checkpointer=mock_cp
            )
