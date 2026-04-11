"""Tests for assistant identity onboarding interview logic."""

from __future__ import annotations

import pytest

from openbad.identity.assistant_profile import AssistantProfile, RhetoricalStyle
from openbad.identity.onboarding import (
    INTERVIEW_SYSTEM_PROMPT,
    apply_interview_result,
    extract_profile_from_json,
    is_assistant_configured,
)


def test_is_assistant_configured_default_profile_not_configured():
    profile = AssistantProfile()
    assert not is_assistant_configured(profile)


def test_is_assistant_configured_default_name_only_not_configured():
    profile = AssistantProfile(
        name="OpenBaD",
        persona_summary="A self-aware Linux agent with biological metaphors",
    )
    assert not is_assistant_configured(profile)


def test_is_assistant_configured_custom_name_is_configured():
    profile = AssistantProfile(name="Ada")
    assert is_assistant_configured(profile)


def test_is_assistant_configured_custom_persona_is_configured():
    profile = AssistantProfile(
        name="OpenBaD",
        persona_summary="A helpful coding assistant focused on Python",
    )
    assert is_assistant_configured(profile)


def test_is_assistant_configured_with_learning_focus_is_configured():
    profile = AssistantProfile(learning_focus=["machine learning", "rust"])
    assert is_assistant_configured(profile)


def test_is_assistant_configured_with_worldview_is_configured():
    profile = AssistantProfile(worldview=["measure twice, cut once"])
    assert is_assistant_configured(profile)


def test_is_assistant_configured_with_boundaries_is_configured():
    profile = AssistantProfile(boundaries=["no dark patterns"])
    assert is_assistant_configured(profile)


def test_interview_system_prompt_is_string():
    assert isinstance(INTERVIEW_SYSTEM_PROMPT, str)
    assert len(INTERVIEW_SYSTEM_PROMPT) > 100


def test_extract_profile_from_json_valid_markdown_fence():
    text = """
Here's what I learned:

```json
{
  "name": "Ada",
  "persona_summary": "Helpful coding assistant",
  "learning_focus": ["Python", "JavaScript"],
  "worldview": ["test everything"],
  "boundaries": ["no tracking"],
  "rhetorical_tone": "direct",
  "rhetorical_sentence_pattern": "concise",
  "rhetorical_challenge_mode": "steel-man first",
  "rhetorical_explanation_depth": "balanced",
  "openness": 0.8,
  "conscientiousness": 0.9,
  "extraversion": 0.6,
  "agreeableness": 0.5,
  "stability": 0.7
}
```

Does this look right?
"""
    result = extract_profile_from_json(text)
    assert result is not None
    assert result["name"] == "Ada"
    assert result["persona_summary"] == "Helpful coding assistant"
    assert result["learning_focus"] == ["Python", "JavaScript"]
    assert result["worldview"] == ["test everything"]
    assert result["boundaries"] == ["no tracking"]
    assert result["rhetorical_tone"] == "direct"
    assert result["openness"] == 0.8
    assert result["conscientiousness"] == 0.9


def test_extract_profile_from_json_valid_without_fence():
    text = """
{"name": "Bob", "persona_summary": "Research assistant", "learning_focus": [], "worldview": [], "boundaries": [], "openness": 0.5, "conscientiousness": 0.5, "extraversion": 0.5, "agreeableness": 0.5, "stability": 0.5}
"""
    result = extract_profile_from_json(text)
    assert result is not None
    assert result["name"] == "Bob"
    assert result["persona_summary"] == "Research assistant"


def test_extract_profile_from_json_missing_name_returns_none():
    text = """
```json
{
  "persona_summary": "Missing name field",
  "learning_focus": []
}
```
"""
    result = extract_profile_from_json(text)
    assert result is None


def test_extract_profile_from_json_no_json_returns_none():
    text = "This is just regular text without any JSON."
    result = extract_profile_from_json(text)
    assert result is None


def test_extract_profile_from_json_invalid_json_returns_none():
    text = """
```json
{
  "name": "Broken",
  "learning_focus": [unquoted string],
}
```
"""
    result = extract_profile_from_json(text)
    assert result is None


def test_apply_interview_result_updates_name():
    profile = AssistantProfile(name="OpenBaD")
    extracted = {
        "name": "Ava",
        "persona_summary": "",
        "learning_focus": [],
        "worldview": [],
        "boundaries": [],
    }
    updated = apply_interview_result(profile, extracted)
    assert updated.name == "Ava"


def test_apply_interview_result_updates_persona_summary():
    profile = AssistantProfile()
    extracted = {
        "name": "OpenBaD",
        "persona_summary": "A proactive research assistant",
        "learning_focus": [],
        "worldview": [],
        "boundaries": [],
    }
    updated = apply_interview_result(profile, extracted)
    assert updated.persona_summary == "A proactive research assistant"


def test_apply_interview_result_updates_learning_focus():
    profile = AssistantProfile()
    extracted = {
        "name": "OpenBaD",
        "learning_focus": ["cryptography", "distributed systems"],
        "worldview": [],
        "boundaries": [],
    }
    updated = apply_interview_result(profile, extracted)
    assert updated.learning_focus == ["cryptography", "distributed systems"]


def test_apply_interview_result_updates_ocean_sliders():
    profile = AssistantProfile(
        openness=0.5,
        conscientiousness=0.5,
        extraversion=0.5,
        agreeableness=0.5,
        stability=0.5,
    )
    extracted = {
        "name": "OpenBaD",
        "learning_focus": [],
        "worldview": [],
        "boundaries": [],
        "openness": 0.9,
        "conscientiousness": 0.2,
        "extraversion": 0.7,
        "agreeableness": 0.3,
        "stability": 0.8,
    }
    updated = apply_interview_result(profile, extracted)
    assert updated.openness == 0.9
    assert updated.conscientiousness == 0.2
    assert updated.extraversion == 0.7
    assert updated.agreeableness == 0.3
    assert updated.stability == 0.8


def test_apply_interview_result_updates_rhetorical_style():
    profile = AssistantProfile()
    extracted = {
        "name": "OpenBaD",
        "learning_focus": [],
        "worldview": [],
        "boundaries": [],
        "rhetorical_tone": "diplomatic",
        "rhetorical_sentence_pattern": "verbose",
        "rhetorical_challenge_mode": "socratic",
        "rhetorical_explanation_depth": "thorough",
    }
    updated = apply_interview_result(profile, extracted)
    assert updated.rhetorical_style.tone == "diplomatic"
    assert updated.rhetorical_style.sentence_pattern == "verbose"
    assert updated.rhetorical_style.challenge_mode == "socratic"
    assert updated.rhetorical_style.explanation_depth == "thorough"


def test_apply_interview_result_preserves_unspecified_fields():
    profile = AssistantProfile(
        name="OpenBaD",
        opinions={"ai_ethics": ["transparency is key"]},
        vocabulary={"greeting": "Hi there"},
        influences=["Alan Turing"],
        anti_patterns=["anthropomorphism"],
        current_focus=["performance optimization"],
    )
    extracted = {
        "name": "Sage",
        "learning_focus": ["philosophy"],
        "worldview": [],
        "boundaries": [],
    }
    updated = apply_interview_result(profile, extracted)
    assert updated.name == "Sage"
    assert updated.learning_focus == ["philosophy"]
    assert updated.opinions == {"ai_ethics": ["transparency is key"]}
    assert updated.vocabulary == {"greeting": "Hi there"}
    assert updated.influences == ["Alan Turing"]
    assert updated.anti_patterns == ["anthropomorphism"]
    assert updated.current_focus == ["performance optimization"]


def test_apply_interview_result_handles_non_list_learning_focus():
    profile = AssistantProfile()
    extracted = {
        "name": "OpenBaD",
        "learning_focus": "not a list",
        "worldview": [],
        "boundaries": [],
    }
    updated = apply_interview_result(profile, extracted)
    assert updated.learning_focus == []


def test_apply_interview_result_handles_missing_ocean_values():
    profile = AssistantProfile(
        openness=0.6,
        conscientiousness=0.7,
        extraversion=0.4,
        agreeableness=0.3,
        stability=0.5,
    )
    extracted = {
        "name": "OpenBaD",
        "learning_focus": [],
        "worldview": [],
        "boundaries": [],
        # No OCEAN values
    }
    updated = apply_interview_result(profile, extracted)
    assert updated.openness == 0.6
    assert updated.conscientiousness == 0.7
    assert updated.extraversion == 0.4
    assert updated.agreeableness == 0.3
    assert updated.stability == 0.5
