"""Tests for openbad.toolbelt.fs_tool (issue #407)."""

from __future__ import annotations

from pathlib import Path

import pytest

import openbad.toolbelt.fs_tool as fs_tool
from openbad.toolbelt.fs_tool import read_file, write_file

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
