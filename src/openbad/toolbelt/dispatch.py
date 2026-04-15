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
_ACCESS_REQUEST_PREFIX = "[access_request]"


def _truncate(text: str) -> str:
    if len(text) > _MAX_RESULT_CHARS:
        return text[:_MAX_RESULT_CHARS] + f"\n... (truncated, {len(text)} chars total)"
    return text


def _format_access_request(target: str, detail: str) -> str:
    return (
        f"{_ACCESS_REQUEST_PREFIX} Access to {target} is not currently permitted. {detail}\n"
        "A path access request must be approved in the Toolbelt UI under Path Access Requests before retrying."
    )


def _format_access_request_with_record(target: str, detail: str, record: dict[str, Any] | None) -> str:
    if not record:
        return _format_access_request(target, detail)
    request = record.get("request") if isinstance(record.get("request"), dict) else {}
    if request:
        request_id = str(request.get("request_id", "")).strip()
        root = str(request.get("normalized_root", "")).strip()
        return (
            f"{_ACCESS_REQUEST_PREFIX} Access to {target} is not currently permitted. {detail}\n"
            f"Pending request: {request_id or 'unknown'} for root {root or 'unknown'}.\n"
            "That request is already created. Tell the user to approve it in Toolbelt -> Path Access Requests, then retry."
        )
    grant = record.get("grant") if isinstance(record.get("grant"), dict) else {}
    if grant:
        return (
            f"{_ACCESS_REQUEST_PREFIX} Access to {target} was previously blocked, but the root is now granted. "
            "Retry the operation."
        )
    return _format_access_request(target, detail)


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
        from openbad.toolbelt.access_control import create_access_request

        try:
            return read_file(args["path"])
        except PermissionError as exc:
            detail = str(exc)
            if "outside allowed roots" in detail:
                record = create_access_request(
                    args["path"],
                    requester="tool:read_file",
                    reason="read_file requested access outside allowed roots",
                    prefer_parent=True,
                )
                return _format_access_request_with_record(f"path {args['path']!r}", detail, record)
            return f"Error executing read_file: PermissionError: {detail}"

    if name == "write_file":
        from openbad.toolbelt.fs_tool import write_file
        from openbad.toolbelt.access_control import create_access_request

        try:
            write_file(args["path"], args["content"])
        except PermissionError as exc:
            detail = str(exc)
            if "outside allowed roots" in detail:
                record = create_access_request(
                    args["path"],
                    requester="tool:write_file",
                    reason="write_file requested access outside allowed roots",
                    prefer_parent=True,
                )
                return _format_access_request_with_record(f"path {args['path']!r}", detail, record)
            return f"Error executing write_file: PermissionError: {detail}"
        return f"File written: {args['path']}"

    if name == "exec_command":
        from openbad.toolbelt.access_control import create_access_request
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
        if result.returncode == -1 and "allowed roots" in (result.stderr or ""):
            requested_cwd = str(args.get("cwd") or cli.config.working_directory)
            record = create_access_request(
                requested_cwd,
                requester="tool:exec_command",
                reason=f"exec_command requested cwd access for command {args.get('command', '')}",
                prefer_parent=False,
            )
            return _format_access_request_with_record(
                f"working directory {requested_cwd!r}",
                result.stderr,
                record,
            )
        if result.stderr:
            parts.append(f"STDERR: {result.stderr}")
        parts.append(f"(exit code {result.returncode})")
        return "\n".join(parts)

    if name == "get_path_access_status":
        from openbad.toolbelt.access_control import list_access_grants, list_access_requests

        return json.dumps(
            {
                "pending_requests": list_access_requests(status="pending"),
                "grants": list_access_grants(),
            },
            indent=2,
            default=str,
        )

    if name == "list_terminal_sessions":
        from openbad.toolbelt.terminal_sessions import get_terminal_session_manager

        return json.dumps(get_terminal_session_manager().list_sessions(), indent=2, default=str)

    if name == "create_terminal_session":
        from openbad.toolbelt.access_control import create_access_request
        from openbad.toolbelt.terminal_sessions import get_terminal_session_manager

        manager = get_terminal_session_manager()
        try:
            session = manager.create_session(
                cwd=str(args.get("cwd") or "."),
                requester=str(args.get("requester") or "session"),
                shell=str(args.get("shell") or "/bin/bash"),
            )
        except PermissionError as exc:
            requested_cwd = str(args.get("cwd") or ".")
            record = create_access_request(
                requested_cwd,
                requester="tool:create_terminal_session",
                reason="terminal session requested cwd access outside allowed roots",
                prefer_parent=False,
            )
            return _format_access_request_with_record(f"working directory {requested_cwd!r}", str(exc), record)
        return json.dumps(session, indent=2, default=str)

    if name == "send_terminal_input":
        from openbad.toolbelt.terminal_sessions import get_terminal_session_manager

        result = get_terminal_session_manager().send_input(
            str(args["session_id"]),
            str(args.get("input", "")),
            append_newline=bool(args.get("append_newline", True)),
        )
        return json.dumps(result, indent=2, default=str)

    if name == "read_terminal_output":
        from openbad.toolbelt.terminal_sessions import get_terminal_session_manager

        result = get_terminal_session_manager().read_output(
            str(args["session_id"]),
            max_bytes=int(args.get("max_bytes", 8192)),
        )
        return json.dumps(result, indent=2, default=str)

    if name == "close_terminal_session":
        from openbad.toolbelt.terminal_sessions import get_terminal_session_manager

        result = get_terminal_session_manager().close_session(
            str(args["session_id"]),
            reason=str(args.get("reason") or "session-request"),
        )
        return json.dumps(result, indent=2, default=str)

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
        from openbad.toolbelt.event_log_tool import EventLogToolAdapter

        adapter = EventLogToolAdapter()
        events = await asyncio.to_thread(
            adapter.read_events,
            limit=args.get("limit", 200),
            source=args.get("system", ""),
        )
        return json.dumps(events, indent=2, default=str)

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

    if name == "call_doctor":
        from openbad.toolbelt.doctor_tool import DoctorToolAdapter

        adapter = DoctorToolAdapter()
        result = await asyncio.to_thread(
            adapter.call_doctor,
            args["reason"],
            source=args.get("source", "session"),
            context=args.get("context") if isinstance(args.get("context"), dict) else None,
        )
        return json.dumps(result, indent=2, default=str)

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

    if name == "update_task":
        from openbad.toolbelt.tasks_diagnostics_tool import TasksDiagnosticsToolAdapter

        adapter = TasksDiagnosticsToolAdapter()
        result = await asyncio.to_thread(
            adapter.update_task,
            args["task_id"],
            title=args.get("title"),
            description=args.get("description"),
            owner=args.get("owner"),
        )
        return json.dumps(result, indent=2, default=str)

    if name == "complete_task":
        from openbad.toolbelt.tasks_diagnostics_tool import TasksDiagnosticsToolAdapter

        adapter = TasksDiagnosticsToolAdapter()
        result = await asyncio.to_thread(adapter.complete_task, args["task_id"])
        return json.dumps(result, indent=2, default=str)

    if name == "work_on_next_task":
        from openbad.toolbelt.tasks_diagnostics_tool import TasksDiagnosticsToolAdapter

        adapter = TasksDiagnosticsToolAdapter()
        result = await asyncio.to_thread(
            adapter.work_on_next_task,
            source=args.get("source", "session"),
            reason=args.get("reason", "next task requested"),
        )
        return json.dumps(result, indent=2, default=str)

    if name == "work_on_task":
        from openbad.toolbelt.tasks_diagnostics_tool import TasksDiagnosticsToolAdapter

        adapter = TasksDiagnosticsToolAdapter()
        result = await asyncio.to_thread(
            adapter.work_on_task,
            args["task_id"],
            source=args.get("source", "session"),
            reason=args.get("reason", "specific task requested"),
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

    if name == "update_research_node":
        from openbad.toolbelt.research_diagnostics_tool import ResearchDiagnosticsToolAdapter

        adapter = ResearchDiagnosticsToolAdapter()
        result = await asyncio.to_thread(
            adapter.update_research_node,
            args["node_id"],
            title=args.get("title"),
            description=args.get("description"),
            priority=args.get("priority"),
            source_task_id=args.get("source_task_id"),
        )
        return json.dumps(result, indent=2, default=str)

    if name == "complete_research_node":
        from openbad.toolbelt.research_diagnostics_tool import ResearchDiagnosticsToolAdapter

        adapter = ResearchDiagnosticsToolAdapter()
        result = await asyncio.to_thread(adapter.complete_research_node, args["node_id"])
        return json.dumps(result, indent=2, default=str)

    if name == "work_on_next_research":
        from openbad.toolbelt.research_diagnostics_tool import ResearchDiagnosticsToolAdapter

        adapter = ResearchDiagnosticsToolAdapter()
        result = await asyncio.to_thread(
            adapter.work_on_next_research,
            source=args.get("source", "session"),
            reason=args.get("reason", "next research requested"),
        )
        return json.dumps(result, indent=2, default=str)

    if name == "work_on_research":
        from openbad.toolbelt.research_diagnostics_tool import ResearchDiagnosticsToolAdapter

        adapter = ResearchDiagnosticsToolAdapter()
        result = await asyncio.to_thread(
            adapter.work_on_research,
            args["node_id"],
            source=args.get("source", "session"),
            reason=args.get("reason", "specific research requested"),
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
