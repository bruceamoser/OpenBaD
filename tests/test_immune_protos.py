"""Tests for immune system proto messages and topic definitions."""

from __future__ import annotations

import time

from openbad.nervous_system.schemas import (
    Header,
    ImmuneAlert,
    QuarantineEvent,
    ScanResult,
    Severity,
    ThreatDetection,
    ThreatType,
)
from openbad.nervous_system.topics import (
    IMMUNE_ALERT,
    IMMUNE_ALL,
    IMMUNE_CLEARED,
    IMMUNE_QUARANTINE,
    IMMUNE_SCAN,
    IMMUNE_THREAT,
    STATIC_TOPICS,
    WILDCARD_TOPICS,
)

# ---------------------------------------------------------------------------
# Proto round-trip tests
# ---------------------------------------------------------------------------


def _header() -> Header:
    return Header(
        timestamp_unix=time.time(),
        source_module="immune_system",
        correlation_id="test-001",
        schema_version=1,
    )


class TestScanResultProto:
    def test_serialize_deserialize(self) -> None:
        msg = ScanResult(
            header=_header(),
            payload_hash="abc123",
            is_threat=True,
            detections=[
                ThreatDetection(
                    detector="rules_engine",
                    threat_type=ThreatType.PROMPT_INJECTION,
                    confidence=0.95,
                    rule_name="instruction_override",
                    detail="Detected 'ignore previous instructions'",
                ),
            ],
            scan_latency_ms=12.5,
            source_topic="agent/sensory/vision/screen0/parsed",
        )
        data = msg.SerializeToString()
        restored = ScanResult()
        restored.ParseFromString(data)
        assert restored.payload_hash == "abc123"
        assert restored.is_threat is True
        assert len(restored.detections) == 1
        assert restored.detections[0].threat_type == ThreatType.PROMPT_INJECTION
        assert restored.detections[0].confidence == 0.95
        assert restored.scan_latency_ms == 12.5

    def test_empty_detections(self) -> None:
        msg = ScanResult(
            header=_header(),
            payload_hash="def456",
            is_threat=False,
        )
        data = msg.SerializeToString()
        restored = ScanResult()
        restored.ParseFromString(data)
        assert restored.is_threat is False
        assert len(restored.detections) == 0


class TestThreatDetectionProto:
    def test_all_threat_types(self) -> None:
        for tt in [
            ThreatType.PROMPT_INJECTION,
            ThreatType.INSTRUCTION_OVERRIDE,
            ThreatType.DATA_EXFILTRATION,
            ThreatType.SSRF_ATTEMPT,
            ThreatType.SCHEMA_VIOLATION,
            ThreatType.PRIVILEGE_ESCALATION,
            ThreatType.ENCODED_PAYLOAD,
            ThreatType.DELIMITER_ESCAPE,
        ]:
            det = ThreatDetection(
                detector="test",
                threat_type=tt,
                confidence=0.9,
            )
            data = det.SerializeToString()
            restored = ThreatDetection()
            restored.ParseFromString(data)
            assert restored.threat_type == tt


class TestImmuneAlertProto:
    def test_serialize_deserialize(self) -> None:
        msg = ImmuneAlert(
            header=_header(),
            severity=Severity.CRITICAL,
            threat_type="prompt-injection",
            source_id="vision-parser",
            detail="Blocked injection attempt",
            evidence=b"raw evidence data",
        )
        data = msg.SerializeToString()
        restored = ImmuneAlert()
        restored.ParseFromString(data)
        assert restored.severity == Severity.CRITICAL
        assert restored.threat_type == "prompt-injection"
        assert restored.evidence == b"raw evidence data"


class TestQuarantineEventProto:
    def test_serialize_deserialize(self) -> None:
        now = time.time()
        msg = QuarantineEvent(
            header=_header(),
            source_id="tool-xyz",
            reason="Prompt injection detected",
            action_taken="payload-quarantined",
            quarantine_until_unix=now + 3600,
        )
        data = msg.SerializeToString()
        restored = QuarantineEvent()
        restored.ParseFromString(data)
        assert restored.source_id == "tool-xyz"
        assert restored.reason == "Prompt injection detected"
        assert restored.quarantine_until_unix > now


# ---------------------------------------------------------------------------
# Topic tests
# ---------------------------------------------------------------------------


class TestImmuneTopics:
    def test_topic_values(self) -> None:
        assert IMMUNE_SCAN == "agent/immune/scan"
        assert IMMUNE_THREAT == "agent/immune/threat"
        assert IMMUNE_ALERT == "agent/immune/alert"
        assert IMMUNE_QUARANTINE == "agent/immune/quarantine"
        assert IMMUNE_CLEARED == "agent/immune/cleared"

    def test_wildcard(self) -> None:
        assert IMMUNE_ALL == "agent/immune/+"

    def test_scan_in_static_topics(self) -> None:
        assert IMMUNE_SCAN in STATIC_TOPICS

    def test_threat_in_static_topics(self) -> None:
        assert IMMUNE_THREAT in STATIC_TOPICS

    def test_alert_in_static_topics(self) -> None:
        assert IMMUNE_ALERT in STATIC_TOPICS

    def test_quarantine_in_static_topics(self) -> None:
        assert IMMUNE_QUARANTINE in STATIC_TOPICS

    def test_cleared_in_static_topics(self) -> None:
        assert IMMUNE_CLEARED in STATIC_TOPICS

    def test_wildcard_in_wildcard_topics(self) -> None:
        assert IMMUNE_ALL in WILDCARD_TOPICS
