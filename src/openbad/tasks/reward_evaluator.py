"""Deterministic reward template evaluator for Phase 9.

Templates are pure functions: identical traces always produce identical results.
Each :class:`RewardTemplate` encodes a scoring rule for a specific combination
of outcome and context.  The :class:`RewardEvaluator` selects the most
specific matching template and applies it.

Template matching priority (most → least specific):
1. ``outcome`` AND ``context_key`` match
2. ``outcome`` only
3. wildcard (``outcome=None``)

If no template matches, a default score of ``0.0`` is returned.
"""

from __future__ import annotations

import dataclasses

from openbad.tasks.reward_models import RewardResult, RewardTrace, TraceOutcome

# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class RewardTemplate:
    """A scoring rule applied to a :class:`~openbad.tasks.reward_models.RewardTrace`.

    Parameters
    ----------
    template_id:
        Unique identifier for this template.
    base_score:
        Base reward score before adjustments.  Must be in ``[-1.0, 1.0]``.
    outcome:
        The :class:`~openbad.tasks.reward_models.TraceOutcome` this template
        applies to.  ``None`` means wildcard (match any outcome).
    context_key:
        If set, this template only matches when ``trace.context`` contains
        a truthy value for this key.  ``None`` means no context requirement.
    retry_penalty:
        Score reduction applied per retry.  Default ``0.05``.  Clamped so
        the final score never drops below ``-1.0``.
    """

    template_id: str
    base_score: float
    outcome: TraceOutcome | None = None
    context_key: str | None = None
    retry_penalty: float = 0.05

    def matches(self, trace: RewardTrace) -> bool:
        """Return ``True`` if this template applies to *trace*."""
        if self.outcome is not None and trace.outcome != self.outcome:
            return False
        return not (self.context_key is not None and not trace.context.get(self.context_key))

    def specificity(self) -> int:
        """Higher specificity templates are preferred.  Range ``[0, 2]``."""
        score = 0
        if self.outcome is not None:
            score += 1
        if self.context_key is not None:
            score += 1
        return score

    def evaluate(self, trace: RewardTrace) -> RewardResult:
        """Apply the template to *trace* and return a :class:`RewardResult`."""
        raw = self.base_score - (self.retry_penalty * trace.retry_count)
        clamped = max(-1.0, min(1.0, raw))
        return RewardResult(
            trace_node_id=trace.node_id,
            score=clamped,
            template_id=self.template_id,
            rationale=_build_rationale(trace, clamped, raw, self),
        )


def _build_rationale(
    trace: RewardTrace, final_score: float, raw_score: float, template: RewardTemplate
) -> str:
    parts = [
        f"template={template.template_id}",
        f"outcome={trace.outcome.value}",
        f"base_score={template.base_score}",
    ]
    if trace.retry_count:
        penalty = template.retry_penalty * trace.retry_count
        parts.append(
            f"retry_penalty={template.retry_penalty}x{trace.retry_count}={penalty:.3f}"
        )
    if final_score != raw_score:
        parts.append(f"clamped from {raw_score:.3f}")
    parts.append(f"final_score={final_score}")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Default templates
# ---------------------------------------------------------------------------

DEFAULT_TEMPLATES: list[RewardTemplate] = [
    RewardTemplate(
        template_id="success.default",
        base_score=1.0,
        outcome=TraceOutcome.SUCCESS,
    ),
    RewardTemplate(
        template_id="failure.default",
        base_score=-0.5,
        outcome=TraceOutcome.FAILURE,
    ),
    RewardTemplate(
        template_id="timeout.default",
        base_score=-0.75,
        outcome=TraceOutcome.TIMEOUT,
    ),
    RewardTemplate(
        template_id="cancelled.default",
        base_score=0.0,
        outcome=TraceOutcome.CANCELLED,
    ),
    # Wildcard — lowest priority fallback
    RewardTemplate(
        template_id="wildcard.default",
        base_score=0.0,
        outcome=None,
    ),
]


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class RewardEvaluator:
    """Selects and applies the best-matching :class:`RewardTemplate`.

    Parameters
    ----------
    templates:
        Ordered list of templates.  The evaluator picks the template with the
        highest :meth:`~RewardTemplate.specificity` that matches *trace*.  On
        equal specificity, the first match wins.
    """

    def __init__(self, templates: list[RewardTemplate] | None = None) -> None:
        self._templates = templates if templates is not None else list(DEFAULT_TEMPLATES)

    def evaluate(self, trace: RewardTrace) -> RewardResult:
        """Evaluate *trace* and return a :class:`RewardResult`.

        Returns a zero-score result with template ``"no_match"`` when no
        template applies.
        """
        best: RewardTemplate | None = None
        best_specificity = -1

        for tmpl in self._templates:
            if tmpl.matches(trace):
                sp = tmpl.specificity()
                if sp > best_specificity:
                    best = tmpl
                    best_specificity = sp

        if best is None:
            return RewardResult(
                trace_node_id=trace.node_id,
                score=0.0,
                template_id="no_match",
                rationale="No matching template",
            )

        return best.evaluate(trace)

    def add_template(self, template: RewardTemplate) -> None:
        """Append a template to the evaluator's template list."""
        self._templates.append(template)
