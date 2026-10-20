"""Tests for #183 TUI event log and command bar behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from openbad.nervous_system.schemas.endocrine_pb2 import EndocrineEvent
from openbad.tui.app import OpenBaDApp
from openbad.tui.panels import CommandBar, EventLogPanel


class TestPanelAndBarTypes:
    def test_event_log_panel_instantiates(self):
        assert isinstance(EventLogPanel(), EventLogPanel)

    def test_command_bar_instantiates(self):
        assert isinstance(CommandBar(), CommandBar)


class TestAppEventHelpers:
    def test_payload_summary_for_proto(self):
        payload = EndocrineEvent(hormone="cortisol", level=0.4)
        text = OpenBaDApp._payload_summary(payload)
        assert "hormone" in text
        assert "level" in text

    def test_payload_summary_for_plain_object(self):
        text = OpenBaDApp._payload_summary("plain")
        assert text == "plain"

    def test_focus_command_action(self):
        app = OpenBaDApp()
        bar = MagicMock()

        def _query_one(selector, _type=None):
            assert selector == "#command-bar"
            return bar

        app.query_one = _query_one  # type: ignore[method-assign]
        app.action_focus_command()
        bar.focus.assert_called_once()

    def test_log_event_writes_to_panel(self):
        app = OpenBaDApp()
        panel = MagicMock()

        def _query_one(selector, _type=None):
            assert selector == "#event-log-panel"
            return panel

        app.query_one = _query_one  # type: ignore[method-assign]
        app._log_event("hello")
        panel.write.assert_called_once_with("hello")


class TestCommandBarHandling:
    def _setup_app_with_mocks(self) -> tuple[OpenBaDApp, MagicMock]:
        app = OpenBaDApp()
        panel = MagicMock()
        app._log_event = MagicMock()  # type: ignore[method-assign]
        app.action_reconnect = MagicMock()  # type: ignore[method-assign]
        app.action_toggle_dark = MagicMock()  # type: ignore[method-assign]
        app.action_quit = MagicMock()  # type: ignore[method-assign]

        def _query_one(selector, _type=None):
            assert selector == "#event-log-panel"
            return panel

        app.query_one = _query_one  # type: ignore[method-assign]
        return app, panel

    def test_help_command(self):
        app, _ = self._setup_app_with_mocks()
        event = SimpleNamespace(
            input=SimpleNamespace(id="command-bar", value="help"),
            value="help",
        )
        app.on_input_submitted(event)
        assert app._log_event.call_count >= 2

    def test_clear_command(self):
        app, panel = self._setup_app_with_mocks()
        event = SimpleNamespace(
            input=SimpleNamespace(id="command-bar", value="clear"),
            value="clear",
        )
        app.on_input_submitted(event)
        panel.clear.assert_called_once()

    def test_reconnect_command(self):
        app, _ = self._setup_app_with_mocks()
        event = SimpleNamespace(
            input=SimpleNamespace(id="command-bar", value="reconnect"),
            value="reconnect",
        )
        app.on_input_submitted(event)
        app.action_reconnect.assert_called_once()

    def test_dark_command(self):
        app, _ = self._setup_app_with_mocks()
        event = SimpleNamespace(
            input=SimpleNamespace(id="command-bar", value="dark"),
            value="dark",
        )
        app.on_input_submitted(event)
        app.action_toggle_dark.assert_called_once()

    def test_quit_command(self):
        app, _ = self._setup_app_with_mocks()
        event = SimpleNamespace(
            input=SimpleNamespace(id="command-bar", value="quit"),
            value="quit",
        )
        app.on_input_submitted(event)
        app.action_quit.assert_called_once()

    def test_unknown_command(self):
        app, _ = self._setup_app_with_mocks()
        event = SimpleNamespace(
            input=SimpleNamespace(id="command-bar", value="boom"),
            value="boom",
        )
        app.on_input_submitted(event)
        assert app._log_event.call_count >= 2

    def test_ignores_other_inputs(self):
        app, _ = self._setup_app_with_mocks()
        event = SimpleNamespace(input=SimpleNamespace(id="other", value="help"), value="help")
        app.on_input_submitted(event)
        app._log_event.assert_not_called()
