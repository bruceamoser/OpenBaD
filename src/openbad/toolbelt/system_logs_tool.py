"""System logs diagnostics tool.

Read-only helper for recent in-memory debug logs exposed by the local WUI API.
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
class SystemLogsToolConfig:
    base_url: str = "http://127.0.0.1:8080"
    timeout: float = 5.0


class SystemLogsToolAdapter:
    def __init__(
        self,
        config: SystemLogsToolConfig | None = None,
        http_get: object | None = None,
    ) -> None:
        self._config = config or SystemLogsToolConfig()
        self._http_get = http_get or self._default_http_get

    def get_system_logs(self, limit: int = 200, system: str = "") -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": max(1, int(limit))}
        if system.strip():
            params["system"] = system.strip()
        query = urllib.parse.urlencode(params)
        url = f"{self._config.base_url.rstrip('/')}/api/debug/logs"
        if query:
            url = f"{url}?{query}"
        try:
            data = json.loads(self._http_get(url, self._config.timeout).decode("utf-8"))
        except Exception:  # noqa: BLE001
            logger.exception("system logs fetch failed")
            return []
        logs = data.get("logs", []) if isinstance(data, dict) else []
        return logs if isinstance(logs, list) else []

    @staticmethod
    def _default_http_get(url: str, timeout: float) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "OpenBaD/1.0"})  # noqa: S310
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read()
