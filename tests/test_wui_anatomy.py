"""Tests for #186 anatomical visualization assets."""

from __future__ import annotations

from openbad.wui.server import STATIC_DIR


def test_index_contains_anatomy_svg():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "anatomy-map" in html
    assert "organ-cognitive" in html
    assert "organ-endocrine" in html
    assert "organ-reflex" in html
    assert "organ-immune" in html
    assert "organ-memory" in html
    assert "organ-sensory" in html
    assert "organ-nervous" in html


def test_js_contains_topic_to_organ_mapping():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "function mapTopicToOrgan" in js
    assert "agent/cognitive/" in js
    assert "agent/endocrine/" in js
    assert "agent/reflex/" in js
    assert "agent/immune/" in js
    assert "els.inference.health.textContent = 'inactive'" in js


def test_css_contains_pulse_style():
    css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
    assert ".organ.pulse" in css
    assert "drop-shadow" in css


def test_index_uses_neutral_health_placeholder():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="i-health">--<' in html
    assert '<span>Providers</span>' in html


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
