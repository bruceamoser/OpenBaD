"""Cognitive sleep maintenance — Hebbian decay, activation pruning, source linking.

Runs as an optional post-pass after the existing STM → LTM consolidation
and LLM-based orchestrator.  Operates directly on the SQLite engrams
schema (migration 0007) to maintain cognitive health.

Pipeline:
1. Decay stale Hebbian association weights
2. Prune activation log ring buffer
3. Link new semantic engrams back to their episodic sources
4. Purge soft-deleted engrams older than retention window
5. Clean up expired STM entries
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from openbad.memory.cognitive import hebbian_decay

logger = logging.getLogger(__name__)


@dataclass
class CognitiveMaintenanceReport:
    """Summary of cognitive maintenance work."""

    associations_decayed: int = 0
    associations_pruned: int = 0
    activation_entries_pruned: int = 0
    source_links_created: int = 0
    soft_deleted_purged: int = 0
    expired_stm_cleaned: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "associations_decayed": self.associations_decayed,
            "associations_pruned": self.associations_pruned,
            "activation_entries_pruned": self.activation_entries_pruned,
            "source_links_created": self.source_links_created,
            "soft_deleted_purged": self.soft_deleted_purged,
            "expired_stm_cleaned": self.expired_stm_cleaned,
            "errors": self.errors,
        }


def run_cognitive_maintenance(
    conn: sqlite3.Connection,
    *,
    half_life_hours: float = 168.0,
    decay_threshold: float = 0.01,
    activation_log_limit: int = 500,
    soft_delete_retention_days: float = 7.0,
    publish_fn: Any | None = None,
) -> CognitiveMaintenanceReport:
    """Run the full cognitive maintenance pipeline.

    Parameters
    ----------
    conn:
        Open SQLite connection with the engrams schema.
    half_life_hours:
        Half-life for Hebbian decay (default: 1 week).
    decay_threshold:
        Associations below this weight after decay are pruned.
    activation_log_limit:
        Maximum activation log entries to retain.
    soft_delete_retention_days:
        Days before soft-deleted engrams are permanently purged.
    publish_fn:
        Optional ``(topic, payload)`` callback for MQTT events.
    """
    report = CognitiveMaintenanceReport()
    now = time.time()

    try:
        _decay_hebbian_weights(conn, now, half_life_hours, decay_threshold, report)
    except Exception as exc:
        report.errors.append(f"hebbian_decay: {exc}")
        logger.warning("Hebbian decay failed: %s", exc)

    try:
        _prune_activation_log(conn, activation_log_limit, report)
    except Exception as exc:
        report.errors.append(f"activation_prune: {exc}")
        logger.warning("Activation log prune failed: %s", exc)

    try:
        _link_semantic_sources(conn, now, report)
    except Exception as exc:
        report.errors.append(f"source_linking: {exc}")
        logger.warning("Source linking failed: %s", exc)

    try:
        _purge_soft_deleted(conn, now, soft_delete_retention_days, report)
    except Exception as exc:
        report.errors.append(f"soft_delete_purge: {exc}")
        logger.warning("Soft-delete purge failed: %s", exc)

    try:
        _clean_expired_stm(conn, now, report)
    except Exception as exc:
        report.errors.append(f"stm_cleanup: {exc}")
        logger.warning("STM cleanup failed: %s", exc)

    if publish_fn is not None:
        import json as _json

        publish_fn(
            "agent/sleep/consolidation",
            _json.dumps(report.to_dict()).encode(),
        )

    logger.info(
        "Cognitive maintenance: %d assoc decayed, %d pruned, "
        "%d activation entries pruned, %d source links, "
        "%d soft-deleted purged, %d expired STM cleaned",
        report.associations_decayed,
        report.associations_pruned,
        report.activation_entries_pruned,
        report.source_links_created,
        report.soft_deleted_purged,
        report.expired_stm_cleaned,
    )
    return report


# ------------------------------------------------------------------
# Hebbian decay
# ------------------------------------------------------------------


def _decay_hebbian_weights(
    conn: sqlite3.Connection,
    now: float,
    half_life_hours: float,
    threshold: float,
    report: CognitiveMaintenanceReport,
) -> None:
    """Batch-decay all association weights; prune those below threshold."""
    rows = conn.execute(
        "SELECT source_id, target_id, weight, updated_at FROM engram_associations",
    ).fetchall()

    for row in rows:
        hours_since = (now - row["updated_at"]) / 3600.0
        if hours_since < 0.01:  # skip if updated less than ~36 seconds ago
            continue

        new_weight = hebbian_decay(row["weight"], hours_since, half_life_hours)

        if new_weight < threshold:
            conn.execute(
                "DELETE FROM engram_associations WHERE source_id = ? AND target_id = ?",
                (row["source_id"], row["target_id"]),
            )
            report.associations_pruned += 1
        else:
            conn.execute(
                """UPDATE engram_associations
                   SET weight = ?, updated_at = ?
                   WHERE source_id = ? AND target_id = ?""",
                (new_weight, now, row["source_id"], row["target_id"]),
            )
            report.associations_decayed += 1

    conn.commit()


# ------------------------------------------------------------------
# Activation log pruning
# ------------------------------------------------------------------


def _prune_activation_log(
    conn: sqlite3.Connection,
    limit: int,
    report: CognitiveMaintenanceReport,
) -> None:
    """Keep only the most recent *limit* activation entries."""
    total = conn.execute("SELECT COUNT(*) FROM activation_log").fetchone()[0]
    if total <= limit:
        return

    excess = total - limit
    conn.execute(
        """DELETE FROM activation_log
           WHERE log_id IN (
               SELECT log_id FROM activation_log
               ORDER BY activated_at ASC
               LIMIT ?
           )""",
        (excess,),
    )
    conn.commit()
    report.activation_entries_pruned = excess


# ------------------------------------------------------------------
# Source linking
# ------------------------------------------------------------------


def _link_semantic_sources(
    conn: sqlite3.Connection,
    now: float,
    report: CognitiveMaintenanceReport,
) -> None:
    """Create associations between semantic engrams and their episodic sources.

    Looks for semantic engrams whose metadata contains
    ``source_episodic_keys`` or ``promoted_from`` = ``"stm"`` with a
    matching episodic key, and creates Hebbian links if none exist.
    """
    rows = conn.execute(
        """SELECT engram_id, key, metadata FROM engrams
           WHERE tier = 'semantic' AND state = 'active'
             AND metadata LIKE '%source_episodic_keys%'""",
    ).fetchall()

    import json

    for row in rows:
        try:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
        except (json.JSONDecodeError, TypeError):
            continue

        source_keys = meta.get("source_episodic_keys", [])
        if not source_keys:
            continue

        semantic_id = row["engram_id"]
        for src_key in source_keys:
            ep_row = conn.execute(
                """SELECT engram_id FROM engrams
                   WHERE key = ? AND tier = 'episodic'
                     AND state = 'active'
                   LIMIT 1""",
                (src_key,),
            ).fetchone()
            if ep_row is None:
                continue

            episodic_id = ep_row["engram_id"]
            # Only create if no link exists
            existing = conn.execute(
                """SELECT 1 FROM engram_associations
                   WHERE source_id = ? AND target_id = ?""",
                (semantic_id, episodic_id),
            ).fetchone()
            if existing:
                continue

            # Bidirectional link
            for src, tgt in (
                (semantic_id, episodic_id),
                (episodic_id, semantic_id),
            ):
                conn.execute(
                    """INSERT OR IGNORE INTO engram_associations
                       (source_id, target_id, weight, co_activation_count,
                        created_at, updated_at)
                       VALUES (?, ?, 0.3, 1, ?, ?)""",
                    (src, tgt, now, now),
                )
            report.source_links_created += 1

    conn.commit()


# ------------------------------------------------------------------
# Soft-delete purge
# ------------------------------------------------------------------


def _purge_soft_deleted(
    conn: sqlite3.Connection,
    now: float,
    retention_days: float,
    report: CognitiveMaintenanceReport,
) -> None:
    """Permanently delete soft-deleted engrams older than retention window."""
    cutoff = now - (retention_days * 86400.0)
    cursor = conn.execute(
        "DELETE FROM engrams WHERE state = 'soft_deleted' AND updated_at < ?",
        (cutoff,),
    )
    conn.commit()
    report.soft_deleted_purged = cursor.rowcount


# ------------------------------------------------------------------
# Expired STM cleanup
# ------------------------------------------------------------------


def _clean_expired_stm(
    conn: sqlite3.Connection,
    now: float,
    report: CognitiveMaintenanceReport,
) -> None:
    """Remove STM entries whose TTL has expired."""
    cursor = conn.execute(
        """DELETE FROM engrams
           WHERE tier = 'stm'
             AND ttl_seconds IS NOT NULL
             AND ttl_seconds > 0
             AND (? - created_at) > ttl_seconds""",
        (now,),
    )
    conn.commit()
    report.expired_stm_cleaned = cursor.rowcount
