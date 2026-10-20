"""Root test configuration — skip integration tests by default."""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip integration-marked tests unless ``-m integration`` is passed."""
    marker_expr: str = config.option.markexpr or ""
    if "integration" in marker_expr:
        return
    skip = pytest.mark.skip(reason="pass -m integration to run")
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(skip)
