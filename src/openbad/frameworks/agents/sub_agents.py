"""Specialized sub-agent definitions for the supervisor graph.

Each :class:`SubAgentDef` describes a domain-specific agent with its own
tool set and system prompt.  The supervisor routes user intent to the
appropriate sub-agent, keeping per-call tool schema tokens small.

Chat sub-agents
~~~~~~~~~~~~~~~
- **MemoryAgent** — episodic and semantic memory search, write, prune
- **LibraryAgent** — knowledge base search, read, create, link
- **WebAgent** — web search and page fetch
- **EntityAgent** — user/assistant profile inspection and update
- **SystemAgent** — health, tasks, research queue, event logs
- **FileAgent** — file discovery and content reading
"""

from __future__ import annotations

from openbad.frameworks.supervisor import SubAgentDef

# ── Chat role sub-agents ─────────────────────────────────────────────── #

MEMORY_AGENT = SubAgentDef(
    name="memory_agent",
    description=(
        "Search, store, and manage long-term memory. Use when the user "
        "asks to remember something, recall past conversations, or "
        "search for previously stored information."
    ),
    tool_names=frozenset({
        "read_memory",
        "write_memory",
        "prune_memory",
        "query_semantic",
    }),
    system_prompt=(
        "You are the Memory Agent. You manage the user's long-term memory "
        "(episodic and semantic). Use read_memory and query_semantic to "
        "search, write_memory to store new memories, and prune_memory to "
        "mark entries for removal. Return concise results."
    ),
)

LIBRARY_AGENT = SubAgentDef(
    name="library_agent",
    description=(
        "Search, read, create, and link knowledge base articles (books). "
        "Use when the user asks about stored knowledge, documentation, "
        "or wants to save structured information."
    ),
    tool_names=frozenset({
        "search_library",
        "read_book",
        "draft_book",
        "link_books",
    }),
    system_prompt=(
        "You are the Library Agent. You manage the knowledge base. "
        "Use search_library to find relevant books, read_book to retrieve "
        "content, draft_book to create new entries, and link_books to "
        "create citation edges. Return concise results."
    ),
)

WEB_AGENT = SubAgentDef(
    name="web_agent",
    description=(
        "Search the web and fetch page content. Use when the user asks "
        "questions requiring current or external information, needs a "
        "URL fetched, or asks you to look something up online."
    ),
    tool_names=frozenset({
        "web_search",
        "web_fetch",
    }),
    system_prompt=(
        "You are the Web Agent. Use web_search to find information "
        "and web_fetch to retrieve specific page content. Summarise "
        "findings concisely."
    ),
)

ENTITY_AGENT = SubAgentDef(
    name="entity_agent",
    description=(
        "View and update user or assistant profiles and personality "
        "traits (OCEAN). Use when the user asks about their own profile, "
        "the assistant's personality, or wants to change preferences."
    ),
    tool_names=frozenset({
        "get_entity_info",
        "update_user_entity",
        "update_assistant_entity",
    }),
    system_prompt=(
        "You are the Entity Agent. You manage user and assistant "
        "identity profiles. Use get_entity_info to view profiles, "
        "update_user_entity and update_assistant_entity to modify them. "
        "Be careful with personality changes — confirm with the user."
    ),
)

SYSTEM_AGENT = SubAgentDef(
    name="system_agent",
    description=(
        "View system health, hormone levels, tasks, research queue, "
        "and event logs. Use when the user asks about system status, "
        "tasks, research, or wants to create a new task or research node."
    ),
    tool_names=frozenset({
        "get_endocrine_status",
        "get_tasks",
        "get_research_nodes",
        "read_events",
        "create_task",
        "create_research_node",
    }),
    system_prompt=(
        "You are the System Agent. You provide system observability "
        "and task management. Use the available tools to check health, "
        "list tasks and research, read event logs, and create new tasks "
        "or research nodes. Return concise summaries."
    ),
)

FILE_AGENT = SubAgentDef(
    name="file_agent",
    description=(
        "Search for and read files on the filesystem. Use when the "
        "user asks to find a file, read file contents, or explore "
        "the directory structure."
    ),
    tool_names=frozenset({
        "read_file",
        "find_files",
    }),
    system_prompt=(
        "You are the File Agent. Use find_files to locate files by "
        "pattern and read_file to retrieve their contents. Return "
        "concise results."
    ),
)

# Ordered list for the chat supervisor — order determines routing
# preference when multiple agents match.
CHAT_SUB_AGENTS: list[SubAgentDef] = [
    MEMORY_AGENT,
    LIBRARY_AGENT,
    WEB_AGENT,
    ENTITY_AGENT,
    SYSTEM_AGENT,
    FILE_AGENT,
]

# Tools that stay directly on the chat supervisor (not in any sub-agent).
CHAT_DIRECT_TOOLS: frozenset[str] = frozenset({"ask_user"})
