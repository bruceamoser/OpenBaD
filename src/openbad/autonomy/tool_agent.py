from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

from openbad.skills import call_skill
from openbad.skills.server import async_get_openai_tools

log = logging.getLogger(__name__)

_MAX_TOOL_ITERATIONS = 6
_TOOL_CALL_TIMEOUT_S = 20

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


def build_tooling_system_prompt(base_prompt: str) -> str:
    return f"{_TOOLING_BASE_PROMPT}\n\n{base_prompt.strip()}"


def _parse_json_object(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _record_verified_creation(
    tool_name: str,
    tool_args: dict[str, Any],
    raw_result: str,
    verified_creations: list[str],
) -> None:
    payload = _parse_json_object(raw_result)
    if tool_name == "create_task":
        task_id = str(payload.get("task_id", "")).strip()
        if not task_id:
            return
        title = str(payload.get("title") or tool_args.get("title") or task_id).strip()
        verified_creations.append(f"task '{title}' ({task_id})")
        return
    if tool_name == "create_research_node":
        node_id = str(payload.get("node_id", "")).strip()
        if not node_id:
            return
        title = str(payload.get("title") or tool_args.get("title") or node_id).strip()
        verified_creations.append(f"research '{title}' ({node_id})")


def _append_verified_creation_footer(
    content: str,
    tools_used: list[str],
    verified_creations: list[str],
) -> str:
    creation_tools = {"create_task", "create_research_node"}
    if not any(tool_name in creation_tools for tool_name in tools_used):
        return content.strip()

    verification = "; ".join(verified_creations) if verified_creations else "none"
    footer = f"Verified follow-up entries created via tools: {verification}."
    base = content.strip()
    if not base:
        return footer
    return f"{base}\n\n{footer}"


async def run_tool_agent(
    adapter: Any,
    model_id: str,
    *,
    provider_name: str,
    system_prompt: str,
    user_prompt: str,
    request_id: str,
    tool_call_validator: Callable[[str, dict[str, Any]], str | None] | None = None,
) -> ToolAgentResult:
    agentic_complete = getattr(adapter, "agentic_complete", None)
    if not callable(agentic_complete):
        prompt = f"{system_prompt.strip()}\n\n{user_prompt.strip()}"
        result = await adapter.complete(prompt, model_id)
        return ToolAgentResult(
            content=result.content.strip(),
            provider=provider_name or getattr(result, "provider", "") or "unknown",
            model=getattr(result, "model_id", "") or model_id,
            tokens_used=int(getattr(result, "tokens_used", 0) or 0),
            used_agentic=False,
        )

    total_tokens = 0
    tool_names_used: list[str] = []
    verified_creations: list[str] = []
    working_messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    skill_schemas = await async_get_openai_tools()
    for iteration in range(_MAX_TOOL_ITERATIONS):
        response = await agentic_complete(working_messages, model_id, tools=skill_schemas)
        usage = getattr(response, "usage", None)
        total_tokens += int(getattr(usage, "total_tokens", 0) or 0)

        choice = response.choices[0] if getattr(response, "choices", None) else None
        if choice is None:
            return ToolAgentResult(
                content="",
                provider=provider_name or "unknown",
                model=model_id,
                tokens_used=total_tokens,
                tools_used=tuple(tool_names_used),
                verified_creations=tuple(verified_creations),
                used_agentic=True,
            )

        assistant_msg = choice.message
        tool_calls = getattr(assistant_msg, "tool_calls", None) or []

        if not tool_calls:
            return ToolAgentResult(
                content=_append_verified_creation_footer(
                    assistant_msg.content or "",
                    tool_names_used,
                    verified_creations,
                ),
                provider=provider_name or "unknown",
                model=getattr(response, "model", "") or model_id,
                tokens_used=total_tokens,
                tools_used=tuple(tool_names_used),
                verified_creations=tuple(verified_creations),
                used_agentic=True,
            )

        if hasattr(assistant_msg, "model_dump"):
            working_messages.append(assistant_msg.model_dump(exclude_none=True))
        else:
            working_messages.append({
                "role": "assistant",
                "content": getattr(assistant_msg, "content", "") or "",
            })

        for tool_call in tool_calls:
            fn_name = tool_call.function.name
            try:
                fn_args = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}
            except (json.JSONDecodeError, TypeError):
                fn_args = {}

            tool_names_used.append(fn_name)
            log.info(
                "Autonomy tool call request=%s iter=%d tool=%s args=%s",
                request_id,
                iteration + 1,
                fn_name,
                json.dumps(fn_args, default=str)[:200],
            )
            rejection_reason = tool_call_validator(fn_name, fn_args) if callable(tool_call_validator) else None
            if rejection_reason:
                result = rejection_reason
                log.info(
                    "Autonomy tool blocked request=%s iter=%d tool=%s reason=%s",
                    request_id,
                    iteration + 1,
                    fn_name,
                    rejection_reason,
                )
            else:
                try:
                    import asyncio as _aio

                    result = await _aio.wait_for(
                        call_skill(fn_name, fn_args),
                        timeout=_TOOL_CALL_TIMEOUT_S,
                    )
                except TimeoutError:
                    result = f"Tool {fn_name} timed out after {_TOOL_CALL_TIMEOUT_S}s"
                    log.warning("Autonomy tool timeout request=%s tool=%s", request_id, fn_name)

            _record_verified_creation(fn_name, fn_args, result, verified_creations)

            working_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )

    working_messages.append(
        {
            "role": "user",
            "content": (
                "You have reached the maximum number of tool calls."
                " Provide the best final answer you can, grounded only in the tool results"
                " and context already gathered."
            ),
        }
    )
    response = await agentic_complete(working_messages, model_id)
    usage = getattr(response, "usage", None)
    total_tokens += int(getattr(usage, "total_tokens", 0) or 0)
    choice = response.choices[0] if getattr(response, "choices", None) else None
    content = (choice.message.content or "") if choice is not None else ""
    return ToolAgentResult(
        content=_append_verified_creation_footer(content, tool_names_used, verified_creations),
        provider=provider_name or "unknown",
        model=getattr(response, "model", "") or model_id,
        tokens_used=total_tokens,
        tools_used=tuple(tool_names_used),
        verified_creations=tuple(verified_creations),
        used_agentic=True,
    )