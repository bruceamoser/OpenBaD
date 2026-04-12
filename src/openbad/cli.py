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
from pathlib import Path
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

_HEARTBEAT_CONFIG_PATH = Path("/var/lib/openbad/heartbeat.yaml")
_HEARTBEAT_APPLY_SCRIPT = Path("/usr/local/bin/openbad-apply-heartbeat-interval")


def _ensure_heartbeat_timer() -> None:
    """Ensure the heartbeat timer is running at the configured interval.

    Called after start/restart/update so the timer is always consistent
    with the persisted config, regardless of how services were started.
    Does nothing if systemctl or the helper script are unavailable.
    """
    systemctl = shutil.which("systemctl")
    if not systemctl or not _HEARTBEAT_APPLY_SCRIPT.is_file():
        return

    # Read configured interval (default 60 s)
    interval = 60
    if _HEARTBEAT_CONFIG_PATH.exists():
        try:
            import yaml  # noqa: PLC0415
            data = yaml.safe_load(_HEARTBEAT_CONFIG_PATH.read_text()) or {}
            interval = max(5, int(data.get("interval_seconds", 60)))
        except Exception:  # noqa: BLE001, S110
            pass  # malformed config — use default

    # Check whether the timer is active
    result = subprocess.run(  # noqa: S603
        [systemctl, "is-active", "openbad-heartbeat.timer"],
        capture_output=True,
        text=True,
        check=False,
    )
    timer_active = result.stdout.strip() == "active"

    dropin = Path("/etc/systemd/system/openbad-heartbeat.timer.d/interval.conf")

    if not timer_active or not dropin.exists():
        sudo = shutil.which("sudo")
        if not sudo:
            return
        subprocess.run(  # noqa: S603
            [sudo, str(_HEARTBEAT_APPLY_SCRIPT), str(interval)],
            check=False,
        )


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
    _ensure_heartbeat_timer()
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
    _ensure_heartbeat_timer()
    click.echo("Restarted: " + ", ".join(units))


@main.command()
@click.option(
    "--skip-services",
    is_flag=True,
    help="Pass --skip-services to the installer (dev mode).",
)
def update(skip_services: bool) -> None:
    """Pull latest code, reinstall, and restart services.

    Runs ``scripts/install.sh`` from the project root to update the
    Python package, configuration files, and systemd units, then
    restarts all managed services.
    """
    install_script = Path(__file__).resolve().parents[2] / "scripts" / "install.sh"
    if not install_script.is_file():
        raise click.ClickException(
            f"Install script not found at {install_script}. "
            "Run from a git checkout of the OpenBaD repository."
        )

    project_root = install_script.parent.parent

    # 1. git pull (best effort — may be a non-git install)
    click.echo("Pulling latest changes...")
    git_bin = shutil.which("git")
    if git_bin:
        pull = subprocess.run(  # noqa: S603
            [git_bin, "-C", str(project_root), "pull", "--ff-only"],
            check=False,
            capture_output=True,
            text=True,
        )
        if pull.returncode == 0:
            click.echo(pull.stdout.strip() or "Already up to date.")
        else:
            click.echo(f"git pull skipped: {pull.stderr.strip()}")
    else:
        click.echo("git not found, skipping pull.")

    # 2. Run install script
    click.echo("Running install script...")
    cmd: list[str] = [str(install_script)]
    if skip_services:
        cmd.append("--skip-services")
    try:
        subprocess.run(  # noqa: S603
            cmd,
            check=True,
            cwd=str(project_root),
        )
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(
            f"Install script failed with exit code {exc.returncode}"
        ) from exc
    except PermissionError as exc:
        raise click.ClickException(
            "Permission denied. Run 'sudo openbad update' to update system services."
        ) from exc

    click.echo("Update complete.")
    _ensure_heartbeat_timer()


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


@main.command()
@click.option("--mqtt-host", default="localhost", help="MQTT broker host.")
@click.option("--mqtt-port", default=1883, type=int, help="MQTT broker port.")
@click.option("--db-path", default=None, type=click.Path(), help="State DB path.")
def heartbeat(mqtt_host: str, mqtt_port: int, db_path: str | None) -> None:
    """Run one heartbeat tick (called by systemd timer, not directly).

    Checks whether the configured interval has elapsed since the last tick.
    If so, creates a scheduled heartbeat task, dispatches pending work, and
    publishes ``agent/scheduler/tick`` to the MQTT nervous system.
    If the interval has not elapsed the command exits silently (non-zero is
    NOT returned — the timer must not be considered failed on a skip).
    """
    import time

    import yaml

    from openbad.state.db import DEFAULT_STATE_DB_PATH, initialize_state_db
    from openbad.tasks.heartbeat import HeartbeatStore
    from openbad.tasks.models import TaskKind, TaskModel, TaskPriority, TaskStatus
    from openbad.tasks.research_queue import initialize_research_db
    from openbad.tasks.scheduler import SchedulerConfig, TaskScheduler
    from openbad.tasks.store import TaskStore

    # -- read interval config -------------------------------------------
    cfg_path = Path("/var/lib/openbad/heartbeat.yaml")
    interval: int = 60
    if cfg_path.exists():
        try:
            data = yaml.safe_load(cfg_path.read_text()) or {}
            interval = max(5, int(data.get("interval_seconds", 60)))
        except Exception:  # noqa: BLE001, S110
            pass  # malformed config — use default

    # -- open DB --------------------------------------------------------
    resolved_db_path = Path(db_path) if db_path else DEFAULT_STATE_DB_PATH
    conn = initialize_state_db(resolved_db_path)
    initialize_research_db(conn)

    hb_store = HeartbeatStore(conn)
    hb_store.initialize()

    # -- eligibility check ----------------------------------------------
    now = time.time()
    state = hb_store.load()
    if state.last_heartbeat_at > 0 and (now - state.last_heartbeat_at) < interval:
        hb_store.increment_silent_skip()
        return  # not due yet — exit silently so the timer isn't marked failed

    hb_store.reset_silent_skip()
    hb_store.record_heartbeat(now)

    # -- create a heartbeat-spawned task --------------------------------
    task_store = TaskStore(conn)
    task = TaskModel.new(
        "Heartbeat",
        description=f"Scheduled heartbeat tick at {now:.0f}",
        kind=TaskKind.SCHEDULED,
        priority=int(TaskPriority.LOW),
        owner="heartbeat-timer",
    )
    task_store.create_task(task)

    # -- dispatch pending work ------------------------------------------
    dispatched: list[str] = []

    def _on_dispatch(t: TaskModel, lease_id: str) -> None:  # noqa: ARG001
        try:
            task_store.update_task_status(t.task_id, TaskStatus.RUNNING)
            dispatched.append(t.task_id)
        except Exception:  # noqa: BLE001, S110
            pass  # status update best-effort

    scheduler = TaskScheduler(
        conn=conn,
        worker_id="heartbeat-cron",
        callback=_on_dispatch,
        config=SchedulerConfig(),
    )
    scheduler.tick()

    # -- publish MQTT tick ----------------------------------------------
    from openbad.nervous_system import topics
    from openbad.nervous_system.client import NervousSystemClient

    tick_payload = json.dumps({
        "ts": now,
        "interval_seconds": interval,
        "dispatched_count": len(dispatched),
        "dispatched_task_ids": dispatched,
        "silent_skip_count": 0,
    }).encode()

    try:
        client = NervousSystemClient.get_instance(host=mqtt_host, port=mqtt_port)
        client.connect(timeout=3.0)
        client.publish_bytes(topics.SCHEDULER_TICK, tick_payload)
        import time as _time  # already imported above but explicit for clarity
        _time.sleep(0.2)  # allow paho to flush the outbound queue
        client.disconnect()
        NervousSystemClient.reset_instance()
    except Exception:  # noqa: BLE001, S110
        pass  # MQTT unavailable — task still created; do not fail the timer


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
        if _service_enabled_state(unit) in {
            "disabled",
            "masked",
            "not-found",
            "systemctl-unavailable",
        }:
            continue
        units.append(unit)
    if units:
        return tuple(units)

    fallback_units = tuple(
        unit
        for unit in CORE_SERVICE_UNITS
        if _service_enabled_state(unit) not in {"masked", "not-found", "systemctl-unavailable"}
    )
    return fallback_units or CORE_SERVICE_UNITS


def _validate_health_url(wui_url: str) -> None:
    parsed = urllib_parse.urlparse(wui_url)
    if parsed.scheme not in {"http", "https"}:
        raise click.ClickException("--wui-url must use http or https")
