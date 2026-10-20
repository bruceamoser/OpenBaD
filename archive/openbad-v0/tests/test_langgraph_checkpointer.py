"""Tests for openbad.frameworks.langgraph_checkpointer."""

from __future__ import annotations

import uuid

import pytest

from openbad.frameworks.langgraph_checkpointer import OpenBaDCheckpointSaver
from openbad.memory.episodic import EpisodicMemory
from openbad.memory.stm import ShortTermMemory

# ── Fixtures ──────────────────────────────────────────────────────────── #


@pytest.fixture()
def episodic(tmp_path):
    return EpisodicMemory(storage_path=tmp_path / "episodic.json")


@pytest.fixture()
def stm():
    return ShortTermMemory()


@pytest.fixture()
def saver(episodic, stm):
    return OpenBaDCheckpointSaver(episodic=episodic, stm=stm)


@pytest.fixture()
def saver_no_stm(episodic):
    return OpenBaDCheckpointSaver(episodic=episodic, stm=None)


# ── Helpers ───────────────────────────────────────────────────────────── #


def _make_config(thread_id="t1", checkpoint_ns="", checkpoint_id=""):
    return {
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_ns": checkpoint_ns,
            "checkpoint_id": checkpoint_id,
        },
    }


def _make_checkpoint(checkpoint_id=None, channel_values=None):
    return {
        "v": 1,
        "ts": "2025-01-01T00:00:00Z",
        "id": checkpoint_id or uuid.uuid4().hex[:8],
        "channel_values": channel_values or {"messages": ["hello"]},
        "channel_versions": {"messages": 1},
        "versions_seen": {},
        "pending_sends": [],
    }


# ── Round-trip: put → get_tuple ──────────────────────────────────────── #


class TestPutAndGetTuple:
    def test_round_trip(self, saver):
        cp = _make_checkpoint("cp1")
        config = _make_config(thread_id="t1")
        metadata = {"source": "input", "step": 0}

        result_config = saver.put(config, cp, metadata, {"messages": 1})
        assert result_config["configurable"]["checkpoint_id"] == "cp1"

        retrieved = saver.get_tuple(_make_config(thread_id="t1", checkpoint_id="cp1"))
        assert retrieved is not None
        assert retrieved.checkpoint["id"] == "cp1"
        assert retrieved.checkpoint["channel_values"] == {"messages": ["hello"]}
        assert retrieved.metadata["source"] == "input"

    def test_get_latest_without_checkpoint_id(self, saver):
        saver.put(_make_config("t1"), _make_checkpoint("cp1"), {"step": 0}, {})
        cfg = _make_config("t1", checkpoint_id="cp1")
        saver.put(cfg, _make_checkpoint("cp2"), {"step": 1}, {})

        retrieved = saver.get_tuple(_make_config("t1"))
        assert retrieved is not None
        assert retrieved.checkpoint["id"] == "cp2"

    def test_returns_none_for_missing(self, saver):
        result = saver.get_tuple(_make_config("nonexistent", checkpoint_id="nope"))
        assert result is None


# ── STM sync ─────────────────────────────────────────────────────────── #


class TestSTMSync:
    def test_active_state_in_stm(self, saver, stm):
        cp = _make_checkpoint("cp1")
        saver.put(_make_config("t1"), cp, {}, {})
        keys = stm.list_keys()
        assert any("cp1" in k for k in keys)

    def test_stm_used_for_fast_read(self, saver, stm):
        cp = _make_checkpoint("cp1")
        saver.put(_make_config("t1"), cp, {}, {})
        # Reading with checkpoint_id should hit STM first.
        retrieved = saver.get_tuple(_make_config("t1", checkpoint_id="cp1"))
        assert retrieved is not None
        assert retrieved.checkpoint["id"] == "cp1"

    def test_no_stm_still_works(self, saver_no_stm):
        cp = _make_checkpoint("cp1")
        saver_no_stm.put(_make_config("t1"), cp, {"step": 0}, {})
        retrieved = saver_no_stm.get_tuple(_make_config("t1", checkpoint_id="cp1"))
        assert retrieved is not None
        assert retrieved.checkpoint["id"] == "cp1"


# ── Episodic persistence ─────────────────────────────────────────────── #


class TestEpisodicPersistence:
    def test_survives_reload(self, episodic, tmp_path):
        saver = OpenBaDCheckpointSaver(episodic=episodic)
        cp = _make_checkpoint("cp1", {"key": "value"})
        saver.put(_make_config("t1"), cp, {"step": 0}, {})
        episodic.save()

        # Create a new episodic memory pointing at the same file.
        episodic2 = EpisodicMemory(storage_path=tmp_path / "episodic.json")
        saver2 = OpenBaDCheckpointSaver(episodic=episodic2)
        retrieved = saver2.get_tuple(_make_config("t1", checkpoint_id="cp1"))
        assert retrieved is not None
        assert retrieved.checkpoint["channel_values"] == {"key": "value"}


# ── Listing ──────────────────────────────────────────────────────────── #


class TestListing:
    def test_lists_all_checkpoints(self, saver):
        saver.put(_make_config("t1"), _make_checkpoint("cp1"), {"step": 0}, {})
        saver.put(
            _make_config("t1", checkpoint_id="cp1"),
            _make_checkpoint("cp2"),
            {"step": 1},
            {},
        )
        results = list(saver.list(_make_config("t1")))
        assert len(results) == 2
        # Newest first.
        assert results[0].checkpoint["id"] == "cp2"
        assert results[1].checkpoint["id"] == "cp1"

    def test_list_with_limit(self, saver):
        saver.put(_make_config("t1"), _make_checkpoint("cp1"), {}, {})
        saver.put(_make_config("t1", checkpoint_id="cp1"), _make_checkpoint("cp2"), {}, {})
        saver.put(_make_config("t1", checkpoint_id="cp2"), _make_checkpoint("cp3"), {}, {})
        results = list(saver.list(_make_config("t1"), limit=2))
        assert len(results) == 2

    def test_list_with_before(self, saver):
        saver.put(_make_config("t1"), _make_checkpoint("cp1"), {}, {})
        saver.put(_make_config("t1", checkpoint_id="cp1"), _make_checkpoint("cp2"), {}, {})
        saver.put(_make_config("t1", checkpoint_id="cp2"), _make_checkpoint("cp3"), {}, {})
        results = list(
            saver.list(_make_config("t1"), before=_make_config("t1", checkpoint_id="cp3"))
        )
        assert all(r.checkpoint["id"] < "cp3" for r in results)

    def test_list_none_config(self, saver):
        results = list(saver.list(None))
        assert results == []


# ── Parent config ────────────────────────────────────────────────────── #


class TestParentConfig:
    def test_parent_config_set(self, saver):
        saver.put(_make_config("t1"), _make_checkpoint("cp1"), {}, {})
        saver.put(
            _make_config("t1", checkpoint_id="cp1"),
            _make_checkpoint("cp2"),
            {},
            {},
        )
        retrieved = saver.get_tuple(_make_config("t1", checkpoint_id="cp2"))
        assert retrieved is not None
        assert retrieved.parent_config is not None
        assert retrieved.parent_config["configurable"]["checkpoint_id"] == "cp1"

    def test_first_checkpoint_no_parent(self, saver):
        saver.put(_make_config("t1"), _make_checkpoint("cp1"), {}, {})
        retrieved = saver.get_tuple(_make_config("t1", checkpoint_id="cp1"))
        assert retrieved is not None
        assert retrieved.parent_config is None


# ── put_writes / pending_writes ──────────────────────────────────────── #


class TestPutWrites:
    def test_writes_stored_and_retrieved(self, saver):
        cp = _make_checkpoint("cp1")
        saver.put(_make_config("t1"), cp, {}, {})
        saver.put_writes(
            _make_config("t1", checkpoint_id="cp1"),
            [("messages", "write1"), ("output", "write2")],
            task_id="task-1",
        )
        retrieved = saver.get_tuple(_make_config("t1", checkpoint_id="cp1"))
        assert retrieved is not None
        assert retrieved.pending_writes is not None
        assert len(retrieved.pending_writes) == 2


# ── Async variants ───────────────────────────────────────────────────── #


class TestAsyncVariants:
    @pytest.mark.asyncio
    async def test_aput_and_aget_tuple(self, saver):
        cp = _make_checkpoint("cp1")
        config = _make_config("t1")
        await saver.aput(config, cp, {"step": 0}, {})
        retrieved = await saver.aget_tuple(_make_config("t1", checkpoint_id="cp1"))
        assert retrieved is not None
        assert retrieved.checkpoint["id"] == "cp1"

    @pytest.mark.asyncio
    async def test_alist(self, saver):
        await saver.aput(_make_config("t1"), _make_checkpoint("cp1"), {}, {})
        await saver.aput(
            _make_config("t1", checkpoint_id="cp1"),
            _make_checkpoint("cp2"),
            {},
            {},
        )
        results = [r async for r in saver.alist(_make_config("t1"))]
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_aput_writes(self, saver):
        cp = _make_checkpoint("cp1")
        await saver.aput(_make_config("t1"), cp, {}, {})
        await saver.aput_writes(
            _make_config("t1", checkpoint_id="cp1"),
            [("ch", "val")],
            task_id="t-1",
        )
        retrieved = await saver.aget_tuple(_make_config("t1", checkpoint_id="cp1"))
        assert retrieved is not None
        assert retrieved.pending_writes is not None


# ── Version management ───────────────────────────────────────────────── #


class TestVersionManagement:
    def test_get_next_version_from_none(self, saver):
        assert saver.get_next_version(None, None) == 1

    def test_get_next_version_increments(self, saver):
        assert saver.get_next_version(3, None) == 4
