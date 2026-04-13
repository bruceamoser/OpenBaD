"""Generate OpenAI-format tool JSON schemas for the agentic loop.

Each schema follows the format expected by LiteLLM / OpenAI:
    {
        "type": "function",
        "function": {
            "name": "<tool_name>",
            "description": "<one-liner>",
            "parameters": {
                "type": "object",
                "properties": { ... },
                "required": [ ... ],
            },
        },
    }
"""

from __future__ import annotations

from typing import Any


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }
    return schema


# ── Tool definitions ──────────────────────────────────────────────────── #


TOOL_SCHEMAS: list[dict[str, Any]] = [
    _tool(
        "read_file",
        "Read the contents of a file. Return the text content.",
        {
            "path": {
                "type": "string",
                "description": "Absolute or relative path to the file.",
            },
        },
        ["path"],
    ),
    _tool(
        "write_file",
        "Write content to a file (creates or overwrites).",
        {
            "path": {
                "type": "string",
                "description": "Absolute or relative path to the file.",
            },
            "content": {
                "type": "string",
                "description": "Content to write to the file.",
            },
        },
        ["path", "content"],
    ),
    _tool(
        "exec_command",
        "Execute a shell command and return stdout/stderr."
        " The command runs in a sandboxed environment.",
        {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of arguments.",
            },
            "cwd": {
                "type": "string",
                "description": "Optional working directory.",
            },
        },
        ["command"],
    ),
    _tool(
        "web_search",
        "Search the web using the configured search engine."
        " Returns a list of results with title, URL, and snippet.",
        {
            "query": {
                "type": "string",
                "description": "The search query.",
            },
        },
        ["query"],
    ),
    _tool(
        "web_fetch",
        "Fetch the text content of a web page given its URL.",
        {
            "url": {
                "type": "string",
                "description": "The URL to fetch.",
            },
        },
        ["url"],
    ),
    _tool(
        "ask_user",
        "Ask the user a question and wait for their response."
        " Use when you need clarification or confirmation.",
        {
            "question": {
                "type": "string",
                "description": "The question to ask the user.",
            },
        },
        ["question"],
    ),
    _tool(
        "get_mqtt_records",
        "Retrieve recent MQTT messages from the nervous system bus.",
        {
            "limit": {
                "type": "integer",
                "description": "Maximum number of records to return (default: 100).",
            },
        },
    ),
    _tool(
        "get_system_logs",
        "Retrieve recent system journal logs. Optionally filter by subsystem.",
        {
            "limit": {
                "type": "integer",
                "description": "Maximum number of log lines (default: 200).",
            },
            "system": {
                "type": "string",
                "description": "Optional system name filter (e.g. 'openbad', 'openbad-wui').",
            },
        },
    ),
    _tool(
        "read_events",
        "Read entries from the persistent event log."
        " Supports filtering by level, source, and text search.",
        {
            "limit": {
                "type": "integer",
                "description": "Maximum number of events (default: 100).",
            },
            "level": {
                "type": "string",
                "description": "Filter by level (e.g. 'INFO', 'WARNING', 'ERROR').",
            },
            "source": {
                "type": "string",
                "description": "Filter by event source.",
            },
            "search": {
                "type": "string",
                "description": "Text search within event messages.",
            },
        },
    ),
    _tool(
        "write_event",
        "Write a new entry to the persistent event log.",
        {
            "message": {
                "type": "string",
                "description": "The event message.",
            },
            "level": {
                "type": "string",
                "description": "Log level (default: 'INFO').",
                "enum": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            },
            "source": {
                "type": "string",
                "description": "Event source identifier (default: 'system').",
            },
        },
        ["message"],
    ),
    _tool(
        "get_endocrine_status",
        "Get the current endocrine system hormone levels (cortisol, dopamine, serotonin, etc.).",
        {},
    ),
    _tool(
        "get_tasks",
        "Retrieve the current task list from the task manager.",
        {},
    ),
    _tool(
        "create_task",
        "Create a new task in the task manager.",
        {
            "title": {
                "type": "string",
                "description": "The task title.",
            },
            "description": {
                "type": "string",
                "description": "Optional task description.",
            },
            "owner": {
                "type": "string",
                "description": "Task owner ('user' or 'agent', default: 'user').",
            },
        },
        ["title"],
    ),
    _tool(
        "get_research_nodes",
        "List current research queue nodes.",
        {},
    ),
    _tool(
        "create_research_node",
        "Create a new research node for exploration.",
        {
            "title": {
                "type": "string",
                "description": "Research topic title.",
            },
            "description": {
                "type": "string",
                "description": "Optional description of the research question.",
            },
            "priority": {
                "type": "integer",
                "description": "Priority (0=normal, higher=more urgent).",
            },
        },
        ["title"],
    ),
    _tool(
        "mcp_bridge",
        "Call a tool on a connected MCP (Model Context Protocol) server.",
        {
            "server": {
                "type": "string",
                "description": "Name of the MCP server to call.",
            },
            "tool_name": {
                "type": "string",
                "description": "Name of the tool on the MCP server.",
            },
            "arguments": {
                "type": "object",
                "description": "Arguments to pass to the MCP tool.",
            },
        },
        ["server", "tool_name"],
    ),
]
