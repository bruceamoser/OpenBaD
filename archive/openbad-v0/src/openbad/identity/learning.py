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
    direct_markers: int = 0
    gentle_markers: int = 0
    challenge_markers: int = 0
    topic_counts: Counter = field(default_factory=Counter)
    correction_count: int = 0
    correction_messages: list[str] = field(default_factory=list)
    interaction_count: int = 0
    activity_hours: list[int] = field(default_factory=list)

    def reset(self) -> None:
        self.message_lengths.clear()
        self.formal_markers = 0
        self.casual_markers = 0
        self.direct_markers = 0
        self.gentle_markers = 0
        self.challenge_markers = 0
        self.topic_counts.clear()
        self.correction_count = 0
        self.correction_messages.clear()
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
_DIRECT_PATTERNS = re.compile(
    r"\b(direct|blunt|straight|to the point|concise)\b",
    re.IGNORECASE,
)
_GENTLE_PATTERNS = re.compile(
    r"\b(gentle|diplomatic|soften|kindly|careful)\b",
    re.IGNORECASE,
)
_CHALLENGE_PATTERNS = re.compile(
    r"\b(challenge me|push back|disagree|debate|socratic)\b",
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
        if _DIRECT_PATTERNS.search(record.message):
            acc.direct_markers += 1
        if _GENTLE_PATTERNS.search(record.message):
            acc.gentle_markers += 1
        if _CHALLENGE_PATTERNS.search(record.message):
            acc.challenge_markers += 1

        for topic in record.topics:
            acc.topic_counts[topic] += 1

        if record.is_correction:
            acc.correction_count += 1
            acc.correction_messages.append(record.message)

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
            top_topics = [t for t, _ in acc.topic_counts.most_common(10)]
            existing = list(self._persistence.user.expertise_domains)
            merged = list(dict.fromkeys(existing + top_topics))
            updates["expertise_domains"] = merged
            active_existing = list(self._persistence.user.active_projects)
            updates["active_projects"] = list(
                dict.fromkeys(active_existing + top_topics[:5]),
            )

        # --- preferred feedback style ---
        if acc.challenge_markers:
            updates["preferred_feedback_style"] = "challenge me"
        elif acc.direct_markers > acc.gentle_markers:
            updates["preferred_feedback_style"] = "direct"
        elif acc.gentle_markers > acc.direct_markers:
            updates["preferred_feedback_style"] = "gentle"

        # --- inferred pet peeves ---
        if acc.correction_count >= 2:
            existing_peeves = list(self._persistence.user.pet_peeves)
            updates["pet_peeves"] = list(
                dict.fromkeys(
                    existing_peeves + ["Dislikes avoidable misunderstandings and repeats"]
                ),
            )

        # --- active hours ---
        if acc.activity_hours:
            start = min(acc.activity_hours)
            end = max(acc.activity_hours)
            updates["work_hours"] = (start, end)

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
            parts.append(f"work_window={min(acc.activity_hours)}-{max(acc.activity_hours)}")
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
