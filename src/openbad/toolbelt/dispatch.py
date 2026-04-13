"""Dispatch tool calls from the agentic loop to actual tool adapters.

Each dispatcher function accepts the parsed JSON arguments from the LLM's
tool_call and returns a plain-text result string for injection back into the
conversation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

log = logging.getLogger(__name__)

# Maximum output length per tool result to avoid ballooning context.
_MAX_RESULT_CHARS = 16_000


def _truncate(text: str) -> str:
    if len(text) > _MAX_RESULT_CHARS:
        return text[:_MAX_RESULT_CHARS] + f"\n... (truncated, {len(text)} chars total)"
    return text


async def dispatch_tool_call(name: str, arguments: dict[str, Any]) -> str:
    """Execute a tool by *name* with the given *arguments*.

    Returns a string result suitable for a ``tool`` role message.
    """
    try:
        result = await _dispatch(name, arguments)
        return _truncate(str(result))
    except Exception as exc:
        log.warning("Tool %s raised: %s", name, exc, exc_info=True)
        return f"Error executing {name}: {type(exc).__name__}: {exc}"


async def _dispatch(name: str, args: dict[str, Any]) -> str:
    """Route to the correct adapter."""

    if name == "read_file":
        from openbad.toolbelt.fs_tool import read_file

        return read_file(args["path"])

    if name == "write_file":
        from openbad.toolbelt.fs_tool import write_file

        write_file(args["path"], args["content"])
        return f"File written: {args['path']}"

    if name == "exec_command":
        from openbad.toolbelt.cli_tool import CliToolAdapter

        cli = CliToolAdapter()
        result = await cli.async_execute(
            args["command"],
            args=args.get("args"),
            cwd=args.get("cwd"),
        )
        parts = []
        if result.stdout:
            parts.append(result.stdout)
        if result.stderr:
            parts.append(f"STDERR: {result.stderr}")
        parts.append(f"(exit code {result.exit_code})")
        return "\n".join(parts)

    if name == "web_search":
        from openbad.toolbelt.web_search import WebSearchToolAdapter

        adapter = WebSearchToolAdapter()
        results = adapter.search(args["query"])
        return json.dumps(
            [{"title": r.title, "url": r.url, "snippet": r.snippet} for r in results],
            indent=2,
        )

    if name == "web_fetch":
        from openbad.toolbelt.web_search import web_fetch

        return web_fetch(args["url"])

    if name == "ask_user":
        # ask_user requires a live WUI connection — return a pending message
        # so the LLM knows to wait.  The WUI layer surfaces this to the user.
        return (
            f"[question_pending] The agent wants to ask: {args['question']}\n"
            "(Waiting for user response via the WUI.)"
        )

    if name == "get_mqtt_records":
        from openbad.toolbelt.mqtt_records_tool import MqttRecordsToolAdapter

        adapter = MqttRecordsToolAdapter()
        records = await asyncio.to_thread(
            adapter.get_mqtt_records, limit=args.get("limit", 100),
        )
        return json.dumps(records, indent=2, default=str)

    if name == "get_system_logs":
        from openbad.toolbelt.system_logs_tool import SystemLogsToolAdapter

        adapter = SystemLogsToolAdapter()
        logs = await asyncio.to_thread(
            adapter.get_system_logs,
            limit=args.get("limit", 200),
            system=args.get("system", ""),
        )
        return json.dumps(logs, indent=2, default=str)

    if name == "read_events":
        from openbad.toolbelt.event_log_tool import EventLogToolAdapter

        adapter = EventLogToolAdapter()
        events = await asyncio.to_thread(
            adapter.read_events,
            limit=args.get("limit", 100),
            level=args.get("level", ""),
            source=args.get("source", ""),
            search=args.get("search", ""),
        )
        return json.dumps(events, indent=2, default=str)

    if name == "write_event":
        from openbad.toolbelt.event_log_tool import EventLogToolAdapter

        adapter = EventLogToolAdapter()
        ok = await asyncio.to_thread(
            adapter.write_event,
            message=args["message"],
            level=args.get("level", "INFO"),
            source=args.get("source", "system"),
        )
        return "Event logged." if ok else "Failed to log event."

    if name == "get_endocrine_status":
        from openbad.toolbelt.endocrine_status_tool import EndocrineStatusToolAdapter

        adapter = EndocrineStatusToolAdapter()
        status = await asyncio.to_thread(adapter.get_endocrine_status)
        return json.dumps(status, indent=2, default=str)

    if name == "get_tasks":
        from openbad.toolbelt.tasks_diagnostics_tool import TasksDiagnosticsToolAdapter

        adapter = TasksDiagnosticsToolAdapter()
        tasks = await asyncio.to_thread(adapter.get_tasks)
        return json.dumps(tasks, indent=2, default=str)

    if name == "create_task":
        from openbad.toolbelt.tasks_diagnostics_tool import TasksDiagnosticsToolAdapter

        adapter = TasksDiagnosticsToolAdapter()
        result = await asyncio.to_thread(
            adapter.create_task,
            title=args["title"],
            description=args.get("description", ""),
            owner=args.get("owner", "user"),
        )
        return json.dumps(result, indent=2, default=str)

    if name == "get_research_nodes":
        from openbad.toolbelt.research_diagnostics_tool import ResearchDiagnosticsToolAdapter

        adapter = ResearchDiagnosticsToolAdapter()
        nodes = await asyncio.to_thread(adapter.get_research_nodes)
        return json.dumps(nodes, indent=2, default=str)

    if name == "create_research_node":
        from openbad.toolbelt.research_diagnostics_tool import ResearchDiagnosticsToolAdapter

        adapter = ResearchDiagnosticsToolAdapter()
        result = await asyncio.to_thread(
            adapter.create_research_node,
            title=args["title"],
            description=args.get("description", ""),
            priority=args.get("priority", 0),
        )
        return json.dumps(result, indent=2, default=str)

    if name == "mcp_bridge":
        from openbad.toolbelt.mcp_bridge import MCPRunner

        server = args["server"]
        tool_name = args["tool_name"]
        tool_args = args.get("arguments", {})
        # MCP bridge requires an async context — run inline
        runner = MCPRunner.stdio([server])
        async with runner:
            result = await runner.call_tool(tool_name, tool_args)
        return json.dumps(result, indent=2, default=str) if not isinstance(result, str) else result

    return f"Unknown tool: {name}"
