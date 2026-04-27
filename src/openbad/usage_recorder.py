"""Shared helpers for persisting LLM usage across subsystems."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from openbad.autonomy.session_policy import load_session_policy, session_id_for
from openbad.cognitive.config import CognitiveSystem
from openbad.cognitive.providers.base import CompletionResult, HealthStatus, ModelInfo, ProviderAdapter
from openbad.wui.usage_tracker import UsageTracker, resolve_usage_db_path

_log = logging.getLogger(__name__)

_POLICY_SESSION_KEYS = {"chat", "tasks", "research", "doctor", "immune"}
_SYSTEM_SESSION_IDS = {
    "reasoning": "reasoning-loop",
    "reactions": "reaction-loop",
    "sleep": "sleep-cycle",
}


def _normalize_system_name(system: str | CognitiveSystem) -> str:
    if isinstance(system, CognitiveSystem):
        return system.value
    return str(system or "").strip().lower() or "unknown"

def _safe_string(value: object, fallback: str) -> str:
    if isinstance(value, str):
        text = value.strip()
        return text or fallback
    return fallback


def _resolve_session_id(
    policy: dict[str, object],
    system: str | CognitiveSystem,
    session_id: str = "",
) -> str:
    normalized_system = _normalize_system_name(system)
    if session_id.strip():
        return session_id.strip()
    if normalized_system in _POLICY_SESSION_KEYS:
        return session_id_for(policy, normalized_system)
    return _SYSTEM_SESSION_IDS.get(normalized_system, normalized_system)


def record_usage_event(
    *,
    provider: str,
    model: str,
    system: str | CognitiveSystem,
    tokens: int,
    request_id: str = "",
    session_id: str = "",
) -> None:
    policy = load_session_policy()
    tracker = UsageTracker(db_path=resolve_usage_db_path())
    try:
        tracker.record(
            provider=_safe_string(provider, "unknown"),
            model=_safe_string(model, "unknown"),
            system=_normalize_system_name(system),
            tokens=tokens,
            request_id=request_id,
            session_id=_resolve_session_id(policy, system, session_id),
        )
    finally:
        tracker.close()


class UsageRecorder:
    """Persist token usage with consistent session attribution."""

    def __init__(
        self,
        tracker: UsageTracker | None = None,
        *,
        policy: dict[str, object] | None = None,
    ) -> None:
        self._tracker = tracker or UsageTracker(db_path=resolve_usage_db_path())
        self._policy = policy or load_session_policy()
        self._owns_tracker = tracker is None

    def close(self) -> None:
        if self._owns_tracker:
            self._tracker.close()

    def record_completion(
        self,
        *,
        provider: str,
        model: str,
        system: str | CognitiveSystem,
        tokens: int,
        request_id: str = "",
        session_id: str = "",
    ) -> None:
        normalized_system = _normalize_system_name(system)
        resolved_session_id = _resolve_session_id(
            self._policy,
            normalized_system,
            session_id,
        )
        self._tracker.record(
            provider=_safe_string(provider, "unknown"),
            model=_safe_string(model, "unknown"),
            system=normalized_system,
            tokens=tokens,
            request_id=request_id,
            session_id=resolved_session_id,
        )

    def session_id_for_system(self, system: str | CognitiveSystem) -> str:
        return _resolve_session_id(self._policy, system)


class UsageTrackingProviderAdapter(ProviderAdapter):
    """Wrap a provider so health checks and model discovery are never invisible."""

    def __init__(
        self,
        adapter: ProviderAdapter,
        *,
        system: str | CognitiveSystem,
        session_id: str = "",
        record_completions: bool = False,
    ) -> None:
        self._adapter = adapter
        self._system = system
        self._session_id = session_id
        self._record_completions = record_completions

    async def complete(
        self,
        prompt: str,
        model_id: str | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        result = await self._adapter.complete(prompt, model_id=model_id, **kwargs)
        if self._record_completions:
            record_usage_event(
                provider=result.provider,
                model=result.model_id or (model_id or "unknown"),
                system=self._system,
                tokens=int(result.tokens_used),
                request_id=f"completion:{uuid4().hex}",
                session_id=self._session_id,
            )
        return result

    async def stream(
        self,
        prompt: str,
        model_id: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        async for chunk in self._adapter.stream(prompt, model_id=model_id, **kwargs):
            yield chunk

    async def list_models(self) -> list[ModelInfo]:
        try:
            return await self._adapter.list_models()
        finally:
            record_usage_event(
                provider=getattr(self._adapter, "_provider_name", "unknown") or "unknown",
                model="list-models",
                system=self._system,
                tokens=0,
                request_id=f"list-models:{uuid4().hex}",
                session_id=self._session_id,
            )

    async def health_check(self) -> HealthStatus:
        status = await self._adapter.health_check()
        model_name = _safe_string(getattr(self._adapter, "_default_model", ""), "health-check")
        provider_name = _safe_string(
            getattr(status, "provider", ""),
            _safe_string(getattr(self._adapter, "_provider_name", ""), "unknown"),
        )
        tokens_used = int(getattr(status, "tokens_used", 0) or 0)
        record_usage_event(
            provider=provider_name,
            model=str(model_name),
            system=self._system,
            tokens=tokens_used,
            request_id=f"health-check:{uuid4().hex}",
            session_id=self._session_id,
        )
        return status

    def __getattr__(self, name: str) -> Any:
        return getattr(self._adapter, name)


class UsageTrackingCallbackHandler(BaseCallbackHandler):
    """LangChain callback handler that records token usage for observability.

    Replaces ``UsageTrackingProviderAdapter`` — instead of wrapping the
    provider, this is attached as a callback to any ``BaseChatModel``.
    """

    def __init__(
        self,
        *,
        provider: str = "unknown",
        model: str = "unknown",
        system: str | CognitiveSystem = "chat",
        session_id: str = "",
    ) -> None:
        super().__init__()
        self.provider = provider
        self.model = model
        self.system = system
        self.session_id = session_id

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        total_tokens = 0
        if response.llm_output and isinstance(response.llm_output, dict):
            usage = response.llm_output.get("token_usage", {})
            if isinstance(usage, dict):
                total_tokens = int(usage.get("total_tokens", 0))
        if total_tokens > 0:
            try:
                record_usage_event(
                    provider=self.provider,
                    model=self.model,
                    system=self.system,
                    tokens=total_tokens,
                    request_id=f"callback:{uuid4().hex}",
                    session_id=self.session_id,
                )
            except Exception:
                _log.debug("Failed to record usage via callback", exc_info=True)