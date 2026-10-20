"""Tests for openbad.frameworks.crews.user_facing — User-Facing Crew."""

from __future__ import annotations

from crewai import Crew, Process

from openbad.frameworks.agents.definitions import AGENT_SPECS
from openbad.frameworks.crews.user_facing import (
    create_user_facing_crew,
)

# ── Test helpers ─────────────────────────────────────────────────────── #
# Import stubs from the agent tests
from tests.test_crewai_agents import _StubLLM, _StubTool

# ── Crew composition ────────────────────────────────────────────────── #


class TestUserFacingCrew:
    def test_returns_crew(self) -> None:
        crew = create_user_facing_crew("hello")
        assert isinstance(crew, Crew)

    def test_sequential_process(self) -> None:
        crew = create_user_facing_crew("hello")
        assert crew.process == Process.sequential

    def test_has_two_agents(self) -> None:
        crew = create_user_facing_crew("hello")
        assert len(crew.agents) == 2

    def test_has_two_tasks(self) -> None:
        crew = create_user_facing_crew("hello")
        assert len(crew.tasks) == 2

    def test_chat_agent_first(self) -> None:
        crew = create_user_facing_crew("hello")
        assert crew.agents[0].role == "Chat Agent"

    def test_task_agent_second(self) -> None:
        crew = create_user_facing_crew("hello")
        assert crew.agents[1].role == "Task Agent"

    def test_user_message_in_chat_task(self) -> None:
        crew = create_user_facing_crew("What is the weather?")
        desc = crew.tasks[0].description
        assert "What is the weather?" in desc

    def test_chat_task_assigned_to_chat_agent(self) -> None:
        crew = create_user_facing_crew("hello")
        assert crew.tasks[0].agent is crew.agents[0]

    def test_exec_task_assigned_to_task_agent(self) -> None:
        crew = create_user_facing_crew("hello")
        assert crew.tasks[1].agent is crew.agents[1]

    def test_exec_task_has_chat_context(self) -> None:
        crew = create_user_facing_crew("hello")
        assert crew.tasks[0] in crew.tasks[1].context

    def test_not_verbose(self) -> None:
        crew = create_user_facing_crew("hello")
        assert crew.verbose is False


# ── Factory parameters ───────────────────────────────────────────────── #


class TestFactoryParameters:
    def test_llm_factory_called_with_priorities(self) -> None:
        received: list[str] = []

        def _factory(priority: str) -> _StubLLM:
            received.append(priority)
            return _StubLLM(model="test")

        create_user_facing_crew("hi", llm_factory=_factory)
        assert AGENT_SPECS["chat"].priority in received
        assert AGENT_SPECS["task"].priority in received

    def test_tools_factory_called_with_roles(self) -> None:
        received: list[str] = []

        def _factory(tool_role: str) -> list[_StubTool]:
            received.append(tool_role)
            return [_StubTool()]

        create_user_facing_crew("hi", tools_factory=_factory)
        assert "chat" in received
        assert "task" in received

    def test_no_factories_still_works(self) -> None:
        crew = create_user_facing_crew("hi")
        assert isinstance(crew, Crew)

    def test_llm_assigned_to_agents(self) -> None:
        llm = _StubLLM(model="test")
        crew = create_user_facing_crew("hi", llm_factory=lambda _: llm)
        assert crew.agents[0].llm is llm
        assert crew.agents[1].llm is llm

    def test_tools_assigned_to_agents(self) -> None:
        tool = _StubTool()
        crew = create_user_facing_crew("hi", tools_factory=lambda _: [tool])
        assert len(crew.agents[0].tools) == 1
        assert len(crew.agents[1].tools) == 1
