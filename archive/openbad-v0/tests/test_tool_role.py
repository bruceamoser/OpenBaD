"""Tests for ToolRole enum and role metadata — Issue #232."""

from __future__ import annotations

from openbad.proprioception.registry import (
    ToolRegistry,
    ToolRole,
    ToolStatus,
)


class TestToolRoleEnum:
    def test_all_values(self) -> None:
        expected = {"CLI", "WEB_SEARCH", "MEMORY", "MEDIA", "CODE", "FILE_SYSTEM", "COMMUNICATION"}
        assert {r.value for r in ToolRole} == expected

    def test_enum_from_string(self) -> None:
        assert ToolRole("CLI") is ToolRole.CLI
        assert ToolRole("WEB_SEARCH") is ToolRole.WEB_SEARCH


class TestToolStatusWithRole:
    def test_default_role_is_none(self) -> None:
        ts = ToolStatus(name="test-tool")
        assert ts.role is None

    def test_role_assigned(self) -> None:
        ts = ToolStatus(name="test-tool", role=ToolRole.CODE)
        assert ts.role is ToolRole.CODE

    def test_all_roles_assignable(self) -> None:
        for role in ToolRole:
            ts = ToolStatus(name=f"tool-{role.value}", role=role)
            assert ts.role is role


class TestRegistryWithRole:
    def test_register_with_role(self) -> None:
        reg = ToolRegistry()
        entry = reg.register("cli-tool", role=ToolRole.CLI)
        assert entry.role is ToolRole.CLI

    def test_register_without_role(self) -> None:
        reg = ToolRegistry()
        entry = reg.register("generic-tool")
        assert entry.role is None

    def test_re_register_updates_role(self) -> None:
        reg = ToolRegistry()
        reg.register("tool", role=ToolRole.MEMORY)
        entry = reg.register("tool", role=ToolRole.FILE_SYSTEM)
        assert entry.role is ToolRole.FILE_SYSTEM

    def test_re_register_preserves_role_if_none(self) -> None:
        reg = ToolRegistry()
        reg.register("tool", role=ToolRole.CODE)
        entry = reg.register("tool")
        assert entry.role is ToolRole.CODE

    def test_snapshot_includes_role(self) -> None:
        reg = ToolRegistry()
        reg.register("web", role=ToolRole.WEB_SEARCH)
        snap = reg.snapshot()
        assert snap[0]["role"] == "WEB_SEARCH"

    def test_snapshot_role_none(self) -> None:
        reg = ToolRegistry()
        reg.register("plain")
        snap = reg.snapshot()
        assert snap[0]["role"] is None


class TestSensoryToolsHaveRole:
    def test_sensory_tools_have_media_role(self) -> None:
        from openbad.sensory.health import register_sensory_tools

        reg = ToolRegistry()
        register_sensory_tools(reg)
        for tool in reg.get_all_tools():
            assert tool.role is ToolRole.MEDIA, f"{tool.name} missing MEDIA role"
