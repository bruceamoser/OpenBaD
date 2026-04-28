"""Tests for openbad.reflex_arc.fsm — FSM engine with agent operational states."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from openbad.nervous_system.schemas.common_pb2 import Header
from openbad.nervous_system.schemas.endocrine_pb2 import EndocrineEvent
from openbad.nervous_system.schemas.immune_pb2 import ImmuneAlert
from openbad.reflex_arc.fsm import BUSY_STATES, STATES, TOPIC_TRIGGER_MAP, AgentFSM


@pytest.fixture
def fsm():
    return AgentFSM()


@pytest.fixture
def client_mock():
    return MagicMock()


@pytest.fixture
def fsm_with_client(client_mock):
    return AgentFSM(client=client_mock)


# ── States & initial state ────────────────────────────────────────


class TestStates:
    def test_all_states_defined(self):
        assert STATES == [
            "IDLE", "ACTIVE",
            "RESEARCHING", "EXECUTING_TASK", "DIAGNOSING",
            "THROTTLED", "SLEEP", "EMERGENCY",
        ]

    def test_busy_states(self):
        assert {"RESEARCHING", "EXECUTING_TASK", "DIAGNOSING"} == BUSY_STATES

    def test_initial_state_idle(self, fsm: AgentFSM):
        assert fsm.state == "IDLE"


# ── Valid transitions ─────────────────────────────────────────────


class TestValidTransitions:
    def test_idle_to_active(self, fsm: AgentFSM):
        assert fsm.fire("activate")
        assert fsm.state == "ACTIVE"

    def test_active_to_idle(self, fsm: AgentFSM):
        fsm.fire("activate")
        assert fsm.fire("deactivate")
        assert fsm.state == "IDLE"

    def test_active_to_throttled(self, fsm: AgentFSM):
        fsm.fire("activate")
        assert fsm.fire("throttle")
        assert fsm.state == "THROTTLED"

    def test_idle_to_throttled(self, fsm: AgentFSM):
        assert fsm.fire("throttle")
        assert fsm.state == "THROTTLED"

    def test_throttled_to_idle(self, fsm: AgentFSM):
        fsm.fire("throttle")
        assert fsm.fire("recover_throttle")
        assert fsm.state == "IDLE"

    def test_active_to_sleep(self, fsm: AgentFSM):
        fsm.fire("activate")
        assert fsm.fire("sleep")
        assert fsm.state == "SLEEP"

    def test_idle_to_sleep(self, fsm: AgentFSM):
        assert fsm.fire("sleep")
        assert fsm.state == "SLEEP"

    def test_sleep_to_idle(self, fsm: AgentFSM):
        fsm.fire("sleep")
        assert fsm.fire("wake")
        assert fsm.state == "IDLE"

    def test_emergency_from_any(self, fsm: AgentFSM):
        """Emergency transition works from every state."""
        for setup_triggers, _expected_source in [
            ([], "IDLE"),
            (["activate"], "ACTIVE"),
            (["throttle"], "THROTTLED"),
            (["sleep"], "SLEEP"),
        ]:
            fresh = AgentFSM()
            for t in setup_triggers:
                fresh.fire(t)
            assert fresh.fire("emergency")
            assert fresh.state == "EMERGENCY"

    def test_emergency_to_idle(self, fsm: AgentFSM):
        fsm.fire("emergency")
        assert fsm.fire("recover_emergency")
        assert fsm.state == "IDLE"


# ── Invalid transitions ───────────────────────────────────────────


class TestInvalidTransitions:
    def test_deactivate_from_idle(self, fsm: AgentFSM):
        assert not fsm.fire("deactivate")
        assert fsm.state == "IDLE"

    def test_wake_from_idle(self, fsm: AgentFSM):
        assert not fsm.fire("wake")
        assert fsm.state == "IDLE"

    def test_recover_throttle_from_idle(self, fsm: AgentFSM):
        assert not fsm.fire("recover_throttle")
        assert fsm.state == "IDLE"

    def test_activate_from_throttled(self, fsm: AgentFSM):
        fsm.fire("throttle")
        assert not fsm.fire("activate")
        assert fsm.state == "THROTTLED"

    def test_nonexistent_trigger(self, fsm: AgentFSM):
        assert not fsm.fire("fly_to_moon")
        assert fsm.state == "IDLE"


# ── Event bus publishing ──────────────────────────────────────────


class TestPublishing:
    def test_publishes_on_transition(self, fsm_with_client: AgentFSM, client_mock):
        fsm_with_client.fire("activate")
        client_mock.publish.assert_called_once()
        topic, payload = client_mock.publish.call_args[0]
        assert topic == "agent/reflex/state"
        assert payload.previous_state == "IDLE"
        assert payload.current_state == "ACTIVE"
        assert payload.trigger_event == "activate"

    def test_no_publish_on_invalid_transition(self, fsm_with_client: AgentFSM, client_mock):
        fsm_with_client.fire("deactivate")
        client_mock.publish.assert_not_called()


# ── handle_event integration ──────────────────────────────────────


def _cortisol_critical() -> bytes:
    return EndocrineEvent(
        header=Header(timestamp_unix=1.0),
        hormone="cortisol",
        level=1.0,
        severity=3,  # CRITICAL
    ).SerializeToString()


def _cortisol_warning() -> bytes:
    return EndocrineEvent(
        header=Header(timestamp_unix=1.0),
        hormone="cortisol",
        level=1.0,
        severity=2,  # WARNING
    ).SerializeToString()


def _immune_critical() -> bytes:
    return ImmuneAlert(
        header=Header(timestamp_unix=1.0),
        severity=3,
        threat_type="prompt-injection",
    ).SerializeToString()


def _immune_info() -> bytes:
    return ImmuneAlert(
        header=Header(timestamp_unix=1.0),
        severity=1,
        threat_type="benign",
    ).SerializeToString()


class TestHandleEvent:
    def test_zero_adrenaline_snapshot_does_not_trigger_emergency(self, fsm: AgentFSM):
        payload = EndocrineEvent(
            header=Header(timestamp_unix=1.0),
            hormone="adrenaline",
            level=0.0,
        ).SerializeToString()
        assert not fsm.handle_event("agent/endocrine/adrenaline", payload)
        assert fsm.state == "IDLE"

    def test_zero_endorphin_snapshot_does_not_trigger_sleep(self, fsm: AgentFSM):
        payload = EndocrineEvent(
            header=Header(timestamp_unix=1.0),
            hormone="endorphin",
            level=0.0,
        ).SerializeToString()
        assert not fsm.handle_event("agent/endocrine/endorphin", payload)
        assert fsm.state == "IDLE"

    def test_cortisol_critical_throttles(self, fsm: AgentFSM):
        assert fsm.handle_event("agent/endocrine/cortisol", _cortisol_critical())
        assert fsm.state == "THROTTLED"

    def test_cortisol_warning_no_transition(self, fsm: AgentFSM):
        assert not fsm.handle_event("agent/endocrine/cortisol", _cortisol_warning())
        assert fsm.state == "IDLE"

    def test_adrenaline_triggers_emergency(self, fsm: AgentFSM):
        payload = EndocrineEvent(
            header=Header(timestamp_unix=1.0),
            hormone="adrenaline",
            level=1.0,
            severity=3,
        ).SerializeToString()
        assert fsm.handle_event("agent/endocrine/adrenaline", payload)
        assert fsm.state == "EMERGENCY"

    def test_endorphin_triggers_sleep(self, fsm: AgentFSM):
        payload = EndocrineEvent(
            header=Header(timestamp_unix=1.0),
            hormone="endorphin",
            level=1.0,
        ).SerializeToString()
        assert fsm.handle_event("agent/endocrine/endorphin", payload)
        assert fsm.state == "SLEEP"

    def test_immune_critical_triggers_emergency(self, fsm: AgentFSM):
        assert fsm.handle_event("agent/immune/alert", _immune_critical())
        assert fsm.state == "EMERGENCY"

    def test_immune_info_no_transition(self, fsm: AgentFSM):
        assert not fsm.handle_event("agent/immune/alert", _immune_info())
        assert fsm.state == "IDLE"

    def test_unknown_topic_ignored(self, fsm: AgentFSM):
        assert not fsm.handle_event("agent/telemetry/cpu", b"")
        assert fsm.state == "IDLE"


# ── subscribe_triggers ────────────────────────────────────────────


class TestSubscribeTriggers:
    def test_subscribes_all_trigger_topics(self, fsm_with_client: AgentFSM, client_mock):
        fsm_with_client.subscribe_triggers()
        subscribed_topics = {c.args[0] for c in client_mock.subscribe.call_args_list}
        assert subscribed_topics == set(TOPIC_TRIGGER_MAP)
        assert all(
            c.args[2] == fsm_with_client.handle_event
            for c in client_mock.subscribe.call_args_list
        )

    def test_no_subscribe_without_client(self, fsm: AgentFSM):
        # Should not raise
        fsm.subscribe_triggers()


# ── Concurrent transitions (atomicity) ────────────────────────────


class TestConcurrency:
    def test_atomic_transitions_under_contention(self):
        """Fire many triggers concurrently — FSM must remain consistent."""
        fsm = AgentFSM()
        errors: list[str] = []
        barrier = threading.Barrier(10)

        def fire(trigger_name: str) -> None:
            barrier.wait()
            fsm.fire(trigger_name)
            if fsm.state not in STATES:
                errors.append(f"Invalid state: {fsm.state}")

        threads = [threading.Thread(target=fire, args=("emergency",)) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert fsm.state == "EMERGENCY"


# ── Busy-state transitions ────────────────────────────────────────


class TestBusyTransitions:
    @pytest.mark.parametrize("trigger,expected", [
        ("begin_research", "RESEARCHING"),
        ("begin_task", "EXECUTING_TASK"),
        ("begin_diagnose", "DIAGNOSING"),
    ])
    def test_idle_to_busy(self, fsm: AgentFSM, trigger, expected):
        assert fsm.fire(trigger)
        assert fsm.state == expected

    @pytest.mark.parametrize("trigger,expected", [
        ("begin_research", "RESEARCHING"),
        ("begin_task", "EXECUTING_TASK"),
        ("begin_diagnose", "DIAGNOSING"),
    ])
    def test_active_to_busy(self, fsm: AgentFSM, trigger, expected):
        fsm.fire("activate")
        assert fsm.fire(trigger)
        assert fsm.state == expected

    @pytest.mark.parametrize("trigger", [
        "begin_research", "begin_task", "begin_diagnose",
    ])
    def test_busy_blocks_other_busy(self, fsm: AgentFSM, trigger):
        fsm.fire("begin_research")
        assert not fsm.fire(trigger)
        assert fsm.state == "RESEARCHING"

    @pytest.mark.parametrize("busy_trigger", [
        "begin_research", "begin_task", "begin_diagnose",
    ])
    def test_complete_work_returns_to_idle(self, fsm: AgentFSM, busy_trigger):
        fsm.fire(busy_trigger)
        assert fsm.fire("complete_work")
        assert fsm.state == "IDLE"

    def test_emergency_overrides_busy(self, fsm: AgentFSM):
        fsm.fire("begin_research")
        assert fsm.fire("emergency")
        assert fsm.state == "EMERGENCY"

    def test_complete_work_from_idle_fails(self, fsm: AgentFSM):
        assert not fsm.fire("complete_work")
        assert fsm.state == "IDLE"


# ── try_begin_work / finish_work helpers ──────────────────────────


class TestWorkHelpers:
    def test_try_begin_work_success(self, fsm: AgentFSM):
        assert fsm.try_begin_work("begin_research")
        assert fsm.state == "RESEARCHING"
        assert fsm.is_busy

    def test_try_begin_work_rejects_when_busy(self, fsm: AgentFSM):
        fsm.try_begin_work("begin_research")
        assert not fsm.try_begin_work("begin_task")
        assert fsm.state == "RESEARCHING"

    def test_finish_work_returns_to_idle(self, fsm: AgentFSM):
        fsm.try_begin_work("begin_task")
        assert fsm.finish_work()
        assert fsm.state == "IDLE"
        assert not fsm.is_busy

    def test_finish_work_safe_when_not_busy(self, fsm: AgentFSM):
        assert not fsm.finish_work()
        assert fsm.state == "IDLE"

    def test_check_work_timeout_no_op_when_idle(self, fsm: AgentFSM):
        assert not fsm.check_work_timeout()

    def test_check_work_timeout_recovers_stuck_state(self, fsm: AgentFSM):
        fsm.try_begin_work("begin_research", timeout_seconds=0.0)
        # Deadline is now in the past (monotonic + 30s minimum).
        # Force deadline to be expired.
        fsm._work_deadline = 0.0001
        assert fsm.check_work_timeout()
        assert fsm.state == "IDLE"

    def test_check_work_timeout_no_recovery_before_deadline(self, fsm: AgentFSM):
        fsm.try_begin_work("begin_research", timeout_seconds=9999)
        assert not fsm.check_work_timeout()
        assert fsm.state == "RESEARCHING"
