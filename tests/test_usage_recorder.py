from __future__ import annotations

from openbad.cognitive.providers.base import CompletionResult, HealthStatus, ModelInfo, ProviderAdapter
from openbad.usage_recorder import UsageTrackingProviderAdapter
from openbad.wui.usage_tracker import UsageTracker


class _FakeProvider(ProviderAdapter):
    async def complete(self, prompt: str, model_id: str | None = None, **kwargs):
        return CompletionResult(
            content="ok",
            model_id=model_id or "fake-model",
            provider="fake",
            tokens_used=0,
        )

    async def stream(self, prompt: str, model_id: str | None = None, **kwargs):
        if False:
            yield ""

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(model_id="fake-model", provider="fake")]

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            provider="fake",
            available=True,
            latency_ms=12.0,
            models_available=1,
            tokens_used=1,
        )


async def test_usage_tracking_provider_adapter_records_probe_activity(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "usage.db"
    monkeypatch.setenv("OPENBAD_USAGE_DB", str(db_path))

    adapter = UsageTrackingProviderAdapter(_FakeProvider(), system="chat")

    status = await adapter.health_check()
    models = await adapter.list_models()

    tracker = UsageTracker(db_path=db_path)
    try:
        snapshot = tracker.snapshot()
        assert status.tokens_used == 1
        assert len(models) == 1
        assert snapshot["summary"]["request_count"] == 2
        assert snapshot["summary"]["total_used"] == 1
        assert {event["request_id"].split(":", 1)[0] for event in snapshot["recent_events"]} == {
            "health-check",
            "list-models",
        }
    finally:
        tracker.close()