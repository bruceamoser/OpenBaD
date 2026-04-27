"""Provider registry — register, resolve, and list provider models."""

from __future__ import annotations

from typing import Any

from openbad.cognitive.providers.base import ProviderAdapter


class ProviderRegistry:
    """Registry for LLM/SLM providers.

    Providers are identified by ``<provider>/<model-id>`` notation
    (e.g. ``"ollama/llama3.2"``).  Each entry stores a ``(BaseChatModel,
    crewai.LLM)`` pair where the model is baked into the instance.

    Backwards-compatible: legacy ``register(name, adapter)`` calls are
    accepted and stored in a separate dict for code that still uses the
    old ``ProviderAdapter`` interface (e.g. bridge health-checks).
    """

    def __init__(self) -> None:
        self._models: dict[str, tuple[Any, Any]] = {}
        # Legacy adapter storage for backwards-compatible callers.
        self._adapters: dict[str, ProviderAdapter] = {}

    # ── New API (BaseChatModel / crewai.LLM) ────────────────────────

    def register_models(
        self,
        provider_model: str,
        chat_model: Any,
        crew_llm: Any,
    ) -> None:
        """Register a ``(BaseChatModel, crewai.LLM)`` pair.

        *provider_model* uses ``<provider>/<model-id>`` notation.
        """
        self._models[provider_model] = (chat_model, crew_llm)

    def get_models(self, provider_model: str) -> tuple[Any, Any] | None:
        """Return ``(BaseChatModel, crewai.LLM)`` for *provider_model*,
        or ``None`` if not registered."""
        return self._models.get(provider_model)

    def resolve(self, provider_model: str) -> tuple[Any, Any]:
        """Parse ``<provider>/<model-id>`` and return ``(BaseChatModel, crewai.LLM)``.

        Raises :class:`KeyError` if not registered.
        Raises :class:`ValueError` if the notation is invalid.
        """
        if "/" not in provider_model:
            raise ValueError(
                f"Invalid provider/model notation: {provider_model!r} "
                "(expected 'provider/model-id')"
            )
        entry = self._models.get(provider_model)
        if entry is None:
            raise KeyError(f"Model not registered: {provider_model!r}")
        return entry

    # ── Legacy API (ProviderAdapter) ─────────────────────────────────

    def register(self, name: str, adapter: ProviderAdapter) -> None:
        """Register a legacy provider adapter under *name*."""
        self._adapters[name] = adapter

    def get(self, name: str) -> ProviderAdapter | None:
        """Return the legacy adapter for *name*, or ``None``."""
        return self._adapters.get(name)

    # ── Shared helpers ───────────────────────────────────────────────

    def list_providers(self) -> list[str]:
        """Return sorted list of registered provider names."""
        adapter_names = set(self._adapters.keys())
        model_names = {k.split("/", 1)[0] for k in self._models}
        return sorted(adapter_names | model_names)

    def unregister(self, name: str) -> bool:
        """Remove a provider or model entry. Return ``True`` if it existed."""
        removed = self._adapters.pop(name, None) is not None
        # Also remove any model entries for this provider name.
        to_drop = [k for k in self._models if k == name or k.startswith(f"{name}/")]
        for k in to_drop:
            del self._models[k]
            removed = True
        return removed
