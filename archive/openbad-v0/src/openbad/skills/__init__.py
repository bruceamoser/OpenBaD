"""OpenBaD embedded skills — native MCP server for built-in capabilities.

Embedded skills are the tools that ship with OpenBaD and work out of the box:
file I/O, command execution, web search, diagnostics, task/research management,
etc.  They are distinct from the *toolbelt*, which is a plugin system for
user-built extensions (not yet implemented).

The skills are defined using the MCP Python SDK's FastMCP decorator API so that:
  - Schemas are auto-generated from type hints (no hand-written JSON).
  - Any MCP client (Claude Code, VS Code, etc.) can connect and use them.
  - The internal agentic loop also consumes them via ``get_openai_tools()``
    and ``call_skill()``.
"""

from openbad.skills.server import get_openai_tools, call_skill, skill_server

__all__ = ["get_openai_tools", "call_skill", "skill_server"]
