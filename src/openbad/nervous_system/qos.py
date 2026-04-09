"""QoS levels and message retention policies for the MQTT nervous system.

Defines per-topic QoS assignments and retained-message policies so that
every publish/subscribe operation uses the correct delivery guarantee
without callers needing to specify QoS manually.

QoS Policy
==========

=========  ===========  ==============================================
QoS Level  Guarantee    Topics
=========  ===========  ==============================================
0          Fire & forget  ``agent/telemetry/*`` (high-frequency metrics)
1          At-least-once  ``agent/reflex/*/trigger``, ``agent/cognitive/*``,
                          ``agent/immune/*``, ``agent/proprioception/*``
2          Exactly-once   ``agent/endocrine/*``, ``agent/memory/*``,
                          ``agent/sleep/*``
=========  ===========  ==============================================

Retention Policy
================

Retained messages are enabled for "state" topics so that late-joining
subscribers get the latest snapshot immediately:

- ``agent/reflex/state``
- ``agent/telemetry/*``

Usage::

    from openbad.nervous_system.qos import qos_for, should_retain

    qos = qos_for("agent/telemetry/cpu")     # → 0
    retain = should_retain("agent/reflex/state")  # → True
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# QoS rules: each entry is (regex_pattern, qos_level).
# Evaluated in order; first match wins.
# ---------------------------------------------------------------------------
_QOS_RULES: list[tuple[re.Pattern[str], int]] = [
    # QoS 0 — fire-and-forget (high-frequency telemetry)
    (re.compile(r"^agent/telemetry(/|$)"), 0),
    # QoS 2 — exactly-once (state transitions)
    (re.compile(r"^agent/endocrine(/|$)"), 2),
    (re.compile(r"^agent/memory(/|$)"), 2),
    (re.compile(r"^agent/sleep(/|$)"), 2),
    # QoS 1 — at-least-once (critical operational messages)
    (re.compile(r"^agent/reflex(/|$)"), 1),
    (re.compile(r"^agent/cognitive(/|$)"), 1),
    (re.compile(r"^agent/immune(/|$)"), 1),
    (re.compile(r"^agent/proprioception(/|$)"), 1),
    (re.compile(r"^agent/sensory(/|$)"), 1),
]

# Default QoS for topics that don't match any rule
_DEFAULT_QOS = 1

# ---------------------------------------------------------------------------
# Retention rules: topics matching these patterns get retain=True.
# ---------------------------------------------------------------------------
_RETAIN_RULES: list[re.Pattern[str]] = [
    re.compile(r"^agent/reflex/state$"),
    re.compile(r"^agent/telemetry/"),
]


def qos_for(topic: str) -> int:
    """Return the QoS level for the given topic.

    >>> qos_for("agent/telemetry/cpu")
    0
    >>> qos_for("agent/immune/alert")
    1
    >>> qos_for("agent/endocrine/cortisol")
    2
    """
    for pattern, qos in _QOS_RULES:
        if pattern.search(topic):
            return qos
    return _DEFAULT_QOS


def should_retain(topic: str) -> bool:
    """Return True if the topic should use MQTT retained messages.

    >>> should_retain("agent/reflex/state")
    True
    >>> should_retain("agent/immune/alert")
    False
    """
    return any(pattern.search(topic) for pattern in _RETAIN_RULES)
