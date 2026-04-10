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

from openbad.tui.mqtt_feed import MqttDisconnected, MqttFeed

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
                    yield PlaceholderPanel("[dim]Hormone gauges (issue #181)[/dim]")
                    yield PlaceholderPanel("[dim]FSM state (issue #181)[/dim]")
                with Horizontal(id="bottom-panels"):
                    yield PlaceholderPanel("[dim]Inference (issue #182)[/dim]")
                    yield PlaceholderPanel("[dim]Vitals (issue #182)[/dim]")
        yield Footer()

    async def on_mount(self) -> None:
        await self.feed.connect(self)

    async def on_unmount(self) -> None:
        await self.feed.disconnect()

    def on_mqtt_disconnected(self, _message: MqttDisconnected) -> None:
        status = self.query_one(StatusPanel)
        status.update("FSM: -- | [red]MQTT: disconnected[/red] | Hormones: --")

    def action_reconnect(self) -> None:
        """Reconnect to the MQTT broker."""
        self.run_worker(self.feed.disconnect())
        self.run_worker(self.feed.connect(self))

    def action_toggle_dark(self) -> None:
        self.dark = not self.dark
