"""Tests for the transmit_message egress skill."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# ── transmit_message ─────────────────────────────────────────────── #


class TestTransmitMessage:
    """Tests for the transmit_message skill function."""

    @pytest.fixture(autouse=True)
    def _patch_mcp_bridge(self) -> Any:
        """Patch mcp_bridge so we don't need a real Corsair sidecar."""
        with patch(
            "openbad.skills.server.mcp_bridge",
            new_callable=AsyncMock,
        ) as mock:
            self.mock_bridge = mock
            yield mock

    @pytest.mark.asyncio
    async def test_calls_mcp_bridge_with_correct_args(self) -> None:
        from openbad.skills.server import transmit_message

        self.mock_bridge.return_value = json.dumps({"ok": True})

        result = await transmit_message(
            platform="discord",
            operation="send_message",
            target="channel-123",
            content="Hello from OpenBaD!",
        )

        self.mock_bridge.assert_awaited_once_with(
            server="corsair",
            tool_name="corsair_run",
            arguments={
                "plugin": "discord",
                "operation": "send_message",
                "params": {
                    "target": "channel-123",
                    "content": "Hello from OpenBaD!",
                },
            },
        )
        assert json.loads(result) == {"ok": True}

    @pytest.mark.asyncio
    async def test_omits_empty_target_and_content(self) -> None:
        from openbad.skills.server import transmit_message

        self.mock_bridge.return_value = json.dumps({"ok": True})

        await transmit_message(
            platform="slack",
            operation="list_channels",
        )

        call_args = self.mock_bridge.call_args
        params = call_args.kwargs["arguments"]["params"]
        assert "target" not in params
        assert "content" not in params

    @pytest.mark.asyncio
    async def test_includes_only_target(self) -> None:
        from openbad.skills.server import transmit_message

        self.mock_bridge.return_value = json.dumps({"ok": True})

        await transmit_message(
            platform="gmail",
            operation="get_messages",
            target="inbox",
        )

        call_args = self.mock_bridge.call_args
        params = call_args.kwargs["arguments"]["params"]
        assert params["target"] == "inbox"
        assert "content" not in params

    @pytest.mark.asyncio
    async def test_returns_error_when_sidecar_unavailable(self) -> None:
        from openbad.skills.server import transmit_message

        self.mock_bridge.return_value = json.dumps(
            {"error": "MCP server binary 'corsair' not found."},
        )

        result = await transmit_message(
            platform="discord",
            operation="send_message",
            content="test",
        )

        parsed = json.loads(result)
        assert "error" in parsed
        assert "not found" in parsed["error"]

    @pytest.mark.asyncio
    async def test_returns_error_for_invalid_plugin(self) -> None:
        from openbad.skills.server import transmit_message

        self.mock_bridge.return_value = json.dumps(
            {"error": "Tool 'corsair_run' failed: unknown plugin 'bogus'"},
        )

        result = await transmit_message(
            platform="bogus",
            operation="send_message",
            content="test",
        )

        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_returns_string_result_as_is(self) -> None:
        from openbad.skills.server import transmit_message

        self.mock_bridge.return_value = "Message sent successfully"

        result = await transmit_message(
            platform="telegram",
            operation="send_message",
            target="@user",
            content="hi",
        )

        assert result == "Message sent successfully"


# ── Capability catalog registration ──────────────────────────────── #


class TestTransmitMessageCatalog:
    """Verify transmit_message appears in the capability catalog."""

    def test_catalog_contains_transmit_message(self) -> None:
        from openbad.wui.server import _CAPABILITIES_CATALOG

        ids = [c["id"] for c in _CAPABILITIES_CATALOG]
        assert "transmit_message" in ids

    def test_catalog_entry_has_correct_tool(self) -> None:
        from openbad.wui.server import _CAPABILITIES_CATALOG

        entry = next(
            c for c in _CAPABILITIES_CATALOG if c["id"] == "transmit_message"
        )
        assert entry["level"] == 1
        tool_names = [t["name"] for t in entry["tools"]]
        assert "transmit_message" in tool_names


# ── Tool registry registration ───────────────────────────────────── #


class TestTransmitMessageRegistry:
    """Verify transmit-message is registered in the tool registry."""

    def test_registry_has_transmit_message(self) -> None:
        from openbad.wui.server import _build_runtime_tool_registry

        registry = _build_runtime_tool_registry()
        snapshot = registry.snapshot()
        names = [t["name"] for t in snapshot]
        assert "transmit-message" in names

    def test_registry_role_is_communication(self) -> None:
        from openbad.proprioception.registry import ToolRole
        from openbad.wui.server import _build_runtime_tool_registry

        registry = _build_runtime_tool_registry()
        snapshot = registry.snapshot()
        entry = next(t for t in snapshot if t["name"] == "transmit-message")
        assert entry["role"] == ToolRole.COMMUNICATION.value
