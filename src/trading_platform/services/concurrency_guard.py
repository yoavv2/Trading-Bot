"""Session-level PostgreSQL advisory-lock primitive guarding concurrent runs.

At most one active run per ``(strategy_id, session_date)`` may perform side
effects (broker calls, state-affecting DB writes). ``session_run_lock()`` is
the contract every side-effecting run acquires before doing anything: it
takes a non-blocking session-level advisory lock on ONE dedicated connection
held for the whole guarded region. A second, concurrent attempt to hold the
same lock fails immediately with a typed :class:`ConcurrentRunLockedError` --
it never blocks or hangs. The lock is released explicitly on normal exit; if
the process crashes instead, PostgreSQL auto-releases session-level advisory
locks when the holding connection drops, so the guarantee holds even then.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text

from trading_platform.core.logging import emit_structured_log
from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.session import get_engine

logger = logging.getLogger(__name__)

# Distinct from argparse's usage exit code (2); signals "another run holds
# the lock" to a scheduler/operator, as opposed to a generic crash/failure.
CONCURRENT_RUN_LOCK_EXIT_CODE = 3


def advisory_lock_key(strategy_id: str, session_date: date) -> int:
    """Derive a deterministic signed 64-bit advisory-lock key.

    Same ``(strategy_id, session_date)`` always yields the same key; a
    different ``session_date`` yields a different key. PostgreSQL advisory
    locks share one global 64-bit keyspace across the whole instance, but
    this repository has no other advisory-lock users today, so a plain hash
    of the canonical tuple is sufficient (Claude's-discretion key
    derivation per the Phase 8 context).
    """
    canonical = f"{strategy_id}:{session_date.isoformat()}"
    digest = hashlib.blake2b(canonical.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


@dataclass(frozen=True)
class ConcurrentRunLockedError(RuntimeError):
    """Raised when another session already holds the run lock for a tuple."""

    strategy_id: str
    session_date: date

    def __str__(self) -> str:
        return (
            f"Another session holds the run lock for strategy '{self.strategy_id}' "
            f"session {self.session_date}."
        )


@contextmanager
def session_run_lock(
    *,
    strategy_id: str,
    session_date: date,
    settings: Settings | None = None,
) -> Iterator[None]:
    """Acquire the non-blocking advisory lock for the guarded region.

    Opens ONE dedicated connection (autocommit, no long-lived transaction)
    from the shared engine and holds it for the whole guarded region -- the
    guarded body's own DB writes must use separate pooled ``session_scope``
    connections, not this one. Raises ``ConcurrentRunLockedError`` and exits
    immediately (no retry, no hang) if another session already holds the
    lock for this tuple.
    """
    resolved_settings = settings if settings is not None else load_settings()
    key = advisory_lock_key(strategy_id, session_date)
    engine = get_engine(resolved_settings)
    connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    acquired = False
    try:
        acquired = bool(
            connection.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
            ).scalar_one()
        )
        if not acquired:
            emit_structured_log(
                logger,
                logging.WARNING,
                "concurrent_run_lock_denied",
                strategy_id=strategy_id,
                session_date=session_date.isoformat(),
            )
            raise ConcurrentRunLockedError(strategy_id, session_date)
        yield
    finally:
        if acquired:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
        connection.close()
