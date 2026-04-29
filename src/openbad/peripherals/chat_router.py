"""Peripheral chat router — bridges inbound peripheral messages to the chat pipeline.

Subscribes to ``sensory/external/+/inbound``, feeds each message through
:func:`~openbad.wui.chat_pipeline.stream_chat`, and publishes the
assembled reply to ``motor/external/{platform}/outbound``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from openbad.nervous_system import topics
from openbad.nervous_system.client import NervousSystemClient
from openbad.wui.chat_pipeline import stream_chat

logger = logging.getLogger(__name__)


class PeripheralChatRouter:
    """Route messages from external peripherals through the chat pipeline."""

    def __init__(
        self,
        mqtt_client: NervousSystemClient,
        model_resolver: Callable[[], tuple[Any, str, str]],
    ) -> None:
        """Initialise the router.

        Parameters
        ----------
        mqtt_client:
            The shared MQTT client for pub/sub.
        model_resolver:
            A callable returning ``(chat_model, model_id, provider_name)``
            for the current default provider.  This is called per-message
            so the router always uses the latest configuration.
        """
        self._mqtt = mqtt_client
        self._resolve_model = model_resolver
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────── #

    def start(self) -> None:
        """Subscribe to the external inbound wildcard topic."""
        if self._running:
            return
        self._running = True
        self._mqtt.subscribe(
            topics.EXTERNAL_INBOUND_ALL,
            bytes,
            self._on_inbound,
        )
        logger.info("PeripheralChatRouter started")

    def stop(self) -> None:
        """Unsubscribe from inbound messages."""
        self._running = False
        self._mqtt.unsubscribe(topics.EXTERNAL_INBOUND_ALL)
        logger.info("PeripheralChatRouter stopped")

    # ── Inbound handler ──────────────────────────────────── #

    def _on_inbound(self, topic: str, payload: bytes) -> None:
        """Dispatch inbound MQTT messages to the async handler."""
        import asyncio

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            logger.error("No event loop — cannot handle inbound message")
            return
        loop.create_task(self._handle_inbound(topic, payload))

    async def _handle_inbound(self, topic: str, payload: bytes) -> None:
        """Parse the inbound message, run through chat, and reply."""
        try:
            data = json.loads(payload.decode("utf-8"))
        except Exception:
            logger.exception("Invalid inbound payload on %s", topic)
            return

        platform = data.get("platform", "")
        event = data.get("event", "")
        msg_data = data.get("data", {})
        content = msg_data.get("content", "")
        sender = msg_data.get("sender", "")
        sender_name = msg_data.get("sender_name", "")

        if not content or event != "message":
            return

        session_id = f"peripheral:{platform}:{sender}"
        logger.info(
            "Peripheral message from %s/%s (%s): %s",
            platform, sender_name, sender, content[:80],
        )

        # Resolve the current model
        try:
            chat_model, model_id, provider_name = self._resolve_model()
        except Exception:
            logger.exception("Failed to resolve chat model")
            return

        if chat_model is None:
            logger.warning("No chat model configured — skipping peripheral message")
            return

        # Collect the streamed response
        reply_parts: list[str] = []
        try:
            async for chunk in stream_chat(
                chat_model,
                model_id,
                content,
                session_id,
                provider_name=provider_name,
                nervous_system_client=self._mqtt,
            ):
                if chunk.error:
                    logger.error("Chat pipeline error: %s", chunk.error)
                    reply_parts.append(f"[Error: {chunk.error}]")
                    break
                if chunk.token:
                    reply_parts.append(chunk.token)
                if chunk.done:
                    break
        except Exception:
            logger.exception("Chat pipeline failed for %s/%s", platform, sender)
            return

        reply_text = "".join(reply_parts).strip()
        if not reply_text:
            logger.warning("Empty reply for %s/%s — not sending", platform, sender)
            return

        # Publish reply to the outbound topic
        outbound_topic = topics.topic_for(
            topics.EXTERNAL_OUTBOUND, platform=platform,
        )
        outbound_payload = json.dumps({
            "chat_id": sender,
            "text": reply_text,
        }).encode()

        self._mqtt.publish_bytes(outbound_topic, outbound_payload, qos=1)
        logger.info(
            "Reply sent to %s/%s (%d chars)",
            platform, sender, len(reply_text),
        )
