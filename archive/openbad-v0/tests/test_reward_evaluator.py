from __future__ import annotations

import pytest

from openbad.tasks.reward_evaluator import (
    DEFAULT_TEMPLATES,
    RewardEvaluator,
    RewardTemplate,
)
from openbad.tasks.reward_models import RewardTrace, TraceOutcome

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def make_trace(
    outcome: TraceOutcome = TraceOutcome.SUCCESS,
    retry_count: int = 0,
    context: dict | None = None,
) -> RewardTrace:
    return RewardTrace(
        node_id="n1",
        task_id="t1",
        outcome=outcome,
        duration_ms=100,
        retry_count=retry_count,
        context=context or {},
    )


@pytest.fixture()
def evaluator() -> RewardEvaluator:
    return RewardEvaluator()


# ---------------------------------------------------------------------------
# Template outputs for representative traces
# ---------------------------------------------------------------------------


def test_success_trace_produces_positive_score(evaluator: RewardEvaluator) -> None:
    result = evaluator.evaluate(make_trace(outcome=TraceOutcome.SUCCESS))
    assert result.score > 0


def test_failure_trace_produces_negative_score(evaluator: RewardEvaluator) -> None:
    result = evaluator.evaluate(make_trace(outcome=TraceOutcome.FAILURE))
    assert result.score < 0


def test_timeout_trace_produces_negative_score(evaluator: RewardEvaluator) -> None:
    result = evaluator.evaluate(make_trace(outcome=TraceOutcome.TIMEOUT))
    assert result.score < 0


def test_cancelled_trace_produces_zero_score(evaluator: RewardEvaluator) -> None:
    result = evaluator.evaluate(make_trace(outcome=TraceOutcome.CANCELLED))
    assert result.score == 0.0


def test_result_template_id_set(evaluator: RewardEvaluator) -> None:
    result = evaluator.evaluate(make_trace(outcome=TraceOutcome.SUCCESS))
    assert result.template_id == "success.default"


def test_result_trace_node_id_matches(evaluator: RewardEvaluator) -> None:
    result = evaluator.evaluate(make_trace())
    assert result.trace_node_id == "n1"


# ---------------------------------------------------------------------------
# Retry penalty
# ---------------------------------------------------------------------------


def test_retry_reduces_score(evaluator: RewardEvaluator) -> None:
    no_retry = evaluator.evaluate(make_trace(outcome=TraceOutcome.SUCCESS, retry_count=0))
    with_retry = evaluator.evaluate(make_trace(outcome=TraceOutcome.SUCCESS, retry_count=3))
    assert with_retry.score < no_retry.score


def test_score_clamped_at_negative_one(evaluator: RewardEvaluator) -> None:
    result = evaluator.evaluate(make_trace(outcome=TraceOutcome.FAILURE, retry_count=100))
    assert result.score >= -1.0


# ---------------------------------------------------------------------------
# Deterministic repeat evaluation
# ---------------------------------------------------------------------------


def test_same_trace_same_result(evaluator: RewardEvaluator) -> None:
    trace = make_trace(outcome=TraceOutcome.SUCCESS, retry_count=2)
    r1 = evaluator.evaluate(trace)
    r2 = evaluator.evaluate(trace)
    assert r1.score == r2.score
    assert r1.template_id == r2.template_id


def test_different_traces_different_results(evaluator: RewardEvaluator) -> None:
    success = evaluator.evaluate(make_trace(outcome=TraceOutcome.SUCCESS))
    failure = evaluator.evaluate(make_trace(outcome=TraceOutcome.FAILURE))
    assert success.score != failure.score


# ---------------------------------------------------------------------------
# Context-specific templates
# ---------------------------------------------------------------------------


def test_context_specific_template_wins_over_default() -> None:
    specific = RewardTemplate(
        template_id="success.with_heavy",
        base_score=0.5,
        outcome=TraceOutcome.SUCCESS,
        context_key="heavy_model",
    )
    ev = RewardEvaluator(templates=[specific] + list(DEFAULT_TEMPLATES))

    result = ev.evaluate(make_trace(context={"heavy_model": True}))
    assert result.template_id == "success.with_heavy"


def test_context_key_missing_falls_back_to_outcome_template(evaluator: RewardEvaluator) -> None:
    result = evaluator.evaluate(make_trace(outcome=TraceOutcome.SUCCESS, context={}))
    assert result.template_id == "success.default"


# ---------------------------------------------------------------------------
# No-match fallback
# ---------------------------------------------------------------------------


def test_no_templates_returns_no_match_result() -> None:
    ev = RewardEvaluator(templates=[])
    result = ev.evaluate(make_trace())
    assert result.template_id == "no_match"
    assert result.score == 0.0
