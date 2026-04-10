"""Hormone gauge and FSM state panels for the TUI."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Static

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
