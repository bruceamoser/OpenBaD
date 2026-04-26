"""Transient MCP bridge for browser sessions — Phase 10, Issue #418.

Wraps :class:`~openbad.sensory.vision.cdp_dom.CDPExtractor` into a
task-node–scoped context.  The browser session is started on demand when a
:class:`~openbad.tasks.models.NodeModel` is tagged with the ``browser``
capability, and is fully torn down when the node completes (success or
failure).  Sessions are *never* shared across task nodes.

Usage::

    async with BrowserContextRunner.for_node(node) as ctx:
        dom = await ctx.snapshot_dom()
        text = await ctx.navigate("https://example.com")
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from openbad.sensory.vision.cdp_dom import CDPExtractor, DOMNode, dom_to_json

if TYPE_CHECKING:
    from openbad.tasks.models import NodeModel

log = logging.getLogger(__name__)

#: Capability tag that must appear in ``node.capability_requirements`` for a
#: browser session to be provisioned.
BROWSER_CAPABILITY = "browser"

#: Default CDP endpoint used when no explicit URL is supplied.
DEFAULT_CDP_URL = "http://localhost:9222"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class NavigateResult:
    """Outcome of a :meth:`BrowserContextRunner.navigate` call."""

    url: str
    dom_json: str
    node_count: int
    error: str | None = None


@dataclass
class BrowserCapabilities:
    """Describes what the current browser session can do."""

    cdp_url: str
    features: list[str] = field(default_factory=lambda: ["dom_snapshot", "navigate"])


# ---------------------------------------------------------------------------
# Core class
# ---------------------------------------------------------------------------


class BrowserContextRunner:
    """Task-node–scoped browser session backed by a :class:`CDPExtractor`.

    Lifecycle:
    1. :meth:`start` — verifies the CDP endpoint is reachable.
    2. :meth:`snapshot_dom` / :meth:`navigate` — browser operations.
    3. :meth:`stop` — releases all resources regardless of outcome.

    Use :meth:`for_node` to gate activation on the ``browser`` capability tag.

    Parameters
    ----------
    cdp_url:
        Base URL for the CDP HTTP endpoint.
    node_id:
        ID of the task node that owns this session (for logging only).
    """

    def __init__(
        self,
        cdp_url: str = DEFAULT_CDP_URL,
        *,
        node_id: str | None = None,
    ) -> None:
        self._cdp_url = cdp_url
        self._node_id = node_id or "unknown"
        self._extractor: CDPExtractor | None = None
        self._active = False

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def for_node(
        cls,
        node: NodeModel,
        cdp_url: str = DEFAULT_CDP_URL,
    ) -> BrowserContextRunner | None:
        """Return a :class:`BrowserContextRunner` if *node* requires ``browser``.

        Returns ``None`` (not a context manager) when the node does not have
        the ``browser`` capability tag so callers can check before entering.
        """
        if not _node_needs_browser(node):
            return None
        return cls(cdp_url=cdp_url, node_id=node.node_id)

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> BrowserContextRunner:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Initialise the :class:`CDPExtractor` for this session."""
        if self._active:
            return
        self._extractor = CDPExtractor(cdp_url=self._cdp_url)
        self._active = True
        log.info("BrowserContextRunner: started for node %s", self._node_id)

    async def stop(self) -> None:
        """Release all browser resources for this session."""
        self._extractor = None
        self._active = False
        log.info("BrowserContextRunner: stopped for node %s", self._node_id)

    # ------------------------------------------------------------------
    # Browser operations
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """``True`` while the session is live."""
        return self._active

    def capabilities(self) -> BrowserCapabilities:
        """Return the capabilities of the current session."""
        return BrowserCapabilities(cdp_url=self._cdp_url)

    async def snapshot_dom(self, *, page_index: int = 0) -> DOMNode:
        """Extract the current DOM tree.

        Returns
        -------
        DOMNode
            Root of the parsed DOM tree.

        Raises
        ------
        RuntimeError
            If the session has not been started.
        """
        self._require_active()
        assert self._extractor is not None  # type narrowing
        return await self._extractor.extract_dom(page_index=page_index)

    async def snapshot_dom_json(self, *, page_index: int = 0) -> str:
        """Extract the DOM tree and return it serialised as JSON."""
        root = await self.snapshot_dom(page_index=page_index)
        return dom_to_json(root)

    async def navigate(
        self,
        url: str,
        *,
        page_index: int = 0,
    ) -> NavigateResult:
        """Navigate to *url* and return a DOM snapshot.

        The CDP protocol does not expose a single "navigate" command through
        :class:`CDPExtractor`; we use ``Page.navigate`` via a raw CDP command
        and then snapshot the DOM.

        Parameters
        ----------
        url:
            The URL to navigate to.
        page_index:
            Page/tab index (zero-based).
        """
        self._require_active()
        assert self._extractor is not None  # type narrowing

        try:
            ws_url = await self._extractor._get_page_ws_url(page_index)  # noqa: SLF001
        except Exception as exc:
            log.warning("BrowserContextRunner: could not get WS URL: %s", exc)
            return NavigateResult(url=url, dom_json="{}", node_count=0, error=str(exc))

        try:
            import aiohttp

            async with (
                aiohttp.ClientSession() as session,
                session.ws_connect(ws_url) as ws,
            ):
                await self._extractor._send_command(  # noqa: SLF001
                    ws, "Page.navigate", {"url": url}
                )
                # Wait briefly for the page to settle, then extract DOM.
                await asyncio.sleep(0.5)
                root = await self._extractor.extract_dom(page_index=page_index)
        except Exception as exc:
            log.warning("BrowserContextRunner: navigate to %r failed: %s", url, exc)
            return NavigateResult(url=url, dom_json="{}", node_count=0, error=str(exc))

        dom_json = dom_to_json(root)
        return NavigateResult(url=url, dom_json=dom_json, node_count=root.node_count())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_active(self) -> None:
        if not self._active or self._extractor is None:
            msg = "BrowserContextRunner has not been started (call start() first)"
            raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node_needs_browser(node: NodeModel) -> bool:
    """Return ``True`` when *node* declares the ``browser`` capability."""
    reqs = getattr(node, "capability_requirements", None)
    if reqs is None:
        return False
    # capability_requirements may be a list, set, or comma-separated string.
    if isinstance(reqs, str):
        return BROWSER_CAPABILITY in {r.strip() for r in reqs.split(",")}
    return BROWSER_CAPABILITY in reqs


@asynccontextmanager
async def browser_session_for_node(
    node: NodeModel,
    cdp_url: str = DEFAULT_CDP_URL,
) -> AsyncIterator[BrowserContextRunner | None]:
    """Async context manager that yields an active :class:`BrowserContextRunner`
    when *node* requires the ``browser`` capability, or ``None`` otherwise.

    Example::

        async with browser_session_for_node(node) as browser:
            if browser is not None:
                dom = await browser.snapshot_dom()
    """
    runner = BrowserContextRunner.for_node(node, cdp_url=cdp_url)
    if runner is None:
        yield None
        return
    async with runner:
        yield runner
