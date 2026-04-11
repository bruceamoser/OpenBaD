"""Onboarding interview logic for assistant identity configuration.

Provides:
- Detection of incomplete assistant identity
- Interview prompt generation
- Natural language extraction of assistant profile fields from conversation
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openbad.identity.assistant_profile import AssistantProfile

logger = logging.getLogger(__name__)


def is_assistant_configured(profile: AssistantProfile) -> bool:
    """Return whether the assistant has a non-default identity.

    An assistant is considered configured if it has a custom name
    or at least one non-empty field beyond the defaults.
    """
    if profile.name and profile.name != "OpenBaD":
        return True

    if (
        profile.persona_summary
        and profile.persona_summary.strip()
        and "self-aware Linux agent" not in profile.persona_summary
    ):
        return True

    if profile.learning_focus:
        return True

    if profile.worldview:
        return True

    return bool(profile.boundaries)
INTERVIEW_SYSTEM_PROMPT = """You are a new AI assistant being \
configured for the first time. Your job is to interview the user to establish \
your identity and personality.

Conduct a conversational interview to learn:
1. What name the user wants to call you
2. Your primary role and purpose
3. How you should communicate (formal/casual, direct/diplomatic, etc.)
4. Your personality traits (exploration style, thoroughness, proactivity, \
challenge posture, stress handling)
5. The domains you should focus on learning
6. Any boundaries or topics to avoid

Keep the interview natural and conversational. Don't make it feel like a form. \
Ask follow-up questions when answers are vague. When you have enough \
information, summarize your understanding back to the user and ask for \
confirmation.

Once confirmed, respond with a final message containing ONLY a JSON block in this exact format:
```json
{
  "name": "...",
  "persona_summary": "...",
  "learning_focus": ["domain1", "domain2"],
  "worldview": ["belief1", "belief2"],
  "boundaries": ["boundary1"],
  "rhetorical_tone": "direct|diplomatic|casual",
  "rhetorical_sentence_pattern": "concise|verbose|balanced",
  "rhetorical_challenge_mode": "steel-man first|socratic|agreeable",
  "rhetorical_explanation_depth": "terse|balanced|thorough",
  "openness": 0.7,
  "conscientiousness": 0.8,
  "extraversion": 0.5,
  "agreeableness": 0.4,
  "stability": 0.6
}
```

The JSON extraction signals that the interview is complete.
"""


def extract_profile_from_json(text: str) -> dict[str, Any] | None:
    """Extract structured assistant profile from LLM JSON output.

    Returns None if no valid JSON block found.
    """
    # Look for JSON block in markdown code fence or raw JSON
    # Use greedy match to capture full JSON object including nested arrays/objects
    json_match = re.search(r'```(?:json)?\s*(\{[^`]+\})\s*```', text, re.DOTALL)
    if not json_match:
        # Try raw JSON - match from first { with "name" to corresponding }
        json_match = re.search(r'(\{[^{}]*"name"[^{}]*\})', text, re.DOTALL)

    if not json_match:
        return None

    try:
        data = json.loads(json_match.group(1))
    except json.JSONDecodeError:
        logger.debug("Failed to parse JSON from interview response", exc_info=True)
        return None

    # Validate required fields
    if not isinstance(data.get("name"), str):
        return None

    return data


def apply_interview_result(
    profile: AssistantProfile,
    extracted: dict[str, Any],
) -> AssistantProfile:
    """Apply extracted interview data to an AssistantProfile.

    Returns a new AssistantProfile with updated fields.
    """
    name = str(extracted.get("name", profile.name)).strip() or profile.name
    persona_summary = str(extracted.get("persona_summary", "")).strip()
    learning_focus = extracted.get("learning_focus", [])
    if not isinstance(learning_focus, list):
        learning_focus = []

    worldview = extracted.get("worldview", [])
    if not isinstance(worldview, list):
        worldview = []

    boundaries = extracted.get("boundaries", [])
    if not isinstance(boundaries, list):
        boundaries = []

    # OCEAN sliders
    openness = float(extracted.get("openness", profile.openness))
    conscientiousness = float(extracted.get("conscientiousness", profile.conscientiousness))
    extraversion = float(extracted.get("extraversion", profile.extraversion))
    agreeableness = float(extracted.get("agreeableness", profile.agreeableness))
    stability = float(extracted.get("stability", profile.stability))

    # Rhetorical style
    rhetorical_style = profile.rhetorical_style
    if "rhetorical_tone" in extracted:
        rhetorical_style.tone = str(extracted["rhetorical_tone"])
    if "rhetorical_sentence_pattern" in extracted:
        rhetorical_style.sentence_pattern = str(extracted["rhetorical_sentence_pattern"])
    if "rhetorical_challenge_mode" in extracted:
        rhetorical_style.challenge_mode = str(extracted["rhetorical_challenge_mode"])
    if "rhetorical_explanation_depth" in extracted:
        rhetorical_style.explanation_depth = str(extracted["rhetorical_explanation_depth"])

    return AssistantProfile(
        name=name,
        persona_summary=persona_summary,
        learning_focus=learning_focus,
        worldview=worldview,
        boundaries=boundaries,
        opinions=profile.opinions,
        vocabulary=profile.vocabulary,
        rhetorical_style=rhetorical_style,
        influences=profile.influences,
        anti_patterns=profile.anti_patterns,
        current_focus=profile.current_focus,
        continuity_log=profile.continuity_log,
        openness=openness,
        conscientiousness=conscientiousness,
        extraversion=extraversion,
        agreeableness=agreeableness,
        stability=stability,
    )
