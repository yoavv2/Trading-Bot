"""The one run-time clock for recovery, execution-operation and ownership services.

Spec objects take an injected ``clock=`` (20.1-05); run-time SERVICE functions have no
such seam, so they call ``now_utc()`` from this module. Tests patch
``trading_platform.core.clock.now_utc`` (consumers must call it through the module,
``clock.now_utc()``, or import it lazily) to move time deterministically.
"""

from __future__ import annotations

from datetime import UTC, datetime


def now_utc() -> datetime:
    """Current time as a timezone-aware UTC ``datetime``."""

    return datetime.now(UTC)
