"""Tests for the multi-agent supervisor graph."""

from __future__ import annotations

from typing import Any

import pytest

from openbad.frameworks.supervisor import (
    SubAgentDef,
    _build_respond_tool,
    _build_routing_tools,
    build_supervisor_graph,
)

# ── Helpers ──────────────────────────────────────────────────────────── #


def _fake_chat_model() -> Any:
    """Build a ChatOpenAI with a fake key for graph construction.

    We only need ``bind_tools()`` to work — no actual API calls are made.
    """
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(api_key="test-key", base_url="http://localhost:99999")


def _fake_lc_tool(name: str, description: str = "A test tool") -> Any:
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    class _Input(BaseModel):
        query: str = Field(default="", description="Query")

    async def _noop(query: str = "") -> str:
        return f"{name}: {query}"

    return StructuredTool(
        name=name,
        description=description,
        coroutine=_noop,
        args_schema=_Input,
    )


# ── SubAgentDef tests ────────────────────────────────────────────────── #


class TestSubAgentDef:
    def test_creation(self) -> None:
        d = SubAgentDef(
            name="memory",
            description="Manage memory",
            tool_names=frozenset({"read_memory", "write_memory"}),
        )
        assert d.name == "memory"
        assert "read_memory" in d.tool_names
        assert d.system_prompt == ""

    def test_immutable(self) -> None:
        d = SubAgentDef(name="web", description="Web tools")
        with pytest.raises(AttributeError):
            d.name = "changed"  # type: ignore[misc]


# ── Routing tools tests ──────────────────────────────────────────────── #


class TestBuildRoutingTools:
    def test_one_tool_per_agent(self) -> None:
        agents = [
            SubAgentDef(name="memory", description="Memory ops"),
            SubAgentDef(name="web", description="Web ops"),
        ]
        tools = _build_routing_tools(agents)
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert names == {"delegate_to_memory", "delegate_to_web"}

    def test_descriptions_match(self) -> None:
        agents = [
            SubAgentDef(name="library", description="Search and manage books"),
        ]
        tools = _build_routing_tools(agents)
        assert tools[0].description == "Search and manage books"

    @pytest.mark.asyncio
    async def test_routing_tool_is_callable(self) -> None:
        agents = [SubAgentDef(name="test", description="Test agent")]
        tools = _build_routing_tools(agents)
        result = await tools[0].coroutine(request="do something")
        assert "test" in result.lower()


# ── Respond tool tests ───────────────────────────────────────────────── #


class TestBuildRespondTool:
    def test_name(self) -> None:
        tool = _build_respond_tool()
        assert tool.name == "respond_to_user"

    @pytest.mark.asyncio
    async def test_returns_response(self) -> None:
        tool = _build_respond_tool()
        result = await tool.coroutine(response="Hello there!")
        assert result == "Hello there!"


# ── Graph construction tests ─────────────────────────────────────────── #


class TestBuildSupervisorGraph:
    def test_builds_without_error(self) -> None:
        model = _fake_chat_model()
        agents = [
            SubAgentDef(
                name="memory",
                description="Memory ops",
                tool_names=frozenset({"read_memory", "write_memory"}),
            ),
            SubAgentDef(
                name="web",
                description="Web ops",
                tool_names=frozenset({"web_search"}),
            ),
        ]
        all_tools = [
            _fake_lc_tool("read_memory"),
            _fake_lc_tool("write_memory"),
            _fake_lc_tool("web_search"),
        ]

        graph = build_supervisor_graph(
            model, agents, all_tools, request_id="test-001",
        )
        assert graph is not None

    def test_with_system_prompt(self) -> None:
        model = _fake_chat_model()
        agents = [
            SubAgentDef(
                name="web",
                description="Web ops",
                tool_names=frozenset({"web_search"}),
            ),
        ]
        all_tools = [_fake_lc_tool("web_search")]

        graph = build_supervisor_graph(
            model,
            agents,
            all_tools,
            system_prompt="You are a helpful assistant.",
            request_id="test-002",
        )
        assert graph is not None

    def test_with_direct_tools(self) -> None:
        model = _fake_chat_model()
        agents = [
            SubAgentDef(
                name="web",
                description="Web ops",
                tool_names=frozenset({"web_search"}),
            ),
        ]
        all_tools = [_fake_lc_tool("web_search")]
        direct = [_fake_lc_tool("ask_user", "Ask user a question")]

        graph = build_supervisor_graph(
            model,
            agents,
            all_tools,
            direct_tools=direct,
            request_id="test-003",
        )
        assert graph is not None

    def test_skips_agent_with_no_matching_tools(self) -> None:
        model = _fake_chat_model()
        agents = [
            SubAgentDef(
                name="ghost",
                description="No tools",
                tool_names=frozenset({"nonexistent_tool"}),
            ),
            SubAgentDef(
                name="web",
                description="Web ops",
                tool_names=frozenset({"web_search"}),
            ),
        ]
        all_tools = [_fake_lc_tool("web_search")]

        # Should build without error, just skip the ghost agent
        graph = build_supervisor_graph(
            model, agents, all_tools, request_id="test-004",
        )
        assert graph is not None

    def test_empty_sub_agents(self) -> None:
        model = _fake_chat_model()
        # No sub-agents — only the respond_to_user tool
        graph = build_supervisor_graph(
            model, [], [], request_id="test-005",
        )
        assert graph is not None

    def test_graph_has_expected_nodes(self) -> None:
        model = _fake_chat_model()
        agents = [
            SubAgentDef(
                name="memory",
                description="Memory ops",
                tool_names=frozenset({"read_memory"}),
            ),
            SubAgentDef(
                name="web",
                description="Web ops",
                tool_names=frozenset({"web_search"}),
            ),
        ]
        all_tools = [
            _fake_lc_tool("read_memory"),
            _fake_lc_tool("web_search"),
        ]

        graph = build_supervisor_graph(
            model, agents, all_tools, request_id="test-006",
        )
        # CompiledGraph exposes .get_graph() to inspect nodes
        node_names = set(graph.get_graph().nodes.keys())
        assert "supervisor" in node_names
        assert "memory" in node_names
        assert "web" in node_names
