"""Onboarding interview logic for assistant and user identity configuration.

Provides:
- Detection of incomplete assistant/user identity
- Interview prompt generation
- Natural language extraction of profile fields from conversation
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openbad.identity.assistant_profile import AssistantProfile
from openbad.identity.user_profile import CommunicationStyle, UserProfile

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
confirmation. Explicitly tell the user that these values can be adjusted later on the Entity page.

Important interview rules:
- If the user already provides your name, role, and communication style in one message, accept that information and do not ask for those same fields again.
- Keep the assistant's identity separate from the user's identity. The assistant's name is what the user wants to call you. The user's name is not your name.
- If you ask for the user's name and they answer with a name like "Bruce", treat that as the user's name, not the assistant's name.
- If the user corrects a name mix-up, acknowledge the correction once, keep the corrected value, and continue from the remaining missing assistant fields.
- Do not restart the interview or repeat the opening prompt after a correction. Continue from the current state.
- Ask only for missing assistant-identity details. Do not re-ask for information the user already provided clearly.
- Preferred procedure:
- First, parse the user's latest message for all assistant-identity details it already contains.
- Second, restate your current understanding using the assistant name the user gave you.
- Third, ask one short follow-up question that covers only the most important missing detail, if anything is still missing.
- If the user has already given enough information, summarize and ask for confirmation instead of continuing the interview.
- When the user says the summary is correct, respond with the final JSON immediately and nothing else.

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
    persona_summary = str(extracted.get("persona_summary", profile.persona_summary)).strip()

    learning_focus = extracted.get("learning_focus", profile.learning_focus)
    if not isinstance(learning_focus, list):
        learning_focus = profile.learning_focus

    worldview = extracted.get("worldview", profile.worldview)
    if not isinstance(worldview, list):
        worldview = profile.worldview

    boundaries = extracted.get("boundaries", profile.boundaries)
    if not isinstance(boundaries, list):
        boundaries = profile.boundaries

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


# ── User profile interview ───────────────────────────────────────────


def is_user_configured(profile: UserProfile) -> bool:
    """Return whether the user has a non-default profile.

    A user is considered configured if they have a non-generic name or
    at least one populated field beyond the defaults.
    """
    if profile.name and profile.name != "User":
        return True

    if profile.expertise_domains:
        return True

    if profile.active_projects:
        return True

    if profile.interests:
        return True

    return bool(profile.worldview or profile.timezone or profile.pet_peeves)


USER_INTERVIEW_SYSTEM_PROMPT = """Now that I know who I am, I'd like to \
learn about you. This will help me serve you better. Let's have a \
conversation about:

1. Your name and what you'd like me to call you
2. Your areas of expertise and what you work on
3. Your current projects
4. Your interests outside of work
5. How you prefer to receive information (formal, casual, terse)
6. Any pet peeves or things that frustrate you
7. Principles or values that guide how you work
8. Your timezone and typical working hours

Don't worry about answering everything at once — we can fill in gaps as we go. \
Keep this natural and conversational.
Explicitly tell the user that these values can be adjusted later on the Entity page.

When we're done, I'll summarize my understanding and confirm. Once you're \
happy with it, I'll respond with a JSON block containing:
```json
{
  "name": "...",
  "preferred_name": "...",
  "communication_style": "formal|casual|terse",
  "expertise_domains": ["domain1", "domain2"],
  "active_projects": ["project1"],
  "interests": ["topic1", "topic2"],
  "pet_peeves": ["annoyance1"],
  "worldview": ["principle1"],
  "preferred_feedback_style": "balanced|thorough|terse",
  "timezone": "America/New_York",
  "work_hours": [9, 17]
}
```

The JSON extraction signals that the interview is complete.
"""


def extract_user_profile_from_json(text: str) -> dict[str, Any] | None:
    """Extract structured user profile from LLM JSON output.

    Returns None if no valid JSON block found.
    """
    # Use same regex as assistant profile extraction
    json_match = re.search(r'```(?:json)?\s*(\{[^`]+\})\s*```', text, re.DOTALL)
    if not json_match:
        json_match = re.search(r'(\{[^{}]*"name"[^{}]*\})', text, re.DOTALL)

    if not json_match:
        return None

    try:
        data = json.loads(json_match.group(1))
    except json.JSONDecodeError:
        logger.debug("Failed to parse JSON from user interview response", exc_info=True)
        return None

    # Validate required fields
    if not isinstance(data.get("name"), str):
        return None

    return data


def apply_user_interview_result(
    profile: UserProfile,
    extracted: dict[str, Any],
) -> UserProfile:
    """Apply extracted interview data to a UserProfile.

    Returns a new UserProfile with updated fields.
    """
    name = str(extracted.get("name", profile.name)).strip() or profile.name
    preferred_name = str(extracted.get("preferred_name", profile.preferred_name)).strip()

    # Communication style
    style_raw = str(extracted.get("communication_style", "casual")).lower()
    try:
        communication_style = CommunicationStyle(style_raw)
    except ValueError:
        communication_style = profile.communication_style

    # List fields
    expertise_domains = extracted.get("expertise_domains", profile.expertise_domains)
    if not isinstance(expertise_domains, list):
        expertise_domains = profile.expertise_domains

    active_projects = extracted.get("active_projects", profile.active_projects)
    if not isinstance(active_projects, list):
        active_projects = profile.active_projects

    interests = extracted.get("interests", profile.interests)
    if not isinstance(interests, list):
        interests = profile.interests

    pet_peeves = extracted.get("pet_peeves", profile.pet_peeves)
    if not isinstance(pet_peeves, list):
        pet_peeves = profile.pet_peeves

    worldview = extracted.get("worldview", profile.worldview)
    if not isinstance(worldview, list):
        worldview = profile.worldview

    # String fields
    preferred_feedback_style = str(
        extracted.get("preferred_feedback_style", profile.preferred_feedback_style)
    )
    timezone = str(extracted.get("timezone", profile.timezone))

    # Work hours
    work_hours_raw = extracted.get("work_hours", list(profile.work_hours))
    if isinstance(work_hours_raw, list) and len(work_hours_raw) == 2:
        work_hours = (int(work_hours_raw[0]), int(work_hours_raw[1]))
    else:
        work_hours = profile.work_hours

    return UserProfile(
        name=name,
        preferred_name=preferred_name,
        communication_style=communication_style,
        expertise_domains=expertise_domains,
        interaction_history_summary=profile.interaction_history_summary,
        worldview=worldview,
        interests=interests,
        pet_peeves=pet_peeves,
        preferred_feedback_style=preferred_feedback_style,
        active_projects=active_projects,
        timezone=timezone,
        work_hours=work_hours,
    )
