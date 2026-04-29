"""Telegram Bot API polling bridge.

Uses aiohttp to long-poll ``getUpdates`` and publish inbound messages to
the MQTT nervous system.  Subscribes to the outbound topic to deliver
responses via ``sendMessage``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import aiohttp

from openbad.nervous_system import topics
from openbad.nervous_system.client import NervousSystemClient

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}"
_CREDS_DIR = Path("data/config/peripherals")
_POLL_TIMEOUT = 30  # Telegram long-poll timeout (seconds)
_MAX_BACKOFF = 60  # Max retry backoff on API errors


class TelegramBridge:
    """Bidirectional Telegram ↔ MQTT bridge using Bot API long-polling."""

    def __init__(
        self,
        bot_token: str,
        mqtt_client: NervousSystemClient,
        *,
        poll_interval: float = 1.0,
    ) -> None:
        self._token = bot_token
        self._mqtt = mqtt_client
        self._poll_interval = poll_interval
        self._base_url = _TELEGRAM_API.format(token=bot_token)
        self._offset: int = 0
        self._session: aiohttp.ClientSession | None = None
        self._task: asyncio.Task[None] | None = None
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────── #

    async def start(self) -> None:
        """Begin polling Telegram and subscribe to outbound MQTT."""
        if self._running:
            return
        self._running = True
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=_POLL_TIMEOUT + 10),
        )

        # Subscribe to outbound messages
        outbound_topic = topics.topic_for(
            topics.EXTERNAL_OUTBOUND, platform="telegram",
        )
        self._mqtt.subscribe(
            outbound_topic, bytes, self._on_outbound,
        )

        self._task = asyncio.create_task(
            self._poll_loop(), name="telegram-poll",
        )
        logger.info("TelegramBridge started")

    async def stop(self) -> None:
        """Cancel polling and clean up."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

        outbound_topic = topics.topic_for(
            topics.EXTERNAL_OUTBOUND, platform="telegram",
        )
        self._mqtt.unsubscribe(outbound_topic)

        if self._session is not None:
            await self._session.close()
            self._session = None
        logger.info("TelegramBridge stopped")

    # ── Polling ───────────────────────────────────────────── #

    async def _poll_loop(self) -> None:
        """Long-poll getUpdates in a loop."""
        backoff = 1.0
        while self._running:
            try:
                updates = await self._get_updates()
                backoff = 1.0  # Reset on success
                for update in updates:
                    await self._handle_update(update)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception(
                    "Telegram poll error (retry in %.0fs)", backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)
                continue
            await asyncio.sleep(self._poll_interval)

    async def _get_updates(self) -> list[dict[str, Any]]:
        """Call getUpdates with long-polling."""
        if self._session is None:
            return []
        params: dict[str, Any] = {
            "timeout": _POLL_TIMEOUT,
            "allowed_updates": ["message"],
        }
        if self._offset:
            params["offset"] = self._offset
        async with self._session.get(
            f"{self._base_url}/getUpdates", params=params,
        ) as resp:
            data = await resp.json()
            if not data.get("ok"):
                desc = data.get("description", "Unknown error")
                logger.error("getUpdates failed: %s", desc)
                return []
            return data.get("result", [])

    async def _handle_update(self, update: dict[str, Any]) -> None:
        """Process a single Telegram update and publish to MQTT."""
        update_id = update.get("update_id", 0)
        self._offset = update_id + 1

        message = update.get("message")
        if message is None:
            return

        chat = message.get("chat", {})
        chat_id = chat.get("id")
        text = message.get("text", "")
        sender = message.get("from", {})
        sender_name = sender.get("first_name", "")

        if not text or chat_id is None:
            return

        # Publish to MQTT
        topic = topics.topic_for(
            topics.EXTERNAL_INBOUND, platform="telegram",
        )
        payload = json.dumps({
            "platform": "telegram",
            "event": "message",
            "data": {
                "sender": str(chat_id),
                "content": text,
                "sender_name": sender_name,
            },
            "received_at": time.time(),
        }).encode()

        self._mqtt.publish_bytes(topic, payload, qos=1)
        logger.debug(
            "Telegram inbound from %s (%s): %s",
            sender_name, chat_id, text[:80],
        )

    # ── Outbound ──────────────────────────────────────────── #

    def _on_outbound(self, _topic: str, payload: bytes) -> None:
        """Handle outbound MQTT messages → send to Telegram."""
        try:
            data = json.loads(payload.decode("utf-8"))
        except Exception:
            logger.exception("Invalid outbound payload")
            return

        chat_id = data.get("chat_id")
        text = data.get("text", "")
        if not chat_id or not text:
            logger.warning("Outbound missing chat_id or text")
            return

        # Schedule the async send on the event loop
        loop = asyncio.get_event_loop()
        loop.create_task(self.send_message(int(chat_id), text))

    async def send_message(self, chat_id: int, text: str) -> bool:
        """Send a message via Telegram Bot API."""
        if self._session is None:
            logger.error("Cannot send — session not open")
            return False
        try:
            async with self._session.post(
                f"{self._base_url}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            ) as resp:
                data = await resp.json()
                if data.get("ok"):
                    logger.debug("Sent to Telegram chat %d", chat_id)
                    return True
                logger.error(
                    "sendMessage failed: %s",
                    data.get("description", "unknown"),
                )
                return False
        except Exception:
            logger.exception("sendMessage to %d failed", chat_id)
            return False

    # ── Factory ───────────────────────────────────────────── #

    @classmethod
    def from_credentials(
        cls,
        mqtt_client: NervousSystemClient,
        credentials_dir: Path | None = None,
    ) -> TelegramBridge | None:
        """Create a bridge from stored credentials, or None if unavailable."""
        creds_dir = credentials_dir or _CREDS_DIR
        creds_path = creds_dir / "telegram.json"
        if not creds_path.exists():
            logger.warning("No Telegram credentials at %s", creds_path)
            return None
        try:
            creds = json.loads(creds_path.read_text())
            token = creds.get("bot_token", "")
            if not token:
                logger.error("Telegram credentials missing bot_token")
                return None
            return cls(token, mqtt_client)
        except Exception:
            logger.exception("Failed to load Telegram credentials")
            return None
