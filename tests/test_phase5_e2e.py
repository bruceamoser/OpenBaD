"""Phase 5 E2E integration tests.

All tests marked ``@pytest.mark.integration``.
Covers the full active inference + endocrine loop without real MQTT or APIs.
"""

from __future__ import annotations

import pytest

from openbad.active_inference.budget import ExplorationBudget
from openbad.active_inference.config import ActiveInferenceConfig
from openbad.active_inference.engine import ExplorationEngine, ExplorationEvent
from openbad.active_inference.plugin_interface import ObservationPlugin, ObservationResult
from openbad.active_inference.takeaway import TakeawayGenerator
from openbad.active_inference.world_model import WorldModel
from openbad.endocrine.config import EndocrineConfig, HormoneConfig
from openbad.endocrine.controller import HORMONES, EndocrineController
from openbad.endocrine.hooks.adrenaline import AdrenalineHooks
from openbad.endocrine.hooks.cortisol import CortisolHooks
from openbad.endocrine.hooks.dopamine import DopamineHooks
from openbad.endocrine.hooks.endorphin import EndorphinHooks
from openbad.endocrine.l2hr import L2HRMapper
from openbad.endocrine.telemetry import EndocrineTelemetry

# ---------- Helpers ---------------------------------------------------------


def _make_engine(
    config: ActiveInferenceConfig,
    wm: WorldModel | None = None,
    budget: ExplorationBudget | None = None,
) -> ExplorationEngine:
    """Create an ExplorationEngine with sensible defaults."""
    if wm is None:
        wm = WorldModel()
    if budget is None:
        budget = ExplorationBudget(
            daily_limit=config.daily_token_budget,
            cooldown_seconds=config.cooldown_seconds,
        )
    return ExplorationEngine(config=config, world_model=wm, budget=budget)


class _AnomalousPlugin(ObservationPlugin):
    """Stub plugin that returns configurable metrics."""

    def __init__(self, metrics: dict[str, float] | None = None) -> None:
        self._metrics = metrics or {"cpu_percent": 95.0, "memory_percent": 88.0}

    @property
    def source_id(self) -> str:
        return "test_anomalous"

    async def observe(self) -> ObservationResult:
        return ObservationResult(metrics=self._metrics)

    def default_predictions(self) -> dict[str, dict[str, float]]:
        return {
            "cpu_percent": {"expected": 30.0, "tolerance": 15.0},
            "memory_percent": {"expected": 50.0, "tolerance": 15.0},
        }


# ---------- Tests -----------------------------------------------------------


@pytest.mark.integration
class TestObservationToSurpriseToExploration:
    """Observation → surprise → exploration trigger."""

    async def test_anomalous_metrics_trigger_exploration(self) -> None:
        config = ActiveInferenceConfig(
            surprise_threshold=0.5,
            daily_token_budget=100,
            cooldown_seconds=0,
        )
        engine = _make_engine(config)
        plugin = _AnomalousPlugin()
        engine.add_plugin(plugin)

        event = await engine.poll_plugin(plugin)
        assert event is not None
        assert event.surprise > config.surprise_threshold
        assert event.explored is True

    async def test_normal_metrics_no_exploration(self) -> None:
        config = ActiveInferenceConfig(
            surprise_threshold=0.5,
            daily_token_budget=100,
            cooldown_seconds=0,
        )
        engine = _make_engine(config)
        plugin = _AnomalousPlugin({"cpu_percent": 30.0, "memory_percent": 50.0})
        engine.add_plugin(plugin)

        event = await engine.poll_plugin(plugin)
        # Normal metrics → low surprise → not explored.
        assert event is not None
        assert event.explored is False


@pytest.mark.integration
class TestWorldModelSelfCalibration:
    """World model EMA convergence over 20+ observations."""

    def test_ema_converges_after_many_updates(self) -> None:
        wm = WorldModel(ema_alpha=0.2, history_size=30)
        wm.register_source(
            "test_anomalous",
            {"cpu_percent": {"expected": 30.0, "tolerance": 15.0}},
        )

        # Feed 25 observations at cpu=50.
        for _ in range(25):
            wm.update("test_anomalous", {"cpu_percent": 50.0})

        preds = wm.get_entry("test_anomalous", "cpu_percent")
        # EMA should converge close to 50.0.
        assert preds is not None
        assert abs(preds.expected_value - 50.0) < 2.0

    def test_tolerance_adjusts_to_variance(self) -> None:
        wm = WorldModel(ema_alpha=0.2, history_size=30)
        wm.register_source(
            "test_anomalous",
            {"cpu_percent": {"expected": 50.0, "tolerance": 5.0}},
        )

        # Feed alternating values to create variance.
        for i in range(25):
            val = 40.0 if i % 2 == 0 else 60.0
            wm.update("test_anomalous", {"cpu_percent": val})

        entry = wm.get_entry("test_anomalous", "cpu_percent")
        # Tolerance should have grown to accommodate the variance.
        assert entry is not None
        assert entry.tolerance > 5.0


@pytest.mark.integration
class TestExplorationBudgetEnforcement:
    """Budget decrements, cooldown, state suppression."""

    def test_budget_decrements(self) -> None:
        budget = ExplorationBudget(daily_limit=10, cooldown_seconds=0)
        assert budget.can_spend()
        budget.spend(3)
        assert budget.can_spend()
        budget.spend(7)
        assert not budget.can_spend()

    def test_cooldown_enforced(self) -> None:
        budget = ExplorationBudget(daily_limit=100, cooldown_seconds=999)
        budget.spend(1)
        assert not budget.can_spend()

    def test_state_suppression(self) -> None:
        config = ActiveInferenceConfig(
            suppressed_in_states=["THROTTLED", "EMERGENCY"],
            daily_token_budget=100,
            cooldown_seconds=0,
        )
        engine = _make_engine(config)
        engine.set_state("THROTTLED")
        assert engine.is_suppressed

        engine.set_state("NORMAL")
        assert not engine.is_suppressed


@pytest.mark.integration
class TestTakeawayGeneration:
    """Takeaway classification from exploration events."""

    def test_high_surprise_events_generate_takeaways(self) -> None:
        gen = TakeawayGenerator(surprise_threshold=0.5)
        events = [
            ExplorationEvent(
                source_id="sys", surprise=0.9,
                explored=True, errors={"cpu": 0.9},
            ),
        ]
        takeaways = gen.process(events)
        assert len(takeaways) == 1
        assert "surprise=0.90" in takeaways[0].summary

    def test_explored_events_always_produce_takeaway(self) -> None:
        gen = TakeawayGenerator(surprise_threshold=0.99)
        events = [
            ExplorationEvent(
                source_id="sys", surprise=0.1,
                explored=True, errors={},
            ),
        ]
        takeaways = gen.process(events)
        assert len(takeaways) == 1

    def test_low_surprise_unexplored_filtered(self) -> None:
        gen = TakeawayGenerator(surprise_threshold=0.5)
        events = [
            ExplorationEvent(
                source_id="sys", surprise=0.1,
                explored=False, errors={},
            ),
        ]
        takeaways = gen.process(events)
        assert len(takeaways) == 0


@pytest.mark.integration
class TestEndocrineControllerLifecycle:
    """Trigger, decay, clamp, threshold checks for all hormones."""

    def test_trigger_all_hormones(self) -> None:
        ctrl = EndocrineController()
        for h in HORMONES:
            ctrl.trigger(h, 0.50)
            assert ctrl.level(h) == pytest.approx(0.50)

    def test_decay_reduces_levels(self) -> None:
        ctrl = EndocrineController()
        for h in HORMONES:
            ctrl.trigger(h, 0.80)
        ctrl.decay(dt=60.0)
        for h in HORMONES:
            assert ctrl.level(h) < 0.80

    def test_clamping_at_one(self) -> None:
        ctrl = EndocrineController()
        ctrl.trigger("dopamine", 0.80)
        ctrl.trigger("dopamine", 0.80)
        assert ctrl.level("dopamine") == pytest.approx(1.0)

    def test_threshold_checks(self) -> None:
        cfg = EndocrineConfig(
            adrenaline=HormoneConfig(
                activation_threshold=0.50,
                escalation_threshold=0.80,
                half_life_seconds=60.0,
            ),
        )
        ctrl = EndocrineController(config=cfg)
        ctrl.trigger("adrenaline", 0.60)
        assert ctrl.is_active("adrenaline")
        assert not ctrl.is_escalated("adrenaline")
        ctrl.trigger("adrenaline", 0.30)
        assert ctrl.is_escalated("adrenaline")


@pytest.mark.integration
class TestDopamineReinforcement:
    """Successful tasks → dopamine rise."""

    def test_task_complete_raises_dopamine(self) -> None:
        ctrl = EndocrineController()
        hooks = DopamineHooks(ctrl)
        before = ctrl.level("dopamine")
        hooks.on_task_complete()
        assert ctrl.level("dopamine") > before

    def test_exploration_success_scales_with_surprise(self) -> None:
        ctrl = EndocrineController()
        hooks = DopamineHooks(ctrl)
        hooks.on_exploration_success(surprise_delta=0.8)
        level_high = ctrl.level("dopamine")
        ctrl.reset()
        hooks.on_exploration_success(surprise_delta=0.2)
        level_low = ctrl.level("dopamine")
        assert level_high > level_low


@pytest.mark.integration
class TestAdrenalineEscalation:
    """Critical threats → adrenaline rise."""

    def test_security_threat_raises_adrenaline(self) -> None:
        # increment=0.25 * severity=3.0 → 0.75 > activation_threshold 0.60.
        ctrl = EndocrineController()
        hooks = AdrenalineHooks(ctrl)
        hooks.on_security_threat(severity=3.0)
        assert ctrl.is_active("adrenaline")

    def test_high_surprise_triggers_adrenaline(self) -> None:
        ctrl = EndocrineController()
        hooks = AdrenalineHooks(ctrl)
        hooks.on_high_surprise(surprise_level=0.95)
        assert ctrl.level("adrenaline") > 0


@pytest.mark.integration
class TestCortisolConservation:
    """Resource breaches → cortisol rise → exploration suppression."""

    def test_sustained_load_raises_cortisol(self) -> None:
        ctrl = EndocrineController()
        hooks = CortisolHooks(ctrl)
        hooks.on_sustained_load(duration_seconds=600)
        assert ctrl.level("cortisol") > 0

    def test_repeated_failure_activates_cortisol(self) -> None:
        # increment=0.15 * min(5, 3.0) = 0.45; two calls → 0.90 > threshold 0.50.
        ctrl = EndocrineController()
        hooks = CortisolHooks(ctrl)
        hooks.on_repeated_failure(failure_count=5)
        hooks.on_repeated_failure(failure_count=5)
        assert ctrl.is_active("cortisol")


@pytest.mark.integration
class TestEndorphinConsolidation:
    """Stress resolution → endorphin rise."""

    def test_recovery_raises_endorphin(self) -> None:
        ctrl = EndocrineController()
        hooks = EndorphinHooks(ctrl)
        hooks.on_recovery(stress_level_before=0.8)
        assert ctrl.level("endorphin") > 0

    def test_self_heal_raises_endorphin(self) -> None:
        ctrl = EndocrineController()
        hooks = EndorphinHooks(ctrl)
        hooks.on_self_heal()
        assert ctrl.level("endorphin") > 0


@pytest.mark.integration
class TestL2HRMapping:
    """NL outcomes → correct hormone adjustments."""

    def test_success_maps_to_dopamine(self) -> None:
        mapper = L2HRMapper()
        adj = mapper.map("Successfully resolved the user's question")
        assert adj.dopamine > 0

    def test_failure_maps_to_cortisol(self) -> None:
        mapper = L2HRMapper()
        adj = mapper.map("Failed after 3 retries")
        assert adj.cortisol > 0
        assert adj.dopamine < 0

    def test_threat_maps_to_multiple(self) -> None:
        mapper = L2HRMapper()
        adj = mapper.map("Detected and quarantined a prompt injection")
        assert adj.dopamine > 0
        assert adj.endorphin > 0


@pytest.mark.integration
class TestFullDriveCycle:
    """End-to-end: explore → surprise → takeaway → hormone → telemetry."""

    async def test_full_cycle(self) -> None:
        # -- Setup --
        ai_config = ActiveInferenceConfig(
            surprise_threshold=0.3,
            daily_token_budget=100,
            cooldown_seconds=0,
        )
        endo_config = EndocrineConfig()
        ctrl = EndocrineController(config=endo_config)
        telemetry = EndocrineTelemetry(ctrl)
        dopamine_hooks = DopamineHooks(ctrl)
        endorphin_hooks = EndorphinHooks(ctrl)

        # Engine with world model.
        plugin = _AnomalousPlugin({"cpu_percent": 95.0, "memory_percent": 88.0})
        engine = _make_engine(ai_config)
        engine.add_plugin(plugin)

        # -- 1. Explore: anomalous metrics trigger exploration --
        event = await engine.poll_plugin(plugin)
        assert event is not None
        assert event.surprise > 0.3
        assert event.explored is True

        # -- 2. Takeaway from exploration --
        gen = TakeawayGenerator(surprise_threshold=0.3)
        takeaways = gen.process([event])
        assert len(takeaways) >= 1

        # -- 3. Dopamine: exploration was successful --
        old_dopa = ctrl.level("dopamine")
        dopamine_hooks.on_exploration_success(surprise_delta=event.surprise)
        new_dopa = ctrl.level("dopamine")
        assert new_dopa > old_dopa
        telemetry.record_trigger("dopamine", old_dopa, new_dopa, "exploration_success")

        # -- 4. Endorphin: recovery after stress resolution --
        old_endo = ctrl.level("endorphin")
        endorphin_hooks.on_recovery(stress_level_before=0.6)
        new_endo = ctrl.level("endorphin")
        assert new_endo > old_endo
        telemetry.record_trigger("endorphin", old_endo, new_endo, "recovery")

        # -- 5. Telemetry observability --
        telemetry.update_activation_stats()
        status = telemetry.status()
        assert status["hormones"]["dopamine"]["level"] > 0
        assert len(telemetry.change_log) == 2

        summary = telemetry.summary()
        assert summary["levels"]["dopamine"] > 0

        # -- 6. Decay cycle --
        ctrl.decay(dt=120.0)
        for h in HORMONES:
            assert ctrl.level(h) < 1.0

        # -- 7. L2HR: NL outcome → adjustments --
        mapper = L2HRMapper()
        adj = mapper.map("Task completed successfully")
        assert adj.dopamine > 0
