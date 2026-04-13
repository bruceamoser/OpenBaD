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
    from contextlib import suppress

    import yaml

    from openbad.autonomy.endocrine_runtime import EndocrineRuntime, load_endocrine_config
    from openbad.autonomy.session_policy import (
        is_destructive_request,
        load_session_policy,
        session_allows,
        session_id_for,
    )
    from openbad.nervous_system.schemas.common_pb2 import Header
    from openbad.nervous_system.schemas.endocrine_pb2 import EndocrineEvent
    from openbad.state.db import DEFAULT_STATE_DB_PATH, initialize_state_db
    from openbad.tasks.heartbeat import HeartbeatStore
    from openbad.tasks.models import TaskKind, TaskModel, TaskPriority, TaskStatus
    from openbad.tasks.research_queue import ResearchQueue, initialize_research_db
    from openbad.tasks.store import TaskStore
    from openbad.wui.chat_pipeline import append_assistant_message, append_session_message
    from openbad.wui.server import _provider_is_valid, _read_providers_config, _resolve_chat_adapter

    from openbad.state.event_log import setup_logging
    setup_logging()

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
    last_heartbeat_at = float(state.last_heartbeat_at or 0.0)
    if last_heartbeat_at > 0 and (now - last_heartbeat_at) < interval:
        hb_store.increment_silent_skip()
        return  # not due yet — exit silently so the timer isn't marked failed

    hb_store.reset_silent_skip()
    hb_store.record_heartbeat(now)

    policy = load_session_policy()
    chat_session_id = session_id_for(policy, "chat")
    task_session_id = session_id_for(policy, "tasks")
    research_session_id = session_id_for(policy, "research")
    doctor_session_id = session_id_for(policy, "doctor")

    log.info(
        "Heartbeat sessions: chat=%s tasks=%s research=%s doctor=%s",
        chat_session_id,
        task_session_id,
        research_session_id,
        doctor_session_id,
    )

    endocrine_config = load_endocrine_config()
    endocrine_runtime = EndocrineRuntime(config=endocrine_config)
    endocrine_runtime.decay_to(now)
    levels_at_tick_start = endocrine_runtime.levels

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

    # -- autonomous work selection/execution -----------------------------
    dispatched: list[str] = []
    executed_task_id: str | None = None
    executed_research_id: str | None = None

    def _post_session(
        session_id: str,
        content: str,
        *,
        extra_metadata: dict[str, object] | None = None,
    ) -> None:
        try:
            append_assistant_message(session_id, content, extra_metadata=extra_metadata)
            log.info("Session message posted: session=%s length=%d", session_id, len(content))
        except Exception:
            log.exception("Failed to post session message: session=%s", session_id)
            _adjust(
                "session_write",
                f"Failed to write session message to {session_id}",
                {"cortisol": 0.04, "adrenaline": 0.02},
            )

    def _adjust(
        source: str,
        reason: str,
        deltas: dict[str, float],
        *,
        doctor_revelation: bool = False,
    ) -> None:
        normalized = _normalized_endocrine_deltas(deltas)
        if not normalized:
            log.info(
                "Skipped endocrine adjustment (no-op): source=%s reason=%s raw_deltas=%s",
                source,
                reason,
                deltas,
            )
            return

        before = endocrine_runtime.levels
        after = endocrine_runtime.apply_adjustment(
            source=source,
            reason=reason,
            deltas=normalized,
            doctor_revelation=doctor_revelation,
        )
        log.info(
            "Endocrine adjustment applied: source=%s reason=%s deltas=%s before=%s after=%s "
            "doctor_revelation=%s",
            source,
            reason,
            normalized,
            before,
            after,
            doctor_revelation,
        )

    def _has_recent_adjustment(source: str, reason: str, *, window_seconds: int) -> bool:
        row = conn.execute(
            """
            SELECT 1
            FROM endocrine_adjustments
            WHERE source = ?
              AND reason = ?
              AND ts >= ?
            LIMIT 1
            """,
            (source, reason, now - max(1, int(window_seconds))),
        ).fetchone()
        return row is not None

    def _adjust_from_log_level(
        level: str,
        source: str,
        reason: str,
        *,
        cooldown_seconds: int = 0,
    ) -> None:
        """Map log severity to endocrine adjustments.

        INFO: no endocrine change
        WARNING: slight stress bump
        ERROR/CRITICAL: larger stress bump
        """
        if cooldown_seconds > 0 and _has_recent_adjustment(
            source,
            reason,
            window_seconds=cooldown_seconds,
        ):
            return

        upper = level.strip().upper()
        if upper == "INFO":
            return
        if upper == "WARNING":
            _adjust(source, reason, {"cortisol": 0.02, "adrenaline": 0.01})
            return
        if upper in {"ERROR", "CRITICAL"}:
            _adjust(source, reason, {"cortisol": 0.12, "adrenaline": 0.06})
            return

    def _parse_doctor_payload(raw: str) -> dict[str, object] | None:
        text = (raw or "").strip()
        if not text:
            return None
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
        try:
            parsed = json.loads(text)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _pending_questions_count() -> int:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM tasks
            WHERE status IN ('pending', 'blocked_on_user')
              AND title LIKE 'Question:%'
            """
        ).fetchone()
        return int(row[0]) if row else 0

    def _run_llm(prompt: str, *, system_name: str) -> tuple[str, str, str] | None:
        try:
            _cfg_path, cfg = _read_providers_config()
            adapter, model, provider_name, is_fallback = _resolve_chat_adapter(cfg, system_name)
            if adapter is None or model is None:
                log.warning("LLM unavailable for %s: adapter=%s model=%s", system_name, adapter, model)
                return None
            if is_fallback:
                log.warning(
                    "Provider degraded for system=%s: using fallback provider=%s model=%s",
                    system_name, provider_name, model,
                )
                # Per-call fallback cortisol — throttled so the scan in
                # _monitor_nominal_state is the primary pressure source.
                fb_reason = f"Fallback provider used for {system_name}"
                if not _has_recent_adjustment(
                    "provider_fallback", fb_reason, window_seconds=300,
                ):
                    _adjust(
                        "provider_fallback",
                        fb_reason,
                        {"cortisol": 0.15, "adrenaline": 0.05},
                    )
                    _post_session(
                        doctor_session_id,
                        (
                            f"**Provider degradation detected** for system `{system_name}`: "
                            f"assigned provider unavailable, falling back to `{provider_name}/{model}`. "
                            "This indicates a non-nominal state — token may be expired or provider down."
                        ),
                    )
            log.info("LLM call: system=%s provider=%s model=%s fallback=%s", system_name, provider_name, model, is_fallback)
            result = asyncio.run(adapter.complete(prompt, model))
            log.info("LLM response: system=%s length=%d model=%s", system_name, len(result.content), result.model_id)
            return result.content.strip(), (provider_name or ""), result.model_id
        except Exception:
            log.exception("LLM call failed for system=%s", system_name)
            _adjust(
                "llm_error",
                f"LLM call failed for {system_name}",
                {"cortisol": 0.12, "adrenaline": 0.06},
            )
            return None

    def _top_pending_task() -> TaskModel | None:
        row = conn.execute(
            """
            SELECT task_id
            FROM tasks
            WHERE status = 'pending'
              AND kind NOT IN ('scheduled', 'system')
              AND title NOT LIKE 'Question:%'
              AND (due_at IS NULL OR due_at <= ?)
            ORDER BY priority DESC, created_at ASC
            LIMIT 1
            """,
            (now,),
        ).fetchone()
        if not row:
            return None
        return task_store.get_task(str(row[0]))

    def _create_question_task(source_task: TaskModel, question: str) -> TaskModel:
        q = TaskModel.new(
            title=f"Question: Approval required for task {source_task.task_id[:8]}",
            description=question,
            kind=TaskKind.SYSTEM,
            priority=int(TaskPriority.HIGH),
            owner="heartbeat-approval",
        )
        return task_store.create_task(q)

    def _create_reenable_task(system_name: str, reason: str, due_at: float) -> TaskModel:
        existing = conn.execute(
            """
            SELECT task_id
            FROM tasks
            WHERE status = 'pending'
              AND kind = 'system'
              AND title = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (f"Endocrine follow-up: re-enable {system_name}",),
        ).fetchone()
        if existing:
            task = task_store.get_task(str(existing[0]))
            if task is not None:
                return task

        t = TaskModel.new(
            title=f"Endocrine follow-up: re-enable {system_name}",
            description=(
                f"ENDOCRINE_REENABLE:{system_name}\n"
                f"Scheduled by doctor loop. Reason: {reason}"
            ),
            kind=TaskKind.SYSTEM,
            priority=int(TaskPriority.NORMAL),
            owner="endocrine-doctor",
            due_at=due_at,
        )
        return task_store.create_task(t)

    def _process_endocrine_followups() -> None:
        rows = conn.execute(
            """
            SELECT task_id
            FROM tasks
            WHERE status = 'pending'
              AND kind = 'system'
              AND title LIKE 'Endocrine follow-up: re-enable %'
              AND (due_at IS NULL OR due_at <= ?)
            ORDER BY created_at ASC
            """,
            (now,),
        ).fetchall()
        for row in rows:
            follow = task_store.get_task(str(row[0]))
            if follow is None:
                continue
            marker = (follow.description or "").splitlines()[0].strip()
            if not marker.startswith("ENDOCRINE_REENABLE:"):
                continue
            system_name = marker.split(":", 1)[1].strip().lower()
            original_reason = ""
            desc_lines = (follow.description or "").splitlines()
            for line in desc_lines:
                if line.strip().startswith("Scheduled by doctor loop. Reason:"):
                    original_reason = line.strip().split("Reason:", 1)[1].strip()
                    break

            # Ask the doctor LLM whether it's safe to re-enable.
            levels = endocrine_runtime.levels
            severity = endocrine_runtime.current_severity()
            followup_prompt = (
                "You are the OpenBaD endocrine doctor evaluating a scheduled follow-up.\n"
                f"The {system_name} subsystem was previously disabled.\n"
                f"Original reason: {original_reason or 'unknown'}\n"
                f"Current hormone levels: {json.dumps(levels, sort_keys=True)}\n"
                f"Current severity: {json.dumps(severity, sort_keys=True)}\n\n"
                "Return strict JSON with keys:\n"
                '  "safe_to_reenable": boolean,\n'
                '  "summary": string explaining your assessment,\n'
                '  "extend_minutes": integer (>0 only if not safe, to reschedule)\n'
            )

            llm = _run_llm(followup_prompt, system_name="doctor")
            if llm is not None:
                response_text, provider_name, model_id = llm
                parsed = _parse_doctor_payload(response_text)
                safe = True
                extend_minutes = 0
                doctor_summary = response_text

                if isinstance(parsed, dict):
                    safe = parsed.get("safe_to_reenable", True) is not False
                    extend_minutes = max(0, int(parsed.get("extend_minutes", 0)))
                    doctor_summary = str(parsed.get("summary", response_text)).strip()

                _post_session(
                    doctor_session_id,
                    (
                        f"Follow-up evaluation for {system_name}:\n"
                        f"{doctor_summary}\n"
                        f"Provider: {provider_name} / {model_id}"
                    ),
                    extra_metadata={
                        "doctor_revelation": True,
                        "health_decision": True,
                        "provider": provider_name,
                        "model": model_id,
                    },
                )
                task_store.append_event(
                    follow.task_id,
                    "doctor_followup_evaluated",
                    payload={
                        "system": system_name,
                        "safe_to_reenable": safe,
                        "summary": doctor_summary,
                        "provider": provider_name,
                        "model": model_id,
                    },
                )

                if safe:
                    endocrine_runtime.enable_system(
                        system_name,
                        reason=f"Doctor follow-up approved: {doctor_summary}",
                        now=now,
                    )
                    task_store.update_task_status(follow.task_id, TaskStatus.DONE)
                    _adjust(
                        "immune",
                        f"Doctor re-enabled {system_name}: {doctor_summary}",
                        {"endorphin": 0.08, "cortisol": -0.05},
                        doctor_revelation=True,
                    )
                    _post_session(
                        doctor_session_id,
                        f"Action executed: re-enabled {system_name}.",
                        extra_metadata={
                            "doctor_revelation": True,
                            "health_decision": True,
                        },
                    )
                else:
                    # Doctor says not safe yet -- reschedule.
                    reschedule_minutes = extend_minutes if extend_minutes > 0 else 15
                    new_due = now + (reschedule_minutes * 60)
                    task_store.update_task_status(follow.task_id, TaskStatus.DONE)
                    _create_reenable_task(
                        system_name,
                        f"Rescheduled: {doctor_summary}",
                        new_due,
                    )
                    _post_session(
                        doctor_session_id,
                        (
                            f"Follow-up deferred: {system_name} not safe to re-enable. "
                            f"Rescheduled in {reschedule_minutes}m. "
                            f"Reason: {doctor_summary}"
                        ),
                        extra_metadata={
                            "doctor_revelation": True,
                            "health_decision": True,
                        },
                    )
            else:
                # LLM unavailable -- fall back to re-enabling (original behavior)
                # but log to doctor session so it's visible.
                endocrine_runtime.enable_system(
                    system_name,
                    reason="Follow-up: doctor LLM unavailable, defaulting to re-enable",
                    now=now,
                )
                task_store.update_task_status(follow.task_id, TaskStatus.DONE)
                task_store.append_event(
                    follow.task_id,
                    "doctor_followup_fallback",
                    payload={
                        "system": system_name,
                        "reason": "doctor LLM unavailable",
                    },
                )
                _adjust(
                    "immune",
                    f"Re-enabled {system_name} (doctor unavailable fallback)",
                    {"endorphin": 0.04, "cortisol": -0.02},
                )
                _post_session(
                    doctor_session_id,
                    (
                        f"Follow-up fallback: re-enabled {system_name} "
                        f"(doctor LLM unavailable for evaluation)."
                    ),
                    extra_metadata={
                        "doctor_revelation": True,
                        "health_decision": True,
                    },
                )

    def _process_task(task_to_run: TaskModel) -> None:
        nonlocal executed_task_id
        combined = f"{task_to_run.title}\n\n{task_to_run.description}".strip()
        allow_destructive = session_allows(
            policy,
            "tasks",
            "allow_destructive",
            False,
        )
        if is_destructive_request(combined) and not allow_destructive:
            task_store.update_task_status(task_to_run.task_id, TaskStatus.BLOCKED_ON_USER)
            question = (
                "Approve destructive action for task "
                f"{task_to_run.task_id}: {task_to_run.title}. "
                "Reply in regular chat with explicit approval details."
            )
            _create_question_task(task_to_run, question)
            pending_q = _pending_questions_count()
            _post_session(
                chat_session_id,
                (
                    f"I deferred task '{task_to_run.title}' because it may be destructive. "
                    f"I also have {pending_q} question{'s' if pending_q != 1 else ''} "
                    "for you when you're ready."
                ),
            )
            _post_session(
                task_session_id,
                f"Deferred task {task_to_run.task_id} pending user approval.",
            )
            _adjust(
                "tasks",
                f"Deferred potentially destructive task {task_to_run.task_id}",
                {"cortisol": 0.06, "adrenaline": 0.03},
            )
            executed_task_id = task_to_run.task_id
            return

        task_store.update_task_status(task_to_run.task_id, TaskStatus.RUNNING)
        llm = _run_llm(
            (
                "You are the autonomous OpenBaD task worker. "
                "Complete the task in a non-destructive way and return a concise "
                "status summary.\n\n"
                f"Task: {task_to_run.title}\nDescription: {task_to_run.description}"
            ),
            system_name="tasks",
        )
        if llm is None:
            task_store.update_task_status(task_to_run.task_id, TaskStatus.FAILED)
            task_store.append_event(
                task_to_run.task_id,
                "autonomy_failed",
                payload={
                    "reason": "provider_unavailable",
                    "owner": task_to_run.owner,
                    "title": task_to_run.title,
                },
            )
            _post_session(
                task_session_id,
                f"Task {task_to_run.task_id} failed: no provider available.",
            )
            _adjust(
                "tasks",
                f"Task {task_to_run.task_id} failed due to unavailable provider",
                {"cortisol": 0.12, "adrenaline": 0.05},
            )
            return

        summary, provider_name, model_id = llm
        _llm_meta = {"provider": provider_name, "model": model_id}
        task_store.update_task_status(task_to_run.task_id, TaskStatus.DONE)
        task_store.append_event(
            task_to_run.task_id,
            "autonomy_completed",
            payload={
                "summary": summary,
                "provider": provider_name,
                "model": model_id,
            },
        )
        _post_session(task_session_id, f"Completed task '{task_to_run.title}': {summary}", extra_metadata=_llm_meta)
        _post_session(chat_session_id, f"Autonomous task update: completed '{task_to_run.title}'.", extra_metadata=_llm_meta)
        _adjust(
            "tasks",
            f"Completed task {task_to_run.task_id}",
            {"dopamine": 0.10, "endorphin": 0.05, "cortisol": -0.03},
        )
        executed_task_id = task_to_run.task_id
        dispatched.append(task_to_run.task_id)

    def _process_research() -> None:
        nonlocal executed_research_id
        queue = ResearchQueue(conn)
        node = queue.dequeue()
        if node is None:
            log.info("Research autonomy enabled but no pending research nodes found.")
            return

        log.info("Processing research node: %s (id=%s)", node.title, node.node_id)

        # Log the research question into the session so the user can follow along
        research_prompt = (
            f"**Research Project:** {node.title}\n\n"
            f"{node.description or '(no description)'}" if node.description
            else f"**Research Project:** {node.title}"
        )
        try:
            append_session_message(research_session_id, "user", research_prompt)
        except Exception:
            log.exception("Failed to post research prompt to session")

        llm = _run_llm(
            (
                "You are the OpenBaD research worker. "
                "Produce a concise research result with actionable next steps.\n\n"
                f"Research title: {node.title}\nDescription: {node.description}"
            ),
            system_name="research",
        )
        if llm is None:
            _post_session(
                research_session_id,
                f"Research node {node.node_id} dequeued but provider was unavailable.",
            )
            _adjust(
                "research",
                f"Research node {node.node_id} failed due to unavailable provider",
                {"cortisol": 0.09, "adrenaline": 0.04},
            )
            return

        summary, provider_name, model_id = llm
        _llm_meta = {"provider": provider_name, "model": model_id}
        _post_session(
            research_session_id,
            (
                f"**Research complete: {node.title}**\n\n"
                f"{summary}\n\n"
                f"*Provider: {provider_name} / {model_id}*"
            ),
            extra_metadata=_llm_meta,
        )
        _post_session(chat_session_id, f"Autonomous research update: completed '{node.title}'.", extra_metadata=_llm_meta)
        _adjust(
            "research",
            f"Completed research node {node.node_id}",
            {"dopamine": 0.08, "endorphin": 0.04, "cortisol": -0.02},
        )
        executed_research_id = node.node_id
        dispatched.append(node.node_id)

    def _monitor_nominal_state() -> None:
        # Lightweight anomaly rule: if many recent failures accumulate,
        # enqueue research for root-cause triage.
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM tasks
            WHERE status = 'failed'
              AND updated_at >= ?
            """,
            (now - 900,),
        ).fetchone()
        recent_failed = int(row[0]) if row else 0
        if recent_failed >= 3:
            rq = ResearchQueue(conn)
            node = rq.enqueue_or_append_pending(
                title="Investigate recent task failure spike",
                description=(
                    f"Detected {recent_failed} failed tasks in the last 15 minutes. "
                    "Review logs, endocrine levels, and provider health."
                ),
                priority=-5,
                source_task_id=task.task_id,
                observation=f"recent_failed_tasks={recent_failed}",
            )
            _post_session(
                chat_session_id,
                (
                    "Anomaly detected: failure spike. "
                    f"Updated research item {node.node_id} for triage."
                ),
            )
            _adjust_from_log_level(
                "ERROR",
                "tasks",
                f"Detected {recent_failed} failed tasks in 15m",
            )

        # Provider availability monitoring (limits/health guardrail).
        try:
            _cfg_path, cfg = _read_providers_config()
            adapter, _model, provider_name, _is_fb = _resolve_chat_adapter(cfg, "chat")
            if adapter is not None:
                status = asyncio.run(adapter.health_check())
                if not status.available:
                    rq = ResearchQueue(conn)
                    node = rq.enqueue_or_append_pending(
                        title="Investigate provider availability degradation",
                        description=(
                            "Primary chat provider health check failed during heartbeat. "
                            f"Provider={provider_name or 'unknown'}."
                        ),
                        priority=-4,
                        source_task_id=task.task_id,
                        observation=(
                            "provider_health_check_failed "
                            f"provider={provider_name or 'unknown'}"
                        ),
                    )
                    _post_session(
                        chat_session_id,
                        (
                            "Provider health anomaly detected. "
                            f"Updated research item {node.node_id}."
                        ),
                    )
                    _adjust_from_log_level(
                        "ERROR",
                        "chat",
                        "Primary chat provider health check failed",
                        cooldown_seconds=600,
                    )
        except Exception:  # noqa: BLE001
            _adjust_from_log_level(
                "WARNING",
                "chat",
                "Primary chat provider health check raised transient exception",
                cooldown_seconds=600,
            )

        # Scan all assigned providers for degradation (expired tokens, missing
        # credentials, disabled).  Any system whose assigned provider is invalid
        # means the system is silently falling back — that is **not** nominal.
        try:
            _cfg_path, cfg = _read_providers_config()
            providers_by_name = {p.name: p for p in cfg.providers}
            degraded_systems: list[str] = []
            for system, assignment in cfg.systems.items():
                if not assignment.provider:
                    continue
                p = providers_by_name.get(assignment.provider)
                if p is None or not p.enabled or not _provider_is_valid(p):
                    degraded_systems.append(f"{system.value}→{assignment.provider}")

            if degraded_systems:
                log.warning(
                    "Provider degradation: %d system(s) using fallback: %s",
                    len(degraded_systems),
                    ", ".join(degraded_systems),
                )
                # Apply cortisol EVERY tick while degradation persists.
                # This counteracts natural decay and keeps the signal honest.
                # Only throttle the doctor session message (once per 5 min).
                cortisol_bump = min(0.25, 0.08 * len(degraded_systems))
                _adjust(
                    "provider_degradation",
                    f"Assigned providers unavailable for: {', '.join(degraded_systems)}",
                    {"cortisol": cortisol_bump, "adrenaline": 0.03},
                )
                _msg_reason = f"Doctor notified: {', '.join(degraded_systems)}"
                if not _has_recent_adjustment(
                    "provider_degradation_msg",
                    _msg_reason,
                    window_seconds=300,
                ):
                    # Write a sentinel row so the throttle works for messaging.
                    conn.execute(
                        "INSERT INTO endocrine_adjustments "
                        "(ts, source, reason, deltas_json, levels_json, doctor_revelation) "
                        "VALUES (?, ?, ?, '{}', '{}', 0)",
                        (now, "provider_degradation_msg", _msg_reason),
                    )
                    conn.commit()
                    _post_session(
                        doctor_session_id,
                        (
                            f"**Provider health scan:** {len(degraded_systems)} system(s) "
                            f"have unavailable assigned providers and are using fallback: "
                            f"{', '.join(degraded_systems)}. "
                            "Cortisol increased to reflect non-nominal state. "
                            "Re-authenticate or reassign providers to resolve."
                        ),
                    )

            # Check copilot token approaching expiry — warn before it dies.
            copilot_provider = providers_by_name.get("github-copilot")
            if copilot_provider and copilot_provider.enabled:
                try:
                    from openbad.cognitive.providers.github_copilot import GitHubCopilotProvider
                    cp = GitHubCopilotProvider()
                    ttl = cp.token_ttl_seconds()
                    if ttl is not None and ttl < 3600 and ttl > 0:
                        warning_reason = f"Copilot token expiring in {int(ttl / 60)}m"
                        if not _has_recent_adjustment(
                            "provider_token_expiry", warning_reason, window_seconds=1800,
                        ):
                            _adjust(
                                "provider_token_expiry",
                                warning_reason,
                                {"cortisol": 0.10, "adrenaline": 0.08},
                            )
                            _post_session(
                                doctor_session_id,
                                (
                                    f"**Copilot token expiry warning:** token expires in "
                                    f"~{int(ttl / 60)} minutes. Re-authenticate via the "
                                    "Providers page device-code flow to avoid service "
                                    "degradation."
                                ),
                            )
                            _post_session(
                                chat_session_id,
                                (
                                    f"⚠️ Copilot token expires in ~{int(ttl / 60)} minutes. "
                                    "Visit the **Providers** page to re-authenticate."
                                ),
                            )
                except Exception:  # noqa: BLE001
                    log.exception("Copilot token TTL check failed")
                    _adjust(
                        "provider_error",
                        "Copilot token TTL check raised an exception",
                        {"cortisol": 0.04, "adrenaline": 0.02},
                    )

        except Exception:  # noqa: BLE001
            log.exception("Provider degradation scan failed")
            _adjust(
                "provider_error",
                "Provider degradation scan raised an exception",
                {"cortisol": 0.08, "adrenaline": 0.04},
            )

    def _recent_adjustment_reasons_by_hormone(
        *,
        window_seconds: int = 3600,
        max_reasons: int = 3,
    ) -> dict[str, list[str]]:
        """Return recent non-zero endocrine adjustment reasons grouped by hormone."""
        rows = conn.execute(
            """
            SELECT ts, source, reason, deltas_json
            FROM endocrine_adjustments
            WHERE ts >= ?
            ORDER BY ts DESC
            LIMIT 400
            """,
            (now - max(1, int(window_seconds)),),
        ).fetchall()
        reasons: dict[str, list[str]] = {
            "dopamine": [],
            "adrenaline": [],
            "cortisol": [],
            "endorphin": [],
        }
        seen: dict[str, set[str]] = {h: set() for h in reasons}
        for row in rows:
            try:
                deltas = json.loads(str(row["deltas_json"]))
            except ValueError:
                continue
            if not isinstance(deltas, dict):
                continue
            source = str(row["source"])
            reason = str(row["reason"])
            for hormone in reasons:
                delta = float(deltas.get(hormone, 0.0))
                if abs(delta) < 1e-12:
                    continue
                summary = f"{source}: {reason} (delta={delta:+.3f})"
                if summary in seen[hormone]:
                    continue
                reasons[hormone].append(summary)
                seen[hormone].add(summary)
                if len(reasons[hormone]) >= max_reasons:
                    continue
        return reasons

    _process_endocrine_followups()

    tasks_policy = session_allows(policy, "tasks", "allow_task_autonomy", True)
    tasks_gate = endocrine_runtime.gate("tasks")
    if tasks_policy and tasks_gate.enabled:
        top_task = _top_pending_task()
        if top_task is not None:
            log.info("Processing task: %s (id=%s)", top_task.title, top_task.task_id)
            _process_task(top_task)
        else:
            log.info("Task autonomy enabled but no eligible pending tasks found.")
    else:
        log.info(
            "Task processing skipped: policy=%s gate_enabled=%s gate_reason=%s",
            tasks_policy,
            tasks_gate.enabled,
            getattr(tasks_gate, "disabled_reason", None),
        )

    research_policy = session_allows(policy, "research", "allow_research_autonomy", True)
    research_gate = endocrine_runtime.gate("research")
    if research_policy and research_gate.enabled:
        _process_research()
    else:
        log.info(
            "Research processing skipped: policy=%s gate_enabled=%s gate_reason=%s",
            research_policy,
            research_gate.enabled,
            getattr(research_gate, "disabled_reason", None),
        )

    _monitor_nominal_state()

    doctor_policy = session_allows(policy, "doctor", "allow_endocrine_doctor", True)
    doctor_activated = endocrine_runtime.has_any_activation()
    if doctor_policy and doctor_activated:
        levels = endocrine_runtime.levels
        log.info("Doctor loop activated: levels=%s", levels)
        source_recent = endocrine_runtime.source_contributions(window_seconds=900, now=now)
        severity = endocrine_runtime.current_severity()
        hormone_names = ("dopamine", "adrenaline", "cortisol", "endorphin")
        activation_thresholds = {
            h: getattr(endocrine_config, h).activation_threshold for h in hormone_names
        }
        escalation_thresholds = {
            h: getattr(endocrine_config, h).escalation_threshold for h in hormone_names
        }
        doctor_prompt = (
            "You are the OpenBaD endocrine doctor. "
            "Evaluate global hormone levels and recent source contributions. "
            "Return strict JSON with keys: summary (string), mood_tags (array of strings), "
            "actions (array). Each action must be one of: "
            "{\"type\":\"disable_system\",\"system\":\"chat|tasks|research\","
            "\"duration_minutes\":int,\"reason\":string}, "
            "{\"type\":\"enable_system\",\"system\":\"chat|tasks|research\","
            "\"reason\":string}, "
            "{\"type\":\"adjust\",\"source\":string,\"reason\":string,"
            "\"deltas\":{dopamine,adrenaline,cortisol,endorphin}}. "
            "Use disable/enable actions only if justified by thresholds. "
            "Available remediations: disable chat, disable research autonomy, "
            "schedule re-enable task, "
            "reduce pressure via calming adjustments, or restore systems after recovery.\n\n"
            f"Current levels: {json.dumps(levels, sort_keys=True)}\n"
            f"Severity: {json.dumps(severity, sort_keys=True)}\n"
            f"Activation thresholds: {json.dumps(activation_thresholds, sort_keys=True)}\n"
            f"Escalation thresholds: {json.dumps(escalation_thresholds, sort_keys=True)}\n"
            f"Recent source contributions (15m): {json.dumps(source_recent, sort_keys=True)}"
        )

        llm = _run_llm(doctor_prompt, system_name="doctor")
        parsed: dict[str, object] | None = None
        if llm is not None:
            summary, provider_name, model_id = llm
            parsed = _parse_doctor_payload(summary)
            endocrine_runtime.add_doctor_note(
                {
                    "source": "llm",
                    "provider": provider_name,
                    "model": model_id,
                    "summary": (
                        str(parsed.get("summary", "")).strip()
                        if isinstance(parsed, dict)
                        else ""
                    ),
                    "raw": summary,
                },
                now=now,
            )

        if parsed is None and levels.get("cortisol", 0.0) >= 0.8:
            parsed = {
                "summary": (
                    "Fallback doctor: high cortisol, temporarily disable chat "
                    "and schedule recovery."
                ),
                "mood_tags": ["overloaded", "protective"],
                "actions": [
                    {
                        "type": "disable_system",
                        "system": "chat",
                        "duration_minutes": 30,
                        "reason": "High cortisol fallback guardrail",
                    }
                ],
            }

        if isinstance(parsed, dict):
            mood_tags = parsed.get("mood_tags", [])
            if isinstance(mood_tags, list):
                endocrine_runtime.set_mood_tags([str(tag) for tag in mood_tags], now=now)

            summary_text = str(parsed.get("summary", "")).strip()
            if summary_text:
                _doctor_meta: dict[str, object] = {
                    "doctor_revelation": True,
                    "health_decision": True,
                }
                if llm is not None:
                    _doctor_meta["provider"] = provider_name
                    _doctor_meta["model"] = model_id
                _post_session(
                    doctor_session_id,
                    f"Doctor summary: {summary_text}",
                    extra_metadata=_doctor_meta,
                )

            # Detect active provider degradation — doctor must not mask
            # cortisol/adrenaline signals while the root cause persists.
            _has_active_degradation = False
            try:
                _cfg_path2, _cfg2 = _read_providers_config()
                _pbn2 = {p.name: p for p in _cfg2.providers}
                for _sys2, _asgn2 in _cfg2.systems.items():
                    if not _asgn2.provider:
                        continue
                    _p2 = _pbn2.get(_asgn2.provider)
                    if _p2 is None or not _p2.enabled or not _provider_is_valid(_p2):
                        _has_active_degradation = True
                        break
            except Exception:  # noqa: BLE001
                pass

            actions = parsed.get("actions", [])
            if isinstance(actions, list):
                for action in actions:
                    if not isinstance(action, dict):
                        continue
                    action_type = str(action.get("type", "")).strip().lower()
                    system_name = str(action.get("system", "")).strip().lower()
                    reason = str(action.get("reason", "doctor recommendation")).strip()

                    if action_type == "adjust":
                        source_name = str(action.get("source", "immune")).strip() or "immune"
                        deltas = action.get("deltas", {})
                        if isinstance(deltas, dict):
                            safe_deltas: dict[str, float] = {}
                            for k, v in deltas.items():
                                if k not in {"dopamine", "adrenaline", "cortisol", "endorphin"}:
                                    continue
                                fv = float(v)
                                # Guard: when providers are degraded, do NOT let
                                # the doctor reduce stress hormones — the signal
                                # must persist until the root cause is resolved.
                                if _has_active_degradation and k in ("cortisol", "adrenaline") and fv < 0:
                                    log.warning(
                                        "Doctor tried to reduce %s by %.3f during active "
                                        "provider degradation — clamped to 0",
                                        k, fv,
                                    )
                                    continue
                                safe_deltas[k] = fv
                            _adjust(
                                source_name,
                                reason,
                                safe_deltas,
                                doctor_revelation=True,
                            )
                        continue

                    if system_name not in {"chat", "tasks", "research"}:
                        continue

                    if action_type == "disable_system":
                        duration_min = max(5, int(action.get("duration_minutes", 30)))
                        disabled_until = now + (duration_min * 60)
                        endocrine_runtime.disable_system(
                            system_name,
                            reason=reason,
                            now=now,
                            until=disabled_until,
                        )
                        _create_reenable_task(system_name, reason, disabled_until)
                        _post_session(
                            chat_session_id,
                            (
                                f"Doctor action: disabled {system_name} "
                                f"for {duration_min} minutes. "
                                f"Reason: {reason}"
                            ),
                        )
                        _post_session(
                            doctor_session_id,
                            (
                                f"Action executed: disable {system_name} "
                                f"until {disabled_until:.0f} ({reason})"
                            ),
                            extra_metadata={
                                "doctor_revelation": True,
                                "health_decision": True,
                            },
                        )

                    if action_type == "enable_system":
                        endocrine_runtime.enable_system(system_name, reason=reason, now=now)
                        _post_session(
                            chat_session_id,
                            f"Doctor action: enabled {system_name}. Reason: {reason}",
                        )
                        _post_session(
                            doctor_session_id,
                            f"Action executed: enable {system_name} ({reason})",
                            extra_metadata={
                                "doctor_revelation": True,
                                "health_decision": True,
                            },
                        )
    else:
        log.info(
            "Doctor loop skipped: policy=%s has_activation=%s levels=%s",
            doctor_policy,
            doctor_activated,
            endocrine_runtime.levels,
        )

    # -- publish MQTT tick ----------------------------------------------
    from openbad.nervous_system import topics
    from openbad.nervous_system.client import NervousSystemClient

    tick_payload = json.dumps({
        "ts": now,
        "interval_seconds": interval,
        "dispatched_count": len(dispatched),
        "dispatched_task_ids": dispatched,
        "executed_task_id": executed_task_id,
        "executed_research_id": executed_research_id,
        "silent_skip_count": 0,
    }).encode()

    try:
        client = NervousSystemClient.get_instance(host=mqtt_host, port=mqtt_port)
        client.connect(timeout=3.0)
        levels = endocrine_runtime.levels
        severity = endocrine_runtime.current_severity()
        top_sources = endocrine_runtime.source_contributions(window_seconds=900, now=now)
        recent_reasons = _recent_adjustment_reasons_by_hormone()
        source_note = ""
        if top_sources:
            source_note = ", ".join(sorted(top_sources.keys())[:3])

        publish_count = 0
        skip_count = 0
        for hormone in ("dopamine", "adrenaline", "cortisol", "endorphin"):
            if not _should_publish_endocrine_event(levels_at_tick_start, levels, hormone):
                skip_count += 1
                level_now = float(levels.get(hormone, 0.0))
                threshold = float(getattr(endocrine_config, hormone).activation_threshold)
                if level_now >= threshold:
                    reasons = recent_reasons.get(hormone, [])
                    reason_note = "; ".join(reasons) if reasons else "no recent adjustment record"
                    log.info(
                        "Suppressed endocrine MQTT no-op for %s (level unchanged at %.3f, "
                        "activation_threshold=%.3f). Recent reasons: %s",
                        hormone,
                        level_now,
                        threshold,
                        reason_note,
                    )
                continue

            hormone_severity = int(severity.get(hormone, 1))
            before_level = float(levels_at_tick_start.get(hormone, 0.0))
            after_level = float(levels.get(hormone, 0.0))
            delta = after_level - before_level
            recommended_action = ""
            reasons = recent_reasons.get(hormone, [])
            if hormone_severity >= 2:
                reason_note = "; ".join(reasons) if reasons else "no recent adjustment reason recorded"
                if source_note:
                    recommended_action = (
                        f"Review endocrine doctor decisions; top sources={source_note}; "
                        f"recent_reasons={reason_note}"
                    )
                else:
                    recommended_action = (
                        "Review endocrine doctor decisions; "
                        f"recent_reasons={reason_note}"
                    )

            metric_name = "delta_since_tick_start"
            if delta > 0:
                metric_name = "increase_since_tick_start"
            elif delta < 0:
                metric_name = "decrease_since_tick_start"

            msg = EndocrineEvent(
                header=Header(timestamp_unix=now, source_module="openbad.heartbeat"),
                hormone=hormone,
                level=after_level,
                severity=hormone_severity,
                metric_name=metric_name,
                metric_value=delta,
                recommended_action=recommended_action,
            )
            client.publish(f"agent/endocrine/{hormone}", msg)
            publish_count += 1
            log.info(
                "Published endocrine MQTT event: hormone=%s level_before=%.3f level_after=%.3f "
                "delta=%+.3f severity=%d metric=%s reasons=%s",
                hormone,
                before_level,
                after_level,
                delta,
                hormone_severity,
                metric_name,
                reasons[:3],
            )

        log.info(
            "Endocrine MQTT publish summary: published=%d skipped_noop=%d levels_start=%s "
            "levels_end=%s",
            publish_count,
            skip_count,
            levels_at_tick_start,
            levels,
        )
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
