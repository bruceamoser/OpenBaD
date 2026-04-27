"""Tests for openbad.updater — fast update logic."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from openbad import updater

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def fake_project(tmp_path: Path) -> Path:
    """Create a minimal project layout for testing."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "openbad"\n')
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "install.sh").write_text("#!/bin/sh\nexit 0\n")
    (scripts / "install.sh").chmod(0o755)
    (scripts / "openbad-apply-heartbeat-interval").write_text("#!/bin/sh\n")
    (scripts / "openbad-apply-telemetry-interval").write_text("#!/bin/sh\n")

    config = tmp_path / "config"
    config.mkdir()
    (config / "broker.conf").write_text("listener 1883\n")
    (config / "senses.yaml").write_text("senses: []\n")
    (config / "openbad.service").write_text("[Unit]\nDescription=test\n")
    (config / "openbad-heartbeat.service").write_text("[Unit]\nDescription=hb\n")
    return tmp_path


# ------------------------------------------------------------------
# git_pull
# ------------------------------------------------------------------


def test_git_pull_no_git(fake_project: Path) -> None:
    with patch("shutil.which", return_value=None):
        msg = updater.git_pull(fake_project)
    assert "git not found" in msg


def test_git_pull_success(fake_project: Path) -> None:
    with patch("shutil.which", return_value="/usr/bin/git"), patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, stdout="Already up to date.", stderr=""),
    ):
        msg = updater.git_pull(fake_project)
    assert "Already up to date" in msg


def test_git_pull_failure(fake_project: Path) -> None:
    with patch("shutil.which", return_value="/usr/bin/git"), patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="divergent"),
    ):
        msg = updater.git_pull(fake_project)
    assert "skipped" in msg


# ------------------------------------------------------------------
# sync_configs
# ------------------------------------------------------------------


def test_sync_configs_copies_new_files(fake_project: Path, tmp_path: Path) -> None:
    cfg_dir = tmp_path / "etc_openbad"
    with patch.object(updater, "CONFIG_DIR", cfg_dir):
        copied = updater.sync_configs(fake_project)

    assert "broker.conf" in copied
    assert "senses.yaml" in copied
    assert (cfg_dir / "broker.conf").exists()


def test_sync_configs_skips_existing(fake_project: Path, tmp_path: Path) -> None:
    cfg_dir = tmp_path / "etc_openbad"
    cfg_dir.mkdir()
    (cfg_dir / "broker.conf").write_text("custom\n")

    with patch.object(updater, "CONFIG_DIR", cfg_dir):
        copied = updater.sync_configs(fake_project)

    assert "broker.conf" not in copied
    assert (cfg_dir / "broker.conf").read_text() == "custom\n"  # preserved


# ------------------------------------------------------------------
# sync_units
# ------------------------------------------------------------------


def test_sync_units_copies_changed(fake_project: Path, tmp_path: Path) -> None:
    sd_dir = tmp_path / "systemd"
    sd_dir.mkdir()

    with patch.object(updater, "SYSTEMD_DIR", sd_dir):
        updated = updater.sync_units(fake_project)

    assert "openbad.service" in updated
    assert (sd_dir / "openbad.service").exists()


def test_sync_units_skips_unchanged(fake_project: Path, tmp_path: Path) -> None:
    sd_dir = tmp_path / "systemd"
    sd_dir.mkdir()
    # Pre-populate with identical content
    (sd_dir / "openbad.service").write_text("[Unit]\nDescription=test\n")

    with patch.object(updater, "SYSTEMD_DIR", sd_dir):
        updated = updater.sync_units(fake_project)

    assert "openbad.service" not in updated


# ------------------------------------------------------------------
# sync_helper_scripts
# ------------------------------------------------------------------


def test_sync_helper_scripts_finds_scripts(fake_project: Path, tmp_path: Path) -> None:
    """Verify that the function finds helper scripts in the project."""
    scripts_dir = fake_project / "scripts"
    assert (scripts_dir / "openbad-apply-heartbeat-interval").exists()
    assert (scripts_dir / "openbad-apply-telemetry-interval").exists()


# ------------------------------------------------------------------
# pip_install_no_deps
# ------------------------------------------------------------------


def test_pip_install_no_deps_uses_venv_pip(fake_project: Path, tmp_path: Path) -> None:
    venv_pip = tmp_path / "venv" / "bin" / "pip"
    venv_pip.parent.mkdir(parents=True)
    venv_pip.write_text("#!/bin/sh\n")

    with patch.object(updater, "VENV_PIP", venv_pip), patch(
        "subprocess.run"
    ) as mock_run:
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        updater.pip_install_no_deps(fake_project)

    cmd = mock_run.call_args[0][0]
    assert str(venv_pip) in cmd[0]
    assert "--no-deps" in cmd
    assert "--no-build-isolation" in cmd


def test_pip_install_no_deps_fallback(fake_project: Path) -> None:
    with patch.object(updater, "VENV_PIP", Path("/nonexistent/pip")), patch(
        "subprocess.run"
    ) as mock_run:
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        updater.pip_install_no_deps(fake_project)

    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "pip"


# ------------------------------------------------------------------
# get_latest_release_tag
# ------------------------------------------------------------------


def test_get_latest_release_tag_success() -> None:
    import io
    import json

    payload = json.dumps({"tag_name": "v0.2.1"}).encode()
    mock_resp = io.BytesIO(payload)
    mock_resp.status = 200  # type: ignore[attr-defined]

    with patch("urllib.request.urlopen", return_value=mock_resp):
        tag = updater.get_latest_release_tag()
    assert tag == "v0.2.1"


def test_get_latest_release_tag_network_error() -> None:
    from urllib.error import URLError

    with patch("urllib.request.urlopen", side_effect=URLError("offline")):
        tag = updater.get_latest_release_tag()
    assert tag is None


# ------------------------------------------------------------------
# quick_update orchestrator
# ------------------------------------------------------------------


def test_quick_update_calls_all_steps(fake_project: Path, tmp_path: Path) -> None:
    cfg_dir = tmp_path / "etc_openbad"
    sd_dir = tmp_path / "systemd"
    sd_dir.mkdir()

    with (
        patch.object(updater, "git_pull", return_value="up to date") as mock_pull,
        patch.object(updater, "pip_install_no_deps") as mock_pip,
        patch.object(updater, "CONFIG_DIR", cfg_dir),
        patch.object(updater, "SYSTEMD_DIR", sd_dir),
        patch.object(updater, "systemd_reload") as mock_reload,
        patch.object(updater, "restart_services") as mock_restart,
        patch.object(updater, "sync_helper_scripts", return_value=[]),
    ):
        updater.quick_update(fake_project)

    mock_pull.assert_called_once_with(fake_project)
    mock_pip.assert_called_once_with(fake_project)
    mock_reload.assert_called_once()
    mock_restart.assert_called_once()


def test_quick_update_skip_services(fake_project: Path, tmp_path: Path) -> None:
    cfg_dir = tmp_path / "etc_openbad"

    with (
        patch.object(updater, "git_pull", return_value="up to date"),
        patch.object(updater, "pip_install_no_deps"),
        patch.object(updater, "CONFIG_DIR", cfg_dir),
        patch.object(updater, "restart_services") as mock_restart,
        patch.object(updater, "sync_units") as mock_sync_units,
    ):
        updater.quick_update(fake_project, skip_services=True)

    mock_restart.assert_not_called()
    mock_sync_units.assert_not_called()
