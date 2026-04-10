"""Daemon bootstrap — starts subsystems and runs the main event loop."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from openbad.endocrine.controller import EndocrineController
from openbad.nervous_system.client import NervousSystemClient
from openbad.reflex_arc.fsm import AgentFSM

logger = logging.getLogger(__name__)


class Daemon:
    """Core daemon lifecycle: connect MQTT, init subsystems, run loop."""

    def __init__(
        self,
        mqtt_host: str = "localhost",
        mqtt_port: int = 1883,
        *,
        dry_run: bool = False,
    ) -> None:
        self._mqtt_host = mqtt_host
        self._mqtt_port = mqtt_port
        self._dry_run = dry_run
        self._running = False
        self._client: NervousSystemClient | None = None
        self._fsm: AgentFSM | None = None
        self._endocrine: EndocrineController | None = None
        self._stop_event: asyncio.Event | None = None

    # -- public --------------------------------------------------------- #

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def fsm(self) -> AgentFSM | None:
        return self._fsm

    @property
    def endocrine(self) -> EndocrineController | None:
        return self._endocrine

    @property
    def client(self) -> NervousSystemClient | None:
        return self._client

    async def start(self) -> None:
        """Connect to MQTT, initialise subsystems, enter the main loop."""
        logger.info("Daemon starting (dry_run=%s)", self._dry_run)
        self._stop_event = asyncio.Event()

        # 1. MQTT nervous system
        self._client = NervousSystemClient.get_instance(
            host=self._mqtt_host, port=self._mqtt_port
        )
        self._client.connect(timeout=5.0)

        # 2. Finite state machine
        self._fsm = AgentFSM(client=self._client)

        # 3. Endocrine controller
        self._endocrine = EndocrineController()

        logger.info("All subsystems initialised")

        if self._dry_run:
            logger.info("Dry-run complete — shutting down")
            await self.stop()
            return

        # 4. Signal handlers (Unix only; no-op on Windows)
        self._install_signal_handlers()

        self._running = True
        logger.info("Daemon running")

        # Block until stop requested
        await self._stop_event.wait()
        await self.stop()

    async def stop(self) -> None:
        """Gracefully tear down subsystems in reverse order."""
        if not self._running and not self._dry_run:
            return
        logger.info("Daemon stopping")
        self._running = False

        # Reverse-order teardown
        self._endocrine = None
        self._fsm = None

        if self._client is not None:
            self._client.disconnect()
            NervousSystemClient.reset_instance()
            self._client = None

        if self._stop_event is not None:
            self._stop_event.set()

        logger.info("Daemon stopped")

    def request_stop(self) -> None:
        """Thread-safe signal to stop the daemon."""
        if self._stop_event is not None:
            self._stop_event.set()

    # -- internals ------------------------------------------------------ #

    def _install_signal_handlers(self) -> None:
        """Register SIGTERM/SIGINT handlers on the running event loop."""
        if sys.platform == "win32":
            return
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.request_stop)
