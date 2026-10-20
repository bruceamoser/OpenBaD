"""Tests for the Corsair webhook ingress bridge."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web

from openbad.peripherals.webhook import (
    _verify_hmac,
    setup_webhook_routes,
)

# ── HMAC verification ────────────────────────────────────────────── #


class TestVerifyHmac:
    """Tests for _verify_hmac()."""

    def test_valid_signature(self) -> None:
        body = b'{"platform":"discord"}'
        secret = "test-secret"  # noqa: S105
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        sig = f"sha256={digest}"
        assert _verify_hmac(body, sig, secret) is True

    def test_invalid_signature(self) -> None:
        body = b'{"platform":"discord"}'
        secret = "test-secret"  # noqa: S105
        assert _verify_hmac(body, "sha256=bad", secret) is False

    def test_missing_prefix(self) -> None:
        body = b'{"platform":"discord"}'
        secret = "test-secret"  # noqa: S105
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert _verify_hmac(body, digest, secret) is False

    def test_empty_secret_skips_validation(self) -> None:
        assert _verify_hmac(b"anything", "whatever", "") is True

    def test_empty_signature_with_secret(self) -> None:
        assert _verify_hmac(b"body", "", "secret") is False


# ── Topic constants ──────────────────────────────────────────────── #


class TestTopicConstants:
    """Verify external topic templates exist in topics.py."""

    def test_external_inbound_exists(self) -> None:
        from openbad.nervous_system import topics

        assert hasattr(topics, "EXTERNAL_INBOUND")
        assert "{platform}" in topics.EXTERNAL_INBOUND

    def test_external_outbound_exists(self) -> None:
        from openbad.nervous_system import topics

        assert hasattr(topics, "EXTERNAL_OUTBOUND")
        assert "{platform}" in topics.EXTERNAL_OUTBOUND

    def test_external_inbound_all_wildcard(self) -> None:
        from openbad.nervous_system import topics

        assert hasattr(topics, "EXTERNAL_INBOUND_ALL")
        assert "+" in topics.EXTERNAL_INBOUND_ALL

    def test_peripherals_health_exists(self) -> None:
        from openbad.nervous_system import topics

        assert hasattr(topics, "PERIPHERALS_HEALTH")
        assert "{name}" in topics.PERIPHERALS_HEALTH

    def test_topic_for_resolves_inbound(self) -> None:
        from openbad.nervous_system.topics import (
            EXTERNAL_INBOUND,
            topic_for,
        )

        assert (
            topic_for(EXTERNAL_INBOUND, platform="discord")
            == "sensory/external/discord/inbound"
        )


# ── Webhook endpoint (aiohttp integration) ──────────────────────── #


@pytest.fixture()
def _no_peripherals_config(tmp_path: Path) -> Any:
    """Patch config loader to return empty (no webhook secret)."""
    from openbad.peripherals.config import CorsairConfig

    with patch(
        "openbad.peripherals.webhook.load_peripherals_config",
        return_value=CorsairConfig(),
    ):
        yield


@pytest.fixture()
def _secret_config() -> Any:
    """Patch config loader to require a specific webhook secret."""
    from openbad.peripherals.config import CorsairConfig

    with patch(
        "openbad.peripherals.webhook.load_peripherals_config",
        return_value=CorsairConfig(webhook_secret="test-secret"),  # noqa: S106
    ):
        yield


def _make_app(bridge: Any = None) -> web.Application:
    """Build a minimal aiohttp app with webhook routes."""
    app = web.Application()
    if bridge is not None:
        app["bridge"] = bridge
    setup_webhook_routes(app)
    return app


def _sign(body: bytes, secret: str) -> str:
    """Compute the HMAC-SHA256 signature header value."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class TestWebhookEndpoint:
    """Integration tests for POST /api/webhooks/corsair."""

    @pytest.mark.asyncio
    async def test_valid_payload_publishes(
        self, _no_peripherals_config: Any, aiohttp_client: Any,
    ) -> None:
        bridge = MagicMock()
        bridge.publish_raw = MagicMock()
        app = _make_app(bridge)
        client = await aiohttp_client(app)

        payload = {"platform": "discord", "event": "message", "data": {}}
        resp = await client.post(
            "/api/webhooks/corsair",
            json=payload,
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert "sensory/external/discord/inbound" in body["topic"]
        bridge.publish_raw.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_platform_returns_400(
        self, _no_peripherals_config: Any, aiohttp_client: Any,
    ) -> None:
        bridge = MagicMock()
        app = _make_app(bridge)
        client = await aiohttp_client(app)

        resp = await client.post(
            "/api/webhooks/corsair",
            json={"event": "test"},
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_malformed_json_returns_400(
        self, _no_peripherals_config: Any, aiohttp_client: Any,
    ) -> None:
        bridge = MagicMock()
        app = _make_app(bridge)
        client = await aiohttp_client(app)

        resp = await client.post(
            "/api/webhooks/corsair",
            data=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_invalid_signature_returns_401(
        self, _secret_config: Any, aiohttp_client: Any,
    ) -> None:
        bridge = MagicMock()
        app = _make_app(bridge)
        client = await aiohttp_client(app)

        resp = await client.post(
            "/api/webhooks/corsair",
            json={"platform": "discord", "event": "test"},
            headers={"X-Corsair-Signature": "sha256=bad"},
        )
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_valid_signature_passes(
        self, _secret_config: Any, aiohttp_client: Any,
    ) -> None:
        bridge = MagicMock()
        bridge.publish_raw = MagicMock()
        app = _make_app(bridge)
        client = await aiohttp_client(app)

        body = json.dumps(
            {"platform": "slack", "event": "msg", "data": {}},
        ).encode()
        sig = _sign(body, "test-secret")

        resp = await client.post(
            "/api/webhooks/corsair",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Corsair-Signature": sig,
            },
        )
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_no_bridge_returns_503(
        self, _no_peripherals_config: Any, aiohttp_client: Any,
    ) -> None:
        app = _make_app(bridge=None)  # No MQTT bridge
        client = await aiohttp_client(app)

        resp = await client.post(
            "/api/webhooks/corsair",
            json={"platform": "discord", "event": "test"},
        )
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_published_topic_matches_platform(
        self, _no_peripherals_config: Any, aiohttp_client: Any,
    ) -> None:
        bridge = MagicMock()
        bridge.publish_raw = MagicMock()
        app = _make_app(bridge)
        client = await aiohttp_client(app)

        await client.post(
            "/api/webhooks/corsair",
            json={"platform": "telegram", "event": "msg", "data": {}},
        )

        call_args = bridge.publish_raw.call_args
        topic = call_args[0][0]
        assert topic == "sensory/external/telegram/inbound"
