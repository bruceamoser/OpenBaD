"""Task management tool backed by the local WUI API."""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

from openbad.nervous_system import topics
from openbad.nervous_system.client import NervousSystemClient

logger = logging.getLogger(__name__)


@dataclass
class TasksDiagnosticsToolConfig:
    base_url: str = "http://127.0.0.1:9200"
    timeout: float = 5.0
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883


class TasksDiagnosticsToolAdapter:
    def __init__(
        self,
        config: TasksDiagnosticsToolConfig | None = None,
        http_get: object | None = None,
        http_post: object | None = None,
        http_patch: object | None = None,
        publisher: object | None = None,
    ) -> None:
        self._config = config or TasksDiagnosticsToolConfig()
        self._http_get = http_get or self._default_http_get
        self._http_post = http_post or self._default_http_post
        self._http_patch = http_patch or self._default_http_patch
        self._publisher = publisher or self._default_publisher

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

    def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        owner: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            key: value
            for key, value in {
                "title": title,
                "description": description,
                "owner": owner,
            }.items()
            if value is not None
        }
        url = f"{self._config.base_url.rstrip('/')}/api/tasks/{task_id}"
        try:
            data = json.loads(
                self._http_patch(url, self._config.timeout, payload).decode("utf-8")
            )
        except Exception:  # noqa: BLE001
            logger.exception("task update failed")
            return {}
        return data if isinstance(data, dict) else {}

    def complete_task(self, task_id: str) -> dict[str, Any]:
        url = f"{self._config.base_url.rstrip('/')}/api/tasks/{task_id}/complete"
        try:
            data = json.loads(self._http_post(url, self._config.timeout, {}).decode("utf-8"))
        except Exception:  # noqa: BLE001
            logger.exception("task complete failed")
            return {}
        return data if isinstance(data, dict) else {}

    def work_on_next_task(
        self,
        *,
        source: str = "session",
        reason: str = "next task requested",
    ) -> dict[str, Any]:
        payload = {
            "ts": time.time(),
            "mode": "next",
            "source": source.strip() or "session",
            "reason": reason.strip() or "next task requested",
        }
        try:
            self._publisher(topics.TASK_WORK_REQUEST, json.dumps(payload).encode("utf-8"))
        except Exception:  # noqa: BLE001
            logger.exception("task work publish failed")
            return {}
        return {"queued": True, "topic": topics.TASK_WORK_REQUEST, **payload}

    def work_on_task(
        self,
        task_id: str,
        *,
        source: str = "session",
        reason: str = "specific task requested",
    ) -> dict[str, Any]:
        payload = {
            "ts": time.time(),
            "mode": "specific",
            "task_id": task_id,
            "source": source.strip() or "session",
            "reason": reason.strip() or "specific task requested",
        }
        try:
            self._publisher(topics.TASK_WORK_REQUEST, json.dumps(payload).encode("utf-8"))
        except Exception:  # noqa: BLE001
            logger.exception("specific task work publish failed")
            return {}
        return {"queued": True, "topic": topics.TASK_WORK_REQUEST, **payload}

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

    @staticmethod
    def _default_http_patch(url: str, timeout: float, payload: dict[str, Any]) -> bytes:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310
            url,
            data=body,
            method="PATCH",
            headers={
                "User-Agent": "OpenBaD/1.0",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read()

    def _default_publisher(self, topic: str, payload: bytes) -> None:
        client = NervousSystemClient(
            host=self._config.mqtt_host,
            port=self._config.mqtt_port,
            client_id=f"openbad-task-tool-{int(time.time() * 1000)}",
        )
        client.connect(timeout=min(self._config.timeout, 5.0))
        try:
            client.publish_bytes(topic, payload)
        finally:
            client.disconnect()
