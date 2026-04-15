"""Tests for WUI server scaffold (#185)."""

from __future__ import annotations

import os
import stat
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from openbad.cognitive.config import (
    CognitiveConfig,
    CognitiveSystem,
    ProviderConfig,
    SystemAssignment,
)
from openbad.cognitive.providers.github_copilot import GitHubCopilotProvider
from openbad.wui.server import BUILD_DIR, create_app


def test_build_dir_defined():
    """BUILD_DIR points to the expected wui/build path."""
    assert BUILD_DIR.name == "build"
    assert BUILD_DIR.parent.name == "wui"


@pytest.mark.asyncio
async def test_index_route(aiohttp_client, tmp_path, monkeypatch):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "index.html").write_text(
        "<html><body>OpenBaD SvelteKit</body></html>"
    )
    import openbad.wui.server as srv
    monkeypatch.setattr(srv, "BUILD_DIR", build_dir)
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)
    resp = await client.get("/")
    assert resp.status == 200
    html = await resp.text()
    assert "OpenBaD" in html


@pytest.mark.asyncio
async def test_spa_fallback_route(aiohttp_client, tmp_path, monkeypatch):
    """Non-API routes fall back to index.html for client-side routing."""
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "index.html").write_text("<html>SPA</html>")
    import openbad.wui.server as srv
    monkeypatch.setattr(srv, "BUILD_DIR", build_dir)
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)
    resp = await client.get("/chat")
    assert resp.status == 200
    html = await resp.text()
    assert "SPA" in html


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
        "providers": [
            {
                "name": "openai",
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
                "timeout_ms": 45000,
                "enabled": True,
            }
        ],
    }

    resp = await client.put("/api/providers", json=payload)

    assert resp.status == 200
    data = await resp.json()
    saved = yaml.safe_load((config_dir / "cognitive.yaml").read_text())
    assert saved["cognitive"]["providers"][0]["api_key_env"] == "OPENAI_API_KEY"
    assert saved["cognitive"]["providers"][0]["timeout_ms"] == 45000


@pytest.mark.asyncio
async def test_put_providers_route_persists_api_key_with_restricted_permissions(
    aiohttp_client, tmp_path, monkeypatch
):
    config_dir = tmp_path / "openbad"
    config_dir.mkdir()
    monkeypatch.setenv("OPENBAD_CONFIG_DIR", str(config_dir))

    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)
    payload = {
        "enabled": True,
        "providers": [
            {
                "name": "anthropic",
                "base_url": "https://api.anthropic.com",
                "api_key": "secret-key",
                "api_key_env": "ANTHROPIC_API_KEY",
                "timeout_ms": 30000,
                "enabled": True,
            }
        ],
    }

    resp = await client.put("/api/providers", json=payload)

    assert resp.status == 200
    data = await resp.json()
    assert data["providers"][0]["has_api_key"] is True
    assert "api_key" not in data["providers"][0]

    saved = yaml.safe_load((config_dir / "cognitive.yaml").read_text())
    assert saved["cognitive"]["providers"][0]["api_key"] == "secret-key"
    assert stat.S_IMODE(os.stat(config_dir / "cognitive.yaml").st_mode) == 0o600


@pytest.mark.asyncio
async def test_get_providers_route_reports_saved_copilot_token(
    aiohttp_client, tmp_path, monkeypatch
):
    config_dir = tmp_path / "openbad"
    config_dir.mkdir()
    config_path = config_dir / "cognitive.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "cognitive": {
                    "enabled": True,
                    "providers": [
                        {
                            "name": "github-copilot",
                            "base_url": "https://api.githubcopilot.com",
                            "api_key_env": "GITHUB_COPILOT_TOKEN",
                            "timeout_ms": 30000,
                            "enabled": True,
                        }
                    ],
                }
            },
            sort_keys=False,
        )
    )
    token_dir = tmp_path / "home" / ".openbad"
    token_dir.mkdir(parents=True)
    (token_dir / "copilot_token.json").write_text('{"access_token":"token","expires_at":9999999999}')
    monkeypatch.setenv("OPENBAD_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr("openbad.cognitive.providers.github_copilot._TOKEN_FILE", token_dir / "copilot_token.json")
    monkeypatch.setattr("openbad.wui.server._TOKEN_FILE", token_dir / "copilot_token.json")

    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    resp = await client.get("/api/providers")

    assert resp.status == 200
    data = await resp.json()
    assert data["providers"][0]["has_api_key"] is True


@pytest.mark.asyncio
async def test_get_setup_status_flags_first_run_without_provider_config(
    aiohttp_client, tmp_path, monkeypatch
):
    config_dir = tmp_path / "openbad"
    config_dir.mkdir()
    monkeypatch.setenv("OPENBAD_CONFIG_DIR", str(config_dir))

    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    resp = await client.get("/api/setup-status")

    assert resp.status == 200
    data = await resp.json()
    assert data["first_run"] is True
    assert "provider" in data["missing"]
    assert "chat_assignment" in data["missing"]
    assert data["redirect_to"] == "/providers?wizard=1"


@pytest.mark.asyncio
async def test_get_setup_status_ready_with_valid_provider_and_chat_assignment(
    aiohttp_client, tmp_path, monkeypatch
):
    config_dir = tmp_path / "openbad"
    config_dir.mkdir()
    config_path = config_dir / "cognitive.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "cognitive": {
                    "enabled": True,
                    "providers": [
                        {
                            "name": "openai",
                            "base_url": "https://api.openai.com",
                            "api_key": "secret-key",
                            "api_key_env": "OPENAI_API_KEY",
                            "timeout_ms": 30000,
                            "enabled": True,
                        }
                    ],
                    "systems": {
                        "chat": {"provider": "openai", "model": "gpt-4o-mini"},
                    },
                }
            },
            sort_keys=False,
        )
    )
    monkeypatch.setenv("OPENBAD_CONFIG_DIR", str(config_dir))

    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    resp = await client.get("/api/setup-status")

    assert resp.status == 200
    data = await resp.json()
    assert data["first_run"] is False
    assert data["provider_ready"] is True
    assert data["chat_assignment_ready"] is True


@pytest.mark.asyncio
async def test_get_setup_status_not_first_run_with_valid_provider_even_when_chat_assignment_invalid(
    aiohttp_client, tmp_path, monkeypatch
):
    config_dir = tmp_path / "openbad"
    config_dir.mkdir()
    config_path = config_dir / "cognitive.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "cognitive": {
                    "enabled": True,
                    "providers": [
                        {
                            "name": "openai",
                            "base_url": "https://api.openai.com",
                            "api_key": "secret-key",
                            "api_key_env": "OPENAI_API_KEY",
                            "timeout_ms": 30000,
                            "enabled": True,
                        }
                    ],
                    "systems": {
                        "chat": {"provider": "github-copilot", "model": "gpt-4o"},
                    },
                }
            },
            sort_keys=False,
        )
    )
    monkeypatch.setenv("OPENBAD_CONFIG_DIR", str(config_dir))

    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    resp = await client.get("/api/setup-status")

    assert resp.status == 200
    data = await resp.json()
    assert data["first_run"] is False
    assert data["provider_ready"] is True
    assert data["chat_assignment_ready"] is False
    assert data["redirect_to"] == ""


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
async def test_verify_openai_provider_route(aiohttp_client):
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    with patch("openbad.wui.server.openai_provider") as provider_factory:
        provider = provider_factory.return_value
        provider.health_check = AsyncMock(
            return_value=type(
                "Status",
                (),
                {"available": True, "latency_ms": 9.0, "models_available": 2},
            )()
        )
        provider.list_models = AsyncMock(
            return_value=[
                type("Model", (), {"model_id": "gpt-4o-mini"})(),
                type("Model", (), {"model_id": "gpt-4.1"})(),
            ]
        )
        resp = await client.post(
            "/api/providers/verify",
            json={
                "provider_type": "openai",
                "api_key": "secret-key",
            },
        )

    assert resp.status == 200
    data = await resp.json()
    assert data["available"] is True
    assert data["provider"]["name"] == "openai"
    assert data["provider"]["has_api_key"] is True
    assert "gpt-4o-mini" in data["models"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload", "expected_status"),
    [
        ("get", "/api/wiring/providers", None, 200),
        ("put", "/api/wiring/providers", {"enabled": True, "providers": []}, 405),
        ("post", "/api/wiring/providers/verify", {"provider_type": "github-copilot"}, 405),
        ("post", "/api/wiring/providers/copilot/device-code", {"timeout_ms": 30000}, 405),
        ("post", "/api/wiring/providers/copilot/complete", {"flow_id": "flow-1"}, 405),
    ],
)
async def test_legacy_provider_prefix_routes_removed(
    aiohttp_client, method, path, payload, expected_status
):
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    request = getattr(client, method)
    kwargs = {}
    if payload is not None:
        kwargs["json"] = payload

    resp = await request(path, **kwargs)

    assert resp.status == expected_status
    assert resp.status != 301


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


@pytest.mark.asyncio
async def test_get_provider_models_includes_persisted_system_model_when_discovery_varies(
    aiohttp_client, tmp_path, monkeypatch
):
    config_dir = tmp_path / "openbad"
    config_dir.mkdir()
    (config_dir / "cognitive.yaml").write_text(
        yaml.safe_dump(
            {
                "cognitive": {
                    "enabled": True,
                    "providers": [
                        {
                            "name": "github-copilot",
                            "base_url": "https://api.githubcopilot.com",
                            "api_key_env": "GITHUB_COPILOT_TOKEN",
                            "timeout_ms": 30000,
                            "enabled": True,
                        }
                    ],
                    "systems": {
                        "chat": {"provider": "github-copilot", "model": "gpt-5.1"},
                    },
                }
            },
            sort_keys=False,
        )
    )
    monkeypatch.setenv("OPENBAD_CONFIG_DIR", str(config_dir))

    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    with patch("openbad.wui.server._build_wizard_adapter") as adapter_builder:
        adapter = adapter_builder.return_value
        adapter.list_models = AsyncMock(
            return_value=[
                type(
                    "Model",
                    (),
                    {"model_id": "gpt-4o", "provider": "github-copilot", "context_window": 128000},
                )(),
            ]
        )

        resp = await client.get("/api/providers/github-copilot/models")

    assert resp.status == 200
    data = await resp.json()
    model_ids = [m["model_id"] for m in data["models"]]
    assert "gpt-5.1" in model_ids
    assert "gpt-4o" in model_ids


# ── System assignment endpoints ──────────────────────────────────── #


def _cognitive_yaml_with_systems(tmp_path, monkeypatch, *, extra_sections=None):
    """Create a temporary cognitive.yaml with systems + fallback chain."""
    config_dir = tmp_path / "openbad"
    config_dir.mkdir(exist_ok=True)
    base = {
        "cognitive": {
            "enabled": True,
            "providers": [
                {
                    "name": "ollama",
                    "base_url": "http://localhost:11434",
                    "timeout_ms": 30000,
                    "enabled": True,
                },
            ],
            "systems": {
                "chat": {"provider": "ollama", "model": "llama3.2"},
                "sleep": {"provider": "ollama", "model": "bonsai-8b"},
                "reasoning": {"provider": "ollama", "model": "llama3.2"},
                "reactions": {"provider": "ollama", "model": "bonsai-8b"},
            },
            "default_fallback_chain": [
                {"provider": "ollama", "model": "bonsai-8b"},
                {"provider": "ollama", "model": "llama3.2"},
            ],
        }
    }
    if extra_sections:
        base["cognitive"].update(extra_sections)
    config_path = config_dir / "cognitive.yaml"
    config_path.write_text(yaml.safe_dump(base, sort_keys=False))
    monkeypatch.setenv("OPENBAD_CONFIG_DIR", str(config_dir))
    return config_dir


@pytest.mark.asyncio
async def test_get_systems_returns_assignments(aiohttp_client, tmp_path, monkeypatch):
    _cognitive_yaml_with_systems(tmp_path, monkeypatch)
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    resp = await client.get("/api/systems")

    assert resp.status == 200
    data = await resp.json()
    assert data["systems"]["chat"]["provider"] == "ollama"
    assert data["systems"]["chat"]["model"] == "llama3.2"
    assert data["systems"]["sleep"]["model"] == "bonsai-8b"
    assert len(data["fallback_chain"]) == 2
    assert data["fallback_chain"][0]["model"] == "bonsai-8b"


@pytest.mark.asyncio
async def test_get_systems_includes_enabled_providers(aiohttp_client, tmp_path, monkeypatch):
    _cognitive_yaml_with_systems(tmp_path, monkeypatch)
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    resp = await client.get("/api/systems")
    data = await resp.json()

    provider_names = [p["name"] for p in data["providers"]]
    assert provider_names == ["ollama"]


@pytest.mark.asyncio
async def test_resolve_chat_adapter_requires_explicit_system_model(aiohttp_client, tmp_path, monkeypatch):
    import openbad.wui.server as srv

    config_dir = tmp_path / "openbad"
    config_dir.mkdir()
    monkeypatch.setenv("OPENBAD_CONFIG_DIR", str(config_dir))

    config = CognitiveConfig(
        providers=[
            ProviderConfig(
                name="github-copilot",
                base_url="https://api.githubcopilot.com",
                model="gpt-4o",
                api_key_env="GITHUB_COPILOT_TOKEN",
            )
        ],
        systems={
            CognitiveSystem.CHAT: SystemAssignment(provider="github-copilot", model=""),
            CognitiveSystem.REASONING: SystemAssignment(),
            CognitiveSystem.REACTIONS: SystemAssignment(),
            CognitiveSystem.SLEEP: SystemAssignment(),
        },
    )

    adapter, model, provider_name, is_fallback = srv._resolve_chat_adapter(config, "chat")

    assert adapter is None
    assert model is None
    assert provider_name == ""


@pytest.mark.asyncio
async def test_resolve_chat_adapter_falls_back_to_first_valid_provider_when_assignment_invalid(
    aiohttp_client, tmp_path, monkeypatch
):
    import openbad.wui.server as srv

    config_dir = tmp_path / "openbad"
    config_dir.mkdir()
    monkeypatch.setenv("OPENBAD_CONFIG_DIR", str(config_dir))

    config = CognitiveConfig(
        providers=[
            ProviderConfig(
                name="openai",
                base_url="https://api.openai.com",
                model="",
                api_key="secret-key",
                api_key_env="OPENAI_API_KEY",
                enabled=True,
            )
        ],
        systems={
            CognitiveSystem.CHAT: SystemAssignment(provider="github-copilot", model="gpt-4o"),
            CognitiveSystem.REASONING: SystemAssignment(),
            CognitiveSystem.REACTIONS: SystemAssignment(),
            CognitiveSystem.SLEEP: SystemAssignment(),
        },
    )

    adapter, model, provider_name, is_fallback = srv._resolve_chat_adapter(config, "chat")

    assert adapter is not None
    assert model == "openai/gpt-4o-mini"
    assert provider_name == "openai"


@pytest.mark.asyncio
async def test_resolve_chat_adapter_fallback_uses_model_from_other_system_assignment(
    aiohttp_client, tmp_path, monkeypatch
):
    import openbad.wui.server as srv

    config_dir = tmp_path / "openbad"
    config_dir.mkdir()
    monkeypatch.setenv("OPENBAD_CONFIG_DIR", str(config_dir))

    config = CognitiveConfig(
        providers=[
            ProviderConfig(
                name="custom",
                base_url="http://localhost:11434/v1",
                model="",
                api_key="",
                api_key_env="",
                enabled=True,
            ),
            ProviderConfig(
                name="github-copilot",
                base_url="https://api.githubcopilot.com",
                model="",
                api_key="",
                api_key_env="GITHUB_COPILOT_TOKEN",
                enabled=True,
            ),
        ],
        systems={
            CognitiveSystem.CHAT: SystemAssignment(provider="github-copilot", model="gpt-5.1"),
            CognitiveSystem.REASONING: SystemAssignment(),
            CognitiveSystem.REACTIONS: SystemAssignment(provider="custom", model="Bonsai-8B.gguf"),
            CognitiveSystem.SLEEP: SystemAssignment(),
            CognitiveSystem.DOCTOR: SystemAssignment(provider="custom", model="Bonsai-8B.gguf"),
        },
    )

    adapter, model, provider_name, is_fallback = srv._resolve_chat_adapter(config, "chat")

    assert adapter is not None
    assert model == "openai/Bonsai-8B.gguf"
    assert provider_name == "custom"


@pytest.mark.asyncio
async def test_put_systems_updates_config(aiohttp_client, tmp_path, monkeypatch):
    config_dir = _cognitive_yaml_with_systems(tmp_path, monkeypatch)
    (config_dir / "model_routing.yaml").write_text("chains: {}\n")
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    payload = {
        "systems": {
            "chat": {"provider": "ollama", "model": "bonsai-8b"},
            "sleep": {"provider": "ollama", "model": "bonsai-8b"},
            "reasoning": {"provider": "ollama", "model": "bonsai-8b"},
            "reactions": {"provider": "ollama", "model": "llama3.2"},
        },
        "fallback_chain": [
            {"provider": "ollama", "model": "llama3.2"},
        ],
    }
    resp = await client.put("/api/systems", json=payload)

    assert resp.status == 200
    data = await resp.json()
    assert data["systems"]["chat"]["model"] == "bonsai-8b"
    assert len(data["fallback_chain"]) == 1

    saved = yaml.safe_load((config_dir / "cognitive.yaml").read_text())
    assert saved["cognitive"]["systems"]["chat"]["model"] == "bonsai-8b"
    assert len(saved["cognitive"]["default_fallback_chain"]) == 1

    routing = yaml.safe_load((config_dir / "model_routing.yaml").read_text())
    assert routing["chains"]["critical"][0]["model"] == "bonsai-8b"
    assert stat.S_IMODE(os.stat(config_dir / "model_routing.yaml").st_mode) == 0o600


@pytest.mark.asyncio
async def test_put_systems_preserves_other_sections(aiohttp_client, tmp_path, monkeypatch):
    config_dir = _cognitive_yaml_with_systems(
        tmp_path, monkeypatch, extra_sections={"model_routing": {"tier": "balanced"}}
    )
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    payload = {
        "systems": {
            "chat": {"provider": "ollama", "model": "llama3.2"},
            "sleep": {"provider": "ollama", "model": "bonsai-8b"},
            "reasoning": {"provider": "ollama", "model": "llama3.2"},
            "reactions": {"provider": "ollama", "model": "bonsai-8b"},
        },
        "fallback_chain": [],
    }
    resp = await client.put("/api/systems", json=payload)
    assert resp.status == 200

    saved = yaml.safe_load((config_dir / "cognitive.yaml").read_text())
    assert saved["cognitive"]["model_routing"]["tier"] == "balanced"
    assert saved["cognitive"]["providers"][0]["name"] == "ollama"


@pytest.mark.asyncio
async def test_put_systems_rejects_unknown_system(aiohttp_client, tmp_path, monkeypatch):
    _cognitive_yaml_with_systems(tmp_path, monkeypatch)
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    payload = {
        "systems": {"badname": {"provider": "x", "model": "y"}},
        "fallback_chain": [],
    }
    resp = await client.put("/api/systems", json=payload)
    assert resp.status == 400
    text = await resp.text()
    assert "unknown system" in text


@pytest.mark.asyncio
async def test_put_systems_rejects_invalid_body(aiohttp_client, tmp_path, monkeypatch):
    _cognitive_yaml_with_systems(tmp_path, monkeypatch)
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    resp = await client.put("/api/systems", json={"systems": "bad", "fallback_chain": []})
    assert resp.status == 400


# ── Senses endpoint tests ──────────────────────────────────────── #


def _senses_yaml(tmp_path, monkeypatch):
    """Create a temporary senses.yaml and point env there."""
    config_dir = tmp_path / "openbad"
    config_dir.mkdir(exist_ok=True)
    data = {
        "hearing": {
            "capture": {"sample_rate": 16000},
            "asr": {"default_engine": "vosk", "vad_sensitivity": 0.5},
            "wake_word": {"phrases": ["hey agent"], "threshold": 0.5},
        },
        "vision": {
            "fps_idle": 1.0,
            "fps_active": 5.0,
            "capture_region": "active-window",
            "capture_interval_s": 1.0,
            "max_resolution": [1920, 1080],
            "compression": {"format": "jpeg", "quality": 85},
            "attention": {"ssim_threshold": 0.05, "cooldown_ms": 500, "roi_enabled": False},
        },
        "speech": {
            "tts": {"engine": "piper", "speaking_rate": 1.0, "volume": 1.0},
        },
    }
    (config_dir / "senses.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    monkeypatch.setenv("OPENBAD_CONFIG_DIR", str(config_dir))
    return config_dir


@pytest.mark.asyncio
async def test_get_senses(aiohttp_client, tmp_path, monkeypatch):
    _senses_yaml(tmp_path, monkeypatch)
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)
    resp = await client.get("/api/senses")
    assert resp.status == 200
    data = await resp.json()
    assert "hearing" in data
    assert "vision" in data
    assert "speech" in data
    assert data["hearing"]["asr"]["default_engine"] == "vosk"
    assert data["vision"]["capture_region"] == "active-window"
    assert data["speech"]["tts"]["engine"] == "piper"


@pytest.mark.asyncio
async def test_put_senses_valid(aiohttp_client, tmp_path, monkeypatch):
    config_dir = _senses_yaml(tmp_path, monkeypatch)
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    payload = {
        "hearing": {
            "capture": {"sample_rate": 22050},
            "asr": {"default_engine": "whisper", "vad_sensitivity": 0.7},
            "wake_word": {"phrases": ["computer"], "threshold": 0.6},
        },
        "vision": {
            "fps_idle": 2.0,
            "fps_active": 10.0,
            "capture_region": "full-screen",
            "capture_interval_s": 0.5,
            "max_resolution": [3840, 2160],
            "compression": {"format": "png", "quality": 100},
            "attention": {"ssim_threshold": 0.1, "cooldown_ms": 1000, "roi_enabled": True},
        },
        "speech": {
            "tts": {"engine": "espeak", "speaking_rate": 1.5, "volume": 0.8},
        },
    }
    resp = await client.put("/api/senses", json=payload)
    assert resp.status == 200
    data = await resp.json()
    assert data["hearing"]["asr"]["default_engine"] == "whisper"
    assert data["vision"]["capture_region"] == "full-screen"
    assert data["speech"]["tts"]["engine"] == "espeak"

    # Verify file was written
    saved = yaml.safe_load((config_dir / "senses.yaml").read_text())
    assert saved["hearing"]["asr"]["default_engine"] == "whisper"


@pytest.mark.asyncio
async def test_put_senses_validation_error(aiohttp_client, tmp_path, monkeypatch):
    _senses_yaml(tmp_path, monkeypatch)
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    payload = {
        "hearing": {
            "asr": {"default_engine": "invalid-engine"},
            "wake_word": {"phrases": ["hey"], "threshold": 0.5},
        },
    }
    resp = await client.put("/api/senses", json=payload)
    assert resp.status == 400
    text = await resp.text()
    assert "default_engine" in text


@pytest.mark.asyncio
async def test_put_senses_rejects_non_object(aiohttp_client, tmp_path, monkeypatch):
    _senses_yaml(tmp_path, monkeypatch)
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    resp = await client.put("/api/senses", json="bad")
    assert resp.status == 400


# ── Sleep config endpoint tests ─────────────────────────────────── #


@pytest.mark.asyncio
async def test_get_sleep_config_defaults(aiohttp_client, tmp_path, monkeypatch):
    config_dir = tmp_path / "openbad"
    config_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("OPENBAD_CONFIG_DIR", str(config_dir))

    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    resp = await client.get("/api/sleep/config")

    assert resp.status == 200
    data = await resp.json()
    assert data["sleep"]["sleep_window_start"] == "02:00"
    assert data["sleep"]["sleep_window_duration_hours"] == 3.0
    assert data["sleep"]["idle_timeout_minutes"] == 15
    assert data["sleep"]["allow_daytime_naps"] is True


@pytest.mark.asyncio
async def test_put_sleep_config_persists_memory_yaml(aiohttp_client, tmp_path, monkeypatch):
    config_dir = tmp_path / "openbad"
    config_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("OPENBAD_CONFIG_DIR", str(config_dir))

    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    payload = {
        "sleep": {
            "sleep_window_start": "01:30",
            "sleep_window_duration_hours": 2.5,
            "idle_timeout_minutes": 20,
            "allow_daytime_naps": False,
            "enabled": True,
        }
    }
    resp = await client.put("/api/sleep/config", json=payload)

    assert resp.status == 200
    data = await resp.json()
    assert data["sleep"]["sleep_window_start"] == "01:30"
    assert data["sleep"]["idle_timeout_minutes"] == 20
    assert data["next_scheduled_consolidation"] is not None

    saved = yaml.safe_load((config_dir / "memory.yaml").read_text())
    assert saved["memory"]["sleep"]["sleep_window_start"] == "01:30"
    assert saved["memory"]["sleep"]["sleep_window_duration_hours"] == 2.5
    assert saved["memory"]["sleep"]["allow_daytime_naps"] is False
    assert saved["onboarding"]["sleep_configured"] is True


@pytest.mark.asyncio
async def test_get_onboarding_status_marks_saved_default_sleep_as_complete(
    aiohttp_client, tmp_path, monkeypatch
):
    """Explicitly saved default sleep values should satisfy onboarding."""
    cfg_path = tmp_path / "identity.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "user": {"name": "User"},
        "assistant": {"name": "OpenBaD"},
    }))

    cognitive_path = tmp_path / "cognitive.yaml"
    cognitive_path.write_text(
        yaml.safe_dump(
            {
                "cognitive": {
                    "providers": [
                        {
                            "name": "github-copilot",
                            "base_url": "https://api.githubcopilot.com",
                            "api_key_env": "GITHUB_COPILOT_TOKEN",
                            "enabled": True,
                        }
                    ],
                    "systems": {
                        "chat": {
                            "provider": "github-copilot",
                            "model": "gpt-4o",
                        }
                    },
                }
            },
            sort_keys=False,
        )
    )

    memory_path = tmp_path / "memory.yaml"
    memory_path.write_text(
        yaml.safe_dump(
            {
                "memory": {
                    "sleep": {
                        "sleep_window_start": "02:00",
                        "sleep_window_duration_hours": 1.0,
                        "idle_timeout_minutes": 15,
                        "allow_daytime_naps": True,
                        "enabled": True,
                    }
                },
                "onboarding": {"sleep_configured": True},
            },
            sort_keys=False,
        )
    )

    monkeypatch.setenv("OPENBAD_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_COPILOT_TOKEN", "test-token")

    from openbad.identity.persistence import IdentityPersistence
    from openbad.memory.episodic import EpisodicMemory

    ep = EpisodicMemory(tmp_path / "ep.json", auto_persist=True)
    persistence = IdentityPersistence(cfg_path, ep)

    app = create_app(enable_mqtt=False)
    app["identity_persistence"] = persistence
    client = await aiohttp_client(app)

    resp = await client.get("/api/onboarding/status")

    assert resp.status == 200
    data = await resp.json()
    assert data["providers_complete"] is True
    assert data["sleep_complete"] is True
    assert data["next_step"] == "assistant_identity"
    assert data["redirect_to"] == "/chat?onboarding=assistant"


@pytest.mark.asyncio
async def test_sleep_trigger_and_wake_update_last_summary(aiohttp_client):
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    trigger_resp = await client.post("/api/sleep/trigger")
    assert trigger_resp.status == 200

    wake_resp = await client.post("/api/sleep/wake")
    assert wake_resp.status == 200

    cfg = await client.get("/api/sleep/config")
    data = await cfg.json()
    assert data["last_consolidation_summary"]["state"] == "manual_wake_requested"


# ---------------------------------------------------------------------------
# Toolbelt API tests
# ---------------------------------------------------------------------------


def _app_with_registry():
    """Create an app with an in-memory ToolRegistry attached."""
    from openbad.proprioception.registry import ToolRegistry, ToolRole

    app = create_app(enable_mqtt=False)
    registry = ToolRegistry(timeout=30.0)
    registry.register("cli-tool", role=ToolRole.CLI)
    registry.register("web-search", role=ToolRole.WEB_SEARCH)
    registry.register("alt-search", role=ToolRole.WEB_SEARCH)
    app["registry"] = registry
    return app


def _configure_toolbelt_state(monkeypatch, db_path):
    import openbad.toolbelt.access_control as access_control
    import openbad.toolbelt.terminal_sessions as terminal_sessions

    monkeypatch.setattr(access_control, "DEFAULT_STATE_DB_PATH", db_path)
    monkeypatch.setattr(terminal_sessions, "DEFAULT_STATE_DB_PATH", db_path)
    monkeypatch.setattr(
        terminal_sessions,
        "_DEFAULT_MANAGER",
        terminal_sessions.TerminalSessionManager(db_path=db_path, idle_timeout_s=60.0),
    )


@pytest.mark.asyncio
async def test_get_toolbelt_no_registry(aiohttp_client):
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)
    resp = await client.get("/api/toolbelt")
    assert resp.status == 200
    data = await resp.json()
    assert "cabinet" in data
    assert "belt" in data
    assert "chat_callable_tools" in data
    assert "tool_surfaces" in data
    assert "cli" in data["cabinet"]
    assert "code" in data["cabinet"]


@pytest.mark.asyncio
async def test_runtime_registry_contains_split_diagnostics_tools(aiohttp_client):
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    resp = await client.get("/api/toolbelt")
    assert resp.status == 200
    data = await resp.json()

    all_names = {
        item["name"]
        for tools in data["cabinet"].values()
        for item in tools
    }
    assert "mqtt-records-tool" in all_names
    assert "system-logs-tool" in all_names
    assert "endocrine-status-tool" in all_names
    assert "tasks-diagnostics-tool" in all_names
    assert "research-diagnostics-tool" in all_names


@pytest.mark.asyncio
async def test_get_toolbelt_with_registry(aiohttp_client):
    app = _app_with_registry()
    client = await aiohttp_client(app)
    resp = await client.get("/api/toolbelt")
    assert resp.status == 200
    data = await resp.json()
    assert "cabinet" in data
    assert "belt" in data
    assert data["tool_surfaces"]["runtime_belt"]
    assert any(tool["name"] == "create_research_node" for tool in data["chat_callable_tools"])
    assert "cli" in data["cabinet"]
    assert "web_search" in data["cabinet"]


@pytest.mark.asyncio
async def test_put_toolbelt_equip(aiohttp_client):
    app = _app_with_registry()
    client = await aiohttp_client(app)
    resp = await client.put(
        "/api/toolbelt/cli",
        json={"tool": "cli-tool"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["belt"].get("cli") == "cli-tool"


@pytest.mark.asyncio
async def test_put_toolbelt_bad_role(aiohttp_client):
    app = _app_with_registry()
    client = await aiohttp_client(app)
    resp = await client.put(
        "/api/toolbelt/nonexistent_role",
        json={"tool": "cli-tool"},
    )
    assert resp.status == 400


@pytest.mark.asyncio
async def test_put_toolbelt_missing_tool_field(aiohttp_client):
    app = _app_with_registry()
    client = await aiohttp_client(app)
    resp = await client.put(
        "/api/toolbelt/cli",
        json={},
    )
    assert resp.status == 400


@pytest.mark.asyncio
async def test_delete_toolbelt_unequip(aiohttp_client):
    app = _app_with_registry()
    # First equip
    registry = app["registry"]
    from openbad.proprioception.registry import ToolRole
    registry.equip(ToolRole.CLI, "cli-tool")

    client = await aiohttp_client(app)
    resp = await client.delete("/api/toolbelt/cli")
    assert resp.status == 200
    data = await resp.json()
    assert data["belt"].get("cli") is None


@pytest.mark.asyncio
async def test_toolbelt_access_endpoints(aiohttp_client, tmp_path, monkeypatch):
    _configure_toolbelt_state(monkeypatch, tmp_path / "state.db")

    from openbad.toolbelt.access_control import create_access_request

    requested_dir = tmp_path / "requested"
    requested_dir.mkdir()
    record = create_access_request(
        str(requested_dir),
        requester="test-suite",
        reason="Need access for inspection",
    )

    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    resp = await client.get("/api/toolbelt/access")
    assert resp.status == 200
    data = await resp.json()
    assert data["pending_requests"]
    assert data["pending_requests"][0]["request_id"] == record["request"]["request_id"]

    approve_resp = await client.post(
        f"/api/toolbelt/access/requests/{record['request']['request_id']}/approve",
        json={"approved_by": "tester", "reason": "Approved for tests"},
    )
    assert approve_resp.status == 200
    approve_data = await approve_resp.json()
    assert approve_data["request"]["status"] == "approved"
    assert approve_data["grant"]["normalized_root"] == str(requested_dir.resolve())

    delete_resp = await client.delete(
        f"/api/toolbelt/access/grants/{approve_data['grant']['grant_id']}?revoked_by=tester"
    )
    assert delete_resp.status == 200
    delete_data = await delete_resp.json()
    assert delete_data["revoked_by"] == "tester"
    assert delete_data["revoked_at"] is not None


@pytest.mark.asyncio
async def test_toolbelt_access_deny_endpoint(aiohttp_client, tmp_path, monkeypatch):
    _configure_toolbelt_state(monkeypatch, tmp_path / "state.db")

    from openbad.toolbelt.access_control import create_access_request

    requested_dir = tmp_path / "denied-request"
    requested_dir.mkdir()
    record = create_access_request(
        str(requested_dir),
        requester="test-suite",
        reason="Need access for inspection",
    )

    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    deny_resp = await client.post(
        f"/api/toolbelt/access/requests/{record['request']['request_id']}/deny",
        json={"denied_by": "tester", "reason": "Declined"},
    )
    assert deny_resp.status == 200
    deny_data = await deny_resp.json()
    assert deny_data["request"]["status"] == "denied"
    assert deny_data["request"]["decided_by"] == "tester"

    access_resp = await client.get("/api/toolbelt/access")
    access_data = await access_resp.json()
    assert access_data["pending_requests"] == []


@pytest.mark.asyncio
async def test_toolbelt_terminal_endpoints(aiohttp_client, tmp_path, monkeypatch):
    _configure_toolbelt_state(monkeypatch, tmp_path / "state.db")

    requested_dir = tmp_path / "terminal-root"
    requested_dir.mkdir()

    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    denied_resp = await client.post(
        "/api/toolbelt/terminal",
        json={"cwd": str(requested_dir), "requester": "test-suite"},
    )
    assert denied_resp.status == 403
    denied_data = await denied_resp.json()
    assert denied_data["access_request"]["status"] == "pending"

    request_id = denied_data["access_request"]["request"]["request_id"]
    approve_resp = await client.post(
        f"/api/toolbelt/access/requests/{request_id}/approve",
        json={"approved_by": "tester"},
    )
    assert approve_resp.status == 200

    create_resp = await client.post(
        "/api/toolbelt/terminal",
        json={"cwd": str(requested_dir), "requester": "test-suite"},
    )
    assert create_resp.status == 201
    session = await create_resp.json()
    session_id = session["session_id"]
    assert session["cwd"] == str(requested_dir.resolve())

    input_resp = await client.post(
        f"/api/toolbelt/terminal/{session_id}/input",
        json={"input": "printf 'hello-from-terminal'", "append_newline": True},
    )
    assert input_resp.status == 200

    output = ""
    for _ in range(10):
        output_resp = await client.get(f"/api/toolbelt/terminal/{session_id}/output?max_bytes=4096")
        assert output_resp.status == 200
        output_data = await output_resp.json()
        output += output_data.get("output", "")
        if "hello-from-terminal" in output:
            break
    assert "hello-from-terminal" in output

    list_resp = await client.get("/api/toolbelt/terminal")
    assert list_resp.status == 200
    list_data = await list_resp.json()
    assert any(item["session_id"] == session_id for item in list_data["sessions"])

    delete_resp = await client.delete(f"/api/toolbelt/terminal/{session_id}?reason=test-cleanup")
    assert delete_resp.status == 200
    delete_data = await delete_resp.json()
    assert delete_data["session_id"] == session_id


# ---------------------------------------------------------------------- #
# Entity endpoints
# ---------------------------------------------------------------------- #


def _app_with_persistence(tmp_path):
    """Create a WUI app with IdentityPersistence wired in."""

    cfg_path = tmp_path / "identity.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "user": {
                    "name": "Alice",
                    "preferred_name": "Ali",
                    "communication_style": "casual",
                    "expertise_domains": ["python"],
                    "interaction_history_summary": "",
                },
                "assistant": {
                    "name": "OpenBaD",
                    "persona_summary": "Helpful",
                    "learning_focus": [],
                    "ocean": {
                        "openness": 0.7,
                        "conscientiousness": 0.8,
                        "extraversion": 0.5,
                        "agreeableness": 0.4,
                        "stability": 0.6,
                    },
                },
            },
            default_flow_style=False,
        ),
        encoding="utf-8",
    )

    from openbad.identity.persistence import IdentityPersistence
    from openbad.memory.episodic import EpisodicMemory

    ep = EpisodicMemory(tmp_path / "ep.json", auto_persist=True)
    persistence = IdentityPersistence(cfg_path, ep)

    app = create_app(enable_mqtt=False)
    app["identity_persistence"] = persistence
    return app


@pytest.mark.asyncio
async def test_get_entity_user(aiohttp_client, tmp_path):
    app = _app_with_persistence(tmp_path)
    client = await aiohttp_client(app)
    resp = await client.get("/api/entity/user")
    assert resp.status == 200
    data = await resp.json()
    assert data["name"] == "Alice"
    assert data["communication_style"] == "casual"


@pytest.mark.asyncio
async def test_get_entity_assistant(aiohttp_client, tmp_path):
    app = _app_with_persistence(tmp_path)
    client = await aiohttp_client(app)
    resp = await client.get("/api/entity/assistant")
    assert resp.status == 200
    data = await resp.json()
    assert data["name"] == "OpenBaD"
    assert data["openness"] == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_put_entity_user(aiohttp_client, tmp_path):
    app = _app_with_persistence(tmp_path)
    client = await aiohttp_client(app)
    resp = await client.put(
        "/api/entity/user",
        json={"preferred_name": "Bob"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["preferred_name"] == "Bob"


@pytest.mark.asyncio
async def test_put_entity_assistant(aiohttp_client, tmp_path):
    app = _app_with_persistence(tmp_path)
    client = await aiohttp_client(app)
    resp = await client.put(
        "/api/entity/assistant",
        json={"openness": 0.9},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["openness"] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_put_entity_user_bad_field(aiohttp_client, tmp_path):
    app = _app_with_persistence(tmp_path)
    client = await aiohttp_client(app)
    resp = await client.put(
        "/api/entity/user",
        json={"nonexistent": "x"},
    )
    assert resp.status == 400


@pytest.mark.asyncio
async def test_post_entity_user_reset(aiohttp_client, tmp_path):
    app = _app_with_persistence(tmp_path)
    client = await aiohttp_client(app)
    # Modify first
    await client.put("/api/entity/user", json={"preferred_name": "Changed"})
    # Reset
    resp = await client.post("/api/entity/user/reset")
    assert resp.status == 200
    data = await resp.json()
    assert data["preferred_name"] == "Ali"


@pytest.mark.asyncio
async def test_post_entity_assistant_reset(aiohttp_client, tmp_path):
    app = _app_with_persistence(tmp_path)
    client = await aiohttp_client(app)
    await client.put("/api/entity/assistant", json={"openness": 0.1})
    resp = await client.post("/api/entity/assistant/reset")
    assert resp.status == 200
    data = await resp.json()
    assert data["openness"] == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_entity_no_persistence_returns_503(aiohttp_client):
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)
    resp = await client.get("/api/entity/user")
    assert resp.status == 503


@pytest.mark.asyncio
async def test_chat_stream_route_emits_session_id_and_tokens(aiohttp_client, monkeypatch):
    import openbad.wui.server as srv
    from openbad.cognitive.config import CognitiveConfig
    from openbad.wui.chat_pipeline import StreamChunk

    monkeypatch.setattr(srv, "_read_providers_config", lambda: (BUILD_DIR, CognitiveConfig()))
    monkeypatch.setattr(
        srv,
        "_resolve_chat_adapter",
        lambda _config, _system_name: (object(), "test-model", "test-provider"),
    )

    async def _fake_stream_chat(*args, **kwargs):
        assert kwargs["provider_name"] == "test-provider"
        assert args[3] == "session-123"
        yield StreamChunk(token="hello", tokens_used=1)  # noqa: S106
        yield StreamChunk(done=True, tokens_used=1)

    monkeypatch.setattr(srv, "stream_chat", _fake_stream_chat)

    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)
    resp = await client.post(
        "/api/chat/stream",
        json={"message": "hi", "system": "CHAT", "session_id": "session-123"},
    )

    assert resp.status == 200
    body = await resp.text()
    assert "session-123" in body
    assert "hello" in body
    assert "[DONE]" in body


@pytest.mark.asyncio
async def test_chat_history_route_returns_serialized_messages(aiohttp_client, monkeypatch):
    import openbad.wui.server as srv
    from openbad.wui.chat_pipeline import ConversationTurn

    monkeypatch.setattr(
        srv,
        "get_conversation_history",
        lambda session_id, limit=50: [
            ConversationTurn(role="user", content="hello", timestamp=1_700_000_000.0),
            ConversationTurn(role="assistant", content="world", timestamp=1_700_000_001.0),
        ] if session_id == "session-abc" and limit == 50 else [],
    )

    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)
    resp = await client.get("/api/chat/history", params={"session_id": "session-abc"})

    assert resp.status == 200
    data = await resp.json()
    assert data["session_id"] == "session-abc"
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][0]["timestamp"].endswith("+00:00")
    assert data["messages"][1]["content"] == "world"


@pytest.mark.asyncio
async def test_get_usage_route_returns_usage_snapshot(aiohttp_client, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBAD_USAGE_DB", str(tmp_path / "usage.db"))
    monkeypatch.setattr("openbad.state.event_log.setup_logging", lambda *args, **kwargs: None)

    app = create_app(enable_mqtt=False)
    tracker = app["usage_tracker"]
    tracker.record(
        provider="openai",
        model="gpt-4o",
        system="chat",
        tokens=320,
        request_id="req-1",
        session_id="85d1ce5f-b679-4d5e-a251-2a27bd0b1f91",
    )
    tracker.record(
        provider="anthropic",
        model="claude-sonnet",
        system="reasoning",
        tokens=180,
        request_id="req-2",
        session_id="doctor-autonomy",
    )

    client = await aiohttp_client(app)
    resp = await client.get("/api/usage")

    assert resp.status == 200
    data = await resp.json()
    assert data["summary"]["total_used"] == 500
    assert data["summary"]["request_count"] == 2
    assert data["by_provider_model"][0]["tokens"] == 320
    assert {item["system"] for item in data["by_system"]} == {"chat", "reasoning"}
    assert {item["session_id"] for item in data["by_session"]} == {
        "85d1ce5f-b679-4d5e-a251-2a27bd0b1f91",
        "doctor-autonomy",
    }
    session_types = {item["session_type"]: item for item in data["by_session"]}
    assert session_types["chat"]["type_label"] == "Chat"
    assert session_types["doctor"]["type_label"] == "Doctor"
    assert {item["session_type"] for item in data["by_session_type"]} == {"chat", "doctor"}
    assert data["recent_events"][0]["session_type"] in {"chat", "doctor"}
    assert data["recent_events"][0]["tokens"] in {180, 320}


@pytest.mark.asyncio
async def test_get_version_route_returns_current_version(aiohttp_client):
    import openbad

    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)
    resp = await client.get("/api/version")

    assert resp.status == 200
    data = await resp.json()
    assert data["version"] == openbad.__version__


# ── Onboarding endpoint tests ──────────────────────────────────────────



# ── Onboarding endpoint tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_onboarding_status_with_default_profiles(aiohttp_client, tmp_path):
    """GET /api/onboarding/status with default OpenBaD assistant."""
    cfg_path = tmp_path / "identity.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "user": {"name": "User"},
        "assistant": {"name": "OpenBaD"},
    }))

    from openbad.identity.persistence import IdentityPersistence
    from openbad.memory.episodic import EpisodicMemory

    ep = EpisodicMemory(tmp_path / "ep.json", auto_persist=True)
    persistence = IdentityPersistence(cfg_path, ep)

    app = create_app(enable_mqtt=False)
    app["identity_persistence"] = persistence
    client = await aiohttp_client(app)

    resp = await client.get("/api/onboarding/status")

    assert resp.status == 200
    data = await resp.json()
    assert data["assistant_identity_complete"] is False  # OpenBaD is default
    assert data["user_profile_complete"] is False  # "User" is default
    assert data["next_step"] == "providers"
    assert data["redirect_to"] == "/providers?wizard=1"


@pytest.mark.asyncio
async def test_get_onboarding_status_with_configured_profiles(aiohttp_client, tmp_path):
    """GET /api/onboarding/status detects configured assistant and user."""
    cfg_path = tmp_path / "identity.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "user": {
            "name": "Bob",
            "expertise_domains": ["Python"],
        },
        "assistant": {
            "name": "Cortex",
            "persona_summary": "A helpful assistant",
        },
    }))

    from openbad.identity.persistence import IdentityPersistence
    from openbad.memory.episodic import EpisodicMemory

    ep = EpisodicMemory(tmp_path / "ep.json", auto_persist=True)
    persistence = IdentityPersistence(cfg_path, ep)

    app = create_app(enable_mqtt=False)
    app["identity_persistence"] = persistence
    client = await aiohttp_client(app)

    resp = await client.get("/api/onboarding/status")

    assert resp.status == 200
    data = await resp.json()
    assert data["assistant_identity_complete"] is True
    assert data["user_profile_complete"] is True
    assert data["next_step"] == "providers"
    assert data["redirect_to"] == "/providers?wizard=1"


@pytest.mark.asyncio
async def test_get_onboarding_status_uses_setup_ready_provider_logic(aiohttp_client, tmp_path, monkeypatch):
    """Configured providers plus chat assignment should not redirect back to provider setup."""
    cfg_path = tmp_path / "identity.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "user": {"name": "User"},
        "assistant": {"name": "OpenBaD"},
    }))

    cognitive_path = tmp_path / "cognitive.yaml"
    cognitive_path.write_text(
        yaml.safe_dump(
            {
                "cognitive": {
                    "providers": [
                        {
                            "name": "github-copilot",
                            "base_url": "https://api.githubcopilot.com",
                            "api_key_env": "GITHUB_COPILOT_TOKEN",
                            "enabled": True,
                        }
                    ],
                    "systems": {
                        "chat": {
                            "provider": "github-copilot",
                            "model": "gpt-4o",
                        }
                    },
                },
            },
            sort_keys=False,
        )
    )

    monkeypatch.setenv("OPENBAD_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_COPILOT_TOKEN", "test-token")

    from openbad.identity.persistence import IdentityPersistence
    from openbad.memory.episodic import EpisodicMemory

    ep = EpisodicMemory(tmp_path / "ep.json", auto_persist=True)
    persistence = IdentityPersistence(cfg_path, ep)

    app = create_app(enable_mqtt=False)
    app["identity_persistence"] = persistence
    client = await aiohttp_client(app)

    resp = await client.get("/api/onboarding/status")

    assert resp.status == 200
    data = await resp.json()
    assert data["providers_complete"] is True
    assert data["next_step"] == "sleep"
    assert data["redirect_to"] == "/scheduling?onboarding=sleep"


@pytest.mark.asyncio
async def test_post_assistant_interview_complete_valid_json(aiohttp_client, tmp_path):
    """POST /api/onboarding/assistant/complete extracts and persists profile."""
    cfg_path = tmp_path / "identity.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "user": {"name": "User"},
        "assistant": {"name": "OpenBaD"},
    }))

    from openbad.identity.persistence import IdentityPersistence
    from openbad.identity.personality_modulator import PersonalityModulator
    from openbad.memory.episodic import EpisodicMemory

    ep = EpisodicMemory(tmp_path / "ep.json", auto_persist=True)
    persistence = IdentityPersistence(cfg_path, ep)
    modulator = PersonalityModulator(persistence.assistant)

    app = create_app(enable_mqtt=False)
    app["identity_persistence"] = persistence
    app["personality_modulator"] = modulator
    client = await aiohttp_client(app)

    interview_text = """Great! Here's your configuration:
```json
{
  "name": "Athena",
  "persona_summary": "A knowledge-focused assistant",
  "learning_focus": ["AI", "Philosophy"],
  "worldview": ["Clarity", "Precision"],
  "openness": 0.9,
  "conscientiousness": 0.8
}
```"""

    resp = await client.post(
        "/api/onboarding/assistant/complete",
        json={"interview_text": interview_text}
    )

    assert resp.status == 200
    data = await resp.json()
    assert data["success"] is True
    assert data["profile"]["name"] == "Athena"
    assert data["profile"]["persona_summary"] == "A knowledge-focused assistant"
    assert "AI" in data["profile"]["learning_focus"]


@pytest.mark.asyncio
async def test_post_assistant_interview_complete_no_json_returns_error(aiohttp_client, tmp_path):
    """POST /api/onboarding/assistant/complete returns 400 when no JSON found."""
    app = _app_with_persistence(tmp_path)
    client = await aiohttp_client(app)

    resp = await client.post(
        "/api/onboarding/assistant/complete",
        json={"interview_text": "Just some text without JSON"}
    )

    assert resp.status == 400
    text = await resp.text()
    assert "No valid profile JSON" in text


@pytest.mark.asyncio
async def test_post_user_interview_complete_valid_json(aiohttp_client, tmp_path):
    """POST /api/onboarding/user/complete extracts and persists user profile."""
    cfg_path = tmp_path / "identity.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "user": {"name": "User"},
        "assistant": {"name": "OpenBaD"},
    }))

    from openbad.identity.persistence import IdentityPersistence
    from openbad.memory.episodic import EpisodicMemory

    ep = EpisodicMemory(tmp_path / "ep.json", auto_persist=True)
    persistence = IdentityPersistence(cfg_path, ep)

    app = create_app(enable_mqtt=False)
    app["identity_persistence"] = persistence
    client = await aiohttp_client(app)

    interview_text = """Perfect! Here's what I learned:
```json
{
  "name": "Bob",
  "preferred_name": "Bobby",
  "communication_style": "casual",
  "expertise_domains": ["Backend", "Databases"],
  "interests": ["Photography", "Hiking"],
  "timezone": "America/Chicago",
  "work_hours": [9, 17]
}
```"""

    resp = await client.post(
        "/api/onboarding/user/complete",
        json={"interview_text": interview_text}
    )

    assert resp.status == 200
    data = await resp.json()
    assert data["success"] is True
    assert data["profile"]["name"] == "Bob"
    assert data["profile"]["preferred_name"] == "Bobby"
    assert "Backend" in data["profile"]["expertise_domains"]


@pytest.mark.asyncio
async def test_post_user_interview_complete_no_json_returns_error(aiohttp_client, tmp_path):
    """POST /api/onboarding/user/complete returns 400 when no JSON found."""
    app = _app_with_persistence(tmp_path)
    client = await aiohttp_client(app)

    resp = await client.post(
        "/api/onboarding/user/complete",
        json={"interview_text": "No JSON here"}
    )

    assert resp.status == 400
    text = await resp.text()
    assert "No valid profile JSON" in text


@pytest.mark.asyncio
async def test_post_onboarding_skip_returns_success(aiohttp_client):
    """POST /api/onboarding/skip returns success."""
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    resp = await client.post("/api/onboarding/skip", json={})

    assert resp.status == 200
    data = await resp.json()
    assert data["success"] is True
    assert data["skipped"] is True


@pytest.mark.asyncio
async def test_get_telemetry_config_defaults(aiohttp_client, tmp_path, monkeypatch):
    """GET /api/telemetry/config returns defaults when file is absent."""
    import openbad.wui.server as srv

    monkeypatch.setattr(srv, "_TELEMETRY_CONFIG_PATH", tmp_path / "telemetry.yaml")
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    resp = await client.get("/api/telemetry/config")

    assert resp.status == 200
    data = await resp.json()
    assert data["interval_seconds"] == 5
    assert data["applies_on_restart"] is False


@pytest.mark.asyncio
async def test_put_telemetry_config_persists_interval(aiohttp_client, tmp_path, monkeypatch):
    """PUT /api/telemetry/config persists selected interval."""
    import openbad.wui.server as srv

    cfg_path = tmp_path / "telemetry.yaml"
    monkeypatch.setattr(srv, "_TELEMETRY_CONFIG_PATH", cfg_path)
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    resp = await client.put("/api/telemetry/config", json={"interval_seconds": 9})

    assert resp.status == 200
    data = await resp.json()
    assert data["interval_seconds"] == 9
    assert data["applies_on_restart"] is False
    saved = yaml.safe_load(cfg_path.read_text())
    assert saved["interval_seconds"] == 9


@pytest.mark.asyncio
async def test_post_tasks_creates_task(aiohttp_client, tmp_path, monkeypatch):
    import openbad.state.db as state_db

    db_path = tmp_path / "state.db"
    monkeypatch.setattr(state_db, "DEFAULT_STATE_DB_PATH", db_path)

    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    resp = await client.post(
        "/api/tasks",
        json={"title": "Manual task", "description": "from ui", "owner": "user"},
    )

    assert resp.status == 201
    data = await resp.json()
    assert data["title"] == "Manual task"
    assert data["owner"] == "user"
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_get_tasks_completed_returns_terminal_tasks(aiohttp_client, tmp_path, monkeypatch):
    import openbad.state.db as state_db
    from openbad.tasks.models import TaskStatus
    from openbad.tasks.service import TaskService
    from openbad.state.db import initialize_state_db

    db_path = tmp_path / "state.db"
    monkeypatch.setattr(state_db, "DEFAULT_STATE_DB_PATH", db_path)

    conn = initialize_state_db(db_path)
    service = TaskService(conn)
    done_task = service.create_task("Completed task", owner="user")
    cancelled_task = service.create_task("Cancelled task", owner="user")
    pending_task = service.create_task("Pending task", owner="user")
    service.transition_task(done_task.task_id, TaskStatus.RUNNING)
    service.complete_task(done_task.task_id)
    service.cancel_task(cancelled_task.task_id)

    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    resp = await client.get("/api/tasks/completed?limit=10")

    assert resp.status == 200
    data = await resp.json()
    titles = [task["title"] for task in data["tasks"]]
    assert "Completed task" in titles
    assert "Cancelled task" in titles
    assert "Pending task" not in titles


@pytest.mark.asyncio
async def test_post_research_creates_node(aiohttp_client, tmp_path, monkeypatch):
    import openbad.state.db as state_db

    db_path = tmp_path / "state.db"
    monkeypatch.setattr(state_db, "DEFAULT_STATE_DB_PATH", db_path)

    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)

    resp = await client.post(
        "/api/research",
        json={
            "title": "Investigate provider timeout",
            "description": "check latency trends",
            "priority": 3,
            "source_task_id": "task-123",
        },
    )

    assert resp.status == 201
    data = await resp.json()
    assert data["title"] == "Investigate provider timeout"
    assert data["priority"] == 3
    assert data["source_task_id"] == "task-123"
