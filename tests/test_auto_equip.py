"""Tests for auto-equip fallback with cortisol escalation — Issue #234."""

from __future__ import annotations

import json

from openbad.proprioception.registry import (
    ToolRegistry,
    ToolRole,
)


def _make_registry(
    **kwargs: object,
) -> tuple[ToolRegistry, list[tuple[str, bytes]], list[tuple[str, str, float]]]:
    published: list[tuple[str, bytes]] = []
    cortisol_calls: list[tuple[str, str, float]] = []

    def pub(topic: str, payload: bytes) -> None:
        published.append((topic, payload))

    def cortisol(name: str, reason: str, delta: float) -> None:
        cortisol_calls.append((name, reason, delta))

    reg = ToolRegistry(publish_fn=pub, **kwargs)
    reg.set_cortisol_hook(cortisol)
    return reg, published, cortisol_calls


class TestFailureSwap:
    def test_swap_to_next_healthy(self) -> None:
        reg, published, cortisol_calls = _make_registry()
        reg.register("tool-a", role=ToolRole.CLI)
        reg.register("tool-b", role=ToolRole.CLI)
        reg.equip(ToolRole.CLI, "tool-a")

        replacement = reg.handle_tool_failure("tool-a", "timeout")
        assert replacement == "tool-b"
        assert reg.belt[ToolRole.CLI].name == "tool-b"

    def test_cortisol_on_swap(self) -> None:
        reg, published, cortisol_calls = _make_registry(swap_cortisol_increment=0.2)
        reg.register("tool-a", role=ToolRole.CLI)
        reg.register("tool-b", role=ToolRole.CLI)
        reg.equip(ToolRole.CLI, "tool-a")

        reg.handle_tool_failure("tool-a", "error")
        assert len(cortisol_calls) == 1
        assert cortisol_calls[0][2] == 0.2

    def test_swap_event_published(self) -> None:
        reg, published, _ = _make_registry()
        reg.register("tool-a", role=ToolRole.CLI)
        reg.register("tool-b", role=ToolRole.CLI)
        reg.equip(ToolRole.CLI, "tool-a")

        reg.handle_tool_failure("tool-a", "timeout")
        toolbelt_events = [
            json.loads(p) for t, p in published if "toolbelt" in t
        ]
        assert any(e["event"] == "swap" for e in toolbelt_events)
        swap_event = next(e for e in toolbelt_events if e["event"] == "swap")
        assert swap_event["old_tool"] == "tool-a"
        assert swap_event["new_tool"] == "tool-b"
        assert swap_event["role"] == "CLI"

    def test_skips_unavailable_candidates(self) -> None:
        reg, _, _ = _make_registry()
        reg.register("a", role=ToolRole.CLI)
        reg.register("b", role=ToolRole.CLI)
        reg.register("c", role=ToolRole.CLI)
        reg.equip(ToolRole.CLI, "a")
        reg.mark_degraded("b", "broken")

        replacement = reg.handle_tool_failure("a", "fail")
        assert replacement == "c"


class TestNoFallbackSpike:
    def test_empty_role_no_healthy_fallback(self) -> None:
        reg, _, cortisol_calls = _make_registry(empty_role_cortisol_spike=0.5)
        reg.register("only-tool", role=ToolRole.WEB_SEARCH)
        reg.equip(ToolRole.WEB_SEARCH, "only-tool")

        replacement = reg.handle_tool_failure("only-tool", "crash")
        assert replacement is None
        assert ToolRole.WEB_SEARCH not in reg.belt
        assert len(cortisol_calls) == 1
        assert cortisol_calls[0][2] == 0.5

    def test_empty_event_published(self) -> None:
        reg, published, _ = _make_registry()
        reg.register("solo", role=ToolRole.MEMORY)
        reg.equip(ToolRole.MEMORY, "solo")

        reg.handle_tool_failure("solo", "oom")
        toolbelt_events = [
            json.loads(p) for t, p in published if "toolbelt" in t
        ]
        assert any(e["event"] == "empty" for e in toolbelt_events)


class TestRecoveryReequip:
    def test_reequip_on_recovery(self) -> None:
        reg, published, _ = _make_registry()
        reg.register("a", role=ToolRole.CLI)
        reg.register("b", role=ToolRole.CLI)
        reg.equip(ToolRole.CLI, "a")
        reg.handle_tool_failure("a", "timeout")
        assert reg.belt[ToolRole.CLI].name == "b"

        # Simulate recovery
        reg.heartbeat("a")
        reequipped = reg.try_reequip_on_recovery("a")
        assert reequipped
        assert reg.belt[ToolRole.CLI].name == "a"

    def test_recovery_event_published(self) -> None:
        reg, published, _ = _make_registry()
        reg.register("a", role=ToolRole.CLI)
        reg.register("b", role=ToolRole.CLI)
        reg.equip(ToolRole.CLI, "a")
        reg.handle_tool_failure("a", "timeout")
        reg.heartbeat("a")
        reg.try_reequip_on_recovery("a")
        toolbelt_events = [
            json.loads(p) for t, p in published if "toolbelt" in t
        ]
        assert any(e["event"] == "recovery" for e in toolbelt_events)

    def test_no_reequip_when_disabled(self) -> None:
        reg, _, _ = _make_registry(auto_reequip_on_recovery=False)
        reg.register("a", role=ToolRole.CLI)
        reg.register("b", role=ToolRole.CLI)
        reg.equip(ToolRole.CLI, "a")
        reg.handle_tool_failure("a", "timeout")
        reg.heartbeat("a")
        assert not reg.try_reequip_on_recovery("a")
        assert reg.belt[ToolRole.CLI].name == "b"

    def test_no_reequip_if_not_original(self) -> None:
        reg, _, _ = _make_registry()
        reg.register("a", role=ToolRole.CLI)
        reg.register("b", role=ToolRole.CLI)
        reg.equip(ToolRole.CLI, "a")
        # b was never the original equipped tool
        assert not reg.try_reequip_on_recovery("b")


class TestEdgeCases:
    def test_failure_nonequipped_noop(self) -> None:
        reg, _, cortisol_calls = _make_registry()
        reg.register("a", role=ToolRole.CLI)
        reg.register("b", role=ToolRole.CLI)
        reg.equip(ToolRole.CLI, "a")
        result = reg.handle_tool_failure("b", "error")
        assert result is None
        assert len(cortisol_calls) == 0

    def test_failure_unknown_tool_noop(self) -> None:
        reg, _, _ = _make_registry()
        assert reg.handle_tool_failure("ghost", "poof") is None

    def test_failure_no_role_noop(self) -> None:
        reg, _, _ = _make_registry()
        reg.register("norole")
        assert reg.handle_tool_failure("norole", "err") is None
