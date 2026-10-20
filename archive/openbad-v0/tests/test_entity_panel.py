"""Tests for SvelteKit Entity panel (#252)."""

from __future__ import annotations

from pathlib import Path

_WUI = Path(__file__).resolve().parent.parent / "wui-svelte"
PAGE = _WUI / "src" / "routes" / "entity" / "+page.svelte"


def _text() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_entity_page_exists():
    assert PAGE.exists()


# -- Tabs --

def test_user_tab():
    t = _text()
    assert "User" in t
    assert "tab === 'user'" in t


def test_assistant_tab():
    t = _text()
    assert "Assistant" in t
    assert "tab === 'assistant'" in t


# -- User profile fields --

def test_user_name_fields():
    t = _text()
    assert "user.name" in t
    assert "preferred_name" in t


def test_communication_style():
    t = _text()
    assert "communication_style" in t
    assert "casual" in t
    assert "formal" in t


def test_expertise_domains():
    t = _text()
    assert "expertise_domains" in t
    assert "addDomain" in t
    assert "removeDomain" in t


# -- User learned summary --

def test_learned_summary_readonly():
    t = _text()
    assert "learned_summary" in t
    assert "read-only" in t.lower()


# -- Assistant profile --

def test_assistant_name_persona():
    t = _text()
    assert "assistant.name" in t
    assert "persona_summary" in t


def test_learning_focus():
    t = _text()
    assert "learning_focus" in t


# -- OCEAN sliders --

def test_ocean_sliders():
    t = _text()
    for trait in ("openness", "conscientiousness",
                  "extraversion", "agreeableness", "stability"):
        assert trait in t


def test_ocean_range_inputs():
    t = _text()
    assert 'type="range"' in t
    assert 'min="0"' in t
    assert 'max="1"' in t


def test_ocean_live_behavior_text():
    t = _text()
    assert "oceanDesc" in t
    assert "Exploratory" in t
    assert "Conventional" in t


# -- Modulation factors --

def test_modulation_factors_display():
    t = _text()
    assert "modulation" in t
    assert "exploration_budget_multiplier" in t
    assert "cortisol_decay_multiplier" in t


# -- Save and Reset --

def test_save_user():
    t = _text()
    assert "saveUser" in t
    assert "/api/entity/user" in t


def test_save_assistant():
    t = _text()
    assert "saveAssistant" in t
    assert "/api/entity/assistant" in t


def test_reset_to_seed():
    t = _text()
    assert "resetUser" in t
    assert "resetAssistant" in t
    assert "Reset to Seed" in t


# -- Responsive layout --

def test_responsive_ocean():
    t = _text()
    assert "flex-wrap" in t


# -- Form validation --

def test_dirty_tracking():
    t = _text()
    assert "userDirty" in t
    assert "assistantDirty" in t
