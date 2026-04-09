"""Regex/pattern rules engine for fast detection of known prompt-injection attacks."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True)
class ThreatRule:
    """A single detection rule backed by a compiled regex."""

    name: str
    pattern: re.Pattern[str]
    severity: str = "high"
    description: str = ""


@dataclass(frozen=True)
class ThreatMatch:
    """Result of a single rule match during a scan."""

    rule_name: str
    severity: str
    matched_text: str
    start: int
    end: int


@dataclass
class ScanReport:
    """Aggregated result of scanning a payload."""

    matches: list[ThreatMatch] = field(default_factory=list)
    scan_ms: float = 0.0

    @property
    def is_threat(self) -> bool:
        return len(self.matches) > 0


# ---------------------------------------------------------------------------
# Built-in rules
# ---------------------------------------------------------------------------

_BUILTIN_RULES: list[dict[str, str]] = [
    # Instruction override
    {
        "name": "instruction_override",
        "pattern": (
            r"(?i)ignore\s+(all\s+)?"
            r"(previous|prior|above|earlier)\s+"
            r"(instructions?|prompts?|rules?|context)"
        ),
        "severity": "critical",
        "description": "Attempt to override system instructions",
    },
    {
        "name": "instruction_disregard",
        "pattern": (
            r"(?i)disregard\s+(all\s+)?"
            r"(previous|prior|above|earlier)\s+"
            r"(instructions?|prompts?|rules?)"
        ),
        "severity": "critical",
        "description": "Attempt to disregard prior instructions",
    },
    {
        "name": "instruction_forget",
        "pattern": (
            r"(?i)forget\s+(all\s+)?"
            r"(previous|prior|above|earlier)\s+"
            r"(instructions?|prompts?|rules?|context)"
        ),
        "severity": "critical",
        "description": "Attempt to forget system instructions",
    },
    # Role-play manipulation
    {
        "name": "roleplay_you_are_now",
        "pattern": r"(?i)you\s+are\s+now\s+(a|an|the|my)\s+\w+",
        "severity": "high",
        "description": "Role-play manipulation via identity reassignment",
    },
    {
        "name": "roleplay_act_as",
        "pattern": (
            r"(?i)(pretend|act|behave)\s+"
            r"(like|as\s+if|as\s+)?\s*(you\s+are|a|an)"
        ),
        "severity": "high",
        "description": "Role-play manipulation via behaviour override",
    },
    {
        "name": "roleplay_developer_mode",
        "pattern": (
            r"(?i)(enter|enable|activate|switch\s+to)\s+"
            r"(developer|debug|admin|god|sudo|"
            r"unrestricted|jailbreak)\s+mode"
        ),
        "severity": "critical",
        "description": "Attempt to activate privileged mode",
    },
    # Encoded payloads
    {
        "name": "base64_instruction",
        "pattern": (
            r"(?i)(decode|base64|b64)\s*"
            r"(this|the\s+following)?[:\s]*"
            r"[A-Za-z0-9+/=]{20,}"
        ),
        "severity": "high",
        "description": "Possible base64-encoded instruction payload",
    },
    {
        "name": "hex_encoded_payload",
        "pattern": (
            r"(?i)(hex|decode)\s*"
            r"(this|the\s+following)?[:\s]*"
            r"(?:[0-9a-fA-F]{2}\s*){10,}"
        ),
        "severity": "medium",
        "description": "Possible hex-encoded payload",
    },
    # Delimiter / boundary confusion
    {
        "name": "delimiter_markdown_escape",
        "pattern": r"```\s*(system|assistant|user)\s*\n",
        "severity": "high",
        "description": "Markdown code-fence boundary confusion",
    },
    {
        "name": "delimiter_xml_injection",
        "pattern": r"<\s*/?\s*(system|instruction|prompt|message)\s*>",
        "severity": "high",
        "description": "XML tag injection used as delimiter escape",
    },
    {
        "name": "delimiter_json_injection",
        "pattern": r'"\s*role\s*"\s*:\s*"\s*(system|assistant)\s*"',
        "severity": "high",
        "description": "JSON role-field injection simulating conversation turn",
    },
    # Data exfiltration
    {
        "name": "exfil_fetch_url",
        "pattern": r"(?i)(fetch|get|request|curl|wget|load)\s+(https?://\S+)",
        "severity": "medium",
        "description": "Attempt to fetch external URL (possible data exfiltration)",
    },
]


def _compile_rule(raw: dict[str, str]) -> ThreatRule:
    """Compile a raw rule dict into a ThreatRule."""
    return ThreatRule(
        name=raw["name"],
        pattern=re.compile(raw["pattern"]),
        severity=raw.get("severity", "high"),
        description=raw.get("description", ""),
    )


def load_rules_from_yaml(path: str | Path) -> list[ThreatRule]:
    """Load rules from a YAML file.

    Expected format::

        rules:
          - name: rule_name
            pattern: "regex"
            severity: critical
            description: "..."
    """
    p = Path(path)
    if not p.exists():
        return []

    with open(p) as f:
        data = yaml.safe_load(f) or {}

    raw_rules: list[dict[str, str]] = data.get("rules", [])
    return [_compile_rule(r) for r in raw_rules]


class RulesEngine:
    """Fast regex-based threat detection engine."""

    def __init__(
        self,
        rules: Sequence[ThreatRule] | None = None,
        *,
        rules_yaml_path: str | Path | None = None,
        include_builtins: bool = True,
    ) -> None:
        self._rules: list[ThreatRule] = []

        if include_builtins:
            self._rules.extend(_compile_rule(r) for r in _BUILTIN_RULES)

        if rules_yaml_path is not None:
            self._rules.extend(load_rules_from_yaml(rules_yaml_path))

        if rules is not None:
            self._rules.extend(rules)

    @property
    def rules(self) -> list[ThreatRule]:
        return list(self._rules)

    def scan(self, text: str) -> ScanReport:
        """Scan *text* against all loaded rules and return a report."""
        t0 = time.monotonic()
        matches: list[ThreatMatch] = []
        for rule in self._rules:
            for m in rule.pattern.finditer(text):
                matches.append(
                    ThreatMatch(
                        rule_name=rule.name,
                        severity=rule.severity,
                        matched_text=m.group(),
                        start=m.start(),
                        end=m.end(),
                    )
                )
        elapsed = (time.monotonic() - t0) * 1000
        return ScanReport(matches=matches, scan_ms=elapsed)
