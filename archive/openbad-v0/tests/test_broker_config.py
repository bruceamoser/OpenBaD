"""Tests for MQTT broker configuration and systemd service files."""

from pathlib import Path

import pytest

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


class TestBrokerConfig:
    """Validate broker.conf is well-formed and contains required settings."""

    @pytest.fixture()
    def broker_conf(self) -> str:
        path = CONFIG_DIR / "broker.conf"
        assert path.exists(), f"broker.conf not found at {path}"
        return path.read_text(encoding="utf-8")

    def test_config_file_exists(self, broker_conf: str) -> None:
        assert len(broker_conf) > 0

    def test_listener_configured(self, broker_conf: str) -> None:
        assert "listeners.tcp" in broker_conf
        assert "1883" in broker_conf

    def test_mqtt_section_present(self, broker_conf: str) -> None:
        assert "mqtt {" in broker_conf or "mqtt{" in broker_conf

    def test_max_packet_size_set(self, broker_conf: str) -> None:
        assert "max_packet_size" in broker_conf

    def test_log_section_present(self, broker_conf: str) -> None:
        assert "log {" in broker_conf or "log{" in broker_conf

    def test_authorization_section_present(self, broker_conf: str) -> None:
        assert "authorization" in broker_conf

    def test_no_plaintext_passwords(self, broker_conf: str) -> None:
        """Ensure no hardcoded passwords in config."""
        lower = broker_conf.lower()
        for bad in ["password =", "password=", "secret =", "secret="]:
            assert bad not in lower, f"Found potential credential: {bad}"


class TestSystemdUnit:
    """Validate openbad-broker.service is well-formed."""

    @pytest.fixture()
    def unit_content(self) -> str:
        path = CONFIG_DIR / "openbad-broker.service"
        assert path.exists(), f"systemd unit not found at {path}"
        return path.read_text(encoding="utf-8")

    def test_unit_file_exists(self, unit_content: str) -> None:
        assert len(unit_content) > 0

    def test_has_unit_section(self, unit_content: str) -> None:
        assert "[Unit]" in unit_content

    def test_has_service_section(self, unit_content: str) -> None:
        assert "[Service]" in unit_content

    def test_has_install_section(self, unit_content: str) -> None:
        assert "[Install]" in unit_content

    def test_restart_on_failure(self, unit_content: str) -> None:
        assert "Restart=on-failure" in unit_content

    def test_watchdog_configured(self, unit_content: str) -> None:
        assert "WatchdogSec=" in unit_content

    def test_after_network(self, unit_content: str) -> None:
        assert "After=network-online.target" in unit_content

    def test_exec_start_references_nanomq(self, unit_content: str) -> None:
        assert "nanomq" in unit_content

    def test_exec_start_references_config(self, unit_content: str) -> None:
        assert "--conf" in unit_content

    def test_security_hardening(self, unit_content: str) -> None:
        assert "NoNewPrivileges=true" in unit_content
        assert "ProtectSystem=" in unit_content

    def test_resource_limits(self, unit_content: str) -> None:
        assert "LimitNOFILE=" in unit_content
