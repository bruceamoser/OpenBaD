"""OpenBaD embedded-skills MCP server.

Uses FastMCP to define every built-in skill with ``@mcp.tool()`` decorators.
Schemas are derived automatically from function signatures, type hints, and
docstrings — no hand-maintained JSON.

Public helpers consumed by the agentic loop:
    ``get_openai_tools()``  – returns OpenAI-format tool schemas for LiteLLM.
    ``call_skill(name, args)``  – dispatches a tool call and returns a string.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

log = logging.getLogger(__name__)

# ── FastMCP instance ──────────────────────────────────────────────────── #

skill_server = FastMCP(
    "OpenBaD Skills",
    instructions=(
        "Embedded skills for OpenBaD — a self-improving, curiosity-driven, "
        "research-first Linux assistant."
    ),
)

# Maximum output length per skill result to avoid ballooning LLM context.
_MAX_RESULT_CHARS = 16_000
_ACCESS_REQUEST_PREFIX = "[access_request]"


def _truncate(text: str) -> str:
    if len(text) > _MAX_RESULT_CHARS:
        return text[:_MAX_RESULT_CHARS] + f"\n... (truncated, {len(text)} chars total)"
    return text


def _fmt_access_request(target: str, detail: str) -> str:
    return (
        f"{_ACCESS_REQUEST_PREFIX} Access to {target} is not currently permitted. {detail}\n"
        "A path access request must be approved in the Toolbelt UI under "
        "Path Access Requests before retrying."
    )


def _fmt_access_with_record(target: str, detail: str, record: dict[str, Any] | None) -> str:
    if not record:
        return _fmt_access_request(target, detail)
    request = record.get("request") if isinstance(record.get("request"), dict) else {}
    if request:
        request_id = str(request.get("request_id", "")).strip()
        root = str(request.get("normalized_root", "")).strip()
        return (
            f"{_ACCESS_REQUEST_PREFIX} Access to {target} is not currently permitted. {detail}\n"
            f"Pending request: {request_id or 'unknown'} for root {root or 'unknown'}.\n"
            "That request is already created. Tell the user to approve it in "
            "Toolbelt -> Path Access Requests, then retry."
        )
    grant = record.get("grant") if isinstance(record.get("grant"), dict) else {}
    if grant:
        return (
            f"{_ACCESS_REQUEST_PREFIX} Access to {target} was previously blocked, "
            "but the root is now granted. Retry the operation."
        )
    return _fmt_access_request(target, detail)


# ── File System ───────────────────────────────────────────────────────── #


@skill_server.tool()
def find_files(
    pattern: str,
    cwd: str = "/",
    limit: int = 50,
) -> str:
    """Find files under a directory using a glob or substring pattern.

    This tool can search anywhere on the filesystem. No permission is needed
    to find files — permission is only required to read or write them.

    Args:
        pattern: Glob-like pattern or plain substring to search for.
        cwd: Directory to search within.  Defaults to "/" so the entire
             filesystem is searched.  Narrow the search by providing a
             specific directory.
        limit: Maximum number of matches to return (default 50).
    """
    from openbad.skills.fs_tool import find_files as _find

    return _find(pattern, cwd=cwd, limit=limit)


@skill_server.tool()
def read_file(path: str) -> str:
    """Read the contents of a file and return the text content.

    Args:
        path: Absolute or relative path to the file.
    """
    from openbad.skills.access_control import create_access_request
    from openbad.skills.fs_tool import read_file as _read

    try:
        return _read(path)
    except PermissionError as exc:
        detail = str(exc)
        if "outside allowed roots" in detail:
            record = create_access_request(
                path, requester="skill:read_file",
                reason="read_file requested access outside allowed roots",
                prefer_parent=True,
            )
            return _fmt_access_with_record(f"path {path!r}", detail, record)
        return f"Error: PermissionError: {detail}"


@skill_server.tool()
def write_file(path: str, content: str) -> str:
    """Write content to a file (creates or overwrites).

    Args:
        path: Absolute or relative path to the file.
        content: Content to write to the file.
    """
    from openbad.skills.access_control import create_access_request
    from openbad.skills.fs_tool import write_file as _write

    try:
        _write(path, content)
    except PermissionError as exc:
        detail = str(exc)
        if "outside allowed roots" in detail:
            record = create_access_request(
                path, requester="skill:write_file",
                reason="write_file requested access outside allowed roots",
                prefer_parent=True,
            )
            return _fmt_access_with_record(f"path {path!r}", detail, record)
        return f"Error: PermissionError: {detail}"
    return f"File written: {path}"


# ── Command Execution ─────────────────────────────────────────────────── #


@skill_server.tool()
async def exec_command(
    command: str,
    args: list[str] | None = None,
    cwd: str | None = None,
) -> str:
    """Execute a shell command and return stdout/stderr.  Runs in a sandboxed environment.

    Args:
        command: The shell command to execute.
        args: Optional list of arguments.
        cwd: Optional working directory.
    """
    from openbad.skills.access_control import create_access_request
    from openbad.skills.cli_tool import CliToolAdapter

    cli = CliToolAdapter()
    result = await cli.async_execute(command, args=args, cwd=cwd)
    parts: list[str] = []
    if result.stdout:
        parts.append(result.stdout)
    if result.returncode == -1 and "allowed roots" in (result.stderr or ""):
        requested_cwd = str(cwd or cli.config.working_directory)
        record = create_access_request(
            requested_cwd, requester="skill:exec_command",
            reason=f"exec_command requested cwd access for command {command}",
            prefer_parent=False,
        )
        return _fmt_access_with_record(
            f"working directory {requested_cwd!r}", result.stderr, record,
        )
    if result.stderr:
        parts.append(f"STDERR: {result.stderr}")
    parts.append(f"(exit code {result.returncode})")
    return "\n".join(parts)


# ── Access Control ────────────────────────────────────────────────────── #


@skill_server.tool()
def get_path_access_status() -> str:
    """Return current approved path roots and pending access requests for tool usage."""
    from openbad.skills.access_control import list_access_grants, list_access_requests

    return json.dumps(
        {"pending_requests": list_access_requests(status="pending"),
         "grants": list_access_grants()},
        indent=2, default=str,
    )


# ── Terminal Sessions ─────────────────────────────────────────────────── #


@skill_server.tool()
def list_terminal_sessions() -> str:
    """List currently active PTY-backed terminal sessions."""
    from openbad.skills.terminal_sessions import get_terminal_session_manager

    return json.dumps(get_terminal_session_manager().list_sessions(), indent=2, default=str)


@skill_server.tool()
def create_terminal_session(
    cwd: str,
    shell: str = "/bin/bash",
    requester: str = "session",
) -> str:
    """Create a PTY-backed interactive terminal session in an approved working directory.

    Args:
        cwd: Working directory for the shell session.
        shell: Shell executable to launch (default /bin/bash).
        requester: Requester identity, such as session or subsystem name.
    """
    from openbad.skills.access_control import create_access_request
    from openbad.skills.terminal_sessions import get_terminal_session_manager

    try:
        session = get_terminal_session_manager().create_session(
            cwd=cwd, requester=requester, shell=shell,
        )
    except PermissionError as exc:
        record = create_access_request(
            cwd, requester="skill:create_terminal_session",
            reason="terminal session requested cwd access outside allowed roots",
            prefer_parent=False,
        )
        return _fmt_access_with_record(f"working directory {cwd!r}", str(exc), record)
    return json.dumps(session, indent=2, default=str)


@skill_server.tool()
def send_terminal_input(
    session_id: str,
    input: str,
    append_newline: bool = True,
) -> str:
    """Send text input to an active PTY terminal session.

    Args:
        session_id: Terminal session identifier.
        input: Text to send to the terminal.
        append_newline: Whether to append a newline after the input (default true).
    """
    from openbad.skills.terminal_sessions import get_terminal_session_manager

    result = get_terminal_session_manager().send_input(
        session_id, input, append_newline=append_newline,
    )
    return json.dumps(result, indent=2, default=str)


@skill_server.tool()
def read_terminal_output(
    session_id: str,
    max_bytes: int = 8192,
) -> str:
    """Read the latest available output from an active PTY terminal session.

    Args:
        session_id: Terminal session identifier.
        max_bytes: Maximum bytes of terminal output to return (default 8192).
    """
    from openbad.skills.terminal_sessions import get_terminal_session_manager

    result = get_terminal_session_manager().read_output(session_id, max_bytes=max_bytes)
    return json.dumps(result, indent=2, default=str)


@skill_server.tool()
def close_terminal_session(
    session_id: str,
    reason: str = "session-request",
) -> str:
    """Close an active PTY terminal session and release resources.

    Args:
        session_id: Terminal session identifier.
        reason: Reason for closing the session.
    """
    from openbad.skills.terminal_sessions import get_terminal_session_manager

    result = get_terminal_session_manager().close_session(session_id, reason=reason)
    return json.dumps(result, indent=2, default=str)


# ── Web ───────────────────────────────────────────────────────────────── #


@skill_server.tool()
def web_search(query: str) -> str:
    """Search the web using the configured search engine.  Returns results with title, URL, and snippet.

    Args:
        query: The search query.
    """
    from openbad.skills.web_search import WebSearchToolAdapter

    adapter = WebSearchToolAdapter()
    results = adapter.search(query)
    return json.dumps(
        [{"title": r.title, "url": r.url, "snippet": r.snippet} for r in results],
        indent=2,
    )


@skill_server.tool()
def web_fetch(url: str) -> str:
    """Fetch the text content of a web page given its URL.

    Args:
        url: The URL to fetch.
    """
    from openbad.skills.web_search import web_fetch as _fetch

    return _fetch(url)


# ── User Interaction ──────────────────────────────────────────────────── #


@skill_server.tool()
def ask_user(question: str) -> str:
    """Ask the user a question and wait for their response.  Use when you need clarification or confirmation.

    Args:
        question: The question to ask the user.
    """
    return (
        f"[question_pending] The agent wants to ask: {question}\n"
        "(Waiting for user response via the WUI.)"
    )


# ── System Diagnostics ────────────────────────────────────────────────── #


@skill_server.tool()
async def get_mqtt_records(limit: int = 100) -> str:
    """Retrieve recent MQTT messages from the nervous system bus.

    Args:
        limit: Maximum number of records to return (default 100).
    """
    from openbad.skills.mqtt_records_tool import MqttRecordsToolAdapter

    adapter = MqttRecordsToolAdapter()
    records = await asyncio.to_thread(adapter.get_mqtt_records, limit=limit)
    return json.dumps(records, indent=2, default=str)


@skill_server.tool()
async def get_system_logs(limit: int = 200, system: str = "") -> str:
    """Retrieve recent persistent system log events.  Optionally filter by source subsystem.

    Args:
        limit: Maximum number of log lines (default 200).
        system: Optional source module filter (e.g. 'wui', 'endocrine').
    """
    from openbad.skills.event_log_tool import EventLogToolAdapter

    adapter = EventLogToolAdapter()
    events = await asyncio.to_thread(adapter.read_events, limit=limit, source=system)
    return json.dumps(events, indent=2, default=str)


@skill_server.tool()
async def read_events(
    limit: int = 100,
    level: str = "",
    source: str = "",
    search: str = "",
) -> str:
    """Read entries from the persistent event log.  Supports filtering by level, source, and text search.

    Args:
        limit: Maximum number of events (default 100).
        level: Filter by level (e.g. 'INFO', 'WARNING', 'ERROR').
        source: Filter by event source.
        search: Text search within event messages.
    """
    from openbad.skills.event_log_tool import EventLogToolAdapter

    adapter = EventLogToolAdapter()
    events = await asyncio.to_thread(
        adapter.read_events, limit=limit, level=level, source=source, search=search,
    )
    return json.dumps(events, indent=2, default=str)


@skill_server.tool()
async def write_event(
    message: str,
    level: str = "INFO",
    source: str = "system",
) -> str:
    """Write a new entry to the persistent event log.

    Args:
        message: The event message.
        level: Log level (default 'INFO').  One of DEBUG, INFO, WARNING, ERROR, CRITICAL.
        source: Event source identifier (default 'system').
    """
    from openbad.skills.event_log_tool import EventLogToolAdapter

    adapter = EventLogToolAdapter()
    ok = await asyncio.to_thread(adapter.write_event, message=message, level=level, source=source)
    return "Event logged." if ok else "Failed to log event."


@skill_server.tool()
async def get_endocrine_status() -> str:
    """Get the current endocrine system hormone levels (cortisol, dopamine, serotonin, etc.)."""
    from openbad.skills.endocrine_status_tool import EndocrineStatusToolAdapter

    adapter = EndocrineStatusToolAdapter()
    status = await asyncio.to_thread(adapter.get_endocrine_status)
    return json.dumps(status, indent=2, default=str)


@skill_server.tool()
async def call_doctor(
    reason: str,
    source: str = "session",
    context: dict[str, Any] | None = None,
) -> str:
    """Request a doctor visit over the embedded MQTT bus with a reason and optional context.

    Args:
        reason: Why the doctor should be called.
        source: Requester identity, such as session or subsystem name.
        context: Optional structured context payload for the doctor.
    """
    from openbad.skills.doctor_tool import DoctorToolAdapter

    adapter = DoctorToolAdapter()
    result = await asyncio.to_thread(adapter.call_doctor, reason, source=source, context=context)
    return json.dumps(result, indent=2, default=str)


# ── Task Management ───────────────────────────────────────────────────── #


@skill_server.tool()
async def get_tasks() -> str:
    """Retrieve the current task list from the task manager."""
    from openbad.skills.tasks_diagnostics_tool import TasksDiagnosticsToolAdapter

    adapter = TasksDiagnosticsToolAdapter()
    tasks = await asyncio.to_thread(adapter.get_tasks)
    return json.dumps(tasks, indent=2, default=str)


@skill_server.tool()
async def create_task(
    title: str,
    description: str = "",
    owner: str = "user",
) -> str:
    """Create a new task in the task manager.

    Args:
        title: The task title.
        description: Optional task description.
        owner: Task owner ('user' or 'agent', default 'user').
    """
    from openbad.skills.tasks_diagnostics_tool import TasksDiagnosticsToolAdapter

    adapter = TasksDiagnosticsToolAdapter()
    result = await asyncio.to_thread(adapter.create_task, title=title, description=description, owner=owner)
    return json.dumps(result, indent=2, default=str)


@skill_server.tool()
async def update_task(
    task_id: str,
    title: str | None = None,
    description: str | None = None,
    owner: str | None = None,
) -> str:
    """Update mutable fields on an existing task.

    Args:
        task_id: The task ID to update.
        title: Optional updated title.
        description: Optional updated description.
        owner: Optional updated owner.
    """
    from openbad.skills.tasks_diagnostics_tool import TasksDiagnosticsToolAdapter

    adapter = TasksDiagnosticsToolAdapter()
    result = await asyncio.to_thread(
        adapter.update_task, task_id, title=title, description=description, owner=owner,
    )
    return json.dumps(result, indent=2, default=str)


@skill_server.tool()
async def complete_task(task_id: str) -> str:
    """Mark an existing task complete.

    Args:
        task_id: The task ID to complete.
    """
    from openbad.skills.tasks_diagnostics_tool import TasksDiagnosticsToolAdapter

    adapter = TasksDiagnosticsToolAdapter()
    result = await asyncio.to_thread(adapter.complete_task, task_id)
    return json.dumps(result, indent=2, default=str)


@skill_server.tool()
async def work_on_next_task(
    source: str = "session",
    reason: str = "next task requested",
) -> str:
    """Queue an event requesting work on the next eligible task.

    Args:
        source: Requester identity, such as session or subsystem name.
        reason: Why the next task should be processed now.
    """
    from openbad.skills.tasks_diagnostics_tool import TasksDiagnosticsToolAdapter

    adapter = TasksDiagnosticsToolAdapter()
    result = await asyncio.to_thread(adapter.work_on_next_task, source=source, reason=reason)
    return json.dumps(result, indent=2, default=str)


@skill_server.tool()
async def work_on_task(
    task_id: str,
    source: str = "session",
    reason: str = "specific task requested",
) -> str:
    """Queue an event requesting work on a specific task by ID.

    Args:
        task_id: The task ID to work on.
        source: Requester identity, such as session or subsystem name.
        reason: Why this specific task should be processed now.
    """
    from openbad.skills.tasks_diagnostics_tool import TasksDiagnosticsToolAdapter

    adapter = TasksDiagnosticsToolAdapter()
    result = await asyncio.to_thread(adapter.work_on_task, task_id, source=source, reason=reason)
    return json.dumps(result, indent=2, default=str)


# ── Research Management ───────────────────────────────────────────────── #


@skill_server.tool()
async def get_research_nodes() -> str:
    """List current research queue nodes."""
    from openbad.skills.research_diagnostics_tool import ResearchDiagnosticsToolAdapter

    adapter = ResearchDiagnosticsToolAdapter()
    nodes = await asyncio.to_thread(adapter.get_research_nodes)
    return json.dumps(nodes, indent=2, default=str)


@skill_server.tool()
async def create_research_node(
    title: str,
    description: str = "",
    priority: int = 0,
) -> str:
    """Create a new research node for exploration.

    Args:
        title: Research topic title.
        description: Optional description of the research question.
        priority: Priority (0 is normal, higher is more urgent).
    """
    from openbad.skills.research_diagnostics_tool import ResearchDiagnosticsToolAdapter

    adapter = ResearchDiagnosticsToolAdapter()
    result = await asyncio.to_thread(
        adapter.create_research_node, title=title, description=description, priority=priority,
    )
    return json.dumps(result, indent=2, default=str)


@skill_server.tool()
async def update_research_node(
    node_id: str,
    title: str | None = None,
    description: str | None = None,
    priority: int | None = None,
    source_task_id: str | None = None,
) -> str:
    """Update a pending research node.

    Args:
        node_id: The research node ID to update.
        title: Optional updated title.
        description: Optional updated description.
        priority: Optional updated priority.
        source_task_id: Optional related task ID.
    """
    from openbad.skills.research_diagnostics_tool import ResearchDiagnosticsToolAdapter

    adapter = ResearchDiagnosticsToolAdapter()
    result = await asyncio.to_thread(
        adapter.update_research_node, node_id,
        title=title, description=description,
        priority=priority, source_task_id=source_task_id,
    )
    return json.dumps(result, indent=2, default=str)


@skill_server.tool()
async def complete_research_node(node_id: str) -> str:
    """Mark a research node complete.

    Args:
        node_id: The research node ID to complete.
    """
    from openbad.skills.research_diagnostics_tool import ResearchDiagnosticsToolAdapter

    adapter = ResearchDiagnosticsToolAdapter()
    result = await asyncio.to_thread(adapter.complete_research_node, node_id)
    return json.dumps(result, indent=2, default=str)


@skill_server.tool()
async def work_on_next_research(
    source: str = "session",
    reason: str = "next research requested",
) -> str:
    """Queue an event requesting work on the next eligible research item.

    Args:
        source: Requester identity, such as session or subsystem name.
        reason: Why the next research item should be processed now.
    """
    from openbad.skills.research_diagnostics_tool import ResearchDiagnosticsToolAdapter

    adapter = ResearchDiagnosticsToolAdapter()
    result = await asyncio.to_thread(adapter.work_on_next_research, source=source, reason=reason)
    return json.dumps(result, indent=2, default=str)


@skill_server.tool()
async def work_on_research(
    node_id: str,
    source: str = "session",
    reason: str = "specific research requested",
) -> str:
    """Queue an event requesting work on a specific research item by ID.

    Args:
        node_id: The research node ID to work on.
        source: Requester identity, such as session or subsystem name.
        reason: Why this specific research item should be processed now.
    """
    from openbad.skills.research_diagnostics_tool import ResearchDiagnosticsToolAdapter

    adapter = ResearchDiagnosticsToolAdapter()
    result = await asyncio.to_thread(adapter.work_on_research, node_id, source=source, reason=reason)
    return json.dumps(result, indent=2, default=str)


# ── MCP Bridge (meta-tool for external MCP servers) ──────────────────── #


@skill_server.tool()
async def mcp_bridge(
    server: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> str:
    """Call a tool on a connected MCP (Model Context Protocol) server.

    Args:
        server: Name of the MCP server to call.
        tool_name: Name of the tool on the MCP server.
        arguments: Arguments to pass to the MCP tool.
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {server: {"command": server, "args": [], "transport": "stdio"}}
    )
    try:
        tools = await client.get_tools()
    except FileNotFoundError:
        return json.dumps(
            {"error": f"MCP server binary '{server}' not found. "
             "Install the server or check the name."},
            indent=2,
        )
    except Exception as exc:
        return json.dumps(
            {"error": f"Failed to connect to MCP server '{server}': {exc}"},
            indent=2,
        )

    matching = [t for t in tools if t.name == tool_name]
    if not matching:
        available = [t.name for t in tools]
        return json.dumps(
            {"error": f"Tool '{tool_name}' not found on server '{server}'.",
             "available_tools": available},
            indent=2,
        )

    try:
        result = await matching[0].ainvoke(arguments or {})
    except Exception as exc:
        return json.dumps(
            {"error": f"Tool '{tool_name}' failed: {exc}"},
            indent=2,
        )
    return json.dumps(result, indent=2, default=str) if not isinstance(result, str) else result


# ── Self-introspection ───────────────────────────────────────────────── #


@skill_server.tool()
async def list_embedded_skills() -> str:
    """List all embedded skills available to you in this session.

    Call this when asked about your tools, capabilities, or embedded skills.
    These are YOUR tools — you call them directly by name.
    """
    tools = await skill_server.list_tools()
    lines = [f"You have {len(tools)} embedded skills:\n"]
    for t in tools:
        lines.append(f"- **{t.name}**: {t.description.splitlines()[0]}")
    return "\n".join(lines)


# ── Public API for the agentic loop ──────────────────────────────────── #


def _mcp_tool_to_openai(tool: Any) -> dict[str, Any]:
    """Convert a single MCP Tool object to OpenAI function-calling format."""
    schema = tool.inputSchema or {}
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": {
                "type": schema.get("type", "object"),
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
            },
        },
    }


async def _async_get_openai_tools() -> list[dict[str, Any]]:
    """Async helper — query the MCP server for tools and convert schemas."""
    mcp_tools = await skill_server.list_tools()
    return [_mcp_tool_to_openai(t) for t in mcp_tools]


def get_openai_tools() -> list[dict[str, Any]]:
    """Return OpenAI-format tool schemas for all embedded skills.

    Designed to be called from both sync and async contexts.  If an event loop
    is already running (e.g. inside aiohttp), this uses eager evaluation via
    a cached result so it doesn't block.
    """
    # Cache to avoid repeated MCP queries during a single server lifetime.
    if not hasattr(get_openai_tools, "_cache"):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Create a future and schedule the coroutine
            import concurrent.futures
            future: concurrent.futures.Future[list[dict[str, Any]]] = concurrent.futures.Future()

            async def _populate() -> None:
                try:
                    result = await _async_get_openai_tools()
                    future.set_result(result)
                except Exception as exc:
                    future.set_exception(exc)

            loop.create_task(_populate())
            # We can't block here, so return empty on first call and rely
            # on the cache being populated for subsequent calls.
            # Better approach: eagerly populate at startup.
            return []
        else:
            get_openai_tools._cache = asyncio.run(_async_get_openai_tools())  # type: ignore[attr-defined]
    return get_openai_tools._cache  # type: ignore[attr-defined]


async def async_get_openai_tools() -> list[dict[str, Any]]:
    """Async version — always preferred when an event loop is available."""
    if not hasattr(get_openai_tools, "_cache"):
        get_openai_tools._cache = await _async_get_openai_tools()  # type: ignore[attr-defined]
    return get_openai_tools._cache  # type: ignore[attr-defined]


async def call_skill(name: str, arguments: dict[str, Any]) -> str:
    """Dispatch a skill call by name and return a plain-text result string.

    This replaces the old ``dispatch_tool_call()`` for embedded skills.
    """
    try:
        result = await skill_server.call_tool(name, arguments)
        # result is (content_list, structured_content | None)
        content_list, _structured = result
        parts: list[str] = []
        for item in content_list:
            if hasattr(item, "text"):
                parts.append(item.text)
            else:
                parts.append(str(item))
        text = "\n".join(parts) if parts else ""
        return _truncate(text)
    except Exception as exc:
        log.warning("Skill %s raised: %s", name, exc, exc_info=True)
        return f"Error executing {name}: {type(exc).__name__}: {exc}"
