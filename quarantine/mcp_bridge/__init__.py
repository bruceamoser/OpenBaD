"""MCP bridge package — sandboxed MCP server lifecycle and RPC.

Provides :class:`MCPRunner` for launching an MCP server subprocess,
listing its tools, and invoking them.  Supports **stdio** and **SSE**
transports.

Usage example (stdio)::

    async with MCPRunner.stdio(["npx", "-y", "@modelcontextprotocol/server-fetch"]) as runner:
        tools = await runner.list_tools()
        result = await runner.call_tool("fetch", {"url": "https://example.com"})

Usage example (SSE)::

    async with MCPRunner.sse("http://localhost:3000/sse") as runner:
        tools = await runner.list_tools()
"""

from openbad.toolbelt.mcp_bridge.mcp_runner import MCPRunner, MCPToolInfo, MCPTransport

__all__ = ["MCPRunner", "MCPToolInfo", "MCPTransport"]
