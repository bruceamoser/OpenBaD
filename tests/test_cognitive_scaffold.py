"""Tests for the cognitive engine scaffold — config, proto, and topics."""

from __future__ import annotations

import textwrap

import pytest

from openbad.cognitive.config import (
    CognitiveConfig,
    CognitiveSystem,
    ContextBudgetConfig,
    FallbackCortisolConfig,
    ProviderConfig,
    ReasoningDefaults,
    SystemAssignment,
    load_cognitive_config,
)
from openbad.nervous_system import topics

# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


class TestProviderConfig:
    def test_defaults(self) -> None:
        pc = ProviderConfig()
        assert pc.name == ""
        assert pc.timeout_ms == 30_000
        assert pc.enabled is True

    def test_custom(self) -> None:
        pc = ProviderConfig(name="openai", model="gpt-4o", timeout_ms=5000)
        assert pc.name == "openai"
        assert pc.model == "gpt-4o"


class TestContextBudgetConfig:
    def test_defaults(self) -> None:
        cb = ContextBudgetConfig()
        assert cb.slm_max_tokens == 8_192
        assert cb.llm_max_tokens == 32_768
        assert cb.reserved_system_tokens == 512


class TestReasoningDefaults:
    def test_defaults(self) -> None:
        rd = ReasoningDefaults()
        assert rd.default_max_tokens == 2_048
        assert rd.default_temperature == 0.7
        assert rd.critical_timeout_ms == 30_000
        assert rd.low_timeout_ms == 5_000


class TestCognitiveConfig:
    def test_defaults(self) -> None:
        cc = CognitiveConfig()
        assert cc.default_provider == "ollama"
        assert cc.enabled is True
        assert cc.providers == []
        assert cc.systems[CognitiveSystem.CHAT] == SystemAssignment()
        assert cc.fallback_cortisol == FallbackCortisolConfig()

    def test_frozen(self) -> None:
        cc = CognitiveConfig()
        with pytest.raises(AttributeError):
            cc.enabled = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


class TestLoadCognitiveConfig:
    def test_missing_file_gives_defaults(self) -> None:
        config = load_cognitive_config("nonexistent.yaml")
        assert config.default_provider == "ollama"
        assert config.providers == []

    def test_load_from_yaml(self, tmp_path: pytest.TempPathFactory) -> None:
        yaml_path = tmp_path / "cog.yaml"
        yaml_path.write_text(textwrap.dedent("""\
            cognitive:
              default_provider: openai
              enabled: false
              providers:
                - name: ollama
                  base_url: "http://localhost:11434"
                  model: "llama3.2"
                  timeout_ms: 5000
                  enabled: true
              systems:
                chat:
                  provider: openai
                  model: gpt-4o-mini
                reasoning:
                  provider: anthropic
                  model: claude-sonnet-4-20250514
              default_fallback_chain:
                - provider: ollama
                  model: llama3.2
                - provider: openai
                  model: gpt-4o-mini
              fallback_cortisol:
                release_per_step: 0.25
                escalation_after: 3
              context_budget:
                slm_max_tokens: 4096
                llm_max_tokens: 16384
                reserved_system_tokens: 256
              reasoning:
                default_max_tokens: 1024
                default_temperature: 0.5
                critical_timeout_ms: 20000
                high_timeout_ms: 10000
                medium_timeout_ms: 7000
                low_timeout_ms: 3000
        """))
        config = load_cognitive_config(yaml_path)
        assert config.default_provider == "openai"
        assert config.enabled is False
        assert len(config.providers) == 1
        assert config.providers[0].name == "ollama"
        assert config.providers[0].timeout_ms == 5000
        assert config.systems[CognitiveSystem.CHAT] == SystemAssignment(
            provider="openai", model="gpt-4o-mini"
        )
        assert config.systems[CognitiveSystem.REASONING] == SystemAssignment(
            provider="anthropic", model="claude-sonnet-4-20250514"
        )
        assert config.default_fallback_chain == (
            SystemAssignment(provider="ollama", model="llama3.2"),
            SystemAssignment(provider="openai", model="gpt-4o-mini"),
        )
        assert config.fallback_cortisol == FallbackCortisolConfig(
            release_per_step=0.25,
            escalation_after=3,
        )
        assert config.context_budget.slm_max_tokens == 4096
        assert config.context_budget.llm_max_tokens == 16384
        assert config.reasoning.default_max_tokens == 1024
        assert config.reasoning.low_timeout_ms == 3000

    def test_empty_yaml(self, tmp_path: pytest.TempPathFactory) -> None:
        yaml_path = tmp_path / "empty.yaml"
        yaml_path.write_text("")
        config = load_cognitive_config(yaml_path)
        assert config.default_provider == "ollama"

    def test_partial_yaml(self, tmp_path: pytest.TempPathFactory) -> None:
        yaml_path = tmp_path / "partial.yaml"
        yaml_path.write_text("cognitive:\n  default_provider: anthropic\n")
        config = load_cognitive_config(yaml_path)
        assert config.default_provider == "anthropic"
        assert config.providers == []
        assert config.context_budget.slm_max_tokens == 8_192


# ---------------------------------------------------------------------------
# Proto round-trip
# ---------------------------------------------------------------------------


class TestProtoRoundTrip:
    def test_reasoning_request(self) -> None:
        from openbad.nervous_system.schemas import cognitive_pb2

        msg = cognitive_pb2.ReasoningRequest(
            prompt="What is 2+2?",
            context_tokens=1024,
            priority=3,  # HIGH
            preferred_provider="ollama",
            max_tokens=512,
        )
        data = msg.SerializeToString()
        parsed = cognitive_pb2.ReasoningRequest()
        parsed.ParseFromString(data)
        assert parsed.prompt == "What is 2+2?"
        assert parsed.context_tokens == 1024
        assert parsed.preferred_provider == "ollama"
        assert parsed.max_tokens == 512

    def test_reasoning_response(self) -> None:
        from openbad.nervous_system.schemas import cognitive_pb2

        msg = cognitive_pb2.ReasoningResponse(
            content="4",
            model_used="llama3.2",
            provider="ollama",
            tokens_used=10,
            latency_ms=123.5,
            reasoning_trace="2+2=4",
        )
        data = msg.SerializeToString()
        parsed = cognitive_pb2.ReasoningResponse()
        parsed.ParseFromString(data)
        assert parsed.content == "4"
        assert parsed.model_used == "llama3.2"
        assert parsed.tokens_used == 10
        assert parsed.latency_ms == pytest.approx(123.5)

    def test_model_health_status(self) -> None:
        from openbad.nervous_system.schemas import cognitive_pb2

        msg = cognitive_pb2.ModelHealthStatus(
            provider="ollama",
            model_id="llama3.2",
            available=True,
            latency_p50=45.0,
            latency_p99=120.0,
        )
        data = msg.SerializeToString()
        parsed = cognitive_pb2.ModelHealthStatus()
        parsed.ParseFromString(data)
        assert parsed.provider == "ollama"
        assert parsed.available is True
        assert parsed.latency_p50 == pytest.approx(45.0)

    def test_escalation_request_unchanged(self) -> None:
        from openbad.nervous_system.schemas import cognitive_pb2

        msg = cognitive_pb2.EscalationRequest(
            event_topic="agent/sensory/vision/cam0/parsed",
            reason="unknown pattern",
            priority=4,  # CRITICAL
            reflex_id="attention",
        )
        data = msg.SerializeToString()
        parsed = cognitive_pb2.EscalationRequest()
        parsed.ParseFromString(data)
        assert parsed.reflex_id == "attention"

    def test_cognitive_result_unchanged(self) -> None:
        from openbad.nervous_system.schemas import cognitive_pb2

        msg = cognitive_pb2.CognitiveResult(
            correlation_id="abc123",
            decision="allow",
            model_used="llama3.2",
            tokens_consumed=42,
        )
        data = msg.SerializeToString()
        parsed = cognitive_pb2.CognitiveResult()
        parsed.ParseFromString(data)
        assert parsed.tokens_consumed == 42


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------


class TestCognitiveTopics:
    def test_request_topic(self) -> None:
        assert topics.COGNITIVE_REQUEST == "agent/cognitive/request"

    def test_response_topic(self) -> None:
        assert topics.COGNITIVE_RESPONSE == "agent/cognitive/response"

    def test_health_topic(self) -> None:
        assert topics.COGNITIVE_HEALTH == "agent/cognitive/health"

    def test_context_topic(self) -> None:
        assert topics.COGNITIVE_CONTEXT == "agent/cognitive/context"

    def test_wildcard(self) -> None:
        assert topics.COGNITIVE_ALL == "agent/cognitive/#"

    def test_existing_escalation_unchanged(self) -> None:
        assert topics.COGNITIVE_ESCALATION == "agent/cognitive/escalation"

    def test_existing_result_unchanged(self) -> None:
        assert topics.COGNITIVE_RESULT == "agent/cognitive/result"
