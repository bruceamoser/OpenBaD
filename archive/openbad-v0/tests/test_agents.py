"""Tests for sub-agent definitions."""

from __future__ import annotations

from openbad.frameworks.agents.sub_agents import (
    CHAT_DIRECT_TOOLS,
    CHAT_SUB_AGENTS,
    ENTITY_AGENT,
    FILE_AGENT,
    LIBRARY_AGENT,
    MEMORY_AGENT,
    SYSTEM_AGENT,
    WEB_AGENT,
)
from openbad.frameworks.langchain_tools import _ROLE_TOOLS


class TestChatSubAgents:
    def test_all_agents_present(self) -> None:
        names = {a.name for a in CHAT_SUB_AGENTS}
        assert names == {
            "memory_agent",
            "library_agent",
            "web_agent",
            "entity_agent",
            "system_agent",
            "file_agent",
        }

    def test_all_tools_covered(self) -> None:
        """Every chat role tool is either in a sub-agent or in CHAT_DIRECT_TOOLS."""
        all_sub_tools: set[str] = set()
        for agent in CHAT_SUB_AGENTS:
            all_sub_tools |= set(agent.tool_names)

        covered = all_sub_tools | CHAT_DIRECT_TOOLS
        chat_tools = _ROLE_TOOLS["chat"]
        missing = chat_tools - covered
        assert not missing, f"Chat tools not assigned to any sub-agent: {missing}"

    def test_no_tool_overlap_between_agents(self) -> None:
        """No tool should be assigned to more than one sub-agent."""
        seen: dict[str, str] = {}
        for agent in CHAT_SUB_AGENTS:
            for tool in agent.tool_names:
                assert tool not in seen, (
                    f"Tool {tool!r} assigned to both {seen[tool]} and {agent.name}"
                )
                seen[tool] = agent.name

    def test_sub_tools_within_role_allowlist(self) -> None:
        """Sub-agent tools must be a subset of the chat role's _ROLE_TOOLS."""
        chat_tools = _ROLE_TOOLS["chat"]
        for agent in CHAT_SUB_AGENTS:
            extra = set(agent.tool_names) - chat_tools
            assert not extra, (
                f"{agent.name} has tools outside chat role: {extra}"
            )

    def test_direct_tools_within_role_allowlist(self) -> None:
        chat_tools = _ROLE_TOOLS["chat"]
        extra = CHAT_DIRECT_TOOLS - chat_tools
        assert not extra, f"Direct tools outside chat role: {extra}"

    def test_each_agent_has_description(self) -> None:
        for agent in CHAT_SUB_AGENTS:
            assert len(agent.description) > 20, (
                f"{agent.name} has too short a description"
            )

    def test_each_agent_has_system_prompt(self) -> None:
        for agent in CHAT_SUB_AGENTS:
            assert len(agent.system_prompt) > 20, (
                f"{agent.name} has too short a system_prompt"
            )


class TestIndividualAgents:
    def test_memory_agent_tools(self) -> None:
        assert MEMORY_AGENT.tool_names == frozenset({
            "read_memory", "write_memory", "prune_memory", "query_semantic",
        })

    def test_library_agent_tools(self) -> None:
        assert LIBRARY_AGENT.tool_names == frozenset({
            "search_library", "read_book", "draft_book", "link_books",
        })

    def test_web_agent_tools(self) -> None:
        assert WEB_AGENT.tool_names == frozenset({
            "web_search", "web_fetch",
        })

    def test_entity_agent_tools(self) -> None:
        assert ENTITY_AGENT.tool_names == frozenset({
            "get_entity_info", "update_user_entity", "update_assistant_entity",
        })

    def test_system_agent_tools(self) -> None:
        assert SYSTEM_AGENT.tool_names == frozenset({
            "get_endocrine_status", "get_tasks", "get_research_nodes",
            "read_events", "create_task", "create_research_node",
        })

    def test_file_agent_tools(self) -> None:
        assert FILE_AGENT.tool_names == frozenset({
            "read_file", "find_files",
        })

    def test_direct_tools(self) -> None:
        assert frozenset({"ask_user"}) == CHAT_DIRECT_TOOLS
