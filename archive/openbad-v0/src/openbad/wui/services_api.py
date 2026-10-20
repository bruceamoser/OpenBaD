"""System services API — query and control OpenBaD systemd units.

Routes:
- GET  /api/services              — list all OpenBaD units with status
- POST /api/services/{unit}/start — start a unit
- POST /api/services/{unit}/stop  — stop a unit
- POST /api/services/{unit}/restart — restart a unit
"""

from __future__ import annotations

import asyncio
import logging
import re

from aiohttp import web

log = logging.getLogger(__name__)


def _parse_memory(val: str | None) -> int | None:
    """Parse MemoryCurrent value from systemctl show."""
    if not val or val == "[not set]" or val == "infinity":
        return None
    try:
        return int(val)
    except ValueError:
        return None


# Only allow controlling known OpenBaD units — prevents arbitrary systemctl abuse
_ALLOWED_UNITS = frozenset({
    "openbad.service",
    "openbad-wui.service",
    "openbad-broker.service",
    "openbad-corsair.service",
    "openbad-searxng.service",
    "openbad-heartbeat.service",
    "openbad-heartbeat.timer",
    "openbad-heartbeat-apply.service",
    "openbad-heartbeat-watch.path",
    "openbad-telemetry-apply.service",
    "openbad-telemetry-watch.path",
})

_UNIT_RE = re.compile(r"^openbad[\w-]*\.(service|timer|path)$")


def _validate_unit(unit: str) -> bool:
    """Ensure the unit name is a known OpenBaD unit."""
    return unit in _ALLOWED_UNITS and _UNIT_RE.match(unit) is not None


async def _run_systemctl(*args: str) -> tuple[int, str]:
    """Run a systemctl command and return (returncode, stdout+stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "systemctl", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace").strip()


async def _run_sudo_systemctl(action: str, unit: str) -> tuple[int, str]:
    """Run sudo systemctl <action> <unit>."""
    proc = await asyncio.create_subprocess_exec(
        "sudo", "--non-interactive", "systemctl", action, unit,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace").strip()


async def _get_unit_info(unit: str) -> dict:
    """Get status info for a single unit."""
    _, raw = await _run_systemctl(
        "show", unit,
        "--property=ActiveState,SubState,Description,LoadState,"
        "MainPID,ExecMainStartTimestamp,MemoryCurrent",
    )
    info: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip()] = v.strip()

    active = info.get("ActiveState", "unknown")
    return {
        "unit": unit,
        "description": info.get("Description", ""),
        "load_state": info.get("LoadState", "unknown"),
        "active_state": active,
        "sub_state": info.get("SubState", "unknown"),
        "pid": int(info.get("MainPID", "0")) or None,
        "started_at": info.get("ExecMainStartTimestamp", ""),
        "memory_bytes": _parse_memory(info.get("MemoryCurrent")),
        "running": active == "active",
    }


async def _list_services(request: web.Request) -> web.Response:
    """Return status for all OpenBaD units."""
    tasks = [_get_unit_info(unit) for unit in sorted(_ALLOWED_UNITS)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    services = []
    for r in results:
        if isinstance(r, Exception):
            log.warning("Failed to query unit: %s", r)
        else:
            services.append(r)
    # Sort: running first, then by name
    services.sort(key=lambda s: (not s["running"], s["unit"]))
    return web.json_response({"services": services})


async def _service_action(request: web.Request) -> web.Response:
    """Start, stop, or restart a unit."""
    unit = request.match_info["unit"]
    action = request.match_info["action"]

    if action not in ("start", "stop", "restart"):
        return web.json_response({"error": f"Unknown action: {action}"}, status=400)

    if not _validate_unit(unit):
        return web.json_response({"error": f"Unit not allowed: {unit}"}, status=403)

    # Don't allow stopping the WUI from the WUI
    if unit == "openbad-wui.service" and action == "stop":
        return web.json_response(
            {"error": "Cannot stop the WUI service from the WUI"},
            status=400,
        )

    log.info("Service action: %s %s", action, unit)
    rc, output = await _run_sudo_systemctl(action, unit)
    if rc != 0:
        return web.json_response(
            {"error": f"systemctl {action} {unit} failed (rc={rc}): {output}"},
            status=500,
        )

    # Return updated status
    info = await _get_unit_info(unit)
    return web.json_response({"result": "ok", "service": info})


def setup_services_routes(app: web.Application) -> None:
    """Register service management routes."""
    app.router.add_get("/api/services", _list_services)
    app.router.add_post("/api/services/{unit}/{action}", _service_action)
