"""Tests for navigation shell, routing, and first-run wizard (#255)."""

from __future__ import annotations

from pathlib import Path

_WUI = Path(__file__).resolve().parent.parent / "wui-svelte"
LAYOUT = _WUI / "src" / "routes" / "+layout.svelte"
CHAT = _WUI / "src" / "routes" / "chat" / "+page.svelte"
PROVIDERS = _WUI / "src" / "routes" / "providers" / "+page.svelte"
HEALTH = _WUI / "src" / "routes" / "health" / "+page.svelte"


def _text() -> str:
    return LAYOUT.read_text(encoding="utf-8")


def _providers_text() -> str:
    return PROVIDERS.read_text(encoding="utf-8")


def _chat_text() -> str:
    return CHAT.read_text(encoding="utf-8")


def _health_text() -> str:
    return HEALTH.read_text(encoding="utf-8")


def test_layout_exists():
    assert LAYOUT.exists()


# -- Sidebar nav --

def test_nav_items():
    t = _text()
    for label in ("Health", "Chat", "Providers",
                  "Senses", "Toolbelt", "Entity"):
        assert label in t


def test_nav_icons():
    t = _text()
    assert "nav-icon" in t


def test_nav_labels():
    t = _text()
    assert "nav-label" in t


# -- Active route highlighting --

def test_active_route():
    t = _text()
    assert "isActive" in t
    assert "active" in t.lower()


# -- Top bar --

def test_top_bar():
    t = _text()
    assert "top-bar" in t
    assert "OpenBaD" in t


def test_connection_status():
    t = _text()
    assert "wsStatus" in t
    assert "ws-dot" in t


def test_fsm_badge_in_topbar():
    t = _text()
    assert "fsmState" in t
    assert "fsm-chip" in t


# -- Responsive hamburger --

def test_hamburger_menu():
    t = _text()
    assert "hamburger" in t
    assert "toggleSidebar" in t


def test_sidebar_collapse_responsive():
    t = _text()
    assert "@media" in t
    assert "768px" in t


# -- First-run wizard --

def test_wizard_overlay():
    t = _text()
    assert "wizard-overlay" in t
    assert "showWizard" in t


def test_wizard_four_steps():
    t = _text()
    assert "WIZARD_STEPS" in t
    assert "User Profile" in t
    assert "Assistant Personality" in t
    assert "Provider Setup" in t
    assert "Senses Check" in t


def test_wizard_step_progression():
    t = _text()
    assert "nextStep" in t
    assert "prevStep" in t
    assert "wizardStep" in t


def test_wizard_skip_button():
    t = _text()
    assert "skipWizard" in t
    assert "Skip" in t


def test_wizard_finish_saves():
    t = _text()
    assert "finishWizard" in t
    assert "/api/setup" in t


def test_wizard_detects_first_run():
    t = _text()
    assert "resolveOnboardingRedirect" in t
    assert "checkOnboarding" in t


def test_layout_checks_full_onboarding_status():
    t = _text()
    assert "resolveOnboardingRedirect" in t
    assert "currentRoute" in t


def test_providers_route_honors_wizard_query_params():
    t = _providers_text()
    assert "searchParams.get('wizard')" in t
    assert "searchParams.get('onboarding')" in t
    assert "openSleepWizard" in t


def test_health_route_advances_onboarding_after_sleep_save():
    t = _health_text()
    assert "resolveOnboardingRedirect" in t
    assert "goto(" in t
    assert "onboardingHint === 'sleep'" in t


def test_chat_route_handles_identity_onboarding():
    t = _chat_text()
    assert "onboardingHint" in t
    assert "ensureOnboardingIntro" in t
    assert "finalizeOnboardingInterview" in t
    assert "/api/onboarding/assistant/complete" in t
    assert "/api/onboarding/user/complete" in t


def test_chat_route_consumes_completion_json_and_resets_between_stages():
    t = _chat_text()
    assert "extractOnboardingCompletionPayload" in t
    assert "resetOnboardingConversation" in t
    assert "currentSessionStorageKey" in t
    assert "`${CHAT_SESSION_STORAGE_KEY}.${onboardingHint}`" in t
    assert "if (onboardingPayload)" in t
    assert "Moving on to your profile." in t
    assert "Onboarding is complete." in t


# -- Wizard step content --

def test_wizard_user_step():
    t = _text()
    assert "wUser" in t
    assert "communication_style" in t


def test_wizard_ocean_step():
    t = _text()
    assert "wAssistant" in t
    assert "openness" in t


def test_wizard_provider_step():
    t = _text()
    assert "wProvider" in t


def test_wizard_senses_step():
    t = _text()
    assert "wSenses" in t
    assert "vision" in t
    assert "hearing" in t
