"""LangChain callback handlers for OpenBaD endocrine, immune, and telemetry integration.

Three handlers bridge OpenBaD's biological systems into the LangChain
execution lifecycle:

``EndocrineCallbackHandler``
    Publishes hormone signals on LLM errors, tool failures, and
    records token usage on every completion.

``ImmuneScanCallbackHandler``
    Scans prompts and tool inputs through the immune rules engine
    before they reach the LLM/tool.  Raises ``ImmuneThreatError``
    on detected threats.

``MQTTTelemetryCallbackHandler``
    Publishes LLM call timing metrics to MQTT.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from openbad.endocrine.controller import EndocrineController
from openbad.endocrine.hooks.adrenaline import AdrenalineEvent, AdrenalineHooks
from openbad.endocrine.hooks.cortisol import CortisolEvent, CortisolHooks
from openbad.immune_system.rules_engine import RulesEngine
from openbad.nervous_system import topics
from openbad.nervous_system.client import NervousSystemClient
from openbad.usage_recorder import record_usage_event

log = logging.getLogger(__name__)


class ImmuneThreatError(Exception):
    """Raised when an immune scan detects a threat in LLM/tool input."""

    def __init__(self, threat_type: str, severity: str, detail: str = "") -> None:
        self.threat_type = threat_type
        self.severity = severity
        self.detail = detail
        super().__init__(f"Immune threat ({severity}): {threat_type} — {detail}")


# ──────────────────────────────────────────────────────────────────────── #
# Endocrine Callback Handler
# ──────────────────────────────────────────────────────────────────────── #


class EndocrineCallbackHandler(BaseCallbackHandler):
    """Publish endocrine signals on LLM/tool events and record token usage.

    Parameters
    ----------
    controller:
        The shared :class:`EndocrineController`.
    mqtt:
        Optional MQTT client for publishing hormone events.  If ``None``
        the handler only triggers the controller (no MQTT).
    system:
        System label for usage recording (e.g. ``"chat"``).
    """

    def __init__(
        self,
        controller: EndocrineController,
        mqtt: NervousSystemClient | None = None,
        *,
        system: str = "langchain",
    ) -> None:
        super().__init__()
        self._controller = controller
        self._mqtt = mqtt
        self._system = system
        self._adrenaline = AdrenalineHooks(controller)
        self._cortisol = CortisolHooks(controller)
        self._tool_error_counts: dict[str, int] = {}

    # -- LLM events ---------------------------------------------------- #

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Provider failure → adrenaline spike + cortisol bump."""
        self._adrenaline.fire(
            AdrenalineEvent(
                source="langchain_callback",
                reason=f"LLM error: {error!r}",
                intensity=1.5,
            ),
        )
        self._cortisol.fire(
            CortisolEvent(
                source="langchain_callback",
                reason=f"LLM error: {error!r}",
            ),
        )
        self._publish_hormone("adrenaline", f"LLM error: {error!r}")
        log.warning("EndocrineCallback: LLM error → adrenaline + cortisol")

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Record token usage and publish cognitive telemetry."""
        for gen_list in response.generations:
            for gen in gen_list:
                info = gen.generation_info or {}
                provider = info.get("provider", "unknown")
                model_id = info.get("model_id", "unknown")
                tokens = info.get("tokens_used", 0)
                if tokens:
                    record_usage_event(
                        provider=provider,
                        model=model_id,
                        system=self._system,
                        tokens=tokens,
                    )
        self._publish_telemetry(response)

    # -- Tool events --------------------------------------------------- #

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Log tool failure; cortisol bump if repeated."""
        tool_name = kwargs.get("name", "unknown")
        self._tool_error_counts[tool_name] = self._tool_error_counts.get(tool_name, 0) + 1
        count = self._tool_error_counts[tool_name]
        self._cortisol.on_repeated_failure(failure_count=count)
        self._publish_hormone("cortisol", f"Tool error ({tool_name}): {error!r}")
        log.warning(
            "EndocrineCallback: tool %s error #%d → cortisol",
            tool_name,
            count,
        )

    # -- Chain events -------------------------------------------------- #

    def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Publish chain success metric to MQTT."""
        if self._mqtt is None:
            return
        payload = json.dumps(
            {"event": "chain_end", "status": "success"},
        ).encode()
        self._mqtt.publish_bytes(topics.COGNITIVE_RESPONSE, payload)

    # -- Helpers ------------------------------------------------------- #

    def _publish_hormone(self, hormone: str, reason: str) -> None:
        if self._mqtt is None:
            return
        topic = getattr(topics, f"ENDOCRINE_{hormone.upper()}", None)
        if topic is None:
            return
        payload = json.dumps(
            {"trigger": "langchain_callback", "hormone": hormone, "reason": reason},
        ).encode()
        self._mqtt.publish_bytes(topic, payload)

    def _publish_telemetry(self, response: LLMResult) -> None:
        if self._mqtt is None:
            return
        total_tokens = 0
        for gen_list in response.generations:
            for gen in gen_list:
                info = gen.generation_info or {}
                total_tokens += info.get("tokens_used", 0)
        payload = json.dumps(
            {"event": "llm_end", "tokens": total_tokens},
        ).encode()
        self._mqtt.publish_bytes(topics.TELEMETRY_TOKENS, payload)


# ──────────────────────────────────────────────────────────────────────── #
# Immune Scan Callback Handler
# ──────────────────────────────────────────────────────────────────────── #


class ImmuneScanCallbackHandler(BaseCallbackHandler):
    """Scan prompts and tool inputs through the immune rules engine.

    Raises :class:`ImmuneThreatError` when a scan detects a threat,
    blocking execution before the LLM or tool is invoked.

    Parameters
    ----------
    rules_engine:
        Shared :class:`RulesEngine` instance.
    raise_on_threat:
        If ``True`` (default), raises on threats.  Set to ``False``
        to log-only mode.
    """

    raise_exceptions = True  # LangChain must propagate our exceptions

    def __init__(
        self,
        rules_engine: RulesEngine,
        *,
        raise_on_threat: bool = True,
    ) -> None:
        super().__init__()
        self._rules = rules_engine
        self._raise = raise_on_threat

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Scan all prompt strings before LLM invocation."""
        for prompt in prompts:
            report = self._rules.scan(prompt)
            if report.is_threat:
                match = report.matches[0]
                log.warning(
                    "ImmuneCallback: threat in LLM prompt — %s (%s)",
                    match.rule_name,
                    match.severity,
                )
                if self._raise:
                    raise ImmuneThreatError(
                        threat_type=match.rule_name,
                        severity=match.severity,
                        detail=match.matched_text,
                    )

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Scan tool input before execution."""
        report = self._rules.scan(input_str)
        if report.is_threat:
            match = report.matches[0]
            log.warning(
                "ImmuneCallback: threat in tool input — %s (%s)",
                match.rule_name,
                match.severity,
            )
            if self._raise:
                raise ImmuneThreatError(
                    threat_type=match.rule_name,
                    severity=match.severity,
                    detail=match.matched_text,
                )


# ──────────────────────────────────────────────────────────────────────── #
# MQTT Telemetry Callback Handler
# ──────────────────────────────────────────────────────────────────────── #


class MQTTTelemetryCallbackHandler(BaseCallbackHandler):
    """Publish LLM call timing metrics to MQTT.

    Parameters
    ----------
    mqtt:
        MQTT client for publishing telemetry.
    """

    def __init__(self, mqtt: NervousSystemClient) -> None:
        super().__init__()
        self._mqtt = mqtt
        self._start_times: dict[str, float] = {}

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Record wall-clock start time for the LLM call."""
        key = str(run_id) if run_id else "default"
        self._start_times[key] = time.monotonic()

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Publish elapsed time to MQTT."""
        key = str(run_id) if run_id else "default"
        start = self._start_times.pop(key, None)
        elapsed_ms = (time.monotonic() - start) * 1000 if start else 0.0
        payload = json.dumps(
            {"event": "llm_latency", "elapsed_ms": round(elapsed_ms, 2)},
        ).encode()
        self._mqtt.publish_bytes(topics.TELEMETRY_TOKENS, payload)
