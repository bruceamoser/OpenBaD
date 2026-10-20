"""Reward trace and result models for Phase 9.

These types form the input/output contract for the reward template evaluator
(#360).  All models are frozen dataclasses with explicit validation and full
``to_dict`` / ``from_dict`` round-trip support.

:class:`RewardTrace` captures the execution history of a task node: its
outcome, duration, retry count, and optional structured context.
:class:`RewardResult` is the scalar output of an evaluator operating on a
trace.
"""

from __future__ import annotations

import dataclasses
from enum import auto
from typing import Any

from openbad.tasks.models import StrEnum

# ---------------------------------------------------------------------------
# Outcome taxonomy
# ---------------------------------------------------------------------------


class TraceOutcome(StrEnum):
    """High-level outcome of a traced node execution."""

    SUCCESS = auto()
    FAILURE = auto()
    TIMEOUT = auto()
    CANCELLED = auto()


# ---------------------------------------------------------------------------
# RewardTrace
# ---------------------------------------------------------------------------


_REQUIRED_TRACE_FIELDS = frozenset(
    {"node_id", "task_id", "outcome", "duration_ms", "retry_count"}
)


@dataclasses.dataclass(frozen=True)
class RewardTrace:
    """Immutable execution trace for a single task node.

    Parameters
    ----------
    node_id:
        The node that was executed.
    task_id:
        The parent task.
    outcome:
        The execution outcome.
    duration_ms:
        Elapsed wall-clock milliseconds for the execution.
    retry_count:
        How many times this node was retried before the current trace.
    context:
        Optional additional context (e.g., model name, capability ID).
    """

    node_id: str
    task_id: str
    outcome: TraceOutcome
    duration_ms: int
    retry_count: int
    context: dict[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")
        if self.retry_count < 0:
            raise ValueError("retry_count must be non-negative")

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "task_id": self.task_id,
            "outcome": self.outcome.value,
            "duration_ms": self.duration_ms,
            "retry_count": self.retry_count,
            "context": dict(self.context),
        }

    @classmethod
    def from_dict(cls, data: dict) -> RewardTrace:
        """Deserialize a :class:`RewardTrace` from *data*.

        Raises
        ------
        ValueError
            If any required field is missing.
        """
        missing = _REQUIRED_TRACE_FIELDS - data.keys()
        if missing:
            raise ValueError(f"Missing required fields: {sorted(missing)}")
        return cls(
            node_id=data["node_id"],
            task_id=data["task_id"],
            outcome=TraceOutcome(data["outcome"]),
            duration_ms=int(data["duration_ms"]),
            retry_count=int(data["retry_count"]),
            context=dict(data.get("context") or {}),
        )


# ---------------------------------------------------------------------------
# RewardResult
# ---------------------------------------------------------------------------


_REQUIRED_RESULT_FIELDS = frozenset({"trace_node_id", "score", "template_id"})


@dataclasses.dataclass(frozen=True)
class RewardResult:
    """Scalar reward output produced by the evaluator for a single trace.

    Parameters
    ----------
    trace_node_id:
        The node ID from the originating :class:`RewardTrace`.
    score:
        Reward score in the range ``[-1.0, 1.0]``.  Positive values signal a
        good outcome; negative values signal a bad outcome.
    template_id:
        Identifier of the template that produced this result.
    rationale:
        Optional human-readable explanation of the score.
    metadata:
        Optional structured metadata.
    """

    trace_node_id: str
    score: float
    template_id: str
    rationale: str = ""
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (-1.0 <= self.score <= 1.0):
            raise ValueError(f"score must be in [-1.0, 1.0], got {self.score}")

    def to_dict(self) -> dict:
        return {
            "trace_node_id": self.trace_node_id,
            "score": self.score,
            "template_id": self.template_id,
            "rationale": self.rationale,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> RewardResult:
        """Deserialize a :class:`RewardResult` from *data*.

        Raises
        ------
        ValueError
            If any required field is missing or *score* is out of range.
        """
        missing = _REQUIRED_RESULT_FIELDS - data.keys()
        if missing:
            raise ValueError(f"Missing required fields: {sorted(missing)}")
        return cls(
            trace_node_id=data["trace_node_id"],
            score=float(data["score"]),
            template_id=data["template_id"],
            rationale=data.get("rationale", ""),
            metadata=dict(data.get("metadata") or {}),
        )
