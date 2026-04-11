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

import os
import tempfile
import uuid
from pathlib import Path

from openbad.immune_system.rules_engine import FileOperationRule

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


def _is_safe_path(resolved: str) -> bool:
    """Return True if *resolved* (an absolute, real path) is under an allowed root."""
    for root in ALLOWED_ROOTS:
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


def read_file(path: str, *, encoding: str = "utf-8") -> str:
    """Read and return the contents of *path* as a string.

    Parameters
    ----------
    path:
        The file path to read.  May be relative.
    encoding:
        Text encoding (default ``utf-8``).

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
    """
    resolved = _validate(path)
    return Path(resolved).read_text(encoding=encoding)


def write_file(path: str, content: str, *, encoding: str = "utf-8") -> None:
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

    Raises
    ------
    PermissionError
        If the path is outside allowed roots.
    FileNotFoundError
        If the parent directory does not exist.
    """
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
