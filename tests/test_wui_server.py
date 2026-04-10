"""Tests for WUI server scaffold (#185)."""

from __future__ import annotations

import pytest

from openbad.wui.server import STATIC_DIR, create_app


def test_static_assets_exist():
    assert (STATIC_DIR / "index.html").exists()
    assert (STATIC_DIR / "styles.css").exists()
    assert (STATIC_DIR / "app.js").exists()


@pytest.mark.asyncio
async def test_index_route(aiohttp_client):
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)
    resp = await client.get("/")
    assert resp.status == 200
    html = await resp.text()
    assert "OpenBaD Live Dashboard" in html
    assert "/static/app.js" in html


@pytest.mark.asyncio
async def test_static_css_route(aiohttp_client):
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)
    resp = await client.get("/static/styles.css")
    assert resp.status == 200
    css = await resp.text()
    assert ":root" in css


@pytest.mark.asyncio
async def test_ws_health_route(aiohttp_client):
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)
    resp = await client.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert "ok" in data
    assert "clients" in data


def test_create_app_attaches_bridge():
    app = create_app(enable_mqtt=False)
    assert "bridge" in app
