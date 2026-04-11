"""MCP server runner — async lifecycle and JSON-RPC 2.0 transport.

:class:`MCPRunner` manages the full lifecycle of one MCP server process
(or SSE endpoint):

1. **start** — spawn subprocess (stdio) or verify SSE endpoint
2. **initialize** — send JSON-RPC ``initialize`` + ``notifications/initialized``
3. **list_tools** — retrieve available tool metadata
4. **call_tool** — invoke a named tool and return its result
5. **stop** — terminate the subprocess (stdio) or release resources (SSE)

The class is designed to be used as an async context manager::

    async with MCPRunner.stdio(["my-mcp-server"]) as runner:
        tools = await runner.list_tools()
        result = await runner.call_tool("my_tool", {"arg": "value"})

Security notes
--------------
* stdio transport runs the server in a full subprocess; the caller is
  responsible for supplying a trusted command.
* SSE transport connects to an already-running server; URL scheme is
  validated to ``http`` / ``https`` only.
* No environment variables beyond a minimal safe set are forwarded to
  the subprocess.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

_RPC_VERSION = "2.0"
_MCP_VERSION = "2024-11-05"
_DEFAULT_TIMEOUT = 30.0


class MCPTransport(StrEnum):
    """Supported MCP transport mechanisms."""

    STDIO = "stdio"
    SSE = "sse"


@dataclass
class MCPToolInfo:
    """Minimal descriptor for a tool advertised by an MCP server."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_request(method: str, params: dict[str, Any] | None, req_id: int) -> str:
    """Serialise a JSON-RPC request to a newline-terminated string."""
    obj: dict[str, Any] = {
        "jsonrpc": _RPC_VERSION,
        "id": req_id,
        "method": method,
    }
    if params is not None:
        obj["params"] = params
    return json.dumps(obj) + "\n"


def _make_notification(method: str, params: dict[str, Any] | None = None) -> str:
    """Serialise a JSON-RPC notification (no id field)."""
    obj: dict[str, Any] = {"jsonrpc": _RPC_VERSION, "method": method}
    if params:
        obj["params"] = params
    return json.dumps(obj) + "\n"


# ---------------------------------------------------------------------------
# MCPRunner
# ---------------------------------------------------------------------------


class MCPRunner:
    """Lifecycle manager for a single MCP server session.

    Create instances via the class-method constructors:
    :meth:`stdio` or :meth:`sse`.
    """

    def __init__(
        self,
        transport: MCPTransport,
        *,
        command: list[str] | None = None,
        sse_url: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._transport = transport
        self._command = command or []
        self._sse_url = sse_url
        self._timeout = timeout

        # stdio state
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

        self._req_id = 0
        self._started = False

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def stdio(
        cls, command: list[str], *, timeout: float = _DEFAULT_TIMEOUT
    ) -> MCPRunner:
        """Create a runner that spawns *command* as a stdio MCP server subprocess."""
        if not command:
            raise ValueError("command must be a non-empty list")
        return cls(MCPTransport.STDIO, command=command, timeout=timeout)

    @classmethod
    def sse(cls, url: str, *, timeout: float = _DEFAULT_TIMEOUT) -> MCPRunner:
        """Create a runner that connects to an already-running SSE MCP server."""
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"SSE URL must use http/https; got: {url!r}")
        return cls(MCPTransport.SSE, sse_url=url, timeout=timeout)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> MCPRunner:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the MCP server and perform the JSON-RPC handshake."""
        if self._started:
            return

        if self._transport is MCPTransport.STDIO:
            await self._start_stdio()
        else:
            await self._verify_sse()

        await self._initialize()
        self._started = True

    async def stop(self) -> None:
        """Terminate the MCP session and clean up resources."""
        self._started = False
        if self._proc is not None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except Exception:
                self._proc.kill()
            finally:
                self._proc = None

        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                with contextlib.suppress(Exception):
                    pass
            finally:
                self._writer = None
        self._reader = None

    # ------------------------------------------------------------------
    # Public RPC
    # ------------------------------------------------------------------

    async def list_tools(self) -> list[MCPToolInfo]:
        """Return the list of tools provided by the MCP server."""
        resp = await self._rpc("tools/list")
        tools_raw = resp.get("tools", [])
        return [
            MCPToolInfo(
                name=t.get("name", ""),
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
            )
            for t in tools_raw
        ]

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> Any:
        """Invoke the named tool with *arguments* and return the result content.

        Parameters
        ----------
        name:
            Tool name as returned by :meth:`list_tools`.
        arguments:
            Tool input arguments.

        Returns
        -------
        Any
            The ``content`` value from the ``tools/call`` response, or the
            full response dict if ``content`` is absent.
        """
        resp = await self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        return resp.get("content", resp)

    # ------------------------------------------------------------------
    # Internal transport helpers
    # ------------------------------------------------------------------

    async def _start_stdio(self) -> None:
        """Spawn the subprocess and wire up stdin/stdout streams."""
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/tmp",  # noqa: S108
        }
        self._proc = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None
        self._writer = self._proc.stdin  # type: ignore[assignment]
        self._reader = self._proc.stdout

    async def _verify_sse(self) -> None:
        """Confirm the SSE endpoint is reachable (HEAD request)."""
        assert self._sse_url is not None
        try:
            req = urllib.request.Request(self._sse_url, method="GET")  # noqa: S310
            with urllib.request.urlopen(req, timeout=self._timeout):  # noqa: S310
                pass
        except Exception as exc:
            raise OSError(f"SSE endpoint unreachable: {self._sse_url!r}") from exc

    async def _initialize(self) -> None:
        """Send the MCP initialize / notifications/initialized handshake."""
        params: dict[str, Any] = {
            "protocolVersion": _MCP_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "OpenBaD", "version": "1.0"},
        }
        await self._rpc("initialize", params)
        # Send the notification (no response expected)
        await self._send(_make_notification("notifications/initialized"))

    async def _rpc(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and return the ``result`` dict."""
        self._req_id += 1
        req = _make_request(method, params, self._req_id)
        await self._send(req)
        return await self._recv(self._req_id)

    async def _send(self, data: str) -> None:
        """Write *data* to the chosen transport."""
        if self._transport is MCPTransport.STDIO:
            if self._writer is None:
                raise RuntimeError("stdio writer not initialised; call start() first")
            self._writer.write(data.encode())
            await self._writer.drain()
        else:
            # SSE: POST the JSON-RPC message to the server's endpoint
            assert self._sse_url is not None
            post_url = self._sse_url.rstrip("/").rsplit("/sse", 1)[0] + "/message"
            req = urllib.request.Request(  # noqa: S310
                post_url,
                data=data.encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: urllib.request.urlopen(req, timeout=self._timeout),  # noqa: S310
            )

    async def _recv(self, expected_id: int) -> dict[str, Any]:
        """Read one JSON-RPC response from the stdio stream.

        For SSE transport this is a stub; a full SSE implementation would
        consume the server-sent events stream.
        """
        if self._transport is MCPTransport.SSE:
            # SSE response polling is not implemented in this minimal runner;
            # callers should use the full async SSE consumer for production use.
            raise NotImplementedError(
                "SSE response reading requires an async SSE event consumer"
            )

        if self._reader is None:
            raise RuntimeError("stdio reader not initialised; call start() first")

        deadline = asyncio.get_event_loop().time() + self._timeout
        while True:
            try:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise TimeoutError(f"Timeout waiting for JSON-RPC id={expected_id}")
                line = await asyncio.wait_for(self._reader.readline(), timeout=remaining)
            except TimeoutError as exc:
                raise TimeoutError(f"Timeout waiting for JSON-RPC id={expected_id}") from exc

            if not line:
                raise EOFError("MCP server closed stdout unexpectedly")

            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("Skipping non-JSON line: %r", line)
                continue

            if resp.get("id") == expected_id:
                if "error" in resp:
                    raise RuntimeError(
                        f"MCP error {resp['error'].get('code')}: {resp['error'].get('message')}"
                    )
                return resp.get("result", {})
