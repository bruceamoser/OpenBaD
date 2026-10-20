"""Comprehensive tests for all embedded skills via call_skill().

Each skill is tested through the public call_skill() API with injected
HTTP/MQTT stubs so no real servers are required.  Verifies:
  - Correct URL construction (including encoding of IDs with special chars)
  - Errors propagate as strings (the LLM sees the error, not empty JSON)
  - Happy-path returns structured data
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from openbad.skills.doctor_tool import DoctorToolAdapter, DoctorToolConfig
from openbad.skills.endocrine_status_tool import (
    EndocrineStatusToolAdapter,
    EndocrineStatusToolConfig,
)
from openbad.skills.event_log_tool import EventLogToolAdapter, EventLogToolConfig
from openbad.skills.mqtt_records_tool import MqttRecordsToolAdapter, MqttRecordsToolConfig
from openbad.skills.research_diagnostics_tool import (
    ResearchDiagnosticsToolAdapter,
    ResearchDiagnosticsToolConfig,
)
from openbad.skills.server import call_skill
from openbad.skills.tasks_diagnostics_tool import (
    TasksDiagnosticsToolAdapter,
    TasksDiagnosticsToolConfig,
)

# ── Tasks tool ────────────────────────────────────────────────────────── #


def _make_task_adapter(
    *,
    get_response: bytes | Exception | None = None,
    post_response: bytes | Exception | None = None,
    patch_response: bytes | Exception | None = None,
    publisher: Any = None,
    capture_urls: list | None = None,
) -> TasksDiagnosticsToolAdapter:
    """Build an adapter with injectable HTTP stubs."""

    def _get(url: str, timeout: float) -> bytes:
        if capture_urls is not None:
            capture_urls.append(("GET", url))
        if isinstance(get_response, Exception):
            raise get_response
        return get_response or b"{}"

    def _post(url: str, timeout: float, payload: dict) -> bytes:
        if capture_urls is not None:
            capture_urls.append(("POST", url))
        if isinstance(post_response, Exception):
            raise post_response
        return post_response or b"{}"

    def _patch(url: str, timeout: float, payload: dict) -> bytes:
        if capture_urls is not None:
            capture_urls.append(("PATCH", url))
        if isinstance(patch_response, Exception):
            raise patch_response
        return patch_response or b"{}"

    return TasksDiagnosticsToolAdapter(
        config=TasksDiagnosticsToolConfig(base_url="http://test:9200"),
        http_get=_get,
        http_post=_post,
        http_patch=_patch,
        publisher=publisher or (lambda *a: None),
    )


class TestTasksGetTasks:
    def test_happy_path(self) -> None:
        body = json.dumps({"tasks": [{"id": "t1", "title": "Do stuff"}]}).encode()
        adapter = _make_task_adapter(get_response=body)
        result = adapter.get_tasks()
        assert len(result) == 1
        assert result[0]["id"] == "t1"

    def test_http_error_propagates(self) -> None:
        adapter = _make_task_adapter(get_response=ConnectionError("refused"))
        with pytest.raises(RuntimeError, match="Failed to fetch tasks"):
            adapter.get_tasks()


class TestTasksCreateTask:
    def test_happy_path(self) -> None:
        body = json.dumps({"id": "t2", "title": "New task"}).encode()
        adapter = _make_task_adapter(post_response=body)
        result = adapter.create_task("New task", description="desc")
        assert result["id"] == "t2"

    def test_http_error_propagates(self) -> None:
        adapter = _make_task_adapter(post_response=ConnectionError("refused"))
        with pytest.raises(RuntimeError, match="Failed to create task"):
            adapter.create_task("title")


class TestTasksUpdateTask:
    def test_url_encodes_task_id(self) -> None:
        urls: list[tuple[str, str]] = []
        body = json.dumps({"id": "Test Task", "title": "Updated"}).encode()
        adapter = _make_task_adapter(patch_response=body, capture_urls=urls)
        adapter.update_task("Test Task", title="Updated")
        assert len(urls) == 1
        assert "Test%20Task" in urls[0][1]
        assert " " not in urls[0][1]

    def test_url_encodes_slash_in_id(self) -> None:
        urls: list[tuple[str, str]] = []
        body = json.dumps({}).encode()
        adapter = _make_task_adapter(patch_response=body, capture_urls=urls)
        adapter.update_task("a/b", title="x")
        assert "a%2Fb" in urls[0][1]

    def test_http_error_propagates(self) -> None:
        adapter = _make_task_adapter(patch_response=ConnectionError("fail"))
        with pytest.raises(RuntimeError, match="Failed to update task"):
            adapter.update_task("id1", title="x")


class TestTasksCompleteTask:
    def test_url_encodes_task_id(self) -> None:
        urls: list[tuple[str, str]] = []
        body = json.dumps({"completed": True}).encode()
        adapter = _make_task_adapter(post_response=body, capture_urls=urls)
        adapter.complete_task("Test Task")
        assert len(urls) == 1
        assert "Test%20Task" in urls[0][1]
        assert urls[0][1].endswith("/complete")

    def test_happy_path_uuid(self) -> None:
        urls: list[tuple[str, str]] = []
        body = json.dumps({"completed": True}).encode()
        adapter = _make_task_adapter(post_response=body, capture_urls=urls)
        adapter.complete_task("abc-123")
        assert "/api/tasks/abc-123/complete" in urls[0][1]

    def test_http_error_propagates(self) -> None:
        adapter = _make_task_adapter(post_response=ConnectionError("fail"))
        with pytest.raises(RuntimeError, match="Failed to complete task"):
            adapter.complete_task("id1")


class TestTasksWorkOn:
    def test_work_on_next_publishes(self) -> None:
        published = []
        adapter = _make_task_adapter(publisher=lambda t, p: published.append((t, p)))
        result = adapter.work_on_next_task(source="test")
        assert result["queued"] is True
        assert len(published) == 1

    def test_work_on_specific_publishes(self) -> None:
        published = []
        adapter = _make_task_adapter(publisher=lambda t, p: published.append((t, p)))
        result = adapter.work_on_task("t1", source="test")
        assert result["queued"] is True
        assert result["task_id"] == "t1"

    def test_publish_error_propagates(self) -> None:
        def _fail(topic: str, payload: bytes) -> None:
            raise ConnectionError("mqtt down")

        adapter = _make_task_adapter(publisher=_fail)
        with pytest.raises(RuntimeError, match="Failed to publish"):
            adapter.work_on_next_task()


# ── Research tool ─────────────────────────────────────────────────────── #


def _make_research_adapter(
    *,
    get_response: bytes | Exception | None = None,
    post_response: bytes | Exception | None = None,
    patch_response: bytes | Exception | None = None,
    publisher: Any = None,
    capture_urls: list | None = None,
) -> ResearchDiagnosticsToolAdapter:
    def _get(url: str, timeout: float) -> bytes:
        if capture_urls is not None:
            capture_urls.append(("GET", url))
        if isinstance(get_response, Exception):
            raise get_response
        return get_response or b"{}"

    def _post(url: str, timeout: float, payload: dict) -> bytes:
        if capture_urls is not None:
            capture_urls.append(("POST", url))
        if isinstance(post_response, Exception):
            raise post_response
        return post_response or b"{}"

    def _patch(url: str, timeout: float, payload: dict) -> bytes:
        if capture_urls is not None:
            capture_urls.append(("PATCH", url))
        if isinstance(patch_response, Exception):
            raise patch_response
        return patch_response or b"{}"

    return ResearchDiagnosticsToolAdapter(
        config=ResearchDiagnosticsToolConfig(base_url="http://test:9200"),
        http_get=_get,
        http_post=_post,
        http_patch=_patch,
        publisher=publisher or (lambda *a: None),
    )


class TestResearchGetNodes:
    def test_happy_path(self) -> None:
        body = json.dumps({"nodes": [{"id": "r1"}]}).encode()
        adapter = _make_research_adapter(get_response=body)
        assert len(adapter.get_research_nodes()) == 1

    def test_http_error_propagates(self) -> None:
        adapter = _make_research_adapter(get_response=ConnectionError("fail"))
        with pytest.raises(RuntimeError, match="Failed to fetch research"):
            adapter.get_research_nodes()


class TestResearchCreate:
    def test_happy_path(self) -> None:
        body = json.dumps({"id": "r2", "title": "Topic"}).encode()
        adapter = _make_research_adapter(post_response=body)
        result = adapter.create_research_node("Topic")
        assert result["id"] == "r2"

    def test_http_error_propagates(self) -> None:
        adapter = _make_research_adapter(post_response=ConnectionError("fail"))
        with pytest.raises(RuntimeError, match="Failed to create research"):
            adapter.create_research_node("Topic")


class TestResearchUpdate:
    def test_url_encodes_node_id(self) -> None:
        urls: list = []
        adapter = _make_research_adapter(patch_response=b"{}", capture_urls=urls)
        adapter.update_research_node("Node With Spaces", title="x")
        assert "Node%20With%20Spaces" in urls[0][1]

    def test_http_error_propagates(self) -> None:
        adapter = _make_research_adapter(patch_response=ConnectionError("fail"))
        with pytest.raises(RuntimeError, match="Failed to update research"):
            adapter.update_research_node("r1", title="x")


class TestResearchComplete:
    def test_url_encodes_node_id(self) -> None:
        urls: list = []
        body = json.dumps({"completed": True}).encode()
        adapter = _make_research_adapter(post_response=body, capture_urls=urls)
        adapter.complete_research_node("a/b c")
        assert "a%2Fb%20c" in urls[0][1]
        assert urls[0][1].endswith("/complete")

    def test_http_error_propagates(self) -> None:
        adapter = _make_research_adapter(post_response=ConnectionError("fail"))
        with pytest.raises(RuntimeError, match="Failed to complete research"):
            adapter.complete_research_node("r1")


class TestResearchWorkOn:
    def test_work_on_next_publishes(self) -> None:
        published = []
        adapter = _make_research_adapter(publisher=lambda t, p: published.append(1))
        result = adapter.work_on_next_research(source="test")
        assert result["queued"] is True

    def test_publish_error_propagates(self) -> None:
        def _fail(t: str, p: bytes) -> None:
            raise ConnectionError("mqtt down")
        adapter = _make_research_adapter(publisher=_fail)
        with pytest.raises(RuntimeError, match="Failed to publish"):
            adapter.work_on_next_research()


# ── Endocrine tool ────────────────────────────────────────────────────── #


class TestEndocrineStatus:
    def test_happy_path(self) -> None:
        body = json.dumps({"cortisol": 0.5, "dopamine": 0.8}).encode()
        adapter = EndocrineStatusToolAdapter(
            config=EndocrineStatusToolConfig(base_url="http://test:9200"),
            http_get=lambda url, t: body,
        )
        result = adapter.get_endocrine_status()
        assert result["cortisol"] == 0.5

    def test_http_error_propagates(self) -> None:
        def _fail(url: str, t: float) -> bytes:
            raise ConnectionError("refused")
        adapter = EndocrineStatusToolAdapter(
            config=EndocrineStatusToolConfig(),
            http_get=_fail,
        )
        with pytest.raises(RuntimeError, match="Failed to fetch endocrine"):
            adapter.get_endocrine_status()


# ── MQTT records tool ─────────────────────────────────────────────────── #


class TestMqttRecords:
    def test_happy_path(self) -> None:
        body = json.dumps({"messages": [{"topic": "a", "payload": "b"}]}).encode()
        adapter = MqttRecordsToolAdapter(
            config=MqttRecordsToolConfig(base_url="http://test:9200"),
            http_get=lambda url, t: body,
        )
        result = adapter.get_mqtt_records(limit=10)
        assert len(result) == 1

    def test_url_includes_limit(self) -> None:
        urls: list[str] = []
        def _get(url: str, t: float) -> bytes:
            urls.append(url)
            return json.dumps({"messages": []}).encode()
        adapter = MqttRecordsToolAdapter(
            config=MqttRecordsToolConfig(base_url="http://test:9200"),
            http_get=_get,
        )
        adapter.get_mqtt_records(limit=25)
        assert "limit=25" in urls[0]

    def test_http_error_propagates(self) -> None:
        def _fail(url: str, t: float) -> bytes:
            raise ConnectionError("refused")
        adapter = MqttRecordsToolAdapter(
            config=MqttRecordsToolConfig(),
            http_get=_fail,
        )
        with pytest.raises(RuntimeError, match="Failed to fetch MQTT"):
            adapter.get_mqtt_records()


# ── Event log tool ────────────────────────────────────────────────────── #


class TestEventLogRead:
    def test_happy_path(self) -> None:
        body = json.dumps({"events": [{"msg": "hello"}]}).encode()
        adapter = EventLogToolAdapter(
            config=EventLogToolConfig(base_url="http://test:9200"),
            http_get=lambda url, t: body,
        )
        result = adapter.read_events(limit=10)
        assert len(result) == 1

    def test_filter_params_in_url(self) -> None:
        urls: list[str] = []
        def _get(url: str, t: float) -> bytes:
            urls.append(url)
            return json.dumps({"events": []}).encode()
        adapter = EventLogToolAdapter(
            config=EventLogToolConfig(base_url="http://test:9200"),
            http_get=_get,
        )
        adapter.read_events(limit=50, level="ERROR", source="wui", search="crash")
        assert "level=ERROR" in urls[0]
        assert "source=wui" in urls[0]
        assert "search=crash" in urls[0]

    def test_http_error_propagates(self) -> None:
        def _fail(url: str, t: float) -> bytes:
            raise ConnectionError("refused")
        adapter = EventLogToolAdapter(
            config=EventLogToolConfig(),
            http_get=_fail,
        )
        with pytest.raises(RuntimeError, match="Failed to fetch events"):
            adapter.read_events()


class TestEventLogWrite:
    def test_write_event_returns_true(self) -> None:
        adapter = EventLogToolAdapter()
        assert adapter.write_event("test event", source="test") is True


# ── Doctor tool ───────────────────────────────────────────────────────── #


class TestDoctorTool:
    def test_call_doctor_publishes(self) -> None:
        published = []
        adapter = DoctorToolAdapter(
            config=DoctorToolConfig(),
            publisher=lambda t, p: published.append((t, p)),
        )
        result = adapter.call_doctor("system seems slow", source="test")
        assert result["queued"] is True
        assert len(published) == 1

    def test_publish_error_propagates(self) -> None:
        def _fail(t: str, p: bytes) -> None:
            raise ConnectionError("mqtt down")
        adapter = DoctorToolAdapter(
            config=DoctorToolConfig(),
            publisher=_fail,
        )
        with pytest.raises(RuntimeError, match="Failed to publish doctor"):
            adapter.call_doctor("reason")


# ── call_skill integration ────────────────────────────────────────────── #


@pytest.mark.asyncio
async def test_call_skill_surfaces_error_as_string() -> None:
    """When a skill raises, call_skill must return an Error string, not '{}'."""
    result = await call_skill("complete_task", {"task_id": "Test Task"})
    # The WUI is not running, so the HTTP call will fail.
    # The key assertion: the LLM gets an error message, not empty JSON.
    assert "Error" in result or "error" in result.lower()
    assert result != "{}"
    assert result != "[]"


@pytest.mark.asyncio
async def test_call_skill_nonexistent_tool() -> None:
    """Calling a non-existent skill should return an error string."""
    result = await call_skill("nonexistent_tool_xyz", {})
    assert "Error" in result or "error" in result.lower()


@pytest.mark.asyncio
async def test_call_skill_find_files() -> None:
    """find_files should work without HTTP (pure filesystem)."""
    result = await call_skill("find_files", {"pattern": "pyproject.toml", "cwd": "."})
    assert "pyproject.toml" in result


@pytest.mark.asyncio
async def test_call_skill_list_embedded_skills() -> None:
    """list_embedded_skills returns a listing of all tools."""
    result = await call_skill("list_embedded_skills", {})
    assert "embedded skills" in result.lower()
    # Spot-check a few known skills
    assert "complete_task" in result
    assert "find_files" in result
    assert "web_search" in result


@pytest.mark.asyncio
async def test_call_skill_ask_user() -> None:
    """ask_user returns a pending message, not an error."""
    result = await call_skill("ask_user", {"question": "Are you there?"})
    assert "question_pending" in result


@pytest.mark.asyncio
async def test_call_skill_write_event() -> None:
    """write_event should succeed (just logs)."""
    result = await call_skill(
        "write_event",
        {"message": "test event from skill test", "level": "INFO", "source": "test"},
    )
    assert "logged" in result.lower() or "Event" in result
