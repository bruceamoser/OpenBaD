"""Web UI server for OpenBaD.

Serves the static dashboard assets and hosts the MQTT->WebSocket bridge.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from aiohttp import web

from openbad.wui.bridge import MqttWebSocketBridge

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(
    mqtt_host: str = "localhost",
    mqtt_port: int = 1883,
    *,
    enable_mqtt: bool = True,
) -> web.Application:
    bridge = MqttWebSocketBridge(mqtt_host=mqtt_host, mqtt_port=mqtt_port)
    app = bridge.create_app()
    app["bridge"] = bridge

    if not enable_mqtt:
        # Tests can disable external broker dependency by skipping startup/shutdown hooks.
        app.on_startup.clear()
        app.on_shutdown.clear()

    async def index(_request: web.Request) -> web.FileResponse:
        return web.FileResponse(STATIC_DIR / "index.html")

    app.router.add_get("/", index)
    app.router.add_static("/static", STATIC_DIR)
    return app


async def run_server(
    host: str = "127.0.0.1",
    port: int = 9200,
    mqtt_host: str = "localhost",
    mqtt_port: int = 1883,
) -> None:
    app = create_app(mqtt_host=mqtt_host, mqtt_port=mqtt_port, enable_mqtt=True)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()

    try:
        # Keep running until cancelled.
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
