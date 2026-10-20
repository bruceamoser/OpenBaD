"""Tests for cabinet/belt registry — Issue #233."""

from __future__ import annotations

import pytest

from openbad.proprioception.registry import (
    ToolRegistry,
    ToolRole,
)


@pytest.fixture()
def reg() -> ToolRegistry:
    r = ToolRegistry()
    r.register("grep-tool", role=ToolRole.CLI)
    r.register("find-tool", role=ToolRole.CLI)
    r.register("web-search", role=ToolRole.WEB_SEARCH)
    r.register("memory-store", role=ToolRole.MEMORY)
    return r


class TestCabinet:
    def test_groups_by_role(self, reg: ToolRegistry) -> None:
        cab = reg.cabinet
        assert len(cab[ToolRole.CLI]) == 2
        assert len(cab[ToolRole.WEB_SEARCH]) == 1
        assert len(cab[ToolRole.MEMORY]) == 1

    def test_excludes_tools_without_role(self) -> None:
        r = ToolRegistry()
        r.register("no-role-tool")
        r.register("cli-tool", role=ToolRole.CLI)
        cab = r.cabinet
        assert ToolRole.CLI in cab
        names = {t.name for tools in cab.values() for t in tools}
        assert "no-role-tool" not in names

    def test_empty_cabinet(self) -> None:
        r = ToolRegistry()
        assert r.cabinet == {}


class TestEquip:
    def test_equip_tool(self, reg: ToolRegistry) -> None:
        entry = reg.equip(ToolRole.CLI, "grep-tool")
        assert entry.name == "grep-tool"
        assert reg.belt[ToolRole.CLI].name == "grep-tool"

    def test_equip_replaces_previous(self, reg: ToolRegistry) -> None:
        reg.equip(ToolRole.CLI, "grep-tool")
        reg.equip(ToolRole.CLI, "find-tool")
        assert reg.belt[ToolRole.CLI].name == "find-tool"

    def test_equip_nonexistent_raises(self, reg: ToolRegistry) -> None:
        with pytest.raises(KeyError, match="not found"):
            reg.equip(ToolRole.CLI, "nonexistent")

    def test_equip_wrong_role_raises(self, reg: ToolRegistry) -> None:
        with pytest.raises(KeyError, match="has role"):
            reg.equip(ToolRole.WEB_SEARCH, "grep-tool")

    def test_only_one_per_role(self, reg: ToolRegistry) -> None:
        reg.equip(ToolRole.CLI, "grep-tool")
        reg.equip(ToolRole.WEB_SEARCH, "web-search")
        belt = reg.get_belt()
        assert len(belt) == 2
        assert belt[ToolRole.CLI].name == "grep-tool"
        assert belt[ToolRole.WEB_SEARCH].name == "web-search"


class TestUnequip:
    def test_unequip_removes_belt_entry(self, reg: ToolRegistry) -> None:
        reg.equip(ToolRole.CLI, "grep-tool")
        reg.unequip(ToolRole.CLI)
        assert ToolRole.CLI not in reg.belt

    def test_unequip_empty_is_noop(self, reg: ToolRegistry) -> None:
        reg.unequip(ToolRole.CODE)  # never equipped — no error
        assert ToolRole.CODE not in reg.belt


class TestGetBelt:
    def test_empty_belt(self) -> None:
        r = ToolRegistry()
        assert r.get_belt() == {}

    def test_belt_reflects_equipped(self, reg: ToolRegistry) -> None:
        reg.equip(ToolRole.MEMORY, "memory-store")
        belt = reg.get_belt()
        assert belt[ToolRole.MEMORY].name == "memory-store"

    def test_belt_excludes_unregistered(self, reg: ToolRegistry) -> None:
        reg.equip(ToolRole.CLI, "grep-tool")
        reg.unregister("grep-tool")
        assert ToolRole.CLI not in reg.get_belt()
