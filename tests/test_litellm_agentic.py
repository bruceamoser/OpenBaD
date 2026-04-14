"""Tests for LiteLLM adapter, tool schemas, tool dispatch, and the agentic loop."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openbad.cognitive.context_manager import ContextWindowManager
from openbad.cognitive.providers.litellm_adapter import (
    LiteLLMAdapter,
    litellm_model_name,
)
from openbad.memory.episodic import EpisodicMemory
from openbad.memory.semantic import SemanticMemory
from openbad.memory.stm import ShortTermMemory
from openbad.toolbelt.dispatch import dispatch_tool_call
from openbad.toolbelt.schemas import TOOL_SCHEMAS
from openbad.usage_recorder import UsageTrackingProviderAdapter
from openbad.wui import chat_pipeline

# ── litellm_model_name ─────────────────────────────────────────────── #


class TestLiteLLMModelName:
    def test_prefixes_provider(self):
        assert litellm_model_name("ollama", "llama3.2") == "ollama/llama3.2"

    def test_github_copilot_routes_via_openai(self):
        assert litellm_model_name("github-copilot", "gpt-4o") == "openai/gpt-4o"

    def test_anthropic(self):
        assert litellm_model_name("anthropic", "claude-sonnet-4") == "anthropic/claude-sonnet-4"

    def test_already_prefixed_passthrough(self):
        assert litellm_model_name("openai", "openai/gpt-4o") == "openai/gpt-4o"

    def test_unknown_provider_defaults_to_openai(self):
        assert litellm_model_name("custom", "my-model") == "openai/my-model"


# ── Tool schemas ───────────────────────────────────────────────────── #


class TestToolSchemas:
    def test_schema_count(self):
        assert len(TOOL_SCHEMAS) == 31

    def test_all_have_required_fields(self):
        for schema in TOOL_SCHEMAS:
            assert schema["type"] == "function"
            fn = schema["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn
            assert fn["parameters"]["type"] == "object"

    def test_unique_names(self):
        names = [s["function"]["name"] for s in TOOL_SCHEMAS]
        assert len(names) == len(set(names))

    def test_known_tools_present(self):
        names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        expected = {
            "read_file", "write_file", "exec_command", "get_path_access_status",
            "list_terminal_sessions", "create_terminal_session", "send_terminal_input",
            "read_terminal_output", "close_terminal_session", "web_search",
            "web_fetch", "ask_user", "get_mqtt_records", "get_system_logs",
            "read_events", "write_event", "get_endocrine_status", "call_doctor",
            "get_tasks", "create_task", "update_task", "complete_task",
            "work_on_next_task", "work_on_task",
            "get_research_nodes", "create_research_node", "update_research_node",
            "complete_research_node", "work_on_next_research", "work_on_research",
            "mcp_bridge",
        }
        assert expected == names

    def test_read_file_has_required_path(self):
        rf = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == "read_file")
        assert "path" in rf["function"]["parameters"]["required"]


# ── Tool dispatch ──────────────────────────────────────────────────── #


class TestToolDispatch:
    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        result = await dispatch_tool_call("nonexistent_tool", {})
        assert "Unknown tool" in result

    @pytest.mark.asyncio
    async def test_read_file(self, tmp_path):
        p = tmp_path / "hello.txt"
        p.write_text("world")
        with patch("openbad.toolbelt.fs_tool.ALLOWED_ROOTS", [str(tmp_path)]):
            result = await dispatch_tool_call("read_file", {"path": str(p)})
        assert "world" in result

    @pytest.mark.asyncio
    async def test_write_file(self, tmp_path):
        p = tmp_path / "out.txt"
        with patch("openbad.toolbelt.fs_tool.ALLOWED_ROOTS", [str(tmp_path)]):
            result = await dispatch_tool_call("write_file", {"path": str(p), "content": "data"})
        assert "File written" in result
        assert p.read_text() == "data"

    @pytest.mark.asyncio
    async def test_ask_user_returns_pending(self):
        result = await dispatch_tool_call("ask_user", {"question": "What?"})
        assert "question_pending" in result

    @pytest.mark.asyncio
    async def test_dispatch_error_handling(self):
        """Tool that raises should return error string, not propagate."""
        with patch("openbad.toolbelt.fs_tool.read_file", side_effect=PermissionError("denied")):
            result = await dispatch_tool_call("read_file", {"path": "/etc/shadow"})
        assert "PermissionError: denied" in result

    @pytest.mark.asyncio
    async def test_exec_command_outside_allowed_roots_returns_access_request(self):
        mock_result = SimpleNamespace(stdout="", stderr="Working directory escapes allowed roots ['/tmp/demo']", returncode=-1)
        with patch("openbad.toolbelt.cli_tool.CliToolAdapter.async_execute", new=AsyncMock(return_value=mock_result)):
            result = await dispatch_tool_call("exec_command", {"command": "find . -name spec.md", "cwd": "/home/bruceamoser"})
        assert "[access_request]" in result
        assert "/home/bruceamoser" in result


# ── LiteLLM Adapter ───────────────────────────────────────────────── #


def _mock_response(content="Hello", tool_calls=None, tokens=10):
    """Build a mock litellm ModelResponse."""
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls
    message.model_dump = MagicMock(return_value={
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in (tool_calls or [])
        ] if tool_calls else None,
    })

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = "tool_calls" if tool_calls else "stop"

    usage = MagicMock()
    usage.total_tokens = tokens

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    response.model = "test/model"
    return response


def _mock_tool_call(name, arguments, call_id="call_1"):
    tc = MagicMock()
    tc.id = call_id
    tc.function = MagicMock()
    tc.function.name = name
    tc.function.arguments = json.dumps(arguments)
    return tc


class TestLiteLLMAdapter:
    @pytest.mark.asyncio
    async def test_complete(self):
        adapter = LiteLLMAdapter(
            provider_name="test", default_model="test/model",
        )
        with patch("litellm.acompletion", new_callable=AsyncMock) as mock:
            mock.return_value = _mock_response("Hello world", tokens=5)
            result = await adapter.complete("Hi")
        assert result.content == "Hello world"
        assert result.tokens_used == 5
        assert result.provider == "test"

    @pytest.mark.asyncio
    async def test_agentic_complete_with_tools(self):
        adapter = LiteLLMAdapter(
            provider_name="test", default_model="test/model",
        )
        tools = [{"type": "function", "function": {"name": "test"}}]
        with patch("litellm.acompletion", new_callable=AsyncMock) as mock:
            mock.return_value = _mock_response("result")
            await adapter.agentic_complete(
                [{"role": "user", "content": "hi"}],
                "test/model",
                tools=tools,
            )
        call_kwargs = mock.call_args.kwargs
        assert call_kwargs.get("tools") == tools
        assert call_kwargs.get("tool_choice") == "auto"

    @pytest.mark.asyncio
    async def test_health_check_success(self):
        adapter = LiteLLMAdapter(
            provider_name="test", default_model="test/model",
        )
        with patch("litellm.acompletion", new_callable=AsyncMock) as mock:
            mock.return_value = _mock_response("pong", tokens=1)
            status = await adapter.health_check()
        assert status.available is True
        assert status.provider == "test"

    @pytest.mark.asyncio
    async def test_health_check_failure(self):
        adapter = LiteLLMAdapter(
            provider_name="test", default_model="test/model",
        )
        with patch("litellm.acompletion", new_callable=AsyncMock) as mock:
            mock.side_effect = Exception("connection refused")
            status = await adapter.health_check()
        assert status.available is False


# ── Agentic loop ───────────────────────────────────────────────────── #


def _make_test_state_conn(db_path):
    """Create an SQLite connection with the session_messages schema for tests."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_messages (
            message_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'assistant',
            content TEXT NOT NULL,
            created_at REAL NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_session_messages_session_id
            ON session_messages (session_id, created_at)
        """
    )
    conn.commit()
    return conn


@pytest.fixture
def _reset_pipeline(monkeypatch, tmp_path):
    monkeypatch.setattr(
        chat_pipeline, "_state_conn",
        _make_test_state_conn(tmp_path / "test_state.db"),
    )
    monkeypatch.setattr(
        chat_pipeline, "_stm",
        ShortTermMemory(max_tokens=8192, default_ttl=7200.0),
    )
    monkeypatch.setattr(
        chat_pipeline, "_episodic",
        EpisodicMemory(storage_path=tmp_path / "episodic.json"),
    )
    monkeypatch.setattr(
        chat_pipeline, "_semantic",
        SemanticMemory(storage_path=tmp_path / "semantic.json"),
    )
    monkeypatch.setattr(
        chat_pipeline, "_ctx_manager",
        ContextWindowManager(default_limit=4096),
    )
    monkeypatch.setattr(
        chat_pipeline, "scan_input",
        lambda _text: SimpleNamespace(is_threat=False, matches=[]),
    )


@pytest.mark.asyncio
async def test_agentic_stream_no_tool_calls(_reset_pipeline):
    """When the LLM responds immediately without tool calls, content is yielded."""
    adapter = LiteLLMAdapter(provider_name="test", default_model="test/model")

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock:
        mock.return_value = _mock_response("The answer is 42.", tokens=15)
        chunks = [
            chunk
            async for chunk in chat_pipeline.stream_chat(
                adapter,
                "test/model",
                "What is the answer?",
                "session-agentic-1",
            )
        ]

    text = "".join(c.token for c in chunks if c.token)
    assert "The answer is 42." in text
    assert chunks[-1].done is True


@pytest.mark.asyncio
async def test_agentic_stream_with_tool_calls(_reset_pipeline):
    """When the LLM calls a tool, the loop executes it and continues."""
    adapter = LiteLLMAdapter(provider_name="test", default_model="test/model")

    tool_call = _mock_tool_call("get_endocrine_status", {})
    response_with_tool = _mock_response(content="", tool_calls=[tool_call], tokens=20)
    response_final = _mock_response(content="Cortisol is at 0.3", tokens=10)

    with (
        patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm,
        patch(
            "openbad.wui.chat_pipeline.dispatch_tool_call",
            new_callable=AsyncMock,
        ) as mock_dispatch,
    ):
        mock_llm.side_effect = [response_with_tool, response_final]
        mock_dispatch.return_value = '{"cortisol": 0.3}'

        chunks = [
            chunk
            async for chunk in chat_pipeline.stream_chat(
                adapter,
                "test/model",
                "Check my cortisol",
                "session-agentic-2",
            )
        ]

    text = "".join(c.token for c in chunks if c.token)
    assert "Cortisol is at 0.3" in text
    # LLM was called twice: once with tool calls, once with results
    assert mock_llm.call_count == 2
    mock_dispatch.assert_called_once_with("get_endocrine_status", {})


@pytest.mark.asyncio
async def test_agentic_stream_with_usage_tracking_wrapper(_reset_pipeline):
    """Wrapped adapters should still enter the tool-calling loop."""
    adapter = UsageTrackingProviderAdapter(
        LiteLLMAdapter(provider_name="test", default_model="test/model"),
        system="chat",
    )

    tool_call = _mock_tool_call("create_research_node", {"title": "Temporal decay"})
    response_with_tool = _mock_response(content="", tool_calls=[tool_call], tokens=20)
    response_final = _mock_response(content="Research queued.", tokens=10)

    with (
        patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm,
        patch(
            "openbad.wui.chat_pipeline.dispatch_tool_call",
            new_callable=AsyncMock,
        ) as mock_dispatch,
    ):
        mock_llm.side_effect = [response_with_tool, response_final]
        mock_dispatch.return_value = '{"node_id": "research-123"}'

        chunks = [
            chunk
            async for chunk in chat_pipeline.stream_chat(
                adapter,
                "test/model",
                "Create a research item",
                "session-agentic-wrapper",
            )
        ]

    text = "".join(c.token for c in chunks if c.token)
    assert "Research queued." in text
    assert any("create_research_node" in c.reasoning for c in chunks if c.reasoning)
    assert mock_llm.call_count == 2
    mock_dispatch.assert_called_once_with(
        "create_research_node",
        {"title": "Temporal decay"},
    )


@pytest.mark.asyncio
async def test_agentic_stream_does_not_double_count_tokens(_reset_pipeline):
    """Agentic chunks report cumulative totals, so stream_chat should not re-sum them."""
    adapter = UsageTrackingProviderAdapter(
        LiteLLMAdapter(provider_name="test", default_model="test/model"),
        system="chat",
    )

    tool_call = _mock_tool_call("get_endocrine_status", {})
    response_with_tool = _mock_response(content="", tool_calls=[tool_call], tokens=20)
    response_final = _mock_response(content="Done", tokens=10)

    with (
        patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm,
        patch(
            "openbad.wui.chat_pipeline.dispatch_tool_call",
            new_callable=AsyncMock,
        ) as mock_dispatch,
    ):
        mock_llm.side_effect = [response_with_tool, response_final]
        mock_dispatch.return_value = '{"cortisol": 0.3}'

        chunks = [
            chunk
            async for chunk in chat_pipeline.stream_chat(
                adapter,
                "test/model",
                "Check cortisol",
                "session-agentic-token-count",
            )
        ]

    assert chunks[-1].done is True
    assert chunks[-1].tokens_used == 30


@pytest.mark.asyncio
async def test_agentic_stream_max_iterations(_reset_pipeline):
    """If LLM keeps calling tools, the loop caps at max iterations."""
    adapter = LiteLLMAdapter(provider_name="test", default_model="test/model")

    tool_call = _mock_tool_call("get_tasks", {})
    response_with_tool = _mock_response(content="", tool_calls=[tool_call], tokens=10)
    response_final = _mock_response(content="Summary after max iterations", tokens=10)

    with (
        patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm,
        patch(
            "openbad.wui.chat_pipeline.dispatch_tool_call",
            new_callable=AsyncMock,
        ) as mock_dispatch,
    ):
        # Return tool calls for all iterations, then the final summary
        mock_llm.side_effect = [response_with_tool] * 5 + [response_final]
        mock_dispatch.return_value = "[]"

        chunks = [
            chunk
            async for chunk in chat_pipeline.stream_chat(
                adapter,
                "test/model",
                "Keep checking tasks",
                "session-agentic-max",
            )
        ]

    text = "".join(c.token for c in chunks if c.token)
    assert "Summary after max iterations" in text
    # 5 tool iterations + 1 final summary = 6 calls
    assert mock_llm.call_count == 6


@pytest.mark.asyncio
async def test_agentic_stream_legacy_fallback(_reset_pipeline):
    """Non-LiteLLM adapters still work via the legacy streaming path."""
    from openbad.cognitive.providers.base import (
        HealthStatus,
        ModelInfo,
        ProviderAdapter,
    )

    class _LegacyAdapter(ProviderAdapter):
        async def complete(self, prompt, model_id=None, **kwargs):
            raise NotImplementedError

        async def stream(self, prompt, model_id=None, **kwargs) -> AsyncIterator[str]:
            yield "legacy"
            yield " works"

        async def list_models(self):
            return [ModelInfo(model_id="m", provider="p")]

        async def health_check(self):
            return HealthStatus(provider="p", available=True)

    chunks = [
        chunk
        async for chunk in chat_pipeline.stream_chat(
            _LegacyAdapter(),
            "test-model",
            "Hello",
            "session-legacy",
        )
    ]

    text = "".join(c.token for c in chunks if c.token)
    assert text == "legacy works"
    assert chunks[-1].done is True
