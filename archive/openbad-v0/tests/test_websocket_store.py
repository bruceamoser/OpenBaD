"""Tests for WebSocket store scaffold (#248)."""

from __future__ import annotations

import re
from pathlib import Path

_WUI = Path(__file__).resolve().parent.parent / "wui-svelte"
STORE = _WUI / "src" / "lib" / "stores" / "websocket.ts"


def test_websocket_store_exists():
    assert STORE.exists()


def test_exports_ws_status():
    text = STORE.read_text(encoding="utf-8")
    assert "export const wsStatus" in text


def test_exports_derived_stores():
    text = STORE.read_text(encoding="utf-8")
    assert "export const cpuTelemetry" in text
    assert "export const endocrineLevels" in text
    assert "export const fsmState" in text
    assert "export const toolbeltHealth" in text


def test_exports_connect_and_disconnect():
    text = STORE.read_text(encoding="utf-8")
    assert "export function connect" in text
    assert "export function disconnect" in text


def test_exports_send():
    text = STORE.read_text(encoding="utf-8")
    assert "export function send" in text


def test_reconnect_backoff():
    text = STORE.read_text(encoding="utf-8")
    assert "MAX_BACKOFF_MS" in text
    # Verify max is 30s
    match = re.search(r"MAX_BACKOFF_MS\s*=\s*(\d[\d_]*)", text)
    assert match is not None
    assert int(match.group(1).replace("_", "")) == 30000


def test_exponential_backoff_formula():
    text = STORE.read_text(encoding="utf-8")
    assert "2 **" in text or "Math.pow" in text


def test_ws_status_type():
    text = STORE.read_text(encoding="utf-8")
    assert "'connecting'" in text
    assert "'connected'" in text
    assert "'disconnected'" in text


def test_handle_message_exported():
    text = STORE.read_text(encoding="utf-8")
    assert "export function _handleMessage" in text


def test_envelope_type():
    text = STORE.read_text(encoding="utf-8")
    assert "topic: string" in text
    assert "payload:" in text


def test_json_parse_in_handler():
    text = STORE.read_text(encoding="utf-8")
    assert "JSON.parse" in text
