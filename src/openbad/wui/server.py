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

from openbad.cognitive.config import (
    CognitiveConfig,
    CognitiveSystem,
    ProviderConfig,
    load_cognitive_config,
)
from openbad.cognitive.providers.github_copilot import CopilotAuthError, GitHubCopilotProvider
from openbad.cognitive.providers.openai_compat import custom_provider
from openbad.sensory.config import load_sensory_config
from openbad.wui.bridge import MqttWebSocketBridge

# SvelteKit build output: wui-svelte/build/ is copied here by ``make wui``.
BUILD_DIR = Path(__file__).resolve().parent / "build"


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


def _serialize_systems_config(config: CognitiveConfig) -> dict[str, object]:
    systems = {}
    for system, assignment in config.systems.items():
        systems[system.value] = {
            "provider": assignment.provider,
            "model": assignment.model,
        }
    fallback_chain = [
        {"provider": step.provider, "model": step.model}
        for step in config.default_fallback_chain
    ]
    return {
        "systems": systems,
        "fallback_chain": fallback_chain,
        "providers": [
            {"name": p.name, "model": p.model}
            for p in config.providers
            if p.enabled
        ],
    }


async def _get_systems(_request: web.Request) -> web.Response:
    _path, config = _read_providers_config()
    return web.json_response(_serialize_systems_config(config))


async def _put_systems(request: web.Request) -> web.Response:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="request body must be an object")

    systems_raw = payload.get("systems")
    if not isinstance(systems_raw, dict):
        raise web.HTTPBadRequest(text="systems must be an object")

    chain_raw = payload.get("fallback_chain")
    if not isinstance(chain_raw, list):
        raise web.HTTPBadRequest(text="fallback_chain must be a list")

    # Validate system names
    systems: dict[str, dict[str, str]] = {}
    for name, assignment in systems_raw.items():
        try:
            CognitiveSystem(name)
        except ValueError as exc:
            raise web.HTTPBadRequest(text=f"unknown system: {name}") from exc
        if not isinstance(assignment, dict):
            raise web.HTTPBadRequest(text=f"assignment for {name} must be an object")
        systems[name] = {
            "provider": str(assignment.get("provider", "")).strip(),
            "model": str(assignment.get("model", "")).strip(),
        }

    chain = []
    for step in chain_raw:
        if not isinstance(step, dict):
            raise web.HTTPBadRequest(text="fallback chain entries must be objects")
        chain.append({
            "provider": str(step.get("provider", "")).strip(),
            "model": str(step.get("model", "")).strip(),
        })

    path = _resolve_cognitive_config_path()
    existing = yaml.safe_load(path.read_text()) or {} if path.exists() else {}

    cognitive = existing.get("cognitive", {})
    if not isinstance(cognitive, dict):
        cognitive = {}

    cognitive["systems"] = systems
    cognitive["default_fallback_chain"] = chain
    existing["cognitive"] = cognitive

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(existing, sort_keys=False), encoding="utf-8")

    config = load_cognitive_config(path)
    return web.json_response(_serialize_systems_config(config))


# ── Senses config endpoints ──────────────────────────────────────── #


def _resolve_senses_config_path() -> Path:
    config_dir = os.environ.get("OPENBAD_CONFIG_DIR", "").strip()
    if config_dir:
        return Path(config_dir) / "senses.yaml"
    return Path("config/senses.yaml")


def _serialize_senses(cfg: object) -> dict:
    """Convert a SensoryConfig to a JSON-safe dict."""
    h = cfg.hearing
    v = cfg.vision
    s = cfg.speech
    return {
        "hearing": {
            "capture": {
                "sample_rate": h.capture.sample_rate,
                "channels": h.capture.channels,
                "sample_format": h.capture.sample_format,
                "chunk_duration_ms": h.capture.chunk_duration_ms,
                "device": h.capture.device,
                "passive": h.capture.passive,
            },
            "asr": {
                "default_engine": h.asr.default_engine,
                "vosk_model_path": h.asr.vosk_model_path,
                "whisper_model": h.asr.whisper_model,
                "vad_sensitivity": h.asr.vad_sensitivity,
            },
            "wake_word": {
                "phrases": list(h.wake_word.phrases),
                "threshold": h.wake_word.threshold,
            },
        },
        "vision": {
            "fps_idle": v.fps_idle,
            "fps_active": v.fps_active,
            "capture_region": str(v.capture_region),
            "capture_interval_s": v.capture_interval_s,
            "max_resolution": list(v.max_resolution) if v.max_resolution else None,
            "compression": {
                "format": v.compression.format,
                "quality": v.compression.quality,
            },
            "attention": {
                "ssim_threshold": v.attention.ssim_threshold,
                "cooldown_ms": v.attention.cooldown_ms,
                "roi_enabled": v.attention.roi_enabled,
            },
        },
        "speech": {
            "tts": {
                "engine": s.tts.engine,
                "voice_model": s.tts.voice_model,
                "model_path": s.tts.model_path,
                "speaking_rate": s.tts.speaking_rate,
                "volume": s.tts.volume,
                "output_device": s.tts.output_device,
            },
        },
    }


async def _get_senses(_request: web.Request) -> web.Response:
    path = _resolve_senses_config_path()
    cfg = load_sensory_config(path)
    return web.json_response(_serialize_senses(cfg))


async def _put_senses(request: web.Request) -> web.Response:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="request body must be an object")

    # Write the raw payload as YAML — validation happens via load_sensory_config
    path = _resolve_senses_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        cfg = load_sensory_config(path)
    except (ValueError, TypeError) as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc

    return web.json_response(_serialize_senses(cfg))


# ---------------------------------------------------------------------------
# Toolbelt API — GET / PUT / DELETE on in-memory ToolRegistry
# ---------------------------------------------------------------------------


def _serialize_toolbelt(registry) -> dict:
    """Serialize ToolRegistry state for JSON response."""
    cabinet: dict[str, list[dict]] = {}
    for role, tools in registry.cabinet.items():
        role_name = role.value.lower() if hasattr(role, "value") else str(role).lower()
        cabinet[role_name] = [
            {
                "name": t.name,
                "status": (
                    t.status.value.lower()
                    if hasattr(t.status, "value")
                    else str(t.status).lower()
                ),
                "role": role_name,
            }
            for t in tools
        ]

    belt: dict[str, str | None] = {}
    for role, tool in registry.get_belt().items():
        role_name = role.value.lower() if hasattr(role, "value") else str(role).lower()
        belt[role_name] = tool.name if tool else None

    return {"cabinet": cabinet, "belt": belt}


async def _get_toolbelt(request: web.Request) -> web.Response:
    registry = request.app.get("registry")
    if registry is None:
        return web.json_response({"cabinet": {}, "belt": {}})
    return web.json_response(_serialize_toolbelt(registry))


async def _put_toolbelt_role(request: web.Request) -> web.Response:
    registry = request.app.get("registry")
    if registry is None:
        raise web.HTTPServiceUnavailable(text="ToolRegistry not available")

    role_str = request.match_info["role"].upper()

    # Resolve ToolRole enum from string
    from openbad.proprioception.registry import ToolRole
    try:
        role = ToolRole(role_str)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=f"Unknown role: {role_str}") from exc

    payload = await request.json()
    tool_name = payload.get("tool") if isinstance(payload, dict) else None
    if not tool_name:
        raise web.HTTPBadRequest(text="'tool' field required")

    try:
        registry.equip(role, tool_name)
    except (KeyError, ValueError) as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc

    return web.json_response(_serialize_toolbelt(registry))


async def _delete_toolbelt_role(request: web.Request) -> web.Response:
    registry = request.app.get("registry")
    if registry is None:
        raise web.HTTPServiceUnavailable(text="ToolRegistry not available")

    role_str = request.match_info["role"].upper()

    from openbad.proprioception.registry import ToolRole
    try:
        role = ToolRole(role_str)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=f"Unknown role: {role_str}") from exc

    try:
        registry.unequip(role)
    except (KeyError, ValueError) as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc

    return web.json_response(_serialize_toolbelt(registry))


# ---------------------------------------------------------------------- #
# Entity endpoints — user / assistant profile management
# ---------------------------------------------------------------------- #


def _serialize_user(profile) -> dict:
    from dataclasses import asdict
    d = asdict(profile)
    d["communication_style"] = profile.communication_style.value
    return d


def _serialize_assistant(profile) -> dict:
    from dataclasses import asdict
    return asdict(profile)


async def _get_entity_user(request: web.Request) -> web.Response:
    persistence = request.app.get("identity_persistence")
    if persistence is None:
        raise web.HTTPServiceUnavailable(text="IdentityPersistence not available")
    return web.json_response(_serialize_user(persistence.user))


async def _get_entity_assistant(request: web.Request) -> web.Response:
    persistence = request.app.get("identity_persistence")
    if persistence is None:
        raise web.HTTPServiceUnavailable(text="IdentityPersistence not available")
    data = _serialize_assistant(persistence.assistant)
    # Include computed modulation factors if PersonalityModulator is available.
    modulator = request.app.get("personality_modulator")
    if modulator is not None:
        f = modulator.factors
        data["modulation"] = {
            "exploration_budget_multiplier": f.exploration_budget_multiplier,
            "max_reasoning_depth_multiplier": f.max_reasoning_depth_multiplier,
            "proactive_suggestion_threshold": f.proactive_suggestion_threshold,
            "challenge_probability": f.challenge_probability,
            "cortisol_decay_multiplier": f.cortisol_decay_multiplier,
        }
    return web.json_response(data)


async def _put_entity_user(request: web.Request) -> web.Response:
    persistence = request.app.get("identity_persistence")
    if persistence is None:
        raise web.HTTPServiceUnavailable(text="IdentityPersistence not available")
    payload = await request.json()
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="Request body must be a JSON object")
    try:
        persistence.update_user(**payload)
    except (AttributeError, ValueError, TypeError) as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    return web.json_response(_serialize_user(persistence.user))


async def _put_entity_assistant(request: web.Request) -> web.Response:
    persistence = request.app.get("identity_persistence")
    if persistence is None:
        raise web.HTTPServiceUnavailable(text="IdentityPersistence not available")
    payload = await request.json()
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="Request body must be a JSON object")
    try:
        persistence.update_assistant(**payload)
    except (AttributeError, ValueError, TypeError) as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    # Recalculate modulation factors if modulator is available.
    modulator = request.app.get("personality_modulator")
    if modulator is not None:
        modulator.update(persistence.assistant)
    return web.json_response(_serialize_assistant(persistence.assistant))


async def _post_entity_user_reset(request: web.Request) -> web.Response:
    persistence = request.app.get("identity_persistence")
    if persistence is None:
        raise web.HTTPServiceUnavailable(text="IdentityPersistence not available")
    persistence.reset_to_seed()
    return web.json_response(_serialize_user(persistence.user))


async def _post_entity_assistant_reset(request: web.Request) -> web.Response:
    persistence = request.app.get("identity_persistence")
    if persistence is None:
        raise web.HTTPServiceUnavailable(text="IdentityPersistence not available")
    persistence.reset_to_seed()
    modulator = request.app.get("personality_modulator")
    if modulator is not None:
        modulator.update(persistence.assistant)
    return web.json_response(_serialize_assistant(persistence.assistant))


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

    # --- SvelteKit SPA serving ---------------------------------------------------

    async def _spa_index(_request: web.Request) -> web.FileResponse:
        return web.FileResponse(BUILD_DIR / "index.html")

    async def _spa_fallback(request: web.Request) -> web.StreamResponse:
        """Serve static file if it exists, otherwise return index.html for client-side routing."""
        rel = request.match_info.get("path", "")
        candidate = BUILD_DIR / rel
        if candidate.is_file():
            return web.FileResponse(candidate)
        return web.FileResponse(BUILD_DIR / "index.html")

    app.router.add_get("/", _spa_index)

    # All API routes
    app.router.add_get("/api/providers", _get_providers)
    app.router.add_post("/api/providers/copilot/device-code", _post_copilot_device_code)
    app.router.add_post("/api/providers/copilot/complete", _post_copilot_complete)
    app.router.add_post("/api/providers/verify", _post_providers_verify)
    app.router.add_put("/api/providers", _put_providers)
    app.router.add_get("/api/systems", _get_systems)
    app.router.add_put("/api/systems", _put_systems)
    app.router.add_get("/api/senses", _get_senses)
    app.router.add_put("/api/senses", _put_senses)
    app.router.add_get("/api/toolbelt", _get_toolbelt)
    app.router.add_put("/api/toolbelt/{role}", _put_toolbelt_role)
    app.router.add_delete("/api/toolbelt/{role}", _delete_toolbelt_role)
    app.router.add_get("/api/entity/user", _get_entity_user)
    app.router.add_put("/api/entity/user", _put_entity_user)
    app.router.add_post("/api/entity/user/reset", _post_entity_user_reset)
    app.router.add_get("/api/entity/assistant", _get_entity_assistant)
    app.router.add_put("/api/entity/assistant", _put_entity_assistant)
    app.router.add_post("/api/entity/assistant/reset", _post_entity_assistant_reset)
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

    # SvelteKit static assets + SPA fallback for client-side routing
    _app_dir = BUILD_DIR / "_app"
    if _app_dir.is_dir():
        app.router.add_static("/_app", _app_dir)
    app.router.add_get("/{path:.*}", _spa_fallback)
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
