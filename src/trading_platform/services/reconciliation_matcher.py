"""TEMPORARY re-export shim (12-05 Task 1 -> deleted in Task 2).

The matcher moved to ``trading_platform.services.reconciliation.matcher``. This shim
keeps the old import path buildable while Task 2 repoints every real consumer; it is
deleted delete-last in Task 2 once the grep for old module paths is clean and the
targeted suites are green. It is NOT shipped.
"""

from __future__ import annotations

from trading_platform.services.reconciliation.matcher import (  # noqa: F401
    _BROKER_STATUS_TO_EXPECTED_LOCAL_STATUS,
    _match_fills,
    _match_orders,
    _match_positions,
    match_snapshots,
    match_snapshots_with_comparisons,
)
