"""Adaptive threat memory — learns from confirmed threats to detect future attacks."""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

# ------------------------------------------------------------------ #
# Data types
# ------------------------------------------------------------------ #


@dataclass(frozen=True)
class SignatureMatch:
    """A match against a learned threat signature."""

    signature_id: str
    confidence: float
    threat_type: str


@dataclass
class ThreatSignature:
    """A learned threat pattern."""

    signature_id: str
    pattern: str
    threat_type: str
    confidence: float
    source: str  # quarantine-confirmed | admin-added | auto-generated
    created_at: float = 0.0
    hit_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ThreatSignature:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ------------------------------------------------------------------ #
# Signature store
# ------------------------------------------------------------------ #


class ThreatSignatureStore:
    """Persistent store for learned threat signatures.

    Parameters
    ----------
    db_path:
        Path to the JSON file where signatures are persisted.
    similarity_threshold:
        Minimum SequenceMatcher ratio to consider a match (0.0–1.0).
    """

    def __init__(
        self,
        db_path: Path | str | None = None,
        similarity_threshold: float = 0.6,
    ) -> None:
        self._db_path = Path(db_path) if db_path else None
        self._threshold = similarity_threshold
        self._signatures: dict[str, ThreatSignature] = {}
        if self._db_path and self._db_path.exists():
            self._load()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def add_signature(
        self,
        pattern: str,
        threat_type: str,
        confidence: float = 0.9,
        source: str = "quarantine-confirmed",
    ) -> ThreatSignature:
        """Add a new threat signature to the store."""
        sig = ThreatSignature(
            signature_id=uuid.uuid4().hex[:12],
            pattern=pattern,
            threat_type=threat_type,
            confidence=min(max(confidence, 0.0), 1.0),
            source=source,
            created_at=time.time(),
        )
        self._signatures[sig.signature_id] = sig
        self._save()
        return sig

    def match(self, text: str) -> list[SignatureMatch]:
        """Compare text against all learned signatures.

        Uses both exact substring and fuzzy (SequenceMatcher) matching.
        Returns matches sorted by confidence descending.
        """
        matches: list[SignatureMatch] = []
        text_lower = text.lower()

        for sig in self._signatures.values():
            pattern_lower = sig.pattern.lower()

            # 1. Exact substring match
            if pattern_lower in text_lower:
                sig.hit_count += 1
                matches.append(
                    SignatureMatch(
                        signature_id=sig.signature_id,
                        confidence=sig.confidence,
                        threat_type=sig.threat_type,
                    )
                )
                continue

            # 2. Regex match (if pattern looks like a regex)
            if _is_regex_pattern(sig.pattern):
                try:
                    if re.search(sig.pattern, text, re.IGNORECASE):
                        sig.hit_count += 1
                        matches.append(
                            SignatureMatch(
                                signature_id=sig.signature_id,
                                confidence=sig.confidence * 0.9,
                                threat_type=sig.threat_type,
                            )
                        )
                        continue
                except re.error:
                    pass

            # 3. Fuzzy similarity
            ratio = SequenceMatcher(None, pattern_lower, text_lower).ratio()
            if ratio >= self._threshold:
                sig.hit_count += 1
                matches.append(
                    SignatureMatch(
                        signature_id=sig.signature_id,
                        confidence=sig.confidence * ratio,
                        threat_type=sig.threat_type,
                    )
                )

        matches.sort(key=lambda m: m.confidence, reverse=True)
        return matches

    def remove_signature(self, signature_id: str) -> bool:
        """Remove a signature by ID. Returns True if it existed."""
        if signature_id in self._signatures:
            del self._signatures[signature_id]
            self._save()
            return True
        return False

    def list_signatures(self) -> list[ThreatSignature]:
        """Return all stored signatures."""
        return list(self._signatures.values())

    def get_signature(self, signature_id: str) -> ThreatSignature | None:
        """Retrieve a single signature by ID."""
        return self._signatures.get(signature_id)

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def _save(self) -> None:
        if self._db_path is None:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        data = [sig.to_dict() for sig in self._signatures.values()]
        self._db_path.write_text(json.dumps(data, indent=2))

    def _load(self) -> None:
        if self._db_path is None or not self._db_path.exists():
            return
        raw = json.loads(self._db_path.read_text())
        for item in raw:
            sig = ThreatSignature.from_dict(item)
            self._signatures[sig.signature_id] = sig


def _is_regex_pattern(pattern: str) -> bool:
    """Heuristic: does the pattern contain regex metacharacters?"""
    return bool(set(pattern) & set(r".*+?[](){}|\\^$"))
