"""Tests for openbad.proprioception.registry — tool registry + heartbeat."""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import MagicMock

from openbad.proprioception.registry import (
    HEARTBEAT_TOPIC_PREFIX,
    STATE_TOPIC,
    HealthStatus,
    ToolRegistry,
)

# ── Registration / unregistration ─────────────────────────────────


class TestRegistration:
    def test_register_adds_tool(self):
        reg = ToolRegistry()
        entry = reg.register("tool_a")
        assert entry.name == "tool_a"
        assert entry.status is HealthStatus.AVAILABLE

    def test_register_is_idempotent(self):
        reg = ToolRegistry()
        reg.register("tool_a")
        entry = reg.register("tool_a")
        assert len(reg.get_all_tools()) == 1
        assert entry.status is HealthStatus.AVAILABLE

    def test_register_with_metadata(self):
        reg = ToolRegistry()
        entry = reg.register("tool_a", metadata={"version": "1.0"})
        assert entry.metadata["version"] == "1.0"

    def test_unregister_removes_tool(self):
        reg = ToolRegistry()
        reg.register("tool_a")
        assert reg.unregister("tool_a") is True
        assert reg.get_all_tools() == []

    def test_unregister_missing_returns_false(self):
        reg = ToolRegistry()
        assert reg.unregister("nonexistent") is False


# ── Heartbeat ─────────────────────────────────────────────────────


class TestHeartbeat:
    def test_heartbeat_updates_timestamp(self):
        reg = ToolRegistry()
        entry = reg.register("tool_a")
        old_ts = entry.last_heartbeat
        time.sleep(0.01)
        reg.heartbeat("tool_a")
        assert entry.last_heartbeat > old_ts

    def test_heartbeat_unknown_tool_noop(self):
        reg = ToolRegistry()
        reg.heartbeat("unknown")  # should not raise

    def test_heartbeat_revives_unavailable(self):
        reg = ToolRegistry(timeout=0.01)
        reg.register("tool_a")
        time.sleep(0.05)
        reg.reap_stale()
        assert reg.get_available_tools() == []
        reg.heartbeat("tool_a")
        assert len(reg.get_available_tools()) == 1


# ── Stale detection ───────────────────────────────────────────────


class TestStaleDetection:
    def test_reap_marks_stale_unavailable(self):
        reg = ToolRegistry(timeout=0.01)
        reg.register("tool_a")
        time.sleep(0.05)
        changed = reg.reap_stale()
        assert changed == 1
        assert reg.get_all_tools()[0].status is HealthStatus.UNAVAILABLE

    def test_reap_leaves_fresh_tools(self):
        reg = ToolRegistry(timeout=10.0)
        reg.register("tool_a")
        changed = reg.reap_stale()
        assert changed == 0
        assert reg.get_all_tools()[0].status is HealthStatus.AVAILABLE

    def test_reap_already_unavailable_no_double_count(self):
        reg = ToolRegistry(timeout=0.01)
        reg.register("tool_a")
        time.sleep(0.05)
        reg.reap_stale()
        # second reap should not count tool_a again
        assert reg.reap_stale() == 0


# ── get_available_tools ───────────────────────────────────────────


class TestGetAvailableTools:
    def test_returns_only_available(self):
        reg = ToolRegistry(timeout=0.01)
        reg.register("tool_a")
        reg.register("tool_b")
        time.sleep(0.05)
        reg.heartbeat("tool_b")  # keep b alive
        reg.reap_stale()
        available = reg.get_available_tools()
        assert len(available) == 1
        assert available[0].name == "tool_b"


# ── Snapshot / publish ────────────────────────────────────────────


class TestSnapshot:
    def test_snapshot_format(self):
        reg = ToolRegistry()
        reg.register("tool_a", metadata={"v": "1"})
        snap = reg.snapshot()
        assert len(snap) == 1
        assert snap[0]["name"] == "tool_a"
        assert snap[0]["status"] == "AVAILABLE"
        assert "last_heartbeat" in snap[0]
        assert snap[0]["metadata"] == {"v": "1"}

    def test_publishes_on_register(self):
        published: list[tuple[str, bytes]] = []
        reg = ToolRegistry(publish_fn=lambda t, p: published.append((t, p)))
        reg.register("tool_a")
        assert len(published) == 1
        assert published[0][0] == STATE_TOPIC
        snap = json.loads(published[0][1])
        assert snap[0]["name"] == "tool_a"

    def test_publishes_on_unregister(self):
        published: list[tuple[str, bytes]] = []
        reg = ToolRegistry(publish_fn=lambda t, p: published.append((t, p)))
        reg.register("tool_a")
        published.clear()
        reg.unregister("tool_a")
        assert len(published) == 1

    def test_publishes_on_reap(self):
        published: list[tuple[str, bytes]] = []
        reg = ToolRegistry(
            timeout=0.01,
            publish_fn=lambda t, p: published.append((t, p)),
        )
        reg.register("tool_a")
        published.clear()
        time.sleep(0.05)
        reg.reap_stale()
        assert len(published) == 1


# ── MQTT integration ──────────────────────────────────────────────


class TestMQTTIntegration:
    def test_subscribe_heartbeats(self):
        reg = ToolRegistry()
        client = MagicMock()
        reg.subscribe_heartbeats(client)
        client.subscribe.assert_called_once()
        topic = client.subscribe.call_args.args[0]
        assert topic == HEARTBEAT_TOPIC_PREFIX + "+/heartbeat"

    def test_heartbeat_message_updates_tool(self):
        reg = ToolRegistry()
        reg.register("my_tool")
        entry = reg.get_all_tools()[0]
        old_ts = entry.last_heartbeat
        time.sleep(0.01)
        reg._on_heartbeat_message("agent/proprioception/my_tool/heartbeat", b"")
        assert entry.last_heartbeat > old_ts


# ── Concurrency ───────────────────────────────────────────────────


class TestConcurrency:
    def test_concurrent_register_and_heartbeat(self):
        reg = ToolRegistry()
        errors: list[Exception] = []

        def worker(name: str) -> None:
            try:
                reg.register(name)
                for _ in range(50):
                    reg.heartbeat(name)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert errors == []
        assert len(reg.get_all_tools()) == 10


# ── Reaper thread ─────────────────────────────────────────────────


class TestReaperThread:
    def test_reaper_marks_stale(self):
        reg = ToolRegistry(timeout=0.05)
        reg.register("tool_a")
        reg.start_reaper(interval=0.02)
        try:
            time.sleep(0.2)
            assert reg.get_all_tools()[0].status is HealthStatus.UNAVAILABLE
        finally:
            reg.stop_reaper()

    def test_double_start_is_noop(self):
        reg = ToolRegistry(timeout=10)
        reg.start_reaper(interval=1)
        reaper1 = reg._reaper
        reg.start_reaper(interval=1)
        assert reg._reaper is reaper1
        reg.stop_reaper()
