"""Tests for openbad.toolbelt.fs_tool (issue #407)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import openbad.toolbelt.fs_tool as fs_tool
from openbad.immune_system.rules_engine import FileOperationRule, is_restricted_path
from openbad.toolbelt.fs_tool import (
    CORTISOL_HIGH_THRESHOLD,
    DISK_FREE_BYTES_MIN,
    DISK_LATENCY_SATURATION_MS,
    LARGE_OP_THRESHOLD_BYTES,
    ResourceDeferredError,
    read_file,
    should_defer,
    write_file,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def allow_tmp(tmp_path: Path, monkeypatch):
    """Ensure ALLOWED_ROOTS includes the test's tmp_path."""
    monkeypatch.setattr(fs_tool, "ALLOWED_ROOTS", [str(tmp_path)])
    return tmp_path


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


class TestReadFile:
    def test_reads_content(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.txt"
        f.write_text("hello world")
        assert read_file(str(f)) == "hello world"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_file(str(tmp_path / "nonexistent.txt"))

    def test_directory_raises(self, tmp_path: Path) -> None:
        with pytest.raises(IsADirectoryError):
            read_file(str(tmp_path))

    def test_path_traversal_rejected(self, tmp_path: Path) -> None:
        # Build a path that tries to escape tmp_path
        evil = str(tmp_path) + "/../../etc/passwd"
        with pytest.raises(PermissionError):
            read_file(evil)

    def test_absolute_outside_allowed_rejected(self) -> None:
        with pytest.raises(PermissionError):
            read_file("/etc/passwd")


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


class TestWriteFile:
    def test_writes_creates_file(self, tmp_path: Path) -> None:
        dest = tmp_path / "out.txt"
        write_file(str(dest), "content here")
        assert dest.read_text() == "content here"

    def test_write_overwrites_existing(self, tmp_path: Path) -> None:
        dest = tmp_path / "existing.txt"
        dest.write_text("old")
        write_file(str(dest), "new")
        assert dest.read_text() == "new"

    def test_write_is_atomic_no_tmp_left(self, tmp_path: Path) -> None:
        dest = tmp_path / "atomic.txt"
        write_file(str(dest), "data")
        # No .tmp files should remain
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_write_missing_parent_raises(self, tmp_path: Path) -> None:
        dest = tmp_path / "subdir" / "file.txt"
        with pytest.raises(FileNotFoundError):
            write_file(str(dest), "content")

    def test_write_path_traversal_rejected(self, tmp_path: Path) -> None:
        evil = str(tmp_path) + "/../../etc/profile"
        with pytest.raises(PermissionError):
            write_file(evil, "bad")

    def test_write_absolute_outside_allowed_rejected(self) -> None:
        with pytest.raises(PermissionError):
            write_file("/etc/evil.conf", "pwned")


# ---------------------------------------------------------------------------
# Symlink escape prevention
# ---------------------------------------------------------------------------


class TestSymlinkEscape:
    def test_symlink_to_outside_rejected(self, tmp_path: Path) -> None:
        # Create a symlink inside tmp_path that points outside
        link = tmp_path / "escape_link.txt"
        link.symlink_to("/etc/passwd")
        with pytest.raises(PermissionError):
            read_file(str(link))

    def test_symlink_within_allowed_ok(self, tmp_path: Path) -> None:
        target = tmp_path / "real.txt"
        target.write_text("real content")
        link = tmp_path / "link.txt"
        link.symlink_to(target)
        assert read_file(str(link)) == "real content"


# ---------------------------------------------------------------------------
# Phase 10: FileOperationRule immune gate (#408)
# ---------------------------------------------------------------------------


class TestIsRestrictedPath:
    def test_etc_passwd_is_restricted(self) -> None:
        assert is_restricted_path("/etc/passwd") is True

    def test_ssh_authorized_keys_is_restricted(self) -> None:
        assert is_restricted_path("/home/user/.ssh/authorized_keys") is True

    def test_proc_is_restricted(self) -> None:
        assert is_restricted_path("/proc/1/maps") is True

    def test_usr_bin_is_restricted(self) -> None:
        assert is_restricted_path("/usr/bin/python3") is True

    def test_tmp_dir_not_restricted(self, tmp_path: Path) -> None:
        assert is_restricted_path(str(tmp_path / "safe.txt")) is False


class TestFileOperationRule:
    def test_restricted_write_raises(self) -> None:
        rule = FileOperationRule()
        with pytest.raises(PermissionError, match="restricted path"):
            rule.check_write("/etc/passwd")

    def test_safe_write_passes(self, tmp_path: Path) -> None:
        rule = FileOperationRule()
        # Should not raise for a safe temp path
        rule.check_write(str(tmp_path / "safe.txt"))

    def test_restricted_write_publishes_alert(self) -> None:
        ns = MagicMock()
        rule = FileOperationRule(ns)
        with pytest.raises(PermissionError):
            rule.check_write("/etc/hosts")
        ns.publish.assert_called_once()
        call_args = ns.publish.call_args[0]
        assert "agent/immune/alert" in call_args[0]

    def test_no_ns_no_crash_on_restricted(self) -> None:
        rule = FileOperationRule(nervous_system=None)
        with pytest.raises(PermissionError):
            rule.check_write("/sys/kernel/notes")


class TestWriteFileImmuneGate:
    def test_write_to_etc_blocked_via_fs_tool(self, tmp_path: Path) -> None:
        """write_file should be blocked when target resolves to a restricted path."""
        # We override ALLOWED_ROOTS to include /etc so the path-safety check passes,
        # but the immune gate should still block it.
        with (
            patch.object(fs_tool, "ALLOWED_ROOTS", ["/etc", str(tmp_path)]),
            patch.object(
                fs_tool._FILE_OP_RULE,
                "check_write",
                side_effect=PermissionError("blocked"),
            ) as mock_check,
        ):
            with pytest.raises(PermissionError):
                write_file("/etc/passwd", "hacked")
            mock_check.assert_called_once()

    def test_safe_write_not_blocked(self, tmp_path: Path) -> None:
        """write_file succeeds for a safe path without triggering the immune gate."""
        dest = tmp_path / "output.txt"
        write_file(str(dest), "hello")
        assert dest.read_text() == "hello"


# ---------------------------------------------------------------------------
# Phase 10: endocrine throttling (#409)
# ---------------------------------------------------------------------------


def _disk(latency_ms: float = 0.0, free_bytes: int = 500 * 1024 * 1024) -> MagicMock:
    snap = MagicMock()
    snap.io_latency_ms = latency_ms
    snap.free_bytes = free_bytes
    return snap


def _endocrine(cortisol: float = 0.0) -> MagicMock:
    ec = MagicMock()
    ec.level.return_value = cortisol
    return ec


class TestShouldDefer:
    def test_small_op_never_defers(self) -> None:
        disk = _disk(latency_ms=9999.0, free_bytes=0)
        ec = _endocrine(cortisol=1.0)
        assert should_defer(100, disk_snapshot=disk, endocrine=ec) is False

    def test_no_disk_snapshot_no_defer(self) -> None:
        result = should_defer(
            LARGE_OP_THRESHOLD_BYTES + 1, disk_snapshot=None, endocrine=_endocrine(1.0)
        )
        assert result is False

    def test_no_endocrine_no_defer(self) -> None:
        disk = _disk(latency_ms=DISK_LATENCY_SATURATION_MS + 1)
        result = should_defer(
            LARGE_OP_THRESHOLD_BYTES + 1, disk_snapshot=disk, endocrine=None
        )
        assert result is False

    def test_healthy_disk_no_defer(self) -> None:
        disk = _disk(latency_ms=10.0, free_bytes=DISK_FREE_BYTES_MIN + 1)
        ec = _endocrine(cortisol=1.0)
        result = should_defer(
            LARGE_OP_THRESHOLD_BYTES + 1, disk_snapshot=disk, endocrine=ec
        )
        assert result is False

    def test_saturated_disk_high_cortisol_defers(self) -> None:
        disk = _disk(latency_ms=DISK_LATENCY_SATURATION_MS + 1)
        ec = _endocrine(cortisol=CORTISOL_HIGH_THRESHOLD)
        result = should_defer(
            LARGE_OP_THRESHOLD_BYTES + 1, disk_snapshot=disk, endocrine=ec
        )
        assert result is True

    def test_low_free_bytes_high_cortisol_defers(self) -> None:
        disk = _disk(free_bytes=DISK_FREE_BYTES_MIN - 1)
        ec = _endocrine(cortisol=CORTISOL_HIGH_THRESHOLD)
        result = should_defer(
            LARGE_OP_THRESHOLD_BYTES + 1, disk_snapshot=disk, endocrine=ec
        )
        assert result is True

    def test_saturated_disk_low_cortisol_no_defer(self) -> None:
        disk = _disk(latency_ms=DISK_LATENCY_SATURATION_MS + 1)
        ec = _endocrine(cortisol=CORTISOL_HIGH_THRESHOLD - 0.01)
        result = should_defer(LARGE_OP_THRESHOLD_BYTES + 1, disk_snapshot=disk, endocrine=ec)
        assert result is False


class TestWriteFileDeferral:
    def test_large_saturated_write_defers(self, tmp_path: Path) -> None:
        disk = _disk(latency_ms=DISK_LATENCY_SATURATION_MS + 1)
        ec = _endocrine(cortisol=CORTISOL_HIGH_THRESHOLD)
        big = "x" * (LARGE_OP_THRESHOLD_BYTES + 1)
        with pytest.raises(ResourceDeferredError):
            write_file(str(tmp_path / "big.txt"), big, disk_snapshot=disk, endocrine=ec)

    def test_normal_write_passes(self, tmp_path: Path) -> None:
        dest = tmp_path / "out.txt"
        disk = _disk(latency_ms=DISK_LATENCY_SATURATION_MS + 1)
        ec = _endocrine(cortisol=CORTISOL_HIGH_THRESHOLD)
        # Small content should not trigger deferral regardless of disk state
        write_file(str(dest), "small content", disk_snapshot=disk, endocrine=ec)
        assert dest.read_text() == "small content"
