"""Tests for web search tool adapter — Issue #237."""

from __future__ import annotations

import json

from openbad.proprioception.registry import ToolRegistry, ToolRole
from openbad.toolbelt.web_search import (
    WebSearchConfig,
    WebSearchToolAdapter,
    _RateLimiter,
)

# ── Helpers ────────────────────────────────────────────────────────── #


def _mock_searxng_response(results: list[dict]) -> bytes:
    return json.dumps({"results": results}).encode()


def _mock_ddg_html(results: list[tuple[str, str, str]]) -> bytes:
    """Build minimal DDG-like HTML for testing."""
    html = "<html><body>"
    for title, url, snippet in results:
        html += (
            f'<a href="{url}" class="result__a">{title}</a>'
            f'<span class="result__snippet">{snippet}</span>'
        )
    html += "</body></html>"
    return html.encode()


# ── SearXNG backend ───────────────────────────────────────────────── #


class TestSearXNG:
    def test_search_returns_results(self) -> None:
        raw = _mock_searxng_response([
            {"title": "Foo", "url": "https://example.com/foo", "content": "About foo"},
            {"title": "Bar", "url": "https://example.com/bar", "content": "About bar"},
        ])

        def mock_get(url: str, timeout: float) -> bytes:
            return raw

        cfg = WebSearchConfig(backend="searxng", searxng_base_url="http://test:8888")
        adapter = WebSearchToolAdapter(cfg, http_get=mock_get)
        results = adapter.search("test")
        assert len(results) == 2
        assert results[0].title == "Foo"
        assert results[1].url == "https://example.com/bar"

    def test_searxng_respects_max_results(self) -> None:
        items = [{"title": f"R{i}", "url": f"https://x.com/{i}", "content": ""} for i in range(20)]
        raw = _mock_searxng_response(items)

        cfg = WebSearchConfig(backend="searxng", max_results=3)
        adapter = WebSearchToolAdapter(cfg, http_get=lambda u, t: raw)
        results = adapter.search("q")
        assert len(results) == 3

    def test_searxng_error_returns_empty(self) -> None:
        def fail_get(url: str, timeout: float) -> bytes:
            msg = "network error"
            raise ConnectionError(msg)

        cfg = WebSearchConfig(backend="searxng")
        adapter = WebSearchToolAdapter(cfg, http_get=fail_get)
        assert adapter.search("q") == []


# ── DuckDuckGo backend ────────────────────────────────────────────── #


class TestDuckDuckGo:
    def test_search_returns_results(self) -> None:
        raw = _mock_ddg_html([
            ("Python", "https://python.org", "Programming language"),
        ])

        cfg = WebSearchConfig(backend="duckduckgo")
        adapter = WebSearchToolAdapter(cfg, http_get=lambda u, t: raw)
        results = adapter.search("python")
        assert len(results) >= 1
        assert results[0].title == "Python"
        assert results[0].url == "https://python.org"

    def test_ddg_error_returns_empty(self) -> None:
        def fail_get(url: str, timeout: float) -> bytes:
            raise OSError("fail")

        cfg = WebSearchConfig(backend="duckduckgo")
        adapter = WebSearchToolAdapter(cfg, http_get=fail_get)
        assert adapter.search("q") == []


# ── Rate limiter ──────────────────────────────────────────────────── #


class TestRateLimiter:
    def test_allows_under_limit(self) -> None:
        limiter = _RateLimiter(max_per_minute=5)
        for _ in range(5):
            assert limiter.allow()

    def test_blocks_over_limit(self) -> None:
        limiter = _RateLimiter(max_per_minute=2)
        assert limiter.allow()
        assert limiter.allow()
        assert not limiter.allow()

    def test_rate_limited_search_returns_empty(self) -> None:
        cfg = WebSearchConfig(max_requests_per_minute=1)
        adapter = WebSearchToolAdapter(cfg, http_get=lambda u, t: b'{"results": []}')
        adapter.search("a")
        assert adapter.search("b") == []


# ── Health check ──────────────────────────────────────────────────── #


class TestHealthCheck:
    def test_healthy_searxng(self) -> None:
        cfg = WebSearchConfig(backend="searxng", searxng_base_url="http://test:8888")
        adapter = WebSearchToolAdapter(cfg, http_get=lambda u, t: b"ok")
        assert adapter.health_check()

    def test_unhealthy_on_error(self) -> None:
        def fail(url: str, timeout: float) -> bytes:
            msg = "fail"
            raise ConnectionError(msg)

        cfg = WebSearchConfig(backend="duckduckgo")
        adapter = WebSearchToolAdapter(cfg, http_get=fail)
        assert not adapter.health_check()


# ── Registration ──────────────────────────────────────────────────── #


class TestRegistration:
    def test_register_as_web_search_role(self) -> None:
        reg = ToolRegistry()
        reg.register("web-search", role=ToolRole.WEB_SEARCH)
        reg.equip(ToolRole.WEB_SEARCH, "web-search")
        assert reg.get_belt()[ToolRole.WEB_SEARCH].name == "web-search"
