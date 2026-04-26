"""Tests for openbad.frameworks.agents.definitions — CrewAI agent roles."""

from __future__ import annotations

from typing import Any

import pytest
from crewai import Agent
from crewai.llms.base_llm import BaseLLM
from crewai.tools.base_tool import BaseTool

from openbad.frameworks.agents.definitions import (
    AGENT_SPECS,
    AgentSpec,
    create_agent,
    create_all_agents,
)

# ── Test helpers ─────────────────────────────────────────────────────── #


class _StubLLM(BaseLLM):
    model: str = "stub"

    def call(self, *args: Any, **kwargs: Any) -> str:
        return "stub"


class _StubTool(BaseTool):
    name: str = "stub_tool"
    description: str = "A stub tool for testing."

    def _run(self, **kwargs: Any) -> str:
        return "ok"


# ── Agent specs ──────────────────────────────────────────────────────── #


class TestAgentSpecs:
    def test_seven_roles_defined(self) -> None:
        assert len(AGENT_SPECS) == 7

    def test_expected_roles(self) -> None:
        expected = {"chat", "task", "research", "doctor", "sleep", "immune", "explorer"}
        assert set(AGENT_SPECS.keys()) == expected

    @pytest.mark.parametrize("role", list(AGENT_SPECS.keys()))
    def test_spec_has_required_fields(self, role: str) -> None:
        spec = AGENT_SPECS[role]
        assert isinstance(spec, AgentSpec)
        assert spec.role
        assert spec.goal
        assert spec.backstory
        assert spec.priority in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
        assert spec.tool_role

    def test_priorities_match_expectations(self) -> None:
        assert AGENT_SPECS["doctor"].priority == "CRITICAL"
        assert AGENT_SPECS["immune"].priority == "CRITICAL"
        assert AGENT_SPECS["chat"].priority == "HIGH"
        assert AGENT_SPECS["task"].priority == "HIGH"
        assert AGENT_SPECS["research"].priority == "MEDIUM"
        assert AGENT_SPECS["sleep"].priority == "LOW"
        assert AGENT_SPECS["explorer"].priority == "LOW"

    def test_tool_roles_match_langchain_tools(self) -> None:
        for role, spec in AGENT_SPECS.items():
            assert spec.tool_role == role


# ── create_agent ─────────────────────────────────────────────────────── #


class TestCreateAgent:
    def test_creates_agent(self) -> None:
        agent = create_agent("chat")
        assert isinstance(agent, Agent)
        assert agent.role == "Chat Agent"

    def test_unknown_role_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown agent role"):
            create_agent("nonexistent")

    def test_with_custom_llm(self) -> None:
        llm = _StubLLM(model="test")
        agent = create_agent("task", llm=llm)
        assert agent.llm is llm

    def test_with_tools(self) -> None:
        tool = _StubTool()
        agent = create_agent("research", tools=[tool])
        assert len(agent.tools) == 1

    def test_no_delegation(self) -> None:
        agent = create_agent("immune")
        assert agent.allow_delegation is False

    @pytest.mark.parametrize("role", list(AGENT_SPECS.keys()))
    def test_all_roles_create_successfully(self, role: str) -> None:
        agent = create_agent(role)
        assert isinstance(agent, Agent)


# ── create_all_agents ────────────────────────────────────────────────── #


class TestCreateAllAgents:
    def test_creates_all_seven(self) -> None:
        agents = create_all_agents()
        assert len(agents) == 7
        assert set(agents.keys()) == set(AGENT_SPECS.keys())

    def test_with_llm_factory(self) -> None:
        llm = _StubLLM(model="test")
        agents = create_all_agents(llm_factory=lambda _priority: llm)
        for agent in agents.values():
            assert agent.llm is llm

    def test_with_tools_factory(self) -> None:
        tool = _StubTool()
        agents = create_all_agents(
            tools_factory=lambda _role: [tool],
        )
        for agent in agents.values():
            assert len(agent.tools) == 1

    def test_llm_factory_receives_priority(self) -> None:
        received: list[str] = []

        def _factory(priority: str) -> _StubLLM:
            received.append(priority)
            return _StubLLM(model="test")

        create_all_agents(llm_factory=_factory)
        assert "CRITICAL" in received
        assert "HIGH" in received
        assert "MEDIUM" in received
        assert "LOW" in received

    def test_tools_factory_receives_role(self) -> None:
        received: list[str] = []

        def _factory(tool_role: str) -> list[_StubTool]:
            received.append(tool_role)
            return [_StubTool()]

        create_all_agents(tools_factory=_factory)
        assert set(received) == set(AGENT_SPECS.keys())
