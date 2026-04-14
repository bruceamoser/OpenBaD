from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from openbad.autonomy.endocrine_runtime import EndocrineRuntime
from openbad.endocrine.config import EndocrineConfig
from openbad.state.db import initialize_state_db
from openbad.tasks.research_queue import ResearchQueue, initialize_research_db


def test_process_research_call_dequeues_and_posts_session(monkeypatch, tmp_path: Path) -> None:
    import openbad.autonomy.scheduler_worker as worker

    db_path = tmp_path / "state.db"
    conn = initialize_state_db(db_path)
    initialize_research_db(conn)
    node = ResearchQueue(conn).enqueue(
        "Temporal decay study",
        description="Investigate temporal decay in semantic memory.",
    )

    posted_user: list[tuple[str, str]] = []
    posted_assistant: list[tuple[str, str]] = []

    monkeypatch.setattr(
        worker,
        "EndocrineRuntime",
        lambda *, config: EndocrineRuntime(config=config, db_path=db_path),
    )
    monkeypatch.setattr(worker, "load_endocrine_config", lambda: EndocrineConfig())
    monkeypatch.setattr(worker, "_read_providers_config", lambda: (Path("unused"), object()))

    class _Adapter:
        async def complete(self, prompt: str, model_id: str | None = None):
            return SimpleNamespace(
                content="Temporal decay summary",
                tokens_used=12,
                model_id=model_id or "test-model",
            )

    monkeypatch.setattr(
        worker,
        "_resolve_chat_adapter",
        lambda _config, _system: (_Adapter(), "test-model", "test-provider", False),
    )
    monkeypatch.setattr(
        worker,
        "append_session_message",
        lambda session_id, role, content: posted_user.append((session_id, content)),
    )
    monkeypatch.setattr(
        worker,
        "append_assistant_message",
        lambda session_id, content, extra_metadata=None: posted_assistant.append((session_id, content)),
    )

    class _UsageTracker:
        def __init__(self, db_path=None) -> None:
            self.db_path = db_path

        def record(self, **kwargs) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(worker, "UsageTracker", _UsageTracker)

    result = worker.process_research_call({"node_id": node.node_id}, db_path=db_path)

    updated = ResearchQueue(conn).get(node.node_id)
    reward_rows = conn.execute("SELECT task_id, node_id, template_id, score FROM reward_records").fetchall()
    adjustment_rows = conn.execute(
        "SELECT source, reason FROM endocrine_adjustments ORDER BY ts ASC"
    ).fetchall()

    assert result["executed_research_id"] == node.node_id
    assert updated is not None
    assert updated.dequeued_at is not None
    assert posted_user
    assert posted_user[0][0] == "research-autonomy"
    assert any("Research complete: Temporal decay study" in content for _, content in posted_assistant)
    assert len(reward_rows) == 1
    assert reward_rows[0][1] == node.node_id
    assert any(row[0] == "research" for row in adjustment_rows)


def test_process_research_call_strips_interactive_followups(monkeypatch, tmp_path: Path) -> None:
    import openbad.autonomy.scheduler_worker as worker

    db_path = tmp_path / "state.db"
    conn = initialize_state_db(db_path)
    initialize_research_db(conn)
    node = ResearchQueue(conn).enqueue(
        "Research hygiene",
        description="Investigate how research sessions summarize outputs.",
    )

    posted_assistant: list[str] = []

    monkeypatch.setattr(
        worker,
        "EndocrineRuntime",
        lambda *, config: EndocrineRuntime(config=config, db_path=db_path),
    )
    monkeypatch.setattr(worker, "load_endocrine_config", lambda: EndocrineConfig())
    monkeypatch.setattr(worker, "_read_providers_config", lambda: (Path("unused"), object()))

    class _Adapter:
        async def complete(self, prompt: str, model_id: str | None = None):
            return SimpleNamespace(
                content=(
                    "Research result: summary of findings.\n\n"
                    "Actionable Next Steps:\n"
                    "1. Review the findings.\n"
                    "2. Choose the next item.\n\n"
                    "Would you like me to proceed with one of these?"
                ),
                tokens_used=12,
                model_id=model_id or "test-model",
            )

    monkeypatch.setattr(
        worker,
        "_resolve_chat_adapter",
        lambda _config, _system: (_Adapter(), "test-model", "test-provider", False),
    )
    monkeypatch.setattr(worker, "append_session_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        worker,
        "append_assistant_message",
        lambda session_id, content, extra_metadata=None: posted_assistant.append(content),
    )

    class _UsageTracker:
        def __init__(self, db_path=None) -> None:
            self.db_path = db_path

        def record(self, **kwargs) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(worker, "UsageTracker", _UsageTracker)

    worker.process_research_call({"node_id": node.node_id}, db_path=db_path)

    assert posted_assistant
    assert "Would you like" not in posted_assistant[0]
    assert "Actionable Next Steps" not in posted_assistant[0]


def test_process_research_call_leaves_requested_node_pending_on_failure(monkeypatch, tmp_path: Path) -> None:
    import openbad.autonomy.scheduler_worker as worker

    db_path = tmp_path / "state.db"
    conn = initialize_state_db(db_path)
    initialize_research_db(conn)
    node = ResearchQueue(conn).enqueue(
        "Pending retry research",
        description="Should remain pending when provider is unavailable.",
    )

    monkeypatch.setattr(
        worker,
        "EndocrineRuntime",
        lambda *, config: EndocrineRuntime(config=config, db_path=db_path),
    )
    monkeypatch.setattr(worker, "load_endocrine_config", lambda: EndocrineConfig())
    monkeypatch.setattr(worker, "_read_providers_config", lambda: (Path("unused"), object()))
    monkeypatch.setattr(
        worker,
        "_resolve_chat_adapter",
        lambda _config, _system: (None, None, "", False),
    )
    monkeypatch.setattr(worker, "append_session_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "append_assistant_message", lambda *args, **kwargs: None)

    class _UsageTracker:
        def __init__(self, db_path=None) -> None:
            self.db_path = db_path

        def record(self, **kwargs) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(worker, "UsageTracker", _UsageTracker)

    result = worker.process_research_call({"node_id": node.node_id}, db_path=db_path)

    updated = ResearchQueue(conn).get(node.node_id)
    assert result["executed_research_id"] is None
    assert updated is not None
    assert updated.dequeued_at is None


def test_process_pending_autonomy_work_escalates_recent_log_errors(monkeypatch, tmp_path: Path) -> None:
    import openbad.autonomy.scheduler_worker as worker

    db_path = tmp_path / "state.db"
    conn = initialize_state_db(db_path)
    initialize_research_db(conn)

    posted_assistant: list[tuple[str, str]] = []

    monkeypatch.setattr(
        worker,
        "EndocrineRuntime",
        lambda *, config: EndocrineRuntime(config=config, db_path=db_path),
    )
    monkeypatch.setattr(worker, "load_endocrine_config", lambda: EndocrineConfig())
    monkeypatch.setattr(worker, "_read_providers_config", lambda: (Path("unused"), SimpleNamespace(providers=[], systems={})))

    now = datetime.now(tz=UTC).isoformat()

    def _recent_events(*, limit=100, level=None, source=None, search=None, log_dir=None):  # noqa: ARG001
        if level == "ERROR":
            return [{"ts": now, "level": "ERROR", "source": "openbad.autonomy.scheduler_worker", "message": "Research worker crashed"}]
        if level == "CRITICAL":
            return []
        if level == "WARNING":
            return [{"ts": now, "level": "WARNING", "source": "openbad.daemon", "message": "Scheduler worker already active"}]
        return []

    monkeypatch.setattr(worker, "recent_events", _recent_events)

    class _DoctorAdapter:
        async def complete(self, prompt: str, model_id: str | None = None):
            return SimpleNamespace(
                content='{"summary":"Doctor observed runtime log stress","mood_tags":["concerned"],"actions":[]}',
                tokens_used=14,
                model_id=model_id or "doctor-model",
            )

    monkeypatch.setattr(
        worker,
        "_resolve_chat_adapter",
        lambda _config, system: (_DoctorAdapter(), f"{system}-model", "test-provider", False),
    )
    monkeypatch.setattr(worker, "append_session_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        worker,
        "append_assistant_message",
        lambda session_id, content, extra_metadata=None: posted_assistant.append((session_id, content)),
    )

    class _UsageTracker:
        def __init__(self, db_path=None) -> None:
            self.db_path = db_path

        def record(self, **kwargs) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(worker, "UsageTracker", _UsageTracker)

    result = worker.process_pending_autonomy_work(db_path=db_path)

    adjustments = conn.execute(
        "SELECT source, reason, deltas_json FROM endocrine_adjustments ORDER BY ts ASC"
    ).fetchall()
    assert result["executed_doctor"] == "Doctor observed runtime log stress"
    assert any(row[0] == "log-health" for row in adjustments)
    assert any(session_id == "doctor-autonomy" for session_id, _ in posted_assistant)


def test_research_tool_validator_blocks_self_duplicate() -> None:
    import openbad.autonomy.scheduler_worker as worker

    node = type("Node", (), {
        "title": "Immune Verified Smoke Research",
        "description": "Smoke test forcing immune analyst follow-up behavior",
    })()

    validator = worker._build_research_tool_validator(node)

    assert validator(
        "create_research_node",
        {
            "title": "Immune Verified Smoke Research",
            "description": "Smoke test forcing immune analyst follow-up behavior",
        },
    ) is not None
    assert validator(
        "create_research_node",
        {
            "title": "Immune Verified Smoke Research follow-up",
            "description": "Smoke test forcing immune analyst follow-up behavior",
        },
    ) is None