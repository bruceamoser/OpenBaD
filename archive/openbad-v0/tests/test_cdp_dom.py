"""Tests for CDP DOM extractor — Issue #46."""

from __future__ import annotations

import json

from openbad.nervous_system.schemas import ParsedScreen
from openbad.nervous_system.schemas.sensory_pb2 import ParseMethod
from openbad.sensory.vision.cdp_dom import (
    CDPExtractor,
    DOMNode,
    _parse_cdp_node,
    dom_to_json,
)

# ---------------------------------------------------------------------------
# DOMNode tests
# ---------------------------------------------------------------------------


class TestDOMNode:
    def test_simple_node(self) -> None:
        node = DOMNode(tag="div")
        assert node.tag == "div"
        assert node.children == []
        assert node.node_count() == 1

    def test_node_with_children(self) -> None:
        child1 = DOMNode(tag="span")
        child2 = DOMNode(tag="p")
        parent = DOMNode(tag="div", children=[child1, child2])
        assert parent.node_count() == 3

    def test_to_dict_minimal(self) -> None:
        node = DOMNode(tag="br")
        d = node.to_dict()
        assert d == {"tag": "br"}

    def test_to_dict_with_node_id(self) -> None:
        node = DOMNode(tag="div", node_id=42)
        d = node.to_dict()
        assert d["nodeId"] == 42

    def test_to_dict_with_attributes(self) -> None:
        node = DOMNode(tag="a", attributes={"href": "/page", "class": "link"})
        d = node.to_dict()
        assert d["attributes"]["href"] == "/page"
        assert d["attributes"]["class"] == "link"

    def test_to_dict_with_text(self) -> None:
        node = DOMNode(tag="#text", text="Hello World")
        d = node.to_dict()
        assert d["text"] == "Hello World"

    def test_to_dict_with_children(self) -> None:
        child = DOMNode(tag="li")
        parent = DOMNode(tag="ul", children=[child])
        d = parent.to_dict()
        assert len(d["children"]) == 1
        assert d["children"][0]["tag"] == "li"

    def test_to_dict_omits_empty_fields(self) -> None:
        node = DOMNode(tag="div")
        d = node.to_dict()
        assert "nodeId" not in d
        assert "attributes" not in d
        assert "text" not in d
        assert "children" not in d

    def test_deep_tree_count(self) -> None:
        leaf = DOMNode(tag="span")
        mid = DOMNode(tag="div", children=[leaf])
        root = DOMNode(tag="body", children=[mid])
        assert root.node_count() == 3


class TestDomToJson:
    def test_single_node(self) -> None:
        node = DOMNode(tag="div")
        result = dom_to_json(node)
        parsed = json.loads(result)
        assert parsed == {"tag": "div"}

    def test_nested_tree(self) -> None:
        child = DOMNode(tag="span", text="Hello")
        root = DOMNode(tag="body", children=[child])
        result = dom_to_json(root)
        parsed = json.loads(result)
        assert parsed["children"][0]["text"] == "Hello"

    def test_compact_format(self) -> None:
        node = DOMNode(tag="br")
        result = dom_to_json(node)
        assert " " not in result


# ---------------------------------------------------------------------------
# CDP node parsing tests
# ---------------------------------------------------------------------------


class TestParseCdpNode:
    def test_simple_element(self) -> None:
        cdp_node = {
            "nodeId": 1,
            "nodeType": 1,
            "localName": "div",
            "nodeName": "DIV",
            "attributes": ["class", "container", "id", "main"],
            "children": [],
        }
        result = _parse_cdp_node(cdp_node)
        assert result.tag == "div"
        assert result.node_id == 1
        assert result.attributes == {"class": "container", "id": "main"}
        assert result.children == []

    def test_text_node(self) -> None:
        cdp_node = {
            "nodeId": 5,
            "nodeType": 3,
            "nodeName": "#text",
            "nodeValue": " Hello World ",
        }
        result = _parse_cdp_node(cdp_node)
        assert result.tag == "#text"
        assert result.text == "Hello World"

    def test_nested_children(self) -> None:
        cdp_node = {
            "nodeId": 1,
            "nodeType": 1,
            "localName": "ul",
            "attributes": [],
            "children": [
                {
                    "nodeId": 2,
                    "nodeType": 1,
                    "localName": "li",
                    "attributes": [],
                    "children": [],
                },
                {
                    "nodeId": 3,
                    "nodeType": 1,
                    "localName": "li",
                    "attributes": [],
                    "children": [],
                },
            ],
        }
        result = _parse_cdp_node(cdp_node)
        assert result.tag == "ul"
        assert len(result.children) == 2
        assert result.children[0].tag == "li"
        assert result.children[1].tag == "li"

    def test_empty_attributes(self) -> None:
        cdp_node = {
            "nodeId": 1,
            "nodeType": 1,
            "localName": "div",
        }
        result = _parse_cdp_node(cdp_node)
        assert result.attributes == {}

    def test_missing_children(self) -> None:
        cdp_node = {
            "nodeId": 1,
            "nodeType": 1,
            "localName": "img",
            "attributes": ["src", "/logo.png"],
        }
        result = _parse_cdp_node(cdp_node)
        assert result.children == []
        assert result.attributes == {"src": "/logo.png"}

    def test_odd_attribute_count_ignored(self) -> None:
        """Odd-length attribute arrays: last key without value is skipped."""
        cdp_node = {
            "nodeId": 1,
            "nodeType": 1,
            "localName": "div",
            "attributes": ["class", "x", "orphan"],
        }
        result = _parse_cdp_node(cdp_node)
        assert result.attributes == {"class": "x"}

    def test_document_node(self) -> None:
        cdp_node = {
            "nodeId": 1,
            "nodeType": 9,
            "nodeName": "#document",
            "children": [
                {
                    "nodeId": 2,
                    "nodeType": 1,
                    "localName": "html",
                    "attributes": [],
                    "children": [],
                }
            ],
        }
        result = _parse_cdp_node(cdp_node)
        assert result.tag == "#document"
        assert len(result.children) == 1


# ---------------------------------------------------------------------------
# CDPExtractor unit tests
# ---------------------------------------------------------------------------


class TestCDPExtractorConfig:
    def test_default_url(self) -> None:
        ext = CDPExtractor()
        assert ext._cdp_url == "http://localhost:9222"

    def test_custom_url(self) -> None:
        ext = CDPExtractor(cdp_url="http://127.0.0.1:9333/")
        assert ext._cdp_url == "http://127.0.0.1:9333"

    def test_message_id_increments(self) -> None:
        ext = CDPExtractor()
        assert ext._next_id() == 1
        assert ext._next_id() == 2
        assert ext._next_id() == 3


class TestCDPExtractorPublish:
    async def test_extract_and_publish_builds_proto(self) -> None:
        published: list[tuple[str, bytes]] = []

        async def mock_publish(topic: str, payload: bytes) -> None:
            published.append((topic, payload))

        ext = CDPExtractor(publish_fn=mock_publish)

        # Monkey-patch extract_dom to return a known tree
        tree = DOMNode(
            tag="html",
            children=[
                DOMNode(tag="head"),
                DOMNode(tag="body", children=[
                    DOMNode(tag="h1", text="Hello"),
                    DOMNode(tag="p", text="World"),
                ]),
            ],
        )

        async def fake_extract(page_index: int = 0) -> DOMNode:
            return tree

        ext.extract_dom = fake_extract  # type: ignore[assignment]

        result = await ext.extract_and_publish("chrome-1")

        assert isinstance(result, ParsedScreen)
        assert result.source_id == "chrome-1"
        assert result.method == ParseMethod.CDP_DOM
        assert result.node_count == 5
        assert result.extraction_ms >= 0

        # Verify tree in JSON
        parsed = json.loads(result.tree_json)
        assert parsed["tag"] == "html"
        assert len(parsed["children"]) == 2

        # Verify publish
        assert len(published) == 1
        topic, payload = published[0]
        assert topic == "agent/sensory/vision/chrome-1/parsed"

        restored = ParsedScreen()
        restored.ParseFromString(payload)
        assert restored.node_count == 5

    async def test_no_publish_fn(self) -> None:
        ext = CDPExtractor()

        tree = DOMNode(tag="html")

        async def fake_extract(page_index: int = 0) -> DOMNode:
            return tree

        ext.extract_dom = fake_extract  # type: ignore[assignment]

        result = await ext.extract_and_publish("tab-1")
        assert isinstance(result, ParsedScreen)
        assert result.node_count == 1


# ---------------------------------------------------------------------------
# Realistic CDP DOM tree test
# ---------------------------------------------------------------------------


class TestRealisticDOM:
    def test_realistic_page_structure(self) -> None:
        """Simulate a typical CDP response for a simple web page."""
        cdp_response = {
            "nodeId": 1,
            "nodeType": 9,
            "nodeName": "#document",
            "children": [{
                "nodeId": 2,
                "nodeType": 1,
                "localName": "html",
                "attributes": ["lang", "en"],
                "children": [
                    {
                        "nodeId": 3,
                        "nodeType": 1,
                        "localName": "head",
                        "attributes": [],
                        "children": [{
                            "nodeId": 4,
                            "nodeType": 1,
                            "localName": "title",
                            "attributes": [],
                            "children": [{
                                "nodeId": 5,
                                "nodeType": 3,
                                "nodeName": "#text",
                                "nodeValue": "Test Page",
                            }],
                        }],
                    },
                    {
                        "nodeId": 6,
                        "nodeType": 1,
                        "localName": "body",
                        "attributes": ["class", "main"],
                        "children": [
                            {
                                "nodeId": 7,
                                "nodeType": 1,
                                "localName": "h1",
                                "attributes": ["id", "title"],
                                "children": [{
                                    "nodeId": 8,
                                    "nodeType": 3,
                                    "nodeName": "#text",
                                    "nodeValue": "Welcome",
                                }],
                            },
                            {
                                "nodeId": 9,
                                "nodeType": 1,
                                "localName": "button",
                                "attributes": ["type", "submit", "class", "btn"],
                                "children": [{
                                    "nodeId": 10,
                                    "nodeType": 3,
                                    "nodeName": "#text",
                                    "nodeValue": "Click Me",
                                }],
                            },
                        ],
                    },
                ],
            }],
        }

        root = _parse_cdp_node(cdp_response)
        assert root.tag == "#document"
        assert root.node_count() == 10

        # Navigate to body > h1 > #text
        html = root.children[0]
        assert html.tag == "html"
        assert html.attributes["lang"] == "en"

        body = html.children[1]
        assert body.tag == "body"
        assert body.attributes["class"] == "main"

        h1 = body.children[0]
        assert h1.tag == "h1"
        assert h1.children[0].text == "Welcome"

        button = body.children[1]
        assert button.tag == "button"
        assert button.attributes["type"] == "submit"
        assert button.children[0].text == "Click Me"

        # Full JSON roundtrip
        js = dom_to_json(root)
        parsed = json.loads(js)
        assert parsed["children"][0]["tag"] == "html"
