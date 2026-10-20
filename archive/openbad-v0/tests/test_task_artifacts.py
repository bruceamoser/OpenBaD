from __future__ import annotations

from pathlib import Path

import pytest

from openbad.state.db import initialize_state_db
from openbad.tasks.artifacts import ArtifactStore
from openbad.tasks.models import TaskModel
from openbad.tasks.notes import NoteStore
from openbad.tasks.store import TaskStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path):
    return initialize_state_db(tmp_path / "state.db")


@pytest.fixture()
def artifact_store(db, tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(db, base_dir=tmp_path / "data" / "tasks")


@pytest.fixture()
def task_id(db) -> str:
    store = TaskStore(db)
    task = TaskModel.new("Artifact task")
    store.create_task(task)
    return task.task_id


# ---------------------------------------------------------------------------
# Artifact write path
# ---------------------------------------------------------------------------


def test_artifact_file_created(artifact_store: ArtifactStore, task_id: str) -> None:
    path = artifact_store.write_artifact(task_id, "result.json", {"answer": 42})

    assert path.exists()
    assert path.name == "result.json"
    assert path.parent.name == task_id


def test_artifact_content_correct(artifact_store: ArtifactStore, task_id: str) -> None:
    import json

    artifact_store.write_artifact(task_id, "out.json", {"key": "value", "n": 7})

    stored_path = artifact_store._base_dir / task_id / "out.json"
    data = json.loads(stored_path.read_text())
    assert data == {"key": "value", "n": 7}


def test_artifact_directory_created(
    artifact_store: ArtifactStore, task_id: str
) -> None:
    artifact_store.write_artifact(task_id, "x.json", {})

    assert (artifact_store._base_dir / task_id).is_dir()


# ---------------------------------------------------------------------------
# Note reference validity
# ---------------------------------------------------------------------------


def test_note_references_artifact_path(
    db, artifact_store: ArtifactStore, task_id: str
) -> None:
    path = artifact_store.write_artifact(task_id, "data.json", {"x": 1})

    ns = NoteStore(db)
    notes = ns.list_notes(task_id)

    assert len(notes) == 1
    assert str(path) in notes[0].summary["artifact_refs"]


def test_note_stores_custom_summary(
    db, artifact_store: ArtifactStore, task_id: str
) -> None:
    artifact_store.write_artifact(
        task_id, "run.json", {}, summary_text="Run finished", facts=["done"]
    )

    ns = NoteStore(db)
    notes = ns.list_notes(task_id)
    assert notes[0].note_text == "Run finished"
    assert notes[0].summary["facts"] == ["done"]


def test_multiple_artifacts_have_separate_notes(
    db, artifact_store: ArtifactStore, task_id: str
) -> None:
    artifact_store.write_artifact(task_id, "a.json", {"n": 1})
    artifact_store.write_artifact(task_id, "b.json", {"n": 2})

    ns = NoteStore(db)
    notes = ns.list_notes(task_id)
    assert len(notes) == 2


# ---------------------------------------------------------------------------
# Resume from artifacts
# ---------------------------------------------------------------------------


def test_load_artifacts_returns_content(artifact_store: ArtifactStore, task_id: str) -> None:
    artifact_store.write_artifact(task_id, "r1.json", {"result": "alpha"})
    artifact_store.write_artifact(task_id, "r2.json", {"result": "beta"})

    loaded = artifact_store.load_artifacts(task_id)

    assert len(loaded) == 2
    results = {d["result"] for d in loaded}
    assert results == {"alpha", "beta"}


def test_load_artifacts_empty_for_no_notes(artifact_store: ArtifactStore, task_id: str) -> None:
    assert artifact_store.load_artifacts(task_id) == []


def test_load_artifacts_skips_missing_files(
    db, artifact_store: ArtifactStore, task_id: str
) -> None:
    """If an artifact file is deleted after being recorded, load is robust."""
    path = artifact_store.write_artifact(task_id, "gone.json", {"x": 1})
    path.unlink()  # simulate missing file

    loaded = artifact_store.load_artifacts(task_id)
    assert loaded == []
