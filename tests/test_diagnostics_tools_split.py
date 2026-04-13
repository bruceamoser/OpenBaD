from __future__ import annotations

import json

from openbad.toolbelt.endocrine_status_tool import EndocrineStatusToolAdapter
from openbad.toolbelt.event_log_tool import EventLogToolAdapter
from openbad.toolbelt.mqtt_records_tool import MqttRecordsToolAdapter
from openbad.toolbelt.research_diagnostics_tool import ResearchDiagnosticsToolAdapter
from openbad.toolbelt.system_logs_tool import SystemLogsToolAdapter
from openbad.toolbelt.tasks_diagnostics_tool import TasksDiagnosticsToolAdapter


def _make_http_get(payload: dict):
    raw = json.dumps(payload).encode("utf-8")

    def _http_get(_url: str, _timeout: float) -> bytes:
        return raw

    return _http_get


def _make_http_post(payload: dict):
    raw = json.dumps(payload).encode("utf-8")

    def _http_post(_url: str, _timeout: float, _body: dict) -> bytes:
        return raw

    return _http_post


def test_mqtt_records_tool_returns_messages() -> None:
    tool = MqttRecordsToolAdapter(
        http_get=_make_http_get({"messages": [{"topic": "agent/endocrine/cortisol"}]})
    )
    out = tool.get_mqtt_records(limit=10)
    assert out == [{"topic": "agent/endocrine/cortisol"}]


def test_system_logs_tool_returns_logs() -> None:
    tool = SystemLogsToolAdapter(
        http_get=_make_http_get({"logs": [{"logger": "openbad.heartbeat"}]})
    )
    out = tool.get_system_logs(limit=50, system="heartbeat")
    assert out == [{"logger": "openbad.heartbeat"}]


def test_endocrine_status_tool_returns_dict() -> None:
    payload = {"levels": {"cortisol": 0.22}, "severity": {"cortisol": 1}}
    tool = EndocrineStatusToolAdapter(http_get=_make_http_get(payload))
    out = tool.get_endocrine_status()
    assert out["levels"]["cortisol"] == 0.22


def test_tasks_tool_returns_tasks() -> None:
    tool = TasksDiagnosticsToolAdapter(http_get=_make_http_get({"tasks": [{"task_id": "t1"}]}))
    out = tool.get_tasks()
    assert out == [{"task_id": "t1"}]


def test_research_tool_returns_nodes() -> None:
    tool = ResearchDiagnosticsToolAdapter(http_get=_make_http_get({"nodes": [{"node_id": "r1"}]}))
    out = tool.get_research_nodes()
    assert out == [{"node_id": "r1"}]


def test_tasks_tool_can_create_task() -> None:
    tool = TasksDiagnosticsToolAdapter(
        http_post=_make_http_post({"task_id": "t-new", "title": "Create docs"})
    )
    out = tool.create_task("Create docs", description="draft runbook", owner="user")
    assert out["task_id"] == "t-new"


def test_research_tool_can_create_node() -> None:
    tool = ResearchDiagnosticsToolAdapter(
        http_post=_make_http_post({"node_id": "r-new", "title": "Investigate"})
    )
    out = tool.create_research_node(
        "Investigate",
        description="provider regressions",
        priority=2,
        source_task_id="task-123",
    )
    assert out["node_id"] == "r-new"


def test_tools_fail_safe_on_transport_error() -> None:
    def _boom(_url: str, _timeout: float) -> bytes:
        raise OSError("down")

    assert MqttRecordsToolAdapter(http_get=_boom).get_mqtt_records() == []
    assert SystemLogsToolAdapter(http_get=_boom).get_system_logs() == []
    assert EndocrineStatusToolAdapter(http_get=_boom).get_endocrine_status() == {}
    assert TasksDiagnosticsToolAdapter(http_get=_boom).get_tasks() == []
    assert ResearchDiagnosticsToolAdapter(http_get=_boom).get_research_nodes() == []
    assert EventLogToolAdapter(http_get=_boom).read_events() == []


def test_event_log_tool_read_returns_events() -> None:
    tool = EventLogToolAdapter(
        http_get=_make_http_get({
            "events": [
                {"ts": "2026-04-12 20:00:00", "level": "ERROR", "source": "openbad.wui", "message": "connection refused"},
            ]
        })
    )
    out = tool.read_events(limit=10, level="ERROR")
    assert len(out) == 1
    assert out[0]["level"] == "ERROR"
    assert out[0]["message"] == "connection refused"


def test_event_log_tool_write_returns_true() -> None:
    tool = EventLogToolAdapter()
    assert tool.write_event("test event", level="INFO", source="test") is True


def test_create_tools_fail_safe_on_transport_error() -> None:
    def _boom(_url: str, _timeout: float, _body: dict | None = None) -> bytes:
        raise OSError("down")

    assert TasksDiagnosticsToolAdapter(http_post=_boom).create_task("x") == {}
    assert ResearchDiagnosticsToolAdapter(http_post=_boom).create_research_node("x") == {}
