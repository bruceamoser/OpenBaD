"""SQLite-backed memory store with cognitive retrieval.

Implements the ``MemoryStore`` ABC using the engrams schema from
migration 0007.  Retrieval is scored by a composite of BM25 full-text
match, ACT-R temporal activation, and Hebbian association weights.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass

from openbad.memory.base import MemoryEntry, MemoryStore, MemoryTier
from openbad.memory.cognitive import (
    act_r_activation,
    composite_score,
    hebbian_update,
)

logger = logging.getLogger(__name__)

_ACTIVATION_LOG_LIMIT = 50  # ring-buffer size per tier


@dataclass
class ActivationResult:
    """A scored retrieval result with explainability."""

    entry: MemoryEntry
    score: float
    why: str  # e.g. "BM25(0.78) + act_r(0.94) + hebbian(0.16) confidence=0.95"


class CognitiveMemoryStore(MemoryStore):
    """SQLite-backed memory store with cognitive retrieval.

    Parameters
    ----------
    conn:
        Open SQLite connection (WAL mode, foreign keys ON).
    tier:
        Which memory tier this store manages.
    """

    def __init__(self, conn: sqlite3.Connection, tier: MemoryTier) -> None:
        self._conn = conn
        self._tier = tier.value

    # ------------------------------------------------------------------
    # MemoryStore ABC
    # ------------------------------------------------------------------

    def write(self, entry: MemoryEntry) -> str:
        now = time.time()
        engram_id = entry.entry_id or uuid.uuid4().hex[:16]
        metadata_json = json.dumps(entry.metadata) if entry.metadata else "{}"
        concept = entry.context or ""
        content = entry.value if isinstance(entry.value, str) else json.dumps(entry.value)

        self._conn.execute(
            """INSERT INTO engrams
               (engram_id, tier, key, concept, content, confidence,
                access_count, last_access_at, created_at, updated_at,
                ttl_seconds, context, metadata, state)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
               ON CONFLICT(engram_id) DO UPDATE SET
                 content    = excluded.content,
                 concept    = excluded.concept,
                 confidence = excluded.confidence,
                 context    = excluded.context,
                 metadata   = excluded.metadata,
                 ttl_seconds = excluded.ttl_seconds,
                 updated_at = excluded.updated_at""",
            (
                engram_id,
                self._tier,
                entry.key,
                concept,
                content,
                entry.metadata.get("confidence", 0.5),
                entry.access_count,
                entry.accessed_at or now,
                entry.created_at or now,
                now,
                entry.ttl_seconds,
                entry.context or "",
                metadata_json,
            ),
        )
        self._conn.commit()
        return engram_id

    def read(self, key: str) -> MemoryEntry | None:
        now = time.time()
        row = self._conn.execute(
            """SELECT * FROM engrams
               WHERE key = ? AND tier = ? AND state = 'active'""",
            (key, self._tier),
        ).fetchone()
        if row is None:
            return None

        # STM TTL check
        if (
            row["ttl_seconds"] is not None
            and row["ttl_seconds"] > 0
            and (now - row["created_at"]) > row["ttl_seconds"]
        ):
            self.delete(key)
            return None

        # Learning on every read: touch access stats
        self._conn.execute(
            """UPDATE engrams
               SET access_count = access_count + 1,
                   last_access_at = ?,
                   updated_at = ?
               WHERE engram_id = ?""",
            (now, now, row["engram_id"]),
        )
        self._conn.commit()

        return self._row_to_entry(row)

    def delete(self, key: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM engrams WHERE key = ? AND tier = ?",
            (key, self._tier),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def query(self, prefix: str) -> list[MemoryEntry]:
        if prefix:
            rows = self._conn.execute(
                """SELECT * FROM engrams
                   WHERE tier = ? AND state = 'active' AND key LIKE ?
                   ORDER BY last_access_at DESC""",
                (self._tier, f"{prefix}%"),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT * FROM engrams
                   WHERE tier = ? AND state = 'active'
                   ORDER BY last_access_at DESC""",
                (self._tier,),
            ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def list_keys(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT key FROM engrams WHERE tier = ? AND state = 'active'",
            (self._tier,),
        ).fetchall()
        return [r["key"] for r in rows]

    def size(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM engrams WHERE tier = ? AND state = 'active'",
            (self._tier,),
        ).fetchone()
        return row[0]

    # ------------------------------------------------------------------
    # Cognitive retrieval
    # ------------------------------------------------------------------

    def activate(
        self,
        context: str,
        limit: int = 10,
    ) -> list[ActivationResult]:
        """Full cognitive retrieval pipeline.

        1. FTS5 BM25 candidate selection
        2. ACT-R temporal scoring
        3. Hebbian association boost
        4. Composite ranking
        5. Record activation + update Hebbian weights
        """
        now = time.time()

        # 1. FTS candidates
        candidates = self._fts_candidates(context, limit * 3)
        if not candidates:
            return []

        # 2+3+4. Score each candidate
        results: list[ActivationResult] = []
        for engram_id, bm25 in candidates:
            row = self._conn.execute(
                "SELECT * FROM engrams WHERE engram_id = ? AND state = 'active'",
                (engram_id,),
            ).fetchone()
            if row is None:
                continue

            # STM TTL check
            if (
                row["ttl_seconds"] is not None
                and row["ttl_seconds"] > 0
                and (now - row["created_at"]) > row["ttl_seconds"]
            ):
                continue

            # ACT-R activation
            age_days = max((now - row["last_access_at"]) / 86400.0, 0.001)
            act_r = act_r_activation(row["access_count"], age_days)

            # Hebbian boost
            hebb = self._hebbian_boost(engram_id)

            # Confidence
            conf = row["confidence"]

            # Composite
            score = composite_score(abs(bm25), act_r, hebb, conf)

            why = (
                f"BM25({abs(bm25):.2f}) + act_r({act_r:.2f})"
                f" + hebbian({hebb:.2f}) confidence={conf:.2f}"
            )

            results.append(ActivationResult(
                entry=self._row_to_entry(row),
                score=score,
                why=why,
            ))

        # Sort by score descending
        results.sort(key=lambda r: r.score, reverse=True)
        results = results[:limit]

        # 5. Record activation and update Hebbian
        activated_ids = [r.entry.entry_id for r in results]
        self._record_activations(activated_ids, context, now)
        self._update_hebbian_weights(activated_ids, now)
        self._prune_activation_log()

        return results

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fts_candidates(
        self, query: str, limit: int,
    ) -> list[tuple[str, float]]:
        """Return (engram_id, bm25_score) from FTS5."""
        try:
            rows = self._conn.execute(
                """SELECT f.engram_id, f.rank
                   FROM engrams_fts f
                   JOIN engrams e ON e.engram_id = f.engram_id
                   WHERE engrams_fts MATCH ?
                     AND e.tier = ?
                     AND e.state = 'active'
                   ORDER BY f.rank
                   LIMIT ?""",
                (query, self._tier, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # Invalid FTS query syntax
            return []
        return [(r["engram_id"], r["rank"]) for r in rows]

    def _hebbian_boost(self, engram_id: str) -> float:
        """Sum of association weights to recently activated engrams."""
        row = self._conn.execute(
            """SELECT COALESCE(SUM(a.weight), 0.0) AS total
               FROM engram_associations a
               WHERE a.source_id = ? OR a.target_id = ?""",
            (engram_id, engram_id),
        ).fetchone()
        return row["total"] if row else 0.0

    def _record_activations(
        self,
        engram_ids: list[str],
        context: str,
        now: float,
    ) -> None:
        """Insert into activation_log and bump access stats."""
        for eid in engram_ids:
            self._conn.execute(
                "INSERT INTO activation_log"
                " (engram_id, activated_at, query_context)"
                " VALUES (?, ?, ?)",
                (eid, now, context),
            )
            self._conn.execute(
                """UPDATE engrams
                   SET access_count = access_count + 1,
                       last_access_at = ?,
                       updated_at = ?
                   WHERE engram_id = ?""",
                (now, now, eid),
            )
        self._conn.commit()

    def _update_hebbian_weights(
        self, engram_ids: list[str], now: float,
    ) -> None:
        """Strengthen associations between co-activated engrams."""
        if len(engram_ids) < 2:
            return
        for i, a in enumerate(engram_ids):
            for b in engram_ids[i + 1 :]:
                self._strengthen_pair(a, b, now)
        self._conn.commit()

    def _strengthen_pair(self, a: str, b: str, now: float) -> None:
        """Bidirectional Hebbian weight update for a pair."""
        for src, tgt in ((a, b), (b, a)):
            row = self._conn.execute(
                "SELECT weight FROM engram_associations WHERE source_id = ? AND target_id = ?",
                (src, tgt),
            ).fetchone()
            if row is not None:
                new_w = hebbian_update(row["weight"])
                self._conn.execute(
                    """UPDATE engram_associations
                       SET weight = ?,
                           co_activation_count = co_activation_count + 1,
                           updated_at = ?
                       WHERE source_id = ? AND target_id = ?""",
                    (new_w, now, src, tgt),
                )
            else:
                self._conn.execute(
                    """INSERT INTO engram_associations
                       (source_id, target_id, weight, co_activation_count, created_at, updated_at)
                       VALUES (?, ?, 0.1, 1, ?, ?)""",
                    (src, tgt, now, now),
                )

    def _prune_activation_log(self) -> None:
        """Keep only the last N activation entries per tier."""
        self._conn.execute(
            """DELETE FROM activation_log
               WHERE log_id NOT IN (
                   SELECT al.log_id
                   FROM activation_log al
                   JOIN engrams e ON e.engram_id = al.engram_id
                   WHERE e.tier = ?
                   ORDER BY al.activated_at DESC
                   LIMIT ?
               )
               AND engram_id IN (
                   SELECT engram_id FROM engrams WHERE tier = ?
               )""",
            (self._tier, _ACTIVATION_LOG_LIMIT, self._tier),
        )
        self._conn.commit()

    def _row_to_entry(self, row: sqlite3.Row) -> MemoryEntry:
        """Convert a database row to a ``MemoryEntry``."""
        metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        # Preserve content: try parsing as JSON; if not, keep as string
        content = row["content"]
        try:
            value = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            value = content

        return MemoryEntry(
            entry_id=row["engram_id"],
            key=row["key"],
            value=value,
            tier=MemoryTier(row["tier"]),
            created_at=row["created_at"],
            accessed_at=row["last_access_at"],
            access_count=row["access_count"],
            ttl_seconds=row["ttl_seconds"],
            context=row["context"],
            metadata=metadata,
        )
