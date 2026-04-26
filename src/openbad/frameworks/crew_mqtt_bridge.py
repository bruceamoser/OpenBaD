"""MQTT ↔ CrewAI activation bridge.

Subscribes to MQTT topics and dispatches to the appropriate crew,
applying endocrine modulation and FSM gating before each dispatch.

Topic routing
-------------
- ``agent/chat/inbound`` → User-Facing Crew
- ``agent/tasks/work`` → User-Facing Crew (Task path)
- ``agent/immune/alert`` → Internal Crew
- ``agent/doctor/call`` → Internal Crew
- ``agent/endocrine/adrenaline`` → Internal Crew (pain/emergency)
- ``agent/research/work`` → Maintenance Crew
- ``agent/endocrine/endorphin`` → Maintenance Crew (sleep trigger)

FSM gating
----------
In THROTTLED / EMERGENCY states only Internal Crew dispatches are allowed.

Public API
----------
``CrewMQTTBridge(client, endocrine, fsm)``
    Instantiate and call ``subscribe()`` to register MQTT handlers.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from openbad.endocrine.controller import EndocrineController
from openbad.frameworks.crews.internal import create_internal_crew
from openbad.frameworks.crews.maintenance import create_maintenance_crew
from openbad.frameworks.crews.user_facing import create_user_facing_crew
from openbad.nervous_system.client import NervousSystemClient
from openbad.nervous_system.topics import (
    AGENT_CHAT_INBOUND,
    AGENT_CHAT_RESPONSE,
    DOCTOR_CALL,
    ENDOCRINE_ADRENALINE,
    ENDOCRINE_ENDORPHIN,
    IMMUNE_ALERT,
    RESEARCH_WORK_REQUEST,
    TASK_WORK_REQUEST,
)
from openbad.reflex_arc.fsm import AgentFSM

log = logging.getLogger(__name__)

# FSM states where only internal crew is allowed.
_RESTRICTED_STATES: frozenset[str] = frozenset({"THROTTLED", "EMERGENCY"})

# ── Topic → Crew mapping ─────────────────────────────────────────────── #

_CREW_USER_FACING = "user_facing"
_CREW_INTERNAL = "internal"
_CREW_MAINTENANCE = "maintenance"

_TOPIC_CREW_MAP: dict[str, str] = {
    AGENT_CHAT_INBOUND: _CREW_USER_FACING,
    TASK_WORK_REQUEST: _CREW_USER_FACING,
    IMMUNE_ALERT: _CREW_INTERNAL,
    DOCTOR_CALL: _CREW_INTERNAL,
    ENDOCRINE_ADRENALINE: _CREW_INTERNAL,
    RESEARCH_WORK_REQUEST: _CREW_MAINTENANCE,
    ENDOCRINE_ENDORPHIN: _CREW_MAINTENANCE,
}

# Response topics per crew type.
_RESPONSE_TOPICS: dict[str, str] = {
    _CREW_USER_FACING: AGENT_CHAT_RESPONSE,
    _CREW_INTERNAL: "system/health/response",
    _CREW_MAINTENANCE: "agent/maintenance/response",
}


class CrewMQTTBridge:
    """Bridge between MQTT topics and CrewAI crews."""

    def __init__(
        self,
        client: NervousSystemClient,
        endocrine: EndocrineController,
        fsm: AgentFSM,
        *,
        llm_factory: Any | None = None,
        tools_factory: Any | None = None,
    ) -> None:
        self._client = client
        self._endocrine = endocrine
        self._fsm = fsm
        self._llm_factory = llm_factory
        self._tools_factory = tools_factory
        self._pending: set[asyncio.Task[None]] = set()

    # ── Public ────────────────────────────────────────────────────── #

    def subscribe(self) -> None:
        """Subscribe to all activation topics."""
        for topic in _TOPIC_CREW_MAP:
            self._client.subscribe(topic, bytes, self._on_message)
            log.info("CrewMQTTBridge subscribed to %s", topic)

    # ── Message handler ───────────────────────────────────────────── #

    def _on_message(self, topic: str, payload: bytes) -> None:
        """Handle an incoming MQTT message (called on MQTT thread)."""
        crew_type = _TOPIC_CREW_MAP.get(topic)
        if crew_type is None:
            log.warning("Unexpected topic: %s", topic)
            return

        # FSM gating: in restricted states only internal crew allowed.
        fsm_state = self._fsm.state
        if fsm_state.upper() in _RESTRICTED_STATES and crew_type != _CREW_INTERNAL:
            log.info(
                "Crew %s blocked by FSM state %s on topic %s",
                crew_type,
                fsm_state,
                topic,
            )
            return

        # Decode payload.
        try:
            message = payload.decode("utf-8", errors="replace")
        except Exception:
            message = payload.hex()

        # Read endocrine state.
        cortisol = self._endocrine.level("cortisol")
        adrenaline = self._endocrine.level("adrenaline")
        dopamine = self._endocrine.level("dopamine")

        # Dispatch asynchronously so we don't block the MQTT loop.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            task = loop.create_task(
                self._dispatch(
                    crew_type,
                    topic,
                    message,
                    fsm_state=fsm_state,
                    cortisol=cortisol,
                    adrenaline=adrenaline,
                    dopamine=dopamine,
                )
            )
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)
        else:
            log.debug(
                "No running event loop; dispatching %s synchronously",
                crew_type,
            )

    # ── Dispatch ──────────────────────────────────────────────────── #

    async def _dispatch(
        self,
        crew_type: str,
        topic: str,
        message: str,
        *,
        fsm_state: str,
        cortisol: float,
        adrenaline: float,
        dopamine: float,
    ) -> None:
        """Build and kick off the appropriate crew."""
        log.info(
            "Dispatching %s crew (topic=%s, fsm=%s, cortisol=%.2f, "
            "adrenaline=%.2f, dopamine=%.2f)",
            crew_type,
            topic,
            fsm_state,
            cortisol,
            adrenaline,
            dopamine,
        )

        crew = None
        try:
            if crew_type == _CREW_USER_FACING:
                crew = create_user_facing_crew(
                    message,
                    llm_factory=self._llm_factory,
                    tools_factory=self._tools_factory,
                )
            elif crew_type == _CREW_INTERNAL:
                crew = create_internal_crew(
                    message,
                    adrenaline=adrenaline,
                    llm_factory=self._llm_factory,
                    tools_factory=self._tools_factory,
                )
            elif crew_type == _CREW_MAINTENANCE:
                crew = create_maintenance_crew(
                    message,
                    cortisol=cortisol,
                    dopamine=dopamine,
                    fsm_state=fsm_state,
                    llm_factory=self._llm_factory,
                    tools_factory=self._tools_factory,
                )

            if crew is None:
                log.info("Crew %s gated; skipping dispatch", crew_type)
                return

            result = crew.kickoff()

            # Publish result back.
            response_topic = _RESPONSE_TOPICS.get(crew_type)
            if response_topic:
                result_text = str(result) if result else ""
                self._client.publish_bytes(response_topic, result_text.encode("utf-8"))

        except Exception:
            log.exception("Error dispatching %s crew on topic %s", crew_type, topic)
