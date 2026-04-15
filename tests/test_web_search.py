"""Tests for web search tool adapter — Issue #237."""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from openbad.proprioception.registry import ToolRegistry, ToolRole
from openbad.skills.web_search import (
    WebFetchError,
    WebFetchEscalator,
    WebSearchConfig,
    WebSearchToolAdapter,
    _RateLimiter,
    web_fetch,
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


# ---------------------------------------------------------------------------
# Phase 10: web_fetch (#412)
# ---------------------------------------------------------------------------


class TestWebFetch:
    def _fake_resp(self, body: bytes, content_type: str = "text/html; charset=utf-8"):
        resp = MagicMock()
        resp.read.return_value = body
        resp.headers.get_content_type.return_value = content_type
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_plain_text_returned(self) -> None:
        body = b"Hello world"
        with patch("urllib.request.urlopen", return_value=self._fake_resp(body, "text/plain")):
            result = web_fetch("https://example.com/page")
        assert "Hello world" in result

    def test_html_tags_stripped(self) -> None:
        body = b"<html><body><p>Hello <b>world</b></p></body></html>"
        with patch("urllib.request.urlopen", return_value=self._fake_resp(body)):
            result = web_fetch("https://example.com/page")
        assert "Hello" in result
        assert "<b>" not in result

    def test_script_content_removed(self) -> None:
        body = b"<html><head><script>alert('evil')</script></head><body>ok</body></html>"
        with patch("urllib.request.urlopen", return_value=self._fake_resp(body)):
            result = web_fetch("https://example.com/page")
        assert "alert" not in result
        assert "ok" in result

    def test_max_chars_truncated(self) -> None:
        body = b"<html><body>" + b"A" * 10000 + b"</body></html>"
        with patch("urllib.request.urlopen", return_value=self._fake_resp(body)):
            result = web_fetch("https://example.com/page", max_chars=100)
        assert len(result) <= 100

    def test_http_error_raises_web_fetch_error(self) -> None:
        err = urllib.error.HTTPError(
            url="https://example.com/404",
            code=404,
            msg="Not Found",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,  # type: ignore[arg-type]
        )
        with (
            patch("urllib.request.urlopen", side_effect=err),
            pytest.raises(WebFetchError) as exc_info,
        ):
            web_fetch("https://example.com/404")
        assert exc_info.value.status_code == 404

    def test_network_error_raises_web_fetch_error(self) -> None:
        with (
            patch("urllib.request.urlopen", side_effect=OSError("timeout")),
            pytest.raises(WebFetchError),
        ):
            web_fetch("https://example.com/page")

    def test_non_http_scheme_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Only http/https"):
            web_fetch("ftp://example.com/file")

    def test_file_scheme_rejected(self) -> None:
        with pytest.raises(ValueError):
            web_fetch("file:///etc/passwd")


# ---------------------------------------------------------------------------
# Phase 10: research escalation bridge (#413)
# ---------------------------------------------------------------------------


def _mock_queue() -> MagicMock:
    q = MagicMock()
    q.enqueue.return_value = MagicMock(node_id="rn-001")
    return q


class TestWebFetchEscalator:
    def test_successful_fetch_returns_content(self) -> None:
        queue = _mock_queue()
        escalator = WebFetchEscalator(queue)
        with patch("openbad.skills.web_search.web_fetch", return_value="page text"):
            outcome = escalator.fetch("https://example.com")
        assert outcome.content == "page text"
        assert outcome.escalated is False
        queue.enqueue.assert_not_called()

    def test_http_404_escalates(self) -> None:
        queue = _mock_queue()
        escalator = WebFetchEscalator(queue)
        err = WebFetchError("Not Found", status_code=404)
        with patch("openbad.skills.web_search.web_fetch", side_effect=err):
            outcome = escalator.fetch("https://example.com/gone", source_task_id="t1")
        assert outcome.content is None
        assert outcome.escalated is True
        assert outcome.error is err
        queue.enqueue.assert_called_once()
        call_kw = queue.enqueue.call_args[1]
        assert call_kw["source_task_id"] == "t1"
        assert "404" in call_kw["description"] or "http_404" in call_kw["description"]

    def test_http_403_escalates(self) -> None:
        queue = _mock_queue()
        escalator = WebFetchEscalator(queue)
        err = WebFetchError("Forbidden", status_code=403)
        with patch("openbad.skills.web_search.web_fetch", side_effect=err):
            outcome = escalator.fetch("https://example.com/secret")
        assert outcome.escalated is True
        queue.enqueue.assert_called_once()

    def test_timeout_escalates(self) -> None:
        queue = _mock_queue()
        escalator = WebFetchEscalator(queue)
        err = WebFetchError("timed out", status_code=0)
        with patch("openbad.skills.web_search.web_fetch", side_effect=err):
            outcome = escalator.fetch("https://slow.example.com")
        assert outcome.escalated is True
        queue.enqueue.assert_called_once()

    def test_research_node_includes_url_in_title(self) -> None:
        queue = _mock_queue()
        escalator = WebFetchEscalator(queue)
        err = WebFetchError("Not Found", status_code=404)
        url = "https://example.com/missing"
        with patch("openbad.skills.web_search.web_fetch", side_effect=err):
            escalator.fetch(url)
        title_arg = queue.enqueue.call_args[0][0]
        assert url in title_arg

    def test_queue_failure_does_not_raise(self) -> None:
        queue = _mock_queue()
        queue.enqueue.side_effect = RuntimeError("db down")
        escalator = WebFetchEscalator(queue)
        err = WebFetchError("Not Found", status_code=404)
        with patch("openbad.skills.web_search.web_fetch", side_effect=err):
            outcome = escalator.fetch("https://example.com/gone")
        assert outcome.escalated is True  # still escalated, but queue silently failed

    def test_non_escalatable_http_error_reraises(self) -> None:
        queue = _mock_queue()
        escalator = WebFetchEscalator(queue)
        err = WebFetchError("Internal Server Error", status_code=500)
        with (
            patch("openbad.skills.web_search.web_fetch", side_effect=err),
            pytest.raises(WebFetchError),
        ):
            escalator.fetch("https://example.com/fail")
        queue.enqueue.assert_not_called()
