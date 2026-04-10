"""Phase 4 E2E integration tests — memory and sleep consolidation.

All tests marked ``@pytest.mark.integration`` and use only in-process
components (mocked MQTT, no external APIs).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from openbad.cognitive.config import CognitiveSystem
from openbad.cognitive.context_manager import (
    CompressedContext,
    CompressionStrategy,
    ContextBudget,
    ContextWindowManager,
)
from openbad.cognitive.event_loop import CognitiveEventLoop
from openbad.cognitive.model_router import FallbackChain, ModelRouter, RouteStep
from openbad.cognitive.providers.base import (
    CompletionResult,
    HealthStatus,
    ProviderAdapter,
)
from openbad.cognitive.providers.registry import ProviderRegistry
from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.memory.config import MemoryConfig
from openbad.memory.controller import MemoryController
from openbad.memory.forgetting import prune_store, retention_score
from openbad.memory.procedural import Skill
from openbad.memory.sleep.orchestrator import (
    MemoryPruner,
    SleepOrchestrator,
)
from openbad.memory.stm import ShortTermMemory

integration = pytest.mark.integration


def _mc(tmp_path: Path, **kwargs: object) -> MemoryController:
    return MemoryController(config=MemoryConfig(ltm_storage_dir=tmp_path, **kwargs))


def _sleep_event_loop() -> CognitiveEventLoop:
    registry = ProviderRegistry()
    adapter = AsyncMock(spec=ProviderAdapter)
    adapter.complete = AsyncMock(
        side_effect=[
            CompletionResult(
                content="failure summary",
                model_id="bonsai-8b",
                provider="ollama",
                tokens_used=10,
            ),
            CompletionResult(
                content="ops, error",
                model_id="bonsai-8b",
                provider="ollama",
                tokens_used=10,
            ),
            CompletionResult(
                content="0.9",
                model_id="bonsai-8b",
                provider="ollama",
                tokens_used=5,
            ),
            CompletionResult(
                content="success summary",
                model_id="bonsai-8b",
                provider="ollama",
                tokens_used=10,
            ),
            CompletionResult(
                content="ops, build",
                model_id="bonsai-8b",
                provider="ollama",
                tokens_used=10,
            ),
            CompletionResult(
                content="0.7",
                model_id="bonsai-8b",
                provider="ollama",
                tokens_used=5,
            ),
        ]
    )
    adapter.health_check = AsyncMock(
        return_value=HealthStatus(provider="ollama", available=True, latency_ms=5)
    )
    registry.register("ollama", adapter)
    router = ModelRouter(
        registry=registry,
        system_assignments={CognitiveSystem.SLEEP: RouteStep("ollama", "bonsai-8b")},
        default_fallback_chain=FallbackChain(steps=(RouteStep("ollama", "bonsai-8b"),)),
    )
    ctx = MagicMock(spec=ContextWindowManager)
    ctx.allocate = MagicMock(
        return_value=ContextBudget(
            max_tokens=8192,
            system_tokens=1000,
            context_tokens=4000,
            response_tokens=2000,
        )
    )
    ctx.compress = MagicMock(
        return_value=CompressedContext(
            text="compressed",
            original_tokens=10,
            compressed_tokens=10,
            strategy=CompressionStrategy.TRUNCATE,
        )
    )
    ctx.track_usage = MagicMock()
    return CognitiveEventLoop(model_router=router, context_manager=ctx, strategies={})


# ------------------------------------------------------------------ #
# 1. STM write → overflow → eviction
# ------------------------------------------------------------------ #


@integration
class TestStmOverflowEviction:
    def test_evicts_oldest_on_overflow(self) -> None:
        # Each "word " * 20 ≈ 27 tokens; budget of 30 allows only one
        stm = ShortTermMemory(max_tokens=30, default_ttl=None)
        now = time.time()
        stm.write(MemoryEntry(
            key="a", value="word " * 20, tier=MemoryTier.STM, created_at=now,
        ))
        stm.write(MemoryEntry(
            key="b", value="word " * 20, tier=MemoryTier.STM, created_at=now + 1,
        ))
        # 'a' should be evicted to make room for 'b'
        assert stm.read("a") is None
        assert stm.read("b") is not None

    def test_multiple_evictions(self) -> None:
        stm = ShortTermMemory(max_tokens=15, default_ttl=None)
        now = time.time()
        for i in range(5):
            stm.write(MemoryEntry(
                key=f"k{i}", value="fill " * 4, tier=MemoryTier.STM,
                created_at=now + i,
            ))
        # Only the most recent entry should survive
        assert stm.size() >= 1
        assert stm.read("k4") is not None


# ------------------------------------------------------------------ #
# 2. STM → episodic promotion
# ------------------------------------------------------------------ #


@integration
class TestStmEpisodicPromotion:
    def test_promote_persists(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        mc.write_stm("task/1", "completed step A")
        mc.promote_to_episodic("task/1")

        # STM should be empty
        assert mc.stm.read("task/1") is None

        # Episodic should have it
        r = mc.episodic.read("task/1")
        assert r is not None
        assert r.value == "completed step A"
        assert r.metadata.get("promoted_from") == "stm"

    def test_promote_survives_reload(self, tmp_path: Path) -> None:
        mc1 = _mc(tmp_path)
        mc1.write_episodic("e1", "persistent data")
        mc1.episodic.save()

        # Simulate restart: new controller, same storage
        mc2 = _mc(tmp_path)
        r = mc2.episodic.read("e1")
        assert r is not None
        assert r.value == "persistent data"


# ------------------------------------------------------------------ #
# 3. Semantic search E2E
# ------------------------------------------------------------------ #


def _keyword_embed(text: str) -> list[float]:
    """Simple bag-of-words embed for deterministic ranking tests."""
    words = {"python", "java", "programming", "coding", "bake", "cake"}
    tokens = text.lower().split()
    return [float(tokens.count(w)) for w in sorted(words)]


@integration
class TestSemanticSearchE2e:
    def test_similarity_ranking(self, tmp_path: Path) -> None:
        cfg = MemoryConfig(ltm_storage_dir=tmp_path)
        from openbad.memory.semantic import SemanticMemory
        sem = SemanticMemory(
            storage_path=cfg.ltm_storage_dir / "semantic.json",
            embed_fn=_keyword_embed,
            similarity_threshold=0.0,
        )
        sem.write(MemoryEntry(
            key="python", value="Python programming language",
            tier=MemoryTier.SEMANTIC,
        ))
        sem.write(MemoryEntry(
            key="java", value="Java programming language",
            tier=MemoryTier.SEMANTIC,
        ))
        sem.write(MemoryEntry(
            key="cooking", value="How to bake a chocolate cake",
            tier=MemoryTier.SEMANTIC,
        ))

        results = sem.search("Python coding", top_k=3)
        assert len(results) >= 1
        keys = [entry.key for entry, _score in results]
        # "Python coding" should be closer to "Python programming" than "bake cake"
        assert keys[0] == "python"


# ------------------------------------------------------------------ #
# 4. Procedural skill lifecycle
# ------------------------------------------------------------------ #


@integration
class TestProceduralLifecycle:
    def test_skill_confidence_update(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        skill = Skill(
            name="deploy", description="Deploy to prod",
            capabilities=["deploy", "release"],
        )
        mc.write_procedural("deploy", skill)

        # Record successes
        for _ in range(5):
            mc.procedural.record_outcome("deploy", success=True)

        s = mc.procedural.get_skill("deploy")
        assert s is not None
        assert s.confidence > 0.7
        assert s.success_count == 5

    def test_top_skills_ranking(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        mc.write_procedural("a", Skill(name="a", description="d", confidence=0.9))
        mc.write_procedural("b", Skill(name="b", description="d", confidence=0.3))
        mc.write_procedural("c", Skill(name="c", description="d", confidence=0.7))

        top = mc.procedural.top_skills(n=2)
        assert len(top) == 2
        assert top[0][0] == "a"
        assert top[1][0] == "c"


# ------------------------------------------------------------------ #
# 5. Full sleep cycle
# ------------------------------------------------------------------ #


@integration
class TestFullSleepCycle:
    async def test_sleep_consolidates_all(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)

        # Populate STM with failures
        mc.stm.write(MemoryEntry(
            key="f1", value="error: disk full", tier=MemoryTier.STM,
            metadata={"status": "error", "action": "write_log"},
        ))
        mc.stm.write(MemoryEntry(
            key="f2", value="error: timeout", tier=MemoryTier.STM,
            metadata={"status": "failure", "action": "api_call"},
        ))

        # Populate STM with successes
        mc.stm.write(MemoryEntry(
            key="s1", value="ok", tier=MemoryTier.STM,
            metadata={"status": "success", "action": "deploy"},
        ))
        mc.stm.write(MemoryEntry(
            key="s2", value="ok", tier=MemoryTier.STM,
            metadata={"status": "success", "action": "deploy"},
        ))

        # Add old episodic entries for pruning
        mc.episodic.write(MemoryEntry(
            key="ancient", value="old data", tier=MemoryTier.EPISODIC,
            created_at=time.time() - 100000 * 3600,
        ))

        pruner = MemoryPruner(memory_controller=mc)
        orch = SleepOrchestrator(
            memory_controller=mc,
            cognitive_event_loop=_sleep_event_loop(),
            pruner=pruner,
        )

        report = await orch.run_cycle()

        # Verify semantic consolidation entries
        assert report.entries_consolidated >= 2
        consolidated = mc.semantic.query("sleep/")
        assert len(consolidated) >= 2

        # Verify pruned entries
        assert report.entries_pruned >= 1


# ------------------------------------------------------------------ #
# 6. Forgetting curve decay
# ------------------------------------------------------------------ #


@integration
class TestForgettingCurveDecay:
    def test_old_entries_decay_and_prune(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        now = time.time()

        # Fresh entry
        mc.episodic.write(MemoryEntry(
            key="fresh", value="recent", tier=MemoryTier.EPISODIC,
            created_at=now,
        ))
        # Old entry
        mc.episodic.write(MemoryEntry(
            key="old", value="ancient", tier=MemoryTier.EPISODIC,
            created_at=now - 10000 * 3600,
        ))
        # Frequently accessed old entry
        mc.episodic.write(MemoryEntry(
            key="popular", value="used often", tier=MemoryTier.EPISODIC,
            created_at=now - 500 * 3600, access_count=50,
            accessed_at=now - 24 * 3600,
        ))

        pruned = prune_store(mc.episodic, threshold=0.1, now=now)
        assert "old" in pruned
        assert "fresh" not in pruned
        assert "popular" not in pruned

    def test_retention_score_consistency(self) -> None:
        now = time.time()
        e1 = MemoryEntry(
            key="a", value="v", tier=MemoryTier.STM,
            created_at=now, access_count=0,
        )
        e2 = MemoryEntry(
            key="b", value="v", tier=MemoryTier.STM,
            created_at=now - 168 * 3600, access_count=10,
        )
        s1 = retention_score(e1, now=now)
        s2 = retention_score(e2, now=now)
        # Fresh entry should have higher retention
        assert s1 > s2


# ------------------------------------------------------------------ #
# 7. Memory controller MQTT handler
# ------------------------------------------------------------------ #


@integration
class TestControllerMqtt:
    def test_stm_write_publishes(self, tmp_path: Path) -> None:
        published: list[tuple[str, bytes]] = []
        mc = MemoryController(
            config=MemoryConfig(ltm_storage_dir=tmp_path),
            publish_fn=lambda t, p: published.append((t, p)),
        )
        mc.write_stm("k", "v")
        assert len(published) == 1
        assert published[0][0] == "agent/memory/stm/write"

    def test_multiple_writes_publish_all(self, tmp_path: Path) -> None:
        published: list[tuple[str, bytes]] = []
        mc = MemoryController(
            config=MemoryConfig(ltm_storage_dir=tmp_path),
            publish_fn=lambda t, p: published.append((t, p)),
        )
        for i in range(5):
            mc.write_stm(f"k{i}", f"v{i}")
        assert len(published) == 5


# ------------------------------------------------------------------ #
# 8. Idle detection → auto sleep
# ------------------------------------------------------------------ #


@integration
class TestIdleAutoSleep:
    async def test_idle_triggers_cycle(self, tmp_path: Path) -> None:
        mc = _mc(tmp_path)
        mc.stm.write(MemoryEntry(
            key="f1", value="error: oops", tier=MemoryTier.STM,
            metadata={"status": "error"},
        ))

        pruner = MemoryPruner(memory_controller=mc)
        phases: list[str] = []
        orch = SleepOrchestrator(
            memory_controller=mc,
            cognitive_event_loop=_sleep_event_loop(),
            pruner=pruner,
            idle_threshold_seconds=0.01,
            publish_fn=lambda t, p: phases.append(p.decode()),
        )

        # Simulate idle for long enough
        async def run_bg() -> None:
            await orch.start_background(
                get_last_activity=lambda: time.time() - 100,
                check_interval=0.05,
            )

        task = asyncio.create_task(run_bg())
        await asyncio.sleep(0.2)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        # Should have gone through at least one full cycle
        assert "sws" in phases
        assert "rem" in phases
        assert "complete" in phases
