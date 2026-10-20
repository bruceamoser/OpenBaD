"""Chrome DevTools Protocol (CDP) DOM extractor.

Connects to a Chromium-based browser's remote debugging port to extract
the live DOM tree, avoiding expensive pixel-level analysis for web
content.

Runtime requirements:
  - A Chromium browser running with ``--remote-debugging-port=<port>``
  - ``aiohttp`` (already a project dependency) for WebSocket transport

The extracted DOM is serialised as a JSON widget tree and published as
a ``ParsedScreen`` protobuf message on
``agent/sensory/vision/{source_id}/parsed``.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from openbad.nervous_system.schemas import Header, ParsedScreen
from openbad.nervous_system.schemas.sensory_pb2 import ParseMethod
from openbad.nervous_system.topics import SENSORY_VISION_PARSED, topic_for

logger = logging.getLogger(__name__)

DEFAULT_CDP_URL = "http://localhost:9222"


# ---------------------------------------------------------------------------
# DOM node model
# ---------------------------------------------------------------------------


@dataclass
class DOMNode:
    """A simplified DOM node extracted via CDP."""

    tag: str
    node_id: int = 0
    attributes: dict[str, str] = field(default_factory=dict)
    text: str = ""
    children: list[DOMNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict."""
        d: dict[str, Any] = {"tag": self.tag}
        if self.node_id:
            d["nodeId"] = self.node_id
        if self.attributes:
            d["attributes"] = self.attributes
        if self.text:
            d["text"] = self.text
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d

    def node_count(self) -> int:
        """Total number of nodes in this subtree (including self)."""
        return 1 + sum(c.node_count() for c in self.children)


def dom_to_json(root: DOMNode) -> str:
    """Serialise a DOM tree to compact JSON."""
    return json.dumps(root.to_dict(), separators=(",", ":"))


# ---------------------------------------------------------------------------
# CDP node parsing
# ---------------------------------------------------------------------------


def _parse_cdp_node(node: dict[str, Any]) -> DOMNode:
    """Parse a CDP ``DOM.Node`` dict into a ``DOMNode``.

    CDP returns attributes as a flat list ``[key, value, key, value, ...]``.
    """
    tag = node.get("localName") or node.get("nodeName", "#unknown")
    node_id = node.get("nodeId", 0)

    # Parse flat attribute list
    raw_attrs = node.get("attributes", [])
    attrs: dict[str, str] = {}
    for i in range(0, len(raw_attrs) - 1, 2):
        attrs[raw_attrs[i]] = raw_attrs[i + 1]

    # Text content for text nodes (#text)
    text = node.get("nodeValue", "") or ""
    if node.get("nodeType") == 3:  # TEXT_NODE
        tag = "#text"

    children: list[DOMNode] = []
    for child in node.get("children", []):
        children.append(_parse_cdp_node(child))

    return DOMNode(
        tag=tag,
        node_id=node_id,
        attributes=attrs,
        text=text.strip(),
        children=children,
    )


# ---------------------------------------------------------------------------
# CDP extractor
# ---------------------------------------------------------------------------


class CDPExtractor:
    """Extract the DOM tree from a Chromium browser via CDP.

    Parameters
    ----------
    cdp_url : str
        Base URL for the CDP HTTP endpoint (e.g. ``http://localhost:9222``).
    publish_fn : callable | None
        Optional async callback ``(topic, payload) -> None``.
    """

    def __init__(
        self,
        cdp_url: str = DEFAULT_CDP_URL,
        publish_fn: Any | None = None,
    ) -> None:
        self._cdp_url = cdp_url.rstrip("/")
        self._publish = publish_fn
        self._ws_url: str | None = None
        self._msg_id = 0

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    # -- Connection ----------------------------------------------------------

    async def _get_ws_url(self) -> str:
        """Discover the WebSocket debugger URL from the CDP HTTP endpoint."""
        import aiohttp

        url = f"{self._cdp_url}/json/version"
        async with aiohttp.ClientSession() as session, session.get(url) as resp:
            if resp.status != 200:
                msg = f"CDP endpoint returned {resp.status}"
                raise RuntimeError(msg)
            data = await resp.json()
            return data["webSocketDebuggerUrl"]

    async def _get_page_ws_url(self, page_index: int = 0) -> str:
        """Get the WebSocket URL of a specific page/tab."""
        import aiohttp

        url = f"{self._cdp_url}/json"
        async with aiohttp.ClientSession() as session, session.get(url) as resp:
            if resp.status != 200:
                msg = f"CDP endpoint returned {resp.status}"
                raise RuntimeError(msg)
            pages = await resp.json()
            pages = [p for p in pages if p.get("type") == "page"]
            if page_index >= len(pages):
                msg = f"Page index {page_index} out of range (have {len(pages)} pages)"
                raise IndexError(msg)
            return pages[page_index]["webSocketDebuggerUrl"]

    async def _send_command(
        self,
        ws: Any,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a CDP command and wait for the result."""
        msg_id = self._next_id()
        payload = {"id": msg_id, "method": method}
        if params:
            payload["params"] = params
        await ws.send_json(payload)

        async for msg in ws:
            if msg.type == msg.type:  # always true — iterate over messages
                data = json.loads(msg.data)
                if data.get("id") == msg_id:
                    if "error" in data:
                        msg_text = data["error"].get("message", "Unknown CDP error")
                        raise RuntimeError(msg_text)
                    return data.get("result", {})
        msg = "WebSocket closed before receiving response"
        raise RuntimeError(msg)

    # -- DOM extraction ------------------------------------------------------

    async def extract_dom(self, page_index: int = 0) -> DOMNode:
        """Extract the DOM tree from a browser tab.

        Parameters
        ----------
        page_index : int
            Zero-based index of the tab to extract from.

        Returns
        -------
        DOMNode
            The root of the parsed DOM tree.
        """
        import aiohttp

        ws_url = await self._get_page_ws_url(page_index)

        async with aiohttp.ClientSession() as session, session.ws_connect(ws_url) as ws:
            result = await self._send_command(ws, "DOM.getDocument", {"depth": -1})
            root_node = result.get("root", {})
            return _parse_cdp_node(root_node)

    async def extract_and_publish(
        self,
        source_id: str,
        page_index: int = 0,
    ) -> ParsedScreen:
        """Extract the DOM and optionally publish via the event bus.

        Returns the ``ParsedScreen`` protobuf regardless of whether
        a publisher is configured.
        """
        start = time.perf_counter()
        root = await self.extract_dom(page_index)
        elapsed_ms = (time.perf_counter() - start) * 1000

        tree_json = dom_to_json(root)
        count = root.node_count()

        proto = ParsedScreen(
            header=Header(
                timestamp_unix=time.time(),
                source_module="sensory.vision.cdp_dom",
                schema_version=1,
            ),
            source_id=source_id,
            method=ParseMethod.CDP_DOM,
            tree_json=tree_json,
            node_count=count,
            extraction_ms=elapsed_ms,
        )

        if self._publish is not None:
            topic = topic_for(SENSORY_VISION_PARSED, source_id=source_id)
            await self._publish(topic, proto.SerializeToString())

        return proto
