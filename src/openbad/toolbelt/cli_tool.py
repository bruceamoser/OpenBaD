"""CLI tool adapter — sandboxed subprocess execution.

Registers under ``ToolRole.CLI`` and provides command execution with
allowlist, working-directory restriction, timeout, and optional cgroup
resource limits.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CliToolConfig:
    """Configuration for the CLI tool adapter."""

    allowed_commands: list[str] = field(
        default_factory=lambda: [
            "ls", "cat", "head", "tail",
            "grep", "wc", "find", "echo",
        ],
    )
    working_directory: str = "/var/lib/openbad/sandbox"  # noqa: S108
    timeout: float = 30.0
    cgroup_path: str | None = None
    max_output_bytes: int = 65536


@dataclass
class CommandResult:
    """Structured result from a CLI command execution."""

    command: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class CliToolAdapter:
    """Execute shell commands with sandboxing constraints.

    Parameters
    ----------
    config:
        CLI tool configuration.
    """

    def __init__(self, config: CliToolConfig | None = None) -> None:
        self._config = config or CliToolConfig()
        self._root = Path(self._config.working_directory).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def config(self) -> CliToolConfig:
        return self._config

    def execute(
        self,
        command: str,
        args: list[str] | None = None,
        cwd: str | None = None,
    ) -> CommandResult:
        """Execute a command with sandboxing.

        Parameters
        ----------
        command:
            The base command to run (must be in allowlist).
        args:
            Optional arguments to pass to the command.
        cwd:
            Optional working directory (must be within the sandbox root).

        Returns
        -------
        CommandResult with stdout, stderr, returncode, and timeout flag.
        """
        if command not in self._config.allowed_commands:
            return CommandResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr=f"Command {command!r} not in allowlist",
            )

        work_dir = self._resolve_cwd(cwd)
        if work_dir is None:
            return CommandResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr=f"Working directory escapes sandbox root {self._root}",
            )

        cmd_list = [command] + (args or [])

        try:
            result = subprocess.run(  # noqa: S603
                cmd_list,
                capture_output=True,
                text=True,
                timeout=self._config.timeout,
                cwd=str(work_dir),
                env=self._sandbox_env(),
            )
            return CommandResult(
                command=command,
                returncode=result.returncode,
                stdout=result.stdout[: self._config.max_output_bytes],
                stderr=result.stderr[: self._config.max_output_bytes],
            )
        except subprocess.TimeoutExpired:
            return CommandResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr=f"Command timed out after {self._config.timeout}s",
                timed_out=True,
            )
        except Exception as exc:
            logger.exception("CLI execution failed: %s", command)
            return CommandResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr=str(exc),
            )

    def _resolve_cwd(self, cwd: str | None) -> Path | None:
        """Resolve and validate working directory within sandbox."""
        if cwd is None:
            return self._root
        resolved = (self._root / cwd).resolve()
        try:
            resolved.relative_to(self._root)
        except ValueError:
            return None
        return resolved

    def _sandbox_env(self) -> dict[str, str]:
        """Build a restricted environment for subprocess execution."""
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(self._root),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        return env
