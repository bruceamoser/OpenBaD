"""AT-SPI2 accessibility tree extractor.

Connects to the Linux accessibility bus (AT-SPI2) via D-Bus to extract
the widget tree of a target application.  The extracted tree is
serialised as JSON and published as a ``ParsedScreen`` protobuf message
on ``agent/sensory/vision/{source_id}/parsed``.

Runtime requirements (Linux only):
  - AT-SPI2 enabled in the desktop session
  - ``dbus-next`` (MIT) Python package
  - Target application exposing an AT-SPI2 accessible hierarchy

On unsupported platforms the module exposes no-op helpers and raises a
clear ``RuntimeError`` at connection time.
"""

from __future__ import annotations

import json
import logging
import platform
import time
from dataclasses import dataclass, field
from typing import Any

from openbad.nervous_system.schemas import Header, ParsedScreen
from openbad.nervous_system.schemas.sensory_pb2 import ParseMethod
from openbad.nervous_system.topics import SENSORY_VISION_PARSED, topic_for

logger = logging.getLogger(__name__)

_IS_LINUX = platform.system() == "Linux"

# AT-SPI2 D-Bus constants
ATSPI_BUS_NAME = "org.a11y.Bus"
ATSPI_REGISTRY_PATH = "/org/a11y/atspi/accessible/root"
ATSPI_ACCESSIBLE_IFACE = "org.a11y.atspi.Accessible"


# ---------------------------------------------------------------------------
# Extracted node model
# ---------------------------------------------------------------------------


@dataclass
class AccessibleNode:
    """One node in an AT-SPI2 accessibility tree."""

    role: str
    name: str
    description: str = ""
    states: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    value: str = ""
    bounds: tuple[int, int, int, int] | None = None  # x, y, w, h
    children: list[AccessibleNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict."""
        d: dict[str, Any] = {"role": self.role, "name": self.name}
        if self.description:
            d["description"] = self.description
        if self.states:
            d["states"] = self.states
        if self.actions:
            d["actions"] = self.actions
        if self.value:
            d["value"] = self.value
        if self.bounds is not None:
            d["bounds"] = {"x": self.bounds[0], "y": self.bounds[1],
                           "w": self.bounds[2], "h": self.bounds[3]}
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d

    def node_count(self) -> int:
        """Return total number of nodes in this subtree (including self)."""
        return 1 + sum(c.node_count() for c in self.children)


def tree_to_json(root: AccessibleNode) -> str:
    """Serialise an accessibility tree to compact JSON."""
    return json.dumps(root.to_dict(), separators=(",", ":"))


# ---------------------------------------------------------------------------
# AT-SPI2 D-Bus extractor
# ---------------------------------------------------------------------------


class ATSPIExtractor:
    """Extract the accessibility tree from a running application via AT-SPI2.

    Parameters
    ----------
    max_depth : int
        Maximum depth to traverse in the accessibility tree. 0 means
        unlimited.
    publish_fn : callable | None
        Optional async callback ``(topic, payload) -> None``.
    """

    def __init__(
        self,
        max_depth: int = 0,
        publish_fn: Any | None = None,
    ) -> None:
        self._max_depth = max_depth
        self._publish = publish_fn
        self._bus: Any | None = None

    # -- D-Bus connection ----------------------------------------------------

    async def connect(self) -> None:
        """Connect to the AT-SPI2 accessibility bus."""
        if not _IS_LINUX:
            msg = "AT-SPI2 requires a Linux desktop session"
            raise RuntimeError(msg)

        try:
            from dbus_next.aio import MessageBus  # type: ignore[import-untyped]
        except ImportError:
            msg = (
                "dbus-next is required for AT-SPI2 extraction. "
                "Install with: pip install dbus-next"
            )
            raise RuntimeError(msg) from None

        self._bus = await MessageBus().connect()
        logger.info("Connected to AT-SPI2 bus")

    async def disconnect(self) -> None:
        """Disconnect from the D-Bus."""
        if self._bus is not None:
            self._bus.disconnect()
            self._bus = None

    # -- Tree extraction -----------------------------------------------------

    async def _extract_node(
        self,
        bus: Any,
        bus_name: str,
        path: str,
        depth: int,
    ) -> AccessibleNode:
        """Recursively extract one accessible node and its children."""
        introspection = await bus.introspect(bus_name, path)
        proxy = bus.get_proxy_object(bus_name, path, introspection)
        accessible = proxy.get_interface(ATSPI_ACCESSIBLE_IFACE)

        name = await accessible.get_name()
        role = str(await accessible.get_role_name())
        description = await accessible.get_description()

        # Get child count and iterate
        child_count = await accessible.get_child_count()
        children: list[AccessibleNode] = []

        if self._max_depth == 0 or depth < self._max_depth:
            for i in range(child_count):
                child_ref = await accessible.call_get_child_at_index(i)
                # child_ref is (bus_name, path)
                child_bus, child_path = child_ref
                if child_path and child_path != "/":
                    try:
                        child = await self._extract_node(
                            bus, child_bus, child_path, depth + 1
                        )
                        children.append(child)
                    except Exception:
                        logger.debug(
                            "Skipping inaccessible child %s:%s", child_bus, child_path
                        )

        return AccessibleNode(
            role=role,
            name=name,
            description=description,
            children=children,
        )

    async def extract_tree(
        self,
        app_bus_name: str,
        root_path: str = ATSPI_REGISTRY_PATH,
    ) -> AccessibleNode:
        """Extract the accessibility tree for an application.

        Parameters
        ----------
        app_bus_name : str
            The D-Bus bus name of the target application.
        root_path : str
            D-Bus object path to start extraction from.

        Returns
        -------
        AccessibleNode
            The root of the extracted accessibility tree.

        Raises
        ------
        RuntimeError
            If not connected.
        """
        if self._bus is None:
            msg = "Not connected — call connect() first"
            raise RuntimeError(msg)

        start = time.perf_counter()
        root = await self._extract_node(self._bus, app_bus_name, root_path, 0)
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "Extracted %d nodes from %s in %.1f ms",
            root.node_count(),
            app_bus_name,
            elapsed_ms,
        )
        return root

    async def extract_and_publish(
        self,
        source_id: str,
        app_bus_name: str,
        root_path: str = ATSPI_REGISTRY_PATH,
    ) -> ParsedScreen:
        """Extract the tree and optionally publish via the event bus.

        Returns the ``ParsedScreen`` protobuf regardless of whether
        a publisher is configured.
        """
        start = time.perf_counter()
        root = await self.extract_tree(app_bus_name, root_path)
        elapsed_ms = (time.perf_counter() - start) * 1000

        tree_json = tree_to_json(root)
        count = root.node_count()

        proto = ParsedScreen(
            header=Header(
                timestamp_unix=time.time(),
                source_module="sensory.vision.accessibility",
                schema_version=1,
            ),
            source_id=source_id,
            method=ParseMethod.AT_SPI2,
            tree_json=tree_json,
            node_count=count,
            extraction_ms=elapsed_ms,
        )

        if self._publish is not None:
            topic = topic_for(SENSORY_VISION_PARSED, source_id=source_id)
            await self._publish(topic, proto.SerializeToString())

        return proto

    async def __aenter__(self) -> ATSPIExtractor:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()
