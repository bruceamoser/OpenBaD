from __future__ import annotations

import pytest

from openbad.tasks.reward_models import (
    RewardResult,
    RewardTrace,
    TraceOutcome,
)

# ---------------------------------------------------------------------------
# RewardTrace — validation
# ---------------------------------------------------------------------------


def test_trace_created_with_required_fields() -> None:
    trace = RewardTrace(
        node_id="n1",
        task_id="t1",
        outcome=TraceOutcome.SUCCESS,
        duration_ms=200,
        retry_count=0,
    )
    assert trace.node_id == "n1"
    assert trace.outcome == TraceOutcome.SUCCESS


def test_trace_negative_duration_raises() -> None:
    with pytest.raises(ValueError, match="duration_ms"):
        RewardTrace(
            node_id="n1",
            task_id="t1",
            outcome=TraceOutcome.SUCCESS,
            duration_ms=-1,
            retry_count=0,
        )


def test_trace_negative_retry_count_raises() -> None:
    with pytest.raises(ValueError, match="retry_count"):
        RewardTrace(
            node_id="n1",
            task_id="t1",
            outcome=TraceOutcome.FAILURE,
            duration_ms=0,
            retry_count=-1,
        )


def test_trace_from_dict_missing_field_raises() -> None:
    with pytest.raises(ValueError, match="Missing required fields"):
        RewardTrace.from_dict({"node_id": "n1", "task_id": "t1"})


def test_trace_from_dict_invalid_outcome_raises() -> None:
    with pytest.raises(ValueError):
        RewardTrace.from_dict(
            {
                "node_id": "n1",
                "task_id": "t1",
                "outcome": "NOT_VALID",
                "duration_ms": 100,
                "retry_count": 0,
            }
        )


# ---------------------------------------------------------------------------
# RewardTrace — serialization round-trip
# ---------------------------------------------------------------------------


def test_trace_round_trip() -> None:
    trace = RewardTrace(
        node_id="n1",
        task_id="t1",
        outcome=TraceOutcome.SUCCESS,
        duration_ms=500,
        retry_count=2,
        context={"model": "gpt-4"},
    )
    loaded = RewardTrace.from_dict(trace.to_dict())

    assert loaded.node_id == trace.node_id
    assert loaded.outcome == trace.outcome
    assert loaded.duration_ms == trace.duration_ms
    assert loaded.retry_count == trace.retry_count
    assert loaded.context == trace.context


def test_trace_to_dict_contains_required_keys() -> None:
    trace = RewardTrace(
        node_id="n1",
        task_id="t1",
        outcome=TraceOutcome.TIMEOUT,
        duration_ms=0,
        retry_count=1,
    )
    d = trace.to_dict()
    for key in ("node_id", "task_id", "outcome", "duration_ms", "retry_count"):
        assert key in d


# ---------------------------------------------------------------------------
# RewardResult — validation
# ---------------------------------------------------------------------------


def test_result_score_out_of_range_high_raises() -> None:
    with pytest.raises(ValueError, match="score"):
        RewardResult(trace_node_id="n1", score=1.5, template_id="tmpl")


def test_result_score_out_of_range_low_raises() -> None:
    with pytest.raises(ValueError, match="score"):
        RewardResult(trace_node_id="n1", score=-2.0, template_id="tmpl")


def test_result_score_boundary_valid() -> None:
    hi = RewardResult(trace_node_id="n1", score=1.0, template_id="tmpl")
    lo = RewardResult(trace_node_id="n1", score=-1.0, template_id="tmpl")
    assert hi.score == 1.0
    assert lo.score == -1.0


def test_result_from_dict_missing_field_raises() -> None:
    with pytest.raises(ValueError, match="Missing required fields"):
        RewardResult.from_dict({"trace_node_id": "n1"})


# ---------------------------------------------------------------------------
# RewardResult — serialization round-trip
# ---------------------------------------------------------------------------


def test_result_round_trip() -> None:
    result = RewardResult(
        trace_node_id="n1",
        score=0.75,
        template_id="success_template",
        rationale="All steps completed",
        metadata={"version": "1.0"},
    )
    loaded = RewardResult.from_dict(result.to_dict())

    assert loaded.trace_node_id == result.trace_node_id
    assert loaded.score == result.score
    assert loaded.template_id == result.template_id
    assert loaded.rationale == result.rationale
    assert loaded.metadata == result.metadata


def test_result_to_dict_contains_required_keys() -> None:
    result = RewardResult(trace_node_id="n1", score=0.0, template_id="t")
    d = result.to_dict()
    for key in ("trace_node_id", "score", "template_id", "rationale", "metadata"):
        assert key in d
