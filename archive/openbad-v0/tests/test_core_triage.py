from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from openbad.state.db import initialize_state_db

from openbad.plugins.core_triage import (
    CORE_TRIAGE_MANIFEST,
    CapabilityError,
    CapabilityRequest,
    CapabilityResponse,
    CoreTriageExecutor,
)
from openbad.tasks.models import TaskKind, TaskModel, TaskStatus
from openbad.tasks.store import TaskStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    path = tmp_path / "state.db"
    return initialize_state_db(path)


@pytest.fixture()
def executor(db: sqlite3.Connection) -> CoreTriageExecutor:
    return CoreTriageExecutor(db)


@pytest.fixture()
def context_task_id(db: sqlite3.Connection) -> str:
    """A pre-existing owning task used as the request context."""
    store = TaskStore(db)
    task = TaskModel.new("context", kind=TaskKind.USER_REQUESTED)
    store.create_task(task)
    return task.task_id


# ---------------------------------------------------------------------------
# Manifest content
# ---------------------------------------------------------------------------


def test_manifest_name() -> None:
    assert CORE_TRIAGE_MANIFEST.name == "core_triage"


def test_manifest_has_three_capabilities() -> None:
    ids = {c.id for c in CORE_TRIAGE_MANIFEST.capabilities}
    assert "core_triage.create_task" in ids
    assert "core_triage.queue_research" in ids
    assert "core_triage.cancel_task" in ids


# ---------------------------------------------------------------------------
# create_task capability
# ---------------------------------------------------------------------------


def test_create_task_returns_response(executor: CoreTriageExecutor) -> None:
    req = CapabilityRequest(
        capability_id="core_triage.create_task",
        params={"title": "Triage me"},
    )

    result = executor.execute(req)

    assert isinstance(result, CapabilityResponse)
    assert result.output["title"] == "Triage me"
    assert result.output["task_id"]


def test_create_task_persists_in_db(
    executor: CoreTriageExecutor, db: sqlite3.Connection
) -> None:
    req = CapabilityRequest(
        capability_id="core_triage.create_task",
        params={"title": "Stored task"},
    )

    result = executor.execute(req)

    assert isinstance(result, CapabilityResponse)
    store = TaskStore(db)
    task = store.get_task(result.output["task_id"])
    assert task is not None
    assert task.title == "Stored task"


def test_create_task_emits_event(
    executor: CoreTriageExecutor,
    db: sqlite3.Connection,
    context_task_id: str,
) -> None:
    req = CapabilityRequest(
        capability_id="core_triage.create_task",
        params={"title": "From context"},
        task_id=context_task_id,
    )

    result = executor.execute(req)

    assert isinstance(result, CapabilityResponse)
    assert result.event_id is not None


def test_create_task_missing_title_returns_error(executor: CoreTriageExecutor) -> None:
    req = CapabilityRequest(
        capability_id="core_triage.create_task",
        params={},
    )

    result = executor.execute(req)

    assert isinstance(result, CapabilityError)
    assert "title" in result.message.lower()


# ---------------------------------------------------------------------------
# queue_research capability
# ---------------------------------------------------------------------------


def test_queue_research_returns_response(executor: CoreTriageExecutor) -> None:
    req = CapabilityRequest(
        capability_id="core_triage.queue_research",
        params={"title": "Research X"},
    )

    result = executor.execute(req)

    assert isinstance(result, CapabilityResponse)
    assert result.output["kind"] == "research"
    assert result.output["task_id"]


def test_queue_research_creates_research_task(
    executor: CoreTriageExecutor, db: sqlite3.Connection
) -> None:
    req = CapabilityRequest(
        capability_id="core_triage.queue_research",
        params={"title": "Find patterns"},
    )

    result = executor.execute(req)

    assert isinstance(result, CapabilityResponse)
    store = TaskStore(db)
    task = store.get_task(result.output["task_id"])
    assert task is not None
    assert task.kind == TaskKind.RESEARCH


def test_queue_research_missing_title_returns_error(executor: CoreTriageExecutor) -> None:
    req = CapabilityRequest(
        capability_id="core_triage.queue_research",
        params={},
    )

    result = executor.execute(req)

    assert isinstance(result, CapabilityError)
    assert "title" in result.message.lower()


# ---------------------------------------------------------------------------
# cancel_task capability
# ---------------------------------------------------------------------------


def test_cancel_task_returns_response(
    executor: CoreTriageExecutor, db: sqlite3.Connection
) -> None:
    store = TaskStore(db)
    task = TaskModel.new("to cancel", kind=TaskKind.USER_REQUESTED)
    store.create_task(task)

    req = CapabilityRequest(
        capability_id="core_triage.cancel_task",
        params={"task_id": task.task_id},
    )

    result = executor.execute(req)

    assert isinstance(result, CapabilityResponse)
    assert result.output["cancelled_task_id"] == task.task_id


def test_cancel_task_updates_status(
    executor: CoreTriageExecutor, db: sqlite3.Connection
) -> None:
    store = TaskStore(db)
    task = TaskModel.new("cancel me", kind=TaskKind.USER_REQUESTED)
    store.create_task(task)

    req = CapabilityRequest(
        capability_id="core_triage.cancel_task",
        params={"task_id": task.task_id},
    )

    executor.execute(req)

    updated = store.get_task(task.task_id)
    assert updated is not None
    assert updated.status == TaskStatus.CANCELLED


def test_cancel_task_unknown_id_returns_error(executor: CoreTriageExecutor) -> None:
    req = CapabilityRequest(
        capability_id="core_triage.cancel_task",
        params={"task_id": "nonexistent"},
    )

    result = executor.execute(req)

    assert isinstance(result, CapabilityError)
    assert "nonexistent" in result.message


def test_cancel_already_cancelled_returns_error(
    executor: CoreTriageExecutor, db: sqlite3.Connection
) -> None:
    store = TaskStore(db)
    task = TaskModel.new("already done", kind=TaskKind.USER_REQUESTED)
    store.create_task(task)
    store.update_task_status(task.task_id, TaskStatus.DONE)

    req = CapabilityRequest(
        capability_id="core_triage.cancel_task",
        params={"task_id": task.task_id},
    )

    result = executor.execute(req)

    assert isinstance(result, CapabilityError)


# ---------------------------------------------------------------------------
# Unknown capability
# ---------------------------------------------------------------------------


def test_unknown_capability_returns_error(executor: CoreTriageExecutor) -> None:
    req = CapabilityRequest(
        capability_id="core_triage.does_not_exist",
        params={},
    )

    result = executor.execute(req)

    assert isinstance(result, CapabilityError)
    assert "unknown" in result.message.lower()
