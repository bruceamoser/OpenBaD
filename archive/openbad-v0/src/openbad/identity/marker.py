"""HMAC-SHA256 marker generation and validation."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import os
import secrets
from pathlib import Path

import yaml


def generate_secret(length: int = 32) -> bytes:
    """Return a cryptographically random secret key."""
    return secrets.token_bytes(length)


def create_marker(
    data: str,
    secret: bytes,
) -> str:
    """Produce an HMAC-SHA256 hex digest of *data* using *secret*."""
    return hmac.new(secret, data.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_marker(
    data: str,
    marker: str,
    secret: bytes,
) -> bool:
    """Return ``True`` if *marker* is a valid HMAC-SHA256 of *data*."""
    expected = create_marker(data, secret)
    return hmac.compare_digest(expected, marker)


# ------------------------------------------------------------------
# Secret-key persistence
# ------------------------------------------------------------------


def load_secret(
    *,
    yaml_path: str | Path = "config/identity.yaml",
    env_var: str = "OPENBAD_IDENTITY_SECRET",
) -> bytes:
    """Load the identity secret key.

    Resolution order:
    1. Environment variable *env_var* (hex-encoded).
    2. ``secret_hex`` field in *yaml_path*.
    3. Generate a new ephemeral key (dev/test only).
    """
    env_val = os.environ.get(env_var, "")
    if env_val:
        return bytes.fromhex(env_val)

    path = Path(yaml_path)
    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        identity = data.get("identity", {})
        hex_key = identity.get("secret_hex", "")
        if hex_key:
            return bytes.fromhex(hex_key)

    return generate_secret()


def save_marker_file(
    marker: str,
    path: str | Path,
) -> None:
    """Write *marker* to *path* with restricted permissions (0600)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(marker)
    # Set 0600 permissions on Linux/macOS; ignored on Windows
    with contextlib.suppress(OSError):
        p.chmod(0o600)


def read_marker_file(path: str | Path) -> str:
    """Read a marker string from *path*."""
    return Path(path).read_text().strip()
