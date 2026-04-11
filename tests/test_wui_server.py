"""Tests for WUI server scaffold (#185)."""

from __future__ import annotations

import os
import stat
from unittest.mock import AsyncMock, patch

import pytest
import yaml

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
        "default_provider": "anthropic",
        "providers": [
            {
                "name": "anthropic",
                "base_url": "https://api.anthropic.com",
                "model": "claude-sonnet-4-20250514",
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
                    "default_provider": "openai",
                    "providers": [
                        {
                            "name": "openai",
                            "base_url": "https://api.openai.com",
                            "model": "gpt-4o-mini",
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


# ── System assignment endpoints ──────────────────────────────────── #


def _cognitive_yaml_with_systems(tmp_path, monkeypatch, *, extra_sections=None):
    """Create a temporary cognitive.yaml with systems + fallback chain."""
    config_dir = tmp_path / "openbad"
    config_dir.mkdir(exist_ok=True)
    base = {
        "cognitive": {
            "enabled": True,
            "default_provider": "ollama",
            "providers": [
                {
                    "name": "ollama",
                    "base_url": "http://localhost:11434",
                    "model": "llama3.2",
                    "timeout_ms": 30000,
                    "enabled": True,
                },
                {
                    "name": "ollama",
                    "base_url": "http://localhost:11434",
                    "model": "bonsai-8b",
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

    provider_models = [(p["name"], p["model"]) for p in data["providers"]]
    assert ("ollama", "llama3.2") in provider_models
    assert ("ollama", "bonsai-8b") in provider_models


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


@pytest.mark.asyncio
async def test_get_toolbelt_no_registry(aiohttp_client):
    app = create_app(enable_mqtt=False)
    client = await aiohttp_client(app)
    resp = await client.get("/api/toolbelt")
    assert resp.status == 200
    data = await resp.json()
    assert data == {"cabinet": {}, "belt": {}}


@pytest.mark.asyncio
async def test_get_toolbelt_with_registry(aiohttp_client):
    app = _app_with_registry()
    client = await aiohttp_client(app)
    resp = await client.get("/api/toolbelt")
    assert resp.status == 200
    data = await resp.json()
    assert "cabinet" in data
    assert "belt" in data
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

    app = create_app(enable_mqtt=False)
    tracker = app["usage_tracker"]
    tracker.record(
        provider="openai",
        model="gpt-4o",
        system="chat",
        tokens=320,
        request_id="req-1",
        session_id="sess-1",
    )
    tracker.record(
        provider="anthropic",
        model="claude-sonnet",
        system="reasoning",
        tokens=180,
        request_id="req-2",
        session_id="sess-2",
    )

    client = await aiohttp_client(app)
    resp = await client.get("/api/usage")

    assert resp.status == 200
    data = await resp.json()
    assert data["summary"]["total_used"] == 500
    assert data["summary"]["request_count"] == 2
    assert data["by_provider_model"][0]["tokens"] == 320
    assert {item["system"] for item in data["by_system"]} == {"chat", "reasoning"}
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
