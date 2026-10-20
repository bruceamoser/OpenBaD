"""Hormone gauge and FSM state panels for the TUI."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Input, RichLog, Static

# ── Hormone constants ────────────────────────────────────────────────

HORMONES = ("dopamine", "adrenaline", "cortisol", "endorphin")

HORMONE_COLOURS = {
    "dopamine": "green",
    "adrenaline": "red",
    "cortisol": "yellow",
    "endorphin": "cyan",
}

# ── Gauge bar helper ─────────────────────────────────────────────────

_BAR_WIDTH = 20


def _bar(value: float, colour: str) -> str:
    """Render a horizontal gauge bar using Rich markup."""
    clamped = max(0.0, min(1.0, value))
    filled = round(clamped * _BAR_WIDTH)
    empty = _BAR_WIDTH - filled
    return f"[{colour}]{'█' * filled}[/{colour}]{'░' * empty} {clamped:.0%}"


# ── Single hormone gauge ─────────────────────────────────────────────


class HormoneGauge(Static):
    """Displays one hormone as a labelled progress bar."""

    DEFAULT_CSS = """
    HormoneGauge {
        height: 1;
        padding: 0 1;
    }
    """

    level: reactive[float] = reactive(0.0)

    def __init__(self, hormone: str, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.hormone = hormone
        self._colour = HORMONE_COLOURS.get(hormone, "white")

    def render(self) -> str:  # type: ignore[override]
        return f"{self.hormone:<12} {_bar(self.level, self._colour)}"


# ── Hormone panel (groups all gauges) ────────────────────────────────


class HormonePanel(Static):
    """Panel containing gauges for all four hormones."""

    DEFAULT_CSS = """
    HormonePanel {
        height: 1fr;
        border: solid $accent;
        padding: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("[b]Hormones[/b]")
        with Vertical():
            for h in HORMONES:
                yield HormoneGauge(h, id=f"gauge-{h}")

    def update_levels(self, levels: dict[str, float]) -> None:
        """Update gauge levels from a ``{hormone: float}`` dict."""
        for name, value in levels.items():
            gauge_id = f"gauge-{name}"
            try:
                gauge = self.query_one(f"#{gauge_id}", HormoneGauge)
                gauge.level = value
            except Exception:  # noqa: BLE001, S110
                pass


# ── FSM state panel ──────────────────────────────────────────────────

FSM_STATES = ("IDLE", "ACTIVE", "THROTTLED", "SLEEP", "EMERGENCY")

STATE_COLOURS = {
    "IDLE": "dim white",
    "ACTIVE": "green",
    "THROTTLED": "yellow",
    "SLEEP": "cyan",
    "EMERGENCY": "bold red",
}


class FSMPanel(Static):
    """Displays the current FSM state and available transitions."""

    DEFAULT_CSS = """
    FSMPanel {
        height: 1fr;
        border: solid $accent;
        padding: 1;
    }
    """

    state: reactive[str] = reactive("UNKNOWN")

    def render(self) -> str:  # type: ignore[override]
        colour = STATE_COLOURS.get(self.state, "white")
        lines = [
            "[b]FSM State[/b]",
            "",
            f"  Current: [{colour}]{self.state}[/{colour}]",
            "",
            "  States:",
        ]
        for s in FSM_STATES:
            marker = "▸" if s == self.state else " "
            sc = STATE_COLOURS.get(s, "white")
            lines.append(f"    {marker} [{sc}]{s}[/{sc}]")
        return "\n".join(lines)


def _bytes_human(value: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    idx = 0
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    return f"{size:.1f} {units[idx]}"


class VitalsPanel(Static):
    """Displays key runtime telemetry from interoception topics."""

    DEFAULT_CSS = """
    VitalsPanel {
        height: 1fr;
        border: solid $accent;
        padding: 1;
    }
    """

    cpu_usage: reactive[float] = reactive(0.0)
    mem_usage: reactive[float] = reactive(0.0)
    disk_usage: reactive[float] = reactive(0.0)
    net_sent: reactive[float] = reactive(0.0)
    net_recv: reactive[float] = reactive(0.0)
    tokens_used: reactive[int] = reactive(0)
    token_remaining: reactive[float] = reactive(0.0)
    model_tier: reactive[str] = reactive("--")

    def render(self) -> str:  # type: ignore[override]
        return "\n".join(
            [
                "[b]Vitals[/b]",
                "",
                f"CPU:    {self.cpu_usage:5.1f}%",
                f"Memory: {self.mem_usage:5.1f}%",
                f"Disk:   {self.disk_usage:5.1f}%",
                f"Net TX: {_bytes_human(self.net_sent)}",
                f"Net RX: {_bytes_human(self.net_recv)}",
                f"Tokens: {self.tokens_used} ({self.token_remaining:4.1f}% left)",
                f"Tier:   {self.model_tier}",
            ]
        )


class InferencePanel(Static):
    """Displays cognitive inference health and latest response metadata."""

    DEFAULT_CSS = """
    InferencePanel {
        height: 1fr;
        border: solid $accent;
        padding: 1;
    }
    """

    provider: reactive[str] = reactive("--")
    model_id: reactive[str] = reactive("--")
    available: reactive[bool] = reactive(False)
    latency_p50: reactive[float] = reactive(0.0)
    latency_p99: reactive[float] = reactive(0.0)
    last_model_used: reactive[str] = reactive("--")
    last_tokens: reactive[int] = reactive(0)
    last_latency_ms: reactive[float] = reactive(0.0)

    def render(self) -> str:  # type: ignore[override]
        availability = "[green]up[/green]" if self.available else "[red]down[/red]"
        return "\n".join(
            [
                "[b]Inference[/b]",
                "",
                f"Provider: {self.provider}",
                f"Model:    {self.model_id}",
                f"Health:   {availability}",
                f"p50/p99:  {self.latency_p50:.1f}ms / {self.latency_p99:.1f}ms",
                "",
                "Last response:",
                f"  model:  {self.last_model_used}",
                f"  tokens: {self.last_tokens}",
                f"  latency:{self.last_latency_ms:.1f}ms",
            ]
        )


class EventLogPanel(Static):
    """Scrollable event log for incoming MQTT activity and user commands."""

    DEFAULT_CSS = """
    EventLogPanel {
        height: 14;
        border: solid $accent;
        padding: 1;
    }
    #event-log-title {
        height: 1;
    }
    #event-log-view {
        height: 1fr;
        border: solid $surface;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("[b]Event Log[/b]", id="event-log-title")
        yield RichLog(highlight=True, wrap=True, id="event-log-view")

    def write(self, line: str) -> None:
        log = self.query_one("#event-log-view", RichLog)
        log.write(line)

    def clear(self) -> None:
        log = self.query_one("#event-log-view", RichLog)
        log.clear()


class CommandBar(Input):
    """Bottom command input for slash-like operator commands."""

    DEFAULT_CSS = """
    CommandBar {
        dock: bottom;
        height: 3;
        border: solid $primary;
    }
    """
