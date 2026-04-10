"""OpenBaD command-line interface.

Entrypoint: ``openbad`` (see ``[project.scripts]`` in ``pyproject.toml``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys

import click

import openbad


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx: click.Context) -> None:
    """OpenBaD — Biological as Digital agent daemon."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@main.command()
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
@click.option("--host", default="localhost", help="MQTT broker host.")
@click.option("--port", default=1883, type=int, help="MQTT broker port.")
def status(host: str, port: int) -> None:
    """Check daemon status (MQTT reachability and FSM state)."""
    from openbad.nervous_system.client import NervousSystemClient

    result: dict[str, object] = {"mqtt_reachable": False, "fsm_state": "UNKNOWN"}
    try:
        client = NervousSystemClient(host=host, port=port)
        client.connect(timeout=3.0)
        result["mqtt_reachable"] = True
        client.disconnect()
    except ConnectionError:
        pass
    click.echo(json.dumps(result, indent=2))
    sys.exit(0 if result["mqtt_reachable"] else 1)


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
def tui() -> None:
    """Launch the terminal UI."""
    click.echo("TUI not yet implemented. See issue #180.")


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
