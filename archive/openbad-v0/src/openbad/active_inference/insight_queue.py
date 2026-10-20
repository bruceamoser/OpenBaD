"""Proactive insight queue for curiosity-driven discoveries."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class ProactiveInsight:
    """A discovery the agent wants to share with the user."""

    id: str
    source: str
    summary: str
    details: dict[str, Any]
    priority: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    dismissed: bool = False


class InsightQueue:
    """Thread-safe queue for proactive insights."""

    def __init__(self, max_size: int = 50) -> None:
        self._max_size = max_size
        self._insights: list[ProactiveInsight] = []
        self._lock = asyncio.Lock()
        self._id_counter = 0

    async def add(
        self,
        source: str,
        summary: str,
        details: dict[str, Any],
        priority: float,
    ) -> str:
        """Add a new insight to the queue."""
        async with self._lock:
            self._id_counter += 1
            insight_id = f"insight_{self._id_counter}"
            insight = ProactiveInsight(
                id=insight_id,
                source=source,
                summary=summary,
                details=details,
                priority=priority,
            )
            self._insights.append(insight)
            self._insights.sort(key=lambda x: x.priority, reverse=True)
            if len(self._insights) > self._max_size:
                self._insights = self._insights[: self._max_size]
            return insight_id

    async def get_pending(self, limit: int = 10) -> list[ProactiveInsight]:
        """Get pending insights (not dismissed), sorted by priority."""
        async with self._lock:
            pending = [i for i in self._insights if not i.dismissed]
            return pending[:limit]

    async def dismiss(self, insight_id: str) -> bool:
        """Mark an insight as dismissed."""
        async with self._lock:
            for insight in self._insights:
                if insight.id == insight_id:
                    insight.dismissed = True
                    return True
        return False

    async def clear_dismissed(self) -> int:
        """Remove all dismissed insights, return count removed."""
        async with self._lock:
            before = len(self._insights)
            self._insights = [i for i in self._insights if not i.dismissed]
            return before - len(self._insights)

    async def count_pending(self) -> int:
        """Return count of pending (not dismissed) insights."""
        async with self._lock:
            return sum(1 for i in self._insights if not i.dismissed)
