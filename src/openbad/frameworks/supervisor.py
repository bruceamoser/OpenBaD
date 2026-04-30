"""Multi-agent supervisor graph for LangGraph.

Builds a :class:`~langgraph.graph.StateGraph` where a **supervisor** node
evaluates user intent and routes to specialized **sub-agents**, each with
its own isolated tool context.  This keeps per-call tool schema tokens
small enough for context-limited models.

Architecture::

    START → supervisor ──(route)──→ sub_agent_X → supervisor → … → END
                 │                                     │
                 └──── (direct answer) ───────────────→ END

Public API
----------
``build_supervisor_graph(chat_model, sub_agents, *, system_prompt, request_id)``
    Returns a compiled LangGraph ``CompiledGraph``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import create_react_agent

log = logging.getLogger(__name__)

# Maximum number of supervisor → sub-agent round-trips before forcing END.
_MAX_SUPERVISOR_CYCLES = 8


# ── Data types ────────────────────────────────────────────────────────── #


@dataclass(frozen=True)
class SubAgentDef:
    """Definition of a specialized sub-agent.

    Attributes
    ----------
    name:
        Unique identifier (used as graph node name).  Must be a valid
        Python identifier (no spaces/hyphens).
    description:
        One-line description shown to the supervisor so it can decide
        when to route to this agent.
    tool_names:
        Tool names this sub-agent is allowed to use.  Must be a subset
        of the parent role's ``_ROLE_TOOLS`` allow-list.
    system_prompt:
        Optional prompt prepended to the sub-agent's messages.
    """

    name: str
    description: str
    tool_names: frozenset[str] = field(default_factory=frozenset)
    system_prompt: str = ""


# ── Supervisor graph builder ──────────────────────────────────────────── #


def _build_routing_tools(
    sub_agents: list[SubAgentDef],
) -> list[StructuredTool]:
    """Build one routing tool per sub-agent for the supervisor.

    Each tool is a no-op function whose name and description tell the
    supervisor LLM *what* the sub-agent can do.  When the supervisor
    calls one of these tools, the conditional edge routes to that
    sub-agent node.
    """
    from pydantic import BaseModel, Field

    tools: list[StructuredTool] = []

    for agent_def in sub_agents:

        class _RouteInput(BaseModel):
            request: str = Field(
                description="What you need this agent to do, in plain language.",
            )

        def _make_route_noop(name: str) -> Any:
            async def _route_noop(request: str = "") -> str:
                # Never actually called — routing is handled by edges.
                return f"Routing to {name}..."
            return _route_noop

        _noop = _make_route_noop(agent_def.name)

        # Give each function a unique __name__ so LangChain doesn't
        # complain about duplicate tool names.
        _noop.__name__ = f"delegate_to_{agent_def.name}"
        _noop.__qualname__ = f"delegate_to_{agent_def.name}"

        tool = StructuredTool(
            name=f"delegate_to_{agent_def.name}",
            description=agent_def.description,
            coroutine=_noop,
            args_schema=_RouteInput,
        )
        tools.append(tool)

    return tools


def _build_respond_tool() -> StructuredTool:
    """Build the ``respond_to_user`` tool.

    When the supervisor calls this tool, the graph routes to END with the
    supervisor's response.  This gives the supervisor an explicit way to
    say "I have the final answer, no sub-agent needed."
    """
    from pydantic import BaseModel, Field

    class _RespondInput(BaseModel):
        response: str = Field(
            description="Your final answer to the user.",
        )

    async def _respond(response: str) -> str:
        return response

    return StructuredTool(
        name="respond_to_user",
        description=(
            "Respond directly to the user when no sub-agent is needed. "
            "Use for greetings, simple questions, clarifications, or "
            "when you already have enough information to answer."
        ),
        coroutine=_respond,
        args_schema=_RespondInput,
    )


def build_supervisor_graph(
    chat_model: Any,
    sub_agents: list[SubAgentDef],
    all_tools: list[BaseTool],
    *,
    system_prompt: str = "",
    request_id: str = "",
    direct_tools: list[BaseTool] | None = None,
) -> Any:
    """Build and compile a supervisor StateGraph.

    Parameters
    ----------
    chat_model:
        LangChain ``BaseChatModel`` (e.g. ``ChatOpenAI``).
    sub_agents:
        Sub-agent definitions.  One graph node is created per entry.
    all_tools:
        The full set of LangChain tools available.  Each sub-agent picks
        its subset by matching ``tool_names``.
    system_prompt:
        System prompt for the supervisor node.
    request_id:
        Logging correlation ID.
    direct_tools:
        Optional tools bound directly to the supervisor (e.g. ``ask_user``).
        These are available alongside the routing tools.

    Returns
    -------
    compiled : CompiledGraph
        Ready to call ``astream_events()`` or ``ainvoke()``.
    """
    tool_lookup: dict[str, BaseTool] = {t.name: t for t in all_tools}

    # ── Build supervisor tools ──
    routing_tools = _build_routing_tools(sub_agents)
    respond_tool = _build_respond_tool()
    supervisor_tools: list[BaseTool] = [
        respond_tool,
        *routing_tools,
        *(direct_tools or []),
    ]

    respond_tool_name = respond_tool.name

    # ── Build supervisor system prompt ──
    # Prepend delegation instructions so the LLM knows it must route
    # tool-requiring tasks to sub-agents instead of answering directly.
    agent_roster = "\n".join(
        f"- delegate_to_{a.name}: {a.description}" for a in sub_agents
    )
    direct_names = ", ".join(t.name for t in (direct_tools or []))
    supervisor_preamble = (
        "You are a SUPERVISOR agent. You do NOT have direct access to "
        "tools like memory, files, web search, or entity updates. "
        "Instead, you DELEGATE tasks to specialized sub-agents by "
        "calling the appropriate delegate_to_* tool.\n\n"
        "IMPORTANT RULES:\n"
        "1. When the user's request requires any tool action (searching "
        "memory, updating entities, reading files, web search, etc.), "
        "you MUST call the appropriate delegate_to_* tool. NEVER say "
        "\"I cannot\" do something that a sub-agent can handle.\n"
        "2. Use respond_to_user ONLY for greetings, simple factual "
        "answers from conversation context, or clarifying questions.\n"
        "3. You may delegate to multiple sub-agents in sequence.\n\n"
        "Available sub-agents:\n"
        f"{agent_roster}\n"
    )
    if direct_names:
        supervisor_preamble += (
            f"\nDirect tools (use these yourself): {direct_names}\n"
        )
    supervisor_preamble += "\n---\n\n"

    full_prompt = supervisor_preamble + (system_prompt or "")

    # ── Supervisor node ──
    supervisor_agent = create_react_agent(
        model=chat_model,
        tools=supervisor_tools,
        prompt=full_prompt,
    )

    # ── Sub-agent nodes ──
    sub_agent_graphs: dict[str, Any] = {}
    for agent_def in sub_agents:
        agent_tools = [
            tool_lookup[n] for n in agent_def.tool_names if n in tool_lookup
        ]
        if not agent_tools:
            log.warning(
                "Sub-agent %s has no matching tools (request=%s)",
                agent_def.name,
                request_id,
            )
            continue

        sub_graph = create_react_agent(
            model=chat_model,
            tools=agent_tools,
            prompt=agent_def.system_prompt or None,
        )
        sub_agent_graphs[agent_def.name] = sub_graph

    # ── State graph ──
    graph = StateGraph(MessagesState)

    # Add supervisor node
    graph.add_node("supervisor", supervisor_agent)

    # Add sub-agent nodes
    for name, sub_graph in sub_agent_graphs.items():
        graph.add_node(name, sub_graph)

    # Supervisor is the entry point
    graph.add_edge(START, "supervisor")

    # Sub-agent results always flow back to supervisor
    for name in sub_agent_graphs:
        graph.add_edge(name, "supervisor")

    # ── Conditional routing from supervisor ──
    def _route_supervisor(state: MessagesState) -> str:
        """Determine next node based on supervisor's last tool call."""
        messages = state["messages"]

        # Walk backwards to find the last AI message with tool calls
        for msg in reversed(messages):
            if not isinstance(msg, AIMessage):
                continue

            # If the AI message has no tool calls, it's a direct response
            if not msg.tool_calls:
                return END

            # Check which tool was called
            for tc in msg.tool_calls:
                tool_name = tc.get("name", "")

                # respond_to_user → END
                if tool_name == respond_tool_name:
                    return END

                # delegate_to_X → route to sub-agent X
                for agent_def in sub_agents:
                    if (
                        tool_name == f"delegate_to_{agent_def.name}"
                        and agent_def.name in sub_agent_graphs
                    ):
                            log.info(
                                "Supervisor routing to %s (request=%s)",
                                agent_def.name,
                                request_id,
                            )
                            return agent_def.name

            # If tool call didn't match any routing, end
            return END

        return END

    # Build the list of possible destinations
    destinations: list[str] = [END] + list(sub_agent_graphs.keys())

    graph.add_conditional_edges(
        "supervisor",
        _route_supervisor,
        destinations,
    )

    log.info(
        "Supervisor graph built request=%s sub_agents=%d "
        "supervisor_tools=%d",
        request_id,
        len(sub_agent_graphs),
        len(supervisor_tools),
    )

    return graph.compile()
