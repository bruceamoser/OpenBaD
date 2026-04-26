"""Tests for BrowserContextRunner — Phase 10, Issue #418."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openbad.toolbelt.mcp_bridge.browser_context import (
    BROWSER_CAPABILITY,
    BrowserCapabilities,
    BrowserContextRunner,
    _node_needs_browser,
    browser_session_for_node,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(*, has_browser: bool = True, cap_list: list | None = None) -> MagicMock:
    node = MagicMock()
    node.node_id = "node-test"
    if cap_list is not None:
        node.capability_requirements = cap_list
    elif has_browser:
        node.capability_requirements = [BROWSER_CAPABILITY]
    else:
        node.capability_requirements = []
    return node


# ---------------------------------------------------------------------------
# _node_needs_browser
# ---------------------------------------------------------------------------


class TestNodeNeedsBrowser:
    def test_returns_true_when_browser_in_list(self) -> None:
        node = _make_node(has_browser=True)
        assert _node_needs_browser(node) is True

    def test_returns_false_when_no_browser(self) -> None:
        node = _make_node(has_browser=False)
        assert _node_needs_browser(node) is False

    def test_returns_true_with_string_capabilities(self) -> None:
        node = _make_node()
        node.capability_requirements = "browser, web"
        assert _node_needs_browser(node) is True

    def test_returns_false_with_string_no_browser(self) -> None:
        node = _make_node()
        node.capability_requirements = "compute, llm"
        assert _node_needs_browser(node) is False

    def test_returns_false_when_no_attribute(self) -> None:
        node = MagicMock(spec=[])
        assert _node_needs_browser(node) is False


# ---------------------------------------------------------------------------
# BrowserContextRunner.for_node
# ---------------------------------------------------------------------------


class TestForNode:
    def test_returns_runner_for_browser_node(self) -> None:
        node = _make_node(has_browser=True)
        runner = BrowserContextRunner.for_node(node)
        assert runner is not None
        assert isinstance(runner, BrowserContextRunner)

    def test_returns_none_for_non_browser_node(self) -> None:
        node = _make_node(has_browser=False)
        runner = BrowserContextRunner.for_node(node)
        assert runner is None


# ---------------------------------------------------------------------------
# Start / stop lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_starts_and_becomes_active(self) -> None:
        runner = BrowserContextRunner(node_id="n1")
        assert runner.is_active is False
        await runner.start()
        assert runner.is_active is True
        await runner.stop()

    @pytest.mark.asyncio
    async def test_stops_and_becomes_inactive(self) -> None:
        runner = BrowserContextRunner(node_id="n2")
        await runner.start()
        await runner.stop()
        assert runner.is_active is False

    @pytest.mark.asyncio
    async def test_context_manager_start_stop(self) -> None:
        node = _make_node(has_browser=True)
        runner = BrowserContextRunner.for_node(node)
        assert runner is not None
        async with runner as ctx:
            assert ctx.is_active is True
        assert runner.is_active is False

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self) -> None:
        runner = BrowserContextRunner(node_id="n3")
        await runner.start()
        await runner.start()  # second call should be a no-op
        assert runner.is_active is True
        await runner.stop()


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


class TestCapabilities:
    @pytest.mark.asyncio
    async def test_capabilities_contains_cdp_url(self) -> None:
        runner = BrowserContextRunner(cdp_url="http://localhost:9222", node_id="n4")
        caps = runner.capabilities()
        assert isinstance(caps, BrowserCapabilities)
        assert caps.cdp_url == "http://localhost:9222"

    @pytest.mark.asyncio
    async def test_capabilities_includes_expected_features(self) -> None:
        runner = BrowserContextRunner(node_id="n5")
        caps = runner.capabilities()
        assert "dom_snapshot" in caps.features
        assert "navigate" in caps.features


# ---------------------------------------------------------------------------
# snapshot_dom / snapshot_dom_json
# ---------------------------------------------------------------------------


class TestSnapshotDom:
    @pytest.mark.asyncio
    async def test_raises_if_not_active(self) -> None:
        runner = BrowserContextRunner(node_id="n6")
        with pytest.raises(RuntimeError, match="not been started"):
            await runner.snapshot_dom()

    @pytest.mark.asyncio
    async def test_delegates_to_cdp_extractor(self) -> None:
        runner = BrowserContextRunner(node_id="n7")
        await runner.start()
        fake_root = MagicMock()
        with patch.object(
            runner._extractor, "extract_dom", new_callable=AsyncMock, return_value=fake_root
        ):
            root = await runner.snapshot_dom()
        assert root is fake_root
        await runner.stop()


# ---------------------------------------------------------------------------
# Non-browser node does not start browser
# ---------------------------------------------------------------------------


class TestNonBrowserNode:
    @pytest.mark.asyncio
    async def test_browser_session_for_non_browser_node_yields_none(self) -> None:
        node = _make_node(has_browser=False)
        async with browser_session_for_node(node) as ctx:
            assert ctx is None

    @pytest.mark.asyncio
    async def test_browser_session_for_browser_node_yields_runner(self) -> None:
        node = _make_node(has_browser=True)
        async with browser_session_for_node(node) as ctx:
            assert ctx is not None
            assert ctx.is_active is True
        assert ctx.is_active is False
