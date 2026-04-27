from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from openbad.autonomy.endocrine_runtime import EndocrineRuntime
from openbad.autonomy.scheduler_worker import (
    _classify_research_complexity,
    _classify_task_complexity,
)
from openbad.endocrine.config import EndocrineConfig
from openbad.state.db import initialize_state_db
from openbad.tasks.models import TaskKind, TaskModel, TaskPriority
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
        lambda _config, _system: (_Adapter(), "test-model", "test-provider", False, None, None),
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
        lambda _config, _system: (_Adapter(), "test-model", "test-provider", False, None, None),
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
        lambda _config, _system: (None, None, "", False, None, None),
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
        lambda _config, system: (_DoctorAdapter(), f"{system}-model", "test-provider", False, None, None),
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


# ── Task complexity classification ──────────────────────────────────── #


def _make_task(title: str, description: str = "") -> TaskModel:
    return TaskModel.new(
        title=title,
        description=description,
        kind=TaskKind.USER_REQUESTED,
        priority=int(TaskPriority.NORMAL),
        owner="test",
    )


class TestClassifyTaskComplexity:
    def test_simple_task(self) -> None:
        task = _make_task("Check status", "Report current system status")
        assert _classify_task_complexity(task) is False

    def test_explicit_crew_flag(self) -> None:
        task = _make_task("Complex work", "Do things [crew]")
        assert _classify_task_complexity(task) is True

    def test_keyword_multistep(self) -> None:
        task = _make_task(
            "Refactor module",
            "Multi-step refactor of the authentication module",
        )
        assert _classify_task_complexity(task) is True

    def test_keyword_pipeline(self) -> None:
        task = _make_task("Build pipeline", "Integrate the data pipeline")
        assert _classify_task_complexity(task) is True

    def test_long_description(self) -> None:
        task = _make_task("Complex task", "x " * 300)
        assert _classify_task_complexity(task) is True

    def test_checklist_subtasks(self) -> None:
        task = _make_task(
            "Multi-item work",
            "Steps:\n- [ ] Step one\n- [ ] Step two\n- [x] Done",
        )
        assert _classify_task_complexity(task) is True

    def test_single_checklist_item_is_simple(self) -> None:
        task = _make_task(
            "Simple checkbox",
            "Steps:\n- [ ] Just one thing",
        )
        assert _classify_task_complexity(task) is False


class TestClassifyResearchComplexity:
    def test_simple_research(self) -> None:
        assert _classify_research_complexity(
            "Check dependency license", "Look up the license."
        ) is False

    def test_explicit_crew_flag(self) -> None:
        assert _classify_research_complexity(
            "Deep dive", "Do analysis [crew]"
        ) is True

    def test_keyword_synthesize(self) -> None:
        assert _classify_research_complexity(
            "Synthesize findings",
            "Compare and synthesize results from multiple sources.",
        ) is True

    def test_keyword_comprehensive(self) -> None:
        assert _classify_research_complexity(
            "Comprehensive audit", "Audit the codebase."
        ) is True

    def test_long_description(self) -> None:
        assert _classify_research_complexity(
            "Deep research", "y " * 300
        ) is True


class TestCrewDispatchIntegration:
    """Test that _process_task routes to crew for complex tasks."""

    def test_complex_task_attempts_crew(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Complex task should attempt crew dispatch."""
        import openbad.autonomy.scheduler_worker as worker

        db_path = tmp_path / "state.db"
        conn = initialize_state_db(db_path)
        from openbad.tasks.research_queue import initialize_research_db
        from openbad.tasks.reward_endocrine import initialize_reward_db

        initialize_research_db(conn)
        initialize_reward_db(conn)

        monkeypatch.setattr(
            worker,
            "EndocrineRuntime",
            lambda *, config: EndocrineRuntime(
                config=config, db_path=db_path
            ),
        )
        monkeypatch.setattr(
            worker, "load_endocrine_config", lambda: EndocrineConfig()
        )
        monkeypatch.setattr(
            worker,
            "load_session_policy",
            lambda: {"tasks": {"allow_task_autonomy": True}},
        )
        monkeypatch.setattr(
            worker,
            "_read_providers_config",
            lambda: (Path("unused"), object()),
        )

        # Mock _resolve_chat_adapter to provide chat_model + crew_llm
        mock_chat_model = SimpleNamespace()
        mock_crew_llm = SimpleNamespace()
        monkeypatch.setattr(
            worker,
            "_resolve_chat_adapter",
            lambda _cfg, _sys: (
                None, "test-model", "test-provider", False,
                mock_chat_model, mock_crew_llm,
            ),
        )
        monkeypatch.setattr(
            worker,
            "append_assistant_message",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            worker,
            "append_session_message",
            lambda *a, **kw: None,
        )

        monkeypatch.setattr(
            worker,
            "recent_events",
            lambda **kw: [],
        )

        class _UsageTracker:
            def __init__(self, db_path=None) -> None:
                pass

            def record(self, **kwargs) -> None:
                pass

            def record_detail(self, **kwargs) -> None:
                pass

            def close(self) -> None:
                pass

        monkeypatch.setattr(worker, "UsageTracker", _UsageTracker)

        # Create a complex task
        from openbad.tasks.service import TaskService

        svc = TaskService.get_instance(db_path)
        task = svc.create_task(
            title="Refactor authentication module",
            description=(
                "Multi-step refactor of the authentication module.\n"
                "- [ ] Audit current code\n"
                "- [ ] Design new architecture\n"
                "- [ ] Implement changes"
            ),
            owner="test-user",
        )

        crew_called = []

        def _mock_create_crew(
            user_message, *, llm_factory=None, tools_factory=None
        ):
            crew_called.append(user_message)
            mock_crew = SimpleNamespace(
                kickoff=lambda: SimpleNamespace(
                    raw="Crew completed the refactor."
                ),
            )
            return mock_crew

        monkeypatch.setattr(
            "openbad.frameworks.crews.user_facing"
            ".create_user_facing_crew",
            _mock_create_crew,
        )

        result = worker.process_task_call(
            {"task_id": task.task_id}, db_path=db_path
        )

        assert result["executed_task_id"] == task.task_id
        assert len(crew_called) == 1
        assert "Refactor authentication" in crew_called[0]