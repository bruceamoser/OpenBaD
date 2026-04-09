"""Tests for the MQTT topic namespace — Issue #3."""

from __future__ import annotations

import re

import pytest

from openbad.nervous_system import topics

# Valid MQTT topic characters (our restricted subset).
_TOPIC_RE = re.compile(r"^[a-z0-9/_{}+#-]+$")

# A topic segment must not be empty (no double slashes).
_NO_DOUBLE_SLASH = re.compile(r"//")


def _all_topic_strings() -> list[str]:
    """Collect every topic constant exported by the module."""
    return list(topics.STATIC_TOPICS + topics.TEMPLATE_TOPICS + topics.WILDCARD_TOPICS)


class TestTopicFormat:
    """Every topic constant must obey the naming conventions."""

    @pytest.mark.parametrize("topic", _all_topic_strings())
    def test_no_leading_slash(self, topic: str) -> None:
        assert not topic.startswith("/"), f"{topic!r} starts with /"

    @pytest.mark.parametrize("topic", _all_topic_strings())
    def test_no_trailing_slash(self, topic: str) -> None:
        assert not topic.endswith("/"), f"{topic!r} ends with /"

    @pytest.mark.parametrize("topic", _all_topic_strings())
    def test_valid_characters(self, topic: str) -> None:
        assert _TOPIC_RE.match(topic), f"{topic!r} contains invalid characters"

    @pytest.mark.parametrize("topic", _all_topic_strings())
    def test_no_double_slashes(self, topic: str) -> None:
        assert not _NO_DOUBLE_SLASH.search(topic), f"{topic!r} has empty segment"

    @pytest.mark.parametrize("topic", _all_topic_strings())
    def test_rooted_under_agent(self, topic: str) -> None:
        assert topic.startswith("agent/"), f"{topic!r} not rooted under agent/"

    @pytest.mark.parametrize("topic", _all_topic_strings())
    def test_lowercase(self, topic: str) -> None:
        # Only check non-placeholder parts
        cleaned = re.sub(r"\{[^}]+}", "", topic)
        assert cleaned == cleaned.lower(), f"{topic!r} contains uppercase"


class TestStaticTopics:
    """Static topics must not contain placeholders or wildcards."""

    @pytest.mark.parametrize("topic", list(topics.STATIC_TOPICS))
    def test_no_placeholders(self, topic: str) -> None:
        assert "{" not in topic and "}" not in topic

    @pytest.mark.parametrize("topic", list(topics.STATIC_TOPICS))
    def test_no_wildcards(self, topic: str) -> None:
        assert "+" not in topic and "#" not in topic


class TestTemplatTopics:
    """Template topics must contain at least one placeholder."""

    @pytest.mark.parametrize("topic", list(topics.TEMPLATE_TOPICS))
    def test_has_placeholder(self, topic: str) -> None:
        assert "{" in topic and "}" in topic


class TestWildcardTopics:
    """Wildcard topics must contain + or #."""

    @pytest.mark.parametrize("topic", list(topics.WILDCARD_TOPICS))
    def test_has_wildcard(self, topic: str) -> None:
        assert "+" in topic or "#" in topic


class TestTopicFor:
    """topic_for() must resolve templates correctly."""

    def test_reflex_trigger(self) -> None:
        result = topics.topic_for(topics.REFLEX_TRIGGER, reflex_id="thermal-throttle")
        assert result == "agent/reflex/thermal-throttle/trigger"

    def test_endocrine(self) -> None:
        result = topics.topic_for(topics.ENDOCRINE, hormone="cortisol")
        assert result == "agent/endocrine/cortisol"

    def test_proprioception_heartbeat(self) -> None:
        result = topics.topic_for(topics.PROPRIOCEPTION_HEARTBEAT, tool_id="fs-write")
        assert result == "agent/proprioception/fs-write/heartbeat"

    def test_missing_key_raises(self) -> None:
        with pytest.raises(KeyError):
            topics.topic_for(topics.REFLEX_TRIGGER)


class TestImportability:
    """All topic constants must be importable from the public path."""

    def test_import_from_module(self) -> None:
        from openbad.nervous_system.topics import (
            COGNITIVE_ESCALATION,
            COGNITIVE_RESULT,
            ENDOCRINE_CORTISOL,
            IMMUNE_ALERT,
            IMMUNE_QUARANTINE,
            MEMORY_LTM_CONSOLIDATE,
            MEMORY_STM_WRITE,
            PROPRIOCEPTION_HEARTBEAT,
            REFLEX_STATE,
            REFLEX_TRIGGER,
            SENSORY_AUDIO,
            SENSORY_VISION,
            SLEEP,
            TELEMETRY_CPU,
            TELEMETRY_DISK,
            TELEMETRY_MEMORY,
            TELEMETRY_TOKENS,
        )

        # Smoke check — they're all strings
        for name in (
            COGNITIVE_ESCALATION,
            COGNITIVE_RESULT,
            ENDOCRINE_CORTISOL,
            IMMUNE_ALERT,
            IMMUNE_QUARANTINE,
            MEMORY_LTM_CONSOLIDATE,
            MEMORY_STM_WRITE,
            PROPRIOCEPTION_HEARTBEAT,
            REFLEX_STATE,
            REFLEX_TRIGGER,
            SENSORY_AUDIO,
            SENSORY_VISION,
            SLEEP,
            TELEMETRY_CPU,
            TELEMETRY_DISK,
            TELEMETRY_MEMORY,
            TELEMETRY_TOKENS,
        ):
            assert isinstance(name, str)
