"""Tests for UserLearningPipeline (#245)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml

from openbad.identity.learning import (
    InteractionRecord,
    UserLearningPipeline,
)
from openbad.identity.persistence import IdentityPersistence
from openbad.memory.episodic import EpisodicMemory


def _make_config(tmp: Path) -> Path:
    cfg = tmp / "identity.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "user": {
                    "name": "Alice",
                    "preferred_name": "",
                    "communication_style": "casual",
                    "expertise_domains": [],
                    "interaction_history_summary": "",
                    "worldview": [],
                    "interests": [],
                    "pet_peeves": [],
                    "preferred_feedback_style": "balanced",
                    "active_projects": [],
                    "timezone": "",
                    "work_hours": [9, 17],
                },
                "assistant": {
                    "name": "OpenBaD",
                    "persona_summary": "",
                    "learning_focus": [],
                    "worldview": [],
                    "boundaries": [],
                    "opinions": {},
                    "vocabulary": {},
                    "rhetorical_style": {
                        "tone": "direct",
                        "sentence_pattern": "concise",
                        "challenge_mode": "steel-man first",
                        "explanation_depth": "balanced",
                    },
                    "influences": [],
                    "anti_patterns": [],
                    "current_focus": [],
                    "continuity_log": [],
                    "ocean": {
                        "openness": 0.7,
                        "conscientiousness": 0.8,
                        "extraversion": 0.5,
                        "agreeableness": 0.4,
                        "stability": 0.6,
                    },
                },
            },
            default_flow_style=False,
        ),
        encoding="utf-8",
    )
    return cfg


@pytest.fixture()
def env(tmp_path: Path):
    cfg = _make_config(tmp_path)
    ep = EpisodicMemory(tmp_path / "ep.json", auto_persist=True)
    ip = IdentityPersistence(cfg, ep)
    return ip, ep


# ------------------------------------------------------------------ #
# Batch triggering
# ------------------------------------------------------------------ #


class TestBatching:
    def test_no_flush_below_batch_size(self, env) -> None:
        ip, _ = env
        pipeline = UserLearningPipeline(ip, batch_size=5)
        for _ in range(4):
            flushed = pipeline.observe(InteractionRecord(message="hi"))
            assert flushed is False
        assert pipeline.pending == 4

    def test_auto_flush_at_batch_size(self, env) -> None:
        ip, _ = env
        pipeline = UserLearningPipeline(ip, batch_size=3)
        for _i in range(3):
            flushed = pipeline.observe(InteractionRecord(message="hello"))
        assert flushed is True
        assert pipeline.pending == 0

    def test_manual_flush_resets(self, env) -> None:
        ip, _ = env
        pipeline = UserLearningPipeline(ip, batch_size=100)
        pipeline.observe(InteractionRecord(message="hey"))
        result = pipeline.flush()
        assert result["interactions"] == 1
        assert pipeline.pending == 0

    def test_flush_empty_returns_empty_dict(self, env) -> None:
        ip, _ = env
        pipeline = UserLearningPipeline(ip, batch_size=10)
        result = pipeline.flush()
        assert result == {}


# ------------------------------------------------------------------ #
# Communication style inference
# ------------------------------------------------------------------ #


class TestCommunicationStyle:
    def test_formal_inferred(self, env) -> None:
        ip, _ = env
        pipeline = UserLearningPipeline(ip, batch_size=100)
        for _ in range(5):
            pipeline.observe(
                InteractionRecord(message="Would you please help me?"),
            )
        result = pipeline.flush()
        assert result["updates"]["communication_style"] == "formal"

    def test_casual_inferred(self, env) -> None:
        ip, _ = env
        pipeline = UserLearningPipeline(ip, batch_size=100)
        for _ in range(5):
            pipeline.observe(InteractionRecord(message="hey thx lol"))
        result = pipeline.flush()
        assert result["updates"]["communication_style"] == "casual"

    def test_terse_inferred(self, env) -> None:
        ip, _ = env
        pipeline = UserLearningPipeline(ip, batch_size=100)
        for _ in range(5):
            pipeline.observe(InteractionRecord(message="ok"))
        result = pipeline.flush()
        assert result["updates"]["communication_style"] == "terse"


# ------------------------------------------------------------------ #
# Topic frequency
# ------------------------------------------------------------------ #


class TestTopicTracking:
    def test_topics_merged(self, env) -> None:
        ip, _ = env
        pipeline = UserLearningPipeline(ip, batch_size=100)
        pipeline.observe(
            InteractionRecord(message="x", topics=["python", "rust"]),
        )
        pipeline.observe(
            InteractionRecord(message="y", topics=["python"]),
        )
        result = pipeline.flush()
        domains = result["updates"]["expertise_domains"]
        assert "python" in domains
        assert "rust" in domains
        assert result["updates"]["active_projects"] == ["python", "rust"]

    def test_topics_preserve_existing(self, env) -> None:
        ip, _ = env
        ip.update_user(expertise_domains=["go"])
        pipeline = UserLearningPipeline(ip, batch_size=100)
        pipeline.observe(
            InteractionRecord(message="x", topics=["python"]),
        )
        result = pipeline.flush()
        domains = result["updates"]["expertise_domains"]
        assert "go" in domains
        assert "python" in domains


# ------------------------------------------------------------------ #
# Correction tracking
# ------------------------------------------------------------------ #


class TestCorrectionTracking:
    def test_corrections_in_summary(self, env) -> None:
        ip, _ = env
        pipeline = UserLearningPipeline(ip, batch_size=100)
        pipeline.observe(
            InteractionRecord(message="no, I meant X", is_correction=True),
        )
        result = pipeline.flush()
        summary = result["updates"]["interaction_history_summary"]
        assert "corrections=1" in summary

    def test_repeated_corrections_infer_pet_peeve(self, env) -> None:
        ip, _ = env
        pipeline = UserLearningPipeline(ip, batch_size=100)
        pipeline.observe(
            InteractionRecord(message="No, that is too verbose", is_correction=True),
        )
        pipeline.observe(
            InteractionRecord(message="You repeated the same mistake", is_correction=True),
        )
        result = pipeline.flush()
        assert result["updates"]["pet_peeves"] == [
            "Dislikes avoidable misunderstandings and repeats",
        ]


# ------------------------------------------------------------------ #
# Activity time tracking
# ------------------------------------------------------------------ #


class TestActivityTime:
    def test_peak_hour_in_summary(self, env) -> None:
        ip, _ = env
        pipeline = UserLearningPipeline(ip, batch_size=100)
        # Use a fixed timestamp at hour 14
        ts = time.mktime(time.strptime("2025-01-15 14:30:00", "%Y-%m-%d %H:%M:%S"))
        for _ in range(3):
            pipeline.observe(
                InteractionRecord(message="work", timestamp=ts),
            )
        result = pipeline.flush()
        summary = result["updates"]["interaction_history_summary"]
        assert "peak_hour=14" in summary
        assert result["updates"]["work_hours"] == (14, 14)


class TestFeedbackStyle:
    def test_direct_feedback_style_inferred(self, env) -> None:
        ip, _ = env
        pipeline = UserLearningPipeline(ip, batch_size=100)
        pipeline.observe(
            InteractionRecord(message="Be direct and concise with me"),
        )
        result = pipeline.flush()
        assert result["updates"]["preferred_feedback_style"] == "direct"

    def test_challenge_feedback_style_inferred(self, env) -> None:
        ip, _ = env
        pipeline = UserLearningPipeline(ip, batch_size=100)
        pipeline.observe(
            InteractionRecord(message="Please challenge me and push back"),
        )
        result = pipeline.flush()
        assert result["updates"]["preferred_feedback_style"] == "challenge me"


# ------------------------------------------------------------------ #
# LTM shadow integration
# ------------------------------------------------------------------ #


class TestLTMIntegration:
    def test_updates_go_to_ltm_not_config(self, env) -> None:
        ip, ep = env
        pipeline = UserLearningPipeline(ip, batch_size=2)
        pipeline.observe(
            InteractionRecord(message="Would you please elaborate?"),
        )
        pipeline.observe(
            InteractionRecord(message="Could you kindly explain?"),
        )
        # Shadow should exist in episodic LTM
        entry = ep.read("identity/user_shadow")
        assert entry is not None
        assert entry.value["communication_style"] == "formal"
