"""Tests for CLI tool adapter — Issue #236."""

from __future__ import annotations

from pathlib import Path

import pytest

from openbad.proprioception.registry import ToolRegistry, ToolRole
from openbad.toolbelt.cli_tool import CliToolAdapter, CliToolConfig, CommandResult


@pytest.fixture()
def sandbox(tmp_path: Path) -> Path:
    d = tmp_path / "sandbox"
    d.mkdir()
    (d / "hello.txt").write_text("hello world\n")
    return d


@pytest.fixture()
def adapter(sandbox: Path) -> CliToolAdapter:
    config = CliToolConfig(
        working_directory=str(sandbox),
        timeout=5.0,
    )
    return CliToolAdapter(config)


class TestAllowedCommand:
    def test_echo(self, adapter: CliToolAdapter) -> None:
        result = adapter.execute("echo", ["hi"])
        assert result.returncode == 0
        assert "hi" in result.stdout

    def test_cat_file(self, adapter: CliToolAdapter, sandbox: Path) -> None:
        result = adapter.execute("cat", ["hello.txt"])
        assert result.returncode == 0
        assert "hello world" in result.stdout

    def test_ls(self, adapter: CliToolAdapter) -> None:
        result = adapter.execute("ls")
        assert result.returncode == 0
        assert "hello.txt" in result.stdout


class TestBlockedCommand:
    def test_blocked_command(self, adapter: CliToolAdapter) -> None:
        result = adapter.execute("rm", ["-rf", "/"])
        assert result.returncode == -1
        assert "not in allowlist" in result.stderr

    def test_python_blocked(self, adapter: CliToolAdapter) -> None:
        result = adapter.execute("python3", ["-c", "print('pwned')"])
        assert result.returncode == -1
        assert "not in allowlist" in result.stderr


class TestDirectoryEscape:
    def test_parent_traversal_blocked(self, adapter: CliToolAdapter) -> None:
        result = adapter.execute("ls", cwd="../../etc")
        assert result.returncode == -1
        assert "escapes sandbox" in result.stderr

    def test_absolute_escape_blocked(self, adapter: CliToolAdapter, sandbox: Path) -> None:
        result = adapter.execute("ls", cwd="/etc")
        assert result.returncode == -1
        assert "escapes sandbox" in result.stderr

    def test_subdir_allowed(self, adapter: CliToolAdapter, sandbox: Path) -> None:
        sub = sandbox / "sub"
        sub.mkdir()
        (sub / "data.txt").write_text("data\n")
        result = adapter.execute("cat", ["data.txt"], cwd="sub")
        assert result.returncode == 0
        assert "data" in result.stdout


class TestTimeout:
    def test_command_timeout(self, sandbox: Path) -> None:
        config = CliToolConfig(
            working_directory=str(sandbox),
            timeout=0.1,
            allowed_commands=["sleep"],
        )
        adapter = CliToolAdapter(config)
        result = adapter.execute("sleep", ["10"])
        assert result.timed_out
        assert result.returncode == -1
        assert "timed out" in result.stderr


class TestOutputTruncation:
    def test_large_output_truncated(self, sandbox: Path) -> None:
        config = CliToolConfig(
            working_directory=str(sandbox),
            max_output_bytes=10,
        )
        adapter = CliToolAdapter(config)
        result = adapter.execute("echo", ["abcdefghijklmnopqrstuvwxyz"])
        assert len(result.stdout) <= 10


class TestRegistration:
    def test_register_as_cli_role(self, sandbox: Path) -> None:
        reg = ToolRegistry()
        CliToolAdapter(CliToolConfig(working_directory=str(sandbox)))
        reg.register("cli", role=ToolRole.CLI)
        reg.equip(ToolRole.CLI, "cli")
        belt = reg.get_belt()
        assert ToolRole.CLI in belt
        assert belt[ToolRole.CLI].name == "cli"


class TestCommandResult:
    def test_result_fields(self) -> None:
        r = CommandResult(command="echo", returncode=0, stdout="hi\n", stderr="")
        assert r.command == "echo"
        assert not r.timed_out


class TestSandboxEnv:
    def test_restricted_path(self, adapter: CliToolAdapter) -> None:
        env = adapter._sandbox_env()
        assert "/usr/bin" in env["PATH"]
        # Should not inherit full parent env
        assert "EDITOR" not in env or env.get("EDITOR") is None
