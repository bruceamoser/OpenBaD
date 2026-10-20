"""L2HR mapper — language to hierarchical rewards.

Translates natural-language task outcomes into hormone adjustments
using a keyword-based heuristic classifier, with optional SLM override.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class HormoneAdjustment:
    """A set of hormone deltas to apply."""

    dopamine: float = 0.0
    adrenaline: float = 0.0
    cortisol: float = 0.0
    endorphin: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "dopamine": self.dopamine,
            "adrenaline": self.adrenaline,
            "cortisol": self.cortisol,
            "endorphin": self.endorphin,
        }

    def is_zero(self) -> bool:
        return all(
            v == 0.0
            for v in (self.dopamine, self.adrenaline, self.cortisol, self.endorphin)
        )


# Default keyword → category mapping.
_DEFAULT_KEYWORDS: dict[str, list[str]] = {
    "success": [
        "success", "resolved", "completed", "fixed", "solved",
        "correct", "accomplished", "achieved", "passed",
    ],
    "failure": [
        "failed", "error", "timeout", "retry", "retries",
        "crash", "exception", "broken", "unable",
    ],
    "threat": [
        "injection", "attack", "threat", "malicious", "exploit",
        "quarantine", "vulnerability", "breach", "suspicious",
    ],
    "urgency": [
        "urgent", "escalat", "critical", "emergency", "immediate",
        "deadline", "priority", "asap",
    ],
    "recovery": [
        "recover", "heal", "restored", "stabiliz", "resilient",
        "bounced back", "normalized",
    ],
}

# Default adjustments per category — deliberately smaller than direct hooks.
_DEFAULT_ADJUSTMENTS: dict[str, HormoneAdjustment] = {
    "success": HormoneAdjustment(dopamine=0.10),
    "failure": HormoneAdjustment(dopamine=-0.05, cortisol=0.10),
    "threat": HormoneAdjustment(dopamine=0.10, endorphin=0.10, adrenaline=0.10),
    "urgency": HormoneAdjustment(adrenaline=0.20),
    "recovery": HormoneAdjustment(endorphin=0.10, dopamine=0.05),
}


@dataclass
class L2HRConfig:
    """Configuration for the L2HR mapper."""

    keywords: dict[str, list[str]] = field(
        default_factory=lambda: {k: list(v) for k, v in _DEFAULT_KEYWORDS.items()},
    )
    adjustments: dict[str, HormoneAdjustment] = field(
        default_factory=lambda: dict(_DEFAULT_ADJUSTMENTS),
    )

    @classmethod
    def from_yaml(cls, path: Path) -> L2HRConfig:
        """Load L2HR config from YAML."""
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data = raw.get("l2hr", raw)

        keywords = dict(_DEFAULT_KEYWORDS)
        if "keywords" in data and isinstance(data["keywords"], dict):
            for cat, words in data["keywords"].items():
                if isinstance(words, list):
                    keywords[cat] = [str(w) for w in words]

        adjustments = dict(_DEFAULT_ADJUSTMENTS)
        if "adjustments" in data and isinstance(data["adjustments"], dict):
            for cat, vals in data["adjustments"].items():
                if isinstance(vals, dict):
                    adjustments[cat] = HormoneAdjustment(**{
                        k: float(v)
                        for k, v in vals.items()
                        if k in HormoneAdjustment.__dataclass_fields__
                    })

        return cls(keywords=keywords, adjustments=adjustments)


ClassifyFn = Callable[[str], str | None]


class L2HRMapper:
    """Maps natural-language outcomes to hormone adjustments.

    Parameters
    ----------
    config
        Keyword/adjustment configuration. Uses defaults if not provided.
    classify_fn
        Optional SLM-based classifier callback. Takes a text string,
        returns a category name (matching a key in adjustments) or ``None``.
        When provided, takes priority over keyword matching.
    """

    def __init__(
        self,
        config: L2HRConfig | None = None,
        classify_fn: ClassifyFn | None = None,
    ) -> None:
        self._config = config or L2HRConfig()
        self._classify_fn = classify_fn
        self._compiled: dict[str, re.Pattern[str]] = {}
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        """Pre-compile keyword regexes for each category."""
        for category, words in self._config.keywords.items():
            pattern = "|".join(re.escape(w) for w in words)
            self._compiled[category] = re.compile(pattern, re.IGNORECASE)

    def classify(self, text: str) -> str | None:
        """Classify *text* into a category.

        Uses *classify_fn* if provided, otherwise falls back to keyword matching.
        Returns ``None`` if no category matches.
        """
        if self._classify_fn is not None:
            result = self._classify_fn(text)
            if result is not None:
                return result

        for category, pattern in self._compiled.items():
            if pattern.search(text):
                return category
        return None

    def map(self, text: str) -> HormoneAdjustment:
        """Map a natural-language outcome to hormone adjustments.

        Returns a zero adjustment if the text doesn't match any category.
        """
        category = self.classify(text)
        if category is None:
            return HormoneAdjustment()
        return self._config.adjustments.get(category, HormoneAdjustment())

    def map_all(self, text: str) -> list[tuple[str, HormoneAdjustment]]:
        """Return adjustments for *all* matching categories.

        Useful when an outcome spans multiple categories (e.g. threat + success).
        """
        results: list[tuple[str, HormoneAdjustment]] = []
        if self._classify_fn is not None:
            cat = self._classify_fn(text)
            if cat is not None and cat in self._config.adjustments:
                results.append((cat, self._config.adjustments[cat]))

        for category, pattern in self._compiled.items():
            if pattern.search(text) and category not in {r[0] for r in results}:
                adj = self._config.adjustments.get(category)
                if adj is not None:
                    results.append((category, adj))

        return results
