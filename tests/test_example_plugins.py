"""Tests for example observation plugin templates."""

from __future__ import annotations

import pytest

from openbad.active_inference.plugin_interface import ObservationPlugin, ObservationResult
from openbad.plugins.observations.examples.browser_history import BrowserHistoryPlugin
from openbad.plugins.observations.examples.calendar_google import GoogleCalendarPlugin
from openbad.plugins.observations.examples.email_gmail import GmailObservationPlugin
from openbad.plugins.observations.examples.journal_filesystem import (
    JournalFilesystemPlugin,
)

ALL_PLUGINS = [
    GmailObservationPlugin,
    GoogleCalendarPlugin,
    BrowserHistoryPlugin,
    JournalFilesystemPlugin,
]


class TestABCCompliance:
    @pytest.mark.parametrize("cls", ALL_PLUGINS, ids=lambda c: c.__name__)
    def test_is_observation_plugin(self, cls: type) -> None:
        assert issubclass(cls, ObservationPlugin)

    @pytest.mark.parametrize("cls", ALL_PLUGINS, ids=lambda c: c.__name__)
    def test_has_source_id(self, cls: type) -> None:
        plugin = cls()
        assert isinstance(plugin.source_id, str)
        assert len(plugin.source_id) > 0

    @pytest.mark.parametrize("cls", ALL_PLUGINS, ids=lambda c: c.__name__)
    def test_has_poll_interval(self, cls: type) -> None:
        plugin = cls()
        assert isinstance(plugin.poll_interval_seconds, int)
        assert plugin.poll_interval_seconds > 0

    @pytest.mark.parametrize("cls", ALL_PLUGINS, ids=lambda c: c.__name__)
    async def test_observe_returns_result(self, cls: type) -> None:
        plugin = cls()
        result = await plugin.observe()
        assert isinstance(result, ObservationResult)
        assert isinstance(result.metrics, dict)
        assert len(result.metrics) > 0

    @pytest.mark.parametrize("cls", ALL_PLUGINS, ids=lambda c: c.__name__)
    def test_default_predictions_valid(self, cls: type) -> None:
        plugin = cls()
        preds = plugin.default_predictions()
        assert isinstance(preds, dict)
        for metric_name, vals in preds.items():
            assert isinstance(metric_name, str)
            assert "expected" in vals
            assert "tolerance" in vals


class TestSourceIdUniqueness:
    def test_unique_source_ids(self) -> None:
        ids = [cls().source_id for cls in ALL_PLUGINS]
        assert len(ids) == len(set(ids)), f"Duplicate source_ids: {ids}"
