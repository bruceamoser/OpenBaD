"""Tests for browser history observation plugin."""

from __future__ import annotations

import pytest

from openbad.plugins.observations.browser import BrowserHistoryObservationPlugin


def test_source_id():
    """Test browser history plugin source ID."""
    plugin = BrowserHistoryObservationPlugin(
        browser_profile_path="/tmp/test", browser_type="firefox"
    )
    assert plugin.source_id == "browser_history"


def test_invalid_browser_type():
    """Test plugin rejects invalid browser type."""
    with pytest.raises(ValueError, match="browser_type must be"):
        BrowserHistoryObservationPlugin(
            browser_profile_path="/tmp/test", browser_type="invalid"
        )


def test_default_predictions():
    """Test browser history plugin default predictions."""
    plugin = BrowserHistoryObservationPlugin(
        browser_profile_path="/tmp/test", browser_type="firefox"
    )
    preds = plugin.default_predictions()
    assert "total_visits" in preds
    assert "unique_domains" in preds
    assert "research_intensity" in preds


def test_extract_domain():
    """Test domain extraction from URL."""
    assert (
        BrowserHistoryObservationPlugin._extract_domain("https://example.com/path")
        == "example.com"
    )
    assert (
        BrowserHistoryObservationPlugin._extract_domain("http://sub.domain.org/test")
        == "sub.domain.org"
    )


@pytest.mark.asyncio
async def test_observe_missing_database():
    """Test browser history observation with missing database."""
    plugin = BrowserHistoryObservationPlugin(
        browser_profile_path="/nonexistent/path", browser_type="firefox"
    )

    result = await plugin.observe()

    assert result.metrics["total_visits"] == 0
    assert result.metrics["unique_domains"] == 0


def test_poll_interval():
    """Test browser history poll interval."""
    plugin = BrowserHistoryObservationPlugin(
        browser_profile_path="/tmp/test", browser_type="chromium"
    )
    assert plugin.poll_interval_seconds == 3600
