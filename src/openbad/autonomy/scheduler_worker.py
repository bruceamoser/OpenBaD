from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from openbad.autonomy.endocrine_runtime import EndocrineRuntime, load_endocrine_config
from openbad.autonomy.session_policy import (
    is_destructive_request,
    load_session_policy,
    session_allows,
    session_id_for,
)
from openbad.state.db import DEFAULT_STATE_DB_PATH, initialize_state_db
from openbad.tasks.models import TaskKind, TaskModel, TaskPriority, TaskStatus
from openbad.tasks.research_queue import ResearchQueue, initialize_research_db
from openbad.tasks.store import TaskStore
from openbad.wui.chat_pipeline import append_assistant_message, append_session_message
from openbad.wui.server import _provider_is_valid, _read_providers_config, _resolve_chat_adapter
from openbad.wui.usage_tracker import UsageTracker, resolve_usage_db_path

log = logging.getLogger(__name__)


def process_pending_autonomy_work(db_path: str | Path | None = None) -> dict[str, str | None]:
    return _process_autonomy_work(
        db_path=db_path,
        run_tasks=True,
        run_research=True,
        task_request=None,
        research_request=None,
        doctor_request=None,
    )


def process_doctor_call(
    request: dict[str, object] | None = None,
    db_path: str | Path | None = None,
) -> dict[str, str | None]:
    return _process_autonomy_work(
        db_path=db_path,
        run_tasks=False,
        run_research=False,
        task_request=None,
        research_request=None,
        doctor_request=request or {},
    )


def process_task_call(
    request: dict[str, object] | None = None,
    db_path: str | Path | None = None,
) -> dict[str, str | None]:
    return _process_autonomy_work(
        db_path=db_path,
        run_tasks=False,
        run_research=False,
        task_request=request or {},
        research_request=None,
        doctor_request=None,
    )


def process_research_call(
    request: dict[str, object] | None = None,
    db_path: str | Path | None = None,
) -> dict[str, str | None]:
    return _process_autonomy_work(
        db_path=db_path,
        run_tasks=False,
        run_research=False,
        task_request=None,
        research_request=request or {},
        doctor_request=None,
    )


def _process_autonomy_work(
    db_path: str | Path | None = None,
    *,
    run_tasks: bool,
    run_research: bool,
    task_request: dict[str, object] | None,
    research_request: dict[str, object] | None,
    doctor_request: dict[str, object] | None,
) -> dict[str, str | None]:
    resolved_db_path = Path(db_path) if db_path else DEFAULT_STATE_DB_PATH
    conn = initialize_state_db(resolved_db_path)
    initialize_research_db(conn)

    policy = load_session_policy()
    chat_session_id = session_id_for(policy, "chat")
    task_session_id = session_id_for(policy, "tasks")
    research_session_id = session_id_for(policy, "research")
    doctor_session_id = session_id_for(policy, "doctor")
    usage_tracker = UsageTracker(db_path=resolve_usage_db_path())

    endocrine_runtime = EndocrineRuntime(config=load_endocrine_config())
    endocrine_runtime.decay_to()
    endocrine_config = endocrine_runtime.config
    task_store = TaskStore(conn)

    executed_task_id: str | None = None
    executed_research_id: str | None = None
    executed_doctor: str | None = None

    def _post_session(
        session_id: str,
        content: str,
        *,
        extra_metadata: dict[str, object] | None = None,
    ) -> None:
        try:
            append_assistant_message(session_id, content, extra_metadata=extra_metadata)
        except Exception:
            log.exception("Failed to post session message: session=%s", session_id)

    def _adjust(source: str, reason: str, deltas: dict[str, float]) -> None:
        try:
            endocrine_runtime.apply_adjustment(source=source, reason=reason, deltas=deltas)
        except Exception:
            log.exception("Failed endocrine adjustment: source=%s reason=%s", source, reason)

    def _has_recent_adjustment(source: str, reason: str, *, window_seconds: int) -> bool:
        row = conn.execute(
            """
            SELECT 1
            FROM endocrine_adjustments
            WHERE source = ?
              AND reason = ?
              AND ts >= ?
            LIMIT 1
            """,
            (source, reason, max(0.0, endocrine_runtime.last_update_ts - max(1, int(window_seconds)))),
        ).fetchone()
        return row is not None

    def _parse_doctor_payload(raw: str) -> dict[str, object] | None:
        text = (raw or "").strip()
        if not text:
            return None
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
        try:
            parsed = json.loads(text)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _run_llm(
        prompt: str,
        *,
        system_name: str,
        session_id: str,
        request_id: str,
    ) -> tuple[str, str, str] | None:
        try:
            _cfg_path, cfg = _read_providers_config()
            adapter, model, provider_name, _is_fallback = _resolve_chat_adapter(cfg, system_name)
            if adapter is None or model is None:
                return None
            result = asyncio.run(adapter.complete(prompt, model))
            usage_tracker.record(
                provider=provider_name or "unknown",
                model=result.model_id or model,
                system=system_name,
                tokens=int(result.tokens_used),
                request_id=request_id,
                session_id=session_id,
            )
            return result.content.strip(), provider_name or "", result.model_id
        except Exception:
            log.exception("LLM call failed for system=%s", system_name)
            return None

    def _top_pending_task() -> TaskModel | None:
        row = conn.execute(
            """
            SELECT task_id
            FROM tasks
            WHERE status = 'pending'
              AND kind NOT IN ('scheduled', 'system')
              AND title NOT LIKE 'Question:%'
              AND (due_at IS NULL OR due_at <= strftime('%s','now'))
            ORDER BY priority DESC, created_at ASC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        return task_store.get_task(str(row[0]))

    def _requested_task(request: dict[str, object] | None) -> TaskModel | None:
        if not isinstance(request, dict):
            return None
        task_id = str(request.get("task_id", "")).strip()
        if task_id:
            task = task_store.get_task(task_id)
            if task is None or task.status is not TaskStatus.PENDING:
                return None
            return task
        return _top_pending_task()

    def _requested_research(request: dict[str, object] | None):
        queue = ResearchQueue(conn)
        if not isinstance(request, dict):
            return None
        node_id = str(request.get("node_id", "")).strip()
        if node_id:
            node = queue.get(node_id)
            if node is None or node.dequeued_at is not None:
                return None
            return node
        return queue.peek()

    def _create_question_task(source_task: TaskModel, question: str) -> TaskModel:
        task = TaskModel.new(
            title=f"Question: Approval required for task {source_task.task_id[:8]}",
            description=question,
            kind=TaskKind.SYSTEM,
            priority=int(TaskPriority.HIGH),
            owner="scheduler-worker",
        )
        return task_store.create_task(task)

    def _create_reenable_task(system_name: str, reason: str, due_at: float) -> TaskModel:
        existing = conn.execute(
            """
            SELECT task_id
            FROM tasks
            WHERE status = 'pending'
              AND kind = 'system'
              AND title = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (f"Endocrine follow-up: re-enable {system_name}",),
        ).fetchone()
        if existing:
            task = task_store.get_task(str(existing[0]))
            if task is not None:
                return task

        followup = TaskModel.new(
            title=f"Endocrine follow-up: re-enable {system_name}",
            description=(
                f"ENDOCRINE_REENABLE:{system_name}\n"
                f"Scheduled by doctor loop. Reason: {reason}"
            ),
            kind=TaskKind.SYSTEM,
            priority=int(TaskPriority.NORMAL),
            owner="endocrine-doctor",
            due_at=due_at,
        )
        return task_store.create_task(followup)

    def _process_endocrine_followups() -> None:
        rows = conn.execute(
            """
            SELECT task_id
            FROM tasks
            WHERE status = 'pending'
              AND kind = 'system'
              AND title LIKE 'Endocrine follow-up: re-enable %'
              AND (due_at IS NULL OR due_at <= strftime('%s','now'))
            ORDER BY created_at ASC
            """
        ).fetchall()
        for row in rows:
            follow = task_store.get_task(str(row[0]))
            if follow is None:
                continue
            marker = (follow.description or "").splitlines()[0].strip()
            if not marker.startswith("ENDOCRINE_REENABLE:"):
                continue
            system_name = marker.split(":", 1)[1].strip().lower()
            endocrine_runtime.enable_system(
                system_name,
                reason="Follow-up re-enable",
                now=endocrine_runtime.last_update_ts,
            )
            task_store.update_task_status(follow.task_id, TaskStatus.DONE)
            task_store.append_event(
                follow.task_id,
                "doctor_followup_completed",
                payload={"system": system_name},
            )
            _post_session(
                doctor_session_id,
                f"Follow-up executed: re-enabled {system_name}.",
                extra_metadata={"doctor_revelation": True, "health_decision": True},
            )

    def _process_task(task_to_run: TaskModel) -> None:
        nonlocal executed_task_id
        combined = f"{task_to_run.title}\n\n{task_to_run.description}".strip()
        allow_destructive = session_allows(policy, "tasks", "allow_destructive", False)
        if is_destructive_request(combined) and not allow_destructive:
            task_store.update_task_status(task_to_run.task_id, TaskStatus.BLOCKED_ON_USER)
            question = (
                "Approve destructive action for task "
                f"{task_to_run.task_id}: {task_to_run.title}. "
                "Reply in regular chat with explicit approval details."
            )
            _create_question_task(task_to_run, question)
            _post_session(
                chat_session_id,
                f"I deferred task '{task_to_run.title}' because it may be destructive.",
            )
            _post_session(task_session_id, f"Deferred task {task_to_run.task_id} pending user approval.")
            _adjust("tasks", f"Deferred potentially destructive task {task_to_run.task_id}", {"cortisol": 0.06})
            executed_task_id = task_to_run.task_id
            return

        task_store.update_task_status(task_to_run.task_id, TaskStatus.RUNNING)
        llm = _run_llm(
            (
                "You are the autonomous OpenBaD task worker. "
                "Complete the task in a non-destructive way and return a concise status summary.\n\n"
                f"Task: {task_to_run.title}\nDescription: {task_to_run.description}"
            ),
            system_name="tasks",
            session_id=task_session_id,
            request_id=task_to_run.task_id,
        )
        if llm is None:
            task_store.update_task_status(task_to_run.task_id, TaskStatus.FAILED)
            task_store.append_event(
                task_to_run.task_id,
                "autonomy_failed",
                payload={"reason": "provider_unavailable", "owner": task_to_run.owner},
            )
            _post_session(task_session_id, f"Task {task_to_run.task_id} failed: no provider available.")
            _adjust("tasks", f"Task {task_to_run.task_id} failed", {"cortisol": 0.12})
            return

        summary, provider_name, model_id = llm
        task_store.update_task_status(task_to_run.task_id, TaskStatus.DONE)
        task_store.append_event(
            task_to_run.task_id,
            "autonomy_completed",
            payload={"summary": summary, "provider": provider_name, "model": model_id},
        )
        meta = {"provider": provider_name, "model": model_id}
        _post_session(task_session_id, f"Completed task '{task_to_run.title}': {summary}", extra_metadata=meta)
        _post_session(chat_session_id, f"Autonomous task update: completed '{task_to_run.title}'.", extra_metadata=meta)
        _adjust("tasks", f"Completed task {task_to_run.task_id}", {"dopamine": 0.10, "endorphin": 0.05})
        executed_task_id = task_to_run.task_id

    def _process_research(node=None) -> None:
        nonlocal executed_research_id
        queue = ResearchQueue(conn)
        if node is None:
            node = queue.dequeue()
        else:
            queue.complete(node.node_id)
        if node is None:
            return
        research_prompt = (
            f"**Research Project:** {node.title}\n\n{node.description}"
            if node.description
            else f"**Research Project:** {node.title}"
        )
        try:
            append_session_message(research_session_id, "user", research_prompt)
        except Exception:
            log.exception("Failed to post research prompt to session")

        llm = _run_llm(
            (
                "You are the OpenBaD research worker. "
                "Produce a concise research result with actionable next steps.\n\n"
                f"Research title: {node.title}\nDescription: {node.description}"
            ),
            system_name="research",
            session_id=research_session_id,
            request_id=node.node_id,
        )
        if llm is None:
            _post_session(research_session_id, f"Research node {node.node_id} dequeued but provider was unavailable.")
            _adjust("research", f"Research node {node.node_id} failed", {"cortisol": 0.09})
            return

        summary, provider_name, model_id = llm
        meta = {"provider": provider_name, "model": model_id}
        _post_session(
            research_session_id,
            f"**Research complete: {node.title}**\n\n{summary}\n\n*Provider: {provider_name} / {model_id}*",
            extra_metadata=meta,
        )
        _post_session(chat_session_id, f"Autonomous research update: completed '{node.title}'.", extra_metadata=meta)
        _adjust("research", f"Completed research node {node.node_id}", {"dopamine": 0.08, "endorphin": 0.04})
        executed_research_id = node.node_id

    def _process_doctor(request: dict[str, object] | None) -> None:
        nonlocal executed_doctor
        levels = endocrine_runtime.levels
        source_recent = endocrine_runtime.source_contributions(window_seconds=900, now=endocrine_runtime.last_update_ts)
        severity = endocrine_runtime.current_severity()
        request_payload = request if isinstance(request, dict) else {}
        request_source = str(request_payload.get("source", "unknown")).strip() or "unknown"
        request_reason = str(request_payload.get("reason", "doctor call requested")).strip() or "doctor call requested"
        request_context = request_payload.get("context", {})
        if not isinstance(request_context, dict):
            request_context = {"raw_context": request_context}
        hormone_names = ("dopamine", "adrenaline", "cortisol", "endorphin")
        activation_thresholds = {
            hormone: getattr(endocrine_config, hormone).activation_threshold
            for hormone in hormone_names
        }
        escalation_thresholds = {
            hormone: getattr(endocrine_config, hormone).escalation_threshold
            for hormone in hormone_names
        }
        doctor_prompt = (
            "You are the OpenBaD endocrine doctor. "
            "Evaluate global hormone levels and recent source contributions. "
            "Return strict JSON with keys: summary (string), mood_tags (array of strings), "
            "actions (array). Each action must be one of: "
            "{\"type\":\"disable_system\",\"system\":\"chat|tasks|research\","
            "\"duration_minutes\":int,\"reason\":string}, "
            "{\"type\":\"enable_system\",\"system\":\"chat|tasks|research\","
            "\"reason\":string}, "
            "{\"type\":\"adjust\",\"source\":string,\"reason\":string,"
            "\"deltas\":{dopamine,adrenaline,cortisol,endorphin}}. "
            "Use disable/enable actions only if justified by thresholds.\n\n"
            f"Doctor call source: {request_source}\n"
            f"Doctor call reason: {request_reason}\n"
            f"Doctor call context: {json.dumps(request_context, sort_keys=True)}\n"
            f"Current levels: {json.dumps(levels, sort_keys=True)}\n"
            f"Severity: {json.dumps(severity, sort_keys=True)}\n"
            f"Activation thresholds: {json.dumps(activation_thresholds, sort_keys=True)}\n"
            f"Escalation thresholds: {json.dumps(escalation_thresholds, sort_keys=True)}\n"
            f"Recent source contributions (15m): {json.dumps(source_recent, sort_keys=True)}"
        )
        llm = _run_llm(
            doctor_prompt,
            system_name="doctor",
            session_id=doctor_session_id,
            request_id=f"doctor-{int(endocrine_runtime.last_update_ts)}",
        )
        parsed: dict[str, object] | None = None
        provider_name = ""
        model_id = ""
        if llm is not None:
            summary, provider_name, model_id = llm
            parsed = _parse_doctor_payload(summary)
            endocrine_runtime.add_doctor_note(
                {
                    "source": "llm",
                    "provider": provider_name,
                    "model": model_id,
                    "summary": str(parsed.get("summary", "")).strip() if isinstance(parsed, dict) else "",
                    "raw": summary,
                },
                now=endocrine_runtime.last_update_ts,
            )

        if parsed is None and levels.get("cortisol", 0.0) >= 0.8:
            parsed = {
                "summary": "Fallback doctor: high cortisol, temporarily disable chat and schedule recovery.",
                "mood_tags": ["overloaded", "protective"],
                "actions": [
                    {
                        "type": "disable_system",
                        "system": "chat",
                        "duration_minutes": 30,
                        "reason": "High cortisol fallback guardrail",
                    }
                ],
            }

        if not isinstance(parsed, dict):
            return

        mood_tags = parsed.get("mood_tags", [])
        if isinstance(mood_tags, list):
            endocrine_runtime.set_mood_tags([str(tag) for tag in mood_tags], now=endocrine_runtime.last_update_ts)

        summary_text = str(parsed.get("summary", "")).strip()
        if summary_text:
            metadata: dict[str, object] = {"doctor_revelation": True, "health_decision": True}
            if provider_name:
                metadata["provider"] = provider_name
            if model_id:
                metadata["model"] = model_id
            metadata["source"] = request_source
            metadata["reason"] = request_reason
            _post_session(doctor_session_id, f"Doctor summary: {summary_text}", extra_metadata=metadata)

        has_active_degradation = False
        try:
            _cfg_path, provider_cfg = _read_providers_config()
            providers_by_name = {provider.name: provider for provider in provider_cfg.providers}
            for system, assignment in provider_cfg.systems.items():
                if not assignment.provider:
                    continue
                provider = providers_by_name.get(assignment.provider)
                if provider is None or not provider.enabled or not _provider_is_valid(provider):
                    has_active_degradation = True
                    break
        except Exception:
            log.exception("Provider degradation scan failed during doctor run")

        actions = parsed.get("actions", [])
        if isinstance(actions, list):
            for action in actions:
                if not isinstance(action, dict):
                    continue
                action_type = str(action.get("type", "")).strip().lower()
                system_name = str(action.get("system", "")).strip().lower()
                reason = str(action.get("reason", "doctor recommendation")).strip()

                if action_type == "adjust":
                    source_name = str(action.get("source", "immune")).strip() or "immune"
                    deltas = action.get("deltas", {})
                    if isinstance(deltas, dict):
                        safe_deltas: dict[str, float] = {}
                        for key, value in deltas.items():
                            if key not in {"dopamine", "adrenaline", "cortisol", "endorphin"}:
                                continue
                            fvalue = float(value)
                            if has_active_degradation and key in {"cortisol", "adrenaline"} and fvalue < 0:
                                continue
                            safe_deltas[key] = fvalue
                        _adjust(source_name, reason, safe_deltas)
                    continue

                if system_name not in {"chat", "tasks", "research"}:
                    continue

                if action_type == "disable_system":
                    duration_minutes = max(5, int(action.get("duration_minutes", 30)))
                    disabled_until = endocrine_runtime.last_update_ts + duration_minutes * 60
                    endocrine_runtime.disable_system(
                        system_name,
                        reason=reason,
                        now=endocrine_runtime.last_update_ts,
                        until=disabled_until,
                    )
                    _create_reenable_task(system_name, reason, disabled_until)
                    _post_session(
                        chat_session_id,
                        f"Doctor action: disabled {system_name} for {duration_minutes} minutes. Reason: {reason}",
                    )
                    _post_session(
                        doctor_session_id,
                        f"Action executed: disable {system_name} until {disabled_until:.0f} ({reason})",
                        extra_metadata={"doctor_revelation": True, "health_decision": True},
                    )

                if action_type == "enable_system":
                    endocrine_runtime.enable_system(system_name, reason=reason, now=endocrine_runtime.last_update_ts)
                    _post_session(chat_session_id, f"Doctor action: enabled {system_name}. Reason: {reason}")
                    _post_session(
                        doctor_session_id,
                        f"Action executed: enable {system_name} ({reason})",
                        extra_metadata={"doctor_revelation": True, "health_decision": True},
                    )

        executed_doctor = summary_text or f"doctor-called:{request_source}"

    _process_endocrine_followups()

    if run_tasks:
        tasks_policy = session_allows(policy, "tasks", "allow_task_autonomy", True)
        tasks_gate = endocrine_runtime.gate("tasks")
        if tasks_policy and tasks_gate.enabled:
            top_task = _top_pending_task()
            if top_task is not None:
                _process_task(top_task)

    if task_request is not None:
        tasks_policy = session_allows(policy, "tasks", "allow_task_autonomy", True)
        tasks_gate = endocrine_runtime.gate("tasks")
        if tasks_policy and tasks_gate.enabled:
            requested_task = _requested_task(task_request)
            if requested_task is not None:
                _process_task(requested_task)

    if run_research:
        research_policy = session_allows(policy, "research", "allow_research_autonomy", True)
        research_gate = endocrine_runtime.gate("research")
        if research_policy and research_gate.enabled:
            _process_research()

    if research_request is not None:
        research_policy = session_allows(policy, "research", "allow_research_autonomy", True)
        research_gate = endocrine_runtime.gate("research")
        if research_policy and research_gate.enabled:
            requested_node = _requested_research(research_request)
            if requested_node is not None:
                _process_research(requested_node)

    doctor_policy = session_allows(policy, "doctor", "allow_endocrine_doctor", True)
    if doctor_request is not None and doctor_policy:
        _process_doctor(doctor_request)

    return {
        "executed_task_id": executed_task_id,
        "executed_research_id": executed_research_id,
        "executed_doctor": executed_doctor,
    }