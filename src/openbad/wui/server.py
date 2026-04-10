"""Web UI server for OpenBaD.

Serves the static dashboard assets and hosts the MQTT->WebSocket bridge.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from uuid import uuid4

import yaml
from aiohttp import web

from openbad.cognitive.config import CognitiveConfig, ProviderConfig, load_cognitive_config
from openbad.cognitive.providers.github_copilot import CopilotAuthError, GitHubCopilotProvider
from openbad.cognitive.providers.openai_compat import custom_provider
from openbad.wui.bridge import MqttWebSocketBridge

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _candidate_cognitive_config_paths() -> list[Path]:
    config_dir = os.environ.get("OPENBAD_CONFIG_DIR", "").strip()
    candidates: list[Path] = []
    if config_dir:
        candidates.append(Path(config_dir) / "cognitive.yaml")
    candidates.extend(
        [
            Path("/etc/openbad/cognitive.yaml"),
            Path.home() / ".config" / "openbad" / "cognitive.yaml",
            Path("config/cognitive.yaml"),
        ]
    )

    unique_candidates: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        unique_candidates.append(path)
        seen.add(path)
    return unique_candidates


def _resolve_cognitive_config_path() -> Path:
    candidates = _candidate_cognitive_config_paths()
    config_dir = os.environ.get("OPENBAD_CONFIG_DIR", "").strip()
    if config_dir:
        return Path(config_dir) / "cognitive.yaml"

    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def _serialize_cognitive_config(config: CognitiveConfig, path: Path) -> dict[str, object]:
    return {
        "config_path": str(path),
        "enabled": config.enabled,
        "default_provider": config.default_provider,
        "providers": [
            {
                "name": provider.name,
                "base_url": provider.base_url,
                "model": provider.model,
                "api_key_env": provider.api_key_env,
                "timeout_ms": provider.timeout_ms,
                "enabled": provider.enabled,
            }
            for provider in config.providers
        ],
    }


def _read_providers_config() -> tuple[Path, CognitiveConfig]:
    path = _resolve_cognitive_config_path()
    return path, load_cognitive_config(path)


def _coerce_provider(payload: object) -> ProviderConfig:
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="provider entries must be objects")

    try:
        timeout_ms = int(payload.get("timeout_ms", 30_000))
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(text="timeout_ms must be an integer") from exc

    name = str(payload.get("name", "")).strip()
    if not name:
        raise web.HTTPBadRequest(text="provider name is required")

    return ProviderConfig(
        name=name,
        base_url=str(payload.get("base_url", "")).strip(),
        model=str(payload.get("model", "")).strip(),
        api_key_env=str(payload.get("api_key_env", "")).strip(),
        timeout_ms=max(timeout_ms, 1000),
        enabled=bool(payload.get("enabled", True)),
    )


def _coerce_timeout_ms(value: object, default: int = 30_000) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError()
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, float):
            parsed = int(value)
        elif isinstance(value, str):
            parsed = int(value.strip())
        elif value is None:
            parsed = default
        else:
            raise ValueError()
        return max(parsed, 1000)
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(text="timeout_ms must be an integer") from exc


def _save_providers_config(path: Path, payload: dict[str, object]) -> None:
    providers_payload = payload.get("providers", [])
    if not isinstance(providers_payload, list):
        raise web.HTTPBadRequest(text="providers must be a list")

    providers = [_coerce_provider(provider) for provider in providers_payload]
    default_provider = str(payload.get("default_provider", "")).strip()
    enabled = bool(payload.get("enabled", True))

    document = {
        "cognitive": {
            "default_provider": default_provider,
            "enabled": enabled,
            "providers": [
                {
                    "name": provider.name,
                    "base_url": provider.base_url,
                    "model": provider.model,
                    "api_key_env": provider.api_key_env,
                    "timeout_ms": provider.timeout_ms,
                    "enabled": provider.enabled,
                }
                for provider in providers
            ],
        }
    }

    if path.exists():
        existing = yaml.safe_load(path.read_text()) or {}
        cognitive = existing.get("cognitive", {})
        if isinstance(cognitive, dict):
            for key in ("context_budget", "reasoning"):
                if key in cognitive and key not in document["cognitive"]:
                    document["cognitive"][key] = cognitive[key]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


async def _get_providers(_request: web.Request) -> web.Response:
    path, config = _read_providers_config()
    return web.json_response(_serialize_cognitive_config(config, path))


async def _put_providers(request: web.Request) -> web.Response:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="request body must be an object")

    path = _resolve_cognitive_config_path()
    _save_providers_config(path, payload)
    config = load_cognitive_config(path)

    bridge = request.app.get("bridge")
    if bridge is not None:
        bridge._configured_provider_count = sum(
            1 for provider in config.providers if provider.enabled
        )

    return web.json_response(_serialize_cognitive_config(config, path))


def _wizard_provider_payload(payload: dict[str, object]) -> ProviderConfig:
    provider_type = str(payload.get("provider_type", "")).strip()
    if provider_type == "github-copilot":
        return ProviderConfig(
            name="github-copilot",
            base_url="https://api.githubcopilot.com",
            model=str(payload.get("model", "")).strip(),
            api_key_env="GITHUB_COPILOT_TOKEN",
            timeout_ms=_coerce_timeout_ms(payload.get("timeout_ms", 30_000)),
            enabled=True,
        )

    if provider_type == "local-openai":
        base_url = str(payload.get("base_url", "")).strip()
        if not base_url:
            raise web.HTTPBadRequest(text="base_url is required for local llama providers")
        return ProviderConfig(
            name="custom",
            base_url=base_url,
            model=str(payload.get("model", "")).strip(),
            api_key_env=str(payload.get("api_key_env", "")).strip(),
            timeout_ms=_coerce_timeout_ms(payload.get("timeout_ms", 30_000)),
            enabled=True,
        )

    raise web.HTTPBadRequest(text="unsupported provider_type")


def _build_wizard_adapter(provider: ProviderConfig):
    timeout_s = max(1.0, provider.timeout_ms / 1000)
    if provider.name == "github-copilot":
        return GitHubCopilotProvider(
            default_model=provider.model or "gpt-4o",
            timeout_s=timeout_s,
        )

    return custom_provider(
        base_url=provider.base_url,
        api_key_env=provider.api_key_env,
        default_model=provider.model,
        timeout_s=timeout_s,
    )


async def _verify_wizard_provider(provider: ProviderConfig) -> dict[str, object]:
    adapter = _build_wizard_adapter(provider)
    status = await adapter.health_check()
    models: list[str] = []
    if status.available:
        try:
            models = [model.model_id for model in await adapter.list_models()]
        except Exception:
            models = []

    return {
        "available": status.available,
        "latency_ms": status.latency_ms,
        "models_available": status.models_available,
        "models": models,
        "provider": {
            "name": provider.name,
            "base_url": provider.base_url,
            "model": provider.model,
            "api_key_env": provider.api_key_env,
            "timeout_ms": provider.timeout_ms,
            "enabled": provider.enabled,
        },
    }


async def _post_providers_verify(request: web.Request) -> web.Response:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="request body must be an object")

    provider = _wizard_provider_payload(payload)
    result = await _verify_wizard_provider(provider)

    message = (
        "Provider verified successfully."
        if bool(result["available"])
        else "Provider verification failed. Confirm credentials, endpoint, and model access."
    )
    provider_type = str(payload.get("provider_type", "")).strip()

    return web.json_response(
        {
            "provider_type": provider_type,
            "available": result["available"],
            "latency_ms": result["latency_ms"],
            "models_available": result["models_available"],
            "models": result["models"],
            "message": message,
            "provider": result["provider"],
        }
    )


def _legacy_providers_redirect_location(request: web.Request) -> str:
    location = request.path.replace("/api/wiring/providers", "/api/providers", 1)
    if request.query_string:
        return f"{location}?{request.query_string}"
    return location


async def _redirect_legacy_wiring_providers(request: web.Request) -> web.StreamResponse:
    raise web.HTTPMovedPermanently(location=_legacy_providers_redirect_location(request))


def _cleanup_copilot_flows(app: web.Application) -> None:
    now = time.time()
    flows = app["copilot_device_flows"]
    expired = [flow_id for flow_id, flow in flows.items() if now >= flow["expires_at"]]
    for flow_id in expired:
        flows.pop(flow_id, None)


async def _post_copilot_device_code(request: web.Request) -> web.Response:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="request body must be an object")

    timeout_ms = _coerce_timeout_ms(payload.get("timeout_ms", 30_000))
    provider = GitHubCopilotProvider(
        default_model=str(payload.get("model", "gpt-4o")).strip() or "gpt-4o",
        timeout_s=max(1.0, timeout_ms / 1000),
    )

    try:
        device = await provider.request_device_code()
    except CopilotAuthError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc

    flow_id = uuid4().hex
    request.app["copilot_device_flows"][flow_id] = {
        "device_code": device.device_code,
        "default_model": provider._default_model,
        "timeout_ms": timeout_ms,
        "interval": device.interval,
        "expires_at": time.time() + device.expires_in,
    }
    _cleanup_copilot_flows(request.app)

    return web.json_response(
        {
            "flow_id": flow_id,
            "user_code": device.user_code,
            "verification_uri": device.verification_uri,
            "interval": device.interval,
            "expires_in": device.expires_in,
            "message": "Enter this code on GitHub to authorize Copilot for OpenBaD.",
        }
    )


async def _post_copilot_complete(request: web.Request) -> web.Response:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="request body must be an object")

    flow_id = str(payload.get("flow_id", "")).strip()
    if not flow_id:
        raise web.HTTPBadRequest(text="flow_id is required")

    _cleanup_copilot_flows(request.app)
    flow = request.app["copilot_device_flows"].get(flow_id)
    if flow is None:
        raise web.HTTPBadRequest(text="Copilot authorization flow expired or was not found")

    provider = GitHubCopilotProvider(
        default_model=str(flow["default_model"]),
        timeout_s=max(1.0, int(flow["timeout_ms"]) / 1000),
    )

    result = await provider.poll_for_token_once(str(flow["device_code"]))
    state = str(result.get("state", "error"))
    if state == "authorization_pending":
        return web.json_response(
            {
                "authorized": False,
                "pending": True,
                "message": (
                    "Authorization is still pending. Finish the GitHub step, "
                    "then check again."
                ),
            }
        )
    if state == "slow_down":
        return web.json_response(
            {
                "authorized": False,
                "pending": True,
                "message": "GitHub asked to slow down. Wait a moment, then check again.",
                "interval": result.get("interval", flow["interval"]),
            }
        )
    if state != "authorized":
        error_message = result.get("error_description", result.get("error", "unknown error"))
        return web.json_response(
            {
                "authorized": False,
                "pending": False,
                "message": f"GitHub authorization failed: {error_message}",
            },
            status=400,
        )

    request.app["copilot_device_flows"].pop(flow_id, None)
    provider_config = ProviderConfig(
        name="github-copilot",
        base_url="https://api.githubcopilot.com",
        model=str(flow["default_model"]),
        api_key_env="GITHUB_COPILOT_TOKEN",
        timeout_ms=int(flow["timeout_ms"]),
        enabled=True,
    )
    verified = await _verify_wizard_provider(provider_config)

    return web.json_response(
        {
            "authorized": bool(verified["available"]),
            "pending": False,
            "message": (
                "Copilot authorization complete."
                if bool(verified["available"])
                else "Copilot token stored, but provider verification failed."
            ),
            "provider": verified["provider"],
            "models": verified["models"],
            "latency_ms": verified["latency_ms"],
            "models_available": verified["models_available"],
        }
    )


def create_app(
    mqtt_host: str = "localhost",
    mqtt_port: int = 1883,
    *,
    enable_mqtt: bool = True,
) -> web.Application:
    bridge = MqttWebSocketBridge(mqtt_host=mqtt_host, mqtt_port=mqtt_port)
    app = bridge.create_app()
    app["bridge"] = bridge
    app["copilot_device_flows"] = {}

    if not enable_mqtt:
        # Tests can disable external broker dependency by skipping startup/shutdown hooks.
        app.on_startup.clear()
        app.on_shutdown.clear()

    async def index(_request: web.Request) -> web.FileResponse:
        return web.FileResponse(STATIC_DIR / "index.html")

    app.router.add_get("/", index)
    app.router.add_get("/api/providers", _get_providers)
    app.router.add_post("/api/providers/copilot/device-code", _post_copilot_device_code)
    app.router.add_post("/api/providers/copilot/complete", _post_copilot_complete)
    app.router.add_post("/api/providers/verify", _post_providers_verify)
    app.router.add_put("/api/providers", _put_providers)
    # TODO: Remove after v1.0 once external consumers have migrated to /api/providers/*.
    app.router.add_get("/api/wiring/providers", _redirect_legacy_wiring_providers)
    app.router.add_post(
        "/api/wiring/providers/copilot/device-code", _redirect_legacy_wiring_providers
    )
    app.router.add_post(
        "/api/wiring/providers/copilot/complete", _redirect_legacy_wiring_providers
    )
    app.router.add_post("/api/wiring/providers/verify", _redirect_legacy_wiring_providers)
    app.router.add_put("/api/wiring/providers", _redirect_legacy_wiring_providers)
    app.router.add_static("/static", STATIC_DIR)
    return app


async def run_server(
    host: str = "127.0.0.1",
    port: int = 9200,
    mqtt_host: str = "localhost",
    mqtt_port: int = 1883,
) -> None:
    app = create_app(mqtt_host=mqtt_host, mqtt_port=mqtt_port, enable_mqtt=True)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()

    try:
        # Keep running until cancelled.
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
