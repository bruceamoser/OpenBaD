"""Tests for openbad.frameworks.crews.maintenance — Maintenance Crew."""

from __future__ import annotations

from crewai import Crew, Process

from openbad.frameworks.crews.maintenance import (
    CORTISOL_DISABLE,
    CORTISOL_SUPPRESS,
    create_maintenance_crew,
)
from tests.test_crewai_agents import _StubLLM, _StubTool

# ── Crew composition ────────────────────────────────────────────────── #


class TestMaintenanceCrew:
    def test_returns_crew(self) -> None:
        crew = create_maintenance_crew("test topic")
        assert isinstance(crew, Crew)

    def test_sequential_process(self) -> None:
        crew = create_maintenance_crew("test topic")
        assert crew.process == Process.sequential

    def test_has_three_agents(self) -> None:
        crew = create_maintenance_crew("test topic")
        assert len(crew.agents) == 3

    def test_has_three_tasks(self) -> None:
        crew = create_maintenance_crew("test topic")
        assert len(crew.tasks) == 3

    def test_agent_order(self) -> None:
        crew = create_maintenance_crew("test topic")
        assert crew.agents[0].role == "Explorer Agent"
        assert crew.agents[1].role == "Research Agent"
        assert crew.agents[2].role == "Sleep Agent"

    def test_topic_in_explore_task(self) -> None:
        crew = create_maintenance_crew("quantum computing")
        assert "quantum computing" in crew.tasks[0].description

    def test_research_has_explore_context(self) -> None:
        crew = create_maintenance_crew("topic")
        assert crew.tasks[0] in crew.tasks[1].context

    def test_sleep_has_research_context(self) -> None:
        crew = create_maintenance_crew("topic")
        assert crew.tasks[1] in crew.tasks[2].context

    def test_not_verbose(self) -> None:
        crew = create_maintenance_crew("topic")
        assert crew.verbose is False


# ── FSM gating ───────────────────────────────────────────────────────── #


class TestFSMGating:
    def test_blocked_in_throttled(self) -> None:
        result = create_maintenance_crew("topic", fsm_state="THROTTLED")
        assert result is None

    def test_blocked_in_emergency(self) -> None:
        result = create_maintenance_crew("topic", fsm_state="EMERGENCY")
        assert result is None

    def test_allowed_in_idle(self) -> None:
        result = create_maintenance_crew("topic", fsm_state="IDLE")
        assert isinstance(result, Crew)

    def test_allowed_in_active(self) -> None:
        result = create_maintenance_crew("topic", fsm_state="ACTIVE")
        assert isinstance(result, Crew)

    def test_case_insensitive(self) -> None:
        result = create_maintenance_crew("topic", fsm_state="throttled")
        assert result is None


# ── Cortisol modulation ──────────────────────────────────────────────── #


class TestCortisolModulation:
    def test_full_scope_below_suppress(self) -> None:
        crew = create_maintenance_crew("topic", cortisol=0.3)
        assert "full" in crew.tasks[0].description

    def test_reduced_scope_above_suppress(self) -> None:
        crew = create_maintenance_crew("topic", cortisol=0.6)
        assert "reduced" in crew.tasks[0].description

    def test_disabled_above_disable_threshold(self) -> None:
        crew = create_maintenance_crew("topic", cortisol=0.9)
        assert "disabled" in crew.tasks[0].description

    def test_suppress_at_threshold(self) -> None:
        crew = create_maintenance_crew("topic", cortisol=CORTISOL_SUPPRESS)
        assert "full" in crew.tasks[0].description

    def test_suppress_just_above(self) -> None:
        crew = create_maintenance_crew("topic", cortisol=CORTISOL_SUPPRESS + 0.01)
        assert "reduced" in crew.tasks[0].description

    def test_disable_at_threshold(self) -> None:
        crew = create_maintenance_crew("topic", cortisol=CORTISOL_DISABLE)
        assert "reduced" in crew.tasks[0].description

    def test_disable_just_above(self) -> None:
        crew = create_maintenance_crew("topic", cortisol=CORTISOL_DISABLE + 0.01)
        assert "disabled" in crew.tasks[0].description


# ── Dopamine modulation ─────────────────────────────────────────────── #


class TestDopamineModulation:
    def test_boosted_above_threshold(self) -> None:
        crew = create_maintenance_crew("topic", dopamine=0.7)
        assert "boosted" in crew.tasks[0].description

    def test_no_boost_below_threshold(self) -> None:
        crew = create_maintenance_crew("topic", dopamine=0.3)
        assert "boosted" not in crew.tasks[0].description

    def test_boost_does_not_override_disabled(self) -> None:
        crew = create_maintenance_crew("topic", cortisol=0.9, dopamine=0.7)
        assert "disabled" in crew.tasks[0].description
        assert "boosted" not in crew.tasks[0].description

    def test_dopamine_in_description(self) -> None:
        crew = create_maintenance_crew("topic", dopamine=0.65)
        assert "0.65" in crew.tasks[0].description


# ── Factory parameters ───────────────────────────────────────────────── #


class TestFactoryParameters:
    def test_llm_factory_called(self) -> None:
        received: list[str] = []

        def _factory(priority: str) -> _StubLLM:
            received.append(priority)
            return _StubLLM(model="test")

        create_maintenance_crew("topic", llm_factory=_factory)
        assert len(received) == 3

    def test_tools_factory_called(self) -> None:
        received: list[str] = []

        def _factory(tool_role: str) -> list[_StubTool]:
            received.append(tool_role)
            return [_StubTool()]

        create_maintenance_crew("topic", tools_factory=_factory)
        assert set(received) == {"explorer", "research", "sleep"}

    def test_no_factories_still_works(self) -> None:
        crew = create_maintenance_crew("topic")
        assert isinstance(crew, Crew)
