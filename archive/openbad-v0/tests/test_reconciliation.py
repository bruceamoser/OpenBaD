"""Tests for library reconciliation — surprise-triggered book updates."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from openbad.active_inference.reconciliation import (
    RECONCILIATION_SURPRISE_THRESHOLD,
    check_library_reconciliation,
    create_reconciliation_task,
    is_reconcile_task,
    parse_reconcile_metadata,
)
from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.tasks.models import TaskKind, TaskModel, TaskPriority, TaskStatus

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _entry(
    key: str = "test-key",
    value: str = "test",
    library_refs: list[str] | None = None,
) -> MemoryEntry:
    metadata: dict[str, Any] = {}
    if library_refs is not None:
        metadata["library_refs"] = library_refs
    return MemoryEntry(key=key, value=value, tier=MemoryTier.SEMANTIC, metadata=metadata)


class FakeTaskStore:
    """Minimal task store recording create_task calls."""

    def __init__(self) -> None:
        self.tasks: list[TaskModel] = []

    def create_task(self, task: TaskModel) -> None:
        self.tasks.append(task)


# ---------------------------------------------------------------------------
# check_library_reconciliation
# ---------------------------------------------------------------------------


class TestCheckLibraryReconciliation:
    def test_below_threshold_returns_empty(self) -> None:
        entry = _entry(library_refs=["book-1"])
        assert check_library_reconciliation(entry, RECONCILIATION_SURPRISE_THRESHOLD - 0.01) == []

    def test_no_library_refs_returns_empty(self) -> None:
        entry = _entry()
        assert check_library_reconciliation(entry, 0.9) == []

    def test_empty_library_refs_returns_empty(self) -> None:
        entry = _entry(library_refs=[])
        assert check_library_reconciliation(entry, 0.9) == []

    def test_above_threshold_with_refs_returns_book_ids(self) -> None:
        entry = _entry(library_refs=["book-1", "book-2"])
        result = check_library_reconciliation(entry, RECONCILIATION_SURPRISE_THRESHOLD + 0.01)
        assert result == ["book-1", "book-2"]

    def test_exact_threshold_triggers(self) -> None:
        entry = _entry(library_refs=["book-1"])
        result = check_library_reconciliation(entry, RECONCILIATION_SURPRISE_THRESHOLD)
        assert result == ["book-1"]


# ---------------------------------------------------------------------------
# create_reconciliation_task
# ---------------------------------------------------------------------------


class TestCreateReconciliationTask:
    def test_creates_system_task(self) -> None:
        store = FakeTaskStore()
        task_id = create_reconciliation_task(
            store,
            book_id="abc-123",
            new_fact="The sky is blue",
            reason="surprise=0.85",
        )
        assert len(store.tasks) == 1
        task = store.tasks[0]
        assert task.task_id == task_id
        assert task.title.startswith("Library Reconciliation:")
        assert task.kind == TaskKind.SYSTEM
        assert task.priority == int(TaskPriority.NORMAL)
        assert task.status == TaskStatus.PENDING
        assert "abc-123" in task.description
        assert "The sky is blue" in task.description
        assert "[reconcile] book_id=abc-123" in task.description


# ---------------------------------------------------------------------------
# is_reconcile_task
# ---------------------------------------------------------------------------


class TestIsReconcileTask:
    def test_positive(self) -> None:
        task = TaskModel.new(
            "Library Reconciliation: abc12345",
            kind=TaskKind.SYSTEM,
        )
        assert is_reconcile_task(task) is True

    def test_negative(self) -> None:
        task = TaskModel.new("Regular task")
        assert is_reconcile_task(task) is False


# ---------------------------------------------------------------------------
# parse_reconcile_metadata
# ---------------------------------------------------------------------------


class TestParseReconcileMetadata:
    def test_extracts_book_id_and_fact(self) -> None:
        task = TaskModel.new(
            "Library Reconciliation: abc12345",
            description=(
                "Reconcile Library book abc-full-id with new information.\n\n"
                "New fact: Water is wet\n"
                "Reason: surprise=0.85\n\n"
                "[reconcile] book_id=abc-full-id"
            ),
            kind=TaskKind.SYSTEM,
        )
        meta = parse_reconcile_metadata(task)
        assert meta["book_id"] == "abc-full-id"
        assert meta["new_fact"] == "Water is wet"

    def test_missing_fields_returns_partial(self) -> None:
        task = TaskModel.new(
            "Library Reconciliation: x",
            description="No structured content here",
            kind=TaskKind.SYSTEM,
        )
        meta = parse_reconcile_metadata(task)
        assert "book_id" not in meta
        assert "new_fact" not in meta


# ---------------------------------------------------------------------------
# Engine integration — _check_reconciliation
# ---------------------------------------------------------------------------


class TestEngineCheckReconciliation:
    def test_poll_plugin_triggers_reconciliation(self) -> None:
        from openbad.active_inference.budget import ExplorationBudget
        from openbad.active_inference.config import ActiveInferenceConfig
        from openbad.active_inference.engine import ExplorationEngine
        from openbad.active_inference.world_model import WorldModel

        config = ActiveInferenceConfig(surprise_threshold=0.5)
        wm = WorldModel()
        budget = ExplorationBudget()

        mem_ctrl = MagicMock()
        entry = _entry(library_refs=["book-1"])
        mem_ctrl.semantic.query.return_value = [entry]

        task_store = FakeTaskStore()

        engine = ExplorationEngine(
            config, wm, budget,
            memory_controller=mem_ctrl,
            task_store=task_store,
        )

        # Simulate high surprise by calling _check_reconciliation directly
        engine._check_reconciliation("source-1", 0.85)

        assert len(task_store.tasks) == 1
        assert task_store.tasks[0].title.startswith("Library Reconciliation:")

    def test_no_reconciliation_when_no_controller(self) -> None:
        from openbad.active_inference.budget import ExplorationBudget
        from openbad.active_inference.config import ActiveInferenceConfig
        from openbad.active_inference.engine import ExplorationEngine
        from openbad.active_inference.world_model import WorldModel

        config = ActiveInferenceConfig(surprise_threshold=0.5)
        wm = WorldModel()
        budget = ExplorationBudget()

        engine = ExplorationEngine(config, wm, budget)
        # Should not raise
        engine._check_reconciliation("source-1", 0.85)

    def test_reconciliation_error_logged_not_raised(self) -> None:
        from openbad.active_inference.budget import ExplorationBudget
        from openbad.active_inference.config import ActiveInferenceConfig
        from openbad.active_inference.engine import ExplorationEngine
        from openbad.active_inference.world_model import WorldModel

        config = ActiveInferenceConfig(surprise_threshold=0.5)
        wm = WorldModel()
        budget = ExplorationBudget()

        mem_ctrl = MagicMock()
        mem_ctrl.semantic.query.side_effect = RuntimeError("DB error")

        engine = ExplorationEngine(
            config, wm, budget,
            memory_controller=mem_ctrl,
            task_store=MagicMock(),
        )
        # Should not raise
        engine._check_reconciliation("source-1", 0.85)
