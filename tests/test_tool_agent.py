from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openbad.autonomy.tool_agent import ToolAgentResult, run_tool_agent


@dataclass
class _ToolFunction:
    name: str
    arguments: str


@dataclass
class _ToolCall:
    id: str
    function: _ToolFunction


class _Message:
    def __init__(self, *, content: str = "", tool_calls: list[_ToolCall] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []

    def model_dump(self, exclude_none: bool = True) -> dict[str, object]:  # noqa: ARG002
        payload: dict[str, object] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            payload["tool_calls"] = self.tool_calls
        return payload


def _response(*, content: str = "", tool_calls: list[_ToolCall] | None = None, tokens: int = 0):
    message = _Message(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(total_tokens=tokens),
        model="test-model",
    )


@pytest.mark.asyncio
async def test_run_tool_agent_uses_agentic_tools() -> None:
    adapter = SimpleNamespace()
    tool_call = _ToolCall(
        id="tool-1",
        function=_ToolFunction("create_task", '{"title": "follow up"}'),
    )
    adapter.agentic_complete = AsyncMock(
        side_effect=[
            _response(tool_calls=[tool_call], tokens=20),
            _response(content="Created follow-up task.", tokens=10),
        ]
    )

    with patch("openbad.autonomy.tool_agent.call_skill", new_callable=AsyncMock) as dispatch:
        dispatch.return_value = '{"task_id": "task-123"}'
        result = await run_tool_agent(
            adapter,
            "test-model",
            provider_name="custom",
            system_prompt="Do the work.",
            user_prompt="Need a follow-up task.",
            request_id="req-1",
        )

    assert isinstance(result, ToolAgentResult)
    assert result.used_agentic is True
    assert result.content == (
        "Created follow-up task.\n\n"
        "Verified follow-up entries created via tools: task 'follow up' (task-123)."
    )
    assert result.tokens_used == 30
    assert result.tools_used == ("create_task",)
    assert result.verified_creations == ("task 'follow up' (task-123)",)
    dispatch.assert_awaited_once_with("create_task", {"title": "follow up"})


@pytest.mark.asyncio
async def test_run_tool_agent_marks_missing_creation_as_unverified() -> None:
    adapter = SimpleNamespace()
    tool_call = _ToolCall(
        id="tool-1",
        function=_ToolFunction("create_task", '{"title": "follow up"}'),
    )
    adapter.agentic_complete = AsyncMock(
        side_effect=[
            _response(tool_calls=[tool_call], tokens=12),
            _response(content="Created follow-up task.", tokens=8),
        ]
    )

    with patch("openbad.autonomy.tool_agent.call_skill", new_callable=AsyncMock) as dispatch:
        dispatch.return_value = "{}"
        result = await run_tool_agent(
            adapter,
            "test-model",
            provider_name="custom",
            system_prompt="Do the work.",
            user_prompt="Need a follow-up task.",
            request_id="req-3",
        )

    assert result.content == (
        "Created follow-up task.\n\n"
        "Verified follow-up entries created via tools: none."
    )
    assert result.verified_creations == ()


@pytest.mark.asyncio
async def test_run_tool_agent_blocks_tool_call_via_validator() -> None:
    adapter = SimpleNamespace()
    tool_call = _ToolCall(
        id="tool-1",
        function=_ToolFunction("create_research_node", '{"title": "same", "description": "same"}'),
    )
    adapter.agentic_complete = AsyncMock(
        side_effect=[
            _response(tool_calls=[tool_call], tokens=5),
            _response(content="No duplicate research created.", tokens=4),
        ]
    )

    with patch("openbad.autonomy.tool_agent.call_skill", new_callable=AsyncMock) as dispatch:
        result = await run_tool_agent(
            adapter,
            "test-model",
            provider_name="custom",
            system_prompt="Do the work.",
            user_prompt="Research topic.",
            request_id="req-4",
            tool_call_validator=lambda name, args: "blocked duplicate" if name == "create_research_node" else None,
        )

    assert result.content == "No duplicate research created.\n\nVerified follow-up entries created via tools: none."
    assert result.tools_used == ("create_research_node",)
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_tool_agent_falls_back_to_complete() -> None:
    adapter = SimpleNamespace()
    adapter.complete = AsyncMock(
        return_value=SimpleNamespace(
            content="Plain response",
            provider="custom",
            model_id="fallback-model",
            tokens_used=7,
        )
    )

    result = await run_tool_agent(
        adapter,
        "fallback-model",
        provider_name="custom",
        system_prompt="Do the work.",
        user_prompt="No agentic support.",
        request_id="req-2",
    )

    assert result.used_agentic is False
    assert result.content == "Plain response"
    assert result.tokens_used == 7
    assert result.tools_used == ()
