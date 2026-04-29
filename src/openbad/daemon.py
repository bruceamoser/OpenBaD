"""Daemon bootstrap — starts subsystems and runs the main event loop."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
import time
from pathlib import Path

import yaml

from openbad.endocrine.controller import EndocrineController
from openbad.frameworks.crew_mqtt_bridge import CrewMQTTBridge
from openbad.interoception.disk_network import DiskNetworkMonitor
from openbad.interoception.monitor import TelemetryMonitor
from openbad.memory.base import MemoryEntry, MemoryTier
from openbad.nervous_system import topics
from openbad.nervous_system.client import NervousSystemClient
from openbad.nervous_system.schemas.cognitive_pb2 import ModelHealthStatus
from openbad.nervous_system.schemas.common_pb2 import Header
from openbad.nervous_system.schemas.endocrine_pb2 import EndocrineEvent
from openbad.nervous_system.schemas.telemetry_pb2 import TokenTelemetry
from openbad.peripherals.chat_router import PeripheralChatRouter
from openbad.peripherals.config import load_peripherals_config
from openbad.peripherals.telegram_bridge import TelegramBridge
from openbad.plugins.observations.external_signals import ExternalSignalPlugin
from openbad.reflex_arc.fsm import AgentFSM

logger = logging.getLogger(__name__)

_TELEMETRY_CONFIG_PATH = Path("/var/lib/openbad/telemetry.yaml")
_TELEMETRY_INTERVAL_DEFAULT_S = 5.0


def _load_hardware_telemetry_interval() -> float:
    """Read persisted hardware telemetry interval in seconds."""
    if _TELEMETRY_CONFIG_PATH.exists():
        try:
            loaded = yaml.safe_load(_TELEMETRY_CONFIG_PATH.read_text()) or {}
            interval = float(loaded.get("interval_seconds", _TELEMETRY_INTERVAL_DEFAULT_S))
            return max(1.0, interval)
        except Exception:  # noqa: BLE001
            logger.warning("Invalid telemetry config at %s; using default", _TELEMETRY_CONFIG_PATH)
    return _TELEMETRY_INTERVAL_DEFAULT_S


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
        self._crew_bridge: CrewMQTTBridge | None = None
        self._external_signal_plugin: ExternalSignalPlugin | None = None
        self._telegram_bridge: TelegramBridge | None = None
        self._chat_router: PeripheralChatRouter | None = None
        self._identity_persistence: object | None = None
        self._personality_modulator: object | None = None

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

    @property
    def crew_bridge(self) -> CrewMQTTBridge | None:
        return self._crew_bridge

    async def start(self) -> None:
        """Connect to MQTT, initialise subsystems, enter the main loop."""
        logger.info("Daemon starting (dry_run=%s)", self._dry_run)
        self._stop_event = asyncio.Event()

        # 1. MQTT nervous system
        self._client = NervousSystemClient.get_instance(
            host=self._mqtt_host, port=self._mqtt_port
        )
        self._client.connect(timeout=5.0)
        self._client.subscribe(topics.SCHEDULER_TICK, bytes, self._on_scheduler_tick)
        self._client.subscribe(topics.TASK_WORK_REQUEST, bytes, self._on_task_work_request)
        self._client.subscribe(topics.RESEARCH_WORK_REQUEST, bytes, self._on_research_work_request)
        self._client.subscribe(topics.DOCTOR_CALL, bytes, self._on_doctor_call)
        self._client.subscribe(
            topics.EXTERNAL_INBOUND_ALL, bytes, self._on_external_inbound,
        )

        # External signal observation plugin (records inbound messages)
        self._external_signal_plugin = ExternalSignalPlugin()

        # 2. Finite state machine
        self._fsm = AgentFSM(client=self._client)
        self._fsm.subscribe_triggers()

        # 3. Endocrine controller
        self._endocrine = EndocrineController()

        # 4. CrewAI ↔ MQTT activation bridge
        self._crew_bridge = CrewMQTTBridge(
            self._client, self._endocrine, self._fsm
        )
        self._crew_bridge.subscribe()

        # 4b. Identity context for peripheral chat
        self._initialize_identity()

        # 4c. Peripheral transducers (Telegram, etc.)
        await self._start_peripherals()

        # 5. Interoception publishers for vitals panels and dashboards
        telemetry_interval_s = _load_hardware_telemetry_interval()
        self._telemetry = TelemetryMonitor(self._client, interval=telemetry_interval_s)
        self._telemetry.start()
        self._disk_network = DiskNetworkMonitor(self._client, interval=telemetry_interval_s)
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

        # Peripheral transducers
        await self._stop_peripherals()

        self._crew_bridge = None
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
                    severity=1,
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

    def _on_scheduler_tick(self, _topic: str, payload: bytes) -> None:
        try:
            tick = json.loads(payload.decode("utf-8"))
        except Exception:
            logger.exception("Invalid scheduler tick payload")
            return

        if (
            not tick.get("eligible_task_id")
            and not tick.get("eligible_research_id")
            and (tick.get("queued_task_id") or tick.get("queued_research_id"))
        ):
            return

        # Check for stuck busy states before attempting new work.
        if self._fsm is not None:
            self._fsm.check_work_timeout()

        if self._fsm is not None and self._fsm.is_busy:
            logger.info("FSM busy (%s); skipping scheduler tick", self._fsm.state)
            return

        try:
            from openbad.autonomy.scheduler_worker import process_pending_autonomy_work

            process_pending_autonomy_work()
        except Exception:
            logger.exception("Scheduler worker failed")

    def _on_doctor_call(self, _topic: str, payload: bytes) -> None:
        try:
            request = json.loads(payload.decode("utf-8")) if payload else {}
        except Exception:
            logger.exception("Invalid doctor call payload")
            return

        if self._fsm is not None and not self._fsm.try_begin_work("begin_diagnose"):
            logger.info("FSM busy (%s); skipping overlapping doctor call", self._fsm.state)
            return

        try:
            from openbad.autonomy.scheduler_worker import process_doctor_call

            process_doctor_call(request if isinstance(request, dict) else None)
        except Exception:
            logger.exception("Doctor call worker failed")
        finally:
            if self._fsm is not None:
                self._fsm.finish_work()

    def _on_task_work_request(self, _topic: str, payload: bytes) -> None:
        try:
            request = json.loads(payload.decode("utf-8")) if payload else {}
        except Exception:
            logger.exception("Invalid task work payload")
            return

        logger.info("Received task work request: %s", request.get("task_id", "??"))
        if self._fsm is not None and not self._fsm.try_begin_work("begin_task"):
            logger.info("FSM busy (%s); skipping overlapping task work request", self._fsm.state)
            return

        try:
            from openbad.autonomy.scheduler_worker import process_task_call

            process_task_call(request if isinstance(request, dict) else None)
        except Exception:
            logger.exception("Task work worker failed")
        finally:
            if self._fsm is not None:
                self._fsm.finish_work()

    def _on_research_work_request(self, _topic: str, payload: bytes) -> None:
        try:
            request = json.loads(payload.decode("utf-8")) if payload else {}
        except Exception:
            logger.exception("Invalid research work payload")
            return

        logger.info("Received research work request: %s", request.get("node_id", "??"))
        if self._fsm is not None and not self._fsm.try_begin_work("begin_research"):
            logger.info(
                "FSM busy (%s); skipping overlapping research work request",
                self._fsm.state,
            )
            return

        try:
            from openbad.autonomy.scheduler_worker import process_research_call

            process_research_call(request if isinstance(request, dict) else None)
        except Exception:
            logger.exception("Research work worker failed")
        finally:
            if self._fsm is not None:
                self._fsm.finish_work()

    # -- identity context ----------------------------------------------- #

    def _initialize_identity(self) -> None:
        """Load identity persistence for peripheral chat context."""
        try:
            from openbad.identity.persistence import IdentityPersistence
            from openbad.identity.personality import PersonalityModulator
            from openbad.memory.base import EpisodicMemory
            from openbad.wui.server import _resolve_identity_config_path

            config_path = _resolve_identity_config_path()
            if not config_path.exists():
                logger.warning(
                    "Identity config not found at %s; "
                    "peripheral chat will lack entity context",
                    config_path,
                )
                return

            episodic_path = Path("/var/lib/openbad/memory/identity.json")
            persistence = IdentityPersistence(
                config_path,
                EpisodicMemory(storage_path=episodic_path),
            )
            self._identity_persistence = persistence
            self._personality_modulator = PersonalityModulator(
                persistence.assistant,
            )
            logger.info("Identity context loaded for peripheral chat")
        except Exception:
            logger.exception("Failed to load identity context")

    # -- peripheral transducers ----------------------------------------- #

    async def _start_peripherals(self) -> None:
        """Start enabled peripheral bridges (Telegram, etc.)."""
        if self._client is None:
            return

        cfg = load_peripherals_config()
        enabled_plugins = {p.name for p in cfg.plugins if p.enabled}

        if "telegram" in enabled_plugins:
            bridge = TelegramBridge.from_credentials(self._client)
            if bridge is not None:
                await bridge.start()
                self._telegram_bridge = bridge
                logger.info("Telegram bridge activated")
            else:
                logger.warning("Telegram enabled but credentials missing")

        if enabled_plugins:
            self._chat_router = PeripheralChatRouter(
                self._client,
                self._resolve_chat_model,
                identity_resolver=self._resolve_identity,
            )
            self._chat_router.start()

    async def _stop_peripherals(self) -> None:
        """Stop peripheral bridges."""
        if self._chat_router is not None:
            self._chat_router.stop()
            self._chat_router = None

        if self._telegram_bridge is not None:
            await self._telegram_bridge.stop()
            self._telegram_bridge = None

    def _resolve_chat_model(self) -> tuple[object | None, str | None, str]:
        """Return ``(chat_model, model_id, provider_name)`` for the default provider."""
        try:
            from openbad.wui.server import _read_providers_config, _resolve_chat_adapter

            _path, config = _read_providers_config()
            chat_model, model_id, provider_name, *_ = _resolve_chat_adapter(
                config, "chat",
            )
            return chat_model, model_id, provider_name
        except Exception:
            logger.exception("Failed to resolve chat model for peripheral router")
            return None, None, ""

    def _resolve_identity(
        self,
    ) -> tuple[
        object | None,
        object | None,
        object | None,
        object | None,
        object | None,
    ]:
        """Return identity context for peripheral chat."""
        if self._identity_persistence is not None:
            p = self._identity_persistence
            m = self._personality_modulator
            return (
                p.user,
                p.assistant,
                getattr(m, "factors", None),
                p,
                m,
            )
        return None, None, None, None, None

    def _on_external_inbound(self, topic: str, payload: bytes) -> None:
        """Handle an inbound external message from the Corsair webhook bridge.

        Writes the message to STM and increments the external-signal counter
        so the active inference engine can compute surprise.
        """
        try:
            data = json.loads(payload.decode("utf-8"))
        except Exception:
            logger.exception("Invalid external inbound payload")
            return

        # Extract platform from topic: sensory/external/{platform}/inbound
        parts = topic.split("/")
        platform = parts[2] if len(parts) >= 4 else "unknown"

        # Record observation for active inference
        if self._external_signal_plugin is not None:
            self._external_signal_plugin.record()

        # Write to Short-Term Memory
        try:
            from openbad.memory.cognitive_store import CognitiveMemoryStore
            from openbad.state.db import initialize_state_db

            conn = initialize_state_db()
            stm = CognitiveMemoryStore(conn, MemoryTier.STM)
            stm.write(
                MemoryEntry(
                    key=f"external:{platform}:{time.time():.0f}",
                    value=json.dumps(data),
                    tier=MemoryTier.STM,
                    ttl_seconds=300.0,
                    context=f"External inbound from {platform}",
                    metadata={
                        "platform": platform,
                        "sender": data.get("sender", ""),
                        "timestamp": data.get("timestamp", time.time()),
                        "content_summary": str(data.get("data", ""))[:200],
                        "event": data.get("event", ""),
                    },
                ),
            )
        except Exception:
            logger.exception("Failed to write external signal to STM")
