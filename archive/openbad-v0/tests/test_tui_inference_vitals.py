"""Tests for #182 inference and vitals TUI panels and message routing."""

from __future__ import annotations

from unittest.mock import MagicMock

from openbad.nervous_system import topics
from openbad.nervous_system.schemas.cognitive_pb2 import ModelHealthStatus, ReasoningResponse
from openbad.nervous_system.schemas.endocrine_pb2 import EndocrineEvent
from openbad.nervous_system.schemas.reflex_pb2 import ReflexState
from openbad.nervous_system.schemas.telemetry_pb2 import (
    CpuTelemetry,
    DiskTelemetry,
    MemoryTelemetry,
    NetworkTelemetry,
    TokenTelemetry,
)
from openbad.tui.app import OpenBaDApp
from openbad.tui.mqtt_feed import MqttPayload
from openbad.tui.panels import InferencePanel, VitalsPanel


class TestVitalAndInferencePanels:
    def test_panel_instantiation(self):
        assert isinstance(VitalsPanel(), VitalsPanel)
        assert isinstance(InferencePanel(), InferencePanel)


class TestTopicSubscriptionRegistration:
    def test_subscribe_topics_registers_expected_proto_topics(self):
        app = OpenBaDApp()
        app.feed = MagicMock()
        app.feed.is_connected = True

        app._subscribe_topics()

        subscribed = [c[0][0] for c in app.feed.subscribe.call_args_list]
        assert topics.ENDOCRINE_ALL in subscribed
        assert topics.REFLEX_STATE in subscribed
        assert topics.TELEMETRY_CPU in subscribed
        assert topics.TELEMETRY_MEMORY in subscribed
        assert topics.TELEMETRY_DISK in subscribed
        assert topics.TELEMETRY_NETWORK in subscribed
        assert topics.TELEMETRY_TOKENS in subscribed
        assert topics.COGNITIVE_HEALTH in subscribed
        assert topics.COGNITIVE_RESPONSE in subscribed


class TestMessageRoutingWithoutUIRuntime:
    def test_reflex_payload_uses_current_state_field(self):
        app = OpenBaDApp()
        fsm = MagicMock()
        status = MagicMock()

        def _query_one(selector, _type=None):
            if selector == "#fsm-panel":
                return fsm
            return status

        app.query_one = _query_one  # type: ignore[method-assign]

        msg = ReflexState(current_state="ACTIVE")
        app.on_mqtt_payload(MqttPayload(topics.REFLEX_STATE, msg))

        assert fsm.state == "ACTIVE"

    def test_endocrine_payload_reads_level_field(self):
        app = OpenBaDApp()
        hormones = MagicMock()

        def _query_one(selector, _type=None):
            return hormones

        app.query_one = _query_one  # type: ignore[method-assign]

        msg = EndocrineEvent(hormone="cortisol", level=0.72)
        app.on_mqtt_payload(MqttPayload("agent/endocrine/cortisol", msg))
        hormones.update_levels.assert_called_once_with({"cortisol": 0.72})

    def test_telemetry_updates_vitals_panel_fields(self):
        app = OpenBaDApp()
        vitals = MagicMock()

        def _query_one(selector, _type=None):
            return vitals

        app.query_one = _query_one  # type: ignore[method-assign]

        app.on_mqtt_payload(MqttPayload(topics.TELEMETRY_CPU, CpuTelemetry(usage_percent=40.5)))
        app.on_mqtt_payload(
            MqttPayload(topics.TELEMETRY_MEMORY, MemoryTelemetry(usage_percent=71.2))
        )
        app.on_mqtt_payload(MqttPayload(topics.TELEMETRY_DISK, DiskTelemetry(usage_percent=55.1)))
        app.on_mqtt_payload(
            MqttPayload(
                topics.TELEMETRY_NETWORK,
                NetworkTelemetry(bytes_sent=2048, bytes_recv=4096),
            )
        )
        app.on_mqtt_payload(
            MqttPayload(
                topics.TELEMETRY_TOKENS,
                TokenTelemetry(tokens_used=1234, budget_remaining_pct=83.3, model_tier="slm"),
            )
        )

        assert vitals.cpu_usage == 40.5
        assert vitals.mem_usage == 71.2
        assert vitals.disk_usage == 55.1
        assert vitals.net_sent == 2048.0
        assert vitals.net_recv == 4096.0
        assert vitals.tokens_used == 1234
        assert vitals.token_remaining == 83.3
        assert vitals.model_tier == "slm"

    def test_cognitive_updates_inference_panel_fields(self):
        app = OpenBaDApp()
        inference = MagicMock()

        def _query_one(selector, _type=None):
            return inference

        app.query_one = _query_one  # type: ignore[method-assign]

        app.on_mqtt_payload(
            MqttPayload(
                topics.COGNITIVE_HEALTH,
                ModelHealthStatus(
                    provider="ollama",
                    model_id="phi3",
                    available=True,
                    latency_p50=52.1,
                    latency_p99=131.4,
                ),
            )
        )
        app.on_mqtt_payload(
            MqttPayload(
                topics.COGNITIVE_RESPONSE,
                ReasoningResponse(model_used="phi3", tokens_used=218, latency_ms=87.5),
            )
        )

        assert inference.provider == "ollama"
        assert inference.model_id == "phi3"
        assert inference.available is True
        assert inference.latency_p50 == 52.1
        assert inference.latency_p99 == 131.4
        assert inference.last_model_used == "phi3"
        assert inference.last_tokens == 218
        assert inference.last_latency_ms == 87.5
