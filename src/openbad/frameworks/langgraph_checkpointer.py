"""LangGraph checkpoint saver backed by OpenBaD's memory hierarchy.

Persists LangGraph workflow state in episodic memory (JSON append-only
log) and mirrors active checkpoints in STM for fast access.  On
workflow completion the final state is available for sleep consolidation.

Public API
----------
``OpenBaDCheckpointSaver``
    Drop-in replacement for ``InMemorySaver`` or ``SqliteSaver``.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)

from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.memory.episodic import EpisodicMemory
from openbad.memory.stm import ShortTermMemory

# Prefix used for all episodic and STM keys managed by this saver.
_KEY_PREFIX = "langgraph:checkpoint"
_WRITES_PREFIX = "langgraph:writes"


def _checkpoint_key(thread_id: str, checkpoint_ns: str, checkpoint_id: str) -> str:
    ns = checkpoint_ns or ""
    return f"{_KEY_PREFIX}:{thread_id}:{ns}:{checkpoint_id}"


def _writes_key(
    thread_id: str, checkpoint_ns: str, checkpoint_id: str, task_id: str, idx: int
) -> str:
    ns = checkpoint_ns or ""
    return f"{_WRITES_PREFIX}:{thread_id}:{ns}:{checkpoint_id}:{task_id}:{idx}"


def _thread_prefix(thread_id: str, checkpoint_ns: str = "") -> str:
    ns = checkpoint_ns or ""
    return f"{_KEY_PREFIX}:{thread_id}:{ns}:"


def _writes_thread_prefix(thread_id: str, checkpoint_ns: str, checkpoint_id: str) -> str:
    ns = checkpoint_ns or ""
    return f"{_WRITES_PREFIX}:{thread_id}:{ns}:{checkpoint_id}:"


def _config_value(config: RunnableConfig, key: str, default: str = "") -> str:
    return str(config.get("configurable", {}).get(key, default))


def _serialize(obj: Any) -> str:
    def _default(o: Any) -> Any:
        if isinstance(o, set):
            return list(o)
        return str(o)

    return json.dumps(obj, default=_default, sort_keys=True)


def _deserialize(s: str) -> Any:
    return json.loads(s)


class OpenBaDCheckpointSaver(BaseCheckpointSaver[int]):
    """Persist LangGraph checkpoints in OpenBaD episodic + STM memory.

    Parameters
    ----------
    episodic:
        Episodic memory store for durable checkpoint storage.
    stm:
        Short-term memory for fast access to active workflow state.
        If ``None``, STM mirroring is skipped.
    """

    def __init__(
        self,
        episodic: EpisodicMemory,
        stm: ShortTermMemory | None = None,
    ) -> None:
        super().__init__()
        self._episodic = episodic
        self._stm = stm

    # ── Version management ────────────────────────────────────────── #

    def get_next_version(self, current: int | None, channel: Any) -> int:
        if current is None:
            return 1
        return current + 1

    # ── put ────────────────────────────────────────────────────────── #

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id = _config_value(config, "thread_id")
        checkpoint_ns = _config_value(config, "checkpoint_ns")
        checkpoint_id = checkpoint["id"]

        key = _checkpoint_key(thread_id, checkpoint_ns, checkpoint_id)
        now = time.time()

        payload = {
            "checkpoint": checkpoint,
            "metadata": metadata,
            "parent_checkpoint_id": _config_value(config, "checkpoint_id"),
        }

        entry = MemoryEntry(
            key=key,
            value=_serialize(payload),
            tier=MemoryTier.EPISODIC,
            created_at=now,
            metadata={
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
                "source": "langgraph",
            },
        )
        self._episodic.write(entry)

        # Mirror to STM for fast reads of active state.
        if self._stm is not None:
            stm_entry = MemoryEntry(
                key=key,
                value=_serialize(payload),
                tier=MemoryTier.STM,
                created_at=now,
                metadata=entry.metadata.copy(),
            )
            self._stm.write(stm_entry)

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            },
        }

    # ── put_writes ────────────────────────────────────────────────── #

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = _config_value(config, "thread_id")
        checkpoint_ns = _config_value(config, "checkpoint_ns")
        checkpoint_id = _config_value(config, "checkpoint_id")
        now = time.time()

        for idx, (channel, value) in enumerate(writes):
            key = _writes_key(thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
            payload = {
                "channel": channel,
                "value": value,
                "task_id": task_id,
                "task_path": task_path,
            }
            entry = MemoryEntry(
                key=key,
                value=_serialize(payload),
                tier=MemoryTier.EPISODIC,
                created_at=now,
                metadata={
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                    "task_id": task_id,
                    "source": "langgraph_writes",
                },
            )
            self._episodic.write(entry)

    # ── get_tuple ─────────────────────────────────────────────────── #

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = _config_value(config, "thread_id")
        checkpoint_ns = _config_value(config, "checkpoint_ns")
        checkpoint_id = _config_value(config, "checkpoint_id")

        if checkpoint_id:
            # Retrieve specific checkpoint.
            key = _checkpoint_key(thread_id, checkpoint_ns, checkpoint_id)
            # Try STM first for fast access.
            entry = (
                self._stm.read(key) if self._stm is not None else None
            ) or self._episodic.read(key)
            if entry is None:
                return None
            return self._entry_to_tuple(entry, thread_id, checkpoint_ns)

        # No checkpoint_id → return the latest for this thread.
        prefix = _thread_prefix(thread_id, checkpoint_ns)
        entries = self._episodic.query(prefix)
        if not entries:
            return None
        # Entries are chronological; take the last one.
        latest = entries[-1]
        return self._entry_to_tuple(latest, thread_id, checkpoint_ns)

    # ── list ──────────────────────────────────────────────────────── #

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        if config is None:
            return

        thread_id = _config_value(config, "thread_id")
        checkpoint_ns = _config_value(config, "checkpoint_ns")
        prefix = _thread_prefix(thread_id, checkpoint_ns)
        entries = self._episodic.query(prefix)

        # Reverse to get newest first.
        entries = list(reversed(entries))

        if before is not None:
            before_id = _config_value(before, "checkpoint_id")
            if before_id:
                entries = [e for e in entries if e.metadata.get("checkpoint_id", "") < before_id]

        if filter:
            for k, v in filter.items():
                entries = [e for e in entries if e.metadata.get(k) == v]

        if limit is not None:
            entries = entries[:limit]

        for entry in entries:
            tup = self._entry_to_tuple(entry, thread_id, checkpoint_ns)
            if tup is not None:
                yield tup

    # ── Async wrappers ────────────────────────────────────────────── #

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self.put_writes(config, writes, task_id, task_path)

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self.get_tuple(config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        for tup in self.list(config, filter=filter, before=before, limit=limit):
            yield tup

    # ── Helpers ───────────────────────────────────────────────────── #

    def _entry_to_tuple(
        self, entry: MemoryEntry, thread_id: str, checkpoint_ns: str
    ) -> CheckpointTuple | None:
        try:
            payload = _deserialize(entry.value)
        except (json.JSONDecodeError, TypeError):
            return None

        checkpoint: Checkpoint = payload["checkpoint"]
        metadata: CheckpointMetadata = payload.get("metadata", {})
        parent_id: str = payload.get("parent_checkpoint_id", "")
        checkpoint_id = checkpoint["id"]

        cfg: RunnableConfig = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            },
        }

        parent_cfg: RunnableConfig | None = None
        if parent_id:
            parent_cfg = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": parent_id,
                },
            }

        # Collect pending writes.
        writes_prefix = _writes_thread_prefix(thread_id, checkpoint_ns, checkpoint_id)
        write_entries = self._episodic.query(writes_prefix)
        pending_writes: list[tuple[str, str, Any]] = []
        for we in write_entries:
            try:
                wp = _deserialize(we.value)
                pending_writes.append((wp["task_id"], wp["channel"], wp["value"]))
            except (json.JSONDecodeError, TypeError, KeyError):
                continue

        return CheckpointTuple(
            config=cfg,
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_cfg,
            pending_writes=pending_writes or None,
        )
