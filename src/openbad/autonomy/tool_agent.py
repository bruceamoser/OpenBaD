"""LangGraph-based tool agent for OpenBaD autonomy subsystem.

Replaces the previous custom agentic loop with LangGraph's
``create_react_agent``, using LangChain tools converted from
OpenBaD's embedded skills.

Public API
----------
``run_tool_agent(chat_model, ...)``
    Execute an agentic task with tools, returning a ``ToolAgentResult``.
``build_tooling_system_prompt(base_prompt)``
    Prepend the standard OpenBaD tool-use instructions to a system prompt.
``ToolAgentResult``
    Frozen dataclass returned by ``run_tool_agent``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.prebuilt import create_react_agent

from openbad.frameworks.langchain_tools import async_get_openbad_tools

log = logging.getLogger(__name__)

_MAX_TOOL_ITERATIONS = 16

_TOOLING_BASE_PROMPT = (
    "You have access to OpenBaD's embedded skills. These are built-in tools provided"
    " directly to you — they are NOT on an external server. Use them proactively when"
    " they improve the quality of the work. You may inspect files, events, MQTT records,"
    " endocrine state, tasks, and research nodes, and you may create or update"
    " task/research entries when follow-up work is warranted. Never fabricate tool results."
    " The mcp_bridge tool is ONLY for connecting to external third-party MCP servers."
    " Do not use mcp_bridge to access your own embedded skills — just call them directly."
    " If the user refers to a file but you have not verified its exact path yet, call"
    " find_files before read_file. Search the current workspace first, and do not"
    " supply a guessed absolute cwd such as another user's home directory unless the"
    " user or a prior tool result explicitly provided that directory."
    " If a tool result starts with [access_request], do not claim the file or directory"
    " is missing. The access request has already been created. Tell the user to approve"
    " it in Toolbelt -> Path Access Requests, then retry after approval. Do not call"
    " ask_user just to repeat the permission request."
    " If a tool fails, acknowledge that explicitly and continue with the best supported"
    " answer you can provide."
)


@dataclass(frozen=True)
class ToolAgentResult:
    content: str
    provider: str
    model: str
    tokens_used: int = 0
    tools_used: tuple[str, ...] = ()
    verified_creations: tuple[str, ...] = ()
    used_agentic: bool = False
    tool_details: tuple[dict[str, object], ...] = ()


def build_tooling_system_prompt(base_prompt: str) -> str:
    return f"{_TOOLING_BASE_PROMPT}\n\n{base_prompt.strip()}"


def _parse_json_object(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_creation_info(messages: list[Any]) -> tuple[list[str], list[str]]:
    """Extract tool names used and verified creations from agent messages."""
    tool_names: list[str] = []
    verified: list[str] = []

    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_names.append(tc["name"])
        if hasattr(msg, "name") and hasattr(msg, "content"):
            fn_name = getattr(msg, "name", "")
            if fn_name in ("create_task", "create_research_node"):
                payload = _parse_json_object(
                    msg.content if isinstance(msg.content, str) else ""
                )
                if fn_name == "create_task":
                    task_id = str(payload.get("task_id", "")).strip()
                    if task_id:
                        title = str(
                            payload.get("title", task_id)
                        ).strip()
                        verified.append(f"task '{title}' ({task_id})")
                elif fn_name == "create_research_node":
                    node_id = str(payload.get("node_id", "")).strip()
                    if node_id:
                        title = str(
                            payload.get("title", node_id)
                        ).strip()
                        verified.append(
                            f"research '{title}' ({node_id})"
                        )

    return tool_names, verified


def _extract_tool_details(messages: list[Any]) -> list[dict[str, object]]:
    """Extract tool call details (name, args, result) from agent messages."""
    from langchain_core.messages import ToolMessage

    # Build a map of tool_call_id -> (name, args) from AIMessage tool_calls
    call_map: dict[str, dict[str, object]] = {}
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                call_id = tc.get("id", "")
                args = tc.get("args", {})
                # Truncate large arg values
                safe_args = {}
                for k, v in (args if isinstance(args, dict) else {}).items():
                    s = str(v)
                    safe_args[k] = s[:500] if len(s) > 500 else v
                call_map[call_id] = {"name": tc["name"], "args": safe_args}

    details: list[dict[str, object]] = []
    for msg in messages:
        if isinstance(msg, ToolMessage):
            call_id = getattr(msg, "tool_call_id", "")
            info = call_map.get(call_id, {})
            result_text = str(msg.content) if msg.content else ""
            details.append({
                "name": info.get("name", getattr(msg, "name", "unknown")),
                "args": info.get("args", {}),
                "result": result_text[:2000] if len(result_text) > 2000 else result_text,
            })
    return details


async def run_tool_agent(
    chat_model: Any,
    model_id: str,
    *,
    provider_name: str,
    system_prompt: str,
    user_prompt: str,
    request_id: str,
    tool_call_validator: Callable[
        [str, dict[str, Any]], str | None
    ]
    | None = None,
    tools_role: str | None = None,
) -> ToolAgentResult:
    """Run an agentic task using LangGraph's ReAct agent.

    *chat_model* must be a LangChain ``BaseChatModel`` (e.g. ``ChatOpenAI``).
    """
    if tools_role:
        from openbad.frameworks.langchain_tools import async_get_tools_for_role

        tools = await async_get_tools_for_role(tools_role)
    else:
        tools = await async_get_openbad_tools()

    # Wrap tools with validator guard if provided
    if tool_call_validator is not None:
        from langchain_core.tools import StructuredTool

        def _wrap_tool(tool: Any) -> Any:
            original = tool.coroutine

            async def _guarded(**kwargs: Any) -> str:
                reason = tool_call_validator(tool.name, kwargs)
                if reason:
                    log.info(
                        "Tool blocked request=%s tool=%s reason=%s",
                        request_id, tool.name, reason,
                    )
                    return reason
                return await original(**kwargs)

            return StructuredTool(
                name=tool.name,
                description=tool.description,
                coroutine=_guarded,
                args_schema=tool.args_schema,
            )

        tools = [_wrap_tool(t) for t in tools]

    agent = create_react_agent(
        model=chat_model,
        tools=tools,
        prompt=system_prompt,
    )

    try:
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=user_prompt)]},
            config={"recursion_limit": _MAX_TOOL_ITERATIONS * 2},
        )
    except Exception:
        log.exception("LangGraph agent failed request=%s", request_id)
        return ToolAgentResult(
            content="",
            provider=provider_name or "unknown",
            model=model_id,
            used_agentic=True,
        )

    all_messages = result.get("messages", [])
    tool_names, verified_creations = _extract_creation_info(
        all_messages
    )
    tool_details = _extract_tool_details(all_messages)

    # Find the last non-tool-call AI message as final response
    final_content = ""
    total_tokens = 0
    for msg in all_messages:
        if isinstance(msg, AIMessage):
            usage = getattr(msg, "usage_metadata", None)
            if usage:
                total_tokens += usage.get("total_tokens", 0)
    for msg in reversed(all_messages):
        if isinstance(msg, AIMessage) and not msg.tool_calls:
            final_content = msg.content or ""
            break

    # Append creation verification footer
    creation_tools = {"create_task", "create_research_node"}
    if any(name in creation_tools for name in tool_names):
        verification = (
            "; ".join(verified_creations)
            if verified_creations
            else "none"
        )
        footer = (
            "Verified follow-up entries created via tools: "
            f"{verification}."
        )
        final_content = f"{final_content.strip()}\n\n{footer}" if final_content else footer

    log.info(
        "LangGraph agent completed request=%s tools_used=%d tokens=%d",
        request_id,
        len(tool_names),
        total_tokens,
    )

    return ToolAgentResult(
        content=final_content.strip(),
        provider=provider_name or "unknown",
        model=model_id,
        tokens_used=total_tokens,
        tools_used=tuple(tool_names),
        verified_creations=tuple(verified_creations),
        used_agentic=True,
        tool_details=tuple(tool_details),
    )
