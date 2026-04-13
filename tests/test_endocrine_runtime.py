from __future__ import annotations

import time
from pathlib import Path

from openbad.autonomy.endocrine_runtime import EndocrineRuntime
from openbad.endocrine.config import EndocrineConfig


def _runtime(path: Path) -> EndocrineRuntime:
    cfg = EndocrineConfig()
    return EndocrineRuntime(config=cfg, db_path=path)


def test_apply_adjustment_records_source_contributions(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "endo.json")
    now = time.time()

    runtime.apply_adjustment(
        source="chat",
        reason="provider failed",
        deltas={"cortisol": 0.2, "adrenaline": 0.1},
        now=now,
    )
    runtime.apply_adjustment(
        source="tasks",
        reason="task completed",
        deltas={"dopamine": 0.15},
        now=now + 1,
    )

    totals = runtime.source_contributions(window_seconds=120, now=now + 2)
    assert totals["chat"]["cortisol"] > 0.19
    assert totals["chat"]["adrenaline"] > 0.09
    assert totals["tasks"]["dopamine"] > 0.14


def test_decay_to_reduces_levels(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "endo.json")
    now = time.time()

    runtime.apply_adjustment(
        source="chat",
        reason="stress",
        deltas={"cortisol": 1.0},
        now=now,
    )
    before = runtime.levels["cortisol"]
    runtime.decay_to(now + 900.0)
    after = runtime.levels["cortisol"]

    assert before > after
    assert after >= 0.0


def test_subsystem_gate_disable_then_enable(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "endo.json")
    now = time.time()

    runtime.disable_system("chat", reason="high cortisol", now=now, until=now + 300)
    gate = runtime.gate("chat")
    assert not gate.enabled
    assert gate.disabled_until is not None

    runtime.enable_system("chat", reason="recovered", now=now + 301)
    enabled = runtime.gate("chat")
    assert enabled.enabled
    assert enabled.disabled_until is None


def test_level_array_order_and_sqlite_persistence(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    runtime = _runtime(db_path)
    runtime.apply_adjustment(
        source="research",
        reason="finding",
        deltas={"dopamine": 0.2, "endorphin": 0.1},
    )
    arr = runtime.level_array()
    assert len(arr) == 4

    row = runtime._conn.execute(  # noqa: SLF001
        "SELECT dopamine, endorphin FROM endocrine_state WHERE id = 1"
    ).fetchone()
    assert row is not None
    assert float(row["dopamine"]) > 0.0
    assert float(row["endorphin"]) > 0.0


def test_has_any_activation_threshold(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "state.db")
    assert not runtime.has_any_activation()
    runtime.apply_adjustment(
        source="chat",
        reason="failure spike",
        deltas={"cortisol": 0.6},
    )
    assert runtime.has_any_activation()
