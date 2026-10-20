from __future__ import annotations

import time

from openbad.wui.usage_tracker import UsageTracker


def test_usage_tracker_snapshot_groups_by_provider_model_and_system(tmp_path) -> None:
    tracker = UsageTracker(
        db_path=tmp_path / "usage.db",
        daily_ceiling=10_000,
        hourly_ceiling=2_000,
    )
    try:
        now = time.time()
        tracker.record(
            provider="openai",
            model="gpt-4o",
            system="chat",
            tokens=300,
            request_id="req-1",
            session_id="sess-1",
            timestamp=now,
        )
        tracker.record(
            provider="openai",
            model="gpt-4o",
            system="reasoning",
            tokens=150,
            request_id="req-2",
            session_id="sess-2",
            timestamp=now,
        )
        tracker.record(
            provider="anthropic",
            model="claude-sonnet",
            system="chat",
            tokens=250,
            request_id="req-3",
            session_id="sess-3",
            timestamp=now,
        )

        snapshot = tracker.snapshot()

        assert snapshot["summary"]["total_used"] == 700
        assert snapshot["summary"]["request_count"] == 3
        assert snapshot["summary"]["daily_used"] == 700
        assert snapshot["summary"]["hourly_used"] == 700
        assert snapshot["summary"]["cost_per_action_avg"] == 233.33
        assert snapshot["by_provider_model"][0]["provider"] == "openai"
        assert snapshot["by_provider_model"][0]["model"] == "gpt-4o"
        assert snapshot["by_provider_model"][0]["tokens"] == 450
        assert snapshot["by_system"][0]["system"] == "chat"
        assert snapshot["by_system"][0]["tokens"] == 550
        assert snapshot["by_session"][0]["session_id"] == "sess-1"
        assert snapshot["by_session"][0]["tokens"] == 300
        assert snapshot["recent_events"][0]["request_id"] == "req-3"
    finally:
        tracker.close()


def test_usage_tracker_persists_across_restarts(tmp_path) -> None:
    db_path = tmp_path / "usage.db"

    tracker = UsageTracker(db_path=db_path)
    tracker.record(
        provider="openai",
        model="gpt-4o-mini",
        system="chat",
        tokens=125,
        request_id="req-a",
        session_id="sess-a",
    )
    tracker.close()

    reopened = UsageTracker(db_path=db_path)
    try:
        snapshot = reopened.snapshot()
        assert snapshot["summary"]["total_used"] == 125
        assert snapshot["summary"]["request_count"] == 1
        assert snapshot["by_provider_model"][0]["model"] == "gpt-4o-mini"
    finally:
        reopened.close()


def test_usage_tracker_excludes_old_records_from_windows(tmp_path) -> None:
    tracker = UsageTracker(db_path=tmp_path / "usage.db")
    try:
        tracker.record(
            provider="openai",
            model="gpt-4o",
            system="chat",
            tokens=900,
            request_id="old",
            session_id="sess-old",
            timestamp=time.time() - 90_000,
        )
        tracker.record(
            provider="openai",
            model="gpt-4o",
            system="chat",
            tokens=100,
            request_id="new",
            session_id="sess-new",
        )

        snapshot = tracker.snapshot()
        assert snapshot["summary"]["total_used"] == 1_000
        assert snapshot["summary"]["daily_used"] == 100
        assert snapshot["summary"]["hourly_used"] == 100
    finally:
        tracker.close()


def test_usage_tracker_counts_zero_token_records(tmp_path) -> None:
    tracker = UsageTracker(db_path=tmp_path / "usage.db")
    try:
        tracker.record(
            provider="custom",
            model="health-check",
            system="chat",
            tokens=0,
            request_id="health-1",
            session_id="chat-main",
        )

        snapshot = tracker.snapshot()
        assert snapshot["summary"]["total_used"] == 0
        assert snapshot["summary"]["request_count"] == 1
        assert snapshot["recent_events"][0]["request_id"] == "health-1"
    finally:
        tracker.close()