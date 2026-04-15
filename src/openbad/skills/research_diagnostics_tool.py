"""Research management tool backed by the local WUI API."""

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
class ResearchDiagnosticsToolConfig:
    base_url: str = "http://127.0.0.1:9200"
    timeout: float = 5.0
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883


class ResearchDiagnosticsToolAdapter:
    def __init__(
        self,
        config: ResearchDiagnosticsToolConfig | None = None,
        http_get: object | None = None,
        http_post: object | None = None,
        http_patch: object | None = None,
        publisher: object | None = None,
    ) -> None:
        self._config = config or ResearchDiagnosticsToolConfig()
        self._http_get = http_get or self._default_http_get
        self._http_post = http_post or self._default_http_post
        self._http_patch = http_patch or self._default_http_patch
        self._publisher = publisher or self._default_publisher

    def get_research_nodes(self) -> list[dict[str, Any]]:
        url = f"{self._config.base_url.rstrip('/')}/api/research"
        try:
            data = json.loads(self._http_get(url, self._config.timeout).decode("utf-8"))
        except Exception:  # noqa: BLE001
            logger.exception("research fetch failed")
            return []
        nodes = data.get("nodes", []) if isinstance(data, dict) else []
        return nodes if isinstance(nodes, list) else []

    def create_research_node(
        self,
        title: str,
        *,
        description: str = "",
        priority: int = 0,
        source_task_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "title": title,
            "description": description,
            "priority": int(priority),
            "source_task_id": source_task_id,
        }
        url = f"{self._config.base_url.rstrip('/')}/api/research"
        try:
            data = json.loads(
                self._http_post(url, self._config.timeout, payload).decode("utf-8")
            )
        except Exception:  # noqa: BLE001
            logger.exception("research create failed")
            return {}
        return data if isinstance(data, dict) else {}

    def update_research_node(
        self,
        node_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        priority: int | None = None,
        source_task_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            key: value
            for key, value in {
                "title": title,
                "description": description,
                "priority": priority,
                "source_task_id": source_task_id,
            }.items()
            if value is not None
        }
        url = f"{self._config.base_url.rstrip('/')}/api/research/{node_id}"
        try:
            data = json.loads(
                self._http_patch(url, self._config.timeout, payload).decode("utf-8")
            )
        except Exception:  # noqa: BLE001
            logger.exception("research update failed")
            return {}
        return data if isinstance(data, dict) else {}

    def complete_research_node(self, node_id: str) -> dict[str, Any]:
        url = f"{self._config.base_url.rstrip('/')}/api/research/{node_id}/complete"
        try:
            data = json.loads(self._http_post(url, self._config.timeout, {}).decode("utf-8"))
        except Exception:  # noqa: BLE001
            logger.exception("research complete failed")
            return {}
        return data if isinstance(data, dict) else {}

    def work_on_next_research(
        self,
        *,
        source: str = "session",
        reason: str = "next research requested",
    ) -> dict[str, Any]:
        payload = {
            "ts": time.time(),
            "mode": "next",
            "source": source.strip() or "session",
            "reason": reason.strip() or "next research requested",
        }
        try:
            self._publisher(topics.RESEARCH_WORK_REQUEST, json.dumps(payload).encode("utf-8"))
        except Exception:  # noqa: BLE001
            logger.exception("research work publish failed")
            return {}
        return {"queued": True, "topic": topics.RESEARCH_WORK_REQUEST, **payload}

    def work_on_research(
        self,
        node_id: str,
        *,
        source: str = "session",
        reason: str = "specific research requested",
    ) -> dict[str, Any]:
        payload = {
            "ts": time.time(),
            "mode": "specific",
            "node_id": node_id,
            "source": source.strip() or "session",
            "reason": reason.strip() or "specific research requested",
        }
        try:
            self._publisher(topics.RESEARCH_WORK_REQUEST, json.dumps(payload).encode("utf-8"))
        except Exception:  # noqa: BLE001
            logger.exception("specific research work publish failed")
            return {}
        return {"queued": True, "topic": topics.RESEARCH_WORK_REQUEST, **payload}

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
            client_id=f"openbad-research-tool-{int(time.time() * 1000)}",
        )
        client.connect(timeout=min(self._config.timeout, 5.0))
        try:
            client.publish_bytes(topic, payload)
        finally:
            client.disconnect()
