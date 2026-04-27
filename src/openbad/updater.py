"""Fast update logic for OpenBaD.

Provides three update strategies:

1. **Quick (default)**: ``git pull`` + ``pip install --no-deps`` + copy
   configs/units + restart services.  Takes ~5 seconds.
2. **Deps**: Download the pre-built wheels tarball from the matching
   GitHub release and install from local files — no PyPI resolution.
3. **Full**: Run the original ``scripts/install.sh`` for first-time
   bootstrap or major upgrades.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request

log = logging.getLogger(__name__)

GITHUB_REPO = "bruceamoser/OpenBaD"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}"

VENV_DIR = Path("/opt/openbad/venv")
VENV_PIP = VENV_DIR / "bin" / "pip"
VENV_PYTHON = VENV_DIR / "bin" / "python"

CONFIG_DIR = Path("/etc/openbad")
SYSTEMD_DIR = Path("/etc/systemd/system")

# Service files to sync from config/ → /etc/systemd/system/
_UNIT_FILES = (
    "openbad.service",
    "openbad-wui.service",
    "openbad-broker.service",
    "openbad-heartbeat.service",
    "openbad-heartbeat.timer",
    "openbad-heartbeat-apply.service",
    "openbad-heartbeat-watch.path",
    "openbad-telemetry-apply.service",
    "openbad-telemetry-watch.path",
)

# Config files to sync (only copies new files; never overwrites existing)
_CONFIG_EXTS = (".yaml", ".conf")


def git_pull(project_root: Path) -> str:
    """Run ``git pull --ff-only`` and return status message."""
    git_bin = shutil.which("git")
    if not git_bin:
        return "git not found, skipping pull."

    proc = subprocess.run(  # noqa: S603
        [git_bin, "-C", str(project_root), "pull", "--ff-only"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        return proc.stdout.strip() or "Already up to date."
    return f"git pull skipped: {proc.stderr.strip()}"


def pip_install_no_deps(project_root: Path) -> None:
    """Install the openbad package without resolving dependencies.

    Uses ``--no-deps --no-build-isolation`` for maximum speed (~2s).
    Falls back to editable install when the venv pip is available.
    """
    pip = str(VENV_PIP) if VENV_PIP.exists() else "pip"
    cmd = [
        pip, "install",
        "--no-deps",
        "--no-build-isolation",
        "--force-reinstall",
        "-q",
        str(project_root),
    ]
    subprocess.run(cmd, check=True, cwd=str(project_root))  # noqa: S603


def sync_configs(project_root: Path) -> list[str]:
    """Copy new config files from the repo to /etc/openbad.

    Returns list of newly copied file names.  Existing files are never
    overwritten (operator may have customised them).
    """
    config_src = project_root / "config"
    copied: list[str] = []
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    for src in sorted(config_src.iterdir()):
        if src.suffix not in _CONFIG_EXTS:
            continue
        dst = CONFIG_DIR / src.name
        if dst.exists():
            continue
        shutil.copy2(src, dst)
        copied.append(src.name)
    return copied


def sync_units(project_root: Path) -> list[str]:
    """Copy systemd unit files from config/ to /etc/systemd/system/.

    Always overwrites — unit files are not operator-customised.
    Returns list of updated unit names.
    """
    config_src = project_root / "config"
    updated: list[str] = []
    for name in _UNIT_FILES:
        src = config_src / name
        if not src.exists():
            continue
        dst = SYSTEMD_DIR / name
        # Skip if unchanged
        if dst.exists() and dst.read_bytes() == src.read_bytes():
            continue
        shutil.copy2(src, dst)
        updated.append(name)
    return updated


def sync_helper_scripts(project_root: Path) -> list[str]:
    """Install privileged helper scripts to /usr/local/bin/."""
    installed: list[str] = []
    scripts_dir = project_root / "scripts"
    for name in ("openbad-apply-heartbeat-interval", "openbad-apply-telemetry-interval"):
        src = scripts_dir / name
        if not src.exists():
            continue
        dst = Path("/usr/local/bin") / name
        shutil.copy2(src, dst)
        dst.chmod(0o755)
        installed.append(name)
    return installed


def systemd_reload() -> None:
    """Run ``systemctl daemon-reload``."""
    systemctl = shutil.which("systemctl")
    if systemctl:
        subprocess.run([systemctl, "daemon-reload"], check=False)  # noqa: S603


def restart_services() -> None:
    """Restart the core OpenBaD services."""
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return
    for unit in ("openbad.service", "openbad-wui.service"):
        subprocess.run(  # noqa: S603
            [systemctl, "restart", unit],
            check=False,
        )


# ------------------------------------------------------------------
# GitHub release helpers
# ------------------------------------------------------------------


def get_latest_release_tag() -> str | None:
    """Query GitHub API for the latest release tag name."""
    url = f"{GITHUB_API}/releases/latest"
    req = urllib_request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
    try:
        with urllib_request.urlopen(req, timeout=10) as resp:  # noqa: S310
            data = json.loads(resp.read())
            return data.get("tag_name")
    except (urllib_error.URLError, json.JSONDecodeError, OSError) as exc:
        log.debug("Failed to query GitHub releases: %s", exc)
        return None


def _find_wheels_asset(release_data: dict) -> dict | None:
    """Find the wheels tarball asset in a release."""
    for asset in release_data.get("assets", []):
        name = asset.get("name", "")
        if name.startswith("openbad-") and name.endswith("-wheels.tar.gz"):
            return asset
    return None


def download_and_install_deps(tag: str | None = None) -> bool:
    """Download the wheels tarball from a GitHub release and install.

    If *tag* is ``None``, uses the latest release.
    Returns ``True`` on success, ``False`` if no tarball is available.
    """
    url = f"{GITHUB_API}/releases/tags/{tag}" if tag else f"{GITHUB_API}/releases/latest"

    req = urllib_request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
    try:
        with urllib_request.urlopen(req, timeout=15) as resp:  # noqa: S310
            release = json.loads(resp.read())
    except (urllib_error.URLError, json.JSONDecodeError, OSError) as exc:
        log.warning("Cannot reach GitHub releases: %s", exc)
        return False

    asset = _find_wheels_asset(release)
    if not asset:
        log.warning("No wheels tarball found in release %s", release.get("tag_name"))
        return False

    download_url = asset["browser_download_url"]
    with tempfile.TemporaryDirectory() as tmpdir:
        tarball = Path(tmpdir) / "wheels.tar.gz"
        # Download
        req = urllib_request.Request(download_url)  # noqa: S310
        with urllib_request.urlopen(req, timeout=120) as resp, tarball.open("wb") as f:  # noqa: S310
            shutil.copyfileobj(resp, f)

        # Extract
        wheels_dir = Path(tmpdir) / "wheels"
        with tarfile.open(tarball, "r:gz") as tf:
            # Security: validate all members before extracting
            for member in tf.getmembers():
                if member.name.startswith("/") or ".." in member.name:
                    raise ValueError(f"Unsafe path in tarball: {member.name}")
            tf.extractall(wheels_dir)  # noqa: S202

        # Install from local wheels — no network
        pip = str(VENV_PIP) if VENV_PIP.exists() else "pip"
        whl_files = list(wheels_dir.rglob("*.whl"))
        if not whl_files:
            log.warning("No .whl files found in tarball")
            return False

        cmd = [
            pip, "install",
            "--no-index",
            "--find-links", str(wheels_dir),
            "--force-reinstall",
            "-q",
            "openbad",
        ]
        subprocess.run(cmd, check=True)  # noqa: S603

    return True


# ------------------------------------------------------------------
# High-level update orchestrators
# ------------------------------------------------------------------


def quick_update(project_root: Path, *, skip_services: bool = False) -> None:
    """Fast update: pull + no-deps install + sync configs + restart.

    This is the default path — takes ~5 seconds.
    """
    import click

    click.echo("Pulling latest changes...")
    click.echo(git_pull(project_root))

    click.echo("Installing package (no dependency resolution)...")
    pip_install_no_deps(project_root)

    click.echo("Syncing configuration files...")
    new_configs = sync_configs(project_root)
    if new_configs:
        click.echo(f"  New configs: {', '.join(new_configs)}")

    if not skip_services:
        click.echo("Syncing systemd units...")
        updated_units = sync_units(project_root)
        sync_helper_scripts(project_root)
        if updated_units:
            click.echo(f"  Updated: {', '.join(updated_units)}")
            systemd_reload()

        click.echo("Restarting services...")
        restart_services()

    click.echo("Update complete.")


def deps_update(project_root: Path, *, skip_services: bool = False) -> None:
    """Update dependencies from the GitHub release wheels tarball."""
    import click

    click.echo("Pulling latest changes...")
    click.echo(git_pull(project_root))

    # Read the current version to find matching release
    import openbad as _openbad

    tag = f"v{_openbad.__version__}"
    click.echo(f"Downloading dependencies for {tag} from GitHub...")

    if download_and_install_deps(tag):
        click.echo("Dependencies installed from release.")
    else:
        click.echo("No pre-built wheels found for this version.")
        click.echo("Falling back to pip install (this may be slow)...")
        pip = str(VENV_PIP) if VENV_PIP.exists() else "pip"
        subprocess.run(  # noqa: S603
            [pip, "install", "-q", str(project_root)],
            check=True,
            cwd=str(project_root),
        )

    click.echo("Syncing configuration files...")
    sync_configs(project_root)

    if not skip_services:
        click.echo("Syncing systemd units...")
        updated_units = sync_units(project_root)
        sync_helper_scripts(project_root)
        if updated_units:
            systemd_reload()
        click.echo("Restarting services...")
        restart_services()

    click.echo("Update complete.")


def full_update(project_root: Path, *, skip_services: bool = False) -> None:
    """Run the full install.sh script (first-time bootstrap path)."""
    import click

    click.echo("Pulling latest changes...")
    click.echo(git_pull(project_root))

    install_script = project_root / "scripts" / "install.sh"
    click.echo("Running full install script...")
    cmd: list[str] = [str(install_script)]
    if skip_services:
        cmd.append("--skip-services")
    subprocess.run(cmd, check=True, cwd=str(project_root))  # noqa: S603
    click.echo("Full update complete.")
