"""Quarantine subsystem — append-only encrypted storage for blocked payloads."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from cryptography.fernet import Fernet


@dataclass(frozen=True)
class QuarantineEntry:
    """Metadata record for a quarantined payload."""

    entry_id: str
    timestamp: float
    sha256: str
    threat_type: str
    confidence: float
    source_topic: str
    payload_file: str
    human_reviewed: bool = False


class QuarantineStore:
    """Append-only quarantine store with Fernet encryption at rest.

    Metadata is stored in a JSON-lines log file.  Raw payloads are
    individually encrypted and stored as separate files.
    """

    def __init__(
        self,
        quarantine_dir: str | Path = "quarantine",
        *,
        encryption_key: bytes | None = None,
        encryption_key_env: str = "OPENBAD_QUARANTINE_KEY",
    ) -> None:
        self._dir = Path(quarantine_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

        self._payloads_dir = self._dir / "payloads"
        self._payloads_dir.mkdir(exist_ok=True)

        self._log_path = self._dir / "quarantine.jsonl"

        # Resolve encryption key
        key = encryption_key
        if key is None:
            env_key = os.environ.get(encryption_key_env, "")
            if env_key:
                key = env_key.encode("utf-8")
        if key is None:
            # Generate an ephemeral key (only for testing / dev).
            key = Fernet.generate_key()

        self._fernet = Fernet(key)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def quarantine(
        self,
        payload: bytes,
        threat_type: str,
        confidence: float,
        source_topic: str,
    ) -> QuarantineEntry:
        """Store *payload* in quarantine and return the metadata entry."""
        entry_id = uuid.uuid4().hex
        sha256 = hashlib.sha256(payload).hexdigest()
        payload_filename = f"{entry_id}.enc"
        payload_path = self._payloads_dir / payload_filename

        # Encrypt and write payload
        encrypted = self._fernet.encrypt(payload)
        payload_path.write_bytes(encrypted)

        entry = QuarantineEntry(
            entry_id=entry_id,
            timestamp=time.time(),
            sha256=sha256,
            threat_type=threat_type,
            confidence=confidence,
            source_topic=source_topic,
            payload_file=payload_filename,
        )

        # Append metadata to log (append-only)
        with open(self._log_path, "a") as f:
            f.write(json.dumps(asdict(entry)) + "\n")

        return entry

    def list_entries(
        self,
        *,
        since: float | None = None,
        threat_type: str | None = None,
    ) -> list[QuarantineEntry]:
        """Return quarantine entries, optionally filtered."""
        if not self._log_path.exists():
            return []

        entries: list[QuarantineEntry] = []
        with open(self._log_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                entry = QuarantineEntry(**data)
                if since is not None and entry.timestamp < since:
                    continue
                if (
                    threat_type is not None
                    and entry.threat_type != threat_type
                ):
                    continue
                entries.append(entry)
        return entries

    def get_payload(self, entry_id: str) -> bytes:
        """Decrypt and return the raw payload for *entry_id*.

        Raises FileNotFoundError if the payload file does not exist.
        """
        payload_path = self._payloads_dir / f"{entry_id}.enc"
        if not payload_path.exists():
            msg = f"Payload not found: {entry_id}"
            raise FileNotFoundError(msg)
        encrypted = payload_path.read_bytes()
        return self._fernet.decrypt(encrypted)

    def verify_hash(self, entry: QuarantineEntry) -> bool:
        """Verify that the stored payload matches the recorded SHA-256."""
        try:
            payload = self.get_payload(entry.entry_id)
        except FileNotFoundError:
            return False
        return hashlib.sha256(payload).hexdigest() == entry.sha256
