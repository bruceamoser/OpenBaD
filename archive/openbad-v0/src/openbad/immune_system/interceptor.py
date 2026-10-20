"""Immune interceptor — gates inbound data before it reaches the cognitive module."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from openbad.immune_system.anomaly_detector import AnomalyDetector
from openbad.immune_system.quarantine import QuarantineStore
from openbad.immune_system.rules_engine import RulesEngine, ScanReport
from openbad.nervous_system import topics


class Verdict(Enum):
    """Outcome of an interceptor scan."""

    CLEARED = "cleared"
    BLOCKED = "blocked"


# Default topics the interceptor subscribes to.
DEFAULT_SUBSCRIBED_TOPICS: list[str] = [
    "agent/sensory/vision/+/parsed",
    "agent/sensory/audio/+",
    topics.COGNITIVE_ESCALATION,
]


@dataclass(frozen=True)
class ImmuneAlert:
    """Alert published when a threat is detected and blocked."""

    entry_id: str
    threat_type: str
    severity: str
    confidence: float
    source_topic: str
    timestamp: float


@dataclass(frozen=True)
class ScanVerdict:
    """Full result of an interceptor scan for one message."""

    verdict: Verdict
    topic: str
    scan_report: ScanReport
    anomaly_count: int = 0
    alert: ImmuneAlert | None = None
    scan_ms: float = 0.0


@dataclass
class InterceptorStats:
    """Running statistics tracked by the interceptor."""

    total_scanned: int = 0
    total_cleared: int = 0
    total_blocked: int = 0
    scan_latency_ms: list[float] = field(default_factory=list)

    @property
    def avg_latency_ms(self) -> float:
        if not self.scan_latency_ms:
            return 0.0
        return sum(self.scan_latency_ms) / len(self.scan_latency_ms)


class ImmuneInterceptor:
    """Middleware that scans inbound messages and gates them.

    For each message the interceptor:
    1. Runs the :class:`RulesEngine` regex scan.
    2. Runs the :class:`AnomalyDetector` structural checks.
    3. If either detects a threat → quarantine the payload and emit
       a :class:`ScanVerdict` with ``BLOCKED`` plus an :class:`ImmuneAlert`.
    4. If clean → return a ``CLEARED`` verdict.
    """

    def __init__(
        self,
        rules_engine: RulesEngine,
        anomaly_detector: AnomalyDetector,
        quarantine_store: QuarantineStore,
        *,
        subscribed_topics: list[str] | None = None,
    ) -> None:
        self._rules = rules_engine
        self._anomaly = anomaly_detector
        self._quarantine = quarantine_store
        self._topics = list(
            subscribed_topics or DEFAULT_SUBSCRIBED_TOPICS,
        )
        self._stats = InterceptorStats()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def subscribed_topics(self) -> list[str]:
        return list(self._topics)

    @property
    def stats(self) -> InterceptorStats:
        return self._stats

    # ------------------------------------------------------------------
    # Core scan
    # ------------------------------------------------------------------

    def scan(self, topic: str, payload: bytes) -> ScanVerdict:
        """Scan *payload* from *topic* and return a verdict.

        The payload is decoded as UTF-8 for text scanning.  Binary
        payloads that cannot be decoded are treated as potential
        threats (schema violation / exfiltration vector).
        """
        t0 = time.monotonic()
        self._stats.total_scanned += 1

        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            text = ""

        # --- Rules engine ---
        report = self._rules.scan(text)

        # --- Anomaly detection ---
        anomaly_report = self._anomaly.scan_text(text)
        anomaly_count = len(anomaly_report.results)

        elapsed_ms = (time.monotonic() - t0) * 1000

        is_threat = report.is_threat or anomaly_report.has_anomalies

        if is_threat:
            return self._handle_threat(
                topic, payload, report, anomaly_count, elapsed_ms,
            )

        self._stats.total_cleared += 1
        self._stats.scan_latency_ms.append(elapsed_ms)
        return ScanVerdict(
            verdict=Verdict.CLEARED,
            topic=topic,
            scan_report=report,
            anomaly_count=0,
            scan_ms=elapsed_ms,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _handle_threat(
        self,
        topic: str,
        payload: bytes,
        report: ScanReport,
        anomaly_count: int,
        elapsed_ms: float,
    ) -> ScanVerdict:
        """Quarantine the payload and produce a blocked verdict + alert."""
        # Determine the most severe threat type and confidence
        threat_type, severity, confidence = self._classify_threat(
            report, anomaly_count,
        )

        entry = self._quarantine.quarantine(
            payload, threat_type, confidence, topic,
        )

        alert = ImmuneAlert(
            entry_id=entry.entry_id,
            threat_type=threat_type,
            severity=severity,
            confidence=confidence,
            source_topic=topic,
            timestamp=entry.timestamp,
        )

        self._stats.total_blocked += 1
        self._stats.scan_latency_ms.append(elapsed_ms)

        return ScanVerdict(
            verdict=Verdict.BLOCKED,
            topic=topic,
            scan_report=report,
            anomaly_count=anomaly_count,
            alert=alert,
            scan_ms=elapsed_ms,
        )

    @staticmethod
    def _classify_threat(
        report: ScanReport,
        anomaly_count: int,
    ) -> tuple[str, str, float]:
        """Return (threat_type, severity, confidence) from combined results."""
        severity_rank = {
            "critical": 3,
            "high": 2,
            "medium": 1,
            "low": 0,
        }

        if report.matches:
            # Pick the most severe rule match
            best = max(
                report.matches,
                key=lambda m: severity_rank.get(m.severity, -1),
            )
            # Base confidence from match count and severity
            confidence = min(
                0.5 + 0.1 * len(report.matches)
                + 0.1 * severity_rank.get(best.severity, 0),
                1.0,
            )
            return best.rule_name, best.severity, confidence

        # Only anomaly hits (no rule matches)
        return (
            "anomaly",
            "high" if anomaly_count > 1 else "medium",
            min(0.5 + 0.1 * anomaly_count, 1.0),
        )
