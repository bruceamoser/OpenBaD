"""Tests for openbad.frameworks.crews.internal — Internal Crew."""

from __future__ import annotations

from crewai import Crew, Process

from openbad.frameworks.agents.definitions import AGENT_SPECS
from openbad.frameworks.crews.internal import (
    ADRENALINE_THRESHOLD,
    create_internal_crew,
)
from tests.test_crewai_agents import _StubLLM, _StubTool

# ── Crew composition ────────────────────────────────────────────────── #


class TestInternalCrew:
    def test_returns_crew(self) -> None:
        crew = create_internal_crew("test alert")
        assert isinstance(crew, Crew)

    def test_sequential_process(self) -> None:
        crew = create_internal_crew("test alert")
        assert crew.process == Process.sequential

    def test_has_two_agents(self) -> None:
        crew = create_internal_crew("test alert")
        assert len(crew.agents) == 2

    def test_has_two_tasks(self) -> None:
        crew = create_internal_crew("test alert")
        assert len(crew.tasks) == 2

    def test_immune_agent_first(self) -> None:
        crew = create_internal_crew("test alert")
        assert crew.agents[0].role == "Immune Agent"

    def test_doctor_agent_second(self) -> None:
        crew = create_internal_crew("test alert")
        assert crew.agents[1].role == "Doctor Agent"

    def test_alert_in_immune_task(self) -> None:
        crew = create_internal_crew("suspicious input detected")
        assert "suspicious input detected" in crew.tasks[0].description

    def test_immune_task_assigned_to_immune_agent(self) -> None:
        crew = create_internal_crew("alert")
        assert crew.tasks[0].agent is crew.agents[0]

    def test_doctor_task_assigned_to_doctor_agent(self) -> None:
        crew = create_internal_crew("alert")
        assert crew.tasks[1].agent is crew.agents[1]

    def test_doctor_task_has_immune_context(self) -> None:
        crew = create_internal_crew("alert")
        assert crew.tasks[0] in crew.tasks[1].context

    def test_not_verbose(self) -> None:
        crew = create_internal_crew("alert")
        assert crew.verbose is False


# ── Adrenaline modulation ────────────────────────────────────────────── #


class TestAdrenalineModulation:
    def test_normal_context_below_threshold(self) -> None:
        crew = create_internal_crew("alert", adrenaline=0.3)
        assert "normal" in crew.tasks[1].description

    def test_expanded_context_above_threshold(self) -> None:
        crew = create_internal_crew("alert", adrenaline=0.8)
        assert "expanded" in crew.tasks[1].description

    def test_normal_at_threshold(self) -> None:
        crew = create_internal_crew("alert", adrenaline=ADRENALINE_THRESHOLD)
        assert "normal" in crew.tasks[1].description

    def test_expanded_just_above_threshold(self) -> None:
        crew = create_internal_crew("alert", adrenaline=ADRENALINE_THRESHOLD + 0.01)
        assert "expanded" in crew.tasks[1].description

    def test_adrenaline_in_doctor_description(self) -> None:
        crew = create_internal_crew("alert", adrenaline=0.75)
        assert "0.75" in crew.tasks[1].description


# ── Factory parameters ───────────────────────────────────────────────── #


class TestFactoryParameters:
    def test_llm_factory_called_with_priorities(self) -> None:
        received: list[str] = []

        def _factory(priority: str) -> _StubLLM:
            received.append(priority)
            return _StubLLM(model="test")

        create_internal_crew("alert", llm_factory=_factory)
        assert AGENT_SPECS["immune"].priority in received
        assert AGENT_SPECS["doctor"].priority in received

    def test_tools_factory_called_with_roles(self) -> None:
        received: list[str] = []

        def _factory(tool_role: str) -> list[_StubTool]:
            received.append(tool_role)
            return [_StubTool()]

        create_internal_crew("alert", tools_factory=_factory)
        assert "immune" in received
        assert "doctor" in received

    def test_no_factories_still_works(self) -> None:
        crew = create_internal_crew("alert")
        assert isinstance(crew, Crew)

    def test_llm_assigned_to_agents(self) -> None:
        llm = _StubLLM(model="test")
        crew = create_internal_crew("alert", llm_factory=lambda _: llm)
        assert crew.agents[0].llm is llm
        assert crew.agents[1].llm is llm

    def test_tools_assigned_to_agents(self) -> None:
        tool = _StubTool()
        crew = create_internal_crew("alert", tools_factory=lambda _: [tool])
        assert len(crew.agents[0].tools) == 1
        assert len(crew.agents[1].tools) == 1
