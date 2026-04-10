"""OpenBaD command-line interface.

Entrypoint: ``openbad`` (see ``[project.scripts]`` in ``pyproject.toml``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import sys
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

import click

import openbad

SYSTEMCTL_BIN = shutil.which("systemctl") or "/bin/systemctl"
SERVICE_UNITS = (
    "openbad-broker.service",
    "openbad.service",
    "openbad-wui.service",
)
CORE_SERVICE_UNITS = (
    "openbad.service",
    "openbad-wui.service",
)
BROKER_SERVICE_UNIT = "openbad-broker.service"


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx: click.Context) -> None:
    """OpenBaD — Biological as Digital agent daemon."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@main.command(hidden=True)
@click.option("--host", default="localhost", help="MQTT broker host.")
@click.option("--port", default=1883, type=int, help="MQTT broker port.")
@click.option("--dry-run", is_flag=True, help="Validate config and exit.")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging.")
def run(host: str, port: int, dry_run: bool, verbose: bool) -> None:
    """Start the OpenBaD daemon."""
    _configure_logging(verbose)

    from openbad.daemon import Daemon

    daemon = Daemon(mqtt_host=host, mqtt_port=port, dry_run=dry_run)
    try:
        asyncio.run(daemon.start())
    except KeyboardInterrupt:
        asyncio.run(daemon.stop())


@main.command()
def start() -> None:
    """Start all OpenBaD services and return immediately."""
    units = _managed_service_units()
    _systemctl("start", *units)
    click.echo("Started: " + ", ".join(units))


@main.command()
def stop() -> None:
    """Stop all OpenBaD services and return immediately."""
    units = tuple(reversed(_managed_service_units()))
    _systemctl("stop", *units)
    click.echo("Stopped: " + ", ".join(units))


@main.command()
def restart() -> None:
    """Restart all OpenBaD services and return immediately."""
    units = _managed_service_units()
    _systemctl("restart", *units)
    click.echo("Restarted: " + ", ".join(units))


@main.command(name="health")
@click.option("--host", default="localhost", help="MQTT broker host.")
@click.option("--port", default=1883, type=int, help="MQTT broker port.")
@click.option("--wui-url", default="http://127.0.0.1:9200/health", help="Web UI health endpoint.")
def health(host: str, port: int, wui_url: str) -> None:
    """Report service, MQTT, and WUI health."""
    from openbad.nervous_system.client import NervousSystemClient

    _validate_health_url(wui_url)

    services = {unit: _service_state(unit) for unit in SERVICE_UNITS}
    enabled = {unit: _service_enabled_state(unit) for unit in SERVICE_UNITS}
    result: dict[str, object] = {
        "services": services,
        "enabled": enabled,
        "mqtt_reachable": False,
        "wui_http": {"ok": False, "url": wui_url},
    }

    try:
        client = NervousSystemClient(host=host, port=port)
        client.connect(timeout=3.0)
        result["mqtt_reachable"] = True
        client.disconnect()
    except ConnectionError:
        pass

    try:
        with urllib_request.urlopen(wui_url, timeout=3.0) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
            result["wui_http"] = {
                "ok": True,
                "url": wui_url,
                "status": response.status,
                "payload": payload,
            }
    except (urllib_error.URLError, TimeoutError, json.JSONDecodeError):
        pass

    click.echo(json.dumps(result, indent=2))
    core_services_ok = all(services[unit] == "active" for unit in CORE_SERVICE_UNITS)
    broker_optional_ok = (
        services[BROKER_SERVICE_UNIT] == "active"
        or enabled[BROKER_SERVICE_UNIT] == "disabled"
        or bool(result["mqtt_reachable"])
    )
    wui_ok = bool(result["wui_http"]["ok"])
    healthy = (
        core_services_ok
        and broker_optional_ok
        and bool(result["mqtt_reachable"])
        and wui_ok
    )
    sys.exit(0 if healthy else 1)


@main.command(name="status", hidden=True)
@click.option("--host", default="localhost", help="MQTT broker host.")
@click.option("--port", default=1883, type=int, help="MQTT broker port.")
@click.option("--wui-url", default="http://127.0.0.1:9200/health", help="Web UI health endpoint.")
def status(host: str, port: int, wui_url: str) -> None:
    """Backward-compatible alias for ``health``."""
    ctx = click.get_current_context()
    ctx.invoke(health, host=host, port=port, wui_url=wui_url)


@main.command()
def version() -> None:
    """Print version and exit."""
    click.echo(f"openbad {openbad.__version__}")


@main.command()
@click.option(
    "--config-dir",
    default=None,
    type=click.Path(),
    help="Configuration directory (default: ~/.config/openbad/).",
)
@click.option("--host", default="localhost", help="MQTT broker host.")
@click.option("--port", default=1883, type=int, help="MQTT broker port.")
@click.option("--non-interactive", is_flag=True, help="Accept all defaults.")
@click.option("--check", "check_only", is_flag=True, help="Validate existing setup.")
def setup(
    config_dir: str | None,
    host: str,
    port: int,
    non_interactive: bool,
    check_only: bool,
) -> None:
    """Interactive first-run setup wizard."""
    from pathlib import Path

    from openbad.setup import DEFAULT_CONFIG_DIR, run_wizard

    path = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
    ok = run_wizard(
        config_dir=path,
        mqtt_host=host,
        mqtt_port=port,
        non_interactive=non_interactive,
        check_only=check_only,
    )
    sys.exit(0 if ok else 1)


@main.command()
@click.option("--host", default="localhost", help="MQTT broker host.")
@click.option("--port", default=1883, type=int, help="MQTT broker port.")
def tui(host: str, port: int) -> None:
    """Launch the terminal UI."""
    try:
        from openbad.tui.app import OpenBaDApp
    except ImportError:
        click.echo("TUI requires the 'tui' extra: pip install openbad[tui]", err=True)
        sys.exit(1)
    app = OpenBaDApp(mqtt_host=host, mqtt_port=port)
    app.run()


@main.command(hidden=True)
@click.option("--host", default="127.0.0.1", help="Web UI bind host.")
@click.option("--port", default=9200, type=int, help="Web UI bind port.")
@click.option("--mqtt-host", default="localhost", help="MQTT broker host.")
@click.option("--mqtt-port", default=1883, type=int, help="MQTT broker port.")
def wui(host: str, port: int, mqtt_host: str, mqtt_port: int) -> None:
    """Launch the web UI server with MQTT->WebSocket bridge."""
    from openbad.wui.server import run_server

    asyncio.run(
        run_server(
            host=host,
            port=port,
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
        )
    )


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _systemctl(command: str, *units: str) -> None:
    try:
        cmd = [SYSTEMCTL_BIN, command, *units]
        subprocess.run(  # noqa: S603
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise click.ClickException("systemctl not found; this command requires systemd") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() or exc.stdout.strip() or f"systemctl {command} failed"
        if "Interactive authentication required" in stderr or "Access denied" in stderr:
            message = (
                f"systemd denied '{command}'. "
                f"Run 'sudo openbad {command}' to manage system services."
            )
            raise click.ClickException(
                message
            ) from exc
        raise click.ClickException(stderr) from exc


def _service_state(unit: str) -> str:
    try:
        cmd = [SYSTEMCTL_BIN, "is-active", unit]
        proc = subprocess.run(  # noqa: S603
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return "systemctl-unavailable"

    state = proc.stdout.strip() or proc.stderr.strip()
    return state or "unknown"


def _service_enabled_state(unit: str) -> str:
    try:
        cmd = [SYSTEMCTL_BIN, "is-enabled", unit]
        proc = subprocess.run(  # noqa: S603
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return "systemctl-unavailable"

    state = proc.stdout.strip() or proc.stderr.strip()
    return state or "unknown"


def _managed_service_units() -> tuple[str, ...]:
    units = []
    for unit in SERVICE_UNITS:
        if _service_enabled_state(unit) == "disabled":
            continue
        units.append(unit)
    return tuple(units) if units else CORE_SERVICE_UNITS


def _validate_health_url(wui_url: str) -> None:
    parsed = urllib_parse.urlparse(wui_url)
    if parsed.scheme not in {"http", "https"}:
        raise click.ClickException("--wui-url must use http or https")
