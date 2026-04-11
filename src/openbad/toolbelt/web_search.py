"""Web search tool adapter — SearXNG / DuckDuckGo backends.

Registers under ``ToolRole.WEB_SEARCH`` and provides structured search
results with rate limiting and health checks.

Also exposes :func:`web_fetch` for downloading and cleaning a single URL
into sanitised plain text or minimal Markdown.
"""

from __future__ import annotations

import html as _html_mod
import json as _json
import logging
import re
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


# ---------------------------------------------------------------------------
# web_fetch
# ---------------------------------------------------------------------------

_MAX_FETCH_BYTES = 2 * 1024 * 1024  # 2 MB hard cap

# Tags whose inner content is stripped completely (not converted)
_DROP_TAGS_RE = re.compile(
    r"<(script|style|noscript|head|header|footer|nav|aside)[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


class WebFetchError(OSError):
    """Raised when :func:`web_fetch` cannot retrieve or decode the resource.

    Attributes
    ----------
    status_code:
        HTTP status code if available, otherwise 0.
    """

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


def web_fetch(
    url: str,
    *,
    timeout: float = 15.0,
    max_chars: int = 32_000,
) -> str:
    """Fetch *url* and return its content as cleaned plain text.

    Parameters
    ----------
    url:
        The HTTP/HTTPS URL to fetch.  Other schemes are rejected.
    timeout:
        Request timeout in seconds (default 15).
    max_chars:
        Maximum characters returned after cleaning (default 32 000).

    Returns
    -------
    str
        Cleaned text extracted from the response body, truncated to
        *max_chars* characters.

    Raises
    ------
    ValueError
        If *url* does not use http or https scheme.
    WebFetchError
        On HTTP errors (4xx, 5xx), network errors, or decode failures.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http/https URLs are supported, got: {url!r}")

    req = urllib.request.Request(url, headers={"User-Agent": "OpenBaD/1.0"})  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw: bytes = resp.read(_MAX_FETCH_BYTES)
            content_type: str = resp.headers.get_content_type() or ""
    except urllib.error.HTTPError as exc:
        raise WebFetchError(
            f"HTTP {exc.code} fetching {url!r}: {exc.reason}", status_code=exc.code
        ) from exc
    except Exception as exc:
        raise WebFetchError(f"Failed to fetch {url!r}: {exc}") from exc

    # Decode bytes
    try:
        charset = "utf-8"
        if "charset=" in content_type:
            charset = content_type.split("charset=")[-1].strip()
        text = raw.decode(charset, errors="replace")
    except Exception as exc:
        raise WebFetchError(f"Could not decode response from {url!r}: {exc}") from exc

    # Clean HTML if applicable
    if "html" in content_type:
        text = _clean_html(text)

    return text[:max_chars]


def _clean_html(html: str) -> str:
    """Strip HTML markup, decode entities, and normalise whitespace."""
    html = _DROP_TAGS_RE.sub("", html)
    html = _TAG_RE.sub("\n", html)
    html = _html_mod.unescape(html)
    # Collapse whitespace
    lines = [line.strip() for line in html.splitlines()]
    text = "\n".join(line for line in lines if line)
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    return text.strip()


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


# ---------------------------------------------------------------------------
# Research escalation bridge (#413)
# ---------------------------------------------------------------------------


#: HTTP status codes that trigger a research escalation instead of failure.
ESCALATION_STATUS_CODES: frozenset[int] = frozenset({403, 404})


@dataclass
class FetchOutcome:
    """Result of a :class:`WebFetchEscalator` guarded fetch.

    Attributes
    ----------
    content:
        Cleaned text content, or ``None`` if the fetch was escalated.
    escalated:
        ``True`` when the error was converted into a research escalation.
    error:
        The underlying :class:`WebFetchError` that caused escalation, or
        ``None`` on success.
    """

    content: str | None
    escalated: bool = False
    error: WebFetchError | None = None


class WebFetchEscalator:
    """Wraps :func:`web_fetch` so that recoverable HTTP errors trigger a
    :class:`~openbad.tasks.research_queue.ResearchNode` instead of failing.

    On HTTP 403, 404, or request timeout the current fetch is suspended
    (``FetchOutcome.escalated = True``) and a research node is pushed to the
    provided queue so the agent can investigate the broken resource later.

    Parameters
    ----------
    research_queue:
        A :class:`~openbad.tasks.research_queue.ResearchQueue` instance.
    escalation_priority:
        Priority for escalated research nodes (lower = urgency, default -5).
    """

    def __init__(
        self,
        research_queue: object,  # ResearchQueue — not imported at top level to avoid cycle
        *,
        escalation_priority: int = -5,
    ) -> None:
        self._queue = research_queue
        self._priority = escalation_priority

    def fetch(
        self,
        url: str,
        *,
        source_task_id: str | None = None,
        timeout: float = 15.0,
        max_chars: int = 50_000,
    ) -> FetchOutcome:
        """Fetch *url* and escalate to research on recoverable error.

        Parameters
        ----------
        url:
            The URL to fetch.
        source_task_id:
            Optional task node ID used to link the research node.
        timeout:
            Request timeout forwarded to :func:`web_fetch`.
        max_chars:
            Maximum characters, forwarded to :func:`web_fetch`.

        Returns
        -------
        FetchOutcome
            ``content`` is set on success; ``escalated=True`` and ``error`` is
            set when the fetch was suspended for research.
        """
        try:
            text = web_fetch(url, timeout=timeout, max_chars=max_chars)
            return FetchOutcome(content=text)
        except OSError as exc:
            # Detect timeout by checking for TimeoutError / ConnectionError subtype
            # or WebFetchError with status_code == 0.
            is_escalatable = False
            error_type = "timeout"
            if isinstance(exc, WebFetchError):
                if exc.status_code in ESCALATION_STATUS_CODES:
                    is_escalatable = True
                    error_type = f"http_{exc.status_code}"
                elif exc.status_code == 0:
                    is_escalatable = True  # timeout / connection error
            else:
                is_escalatable = True  # generic OS/timeout error

            if not is_escalatable:
                raise

            self._push_research(url, source_task_id, error_type, exc)
            return FetchOutcome(content=None, escalated=True, error=exc)  # type: ignore[arg-type]

    def _push_research(
        self,
        url: str,
        source_task_id: str | None,
        error_type: str,
        exc: OSError,
    ) -> None:
        """Enqueue a research node for the broken URL."""
        title = f"Investigate broken resource: {url}"
        description = (
            f"web_fetch failed with {error_type!r} for URL {url!r}.\n"
            f"Error: {exc}\n"
            f"Source task: {source_task_id or 'unknown'}\n"
            "Goal: determine whether the resource has moved, is restricted, or "
            "can be replaced by an alternative source."
        )
        try:
            self._queue.enqueue(
                title,
                priority=self._priority,
                description=description,
                source_task_id=source_task_id,
            )
        except Exception:
            logging.getLogger(__name__).debug(
                "WebFetchEscalator: could not enqueue research node", exc_info=True
            )
