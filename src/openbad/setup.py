"""Interactive setup wizard for OpenBaD first-run configuration."""

from __future__ import annotations

import os
import platform
import secrets
import shutil
import sys
from pathlib import Path

import click
import yaml

# Template configs shipped with the package
_PACKAGE_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"

# Config filenames that the wizard copies
CONFIG_FILES = [
    "active_inference.yaml",
    "broker.conf",
    "cognitive.yaml",
    "endocrine.yaml",
    "identity.yaml",
    "immune.yaml",
    "immune_rules.yaml",
    "memory.yaml",
    "model_routing.yaml",
    "permissions.yaml",
    "sensory_audio.yaml",
    "sensory_vision.yaml",
    "threshold_policies.yaml",
]

SYSTEMD_UNITS = [
    "openbad-broker.service",
    "openbad.service",
    "openbad-wui.service",
]

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "openbad"


# ------------------------------------------------------------------
# Environment checks
# ------------------------------------------------------------------


def check_python_version() -> tuple[bool, str]:
    """Verify Python >= 3.11."""
    version = sys.version_info
    ok = version >= (3, 11)
    msg = f"Python {version.major}.{version.minor}.{version.micro}"
    return ok, msg


def check_platform() -> tuple[bool, str]:
    """Check if running on Linux."""
    is_linux = platform.system() == "Linux"
    return is_linux, platform.system()


def check_cgroup_v2() -> tuple[bool, str]:
    """Detect cgroup v2 availability."""
    cgroup_path = Path("/sys/fs/cgroup/cgroup.controllers")
    if cgroup_path.exists():
        return True, "cgroup v2 available"
    return False, "cgroup v2 not detected"


def check_systemd() -> tuple[bool, str]:
    """Check if systemd is the init system."""
    if Path("/run/systemd/system").is_dir():
        return True, "systemd detected"
    return False, "systemd not detected"


def check_mqtt_broker(host: str = "localhost", port: int = 1883) -> tuple[bool, str]:
    """Attempt MQTT connection to detect running broker."""
    try:
        from openbad.nervous_system.client import NervousSystemClient

        client = NervousSystemClient(host=host, port=port)
        client.connect(timeout=3.0)
        client.disconnect()
        return True, f"Broker reachable at {host}:{port}"
    except Exception:  # noqa: BLE001
        return False, f"Broker not reachable at {host}:{port}"


# ------------------------------------------------------------------
# Config management
# ------------------------------------------------------------------


def copy_configs(dest_dir: Path, *, overwrite: bool = False) -> list[str]:
    """Copy template config files to *dest_dir*.

    Returns list of files copied.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in CONFIG_FILES:
        src = _PACKAGE_CONFIG_DIR / name
        dst = dest_dir / name
        if not src.exists():
            continue
        if dst.exists() and not overwrite:
            continue
        shutil.copy2(src, dst)
        copied.append(name)
    return copied


def generate_secret_key() -> str:
    """Generate a cryptographic hex secret for identity module."""
    return secrets.token_hex(32)


def patch_identity_config(config_dir: Path, secret_hex: str) -> None:
    """Update identity.yaml with the given secret key."""
    identity_path = config_dir / "identity.yaml"
    if not identity_path.exists():
        return
    data = yaml.safe_load(identity_path.read_text()) or {}
    identity_block = data.get("identity", {})
    identity_block["secret_hex"] = secret_hex
    data["identity"] = identity_block
    identity_path.write_text(yaml.dump(data, default_flow_style=False))


def install_systemd_units(config_dir: Path) -> list[str]:
    """Copy systemd unit files to /etc/systemd/system/.

    Returns list of installed units. Requires root.
    """
    systemd_dir = Path("/etc/systemd/system")
    installed: list[str] = []
    for name in SYSTEMD_UNITS:
        src = _PACKAGE_CONFIG_DIR / name
        if not src.exists():
            continue
        dst = systemd_dir / name
        try:
            shutil.copy2(src, dst)
            installed.append(name)
        except PermissionError:
            click.echo(f"  Permission denied: {dst} (run as root)")
    return installed


# ------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------


def validate_config(config_dir: Path) -> list[str]:
    """Check that all expected config files exist. Return missing names."""
    missing = []
    for name in CONFIG_FILES:
        if not (config_dir / name).exists():
            missing.append(name)
    return missing


# ------------------------------------------------------------------
# Wizard steps
# ------------------------------------------------------------------


def run_wizard(
    config_dir: Path = DEFAULT_CONFIG_DIR,
    mqtt_host: str = "localhost",
    mqtt_port: int = 1883,
    *,
    non_interactive: bool = False,
    check_only: bool = False,
) -> bool:
    """Run the full setup wizard. Returns True on success."""
    click.echo("=" * 50)
    click.echo("  OpenBaD Setup Wizard")
    click.echo("=" * 50)
    click.echo()

    # Step 1: Environment checks
    click.echo("[1/6] Environment check")
    checks = [
        ("Python version", check_python_version()),
        ("Platform", check_platform()),
        ("cgroup v2", check_cgroup_v2()),
        ("systemd", check_systemd()),
    ]
    all_ok = True
    for label, (ok, detail) in checks:
        icon = "OK" if ok else "WARN"
        click.echo(f"  [{icon}] {label}: {detail}")
        if not ok and label == "Python version":
            all_ok = False

    if not all_ok:
        click.echo("\nCritical requirement not met. Aborting.")
        return False

    # Step 2: MQTT broker
    click.echo(f"\n[2/6] MQTT broker ({mqtt_host}:{mqtt_port})")
    broker_ok, broker_msg = check_mqtt_broker(mqtt_host, mqtt_port)
    icon = "OK" if broker_ok else "WARN"
    click.echo(f"  [{icon}] {broker_msg}")
    if not broker_ok and not check_only:
        click.echo("  Hint: Install NanoMQ or start the broker first.")

    # Step 3: Config directory
    click.echo(f"\n[3/6] Config directory: {config_dir}")
    if check_only:
        missing = validate_config(config_dir)
        if missing:
            click.echo(f"  [WARN] Missing: {', '.join(missing)}")
        else:
            click.echo("  [OK] All config files present")
        click.echo()
        return len(missing) == 0 and all_ok

    if not non_interactive:
        response = click.prompt(
            "  Config directory", default=str(config_dir), type=str
        )
        config_dir = Path(response)

    copied = copy_configs(config_dir)
    if copied:
        click.echo(f"  Copied {len(copied)} config files")
    else:
        click.echo("  Config files already exist (no overwrite)")

    # Step 4: Identity setup
    click.echo("\n[4/6] Identity setup")
    identity_path = config_dir / "identity.yaml"
    if identity_path.exists():
        data = yaml.safe_load(identity_path.read_text()) or {}
        current_secret = (data.get("identity") or {}).get("secret_hex", "")
    else:
        current_secret = ""

    if not current_secret:
        secret = generate_secret_key()
        patch_identity_config(config_dir, secret)
        click.echo("  Generated new secret key")
    else:
        click.echo("  Secret key already configured")

    # Step 5: Systemd integration
    click.echo("\n[5/6] Systemd integration")
    if os.geteuid() == 0 if hasattr(os, "geteuid") else False:
        installed = install_systemd_units(config_dir)
        if installed:
            click.echo(f"  Installed: {', '.join(installed)}")
        else:
            click.echo("  Units already installed")
    else:
        click.echo("  [SKIP] Not running as root (run with sudo to install units)")

    # Step 6: Validation
    click.echo("\n[6/6] Validation")
    missing = validate_config(config_dir)
    if missing:
        click.echo(f"  [WARN] Missing configs: {', '.join(missing)}")
        return False

    click.echo("  [OK] All config files present")
    click.echo("\nSetup complete!")
    return True
