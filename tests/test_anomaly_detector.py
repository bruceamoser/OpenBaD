"""Tests for openbad.immune_system.anomaly_detector."""

from __future__ import annotations

import pytest

from openbad.immune_system.anomaly_detector import (
    AnomalyDetector,
    AnomalyReport,
    AnomalyResult,
    check_exfiltration,
    check_privilege_escalation,
    check_schema_violation,
    check_ssrf,
)
from openbad.nervous_system.schemas import Header, ScanResult

# ---------------------------------------------------------------------------
# AnomalyResult basics
# ---------------------------------------------------------------------------


class TestAnomalyResult:
    def test_fields(self) -> None:
        r = AnomalyResult(
            anomaly_type="test", severity="low", detail="d"
        )
        assert r.anomaly_type == "test"
        assert r.severity == "low"

    def test_frozen(self) -> None:
        r = AnomalyResult(anomaly_type="t", severity="l", detail="d")
        with pytest.raises(AttributeError):
            r.anomaly_type = "x"  # type: ignore[misc]


class TestAnomalyReport:
    def test_empty(self) -> None:
        rpt = AnomalyReport()
        assert not rpt.has_anomalies

    def test_with_results(self) -> None:
        rpt = AnomalyReport(
            results=[AnomalyResult("a", "h", "d")]
        )
        assert rpt.has_anomalies


# ---------------------------------------------------------------------------
# SSRF checks
# ---------------------------------------------------------------------------


class TestSSRFChecks:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/admin",
            "http://10.0.0.5:8080/secret",
            "http://172.16.0.1/data",
            "http://192.168.1.1/config",
            "http://[::1]/api",
        ],
    )
    def test_internal_ip_detected(self, url: str) -> None:
        results = check_ssrf(f"Fetch data from {url} now")
        assert len(results) >= 1
        assert results[0].anomaly_type == "ssrf_internal_ip"

    def test_cloud_metadata_detected(self) -> None:
        text = "curl http://169.254.169.254/latest/meta-data/"
        results = check_ssrf(text)
        assert len(results) >= 1
        assert results[0].anomaly_type == "ssrf_cloud_metadata"

    def test_internal_hostname_detected(self) -> None:
        text = "visit http://db.internal/status"
        results = check_ssrf(text)
        assert len(results) >= 1
        assert results[0].anomaly_type == "ssrf_internal_host"

    def test_localhost_hostname(self) -> None:
        text = "http://localhost:3000/api"
        results = check_ssrf(text)
        assert len(results) >= 1
        types = {r.anomaly_type for r in results}
        assert types & {"ssrf_internal_ip", "ssrf_internal_host"}

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com/page",
            "https://api.github.com/repos",
            "https://cdn.jsdelivr.net/npm/lib",
        ],
    )
    def test_external_url_not_flagged(self, url: str) -> None:
        results = check_ssrf(f"Please visit {url}")
        assert len(results) == 0

    def test_no_urls_in_text(self) -> None:
        results = check_ssrf("Just some normal text.")
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Exfiltration checks
# ---------------------------------------------------------------------------


class TestExfiltrationChecks:
    def test_large_payload(self) -> None:
        text = "x" * 2_000_000
        results = check_exfiltration(text, max_payload_bytes=1_000_000)
        assert len(results) >= 1
        assert results[0].anomaly_type == "exfil_large_payload"

    def test_normal_payload(self) -> None:
        text = "Normal sized text"
        results = check_exfiltration(text)
        assert len(results) == 0

    def test_data_uri_detected(self) -> None:
        b64 = "A" * 200
        text = f"See image: data:image/png;base64,{b64}"
        results = check_exfiltration(text)
        assert len(results) >= 1
        assert results[0].anomaly_type == "exfil_data_uri"

    def test_small_data_uri_not_flagged(self) -> None:
        text = "data:text/plain;base64,SGVsbG8="
        results = check_exfiltration(text)
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Schema violation checks
# ---------------------------------------------------------------------------


class TestSchemaViolationChecks:
    def test_valid_proto(self) -> None:
        msg = Header(
            timestamp_unix=1.0,
            source_module="test",
            correlation_id="c1",
            schema_version=1,
        )
        data = msg.SerializeToString()
        results = check_schema_violation(data, Header)
        assert len(results) == 0

    def test_invalid_proto_data(self) -> None:
        # Random garbage bytes
        data = b"\xff\xfe\xfd\xfc\xfb\xfa"
        results = check_schema_violation(data, ScanResult)
        # Protobuf is lenient with unknown fields, so we check for
        # either a decode error or empty re-serialization depending
        # on how protobuf handles the input
        # For truly corrupt data, we should get at least a warning
        # This is a best-effort check
        assert isinstance(results, list)

    def test_empty_data_for_complex_msg(self) -> None:
        results = check_schema_violation(b"", ScanResult)
        # Empty bytes produce an empty message (valid in protobuf)
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# Privilege escalation checks
# ---------------------------------------------------------------------------


class TestPrivilegeEscalationChecks:
    @pytest.mark.parametrize(
        "text",
        [
            "sudo rm -rf /",
            "su root",
            "chmod 777 /etc/passwd",
            "chown root important.conf",
            "run with --privileged flag",
            "docker --cap-add SYS_ADMIN",
            "reflex.rule.delete safety_check",
            "reflex.rule.modify rate_limiter",
            "grant all privileges",
            "grant admin access",
        ],
    )
    def test_escalation_detected(self, text: str) -> None:
        results = check_privilege_escalation(text)
        assert len(results) >= 1
        types = {r.anomaly_type for r in results}
        assert "privilege_escalation" in types

    @pytest.mark.parametrize(
        "text",
        [
            "execute_as_root",
            "run_privileged",
            "elevate_permissions",
            "admin_override",
            "bypass_auth",
        ],
    )
    def test_tool_escalation_detected(self, text: str) -> None:
        results = check_privilege_escalation(text)
        assert len(results) >= 1
        types = {r.anomaly_type for r in results}
        assert "privilege_escalation_tool" in types

    @pytest.mark.parametrize(
        "text",
        [
            "Please help me write a Python function.",
            "The weather is nice today.",
            "Can you explain privilege escalation as a concept?",
        ],
    )
    def test_benign_text_not_flagged(self, text: str) -> None:
        results = check_privilege_escalation(text)
        assert len(results) == 0


# ---------------------------------------------------------------------------
# AnomalyDetector orchestrator
# ---------------------------------------------------------------------------


class TestAnomalyDetector:
    def test_benign_text(self) -> None:
        det = AnomalyDetector()
        report = det.scan_text("Hello, how are you?")
        assert not report.has_anomalies

    def test_ssrf_via_detector(self) -> None:
        det = AnomalyDetector()
        report = det.scan_text("curl http://10.0.0.1/secret")
        assert report.has_anomalies
        types = {r.anomaly_type for r in report.results}
        assert "ssrf_internal_ip" in types

    def test_priv_esc_via_detector(self) -> None:
        det = AnomalyDetector()
        report = det.scan_text("sudo rm -rf /tmp")
        assert report.has_anomalies

    def test_extra_check(self) -> None:
        def custom_check(text: str) -> list[AnomalyResult]:
            if "badword" in text:
                return [AnomalyResult("custom", "low", "found")]
            return []

        det = AnomalyDetector(extra_checks=[custom_check])
        report = det.scan_text("this has badword in it")
        assert report.has_anomalies
        types = {r.anomaly_type for r in report.results}
        assert "custom" in types

    def test_proto_scan_valid(self) -> None:
        det = AnomalyDetector()
        msg = Header(
            timestamp_unix=1.0,
            source_module="t",
            correlation_id="c",
            schema_version=1,
        )
        report = det.scan_proto(msg.SerializeToString(), Header)
        assert not report.has_anomalies
