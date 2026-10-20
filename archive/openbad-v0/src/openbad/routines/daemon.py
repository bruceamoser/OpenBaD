"""Routine runner daemon — polls for due routines and executes them via agent."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, UTC
from pathlib import Path

from openbad.routines import Routine, RoutineRun, RunStatus
from openbad.routines.store import RoutineStore
from openbad.state.db import initialize_state_db
from openbad.tasks.recurrence import compute_next_due

log = logging.getLogger(__name__)

_DEFAULT_POLL_INTERVAL = 30  # seconds


async def _execute_routine(routine: Routine, store: RoutineStore) -> None:
    """Run a single routine through the agent with full tool access."""
    run = RoutineRun.start(routine.routine_id)
    store.create_run(run)

    log.info(
        "Routine %s (%s) executing run=%s",
        routine.routine_id, routine.name, run.run_id,
    )

    try:
        from openbad.autonomy.tool_agent import build_tooling_system_prompt, run_tool_agent
        from openbad.wui.server import _read_providers_config, _resolve_chat_adapter

        _cfg_path, cfg = _read_providers_config()
        resolved = _resolve_chat_adapter(cfg, "task")
        _adapter, model, provider_name, _fb, chat_model, _cl = resolved

        if chat_model is None or model is None:
            raise RuntimeError("No LLM provider available for routine execution")

        system_prompt = build_tooling_system_prompt(
            "You are an autonomous agent executing a scheduled routine. "
            "Follow the instructions below precisely. Use your tools to accomplish "
            "every step. Report what you did at the end."
        )

        user_prompt = (
            f"# Routine: {routine.name}\n\n"
            f"{routine.body_md}"
        )

        result = await run_tool_agent(
            chat_model,
            model,
            provider_name=provider_name or "unknown",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            request_id=f"routine-{run.run_id}",
            tools_role="task",
        )

        store.finish_run(
            run.run_id,
            status=RunStatus.DONE,
            output=result.content.strip()[:10000],
            tokens_used=int(result.tokens_used),
        )
        log.info(
            "Routine %s run=%s completed, tokens=%d",
            routine.routine_id, run.run_id, result.tokens_used,
        )

        # Publish event via MQTT if available
        try:
            from openbad.nervous_system.client import get_global_client
            ns = get_global_client()
            if ns:
                ns.publish(
                    "agent/routine/completed",
                    {
                        "routine_id": routine.routine_id,
                        "run_id": run.run_id,
                        "name": routine.name,
                    },
                )
        except Exception:
            pass

    except Exception as exc:
        log.exception("Routine %s run=%s failed", routine.routine_id, run.run_id)
        store.finish_run(
            run.run_id,
            status=RunStatus.FAILED,
            error=str(exc)[:5000],
        )


def _advance_next_run(routine: Routine, store: RoutineStore) -> None:
    """Compute and set the next run time based on recurrence rule."""
    if not routine.recurrence_rule:
        # One-shot routine — disable after running
        store.update(routine.routine_id, next_run_at=None, enabled=False)
        return

    try:
        next_ts = compute_next_due(
            routine.recurrence_rule,
            after=datetime.fromtimestamp(time.time(), tz=UTC),
        )
        store.update(routine.routine_id, next_run_at=next_ts)
        log.debug(
            "Routine %s next run at %s",
            routine.routine_id,
            datetime.fromtimestamp(next_ts, tz=UTC).isoformat(),
        )
    except Exception:
        log.exception("Failed to compute next run for routine %s", routine.routine_id)
        store.update(routine.routine_id, enabled=False)


async def poll_and_run(
    *,
    poll_interval: float = _DEFAULT_POLL_INTERVAL,
    db_path: str | Path | None = None,
) -> None:
    """Main daemon loop — poll for due routines and execute them.

    Runs forever. Designed to be launched as an asyncio task from
    the main OpenBaD daemon.
    """
    conn = initialize_state_db(db_path or "")
    store = RoutineStore(conn)
    log.info("Routine daemon started, poll_interval=%.0fs", poll_interval)

    while True:
        try:
            due = store.due_routines()
            for routine in due:
                # Advance schedule BEFORE executing so overlapping polls
                # don't re-fire the same routine
                _advance_next_run(routine, store)
                await _execute_routine(routine, store)
        except Exception:
            log.exception("Routine poll error")

        await asyncio.sleep(poll_interval)


def start_routine_daemon(
    *,
    poll_interval: float = _DEFAULT_POLL_INTERVAL,
    db_path: str | Path | None = None,
) -> asyncio.Task:
    """Start the routine daemon as a background asyncio task.

    Returns the Task so callers can cancel it on shutdown.
    """
    return asyncio.ensure_future(
        poll_and_run(poll_interval=poll_interval, db_path=db_path)
    )
