"""Provider registry — register, resolve, and list provider adapters."""

from __future__ import annotations

from openbad.cognitive.providers.base import ProviderAdapter


class ProviderRegistry:
    """Registry for LLM/SLM provider adapters.

    Providers are identified by name (e.g. ``"ollama"``, ``"openai"``).
    Model IDs use ``<provider>/<model-id>`` notation (e.g. ``"ollama/llama3.3"``).
    """

    def __init__(self) -> None:
        self._providers: dict[str, ProviderAdapter] = {}

    def register(self, name: str, adapter: ProviderAdapter) -> None:
        """Register a provider adapter under *name*."""
        self._providers[name] = adapter

    def get(self, name: str) -> ProviderAdapter | None:
        """Return the adapter for *name*, or ``None``."""
        return self._providers.get(name)

    def list_providers(self) -> list[str]:
        """Return sorted list of registered provider names."""
        return sorted(self._providers)

    def resolve(self, provider_model: str) -> tuple[ProviderAdapter, str]:
        """Parse ``<provider>/<model-id>`` and return ``(adapter, model_id)``.

        Raises :class:`KeyError` if the provider is not registered.
        Raises :class:`ValueError` if the notation is invalid.
        """
        if "/" not in provider_model:
            raise ValueError(
                f"Invalid provider/model notation: {provider_model!r} "
                "(expected 'provider/model-id')"
            )
        provider_name, model_id = provider_model.split("/", 1)
        adapter = self._providers.get(provider_name)
        if adapter is None:
            raise KeyError(f"Provider not registered: {provider_name!r}")
        return adapter, model_id

    def unregister(self, name: str) -> bool:
        """Remove a provider. Return ``True`` if it existed."""
        return self._providers.pop(name, None) is not None
