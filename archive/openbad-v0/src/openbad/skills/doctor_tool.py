"""Doctor orchestration tool for embedded session use."""

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
class DoctorToolConfig:
    base_url: str = "http://127.0.0.1:9200"
    timeout: float = 5.0
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883


class DoctorToolAdapter:
    def __init__(
        self,
        config: DoctorToolConfig | None = None,
        http_get: object | None = None,
        publisher: object | None = None,
    ) -> None:
        self._config = config or DoctorToolConfig()
        self._http_get = http_get or self._default_http_get
        self._publisher = publisher or self._default_publisher

    def get_doctor_status(self) -> dict[str, Any]:
        url = f"{self._config.base_url.rstrip('/')}/api/endocrine/status"
        try:
            data = json.loads(self._http_get(url, self._config.timeout).decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch doctor status: {exc}") from exc
        return data if isinstance(data, dict) else {}

    def call_doctor(
        self,
        reason: str,
        *,
        source: str = "session",
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "ts": time.time(),
            "source": source.strip() or "session",
            "reason": reason.strip() or "doctor requested",
            "context": context or {},
        }
        try:
            self._publisher(topics.DOCTOR_CALL, json.dumps(payload).encode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Failed to publish doctor call: {exc}") from exc
        return {"queued": True, "topic": topics.DOCTOR_CALL, **payload}

    @staticmethod
    def _default_http_get(url: str, timeout: float) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "OpenBaD/1.0"})  # noqa: S310
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read()

    def _default_publisher(self, topic: str, payload: bytes) -> None:
        client = NervousSystemClient(
            host=self._config.mqtt_host,
            port=self._config.mqtt_port,
            client_id=f"openbad-doctor-tool-{int(time.time() * 1000)}",
        )
        client.connect(timeout=min(self._config.timeout, 5.0))
        try:
            client.publish_bytes(topic, payload)
        finally:
            client.disconnect()
