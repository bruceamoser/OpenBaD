"""Tests for plugin discovery and loading."""

from __future__ import annotations

from pathlib import Path

from openbad.active_inference.plugin_interface import ObservationPlugin
from openbad.active_inference.plugin_loader import discover_plugins, load_plugins

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

_GOOD_PLUGIN_SRC = """\
from openbad.active_inference.plugin_interface import ObservationPlugin, ObservationResult

class DemoPlugin(ObservationPlugin):
    @property
    def source_id(self) -> str:
        return "demo"

    async def observe(self) -> ObservationResult:
        return ObservationResult(metrics={"x": 1})

    def default_predictions(self) -> dict:
        return {"x": {"expected": 0.0, "tolerance": 1.0}}
"""

_ABSTRACT_PLUGIN_SRC = """\
from openbad.active_inference.plugin_interface import ObservationPlugin

class AbstractOnly(ObservationPlugin):
    pass
"""

_BAD_MODULE_SRC = "raise RuntimeError('import boom')\n"


# ------------------------------------------------------------------ #
# Tests
# ------------------------------------------------------------------ #


class TestDiscoverPlugins:
    def test_finds_concrete_plugin(self, tmp_path: Path) -> None:
        (tmp_path / "demo.py").write_text(_GOOD_PLUGIN_SRC, encoding="utf-8")
        found = discover_plugins(tmp_path)
        assert len(found) == 1
        assert found[0].__name__ == "DemoPlugin"

    def test_skips_abstract_plugin(self, tmp_path: Path) -> None:
        (tmp_path / "abstract.py").write_text(
            _ABSTRACT_PLUGIN_SRC, encoding="utf-8",
        )
        found = discover_plugins(tmp_path)
        assert len(found) == 0

    def test_skips_bad_module(self, tmp_path: Path) -> None:
        (tmp_path / "bad.py").write_text(_BAD_MODULE_SRC, encoding="utf-8")
        found = discover_plugins(tmp_path)
        assert len(found) == 0

    def test_skips_underscore_files(self, tmp_path: Path) -> None:
        (tmp_path / "__init__.py").write_text(
            _GOOD_PLUGIN_SRC, encoding="utf-8",
        )
        found = discover_plugins(tmp_path)
        assert len(found) == 0

    def test_empty_directory(self, tmp_path: Path) -> None:
        found = discover_plugins(tmp_path)
        assert found == []

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        found = discover_plugins(tmp_path / "nope")
        assert found == []

    def test_multiple_plugins(self, tmp_path: Path) -> None:
        for name in ("a", "b"):
            src = _GOOD_PLUGIN_SRC.replace("DemoPlugin", f"Plugin{name.upper()}")
            src = src.replace('"demo"', f'"{name}"')
            (tmp_path / f"{name}.py").write_text(src, encoding="utf-8")
        found = discover_plugins(tmp_path)
        assert len(found) == 2


class TestLoadPlugins:
    def test_instantiates_plugins(self, tmp_path: Path) -> None:
        (tmp_path / "demo.py").write_text(_GOOD_PLUGIN_SRC, encoding="utf-8")
        plugins = load_plugins(tmp_path)
        assert len(plugins) == 1
        assert isinstance(plugins[0], ObservationPlugin)
        assert plugins[0].source_id == "demo"

    def test_empty_returns_empty(self, tmp_path: Path) -> None:
        assert load_plugins(tmp_path) == []


class TestConfigFromYaml:
    def test_round_trip(self, tmp_path: Path) -> None:
        from openbad.active_inference.config import ActiveInferenceConfig

        cfg = ActiveInferenceConfig(surprise_threshold=0.8)
        path = tmp_path / "ai.yaml"
        import yaml

        path.write_text(
            yaml.dump({"active_inference": cfg.to_dict()}),
            encoding="utf-8",
        )

        loaded = ActiveInferenceConfig.from_yaml(path)
        assert loaded.surprise_threshold == 0.8
        assert loaded.plugin_dir == cfg.plugin_dir

    def test_defaults(self) -> None:
        from openbad.active_inference.config import ActiveInferenceConfig

        cfg = ActiveInferenceConfig()
        assert cfg.surprise_threshold == 0.6
        assert cfg.daily_token_budget == 5000
        assert cfg.cooldown_seconds == 300
        assert cfg.max_concurrent == 1
        assert cfg.world_model_history_size == 20
        assert cfg.ema_alpha == 0.1

    def test_to_dict(self) -> None:
        from openbad.active_inference.config import ActiveInferenceConfig

        cfg = ActiveInferenceConfig()
        d = cfg.to_dict()
        assert isinstance(d["plugin_dir"], str)
        assert d["surprise_threshold"] == 0.6
