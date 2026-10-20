"""Identity evolution during sleep consolidation.

During SWS phase: extract anti_patterns from negative feedback
During REM phase: reinforce worldview/opinions from positive outcomes
Compress continuity_log and apply bounded OCEAN drift

Design
------
- Scans episodic memory for feedback signals (corrections, praise, failures)
- Extracts patterns from user behavior and task outcomes
- Updates assistant profile fields via IdentityPersistence
- All changes logged in continuity_log with reasoning
- OCEAN drift bounded to ±0.05 per consolidation cycle

Integration
-----------
- Called by SleepOrchestrator during SWS and REM phases
- Requires IdentityPersistence and EpisodicMemory instances
- Not a daemon subprocess — invoked as part of sleep cycle
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbad.identity.persistence import IdentityPersistence
    from openbad.memory.episodic import EpisodicMemory

logger = logging.getLogger(__name__)

# Drift limit per consolidation cycle
MAX_OCEAN_DRIFT = 0.05

# Continuity log size management
MAX_RECENT_ENTRIES = 50  # Keep last N verbatim
CONTINUITY_RETENTION_DAYS = 90  # Delete entries older than this


class IdentityEvolver:
    """Evolves assistant identity during sleep based on interaction patterns."""

    def __init__(
        self,
        persistence: IdentityPersistence,
        episodic: EpisodicMemory,
    ) -> None:
        self.persistence = persistence
        self.episodic = episodic
        self._changes: list[str] = []  # Track changes for logging

    def apply_sws_phase(self) -> int:
        """Extract anti_patterns from negative feedback (SWS phase).

        Returns count of identity changes made.
        """
        self._changes = []

        # Query recent episodic entries for failure signals
        all_entries = self.episodic.query("")
        # Take most recent 200 entries
        recent = sorted(
            all_entries,
            key=lambda e: getattr(e, "timestamp", 0.0),
            reverse=True,
        )[:200]

        failures = []
        corrections = []

        for entry in recent:
            metadata = entry.metadata or {}

            # Look for failure markers
            if metadata.get("outcome") == "failure":
                failures.append(entry)

            # Look for user corrections
            if metadata.get("type") == "user_correction":
                corrections.append(entry)

            # Look for negative sentiment
            if metadata.get("sentiment") == "negative":
                failures.append(entry)

        # Extract patterns from failures
        anti_patterns = self._extract_anti_patterns(failures, corrections)

        if anti_patterns:
            assistant = self.persistence.assistant
            existing = set(assistant.anti_patterns)
            new_patterns = [p for p in anti_patterns if p not in existing]

            if new_patterns:
                updated_patterns = assistant.anti_patterns + new_patterns
                self.persistence.update_assistant(anti_patterns=updated_patterns)

                # Log changes
                for pattern in new_patterns:
                    self._log_change(
                        f"Learned anti-pattern: {pattern}",
                        "sws_negative_feedback",
                    )

                logger.info("SWS: Added %d anti-patterns", len(new_patterns))

        return len(self._changes)

    def apply_rem_phase(self) -> int:
        """Reinforce worldview/opinions from positive outcomes (REM phase).

        Returns count of identity changes made.
        """
        # Query recent episodic entries for success signals
        all_entries = self.episodic.query("")
        # Take most recent 200 entries
        recent = sorted(
            all_entries,
            key=lambda e: getattr(e, "timestamp", 0.0),
            reverse=True,
        )[:200]

        successes = []
        praise = []

        for entry in recent:
            metadata = entry.metadata or {}

            # Look for success markers
            if metadata.get("outcome") == "success":
                successes.append(entry)

            # Look for positive feedback
            if metadata.get("sentiment") == "positive":
                praise.append(entry)

            # Look for task completions
            if metadata.get("type") == "task_completion":
                successes.append(entry)

        # Analyze successful interactions
        if successes or praise:
            self._reinforce_successful_patterns(successes, praise)

        # Apply bounded OCEAN drift toward successful patterns
        self._apply_ocean_drift(successes, praise)

        return len(self._changes)

    def compress_continuity_log(self) -> dict[str, int]:
        """Compress old continuity log entries, keep recent ones.

        Returns
        -------
        dict
            {"kept": N, "summarized": M, "deleted": K}
        """
        assistant = self.persistence.assistant
        entries = assistant.continuity_log

        if not entries:
            return {"kept": 0, "summarized": 0, "deleted": 0}

        now = time.time()
        cutoff = now - (CONTINUITY_RETENTION_DAYS * 86400)

        # Sort by timestamp (newest first)
        sorted_entries = sorted(
            entries,
            key=lambda e: getattr(e, "timestamp", 0.0),
            reverse=True,
        )

        # Keep most recent N verbatim
        kept = sorted_entries[:MAX_RECENT_ENTRIES]

        # Process older entries
        older = sorted_entries[MAX_RECENT_ENTRIES:]
        deleted = [e for e in older if getattr(e, "timestamp", 0.0) < cutoff]
        summarized = [e for e in older if e not in deleted]

        if summarized:
            # Summarize into persona_summary
            summaries = [
                getattr(e, "summary", "") for e in summarized if getattr(e, "summary", "")
            ]
            if summaries:
                current_summary = assistant.persona_summary
                suffix = f"\n\nCompressed history: {'; '.join(summaries[:10])}"
                compressed = f"{current_summary}{suffix}"
                self.persistence.update_assistant(persona_summary=compressed)
                self._log_change(
                    f"Compressed {len(summarized)} old continuity entries",
                    "continuity_compression",
                )

        if deleted or summarized:
            self.persistence.update_assistant(continuity_log=kept)

        logger.info(
            "Continuity log: kept=%d, summarized=%d, deleted=%d",
            len(kept),
            len(summarized),
            len(deleted),
        )

        return {
            "kept": len(kept),
            "summarized": len(summarized),
            "deleted": len(deleted),
        }

    # ------------------------------------------------------------------ #
    # Internal pattern extraction
    # ------------------------------------------------------------------ #

    def _extract_anti_patterns(
        self,
        failures: list,
        corrections: list,
    ) -> list[str]:
        """Extract behavioral anti-patterns from failures and corrections."""
        patterns = []

        # Look for repeated themes in failures
        failure_themes = defaultdict(int)

        for entry in failures[:20]:  # Sample recent failures
            # Extract keywords from failure metadata or value
            value = getattr(entry, "value", "")
            if isinstance(value, str):
                if "verbose" in value.lower() or "too much" in value.lower():
                    failure_themes["verbosity"] += 1
                if "unclear" in value.lower() or "confusing" in value.lower():
                    failure_themes["clarity"] += 1
                if "slow" in value.lower() or "timeout" in value.lower():
                    failure_themes["performance"] += 1

        # Convert frequent themes into anti-patterns
        if failure_themes.get("verbosity", 0) >= 3:
            patterns.append("Don't over-explain simple concepts")

        if failure_themes.get("clarity", 0) >= 3:
            patterns.append("Always clarify assumptions before proceeding")

        if failure_themes.get("performance", 0) >= 3:
            patterns.append("Prioritize speed over exhaustive detail")

        # Analyze corrections for user preferences
        for entry in corrections[:10]:
            value = getattr(entry, "value", "")
            if isinstance(value, str):
                if "formal" in value.lower():
                    patterns.append("User prefers formal communication")
                elif "casual" in value.lower():
                    patterns.append("User prefers casual communication")

        return patterns

    def _reinforce_successful_patterns(
        self,
        successes: list,
        praise: list,
    ) -> None:
        """Strengthen worldview/opinions based on successful outcomes."""
        success_themes = defaultdict(int)

        for entry in successes[:20] + praise[:10]:
            value = getattr(entry, "value", "")
            metadata = getattr(entry, "metadata", {}) or {}

            if isinstance(value, str):
                # Detect successful approaches
                if "concise" in value.lower() or "brief" in value.lower():
                    success_themes["conciseness"] += 1
                if "detail" in value.lower() and metadata.get("outcome") == "success":
                    success_themes["thoroughness"] += 1
                if "creative" in value.lower() or "innovation" in value.lower():
                    success_themes["creativity"] += 1

        # Update worldview based on successful patterns
        assistant = self.persistence.assistant
        worldview = assistant.worldview.copy()

        if (
            success_themes.get("conciseness", 0) >= 3
            and "Brevity serves clarity" not in worldview
        ):
            worldview.append("Brevity serves clarity")
            self.persistence.update_assistant(worldview=worldview)
            self._log_change(
                "Reinforced: Brevity serves clarity",
                "rem_positive_feedback",
            )

        if (
            success_themes.get("thoroughness", 0) >= 3
            and "Thorough analysis prevents mistakes" not in worldview
        ):
            worldview.append("Thorough analysis prevents mistakes")
            self.persistence.update_assistant(worldview=worldview)
            self._log_change(
                "Reinforced: Thorough analysis prevents mistakes",
                "rem_positive_feedback",
            )

        if (
            success_themes.get("creativity", 0) >= 3
            and "Innovation emerges from exploration" not in worldview
        ):
            worldview.append("Innovation emerges from exploration")
            self.persistence.update_assistant(worldview=worldview)
            self._log_change(
                "Reinforced: Innovation emerges from exploration",
                "rem_positive_feedback",
            )

    def _apply_ocean_drift(self, successes: list, praise: list) -> None:
        """Apply bounded OCEAN personality drift toward successful patterns."""
        assistant = self.persistence.assistant

        # Calculate drift direction based on feedback
        drift = {
            "openness": 0.0,
            "conscientiousness": 0.0,
            "extraversion": 0.0,
            "agreeableness": 0.0,
            "stability": 0.0,
        }

        for entry in successes[:10] + praise[:5]:
            value = getattr(entry, "value", "")

            if isinstance(value, str):
                # Openness: creativity, exploration
                if "creative" in value.lower() or "explore" in value.lower():
                    drift["openness"] += 0.01

                # Conscientiousness: thoroughness, rigor
                if "thorough" in value.lower() or "careful" in value.lower():
                    drift["conscientiousness"] += 0.01

                # Extraversion: engagement, interaction
                if "engage" in value.lower() or "proactive" in value.lower():
                    drift["extraversion"] += 0.01

                # Agreeableness: collaboration, support
                if "helpful" in value.lower() or "supportive" in value.lower():
                    drift["agreeableness"] += 0.01

                # Stability: calm under pressure
                if "calm" in value.lower() or "stable" in value.lower():
                    drift["stability"] += 0.01

        # Apply bounded drift
        changes = {}
        for trait, delta in drift.items():
            if abs(delta) > 0.001:  # Only apply meaningful drift
                current = getattr(assistant, trait)
                # Clamp drift to maximum
                clamped_delta = max(-MAX_OCEAN_DRIFT, min(MAX_OCEAN_DRIFT, delta))
                new_value = max(0.0, min(1.0, current + clamped_delta))

                if abs(new_value - current) > 0.001:
                    changes[trait] = new_value
                    self._log_change(
                        f"OCEAN drift: {trait} {current:.3f} → {new_value:.3f}",
                        "ocean_drift",
                    )

        if changes:
            self.persistence.update_assistant(**changes)
            logger.info("Applied OCEAN drift: %s", changes)

    def _log_change(self, summary: str, source: str) -> None:
        """Log identity change to continuity log."""
        from openbad.identity.assistant_profile import ContinuityEntry

        assistant = self.persistence.assistant
        entries = assistant.continuity_log.copy()

        new_entry = ContinuityEntry(
            summary=summary,
            timestamp=time.time(),
            source=source,
            tags=["sleep_evolution"],
        )
        entries.append(new_entry)

        self.persistence.update_assistant(continuity_log=entries)
        self._changes.append(summary)
        logger.debug("Identity evolution: %s", summary)
