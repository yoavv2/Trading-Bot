"""Reconciliation package (STRUCT-05).

Reorganizes the reconciliation subsystem into four role-named modules:

- ``snapshot`` — typed ``Local*Snapshot`` dataclasses + ``ReconciliationIdentity``.
- ``findings`` — the closed ``ReconciliationFinding`` enum + the ``Finding`` value type.
- ``matcher`` — the pure, indexed ``match_snapshots`` matcher.
- ``report`` — the read-only ``reconcile_paper_execution`` orchestrator, the
  separately-invoked ``apply_reconciliation_corrections`` corrective entrypoint, and
  the materialized ``ReconciliationReport``.

Consumers import the public surface from this package (``trading_platform.services.
reconciliation``). ``ReconciliationFinding`` re-exported here is the closed-enum finding
CATEGORY (from ``findings``); the report-level ``ReconciliationFinding`` dataclass that
``ReconciliationReport.findings`` carries lives in ``report`` and is imported from there
directly when needed (the two intentionally share a name across different roles).
"""

from __future__ import annotations

from trading_platform.services.reconciliation.findings import Finding, ReconciliationFinding
from trading_platform.services.reconciliation.matcher import (
    match_snapshots,
    match_snapshots_with_comparisons,
)
from trading_platform.services.reconciliation.report import (
    BrokerStateSnapshot,
    ReconciliationReport,
    apply_reconciliation_corrections,
    load_broker_state,
    reconcile_paper_execution,
    recover_inflight_paper_orders,
)
from trading_platform.services.reconciliation.snapshot import (
    DEFAULT_ACCOUNT,
    LocalAccountSnapshot,
    LocalFillSnapshot,
    LocalOrderSnapshot,
    LocalPositionSnapshot,
    PositionSide,
    ReconciliationIdentity,
    identity_for_broker_position,
    side_from_quantity,
)

__all__ = [
    # report / orchestrator surface
    "reconcile_paper_execution",
    "apply_reconciliation_corrections",
    "recover_inflight_paper_orders",
    "load_broker_state",
    "BrokerStateSnapshot",
    "ReconciliationReport",
    # matcher surface
    "match_snapshots",
    "match_snapshots_with_comparisons",
    # findings surface
    "ReconciliationFinding",
    "Finding",
    # snapshot surface
    "LocalOrderSnapshot",
    "LocalFillSnapshot",
    "LocalPositionSnapshot",
    "LocalAccountSnapshot",
    "ReconciliationIdentity",
    "PositionSide",
    "DEFAULT_ACCOUNT",
    "side_from_quantity",
    "identity_for_broker_position",
]
