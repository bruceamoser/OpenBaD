"""Tests for immune system endocrine monitor."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from openbad.immune_system.monitor import (
    ADRENALINE_SPIKE_COUNT,
    ALERT_COOLDOWN,
    IMMUNE_CORTISOL_BUMP,
    EndocrineMonitor,
)

# ── Helpers ───────────────────────────────────────────────────────────── #


def _monitor(**kwargs) -> EndocrineMonitor:
    return EndocrineMonitor(window_seconds=300.0, **kwargs)


# ── Sustained cortisol ────────────────────────────────────────────────── #


class TestSustainedCortisol:
    def test_triggers_after_multiple_high_samples(self) -> None:
        m = _monitor()
        now = time.time()
        all_alerts = []
        for i in range(4):
            all_alerts.extend(m.record_sample("cortisol", 0.75, ts=now + i))
        sustained = [
            a for a in all_alerts if a.pattern_type == "sustained_cortisol"
        ]
        assert len(sustained) == 1

    def test_no_alert_below_threshold(self) -> None:
        m = _monitor()
        now = time.time()
        for i in range(5):
            alerts = m.record_sample("cortisol", 0.40, ts=now + i)
        assert not alerts

    def test_needs_minimum_samples(self) -> None:
        m = _monitor()
        now = time.time()
        alerts = m.record_sample("cortisol", 0.80, ts=now)
        assert not alerts  # Only 1 sample, need ≥ 3


# ── Adrenaline spikes ────────────────────────────────────────────────── #


class TestAdrenalineSpikes:
    def test_triggers_after_repeated_spikes(self) -> None:
        m = _monitor()
        now = time.time()
        all_alerts = []
        for i in range(ADRENALINE_SPIKE_COUNT + 1):
            all_alerts.extend(
                m.record_sample("adrenaline", 0.85, ts=now + i),
            )
        assert any(
            a.pattern_type == "repeated_adrenaline_spikes"
            for a in all_alerts
        )

    def test_no_alert_with_few_spikes(self) -> None:
        m = _monitor()
        now = time.time()
        m.record_sample("adrenaline", 0.80, ts=now)
        alerts = m.record_sample("adrenaline", 0.50, ts=now + 1)
        assert not any(
            a.pattern_type == "repeated_adrenaline_spikes" for a in alerts
        )


# ── Extreme levels ───────────────────────────────────────────────────── #


class TestExtremeLevels:
    def test_extreme_any_hormone(self) -> None:
        m = _monitor()
        alerts = m.record_sample("dopamine", 0.95)
        assert any(a.pattern_type == "extreme_dopamine" for a in alerts)

    def test_below_extreme_no_alert(self) -> None:
        m = _monitor()
        alerts = m.record_sample("dopamine", 0.85)
        assert not alerts


# ── MQTT message handling ─────────────────────────────────────────────── #


class TestMqttMessageHandling:
    def test_json_payload(self) -> None:
        m = _monitor()
        payload = json.dumps({"level": 0.95}).encode()
        alerts = m.on_mqtt_message("agent/endocrine/cortisol", payload)
        # Only 1 sample so no sustained_cortisol, but extreme check fires
        assert any(a.hormone == "cortisol" for a in alerts)

    def test_raw_float_payload(self) -> None:
        m = _monitor()
        alerts = m.on_mqtt_message("agent/endocrine/dopamine", b"0.92")
        assert any(a.hormone == "dopamine" for a in alerts)

    def test_invalid_payload_no_crash(self) -> None:
        m = _monitor()
        alerts = m.on_mqtt_message("agent/endocrine/cortisol", b"\xff\xfe")
        assert alerts == []

    def test_bad_topic_no_crash(self) -> None:
        m = _monitor()
        alerts = m.on_mqtt_message("bad", b"0.5")
        assert alerts == []


# ── Alert publishing ──────────────────────────────────────────────────── #


class TestAlertPublishing:
    def test_publishes_mqtt_alert(self) -> None:
        publish = MagicMock()
        m = _monitor(publish_fn=publish)
        now = time.time()
        for i in range(4):
            m.record_sample("cortisol", 0.75, ts=now + i)
        publish.assert_called()
        topic = publish.call_args[0][0]
        assert topic == "agent/immune/alert"

    def test_creates_investigation_task(self) -> None:
        store = MagicMock()
        store.create_task.return_value = SimpleNamespace(task_id="t1")
        m = _monitor(task_store=store)
        now = time.time()
        for i in range(5):
            m.record_sample("cortisol", 0.75, ts=now + i)
        store.create_task.assert_called()

    def test_creates_research_for_novel_pattern(self) -> None:
        store = MagicMock()
        store.enqueue.return_value = SimpleNamespace(node_id="r1")
        m = _monitor(research_store=store)
        now = time.time()
        for i in range(4):
            m.record_sample("cortisol", 0.75, ts=now + i)
        store.enqueue.assert_called_once()  # Novel = first time

    def test_not_novel_on_second_occurrence(self) -> None:
        store = MagicMock()
        store.enqueue.return_value = SimpleNamespace(node_id="r1")
        m = _monitor(research_store=store)
        now = time.time()
        # First occurrence
        for i in range(4):
            m.record_sample("cortisol", 0.75, ts=now + i)
        # Wait past cooldown, second occurrence
        for i in range(4):
            m.record_sample(
                "cortisol", 0.75, ts=now + ALERT_COOLDOWN + 10 + i,
            )
        # Only 1 research entry (first was novel, second is not)
        assert store.enqueue.call_count == 1


# ── Alert cooldown ────────────────────────────────────────────────────── #


class TestAlertCooldown:
    def test_suppresses_repeated_alerts(self) -> None:
        m = _monitor()
        now = time.time()
        # Fill window with high cortisol
        for i in range(4):
            m.record_sample("cortisol", 0.75, ts=now + i)
        # Next sample within cooldown should not alert
        alerts = m.record_sample("cortisol", 0.80, ts=now + 5)
        assert not any(
            a.pattern_type == "sustained_cortisol" for a in alerts
        )

    def test_allows_after_cooldown(self) -> None:
        m = _monitor()
        now = time.time()
        for i in range(4):
            m.record_sample("cortisol", 0.75, ts=now + i)
        # After cooldown — collect all alerts from second batch
        all_alerts: list = []
        for i in range(4):
            all_alerts.extend(
                m.record_sample(
                    "cortisol", 0.75,
                    ts=now + ALERT_COOLDOWN + 10 + i,
                ),
            )
        assert any(
            a.pattern_type == "sustained_cortisol" for a in all_alerts
        )


# ── Immune → endocrine feedback ──────────────────────────────────────── #


class TestImmuneFeedback:
    def test_threat_bumps_cortisol(self) -> None:
        publish = MagicMock()
        m = _monitor(publish_fn=publish)
        m.on_threat_detected("prompt_injection")
        publish.assert_called_once()
        topic = publish.call_args[0][0]
        assert "cortisol" in topic
        payload = json.loads(publish.call_args[0][1])
        assert payload["level_delta"] == IMMUNE_CORTISOL_BUMP

    def test_no_publish_without_fn(self) -> None:
        m = _monitor()
        # Should not raise
        m.on_threat_detected("jailbreak")


# ── Alerts property ──────────────────────────────────────────────────── #


class TestAlertsProperty:
    def test_collects_all_alerts(self) -> None:
        m = _monitor()
        now = time.time()
        # Extreme dopamine alert
        m.record_sample("dopamine", 0.95, ts=now)
        # Sustained cortisol (needs 3+ samples)
        for i in range(4):
            m.record_sample("cortisol", 0.75, ts=now + 100 + i)
        assert len(m.alerts) >= 2
