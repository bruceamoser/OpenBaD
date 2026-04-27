"""Self-contained endocrine runtime for autonomous regulation.

Uses the OpenBaD SQLite state database for persistent endocrine state,
source-tagged adjustments, doctor notes, and subsystem gate status.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

from openbad.endocrine.config import EndocrineConfig
from openbad.state.db import DEFAULT_STATE_DB_PATH, initialize_state_db

HORMONES: tuple[str, ...] = ("dopamine", "adrenaline", "cortisol", "endorphin")
SYSTEMS: tuple[str, ...] = ("chat", "tasks", "research")
_RETENTION_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class SubsystemGate:
    enabled: bool = True
    disabled_reason: str = ""
    disabled_until: float | None = None


class EndocrineRuntime:
    """Persistent endocrine runtime backed by SQLite."""

    def __init__(
        self,
        *,
        config: EndocrineConfig,
        db_path: str | Path | None = None,
        max_adjustment_history: int = 300,
    ) -> None:
        self._config = config
        self._max_adjustment_history = max_adjustment_history
        self._conn = initialize_state_db(db_path or _resolve_state_db_path())
        self._ensure_rows()

    @property
    def config(self) -> EndocrineConfig:
        return self._config

    @property
    def levels(self) -> dict[str, float]:
        row = self._conn.execute(
            "SELECT dopamine, adrenaline, cortisol, endorphin FROM endocrine_state WHERE id = 1"
        ).fetchone()
        if row is None:
            return {h: 0.0 for h in HORMONES}
        return {
            "dopamine": float(row["dopamine"]),
            "adrenaline": float(row["adrenaline"]),
            "cortisol": float(row["cortisol"]),
            "endorphin": float(row["endorphin"]),
        }

    @property
    def mood_tags(self) -> list[str]:
        row = self._conn.execute(
            "SELECT mood_tags_json FROM endocrine_state WHERE id = 1"
        ).fetchone()
        if row is None:
            return []
        try:
            tags = json.loads(str(row["mood_tags_json"]))
        except ValueError:
            return []
        if not isinstance(tags, list):
            return []
        return [str(tag) for tag in tags if str(tag).strip()]

    def level_array(self) -> list[float]:
        levels = self.levels
        return [float(levels.get(h, 0.0)) for h in HORMONES]

    def decay_to(self, now: float | None = None) -> None:
        ts_now = now if now is not None else time.time()
        row = self._conn.execute(
            "SELECT dopamine, adrenaline, cortisol, endorphin, last_decay_at "
            "FROM endocrine_state WHERE id = 1"
        ).fetchone()
        if row is None:
            return

        last_decay_at = float(row["last_decay_at"])
        dt = max(0.0, ts_now - last_decay_at)
        if dt <= 0.0:
            self._conn.execute(
                "UPDATE endocrine_state SET last_decay_at = ?, updated_at = ? WHERE id = 1",
                (ts_now, ts_now),
            )
            self._conn.commit()
            return

        levels = {
            "dopamine": float(row["dopamine"]),
            "adrenaline": float(row["adrenaline"]),
            "cortisol": float(row["cortisol"]),
            "endorphin": float(row["endorphin"]),
        }
        for hormone in HORMONES:
            hl = max(1e-3, float(getattr(self._config, hormone).half_life_seconds))
            value = levels[hormone] * math.pow(2.0, -dt / hl)
            if value < 1e-4:
                value = 0.0
            levels[hormone] = _clamp(value)

        self._conn.execute(
            """
            UPDATE endocrine_state
            SET dopamine = ?, adrenaline = ?, cortisol = ?, endorphin = ?,
                last_decay_at = ?, updated_at = ?
            WHERE id = 1
            """,
            (
                levels["dopamine"],
                levels["adrenaline"],
                levels["cortisol"],
                levels["endorphin"],
                ts_now,
                ts_now,
            ),
        )
        self._conn.commit()

    def apply_adjustment(
        self,
        *,
        source: str,
        reason: str,
        deltas: dict[str, float],
        now: float | None = None,
        doctor_revelation: bool = False,
    ) -> dict[str, float]:
        ts_now = now if now is not None else time.time()
        self.decay_to(ts_now)
        levels = self.levels

        normalized: dict[str, float] = {}
        for hormone in HORMONES:
            delta = float(deltas.get(hormone, 0.0))
            if delta == 0.0:
                continue
            levels[hormone] = _clamp(float(levels.get(hormone, 0.0)) + delta)
            normalized[hormone] = delta

        self._conn.execute(
            """
            UPDATE endocrine_state
            SET dopamine = ?, adrenaline = ?, cortisol = ?, endorphin = ?, updated_at = ?
            WHERE id = 1
            """,
            (
                levels["dopamine"],
                levels["adrenaline"],
                levels["cortisol"],
                levels["endorphin"],
                ts_now,
            ),
        )

        if normalized:
            self._conn.execute(
                """
                INSERT INTO endocrine_adjustments (
                    ts, source, reason, deltas_json, levels_json, doctor_revelation
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    ts_now,
                    source.strip() or "unknown",
                    reason.strip() or "unspecified",
                    json.dumps(normalized, sort_keys=True),
                    json.dumps(levels, sort_keys=True),
                    int(doctor_revelation),
                ),
            )

        self._prune_old(ts_now)
        self._conn.commit()
        return dict(levels)

    def source_contributions(
        self,
        *,
        window_seconds: int = 900,
        now: float | None = None,
    ) -> dict[str, dict[str, float]]:
        ts_now = now if now is not None else time.time()
        since = ts_now - float(window_seconds)
        rows = self._conn.execute(
            """
            SELECT source, deltas_json
            FROM endocrine_adjustments
            WHERE ts >= ?
            ORDER BY ts ASC
            """,
            (since,),
        ).fetchall()

        totals: dict[str, dict[str, float]] = {}
        for row in rows:
            source = str(row["source"])
            source_totals = totals.setdefault(source, {h: 0.0 for h in HORMONES})
            try:
                deltas = json.loads(str(row["deltas_json"]))
            except ValueError:
                deltas = {}
            if not isinstance(deltas, dict):
                continue
            for hormone in HORMONES:
                source_totals[hormone] += float(deltas.get(hormone, 0.0))
        return totals

    def current_severity(self) -> dict[str, int]:
        levels = self.levels
        severities: dict[str, int] = {}
        for hormone in HORMONES:
            cfg = getattr(self._config, hormone)
            level = float(levels.get(hormone, 0.0))
            severity = 1
            if level >= float(cfg.activation_threshold):
                severity = 2
            if cfg.escalation_threshold is not None and level >= float(cfg.escalation_threshold):
                severity = 3
            severities[hormone] = severity
        return severities

    def has_any_activation(self) -> bool:
        levels = self.levels
        for hormone in HORMONES:
            cfg = getattr(self._config, hormone)
            if float(levels.get(hormone, 0.0)) >= float(cfg.activation_threshold):
                return True
        return False

    def gate(self, system: str) -> SubsystemGate:
        row = self._conn.execute(
            """
            SELECT enabled, disabled_reason, disabled_until
            FROM endocrine_subsystems
            WHERE system_name = ?
            """,
            (system,),
        ).fetchone()
        if row is None:
            return SubsystemGate()
        enabled = bool(row["enabled"])
        disabled_until = _float_or_none(row["disabled_until"])
        # Auto-expire: if the gate is disabled but disabled_until has passed,
        # re-enable it so the system isn't stuck forever if follow-ups fail.
        if not enabled and disabled_until is not None and disabled_until <= time.time():
            self.enable_system(system, reason="Auto-expired: disabled_until elapsed")
            return SubsystemGate(enabled=True, disabled_reason="auto-expired")
        return SubsystemGate(
            enabled=enabled,
            disabled_reason=str(row["disabled_reason"] or ""),
            disabled_until=disabled_until,
        )

    def all_gates(self) -> dict[str, SubsystemGate]:
        return {system: self.gate(system) for system in SYSTEMS}

    def disable_system(
        self,
        system: str,
        *,
        reason: str,
        now: float | None = None,
        until: float | None = None,
    ) -> None:
        if system not in SYSTEMS:
            return
        ts_now = now if now is not None else time.time()
        self._conn.execute(
            """
            INSERT INTO endocrine_subsystems (
                system_name, enabled, disabled_reason, disabled_until, updated_at
            )
            VALUES (?, 0, ?, ?, ?)
            ON CONFLICT(system_name) DO UPDATE SET
                enabled = excluded.enabled,
                disabled_reason = excluded.disabled_reason,
                disabled_until = excluded.disabled_until,
                updated_at = excluded.updated_at
            """,
            (system, reason.strip() or "doctor recommendation", until, ts_now),
        )
        self._conn.commit()

    def enable_system(
        self,
        system: str,
        *,
        reason: str,
        now: float | None = None,
    ) -> None:
        if system not in SYSTEMS:
            return
        ts_now = now if now is not None else time.time()
        self._conn.execute(
            """
            INSERT INTO endocrine_subsystems (
                system_name, enabled, disabled_reason, disabled_until, updated_at
            )
            VALUES (?, 1, ?, NULL, ?)
            ON CONFLICT(system_name) DO UPDATE SET
                enabled = excluded.enabled,
                disabled_reason = excluded.disabled_reason,
                disabled_until = excluded.disabled_until,
                updated_at = excluded.updated_at
            """,
            (system, reason.strip() or "doctor follow-up", ts_now),
        )
        self._conn.commit()

    def set_mood_tags(self, tags: list[str], *, now: float | None = None) -> None:
        ts_now = now if now is not None else time.time()
        normalized = [tag.strip() for tag in tags if tag.strip()]
        self._conn.execute(
            "UPDATE endocrine_state SET mood_tags_json = ?, updated_at = ? WHERE id = 1",
            (json.dumps(normalized), ts_now),
        )
        self._conn.commit()

    def add_doctor_note(self, note: dict[str, object], *, now: float | None = None) -> None:
        ts_now = now if now is not None else time.time()
        source = str(note.get("source", "llm")).strip() or "llm"
        provider = str(note.get("provider", "")).strip()
        model = str(note.get("model", "")).strip()
        summary = str(note.get("summary", "")).strip()
        self._conn.execute(
            """
            INSERT INTO endocrine_doctor_notes (
                ts, source, provider, model, summary, raw_json, doctor_revelation
            ) VALUES (?, ?, ?, ?, ?, ?, 1)
            """,
            (
                ts_now,
                source,
                provider,
                model,
                summary,
                json.dumps(note, sort_keys=True),
            ),
        )
        self._prune_old(ts_now)
        self._conn.commit()

    def recent_adjustments(self, *, limit: int = 50) -> list[dict[str, object]]:
        """Return the most recent endocrine adjustment log entries."""
        rows = self._conn.execute(
            """
            SELECT adjustment_id, ts, source, reason, deltas_json, levels_json
            FROM endocrine_adjustments
            ORDER BY ts DESC
            LIMIT ?
            """,
            (max(1, min(limit, 500)),),
        ).fetchall()
        items: list[dict[str, object]] = []
        for row in rows:
            deltas: dict[str, float] = {}
            levels: dict[str, float] = {}
            try:
                deltas = json.loads(str(row["deltas_json"])) if row["deltas_json"] else {}
            except ValueError:
                pass
            try:
                levels = json.loads(str(row["levels_json"])) if row["levels_json"] else {}
            except ValueError:
                pass
            items.append({
                "id": int(row["adjustment_id"]),
                "ts": float(row["ts"]),
                "source": str(row["source"]),
                "reason": str(row["reason"]),
                "deltas": deltas,
                "levels": levels,
            })
        return items

    def recent_doctor_notes(self, *, limit: int = 20) -> list[dict[str, object]]:
        rows = self._conn.execute(
            """
            SELECT ts, source, provider, model, summary, raw_json
            FROM endocrine_doctor_notes
            ORDER BY ts DESC
            LIMIT ?
            """,
            (max(1, min(limit, 200)),),
        ).fetchall()
        notes: list[dict[str, object]] = []
        for row in rows:
            payload: dict[str, object] = {}
            try:
                raw = json.loads(str(row["raw_json"]))
                if isinstance(raw, dict):
                    payload = raw
            except ValueError:
                payload = {}
            notes.append(
                {
                    "ts": float(row["ts"]),
                    "source": str(row["source"]),
                    "provider": str(row["provider"]),
                    "model": str(row["model"]),
                    "summary": str(row["summary"]),
                    "doctor_revelation": True,
                    "payload": payload,
                }
            )
        return notes

    def snapshot(self) -> dict[str, object]:
        return {
            "levels": self.levels,
            "mood_tags": self.mood_tags,
            "subsystems": {
                name: {
                    "enabled": gate.enabled,
                    "disabled_reason": gate.disabled_reason,
                    "disabled_until": gate.disabled_until,
                }
                for name, gate in self.all_gates().items()
            },
            "severity": self.current_severity(),
            "doctor_notes": self.recent_doctor_notes(limit=10),
        }

    def _ensure_rows(self) -> None:
        now = time.time()
        self._conn.execute(
            """
            INSERT OR IGNORE INTO endocrine_state (
                id, dopamine, adrenaline, cortisol, endorphin,
                mood_tags_json, last_decay_at, updated_at
            ) VALUES (1, 0.0, 0.0, 0.0, 0.0, '[]', ?, ?)
            """,
            (now, now),
        )
        for system in SYSTEMS:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO endocrine_subsystems (
                    system_name, enabled, disabled_reason, disabled_until, updated_at
                ) VALUES (?, 1, '', NULL, ?)
                """,
                (system, now),
            )
        self._conn.commit()

    def _prune_old(self, now_ts: float) -> None:
        cutoff = now_ts - _RETENTION_SECONDS
        self._conn.execute("DELETE FROM endocrine_adjustments WHERE ts < ?", (cutoff,))
        self._conn.execute("DELETE FROM endocrine_doctor_notes WHERE ts < ?", (cutoff,))


def load_endocrine_config() -> EndocrineConfig:
    """Load endocrine config from OPENBAD_CONFIG_DIR/standard fallback paths."""
    config_dir = Path("/etc/openbad")
    home_dir = Path.home()
    path_candidates: list[Path] = []

    from os import environ

    configured = environ.get("OPENBAD_CONFIG_DIR", "").strip()
    if configured:
        path_candidates.append(Path(configured) / "endocrine.yaml")

    path_candidates.extend(
        [
            Path("/var/lib/openbad/endocrine.yaml"),
            config_dir / "endocrine.yaml",
            home_dir / ".config" / "openbad" / "endocrine.yaml",
            Path("config/endocrine.yaml"),
        ]
    )

    for path in path_candidates:
        if path.exists():
            return EndocrineConfig.from_yaml(path)

    return EndocrineConfig()


def _resolve_state_db_path() -> Path:
    from os import environ

    configured = environ.get("OPENBAD_STATE_DB", "").strip()
    if configured:
        return Path(configured)

    preferred = Path("/var/lib/openbad/data/state.db")
    if preferred.exists():
        return preferred

    return DEFAULT_STATE_DB_PATH


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (float, int)):
        return float(value)
    return None
