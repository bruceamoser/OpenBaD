"""Prompt templates for LLM-backed sleep consolidation passes."""

from __future__ import annotations

SUMMARIZE_BATCH = (
    "You are a memory consolidation assistant. Below are several related "
    "episodic memory entries grouped by topic. Produce a single concise "
    "semantic summary (2-4 sentences) that captures ALL key facts, entities, "
    "outcomes, and causal relationships from these entries.\n\n"
    "Entries:\n{entries}\n\n"
    "Summary:"
)

SUMMARIZE_SINGLE = (
    "You are a memory consolidation assistant. Summarize this memory entry "
    "for long-term recall in 1-2 sentences. Preserve key facts, entities, "
    "and outcomes.\n\n"
    "Entry:\n{entry}\n\n"
    "Summary:"
)

EXTRACT_TAGS = (
    "Extract up to 5 short retrieval tags for this summary. "
    "Return a comma-separated list only.\n\n"
    "Summary:\n{summary}\n\n"
    "Tags:"
)

SCORE_IMPORTANCE = (
    "Score the long-term importance of this summary from 0.0 to 1.0. "
    "Consider: uniqueness of information, actionability, emotional "
    "significance, and relevance to recurring tasks. "
    "Return only the numeric score.\n\n"
    "Summary:\n{summary}\n\n"
    "Score:"
)
