"""Plugin discovery and loading from a configurable directory."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from pathlib import Path

from openbad.active_inference.plugin_interface import ObservationPlugin


def _load_module_from_path(path: Path) -> object:
    """Import a single ``.py`` file as a module and return it."""
    module_name = f"_openbad_plugin_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def discover_plugins(directory: Path) -> list[type[ObservationPlugin]]:
    """Discover :class:`ObservationPlugin` subclasses in *directory*.

    Scans all ``*.py`` files (non-recursive) in *directory* for concrete
    classes that implement :class:`ObservationPlugin`.
    """
    if not directory.is_dir():
        return []

    found: list[type[ObservationPlugin]] = []
    for py_file in sorted(directory.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            mod = _load_module_from_path(py_file)
        except Exception:  # noqa: BLE001, S112
            continue
        for _name, obj in inspect.getmembers(mod, inspect.isclass):
            if (
                issubclass(obj, ObservationPlugin)
                and obj is not ObservationPlugin
                and not inspect.isabstract(obj)
            ):
                found.append(obj)
    return found


def load_plugins(directory: Path) -> list[ObservationPlugin]:
    """Discover **and instantiate** all plugins in *directory*."""
    return [cls() for cls in discover_plugins(directory)]
