"""Verify framework packages are importable and dependencies are available."""

from __future__ import annotations


def test_frameworks_package_importable() -> None:
    import openbad.frameworks
    assert openbad.frameworks.__doc__ is not None


def test_agents_subpackage_importable() -> None:
    import openbad.frameworks.agents
    assert openbad.frameworks.agents is not None


def test_crews_subpackage_importable() -> None:
    import openbad.frameworks.crews
    assert openbad.frameworks.crews is not None


def test_workflows_subpackage_importable() -> None:
    import openbad.frameworks.workflows
    assert openbad.frameworks.workflows is not None


def test_langchain_core_available() -> None:
    from langchain_core.language_models import BaseChatModel  # noqa: F401


def test_langchain_available() -> None:
    from langchain.tools import StructuredTool  # noqa: F401


def test_langgraph_available() -> None:
    from langgraph.graph import StateGraph  # noqa: F401


def test_langgraph_checkpoint_available() -> None:
    from langgraph.checkpoint.base import BaseCheckpointSaver  # noqa: F401


def test_crewai_available() -> None:
    from crewai import Agent, Crew, Task  # noqa: F401
