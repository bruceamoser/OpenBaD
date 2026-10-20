"""MQTT records diagnostics tool.

Read-only helper for recent MQTT bridge records exposed by the local WUI API.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MqttRecordsToolConfig:
    base_url: str = "http://127.0.0.1:9200"
    timeout: float = 5.0


class MqttRecordsToolAdapter:
    def __init__(
        self,
        config: MqttRecordsToolConfig | None = None,
        http_get: object | None = None,
    ) -> None:
        self._config = config or MqttRecordsToolConfig()
        self._http_get = http_get or self._default_http_get

    def get_mqtt_records(self, limit: int = 100) -> list[dict[str, Any]]:
        params = urllib.parse.urlencode({"limit": max(1, int(limit))})
        url = f"{self._config.base_url.rstrip('/')}/api/mqtt/log?{params}"
        try:
            data = json.loads(self._http_get(url, self._config.timeout).decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch MQTT records: {exc}") from exc
        messages = data.get("messages", []) if isinstance(data, dict) else []
        return messages if isinstance(messages, list) else []

    @staticmethod
    def _default_http_get(url: str, timeout: float) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "OpenBaD/1.0"})  # noqa: S310
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read()
