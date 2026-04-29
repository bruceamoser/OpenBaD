"""Tests for peripheral session listing and entity tools."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from openbad.wui.chat_pipeline import list_peripheral_sessions


@pytest.fixture
def mock_db(tmp_path):
    """Create a temporary SQLite DB with session_messages table."""
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE session_messages (
            message_id INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL NOT NULL,
            metadata_json TEXT
        )
    """)
    return conn


class TestListPeripheralSessions:

    def test_returns_peripheral_sessions(self, mock_db) -> None:
        mock_db.execute(
            "INSERT INTO session_messages VALUES"
            " (1, 'peripheral:telegram:42', 'user', 'hi', 1.0, '{}')",
        )
        mock_db.execute(
            "INSERT INTO session_messages VALUES"
            " (2, 'peripheral:telegram:42', 'assistant', 'hello', 2.0, '{}')",
        )
        mock_db.execute(
            "INSERT INTO session_messages VALUES"
            " (3, 'peripheral:discord:99', 'user', 'yo', 3.0, '{}')",
        )
        mock_db.execute(
            "INSERT INTO session_messages VALUES (4, 'chat-main', 'user', 'test', 4.0, '{}')",
        )
        mock_db.commit()

        with patch(
            "openbad.wui.chat_pipeline._get_state_conn",
            return_value=mock_db,
        ):
            result = list_peripheral_sessions()

        assert len(result) == 2
        assert result[0]["session_id"] == "peripheral:discord:99"
        assert result[0]["label"] == "Discord (99)"
        assert result[1]["session_id"] == "peripheral:telegram:42"
        assert result[1]["label"] == "Telegram (42)"

    def test_returns_empty_when_no_peripheral_sessions(self, mock_db) -> None:
        mock_db.execute(
            "INSERT INTO session_messages VALUES (1, 'chat-main', 'user', 'hi', 1.0, '{}')",
        )
        mock_db.commit()

        with patch(
            "openbad.wui.chat_pipeline._get_state_conn",
            return_value=mock_db,
        ):
            result = list_peripheral_sessions()

        assert result == []

    def test_returns_empty_on_error(self) -> None:
        with patch(
            "openbad.wui.chat_pipeline._get_state_conn",
            side_effect=Exception("db error"),
        ):
            result = list_peripheral_sessions()

        assert result == []


class TestEntityTools:

    @pytest.mark.asyncio
    async def test_get_entity_info(self) -> None:
        from openbad.skills.server import get_entity_info

        mock_user = MagicMock()
        mock_user.name = "Bruce"
        mock_user.preferred_name = ""
        mock_user.communication_style = "casual"
        mock_user.expertise_domains = ["python"]
        mock_user.interaction_history_summary = ""
        mock_user.worldview = []
        mock_user.interests = ["AI"]
        mock_user.pet_peeves = []
        mock_user.preferred_feedback_style = "balanced"
        mock_user.active_projects = []
        mock_user.timezone = ""
        mock_user.work_hours = (9, 17)

        mock_asst = MagicMock()
        mock_asst.name = "Sven"
        mock_asst.persona_summary = "Test bot"
        mock_asst.learning_focus = []
        mock_asst.worldview = []
        mock_asst.boundaries = []
        mock_asst.opinions = {}
        mock_asst.vocabulary = {}
        mock_asst.influences = []
        mock_asst.anti_patterns = []
        mock_asst.current_focus = []
        mock_asst.openness = 0.7
        mock_asst.conscientiousness = 0.8
        mock_asst.extraversion = 0.5
        mock_asst.agreeableness = 0.4
        mock_asst.stability = 0.6

        mock_persist = MagicMock()
        mock_persist.user = mock_user
        mock_persist.assistant = mock_asst

        with patch(
            "openbad.skills.server._get_identity_persistence",
            return_value=mock_persist,
        ):
            result = await get_entity_info()

        assert "Bruce" in result
        assert "Sven" in result
        assert "OCEAN" in result

    @pytest.mark.asyncio
    async def test_update_user_entity(self) -> None:
        from openbad.skills.server import update_user_entity

        mock_persist = MagicMock()
        mock_persist.update_user = MagicMock()

        with patch(
            "openbad.skills.server._get_identity_persistence",
            return_value=mock_persist,
        ):
            result = await update_user_entity(
                json.dumps({"name": "Bruce", "interests": ["AI", "Linux"]}),
            )

        assert "name" in result
        assert "interests" in result
        mock_persist.update_user.assert_called_once_with(
            name="Bruce", interests=["AI", "Linux"],
        )

    @pytest.mark.asyncio
    async def test_update_user_entity_invalid_json(self) -> None:
        from openbad.skills.server import update_user_entity

        with patch(
            "openbad.skills.server._get_identity_persistence",
            return_value=MagicMock(),
        ):
            result = await update_user_entity("not json")
        assert "Invalid JSON" in result

    @pytest.mark.asyncio
    async def test_update_assistant_entity(self) -> None:
        import openbad.skills.server as skills_mod
        from openbad.skills.server import update_assistant_entity

        mock_persist = MagicMock()
        mock_persist.update_assistant = MagicMock()
        mock_persist.assistant = MagicMock()
        mock_modulator = MagicMock()

        with patch(
            "openbad.skills.server._get_identity_persistence",
            return_value=mock_persist,
        ):
            old_mod = skills_mod._personality_modulator
            try:
                skills_mod._personality_modulator = mock_modulator
                result = await update_assistant_entity(
                    json.dumps({"persona_summary": "A helpful agent"}),
                )
            finally:
                skills_mod._personality_modulator = old_mod

        assert "persona_summary" in result
        mock_persist.update_assistant.assert_called_once_with(
            persona_summary="A helpful agent",
        )
        mock_modulator.update.assert_called_once()
