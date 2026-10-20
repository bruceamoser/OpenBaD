"""Tests for autonomic ingress routing — external signals to STM + active inference."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.plugins.observations.external_signals import ExternalSignalPlugin

# ── ExternalSignalPlugin ─────────────────────────────────────────── #


class TestExternalSignalPlugin:
    """Tests for the observation plugin counter logic."""

    def test_source_id(self) -> None:
        plugin = ExternalSignalPlugin()
        assert plugin.source_id == "external_signals"

    def test_default_predictions(self) -> None:
        plugin = ExternalSignalPlugin()
        preds = plugin.default_predictions()
        assert "message_count" in preds
        assert preds["message_count"]["expected"] == 0.0

    @pytest.mark.asyncio
    async def test_observe_returns_zero_initially(self) -> None:
        plugin = ExternalSignalPlugin()
        result = await plugin.observe()
        assert result.metrics["message_count"] == 0

    @pytest.mark.asyncio
    async def test_observe_returns_recorded_count(self) -> None:
        plugin = ExternalSignalPlugin()
        plugin.record()
        plugin.record()
        plugin.record()
        result = await plugin.observe()
        assert result.metrics["message_count"] == 3

    @pytest.mark.asyncio
    async def test_observe_resets_counter(self) -> None:
        plugin = ExternalSignalPlugin()
        plugin.record()
        plugin.record()
        await plugin.observe()
        result = await plugin.observe()
        assert result.metrics["message_count"] == 0

    def test_record_is_thread_safe(self) -> None:
        """Smoke test that record doesn't raise under concurrent calls."""
        import threading

        plugin = ExternalSignalPlugin()
        threads = [threading.Thread(target=plugin.record) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # All 50 records should be captured
        assert plugin._count == 50  # noqa: SLF001

    def test_poll_interval_default(self) -> None:
        plugin = ExternalSignalPlugin()
        assert plugin.poll_interval_seconds == 60


# ── ExternalSignalPlugin with surprise ──────────────────────────── #


class TestExternalSignalSurprise:
    """Tests that unexpected messages produce surprise in the engine."""

    @pytest.mark.asyncio
    async def test_surprise_on_unexpected_messages(self) -> None:
        """Non-zero messages when prediction is 0 → positive surprise."""
        from openbad.active_inference.surprise import aggregate_surprise
        from openbad.active_inference.world_model import WorldModel

        wm = WorldModel()
        plugin = ExternalSignalPlugin()
        wm.register_source(plugin.source_id, plugin.default_predictions())

        # Record some inbound messages
        plugin.record()
        plugin.record()
        result = await plugin.observe()

        errors = wm.update(plugin.source_id, result.metrics)
        surprise = aggregate_surprise(errors)
        assert surprise > 0.0

    @pytest.mark.asyncio
    async def test_no_surprise_when_zero_messages(self) -> None:
        """Zero messages matches prediction of 0 → zero surprise."""
        from openbad.active_inference.surprise import aggregate_surprise
        from openbad.active_inference.world_model import WorldModel

        wm = WorldModel()
        plugin = ExternalSignalPlugin()
        wm.register_source(plugin.source_id, plugin.default_predictions())

        result = await plugin.observe()
        errors = wm.update(plugin.source_id, result.metrics)
        surprise = aggregate_surprise(errors)
        assert surprise == 0.0


# ── Daemon handler ───────────────────────────────────────────────── #


class TestDaemonExternalInbound:
    """Tests for the daemon's _on_external_inbound handler."""

    def _make_daemon(self) -> MagicMock:
        """Construct a daemon-like object with patched dependencies."""
        from openbad.daemon import Daemon

        d = Daemon.__new__(Daemon)
        d._external_signal_plugin = ExternalSignalPlugin()  # noqa: SLF001
        d._fsm = None  # noqa: SLF001
        d._client = None  # noqa: SLF001
        return d

    def test_handler_records_to_plugin(self) -> None:
        d = self._make_daemon()
        payload = json.dumps({
            "platform": "discord",
            "event": "message",
            "data": {"text": "hello"},
        }).encode()

        with patch(
            "openbad.memory.cognitive_store.CognitiveMemoryStore"
        ) as mock_store_cls, patch(
            "openbad.state.db.initialize_state_db",
            return_value=MagicMock(),
        ):
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            d._on_external_inbound(  # noqa: SLF001
                "sensory/external/discord/inbound", payload,
            )

        assert d._external_signal_plugin._count == 1  # noqa: SLF001

    def test_handler_writes_stm_entry(self) -> None:
        d = self._make_daemon()
        payload = json.dumps({
            "platform": "slack",
            "event": "message",
            "data": {"text": "hi there"},
            "sender": "user42",
        }).encode()

        with patch(
            "openbad.memory.cognitive_store.CognitiveMemoryStore"
        ) as mock_store_cls, patch(
            "openbad.state.db.initialize_state_db",
            return_value=MagicMock(),
        ):
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            d._on_external_inbound(  # noqa: SLF001
                "sensory/external/slack/inbound", payload,
            )

        mock_store.write.assert_called_once()
        entry: MemoryEntry = mock_store.write.call_args[0][0]
        assert entry.tier == MemoryTier.STM
        assert entry.metadata["platform"] == "slack"
        assert entry.metadata["sender"] == "user42"
        assert entry.ttl_seconds == 300.0
        assert "slack" in entry.key

    def test_handler_extracts_platform_from_topic(self) -> None:
        d = self._make_daemon()
        payload = json.dumps({
            "platform": "gmail",
            "event": "new_email",
            "data": {},
        }).encode()

        with patch(
            "openbad.memory.cognitive_store.CognitiveMemoryStore"
        ) as mock_store_cls, patch(
            "openbad.state.db.initialize_state_db",
            return_value=MagicMock(),
        ):
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            d._on_external_inbound(  # noqa: SLF001
                "sensory/external/gmail/inbound", payload,
            )

        entry: MemoryEntry = mock_store.write.call_args[0][0]
        assert entry.metadata["platform"] == "gmail"
        assert "gmail" in entry.context

    def test_handler_survives_bad_payload(self) -> None:
        d = self._make_daemon()
        # Invalid JSON should not raise
        d._on_external_inbound(  # noqa: SLF001
            "sensory/external/discord/inbound", b"not json",
        )
        # Plugin count should stay at 0 (handler bails before record)
        assert d._external_signal_plugin._count == 0  # noqa: SLF001

    def test_handler_survives_stm_failure(self) -> None:
        d = self._make_daemon()
        payload = json.dumps({"platform": "discord", "event": "msg", "data": {}}).encode()

        with patch(
            "openbad.state.db.initialize_state_db",
            side_effect=RuntimeError("db error"),
        ):
            # Should not raise
            d._on_external_inbound(  # noqa: SLF001
                "sensory/external/discord/inbound", payload,
            )
        # Plugin still recorded despite STM failure
        assert d._external_signal_plugin._count == 1  # noqa: SLF001
