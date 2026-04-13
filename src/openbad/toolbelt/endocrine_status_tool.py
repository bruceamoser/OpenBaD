"""Endocrine status diagnostics tool.

Read-only helper for current endocrine runtime snapshot via the local WUI API.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class EndocrineStatusToolConfig:
    base_url: str = "http://127.0.0.1:8080"
    timeout: float = 5.0


class EndocrineStatusToolAdapter:
    def __init__(
        self,
        config: EndocrineStatusToolConfig | None = None,
        http_get: object | None = None,
    ) -> None:
        self._config = config or EndocrineStatusToolConfig()
        self._http_get = http_get or self._default_http_get

    def get_endocrine_status(self) -> dict[str, Any]:
        url = f"{self._config.base_url.rstrip('/')}/api/endocrine/status"
        try:
            data = json.loads(self._http_get(url, self._config.timeout).decode("utf-8"))
        except Exception:  # noqa: BLE001
            logger.exception("endocrine status fetch failed")
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _default_http_get(url: str, timeout: float) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "OpenBaD/1.0"})  # noqa: S310
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read()
