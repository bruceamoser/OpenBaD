"""Web UI server for OpenBaD.

Serves the static dashboard assets and hosts the MQTT->WebSocket bridge.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import stat
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import yaml
from aiohttp import web

import openbad
from openbad.cognitive.config import (
    CognitiveConfig,
    CognitiveSystem,
    ProviderConfig,
    SystemAssignment,
    load_cognitive_config,
)
from openbad.cognitive.providers.anthropic import AnthropicProvider
from openbad.cognitive.providers.github_copilot import (
    _KNOWN_MODELS,
    _TOKEN_FILE,
    CopilotAuthError,
    GitHubCopilotProvider,
)
from openbad.cognitive.providers.ollama import OllamaProvider
from openbad.cognitive.providers.openai_compat import (
    custom_provider,
    groq_provider,
    mistral_provider,
    openai_provider,
    openrouter_provider,
    xai_provider,
)
from openbad.identity.persistence import IdentityPersistence
from openbad.identity.personality_modulator import PersonalityModulator
from openbad.memory.episodic import EpisodicMemory
from openbad.memory.sleep.schedule import SleepScheduleConfig
from openbad.sensory.config import load_sensory_config
from openbad.wui.bridge import MqttWebSocketBridge
from openbad.wui.chat_pipeline import get_conversation_history, stream_chat
from openbad.wui.usage_tracker import UsageTracker

# SvelteKit build output: wui-svelte/build/ is copied here by ``make wui``.
BUILD_DIR = Path(__file__).resolve().parent / "build"

log = logging.getLogger(__name__)

_RESTRICTED_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR
_SETUP_REDIRECT = "/providers?wizard=1"
_SUPPORTED_PROVIDER_TYPES: tuple[dict[str, object], ...] = (
    {
        "provider_type": "openai",
        "name": "openai",
        "label": "OpenAI",
        "auth": "api_key",
        "base_url": "https://api.openai.com",
        "api_key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
    },
    {
        "provider_type": "anthropic",
        "name": "anthropic",
        "label": "Anthropic",
        "auth": "api_key",
        "base_url": "https://api.anthropic.com",
        "api_key_env": "ANTHROPIC_API_KEY",
        "default_model": "claude-sonnet-4-20250514",
    },
    {
        "provider_type": "github-copilot",
        "name": "github-copilot",
        "label": "GitHub Copilot",
        "auth": "device_code",
        "base_url": "https://api.githubcopilot.com",
        "api_key_env": "GITHUB_COPILOT_TOKEN",
        "default_model": "gpt-4o",
    },
    {
        "provider_type": "ollama",
        "name": "ollama",
        "label": "Ollama",
        "auth": "local",
        "base_url": "http://localhost:11434",
        "api_key_env": "",
        "default_model": "llama3.2",
    },
    {
        "provider_type": "openrouter",
        "name": "openrouter",
        "label": "OpenRouter",
        "auth": "api_key",
        "base_url": "https://openrouter.ai/api",
        "api_key_env": "OPENROUTER_API_KEY",
        "default_model": "openai/gpt-4o-mini",
    },
    {
        "provider_type": "groq",
        "name": "groq",
        "label": "Groq",
        "auth": "api_key",
        "base_url": "https://api.groq.com/openai",
        "api_key_env": "GROQ_API_KEY",
        "default_model": "llama-3.1-8b-instant",
    },
    {
        "provider_type": "mistral",
        "name": "mistral",
        "label": "Mistral",
        "auth": "api_key",
        "base_url": "https://api.mistral.ai",
        "api_key_env": "MISTRAL_API_KEY",
        "default_model": "mistral-small-latest",
    },
    {
        "provider_type": "xai",
        "name": "xai",
        "label": "xAI",
        "auth": "api_key",
        "base_url": "https://api.x.ai",
        "api_key_env": "XAI_API_KEY",
        "default_model": "grok-3-mini",
    },
    {
        "provider_type": "local-openai",
        "name": "custom",
        "label": "Local / OpenAI-Compatible",
        "auth": "local",
        "base_url": "http://localhost:11434/v1",
        "api_key_env": "",
        "default_model": "",
    },
)
_SUPPORTED_PROVIDER_INDEX = {
    str(entry["provider_type"]): entry for entry in _SUPPORTED_PROVIDER_TYPES
}


def _resolve_usage_db_path() -> Path:
    configured = os.environ.get("OPENBAD_USAGE_DB", "").strip()
    if configured:
        return Path(configured)

    preferred_dir = Path("/var/lib/openbad/state")
    try:
        if preferred_dir.is_dir() and os.access(preferred_dir, os.W_OK):
            return preferred_dir / "usage.db"
    except PermissionError:
        pass

    preferred_parent = preferred_dir.parent
    try:
        if preferred_parent.is_dir() and os.access(preferred_parent, os.W_OK):
            return preferred_dir / "usage.db"
    except PermissionError:
        pass

    state_home = Path(
        os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
    )
    return state_home / "openbad" / "usage.db"


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


def _candidate_identity_config_paths() -> list[Path]:
    config_dir = os.environ.get("OPENBAD_CONFIG_DIR", "").strip()
    candidates: list[Path] = []
    if config_dir:
        candidates.append(Path(config_dir) / "identity.yaml")
    candidates.extend(
        [
            Path("/etc/openbad/identity.yaml"),
            Path.home() / ".config" / "openbad" / "identity.yaml",
            Path("config/identity.yaml"),
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


def _resolve_identity_config_path() -> Path:
    candidates = _candidate_identity_config_paths()
    config_dir = os.environ.get("OPENBAD_CONFIG_DIR", "").strip()
    if config_dir:
        return Path(config_dir) / "identity.yaml"

    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def _resolve_model_routing_path() -> Path:
    config_dir = os.environ.get("OPENBAD_CONFIG_DIR", "").strip()
    if config_dir:
        return Path(config_dir) / "model_routing.yaml"
    return Path("config/model_routing.yaml")


def _restrict_permissions(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.chmod(_RESTRICTED_FILE_MODE)


def _provider_uses_local_endpoint(provider: ProviderConfig) -> bool:
    return provider.name in {"ollama", "custom"}


def _provider_has_secret(provider: ProviderConfig) -> bool:
    if provider.api_key:
        return True
    if provider.name == "github-copilot":
        return bool(os.environ.get("GITHUB_COPILOT_TOKEN", "")) or _TOKEN_FILE.exists()
    return bool(provider.api_key_env and os.environ.get(provider.api_key_env, ""))


def _provider_is_valid(provider: ProviderConfig) -> bool:
    if not provider.enabled:
        return False
    if _provider_uses_local_endpoint(provider):
        return bool(provider.base_url)
    return _provider_has_secret(provider)


def _chat_assignment_is_ready(config: CognitiveConfig) -> bool:
    assignment = config.systems.get(CognitiveSystem.CHAT, SystemAssignment())
    if not assignment.provider or not assignment.model:
        return False
    for provider in config.providers:
        if provider.name == assignment.provider and _provider_is_valid(provider):
            return True
    return False


def _provider_setup_status(config: CognitiveConfig) -> dict[str, object]:
    valid_providers = [provider for provider in config.providers if _provider_is_valid(provider)]
    chat_ready = _chat_assignment_is_ready(config)
    missing: list[str] = []
    if not valid_providers:
        missing.append("provider")
    if not chat_ready:
        missing.append("chat_assignment")
    return {
        "first_run": bool(missing),
        "provider_ready": bool(valid_providers),
        "chat_assignment_ready": chat_ready,
        "configured_provider_count": len(valid_providers),
        "missing": missing,
        "redirect_to": _SETUP_REDIRECT if missing else "",
        "supported_providers": list(_SUPPORTED_PROVIDER_TYPES),
    }


def _rank_model(model_id: str) -> int:
    value = model_id.lower()
    if any(token in value for token in ("opus", "gpt-5", "sonnet-4", "claude-sonnet-4", "grok-3")):
        return 5
    if any(token in value for token in ("sonnet", "gpt-4", "mixtral", "mistral-large")):
        return 4
    if any(token in value for token in ("mini", "haiku", "small", "8b", "7b", "instant")):
        return 2
    if any(token in value for token in ("tiny", "3b", "1b")):
        return 1
    return 3


def _provider_model(provider: ProviderConfig) -> str:
    return provider.model.strip()


def _pick_primary_provider(providers: list[ProviderConfig]) -> ProviderConfig | None:
    for provider in providers:
        if provider.name not in {"ollama", "custom"}:
            return provider
    return providers[0] if providers else None


def _pick_fast_provider(providers: list[ProviderConfig]) -> ProviderConfig | None:
    ranked = [provider for provider in providers if _provider_model(provider)]
    if not ranked:
        return providers[0] if providers else None
    return min(ranked, key=lambda provider: _rank_model(_provider_model(provider)))


def _pick_capable_provider(providers: list[ProviderConfig]) -> ProviderConfig | None:
    ranked = [provider for provider in providers if _provider_model(provider)]
    if not ranked:
        return providers[0] if providers else None
    return max(ranked, key=lambda provider: _rank_model(_provider_model(provider)))


def _pick_local_provider(providers: list[ProviderConfig]) -> ProviderConfig | None:
    for provider in providers:
        if provider.name == "ollama":
            return provider
    for provider in providers:
        if provider.name == "custom" and provider.base_url.startswith("http://localhost"):
            return provider
    return None


def _dedupe_assignments(assignments: list[SystemAssignment]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for assignment in assignments:
        provider = assignment.provider.strip()
        model = assignment.model.strip()
        if not provider or not model:
            continue
        key = (provider, model)
        if key in seen:
            continue
        seen.add(key)
        result.append({"provider": provider, "model": model})
    return result


def _default_assignments(config: CognitiveConfig) -> dict[str, dict[str, str]]:
    providers = [provider for provider in config.providers if _provider_is_valid(provider)]
    primary = _pick_primary_provider(providers)
    capable = _pick_capable_provider(providers) or primary
    fast = _pick_fast_provider(providers) or primary
    local = _pick_local_provider(providers) or fast or primary

    def assignment(provider: ProviderConfig | None) -> dict[str, str]:
        if provider is None:
            return {"provider": "", "model": ""}
        return {"provider": provider.name, "model": _provider_model(provider)}

    return {
        CognitiveSystem.CHAT.value: assignment(primary),
        CognitiveSystem.REASONING.value: assignment(capable),
        CognitiveSystem.REACTIONS.value: assignment(fast),
        CognitiveSystem.SLEEP.value: assignment(local),
    }


def _write_model_routing_from_config(config: CognitiveConfig) -> None:
    path = _resolve_model_routing_path()
    existing = yaml.safe_load(path.read_text()) or {} if path.exists() else {}
    defaults = _default_assignments(config)

    def to_assignment(system: CognitiveSystem) -> SystemAssignment:
        configured = config.systems.get(system, SystemAssignment())
        if configured.provider and configured.model:
            return configured
        fallback = defaults.get(system.value, {"provider": "", "model": ""})
        return SystemAssignment(provider=fallback["provider"], model=fallback["model"])

    reasoning = to_assignment(CognitiveSystem.REASONING)
    chat = to_assignment(CognitiveSystem.CHAT)
    reactions = to_assignment(CognitiveSystem.REACTIONS)
    sleep = to_assignment(CognitiveSystem.SLEEP)

    document = {
        "cortisol_threshold": existing.get("cortisol_threshold", 0.8),
        "budget_limit": existing.get("budget_limit", 0),
        "health_ttl_s": existing.get("health_ttl_s", 60),
        "chains": {
            "critical": _dedupe_assignments([reasoning, chat, reactions]),
            "high": _dedupe_assignments([chat, reactions, reasoning]),
            "medium": _dedupe_assignments([reactions, chat]),
            "low": _dedupe_assignments([sleep, reactions]),
        },
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    _restrict_permissions(path)


def _initialize_identity_state(app: web.Application) -> None:
    config_path = _resolve_identity_config_path()
    if not config_path.exists():
        log.warning("identity config not found at %s; identity context disabled", config_path)
        return

    episodic_path = Path("/var/lib/openbad/memory/identity.json")
    persistence = IdentityPersistence(
        config_path,
        EpisodicMemory(storage_path=episodic_path),
    )
    app["identity_persistence"] = persistence
    app["personality_modulator"] = PersonalityModulator(persistence.assistant)


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
                "has_api_key": bool(provider.api_key),
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
        api_key=str(payload.get("api_key", "")).strip(),
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
                    "api_key": provider.api_key,
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
            for key in (
                "context_budget",
                "reasoning",
                "systems",
                "default_fallback_chain",
                "fallback_cortisol",
            ):
                if key in cognitive and key not in document["cognitive"]:
                    document["cognitive"][key] = cognitive[key]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    _restrict_permissions(path)


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
    _write_model_routing_from_config(config)

    bridge = request.app.get("bridge")
    if bridge is not None:
        bridge._configured_provider_count = sum(
            1 for provider in config.providers if provider.enabled
        )

    return web.json_response(_serialize_cognitive_config(config, path))


def _wizard_provider_payload(payload: dict[str, object]) -> ProviderConfig:
    provider_type = str(payload.get("provider_type", "")).strip()
    spec = _SUPPORTED_PROVIDER_INDEX.get(provider_type)
    if spec is None:
        raise web.HTTPBadRequest(text="unsupported provider_type")

    base_url = str(payload.get("base_url", spec["base_url"])).strip()
    api_key = str(payload.get("api_key", "")).strip()
    api_key_env = str(payload.get("api_key_env", spec["api_key_env"])).strip()
    model = str(payload.get("model", spec["default_model"])).strip()

    if spec["auth"] == "local" and not base_url:
        raise web.HTTPBadRequest(text="base_url is required for local providers")
    if spec["auth"] == "api_key" and not api_key and not api_key_env:
        raise web.HTTPBadRequest(text="api_key is required for this provider")

    return ProviderConfig(
        name=str(spec["name"]),
        base_url=base_url,
        model=model,
        api_key=api_key,
        api_key_env=api_key_env,
        timeout_ms=_coerce_timeout_ms(payload.get("timeout_ms", 30_000)),
        enabled=True,
    )


def _build_wizard_adapter(provider: ProviderConfig):
    timeout_s = max(1.0, provider.timeout_ms / 1000)
    if provider.name == "github-copilot":
        return GitHubCopilotProvider(
            default_model=provider.model or "gpt-4o",
            timeout_s=timeout_s,
        )

    if provider.name == "anthropic":
        return AnthropicProvider(
            base_url=provider.base_url or "https://api.anthropic.com",
            api_key=provider.api_key,
            api_key_env=provider.api_key_env or "ANTHROPIC_API_KEY",
            default_model=provider.model or "claude-sonnet-4-20250514",
            timeout_s=timeout_s,
        )

    if provider.name == "ollama":
        return OllamaProvider(
            base_url=provider.base_url or "http://localhost:11434",
            default_model=provider.model or "llama3.2",
            timeout_s=timeout_s,
        )

    if provider.name == "openai":
        return openai_provider(
            api_key=provider.api_key,
            api_key_env=provider.api_key_env or "OPENAI_API_KEY",
            default_model=provider.model or "gpt-4o-mini",
            timeout_s=timeout_s,
        )

    if provider.name == "openrouter":
        return openrouter_provider(
            api_key=provider.api_key,
            api_key_env=provider.api_key_env or "OPENROUTER_API_KEY",
            default_model=provider.model or "openai/gpt-4o-mini",
            timeout_s=timeout_s,
        )

    if provider.name == "groq":
        return groq_provider(
            api_key=provider.api_key,
            api_key_env=provider.api_key_env or "GROQ_API_KEY",
            default_model=provider.model or "llama-3.1-8b-instant",
            timeout_s=timeout_s,
        )

    if provider.name == "mistral":
        return mistral_provider(
            api_key=provider.api_key,
            api_key_env=provider.api_key_env or "MISTRAL_API_KEY",
            default_model=provider.model or "mistral-small-latest",
            timeout_s=timeout_s,
        )

    if provider.name == "xai":
        return xai_provider(
            api_key=provider.api_key,
            api_key_env=provider.api_key_env or "XAI_API_KEY",
            default_model=provider.model or "grok-3-mini",
            timeout_s=timeout_s,
        )

    return custom_provider(
        base_url=provider.base_url,
        api_key=provider.api_key,
        api_key_env=provider.api_key_env,
        default_model=provider.model,
        timeout_s=timeout_s,
    )


async def _verify_wizard_provider(provider: ProviderConfig) -> dict[str, object]:
    adapter = _build_wizard_adapter(provider)
    log.info("Verifying provider %s (%s)", provider.name, provider.base_url)
    try:
        status = await adapter.health_check()
    except Exception:
        log.exception("Health check failed for %s", provider.name)
        status = type(
            "HS",
            (),
            {"available": False, "latency_ms": 0, "models_available": 0},
        )()
    log.info(
        "Provider %s available=%s latency=%.0fms",
        provider.name,
        status.available,
        status.latency_ms,
    )
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
            "api_key": provider.api_key,
            "has_api_key": bool(provider.api_key),
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


async def _get_setup_status(_request: web.Request) -> web.Response:
    _path, config = _read_providers_config()
    return web.json_response(_provider_setup_status(config))


async def _post_setup(request: web.Request) -> web.Response:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="request body must be an object")

    persistence = request.app.get("identity_persistence")
    if persistence is not None:
        user_payload = payload.get("user")
        if isinstance(user_payload, dict):
            persistence.update_user(**user_payload)

        assistant_payload = payload.get("assistant")
        if isinstance(assistant_payload, dict):
            persistence.update_assistant(**assistant_payload)
            modulator = request.app.get("personality_modulator")
            if modulator is not None:
                modulator.update(persistence.assistant)

    senses_payload = payload.get("senses")
    if isinstance(senses_payload, dict):
        path = _resolve_senses_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(senses_payload, sort_keys=False), encoding="utf-8")
        _restrict_permissions(path)

    providers_payload = payload.get("providers")
    if isinstance(providers_payload, list):
        providers_path = _resolve_cognitive_config_path()
        _save_providers_config(
            providers_path,
            {
                "enabled": True,
                "default_provider": str(payload.get("default_provider", "")).strip(),
                "providers": providers_payload,
            },
        )

    config = load_cognitive_config(_resolve_cognitive_config_path())
    _write_model_routing_from_config(config)
    return web.json_response(_provider_setup_status(config))


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

    log.debug("Copilot complete poll for flow %s", flow_id)
    _cleanup_copilot_flows(request.app)
    flow = request.app["copilot_device_flows"].get(flow_id)
    if flow is None:
        raise web.HTTPBadRequest(text="Copilot authorization flow expired or was not found")

    # Guard against concurrent polls processing the same authorization
    if flow.get("completing"):
        return web.json_response(
            {"authorized": False, "pending": True, "message": "Processing authorization..."}
        )

    provider = GitHubCopilotProvider(
        default_model=str(flow["default_model"]),
        timeout_s=max(1.0, int(flow["timeout_ms"]) / 1000),
    )

    try:
        result = await provider.poll_for_token_once(str(flow["device_code"]))
    except Exception as exc:
        log.exception("poll_for_token_once failed for flow %s", flow_id)
        raise web.HTTPBadRequest(
            text="Failed to poll GitHub for authorization status"
        ) from exc
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

    flow["completing"] = True
    request.app["copilot_device_flows"].pop(flow_id, None)
    log.info("Copilot device-flow authorized for flow %s", flow_id)
    provider_dict = {
        "name": "github-copilot",
        "base_url": "https://api.githubcopilot.com",
        "model": str(flow["default_model"]),
        "api_key_env": "GITHUB_COPILOT_TOKEN",
        "timeout_ms": int(flow["timeout_ms"]),
        "enabled": True,
    }

    # Return immediately — health check is done separately via /api/providers/verify
    return web.json_response(
        {
            "authorized": True,
            "pending": False,
            "message": "Copilot authorization complete.",
            "provider": provider_dict,
            "models": [m["id"] for m in _KNOWN_MODELS],
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


async def _get_provider_models(request: web.Request) -> web.Response:
    """Return the list of models available from a registered provider.

    Uses the ProviderAdapter.list_models() interface method so each provider
    implementation controls its own discovery logic.
    """
    provider_name = request.match_info["name"]
    _path, config = _read_providers_config()

    provider_cfg = None
    for p in config.providers:
        if p.name == provider_name and p.enabled:
            provider_cfg = p
            break
    if provider_cfg is None:
        raise web.HTTPNotFound(text=f"provider not found: {provider_name}")

    adapter = _build_wizard_adapter(provider_cfg)
    try:
        model_infos = await adapter.list_models()
    except Exception:
        log.exception("list_models failed for %s", provider_name)
        model_infos = []

    return web.json_response({
        "provider": provider_name,
        "models": [
            {
                "model_id": m.model_id,
                "provider": m.provider,
                "context_window": m.context_window,
            }
            for m in model_infos
        ],
    })


# ── Chat streaming endpoint ──────────────────────────────────────── #


def _resolve_chat_adapter(config: CognitiveConfig, system_name: str):
    """Build a ProviderAdapter for the given cognitive system."""
    try:
        system = CognitiveSystem(system_name.lower())
    except ValueError:
        system = CognitiveSystem.CHAT

    assignment = config.systems.get(system)
    if not assignment or not assignment.provider:
        return None, None

    for p in config.providers:
        if p.name == assignment.provider and p.enabled:
            adapter = _build_wizard_adapter(p)
            model = assignment.model or p.model
            return adapter, model, p.name
    return None, None, None


def _serialize_chat_turn(turn) -> dict[str, object]:
    return {
        "role": turn.role,
        "content": turn.content,
        "timestamp": datetime.fromtimestamp(
            turn.timestamp,
            tz=UTC,
        ).isoformat(),
    }


async def _get_chat_history(request: web.Request) -> web.Response:
    session_id = str(request.query.get("session_id", "")).strip()
    if not session_id:
        raise web.HTTPBadRequest(text="session_id is required")

    limit_raw = request.query.get("limit")
    limit = 50
    if limit_raw not in (None, ""):
        try:
            limit = max(1, min(int(limit_raw), 200))
        except ValueError as exc:
            raise web.HTTPBadRequest(text="limit must be an integer") from exc

    history = get_conversation_history(session_id, limit=limit)
    return web.json_response(
        {
            "session_id": session_id,
            "messages": [_serialize_chat_turn(turn) for turn in history],
        }
    )


async def _post_chat_stream(request: web.Request) -> web.StreamResponse:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="request body must be an object")

    message = str(payload.get("message", "")).strip()
    if not message:
        raise web.HTTPBadRequest(text="message is required")

    system_name = str(payload.get("system", "chat")).strip()
    session_id = str(payload.get("session_id", "")).strip() or uuid4().hex
    _path, config = _read_providers_config()
    adapter, model, provider_name = _resolve_chat_adapter(config, system_name)

    if adapter is None:
        raise web.HTTPBadRequest(
            text=f"No provider configured for system '{system_name}'. "
            "Assign a provider on the Providers page."
        )

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    await resp.prepare(request)

    try:
        await resp.write(
            (
                "data: "
                + json.dumps({"session_id": session_id, "tokens_used": 0})
                + "\n\n"
            ).encode()
        )

        try:
            system = CognitiveSystem(system_name.lower())
        except ValueError:
            system = CognitiveSystem.CHAT

        persistence = request.app.get("identity_persistence")
        modulator = request.app.get("personality_modulator")

        async for chunk in stream_chat(
            adapter,
            model,
            message,
            session_id,
            system=system,
            provider_name=provider_name or "",
            user_profile=getattr(persistence, "user", None),
            assistant_profile=getattr(persistence, "assistant", None),
            modulation=getattr(modulator, "factors", None),
            usage_tracker=request.app.get("usage_tracker"),
        ):
            if chunk.error:
                data = json.dumps(
                    {
                        "session_id": session_id,
                        "token": f"\n\n[Error: {chunk.error}]",
                        "tokens_used": chunk.tokens_used,
                    }
                )
                await resp.write(f"data: {data}\n\n".encode())
                break
            if chunk.done:
                break
            data = json.dumps(
                {
                    "session_id": session_id,
                    "token": chunk.token,
                    "reasoning": chunk.reasoning,
                    "tokens_used": chunk.tokens_used,
                }
            )
            await resp.write(f"data: {data}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
    except Exception:
        log.exception(
            "Chat stream error for system=%s provider=%s model=%s",
            system_name,
            provider_name,
            model,
        )
        error_data = json.dumps(
            {
                "session_id": session_id,
                "token": "\n\n[Error: provider request failed]",
                "tokens_used": 0,
            }
        )
        await resp.write(f"data: {error_data}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")

    return resp


async def _get_usage(request: web.Request) -> web.Response:
    tracker = request.app.get("usage_tracker")
    if tracker is None:
        raise web.HTTPServiceUnavailable(text="UsageTracker not available")
    return web.json_response(
        {
            "generated_at": datetime.now(UTC).isoformat(),
            **tracker.snapshot(),
        }
    )


async def _get_version(_request: web.Request) -> web.Response:
    return web.json_response({"version": openbad.__version__})


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
    _restrict_permissions(path)

    config = load_cognitive_config(path)
    _write_model_routing_from_config(config)
    return web.json_response(_serialize_systems_config(config))


# ── Senses config endpoints ──────────────────────────────────────── #


def _resolve_senses_config_path() -> Path:
    config_dir = os.environ.get("OPENBAD_CONFIG_DIR", "").strip()
    if config_dir:
        return Path(config_dir) / "senses.yaml"
    return Path("config/senses.yaml")


def _resolve_memory_config_path() -> Path:
    config_dir = os.environ.get("OPENBAD_CONFIG_DIR", "").strip()
    if config_dir:
        return Path(config_dir) / "memory.yaml"
    return Path("config/memory.yaml")


def _read_memory_document(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _coerce_sleep_config(payload: object) -> SleepScheduleConfig:
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="sleep config must be an object")

    raw = payload.get("sleep", payload)
    if not isinstance(raw, dict):
        raise web.HTTPBadRequest(text="sleep must be an object")

    normalized: dict[str, object] = {}
    if "sleep_window_start" in raw:
        normalized["sleep_window_start"] = str(raw["sleep_window_start"])
    if "sleep_window_duration_hours" in raw:
        normalized["duration_hours"] = raw["sleep_window_duration_hours"]
    if "idle_timeout_minutes" in raw:
        normalized["idle_timeout_minutes"] = raw["idle_timeout_minutes"]
    if "allow_daytime_naps" in raw:
        normalized["allow_daytime_naps"] = raw["allow_daytime_naps"]
    if "enabled" in raw:
        normalized["enabled"] = raw["enabled"]

    # Backward-compatible aliases.
    if "start_hour" in raw and "sleep_window_start" not in raw:
        normalized["start_hour"] = raw["start_hour"]
    if "duration_hours" in raw and "sleep_window_duration_hours" not in raw:
        normalized["duration_hours"] = raw["duration_hours"]

    try:
        return SleepScheduleConfig.from_dict(normalized)
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc


def _sleep_config_to_dict(config: SleepScheduleConfig) -> dict[str, object]:
    return {
        "sleep_window_start": config.sleep_window_start,
        "sleep_window_duration_hours": config.sleep_window_duration_hours,
        "idle_timeout_minutes": config.idle_timeout_minutes,
        "allow_daytime_naps": config.allow_daytime_naps,
        "enabled": config.enabled,
    }


def _next_sleep_window_start(config: SleepScheduleConfig, now: datetime) -> datetime:
    base = now.replace(
        hour=config.start_hour,
        minute=config.start_minute,
        second=0,
        microsecond=0,
    )
    if base <= now:
        base += timedelta(days=1)
    return base


def _read_sleep_config(path: Path) -> tuple[dict[str, object], SleepScheduleConfig]:
    document = _read_memory_document(path)
    memory = document.get("memory")
    sleep_raw: object = {}
    if isinstance(memory, dict):
        sleep_raw = memory.get("sleep", {})
    return document, _coerce_sleep_config(sleep_raw)


def _sleep_payload(
    request: web.Request,
    config: SleepScheduleConfig,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    ts = now or datetime.now(timezone.utc)

    next_scheduled: str | None
    if not config.enabled:
        next_scheduled = None
    elif config.is_in_window(ts):
        next_scheduled = ts.isoformat()
    else:
        next_scheduled = _next_sleep_window_start(config, ts).isoformat()

    return {
        "sleep": _sleep_config_to_dict(config),
        "next_scheduled_consolidation": next_scheduled,
        "last_consolidation_summary": request.app.get("sleep_runtime", {}).get(
            "last_summary"
        ),
    }


async def _get_sleep_config(request: web.Request) -> web.Response:
    path = _resolve_memory_config_path()
    _, config = _read_sleep_config(path)
    return web.json_response(_sleep_payload(request, config))


async def _put_sleep_config(request: web.Request) -> web.Response:
    payload = await request.json()
    config = _coerce_sleep_config(payload)

    path = _resolve_memory_config_path()
    document = _read_memory_document(path)
    memory = document.get("memory")
    if not isinstance(memory, dict):
        memory = {}
    memory["sleep"] = _sleep_config_to_dict(config)
    document["memory"] = memory

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    return web.json_response(_sleep_payload(request, config))


def _publish_sleep_command(request: web.Request, command: str) -> None:
    bridge = request.app.get("bridge")
    mqtt_client = getattr(bridge, "_mqtt", None) if bridge is not None else None
    if mqtt_client is None:
        return
    try:
        mqtt_client.publish("openbad/sleep/command", command.encode("utf-8"))
    except Exception:
        log.exception("Failed to publish sleep command: %s", command)


async def _post_sleep_trigger(request: web.Request) -> web.Response:
    runtime = request.app.get("sleep_runtime")
    if isinstance(runtime, dict):
        runtime["last_summary"] = {
            "state": "manual_sleep_requested",
            "at": datetime.now(timezone.utc).isoformat(),
        }
    _publish_sleep_command(request, "sleep")
    return web.json_response({"ok": True, "state": "sleep_requested"})


async def _post_sleep_wake(request: web.Request) -> web.Response:
    runtime = request.app.get("sleep_runtime")
    if isinstance(runtime, dict):
        runtime["last_summary"] = {
            "state": "manual_wake_requested",
            "at": datetime.now(timezone.utc).isoformat(),
        }
    _publish_sleep_command(request, "wake")
    return web.json_response({"ok": True, "state": "wake_requested"})


async def _post_sleep_trigger_legacy(request: web.Request) -> web.Response:
    """Backwards-compatible alias for sleep trigger endpoint."""
    return await _post_sleep_trigger(request)


async def _post_sleep_wake_legacy(request: web.Request) -> web.Response:
    """Backwards-compatible alias for sleep wake endpoint."""
    return await _post_sleep_wake(request)


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
        _restrict_permissions(path)
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
    app["sleep_runtime"] = {"last_summary": None}
    app["usage_tracker"] = UsageTracker(db_path=_resolve_usage_db_path())

    async def _cleanup_usage_tracker(app: web.Application) -> None:
        tracker = app.get("usage_tracker")
        if tracker is not None:
            tracker.close()

    app.on_cleanup.append(_cleanup_usage_tracker)

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
    app.router.add_get("/api/setup-status", _get_setup_status)
    app.router.add_post("/api/setup", _post_setup)
    app.router.add_get("/api/systems", _get_systems)
    app.router.add_put("/api/systems", _put_systems)
    app.router.add_get("/api/providers/{name}/models", _get_provider_models)
    app.router.add_get("/api/chat/history", _get_chat_history)
    app.router.add_post("/api/chat/stream", _post_chat_stream)
    app.router.add_get("/api/usage", _get_usage)
    app.router.add_get("/api/version", _get_version)
    app.router.add_get("/api/senses", _get_senses)
    app.router.add_put("/api/senses", _put_senses)
    app.router.add_get("/api/sleep/config", _get_sleep_config)
    app.router.add_put("/api/sleep/config", _put_sleep_config)
    app.router.add_post("/api/sleep/trigger", _post_sleep_trigger)
    app.router.add_post("/api/sleep/wake", _post_sleep_wake)
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
    _initialize_identity_state(app)
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
