"""Tests for MCPRunner — Phase 10 issue #417."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openbad.toolbelt.mcp_bridge.mcp_runner import (
    MCPRunner,
    MCPToolInfo,
    MCPTransport,
    _make_notification,
    _make_request,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rpc_response(req_id: int, result: dict) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}) + "\n"


def _rpc_error(req_id: int, code: int, message: str) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    ) + "\n"


# ---------------------------------------------------------------------------
# _make_request / _make_notification
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_make_request_has_id(self) -> None:
        raw = _make_request("tools/list", None, 1)
        obj = json.loads(raw)
        assert obj["id"] == 1
        assert obj["method"] == "tools/list"
        assert "params" not in obj

    def test_make_request_includes_params(self) -> None:
        raw = _make_request("initialize", {"k": "v"}, 2)
        obj = json.loads(raw)
        assert obj["params"] == {"k": "v"}

    def test_make_notification_no_id(self) -> None:
        raw = _make_notification("notifications/initialized")
        obj = json.loads(raw)
        assert "id" not in obj
        assert obj["method"] == "notifications/initialized"


# ---------------------------------------------------------------------------
# MCPTransport enum
# ---------------------------------------------------------------------------


class TestMCPTransport:
    def test_stdio_value(self) -> None:
        assert MCPTransport.STDIO == "stdio"

    def test_sse_value(self) -> None:
        assert MCPTransport.SSE == "sse"


# ---------------------------------------------------------------------------
# MCPToolInfo
# ---------------------------------------------------------------------------


class TestMCPToolInfo:
    def test_defaults(self) -> None:
        info = MCPToolInfo(name="my_tool")
        assert info.description == ""
        assert info.input_schema == {}


# ---------------------------------------------------------------------------
# MCPRunner constructors
# ---------------------------------------------------------------------------


class TestMCPRunnerConstructors:
    def test_stdio_requires_command(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            MCPRunner.stdio([])

    def test_stdio_sets_transport(self) -> None:
        runner = MCPRunner.stdio(["my-server"])
        assert runner._transport is MCPTransport.STDIO

    def test_sse_validates_scheme(self) -> None:
        with pytest.raises(ValueError, match="http/https"):
            MCPRunner.sse("ftp://localhost/sse")

    def test_sse_sets_transport(self) -> None:
        runner = MCPRunner.sse("http://localhost:3000/sse")
        assert runner._transport is MCPTransport.SSE


# ---------------------------------------------------------------------------
# stdio: start, list_tools, call_tool
# ---------------------------------------------------------------------------


def _build_stdio_mock(responses: list[str]):
    """Build a mock Process with scripted readline() responses."""
    stdout = MagicMock()
    calls = iter(responses)

    async def _readline():
        try:
            return next(calls).encode()
        except StopIteration:
            return b""

    stdout.readline = _readline

    stdin = MagicMock()
    stdin.write = MagicMock()
    stdin.drain = AsyncMock()
    stdin.close = MagicMock()
    stdin.wait_closed = AsyncMock()

    proc = MagicMock()
    proc.stdin = stdin
    proc.stdout = stdout
    proc.terminate = MagicMock()
    proc.wait = AsyncMock()

    return proc


class TestStdioRunner:
    def _standard_responses(self, extra: list[str] | None = None) -> list[str]:
        # id=1: initialize, id=2+: caller's requests
        return [
            _rpc_response(1, {"protocolVersion": "2024-11-05", "capabilities": {}}),
        ] + (extra or [])

    @pytest.mark.asyncio
    async def test_list_tools(self) -> None:
        tools_raw = [{"name": "fetch", "description": "Fetches a URL", "inputSchema": {}}]
        mock_proc = _build_stdio_mock(
            self._standard_responses([_rpc_response(2, {"tools": tools_raw})])
        )
        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_proc),
        ):
            runner = MCPRunner.stdio(["fake-server"])
            await runner.start()
            tools = await runner.list_tools()
            await runner.stop()

        assert len(tools) == 1
        assert tools[0].name == "fetch"
        assert tools[0].description == "Fetches a URL"

    @pytest.mark.asyncio
    async def test_call_tool_returns_content(self) -> None:
        content = [{"type": "text", "text": "hello"}]
        mock_proc = _build_stdio_mock(
            self._standard_responses([_rpc_response(2, {"content": content})])
        )
        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_proc),
        ):
            runner = MCPRunner.stdio(["fake-server"])
            await runner.start()
            result = await runner.call_tool("fetch", {"url": "https://example.com"})
            await runner.stop()

        assert result == content

    @pytest.mark.asyncio
    async def test_rpc_error_raises_runtime_error(self) -> None:
        mock_proc = _build_stdio_mock(
            self._standard_responses([_rpc_error(2, -32601, "Method not found")])
        )
        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_proc),
        ):
            runner = MCPRunner.stdio(["fake-server"])
            await runner.start()
            with pytest.raises(RuntimeError, match="Method not found"):
                await runner.list_tools()
            await runner.stop()

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        tools_raw = [{"name": "t1", "description": "", "inputSchema": {}}]
        mock_proc = _build_stdio_mock(
            self._standard_responses([_rpc_response(2, {"tools": tools_raw})])
        )
        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_proc),
        ):
            async with MCPRunner.stdio(["fake-server"]) as runner:
                tools = await runner.list_tools()

        assert tools[0].name == "t1"

    @pytest.mark.asyncio
    async def test_double_start_is_noop(self) -> None:
        mock_proc = _build_stdio_mock(
            self._standard_responses()
        )
        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_proc),
        ) as mock_spawn:
            runner = MCPRunner.stdio(["fake-server"])
            await runner.start()
            await runner.start()  # should not re-spawn

        assert mock_spawn.call_count == 1

    @pytest.mark.asyncio
    async def test_eof_raises(self) -> None:
        mock_proc = _build_stdio_mock([_rpc_response(1, {}), b""])
        mock_proc.stdout.readline = AsyncMock(
            side_effect=[
                _rpc_response(1, {}).encode(),
                b"",
            ]
        )
        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_proc),
        ):
            runner = MCPRunner.stdio(["fake-server"])
            await runner.start()
            with pytest.raises(EOFError):
                await runner.list_tools()
            await runner.stop()


# ---------------------------------------------------------------------------
# SSE constructors / validation
# ---------------------------------------------------------------------------


class TestSSERunner:
    def test_sse_raises_for_file_scheme(self) -> None:
        with pytest.raises(ValueError):
            MCPRunner.sse("file:///etc/passwd")

    @pytest.mark.asyncio
    async def test_sse_recv_raises_not_implemented(self) -> None:
        runner = MCPRunner.sse("http://localhost:9999/sse")
        runner._started = True  # skip handshake
        with pytest.raises(NotImplementedError, match="SSE response reading"):
            await runner._recv(1)
