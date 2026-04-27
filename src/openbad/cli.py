"""OpenBaD command-line interface.

Entrypoint: ``openbad`` (see ``[project.scripts]`` in ``pyproject.toml``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

import click

import openbad
from openbad.wui.usage_tracker import UsageTracker, resolve_usage_db_path

log = logging.getLogger(__name__)

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


def _normalized_endocrine_deltas(deltas: dict[str, float]) -> dict[str, float]:
    """Return hormone deltas with unsupported/zero values removed."""
    allowed = {"dopamine", "adrenaline", "cortisol", "endorphin"}
    normalized: dict[str, float] = {}
    for key, value in deltas.items():
        if key not in allowed:
            continue
        parsed = float(value)
        if abs(parsed) < 1e-12:
            continue
        normalized[key] = parsed
    return normalized


def _should_publish_endocrine_event(
    previous_levels: dict[str, float],
    current_levels: dict[str, float],
    hormone: str,
    *,
    epsilon: float = 1e-9,
) -> bool:
    """Return True when a hormone level changed meaningfully."""
    before = float(previous_levels.get(hormone, 0.0))
    after = float(current_levels.get(hormone, 0.0))
    return abs(after - before) > epsilon


def _ensure_heartbeat_timer() -> None:
    """Ensure the heartbeat timer is running at the configured interval.

    Called after start/restart/update (all run as root via sudo) so the
    timer is always consistent with the persisted config.
    Uses ``systemctl start openbad-heartbeat-apply.service`` which runs the
    apply script as root without needing sudo inside the process.
    Does nothing if systemctl is unavailable (dev/test environments).
    """
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return

    # Check timer and drop-in state
    timer_active = subprocess.run(  # noqa: S603
        [systemctl, "is-active", "openbad-heartbeat.timer"],
        capture_output=True, text=True, check=False,
    ).stdout.strip() == "active"

    dropin = Path("/etc/systemd/system/openbad-heartbeat.timer.d/interval.conf")

    if not timer_active or not dropin.exists():
        subprocess.run(  # noqa: S603
            [systemctl, "start", "openbad-heartbeat-apply.service"],
            check=False,
        )


def _find_project_root() -> Path | None:
    env_root = os.environ.get("OPENBAD_PROJECT_ROOT")
    candidates: list[Path] = []
    if env_root:
        candidates.append(Path(env_root).expanduser())
    candidates.append(Path.cwd())
    candidates.extend(Path(__file__).resolve().parents)

    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        for root in (resolved, *resolved.parents):
            if root in seen:
                continue
            seen.add(root)
            if (root / "pyproject.toml").is_file() and (root / "scripts" / "install.sh").is_file():
                return root
    return None


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
    help="Skip systemd unit sync and service restart (dev mode).",
)
@click.option(
    "--deps",
    is_flag=True,
    help="Also update dependencies from the GitHub release wheels tarball.",
)
@click.option(
    "--full",
    is_flag=True,
    help="Run the full install.sh bootstrap script.",
)
def update(skip_services: bool, deps: bool, full: bool) -> None:
    """Pull latest code, reinstall, and restart services.

    \b
    Three modes (fastest → slowest):
      openbad update          Quick: git pull + no-deps install + restart (~5s)
      openbad update --deps   Also update deps from GitHub release wheels
      openbad update --full   Run the full scripts/install.sh bootstrap
    """
    from openbad.updater import deps_update, full_update, quick_update

    project_root = _find_project_root()
    if project_root is None:
        raise click.ClickException(
            "Project root not found. Run from a git checkout of the OpenBaD repository "
            "or set OPENBAD_PROJECT_ROOT."
        )

    try:
        if full:
            full_update(project_root, skip_services=skip_services)
        elif deps:
            deps_update(project_root, skip_services=skip_services)
        else:
            quick_update(project_root, skip_services=skip_services)
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(
            f"Update failed with exit code {exc.returncode}"
        ) from exc
    except PermissionError as exc:
        raise click.ClickException(
            "Permission denied. Run 'sudo openbad update' to update system services."
        ) from exc

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
    If so, inspects the task and research queues and publishes a scheduler tick
    to the MQTT nervous system. Actual autonomous execution happens in the
    long-lived daemon subscriber, not in the timer process.
    If the interval has not elapsed the command exits silently (non-zero is
    NOT returned — the timer must not be considered failed on a skip).
    """
    import time

    import yaml

    from openbad.autonomy.endocrine_runtime import EndocrineRuntime, load_endocrine_config
    from openbad.autonomy.session_policy import load_session_policy, session_allows
    from openbad.nervous_system import topics
    from openbad.nervous_system.client import NervousSystemClient
    from openbad.state.db import DEFAULT_STATE_DB_PATH, initialize_state_db
    from openbad.tasks.heartbeat import HeartbeatStore
    from openbad.tasks.models import TaskModel
    from openbad.tasks.research_queue import initialize_research_db
    from openbad.tasks.research_service import ResearchService
    from openbad.tasks.service import TaskService

    from openbad.state.event_log import setup_logging

    setup_logging()

    cfg_path = Path("/var/lib/openbad/heartbeat.yaml")
    interval = 60
    if cfg_path.exists():
        try:
            data = yaml.safe_load(cfg_path.read_text()) or {}
            interval = max(5, int(data.get("interval_seconds", 60)))
        except Exception:  # noqa: BLE001, S110
            pass

    resolved_db_path = Path(db_path) if db_path else DEFAULT_STATE_DB_PATH
    conn = initialize_state_db(resolved_db_path)
    initialize_research_db(conn)

    hb_store = HeartbeatStore(conn)
    hb_store.initialize()

    now = time.time()
    state = hb_store.load()
    last_heartbeat_at = float(state.last_heartbeat_at or 0.0)
    if last_heartbeat_at > 0 and (now - last_heartbeat_at) < interval:
        hb_store.increment_silent_skip()
        return

    hb_store.reset_silent_skip()
    hb_store.record_heartbeat(now)

    policy = load_session_policy()
    endocrine_runtime = EndocrineRuntime(config=load_endocrine_config())
    endocrine_runtime.decay_to(now)
    task_svc = TaskService.get_instance(resolved_db_path)

    def _top_pending_task() -> TaskModel | None:
        return task_svc.top_pending_user_task()

    eligible_task_id: str | None = None
    tasks_policy = session_allows(policy, "tasks", "allow_task_autonomy", True)
    tasks_gate = endocrine_runtime.gate("tasks")
    if tasks_policy and tasks_gate.enabled:
        top_task = _top_pending_task()
        if top_task is not None:
            eligible_task_id = top_task.task_id
        else:
            log.info("Task autonomy enabled but no eligible pending tasks found.")
    else:
        log.info(
            "Task processing skipped: policy=%s gate_enabled=%s gate_reason=%s",
            tasks_policy,
            tasks_gate.enabled,
            getattr(tasks_gate, "disabled_reason", None),
        )

    eligible_research_id: str | None = None
    research_policy = session_allows(policy, "research", "allow_research_autonomy", True)
    research_gate = endocrine_runtime.gate("research")
    if research_policy and research_gate.enabled:
        research_svc = ResearchService.get_instance(resolved_db_path)
        node = research_svc.peek()
        if node is not None:
            eligible_research_id = node.node_id
        else:
            log.info("Research autonomy enabled but no pending research nodes found.")
    else:
        log.info(
            "Research processing skipped: policy=%s gate_enabled=%s gate_reason=%s",
            research_policy,
            research_gate.enabled,
            getattr(research_gate, "disabled_reason", None),
        )

    eligible_doctor = False
    doctor_policy = session_allows(policy, "doctor", "allow_endocrine_doctor", True)
    doctor_activated = endocrine_runtime.has_any_activation()
    if doctor_policy and doctor_activated:
        eligible_doctor = True
    else:
        log.info(
            "Doctor processing skipped: policy=%s has_activation=%s levels=%s",
            doctor_policy,
            doctor_activated,
            endocrine_runtime.levels,
        )

    tick_payload = json.dumps(
        {
            "ts": now,
            "interval_seconds": interval,
            "dispatched_count": (
                int(eligible_task_id is not None)
                + int(eligible_research_id is not None)
                + int(eligible_doctor)
            ),
            "dispatched_task_ids": [task_id for task_id in (eligible_task_id, eligible_research_id) if task_id],
            "eligible_task_id": None,
            "eligible_research_id": None,
            "queued_task_id": eligible_task_id,
            "queued_research_id": eligible_research_id,
            "queued_doctor": eligible_doctor,
            "executed_task_id": None,
            "executed_research_id": None,
            "silent_skip_count": 0,
        }
    ).encode()

    task_payload = None
    if eligible_task_id is not None:
        task_payload = json.dumps(
            {
                "ts": now,
                "mode": "specific",
                "task_id": eligible_task_id,
                "source": "heartbeat",
                "reason": "heartbeat selected next eligible task",
            }
        ).encode()

    research_payload = None
    if eligible_research_id is not None:
        research_payload = json.dumps(
            {
                "ts": now,
                "mode": "specific",
                "node_id": eligible_research_id,
                "source": "heartbeat",
                "reason": "heartbeat selected next eligible research",
            }
        ).encode()

    doctor_payload = None
    if eligible_doctor:
        doctor_payload = json.dumps(
            {
                "ts": now,
                "source": "heartbeat",
                "reason": "endocrine activation detected",
                "context": {
                    "levels": endocrine_runtime.levels,
                    "severity": endocrine_runtime.current_severity(),
                },
            }
        ).encode()

    try:
        client = NervousSystemClient.get_instance(host=mqtt_host, port=mqtt_port)
        client.connect(timeout=3.0)
        client.publish_bytes(topics.SCHEDULER_TICK, tick_payload)
        if task_payload is not None:
            client.publish_bytes(topics.TASK_WORK_REQUEST, task_payload)
        if research_payload is not None:
            client.publish_bytes(topics.RESEARCH_WORK_REQUEST, research_payload)
        if doctor_payload is not None:
            client.publish_bytes(topics.DOCTOR_CALL, doctor_payload)
        client.disconnect()
        NervousSystemClient.reset_instance()
    except Exception:  # noqa: BLE001, S110
        pass


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
    from openbad.state.event_log import setup_logging  # noqa: PLC0415

    setup_logging(verbose=verbose)


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
            raise click.ClickException(message) from exc
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
