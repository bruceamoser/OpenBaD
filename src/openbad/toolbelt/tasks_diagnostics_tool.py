"""Tasks diagnostics tool.

Read-only helper for current task list via the local WUI API.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TasksDiagnosticsToolConfig:
    base_url: str = "http://127.0.0.1:8080"
    timeout: float = 5.0


class TasksDiagnosticsToolAdapter:
    def __init__(
        self,
        config: TasksDiagnosticsToolConfig | None = None,
        http_get: object | None = None,
        http_post: object | None = None,
    ) -> None:
        self._config = config or TasksDiagnosticsToolConfig()
        self._http_get = http_get or self._default_http_get
        self._http_post = http_post or self._default_http_post

    def get_tasks(self) -> list[dict[str, Any]]:
        url = f"{self._config.base_url.rstrip('/')}/api/tasks"
        try:
            data = json.loads(self._http_get(url, self._config.timeout).decode("utf-8"))
        except Exception:  # noqa: BLE001
            logger.exception("tasks fetch failed")
            return []
        tasks = data.get("tasks", []) if isinstance(data, dict) else []
        return tasks if isinstance(tasks, list) else []

    def create_task(
        self,
        title: str,
        *,
        description: str = "",
        owner: str = "user",
    ) -> dict[str, Any]:
        payload = {
            "title": title,
            "description": description,
            "owner": owner,
        }
        url = f"{self._config.base_url.rstrip('/')}/api/tasks"
        try:
            data = json.loads(
                self._http_post(url, self._config.timeout, payload).decode("utf-8")
            )
        except Exception:  # noqa: BLE001
            logger.exception("task create failed")
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _default_http_get(url: str, timeout: float) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "OpenBaD/1.0"})  # noqa: S310
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read()

    @staticmethod
    def _default_http_post(url: str, timeout: float, payload: dict[str, Any]) -> bytes:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310
            url,
            data=body,
            method="POST",
            headers={
                "User-Agent": "OpenBaD/1.0",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read()
