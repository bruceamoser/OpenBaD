"""Tests for onboarding interview logic."""

from openbad.identity.assistant_profile import AssistantProfile
from openbad.identity.onboarding import (
    INTERVIEW_SYSTEM_PROMPT,
    USER_INTERVIEW_SYSTEM_PROMPT,
    apply_interview_result,
    apply_user_interview_result,
    extract_profile_from_json,
    extract_user_profile_from_json,
    is_assistant_configured,
    is_user_configured,
)
from openbad.identity.user_profile import CommunicationStyle, UserProfile


def test_assistant_interview_prompt_mentions_entity_page_adjustments():
    assert "Entity page" in INTERVIEW_SYSTEM_PROMPT


def test_assistant_interview_prompt_preserves_user_supplied_identity_details():
    assert "do not ask for those same fields again" in INTERVIEW_SYSTEM_PROMPT
    assert "assistant's identity separate from the user's identity" in INTERVIEW_SYSTEM_PROMPT
    assert "Do not restart the interview" in INTERVIEW_SYSTEM_PROMPT
    assert "Preferred procedure" in INTERVIEW_SYSTEM_PROMPT
    assert "respond with the final JSON immediately" in INTERVIEW_SYSTEM_PROMPT


def test_user_interview_prompt_mentions_entity_page_adjustments():
    assert "Entity page" in USER_INTERVIEW_SYSTEM_PROMPT

# ── Assistant configuration detection ────────────────────────────────


def test_is_assistant_configured_returns_false_for_default():
    profile = AssistantProfile(name="OpenBaD")
    assert not is_assistant_configured(profile)


def test_is_assistant_configured_returns_false_for_default_persona():
    profile = AssistantProfile(
        name="OpenBaD",
        persona_summary="A self-aware Linux agent",
    )
    assert not is_assistant_configured(profile)


def test_is_assistant_configured_returns_true_for_custom_name():
    profile = AssistantProfile(name="Cortex")
    assert is_assistant_configured(profile)


def test_is_assistant_configured_returns_true_for_custom_persona():
    profile = AssistantProfile(
        name="OpenBaD",
        persona_summary="A helpful coding assistant",
    )
    assert is_assistant_configured(profile)


def test_is_assistant_configured_returns_true_for_learning_focus():
    profile = AssistantProfile(name="OpenBaD", learning_focus=["Python", "DevOps"])
    assert is_assistant_configured(profile)


def test_is_assistant_configured_returns_true_for_worldview():
    profile = AssistantProfile(name="OpenBaD", worldview=["Simplicity first"])
    assert is_assistant_configured(profile)


def test_is_assistant_configured_returns_true_for_boundaries():
    profile = AssistantProfile(name="OpenBaD", boundaries=["No code obfuscation"])
    assert is_assistant_configured(profile)


# ── Assistant profile JSON extraction ────────────────────────────────


def test_extract_profile_from_json_with_markdown_fence():
    text = """Here's the configuration:
```json
{
  "name": "Athena",
  "persona_summary": "A knowledge-focused assistant",
  "learning_focus": ["AI", "Philosophy"],
  "openness": 0.9
}
```
"""
    result = extract_profile_from_json(text)
    assert result is not None
    assert result["name"] == "Athena"
    assert result["persona_summary"] == "A knowledge-focused assistant"
    assert result["learning_focus"] == ["AI", "Philosophy"]
    assert result["openness"] == 0.9


def test_extract_profile_from_json_with_raw_json():
    text = """The profile is: {"name": "Nova", "persona_summary": "Brief assistant"}"""
    result = extract_profile_from_json(text)
    assert result is not None
    assert result["name"] == "Nova"


def test_extract_profile_from_json_returns_none_for_missing_name():
    text = """```json
{
  "persona_summary": "No name"
}
```"""
    result = extract_profile_from_json(text)
    assert result is None


def test_extract_profile_from_json_returns_none_for_no_json():
    text = "Just regular text without any JSON"
    result = extract_profile_from_json(text)
    assert result is None


def test_extract_profile_from_json_returns_none_for_invalid_json():
    text = """```json
{
  "name": "Broken
  "missing": "quotes"
}
```"""
    result = extract_profile_from_json(text)
    assert result is None


def test_extract_profile_from_json_with_nested_arrays():
    text = """```json
{
  "name": "Test",
  "learning_focus": ["topic1", "topic2", "topic3"],
  "worldview": ["belief1", "belief2"]
}
```"""
    result = extract_profile_from_json(text)
    assert result is not None
    assert len(result["learning_focus"]) == 3
    assert len(result["worldview"]) == 2


# ── Assistant profile application ─────────────────────────────────────


def test_apply_interview_result_updates_name():
    profile = AssistantProfile(name="OpenBaD")
    extracted = {"name": "Echo"}
    result = apply_interview_result(profile, extracted)
    assert result.name == "Echo"


def test_apply_interview_result_updates_persona():
    profile = AssistantProfile(name="Test")
    extracted = {"name": "Test", "persona_summary": "Updated summary"}
    result = apply_interview_result(profile, extracted)
    assert result.persona_summary == "Updated summary"


def test_apply_interview_result_updates_learning_focus():
    profile = AssistantProfile(name="Test")
    extracted = {"name": "Test", "learning_focus": ["Python", "Rust"]}
    result = apply_interview_result(profile, extracted)
    assert result.learning_focus == ["Python", "Rust"]


def test_apply_interview_result_updates_ocean_sliders():
    profile = AssistantProfile(name="Test", openness=0.5, conscientiousness=0.5)
    extracted = {
        "name": "Test",
        "openness": 0.8,
        "conscientiousness": 0.7,
        "extraversion": 0.6,
    }
    result = apply_interview_result(profile, extracted)
    assert result.openness == 0.8
    assert result.conscientiousness == 0.7
    assert result.extraversion == 0.6


def test_apply_interview_result_updates_rhetorical_style():
    profile = AssistantProfile(name="Test")
    extracted = {
        "name": "Test",
        "rhetorical_tone": "formal",
        "rhetorical_sentence_pattern": "verbose",
    }
    result = apply_interview_result(profile, extracted)
    assert result.rhetorical_style.tone == "formal"
    assert result.rhetorical_style.sentence_pattern == "verbose"


def test_apply_interview_result_preserves_unspecified_fields():
    profile = AssistantProfile(
        name="Test",
        persona_summary="Original",
        learning_focus=["Original"],
        openness=0.5,
    )
    extracted = {"name": "Test", "persona_summary": "Updated"}
    result = apply_interview_result(profile, extracted)
    assert result.persona_summary == "Updated"
    assert result.learning_focus == ["Original"]  # preserved
    assert result.openness == 0.5  # preserved


def test_apply_interview_result_handles_non_list_gracefully():
    profile = AssistantProfile(name="Test")
    extracted = {"name": "Test", "learning_focus": "not a list"}
    result = apply_interview_result(profile, extracted)
    assert result.learning_focus == []


def test_apply_interview_result_handles_missing_values():
    profile = AssistantProfile(name="Test", persona_summary="Original")
    extracted = {"name": "Updated"}
    result = apply_interview_result(profile, extracted)
    assert result.name == "Updated"
    assert result.persona_summary == "Original"  # preserved when not in extracted


# ── User configuration detection ──────────────────────────────────────


def test_is_user_configured_returns_false_for_default():
    profile = UserProfile(name="User")
    assert not is_user_configured(profile)


def test_is_user_configured_returns_true_for_custom_name():
    profile = UserProfile(name="Alice")
    assert is_user_configured(profile)


def test_is_user_configured_returns_true_for_expertise():
    profile = UserProfile(name="User", expertise_domains=["Python", "DevOps"])
    assert is_user_configured(profile)


def test_is_user_configured_returns_true_for_projects():
    profile = UserProfile(name="User", active_projects=["Project X"])
    assert is_user_configured(profile)


def test_is_user_configured_returns_true_for_interests():
    profile = UserProfile(name="User", interests=["Music", "Hiking"])
    assert is_user_configured(profile)


def test_is_user_configured_returns_true_for_worldview():
    profile = UserProfile(name="User", worldview=["Pragmatic"])
    assert is_user_configured(profile)


def test_is_user_configured_returns_true_for_timezone():
    profile = UserProfile(name="User", timezone="America/New_York")
    assert is_user_configured(profile)


def test_is_user_configured_returns_true_for_pet_peeves():
    profile = UserProfile(name="User", pet_peeves=["Verbose code"])
    assert is_user_configured(profile)


# ── User profile JSON extraction ──────────────────────────────────────


def test_extract_user_profile_from_json_with_markdown_fence():
    text = """```json
{
  "name": "Bob",
  "preferred_name": "Bobby",
  "communication_style": "casual",
  "expertise_domains": ["Backend", "Database"],
  "timezone": "America/Chicago"
}
```"""
    result = extract_user_profile_from_json(text)
    assert result is not None
    assert result["name"] == "Bob"
    assert result["preferred_name"] == "Bobby"
    assert result["communication_style"] == "casual"
    assert result["timezone"] == "America/Chicago"


def test_extract_user_profile_from_json_with_raw_json():
    text = """{"name": "Carol", "interests": ["Reading"]}"""
    result = extract_user_profile_from_json(text)
    assert result is not None
    assert result["name"] == "Carol"


def test_extract_user_profile_from_json_returns_none_for_missing_name():
    text = """```json
{
  "preferred_name": "No name field"
}
```"""
    result = extract_user_profile_from_json(text)
    assert result is None


def test_extract_user_profile_from_json_returns_none_for_no_json():
    text = "No JSON here"
    result = extract_user_profile_from_json(text)
    assert result is None


def test_extract_user_profile_from_json_with_work_hours():
    text = """```json
{
  "name": "Dave",
  "work_hours": [8, 16],
  "timezone": "UTC"
}
```"""
    result = extract_user_profile_from_json(text)
    assert result is not None
    assert result["work_hours"] == [8, 16]


# ── User profile application ──────────────────────────────────────────


def test_apply_user_interview_result_updates_name():
    profile = UserProfile(name="User")
    extracted = {"name": "Alice"}
    result = apply_user_interview_result(profile, extracted)
    assert result.name == "Alice"


def test_apply_user_interview_result_updates_preferred_name():
    profile = UserProfile(name="Alice")
    extracted = {"name": "Alice", "preferred_name": "Ali"}
    result = apply_user_interview_result(profile, extracted)
    assert result.preferred_name == "Ali"


def test_apply_user_interview_result_updates_communication_style():
    profile = UserProfile(name="User")
    extracted = {"name": "User", "communication_style": "formal"}
    result = apply_user_interview_result(profile, extracted)
    assert result.communication_style == CommunicationStyle.FORMAL


def test_apply_user_interview_result_updates_expertise_domains():
    profile = UserProfile(name="User")
    extracted = {"name": "User", "expertise_domains": ["ML", "NLP"]}
    result = apply_user_interview_result(profile, extracted)
    assert result.expertise_domains == ["ML", "NLP"]


def test_apply_user_interview_result_updates_active_projects():
    profile = UserProfile(name="User")
    extracted = {"name": "User", "active_projects": ["OpenBaD", "WebApp"]}
    result = apply_user_interview_result(profile, extracted)
    assert result.active_projects == ["OpenBaD", "WebApp"]


def test_apply_user_interview_result_updates_interests():
    profile = UserProfile(name="User")
    extracted = {"name": "User", "interests": ["Photography", "Travel"]}
    result = apply_user_interview_result(profile, extracted)
    assert result.interests == ["Photography", "Travel"]


def test_apply_user_interview_result_updates_pet_peeves():
    profile = UserProfile(name="User")
    extracted = {"name": "User", "pet_peeves": ["Slow code reviews"]}
    result = apply_user_interview_result(profile, extracted)
    assert result.pet_peeves == ["Slow code reviews"]


def test_apply_user_interview_result_updates_worldview():
    profile = UserProfile(name="User")
    extracted = {"name": "User", "worldview": ["Simplicity", "Transparency"]}
    result = apply_user_interview_result(profile, extracted)
    assert result.worldview == ["Simplicity", "Transparency"]


def test_apply_user_interview_result_updates_feedback_style():
    profile = UserProfile(name="User")
    extracted = {"name": "User", "preferred_feedback_style": "thorough"}
    result = apply_user_interview_result(profile, extracted)
    assert result.preferred_feedback_style == "thorough"


def test_apply_user_interview_result_updates_timezone():
    profile = UserProfile(name="User")
    extracted = {"name": "User", "timezone": "Europe/London"}
    result = apply_user_interview_result(profile, extracted)
    assert result.timezone == "Europe/London"


def test_apply_user_interview_result_updates_work_hours():
    profile = UserProfile(name="User", work_hours=(9, 17))
    extracted = {"name": "User", "work_hours": [10, 18]}
    result = apply_user_interview_result(profile, extracted)
    assert result.work_hours == (10, 18)


def test_apply_user_interview_result_preserves_unspecified_fields():
    profile = UserProfile(
        name="User",
        expertise_domains=["Original"],
        interests=["Music"],
        timezone="UTC",
    )
    extracted = {"name": "User", "timezone": "America/Denver"}
    result = apply_user_interview_result(profile, extracted)
    assert result.timezone == "America/Denver"
    assert result.expertise_domains == ["Original"]  # preserved
    assert result.interests == ["Music"]  # preserved


def test_apply_user_interview_result_handles_non_list_gracefully():
    profile = UserProfile(name="User")
    extracted = {"name": "User", "interests": "not a list"}
    result = apply_user_interview_result(profile, extracted)
    assert result.interests == []


def test_apply_user_interview_result_handles_invalid_communication_style():
    profile = UserProfile(name="User", communication_style=CommunicationStyle.CASUAL)
    extracted = {"name": "User", "communication_style": "invalid"}
    result = apply_user_interview_result(profile, extracted)
    assert result.communication_style == CommunicationStyle.CASUAL  # preserved


def test_apply_user_interview_result_handles_invalid_work_hours():
    profile = UserProfile(name="User", work_hours=(9, 17))
    extracted = {"name": "User", "work_hours": "invalid"}
    result = apply_user_interview_result(profile, extracted)
    assert result.work_hours == (9, 17)  # preserved
