"""Daemon bootstrap — starts subsystems and runs the main event loop."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time

from openbad.endocrine.controller import EndocrineController
from openbad.interoception.disk_network import DiskNetworkMonitor
from openbad.interoception.monitor import TelemetryMonitor
from openbad.nervous_system.client import NervousSystemClient
from openbad.nervous_system.schemas.cognitive_pb2 import ModelHealthStatus
from openbad.nervous_system.schemas.common_pb2 import Header
from openbad.nervous_system.schemas.endocrine_pb2 import EndocrineEvent
from openbad.nervous_system.schemas.telemetry_pb2 import TokenTelemetry
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
        self._telemetry: TelemetryMonitor | None = None
        self._disk_network: DiskNetworkMonitor | None = None
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
        self._fsm.subscribe_triggers()

        # 3. Endocrine controller
        self._endocrine = EndocrineController()

        # 4. Interoception publishers for vitals panels and dashboards
        self._telemetry = TelemetryMonitor(self._client)
        self._telemetry.start()
        self._disk_network = DiskNetworkMonitor(self._client)
        self._disk_network.start()

        # Seed UI consumers with an initial state snapshot.
        self._fsm.publish_current_state()
        self._publish_bootstrap_snapshots()

        logger.info("All subsystems initialised")

        if self._dry_run:
            logger.info("Dry-run complete — shutting down")
            await self.stop()
            return

        # 5. Signal handlers (Unix only; no-op on Windows)
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
        if self._disk_network is not None:
            self._disk_network.stop()
            self._disk_network = None

        if self._telemetry is not None:
            self._telemetry.stop()
            self._telemetry = None

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

    def _publish_bootstrap_snapshots(self) -> None:
        """Publish one-time retained snapshots for stateful dashboard panels."""
        if self._client is None or self._endocrine is None:
            return

        now = time.time()
        endocrine_state = self._endocrine.get_state()
        for hormone, level in endocrine_state.to_dict().items():
            self._client.publish(
                f"agent/endocrine/{hormone}",
                EndocrineEvent(
                    header=Header(timestamp_unix=now, source_module="openbad.daemon"),
                    hormone=hormone,
                    level=level,
                ),
            )

        self._client.publish(
            "agent/telemetry/tokens",
            TokenTelemetry(
                header=Header(timestamp_unix=now, source_module="openbad.daemon"),
                tokens_used=0,
                budget_ceiling=0,
                budget_remaining_pct=100.0,
                cost_per_action_avg=0.0,
                model_tier="idle",
            ),
        )

        self._client.publish(
            "agent/cognitive/health",
            ModelHealthStatus(
                header=Header(timestamp_unix=now, source_module="openbad.daemon"),
                provider="inactive",
                model_id="none",
                available=False,
                latency_p50=0.0,
                latency_p99=0.0,
            ),
        )
