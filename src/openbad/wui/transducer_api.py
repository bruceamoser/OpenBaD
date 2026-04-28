"""WUI API routes for the Transducers panel (Corsair peripheral plugins)."""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

from aiohttp import web

from openbad.peripherals.config import (
    CorsairConfig,
    PluginConfig,
    load_peripherals_config,
)

logger = logging.getLogger(__name__)

_CREDS_DIR = Path("data/config/peripherals")


def _read_config() -> CorsairConfig:
    """Load the current peripherals configuration."""
    return load_peripherals_config()


def _plugin_dict(p: PluginConfig, health_cache: dict[str, str]) -> dict:
    return {
        "name": p.name,
        "enabled": p.enabled,
        "has_credentials": p.credentials_file is not None,
        "health": health_cache.get(p.name, "unknown"),
    }


# ── Handlers ─────────────────────────────────────────────────────── #


async def _get_transducers(_request: web.Request) -> web.Response:
    """GET /api/transducers — list available plugins with status."""
    cfg = _read_config()
    health_cache: dict[str, str] = _request.app.get("_transducers_health", {})
    plugins = [_plugin_dict(p, health_cache) for p in cfg.plugins]
    return web.json_response({"plugins": plugins})


async def _put_transducer(request: web.Request) -> web.Response:
    """PUT /api/transducers/{plugin} — enable/disable + save credentials."""
    plugin_name = request.match_info["plugin"]
    cfg = _read_config()

    # Find the plugin in config
    target: PluginConfig | None = None
    for p in cfg.plugins:
        if p.name == plugin_name:
            target = p
            break
    if target is None:
        raise web.HTTPNotFound(text=f"plugin not found: {plugin_name}")

    try:
        payload = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="invalid JSON body")  # noqa: B904

    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="request body must be an object")

    # Handle enable/disable
    enabled = payload.get("enabled")
    credentials = payload.get("credentials")

    # Save credentials securely if provided
    if credentials and isinstance(credentials, dict):
        _CREDS_DIR.mkdir(parents=True, exist_ok=True)
        creds_path = _CREDS_DIR / f"{plugin_name}.json"
        creds_path.write_text(json.dumps(credentials, indent=2))
        # Set file permissions to 0600 (owner read/write only)
        os.chmod(creds_path, stat.S_IRUSR | stat.S_IWUSR)

    # Update the YAML config for enabled state
    if enabled is not None:
        _update_plugin_enabled(plugin_name, bool(enabled))

    # Re-read config after update
    cfg = _read_config()
    for p in cfg.plugins:
        if p.name == plugin_name:
            health_cache: dict[str, str] = request.app.get("_transducers_health", {})
            return web.json_response(_plugin_dict(p, health_cache))

    return web.json_response({"name": plugin_name, "enabled": bool(enabled)})


async def _get_transducer_health(request: web.Request) -> web.Response:
    """GET /api/transducers/{plugin}/health — return health status."""
    plugin_name = request.match_info["plugin"]
    health_cache: dict[str, str] = request.app.get("_transducers_health", {})
    status = health_cache.get(plugin_name, "unknown")
    return web.json_response({"plugin": plugin_name, "status": status})


async def _post_transducer_test(request: web.Request) -> web.Response:
    """POST /api/transducers/{plugin}/test — send a test message."""
    plugin_name = request.match_info["plugin"]

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    target = payload.get("target", "test")
    content = payload.get("content", f"Test message from OpenBaD to {plugin_name}")

    try:
        from openbad.skills.server import skill_server  # noqa: PLC0415

        tools = {t.name: t for t in skill_server.list_tools()}
        if "transmit_message" not in tools:
            raise web.HTTPServiceUnavailable(text="transmit_message skill not available")

        # Call the transmit_message skill
        result = await tools["transmit_message"].run(
            platform=plugin_name,
            operation="sendMessage",
            target=target,
            content=content,
        )
        return web.json_response({"ok": True, "result": str(result)})
    except web.HTTPException:
        raise
    except Exception as exc:
        logger.exception("Test message to %s failed", plugin_name)
        return web.json_response(
            {"ok": False, "error": str(exc)}, status=500,
        )


# ── Config mutation helper ────────────────────────────────────────── #


def _update_plugin_enabled(plugin_name: str, enabled: bool) -> None:
    """Toggle the enabled flag for a plugin in peripherals.yaml."""
    import yaml  # noqa: PLC0415

    # Find the config file
    for candidate in [
        Path("/etc/openbad/peripherals.yaml"),
        Path("config/peripherals.yaml"),
    ]:
        if candidate.exists():
            config_path = candidate
            break
    else:
        config_path = Path("config/peripherals.yaml")

    raw = (yaml.safe_load(config_path.read_text()) or {}) if config_path.exists() else {}

    corsair = raw.setdefault("corsair", {})
    plugins = corsair.setdefault("plugins", [])

    for p in plugins:
        if isinstance(p, dict) and p.get("name") == plugin_name:
            p["enabled"] = enabled
            break
    else:
        plugins.append({"name": plugin_name, "enabled": enabled})

    config_path.write_text(yaml.dump(raw, default_flow_style=False))


# ── Route registration ────────────────────────────────────────────── #


def setup_transducer_routes(app: web.Application) -> None:
    """Register transducer API routes on the aiohttp app."""
    app.router.add_get("/api/transducers", _get_transducers)
    app.router.add_put("/api/transducers/{plugin}", _put_transducer)
    app.router.add_get("/api/transducers/{plugin}/health", _get_transducer_health)
    app.router.add_post("/api/transducers/{plugin}/test", _post_transducer_test)
