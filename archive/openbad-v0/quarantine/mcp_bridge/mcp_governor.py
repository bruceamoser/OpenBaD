"""Interoceptive governor for MCP bridge launches — Phase 10, Issue #419.

Before an isolated MCP bridge is started, this module queries the
interoception layer (:mod:`openbad.interoception.monitor`) for current
RAM and CPU states.  If system limits are breached the governor:

1. Refuses the MCP launch and returns :attr:`GovernorDecision.DEFERRED`.
2. Publishes an endocrine hormone event on the nervous system bus:
   - **CRITICAL** priority → Adrenaline (urgent, short burst).
   - Otherwise → Cortisol (sustained high-load signal).

Thresholds are configurable at construction time.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from openbad.interoception.monitor import CpuSnapshot, MemorySnapshot, collect_cpu, collect_memory
from openbad.nervous_system import topics
from openbad.tasks.models import TaskPriority

if TYPE_CHECKING:
    from openbad.nervous_system.client import NervousSystemClient

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurable defaults
# ---------------------------------------------------------------------------

#: RAM usage % above which MCP launch is deferred.
DEFAULT_RAM_THRESHOLD_PCT: float = 90.0

#: CPU usage % above which MCP launch is deferred.
DEFAULT_CPU_THRESHOLD_PCT: float = 95.0


# ---------------------------------------------------------------------------
# Public API types
# ---------------------------------------------------------------------------


class GovernorDecision(StrEnum):
    """Outcome of a :class:`McpGovernor` resource check."""

    ALLOW = "allow"
    DEFERRED = "deferred"


@dataclass(frozen=True)
class GovernorResult:
    """Result of :meth:`McpGovernor.check`.

    Attributes
    ----------
    decision:
        :attr:`GovernorDecision.ALLOW` or :attr:`GovernorDecision.DEFERRED`.
    reason:
        Human-readable explanation (empty string on ALLOW).
    cpu_snapshot:
        The :class:`~openbad.interoception.monitor.CpuSnapshot` used.
    memory_snapshot:
        The :class:`~openbad.interoception.monitor.MemorySnapshot` used.
    """

    decision: GovernorDecision
    reason: str
    cpu_snapshot: CpuSnapshot
    memory_snapshot: MemorySnapshot


# ---------------------------------------------------------------------------
# Governor class
# ---------------------------------------------------------------------------


class McpGovernor:
    """Resource gate for MCP bridge launches.

    Parameters
    ----------
    mqtt:
        Live :class:`~openbad.nervous_system.client.NervousSystemClient`.
        When ``None``, endocrine events are not published (useful in tests).
    ram_threshold_pct:
        RAM usage percentage ceiling; above this the launch is deferred.
    cpu_threshold_pct:
        CPU usage percentage ceiling; above this the launch is deferred.
    cpu_collector:
        Callable that returns a :class:`CpuSnapshot`.  Defaults to
        :func:`~openbad.interoception.monitor.collect_cpu`.
    memory_collector:
        Callable that returns a :class:`MemorySnapshot`.  Defaults to
        :func:`~openbad.interoception.monitor.collect_memory`.
    """

    def __init__(
        self,
        mqtt: NervousSystemClient | None = None,
        *,
        ram_threshold_pct: float = DEFAULT_RAM_THRESHOLD_PCT,
        cpu_threshold_pct: float = DEFAULT_CPU_THRESHOLD_PCT,
        cpu_collector=collect_cpu,      # noqa: ANN001
        memory_collector=collect_memory,  # noqa: ANN001
    ) -> None:
        self._mqtt = mqtt
        self._ram_threshold = ram_threshold_pct
        self._cpu_threshold = cpu_threshold_pct
        self._cpu_collector = cpu_collector
        self._memory_collector = memory_collector

    def check(self, *, task_priority: int = TaskPriority.NORMAL) -> GovernorResult:
        """Check current resource state and decide whether to allow an MCP launch.

        Parameters
        ----------
        task_priority:
            Priority of the requesting task node.  Use
            :class:`~openbad.tasks.models.TaskPriority` constants.

        Returns
        -------
        GovernorResult
            Contains :attr:`GovernorDecision.ALLOW` or
            :attr:`GovernorDecision.DEFERRED` plus snapshots.
        """
        cpu = self._cpu_collector()
        mem = self._memory_collector()

        breach_reasons: list[str] = []

        if mem.usage_percent >= self._ram_threshold:
            breach_reasons.append(
                f"RAM {mem.usage_percent:.1f}% ≥ threshold {self._ram_threshold:.1f}%"
            )

        if cpu.usage_percent >= self._cpu_threshold:
            breach_reasons.append(
                f"CPU {cpu.usage_percent:.1f}% ≥ threshold {self._cpu_threshold:.1f}%"
            )

        if not breach_reasons:
            return GovernorResult(
                decision=GovernorDecision.ALLOW,
                reason="",
                cpu_snapshot=cpu,
                memory_snapshot=mem,
            )

        reason = "; ".join(breach_reasons)
        log.warning("McpGovernor: deferring MCP launch — %s", reason)

        self._publish_endocrine(task_priority, reason)

        return GovernorResult(
            decision=GovernorDecision.DEFERRED,
            reason=reason,
            cpu_snapshot=cpu,
            memory_snapshot=mem,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _publish_endocrine(self, task_priority: int, reason: str) -> None:
        """Publish an Adrenaline or Cortisol event depending on priority."""
        if self._mqtt is None:
            return

        is_critical = task_priority >= int(TaskPriority.CRITICAL)
        topic = topics.ENDOCRINE_ADRENALINE if is_critical else topics.ENDOCRINE_CORTISOL
        hormone = "adrenaline" if is_critical else "cortisol"

        payload = json.dumps(
            {
                "trigger": "mcp_resource_breach",
                "hormone": hormone,
                "reason": reason,
            }
        ).encode()

        try:
            self._mqtt.publish_bytes(topic, payload)
            log.info("McpGovernor: published %s event (priority=%d)", hormone, task_priority)
        except Exception:
            log.exception("McpGovernor: could not publish endocrine event")
