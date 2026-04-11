from __future__ import annotations

import pytest

from openbad.tasks.rewards import ExecutionTrace, RewardResult

# ---------------------------------------------------------------------------
# ExecutionTrace validation
# ---------------------------------------------------------------------------


def make_trace(**overrides) -> ExecutionTrace:
    defaults = dict(
        run_id="run-1",
        task_id="task-1",
        node_id="node-1",
        outcome="success",
        retries=0,
        verified=True,
        budget_consumed=10.5,
    )
    defaults.update(overrides)
    return ExecutionTrace(**defaults)


def test_trace_valid_construction() -> None:
    t = make_trace()
    assert t.run_id == "run-1"
    assert t.verified is True
    assert t.budget_consumed == 10.5


def test_trace_rejects_negative_retries() -> None:
    with pytest.raises(ValueError, match="retries"):
        make_trace(retries=-1)


def test_trace_rejects_negative_budget() -> None:
    with pytest.raises(ValueError, match="budget_consumed"):
        make_trace(budget_consumed=-0.01)


def test_trace_rejects_empty_run_id() -> None:
    with pytest.raises(ValueError, match="run_id"):
        make_trace(run_id="")


def test_trace_rejects_empty_outcome() -> None:
    with pytest.raises(ValueError, match="outcome"):
        make_trace(outcome="")


def test_trace_with_hormone_context() -> None:
    t = make_trace(hormone_context={"dopamine": 0.4, "cortisol": 0.1})
    assert t.hormone_context["dopamine"] == 0.4


# ---------------------------------------------------------------------------
# ExecutionTrace serialization
# ---------------------------------------------------------------------------


def test_trace_to_dict_round_trip() -> None:
    original = make_trace(
        retries=2,
        verified=False,
        budget_consumed=5.0,
        hormone_context={"dopamine": 0.3},
        metadata={"actor": "scheduler"},
    )
    d = original.to_dict()
    restored = ExecutionTrace.from_dict(d)

    assert restored.run_id == original.run_id
    assert restored.retries == original.retries
    assert restored.verified is False
    assert restored.budget_consumed == original.budget_consumed
    assert restored.hormone_context == {"dopamine": 0.3}
    assert restored.metadata == {"actor": "scheduler"}


def test_trace_from_dict_missing_field_raises() -> None:
    d = {"run_id": "r1", "task_id": "t1"}  # missing many fields
    with pytest.raises(ValueError, match="missing required fields"):
        ExecutionTrace.from_dict(d)


# ---------------------------------------------------------------------------
# RewardResult validation
# ---------------------------------------------------------------------------


def make_result(**overrides) -> RewardResult:
    defaults = dict(run_id="run-1", score=0.75, rationale="good work")
    defaults.update(overrides)
    return RewardResult(**defaults)


def test_result_valid_construction() -> None:
    r = make_result()
    assert r.score == 0.75
    assert r.rationale == "good work"


def test_result_rejects_empty_run_id() -> None:
    with pytest.raises(ValueError, match="run_id"):
        make_result(run_id="")


def test_result_rejects_score_above_one() -> None:
    with pytest.raises(ValueError, match="score"):
        make_result(score=1.001)


def test_result_rejects_score_below_neg_one() -> None:
    with pytest.raises(ValueError, match="score"):
        make_result(score=-1.001)


def test_result_score_boundary_values() -> None:
    make_result(score=1.0)
    make_result(score=-1.0)
    make_result(score=0.0)


# ---------------------------------------------------------------------------
# RewardResult serialization
# ---------------------------------------------------------------------------


def test_result_to_dict_round_trip() -> None:
    original = make_result(score=-0.5, template_id="tmpl.default", metadata={"k": "v"})
    d = original.to_dict()
    restored = RewardResult.from_dict(d)

    assert restored.run_id == original.run_id
    assert restored.score == original.score
    assert restored.template_id == "tmpl.default"
    assert restored.metadata == {"k": "v"}


def test_result_from_dict_missing_field_raises() -> None:
    with pytest.raises(ValueError, match="missing required fields"):
        RewardResult.from_dict({"run_id": "r1"})
