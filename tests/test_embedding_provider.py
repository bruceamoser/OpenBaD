"""Tests for embedding provider — base.embed(), OllamaProvider.embed(), fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from openbad.cognitive.providers.base import ProviderAdapter
from openbad.cognitive.providers.ollama import OllamaProvider
from openbad.memory.controller import make_ollama_embed_fn
from openbad.memory.semantic import hash_embedding


class TestProviderAdapterEmbed:
    """The base class embed() should raise NotImplementedError."""

    @pytest.mark.asyncio
    async def test_embed_not_implemented(self) -> None:
        # Create a concrete subclass with stubs for the abstract methods
        class StubProvider(ProviderAdapter):
            async def complete(self, prompt, model_id=None, **kw):  # type: ignore[override]
                ...

            async def stream(self, prompt, model_id=None, **kw):  # type: ignore[override]
                yield ""

            async def list_models(self):  # type: ignore[override]
                return []

            async def health_check(self):  # type: ignore[override]
                ...

        provider = StubProvider()
        with pytest.raises(NotImplementedError, match="not supported"):
            await provider.embed(["hello"])


class TestOllamaProviderEmbed:
    """OllamaProvider.embed() should call /api/embed and return embeddings."""

    @pytest.mark.asyncio
    async def test_embed_calls_api(self) -> None:
        provider = OllamaProvider()
        fake_response = {"embeddings": [[0.1] * 768, [0.2] * 768]}
        with patch.object(
            provider, "_post", new_callable=AsyncMock, return_value=fake_response
        ) as mock_post:
            result = await provider.embed(["hello", "world"])
            mock_post.assert_called_once_with(
                "/api/embed",
                {"model": "nomic-embed-text", "input": ["hello", "world"]},
            )
            assert len(result) == 2
            assert len(result[0]) == 768

    @pytest.mark.asyncio
    async def test_embed_custom_model(self) -> None:
        provider = OllamaProvider()
        fake_response = {"embeddings": [[0.5] * 384]}
        with patch.object(
            provider, "_post", new_callable=AsyncMock, return_value=fake_response
        ) as mock_post:
            result = await provider.embed(["test"], model_id="custom-embed")
            mock_post.assert_called_once_with(
                "/api/embed",
                {"model": "custom-embed", "input": ["test"]},
            )
            assert len(result) == 1


class TestMakeOllamaEmbedFn:
    """make_ollama_embed_fn should produce a sync callable with fallback."""

    def test_fallback_to_hash_embedding(self) -> None:
        """When Ollama is unreachable, should fall back to hash_embedding."""
        embed_fn = make_ollama_embed_fn(base_url="http://localhost:99999")
        vec = embed_fn("hello world")
        # Should return 768-dim hash_embedding fallback
        assert len(vec) == 768
        # Should match hash_embedding output
        expected = hash_embedding("hello world", dim=768)
        assert vec == expected

    def test_returns_callable(self) -> None:
        embed_fn = make_ollama_embed_fn()
        assert callable(embed_fn)
