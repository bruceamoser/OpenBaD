"""Tests for the setup wizard."""

from __future__ import annotations

from unittest.mock import patch

import yaml

from openbad.setup import (
    CONFIG_FILES,
    check_cgroup_v2,
    check_mqtt_broker,
    check_platform,
    check_python_version,
    check_systemd,
    copy_configs,
    generate_secret_key,
    patch_identity_config,
    run_wizard,
    validate_config,
)


class TestEnvironmentChecks:
    """Environment detection helpers."""

    def test_python_version_passes(self):
        ok, msg = check_python_version()
        assert ok
        assert "Python" in msg

    def test_platform_returns_string(self):
        _ok, msg = check_platform()
        assert len(msg) > 0

    @patch("openbad.setup.Path.exists", return_value=True)
    def test_cgroup_v2_detected(self, _mock):
        ok, msg = check_cgroup_v2()
        assert ok
        assert "v2" in msg

    @patch("openbad.setup.Path.exists", return_value=False)
    def test_cgroup_v2_not_detected(self, _mock):
        ok, _msg = check_cgroup_v2()
        assert not ok

    @patch("openbad.setup.Path.is_dir", return_value=True)
    def test_systemd_detected(self, _mock):
        ok, _msg = check_systemd()
        assert ok

    @patch("openbad.setup.Path.is_dir", return_value=False)
    def test_systemd_not_detected(self, _mock):
        ok, _msg = check_systemd()
        assert not ok

    def test_mqtt_broker_unreachable(self):
        ok, msg = check_mqtt_broker("localhost", 19999)
        assert not ok
        assert "not reachable" in msg


class TestCopyConfigs:
    """Config file copying."""

    def test_copies_to_empty_dir(self, tmp_path):
        copied = copy_configs(tmp_path)
        assert len(copied) > 0
        for name in copied:
            assert (tmp_path / name).exists()

    def test_no_overwrite_by_default(self, tmp_path):
        copy_configs(tmp_path)
        copied2 = copy_configs(tmp_path)
        assert len(copied2) == 0  # nothing overwritten

    def test_overwrite_flag(self, tmp_path):
        copy_configs(tmp_path)
        copied2 = copy_configs(tmp_path, overwrite=True)
        assert len(copied2) > 0


class TestSecretKey:
    """Secret key generation and patching."""

    def test_generate_secret_key_length(self):
        key = generate_secret_key()
        assert len(key) == 64  # 32 bytes hex

    def test_generate_secret_key_unique(self):
        k1 = generate_secret_key()
        k2 = generate_secret_key()
        assert k1 != k2

    def test_patch_identity_config(self, tmp_path):
        identity_path = tmp_path / "identity.yaml"
        identity_path.write_text(yaml.dump({"identity": {"secret_hex": ""}}))
        patch_identity_config(tmp_path, "abc123")
        data = yaml.safe_load(identity_path.read_text())
        assert data["identity"]["secret_hex"] == "abc123"  # noqa: S105

    def test_patch_identity_missing_file(self, tmp_path):
        # Should not raise
        patch_identity_config(tmp_path, "abc123")


class TestValidateConfig:
    """Config validation."""

    def test_all_present(self, tmp_path):
        copy_configs(tmp_path)
        missing = validate_config(tmp_path)
        assert len(missing) == 0

    def test_missing_files(self, tmp_path):
        missing = validate_config(tmp_path)
        assert len(missing) == len(CONFIG_FILES)

    def test_partial(self, tmp_path):
        copy_configs(tmp_path)
        (tmp_path / CONFIG_FILES[0]).unlink()
        missing = validate_config(tmp_path)
        assert len(missing) == 1
        assert missing[0] == CONFIG_FILES[0]


class TestRunWizard:
    """Full wizard flow."""

    def test_non_interactive_creates_configs(self, tmp_path):
        ok = run_wizard(
            config_dir=tmp_path,
            mqtt_host="localhost",
            mqtt_port=19999,
            non_interactive=True,
            check_only=False,
        )
        assert ok
        missing = validate_config(tmp_path)
        assert len(missing) == 0

    def test_check_only_no_configs(self, tmp_path):
        ok = run_wizard(
            config_dir=tmp_path,
            mqtt_host="localhost",
            mqtt_port=19999,
            non_interactive=True,
            check_only=True,
        )
        assert not ok  # configs missing

    def test_check_only_with_configs(self, tmp_path):
        copy_configs(tmp_path)
        ok = run_wizard(
            config_dir=tmp_path,
            mqtt_host="localhost",
            mqtt_port=19999,
            non_interactive=True,
            check_only=True,
        )
        assert ok

    def test_idempotent(self, tmp_path):
        run_wizard(
            config_dir=tmp_path,
            non_interactive=True,
            check_only=False,
        )
        # Second run should not break
        ok = run_wizard(
            config_dir=tmp_path,
            non_interactive=True,
            check_only=False,
        )
        assert ok

    def test_generates_secret_key(self, tmp_path):
        run_wizard(
            config_dir=tmp_path,
            non_interactive=True,
            check_only=False,
        )
        data = yaml.safe_load((tmp_path / "identity.yaml").read_text())
        secret = data["identity"]["secret_hex"]
        assert len(secret) == 64
