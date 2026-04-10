"""Tests for systemd service file syntax and install script validation."""

from __future__ import annotations

import re
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


class TestOpenbadServiceFile:
    """Validate config/openbad.service syntax."""

    def setup_method(self):
        self.path = CONFIG_DIR / "openbad.service"
        self.content = self.path.read_text()

    def test_file_exists(self):
        assert self.path.exists()

    def test_has_unit_section(self):
        assert "[Unit]" in self.content

    def test_has_service_section(self):
        assert "[Service]" in self.content

    def test_has_install_section(self):
        assert "[Install]" in self.content

    def test_after_broker(self):
        assert "openbad-broker.service" in self.content

    def test_exec_start(self):
        match = re.search(r"ExecStart=.*openbad run", self.content)
        assert match is not None

    def test_restart_on_failure(self):
        assert "Restart=on-failure" in self.content

    def test_security_hardening(self):
        assert "NoNewPrivileges=true" in self.content
        assert "ProtectSystem=strict" in self.content
        assert "PrivateTmp=true" in self.content

    def test_user_set(self):
        assert "User=openbad" in self.content

    def test_wanted_by(self):
        assert "WantedBy=multi-user.target" in self.content


class TestBrokerServiceFile:
    """Validate config/openbad-broker.service (pre-existing)."""

    def setup_method(self):
        self.path = CONFIG_DIR / "openbad-broker.service"
        self.content = self.path.read_text()

    def test_file_exists(self):
        assert self.path.exists()

    def test_has_all_sections(self):
        for section in ("[Unit]", "[Service]", "[Install]"):
            assert section in self.content

    def test_exec_start_nanomq(self):
        assert "nanomq" in self.content


class TestInstallScript:
    """Validate scripts/install.sh structure."""

    def setup_method(self):
        self.path = SCRIPTS_DIR / "install.sh"
        self.content = self.path.read_text()

    def test_file_exists(self):
        assert self.path.exists()

    def test_shebang(self):
        assert self.content.startswith("#!/usr/bin/env bash")

    def test_set_euo_pipefail(self):
        assert "set -euo pipefail" in self.content

    def test_requires_root(self):
        assert "require_root" in self.content
        assert "EUID" in self.content

    def test_has_usage_and_arg_parsing(self):
        assert "usage()" in self.content
        assert "parse_args" in self.content
        assert "--bootstrap" in self.content
        assert "--configure-wsl-systemd" in self.content
        assert "--skip-services" in self.content
        assert "--uninstall" in self.content

    def test_bootstrap_support_present(self):
        assert "bootstrap_os" in self.content
        assert "install_prereqs_apt" in self.content
        assert "python3-venv" in self.content

    def test_systemd_detection_present(self):
        assert "has_systemd" in self.content
        assert "ensure_systemd_ready" in self.content
        assert "systemd is required for full install" in self.content

    def test_wsl_detection_present(self):
        assert "is_wsl" in self.content
        assert "WSL environment detected" in self.content
        assert "configure_wsl_systemd" in self.content

    def test_broker_fallback_support_present(self):
        assert "select_broker_impl" in self.content
        assert "install_broker_unit" in self.content
        assert "mosquitto" in self.content

    def test_creates_user(self):
        assert "useradd" in self.content
        assert "openbad" in self.content

    def test_installs_package(self):
        assert "VENV_DIR" in self.content
        assert "python3 -m venv" in self.content
        assert "bin/python\" -m pip install" in self.content
        assert "OPENBAD_BIN" in self.content

    def test_copies_configs(self):
        assert "CONFIG_DIR" in self.content

    def test_installs_systemd_units(self):
        assert "systemctl daemon-reload" in self.content
        assert "systemctl enable" in self.content

    def test_has_uninstall(self):
        assert "--uninstall" in self.content
        assert "systemctl stop" in self.content
        assert "systemctl disable" in self.content

    def test_no_rm_rf_dangerous(self):
        """Install script should not have bare 'rm -rf /' patterns."""
        # Ensure no dangerous rm -rf patterns
        dangerous = re.findall(r"rm\s+-rf\s+/[^$\s]", self.content)
        # Allow only specific paths like /etc/systemd/system/openbad*
        for match in dangerous:
            assert "openbad" in match or "SYSTEMD_DIR" in match
