"""Tests for the LangChain tool wrappers around OpenBaD skills."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openbad.frameworks.langchain_tools import (
    _DIRECT_TOOLS,
    _ROLE_TOOLS,
    _build_args_schema,
    _build_meta_tools,
    _json_type_to_python,
    _make_tool_func,
    _mcp_tool_to_langchain,
    async_get_openbad_tools,
    async_get_tools_for_role,
    clear_tools_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Ensure tool cache is reset between tests."""
    clear_tools_cache()
    yield
    clear_tools_cache()


# ── Fake MCP tool objects ─────────────────────────────────────────────── #


def _fake_mcp_tool(
    name: str = "test_tool",
    description: str = "A test tool",
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> MagicMock:
    """Build a mock MCP Tool object."""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.inputSchema = {
        "type": "object",
        "properties": properties or {"query": {"type": "string", "description": "Search query"}},
        "required": required or ["query"],
    }
    return tool


# ── Unit tests ────────────────────────────────────────────────────────── #


class TestJsonTypeToPython:
    def test_string(self) -> None:
        assert _json_type_to_python("string") is str

    def test_integer(self) -> None:
        assert _json_type_to_python("integer") is int

    def test_number(self) -> None:
        assert _json_type_to_python("number") is float

    def test_boolean(self) -> None:
        assert _json_type_to_python("boolean") is bool

    def test_array(self) -> None:
        assert _json_type_to_python("array") is list

    def test_object(self) -> None:
        assert _json_type_to_python("object") is dict

    def test_unknown_defaults_to_str(self) -> None:
        assert _json_type_to_python("foobar") is str


class TestBuildArgsSchema:
    def test_creates_pydantic_model(self) -> None:
        props = {
            "query": {"type": "string", "description": "Search query"},
            "limit": {"type": "integer", "description": "Max results", "default": 10},
        }
        model = _build_args_schema(props, required=["query"])
        instance = model(query="hello")
        assert instance.query == "hello"
        assert instance.limit == 10

    def test_empty_properties(self) -> None:
        model = _build_args_schema({}, required=[])
        assert model is not None


class TestMcpToolToLangchain:
    def test_converts_name_and_description(self) -> None:
        mcp_tool = _fake_mcp_tool(name="web_search", description="Search the web")
        lc_tool = _mcp_tool_to_langchain(mcp_tool)
        assert lc_tool.name == "web_search"
        assert lc_tool.description == "Search the web"

    def test_has_coroutine(self) -> None:
        mcp_tool = _fake_mcp_tool()
        lc_tool = _mcp_tool_to_langchain(mcp_tool)
        assert lc_tool.coroutine is not None


class TestMakeToolFunc:
    @pytest.mark.asyncio
    async def test_dispatches_to_call_skill(self) -> None:
        func = _make_tool_func("web_search")
        with patch(
            "openbad.frameworks.langchain_tools.call_skill",
            new_callable=AsyncMock,
            return_value="result text",
        ) as mock_call:
            result = await func(query="test")
            mock_call.assert_called_once_with("web_search", {"query": "test"})
            assert result == "result text"


class TestAsyncGetOpenbadTools:
    @pytest.mark.asyncio
    async def test_returns_langchain_tools(self) -> None:
        fake_tools = [
            _fake_mcp_tool("read_file", "Read a file", {"path": {"type": "string"}}, ["path"]),
            _fake_mcp_tool("web_search", "Search", {"query": {"type": "string"}}, ["query"]),
        ]
        with patch("openbad.frameworks.langchain_tools.skill_server") as mock_server:
            mock_server.list_tools = AsyncMock(return_value=fake_tools)
            # Re-import to pick up the patched server
            from openbad.frameworks import langchain_tools

            langchain_tools._tools_cache = None
            tools = await langchain_tools._async_build_tools()
            assert len(tools) == 2
            names = {t.name for t in tools}
            assert "read_file" in names
            assert "web_search" in names

    @pytest.mark.asyncio
    async def test_caches_result(self) -> None:
        fake_tools = [_fake_mcp_tool("test", "Test")]
        with patch("openbad.frameworks.langchain_tools.skill_server") as mock_server:
            mock_server.list_tools = AsyncMock(return_value=fake_tools)
            from openbad.frameworks import langchain_tools

            langchain_tools._tools_cache = None
            first = await langchain_tools._async_build_tools()
            langchain_tools._tools_cache = first
            second = await async_get_openbad_tools()
            # Should return cached copy
            assert len(second) == len(first)
            # list_tools should only have been called once (during _async_build_tools)
            assert mock_server.list_tools.await_count == 1


class TestRoleFiltering:
    def test_all_roles_defined(self) -> None:
        expected_roles = {"chat", "task", "research", "doctor", "sleep", "immune", "explorer"}
        assert expected_roles == set(_ROLE_TOOLS.keys())

    @pytest.mark.asyncio
    async def test_filters_by_role(self) -> None:
        fake_tools = [
            _fake_mcp_tool("web_search", "Search"),
            _fake_mcp_tool("read_file", "Read file"),
            _fake_mcp_tool("exec_command", "Execute command"),
            _fake_mcp_tool("call_doctor", "Doctor"),
        ]
        with patch("openbad.frameworks.langchain_tools.skill_server") as mock_server:
            mock_server.list_tools = AsyncMock(return_value=fake_tools)
            from openbad.frameworks import langchain_tools

            langchain_tools._tools_cache = None
            langchain_tools._tools_cache = await langchain_tools._async_build_tools()

            doctor_tools = await async_get_tools_for_role("doctor")
            doctor_names = {t.name for t in doctor_tools}
            assert "call_doctor" in doctor_names
            # Doctor should NOT have exec_command
            assert "exec_command" not in doctor_names

    @pytest.mark.asyncio
    async def test_unknown_role_returns_empty(self) -> None:
        from openbad.frameworks import langchain_tools

        langchain_tools._tools_cache = []
        tools = await async_get_tools_for_role("nonexistent_role")
        assert tools == []

    def test_task_role_has_exec_command(self) -> None:
        assert "exec_command" in _ROLE_TOOLS["task"]

    def test_immune_role_has_no_write_tools(self) -> None:
        immune_tools = _ROLE_TOOLS["immune"]
        assert "write_file" not in immune_tools
        assert "exec_command" not in immune_tools

    def test_sleep_role_is_read_only(self) -> None:
        sleep_tools = _ROLE_TOOLS["sleep"]
        assert "write_file" not in sleep_tools
        assert "exec_command" not in sleep_tools
        assert "create_task" not in sleep_tools

    def test_chat_role_has_memory_tools(self) -> None:
        chat_tools = _ROLE_TOOLS["chat"]
        assert "read_memory" in chat_tools
        assert "write_memory" in chat_tools
        assert "prune_memory" in chat_tools
        assert "query_semantic" in chat_tools

    def test_chat_role_has_library_tools(self) -> None:
        chat_tools = _ROLE_TOOLS["chat"]
        assert "search_library" in chat_tools
        assert "read_book" in chat_tools
        assert "draft_book" in chat_tools
        assert "link_books" in chat_tools

    def test_chat_role_has_entity_tools(self) -> None:
        chat_tools = _ROLE_TOOLS["chat"]
        assert "get_entity_info" in chat_tools
        assert "update_user_entity" in chat_tools
        assert "update_assistant_entity" in chat_tools


# ── Hierarchical tool routing tests ──────────────────────────────────── #


class TestDirectToolsConfig:
    def test_chat_has_direct_tools(self) -> None:
        assert "chat" in _DIRECT_TOOLS

    def test_direct_tools_subset_of_role_tools(self) -> None:
        for role, direct in _DIRECT_TOOLS.items():
            assert direct.issubset(_ROLE_TOOLS[role]), (
                f"_DIRECT_TOOLS[{role!r}] has tools not in _ROLE_TOOLS: "
                f"{direct - _ROLE_TOOLS[role]}"
            )

    def test_chat_direct_tools_are_small(self) -> None:
        assert len(_DIRECT_TOOLS["chat"]) <= 10


class TestBuildMetaTools:
    def test_returns_list_and_use_tool(self) -> None:
        fake = _mcp_tool_to_langchain(
            _fake_mcp_tool("search_library", "Search library"),
        )
        meta = _build_meta_tools("chat", [fake])
        names = {t.name for t in meta}
        assert names == {"list_tools", "use_tool"}

    @pytest.mark.asyncio
    async def test_list_tools_shows_catalogue(self) -> None:
        fakes = [
            _mcp_tool_to_langchain(_fake_mcp_tool("search_library", "Search the library")),
            _mcp_tool_to_langchain(_fake_mcp_tool("draft_book", "Draft a book")),
        ]
        meta = _build_meta_tools("chat", fakes)
        list_tool = next(t for t in meta if t.name == "list_tools")
        result = await list_tool.coroutine(query="")
        assert "search_library" in result
        assert "draft_book" in result
        assert "Available tools (2)" in result

    @pytest.mark.asyncio
    async def test_list_tools_filters_by_query(self) -> None:
        fakes = [
            _mcp_tool_to_langchain(_fake_mcp_tool("search_library", "Search the library")),
            _mcp_tool_to_langchain(_fake_mcp_tool("draft_book", "Draft a book")),
        ]
        meta = _build_meta_tools("chat", fakes)
        list_tool = next(t for t in meta if t.name == "list_tools")
        result = await list_tool.coroutine(query="library")
        assert "search_library" in result
        assert "draft_book" not in result

    @pytest.mark.asyncio
    async def test_use_tool_dispatches(self) -> None:
        fake = _mcp_tool_to_langchain(
            _fake_mcp_tool("search_library", "Search the library"),
        )
        meta = _build_meta_tools("chat", [fake])
        use = next(t for t in meta if t.name == "use_tool")

        with patch(
            "openbad.frameworks.langchain_tools.call_skill",
            new_callable=AsyncMock,
            return_value="found 3 books",
        ):
            result = await use.coroutine(
                tool_name="search_library",
                arguments='{"query": "python"}',
            )
        assert "found 3 books" in result

    @pytest.mark.asyncio
    async def test_use_tool_rejects_unknown(self) -> None:
        fake = _mcp_tool_to_langchain(
            _fake_mcp_tool("search_library", "Search the library"),
        )
        meta = _build_meta_tools("chat", [fake])
        use = next(t for t in meta if t.name == "use_tool")
        result = await use.coroutine(tool_name="nonexistent", arguments="{}")
        assert "Unknown tool" in result

    @pytest.mark.asyncio
    async def test_use_tool_rejects_bad_json(self) -> None:
        fake = _mcp_tool_to_langchain(
            _fake_mcp_tool("search_library", "Search"),
        )
        meta = _build_meta_tools("chat", [fake])
        use = next(t for t in meta if t.name == "use_tool")
        result = await use.coroutine(tool_name="search_library", arguments="{bad")
        assert "Invalid JSON" in result


class TestHierarchicalRouting:
    @pytest.mark.asyncio
    async def test_chat_gets_meta_tools(self) -> None:
        # Build fake MCP tools for all chat role tools
        chat_names = _ROLE_TOOLS["chat"]
        fake_tools = [_fake_mcp_tool(n, f"Tool {n}") for n in chat_names]

        with patch("openbad.frameworks.langchain_tools.skill_server") as mock_server:
            mock_server.list_tools = AsyncMock(return_value=fake_tools)
            from openbad.frameworks import langchain_tools

            langchain_tools._tools_cache = None
            langchain_tools._tools_cache = await langchain_tools._async_build_tools()

            tools = await async_get_tools_for_role("chat")
            names = {t.name for t in tools}

        # Should have direct tools + list_tools + use_tool
        direct = _DIRECT_TOOLS["chat"]
        for name in direct:
            assert name in names, f"Direct tool {name!r} missing"
        assert "list_tools" in names
        assert "use_tool" in names
        # Total should be direct + 2 meta-tools
        assert len(tools) == len(direct) + 2

    @pytest.mark.asyncio
    async def test_task_gets_all_tools_directly(self) -> None:
        # Task role has no _DIRECT_TOOLS entry → all tools bound directly
        task_names = _ROLE_TOOLS["task"]
        fake_tools = [_fake_mcp_tool(n, f"Tool {n}") for n in task_names]

        with patch("openbad.frameworks.langchain_tools.skill_server") as mock_server:
            mock_server.list_tools = AsyncMock(return_value=fake_tools)
            from openbad.frameworks import langchain_tools

            langchain_tools._tools_cache = None
            langchain_tools._tools_cache = await langchain_tools._async_build_tools()

            tools = await async_get_tools_for_role("task")
            names = {t.name for t in tools}

        # No meta-tools for task role
        assert "list_tools" not in names
        assert "use_tool" not in names
        assert len(tools) == len(task_names)


# ── CrewAI tool adapter tests ────────────────────────────────────────── #


class TestLangchainToCrewTool:
    def test_wraps_name_and_description(self) -> None:
        from openbad.frameworks.langchain_tools import langchain_to_crew_tool

        mcp = _fake_mcp_tool(name="web_search", description="Search the web")
        lc_tool = _mcp_tool_to_langchain(mcp)
        crew_tool = langchain_to_crew_tool(lc_tool)

        assert crew_tool.name == "web_search"
        assert "Search the web" in crew_tool.description

    def test_run_delegates_to_langchain_invoke(self) -> None:
        from openbad.frameworks.langchain_tools import langchain_to_crew_tool

        mcp = _fake_mcp_tool(name="echo_tool", description="Echo")
        lc_tool = _mcp_tool_to_langchain(mcp)

        crew_tool = langchain_to_crew_tool(lc_tool)

        with patch.object(type(lc_tool), "invoke", return_value="echoed: hello") as mock_invoke:
            result = crew_tool._run(query="hello")

        assert result == "echoed: hello"
        mock_invoke.assert_called_once()
        # The invoke call receives (self, input_dict) — verify the dict arg
        call_args = mock_invoke.call_args[0]
        assert {"query": "hello"} in call_args

    def test_is_crewai_base_tool(self) -> None:
        from crewai.tools import BaseTool as CrewBaseTool

        from openbad.frameworks.langchain_tools import langchain_to_crew_tool

        mcp = _fake_mcp_tool()
        lc_tool = _mcp_tool_to_langchain(mcp)
        crew_tool = langchain_to_crew_tool(lc_tool)
        assert isinstance(crew_tool, CrewBaseTool)


class TestAsyncGetCrewTools:
    @pytest.mark.asyncio
    async def test_returns_crew_tools_for_role(self) -> None:
        from openbad.frameworks import langchain_tools
        from openbad.frameworks.langchain_tools import async_get_crew_tools

        mcp_web = _fake_mcp_tool(name="web_search", description="Search")
        mcp_extra = _fake_mcp_tool(name="exec_command", description="Exec")
        langchain_tools._tools_cache = [
            _mcp_tool_to_langchain(mcp_web),
            _mcp_tool_to_langchain(mcp_extra),
        ]

        crew_tools = await async_get_crew_tools("doctor")
        # doctor role does NOT have web_search or exec_command
        names = {t.name for t in crew_tools}
        assert "exec_command" not in names

    @pytest.mark.asyncio
    async def test_empty_for_unknown_role(self) -> None:
        from openbad.frameworks import langchain_tools
        from openbad.frameworks.langchain_tools import async_get_crew_tools

        langchain_tools._tools_cache = []
        crew_tools = await async_get_crew_tools("nonexistent")
        assert crew_tools == []
