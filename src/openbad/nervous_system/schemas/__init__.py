"""Generated protobuf message schemas for OpenBaD event bus."""

from openbad.nervous_system.schemas.cognitive_pb2 import CognitiveResult, EscalationRequest
from openbad.nervous_system.schemas.common_pb2 import Header, Priority, Severity
from openbad.nervous_system.schemas.endocrine_pb2 import EndocrineEvent
from openbad.nervous_system.schemas.immune_pb2 import ImmuneAlert, QuarantineEvent
from openbad.nervous_system.schemas.memory_pb2 import LtmConsolidate, StmWrite
from openbad.nervous_system.schemas.reflex_pb2 import ReflexResult, ReflexState, ReflexTrigger
from openbad.nervous_system.schemas.telemetry_pb2 import (
    CpuTelemetry,
    DiskTelemetry,
    MemoryTelemetry,
    TokenTelemetry,
)

__all__ = [
    "CognitiveResult",
    "CpuTelemetry",
    "DiskTelemetry",
    "EndocrineEvent",
    "EscalationRequest",
    "Header",
    "ImmuneAlert",
    "LtmConsolidate",
    "MemoryTelemetry",
    "Priority",
    "QuarantineEvent",
    "ReflexResult",
    "ReflexState",
    "ReflexTrigger",
    "Severity",
    "StmWrite",
    "TokenTelemetry",
]
