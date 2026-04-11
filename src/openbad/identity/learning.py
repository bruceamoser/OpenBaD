"""User learning pipeline — infer preferences from interaction patterns.

Observes cognitive events and updates the UserProfile LTM shadow with
inferred preferences. All learning is local; no data leaves the system.
"""

from __future__ import annotations

import logging
import re
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from openbad.identity.persistence import IdentityPersistence

logger = logging.getLogger(__name__)


@dataclass
class InteractionRecord:
    """A single observed user interaction."""

    message: str
    timestamp: float = 0.0
    topics: list[str] = field(default_factory=list)
    is_correction: bool = False


@dataclass
class _Accumulator:
    """Internal batch accumulator for tracked signals."""

    message_lengths: list[int] = field(default_factory=list)
    formal_markers: int = 0
    casual_markers: int = 0
    topic_counts: Counter = field(default_factory=Counter)
    correction_count: int = 0
    interaction_count: int = 0
    activity_hours: list[int] = field(default_factory=list)

    def reset(self) -> None:
        self.message_lengths.clear()
        self.formal_markers = 0
        self.casual_markers = 0
        self.topic_counts.clear()
        self.correction_count = 0
        self.interaction_count = 0
        self.activity_hours.clear()


_FORMAL_PATTERNS = re.compile(
    r"\b(please|kindly|would you|could you|i would appreciate)\b",
    re.IGNORECASE,
)
_CASUAL_PATTERNS = re.compile(
    r"\b(hey|yo|lol|thx|k|nah|yeah|gonna|wanna)\b",
    re.IGNORECASE,
)


class UserLearningPipeline:
    """Learns user preferences from interaction patterns.

    Updates are batched and written to the LTM shadow at configurable
    intervals. Does not modify UserProfile directly.

    Parameters
    ----------
    persistence:
        The identity persistence layer managing LTM shadows.
    batch_size:
        Number of interactions before flushing to LTM (default 50).
    """

    def __init__(
        self,
        persistence: IdentityPersistence,
        batch_size: int = 50,
    ) -> None:
        self._persistence = persistence
        self._batch_size = batch_size
        self._acc = _Accumulator()

    @property
    def pending(self) -> int:
        """Number of interactions accumulated since last flush."""
        return self._acc.interaction_count

    def observe(self, record: InteractionRecord) -> bool:
        """Process one user interaction.

        Returns ``True`` if a batch flush was triggered.
        """
        acc = self._acc
        acc.interaction_count += 1
        acc.message_lengths.append(len(record.message))

        if _FORMAL_PATTERNS.search(record.message):
            acc.formal_markers += 1
        if _CASUAL_PATTERNS.search(record.message):
            acc.casual_markers += 1

        for topic in record.topics:
            acc.topic_counts[topic] += 1

        if record.is_correction:
            acc.correction_count += 1

        ts = record.timestamp or time.time()
        hour = time.localtime(ts).tm_hour
        acc.activity_hours.append(hour)

        if acc.interaction_count >= self._batch_size:
            self.flush()
            return True
        return False

    def flush(self) -> dict[str, Any]:
        """Write accumulated signals to LTM shadow and reset.

        Returns a dict summarising what was written.
        """
        acc = self._acc
        if acc.interaction_count == 0:
            return {}

        updates: dict[str, Any] = {}

        # --- communication style inference ---
        if acc.formal_markers > acc.casual_markers:
            updates["communication_style"] = "formal"
        elif acc.casual_markers > acc.formal_markers:
            updates["communication_style"] = "casual"
        elif acc.message_lengths:
            avg_len = statistics.mean(acc.message_lengths)
            if avg_len < 30:
                updates["communication_style"] = "terse"

        # --- expertise domains ---
        if acc.topic_counts:
            top_topics = [
                t for t, _ in acc.topic_counts.most_common(10)
            ]
            existing = list(self._persistence.user.expertise_domains)
            merged = list(dict.fromkeys(existing + top_topics))
            updates["expertise_domains"] = merged

        # --- interaction history summary ---
        parts: list[str] = []
        if acc.message_lengths:
            avg = statistics.mean(acc.message_lengths)
            parts.append(f"avg_message_length={avg:.0f}")
        if acc.correction_count:
            parts.append(f"corrections={acc.correction_count}")
        if acc.activity_hours:
            mode_hour = max(set(acc.activity_hours), key=acc.activity_hours.count)
            parts.append(f"peak_hour={mode_hour}")
        if parts:
            updates["interaction_history_summary"] = "; ".join(parts)

        if updates:
            self._persistence.update_user(**updates)

        result = {
            "interactions": acc.interaction_count,
            "updates": updates,
        }
        acc.reset()
        return result
