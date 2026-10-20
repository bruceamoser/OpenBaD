"""Rich execution trace and reward result models for Phase 9.

These models capture a superset of the fields required for reward evaluation,
including retries, verification, budget consumption, and hormone context.
They are serialization-only (no side-effects) and serve as the typed boundary
between the executor and any downstream reward / logging pipeline.

Distinct from :mod:`openbad.tasks.reward_models` (which drives the template
evaluator): these models are optimised for full-fidelity persistence and API
responses.
"""

from __future__ import annotations

import dataclasses
from typing import Any

# ---------------------------------------------------------------------------
# Field validation helpers
# ---------------------------------------------------------------------------

_REQUIRED_TRACE_FIELDS = {
    "run_id",
    "task_id",
    "node_id",
    "retries",
    "verified",
    "budget_consumed",
    "outcome",
}

_REQUIRED_RESULT_FIELDS = {
    "run_id",
    "score",
    "rationale",
}


def _require(d: dict[str, Any], required: set[str], label: str) -> None:
    missing = required - d.keys()
    if missing:
        raise ValueError(f"{label}: missing required fields: {sorted(missing)}")


# ---------------------------------------------------------------------------
# ExecutionTrace
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ExecutionTrace:
    """A complete record of a single task/node execution.

    Parameters
    ----------
    run_id:
        Identifier for the execution run.
    task_id:
        Parent task identifier.
    node_id:
        DAG node identifier.
    outcome:
        Final execution outcome string (e.g. ``"success"``, ``"failure"``).
    retries:
        Number of retry attempts made (≥ 0).
    verified:
        Whether the result was externally verified/confirmed.
    budget_consumed:
        Tokens or cost units consumed (≥ 0.0).
    hormone_context:
        Snapshot of hormone levels at execution time.  Keys are hormone
        names; values are floats.
    metadata:
        Arbitrary additional key-value context for logging / downstream use.
    """

    run_id: str
    task_id: str
    node_id: str
    outcome: str
    retries: int
    verified: bool
    budget_consumed: float
    hormone_context: dict[str, float] = dataclasses.field(default_factory=dict)
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.retries < 0:
            raise ValueError("retries must be non-negative")
        if self.budget_consumed < 0.0:
            raise ValueError("budget_consumed must be non-negative")
        if not self.run_id:
            raise ValueError("run_id must not be empty")
        if not self.task_id:
            raise ValueError("task_id must not be empty")
        if not self.node_id:
            raise ValueError("node_id must not be empty")
        if not self.outcome:
            raise ValueError("outcome must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "node_id": self.node_id,
            "outcome": self.outcome,
            "retries": self.retries,
            "verified": self.verified,
            "budget_consumed": self.budget_consumed,
            "hormone_context": dict(self.hormone_context),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExecutionTrace:
        _require(d, _REQUIRED_TRACE_FIELDS, "ExecutionTrace")
        return cls(
            run_id=str(d["run_id"]),
            task_id=str(d["task_id"]),
            node_id=str(d["node_id"]),
            outcome=str(d["outcome"]),
            retries=int(d["retries"]),
            verified=bool(d["verified"]),
            budget_consumed=float(d["budget_consumed"]),
            hormone_context=dict(d.get("hormone_context") or {}),
            metadata=dict(d.get("metadata") or {}),
        )


# ---------------------------------------------------------------------------
# RewardResult
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class RewardResult:
    """The output of a reward evaluation, ready for persistence or API response.

    Parameters
    ----------
    run_id:
        The run that was evaluated.
    score:
        Normalised reward score in ``[-1.0, 1.0]``.
    rationale:
        Human/machine-readable explanation of the score.
    template_id:
        Optional identifier of the scoring template used.
    metadata:
        Arbitrary additional context.
    """

    run_id: str
    score: float
    rationale: str
    template_id: str = ""
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("run_id must not be empty")
        if not -1.0 <= self.score <= 1.0:
            raise ValueError(f"score must be in [-1.0, 1.0], got {self.score}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "score": self.score,
            "rationale": self.rationale,
            "template_id": self.template_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RewardResult:
        _require(d, _REQUIRED_RESULT_FIELDS, "RewardResult")
        return cls(
            run_id=str(d["run_id"]),
            score=float(d["score"]),
            rationale=str(d["rationale"]),
            template_id=str(d.get("template_id", "")),
            metadata=dict(d.get("metadata") or {}),
        )
