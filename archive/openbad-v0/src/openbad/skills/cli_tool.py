"""CLI tool adapter — sandboxed subprocess execution.

Registers under ``ToolRole.CLI`` and provides command execution with
allowlist, working-directory restriction, timeout, and optional cgroup
resource limits.

Provides both synchronous (:meth:`CliToolAdapter.execute`) and
asynchronous (:meth:`CliToolAdapter.async_execute`) execution paths.
The async variant is a non-blocking coroutine built on
:mod:`asyncio.subprocess`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from openbad.immune_system.rules_engine import DestructiveCommandRule
from openbad.skills.access_control import effective_allowed_roots

logger = logging.getLogger(__name__)

# Module-level quarantine rule.  Replace with a wired instance to enable
# IMMUNE_ALERT + IMMUNE_QUARANTINE publishing.
_DESTRUCTIVE_RULE: DestructiveCommandRule = DestructiveCommandRule()


def _default_allowed_roots() -> list[str]:
    roots: list[str] = []
    configured = os.environ.get("OPENBAD_TOOL_ALLOWED_ROOTS", "").strip()
    if configured:
        roots.extend(part.strip() for part in configured.split(os.pathsep) if part.strip())

    project_root = os.environ.get("OPENBAD_PROJECT_ROOT", "").strip()
    if project_root:
        roots.append(project_root)

    roots.extend([
        str(Path.cwd()),
        tempfile.gettempdir(),
        "/var/lib/openbad/sandbox",
    ])

    seen: set[str] = set()
    normalized: list[str] = []
    for root in roots:
        try:
            resolved = str(Path(root).expanduser().resolve())
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        normalized.append(resolved)
    return effective_allowed_roots(normalized)


def _default_working_directory() -> str:
    return _default_allowed_roots()[0]


@dataclass
class CliToolConfig:
    """Configuration for the CLI tool adapter."""

    allowed_commands: list[str] = field(
        default_factory=lambda: [
            "ls", "cat", "head", "tail",
            "grep", "wc", "find", "echo", "pwd", "sed", "rg",
        ],
    )
    working_directory: str = field(default_factory=_default_working_directory)
    allowed_roots: list[str] | None = None
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
        configured_roots = self._config.allowed_roots
        if configured_roots is None:
            if config is None:
                configured_roots = _default_allowed_roots()
            else:
                configured_roots = [self._config.working_directory]
        self._allowed_roots_display = list(configured_roots)
        self._allowed_roots = [Path(root).expanduser().resolve() for root in configured_roots]
        self._root = self._resolve_cwd(self._config.working_directory)
        if self._root is None:
            raise ValueError("working_directory must resolve inside an allowed root")
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
        command, args = self.parse_command(command, args)
        if not command:
            return CommandResult(
                command="",
                returncode=-1,
                stdout="",
                stderr="Command must not be empty",
            )
        # Immune gate: block destructive commands BEFORE allowlist check.
        try:
            _DESTRUCTIVE_RULE.check(command, args)
        except PermissionError as exc:
            return CommandResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr=str(exc),
            )

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
                stderr=f"Working directory escapes sandbox / allowed roots {self._allowed_roots_display}",
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
        """Resolve and validate working directory within allowed roots."""
        if cwd is None:
            return self._root
        raw = Path(cwd).expanduser()
        resolved = (raw if raw.is_absolute() else (self._root / raw)).resolve()
        for root in self._allowed_roots:
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue
        return None

    @staticmethod
    def parse_command(command: str, args: list[str] | None = None) -> tuple[str, list[str]]:
        """Normalize either a shell-style command string or explicit command + args."""
        normalized_args = list(args or [])
        if normalized_args:
            return command, normalized_args
        parts = shlex.split(command)
        if not parts:
            return "", []
        return parts[0], parts[1:]

    async def async_execute(
        self,
        command: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        """Execute *command* asynchronously with sandboxing.

        Parameters
        ----------
        command:
            The base command to run (must be in allowlist).
        args:
            Optional arguments to pass to the command.
        cwd:
            Optional working directory (must be within the sandbox root).
        timeout:
            Override for this call; defaults to ``config.timeout``.

        Returns
        -------
        CommandResult with stdout, stderr, returncode, and timeout flag.
        """
        command, args = self.parse_command(command, args)
        if not command:
            return CommandResult(
                command="",
                returncode=-1,
                stdout="",
                stderr="Command must not be empty",
            )
        # Immune gate: block destructive commands BEFORE allowlist check.
        try:
            _DESTRUCTIVE_RULE.check(command, args)
        except PermissionError as exc:
            return CommandResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr=str(exc),
            )

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
                stderr=f"Working directory escapes sandbox / allowed roots {self._allowed_roots_display}",
            )

        deadline = timeout if timeout is not None else self._config.timeout
        cmd_list = [command] + (args or [])
        env = self._sandbox_env()

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_list,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(work_dir),
                env=env,
            )
            try:
                raw_out, raw_err = await asyncio.wait_for(
                    proc.communicate(), timeout=deadline
                )
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                return CommandResult(
                    command=command,
                    returncode=-1,
                    stdout="",
                    stderr=f"Command timed out after {deadline}s",
                    timed_out=True,
                )
            return CommandResult(
                command=command,
                returncode=proc.returncode or 0,
                stdout=raw_out.decode(errors="replace")[: self._config.max_output_bytes],
                stderr=raw_err.decode(errors="replace")[: self._config.max_output_bytes],
            )
        except Exception as exc:
            logger.exception("Async CLI execution failed: %s", command)
            return CommandResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr=str(exc),
            )

    def _sandbox_env(self) -> dict[str, str]:
        """Build a restricted environment for subprocess execution."""
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(self._root),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        return env
