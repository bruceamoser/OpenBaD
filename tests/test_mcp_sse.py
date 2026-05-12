"""Tests for the embedded-skills MCP server (Streamable HTTP + SSE)."""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_skill_server_streamable_http_app_returns_starlette():
    """skill_server.streamable_http_app() produces a valid Starlette app."""
    from starlette.applications import Starlette

    from openbad.skills import skill_server

    skill_server.settings.stateless_http = True
    app = skill_server.streamable_http_app()
    assert isinstance(app, Starlette)


@pytest.mark.asyncio
async def test_streamable_http_endpoint_accepts_post():
    """The /mcp endpoint accepts POST with JSON-RPC and returns 200."""
    import json

    from httpx import ASGITransport, AsyncClient
    from mcp.server.transport_security import TransportSecuritySettings

    from openbad.skills import skill_server

    skill_server.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )
    skill_server.settings.stateless_http = True
    # Reset cached session manager so the new security settings take effect.
    skill_server._session_manager = None
    app = skill_server.streamable_http_app()

    transport = ASGITransport(app=app, raise_app_exceptions=False)  # type: ignore[arg-type]

    # Send a valid JSON-RPC initialize request.
    init_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0.1"},
        },
    }

    async with skill_server.session_manager.run():
        async with AsyncClient(transport=transport, base_url="http://localhost") as client:
            resp = await client.post(
                "/mcp",
                content=json.dumps(init_request),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
            assert resp.status_code == 200
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_streamable_http_app_has_mcp_route():
    """Streamable HTTP app has /mcp route."""
    from openbad.skills import skill_server

    skill_server.settings.stateless_http = True
    app = skill_server.streamable_http_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/mcp" in paths


@pytest.mark.asyncio
async def test_sse_app_still_works():
    """SSE app still builds and has expected routes."""
    from starlette.applications import Starlette

    from openbad.skills import skill_server

    app = skill_server.sse_app()
    assert isinstance(app, Starlette)
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/sse" in paths


@pytest.mark.asyncio
async def test_async_get_openai_tools_not_empty():
    """async_get_openai_tools() returns a non-empty list of tool schemas."""
    from openbad.skills.server import async_get_openai_tools

    tools = await async_get_openai_tools()
    assert isinstance(tools, list)
    assert len(tools) > 0
    # Each tool should have the standard OpenAI schema shape.
    for tool in tools:
        assert tool["type"] == "function"
        assert "name" in tool["function"]
        assert "parameters" in tool["function"]
