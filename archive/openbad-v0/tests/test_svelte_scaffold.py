"""Tests for SvelteKit project scaffold (#247)."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "wui-svelte"


def test_scaffold_directory_exists():
    assert ROOT.is_dir()


def test_package_json_valid():
    pkg = ROOT / "package.json"
    assert pkg.exists()
    data = json.loads(pkg.read_text(encoding="utf-8"))
    assert data["name"] == "openbad-wui"
    assert "dev" in data["scripts"]
    assert "build" in data["scripts"]


def test_svelte_config_exists():
    assert (ROOT / "svelte.config.js").exists()


def test_vite_config_exists():
    assert (ROOT / "vite.config.ts").exists()


def test_tsconfig_exists():
    assert (ROOT / "tsconfig.json").exists()


def test_routes_exist():
    routes = ROOT / "src" / "routes"
    assert (routes / "+layout.svelte").exists()
    assert (routes / "+page.svelte").exists()
    for route in ("chat", "providers", "senses", "toolbelt", "entity", "health"):
        assert (routes / route / "+page.svelte").exists(), f"Missing route: {route}"


def test_lib_structure():
    lib = ROOT / "src" / "lib"
    assert (lib / "api" / "client.ts").exists()
    assert (lib / "stores" / "telemetry.ts").exists()
    assert (lib / "stores" / "config.ts").exists()
    assert (lib / "components" / "Card.svelte").exists()


def test_app_html_exists():
    assert (ROOT / "src" / "app.html").exists()


def test_static_adapter_configured():
    config = (ROOT / "svelte.config.js").read_text(encoding="utf-8")
    assert "adapter-static" in config
    assert "fallback" in config


def test_gitignore_present():
    gi = ROOT / ".gitignore"
    assert gi.exists()
    text = gi.read_text(encoding="utf-8")
    assert "node_modules" in text
    assert "build" in text


def test_layout_has_spa_mode():
    layout_ts = ROOT / "src" / "routes" / "+layout.ts"
    assert layout_ts.exists()
    text = layout_ts.read_text(encoding="utf-8")
    assert "prerender" in text
    assert "ssr" in text


def test_makefile_targets():
    makefile = ROOT.parent / "Makefile"
    text = makefile.read_text(encoding="utf-8")
    assert "wui:" in text
    assert "wui-dev" in text


def test_dependencies_are_dev_only():
    """All SvelteKit deps should be devDependencies (no production deps)."""
    pkg = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert "dependencies" not in pkg or len(pkg.get("dependencies", {})) == 0
