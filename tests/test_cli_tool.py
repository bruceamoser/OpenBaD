"""Tests for CLI tool adapter — Issue #236."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openbad.immune_system.rules_engine import DestructiveCommandRule, is_destructive_command
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
        # rm -rf / is now caught by the immune gate before the allowlist check
        assert result.returncode == -1

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


# ---------------------------------------------------------------------------
# Phase 10: async_execute (#410)
# ---------------------------------------------------------------------------


class TestAsyncExecute:
    @pytest.mark.asyncio
    async def test_async_echo(self, adapter: CliToolAdapter) -> None:
        result = await adapter.async_execute("echo", ["hello async"])
        assert result.returncode == 0
        assert "hello async" in result.stdout
        assert not result.timed_out

    @pytest.mark.asyncio
    async def test_async_blocked_command(self, adapter: CliToolAdapter) -> None:
        result = await adapter.async_execute("python3", ["-c", "print('x')"])
        assert result.returncode == -1
        assert "not in allowlist" in result.stderr

    @pytest.mark.asyncio
    async def test_async_path_escape_rejected(self, adapter: CliToolAdapter) -> None:
        result = await adapter.async_execute("ls", cwd="../../../etc")
        assert result.returncode == -1
        assert "escapes sandbox" in result.stderr

    @pytest.mark.asyncio
    async def test_async_timeout(self, sandbox: Path) -> None:
        config = CliToolConfig(
            allowed_commands=["sleep"],
            working_directory=str(sandbox),
            timeout=0.1,
        )
        adapter = CliToolAdapter(config)
        result = await adapter.async_execute("sleep", ["5"])
        assert result.timed_out

    @pytest.mark.asyncio
    async def test_async_returncode_nonzero(self, adapter: CliToolAdapter) -> None:
        result = await adapter.async_execute("ls", ["/no_such_dir_xyz"])
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# Phase 10: destructive CLI quarantine gate (#411)
# ---------------------------------------------------------------------------


class TestIsDestructiveCommand:
    def test_rm_rf_slash_is_destructive(self) -> None:
        assert is_destructive_command("rm", ["-rf", "/"]) is True

    def test_rm_rf_tilde_is_destructive(self) -> None:
        assert is_destructive_command("rm", ["-rf", "~"]) is True

    def test_mkfs_is_destructive(self) -> None:
        assert is_destructive_command("mkfs.ext4", ["/dev/sda1"]) is True

    def test_chmod_777_root_is_destructive(self) -> None:
        assert is_destructive_command("chmod", ["-R", "777", "/"]) is True

    def test_dd_if_is_destructive(self) -> None:
        assert is_destructive_command("dd", ["if=/dev/zero", "of=/dev/sda"]) is True

    def test_ls_is_safe(self) -> None:
        assert is_destructive_command("ls", ["-la"]) is False

    def test_echo_is_safe(self) -> None:
        assert is_destructive_command("echo", ["hello"]) is False


class TestDestructiveCommandRule:
    def test_destructive_raises(self) -> None:
        rule = DestructiveCommandRule()
        with pytest.raises(PermissionError, match="destructive pattern"):
            rule.check("rm", ["-rf", "/"])

    def test_safe_passes(self) -> None:
        rule = DestructiveCommandRule()
        rule.check("ls", ["-la"])  # should not raise

    def test_destructive_publishes_alert_and_quarantine(self) -> None:
        ns = MagicMock()
        rule = DestructiveCommandRule(ns)
        with pytest.raises(PermissionError):
            rule.check("mkfs", ["/dev/sda"])
        assert ns.publish.call_count == 2
        topics = {call[0][0] for call in ns.publish.call_args_list}
        assert "agent/immune/alert" in topics
        assert "agent/immune/quarantine" in topics

    def test_no_ns_no_crash(self) -> None:
        rule = DestructiveCommandRule(nervous_system=None)
        with pytest.raises(PermissionError):
            rule.check("rm", ["-rf", "/"])


class TestCliAdapterQuarantine:
    """Verify cli_tool.py wires the immune gate for sync and async paths."""

    def test_sync_destructive_blocked(self, adapter: CliToolAdapter) -> None:
        result = adapter.execute("rm", ["-rf", "/"])
        assert result.returncode == -1
        assert "destructive" in result.stderr.lower()

    @pytest.mark.asyncio
    async def test_async_destructive_blocked(self, adapter: CliToolAdapter) -> None:
        result = await adapter.async_execute("rm", ["-rf", "/"])
        assert result.returncode == -1
        assert "destructive" in result.stderr.lower()

    def test_sync_safe_command_proceeds(self, adapter: CliToolAdapter) -> None:
        result = adapter.execute("ls", ["-la"])
        assert result.returncode == 0

    @pytest.mark.asyncio
    async def test_async_safe_command_proceeds(self, adapter: CliToolAdapter) -> None:
        result = await adapter.async_execute("echo", ["hi"])
        assert result.returncode == 0
