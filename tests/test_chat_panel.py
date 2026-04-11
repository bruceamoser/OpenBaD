"""Tests for SvelteKit Chat panel (#253)."""

from __future__ import annotations

from pathlib import Path

_WUI = Path(__file__).resolve().parent.parent / "wui-svelte"
PAGE = _WUI / "src" / "routes" / "chat" / "+page.svelte"


def _text() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_chat_page_exists():
    assert PAGE.exists()


# -- Message list --

def test_message_bubbles():
    t = _text()
    assert "msg.role" in t or "msg role" in t or 'class="msg' in t
    assert "user" in t
    assert "assistant" in t


def test_message_timestamps():
    t = _text()
    assert "timestamp" in t


# -- Streaming --

def test_streaming_token_display():
    t = _text()
    assert "streaming" in t
    assert "token" in t


def test_sse_data_parsing():
    t = _text()
    assert "data: " in t or "data:" in t
    assert "[DONE]" in t


# -- Input --

def test_input_box():
    t = _text()
    assert "textarea" in t
    assert "Send" in t


def test_shift_enter_newline():
    t = _text()
    assert "shiftKey" in t or "Shift+Enter" in t


# -- System selector --

def test_system_selector():
    t = _text()
    assert "CHAT" in t
    assert "REASONING" in t
    assert "system" in t


# -- Context budget --

def test_context_budget_indicator():
    t = _text()
    assert "tokensUsed" in t
    assert "tokensMax" in t
    assert "budget" in t.lower()


# -- Chain of thought --

def test_cot_toggle():
    t = _text()
    assert "showCot" in t
    assert "Chain of Thought" in t


def test_reasoning_trace():
    t = _text()
    assert "reasoning" in t


# -- Scroll behavior --

def test_scroll_to_bottom():
    t = _text()
    assert "scrollToBottom" in t
    assert "autoScroll" in t


# -- Responsive --

def test_responsive_layout():
    t = _text()
    assert "flex" in t
    assert "flex-wrap" in t
