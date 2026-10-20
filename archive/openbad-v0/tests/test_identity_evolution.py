"""Tests for identity evolution during sleep consolidation."""

from __future__ import annotations

import time
from pathlib import Path

from openbad.identity.assistant_profile import ContinuityEntry
from openbad.identity.evolution import (
    CONTINUITY_RETENTION_DAYS,
    MAX_OCEAN_DRIFT,
    MAX_RECENT_ENTRIES,
    IdentityEvolver,
)
from openbad.identity.persistence import IdentityPersistence
from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.memory.episodic import EpisodicMemory


def create_test_identity(tmp_path: Path) -> IdentityPersistence:
    """Create a test identity for evolution tests."""
    identity_yaml = tmp_path / "identity.yaml"
    identity_yaml.write_text(
        """user:
  name: Test User
  preferred_name: Tester
  communication_style: casual
  expertise_domains: []
  interaction_history_summary: ""
  worldview: []
  interests: []
  pet_peeves: []
  preferred_feedback_style: balanced
  active_projects: []
  timezone: UTC
  work_hours: [9, 17]

assistant:
  name: TestBot
  persona_summary: Test assistant
  learning_focus: Testing
  worldview:
    - Test-driven development matters
  boundaries:
    hard: []
    soft: []
  opinions: {}
  vocabulary:
    preferred: []
    avoid: []
  rhetorical_style:
    tone: direct
    sentence_pattern: concise
    challenge_mode: steel-man first
    explanation_depth: balanced
  influences: []
  anti_patterns: []
  current_focus: ""
  continuity_log: []
  ocean:
    openness: 0.5
    conscientiousness: 0.5
    extraversion: 0.5
    agreeableness: 0.5
    stability: 0.5
"""
    )

    episodic = EpisodicMemory(storage_path=tmp_path / "episodic")
    return IdentityPersistence(config_path=identity_yaml, episodic=episodic)


# ------------------------------------------------------------------ #
# SWS phase: anti-pattern extraction
# ------------------------------------------------------------------ #


def test_sws_extracts_anti_patterns_from_failures(tmp_path: Path) -> None:
    """SWS should extract anti-patterns from failure entries."""
    persistence = create_test_identity(tmp_path)
    evolver = IdentityEvolver(persistence, persistence._episodic)

    # Add failure entries with verbosity theme
    for i in range(5):
        entry = MemoryEntry(
            key=f"failure_{i}",
            value="Response was too verbose and over-explained",
            tier=MemoryTier.EPISODIC,
            metadata={"outcome": "failure", "sentiment": "negative"},
        )
        persistence._episodic.write(entry)

    # Run SWS phase
    changes = evolver.apply_sws_phase()

    # Should extract verbosity anti-pattern
    assert changes > 0
    assert any("over-explain" in p for p in persistence.assistant.anti_patterns)


def test_sws_extracts_patterns_from_corrections(tmp_path: Path) -> None:
    """SWS should learn from user corrections."""
    persistence = create_test_identity(tmp_path)
    evolver = IdentityEvolver(persistence, persistence._episodic)

    # Add correction entries
    for i in range(3):
        entry = MemoryEntry(
            key=f"correction_{i}",
            value="Please use more formal language",
            tier=MemoryTier.EPISODIC,
            metadata={"type": "user_correction"},
        )
        persistence._episodic.write(entry)

    changes = evolver.apply_sws_phase()

    assert changes > 0
    assert any("formal" in p for p in persistence.assistant.anti_patterns)


def test_sws_avoids_duplicate_anti_patterns(tmp_path: Path) -> None:
    """SWS should not add duplicate anti-patterns."""
    persistence = create_test_identity(tmp_path)

    # Pre-populate with an anti-pattern
    persistence.update_assistant(anti_patterns=["Don't over-explain simple concepts"])

    evolver = IdentityEvolver(persistence, persistence._episodic)

    # Add failure entries that would trigger the same pattern
    for i in range(5):
        entry = MemoryEntry(
            key=f"failure_{i}",
            value="Too verbose",
            tier=MemoryTier.EPISODIC,
            metadata={"outcome": "failure"},
        )
        persistence._episodic.write(entry)

    initial_count = len(persistence.assistant.anti_patterns)
    evolver.apply_sws_phase()
    final_count = len(persistence.assistant.anti_patterns)

    # Should not add duplicate
    assert final_count == initial_count


# ------------------------------------------------------------------ #
# REM phase: positive reinforcement
# ------------------------------------------------------------------ #


def test_rem_reinforces_worldview_from_successes(tmp_path: Path) -> None:
    """REM should strengthen worldview from successful outcomes."""
    persistence = create_test_identity(tmp_path)
    evolver = IdentityEvolver(persistence, persistence._episodic)

    # Add success entries with conciseness theme
    for i in range(5):
        entry = MemoryEntry(
            key=f"success_{i}",
            value="User appreciated the brief, concise response",
            tier=MemoryTier.EPISODIC,
            metadata={"outcome": "success", "sentiment": "positive"},
        )
        persistence._episodic.write(entry)

    initial_worldview = persistence.assistant.worldview.copy()
    changes = evolver.apply_rem_phase()

    assert changes > 0
    # Should add conciseness to worldview
    assert any("Brevity" in w for w in persistence.assistant.worldview)
    assert len(persistence.assistant.worldview) > len(initial_worldview)


def test_rem_applies_bounded_ocean_drift(tmp_path: Path) -> None:
    """REM should apply bounded OCEAN drift toward successful patterns."""
    persistence = create_test_identity(tmp_path)
    evolver = IdentityEvolver(persistence, persistence._episodic)

    # Add many creative success entries
    for i in range(15):
        entry = MemoryEntry(
            key=f"creative_{i}",
            value="Creative solution worked well, user praised exploration",
            tier=MemoryTier.EPISODIC,
            metadata={"outcome": "success", "sentiment": "positive"},
        )
        persistence._episodic.write(entry)

    initial_openness = persistence.assistant.openness
    evolver.apply_rem_phase()
    final_openness = persistence.assistant.openness

    # Openness should drift up
    assert final_openness > initial_openness
    # But drift should be bounded (with epsilon for floating point)
    assert abs(final_openness - initial_openness) <= MAX_OCEAN_DRIFT + 1e-10


def test_ocean_drift_respects_bounds(tmp_path: Path) -> None:
    """OCEAN drift must not exceed ±0.05 per cycle."""
    persistence = create_test_identity(tmp_path)
    evolver = IdentityEvolver(persistence, persistence._episodic)

    # Add excessive positive signals for openness
    for i in range(100):
        entry = MemoryEntry(
            key=f"creative_{i}",
            value="Creative and exploratory",
            tier=MemoryTier.EPISODIC,
            metadata={"outcome": "success"},
        )
        persistence._episodic.write(entry)

    initial_openness = persistence.assistant.openness
    evolver.apply_rem_phase()
    final_openness = persistence.assistant.openness

    drift = abs(final_openness - initial_openness)
    assert drift <= MAX_OCEAN_DRIFT + 1e-10, f"Drift {drift} exceeds max {MAX_OCEAN_DRIFT}"


def test_ocean_values_clamped_to_range(tmp_path: Path) -> None:
    """OCEAN values must stay in [0.0, 1.0] range."""
    persistence = create_test_identity(tmp_path)

    # Start with high openness
    persistence.update_assistant(openness=0.98)

    evolver = IdentityEvolver(persistence, persistence._episodic)

    # Add signals that would push above 1.0
    for i in range(10):
        entry = MemoryEntry(
            key=f"creative_{i}",
            value="Creative exploration",
            tier=MemoryTier.EPISODIC,
            metadata={"outcome": "success"},
        )
        persistence._episodic.write(entry)

    evolver.apply_rem_phase()

    # Should be clamped to 1.0
    assert 0.0 <= persistence.assistant.openness <= 1.0
    assert 0.0 <= persistence.assistant.conscientiousness <= 1.0


# ------------------------------------------------------------------ #
# Continuity log compression
# ------------------------------------------------------------------ #


def test_continuity_log_keeps_recent_entries(tmp_path: Path) -> None:
    """Continuity log should keep most recent N entries verbatim."""
    persistence = create_test_identity(tmp_path)
    evolver = IdentityEvolver(persistence, persistence._episodic)

    # Add many continuity entries
    entries = []
    now = time.time()
    for i in range(MAX_RECENT_ENTRIES + 20):
        entry = ContinuityEntry(
            summary=f"Event {i}",
            timestamp=now - (i * 3600),  # Space 1 hour apart
            source="test",
        )
        entries.append(entry)

    persistence.update_assistant(continuity_log=entries)

    result = evolver.compress_continuity_log()

    # Should keep most recent
    assert result["kept"] == MAX_RECENT_ENTRIES
    assert result["summarized"] + result["deleted"] > 0


def test_continuity_log_deletes_old_entries(tmp_path: Path) -> None:
    """Continuity log should delete entries older than retention period."""
    persistence = create_test_identity(tmp_path)
    evolver = IdentityEvolver(persistence, persistence._episodic)

    now = time.time()
    old_cutoff = now - (CONTINUITY_RETENTION_DAYS * 86400 + 86400)

    # Add old entries beyond retention (these should be deleted)
    entries = [
        ContinuityEntry(
            summary=f"Old event {i}",
            timestamp=old_cutoff - (i * 3600),
            source="test",
        )
        for i in range(20)
    ]

    # Add some recent entries (will fill up the MAX_RECENT_ENTRIES quota)
    entries.extend([
        ContinuityEntry(
            summary=f"Recent event {i}",
            timestamp=now - (i * 3600),
            source="test",
        )
        for i in range(60)  # More than MAX_RECENT_ENTRIES
    ])

    persistence.update_assistant(continuity_log=entries)

    result = evolver.compress_continuity_log()

    # Old entries should be deleted (since we have 80 total, only 50 kept)
    assert result["deleted"] > 0
    assert len(persistence.assistant.continuity_log) < len(entries)


def test_continuity_log_summarizes_middle_entries(tmp_path: Path) -> None:
    """Continuity log should summarize entries between recent and deleted."""
    persistence = create_test_identity(tmp_path)
    evolver = IdentityEvolver(persistence, persistence._episodic)

    now = time.time()

    # Add entries spanning full range
    entries = [
        ContinuityEntry(
            summary=f"Event {i}",
            timestamp=now - (i * 3600 * 24),  # Space 1 day apart
            source="test",
        )
        for i in range(100)
    ]

    persistence.update_assistant(continuity_log=entries)
    initial_summary = persistence.assistant.persona_summary

    result = evolver.compress_continuity_log()

    # Should have summarized some entries
    assert result["summarized"] > 0
    # Persona summary should be updated
    assert persistence.assistant.persona_summary != initial_summary
    assert "Compressed history:" in persistence.assistant.persona_summary


# ------------------------------------------------------------------ #
# Change logging
# ------------------------------------------------------------------ #


def test_all_changes_logged_to_continuity(tmp_path: Path) -> None:
    """All identity changes should be logged to continuity_log."""
    persistence = create_test_identity(tmp_path)
    evolver = IdentityEvolver(persistence, persistence._episodic)

    # Add entries that trigger changes
    for i in range(5):
        entry = MemoryEntry(
            key=f"failure_{i}",
            value="Too verbose",
            tier=MemoryTier.EPISODIC,
            metadata={"outcome": "failure"},
        )
        persistence._episodic.write(entry)

    initial_log_len = len(persistence.assistant.continuity_log)
    evolver.apply_sws_phase()
    final_log_len = len(persistence.assistant.continuity_log)

    # Should have added continuity entries
    assert final_log_len > initial_log_len

    # Check that changes are logged
    latest_entries = persistence.assistant.continuity_log[-5:]
    assert any("anti-pattern" in e.summary.lower() for e in latest_entries)


def test_change_entries_have_timestamps_and_sources(tmp_path: Path) -> None:
    """Continuity log entries should have proper metadata."""
    persistence = create_test_identity(tmp_path)
    evolver = IdentityEvolver(persistence, persistence._episodic)

    # Trigger a change
    for i in range(5):
        entry = MemoryEntry(
            key=f"success_{i}",
            value="Brief and concise",
            tier=MemoryTier.EPISODIC,
            metadata={"outcome": "success"},
        )
        persistence._episodic.write(entry)

    evolver.apply_rem_phase()

    # Check continuity entries
    latest = persistence.assistant.continuity_log[-1]
    assert latest.timestamp > 0
    assert latest.source in ["rem_positive_feedback", "ocean_drift"]
    assert "sleep_evolution" in latest.tags


# ------------------------------------------------------------------ #
# Integration
# ------------------------------------------------------------------ #


def test_full_sleep_cycle_evolution(tmp_path: Path) -> None:
    """Full sleep cycle: SWS → REM → compression."""
    persistence = create_test_identity(tmp_path)
    evolver = IdentityEvolver(persistence, persistence._episodic)

    # Add mixed feedback
    for i in range(5):
        persistence._episodic.write(
            MemoryEntry(
                key=f"failure_{i}",
                value="Too verbose",
                tier=MemoryTier.EPISODIC,
                metadata={"outcome": "failure"},
            )
        )
        persistence._episodic.write(
            MemoryEntry(
                key=f"success_{i}",
                value="Concise response",
                tier=MemoryTier.EPISODIC,
                metadata={"outcome": "success"},
            )
        )

    # Add old continuity entries (enough to trigger deletion)
    now = time.time()
    old_cutoff = now - (CONTINUITY_RETENTION_DAYS * 86400 + 86400)
    old_entries = [
        ContinuityEntry(
            summary=f"Old event {i}",
            timestamp=old_cutoff - (i * 86400),
            source="test",
        )
        for i in range(30)
    ]
    # Add recent entries to fill MAX_RECENT_ENTRIES
    old_entries.extend([
        ContinuityEntry(
            summary=f"Recent event {i}",
            timestamp=now - (i * 3600),
            source="test",
        )
        for i in range(40)
    ])
    persistence.update_assistant(continuity_log=old_entries)

    initial_anti_patterns = len(persistence.assistant.anti_patterns)
    initial_worldview = len(persistence.assistant.worldview)

    # Run full cycle
    sws_changes = evolver.apply_sws_phase()
    rem_changes = evolver.apply_rem_phase()
    compression = evolver.compress_continuity_log()

    # Verify changes
    assert sws_changes > 0
    assert rem_changes > 0
    assert compression["deleted"] > 0

    assert len(persistence.assistant.anti_patterns) >= initial_anti_patterns
    assert len(persistence.assistant.worldview) >= initial_worldview
    # OCEAN may drift
    assert 0.0 <= persistence.assistant.openness <= 1.0
