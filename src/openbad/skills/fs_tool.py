"""Trusted in-process file system operations for Phase 10 toolbelt.

:func:`read_file` and :func:`write_file` provide POSIX file I/O with
path-safety enforcement.  Both functions:

* Canonicalize the requested path via :func:`os.path.realpath` before any I/O.
* Reject attempts to escape an allowed root (``ALLOWED_ROOT``, default: the
  system temporary directory plus the current working directory).
* Write atomically — content is written to a sibling ``.tmp`` file then
  renamed, so partial writes never corrupt an existing file.

Security guarantees
-------------------
* Symlink traversal is resolved before validation; following a symlink into a
  restricted area is blocked just like a direct path reference.
* Directory-traversal sequences (``../``) are neutralised by
  :func:`os.path.realpath`.
"""

from __future__ import annotations

import logging
import os
import tempfile
import uuid
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING

import json

from openbad.immune_system.rules_engine import FileOperationRule
from openbad.skills.access_control import effective_allowed_roots

if TYPE_CHECKING:
    from openbad.endocrine.controller import EndocrineController
    from openbad.interoception.disk_network import DiskSnapshot

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Allowed roots
# ---------------------------------------------------------------------------

# Default safe root directories.  Callers may extend this list by appending
# to ALLOWED_ROOTS before invoking the module.
ALLOWED_ROOTS: list[str] = [
    tempfile.gettempdir(),
    str(Path.cwd()),
]

# Module-level rule instance.  Replace with a wired instance (passing a
# NervousSystemClient) to enable IMMUNE_ALERT publishing.
_FILE_OP_RULE: FileOperationRule = FileOperationRule()

# ---------------------------------------------------------------------------
# Endocrine / disk throttling
# ---------------------------------------------------------------------------

#: Operations larger than this many bytes trigger the disk-saturation check.
LARGE_OP_THRESHOLD_BYTES: int = 1024 * 1024  # 1 MiB

#: Disk I/O latency (ms) above which the disk is considered saturated.
DISK_LATENCY_SATURATION_MS: float = 200.0

#: Free-bytes floor below which the disk is considered saturated.
DISK_FREE_BYTES_MIN: int = 50 * 1024 * 1024  # 50 MiB

#: Cortisol level (0–1) at or above which "high cortisol" is asserted.
CORTISOL_HIGH_THRESHOLD: float = 0.5


def should_defer(
    byte_count: int,
    disk_snapshot: DiskSnapshot | None = None,
    endocrine: EndocrineController | None = None,
) -> bool:
    """Return *True* if the operation should be deferred due to resource pressure.

    Parameters
    ----------
    byte_count:
        Approximate size of the pending I/O operation in bytes.
    disk_snapshot:
        Current disk metrics (optional).  If *None*, no disk check is performed.
    endocrine:
        Endocrine controller to read cortisol level (optional).

    Returns
    -------
    bool
        ``True``  — defer the operation; the caller should raise / return DEFERRED.
        ``False`` — proceed normally.
    """
    if byte_count < LARGE_OP_THRESHOLD_BYTES:
        return False
    if disk_snapshot is None or endocrine is None:
        return False
    saturated = (
        disk_snapshot.io_latency_ms >= DISK_LATENCY_SATURATION_MS
        or disk_snapshot.free_bytes < DISK_FREE_BYTES_MIN
    )
    if not saturated:
        return False
    high_cortisol = endocrine.level("cortisol") >= CORTISOL_HIGH_THRESHOLD
    return high_cortisol


class ResourceDeferredError(OSError):
    """Raised when an FS operation is deferred due to disk saturation + high cortisol.

    Callers should set the associated task node to ``NodeStatus.DEFERRED_RESOURCES``
    and reschedule the operation.
    """


def _is_safe_path(resolved: str) -> bool:
    """Return True if *resolved* (an absolute, real path) is under an allowed root."""
    for root in effective_allowed_roots(ALLOWED_ROOTS):
        real_root = os.path.realpath(root)
        if resolved.startswith(real_root + os.sep) or resolved == real_root:
            return True
    return False


def _validate(path: str) -> str:
    """Resolve and validate *path*.

    Returns the resolved absolute path string.

    Raises
    ------
    PermissionError
        If the resolved path is outside all allowed roots.
    """
    resolved = os.path.realpath(os.path.abspath(path))
    if not _is_safe_path(resolved):
        raise PermissionError(
            f"Path {path!r} resolves to {resolved!r} which is outside allowed roots."
        )
    return resolved


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def read_file(
    path: str,
    *,
    encoding: str = "utf-8",
    disk_snapshot: DiskSnapshot | None = None,
    endocrine: EndocrineController | None = None,
) -> str:
    """Read and return the contents of *path* as a string.

    Parameters
    ----------
    path:
        The file path to read.  May be relative.
    encoding:
        Text encoding (default ``utf-8``).
    disk_snapshot:
        Optional current disk snapshot for endocrine throttling.
    endocrine:
        Optional endocrine controller for cortisol check.

    Returns
    -------
    str
        The file contents.

    Raises
    ------
    PermissionError
        If the path is outside allowed roots.
    FileNotFoundError
        If the file does not exist.
    IsADirectoryError
        If the path points to a directory.
    ResourceDeferredError
        If disk is saturated and cortisol is high; the operation should be retried later.
    """
    resolved = _validate(path)
    byte_estimate = Path(resolved).stat().st_size if Path(resolved).exists() else 0
    if should_defer(byte_estimate, disk_snapshot=disk_snapshot, endocrine=endocrine):
        log.info("fs_tool.read_file: deferring large read due to resource saturation")
        raise ResourceDeferredError(
            f"read_file deferred: disk saturated (path={resolved!r})"
        )
    return Path(resolved).read_text(encoding=encoding)


def find_files(
    pattern: str,
    *,
    cwd: str = "/",
    limit: int = 50,
) -> str:
    """Find files under *cwd* matching *pattern* and return JSON paths.

    Unlike read_file/write_file, find_files does NOT require the cwd to be
    inside allowed roots.  Searching for file names is a read-only metadata
    operation; the permission gate is on read_file/write_file.

    Parameters
    ----------
    pattern:
        Glob-like pattern or plain substring to search for.
    cwd:
        Root directory to search within.  Defaults to "/" so the entire
        filesystem is searched when no directory is specified.
    limit:
        Maximum number of matches to return.
    """
    resolved_root = os.path.realpath(os.path.abspath(cwd))
    root = Path(resolved_root)
    needle = pattern.strip()
    max_results = max(1, min(int(limit), 200))

    results: list[str] = []
    if any(char in needle for char in "*?[]"):
        glob_pattern = needle if "/" in needle or needle.startswith("**") else f"**/{needle}"
        iterator = root.glob(glob_pattern)
        for candidate in iterator:
            if not candidate.is_file():
                continue
            results.append(str(candidate.resolve(strict=False)))
            if len(results) >= max_results:
                break
    else:
        lowered = needle.lower()
        for candidate in root.rglob("*"):
            if not candidate.is_file():
                continue
            resolved_candidate = str(candidate.resolve(strict=False))
            if lowered and lowered not in candidate.name.lower() and lowered not in resolved_candidate.lower():
                continue
            results.append(resolved_candidate)
            if len(results) >= max_results:
                break

    results = sorted(dict.fromkeys(results))
    return json.dumps(results, indent=2)


def write_file(
    path: str,
    content: str,
    *,
    encoding: str = "utf-8",
    disk_snapshot: DiskSnapshot | None = None,
    endocrine: EndocrineController | None = None,
) -> None:
    """Write *content* to *path* atomically.

    The write is performed to a sibling temporary file and then renamed into
    place, ensuring the target file is never partially written.

    Parameters
    ----------
    path:
        Destination file path.  May be relative.  Parent directory must exist.
    content:
        Text to write.
    encoding:
        Text encoding (default ``utf-8``).
    disk_snapshot:
        Optional current disk snapshot for endocrine throttling.
    endocrine:
        Optional endocrine controller for cortisol check.

    Raises
    ------
    PermissionError
        If the path is outside allowed roots or is a restricted system path.
    FileNotFoundError
        If the parent directory does not exist.
    ResourceDeferredError
        If disk is saturated and cortisol is high; the operation should be retried later.
    """
    byte_count = len(content.encode(encoding))
    if should_defer(byte_count, disk_snapshot=disk_snapshot, endocrine=endocrine):
        log.info("fs_tool.write_file: deferring large write due to resource saturation")
        raise ResourceDeferredError(
            f"write_file deferred: disk saturated (path={path!r})"
        )
    resolved = _validate(path)
    # Immune gate: block writes to restricted system paths before any I/O.
    _FILE_OP_RULE.check_write(resolved)
    parent = Path(resolved).parent
    if not parent.exists():
        raise FileNotFoundError(f"Parent directory does not exist: {parent}")

    tmp_path = parent / f".{uuid.uuid4().hex}.tmp"
    try:
        tmp_path.write_text(content, encoding=encoding)
        tmp_path.rename(resolved)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
