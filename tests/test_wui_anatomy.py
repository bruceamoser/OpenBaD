"""Tests for the WUI control-surface assets."""

from __future__ import annotations

from openbad.wui.server import STATIC_DIR


def test_index_contains_left_nav_shell():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "side-nav" in html
    assert 'data-view-target="health"' in html
    assert 'data-view-target="chat"' in html
    assert 'data-view-target="wiring"' in html
    assert 'data-view-target="models"' in html


def test_js_contains_view_and_wiring_logic():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "function setView" in js
    assert "loadWiringConfig" in js
    assert "verifyWizardProvider" in js
    assert "saveWizardProvider" in js
    assert "/api/wiring/providers" in js
    assert "/api/wiring/providers/verify" in js


def test_css_contains_nav_and_wiring_styles():
    css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
    assert ".side-nav" in css
    assert ".provider-card" in css
    assert ".metric-cell.flash" in css


def test_index_uses_neutral_health_placeholder():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="i-health">--<' in html
    assert 'id="wiring-config-path"' in html
    assert 'id="provider-wizard"' in html
    assert 'id="copilot-user-code"' in html
    assert 'id="copilot-start-auth"' in html


def test_index_contains_plain_language_hormone_labels():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "<span class=\"label-title\">Dopamine</span>" in html
    assert "<span class=\"label-note\">reward and motivation</span>" in html
    assert "<span class=\"label-title\">Adrenaline</span>" in html
    assert "<span class=\"label-note\">fight-or-flight energy</span>" in html
    assert "<span class=\"label-title\">Cortisol</span>" in html
    assert "<span class=\"label-note\">stress load</span>" in html
    assert "<span class=\"label-title\">Endorphin</span>" in html
    assert "<span class=\"label-note\">calm and relief</span>" in html


def test_css_contains_hormone_label_layout():
    css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
    assert ".hormone-row" in css
    assert ".label-block" in css
    assert ".label-note" in css
