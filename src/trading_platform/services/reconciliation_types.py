"""TEMPORARY re-export shim (12-05 Task 1 -> deleted in Task 2).

The typed contracts split into ``trading_platform.services.reconciliation.snapshot``
(typed ``Local*Snapshot`` dataclasses + ``ReconciliationIdentity``) and
``trading_platform.services.reconciliation.findings`` (the closed ``ReconciliationFinding``
enum + the ``Finding`` value type). This shim keeps the old import path buildable while
Task 2 repoints every real consumer; it is deleted delete-last in Task 2 once the grep
for old module paths is clean and the targeted suites are green. It is NOT shipped.
"""

from __future__ import annotations

from trading_platform.services.reconciliation.findings import (  # noqa: F401
    Finding,
    ReconciliationFinding,
)
from trading_platform.services.reconciliation.snapshot import (  # noqa: F401
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
