"""OpenBaD Terminal UI application.

Provides the main Textual ``App`` with a layout containing a sidebar for
navigation and a main content area with placeholder panels (wired in by
subsequent issues).
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Static

from openbad.nervous_system import topics
from openbad.nervous_system.schemas.cognitive_pb2 import ModelHealthStatus, ReasoningResponse
from openbad.nervous_system.schemas.endocrine_pb2 import EndocrineEvent
from openbad.nervous_system.schemas.reflex_pb2 import ReflexState
from openbad.nervous_system.schemas.telemetry_pb2 import (
    CpuTelemetry,
    DiskTelemetry,
    MemoryTelemetry,
    NetworkTelemetry,
    TokenTelemetry,
)
from openbad.tui.mqtt_feed import MqttConnected, MqttDisconnected, MqttFeed, MqttPayload
from openbad.tui.panels import FSMPanel, HormonePanel, InferencePanel, VitalsPanel

# ── Placeholder panels (replaced by #181-#183) ──────────────────────


class StatusPanel(Static):
    """Connection and FSM status bar across the top of the main area."""

    DEFAULT_CSS = """
    StatusPanel {
        height: 3;
        border: solid $accent;
        padding: 0 1;
    }
    """

    def on_mount(self) -> None:
        self.update("FSM: -- | MQTT: disconnected | Hormones: --")


class PlaceholderPanel(Static):
    """Generic placeholder for panels not yet implemented."""

    DEFAULT_CSS = """
    PlaceholderPanel {
        height: 1fr;
        border: solid $surface;
        padding: 1;
    }
    """


# ── Sidebar ──────────────────────────────────────────────────────────


class Sidebar(Static):
    """Navigation sidebar showing subsystem list."""

    DEFAULT_CSS = """
    Sidebar {
        width: 24;
        border: solid $primary;
        padding: 1;
    }
    """

    def on_mount(self) -> None:
        self.update(
            "[b]Subsystems[/b]\n"
            "──────────────\n"
            "• Hormones\n"
            "• FSM State\n"
            "• Inference\n"
            "• Vitals\n"
            "• Event Log\n"
        )


# ── Main App ─────────────────────────────────────────────────────────


class OpenBaDApp(App):
    """OpenBaD interactive terminal dashboard."""

    TITLE = "OpenBaD"
    SUB_TITLE = "Biological-as-Digital Agent"

    CSS = """
    Screen {
        layout: vertical;
    }
    #main-area {
        height: 1fr;
    }
    #panels {
        height: 1fr;
    }
    #top-panels {
        height: 1fr;
    }
    #bottom-panels {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("d", "toggle_dark", "Dark/Light", show=True),
        Binding("r", "reconnect", "Reconnect", show=True),
    ]

    def __init__(
        self,
        mqtt_host: str = "localhost",
        mqtt_port: int = 1883,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self.feed = MqttFeed(host=mqtt_host, port=mqtt_port)

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-area"):
            yield Sidebar()
            with Vertical(id="panels"):
                yield StatusPanel()
                with Horizontal(id="top-panels"):
                    yield HormonePanel(id="hormone-panel")
                    yield FSMPanel(id="fsm-panel")
                with Horizontal(id="bottom-panels"):
                    yield InferencePanel(id="inference-panel")
                    yield VitalsPanel(id="vitals-panel")
        yield Footer()

    async def on_mount(self) -> None:
        await self.feed.connect(self)
        self._subscribe_topics()

    def _subscribe_topics(self) -> None:
        """Subscribe to hormone and FSM state topics."""
        if not self.feed.is_connected:
            return
        self.feed.subscribe(topics.ENDOCRINE_ALL, EndocrineEvent)
        self.feed.subscribe(topics.REFLEX_STATE, ReflexState)
        self.feed.subscribe(topics.TELEMETRY_CPU, CpuTelemetry)
        self.feed.subscribe(topics.TELEMETRY_MEMORY, MemoryTelemetry)
        self.feed.subscribe(topics.TELEMETRY_DISK, DiskTelemetry)
        self.feed.subscribe(topics.TELEMETRY_NETWORK, NetworkTelemetry)
        self.feed.subscribe(topics.TELEMETRY_TOKENS, TokenTelemetry)
        self.feed.subscribe(topics.COGNITIVE_HEALTH, ModelHealthStatus)
        self.feed.subscribe(topics.COGNITIVE_RESPONSE, ReasoningResponse)

    async def on_unmount(self) -> None:
        await self.feed.disconnect()

    def on_mqtt_disconnected(self, _message: MqttDisconnected) -> None:
        status = self.query_one(StatusPanel)
        status.update("FSM: -- | [red]MQTT: disconnected[/red] | Hormones: --")

    def on_mqtt_connected(self, _message: MqttConnected) -> None:
        status = self.query_one(StatusPanel)
        status.update("FSM: -- | [green]MQTT: connected[/green] | Hormones: --")
        self._subscribe_topics()

    def on_mqtt_payload(self, message: MqttPayload) -> None:
        """Route incoming MQTT messages to the appropriate panel."""
        topic = message.topic
        payload = message.payload

        if topic.startswith("agent/endocrine/"):
            hormone = topic.rsplit("/", 1)[-1]
            try:
                panel = self.query_one("#hormone-panel", HormonePanel)
                level = float(getattr(payload, "level", payload))
                panel.update_levels({hormone: level})
            except Exception:  # noqa: BLE001, S110
                pass

        elif topic == topics.REFLEX_STATE:
            try:
                fsm = self.query_one("#fsm-panel", FSMPanel)
                state = str(getattr(payload, "current_state", payload))
                fsm.state = state
                # Also update the status bar
                status = self.query_one(StatusPanel)
                status.update(
                    f"FSM: {state} | [green]MQTT: connected[/green] | Hormones: live"
                )
            except Exception:  # noqa: BLE001, S110
                pass

        elif topic == topics.TELEMETRY_CPU:
            panel = self.query_one("#vitals-panel", VitalsPanel)
            panel.cpu_usage = float(payload.usage_percent)

        elif topic == topics.TELEMETRY_MEMORY:
            panel = self.query_one("#vitals-panel", VitalsPanel)
            panel.mem_usage = float(payload.usage_percent)

        elif topic == topics.TELEMETRY_DISK:
            panel = self.query_one("#vitals-panel", VitalsPanel)
            panel.disk_usage = float(payload.usage_percent)

        elif topic == topics.TELEMETRY_NETWORK:
            panel = self.query_one("#vitals-panel", VitalsPanel)
            panel.net_sent = float(payload.bytes_sent)
            panel.net_recv = float(payload.bytes_recv)

        elif topic == topics.TELEMETRY_TOKENS:
            panel = self.query_one("#vitals-panel", VitalsPanel)
            panel.tokens_used = int(payload.tokens_used)
            panel.token_remaining = float(payload.budget_remaining_pct)
            panel.model_tier = str(payload.model_tier)

        elif topic == topics.COGNITIVE_HEALTH:
            panel = self.query_one("#inference-panel", InferencePanel)
            panel.provider = str(payload.provider)
            panel.model_id = str(payload.model_id)
            panel.available = bool(payload.available)
            panel.latency_p50 = float(payload.latency_p50)
            panel.latency_p99 = float(payload.latency_p99)

        elif topic == topics.COGNITIVE_RESPONSE:
            panel = self.query_one("#inference-panel", InferencePanel)
            panel.last_model_used = str(payload.model_used)
            panel.last_tokens = int(payload.tokens_used)
            panel.last_latency_ms = float(payload.latency_ms)

    def action_reconnect(self) -> None:
        """Reconnect to the MQTT broker."""
        self.run_worker(self.feed.disconnect())
        self.run_worker(self.feed.connect(self))

    def action_toggle_dark(self) -> None:
        self.dark = not self.dark
