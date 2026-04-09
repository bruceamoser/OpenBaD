"""Cgroup v2 hierarchy management for the OpenBaD agent process.

Creates and configures a dedicated cgroup at ``/sys/fs/cgroup/openbad/``
with CPU, memory, and I/O controllers enabled, providing isolated
resource measurement for the eBPF probe infrastructure.

Cgroup path convention::

    /sys/fs/cgroup/openbad/          # root cgroup for the agent
    /sys/fs/cgroup/openbad/tools/    # (future) per-tool sub-cgroups

Requires Linux with cgroup v2 (unified hierarchy) and kernel >= 5.8.
"""

from __future__ import annotations

import logging
import os
import platform
from pathlib import Path

logger = logging.getLogger(__name__)

CGROUP_BASE = Path("/sys/fs/cgroup")
CGROUP_NAME = "openbad"
CGROUP_PATH = CGROUP_BASE / CGROUP_NAME

REQUIRED_CONTROLLERS = ("cpu", "memory", "io")


class CgroupError(RuntimeError):
    """Raised when cgroup operations fail."""


def _require_linux() -> None:
    if platform.system() != "Linux":
        msg = "Cgroup v2 management requires Linux"
        raise CgroupError(msg)


def _read_text(path: Path) -> str:
    return path.read_text().strip()


def is_cgroup_v2() -> bool:
    """Return True if the system uses the cgroup v2 unified hierarchy."""
    _require_linux()
    # cgroup v2 unified: /sys/fs/cgroup/cgroup.controllers exists
    controllers_file = CGROUP_BASE / "cgroup.controllers"
    return controllers_file.exists()


def available_controllers() -> list[str]:
    """Return the list of controllers available in the root cgroup."""
    _require_linux()
    controllers_file = CGROUP_BASE / "cgroup.controllers"
    if not controllers_file.exists():
        return []
    return _read_text(controllers_file).split()


def enable_controllers(path: Path, controllers: tuple[str, ...] = REQUIRED_CONTROLLERS) -> None:
    """Enable the given controllers in the cgroup subtree at *path*.

    Writes to ``cgroup.subtree_control`` in the **parent** of *path*
    so that children (including *path*) can use those controllers.
    """
    _require_linux()
    parent = path.parent
    subtree_control = parent / "cgroup.subtree_control"
    if not subtree_control.exists():
        msg = f"subtree_control not found at {subtree_control}"
        raise CgroupError(msg)

    for ctrl in controllers:
        try:
            subtree_control.write_text(f"+{ctrl}\n")
            logger.info("Enabled controller %s in %s", ctrl, parent)
        except OSError as exc:
            msg = f"Failed to enable controller {ctrl} in {parent}: {exc}"
            raise CgroupError(msg) from exc


def create_cgroup(name: str = CGROUP_NAME) -> Path:
    """Create the agent cgroup directory if it doesn't exist.

    Returns the Path to the cgroup directory.
    """
    _require_linux()
    cgroup_dir = CGROUP_BASE / name
    if not cgroup_dir.exists():
        try:
            cgroup_dir.mkdir(parents=False, exist_ok=True)
            logger.info("Created cgroup %s", cgroup_dir)
        except OSError as exc:
            msg = f"Failed to create cgroup {cgroup_dir}: {exc}"
            raise CgroupError(msg) from exc
    else:
        logger.info("Cgroup %s already exists", cgroup_dir)
    return cgroup_dir


def add_pid(pid: int | None = None, cgroup: Path = CGROUP_PATH) -> None:
    """Add a process to the agent cgroup.

    If *pid* is None, adds the current process.
    """
    _require_linux()
    if pid is None:
        pid = os.getpid()
    procs_file = cgroup / "cgroup.procs"
    try:
        procs_file.write_text(f"{pid}\n")
        logger.info("Added PID %d to cgroup %s", pid, cgroup)
    except OSError as exc:
        msg = f"Failed to add PID {pid} to {cgroup}: {exc}"
        raise CgroupError(msg) from exc


def setup_cgroup() -> Path:
    """Full cgroup setup: create hierarchy, enable controllers, add current PID.

    Returns the Path to the agent cgroup directory.
    """
    _require_linux()
    cgroup_dir = create_cgroup()
    enable_controllers(cgroup_dir)
    add_pid(cgroup=cgroup_dir)
    return cgroup_dir
