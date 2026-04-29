"""WUI API routes for the Transducers panel (Corsair peripheral plugins)."""

from __future__ import annotations

import json
import logging
import os
import stat

from aiohttp import web

from openbad.peripherals.config import (
    CorsairConfig,
    PluginConfig,
    load_peripherals_config,
    resolve_config_write_path,
    resolve_credentials_dir,
)

logger = logging.getLogger(__name__)

# Available Corsair plugin catalog — integrations users can add.
# Each entry describes the plugin, required credential fields, and a hint.
_PLUGIN_CATALOG: list[dict] = [
    {
        "name": "discord",
        "label": "Discord",
        "icon": "💬",
        "description": "Send and receive messages via a Discord bot.",
        "credential_fields": [
            {"key": "bot_token", "label": "Bot Token", "secret": True},
        ],
    },
    {
        "name": "slack",
        "label": "Slack",
        "icon": "📨",
        "description": "Integrate with Slack workspaces via Bot/App tokens.",
        "credential_fields": [
            {"key": "bot_token", "label": "Bot Token", "secret": True},
            {"key": "app_token", "label": "App-Level Token", "secret": True},
        ],
    },
    {
        "name": "gmail",
        "label": "Gmail",
        "icon": "📧",
        "description": "Send and read emails through a Gmail account.",
        "credential_fields": [
            {"key": "credentials_json", "label": "OAuth Credentials JSON", "secret": True},
        ],
    },
    {
        "name": "telegram",
        "label": "Telegram",
        "icon": "✈️",
        "description": "Communicate via a Telegram bot.",
        "credential_fields": [
            {"key": "bot_token", "label": "Bot Token", "secret": True},
        ],
    },
    {
        "name": "matrix",
        "label": "Matrix",
        "icon": "🟩",
        "description": "Connect to Matrix/Element chat rooms.",
        "credential_fields": [
            {"key": "homeserver", "label": "Homeserver URL", "secret": False},
            {"key": "access_token", "label": "Access Token", "secret": True},
        ],
    },
    {
        "name": "whatsapp",
        "label": "WhatsApp",
        "icon": "📱",
        "description": "Send and receive messages via the WhatsApp Business API.",
        "credential_fields": [
            {"key": "phone_number_id", "label": "Phone Number ID", "secret": False},
            {"key": "access_token", "label": "Access Token", "secret": True},
        ],
    },
    {
        "name": "webhook",
        "label": "Generic Webhook",
        "icon": "🔗",
        "description": "Send outbound HTTP webhooks to any URL.",
        "credential_fields": [
            {"key": "url", "label": "Webhook URL", "secret": False},
            {"key": "auth_header", "label": "Authorization Header (optional)", "secret": True},
        ],
    },
]


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


async def _get_transducers_catalog(_request: web.Request) -> web.Response:
    """GET /api/transducers/catalog — list all available plugin types."""
    cfg = _read_config()
    existing_names = {p.name for p in cfg.plugins}
    catalog = [
        {**entry, "installed": entry["name"] in existing_names}
        for entry in _PLUGIN_CATALOG
    ]
    return web.json_response({"catalog": catalog})


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

    # Find the plugin in config — or accept if it was just created
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
        creds_dir = resolve_credentials_dir()
        creds_path = creds_dir / f"{plugin_name}.json"
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


async def _post_transducer(request: web.Request) -> web.Response:
    """POST /api/transducers — add a new plugin via the setup wizard."""
    try:
        payload = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="invalid JSON body")  # noqa: B904

    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="request body must be an object")

    name = str(payload.get("name", "")).strip()
    if not name:
        raise web.HTTPBadRequest(text="name is required")

    # Reject duplicates
    cfg = _read_config()
    if any(p.name == name for p in cfg.plugins):
        raise web.HTTPConflict(text=f"plugin '{name}' already exists")

    # Validate against catalog
    catalog_entry = next((c for c in _PLUGIN_CATALOG if c["name"] == name), None)
    if catalog_entry is None:
        raise web.HTTPBadRequest(text=f"unknown plugin type: {name}")

    credentials = payload.get("credentials")
    enabled = bool(payload.get("enabled", False))

    # Save credentials if provided
    if credentials and isinstance(credentials, dict):
        creds_dir = resolve_credentials_dir()
        creds_path = creds_dir / f"{name}.json"
        creds_path.write_text(json.dumps(credentials, indent=2))
        os.chmod(creds_path, stat.S_IRUSR | stat.S_IWUSR)

    # Add to YAML config
    _update_plugin_enabled(name, enabled)

    # Re-read and return
    cfg = _read_config()
    for p in cfg.plugins:
        if p.name == name:
            health_cache: dict[str, str] = request.app.get("_transducers_health", {})
            return web.json_response(_plugin_dict(p, health_cache), status=201)

    return web.json_response({"name": name, "enabled": enabled}, status=201)


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

        tools_list = skill_server.list_tools()
        if hasattr(tools_list, '__await__'):
            tools_list = await tools_list
        tools = {t.name: t for t in tools_list}
        if "transmit_message" not in tools:
            # Fall back to credential verification
            return await _verify_credentials(plugin_name)

        # Call the transmit_message skill via call_skill
        from openbad.skills.server import call_skill  # noqa: PLC0415

        result = await call_skill("transmit_message", {
            "platform": plugin_name,
            "operation": "sendMessage",
            "target": target,
            "content": content,
        })
        return web.json_response({"ok": True, "result": str(result)})
    except web.HTTPException:
        raise
    except Exception as exc:
        logger.exception("Test message to %s failed", plugin_name)
        # Fall back to credential verification on skill errors
        try:
            return await _verify_credentials(plugin_name)
        except Exception:
            return web.json_response(
                {"ok": False, "error": str(exc)}, status=500,
            )


async def _verify_credentials(plugin_name: str) -> web.Response:
    """Verify credentials by calling the platform's API directly."""
    import aiohttp as _aiohttp  # noqa: PLC0415

    creds_dir = resolve_credentials_dir()
    creds_path = creds_dir / f"{plugin_name}.json"
    if not creds_path.exists():
        return web.json_response(
            {"ok": False, "error": "No credentials file found"}, status=400,
        )

    creds = json.loads(creds_path.read_text())

    async with _aiohttp.ClientSession() as session:
        if plugin_name == "telegram":
            token = creds.get("bot_token", "")
            async with session.get(
                f"https://api.telegram.org/bot{token}/getMe",
                timeout=_aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                if data.get("ok"):
                    bot = data["result"]
                    return web.json_response(
                        {"ok": True, "result": f"Connected as @{bot.get('username', '?')}"},
                    )
                return web.json_response(
                    {"ok": False, "error": data.get("description", "Invalid token")},
                )

        if plugin_name == "discord":
            token = creds.get("bot_token", "")
            async with session.get(
                "https://discord.com/api/v10/users/@me",
                headers={"Authorization": f"Bot {token}"},
                timeout=_aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return web.json_response(
                        {"ok": True, "result": f"Connected as {data.get('username', '?')}"},
                    )
                return web.json_response(
                    {"ok": False, "error": f"Discord returned {resp.status}"},
                )

        if plugin_name == "slack":
            token = creds.get("bot_token", "")
            async with session.post(
                "https://slack.com/api/auth.test",
                headers={"Authorization": f"Bearer {token}"},
                timeout=_aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                if data.get("ok"):
                    user = data.get('user', '?')
                    team = data.get('team', '?')
                    return web.json_response(
                        {"ok": True, "result": f"Connected as {user} in {team}"},
                    )
                return web.json_response(
                    {"ok": False, "error": data.get("error", "Invalid token")},
                )

        if plugin_name == "webhook":
            url = creds.get("url", "")
            if not url:
                return web.json_response(
                    {"ok": False, "error": "No webhook URL configured"},
                )
            headers: dict[str, str] = {}
            auth = creds.get("auth_header", "")
            if auth:
                headers["Authorization"] = auth
            async with session.post(
                url,
                json={"text": "OpenBaD connection test"},
                headers=headers,
                timeout=_aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status < 400:
                    return web.json_response(
                        {"ok": True, "result": f"Webhook returned {resp.status}"},
                    )
                return web.json_response(
                    {"ok": False, "error": f"Webhook returned {resp.status}"},
                )

    msg = "Credentials saved (no live verification available for this plugin)"
    return web.json_response({"ok": True, "result": msg})


# ── Config mutation helper ────────────────────────────────────────── #


def _update_plugin_enabled(plugin_name: str, enabled: bool) -> None:
    """Toggle the enabled flag for a plugin in peripherals.yaml.

    Writes to the first writable config path (``/etc/openbad`` in
    production, ``config/`` in dev) so the daemon picks up changes
    without manual file copying.
    """
    import yaml  # noqa: PLC0415

    write_path = resolve_config_write_path()

    raw = (yaml.safe_load(write_path.read_text()) or {}) if write_path.exists() else {}

    corsair = raw.setdefault("corsair", {})
    plugins = corsair.setdefault("plugins", [])

    for p in plugins:
        if isinstance(p, dict) and p.get("name") == plugin_name:
            p["enabled"] = enabled
            # Ensure credentials_file is set
            if not p.get("credentials_file"):
                p["credentials_file"] = f"{plugin_name}.json"
            break
    else:
        plugins.append({
            "name": plugin_name,
            "enabled": enabled,
            "credentials_file": f"{plugin_name}.json",
        })

    write_path.parent.mkdir(parents=True, exist_ok=True)
    write_path.write_text(yaml.dump(raw, default_flow_style=False))


# ── Route registration ────────────────────────────────────────────── #


def setup_transducer_routes(app: web.Application) -> None:
    """Register transducer API routes on the aiohttp app."""
    app.router.add_get("/api/transducers/catalog", _get_transducers_catalog)
    app.router.add_get("/api/transducers", _get_transducers)
    app.router.add_post("/api/transducers", _post_transducer)
    app.router.add_put("/api/transducers/{plugin}", _put_transducer)
    app.router.add_get("/api/transducers/{plugin}/health", _get_transducer_health)
    app.router.add_post("/api/transducers/{plugin}/test", _post_transducer_test)
