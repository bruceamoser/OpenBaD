"""Observation plugins for Active Inference environmental scanning."""

from __future__ import annotations

from openbad.plugins.observations.browser import BrowserHistoryObservationPlugin
from openbad.plugins.observations.calendar import CalendarObservationPlugin
from openbad.plugins.observations.email import EmailObservationPlugin
from openbad.plugins.observations.syslog import SyslogObservationPlugin

__all__ = [
    "BrowserHistoryObservationPlugin",
    "CalendarObservationPlugin",
    "EmailObservationPlugin",
    "SyslogObservationPlugin",
]
