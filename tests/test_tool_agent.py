from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from openbad.autonomy.tool_agent import (
    ToolAgentResult,
    _extract_creation_info,
    build_tooling_system_prompt,
    run_tool_agent,
)

# ------------------------------------------------------------------ #
#  Unit tests for helper functions
# ------------------------------------------------------------------ #


def test_build_tooling_system_prompt_prepends_instructions() -> None:
    prompt = build_tooling_system_prompt("You are a test agent.")
    assert "You are a test agent." in prompt
    assert "embedded skills" in prompt


def test_extract_creation_info_finds_tasks() -> None:
    messages = [
        AIMessage(content="", tool_calls=[{"name": "create_task", "args": {}, "id": "t1"}]),
        ToolMessage(
            content='{"task_id": "task-42", "title": "Follow up"}',
            tool_call_id="t1",
            name="create_task",
        ),
        AIMessage(content="Done."),
    ]
    names, verified = _extract_creation_info(messages)
    assert "create_task" in names
    assert any("task-42" in v for v in verified)


def test_extract_creation_info_finds_research() -> None:
    messages = [
        AIMessage(
            content="", tool_calls=[{"name": "create_research_node", "args": {}, "id": "t2"}]
        ),
        ToolMessage(
            content='{"node_id": "rn-7", "title": "Deep dive"}',
            tool_call_id="t2",
            name="create_research_node",
        ),
        AIMessage(content="Research created."),
    ]
    names, verified = _extract_creation_info(messages)
    assert "create_research_node" in names
    assert any("rn-7" in v for v in verified)


def test_extract_creation_info_empty_on_no_creation() -> None:
    messages = [
        AIMessage(content="", tool_calls=[{"name": "find_files", "args": {}, "id": "t3"}]),
        ToolMessage(content='["a.txt"]', tool_call_id="t3", name="find_files"),
        AIMessage(content="Found a.txt."),
    ]
    names, verified = _extract_creation_info(messages)
    assert "find_files" in names
    assert verified == []


# ------------------------------------------------------------------ #
#  Integration-style tests (mocked LangGraph agent)
# ------------------------------------------------------------------ #


def _fake_agent_result(content: str, tool_names: list[str] | None = None):
    """Build a fake LangGraph agent result dict."""
    messages = [HumanMessage(content="test")]
    if tool_names:
        for name in tool_names:
            messages.append(
                AIMessage(content="", tool_calls=[{"name": name, "args": {}, "id": f"tc-{name}"}])
            )
            messages.append(ToolMessage(content="{}", tool_call_id=f"tc-{name}", name=name))
    messages.append(AIMessage(content=content))
    return {"messages": messages}


@pytest.mark.asyncio
async def test_run_tool_agent_returns_result() -> None:
    mock_chat_model = MagicMock()
    fake_result = _fake_agent_result("Hello from the agent.", tool_names=["find_files"])

    with (
        patch(
            "openbad.autonomy.tool_agent.async_get_openbad_tools",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch("openbad.autonomy.tool_agent.create_react_agent") as mock_create,
    ):
        mock_agent = AsyncMock()
        mock_agent.ainvoke.return_value = fake_result
        mock_create.return_value = mock_agent

        result = await run_tool_agent(
            mock_chat_model,
            "openai/test-model",
            provider_name="custom",
            system_prompt="Do the work.",
            user_prompt="Find files.",
            request_id="req-1",
        )

    assert isinstance(result, ToolAgentResult)
    assert result.used_agentic is True
    assert result.content == "Hello from the agent."
    assert "find_files" in result.tools_used
    assert result.provider == "custom"


@pytest.mark.asyncio
async def test_run_tool_agent_creation_footer() -> None:
    mock_chat_model = MagicMock()
    messages = [
        HumanMessage(content="test"),
        AIMessage(content="", tool_calls=[{"name": "create_task", "args": {}, "id": "tc-1"}]),
        ToolMessage(
            content='{"task_id": "t-99", "title": "Follow up"}',
            tool_call_id="tc-1",
            name="create_task",
        ),
        AIMessage(content="Created a follow-up task."),
    ]
    fake_result = {"messages": messages}

    with (
        patch(
            "openbad.autonomy.tool_agent.async_get_openbad_tools",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch("openbad.autonomy.tool_agent.create_react_agent") as mock_create,
    ):
        mock_agent = AsyncMock()
        mock_agent.ainvoke.return_value = fake_result
        mock_create.return_value = mock_agent

        result = await run_tool_agent(
            mock_chat_model,
            "openai/test-model",
            provider_name="custom",
            system_prompt="Do the work.",
            user_prompt="Create task.",
            request_id="req-2",
        )

    assert "Verified follow-up entries created via tools:" in result.content
    assert "t-99" in result.content
    assert result.verified_creations == ("task 'Follow up' (t-99)",)


@pytest.mark.asyncio
async def test_run_tool_agent_handles_exception() -> None:
    mock_chat_model = MagicMock()

    with (
        patch(
            "openbad.autonomy.tool_agent.async_get_openbad_tools",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch("openbad.autonomy.tool_agent.create_react_agent") as mock_create,
    ):
        mock_agent = AsyncMock()
        mock_agent.ainvoke.side_effect = RuntimeError("LLM connection failed")
        mock_create.return_value = mock_agent

        result = await run_tool_agent(
            mock_chat_model,
            "openai/test-model",
            provider_name="custom",
            system_prompt="Do the work.",
            user_prompt="Will fail.",
            request_id="req-err",
        )

    assert result.content == ""
    assert result.used_agentic is True
    assert result.provider == "custom"
