"""Web search tool adapter — SearXNG / DuckDuckGo backends.

Registers under ``ToolRole.WEB_SEARCH`` and provides structured search
results with rate limiting and health checks.
"""

from __future__ import annotations

import json as _json
import logging
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A single search result."""

    title: str
    url: str
    snippet: str = ""


@dataclass
class WebSearchConfig:
    """Configuration for the web search adapter."""

    backend: str = "duckduckgo"
    searxng_base_url: str = "http://localhost:8888"
    max_results: int = 5
    max_requests_per_minute: int = 10
    timeout: float = 10.0


class _RateLimiter:
    """Simple sliding-window rate limiter."""

    def __init__(self, max_per_minute: int) -> None:
        self._max = max_per_minute
        self._timestamps: list[float] = []

    def allow(self) -> bool:
        now = time.monotonic()
        cutoff = now - 60.0
        self._timestamps = [t for t in self._timestamps if t > cutoff]
        if len(self._timestamps) >= self._max:
            return False
        self._timestamps.append(now)
        return True


class WebSearchToolAdapter:
    """Search the web via SearXNG or DuckDuckGo.

    Parameters
    ----------
    config:
        Web search configuration.
    http_get:
        Optional HTTP GET function for dependency injection (testing).
        Signature: ``(url, timeout) -> bytes``.
    """

    def __init__(
        self,
        config: WebSearchConfig | None = None,
        http_get: object | None = None,
    ) -> None:
        self._config = config or WebSearchConfig()
        self._limiter = _RateLimiter(self._config.max_requests_per_minute)
        self._http_get = http_get or self._default_http_get

    @property
    def config(self) -> WebSearchConfig:
        return self._config

    def search(self, query: str) -> list[SearchResult]:
        """Execute a search query.

        Returns a list of :class:`SearchResult` objects, or an empty list
        if rate-limited or on error.
        """
        if not self._limiter.allow():
            logger.warning("Web search rate-limited")
            return []

        if self._config.backend == "searxng":
            return self._search_searxng(query)
        return self._search_duckduckgo(query)

    def health_check(self) -> bool:
        """Verify backend reachability."""
        try:
            if self._config.backend == "searxng":
                url = self._config.searxng_base_url.rstrip("/") + "/healthz"
            else:
                url = "https://html.duckduckgo.com/"
            self._http_get(url, self._config.timeout)
            return True
        except Exception:
            logger.debug("Health check failed for %s", self._config.backend)
            return False

    def _search_searxng(self, query: str) -> list[SearchResult]:
        base = self._config.searxng_base_url.rstrip("/")
        params = urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "categories": "general",
        })
        url = f"{base}/search?{params}"
        try:
            data = self._http_get(url, self._config.timeout)
            resp = _json.loads(data)
            results = []
            for item in resp.get("results", [])[:self._config.max_results]:
                results.append(SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", ""),
                ))
            return results
        except Exception:
            logger.exception("SearXNG search failed")
            return []

    def _search_duckduckgo(self, query: str) -> list[SearchResult]:
        params = urllib.parse.urlencode({"q": query})
        url = f"https://html.duckduckgo.com/html/?{params}"
        try:
            data = self._http_get(url, self._config.timeout)
            return self._parse_ddg_html(data.decode("utf-8", errors="replace"))
        except Exception:
            logger.exception("DuckDuckGo search failed")
            return []

    def _parse_ddg_html(self, html: str) -> list[SearchResult]:
        """Minimal HTML parsing for DDG results."""
        results: list[SearchResult] = []
        # DDG HTML results are in <a class="result__a" ...> tags
        marker = 'class="result__a"'
        pos = 0
        while len(results) < self._config.max_results:
            idx = html.find(marker, pos)
            if idx == -1:
                break
            # Extract href
            href_start = html.rfind('href="', max(0, idx - 200), idx)
            if href_start == -1:
                pos = idx + len(marker)
                continue
            href_start += 6
            href_end = html.find('"', href_start)
            href = html[href_start:href_end]

            # Extract title (text between > and </a>)
            tag_end = html.find(">", idx)
            close_a = html.find("</a>", tag_end)
            title = html[tag_end + 1:close_a].strip() if close_a > tag_end else ""
            # Strip HTML tags from title
            import re

            title = re.sub(r"<[^>]+>", "", title)

            # Extract snippet
            snippet_marker = 'class="result__snippet"'
            snip_idx = html.find(snippet_marker, close_a if close_a > 0 else idx)
            snippet = ""
            if snip_idx != -1:
                snip_start = html.find(">", snip_idx) + 1
                snip_end = html.find("</", snip_start)
                if snip_end > snip_start:
                    snippet = re.sub(r"<[^>]+>", "", html[snip_start:snip_end]).strip()

            if href and title:
                results.append(SearchResult(title=title, url=href, snippet=snippet))
            pos = close_a + 4 if close_a > 0 else idx + len(marker)
        return results

    @staticmethod
    def _default_http_get(url: str, timeout: float) -> bytes:
        req = urllib.request.Request(  # noqa: S310
            url,
            headers={"User-Agent": "OpenBaD/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read()
