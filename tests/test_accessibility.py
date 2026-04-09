"""Tests for AT-SPI2 accessibility tree extractor — Issue #45."""

from __future__ import annotations

import json

import pytest

from openbad.nervous_system.schemas import ParsedScreen
from openbad.nervous_system.schemas.sensory_pb2 import ParseMethod
from openbad.sensory.vision.accessibility import (
    _IS_LINUX,
    AccessibleNode,
    ATSPIExtractor,
    tree_to_json,
)

# ---------------------------------------------------------------------------
# AccessibleNode tests
# ---------------------------------------------------------------------------


class TestAccessibleNode:
    def test_simple_node(self) -> None:
        node = AccessibleNode(role="push button", name="OK")
        assert node.role == "push button"
        assert node.name == "OK"
        assert node.children == []
        assert node.node_count() == 1

    def test_node_with_children(self) -> None:
        child1 = AccessibleNode(role="label", name="Hello")
        child2 = AccessibleNode(role="text", name="World")
        parent = AccessibleNode(role="panel", name="Container", children=[child1, child2])
        assert parent.node_count() == 3

    def test_deep_tree(self) -> None:
        leaf = AccessibleNode(role="label", name="Leaf")
        mid = AccessibleNode(role="panel", name="Mid", children=[leaf])
        root = AccessibleNode(role="frame", name="Root", children=[mid])
        assert root.node_count() == 3

    def test_to_dict_minimal(self) -> None:
        node = AccessibleNode(role="button", name="Click me")
        d = node.to_dict()
        assert d == {"role": "button", "name": "Click me"}

    def test_to_dict_with_description(self) -> None:
        node = AccessibleNode(role="button", name="OK", description="Confirm action")
        d = node.to_dict()
        assert d["description"] == "Confirm action"

    def test_to_dict_with_states(self) -> None:
        node = AccessibleNode(role="check box", name="Remember", states=["enabled", "checked"])
        d = node.to_dict()
        assert d["states"] == ["enabled", "checked"]

    def test_to_dict_with_actions(self) -> None:
        node = AccessibleNode(role="button", name="Act", actions=["click", "press"])
        d = node.to_dict()
        assert d["actions"] == ["click", "press"]

    def test_to_dict_with_value(self) -> None:
        node = AccessibleNode(role="slider", name="Volume", value="75")
        d = node.to_dict()
        assert d["value"] == "75"

    def test_to_dict_with_bounds(self) -> None:
        node = AccessibleNode(role="button", name="B", bounds=(10, 20, 100, 50))
        d = node.to_dict()
        assert d["bounds"] == {"x": 10, "y": 20, "w": 100, "h": 50}

    def test_to_dict_with_children(self) -> None:
        child = AccessibleNode(role="label", name="Text")
        parent = AccessibleNode(role="panel", name="P", children=[child])
        d = parent.to_dict()
        assert len(d["children"]) == 1
        assert d["children"][0]["role"] == "label"

    def test_to_dict_omits_empty_optional_fields(self) -> None:
        node = AccessibleNode(role="label", name="Plain")
        d = node.to_dict()
        assert "description" not in d
        assert "states" not in d
        assert "actions" not in d
        assert "value" not in d
        assert "bounds" not in d
        assert "children" not in d


class TestTreeToJson:
    def test_single_node(self) -> None:
        node = AccessibleNode(role="button", name="OK")
        result = tree_to_json(node)
        parsed = json.loads(result)
        assert parsed == {"role": "button", "name": "OK"}

    def test_nested_tree(self) -> None:
        child = AccessibleNode(role="label", name="Hello")
        root = AccessibleNode(role="frame", name="Window", children=[child])
        result = tree_to_json(root)
        parsed = json.loads(result)
        assert parsed["children"][0]["name"] == "Hello"

    def test_compact_json_no_spaces(self) -> None:
        node = AccessibleNode(role="x", name="y")
        result = tree_to_json(node)
        assert " " not in result  # compact separators

    def test_roundtrip_preserves_structure(self) -> None:
        leaf1 = AccessibleNode(role="label", name="A", states=["enabled"])
        leaf2 = AccessibleNode(role="button", name="B", bounds=(0, 0, 50, 30))
        panel = AccessibleNode(role="panel", name="P", children=[leaf1, leaf2])
        root = AccessibleNode(role="frame", name="App", children=[panel])

        js = tree_to_json(root)
        parsed = json.loads(js)
        assert parsed["name"] == "App"
        assert len(parsed["children"]) == 1
        panel_d = parsed["children"][0]
        assert len(panel_d["children"]) == 2
        assert panel_d["children"][0]["states"] == ["enabled"]
        assert panel_d["children"][1]["bounds"]["w"] == 50


# ---------------------------------------------------------------------------
# ATSPIExtractor unit tests
# ---------------------------------------------------------------------------


class TestATSPIExtractorConfig:
    def test_default_max_depth(self) -> None:
        ext = ATSPIExtractor()
        assert ext._max_depth == 0

    def test_custom_max_depth(self) -> None:
        ext = ATSPIExtractor(max_depth=5)
        assert ext._max_depth == 5


class TestATSPIExtractorPlatformGuard:
    @pytest.mark.skipif(_IS_LINUX, reason="Test only on non-Linux")
    async def test_connect_fails_on_non_linux(self) -> None:
        ext = ATSPIExtractor()
        with pytest.raises(RuntimeError, match="Linux desktop session"):
            await ext.connect()


class TestATSPIExtractorNotConnected:
    async def test_extract_tree_requires_connection(self) -> None:
        ext = ATSPIExtractor()
        with pytest.raises(RuntimeError, match="Not connected"):
            await ext.extract_tree("org.some.App")


class TestATSPIExtractorPublish:
    """Test extract_and_publish with a mocked tree extraction."""

    async def test_publish_builds_proto(self) -> None:
        published: list[tuple[str, bytes]] = []

        async def mock_publish(topic: str, payload: bytes) -> None:
            published.append((topic, payload))

        ext = ATSPIExtractor(publish_fn=mock_publish)
        # Manually inject a connected bus (mocked)
        ext._bus = object()  # truthy sentinel

        # Monkey-patch extract_tree to return a known tree
        tree = AccessibleNode(
            role="frame",
            name="TestApp",
            children=[
                AccessibleNode(role="button", name="OK"),
                AccessibleNode(role="label", name="Status"),
            ],
        )

        async def fake_extract(app_bus_name: str, root_path: str = "") -> AccessibleNode:
            return tree

        ext.extract_tree = fake_extract  # type: ignore[assignment]

        result = await ext.extract_and_publish("firefox-1", "org.firefox")

        assert isinstance(result, ParsedScreen)
        assert result.source_id == "firefox-1"
        assert result.method == ParseMethod.AT_SPI2
        assert result.node_count == 3
        assert result.extraction_ms >= 0

        # Verify JSON tree
        parsed_tree = json.loads(result.tree_json)
        assert parsed_tree["name"] == "TestApp"
        assert len(parsed_tree["children"]) == 2

        # Verify publish was called
        assert len(published) == 1
        topic, payload = published[0]
        assert topic == "agent/sensory/vision/firefox-1/parsed"

        # Verify payload deserialises
        restored = ParsedScreen()
        restored.ParseFromString(payload)
        assert restored.source_id == "firefox-1"
        assert restored.node_count == 3

    async def test_no_publish_fn_returns_proto_only(self) -> None:
        ext = ATSPIExtractor()
        ext._bus = object()

        tree = AccessibleNode(role="frame", name="App")

        async def fake_extract(app_bus_name: str, root_path: str = "") -> AccessibleNode:
            return tree

        ext.extract_tree = fake_extract  # type: ignore[assignment]

        result = await ext.extract_and_publish("vim-1", "org.vim")
        assert isinstance(result, ParsedScreen)
        assert result.node_count == 1


class TestATSPIExtractorContextManager:
    @pytest.mark.skipif(_IS_LINUX, reason="Test only on non-Linux")
    async def test_context_manager_connect_fails_gracefully(self) -> None:
        with pytest.raises(RuntimeError):
            async with ATSPIExtractor():
                pass

    async def test_disconnect_is_safe_when_not_connected(self) -> None:
        ext = ATSPIExtractor()
        await ext.disconnect()  # Should not raise


# ---------------------------------------------------------------------------
# Large tree performance test
# ---------------------------------------------------------------------------


class TestAccessibleNodeLargeTree:
    def test_large_tree_node_count(self) -> None:
        """Build a tree of ~1000 nodes and verify count."""

        def build_tree(depth: int, breadth: int) -> AccessibleNode:
            if depth == 0:
                return AccessibleNode(role="label", name="leaf")
            children = [build_tree(depth - 1, breadth) for _ in range(breadth)]
            return AccessibleNode(role="panel", name=f"d{depth}", children=children)

        # 4 levels, 5 children each = 5^0 + 5^1 + 5^2 + 5^3 + 5^4 = 781
        root = build_tree(4, 5)
        assert root.node_count() == 781

    def test_large_tree_serialises(self) -> None:
        """Ensure JSON serialisation handles moderate trees."""

        def build_tree(depth: int, breadth: int) -> AccessibleNode:
            if depth == 0:
                return AccessibleNode(role="label", name="leaf")
            children = [build_tree(depth - 1, breadth) for _ in range(breadth)]
            return AccessibleNode(role="panel", name=f"d{depth}", children=children)

        root = build_tree(3, 4)  # 85 nodes
        js = tree_to_json(root)
        parsed = json.loads(js)
        assert parsed["name"] == "d3"
