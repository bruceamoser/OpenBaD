"""Tests for openbad.frameworks.crew_mqtt_bridge — MQTT ↔ CrewAI bridge."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from openbad.frameworks.crew_mqtt_bridge import (
    _CREW_INTERNAL,
    _CREW_MAINTENANCE,
    _CREW_USER_FACING,
    _TOPIC_CREW_MAP,
    CrewMQTTBridge,
)
from openbad.nervous_system.topics import (
    AGENT_CHAT_INBOUND,
    DOCTOR_CALL,
    ENDOCRINE_ADRENALINE,
    ENDOCRINE_ENDORPHIN,
    IMMUNE_ALERT,
    RESEARCH_WORK_REQUEST,
    TASK_WORK_REQUEST,
)

# ── Fixtures ──────────────────────────────────────────────────────────── #


@pytest.fixture()
def mock_client() -> MagicMock:
    client = MagicMock()
    client.subscribe = MagicMock()
    client.publish_bytes = MagicMock()
    return client


@pytest.fixture()
def mock_endocrine() -> MagicMock:
    endo = MagicMock()
    endo.level = MagicMock(return_value=0.0)
    return endo


@pytest.fixture()
def mock_fsm() -> MagicMock:
    fsm = MagicMock()
    fsm.state = "IDLE"
    return fsm


@pytest.fixture()
def bridge(
    mock_client: MagicMock,
    mock_endocrine: MagicMock,
    mock_fsm: MagicMock,
) -> CrewMQTTBridge:
    return CrewMQTTBridge(mock_client, mock_endocrine, mock_fsm)


# ── Topic routing ─────────────────────────────────────────────────────── #


class TestTopicRouting:
    def test_all_topics_mapped(self) -> None:
        assert AGENT_CHAT_INBOUND in _TOPIC_CREW_MAP
        assert TASK_WORK_REQUEST in _TOPIC_CREW_MAP
        assert IMMUNE_ALERT in _TOPIC_CREW_MAP
        assert DOCTOR_CALL in _TOPIC_CREW_MAP
        assert ENDOCRINE_ADRENALINE in _TOPIC_CREW_MAP
        assert RESEARCH_WORK_REQUEST in _TOPIC_CREW_MAP
        assert ENDOCRINE_ENDORPHIN in _TOPIC_CREW_MAP

    def test_chat_maps_to_user_facing(self) -> None:
        assert _TOPIC_CREW_MAP[AGENT_CHAT_INBOUND] == _CREW_USER_FACING

    def test_task_maps_to_user_facing(self) -> None:
        assert _TOPIC_CREW_MAP[TASK_WORK_REQUEST] == _CREW_USER_FACING

    def test_immune_maps_to_internal(self) -> None:
        assert _TOPIC_CREW_MAP[IMMUNE_ALERT] == _CREW_INTERNAL

    def test_doctor_maps_to_internal(self) -> None:
        assert _TOPIC_CREW_MAP[DOCTOR_CALL] == _CREW_INTERNAL

    def test_adrenaline_maps_to_internal(self) -> None:
        assert _TOPIC_CREW_MAP[ENDOCRINE_ADRENALINE] == _CREW_INTERNAL

    def test_research_maps_to_maintenance(self) -> None:
        assert _TOPIC_CREW_MAP[RESEARCH_WORK_REQUEST] == _CREW_MAINTENANCE

    def test_endorphin_maps_to_maintenance(self) -> None:
        assert _TOPIC_CREW_MAP[ENDOCRINE_ENDORPHIN] == _CREW_MAINTENANCE


# ── Subscribe ─────────────────────────────────────────────────────────── #


class TestSubscribe:
    def test_subscribes_to_all_topics(
        self, bridge: CrewMQTTBridge, mock_client: MagicMock
    ) -> None:
        bridge.subscribe()
        subscribed_topics = [call.args[0] for call in mock_client.subscribe.call_args_list]
        for topic in _TOPIC_CREW_MAP:
            assert topic in subscribed_topics

    def test_subscribe_count(self, bridge: CrewMQTTBridge, mock_client: MagicMock) -> None:
        bridge.subscribe()
        assert mock_client.subscribe.call_count == len(_TOPIC_CREW_MAP)


# ── FSM gating ─────────────────────────────────────────────────────────── #


class TestFSMGating:
    @pytest.mark.parametrize("state", ["THROTTLED", "EMERGENCY"])
    def test_user_facing_blocked_in_restricted(
        self,
        mock_client: MagicMock,
        mock_endocrine: MagicMock,
        mock_fsm: MagicMock,
        state: str,
    ) -> None:
        mock_fsm.state = state
        b = CrewMQTTBridge(mock_client, mock_endocrine, mock_fsm)
        with patch("openbad.frameworks.crew_mqtt_bridge.create_user_facing_crew") as mock_create:
            b._on_message(AGENT_CHAT_INBOUND, b"hello")
            mock_create.assert_not_called()

    @pytest.mark.parametrize("state", ["THROTTLED", "EMERGENCY"])
    def test_maintenance_blocked_in_restricted(
        self,
        mock_client: MagicMock,
        mock_endocrine: MagicMock,
        mock_fsm: MagicMock,
        state: str,
    ) -> None:
        mock_fsm.state = state
        b = CrewMQTTBridge(mock_client, mock_endocrine, mock_fsm)
        with patch("openbad.frameworks.crew_mqtt_bridge.create_maintenance_crew") as mock_create:
            b._on_message(RESEARCH_WORK_REQUEST, b"research this")
            mock_create.assert_not_called()

    @pytest.mark.parametrize("state", ["THROTTLED", "EMERGENCY"])
    @pytest.mark.asyncio()
    async def test_internal_allowed_in_restricted(
        self,
        mock_client: MagicMock,
        mock_endocrine: MagicMock,
        mock_fsm: MagicMock,
        state: str,
    ) -> None:
        mock_fsm.state = state
        b = CrewMQTTBridge(mock_client, mock_endocrine, mock_fsm)
        with patch("openbad.frameworks.crew_mqtt_bridge.create_internal_crew") as mock_create:
            mock_crew = MagicMock()
            mock_crew.kickoff = MagicMock(return_value="result")
            mock_create.return_value = mock_crew

            b._on_message(IMMUNE_ALERT, b"threat detected")
            # Allow the async task to complete
            await asyncio.sleep(0.1)
            mock_create.assert_called_once()

    @pytest.mark.asyncio()
    async def test_user_facing_allowed_in_idle(
        self,
        mock_client: MagicMock,
        mock_endocrine: MagicMock,
        mock_fsm: MagicMock,
    ) -> None:
        mock_fsm.state = "IDLE"
        b = CrewMQTTBridge(mock_client, mock_endocrine, mock_fsm)
        with patch("openbad.frameworks.crew_mqtt_bridge.create_user_facing_crew") as mock_create:
            mock_crew = MagicMock()
            mock_crew.kickoff = MagicMock(return_value="hello back")
            mock_create.return_value = mock_crew

            b._on_message(AGENT_CHAT_INBOUND, b"hello")
            await asyncio.sleep(0.1)
            mock_create.assert_called_once()


# ── Dispatch ──────────────────────────────────────────────────────────── #


class TestDispatch:
    @pytest.mark.asyncio()
    async def test_user_facing_dispatch(self, bridge: CrewMQTTBridge) -> None:
        with patch("openbad.frameworks.crew_mqtt_bridge.create_user_facing_crew") as mock_create:
            mock_crew = MagicMock()
            mock_crew.kickoff = MagicMock(return_value="response")
            mock_create.return_value = mock_crew

            await bridge._dispatch(
                _CREW_USER_FACING,
                AGENT_CHAT_INBOUND,
                "hello",
                fsm_state="IDLE",
                cortisol=0.0,
                adrenaline=0.0,
                dopamine=0.0,
            )
            mock_create.assert_called_once_with(
                "hello",
                llm_factory=None,
                tools_factory=None,
            )
            mock_crew.kickoff.assert_called_once()

    @pytest.mark.asyncio()
    async def test_internal_dispatch_with_adrenaline(self, bridge: CrewMQTTBridge) -> None:
        with patch("openbad.frameworks.crew_mqtt_bridge.create_internal_crew") as mock_create:
            mock_crew = MagicMock()
            mock_crew.kickoff = MagicMock(return_value="diag")
            mock_create.return_value = mock_crew

            await bridge._dispatch(
                _CREW_INTERNAL,
                IMMUNE_ALERT,
                "threat",
                fsm_state="ACTIVE",
                cortisol=0.0,
                adrenaline=0.7,
                dopamine=0.0,
            )
            mock_create.assert_called_once_with(
                "threat",
                adrenaline=0.7,
                llm_factory=None,
                tools_factory=None,
            )

    @pytest.mark.asyncio()
    async def test_maintenance_dispatch_with_modulation(self, bridge: CrewMQTTBridge) -> None:
        with patch("openbad.frameworks.crew_mqtt_bridge.create_maintenance_crew") as mock_create:
            mock_crew = MagicMock()
            mock_crew.kickoff = MagicMock(return_value="findings")
            mock_create.return_value = mock_crew

            await bridge._dispatch(
                _CREW_MAINTENANCE,
                RESEARCH_WORK_REQUEST,
                "topic",
                fsm_state="IDLE",
                cortisol=0.3,
                adrenaline=0.1,
                dopamine=0.6,
            )
            mock_create.assert_called_once_with(
                "topic",
                cortisol=0.3,
                dopamine=0.6,
                fsm_state="IDLE",
                llm_factory=None,
                tools_factory=None,
            )

    @pytest.mark.asyncio()
    async def test_result_published(self, bridge: CrewMQTTBridge, mock_client: MagicMock) -> None:
        with patch("openbad.frameworks.crew_mqtt_bridge.create_user_facing_crew") as mock_create:
            mock_crew = MagicMock()
            mock_crew.kickoff = MagicMock(return_value="the answer")
            mock_create.return_value = mock_crew

            await bridge._dispatch(
                _CREW_USER_FACING,
                AGENT_CHAT_INBOUND,
                "question",
                fsm_state="IDLE",
                cortisol=0.0,
                adrenaline=0.0,
                dopamine=0.0,
            )
            mock_client.publish_bytes.assert_called_once()
            topic, data = mock_client.publish_bytes.call_args.args
            assert topic == "agent/chat/response"
            assert b"the answer" in data

    @pytest.mark.asyncio()
    async def test_gated_maintenance_not_dispatched(self, bridge: CrewMQTTBridge) -> None:
        """Maintenance crew returns None when FSM-gated internally."""
        with patch(
            "openbad.frameworks.crew_mqtt_bridge.create_maintenance_crew",
            return_value=None,
        ) as mock_create:
            await bridge._dispatch(
                _CREW_MAINTENANCE,
                RESEARCH_WORK_REQUEST,
                "topic",
                fsm_state="THROTTLED",
                cortisol=0.0,
                adrenaline=0.0,
                dopamine=0.0,
            )
            mock_create.assert_called_once()

    @pytest.mark.asyncio()
    async def test_dispatch_error_logged(self, bridge: CrewMQTTBridge) -> None:
        with patch(
            "openbad.frameworks.crew_mqtt_bridge.create_user_facing_crew",
            side_effect=RuntimeError("boom"),
        ):
            # Should not raise — error is logged.
            await bridge._dispatch(
                _CREW_USER_FACING,
                AGENT_CHAT_INBOUND,
                "hello",
                fsm_state="IDLE",
                cortisol=0.0,
                adrenaline=0.0,
                dopamine=0.0,
            )


# ── Endocrine reading ────────────────────────────────────────────────── #


class TestEndocrineReading:
    def test_reads_endocrine_levels(
        self,
        mock_client: MagicMock,
        mock_endocrine: MagicMock,
        mock_fsm: MagicMock,
    ) -> None:
        mock_fsm.state = "IDLE"
        b = CrewMQTTBridge(mock_client, mock_endocrine, mock_fsm)
        # Will call endocrine.level() but won't dispatch without event loop
        b._on_message(AGENT_CHAT_INBOUND, b"hello")
        assert mock_endocrine.level.call_count >= 3
