"""Tests for openbad.immune_system.quarantine — quarantine subsystem."""

from __future__ import annotations

import hashlib
import json
import time

import pytest
from cryptography.fernet import Fernet

from openbad.immune_system.quarantine import QuarantineEntry, QuarantineStore

# ---------------------------------------------------------------------------
# QuarantineEntry basics
# ---------------------------------------------------------------------------


class TestQuarantineEntry:
    def test_fields(self) -> None:
        e = QuarantineEntry(
            entry_id="abc",
            timestamp=1.0,
            sha256="deadbeef",
            threat_type="prompt_injection",
            confidence=0.95,
            source_topic="agent/sensory/test",
            payload_file="abc.enc",
        )
        assert e.entry_id == "abc"
        assert e.human_reviewed is False

    def test_frozen(self) -> None:
        e = QuarantineEntry(
            entry_id="x",
            timestamp=0,
            sha256="y",
            threat_type="t",
            confidence=0,
            source_topic="s",
            payload_file="f",
        )
        with pytest.raises(AttributeError):
            e.human_reviewed = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# QuarantineStore — storage and retrieval
# ---------------------------------------------------------------------------


class TestQuarantineStore:
    @pytest.fixture()
    def store(self, tmp_path: pytest.TempPathFactory) -> QuarantineStore:
        key = Fernet.generate_key()
        return QuarantineStore(
            quarantine_dir=tmp_path / "qdir",
            encryption_key=key,
        )

    def test_quarantine_returns_entry(
        self, store: QuarantineStore
    ) -> None:
        entry = store.quarantine(
            b"malicious payload",
            "prompt_injection",
            0.99,
            "agent/sensory/test",
        )
        assert entry.entry_id
        assert entry.threat_type == "prompt_injection"
        assert entry.confidence == 0.99
        assert entry.human_reviewed is False

    def test_sha256_matches(self, store: QuarantineStore) -> None:
        payload = b"test payload data"
        entry = store.quarantine(payload, "test", 0.5, "topic")
        expected = hashlib.sha256(payload).hexdigest()
        assert entry.sha256 == expected

    def test_get_payload_decryption(
        self, store: QuarantineStore
    ) -> None:
        payload = b"sensitive payload bytes"
        entry = store.quarantine(payload, "test", 0.9, "topic")
        retrieved = store.get_payload(entry.entry_id)
        assert retrieved == payload

    def test_get_payload_missing(self, store: QuarantineStore) -> None:
        with pytest.raises(FileNotFoundError):
            store.get_payload("nonexistent-id")

    def test_verify_hash_valid(self, store: QuarantineStore) -> None:
        payload = b"check hash"
        entry = store.quarantine(payload, "test", 0.8, "topic")
        assert store.verify_hash(entry) is True

    def test_verify_hash_missing_file(
        self, store: QuarantineStore, tmp_path: pytest.TempPathFactory
    ) -> None:
        entry = QuarantineEntry(
            entry_id="gone",
            timestamp=0,
            sha256="abc",
            threat_type="t",
            confidence=0,
            source_topic="s",
            payload_file="gone.enc",
        )
        assert store.verify_hash(entry) is False


# ---------------------------------------------------------------------------
# Append-only log
# ---------------------------------------------------------------------------


class TestAppendOnlyLog:
    @pytest.fixture()
    def store(self, tmp_path: pytest.TempPathFactory) -> QuarantineStore:
        key = Fernet.generate_key()
        return QuarantineStore(
            quarantine_dir=tmp_path / "qdir",
            encryption_key=key,
        )

    def test_log_file_created(
        self, store: QuarantineStore, tmp_path: pytest.TempPathFactory
    ) -> None:
        store.quarantine(b"data", "test", 0.5, "topic")
        log_path = tmp_path / "qdir" / "quarantine.jsonl"
        assert log_path.exists()

    def test_log_has_one_line_per_entry(
        self, store: QuarantineStore, tmp_path: pytest.TempPathFactory
    ) -> None:
        store.quarantine(b"one", "t1", 0.5, "topic")
        store.quarantine(b"two", "t2", 0.7, "topic")
        store.quarantine(b"three", "t3", 0.9, "topic")
        log_path = tmp_path / "qdir" / "quarantine.jsonl"
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 3

    def test_log_entries_are_valid_json(
        self, store: QuarantineStore, tmp_path: pytest.TempPathFactory
    ) -> None:
        store.quarantine(b"payload", "test", 0.5, "topic")
        log_path = tmp_path / "qdir" / "quarantine.jsonl"
        data = json.loads(log_path.read_text().strip())
        assert "entry_id" in data
        assert "sha256" in data


# ---------------------------------------------------------------------------
# Listing / filtering
# ---------------------------------------------------------------------------


class TestListEntries:
    @pytest.fixture()
    def store(self, tmp_path: pytest.TempPathFactory) -> QuarantineStore:
        key = Fernet.generate_key()
        return QuarantineStore(
            quarantine_dir=tmp_path / "qdir",
            encryption_key=key,
        )

    def test_list_all(self, store: QuarantineStore) -> None:
        store.quarantine(b"a", "t1", 0.5, "topic")
        store.quarantine(b"b", "t2", 0.6, "topic")
        entries = store.list_entries()
        assert len(entries) == 2

    def test_list_empty(self, store: QuarantineStore) -> None:
        entries = store.list_entries()
        assert entries == []

    def test_filter_by_threat_type(
        self, store: QuarantineStore
    ) -> None:
        store.quarantine(b"a", "ssrf", 0.9, "topic")
        store.quarantine(b"b", "injection", 0.8, "topic")
        store.quarantine(b"c", "ssrf", 0.7, "topic")
        entries = store.list_entries(threat_type="ssrf")
        assert len(entries) == 2
        assert all(e.threat_type == "ssrf" for e in entries)

    def test_filter_by_since(self, store: QuarantineStore) -> None:
        store.quarantine(b"old", "t", 0.5, "topic")
        cutoff = time.time()
        time.sleep(0.01)
        store.quarantine(b"new", "t", 0.5, "topic")
        entries = store.list_entries(since=cutoff)
        assert len(entries) == 1


# ---------------------------------------------------------------------------
# Encryption at rest
# ---------------------------------------------------------------------------


class TestEncryption:
    def test_payload_file_is_encrypted(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        key = Fernet.generate_key()
        store = QuarantineStore(
            quarantine_dir=tmp_path / "qdir",
            encryption_key=key,
        )
        payload = b"top secret data"
        entry = store.quarantine(payload, "test", 0.9, "topic")
        enc_path = (
            tmp_path / "qdir" / "payloads" / entry.payload_file
        )
        raw = enc_path.read_bytes()
        # Encrypted data should NOT equal the original plaintext
        assert raw != payload
        # But Fernet should be able to decrypt it
        f = Fernet(key)
        assert f.decrypt(raw) == payload

    def test_ephemeral_key_when_none(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Store works without explicit key (generates ephemeral key)."""
        store = QuarantineStore(
            quarantine_dir=tmp_path / "qdir",
        )
        entry = store.quarantine(b"data", "test", 0.5, "topic")
        # Should be able to retrieve within same store instance
        assert store.get_payload(entry.entry_id) == b"data"
