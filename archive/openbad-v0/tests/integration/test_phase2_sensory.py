"""Phase 2 end-to-end integration tests — sensory pipeline.

**Requires a running MQTT broker at ``localhost:1883``** (e.g. NanoMQ
or Mosquitto).  Excluded from the default ``pytest`` run.

Run with::

    pytest -m integration tests/integration/test_phase2_sensory.py -v
"""

from __future__ import annotations

import contextlib
import threading
import time
import uuid

import paho.mqtt.client as mqtt
import pytest

from openbad.nervous_system.schemas import (
    AttentionTrigger,
    Header,
    ParsedScreen,
    TranscriptionEvent,
    TTSComplete,
    TTSRequest,
    WakeWordEvent,
)
from openbad.nervous_system.schemas.reflex_pb2 import ReflexResult
from openbad.nervous_system.topics import (
    SENSORY_ATTENTION_TRIGGER,
    SENSORY_AUDIO,
    SENSORY_AUDIO_TTS_COMPLETE,
    topic_for,
)
from openbad.reflex_arc.escalation import ESCALATION_TOPIC, EscalationGateway
from openbad.reflex_arc.fsm import AgentFSM
from openbad.reflex_arc.handlers.sensory_audio import AudioReflexHandler
from openbad.reflex_arc.handlers.sensory_visual import VisualReflexHandler
from openbad.sensory.dispatcher import SensoryDispatcher

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
#  MQTT test harness (same pattern as Phase 1)
# ---------------------------------------------------------------------------


class MQTTHarness:
    """Wraps a real paho-mqtt connection with helper methods for E2E tests."""

    def __init__(self) -> None:
        uid = uuid.uuid4().hex[:8]
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"openbad-e2e-{uid}",
            protocol=mqtt.MQTTv5,
        )
        self._lock = threading.Lock()
        self._handlers: dict[str, list] = {}
        self._collected: dict[str, list[bytes]] = {}
        self._client.on_message = self._on_message

    def connect(self) -> None:
        self._client.connect("localhost", 1883)
        self._client.loop_start()
        time.sleep(0.3)

    def close(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def subscribe_handler(self, topic: str, callback: callable) -> None:  # type: ignore[valid-type]
        """Subscribe and dispatch to *callback(topic, payload)*."""
        with self._lock:
            self._handlers.setdefault(topic, []).append(callback)
        self._client.subscribe(topic, qos=1)

    def subscribe_handler_wildcard(self, topic: str, callback: callable) -> None:  # type: ignore[valid-type]
        """Subscribe with a wildcard and dispatch to *callback(topic, payload)*."""
        with self._lock:
            self._handlers.setdefault(topic, []).append(callback)
        self._client.subscribe(topic, qos=1)

    def subscribe_collect(self, topic: str) -> None:
        """Subscribe and collect messages for later assertion."""
        with self._lock:
            self._collected.setdefault(topic, [])
        self._client.subscribe(topic, qos=1)

    def subscribe_collect_wildcard(self, topic: str) -> None:
        """Subscribe with a wildcard and collect all matching messages."""
        with self._lock:
            self._collected.setdefault(topic, [])
        self._client.subscribe(topic, qos=1)

    def publish(self, topic: str, data: bytes) -> None:
        self._client.publish(topic, data, qos=1)

    def wait_for(self, topic: str, count: int = 1, timeout: float = 3.0) -> list[bytes]:
        """Block until *count* messages collected on *topic*."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                msgs = self._collected.get(topic, [])
                if len(msgs) >= count:
                    return list(msgs[:count])
            time.sleep(0.05)
        with self._lock:
            return list(self._collected.get(topic, []))

    def _on_message(
        self, _client: object, _userdata: object, msg: mqtt.MQTTMessage,
    ) -> None:
        with self._lock:
            cbs = list(self._handlers.get(msg.topic, []))
            # Also check wildcard handlers
            for pattern, handlers in self._handlers.items():
                if pattern != msg.topic and self._topic_matches(pattern, msg.topic):
                    cbs.extend(handlers)
            # Collect for exact topics
            if msg.topic in self._collected:
                self._collected[msg.topic].append(msg.payload)
            # Collect for wildcard topics
            for pat in list(self._collected):
                if pat != msg.topic and self._topic_matches(pat, msg.topic):
                    self._collected[pat].append(msg.payload)
        for cb in cbs:
            with contextlib.suppress(Exception):
                cb(msg.topic, msg.payload)

    @staticmethod
    def _topic_matches(pattern: str, topic: str) -> bool:
        """Simple MQTT wildcard match (# only)."""
        if pattern.endswith("#"):
            return topic.startswith(pattern[:-1])
        return pattern == topic


@pytest.fixture()
def harness():
    """Yield a connected :class:`MQTTHarness`, skip if no broker."""
    h = MQTTHarness()
    try:
        h.connect()
    except (OSError, ConnectionRefusedError):
        pytest.skip("No MQTT broker at localhost:1883")
    yield h
    h.close()


# ---------------------------------------------------------------------------
# Scenario 1 — Visual attention trigger
# ---------------------------------------------------------------------------


class TestVisualAttentionTrigger:
    """Frame change → attention filter fires → dispatcher routes →
    reflex handler runs → FSM reacts."""

    def test_full_pipeline(self, harness: MQTTHarness) -> None:
        fsm = AgentFSM()
        gateway = EscalationGateway(publish_fn=harness.publish)
        visual_handler = VisualReflexHandler(
            escalation_gw=gateway, critical_delta=0.4,
        )

        dispatcher = SensoryDispatcher(
            fsm=fsm,
            publish_fn=harness.publish,
            visual_handler=visual_handler.handle,
        )

        # Collect results
        result_topic = "agent/reflex/sensory/visual/result"
        harness.subscribe_collect(result_topic)
        harness.subscribe_collect(ESCALATION_TOPIC)

        # Wire dispatcher to sensory wildcard
        harness.subscribe_handler(
            SENSORY_ATTENTION_TRIGGER,
            lambda topic, payload: dispatcher.dispatch(topic, payload),
        )
        time.sleep(0.3)

        # Simulate: attention filter detects a change and publishes trigger
        trigger = AttentionTrigger(
            header=Header(
                timestamp_unix=time.time(),
                source_module="attention_filter",
                schema_version=1,
            ),
            source_id="screen-0",
            ssim_delta=0.65,
            region_description="Dialog appeared",
            changed_pixels=500,
        )
        harness.publish(SENSORY_ATTENTION_TRIGGER, trigger.SerializeToString())

        # Verify reflex result published
        results = harness.wait_for(result_topic, timeout=3.0)
        assert len(results) >= 1
        rr = ReflexResult()
        rr.ParseFromString(results[0])
        assert rr.handled
        assert rr.reflex_id == "sensory/visual"
        assert "visual_attention_routed" in rr.action_taken

        # Verify escalation (ssim_delta 0.65 > critical_delta 0.4)
        escalations = harness.wait_for(ESCALATION_TOPIC, timeout=3.0)
        assert len(escalations) >= 1

        # Verify handler stats
        assert visual_handler.trigger_count >= 1
        assert visual_handler.escalation_count >= 1


# ---------------------------------------------------------------------------
# Scenario 2 — Accessibility extraction roundtrip
# ---------------------------------------------------------------------------


class TestAccessibilityRoundtrip:
    """Mock AT-SPI2 tree → ParsedScreen proto → event bus → consumer."""

    def test_parsed_screen_roundtrip(self, harness: MQTTHarness) -> None:
        parsed_topic = "agent/sensory/vision/screen-0/parsed"
        harness.subscribe_collect(parsed_topic)
        time.sleep(0.3)

        # Build a ParsedScreen with a mock accessibility tree
        tree_json = (
            '{"role": "window", "name": "Terminal", "children": '
            '[{"role": "text", "name": "output", "value": "hello"}]}'
        )
        screen = ParsedScreen(
            header=Header(
                timestamp_unix=time.time(),
                source_module="at_spi2",
                schema_version=1,
            ),
            source_id="screen-0",
            tree_json=tree_json,
            method="at-spi2",
        )
        harness.publish(parsed_topic, screen.SerializeToString())

        # Consumer receives valid proto
        msgs = harness.wait_for(parsed_topic, timeout=3.0)
        assert len(msgs) >= 1
        received = ParsedScreen()
        received.ParseFromString(msgs[0])
        assert received.source_id == "screen-0"
        assert received.method == "at-spi2"
        assert '"role": "window"' in received.tree_json
        assert '"name": "Terminal"' in received.tree_json


# ---------------------------------------------------------------------------
# Scenario 3 — Vosk ambient → wake word → Whisper handoff
# ---------------------------------------------------------------------------


class TestVoskWakeWordWhisperHandoff:
    """Vosk partial → wake word detected → Whisper transcription published."""

    def test_audio_pipeline(self, harness: MQTTHarness) -> None:
        fsm = AgentFSM()
        gateway = EscalationGateway(publish_fn=harness.publish)
        audio_handler = AudioReflexHandler(
            fsm=fsm,
            escalation_gw=gateway,
            escalation_confidence=0.7,
        )

        dispatcher = SensoryDispatcher(
            fsm=fsm,
            publish_fn=harness.publish,
            audio_handler=audio_handler.handle_transcription,
            wake_word_handler=audio_handler.handle_wake_word,
        )

        mic_topic = topic_for(SENSORY_AUDIO, source_id="mic-0")
        audio_result_topic = "agent/reflex/sensory/audio/result"
        harness.subscribe_collect(audio_result_topic)
        harness.subscribe_collect(ESCALATION_TOPIC)

        # Wire dispatcher
        harness.subscribe_handler(
            mic_topic,
            lambda topic, payload: dispatcher.dispatch(topic, payload),
        )
        time.sleep(0.3)

        # Step 1: Vosk publishes ambient transcription (low confidence, filtered)
        ambient = TranscriptionEvent(
            header=Header(
                timestamp_unix=time.time(),
                source_module="vosk",
                schema_version=1,
            ),
            source_id="mic-0",
            text="background noise",
            confidence=0.15,
            is_final=True,
            engine="vosk",
        )
        harness.publish(mic_topic, ambient.SerializeToString())
        time.sleep(0.2)

        # Low confidence should be filtered
        assert dispatcher.stats.below_threshold >= 1

        # Step 2: Wake word detected
        wake = WakeWordEvent(
            header=Header(
                timestamp_unix=time.time(),
                source_module="openwakeword",
                schema_version=1,
            ),
            keyword="hey agent",
            score=0.95,
            buffer_seconds=2.0,
        )
        harness.publish(mic_topic, wake.SerializeToString())

        # Wait for FSM to activate
        time.sleep(0.3)
        assert fsm.state == "ACTIVE"
        assert dispatcher.stats.wake_word_events >= 1

        # Step 3: Whisper publishes high-accuracy transcription
        whisper_trans = TranscriptionEvent(
            header=Header(
                timestamp_unix=time.time(),
                source_module="whisper",
                schema_version=1,
            ),
            source_id="mic-0",
            text="open the terminal",
            confidence=0.92,
            is_final=True,
            engine="whisper",
        )
        harness.publish(mic_topic, whisper_trans.SerializeToString())

        # Verify result published and escalation fired
        results = harness.wait_for(audio_result_topic, timeout=3.0)
        assert len(results) >= 1
        rr = ReflexResult()
        rr.ParseFromString(results[0])
        assert rr.handled
        assert rr.reflex_id == "sensory/audio"

        # Verify escalation for high-confidence transcription
        escalations = harness.wait_for(ESCALATION_TOPIC, timeout=3.0)
        assert len(escalations) >= 1

        # Stats check
        assert dispatcher.stats.audio_events >= 1


# ---------------------------------------------------------------------------
# Scenario 4 — TTS request/response
# ---------------------------------------------------------------------------


class TestTTSRequestResponse:
    """TTSRequest published → consumer processes → TTSComplete published."""

    def test_tts_roundtrip(self, harness: MQTTHarness) -> None:
        tts_request_topic = "agent/sensory/audio/tts/request"
        harness.subscribe_collect(SENSORY_AUDIO_TTS_COMPLETE)

        # Wire up: subscriber receives TTSRequest → publishes TTSComplete
        def on_tts_request(topic: str, payload: bytes) -> None:
            req = TTSRequest()
            req.ParseFromString(payload)
            # Simulate synthesis (no real Piper needed)
            complete = TTSComplete(
                header=Header(
                    timestamp_unix=time.time(),
                    source_module="tts_engine",
                    schema_version=1,
                ),
                request_id=uuid.uuid4().hex[:12],
                duration_ms=456.0,
                success=True,
            )
            harness.publish(
                SENSORY_AUDIO_TTS_COMPLETE,
                complete.SerializeToString(),
            )

        harness.subscribe_handler(tts_request_topic, on_tts_request)
        time.sleep(0.3)

        # Publish TTS request
        req = TTSRequest(
            header=Header(
                timestamp_unix=time.time(),
                source_module="cognitive",
                schema_version=1,
            ),
            text="Hello, how can I help you?",
            voice_model="en_US-hfc_female-medium",
            priority=2,
        )
        harness.publish(tts_request_topic, req.SerializeToString())

        # Verify completion event received
        completions = harness.wait_for(SENSORY_AUDIO_TTS_COMPLETE, timeout=3.0)
        assert len(completions) >= 1
        done = TTSComplete()
        done.ParseFromString(completions[0])
        assert done.success
        assert done.duration_ms > 0
        assert done.request_id != ""


# ---------------------------------------------------------------------------
# Scenario 5 — Sensory overload (throttling)
# ---------------------------------------------------------------------------


class TestSensoryOverloadThrottling:
    """Rapid sensory events → FSM throttled → dispatcher suppresses."""

    def test_throttle_suppression(self, harness: MQTTHarness) -> None:
        fsm = AgentFSM()
        dispatcher = SensoryDispatcher(
            fsm=fsm,
            publish_fn=harness.publish,
        )

        harness.subscribe_handler(
            SENSORY_ATTENTION_TRIGGER,
            lambda topic, payload: dispatcher.dispatch(topic, payload),
        )
        time.sleep(0.3)

        # First: dispatch a normal trigger (should work)
        trigger = AttentionTrigger(
            header=Header(
                timestamp_unix=time.time(),
                source_module="test",
                schema_version=1,
            ),
            source_id="screen-0",
            ssim_delta=0.3,
        )
        harness.publish(SENSORY_ATTENTION_TRIGGER, trigger.SerializeToString())
        time.sleep(0.3)
        assert dispatcher.stats.vision_events >= 1

        # Force FSM into THROTTLED by firing activate then throttle
        fsm.fire("activate")
        fsm.fire("throttle")
        assert fsm.state == "THROTTLED"

        # Publish more triggers — they should be suppressed
        pre_suppressed = dispatcher.stats.total_suppressed
        for _ in range(5):
            trigger = AttentionTrigger(
                header=Header(
                    timestamp_unix=time.time(),
                    source_module="test",
                    schema_version=1,
                ),
                source_id="screen-0",
                ssim_delta=0.5,
            )
            harness.publish(SENSORY_ATTENTION_TRIGGER, trigger.SerializeToString())

        time.sleep(0.5)
        assert dispatcher.stats.total_suppressed >= pre_suppressed + 5

        # Recover and verify events flow again
        fsm.fire("recover_throttle")
        assert fsm.state == "IDLE"

        trigger = AttentionTrigger(
            header=Header(
                timestamp_unix=time.time(),
                source_module="test",
                schema_version=1,
            ),
            source_id="screen-0",
            ssim_delta=0.2,
        )
        pre_vision = dispatcher.stats.vision_events
        harness.publish(SENSORY_ATTENTION_TRIGGER, trigger.SerializeToString())
        time.sleep(0.3)
        assert dispatcher.stats.vision_events >= pre_vision + 1
