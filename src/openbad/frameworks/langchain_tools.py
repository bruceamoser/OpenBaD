"""Wrap OpenBaD's embedded skills as LangChain ``StructuredTool`` instances.

Reads tool definitions from the FastMCP ``skill_server`` and produces
LangChain-compatible tools that preserve immune gating, access control,
and result truncation.

Hierarchical tool routing
~~~~~~~~~~~~~~~~~~~~~~~~~
When a role defines ``_DIRECT_TOOLS`` (a small set always bound to the
model) vs the full ``_ROLE_TOOLS`` allow-list, the extra tools are made
available through two meta-tools: ``list_tools`` and ``use_tool``.  This
keeps the tool-schema token footprint small enough for context-limited
models while preserving access to the full capability set.

Public API
----------
``get_openbad_tools()``
    Returns all embedded skills as a ``list[BaseTool]``.
``get_tools_for_role(role)``
    Returns a role-filtered subset.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from openbad.skills.server import call_skill, skill_server

log = logging.getLogger(__name__)

# ── Role → tool-name allow-lists ──────────────────────────────────────── #
#
# Each agent role gets a curated subset of tools.  Names must match the
# function names registered on ``skill_server``.

_ROLE_TOOLS: dict[str, set[str]] = {
    "chat": {
        "ask_user",
        "web_search",
        "web_fetch",
        "read_file",
        "find_files",
        "get_endocrine_status",
        "get_tasks",
        "get_research_nodes",
        "read_events",
        "create_task",
        "create_research_node",
        "read_memory",
        "write_memory",
        "prune_memory",
        "query_semantic",
        "search_library",
        "read_book",
        "draft_book",
        "link_books",
        "get_entity_info",
        "update_user_entity",
        "update_assistant_entity",
    },
    "task": {
        "read_file",
        "write_file",
        "find_files",
        "exec_command",
        "update_task",
        "complete_task",
        "work_on_task",
        "work_on_next_task",
        "create_research_node",
        "list_terminal_sessions",
        "create_terminal_session",
        "send_terminal_input",
        "read_terminal_output",
        "close_terminal_session",
        "web_search",
        "web_fetch",
        "ask_user",
        "search_library",
        "read_book",
        "draft_book",
    },
    "research": {
        "web_search",
        "web_fetch",
        "read_file",
        "find_files",
        "create_research_node",
        "update_research_node",
        "complete_research_node",
        "work_on_research",
        "work_on_next_research",
        "get_research_nodes",
        "read_events",
        "search_library",
        "read_book",
    },
    "doctor": {
        "call_doctor",
        "get_endocrine_status",
        "get_system_logs",
        "get_mqtt_records",
        "read_events",
        "get_tasks",
        "get_research_nodes",
        "list_embedded_skills",
    },
    "sleep": {
        "read_events",
        "get_tasks",
        "get_research_nodes",
        "get_endocrine_status",
        "read_memory",
        "write_memory",
        "prune_memory",
        "query_semantic",
        "search_library",
        "read_book",
        "draft_book",
        "link_books",
    },
    "immune": {
        "get_mqtt_records",
        "get_system_logs",
        "read_events",
        "get_endocrine_status",
        "get_path_access_status",
    },
    "explorer": {
        "web_search",
        "web_fetch",
        "read_file",
        "find_files",
        "create_research_node",
        "read_events",
        "get_endocrine_status",
        "search_library",
        "read_book",
    },
}

# ── Direct (always-bound) tools per role ──────────────────────────────── #
#
# Roles listed here expose only these tools directly to the model.
# All remaining tools in ``_ROLE_TOOLS[role]`` are accessible via the
# auto-generated ``list_tools`` and ``use_tool`` meta-tools.
# Roles NOT listed here bind all their tools directly (no meta-tools).

_DIRECT_TOOLS: dict[str, set[str]] = {
    "chat": {
        "ask_user",
        "web_search",
        "web_fetch",
        "read_memory",
        "write_memory",
        "query_semantic",
        "get_entity_info",
        "read_events",
    },
}


# ── Internal helpers ──────────────────────────────────────────────────── #


def _build_args_schema(
    properties: dict[str, Any],
    required: list[str],
) -> type:
    """Dynamically build a pydantic model from JSON Schema properties.

    LangChain ``StructuredTool`` uses an ``args_schema`` pydantic model
    for input validation.  We create one on-the-fly from the MCP tool's
    ``inputSchema``.
    """
    from pydantic import create_model
    from pydantic.fields import FieldInfo

    field_definitions: dict[str, Any] = {}
    for name, prop in properties.items():
        json_type = prop.get("type", "string")
        py_type = _json_type_to_python(json_type)
        default = ... if name in required else prop.get("default")
        description = prop.get("description", "")
        field_definitions[name] = (
            py_type,
            FieldInfo(default=default, description=description),
        )

    if not field_definitions:
        return create_model("ToolInput")

    return create_model("ToolInput", **field_definitions)


def _json_type_to_python(json_type: str) -> type:
    """Map JSON Schema type strings to Python types."""
    mapping: dict[str, type] = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    return mapping.get(json_type, str)


def _make_tool_func(tool_name: str) -> Any:
    """Create an async callable that dispatches to ``call_skill``."""

    async def _invoke(**kwargs: Any) -> str:
        return await call_skill(tool_name, kwargs)

    _invoke.__name__ = tool_name
    _invoke.__qualname__ = tool_name
    return _invoke


def _mcp_tool_to_langchain(mcp_tool: Any) -> StructuredTool:
    """Convert a single MCP ``Tool`` object to a LangChain ``StructuredTool``."""
    schema = mcp_tool.inputSchema or {}
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    args_schema = _build_args_schema(properties, required)
    func = _make_tool_func(mcp_tool.name)

    return StructuredTool(
        name=mcp_tool.name,
        description=mcp_tool.description or f"OpenBaD skill: {mcp_tool.name}",
        coroutine=func,
        args_schema=args_schema,
    )


# ── Meta-tool builders (hierarchical tool routing) ────────────────────── #


def _build_meta_tools(
    role: str,
    discoverable: list[BaseTool],
) -> list[BaseTool]:
    """Build ``list_tools`` and ``use_tool`` meta-tools.

    *discoverable* is the list of tools accessible only through these
    meta-tools (i.e. those in ``_ROLE_TOOLS[role]`` but not in
    ``_DIRECT_TOOLS[role]``).
    """
    from pydantic import BaseModel, Field

    # Build a lookup for fast dispatch
    _by_name: dict[str, BaseTool] = {t.name: t for t in discoverable}

    # Pre-build the catalogue string once
    _catalogue = "\n".join(
        f"- {t.name}: {(t.description or '')[:120]}"
        for t in discoverable
    )

    class ListToolsInput(BaseModel):
        query: str = Field(
            default="",
            description="Optional keyword to filter tools by name or description.",
        )

    async def _list_tools(query: str = "") -> str:
        if not query:
            return (
                f"Available tools ({len(_by_name)}):\n{_catalogue}\n\n"
                "Call use_tool with the tool name and a JSON arguments object."
            )
        q = query.lower()
        matches = [
            f"- {t.name}: {(t.description or '')[:120]}"
            for t in discoverable
            if q in t.name.lower() or q in (t.description or "").lower()
        ]
        if not matches:
            return f"No tools matching '{query}'. Call list_tools without a query to see all."
        return "\n".join(matches)

    class UseToolInput(BaseModel):
        tool_name: str = Field(description="Name of the tool to invoke.")
        arguments: str = Field(
            default="{}",
            description="JSON object of arguments to pass to the tool.",
        )

    async def _use_tool(tool_name: str, arguments: str = "{}") -> str:
        tool = _by_name.get(tool_name)
        if tool is None:
            available = ", ".join(sorted(_by_name))
            return (
                f"Unknown tool '{tool_name}'. "
                f"Available: {available}"
            )
        try:
            kwargs = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError as exc:
            return f"Invalid JSON arguments: {exc}"

        if not isinstance(kwargs, dict):
            return "Arguments must be a JSON object (dict), not a list or scalar."

        try:
            if tool.coroutine:
                result = await tool.coroutine(**kwargs)
            else:
                result = tool.invoke(kwargs)
            return str(result)
        except Exception as exc:
            log.exception("use_tool dispatch failed: %s(%s)", tool_name, arguments)
            return f"Tool {tool_name} failed: {type(exc).__name__}: {exc}"

    list_tool = StructuredTool(
        name="list_tools",
        description=(
            f"List {len(_by_name)} additional tools available for this session. "
            "Pass an optional query to filter by keyword. Use use_tool to call them."
        ),
        coroutine=_list_tools,
        args_schema=ListToolsInput,
    )

    use_tool = StructuredTool(
        name="use_tool",
        description=(
            "Invoke an additional tool by name. Call list_tools first to see "
            "available tools and their descriptions. Pass arguments as a JSON string."
        ),
        coroutine=_use_tool,
        args_schema=UseToolInput,
    )

    return [list_tool, use_tool]


# ── Public API ────────────────────────────────────────────────────────── #

# Module-level cache so tools are built only once.
_tools_cache: list[BaseTool] | None = None


async def _async_build_tools() -> list[BaseTool]:
    """Query the MCP server for tools and convert them all."""
    mcp_tools = await skill_server.list_tools()
    return [_mcp_tool_to_langchain(t) for t in mcp_tools]


def get_openbad_tools() -> list[BaseTool]:
    """Return all embedded OpenBaD skills as LangChain tools.

    Caches the result on first call.  Safe to call from sync or async
    contexts.
    """
    global _tools_cache  # noqa: PLW0603
    if _tools_cache is not None:
        return list(_tools_cache)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        log.warning(
            "get_openbad_tools() called inside a running event loop; "
            "returning empty list.  Use async_get_openbad_tools() instead."
        )
        return []

    _tools_cache = asyncio.run(_async_build_tools())
    return list(_tools_cache)


async def async_get_openbad_tools() -> list[BaseTool]:
    """Async version — preferred when an event loop is available."""
    global _tools_cache  # noqa: PLW0603
    if _tools_cache is not None:
        return list(_tools_cache)
    _tools_cache = await _async_build_tools()
    return list(_tools_cache)


def get_tools_for_role(role: str) -> list[BaseTool]:
    """Return the subset of tools allowed for a given agent role.

    Parameters
    ----------
    role:
        One of: ``chat``, ``task``, ``research``, ``doctor``, ``sleep``,
        ``immune``, ``explorer``.  Unknown roles get an empty list.
    """
    allowed = _ROLE_TOOLS.get(role.lower(), set())
    if not allowed:
        log.warning("No tool allowlist defined for role %r", role)
        return []
    return [t for t in get_openbad_tools() if t.name in allowed]


async def async_get_tools_for_role(role: str) -> list[BaseTool]:
    """Async variant of :func:`get_tools_for_role`.

    When the role has a ``_DIRECT_TOOLS`` entry, only those tools are
    bound directly.  The remaining allowed tools are made discoverable
    via ``list_tools`` / ``use_tool`` meta-tools to reduce schema tokens.
    """
    role_lower = role.lower()
    allowed = _ROLE_TOOLS.get(role_lower, set())
    if not allowed:
        log.warning("No tool allowlist defined for role %r", role)
        return []

    all_tools = await async_get_openbad_tools()
    role_tools = [t for t in all_tools if t.name in allowed]

    direct_names = _DIRECT_TOOLS.get(role_lower)
    if direct_names is None:
        # No hierarchical routing — bind all tools directly.
        return role_tools

    direct = [t for t in role_tools if t.name in direct_names]
    discoverable = [t for t in role_tools if t.name not in direct_names]

    if discoverable:
        meta = _build_meta_tools(role_lower, discoverable)
        direct.extend(meta)
        log.info(
            "Hierarchical tools for role=%s: %d direct + %d discoverable "
            "(via list_tools/use_tool)",
            role_lower,
            len(direct) - len(meta),
            len(discoverable),
        )

    return direct


def clear_tools_cache() -> None:
    """Reset the tool cache — useful in tests."""
    global _tools_cache  # noqa: PLW0603
    _tools_cache = None


# ── CrewAI tool adapter ──────────────────────────────────────────────── #


def langchain_to_crew_tool(lc_tool: BaseTool) -> Any:
    """Convert a LangChain ``BaseTool`` to a CrewAI ``BaseTool``.

    CrewAI's ``BaseTool`` is **not** a subclass of LangChain's, so we
    create a thin subclass that delegates ``_run`` to the LangChain tool.
    """
    from crewai.tools import BaseTool as CrewBaseTool

    lc = lc_tool  # closure

    class _Adapted(CrewBaseTool):
        name: str = lc.name
        description: str = lc.description or ""

        def _run(self, **kwargs: Any) -> str:
            # LangChain StructuredTool.invoke expects a dict of kwargs.
            return lc.invoke(kwargs)

    return _Adapted()


async def async_get_crew_tools(role: str) -> list[Any]:
    """Return CrewAI-compatible tools for *role*.

    Fetches the LangChain tools for the role, then wraps each one via
    :func:`langchain_to_crew_tool`.
    """
    lc_tools = await async_get_tools_for_role(role)
    return [langchain_to_crew_tool(t) for t in lc_tools]
