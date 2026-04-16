from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from openbad.autonomy.endocrine_runtime import EndocrineRuntime, load_endocrine_config
from openbad.autonomy.session_policy import (
    is_destructive_request,
    load_session_policy,
    session_allows,
    session_id_for,
)
from openbad.autonomy.tool_agent import build_tooling_system_prompt, run_tool_agent
from openbad.state.db import DEFAULT_STATE_DB_PATH, initialize_state_db
from openbad.state.event_log import recent_events
from openbad.tasks.models import TaskKind, TaskModel, TaskPriority, TaskStatus
from openbad.tasks.reward_endocrine import RewardEndocrineBridge, initialize_reward_db
from openbad.tasks.reward_evaluator import RewardEvaluator
from openbad.tasks.reward_models import RewardTrace, TraceOutcome
from openbad.tasks.research_queue import ResearchQueue, initialize_research_db
from openbad.tasks.store import TaskStore
from openbad.wui.chat_pipeline import append_assistant_message, append_session_message
from openbad.wui.server import _provider_is_valid, _read_providers_config, _resolve_chat_adapter
from openbad.wui.usage_tracker import UsageTracker, resolve_usage_db_path

log = logging.getLogger(__name__)

_AUTONOMY_INTERACTIVE_PATTERNS = (
    re.compile(r"(^|\n)(?:#+\s*)?(?:actionable\s+)?next\s+steps\s*:?.*", re.IGNORECASE),
    re.compile(r"\bwould you like\b", re.IGNORECASE),
    re.compile(r"\blet me know if you'd like\b", re.IGNORECASE),
    re.compile(r"\blet me know if you want\b", re.IGNORECASE),
    re.compile(r"\bif you need further assistance\b", re.IGNORECASE),
    re.compile(r"\bdo you want me to\b", re.IGNORECASE),
)


def _normalize_research_field(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def _build_research_tool_validator(node) -> Callable[[str, dict[str, Any]], str | None]:
    current_title = _normalize_research_field(getattr(node, "title", ""))
    current_description = _normalize_research_field(getattr(node, "description", ""))

    def _validator(tool_name: str, tool_args: dict[str, Any]) -> str | None:
        if tool_name != "create_research_node":
            return None
        requested_title = _normalize_research_field(str(tool_args.get("title", "")))
        requested_description = _normalize_research_field(str(tool_args.get("description", "")))
        if requested_title == current_title and requested_description == current_description:
            return (
                "Blocked tool call: refusing to create a duplicate research node with the same"
                " title and description as the node currently being processed."
            )
        return None

    return _validator


def _strip_autonomy_interaction(text: str) -> str:
    paragraphs = [chunk.strip() for chunk in re.split(r"\n\s*\n", (text or "").strip()) if chunk.strip()]
    kept: list[str] = []
    for paragraph in paragraphs:
        if any(pattern.search(paragraph) for pattern in _AUTONOMY_INTERACTIVE_PATTERNS):
            continue
        kept.append(paragraph)
    return "\n\n".join(kept).strip()


def _parse_event_timestamp(raw: object) -> float | None:
    text = str(raw or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


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
    initialize_reward_db(conn)

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
    reward_evaluator = RewardEvaluator()

    class _BufferedRewardController:
        def __init__(self) -> None:
            self._deltas: dict[str, float] = {}

        def trigger(self, hormone: str, amount: float | None = None) -> float:
            delta = float(amount or 0.0)
            self._deltas[hormone] = self._deltas.get(hormone, 0.0) + delta
            return self._deltas[hormone]

        def consume(self) -> dict[str, float]:
            deltas = dict(self._deltas)
            self._deltas.clear()
            return deltas

    reward_controller = _BufferedRewardController()
    reward_bridge = RewardEndocrineBridge(conn, reward_controller)

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
        cutoff = max(0.0, time.time() - max(1, int(window_seconds)))
        row = conn.execute(
            """
            SELECT 1
            FROM endocrine_adjustments
            WHERE source = ?
              AND reason = ?
              AND ts >= ?
            LIMIT 1
            """,
            (source, reason, cutoff),
        ).fetchone()
        return row is not None

    def _apply_reward(
        *,
        outcome: TraceOutcome,
        node_id: str,
        task_id: str,
        source: str,
        reason: str,
        context: dict[str, object] | None = None,
    ) -> None:
        trace = RewardTrace(
            node_id=node_id,
            task_id=task_id,
            outcome=outcome,
            duration_ms=0,
            retry_count=0,
            context=dict(context or {}),
        )
        result = reward_evaluator.evaluate(trace)
        reward_bridge.apply(trace, result)
        deltas = reward_controller.consume()
        if deltas:
            _adjust(source, reason, deltas)

    def _summarize_log_health(*, lookback_seconds: int = 900) -> dict[str, object] | None:
        cutoff = time.time() - max(60, int(lookback_seconds))
        warning_events = recent_events(limit=80, level="WARNING")
        error_events = recent_events(limit=80, level="ERROR")
        critical_events = recent_events(limit=40, level="CRITICAL")

        def _filter(events: list[dict[str, object]]) -> list[dict[str, object]]:
            filtered: list[dict[str, object]] = []
            for event in events:
                event_ts = _parse_event_timestamp(event.get("ts"))
                if event_ts is None or event_ts < cutoff:
                    continue
                source_name = str(event.get("source", "")).strip().lower()
                if not source_name.startswith("openbad"):
                    continue
                filtered.append(event)
            return filtered

        warnings = _filter(warning_events)
        errors = _filter(error_events)
        criticals = _filter(critical_events)
        if not warnings and not errors and not criticals:
            return None

        sources = sorted(
            {
                str(event.get("source", "")).strip()
                for event in [*warnings, *errors, *criticals]
                if str(event.get("source", "")).strip()
            }
        )
        recent_messages = [
            {
                "level": str(event.get("level", "")),
                "source": str(event.get("source", "")),
                "message": str(event.get("message", "")),
            }
            for event in [*criticals[:3], *errors[:5], *warnings[:5]]
        ]
        return {
            "warning_count": len(warnings),
            "error_count": len(errors),
            "critical_count": len(criticals),
            "sources": sources,
            "recent_messages": recent_messages,
        }

    def _process_log_health() -> None:
        summary = _summarize_log_health()
        if summary is None:
            return

        warning_count = int(summary.get("warning_count", 0))
        error_count = int(summary.get("error_count", 0))
        critical_count = int(summary.get("critical_count", 0))

        if error_count or critical_count:
            reason = "Observed runtime error accumulation in persistent event log"
            if not _has_recent_adjustment("log-health", reason, window_seconds=600):
                _adjust(
                    "log-health",
                    reason,
                    {
                        "cortisol": min(0.04 * error_count + 0.08 * critical_count, 0.25),
                        "adrenaline": min(0.02 * error_count + 0.05 * critical_count, 0.16),
                    },
                )
            doctor_policy = session_allows(policy, "doctor", "allow_endocrine_doctor", True)
            if doctor_policy:
                _process_doctor(
                    {
                        "source": "log-health",
                        "reason": reason,
                        "context": summary,
                    }
                )
            return

        reason = "Observed runtime warning accumulation in persistent event log"
        if not _has_recent_adjustment("log-health", reason, window_seconds=900):
            _adjust(
                "log-health",
                reason,
                {"cortisol": min(0.01 * warning_count, 0.08)},
            )

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
        system_prompt: str,
        user_prompt: str,
        *,
        system_name: str,
        session_id: str,
        request_id: str,
        tool_call_validator: Callable[[str, dict[str, Any]], str | None] | None = None,
    ) -> tuple[str, str, str, tuple[str, ...]] | None:
        try:
            _cfg_path, cfg = _read_providers_config()
            adapter, model, provider_name, _is_fallback = _resolve_chat_adapter(cfg, system_name)
            if adapter is None or model is None:
                return None
            result = asyncio.run(
                run_tool_agent(
                    adapter,
                    model,
                    provider_name=provider_name,
                    system_prompt=build_tooling_system_prompt(system_prompt),
                    user_prompt=user_prompt,
                    request_id=request_id,
                    tool_call_validator=tool_call_validator,
                )
            )
            usage_tracker.record(
                provider=result.provider or provider_name or "unknown",
                model=result.model or model,
                system=system_name,
                tokens=int(result.tokens_used),
                request_id=request_id,
                session_id=session_id,
            )
            return result.content.strip(), result.provider or provider_name or "", result.model or model, result.tools_used
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
            followup_now = time.time()
            endocrine_runtime.enable_system(
                system_name,
                reason="Follow-up re-enable",
                now=followup_now,
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
            system_prompt=(
                "You are the autonomous OpenBaD task worker. "
                "Complete the assigned task in a non-destructive way. Use tools whenever"
                " they improve execution or diagnosis. There is no interactive human in this"
                " session. Do not ask the operator questions, do not ask what to do next, and"
                " do not end with invitations for follow-up. When the task reveals additional"
                " concrete follow-up work, create task or research entries directly via tools"
                " instead of only describing them. Return a concise execution summary of what"
                " you completed and what follow-up entries you created."
            ),
            user_prompt=(
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
            _apply_reward(
                outcome=TraceOutcome.FAILURE,
                node_id=task_to_run.task_id,
                task_id=task_to_run.task_id,
                source="tasks",
                reason=f"Reward evaluation for failed task {task_to_run.task_id}",
                context={"system": "tasks", "owner": task_to_run.owner, "failure": "provider_unavailable"},
            )
            return

        summary, provider_name, model_id, tools_used = llm
        summary = _strip_autonomy_interaction(summary)
        task_store.update_task_status(task_to_run.task_id, TaskStatus.DONE)
        task_store.append_event(
            task_to_run.task_id,
            "autonomy_completed",
            payload={"summary": summary, "provider": provider_name, "model": model_id},
        )
        meta = {"provider": provider_name, "model": model_id, "tools_used": list(tools_used)}
        tools_suffix = f"\n\n*Tools used: {', '.join(tools_used)}*" if tools_used else ""
        _post_session(task_session_id, f"Completed task '{task_to_run.title}': {summary}{tools_suffix}", extra_metadata=meta)
        _post_session(chat_session_id, f"Autonomous task update: completed '{task_to_run.title}'.", extra_metadata=meta)
        _apply_reward(
            outcome=TraceOutcome.SUCCESS,
            node_id=task_to_run.task_id,
            task_id=task_to_run.task_id,
            source="tasks",
            reason=f"Reward evaluation for completed task {task_to_run.task_id}",
            context={"system": "tasks", "owner": task_to_run.owner, "tools_used": list(tools_used)},
        )
        executed_task_id = task_to_run.task_id

    def _process_research(node=None) -> None:
        nonlocal executed_research_id
        queue = ResearchQueue(conn)
        requested_node = node is not None
        if node is None:
            node = queue.dequeue()
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
            system_prompt=(
                "You are the OpenBaD research worker. "
                "Investigate the topic thoroughly using your tools. Read relevant files, "
                "inspect logs, and gather concrete evidence before reaching conclusions. "
                "Do NOT narrate what you intend to do — call the tools directly. "
                "For example, do not say 'I will now read the file'; instead, call read_file immediately. "
                "If the findings imply"
                " more concrete work, create follow-up task or research entries directly via"
                " tools. Never create a follow-up research node that duplicates the current"
                " research title and description. There is no interactive human in this"
                " session. Do not ask questions, do not ask what should be worked on next,"
                " and do not emit recommendation lists for a human to choose from. If more"
                " work is warranted, create follow-up task or research entries directly and"
                " report what you created. Return a concise research result and concrete"
                " findings only."
            ),
            user_prompt=(
                f"Research title: {node.title}\nDescription: {node.description}"
            ),
            system_name="research",
            session_id=research_session_id,
            request_id=node.node_id,
            tool_call_validator=_build_research_tool_validator(node),
        )
        if llm is None:
            if requested_node:
                log.warning("Research node %s failed before completion; leaving pending for retry", node.node_id)
            _post_session(research_session_id, f"Research node {node.node_id} dequeued but provider was unavailable.")
            _apply_reward(
                outcome=TraceOutcome.FAILURE,
                node_id=node.node_id,
                task_id=node.source_task_id or f"research:{node.node_id}",
                source="research",
                reason=f"Reward evaluation for failed research node {node.node_id}",
                context={"system": "research", "source_task_id": node.source_task_id or "", "failure": "provider_unavailable"},
            )
            return

        summary, provider_name, model_id, tools_used = llm
        summary = _strip_autonomy_interaction(summary)
        if requested_node:
            queue.complete(node.node_id)
        meta = {"provider": provider_name, "model": model_id, "tools_used": list(tools_used)}
        tools_suffix = f"\n\n*Tools used: {', '.join(tools_used)}*" if tools_used else ""
        _post_session(
            research_session_id,
            f"**Research complete: {node.title}**\n\n{summary}\n\n*Provider: {provider_name} / {model_id}*{tools_suffix}",
            extra_metadata=meta,
        )
        _post_session(chat_session_id, f"Autonomous research update: completed '{node.title}'.", extra_metadata=meta)
        _apply_reward(
            outcome=TraceOutcome.SUCCESS,
            node_id=node.node_id,
            task_id=node.source_task_id or f"research:{node.node_id}",
            source="research",
            reason=f"Reward evaluation for completed research node {node.node_id}",
            context={"system": "research", "source_task_id": node.source_task_id or "", "tools_used": list(tools_used)},
        )
        executed_research_id = node.node_id

    def _process_doctor(request: dict[str, object] | None) -> None:
        nonlocal executed_doctor
        doctor_now = time.time()
        levels = endocrine_runtime.levels
        source_recent = endocrine_runtime.source_contributions(window_seconds=900, now=doctor_now)
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
            "Use tools when you need better evidence before deciding. "
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
            system_prompt=(
                "You are the OpenBaD endocrine doctor. "
                "Use available tools to inspect state, logs, tasks, research, and endocrine"
                " signals before making decisions when that improves confidence. Final output"
                " must be strict JSON that matches the requested schema exactly."
            ),
            user_prompt=doctor_prompt,
            system_name="doctor",
            session_id=doctor_session_id,
            request_id=f"doctor-{int(doctor_now)}",
        )
        parsed: dict[str, object] | None = None
        provider_name = ""
        model_id = ""
        tools_used: tuple[str, ...] = ()
        if llm is not None:
            summary, provider_name, model_id, tools_used = llm
            parsed = _parse_doctor_payload(summary)
            endocrine_runtime.add_doctor_note(
                {
                    "source": "llm",
                    "provider": provider_name,
                    "model": model_id,
                    "summary": str(parsed.get("summary", "")).strip() if isinstance(parsed, dict) else "",
                    "raw": summary,
                },
                now=doctor_now,
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
            endocrine_runtime.set_mood_tags([str(tag) for tag in mood_tags], now=doctor_now)

        summary_text = str(parsed.get("summary", "")).strip()
        if summary_text:
            metadata: dict[str, object] = {"doctor_revelation": True, "health_decision": True}
            if provider_name:
                metadata["provider"] = provider_name
            if model_id:
                metadata["model"] = model_id
            if tools_used:
                metadata["tools_used"] = list(tools_used)
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
                    disabled_until = doctor_now + duration_minutes * 60
                    endocrine_runtime.disable_system(
                        system_name,
                        reason=reason,
                        now=doctor_now,
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
                    endocrine_runtime.enable_system(system_name, reason=reason, now=doctor_now)
                    _post_session(chat_session_id, f"Doctor action: enabled {system_name}. Reason: {reason}")
                    _post_session(
                        doctor_session_id,
                        f"Action executed: enable {system_name} ({reason})",
                        extra_metadata={"doctor_revelation": True, "health_decision": True},
                    )

        executed_doctor = summary_text or f"doctor-called:{request_source}"

    _process_endocrine_followups()

    if doctor_request is None:
        _process_log_health()

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