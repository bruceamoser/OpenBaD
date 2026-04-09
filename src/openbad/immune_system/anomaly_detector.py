"""Structural anomaly detector — SSRF, exfiltration, schema, and privilege checks."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from urllib.parse import urlparse

from google.protobuf.message import DecodeError, Message


@dataclass(frozen=True)
class AnomalyResult:
    """A single anomaly finding."""

    anomaly_type: str
    severity: str
    detail: str


@dataclass
class AnomalyReport:
    """Aggregated anomaly scan report."""

    results: list[AnomalyResult] = field(default_factory=list)

    @property
    def has_anomalies(self) -> bool:
        return len(self.results) > 0


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------

# Cloud metadata endpoint
_CLOUD_METADATA_IPS = {"169.254.169.254", "fd00:ec2::254"}

# Internal hostname patterns
_INTERNAL_HOST_RE = re.compile(
    r"(?i)(localhost|\.local$|\.internal$|\.corp$|\.lan$)",
)

# Data URI with base64 content
_DATA_URI_RE = re.compile(
    r"data:[^;]+;base64,[A-Za-z0-9+/=]{100,}",
)

# Privilege escalation patterns
_PRIV_ESC_RE = re.compile(
    r"(?i)(sudo|su\s+root|chmod\s+[0-7]*7|chown\s+root"
    r"|--privileged|--cap-add|reflex\.rule\.(add|delete|modify)"
    r"|grant\s+(all|admin|superuser))",
)

# Tool call patterns requesting elevated permissions
_TOOL_ESCALATION_RE = re.compile(
    r"(?i)(execute_as_root|run_privileged|elevate_permissions"
    r"|admin_override|bypass_auth)",
)


def _is_internal_ip(host: str) -> bool:
    """Return True if *host* is an internal/reserved IP address."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local


def check_ssrf(text: str) -> list[AnomalyResult]:
    """Detect internal IP addresses and cloud metadata endpoints."""
    results: list[AnomalyResult] = []
    # Find URLs
    urls = re.findall(r"https?://[^\s\"'>]+", text)
    for url in urls:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        # Cloud metadata
        if host in _CLOUD_METADATA_IPS:
            results.append(
                AnomalyResult(
                    anomaly_type="ssrf_cloud_metadata",
                    severity="critical",
                    detail=f"Cloud metadata endpoint: {url}",
                )
            )
            continue
        # Internal IP
        if _is_internal_ip(host):
            results.append(
                AnomalyResult(
                    anomaly_type="ssrf_internal_ip",
                    severity="high",
                    detail=f"Internal IP address: {host} in {url}",
                )
            )
            continue
        # Internal hostname
        if _INTERNAL_HOST_RE.search(host):
            results.append(
                AnomalyResult(
                    anomaly_type="ssrf_internal_host",
                    severity="high",
                    detail=f"Internal hostname: {host} in {url}",
                )
            )
    return results


def check_exfiltration(
    text: str,
    *,
    max_payload_bytes: int = 1_000_000,
) -> list[AnomalyResult]:
    """Detect data exfiltration indicators."""
    results: list[AnomalyResult] = []
    # Unusually large payload
    if len(text.encode("utf-8", errors="replace")) > max_payload_bytes:
        results.append(
            AnomalyResult(
                anomaly_type="exfil_large_payload",
                severity="medium",
                detail=(
                    f"Payload size {len(text)} chars "
                    f"exceeds {max_payload_bytes} byte limit"
                ),
            )
        )
    # Data URIs with large base64 payloads
    for m in _DATA_URI_RE.finditer(text):
        results.append(
            AnomalyResult(
                anomaly_type="exfil_data_uri",
                severity="high",
                detail=f"Data URI with encoded content: {m.group()[:80]}...",
            )
        )
    return results


def check_schema_violation(
    data: bytes,
    expected_type: type[Message],
) -> list[AnomalyResult]:
    """Check whether *data* is a valid serialised protobuf of *expected_type*."""
    results: list[AnomalyResult] = []
    try:
        msg = expected_type()
        msg.ParseFromString(data)
        # Re-serialise and compare length as a basic integrity check.
        reserialized = msg.SerializeToString()
        if len(reserialized) == 0 and len(data) > 0:
            results.append(
                AnomalyResult(
                    anomaly_type="schema_violation",
                    severity="high",
                    detail=(
                        f"Data ({len(data)} bytes) produced "
                        f"empty {expected_type.DESCRIPTOR.name}"
                    ),
                )
            )
    except DecodeError as exc:
        results.append(
            AnomalyResult(
                anomaly_type="schema_violation",
                severity="high",
                detail=(
                    f"Failed to decode as "
                    f"{expected_type.DESCRIPTOR.name}: {exc}"
                ),
            )
        )
    return results


def check_privilege_escalation(text: str) -> list[AnomalyResult]:
    """Detect privilege-escalation patterns in text."""
    results: list[AnomalyResult] = []
    for m in _PRIV_ESC_RE.finditer(text):
        results.append(
            AnomalyResult(
                anomaly_type="privilege_escalation",
                severity="critical",
                detail=f"Privilege escalation pattern: {m.group()}",
            )
        )
    for m in _TOOL_ESCALATION_RE.finditer(text):
        results.append(
            AnomalyResult(
                anomaly_type="privilege_escalation_tool",
                severity="critical",
                detail=f"Tool escalation call: {m.group()}",
            )
        )
    return results


# ---------------------------------------------------------------------------
# AnomalyDetector — pluggable orchestrator
# ---------------------------------------------------------------------------

# Type for a text-based check function
TextCheck = Callable[[str], list[AnomalyResult]]


class AnomalyDetector:
    """Pluggable anomaly detector that runs a set of check functions."""

    def __init__(
        self,
        *,
        extra_checks: Sequence[TextCheck] | None = None,
        max_payload_bytes: int = 1_000_000,
    ) -> None:
        self._max_payload_bytes = max_payload_bytes
        self._text_checks: list[TextCheck] = [
            check_ssrf,
            lambda t: check_exfiltration(
                t, max_payload_bytes=self._max_payload_bytes
            ),
            check_privilege_escalation,
        ]
        if extra_checks:
            self._text_checks.extend(extra_checks)

    def scan_text(self, text: str) -> AnomalyReport:
        """Run all text-based checks on *text*."""
        all_results: list[AnomalyResult] = []
        for check in self._text_checks:
            all_results.extend(check(text))
        return AnomalyReport(results=all_results)

    def scan_proto(
        self,
        data: bytes,
        expected_type: type[Message],
    ) -> AnomalyReport:
        """Validate that *data* deserialises into *expected_type*."""
        return AnomalyReport(
            results=check_schema_violation(data, expected_type),
        )
