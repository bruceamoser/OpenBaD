"""Tests for WUI server scaffold (#185)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import yaml

from openbad.cognitive.providers.github_copilot import GitHubCopilotProvider
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
    assert "OpenBaD Control Surface" in html
    assert "/static/app.js" in html
    assert "Providers" in html


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
    assert "websocket_clients" in data
    assert "event_stream_clients" in data


@pytest.mark.asyncio
async def test_event_stream_route(aiohttp_client):
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)
    resp = await client.get("/events")
    assert resp.status == 200
    first_line = await resp.content.readline()
    assert first_line.startswith(b"data: ")


def test_create_app_attaches_bridge():
    app = create_app(enable_mqtt=False)
    assert "bridge" in app


@pytest.mark.asyncio
async def test_get_providers_route(aiohttp_client, tmp_path, monkeypatch):
    config_dir = tmp_path / "openbad"
    config_dir.mkdir()
    config_path = config_dir / "cognitive.yaml"
    config_path.write_text(
        """
cognitive:
  default_provider: ollama
  enabled: true
  providers:
    - name: ollama
      base_url: http://localhost:11434
      model: llama3.2
      timeout_ms: 30000
      enabled: true
""".strip()
    )
    monkeypatch.setenv("OPENBAD_CONFIG_DIR", str(config_dir))

    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)
    resp = await client.get("/api/providers")

    assert resp.status == 200
    data = await resp.json()
    assert data["default_provider"] == "ollama"
    assert data["providers"][0]["name"] == "ollama"
    assert data["config_path"].endswith("cognitive.yaml")


@pytest.mark.asyncio
async def test_put_providers_route(aiohttp_client, tmp_path, monkeypatch):
    config_dir = tmp_path / "openbad"
    config_dir.mkdir()
    monkeypatch.setenv("OPENBAD_CONFIG_DIR", str(config_dir))

    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)
    payload = {
        "enabled": True,
        "default_provider": "openai",
        "providers": [
            {
                "name": "openai",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "api_key_env": "OPENAI_API_KEY",
                "timeout_ms": 45000,
                "enabled": True,
            }
        ],
    }

    resp = await client.put("/api/providers", json=payload)

    assert resp.status == 200
    data = await resp.json()
    assert data["default_provider"] == "openai"
    saved = yaml.safe_load((config_dir / "cognitive.yaml").read_text())
    assert saved["cognitive"]["providers"][0]["api_key_env"] == "OPENAI_API_KEY"
    assert saved["cognitive"]["providers"][0]["timeout_ms"] == 45000


@pytest.mark.asyncio
async def test_verify_copilot_provider_route(aiohttp_client):
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    with patch("openbad.wui.server.GitHubCopilotProvider") as provider_cls:
        provider = provider_cls.return_value
        provider.health_check = AsyncMock(
            return_value=type(
                "Status",
                (),
                {"available": True, "latency_ms": 12.0, "models_available": 4},
            )()
        )
        provider.list_models = AsyncMock(
            return_value=[
                type("Model", (), {"model_id": "gpt-4o"})(),
                type("Model", (), {"model_id": "claude-sonnet-4-20250514"})(),
            ]
        )
        resp = await client.post("/api/providers/verify", json={"provider_type": "github-copilot"})

    assert resp.status == 200
    data = await resp.json()
    assert data["available"] is True
    assert data["provider"]["name"] == "github-copilot"
    assert "gpt-4o" in data["models"]


@pytest.mark.asyncio
async def test_start_copilot_device_flow_route(aiohttp_client):
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    with patch("openbad.wui.server.GitHubCopilotProvider") as provider_cls:
        provider = provider_cls.return_value
        provider.request_device_code = AsyncMock(
            return_value=type(
                "DeviceCode",
                (),
                {
                    "device_code": "device-123",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://github.com/login/device",
                    "interval": 5,
                    "expires_in": 900,
                },
            )()
        )
        resp = await client.post("/api/providers/copilot/device-code", json={"timeout_ms": 30000})

    assert resp.status == 200
    data = await resp.json()
    assert data["user_code"] == "ABCD-EFGH"
    assert data["verification_uri"].startswith("https://github.com")
    assert data["flow_id"]


@pytest.mark.asyncio
async def test_complete_copilot_device_flow_route(aiohttp_client):
    app = create_app(enable_mqtt=False)
    app["copilot_device_flows"]["flow-1"] = {
        "device_code": "device-123",
        "default_model": "gpt-4o",
        "timeout_ms": 30000,
        "interval": 5,
        "expires_at": 9999999999,
    }
    client = await aiohttp_client(app)

    with patch("openbad.wui.server.GitHubCopilotProvider") as provider_cls:
        provider = provider_cls.return_value
        provider.poll_for_token_once = AsyncMock(
            return_value={"state": "authorized", "access_token": "token"}
        )
        provider.health_check = AsyncMock(
            return_value=type(
                "Status",
                (),
                {"available": True, "latency_ms": 12.0, "models_available": 2},
            )()
        )
        provider.list_models = AsyncMock(
            return_value=[
                type("Model", (), {"model_id": "gpt-4o"})(),
                type("Model", (), {"model_id": "claude-sonnet-4-20250514"})(),
            ]
        )
        resp = await client.post("/api/providers/copilot/complete", json={"flow_id": "flow-1"})

    assert resp.status == 200
    data = await resp.json()
    assert data["authorized"] is True
    assert data["provider"]["name"] == "github-copilot"
    assert "gpt-4o" in data["models"]


@pytest.mark.asyncio
async def test_verify_local_provider_route(aiohttp_client):
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    with patch("openbad.wui.server.custom_provider") as provider_factory:
        provider = provider_factory.return_value
        provider.health_check = AsyncMock(
            return_value=type(
                "Status",
                (),
                {"available": True, "latency_ms": 7.5, "models_available": 1},
            )()
        )
        provider.list_models = AsyncMock(
            return_value=[type("Model", (), {"model_id": "bonsai-8b"})()]
        )
        resp = await client.post(
            "/api/providers/verify",
            json={
                "provider_type": "local-openai",
                "base_url": "http://127.0.0.1:11434",
                "api_key_env": "",
                "timeout_ms": 30000,
            },
        )

    assert resp.status == 200
    data = await resp.json()
    assert data["available"] is True
    assert data["provider"]["name"] == "custom"
    assert data["models"] == ["bonsai-8b"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload", "location"),
    [
        ("get", "/api/wiring/providers", None, "/api/providers"),
        (
            "put",
            "/api/wiring/providers",
            {"enabled": True, "default_provider": "", "providers": []},
            "/api/providers",
        ),
        (
            "post",
            "/api/wiring/providers/verify",
            {"provider_type": "github-copilot"},
            "/api/providers/verify",
        ),
        (
            "post",
            "/api/wiring/providers/copilot/device-code",
            {"timeout_ms": 30000},
            "/api/providers/copilot/device-code",
        ),
        (
            "post",
            "/api/wiring/providers/copilot/complete",
            {"flow_id": "flow-1"},
            "/api/providers/copilot/complete",
        ),
    ],
)
async def test_legacy_wiring_routes_redirect(aiohttp_client, method, path, payload, location):
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    request = getattr(client, method)
    kwargs = {"allow_redirects": False}
    if payload is not None:
        kwargs["json"] = payload

    resp = await request(path, **kwargs)

    assert resp.status == 301
    assert resp.headers["Location"] == location


@pytest.mark.asyncio
async def test_github_copilot_list_models_prefers_discovered_models():
    provider = GitHubCopilotProvider()
    provider._get = AsyncMock(
        side_effect=[
            {"data": [{"id": "gpt-4.1"}, {"id": "o4-mini"}, {"id": "gpt-4.1"}]}
        ]
    )

    models = await provider.list_models()

    assert [model.model_id for model in models] == ["gpt-4.1", "o4-mini"]


@pytest.mark.asyncio
async def test_github_copilot_list_models_falls_back_to_known_models():
    provider = GitHubCopilotProvider()
    provider._get = AsyncMock(side_effect=OSError("unavailable"))

    models = await provider.list_models()

    assert "gpt-4o" in [model.model_id for model in models]
