"""Atomic lease acquisition, release, and renewal API backed by SQLite."""

from __future__ import annotations

import dataclasses
import sqlite3
import time
import uuid


@dataclasses.dataclass(frozen=True)
class Lease:
    """An acquired resource lease."""

    lease_id: str
    owner_id: str
    resource_type: str
    resource_id: str
    leased_at: float
    expires_at: float

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at


class LeaseError(Exception):
    """Raised when a lease operation is denied."""


class LeaseStore:
    """Atomic lease acquisition and lifecycle management.

    Uses ``BEGIN EXCLUSIVE`` transactions so that acquire is single-winner
    even under SQLite WAL concurrency.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def acquire(
        self,
        resource_type: str,
        resource_id: str,
        owner_id: str,
        ttl_seconds: float,
    ) -> Lease | None:
        """Atomically try to acquire a lease.

        Returns the :class:`Lease` on success, or ``None`` if the resource is
        already held by an active (non-expired) lease.
        """
        now = time.time()
        expires_at = now + ttl_seconds

        # BEGIN EXCLUSIVE serialises concurrent writers; only one wins the race.
        self._conn.execute("BEGIN EXCLUSIVE")
        try:
            active = self._conn.execute(
                """
                SELECT lease_id FROM task_leases
                WHERE resource_type = ? AND resource_id = ? AND expires_at > ?
                """,
                (resource_type, resource_id, now),
            ).fetchone()

            if active:
                self._conn.execute("ROLLBACK")
                return None

            lease_id = str(uuid.uuid4())
            self._conn.execute(
                """
                INSERT INTO task_leases
                    (lease_id, owner_id, resource_type, resource_id,
                     leased_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (lease_id, owner_id, resource_type, resource_id, now, expires_at),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

        return Lease(
            lease_id=lease_id,
            owner_id=owner_id,
            resource_type=resource_type,
            resource_id=resource_id,
            leased_at=now,
            expires_at=expires_at,
        )

    def release(self, lease_id: str, owner_id: str) -> bool:
        """Release the lease identified by *lease_id*.

        Returns ``True`` if deleted.  Raises :class:`LeaseError` if the caller
        is not the owner of an active lease.
        """
        row = self._conn.execute(
            "SELECT owner_id FROM task_leases WHERE lease_id = ?", (lease_id,)
        ).fetchone()

        if row is None:
            return False

        if row["owner_id"] != owner_id:
            raise LeaseError(
                f"Lease {lease_id!r} is owned by {row['owner_id']!r},"
                f" not {owner_id!r}"
            )

        self._conn.execute(
            "DELETE FROM task_leases WHERE lease_id = ?", (lease_id,)
        )
        self._conn.commit()
        return True

    def renew(
        self,
        lease_id: str,
        owner_id: str,
        ttl_seconds: float,
    ) -> Lease:
        """Extend an active lease by *ttl_seconds* from now.

        Raises :class:`LeaseError` if the lease does not exist, is expired,
        or is owned by a different caller.
        """
        now = time.time()
        row = self._conn.execute(
            "SELECT * FROM task_leases WHERE lease_id = ?", (lease_id,)
        ).fetchone()

        if row is None:
            raise LeaseError(f"Lease {lease_id!r} not found")
        if row["owner_id"] != owner_id:
            raise LeaseError(
                f"Lease {lease_id!r} is owned by {row['owner_id']!r},"
                f" not {owner_id!r}"
            )
        if row["expires_at"] <= now:
            raise LeaseError(f"Lease {lease_id!r} has already expired")

        new_expires = now + ttl_seconds
        self._conn.execute(
            "UPDATE task_leases SET expires_at = ? WHERE lease_id = ?",
            (new_expires, lease_id),
        )
        self._conn.commit()

        return Lease(
            lease_id=lease_id,
            owner_id=owner_id,
            resource_type=row["resource_type"],
            resource_id=row["resource_id"],
            leased_at=row["leased_at"],
            expires_at=new_expires,
        )

    def get_active(self, resource_type: str, resource_id: str) -> Lease | None:
        """Return the active (non-expired) lease for *resource*, or ``None``."""
        now = time.time()
        row = self._conn.execute(
            """
            SELECT * FROM task_leases
            WHERE resource_type = ? AND resource_id = ? AND expires_at > ?
            """,
            (resource_type, resource_id, now),
        ).fetchone()

        if row is None:
            return None

        return Lease(
            lease_id=row["lease_id"],
            owner_id=row["owner_id"],
            resource_type=row["resource_type"],
            resource_id=row["resource_id"],
            leased_at=row["leased_at"],
            expires_at=row["expires_at"],
        )
