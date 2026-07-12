"""Session-level PostgreSQL advisory-lock primitive guarding concurrent runs.

At most one active run per ``(strategy_id, session_date)`` may perform side
effects (broker calls, state-affecting DB writes). This module builds the
typed denial exception and the deterministic advisory-lock key derivation
that the ``session_run_lock()`` context manager (added next) relies on.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date

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
