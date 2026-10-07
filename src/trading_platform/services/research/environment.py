"""Process-level research environment: calendar pin and bar source.

``apply_research_environment`` is called by every research entrypoint (worker,
API, scripts) after settings load. With ``research.mode`` off it clears the pin,
so a trading process is never affected by a stale research setting.
"""

from __future__ import annotations

from trading_platform.core.settings import Settings
from trading_platform.services.calendar import pin_calendar_start


def apply_research_environment(settings: Settings) -> None:
    if settings.research.mode:
        pin_calendar_start(settings.research.calendar_start)
    else:
        pin_calendar_start(None)


def bar_source(settings: Settings) -> tuple[str, bool]:
    """``(provider, adjusted)`` every research bar read passes explicitly."""

    return settings.research.bar_provider, settings.research.bar_adjusted
