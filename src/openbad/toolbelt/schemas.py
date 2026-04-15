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
        "find_files",
        "Find files under a directory using a glob or substring pattern.",
        {
            "pattern": {
                "type": "string",
                "description": "Glob-like pattern or plain substring to search for.",
            },
            "cwd": {
                "type": "string",
                "description": "Directory to search within. Defaults to the current working directory. Omit this unless the directory was explicitly provided by the user or a prior tool result.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of matches to return (default: 50).",
            },
        },
        ["pattern"],
    ),
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
        "get_path_access_status",
        "Return current approved path roots and pending access requests for tool usage.",
        {},
    ),
    _tool(
        "list_terminal_sessions",
        "List currently active PTY-backed terminal sessions.",
        {},
    ),
    _tool(
        "create_terminal_session",
        "Create a PTY-backed interactive terminal session in an approved working directory.",
        {
            "cwd": {
                "type": "string",
                "description": "Working directory for the shell session.",
            },
            "shell": {
                "type": "string",
                "description": "Shell executable to launch (default: /bin/bash).",
            },
            "requester": {
                "type": "string",
                "description": "Requester identity, such as session or subsystem name.",
            },
        },
        ["cwd"],
    ),
    _tool(
        "send_terminal_input",
        "Send text input to an active PTY terminal session.",
        {
            "session_id": {
                "type": "string",
                "description": "Terminal session identifier.",
            },
            "input": {
                "type": "string",
                "description": "Text to send to the terminal.",
            },
            "append_newline": {
                "type": "boolean",
                "description": "Whether to append a newline after the input (default: true).",
            },
        },
        ["session_id", "input"],
    ),
    _tool(
        "read_terminal_output",
        "Read the latest available output from an active PTY terminal session.",
        {
            "session_id": {
                "type": "string",
                "description": "Terminal session identifier.",
            },
            "max_bytes": {
                "type": "integer",
                "description": "Maximum bytes of terminal output to return (default: 8192).",
            },
        },
        ["session_id"],
    ),
    _tool(
        "close_terminal_session",
        "Close an active PTY terminal session and release resources.",
        {
            "session_id": {
                "type": "string",
                "description": "Terminal session identifier.",
            },
            "reason": {
                "type": "string",
                "description": "Reason for closing the session.",
            },
        },
        ["session_id"],
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
        "Retrieve recent persistent system log events. Optionally filter by source subsystem.",
        {
            "limit": {
                "type": "integer",
                "description": "Maximum number of log lines (default: 200).",
            },
            "system": {
                "type": "string",
                "description": "Optional source module filter (e.g. 'wui', 'endocrine').",
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
        "call_doctor",
        "Request a doctor visit over the embedded MQTT bus with a reason and optional context.",
        {
            "reason": {
                "type": "string",
                "description": "Why the doctor should be called.",
            },
            "source": {
                "type": "string",
                "description": "Requester identity, such as session or subsystem name.",
            },
            "context": {
                "type": "object",
                "description": "Optional structured context payload for the doctor.",
            },
        },
        ["reason"],
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
        "update_task",
        "Update mutable fields on an existing task.",
        {
            "task_id": {
                "type": "string",
                "description": "The task ID to update.",
            },
            "title": {
                "type": "string",
                "description": "Optional updated title.",
            },
            "description": {
                "type": "string",
                "description": "Optional updated description.",
            },
            "owner": {
                "type": "string",
                "description": "Optional updated owner.",
            },
        },
        ["task_id"],
    ),
    _tool(
        "complete_task",
        "Mark an existing task complete.",
        {
            "task_id": {
                "type": "string",
                "description": "The task ID to complete.",
            },
        },
        ["task_id"],
    ),
    _tool(
        "work_on_next_task",
        "Queue an event requesting work on the next eligible task.",
        {
            "source": {
                "type": "string",
                "description": "Requester identity, such as session or subsystem name.",
            },
            "reason": {
                "type": "string",
                "description": "Why the next task should be processed now.",
            },
        },
    ),
    _tool(
        "work_on_task",
        "Queue an event requesting work on a specific task by ID.",
        {
            "task_id": {
                "type": "string",
                "description": "The task ID to work on.",
            },
            "source": {
                "type": "string",
                "description": "Requester identity, such as session or subsystem name.",
            },
            "reason": {
                "type": "string",
                "description": "Why this specific task should be processed now.",
            },
        },
        ["task_id"],
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
        "update_research_node",
        "Update a pending research node.",
        {
            "node_id": {
                "type": "string",
                "description": "The research node ID to update.",
            },
            "title": {
                "type": "string",
                "description": "Optional updated title.",
            },
            "description": {
                "type": "string",
                "description": "Optional updated description.",
            },
            "priority": {
                "type": "integer",
                "description": "Optional updated priority.",
            },
            "source_task_id": {
                "type": "string",
                "description": "Optional related task ID.",
            },
        },
        ["node_id"],
    ),
    _tool(
        "complete_research_node",
        "Mark a research node complete.",
        {
            "node_id": {
                "type": "string",
                "description": "The research node ID to complete.",
            },
        },
        ["node_id"],
    ),
    _tool(
        "work_on_next_research",
        "Queue an event requesting work on the next eligible research item.",
        {
            "source": {
                "type": "string",
                "description": "Requester identity, such as session or subsystem name.",
            },
            "reason": {
                "type": "string",
                "description": "Why the next research item should be processed now.",
            },
        },
    ),
    _tool(
        "work_on_research",
        "Queue an event requesting work on a specific research item by ID.",
        {
            "node_id": {
                "type": "string",
                "description": "The research node ID to work on.",
            },
            "source": {
                "type": "string",
                "description": "Requester identity, such as session or subsystem name.",
            },
            "reason": {
                "type": "string",
                "description": "Why this specific research item should be processed now.",
            },
        },
        ["node_id"],
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
