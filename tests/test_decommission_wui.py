"""Smoke tests for decommissioned legacy WUI and SvelteKit serving (#256)."""

from __future__ import annotations

from pathlib import Path

import pytest

# ── Legacy removal checks ─────────────────────────────────────────

_WUI_PKG = Path(__file__).resolve().parent.parent / "src" / "openbad" / "wui"


def test_legacy_static_dir_removed():
    assert not (_WUI_PKG / "static").exists()


def test_legacy_index_html_gone():
    assert not (_WUI_PKG / "static" / "index.html").exists()


def test_legacy_app_js_gone():
    assert not (_WUI_PKG / "static" / "app.js").exists()


def test_legacy_styles_css_gone():
    assert not (_WUI_PKG / "static" / "styles.css").exists()


# ── Server references updated ─────────────────────────────────────

def test_server_uses_build_dir():
    from openbad.wui.server import BUILD_DIR
    assert BUILD_DIR.name == "build"


def test_server_no_static_dir():
    import openbad.wui.server as srv
    assert not hasattr(srv, "STATIC_DIR")


def test_server_spa_fallback_handler():
    """create_app registers a catch-all SPA fallback route."""
    from openbad.wui.server import create_app
    app = create_app(enable_mqtt=False)
    routes = [r.resource.canonical for r in app.router.routes()
              if hasattr(r, "resource") and r.resource]
    assert any("{path}" in r for r in routes)


# ── API endpoints still present ───────────────────────────────────

def test_api_routes_present():
    from openbad.wui.server import create_app
    app = create_app(enable_mqtt=False)
    routes = [r.resource.canonical for r in app.router.routes()
              if hasattr(r, "resource") and r.resource]
    for endpoint in (
        "/api/providers",
        "/api/systems",
        "/api/senses",
        "/api/toolbelt",
        "/api/entity/user",
        "/api/entity/assistant",
    ):
        assert endpoint in routes, f"{endpoint} missing from routes"


# ── Makefile targets ──────────────────────────────────────────────

_MAKEFILE = Path(__file__).resolve().parent.parent / "Makefile"


def test_make_wui_target():
    text = _MAKEFILE.read_text(encoding="utf-8")
    assert "wui:" in text
    assert "npm run build" in text
    assert "cp -r wui-svelte/build src/openbad/wui/build" in text


def test_make_dev_wui_target():
    text = _MAKEFILE.read_text(encoding="utf-8")
    assert "wui-dev:" in text
    assert "npm run dev" in text


# ── SPA fallback integration test ─────────────────────────────────

@pytest.mark.asyncio
async def test_spa_serves_index_for_unknown_path(aiohttp_client, tmp_path, monkeypatch):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "index.html").write_text("<html>SPA</html>")
    import openbad.wui.server as srv
    monkeypatch.setattr(srv, "BUILD_DIR", build_dir)
    from openbad.wui.server import create_app
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)
    resp = await client.get("/providers")
    assert resp.status == 200
    assert "SPA" in await resp.text()


@pytest.mark.asyncio
async def test_spa_serves_static_file_when_exists(
    aiohttp_client, tmp_path, monkeypatch
):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "index.html").write_text("<html>SPA</html>")
    (build_dir / "favicon.png").write_bytes(b"\x89PNG")
    import openbad.wui.server as srv
    monkeypatch.setattr(srv, "BUILD_DIR", build_dir)
    from openbad.wui.server import create_app
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)
    resp = await client.get("/favicon.png")
    assert resp.status == 200
