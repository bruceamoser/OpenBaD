"""Tests for memory base types and configuration."""

from __future__ import annotations

import time

import pytest
import yaml

from openbad.memory.base import MemoryEntry, MemoryStore, MemoryTier

# ------------------------------------------------------------------ #
# MemoryTier
# ------------------------------------------------------------------ #


class TestMemoryTier:
    def test_values(self) -> None:
        assert MemoryTier.STM.value == "stm"
        assert MemoryTier.EPISODIC.value == "episodic"
        assert MemoryTier.SEMANTIC.value == "semantic"
        assert MemoryTier.PROCEDURAL.value == "procedural"

    def test_all_tiers(self) -> None:
        assert len(MemoryTier) == 4


# ------------------------------------------------------------------ #
# MemoryEntry
# ------------------------------------------------------------------ #


class TestMemoryEntry:
    def test_create_with_defaults(self) -> None:
        entry = MemoryEntry(key="k1", value="hello", tier=MemoryTier.STM)
        assert entry.key == "k1"
        assert entry.value == "hello"
        assert entry.tier is MemoryTier.STM
        assert entry.entry_id  # auto-generated
        assert entry.access_count == 0
        assert entry.context == ""
        assert entry.metadata == {}

    def test_entry_id_uniqueness(self) -> None:
        a = MemoryEntry(key="k", value="v", tier=MemoryTier.STM)
        b = MemoryEntry(key="k", value="v", tier=MemoryTier.STM)
        assert a.entry_id != b.entry_id

    def test_is_expired_no_ttl(self) -> None:
        entry = MemoryEntry(key="k", value="v", tier=MemoryTier.STM)
        assert not entry.is_expired(time.time() + 99999)

    def test_is_expired_zero_ttl(self) -> None:
        entry = MemoryEntry(
            key="k", value="v", tier=MemoryTier.STM,
            ttl_seconds=0, created_at=1000.0,
        )
        assert not entry.is_expired(9999.0)

    def test_is_expired_within_ttl(self) -> None:
        entry = MemoryEntry(
            key="k", value="v", tier=MemoryTier.STM,
            ttl_seconds=60.0, created_at=1000.0,
        )
        assert not entry.is_expired(1050.0)

    def test_is_expired_past_ttl(self) -> None:
        entry = MemoryEntry(
            key="k", value="v", tier=MemoryTier.STM,
            ttl_seconds=60.0, created_at=1000.0,
        )
        assert entry.is_expired(1061.0)

    def test_touch(self) -> None:
        entry = MemoryEntry(key="k", value="v", tier=MemoryTier.STM)
        entry.touch(100.0)
        assert entry.accessed_at == 100.0
        assert entry.access_count == 1
        entry.touch(200.0)
        assert entry.accessed_at == 200.0
        assert entry.access_count == 2

    def test_to_dict_roundtrip(self) -> None:
        entry = MemoryEntry(
            key="k1", value={"data": [1, 2]}, tier=MemoryTier.EPISODIC,
            created_at=1000.0, accessed_at=1001.0, access_count=3,
            ttl_seconds=300.0, context="task-42",
            metadata={"source": "test"},
        )
        d = entry.to_dict()
        restored = MemoryEntry.from_dict(d)
        assert restored.key == entry.key
        assert restored.value == entry.value
        assert restored.tier == entry.tier
        assert restored.entry_id == entry.entry_id
        assert restored.created_at == entry.created_at
        assert restored.context == entry.context
        assert restored.metadata == entry.metadata

    def test_from_dict_defaults(self) -> None:
        d = {
            "entry_id": "abc123",
            "key": "k",
            "value": "v",
            "tier": "stm",
        }
        entry = MemoryEntry.from_dict(d)
        assert entry.entry_id == "abc123"
        assert entry.access_count == 0
        assert entry.context == ""


# ------------------------------------------------------------------ #
# MemoryStore ABC
# ------------------------------------------------------------------ #


class TestMemoryStoreABC:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            MemoryStore()  # type: ignore[abstract]

    def test_subclass_must_implement(self) -> None:
        class Incomplete(MemoryStore):
            pass

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_valid_subclass(self) -> None:
        class InMemory(MemoryStore):
            def __init__(self):
                self._data: dict[str, MemoryEntry] = {}

            def write(self, entry):
                self._data[entry.key] = entry
                return entry.entry_id

            def read(self, key):
                return self._data.get(key)

            def delete(self, key):
                return self._data.pop(key, None) is not None

            def query(self, prefix):
                return [e for k, e in self._data.items() if k.startswith(prefix)]

            def list_keys(self):
                return list(self._data.keys())

            def size(self):
                return len(self._data)

        store = InMemory()
        entry = MemoryEntry(key="test", value="data", tier=MemoryTier.STM)
        eid = store.write(entry)
        assert eid == entry.entry_id
        assert store.read("test") is entry
        assert store.size() == 1
        assert store.list_keys() == ["test"]
        assert store.query("tes") == [entry]
        assert store.delete("test")
        assert store.size() == 0


# ------------------------------------------------------------------ #
# MemoryConfig
# ------------------------------------------------------------------ #


class TestMemoryConfig:
    def test_defaults(self) -> None:
        from openbad.memory.config import MemoryConfig
        cfg = MemoryConfig()
        assert cfg.stm_max_tokens == 32768
        assert cfg.stm_ttl_seconds == 3600.0
        assert cfg.ltm_backend == "json"
        assert cfg.pruning_interval_seconds == 3600.0
        assert cfg.forgetting_half_life_hours == 168.0
        assert cfg.episodic_retention_days == 7.0

    def test_from_yaml(self, tmp_path) -> None:
        from openbad.memory.config import MemoryConfig
        data = {
            "memory": {
                "stm_max_tokens": 16384,
                "stm_ttl_seconds": 1800.0,
                "ltm_backend": "sqlite",
                "ltm_storage_dir": "/tmp/mem",  # noqa: S108
                "pruning_interval_seconds": 600.0,
                "forgetting_half_life_hours": 72.0,
            },
        }
        yaml_file = tmp_path / "mem.yaml"
        yaml_file.write_text(yaml.dump(data))

        cfg = MemoryConfig.from_yaml(yaml_file)
        assert cfg.stm_max_tokens == 16384
        assert cfg.ltm_backend == "sqlite"
        assert cfg.forgetting_half_life_hours == 72.0

    def test_to_dict(self) -> None:
        from openbad.memory.config import MemoryConfig
        cfg = MemoryConfig()
        d = cfg.to_dict()
        assert d["stm_max_tokens"] == 32768
        assert d["ltm_backend"] == "json"

    def test_from_yaml_empty_file(self, tmp_path) -> None:
        from openbad.memory.config import MemoryConfig
        yaml_file = tmp_path / "empty.yaml"
        yaml_file.write_text("")
        cfg = MemoryConfig.from_yaml(yaml_file)
        assert cfg.stm_max_tokens == 32768  # defaults
        assert cfg.episodic_retention_days == 7.0

    def test_episodic_retention_days_from_yaml(self, tmp_path) -> None:
        from openbad.memory.config import MemoryConfig
        data = {"memory": {"episodic_retention_days": 14.0}}
        yaml_file = tmp_path / "ret.yaml"
        yaml_file.write_text(yaml.dump(data))
        cfg = MemoryConfig.from_yaml(yaml_file)
        assert cfg.episodic_retention_days == 14.0

    def test_episodic_retention_days_to_dict(self) -> None:
        from openbad.memory.config import MemoryConfig
        cfg = MemoryConfig(episodic_retention_days=3.0)
        assert cfg.to_dict()["episodic_retention_days"] == 3.0
