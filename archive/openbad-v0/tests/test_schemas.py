"""Round-trip serialization tests for protobuf schemas — Issue #4."""

from __future__ import annotations

import time

from openbad.nervous_system.schemas import (
    CognitiveResult,
    CpuTelemetry,
    DiskTelemetry,
    EndocrineEvent,
    EscalationRequest,
    Header,
    ImmuneAlert,
    LtmConsolidate,
    MemoryTelemetry,
    Priority,
    QuarantineEvent,
    ReflexResult,
    ReflexState,
    ReflexTrigger,
    Severity,
    StmWrite,
    TokenTelemetry,
)


def _make_header(**overrides: object) -> Header:
    defaults = {
        "timestamp_unix": time.time(),
        "source_module": "test",
        "correlation_id": "test-corr-001",
        "schema_version": 1,
    }
    defaults.update(overrides)
    return Header(**defaults)


class TestHeaderRoundTrip:
    def test_serialize_deserialize(self) -> None:
        original = _make_header()
        data = original.SerializeToString()
        restored = Header()
        restored.ParseFromString(data)
        assert restored.source_module == original.source_module
        assert restored.schema_version == original.schema_version


class TestCpuTelemetryRoundTrip:
    def test_round_trip(self) -> None:
        original = CpuTelemetry(
            header=_make_header(source_module="interoception"),
            usage_percent=42.5,
            system_percent=12.1,
            user_percent=30.4,
            core_count=8,
            load_avg_1m=2.1,
        )
        data = original.SerializeToString()
        restored = CpuTelemetry()
        restored.ParseFromString(data)
        assert abs(restored.usage_percent - 42.5) < 0.01
        assert restored.core_count == 8
        assert restored.header.source_module == "interoception"

    def test_binary_is_compact(self) -> None:
        msg = CpuTelemetry(
            header=_make_header(),
            usage_percent=99.9,
            core_count=16,
        )
        data = msg.SerializeToString()
        assert len(data) < 100  # Protobuf should be very compact


class TestMemoryTelemetryRoundTrip:
    def test_round_trip(self) -> None:
        original = MemoryTelemetry(
            header=_make_header(),
            usage_percent=67.3,
            used_bytes=8_000_000_000,
            total_bytes=16_000_000_000,
            available_bytes=8_000_000_000,
            swap_percent=5.0,
        )
        data = original.SerializeToString()
        restored = MemoryTelemetry()
        restored.ParseFromString(data)
        assert restored.used_bytes == 8_000_000_000
        assert restored.total_bytes == 16_000_000_000
        assert abs(restored.swap_percent - 5.0) < 0.01


class TestDiskTelemetryRoundTrip:
    def test_round_trip(self) -> None:
        original = DiskTelemetry(
            header=_make_header(),
            usage_percent=55.0,
            read_bytes=1_000_000,
            write_bytes=2_000_000,
            io_latency_ms=3.5,
            free_bytes=500_000_000,
        )
        data = original.SerializeToString()
        restored = DiskTelemetry()
        restored.ParseFromString(data)
        assert restored.read_bytes == 1_000_000
        assert abs(restored.io_latency_ms - 3.5) < 0.01


class TestTokenTelemetryRoundTrip:
    def test_round_trip(self) -> None:
        original = TokenTelemetry(
            header=_make_header(),
            tokens_used=50_000,
            budget_ceiling=100_000,
            budget_remaining_pct=50.0,
            cost_per_action_avg=0.003,
            model_tier="llm",
        )
        data = original.SerializeToString()
        restored = TokenTelemetry()
        restored.ParseFromString(data)
        assert restored.tokens_used == 50_000
        assert restored.model_tier == "llm"


class TestReflexTriggerRoundTrip:
    def test_round_trip(self) -> None:
        original = ReflexTrigger(
            header=_make_header(),
            reflex_id="thermal-throttle",
            event_topic="agent/endocrine/cortisol",
            event_payload=b'{"level": 0.9}',
            severity=Severity.CRITICAL,
        )
        data = original.SerializeToString()
        restored = ReflexTrigger()
        restored.ParseFromString(data)
        assert restored.reflex_id == "thermal-throttle"
        assert restored.severity == Severity.CRITICAL
        assert restored.event_payload == b'{"level": 0.9}'


class TestReflexResultRoundTrip:
    def test_round_trip(self) -> None:
        original = ReflexResult(
            header=_make_header(),
            reflex_id="budget-exhaustion",
            handled=True,
            action_taken="Blocked new LLM calls",
            escalated=False,
            error="",
        )
        data = original.SerializeToString()
        restored = ReflexResult()
        restored.ParseFromString(data)
        assert restored.handled is True
        assert restored.escalated is False


class TestReflexStateRoundTrip:
    def test_round_trip(self) -> None:
        original = ReflexState(
            header=_make_header(),
            previous_state="ACTIVE",
            current_state="THROTTLED",
            trigger_event="cortisol_critical",
        )
        data = original.SerializeToString()
        restored = ReflexState()
        restored.ParseFromString(data)
        assert restored.previous_state == "ACTIVE"
        assert restored.current_state == "THROTTLED"


class TestEscalationRequestRoundTrip:
    def test_round_trip(self) -> None:
        original = EscalationRequest(
            header=_make_header(),
            event_topic="agent/immune/alert",
            event_payload=b"binary-data",
            reason="Unknown threat pattern",
            priority=Priority.HIGH,
            reflex_id="security",
        )
        data = original.SerializeToString()
        restored = EscalationRequest()
        restored.ParseFromString(data)
        assert restored.priority == Priority.HIGH
        assert restored.reason == "Unknown threat pattern"


class TestCognitiveResultRoundTrip:
    def test_round_trip(self) -> None:
        original = CognitiveResult(
            header=_make_header(),
            correlation_id="corr-42",
            decision="Quarantine the source",
            action_payload=b"action-bytes",
            model_used="llm",
            tokens_consumed=1500,
        )
        data = original.SerializeToString()
        restored = CognitiveResult()
        restored.ParseFromString(data)
        assert restored.correlation_id == "corr-42"
        assert restored.tokens_consumed == 1500


class TestImmuneAlertRoundTrip:
    def test_round_trip(self) -> None:
        original = ImmuneAlert(
            header=_make_header(),
            severity=Severity.WARNING,
            threat_type="prompt-injection",
            source_id="tool-xyz",
            detail="Suspicious prompt detected",
            evidence=b"evidence-bytes",
        )
        data = original.SerializeToString()
        restored = ImmuneAlert()
        restored.ParseFromString(data)
        assert restored.threat_type == "prompt-injection"
        assert restored.severity == Severity.WARNING


class TestQuarantineEventRoundTrip:
    def test_round_trip(self) -> None:
        original = QuarantineEvent(
            header=_make_header(),
            source_id="tool-abc",
            reason="Repeated violations",
            action_taken="topic-publish-revoked",
            quarantine_until_unix=time.time() + 3600,
        )
        data = original.SerializeToString()
        restored = QuarantineEvent()
        restored.ParseFromString(data)
        assert restored.source_id == "tool-abc"
        assert restored.action_taken == "topic-publish-revoked"


class TestEndocrineEventRoundTrip:
    def test_round_trip(self) -> None:
        original = EndocrineEvent(
            header=_make_header(),
            hormone="cortisol",
            level=0.85,
            severity=Severity.CRITICAL,
            metric_name="cpu_percent",
            metric_value=92.0,
            recommended_action="throttle_background_tasks",
        )
        data = original.SerializeToString()
        restored = EndocrineEvent()
        restored.ParseFromString(data)
        assert restored.hormone == "cortisol"
        assert abs(restored.level - 0.85) < 0.01
        assert restored.severity == Severity.CRITICAL


class TestStmWriteRoundTrip:
    def test_round_trip(self) -> None:
        original = StmWrite(
            header=_make_header(),
            key="task-42-context",
            value=b"serialised-context-data",
            ttl_seconds=300.0,
            context="task-42",
        )
        data = original.SerializeToString()
        restored = StmWrite()
        restored.ParseFromString(data)
        assert restored.key == "task-42-context"
        assert restored.value == b"serialised-context-data"
        assert abs(restored.ttl_seconds - 300.0) < 0.01


class TestLtmConsolidateRoundTrip:
    def test_round_trip(self) -> None:
        original = LtmConsolidate(
            header=_make_header(),
            stm_keys=["key-a", "key-b", "key-c"],
            strategy="summarise",
            destination="long-term-store",
        )
        data = original.SerializeToString()
        restored = LtmConsolidate()
        restored.ParseFromString(data)
        assert list(restored.stm_keys) == ["key-a", "key-b", "key-c"]
        assert restored.strategy == "summarise"


class TestSeverityEnum:
    def test_values(self) -> None:
        assert Severity.SEVERITY_UNSPECIFIED == 0
        assert Severity.INFO == 1
        assert Severity.WARNING == 2
        assert Severity.CRITICAL == 3


class TestPriorityEnum:
    def test_values(self) -> None:
        assert Priority.PRIORITY_UNSPECIFIED == 0
        assert Priority.LOW == 1
        assert Priority.MEDIUM == 2
        assert Priority.HIGH == 3
        assert Priority.PRIORITY_CRITICAL == 4


class TestAllSchemasImportable:
    """Verify every type is accessible from openbad.nervous_system.schemas."""

    def test_all_exports(self) -> None:
        from openbad.nervous_system import schemas

        for name in schemas.__all__:
            assert hasattr(schemas, name), f"{name} not importable"
