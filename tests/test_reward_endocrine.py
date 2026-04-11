from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openbad.endocrine.controller import EndocrineController
from openbad.tasks.reward_endocrine import (
    RewardEndocrineBridge,
    RewardRecord,
    initialize_reward_db,
)
from openbad.tasks.reward_models import RewardResult, RewardTrace, TraceOutcome

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_trace(outcome: TraceOutcome = TraceOutcome.SUCCESS) -> RewardTrace:
    return RewardTrace(
        node_id="n1",
        task_id="t1",
        outcome=outcome,
        duration_ms=100,
        retry_count=0,
    )


def make_result(score: float = 0.8, template_id: str = "success.default") -> RewardResult:
    return RewardResult(
        trace_node_id="n1",
        score=score,
        template_id=template_id,
        rationale="test",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "reward.db")
    initialize_reward_db(conn)
    return conn


@pytest.fixture()
def controller() -> EndocrineController:
    return EndocrineController()


@pytest.fixture()
def bridge(
    db: sqlite3.Connection, controller: EndocrineController
) -> RewardEndocrineBridge:
    return RewardEndocrineBridge(db, controller)


# ---------------------------------------------------------------------------
# Hormone mapping
# ---------------------------------------------------------------------------


def test_success_triggers_dopamine(
    bridge: RewardEndocrineBridge, controller: EndocrineController
) -> None:
    before = controller.level("dopamine")
    bridge.apply(make_trace(TraceOutcome.SUCCESS), make_result())
    assert controller.level("dopamine") > before


def test_failure_triggers_cortisol(
    bridge: RewardEndocrineBridge, controller: EndocrineController
) -> None:
    before = controller.level("cortisol")
    bridge.apply(make_trace(TraceOutcome.FAILURE), make_result(score=-0.5))
    assert controller.level("cortisol") > before


def test_timeout_triggers_adrenaline(
    bridge: RewardEndocrineBridge, controller: EndocrineController
) -> None:
    before = controller.level("adrenaline")
    bridge.apply(make_trace(TraceOutcome.TIMEOUT), make_result(score=-0.75))
    assert controller.level("adrenaline") > before


def test_cancelled_applies_no_hormone_changes(
    bridge: RewardEndocrineBridge, controller: EndocrineController
) -> None:
    state_before = controller.get_state().to_dict()
    bridge.apply(make_trace(TraceOutcome.CANCELLED), make_result(score=0.0))
    # Cancelled → empty mappings; state unchanged
    state_after = controller.get_state().to_dict()
    assert state_before == state_after


def test_endocrine_hook_error_does_not_propagate(db: sqlite3.Connection) -> None:
    """Endocrine triggers must not crash execution."""
    bad_controller = MagicMock(spec=EndocrineController)
    bad_controller.trigger.side_effect = RuntimeError("boom")

    bridge = RewardEndocrineBridge(db, bad_controller)
    # Should not raise
    bridge.apply(make_trace(TraceOutcome.SUCCESS), make_result())


# ---------------------------------------------------------------------------
# Reward record persistence
# ---------------------------------------------------------------------------


def test_apply_persists_record(bridge: RewardEndocrineBridge) -> None:
    record = bridge.apply(make_trace(), make_result())

    assert isinstance(record, RewardRecord)
    assert record.task_id == "t1"
    assert record.node_id == "n1"
    assert record.score == 0.8


def test_query_by_task_returns_records(bridge: RewardEndocrineBridge) -> None:
    bridge.apply(make_trace(), make_result(score=0.5))
    bridge.apply(make_trace(), make_result(score=0.9))

    records = bridge.query_by_task("t1")
    assert len(records) == 2


def test_query_by_node_returns_records(bridge: RewardEndocrineBridge) -> None:
    bridge.apply(make_trace(), make_result())
    records = bridge.query_by_node("n1")
    assert len(records) == 1
    assert records[0].node_id == "n1"


def test_query_by_task_excludes_other_tasks(
    db: sqlite3.Connection, controller: EndocrineController
) -> None:
    bridge = RewardEndocrineBridge(db, controller)
    other_trace = RewardTrace(
        node_id="n2",
        task_id="other-task",
        outcome=TraceOutcome.SUCCESS,
        duration_ms=50,
        retry_count=0,
    )
    bridge.apply(make_trace(), make_result())
    bridge.apply(other_trace, make_result())

    records = bridge.query_by_task("t1")
    assert all(r.task_id == "t1" for r in records)


def test_query_empty_task_returns_empty(bridge: RewardEndocrineBridge) -> None:
    assert bridge.query_by_task("no-such-task") == []
