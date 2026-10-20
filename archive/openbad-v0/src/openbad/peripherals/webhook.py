"""Corsair webhook ingress bridge.

Receives HTTP webhook callbacks from the Corsair sidecar and publishes
them to the MQTT nervous system at ``sensory/external/{platform}/inbound``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

from aiohttp import web

from openbad.nervous_system import topics
from openbad.peripherals.config import load_peripherals_config

log = logging.getLogger(__name__)


def _verify_hmac(
    body: bytes,
    signature: str,
    secret: str,
) -> bool:
    """Validate an HMAC-SHA256 signature header.

    The expected format of *signature* is ``sha256=<hex-digest>``.
    Returns ``True`` when the signature is valid.
    """
    if not secret:
        # No secret configured — skip validation (dev mode).
        return True
    if not signature.startswith("sha256="):
        return False
    expected = hmac.new(
        secret.encode(), body, hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature[7:])


async def post_webhook_corsair(request: web.Request) -> web.Response:
    """Handle ``POST /api/webhooks/corsair``.

    Expected JSON body::

        {
            "platform": "discord",
            "event": "message.created",
            "data": { ... platform-specific payload ... }
        }

    Validated via HMAC-SHA256 if ``corsair.webhook_secret`` is set.
    """
    # --- Signature validation ------------------------------------------------
    cfg = load_peripherals_config()
    body = await request.read()
    signature = request.headers.get("X-Corsair-Signature", "")

    if not _verify_hmac(body, signature, cfg.webhook_secret):
        log.warning("Webhook signature mismatch from %s", request.remote)
        return web.json_response(
            {"error": "Invalid signature"}, status=401,
        )

    # --- Parse payload -------------------------------------------------------
    try:
        payload: dict[str, Any] = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return web.json_response(
            {"error": "Malformed JSON body"}, status=400,
        )

    platform = payload.get("platform", "")
    if not platform or not isinstance(platform, str):
        return web.json_response(
            {"error": "Missing or invalid 'platform' field"}, status=400,
        )

    # --- Publish to MQTT -----------------------------------------------------
    mqtt = request.app.get("bridge")
    if mqtt is None:
        log.error("MQTT bridge not available in app context")
        return web.json_response(
            {"error": "MQTT bridge unavailable"}, status=503,
        )

    topic = topics.topic_for(
        topics.EXTERNAL_INBOUND, platform=platform,
    )
    mqtt_payload = json.dumps({
        "platform": platform,
        "event": payload.get("event", "unknown"),
        "data": payload.get("data", {}),
        "received_at": time.time(),
    }).encode()

    try:
        mqtt.publish_raw(topic, mqtt_payload, qos=1)
    except Exception:
        # Fallback: some bridge impls use a different method name.
        try:
            from openbad.nervous_system.client import NervousSystemClient

            client = NervousSystemClient.get_instance()
            client._mqtt.publish(topic, mqtt_payload, qos=1)
        except Exception as exc:
            log.exception("Failed to publish webhook to MQTT: %s", exc)
            return web.json_response(
                {"error": "Failed to publish to MQTT"}, status=503,
            )

    log.info("Webhook published: %s → %s", platform, topic)
    return web.json_response({"ok": True, "topic": topic})


def setup_webhook_routes(app: web.Application) -> None:
    """Register webhook routes on the aiohttp application."""
    app.router.add_post("/api/webhooks/corsair", post_webhook_corsair)
