"""Threshold-based policy evaluation and cortisol event publisher.

Loads configurable thresholds from YAML, evaluates incoming telemetry
values, and publishes :class:`EndocrineEvent` (cortisol) messages when
a threshold is breached.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from openbad.nervous_system.schemas.common_pb2 import Header
from openbad.nervous_system.schemas.endocrine_pb2 import EndocrineEvent

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = (
    Path(__file__).resolve().parent.parent.parent.parent / "config" / "threshold_policies.yaml"
)

# Proto severity enum values
_SEVERITY_WARNING = 2
_SEVERITY_CRITICAL = 3


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThresholdSpec:
    """Warning and critical bounds for a single metric."""

    metric: str
    warning: float
    critical: float


@dataclass(frozen=True)
class Breach:
    """Describes a threshold breach."""

    metric: str
    value: float
    threshold: float
    severity: int  # 2=WARNING, 3=CRITICAL


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_thresholds(path: str | Path | None = None) -> list[ThresholdSpec]:
    """Load threshold specs from a YAML file.

    Parameters
    ----------
    path:
        Path to the YAML config.  Defaults to ``config/threshold_policies.yaml``.
    """
    path = Path(path) if path else _DEFAULT_CONFIG
    with open(path) as f:  # noqa: PTH123
        data = yaml.safe_load(f)

    specs: list[ThresholdSpec] = []
    for metric, bounds in data.get("thresholds", {}).items():
        specs.append(
            ThresholdSpec(
                metric=metric,
                warning=float(bounds["warning"]),
                critical=float(bounds["critical"]),
            )
        )
    return specs


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _is_upper_bound(metric: str) -> bool:
    """Return True for metrics where exceeding the threshold is bad.

    ``token_budget_remaining_pct`` is inverted: *lower* is worse.
    """
    return metric != "token_budget_remaining_pct"


def evaluate(
    specs: list[ThresholdSpec],
    values: dict[str, float],
) -> list[Breach]:
    """Evaluate metric *values* against *specs* and return any breaches.

    For most metrics, a value **above** the threshold triggers.
    For ``token_budget_remaining_pct`` a value **below** triggers.
    """
    breaches: list[Breach] = []
    for spec in specs:
        val = values.get(spec.metric)
        if val is None:
            continue

        if _is_upper_bound(spec.metric):
            if val >= spec.critical:
                breaches.append(Breach(spec.metric, val, spec.critical, _SEVERITY_CRITICAL))
            elif val >= spec.warning:
                breaches.append(Breach(spec.metric, val, spec.warning, _SEVERITY_WARNING))
        else:
            # Inverted metric (lower is worse)
            if val <= spec.critical:
                breaches.append(Breach(spec.metric, val, spec.critical, _SEVERITY_CRITICAL))
            elif val <= spec.warning:
                breaches.append(Breach(spec.metric, val, spec.warning, _SEVERITY_WARNING))

    return breaches


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------

_RECOMMENDED_ACTIONS: dict[str, dict[int, str]] = {
    "cpu_percent": {
        _SEVERITY_WARNING: "Consider deferring non-critical tasks",
        _SEVERITY_CRITICAL: "Throttle all non-essential workloads immediately",
    },
    "memory_percent": {
        _SEVERITY_WARNING: "Release caches or defer memory-heavy tasks",
        _SEVERITY_CRITICAL: "Trigger emergency memory reclamation",
    },
    "token_budget_remaining_pct": {
        _SEVERITY_WARNING: "Switch to smaller model tier",
        _SEVERITY_CRITICAL: "Halt all LLM calls until budget resets",
    },
}


def breach_to_proto(breach: Breach) -> EndocrineEvent:
    """Convert a :class:`Breach` into a cortisol :class:`EndocrineEvent`."""
    action = _RECOMMENDED_ACTIONS.get(breach.metric, {}).get(
        breach.severity, "Investigate metric breach"
    )
    return EndocrineEvent(
        header=Header(timestamp_unix=time.time()),
        hormone="cortisol",
        level=1.0 if breach.severity == _SEVERITY_CRITICAL else 0.6,
        severity=breach.severity,
        metric_name=breach.metric,
        metric_value=breach.value,
        recommended_action=action,
    )


def publish_breaches(
    client: object,
    breaches: list[Breach],
) -> int:
    """Publish cortisol events for each breach. Returns count published."""
    count = 0
    for breach in breaches:
        msg = breach_to_proto(breach)
        client.publish("agent/endocrine/cortisol", msg.SerializeToString())  # type: ignore[union-attr]
        logger.info(
            "Cortisol event: %s=%.2f (severity=%d)",
            breach.metric,
            breach.value,
            breach.severity,
        )
        count += 1
    return count
