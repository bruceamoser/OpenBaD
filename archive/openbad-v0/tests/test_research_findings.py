from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from openbad.memory.base import MemoryEntry, MemoryStore, MemoryTier
from openbad.tasks.research_findings import (
    FindingStore,
    ResearchFinding,
    initialize_findings_db,
)

# ---------------------------------------------------------------------------
# Minimal in-memory MemoryStore stub
# ---------------------------------------------------------------------------


class MemoryStoreSpy(MemoryStore):
    """Spy: records write calls without any actual persistence."""

    def __init__(self) -> None:
        self.written: list[MemoryEntry] = []

    def write(self, entry: MemoryEntry) -> str:
        self.written.append(entry)
        return entry.entry_id

    def read(self, key: str) -> MemoryEntry | None:
        for e in reversed(self.written):
            if e.key == key:
                return e
        return None

    def delete(self, key: str) -> bool:
        before = len(self.written)
        self.written = [e for e in self.written if e.key != key]
        return len(self.written) < before

    def query(self, prefix: str) -> list[MemoryEntry]:
        return [e for e in self.written if e.key.startswith(prefix)]

    def list_keys(self) -> list[str]:
        return [e.key for e in self.written]

    def size(self) -> int:
        return len(self.written)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "findings.db")
    initialize_findings_db(conn)
    return conn


@pytest.fixture()
def spy() -> MemoryStoreSpy:
    return MemoryStoreSpy()


@pytest.fixture()
def store(db: sqlite3.Connection, spy: MemoryStoreSpy) -> FindingStore:
    return FindingStore(db, spy)


# ---------------------------------------------------------------------------
# Findings persistence
# ---------------------------------------------------------------------------


def test_persist_finding_returns_record(store: FindingStore) -> None:
    f = store.persist_finding("node-1", "Found something")

    assert isinstance(f, ResearchFinding)
    assert f.content == "Found something"
    assert f.validated is False


def test_persist_finding_not_validated(store: FindingStore) -> None:
    f = store.persist_finding("node-1", "raw finding")
    loaded = store.get_finding(f.finding_id)

    assert loaded is not None
    assert loaded.validated is False


def test_persist_finding_with_source_task(store: FindingStore) -> None:
    f = store.persist_finding("node-1", "content", source_task_id="task-abc")
    loaded = store.get_finding(f.finding_id)

    assert loaded is not None
    assert loaded.source_task_id == "task-abc"


def test_list_findings_for_node(store: FindingStore) -> None:
    store.persist_finding("node-1", "A")
    store.persist_finding("node-1", "B")
    store.persist_finding("node-2", "C")

    findings = store.list_findings("node-1")
    assert len(findings) == 2


# ---------------------------------------------------------------------------
# Memory writeback
# ---------------------------------------------------------------------------


def test_commit_finding_calls_memory_write(store: FindingStore, spy: MemoryStoreSpy) -> None:
    store.commit_finding("node-1", "important fact")

    assert len(spy.written) == 1
    entry = spy.written[0]
    assert entry.tier == MemoryTier.SEMANTIC
    assert "node-1" in entry.key


def test_commit_finding_writes_content_to_memory(store: FindingStore, spy: MemoryStoreSpy) -> None:
    store.commit_finding("node-1", "the answer")

    entry = spy.written[0]
    assert entry.value["content"] == "the answer"


def test_commit_finding_is_validated(store: FindingStore) -> None:
    f = store.commit_finding("node-1", "validated fact")
    loaded = store.get_finding(f.finding_id)

    assert loaded is not None
    assert loaded.validated is True


def test_persist_finding_does_not_call_memory_write(
    store: FindingStore, spy: MemoryStoreSpy
) -> None:
    store.persist_finding("node-1", "raw")
    assert len(spy.written) == 0


# ---------------------------------------------------------------------------
# Reevaluation signal emission
# ---------------------------------------------------------------------------


def test_commit_finding_emits_signal_when_source_task(store: FindingStore) -> None:
    store.commit_finding("node-1", "fact", source_task_id="task-1")

    signals = store.list_signals("task-1")
    assert len(signals) == 1
    assert signals[0].task_id == "task-1"


def test_commit_finding_no_signal_without_source_task(store: FindingStore) -> None:
    store.commit_finding("node-1", "fact")
    # With no source task, we expect no signals for arbitrary task IDs
    assert store.list_signals("some-task") == []


def test_multiple_findings_emit_multiple_signals(store: FindingStore) -> None:
    store.commit_finding("node-1", "fact A", source_task_id="task-1")
    store.commit_finding("node-2", "fact B", source_task_id="task-1")

    signals = store.list_signals("task-1")
    assert len(signals) == 2
