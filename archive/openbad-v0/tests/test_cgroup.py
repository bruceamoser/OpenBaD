"""Tests for openbad.interoception.cgroup — cgroup v2 management.

All tests mock filesystem operations so they run on any OS (including
the Windows dev environment).  Integration tests that require a real
Linux cgroup v2 filesystem are marked with ``@pytest.mark.integration``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openbad.interoception.cgroup import (
    CGROUP_BASE,
    CGROUP_NAME,
    CGROUP_PATH,
    REQUIRED_CONTROLLERS,
    CgroupError,
    add_pid,
    available_controllers,
    create_cgroup,
    enable_controllers,
    is_cgroup_v2,
    setup_cgroup,
)

# ── Constants ──────────────────────────────────────────────────────


class TestConstants:
    def test_cgroup_base(self):
        assert Path("/sys/fs/cgroup") == CGROUP_BASE

    def test_cgroup_name(self):
        assert CGROUP_NAME == "openbad"

    def test_cgroup_path(self):
        assert Path("/sys/fs/cgroup/openbad") == CGROUP_PATH

    def test_required_controllers(self):
        assert "cpu" in REQUIRED_CONTROLLERS
        assert "memory" in REQUIRED_CONTROLLERS
        assert "io" in REQUIRED_CONTROLLERS


# ── Non-Linux guard ────────────────────────────────────────────────


class TestNonLinuxGuard:
    @patch("openbad.interoception.cgroup.platform")
    def test_is_cgroup_v2_raises_on_non_linux(self, mock_platform):
        mock_platform.system.return_value = "Windows"
        with pytest.raises(CgroupError, match="requires Linux"):
            is_cgroup_v2()

    @patch("openbad.interoception.cgroup.platform")
    def test_create_cgroup_raises_on_non_linux(self, mock_platform):
        mock_platform.system.return_value = "Darwin"
        with pytest.raises(CgroupError, match="requires Linux"):
            create_cgroup()

    @patch("openbad.interoception.cgroup.platform")
    def test_add_pid_raises_on_non_linux(self, mock_platform):
        mock_platform.system.return_value = "Windows"
        with pytest.raises(CgroupError, match="requires Linux"):
            add_pid(1234)


# ── is_cgroup_v2 ──────────────────────────────────────────────────


class TestIsCgroupV2:
    @patch("openbad.interoception.cgroup.platform")
    def test_returns_true_when_controllers_file_exists(self, mock_platform):
        mock_platform.system.return_value = "Linux"
        with patch.object(Path, "exists", return_value=True):
            assert is_cgroup_v2() is True

    @patch("openbad.interoception.cgroup.platform")
    def test_returns_false_when_no_controllers_file(self, mock_platform):
        mock_platform.system.return_value = "Linux"
        with patch.object(Path, "exists", return_value=False):
            assert is_cgroup_v2() is False


# ── available_controllers ──────────────────────────────────────────


class TestAvailableControllers:
    @patch("openbad.interoception.cgroup.platform")
    def test_returns_controllers_list(self, mock_platform):
        mock_platform.system.return_value = "Linux"
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "read_text", return_value="cpu memory io pids\n"),
        ):
            result = available_controllers()
            assert result == ["cpu", "memory", "io", "pids"]

    @patch("openbad.interoception.cgroup.platform")
    def test_returns_empty_when_no_file(self, mock_platform):
        mock_platform.system.return_value = "Linux"
        with patch.object(Path, "exists", return_value=False):
            assert available_controllers() == []


# ── enable_controllers ─────────────────────────────────────────────


class TestEnableControllers:
    @patch("openbad.interoception.cgroup.platform")
    def test_writes_controller_enables(self, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_write = MagicMock()
        cgroup = Path("/sys/fs/cgroup/openbad")
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "write_text", mock_write),
        ):
            enable_controllers(cgroup)
            assert mock_write.call_count == len(REQUIRED_CONTROLLERS)
            calls = [c.args[0] for c in mock_write.call_args_list]
            assert "+cpu\n" in calls
            assert "+memory\n" in calls
            assert "+io\n" in calls

    @patch("openbad.interoception.cgroup.platform")
    def test_raises_when_subtree_control_missing(self, mock_platform):
        mock_platform.system.return_value = "Linux"
        cgroup = Path("/sys/fs/cgroup/openbad")
        with (
            patch.object(Path, "exists", return_value=False),
            pytest.raises(CgroupError, match="subtree_control not found"),
        ):
            enable_controllers(cgroup)


# ── create_cgroup ──────────────────────────────────────────────────


class TestCreateCgroup:
    @patch("openbad.interoception.cgroup.platform")
    def test_creates_directory(self, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_mkdir = MagicMock()
        with (
            patch.object(Path, "exists", return_value=False),
            patch.object(Path, "mkdir", mock_mkdir),
        ):
            result = create_cgroup()
            assert result == CGROUP_PATH
            mock_mkdir.assert_called_once_with(parents=False, exist_ok=True)

    @patch("openbad.interoception.cgroup.platform")
    def test_skips_if_exists(self, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_mkdir = MagicMock()
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "mkdir", mock_mkdir),
        ):
            result = create_cgroup()
            assert result == CGROUP_PATH
            mock_mkdir.assert_not_called()

    @patch("openbad.interoception.cgroup.platform")
    def test_raises_on_mkdir_failure(self, mock_platform):
        mock_platform.system.return_value = "Linux"
        with (
            patch.object(Path, "exists", return_value=False),
            patch.object(Path, "mkdir", side_effect=OSError("permission denied")),
            pytest.raises(CgroupError, match="Failed to create"),
        ):
            create_cgroup()


# ── add_pid ────────────────────────────────────────────────────────


class TestAddPid:
    @patch("openbad.interoception.cgroup.platform")
    def test_writes_pid(self, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_write = MagicMock()
        with patch.object(Path, "write_text", mock_write):
            add_pid(42)
            mock_write.assert_called_once_with("42\n")

    @patch("openbad.interoception.cgroup.platform")
    @patch("openbad.interoception.cgroup.os")
    def test_uses_current_pid_when_none(self, mock_os, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_os.getpid.return_value = 9999
        mock_write = MagicMock()
        with patch.object(Path, "write_text", mock_write):
            add_pid()
            mock_write.assert_called_once_with("9999\n")


# ── setup_cgroup (integration) ────────────────────────────────────


class TestSetupCgroup:
    @patch("openbad.interoception.cgroup.platform")
    def test_calls_create_enable_add(self, mock_platform):
        mock_platform.system.return_value = "Linux"
        with (
            patch(
                "openbad.interoception.cgroup.create_cgroup", return_value=CGROUP_PATH
            ) as m_create,
            patch("openbad.interoception.cgroup.enable_controllers") as m_enable,
            patch("openbad.interoception.cgroup.add_pid") as m_add,
        ):
            result = setup_cgroup()
            assert result == CGROUP_PATH
            m_create.assert_called_once()
            m_enable.assert_called_once_with(CGROUP_PATH)
            m_add.assert_called_once_with(cgroup=CGROUP_PATH)
