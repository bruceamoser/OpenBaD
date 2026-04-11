from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from openbad.cognitive.context_manager import ContextWindowManager
from openbad.cognitive.providers.base import HealthStatus, ModelInfo, ProviderAdapter
from openbad.memory.episodic import EpisodicMemory
from openbad.memory.semantic import SemanticMemory
from openbad.memory.stm import ShortTermMemory
from openbad.wui import chat_pipeline
from openbad.wui.usage_tracker import UsageTracker


class _CapturingAdapter(ProviderAdapter):
    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens
        self.prompts: list[str] = []

    async def complete(self, prompt: str, model_id: str | None = None, **kwargs):
        raise NotImplementedError

    async def stream(
        self,
        prompt: str,
        model_id: str | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        self.prompts.append(prompt)
        for token in self._tokens:
            yield token

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(model_id="test-model", provider="test-provider")]

    async def health_check(self) -> HealthStatus:
        return HealthStatus(provider="test-provider", available=True)


@pytest.fixture(autouse=True)
def _reset_chat_pipeline_state(monkeypatch, tmp_path):
    monkeypatch.setattr(
        chat_pipeline,
        "_stm",
        ShortTermMemory(max_tokens=8192, default_ttl=7200.0),
    )
    monkeypatch.setattr(
        chat_pipeline,
        "_episodic",
        EpisodicMemory(storage_path=tmp_path / "episodic.json"),
    )
    monkeypatch.setattr(
        chat_pipeline,
        "_semantic",
        SemanticMemory(storage_path=tmp_path / "semantic.json"),
    )
    monkeypatch.setattr(
        chat_pipeline,
        "_ctx_manager",
        ContextWindowManager(default_limit=4096),
    )
    monkeypatch.setattr(
        chat_pipeline,
        "scan_input",
        lambda _text: SimpleNamespace(is_threat=False, matches=[]),
    )


@pytest.mark.asyncio
async def test_stream_chat_uses_persistent_history_and_no_duplicate_current_message():
    chat_pipeline._write_turn(
        "session-1",
        chat_pipeline.ConversationTurn(role="user", content="prior question"),
    )
    chat_pipeline._write_turn(
        "session-1",
        chat_pipeline.ConversationTurn(role="assistant", content="prior answer"),
    )
    chat_pipeline._write_turn(
        "session-older",
        chat_pipeline.ConversationTurn(
            role="assistant",
            content="Remember the maintenance window on Friday.",
        ),
    )

    adapter = _CapturingAdapter(["Hello", " world"])

    user_profile = SimpleNamespace(
        name="Bruce",
        preferred_name="Bruce",
        communication_style=SimpleNamespace(value="terse"),
        expertise_domains=["systems", "python"],
        interaction_history_summary="Prefers direct answers.",
    )
    assistant_profile = SimpleNamespace(
        name="OpenBaD",
        persona_summary="System-level orchestration assistant",
        learning_focus=["memory", "routing"],
    )
    modulation = SimpleNamespace(
        exploration_budget_multiplier=1.2,
        max_reasoning_depth_multiplier=1.3,
        proactive_suggestion_threshold=0.4,
        challenge_probability=0.6,
        cortisol_decay_multiplier=1.1,
    )

    chunks = [
        chunk
        async for chunk in chat_pipeline.stream_chat(
            adapter,
            "test-model",
            "What should I remember about Friday?",
            "session-1",
            provider_name="test-provider",
            user_profile=user_profile,
            assistant_profile=assistant_profile,
            modulation=modulation,
        )
    ]

    assert adapter.prompts
    prompt = adapter.prompts[0]
    assert prompt.count("What should I remember about Friday?") == 1
    assert "Assistant identity: OpenBaD" in prompt
    assert "Preferred communication style: terse" in prompt
    assert "Relevant prior memories:" in prompt

    assert chunks[-1].done is True
    assert [chunk.token for chunk in chunks if chunk.token] == ["Hello", " world"]

    history = chat_pipeline.get_conversation_history("session-1", limit=10)
    assert [turn.content for turn in history] == [
        "prior question",
        "prior answer",
        "What should I remember about Friday?",
        "Hello world",
    ]


def test_assemble_context_uses_semantic_retrieval_from_prior_sessions():
    chat_pipeline._write_turn(
        "session-previous",
        chat_pipeline.ConversationTurn(
            role="assistant",
            content="Deployment notes: rotate the API key after the Friday rollout.",
        ),
    )

    context = chat_pipeline.assemble_context(
        "session-current",
        "What do the deployment notes say about Friday rollout?",
        chat_pipeline.CognitiveSystem.CHAT,
        "test-model",
    )

    assert "Relevant prior memories:" in context.supporting_context
    assert "Friday rollout" in context.supporting_context


@pytest.mark.asyncio
async def test_stream_chat_records_usage_to_tracker(tmp_path):
    adapter = _CapturingAdapter(["a", "b", "c"])
    tracker = UsageTracker(db_path=tmp_path / "usage.db")

    try:
        chunks = [
            chunk
            async for chunk in chat_pipeline.stream_chat(
                adapter,
                "test-model",
                "Count these tokens",
                "session-usage",
                system=chat_pipeline.CognitiveSystem.REASONING,
                provider_name="test-provider",
                usage_tracker=tracker,
            )
        ]
    finally:
        snapshot = tracker.snapshot()
        tracker.close()

    assert chunks[-1].done is True
    assert snapshot["summary"]["total_used"] == 3
    assert snapshot["by_provider_model"][0]["provider"] == "test-provider"
    assert snapshot["by_provider_model"][0]["model"] == "test-model"
    assert snapshot["by_system"][0]["system"] == "reasoning"